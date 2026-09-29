from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.accepted_ledger_batch import AcceptedLedgerBatchIssuer
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_character_interior_world_stimulus import (
    SOURCE_REF,
    _RoleModel,
    _runtime_for_ledger,
    _seed_relationship_state,
)
from companion_daemon.world_v2.relationship_proposal_compiler import RelationshipProposalCompilerError
from test_life_projection import WORLD_ID, commit, event, seed_through_proposal, settlement_batch


def _open(path, model, *, seed=False):
    issuer = AcceptedLedgerBatchIssuer()
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID, accepted_batch_issuer=issuer)
    if seed:
        seed_through_proposal(ledger)
        commit(ledger, settlement_batch())
    runtime, _, _ = _runtime_for_ledger(
        ledger=ledger, issuer=issuer, model=model, source_ref=SOURCE_REF,
        companion_actor_ref="actor:companion",
    )
    return ledger, runtime


def _advance(ledger, at):
    current = ledger.project().logical_time
    if at == current:
        return
    commit(ledger, [event(
        f"clock:retry:{at.isoformat()}", "ClockAdvanced",
        {"logical_time_from": current.isoformat(), "logical_time_to": at.isoformat()},
        at=at,
    )])


@pytest.mark.asyncio
async def test_sqlite_failure_backoff_survives_cold_reopen_and_no_change_stays_terminal(tmp_path):
    path = tmp_path / "retry.sqlite"
    failed_model = _RoleModel(failure=ConnectionError("provider unavailable"))
    ledger, runtime = _open(path, failed_model, seed=True)
    failed_at = ledger.project().logical_time
    assert (await runtime.drain_one()).work_status == "technical_failure"
    assert failed_model.calls == 1
    failure_hash = ledger.project().model_result_audits[-1].audit_hash
    ledger.close()

    recovered_model = _RoleModel(decision="no_change")
    ledger, runtime = _open(path, recovered_model)
    assert (await runtime.drain_one()).status == "idle"
    assert runtime.health_snapshot(WORLD_ID).deferred_count == 1
    _advance(ledger, failed_at + timedelta(seconds=599))
    assert (await runtime.drain_one()).status == "idle"
    assert recovered_model.calls == 0
    assert ledger.project().model_result_audits[-1].audit_hash == failure_hash

    _advance(ledger, failed_at + timedelta(seconds=600))
    assert (await runtime.drain_one()).work_status == "no_change"
    assert recovered_model.calls == 1
    assert ledger.rebuild() == ledger.project()
    ledger.close()

    ledger, runtime = _open(path, recovered_model)
    _advance(ledger, failed_at + timedelta(days=1))
    assert (await runtime.drain_one()).status == "idle"
    assert recovered_model.calls == 1
    assert runtime.health_snapshot(WORLD_ID).no_change_count == 1
    ledger.close()


@pytest.mark.asyncio
async def test_each_failed_attempt_uses_existing_life_backoff_and_cap_after_reopen(tmp_path):
    path = tmp_path / "repeated.sqlite"
    model = _RoleModel(failure=ConnectionError("provider unavailable"))
    ledger, runtime = _open(path, model, seed=True)
    now = ledger.project().logical_time
    for expected_calls, delay in enumerate((600, 1800, 7200, 7200), start=1):
        assert (await runtime.drain_one()).work_status == "technical_failure"
        assert model.calls == expected_calls
        ledger.close()
        ledger, runtime = _open(path, model)
        _advance(ledger, now + timedelta(seconds=delay - 1))
        assert (await runtime.drain_one()).status == "idle"
        assert model.calls == expected_calls
        now += timedelta(seconds=delay)
        _advance(ledger, now)
    assert runtime.health_snapshot(WORLD_ID).technical_failure_count == 1
    assert runtime.health_snapshot(WORLD_ID).no_change_count == 0
    assert ledger.rebuild() == ledger.project()
    ledger.close()


@pytest.mark.asyncio
async def test_failed_retry_uses_persisted_opportunity_lineage_without_historical_replay(
    tmp_path, monkeypatch,
):
    ledger, runtime = _open(
        tmp_path / "lineage-retry.sqlite",
        _RoleModel(failure=ConnectionError("provider unavailable")),
        seed=True,
    )
    assert (await runtime.drain_one()).work_status == "technical_failure"
    projection = ledger.project()
    audit = next(
        item for item in projection.model_result_audits
        if item.attempt_id in {
            attempt
            for process in projection.trigger_processes
            if process.process_kind == "npc_world_appraisal"
            for attempt in process.attempt_ids
        }
    )
    identity = runtime._retry_opportunity_identity(audit)
    assert identity is not None

    def historical_projection_is_forbidden(*_args, **_kwargs):
        raise AssertionError("retry reconstruction must not replay a historical cursor")

    monkeypatch.setattr(ledger, "project_at", historical_projection_is_forbidden)
    failed = runtime._technical_retry_schedule._failure(audit, projection)
    assert failed.opportunity_ref == identity.opportunity_ref
    assert failed.attempt_id == audit.attempt_id
    assert failed.failed_at == ledger.lookup_event_commit(audit.event_ref)[0].logical_time
    ledger.close()


@pytest.mark.asyncio
async def test_due_selection_skips_failed_opportunity_but_new_source_group_inherits_no_failure(
    tmp_path, monkeypatch,
):
    path = tmp_path / "independent.sqlite"
    model = _RoleModel(failure=ConnectionError("provider unavailable"))
    ledger, runtime = _open(path, model, seed=True)
    assert (await runtime.drain_one()).work_status == "technical_failure"
    ledger.close()
    ledger, runtime = _open(path, model)
    projection = ledger.project()
    first = next(p for p in projection.trigger_processes if p.process_kind == "npc_world_appraisal")
    source = ledger.lookup_event_commit(SOURCE_REF)[0]
    second = first.model_copy(update={
        "trigger_id": "appraisal:another", "trigger_ref": "appraisal:another",
        "source_evidence_ref": "source:another", "state": "open", "claim_lease": None,
        "attempt_ids": (),
    })
    second_event = source.model_copy(update={
        "event_id": "source:another", "correlation_id": "correlation:another",
    })
    # Candidate routing seam only. The previous failure and its historical
    # opportunity are read from real SQLite, never fabricated failure state.
    real_source = runtime._health_source_event
    monkeypatch.setattr(runtime, "_health_source_event", lambda ref:
                        second_event if ref == second_event.event_id else real_source(ref))
    candidates = SimpleNamespace(**{
        **{key: getattr(projection, key) for key in (
            "logical_time", "model_result_audits", "proposal_audits", "world_revision",
            "deliberation_revision", "ledger_sequence",
        )},
        "trigger_processes": (first, second),
    })
    assert runtime._next_process(candidates).trigger_id == second.trigger_id
    # Adding a genuinely new source to the same merge group changes its exact
    # opportunity identity; the old group's failure cannot postpone this work.
    second_event = second_event.model_copy(update={"correlation_id": source.correlation_id})
    assert runtime._durable_deferred_triggers(candidates) == frozenset()
    assert runtime._next_process(candidates).trigger_id == first.trigger_id
    assert model.calls == 1
    ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("minutes", [
    (0, 40, 43, 47, 51, 58, 73.5, 90, 93, 143, 155, 160, 180),
    (0, 4, 13, 28.5, 45, 48, 75, 78.5, 90, 98, 110),
], ids=["reflection-13-claims-18-physical-calls", "occurrence-11-claims-17-physical-calls"])
async def test_recorded_repeat_trigger_times_reduce_calls_even_with_restart_each_pass(
    tmp_path, minutes,
):
    """Replay the two audit trigger schedules with deterministic provider failure.

    The real histories had 18/17 physical author calls, including corrections;
    this one-call failure fixture measures scheduler attempts (13/11), not model
    quality or historical provider billing.
    """
    path = tmp_path / "schedule.sqlite"
    model = _RoleModel(failure=ConnectionError("provider unavailable"))
    ledger, runtime = _open(path, model, seed=True)
    start = ledger.project().logical_time
    for minute in minutes:
        _advance(ledger, start + timedelta(minutes=minute))
        await runtime.drain_one()
        ledger.close()
        ledger, runtime = _open(path, model)
    assert model.calls == 3
    assert len(ledger.project().model_result_audits) == 3
    assert not ledger.project().appraisals
    assert all(p.state != "terminal" for p in ledger.project().trigger_processes)
    assert ledger.rebuild() == ledger.project()
    ledger.close()


@pytest.mark.asyncio
async def test_cold_accepted_author_recovers_settlement_without_waiting_or_new_call(tmp_path, monkeypatch):
    path = tmp_path / "accepted.sqlite"
    model = _RoleModel(decision="activate", relationship_subject_ref="user:geoff")
    ledger, _ = _open(path, model, seed=True)
    issuer = ledger._accepted_batch_issuer
    await _seed_relationship_state(ledger=ledger, issuer=issuer, source_ref=SOURCE_REF,
                                   subject_ref="user:geoff")
    runtime, _, _ = _runtime_for_ledger(
        ledger=ledger, issuer=issuer, model=model, source_ref=SOURCE_REF,
        companion_actor_ref="actor:companion", settle_relationship=True,
    )
    async def fail_compilation(**_kwargs):
        raise RelationshipProposalCompilerError("injected_failure")
    monkeypatch.setattr(runtime._relationship_settlement, "process", fail_compilation)
    assert (await runtime.drain_one()).work_status == "technical_failure"
    assert model.calls == 1
    assert ledger.project().proposal_audits
    ledger.close()

    model = _RoleModel(failure=AssertionError("settlement must reuse the accepted author"))
    ledger, _ = _open(path, model)
    runtime, _, _ = _runtime_for_ledger(
        ledger=ledger, issuer=ledger._accepted_batch_issuer, model=model, source_ref=SOURCE_REF,
        companion_actor_ref="actor:companion", settle_relationship=True,
    )
    assert (await runtime.drain_one()).work_status == "accepted"
    assert model.calls == 0
    assert ledger.rebuild() == ledger.project()
    ledger.close()


@pytest.mark.asyncio
async def test_unreadable_failure_identity_stays_blocked_then_recovers_reader(tmp_path, monkeypatch, caplog):
    ledger, runtime = _open(tmp_path / "unreadable.sqlite",
                            _RoleModel(failure=ConnectionError()), seed=True)
    assert (await runtime.drain_one()).work_status == "technical_failure"
    runtime._technical_failure_deferred_until.clear()
    original = runtime._technical_retry_schedule._failure
    def unavailable(*_args):
        raise ValueError("historical identity reader unavailable")
    monkeypatch.setattr(runtime._technical_retry_schedule, "_failure", unavailable)
    _advance(ledger, ledger.project().logical_time + timedelta(hours=1))
    assert (await runtime.drain_one()).status == "idle"
    assert runtime.health_snapshot(WORLD_ID).deferred_count == 1
    assert "retry_identity_unavailable" in caplog.text
    monkeypatch.setattr(runtime._technical_retry_schedule, "_failure", original)
    assert (await runtime.drain_one()).work_status == "technical_failure"
    ledger.close()


@pytest.mark.asyncio
async def test_lineage_cache_preserves_projection_membership_and_original_audit_bytes(tmp_path):
    ledger, runtime = _open(tmp_path / "lineage.sqlite", _RoleModel(decision="no_change"), seed=True)
    await runtime.drain_one()
    projection = ledger.project()
    identity = runtime._durable_opportunity_identity(projection, SOURCE_REF)
    assert identity is not None
    assert runtime._durable_opportunity_identity(
        SimpleNamespace(model_result_audits=()), SOURCE_REF,
    ) is None
    audit = projection.model_result_audits[-1]
    # Reusing an event/hash with different bytes must never reuse its parsed
    # authority. This malformed seam fixture is rejected by the same validator.
    changed = audit.model_copy(update={"audit_json": "{}"})
    assert runtime._durable_opportunity_identity(
        SimpleNamespace(model_result_audits=(changed,)), SOURCE_REF,
    ) is None
    assert runtime._durable_opportunity_identity(projection, SOURCE_REF) == identity
    ledger.close()
