"""Recover immutable role choices without reconstructing their whole past."""

import pytest
import pytest_asyncio
import sqlite3
from datetime import timedelta

from companion_daemon.world_v2.accepted_ledger_batch import AcceptedLedgerBatchIssuer
from companion_daemon.world_v2.character_interior.experience_transitions import (
    ExperienceTransitionSettlement,
)
from companion_daemon.world_v2.ledger import WorldLedger
from companion_daemon.world_v2.errors import LedgerIntegrityError
from companion_daemon.world_v2.schemas import ProjectionCursor
from companion_daemon.world_v2.projection import InternalAuthorityReader
from companion_daemon.world_v2.runtime import WorldRuntime
from companion_daemon.world_v2.schemas import ClockObservation
from companion_daemon.world_v2.proposal_audit import ProposalAuditRecorder
from companion_daemon.world_v2.schemas import WorldEvent
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_character_interior_world_stimulus import (
    SOURCE_REF,
    WORLD_ID,
    _RoleModel,
    _runtime_for_ledger,
    commit,
    seed_through_proposal,
    settlement_batch,
)


def cursor(value):
    return ProjectionCursor(
        world_revision=value.world_revision,
        deliberation_revision=value.deliberation_revision,
        ledger_sequence=value.ledger_sequence,
    )


@pytest_asyncio.fixture(params=["memory", "sqlite"])
async def history(request, tmp_path):
    issuer = AcceptedLedgerBatchIssuer()
    path = tmp_path / "history.sqlite"
    ledger = (
        WorldLedger.in_memory(world_id=WORLD_ID, accepted_batch_issuer=issuer)
        if request.param == "memory"
        else SQLiteWorldLedger(path=path, world_id=WORLD_ID, accepted_batch_issuer=issuer)
    )
    seed_through_proposal(ledger)
    commit(ledger, settlement_batch())
    before = cursor(ledger.project())
    model = _RoleModel(decision="activate", experience_transition={
        "domain": "thread", "operation": "open", "target_id": None,
        "expected_entity_revision": 0, "thread_kind": "reply_reconsideration",
        "importance_bp": 6100, "due_at": "2026-08-05T18:00:00Z",
        "expires_at": "2026-08-06T18:00:00Z", "resolution_kind": None,
        "cancellation_reason_code": None, "source_refs": [SOURCE_REF],
        "reason_summary": "她想过一会儿再决定是否联系。",
    })
    runtime, _, _ = _runtime_for_ledger(
        ledger=ledger, issuer=issuer, model=model, source_ref=SOURCE_REF,
        companion_actor_ref="actor:companion",
    )
    assert (await runtime.drain_one()).work_status == "accepted"
    assert model.calls == 1
    audit = ledger.project().proposal_audits[0]
    audit_cursor = cursor(ledger.lookup_event_commit(audit.event_ref)[1])
    assert audit_cursor.ledger_sequence < ledger.project().ledger_sequence
    if request.param == "sqlite":
        ledger.close()
        ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID, accepted_batch_issuer=issuer)
    try:
        yield ledger, audit, audit_cursor, before
    finally:
        if isinstance(ledger, SQLiteWorldLedger):
            ledger.close()


@pytest.mark.asyncio
async def test_old_experience_choice_recovery_avoids_historical_projection(history, monkeypatch):
    ledger, audit, audit_cursor, _ = history
    settlement = ExperienceTransitionSettlement(
        ledger=ledger, owner_id="worker:appraisal", companion_actor_ref="actor:companion",
    )
    source_event = ledger.lookup_event_commit(SOURCE_REF)[0]
    expected = settlement._authored_change(
        audit_cursor=audit_cursor, proposal_id=audit.proposal_id, source_event=source_event,
    )
    before = ledger.project()

    original = ledger.project_at

    def forbidden(pin):
        if pin == cursor(ledger.project()):
            return original(pin)
        raise AssertionError("old choice recovery reconstructs the entire historical world")

    monkeypatch.setattr(ledger, "project_at", forbidden)
    assert settlement._authored_change(
        audit_cursor=audit_cursor, proposal_id=audit.proposal_id, source_event=source_event,
    ) == expected
    result = await settlement.process(
        audit_cursor=audit_cursor, current_cursor=cursor(before),
        proposal_id=audit.proposal_id, source_event=source_event,
    )
    assert result.status == "accepted" and result.replayed
    assert ledger.project() == before
    runtime, _, _ = _runtime_for_ledger(
        ledger=ledger, issuer=ledger._accepted_batch_issuer, model=_RoleModel(decision="no_change"),
        source_ref=SOURCE_REF, companion_actor_ref="actor:companion",
    )
    aspiration = await runtime._process_aspiration(
        audit_cursor=audit_cursor, current_cursor=cursor(before),
        proposal_id=audit.proposal_id, source_event=source_event,
    )
    assert aspiration.status == "no_change"
    await runtime._process_emotion(
        audit_cursor=audit_cursor, current_cursor=cursor(before), proposal_id=audit.proposal_id,
    )


@pytest.mark.asyncio
async def test_audit_read_matches_replay_at_every_prefix_including_mid_batch(history):
    ledger, audit, _, _ = history
    if isinstance(ledger, SQLiteWorldLedger):
        rows = ledger._connection.execute(
            "SELECT world_revision, deliberation_revision, ledger_sequence FROM world_v2_events "
            "WHERE world_id = ? ORDER BY ledger_sequence", (WORLD_ID,),
        ).fetchall()
        cursors = [ProjectionCursor(**dict(row)) for row in rows]
    else:
        cursors = [cursor(row) for row in ledger._events]
    cursors.insert(0, ProjectionCursor(world_revision=0, deliberation_revision=0, ledger_sequence=0))
    for pin in cursors:
        projection = ledger.project_at(pin)
        expected = next((a for a in projection.proposal_audits
                         if a.proposal_id == audit.proposal_id), None)
        assert ledger._proposal_audit_at(proposal_id=audit.proposal_id, cursor=pin) == expected
        assert ledger._proposal_audit_at(proposal_id="proposal:absent", cursor=pin) is None
        model_expected = next((a for a in projection.model_result_audits
                               if a.model_result_ref == audit.model_result_ref), None)
        reader = InternalAuthorityReader(ledger=ledger)
        assert reader.model_result_audit_by_ref(
            world_id=WORLD_ID, cursor=pin, model_result_ref=audit.model_result_ref,
        ) == model_expected
        with pytest.raises(ValueError, match="another world"):
            reader.proposal_audit_by_id(world_id="world:other", cursor=pin, proposal_id=audit.proposal_id)


@pytest.mark.asyncio
async def test_audit_read_rejects_forged_or_future_cursor_even_for_missing_proposal(history):
    ledger, audit, pin, _ = history
    for field in ("world_revision", "deliberation_revision", "ledger_sequence"):
        bad = pin.model_copy(update={field: getattr(pin, field) + 1000})
        for identity in (audit.proposal_id, "proposal:absent"):
            with pytest.raises(ValueError, match="cursor"):
                ledger._proposal_audit_at(proposal_id=identity, cursor=bad)
    bad_zero = ProjectionCursor(world_revision=1, deliberation_revision=0, ledger_sequence=0)
    with pytest.raises(ValueError, match="cursor"):
        ledger._proposal_audit_at(proposal_id=audit.proposal_id, cursor=bad_zero)


@pytest.mark.asyncio
async def test_recovery_cannot_use_future_audit_or_wrong_trigger(history):
    ledger, audit, pin, before = history
    settlement = ExperienceTransitionSettlement(
        ledger=ledger, owner_id="worker:appraisal", companion_actor_ref="actor:companion",
    )
    source = ledger.lookup_event_commit(SOURCE_REF)[0]
    for at, event in ((before, source), (pin, source.model_copy(update={"event_id": "event:wrong"}))):
        with pytest.raises(ValueError, match="source audit is unavailable"):
            settlement._authored_change(audit_cursor=at, proposal_id=audit.proposal_id, source_event=event)


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["audit", "boundary", "model_result"])
async def test_sqlite_external_tamper_invalidates_warm_audit_read(history, target):
    ledger, audit, pin, _ = history
    if not isinstance(ledger, SQLiteWorldLedger):
        return
    assert ledger._proposal_audit_at(proposal_id=audit.proposal_id, cursor=pin) == audit
    if target == "audit":
        event_id = audit.event_ref
    elif target == "model_result":
        event_id = next(a.event_ref for a in ledger.project().model_result_audits
                        if a.model_result_ref == audit.model_result_ref)
    else:
        event_id = ledger._connection.execute(
            "SELECT event_id FROM world_v2_events WHERE world_id = ? AND ledger_sequence = ?",
            (WORLD_ID, pin.ledger_sequence - 1),
        ).fetchone()[0]
    path = ledger._connection.execute("PRAGMA database_list").fetchone()[2]
    with sqlite3.connect(path) as writer:
        writer.execute("UPDATE world_v2_events SET event_json = event_json || ' ' WHERE event_id = ?", (event_id,))
    with pytest.raises(LedgerIntegrityError):
        ledger._proposal_audit_at(proposal_id=audit.proposal_id, cursor=pin)


@pytest.mark.asyncio
async def test_sqlite_valid_external_append_keeps_original_audit_pin(history):
    ledger, audit, pin, _ = history
    if not isinstance(ledger, SQLiteWorldLedger):
        return
    assert ledger._proposal_audit_at(proposal_id=audit.proposal_id, cursor=pin) == audit
    path = ledger._connection.execute("PRAGMA database_list").fetchone()[2]
    other = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    try:
        now = other.project().logical_time
        await WorldRuntime(world_id=WORLD_ID, ledger=other).advance(ClockObservation(
            schema_version="world-v2.1", tick_id="tick:external-append", world_id=WORLD_ID,
            logical_time_from=now, logical_time_to=now + timedelta(seconds=1), created_at=now,
            trace_id="trace:external-append", causation_id="cause:external-append",
            correlation_id="correlation:external-append", logical_time=now, reason="scheduled_tick",
        ))
    finally:
        other.close()
    assert ledger._proposal_audit_at(proposal_id=audit.proposal_id, cursor=pin) == audit
    assert not ledger._connection.in_transaction


@pytest.mark.parametrize("sqlite", [False, True])
@pytest.mark.parametrize("encoding", ["missing_default", "explicit_null"])
def test_legal_historical_model_audit_encodings_match_reducer(sqlite, encoding, tmp_path):
    from test_proposal_audit import WORLD, _started, _result, _context
    ledger = (SQLiteWorldLedger(path=tmp_path / "legacy.sqlite", world_id=WORLD)
              if sqlite else WorldLedger.in_memory(world_id=WORLD))
    try:
        _started(ledger)
        events = list(ProposalAuditRecorder(ledger=ledger).build_events(_result(), _context()))
        raw = events[0].payload()
        if encoding == "missing_default":
            raw.pop("audit_contract")
        else:
            raw["parent_model_call_id"] = None
        envelope = events[0].model_dump(exclude={"payload_json", "payload_hash"})
        events[0] = WorldEvent.from_payload(**envelope, payload=raw)
        result = ledger.commit_at_cursor(events, expected_cursor=cursor(ledger.project()))
        expected = ledger.project_at(cursor(result)).model_result_audits[0]
        assert InternalAuthorityReader(ledger=ledger).model_result_audit_by_ref(
            world_id=WORLD, cursor=cursor(result), model_result_ref=expected.model_result_ref,
        ) == expected
    finally:
        if sqlite:
            ledger.close()


def test_already_settled_affect_does_not_replay_original_mutable_bounds(monkeypatch):
    from test_immediate_emotion_proposal_worker import _record_combined_emotion_proposal, _worker, WORLD_ID
    issuer = AcceptedLedgerBatchIssuer()
    ledger = WorldLedger.in_memory(world_id=WORLD_ID, accepted_batch_issuer=issuer)
    proposal, pin = _record_combined_emotion_proposal(ledger)
    worker = _worker(ledger=ledger, issuer=issuer)
    assert worker.process(world_id=WORLD_ID, audit_cursor=pin, proposal_id=proposal.proposal_id).status == "accepted"
    original = ledger.project_at

    def forbid_original(value):
        if value == pin:
            raise AssertionError("settled affect reconstructs old mutable bounds it never uses")
        return original(value)

    monkeypatch.setattr(ledger, "project_at", forbid_original)
    result = worker._affect_compiler.record_rebased(
        world_id=WORLD_ID, audit_cursor=pin, current_cursor=cursor(ledger.project()), proposal_id=proposal.proposal_id,
    )
    assert result.status == "candidate_recorded"


@pytest.mark.parametrize("missing_mutation", [False, True])
def test_emotion_recovery_returns_verified_existing_receipt_without_reacceptance(monkeypatch, missing_mutation):
    from test_immediate_emotion_proposal_worker import _record_combined_emotion_proposal, _worker, WORLD_ID
    issuer = AcceptedLedgerBatchIssuer()
    ledger = WorldLedger.in_memory(world_id=WORLD_ID, accepted_batch_issuer=issuer)
    proposal, pin = _record_combined_emotion_proposal(ledger)
    worker = _worker(ledger=ledger, issuer=issuer)
    first = worker.process(world_id=WORLD_ID, audit_cursor=pin, proposal_id=proposal.proposal_id)
    before = ledger.project()
    original = ledger.project_at

    def current_only(value):
        if value != cursor(ledger.project()):
            raise AssertionError("completed emotion recovery replayed old acceptance state")
        return original(value)

    monkeypatch.setattr(ledger, "project_at", current_only)
    if missing_mutation:
        lookup = ledger.lookup_event_commit
        mutation_id = first.affect_acceptance_commit.event_ids[-1]
        monkeypatch.setattr(ledger, "lookup_event_commit", lambda ref: None if ref == mutation_id else lookup(ref))
        with pytest.raises(ValueError, match="settled_affect_receipt_mismatch"):
            worker.process(world_id=WORLD_ID, audit_cursor=pin,
                           current_cursor=cursor(before), proposal_id=proposal.proposal_id)
        assert ledger.project() == before
        return
    repeated = worker.process(world_id=WORLD_ID, audit_cursor=pin,
                              current_cursor=cursor(before), proposal_id=proposal.proposal_id)
    assert repeated.status == first.status == "accepted"
    assert repeated.affect_compile_commit == first.affect_compile_commit
    assert repeated.affect_acceptance_commit == first.affect_acceptance_commit
    assert ledger.project() == before


def test_audit_boundary_respects_existing_legacy_migration_hash_policy(tmp_path):
    from test_sqlite_ledger import test_sqlite_isolates_legacy_v3_unbound_acceptance_audit
    test_sqlite_isolates_legacy_v3_unbound_acceptance_audit(
        tmp_path, "minimal", {"status": "rejected", "acceptance_id": "legacy:acceptance"},
    )
    path = tmp_path / "world-v2-v3-legacy-acceptance-minimal.sqlite3"
    with SQLiteWorldLedger(path=path, world_id="world-sqlite-test") as ledger:
        pin = cursor(ledger.project())
        assert ledger.project_at(pin).proposal_audits == ()
        reader = InternalAuthorityReader(ledger=ledger)
        assert reader.proposal_audit_by_id(world_id=ledger.world_id, cursor=pin, proposal_id="absent") is None
        assert reader.model_result_audit_by_ref(world_id=ledger.world_id, cursor=pin, model_result_ref="absent") is None
