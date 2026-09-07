from __future__ import annotations

from datetime import timedelta
import json

import pytest

from companion_daemon.world_v2.accepted_ledger_batch import AcceptedLedgerBatchIssuer
from companion_daemon.world_v2.deferred_reply_runtime import DeferredReplyRuntime
from test_commitment_authority import (
    DUE,
    accept,
    changed,
    commitment,
    event as commitment_event,
    initialized as commitment_ledger,
)
from companion_daemon.world_v2.schemas import WorldEvent
from test_proactive_action_production import (
    _DraftModel,
    _TimeoutProactiveModel,
    _make_proactive_runtime,
)
from test_thread_authority import NOW, initialized
from test_thread_reschedule_attention import _accept_thread, _commit, _compiler, _tick


def _runtime(ledger, model):
    return _make_proactive_runtime(
        ledger=ledger,
        issuer=AcceptedLedgerBatchIssuer(),
        model=model,
        social_initiative=_compiler(ledger),
    )[0]


def _advance_clock(ledger, at):
    clock = WorldEvent.from_payload(
        schema_version="world-v2.1", world_id=ledger.world_id,
        event_id=f"clock:{at.isoformat()}", event_type="ClockAdvanced",
        logical_time=at, created_at=at, actor="system:test", source="test",
        trace_id="trace:recovery", causation_id="cause:recovery",
        correlation_id="correlation:recovery", idempotency_key=f"clock:{at.isoformat()}",
        payload={"logical_time_from": ledger.project().logical_time.isoformat(),
                 "logical_time_to": at.isoformat()},
    )
    _commit(ledger, [clock])
    return clock


def _declared_domain(source_type):
    if source_type.startswith("thread"):
        ledger = initialized()
        due = NOW + timedelta(minutes=5)
        current = _accept_thread(ledger, None, due=due, duration=timedelta(hours=1))
        _advance_clock(ledger, due)
        if source_type == "thread_updated":
            current = _accept_thread(
                ledger, current, due=due, duration=timedelta(hours=1), importance=7000,
            )
    else:
        ledger = commitment_ledger()
        current = commitment()
        accept(ledger, changed(
            operation="open", before=None, after=current,
            proposal_id="proposal:open", world_revision=ledger.project().world_revision,
        ))
        clock = _advance_clock(ledger, DUE)
        if source_type == "commitment_due":
            _commit(ledger, DeferredReplyRuntime(ledger=ledger).clock_events(
                projection=ledger.project(), clock_event=clock,
            ))
            current = ledger.project().commitments[-1]
    return ledger, current


@pytest.mark.asyncio
async def test_open_thread_consideration_survives_a_material_update_in_the_same_schedule():
    ledger = initialized()
    due = NOW + timedelta(minutes=5)
    first = _accept_thread(ledger, None, due=due)
    _tick(ledger, due)
    model = _DraftModel("silent")
    runtime = _runtime(ledger, model)
    assert (await runtime.drain_one()).status == "opened"
    original = ledger.project().trigger_processes[-1]

    _accept_thread(ledger, first, due=due, importance=7000)
    restarted = _runtime(ledger, model)
    result = await restarted.drain_one()

    assert result.status == "silent", result
    assert model.calls == 1
    process = ledger.project().trigger_processes[-1]
    assert process.trigger_id == original.trigger_id
    assert process.source_evidence_ref == original.source_evidence_ref
    assert process.runtime_outcome_ref == "proactive:silent"
    assert (await restarted.drain_one()).status == "idle"
    assert model.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("return_to_original_window", [False, True])
async def test_accepted_reschedule_retires_the_old_process_and_considers_the_new_schedule_once(
    return_to_original_window,
):
    ledger = initialized()
    original_due = NOW + timedelta(minutes=5)
    first = _accept_thread(ledger, None, due=original_due)
    _tick(ledger, original_due)
    model = _DraftModel("silent")
    runtime = _runtime(ledger, model)
    assert (await runtime.drain_one()).status == "opened"
    original = ledger.project().trigger_processes[-1]

    new_due = original_due + timedelta(minutes=20)
    rescheduled = _accept_thread(ledger, first, due=new_due)
    if return_to_original_window:
        new_due = original_due
        rescheduled = _accept_thread(ledger, rescheduled, due=new_due)
    restarted = _runtime(ledger, model)
    rejected = await restarted.drain_one()
    assert rejected.reason_code == "proactive.source_binding_invalid"
    assert model.calls == 0
    assert ledger.project().trigger_processes[-1].runtime_outcome_ref == (
        "proactive:source-binding-invalid"
    )

    if new_due != original_due:
        assert (await restarted.drain_one()).status == "idle"
        _tick(ledger, new_due)
    assert (await restarted.drain_one()).status == "opened"
    renewed = ledger.project().trigger_processes[-1]
    assert renewed.trigger_id != original.trigger_id
    assert renewed.source_evidence_ref == rescheduled.origin.accepted_event_ref
    assert (await restarted.drain_one()).status == "silent"
    assert model.calls == 1
    assert (await restarted.drain_one()).status == "idle"


@pytest.mark.asyncio
async def test_open_commitment_consideration_survives_its_mechanical_due_transition():
    ledger = commitment_ledger()
    current = commitment()
    accept(ledger, changed(
        operation="open", before=None, after=current,
        proposal_id="proposal:open", world_revision=ledger.project().world_revision,
    ))
    clock = commitment_event(
        "clock:due", "ClockAdvanced",
        {"logical_time_from": ledger.project().logical_time.isoformat(),
         "logical_time_to": DUE.isoformat()},
        at=DUE,
    )
    _commit(ledger, [clock])
    model = _DraftModel("silent")
    runtime = _runtime(ledger, model)
    assert (await runtime.drain_one()).status == "opened"
    original = ledger.project().trigger_processes[-1]

    _commit(ledger, DeferredReplyRuntime(ledger=ledger).clock_events(
        projection=ledger.project(), clock_event=clock,
    ))
    assert ledger.project().commitments[-1].values.status == "due"
    restarted = _runtime(ledger, model)
    result = await restarted.drain_one()
    assert result.status == "silent", result
    assert model.calls == 1
    process = ledger.project().trigger_processes[-1]
    assert process.trigger_id == original.trigger_id
    assert process.source_evidence_ref == current.origin.accepted_event_ref
    assert (await restarted.drain_one()).status == "idle"


@pytest.mark.asyncio
async def test_installing_social_initiative_recovers_an_open_legacy_thread_process_once():
    ledger = initialized()
    due = NOW + timedelta(minutes=5)
    first = _accept_thread(ledger, None, due=due)
    _tick(ledger, due)
    model = _DraftModel("silent")
    legacy, _ = _make_proactive_runtime(
        ledger=ledger, issuer=AcceptedLedgerBatchIssuer(), model=model,
    )
    assert (await legacy.drain_one()).status == "opened"
    original = ledger.project().trigger_processes[-1]
    _accept_thread(ledger, first, due=due, importance=7000)

    installed = _runtime(ledger, model)
    result = await installed.drain_one()
    assert result.status == "silent", result
    assert model.calls == 1
    assert ledger.project().trigger_processes[-1].trigger_id == original.trigger_id
    assert (await installed.drain_one()).status == "idle"


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", ["commitment_open", "commitment_due"])
async def test_installing_social_initiative_recovers_a_legacy_commitment_once(source_type):
    ledger, _current = _declared_domain(source_type)
    model = _DraftModel("silent")
    legacy, _ = _make_proactive_runtime(
        ledger=ledger, issuer=AcceptedLedgerBatchIssuer(), model=model,
    )
    assert (await legacy.drain_one()).status == "opened"
    original = ledger.project().trigger_processes[-1]

    installed = _runtime(ledger, model)
    assert (await installed.drain_one()).status == "silent"
    assert model.calls == 1
    assert ledger.project().trigger_processes[-1].trigger_id == original.trigger_id
    assert (await installed.drain_one()).status == "idle"


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", [
    "thread_open", "thread_updated", "commitment_open", "commitment_due",
])
async def test_domain_consideration_retries_the_same_source_after_provider_failure(source_type):
    ledger, current = _declared_domain(source_type)
    failing_model = _TimeoutProactiveModel()
    runtime = _runtime(ledger, failing_model)
    assert (await runtime.drain_one()).status == "opened"
    original = ledger.project().trigger_processes[-1]
    assert (await runtime.drain_one()).status == "failed_safe"
    waiting = await runtime.drain_one()
    assert waiting.status == "retry_wait", waiting
    assert waiting.source_ref == current.origin.accepted_event_ref
    _advance_clock(ledger, waiting.next_retry_at)

    model = _DraftModel("silent")
    restarted = _runtime(ledger, model)
    assert (await restarted.drain_one()).status == "opened"
    retry = ledger.project().trigger_processes[-1]
    assert retry.trigger_ref == original.trigger_ref
    assert retry.source_evidence_ref == original.source_evidence_ref
    assert (await restarted.drain_one()).status == "silent"
    assert model.calls == 1
    proposal = json.loads(ledger.project().proposal_audits[-1].proposal_json)
    assert proposal["proactive_opportunity_decision"]["source_kind"] == (
        "thread" if source_type.startswith("thread") else "commitment"
    )
    assert (await restarted.drain_one()).status == "idle"


@pytest.mark.asyncio
async def test_rescheduling_after_provider_failure_does_not_retry_the_old_schedule():
    ledger, current = _declared_domain("thread_updated")
    runtime = _runtime(ledger, _TimeoutProactiveModel())
    assert (await runtime.drain_one()).status == "opened"
    original = ledger.project().trigger_processes[-1]
    assert (await runtime.drain_one()).status == "failed_safe"

    new_due = ledger.project().logical_time + timedelta(minutes=20)
    rescheduled = _accept_thread(
        ledger, current, due=new_due, duration=timedelta(hours=1),
    )
    model = _DraftModel("silent")
    restarted = _runtime(ledger, model)
    assert (await restarted.drain_one()).status == "idle"
    _advance_clock(ledger, new_due)
    assert (await restarted.drain_one()).status == "opened"
    renewed = ledger.project().trigger_processes[-1]
    assert renewed.trigger_ref != original.trigger_ref
    assert renewed.source_evidence_ref == rescheduled.origin.accepted_event_ref
    assert (await restarted.drain_one()).status == "silent"
    assert model.calls == 1
    assert (await restarted.drain_one()).status == "idle"
