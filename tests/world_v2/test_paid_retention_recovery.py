"""A paid decision remains recoverable when its best-effort hitch loses storage."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json

import pytest

from companion_daemon.world_v2.character_interior.inbound_author import _InboundCharacterAuthor
from companion_daemon.world_v2.private_impression_producer import PrivateImpressionTriggerRuntime
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.schemas import ProjectionCursor
from companion_daemon.world_v2.world_turn_runtime import InboundTurn
from test_production_turn_application import NOW, _config, _DeliveredTransport, _Identities, _Router
from world_v2_application import build_sqlite_world_v2_test_application, compose_fixture_character_interior

KEPT = "他说只是忙，不等于我对他不重要"


class PaidRole:
    model = "test-paid-retention-recovery"

    def __init__(self):
        self.calls = 0

    async def complete(self, messages, *, temperature=0.8):
        self.calls += 1
        return json.dumps({
            "messages": ["嗯，我听到了。"], "meaning_of_this": "他解释了这两天为什么忙",
            "my_state": "听完稍微松了口气", "stuck_with_me": KEPT, "keep_impression": True,
        }, ensure_ascii=False)


def build(path, model):
    return build_sqlite_world_v2_test_application(
        path=path, config=replace(_config(), private_impression_daily_model_call_limit=0),
        identities=_Identities(), router=_Router(),
        character_interior=compose_fixture_character_interior(
            inbound_author=_InboundCharacterAuthor(flash_model=model),
        ), transport=_DeliveredTransport(), now=NOW,
    )


async def respond(app):
    result = await app.respond(InboundTurn(
        platform="test", platform_user_id="user.1", platform_message_id="paid-recovery",
        text="最近很忙，没来得及回。", observed_at=NOW, trace_id="trace:paid-recovery",
    ))
    assert result.status == "action_authorized"


def persist_legacy_prefix(original, ledger, events, prefix, kwargs):
    original(ledger, events[:2], **{**kwargs, "commit_id": kwargs["commit_id"] + ":legacy-audit"})
    if prefix == 3:
        projection = ledger.project()
        original(ledger, events[2:3], **{
            **kwargs, "commit_id": kwargs["commit_id"] + ":legacy-proposal",
            "expected_cursor": ProjectionCursor(
                world_revision=projection.world_revision,
                deliberation_revision=projection.deliberation_revision,
                ledger_sequence=projection.ledger_sequence,
            ),
        })


def at_cut(events, cut):
    if cut == "audit":
        return any(event.event_type == "ModelResultRecorded" and
                   event.event_id.startswith("event:private-impression:model-result:") for event in events)
    if cut == "proposal":
        return any(event.event_type == "ProposalRecorded" and
                   event.payload().get("proposal_kind") == "private_impression_transition" for event in events)
    return any(event.event_type == "PrivateImpressionAccepted" for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("cut", [
    "hitch", "audit", "proposal", "acceptance", "legacy_audit", "legacy_proposal",
])
async def test_background_recovers_paid_choice_after_restart_without_reflection_budget(
    tmp_path, monkeypatch, cut,
):
    path = tmp_path / "restart.sqlite"
    model = PaidRole()
    original_commit = SQLiteWorldLedger.commit_at_cursor
    failed = False

    def fail_once(ledger, events, **kwargs):
        nonlocal failed
        if not failed and at_cut(events, cut):
            failed = True
            if cut.startswith("legacy_"):
                # Import the old two/three-event write prefix exactly. New
                # writes commit the full derived authority atomically.
                prefix = 2 if cut == "legacy_audit" else 3
                persist_legacy_prefix(original_commit, ledger, events, prefix, kwargs)
            raise OSError(f"storage interrupted before {cut}")
        return original_commit(ledger, events, **kwargs)

    async def lost_hitch(*args, **kwargs):
        nonlocal failed
        failed = True
        raise OSError("crash before hitch can record anything")

    with monkeypatch.context() as patch:
        if cut == "hitch":
            patch.setattr(PrivateImpressionTriggerRuntime, "record_paid_inbound", lost_hitch)
        else:
            patch.setattr(SQLiteWorldLedger, "commit_at_cursor", fail_once)
        app = build(path, model)
        try:
            await respond(app)
            before = app.export_replay_evidence().projection
            assert before.private_impressions == ()
            if not cut.startswith("legacy_"):
                assert before.private_impression_proposals == ()
                assert not any(audit.model_call_id.startswith("paid-inbound-impression:")
                               for audit in before.model_result_audits)
        finally:
            app.close()
    assert failed
    assert model.calls == 1

    app = build(path, model)
    try:
        for _ in range(12):
            await app.drain_background_once()
            if app.export_replay_evidence().projection.private_impressions:
                break
        evidence = app.export_replay_evidence()
        assert [item.reflection_summary for item in evidence.projection.private_impressions] == [KEPT]
        impression = evidence.projection.private_impressions[0]
        appraisal = evidence.projection.appraisals[0]
        assert impression.source_refs == (appraisal.origin.accepted_event_ref,)
        assert impression.interpretation_refs == tuple(
            f"appraisal:{appraisal.appraisal_id}:{item.hypothesis_id}" for item in appraisal.hypotheses
        )
        for _ in range(4):
            await app.drain_background_once()
        after = app.export_replay_evidence()
        assert after.projection.private_impressions == evidence.projection.private_impressions
        assert after.replay == after.projection
        assert model.calls == 1
    finally:
        app.close()


@pytest.mark.asyncio
async def test_recovery_storage_backoff_survives_restart(tmp_path, monkeypatch):
    import companion_daemon.world_v2.private_impression_producer as producer

    path = tmp_path / "backoff.sqlite"
    model = PaidRole()
    original = SQLiteWorldLedger.commit_at_cursor
    failures = 0
    now = NOW
    monkeypatch.setattr(producer, "_paid_recovery_now", lambda: now)

    def unavailable(ledger, events, **kwargs):
        nonlocal failures
        if at_cut(events, "proposal"):
            failures += 1
            raise OSError("proposal storage unavailable")
        return original(ledger, events, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(SQLiteWorldLedger, "commit_at_cursor", unavailable)
        app = build(path, model)
        try:
            await respond(app)
            for _ in range(12):
                await app.drain_background_once()
                if failures == 2:
                    break
            assert failures == 2
        finally:
            app.close()
        app = build(path, model)
        try:
            for _ in range(8):
                await app.drain_background_once()
            assert failures == 2
        finally:
            app.close()
    now += timedelta(seconds=61)
    app = build(path, model)
    try:
        for _ in range(12):
            await app.drain_background_once()
            if app.export_replay_evidence().projection.private_impressions:
                break
        assert len(app.export_replay_evidence().projection.private_impressions) == 1
        assert model.calls == 1
    finally:
        app.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cut", [
    "hitch", "audit", "proposal", "acceptance", "legacy_audit", "legacy_proposal",
])
async def test_later_world_prefix_preserves_paid_authority_or_records_terminal_failure(
    tmp_path, monkeypatch, cut,
):
    from companion_daemon.world_v2 import WorldRuntime
    from companion_daemon.world_v2.schemas import ClockObservation
    from companion_daemon.world_v2.private_impression_producer import PrivateImpressionTriggerOpener

    path = tmp_path / "later-prefix.sqlite"
    model = PaidRole()
    original = SQLiteWorldLedger.commit_at_cursor

    def unavailable(ledger, events, **kwargs):
        if at_cut(events, cut):
            if cut.startswith("legacy_"):
                prefix = 2 if cut == "legacy_audit" else 3
                persist_legacy_prefix(original, ledger, events, prefix, kwargs)
            raise OSError("storage interrupted")
        return original(ledger, events, **kwargs)

    async def lost_hitch(*args, **kwargs):
        raise OSError("lost hitch")

    with monkeypatch.context() as patch:
        if cut == "hitch":
            patch.setattr(PrivateImpressionTriggerRuntime, "record_paid_inbound", lost_hitch)
        else:
            patch.setattr(SQLiteWorldLedger, "commit_at_cursor", unavailable)
        app = build(path, model)
        try:
            await respond(app)
        finally:
            app.close()
    ledger = SQLiteWorldLedger(path=path, world_id=_config().world_id)
    try:
        before = ledger.project()
        later = before.logical_time + timedelta(seconds=1)
        await WorldRuntime(world_id=ledger.world_id, ledger=ledger).advance(ClockObservation(
            schema_version="world-v2.1", tick_id="paid-recovery:later", world_id=ledger.world_id,
            logical_time=later, created_at=later, trace_id="paid-recovery:later",
            causation_id="paid-recovery:later", correlation_id="paid-recovery:later",
            logical_time_from=before.logical_time, logical_time_to=later,
            reason="the world continues before durable storage recovery",
        ))
    finally:
        ledger.close()
    app = build(path, model)
    try:
        for _ in range(12):
            await app.drain_background_once()
        evidence = app.export_replay_evidence()
        if not cut.startswith("legacy_"):
            assert [item.reflection_summary for item in evidence.projection.private_impressions] == [KEPT]
        else:
            assert evidence.projection.private_impressions == ()
            failures = [json.loads(audit.audit_json).get("failure_code")
                        for audit in evidence.projection.model_result_audits]
            assert failures.count("private_impression_paid_retention_recovery_prefix_changed") == 1
        assert evidence.replay == evidence.projection
        assert model.calls == 1
    finally:
        app.close()
    ledger = SQLiteWorldLedger(path=path, world_id=_config().world_id)
    try:
        # A storage rejection cannot turn into another paid semantic choice.
        opener = PrivateImpressionTriggerOpener(ledger=ledger, owner_id="test:recovery")
        assert await opener.open_once() is None
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_atomic_paid_batch_rejects_unrelated_authority_and_extra_effects(tmp_path, monkeypatch):
    from companion_daemon.world_v2.batch_invariants import validate_commit_batch
    from companion_daemon.world_v2.proposal_audit_schemas import canonical_json, sha256

    captured = []
    original = SQLiteWorldLedger.commit_at_cursor

    def capture(ledger, events, **kwargs):
        if any(item.event_type == "PrivateImpressionAccepted" for item in events):
            captured.append((events, kwargs["expected_cursor"]))
        return original(ledger, events, **kwargs)

    monkeypatch.setattr(SQLiteWorldLedger, "commit_at_cursor", capture)
    app = build(tmp_path / "atomic.sqlite", PaidRole())
    try:
        await respond(app)
        assert len(app.export_replay_evidence().projection.private_impressions) == 1
    finally:
        app.close()
    assert len(captured) == 1
    batch, cursor = captured[0]
    assert tuple(event.event_type for event in batch) == (
        "ModelResultRecorded", "ProposalRecorded", "ProposalRecorded",
        "AcceptanceRecorded", "PrivateImpressionAccepted",
    )
    validate_commit_batch(batch, expected_world_revision=cursor.world_revision)

    def changed(event, payload):
        encoded = canonical_json(payload)
        return event.model_copy(update={"payload_json": encoded, "payload_hash": sha256(encoded)})

    invalid_batches = []
    for key, value in (
        ("source_model_result", "model-result:unrelated"),
        ("source_capsule_id", "f" * 64),
        ("transition_kind", "supersede"),
        ("proposed_mutation", {"event_type": "PrivateImpressionAccepted", "payload_json": "{}"}),
    ):
        invalid_batches.append((*batch[:2], changed(batch[2], {**batch[2].payload(), key: value}), *batch[3:]))
    invalid_batches.append((*batch[:3], changed(batch[3], {
        **batch[3].payload(), "accepted_change_hash": "f" * 64,
    }), batch[4]))
    invalid_batches.append((*batch, batch[-1]))
    # Keep the nested audit internally coherent while replacing the paid
    # attestation identity with an independent model call: no generic exception.
    old_call = batch[0].payload()["model_call_id"]
    replacement = "model-call:unpaid-independent-reflection"
    model_payload = json.loads(batch[0].payload_json.replace(old_call, replacement))
    model_payload["audit_hash"] = sha256(model_payload["audit_json"])
    proposal_payload = json.loads(batch[1].payload_json.replace(old_call, replacement))
    invalid_batches.append((changed(batch[0], model_payload), changed(batch[1], proposal_payload), *batch[2:]))
    for invalid in invalid_batches:
        with pytest.raises(ValueError):
            validate_commit_batch(invalid, expected_world_revision=cursor.world_revision)


@pytest.mark.asyncio
async def test_paid_commit_lost_acknowledgement_is_resolved_from_accepted_effect(tmp_path, monkeypatch):
    original = SQLiteWorldLedger.commit_at_cursor
    failed = False
    model = PaidRole()
    path = tmp_path / "lost-ack.sqlite"

    def lost_ack(ledger, events, **kwargs):
        nonlocal failed
        result = original(ledger, events, **kwargs)
        if not failed and any(event.event_type == "PrivateImpressionAccepted" for event in events):
            failed = True
            raise OSError("acknowledgement lost after SQLite committed")
        return result

    monkeypatch.setattr(SQLiteWorldLedger, "commit_at_cursor", lost_ack)
    app = build(path, model)
    try:
        await respond(app)
        before = app.export_replay_evidence()
        assert failed
        assert len(before.projection.private_impressions) == 1
        assert not any((json.loads(audit.audit_json).get("failure_code") or "").startswith(
            "private_impression_paid_retention"
        ) for audit in before.projection.model_result_audits)
    finally:
        app.close()
    app = build(path, model)
    try:
        for _ in range(8):
            await app.drain_background_once()
        after = app.export_replay_evidence()
        assert after.projection.private_impressions == before.projection.private_impressions
        assert after.replay == after.projection
        assert model.calls == 1
    finally:
        app.close()


@pytest.mark.asyncio
async def test_two_recovery_owners_complete_one_paid_choice_once(tmp_path, monkeypatch):
    import asyncio
    from test_private_impression_producer import _Model, _private_runtime

    path = tmp_path / "concurrent.sqlite"
    paid_model = PaidRole()

    async def lost_hitch(*args, **kwargs):
        raise OSError("hitch lost")

    with monkeypatch.context() as patch:
        patch.setattr(PrivateImpressionTriggerRuntime, "record_paid_inbound", lost_hitch)
        app = build(path, paid_model)
        try:
            await respond(app)
        finally:
            app.close()
    ledgers = [SQLiteWorldLedger(path=path, world_id=_config().world_id) for _ in range(2)]
    reflection_models = [_Model([]), _Model([])]
    runtimes = [_private_runtime(ledger, model, owner_id=f"recovery:{index}")[0]
                for index, (ledger, model) in enumerate(zip(ledgers, reflection_models, strict=True))]
    try:
        results = await asyncio.gather(*(runtime.recover_paid_once() for runtime in runtimes))
        assert all(result.work_status == "accepted" for result in results)
        evidence = ledgers[0].export_replay_evidence()
        assert len(evidence.projection.private_impressions) == 1
        assert evidence.replay == evidence.projection
        assert paid_model.calls == 1
        assert all(model.calls == [] for model in reflection_models)
    finally:
        for ledger in ledgers:
            ledger.close()


class UnreadableOldAudit:
    @property
    def proposal_json(self):
        raise AssertionError("ineligible or already resolved source must not parse historical proposals")


def opportunity_projection():
    from types import SimpleNamespace

    appraisal = SimpleNamespace(
        appraisal_id="appraisal:paid-source", status="active",
        origin=SimpleNamespace(accepted_event_ref="event:paid-appraisal", change_id="change:paid-appraisal"),
        hypotheses=(SimpleNamespace(hypothesis_id="hypothesis:paid-reading"),),
    )
    return SimpleNamespace(
        logical_time=NOW, world_id="world:lazy-paid-ownership",
        appraisals=(appraisal,), private_impressions=(), trigger_processes=(),
        # Ordinary current clocks bury the source below the head shortcut.
        committed_world_event_refs=(SimpleNamespace(event_type="ClockAdvanced", event_id="event:clock"),),
        proposal_audits=(UnreadableOldAudit(),),
    )


@pytest.mark.parametrize("unavailable", ["empty", "expired", "claimed", "interpreted"])
def test_no_eligible_appraisal_never_reads_historical_paid_proposals(unavailable):
    from types import SimpleNamespace
    from companion_daemon.world_v2.batch_invariants import private_impression_trigger_identity
    from companion_daemon.world_v2.private_impression_producer import private_impression_opportunity

    projection = opportunity_projection()
    if unavailable == "empty":
        projection.appraisals = ()
    elif unavailable == "expired":
        projection.appraisals[0].status = "expired"
    elif unavailable == "claimed":
        projection.trigger_processes = (SimpleNamespace(
            process_kind="private_impression_deliberation", state="terminal",
            trigger_id=private_impression_trigger_identity(projection.world_id, "event:paid-appraisal"),
        ),)
    else:
        projection.private_impressions = (SimpleNamespace(
            interpretation_refs=("appraisal:appraisal:paid-source:hypothesis:paid-reading",),
        ),)
    assert private_impression_opportunity(projection) is None


@pytest.mark.parametrize("keep", [True, False])
def test_buried_opportunity_checks_only_its_exact_paid_change(keep):
    from types import SimpleNamespace
    from companion_daemon.world_v2.private_impression_producer import private_impression_opportunity

    def audit(change_id, keep):
        return SimpleNamespace(proposal_json=json.dumps({
            "proposed_changes": [{"kind": "appraisal_transition", "change_id": change_id}],
            "private_turn_state": {"keep_impression": keep, "stuck_with_me": "她写下的读法"},
        }))

    projection = opportunity_projection()
    projection.proposal_audits = (
        UnreadableOldAudit(), audit("change:paid-appraisal", keep), audit("change:unrelated", True),
    )
    result = private_impression_opportunity(projection)
    if keep:
        assert result is None
    else:
        assert result is not None
        assert result[1] == "event:paid-appraisal"


@pytest.mark.asyncio
async def test_paid_failure_index_reads_each_immutable_audit_once():
    from types import SimpleNamespace
    from test_private_impression_producer import _Model, _private_runtime

    class ReadOnceFailure:
        attempt_id = "attempt:paid-inbound-impression:original:recovery:1"
        event_ref = "event:paid-recovery-failure"

        def __init__(self):
            self.reads = 0

        @property
        def audit_json(self):
            self.reads += 1
            assert self.reads == 1, "quiet recovery reparsed an immutable failure audit"
            return json.dumps({"failure_code": "private_impression_paid_retention_recovery_storage_failed"})

    first = ReadOnceFailure()
    projection = SimpleNamespace(proposal_audits=(), model_result_audits=(first,))
    ledger = SimpleNamespace(world_id="world:incremental-failure-index", blocks_event_loop=False,
                             project=lambda: projection)
    model = _Model([])
    runtime, _ = _private_runtime(ledger, model)
    assert (await runtime.recover_paid_once()).status == "idle"
    assert (await runtime.recover_paid_once()).status == "idle"
    second = ReadOnceFailure()
    projection.model_result_audits += (second,)
    assert (await runtime.recover_paid_once()).status == "idle"
    assert (await runtime.recover_paid_once()).status == "idle"
    assert first.reads == second.reads == 1
    assert model.calls == []
