from __future__ import annotations

import pytest

from companion_daemon.world_v2.accepted_ledger_batch import AcceptedLedgerBatchIssuer
from companion_daemon.world_v2.interaction_act_acceptance_runtime import (
    InteractionActAcceptanceRuntime,
)
from companion_daemon.world_v2.interaction_act_proposal_compiler import (
    InteractionActProposalCompiler,
)
from companion_daemon.world_v2.interaction_act_worker import InteractionActWorker
from companion_daemon.world_v2.ledger import LedgerPort, WorldLedger
from companion_daemon.world_v2.runtime import WorldRuntime
from companion_daemon.world_v2.schemas import ProjectionCursor, WorldEvent
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger

from test_interaction_act_proposal_compiler import (
    COMPANION,
    NOW,
    USER,
    WORLD,
    _record_decision,
)


def _worker(*, ledger: LedgerPort, issuer: AcceptedLedgerBatchIssuer) -> InteractionActWorker:
    return InteractionActWorker(
        ledger=ledger,
        compiler=InteractionActProposalCompiler(ledger=ledger),
        acceptance=InteractionActAcceptanceRuntime(
            ledger=ledger,
            batch_issuer=issuer,
        ),
        actor="worker:interaction-act",
    )


def _cursor(ledger: LedgerPort) -> ProjectionCursor:
    projection = ledger.project()
    return ProjectionCursor(
        world_revision=projection.world_revision,
        deliberation_revision=projection.deliberation_revision,
        ledger_sequence=projection.ledger_sequence,
    )


@pytest.mark.asyncio
async def test_observed_declaration_is_compiled_and_atomically_accepted() -> None:
    issuer = AcceptedLedgerBatchIssuer()
    ledger = WorldLedger.in_memory(world_id=WORLD, accepted_batch_issuer=issuer)
    source, _audit_cursor, _source_event = _record_decision(ledger)
    runtime = WorldRuntime(
        world_id=WORLD,
        ledger=ledger,
        interaction_act_worker=_worker(ledger=ledger, issuer=issuer),
    )

    result = await runtime.drain_background_once()

    assert result is not None
    assert result.status == "accepted"
    assert result.source_proposal_id == source.proposal_id
    projection = ledger.project()
    assert len(projection.interaction_acts) == 1
    act = projection.interaction_acts[0]
    assert act.subject_ref == USER
    assert act.counterparty_refs == (COMPANION,)
    assert tuple((item.actor_ref, item.status_code) for item in act.participant_statuses) == (
        (USER, "等待下轮继续"),
    )
    assert act.external_outcome == "not_established"
    assert projection.interaction_act_proposals == ()
    assert tuple(
        ledger.lookup_event_commit(event_id)[0].event_type
        for event_id in result.acceptance_commit.event_ids
    ) == ("AcceptanceRecorded", "InteractionActTransitionAccepted")
    assert ledger.rebuild() == projection


@pytest.mark.asyncio
async def test_settled_history_is_not_decoded_again_on_each_idle_scan(monkeypatch):
    from companion_daemon.world_v2 import proposal_envelope

    issuer = AcceptedLedgerBatchIssuer()
    ledger = WorldLedger.in_memory(world_id=WORLD, accepted_batch_issuer=issuer)
    _record_decision(ledger)
    worker = _worker(ledger=ledger, issuer=issuer)
    assert (await worker.drain_one()).status == "accepted"
    before = ledger.project()
    original = proposal_envelope._PROPOSAL_INPUT_ADAPTER.validate_json
    decoded = []

    def count(*args, **kwargs):
        decoded.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(proposal_envelope._PROPOSAL_INPUT_ADAPTER, "validate_json", count)
    assert await worker.drain_one() is None
    assert await worker.drain_one() is None
    # Each pass still revalidates its accepted descendant's exact proof;
    # only the additional historical scan decode is reused (formerly 4).
    assert len(decoded) == 2
    assert ledger.project() == before
    receipt = before.acceptance_decisions[-1].acceptance_event_ref
    lookup = ledger.lookup_event_commit
    monkeypatch.setattr(ledger, "lookup_event_commit",
                        lambda ref: None if ref == receipt else lookup(ref))
    with pytest.raises(ValueError, match="accepted_manifest_event_missing"):
        await worker.drain_one()
    assert ledger.project() == before


@pytest.mark.parametrize("registry", ("PAYLOAD_MODEL_REGISTRY", "CHANGE_TRANSITION_REGISTRY"))
@pytest.mark.parametrize("replace_table", (False, True))
def test_history_decode_bypasses_changed_codec_bindings(monkeypatch, registry, replace_table):
    from companion_daemon.world_v2 import proposal_envelope
    from companion_daemon.world_v2.proposal_audit_schemas import decode_recorded_proposal

    ledger = WorldLedger.in_memory(world_id=WORLD)
    _record_decision(ledger)
    audit = ledger.project().proposal_audits[0]
    original = decode_recorded_proposal(audit)
    table = getattr(proposal_envelope, registry)
    with monkeypatch.context() as changed:
        if replace_table:
            changed.setattr(proposal_envelope, registry,
                            {k: v for k, v in table.items() if k != "interaction_act"})
        else:
            changed.delitem(table, "interaction_act")
        for _ in range(2):
            with pytest.raises(ValueError):
                decode_recorded_proposal(audit)
    assert decode_recorded_proposal(audit) is original


def test_history_decode_rechecks_changed_bytes_and_does_not_share_mutable_payloads():
    from companion_daemon.world_v2.proposal_audit_schemas import decode_recorded_proposal

    ledger = WorldLedger.in_memory(world_id=WORLD)
    _record_decision(ledger)
    audit = ledger.project().proposal_audits[0]
    proposal = decode_recorded_proposal(audit)
    payload = proposal.proposed_changes[0].payload.value()
    payload.clear()
    assert decode_recorded_proposal(audit).proposed_changes[0].payload.value()
    changed = audit.model_copy(update={"proposal_json": "{}"})
    for _ in range(2):
        with pytest.raises(ValueError):
            decode_recorded_proposal(changed)


@pytest.mark.asyncio
async def test_cold_restart_skips_already_accepted_source_descendant(tmp_path) -> None:
    path = tmp_path / "interaction-act-worker.sqlite"
    first_issuer = AcceptedLedgerBatchIssuer()
    first = SQLiteWorldLedger(
        path=path,
        world_id=WORLD,
        accepted_batch_issuer=first_issuer,
    )
    source, _audit_cursor, _source_event = _record_decision(first)
    accepted = await _worker(ledger=first, issuer=first_issuer).drain_one()
    assert accepted is not None
    expected = first.project()
    first.close()

    second_issuer = AcceptedLedgerBatchIssuer()
    reopened = SQLiteWorldLedger(
        path=path,
        world_id=WORLD,
        accepted_batch_issuer=second_issuer,
    )
    try:
        replay = await _worker(ledger=reopened, issuer=second_issuer).drain_one()

        assert replay is None
        assert reopened.project() == expected
        assert reopened.rebuild() == expected
        assert len(reopened.project().interaction_acts) == 1
        assert reopened.project().interaction_act_proposals == ()
        assert source.proposal_id == accepted.source_proposal_id
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_cold_restart_accepts_pending_compiled_proposal_once(tmp_path) -> None:
    path = tmp_path / "interaction-act-worker-pending.sqlite"
    first_issuer = AcceptedLedgerBatchIssuer()
    first = SQLiteWorldLedger(
        path=path,
        world_id=WORLD,
        accepted_batch_issuer=first_issuer,
    )
    source, audit_cursor, _source_event = _record_decision(first)
    compiled = InteractionActProposalCompiler(ledger=first).record_rebased(
        world_id=WORLD,
        audit_cursor=audit_cursor,
        current_cursor=_cursor(first),
        proposal_id=source.proposal_id,
    )
    assert compiled.status == "candidate_recorded"
    assert len(first.project().interaction_act_proposals) == 1
    first.close()

    second_issuer = AcceptedLedgerBatchIssuer()
    reopened = SQLiteWorldLedger(
        path=path,
        world_id=WORLD,
        accepted_batch_issuer=second_issuer,
    )
    try:
        recovered = await _worker(
            ledger=reopened,
            issuer=second_issuer,
        ).drain_one()
        replay = await _worker(
            ledger=reopened,
            issuer=second_issuer,
        ).drain_one()

        assert recovered is not None and recovered.status == "accepted"
        assert replay is None
        projection = reopened.project()
        assert len(projection.interaction_acts) == 1
        assert projection.interaction_act_proposals == ()
        assert reopened.rebuild() == projection
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_cold_restart_rebases_pending_proposal_after_deliberation_only_commit(
    tmp_path,
) -> None:
    path = tmp_path / "interaction-act-worker-pending-rebased.sqlite"
    first_issuer = AcceptedLedgerBatchIssuer()
    first = SQLiteWorldLedger(
        path=path,
        world_id=WORLD,
        accepted_batch_issuer=first_issuer,
    )
    source, audit_cursor, _source_event = _record_decision(first)
    compiled = InteractionActProposalCompiler(ledger=first).record_rebased(
        world_id=WORLD,
        audit_cursor=audit_cursor,
        current_cursor=_cursor(first),
        proposal_id=source.proposal_id,
    )
    assert compiled.status == "candidate_recorded"
    pending_cursor = _cursor(first)
    deliberation_only = first.commit(
        (
            WorldEvent.from_payload(
                schema_version="world-v2.1",
                event_id="event:interaction-act:unrelated-deliberation",
                world_id=WORLD,
                event_type="ProposalRecorded",
                logical_time=NOW,
                created_at=NOW,
                actor=COMPANION,
                source="test:unrelated-deliberation",
                trace_id="trace:interaction-act-compiler",
                causation_id="event:unrelated-deliberation-trigger",
                correlation_id="qq:primary:message:unrelated",
                idempotency_key="interaction-act:unrelated-deliberation",
                payload={"proposal_id": "proposal:unrelated-deliberation"},
            ),
        ),
        expected_world_revision=pending_cursor.world_revision,
        expected_deliberation_revision=pending_cursor.deliberation_revision,
    )
    assert deliberation_only.world_revision == pending_cursor.world_revision
    assert deliberation_only.deliberation_revision == pending_cursor.deliberation_revision + 1
    assert len(first.project().interaction_act_proposals) == 1
    first.close()

    second_issuer = AcceptedLedgerBatchIssuer()
    reopened = SQLiteWorldLedger(
        path=path,
        world_id=WORLD,
        accepted_batch_issuer=second_issuer,
    )
    try:
        recovered = await _worker(
            ledger=reopened,
            issuer=second_issuer,
        ).drain_one()
        replay = await _worker(
            ledger=reopened,
            issuer=second_issuer,
        ).drain_one()

        assert recovered is not None and recovered.status == "accepted"
        assert replay is None
        projection = reopened.project()
        assert len(projection.interaction_acts) == 1
        assert len(projection.interaction_act_transitions) == 1
        assert projection.interaction_act_proposals == ()
        assert reopened.rebuild() == projection
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_role_omission_leaves_interaction_act_worker_idle() -> None:
    issuer = AcceptedLedgerBatchIssuer()
    ledger = WorldLedger.in_memory(world_id=WORLD, accepted_batch_issuer=issuer)
    _record_decision(ledger, include_act=False)
    before = ledger.project()

    result = await _worker(ledger=ledger, issuer=issuer).drain_one()

    assert result is None
    assert ledger.project() == before
    assert ledger.project().interaction_acts == ()
    assert ledger.project().interaction_act_proposals == ()
