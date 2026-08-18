from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.audited_change_terminal import (
    AUDITED_CHANGE_TERMINAL_ADVISORY_KIND,
    RELATIONSHIP_COMMITMENT_POLICY_UNINSTALLED_REASON,
    RELATIONSHIP_COMMITMENT_STATE_IDENTITY_REASON,
    RELATIONSHIP_COMMITMENT_TERMINAL_REASON,
    RELATIONSHIP_COMMITMENT_TERMINAL_REASONS,
    RELATIONSHIP_COMMITMENT_VISIBLE_SPAN_REASON,
    audited_change_authority_fingerprint,
    audited_change_terminal_event_id,
    audited_change_terminal_proposal_id,
    validate_relationship_commitment_terminal_state,
)
from companion_daemon.world_v2.character_interior.inbound_author import (
    _InboundCharacterAuthor,
)
from companion_daemon.world_v2.errors import ConcurrencyConflict, IdempotencyConflict
from companion_daemon.world_v2.event_identity import domain_idempotency_key
from companion_daemon.world_v2.ledger import WorldLedger
from companion_daemon.world_v2.relationship_commitment_worker import (
    RelationshipCommitmentWorker,
    RelationshipCommitmentWorkResult,
)
from companion_daemon.world_v2.relationship_proposal_compiler import (
    RelationshipProposalCompiler,
    RelationshipProposalCompilerError,
)
from companion_daemon.world_v2.relationship_reducers import (
    RELATIONSHIP_POLICY_DIGEST,
    relationship_primary_id,
)
from companion_daemon.world_v2.reducers import reduce_event
from companion_daemon.world_v2.runtime import WorldRuntime
from companion_daemon.world_v2.schemas import CommitResult, RelationshipStateProjection, WorldEvent
from companion_daemon.world_v2.world_turn_runtime import InboundTurn

from test_appraisal_authority import WORLD_ID
from test_interaction_act_proposal_compiler import (
    WORLD as INTERACTION_ACT_WORLD,
    _record_decision as _record_interaction_act_decision,
)
from test_relationship_commitment_compiler import (
    _compiler_fixture,
    _reducer_state_with_delivery,
)
from test_production_turn_application import (
    NOW as PRODUCTION_NOW,
    _build_application,
    _config,
    _ConversationTargetIdentities,
    _DeliveredTransport,
    _fixture_observation_ref,
    _Router,
)


class _Acceptance:
    def __init__(self, ledger) -> None:
        self.ledger = ledger
        self.pinned: tuple[object, str] | None = None
        self.accepted = 0

    def pin_proposal(self, *, cursor, proposal_id: str):
        self.pinned = (cursor, proposal_id)
        return SimpleNamespace(cursor=cursor, proposal_id=proposal_id)

    def accept_runtime_owned(self, *, handle, actor: str, source: str) -> CommitResult:
        del actor, source
        self.accepted += 1
        return CommitResult(
            world_revision=handle.cursor.world_revision + 1,
            deliberation_revision=handle.cursor.deliberation_revision,
            ledger_sequence=handle.cursor.ledger_sequence + 2,
            event_ids=(
                "event:relationship-commitment-acceptance",
                "event:relationship-commitment-mutation",
            ),
        )


def _crafted_terminal_event(
    *,
    ledger,
    audit,
    change,
    reason_code: str,
    stage: str = "rejected",
) -> WorldEvent:
    payload = {
        "proposal_id": audited_change_terminal_proposal_id(
            audit=audit,
            change=change,
        ),
        "source_event_ref": audit.event_ref,
        "advisory_kind": AUDITED_CHANGE_TERMINAL_ADVISORY_KIND,
        "stage": stage,
        "reason_code": reason_code,
        "failure_fingerprint": audited_change_authority_fingerprint(
            audit=audit,
            change=change,
        ),
    }
    identity = domain_idempotency_key(
        event_type="AdvisoryAcceptanceRejected",
        world_id=ledger.world_id,
        payload=payload,
    )
    assert identity is not None
    source_event = ledger.lookup_event_commit(audit.event_ref)
    assert source_event is not None
    return WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id=audited_change_terminal_event_id(audit=audit, change=change),
        world_id=ledger.world_id,
        event_type="AdvisoryAcceptanceRejected",
        logical_time=source_event[0].logical_time,
        created_at=source_event[0].created_at,
        actor="worker:relationship-commitment",
        source="test:crafted-terminal",
        trace_id=source_event[0].trace_id,
        causation_id=audit.event_ref,
        correlation_id=source_event[0].correlation_id,
        idempotency_key=identity,
        payload=payload,
    )


def test_public_reducer_rejects_arbitrary_typed_change_terminal_reason() -> None:
    wrapped, proposal, audit_cursor, _current_cursor = _compiler_fixture(
        target_stage="close_friend"
    )
    ledger = wrapped._delegate
    audit = next(
        item
        for item in ledger.project_at(audit_cursor).proposal_audits
        if item.proposal_id == proposal.proposal_id
    )
    change = next(
        item
        for item in proposal.proposed_changes
        if item.kind == "relationship_commitment"
    )
    event = _crafted_terminal_event(
        ledger=ledger,
        audit=audit,
        change=change,
        reason_code="crafted_arbitrary_reason",
    )

    with pytest.raises(ValueError, match="reason"):
        ledger.commit_at_cursor((event,), expected_cursor=audit_cursor)


def test_public_reducer_rejects_terminal_for_installed_relationship_transition() -> None:
    wrapped, proposal, audit_cursor, _current_cursor = _compiler_fixture(
        target_stage="friend"
    )
    ledger = wrapped._delegate
    audit = next(
        item
        for item in ledger.project_at(audit_cursor).proposal_audits
        if item.proposal_id == proposal.proposal_id
    )
    change = next(
        item
        for item in proposal.proposed_changes
        if item.kind == "relationship_commitment"
    )
    event = _crafted_terminal_event(
        ledger=ledger,
        audit=audit,
        change=change,
        reason_code=RELATIONSHIP_COMMITMENT_TERMINAL_REASON,
    )

    with pytest.raises(ValueError, match="transition is installed"):
        ledger.commit_at_cursor((event,), expected_cursor=audit_cursor)


def test_terminal_identity_rejects_non_relationship_typed_change() -> None:
    ledger = WorldLedger.in_memory(world_id=INTERACTION_ACT_WORLD)
    proposal, audit_cursor, _source_event = _record_interaction_act_decision(ledger)
    audit = next(
        item
        for item in ledger.project_at(audit_cursor).proposal_audits
        if item.proposal_id == proposal.proposal_id
    )
    change = next(
        item for item in proposal.proposed_changes if item.kind == "interaction_act"
    )

    with pytest.raises(ValueError, match="relationship commitment"):
        audited_change_authority_fingerprint(audit=audit, change=change)


@pytest.mark.asyncio
async def test_background_commitment_waits_for_delivered_receipt_then_accepts_typed_output() -> None:
    ledger, proposal, _audit_cursor, _current_cursor = _compiler_fixture()
    acceptance = _Acceptance(ledger)
    worker = RelationshipCommitmentWorker(
        ledger=ledger,
        compiler=RelationshipProposalCompiler(ledger=ledger),
        acceptance=acceptance,
        actor="worker:relationship-commitment",
    )

    ledger.without_delivered_action()
    assert await worker.drain_one() is None
    assert ledger.recorded == ()
    assert acceptance.accepted == 0

    ready, _proposal, _audit_cursor, _current_cursor = _compiler_fixture()
    ready_acceptance = _Acceptance(ready)
    ready_worker = RelationshipCommitmentWorker(
        ledger=ready,
        compiler=RelationshipProposalCompiler(ledger=ready),
        acceptance=ready_acceptance,
        actor="worker:relationship-commitment",
    )
    result = await ready_worker.drain_one()

    assert result is not None
    assert result.status == "accepted"
    assert result.source_proposal_id == proposal.proposal_id
    assert len(ready.recorded) == 1
    assert ready_acceptance.accepted == 1


class _RuntimeCommitmentWorker:
    def __init__(self, ledger) -> None:
        self.ledger = ledger
        self.calls = 0

    async def drain_one(self) -> RelationshipCommitmentWorkResult:
        self.calls += 1
        return RelationshipCommitmentWorkResult(
            status="accepted",
            source_proposal_id="proposal:relationship-commitment",
            typed_proposal_id="proposal:relationship-commitment:compiled",
            compile_commit=CommitResult(
                world_revision=1,
                deliberation_revision=1,
                ledger_sequence=2,
                event_ids=("event:proposal:relationship-commitment",),
            ),
            acceptance_commit=CommitResult(
                world_revision=2,
                deliberation_revision=1,
                ledger_sequence=4,
                event_ids=(
                    "event:relationship-commitment-acceptance",
                    "event:relationship-commitment-mutation",
                ),
            ),
        )


class _RuntimeInteractionActWorker:
    def __init__(self, ledger) -> None:
        self.ledger = ledger
        self.calls = 0
        self.result = SimpleNamespace(status="accepted", lane="interaction_act")

    async def drain_one(self):
        self.calls += 1
        return self.result


class _RuntimeCharacterInterior:
    def __init__(self, ledger) -> None:
        self.ledger = ledger
        self.result = SimpleNamespace(status="authorized", lane="proactive")

    def _is_bound_to(self, ledger) -> bool:
        return ledger is self.ledger

    async def _drain_reconsideration_once(self):
        return None

    async def _drain_proactive_once(self):
        return self.result


class _FriendThenUnifiedSemanticModel:
    """First establish friendship, then author two independent typed lanes."""

    model = "test-friend-then-unified-semantic"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, *, temperature: float = 0.8):  # type: ignore[no-untyped-def]
        del temperature
        self.calls += 1
        observation_ref = _fixture_observation_ref(messages)
        repeated_friendship = self.calls > 1
        friendship_span = "你还是我朋友" if repeated_friendship else "你是我朋友了"
        text = (
            "好呀，你还是我朋友。下次见面我把这本书给你。"
            if repeated_friendship
            else "好呀，那说好了，你是我朋友了。"
        )
        appraisal: dict[str, object] = {
            "appraise": False,
            "affect": "no_change",
            "relationship_commitment": {
                "target_stage": "friend",
                "commitment_code": "mutual_friendship",
                "persistence": "durable",
                "visible_text_span": friendship_span,
            },
            "behavior_tendency": "明确回应",
            "stance": "接纳",
            "display_strategy": "直接表达",
            "brief_rationale": "她选择把自己的关系理解和后续安排明确说出来。",
            "confidence": 8200,
        }
        if repeated_friendship:
            appraisal["interaction_act"] = {
                "operation": "declare",
                "status_code": "等待后续交接",
                "source_scope": "delivered_expression",
                "source_text_span": "下次见面我把这本书给你",
                "interaction_act_ref": None,
                "act_kind": "约定后续交接",
                "subject_role": "self",
                "counterparty_roles": ["current_counterpart"],
                "object_ref": None,
                "object_label": "这本书",
            }
        return json.dumps(
            {
                "appraisal_draft": appraisal,
                "expression_draft": {
                    "private_turn_state": {
                        "contract": "private-turn-state.1",
                        "inner_state_summary": "我愿意明确关系，也愿意留下后续交接安排。",
                        "attended_source_refs": [observation_ref],
                    },
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": text}],
                    "stance": "认真",
                    "brief_rationale": "她选择明确说出自己的关系理解和后续安排。",
                    "confidence": 8200,
                    "world_claims": [],
                },
            },
            ensure_ascii=False,
        )


@pytest.mark.asyncio
async def test_terminal_relationship_change_does_not_close_unified_interaction_act(
    tmp_path: Path,
) -> None:
    path = tmp_path / "audited-change-terminal-granularity.sqlite"
    model = _FriendThenUnifiedSemanticModel()
    config = replace(
        _config(),
        reply_target="conversation:test:c2c:user.1",
        counterpart_actor_ref="user:user.1",
    )
    app = _build_application(
        path=path,
        config=config,
        identities=_ConversationTargetIdentities(),
        router=_Router(),
        inbound_author=_InboundCharacterAuthor(flash_model=model),
        transport=_DeliveredTransport(received_at=PRODUCTION_NOW),
        now=PRODUCTION_NOW,
    )
    try:
        for message_id, text in (
            ("friendship:opening", "我们可以成为好朋友吗？"),
            ("friendship:repeat-with-act", "那下次见面把书带给我吧。"),
        ):
            response = await app.respond(
                InboundTurn(
                    platform="test",
                    platform_user_id="user.1",
                    platform_message_id=message_id,
                    text=text,
                    observed_at=PRODUCTION_NOW,
                    trace_id=f"trace:{message_id}",
                )
            )
            assert response.status == "action_authorized"
            delivery = await app.drain_actions_once()
            assert delivery is not None and delivery.status == "settled"

        first_commitment = await app.drain_background_once()
        assert first_commitment is not None and first_commitment.status == "accepted"
        settlement = await app.drain_background_once()
        assert settlement is not None

        assert settlement.status == "stale"
        unified_source_proposal_id = settlement.source_proposal_id
        projection = app._ledger.project()  # noqa: SLF001 - public worker integration seam
        assert len(projection.relationship_commitments) == 1
        assert all(
            decision.proposal_id != unified_source_proposal_id
            for decision in projection.acceptance_decisions
        )
        assert len(settlement.acceptance_commit.event_ids) == 1
        terminal_event_id = settlement.acceptance_commit.event_ids[0]
        terminal = app._ledger.lookup_event_commit(terminal_event_id)  # noqa: SLF001
        assert terminal is not None
        terminal_payload = terminal[0].payload()
        assert terminal_payload["advisory_kind"] == "typed_change_terminal"
        assert terminal_payload["proposal_id"] != unified_source_proposal_id
    finally:
        app.close()

    reopened = _build_application(
        path=path,
        config=config,
        identities=_ConversationTargetIdentities(),
        router=_Router(),
        inbound_author=_InboundCharacterAuthor(
            flash_model=_FriendThenUnifiedSemanticModel()
        ),
        transport=_DeliveredTransport(received_at=PRODUCTION_NOW),
        now=PRODUCTION_NOW,
    )
    try:
        interaction = await reopened.drain_background_once()

        assert interaction is not None and interaction.status == "accepted"
        assert interaction.source_proposal_id == unified_source_proposal_id
        settled = reopened._ledger.project()  # noqa: SLF001 - cold replay evidence
        assert len(settled.relationship_commitments) == 1
        assert len(settled.interaction_acts) == 1
        replayed_terminal = reopened._ledger.lookup_event_commit(terminal_event_id)  # noqa: SLF001
        assert replayed_terminal == terminal
        assert reopened._ledger.rebuild() == settled  # noqa: SLF001
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_world_runtime_schedules_relationship_commitment_only_in_background() -> None:
    ledger, _proposal, _audit_cursor, _current_cursor = _compiler_fixture()
    worker = _RuntimeCommitmentWorker(ledger)
    runtime = WorldRuntime(
        world_id=WORLD_ID,
        ledger=ledger,
        relationship_commitment_worker=worker,
    )

    result = await runtime.drain_background_once()

    assert result is not None
    assert result.status == "accepted"
    assert worker.calls == 1


@pytest.mark.asyncio
async def test_world_runtime_prioritizes_eligible_proactive_over_pending_social_workers() -> None:
    ledger, _proposal, _audit_cursor, _current_cursor = _compiler_fixture()
    commitment_worker = _RuntimeCommitmentWorker(ledger)
    interaction_act_worker = _RuntimeInteractionActWorker(ledger)
    interior = _RuntimeCharacterInterior(ledger)
    runtime = WorldRuntime(
        world_id=WORLD_ID,
        ledger=ledger,
        relationship_commitment_worker=commitment_worker,
        interaction_act_worker=interaction_act_worker,  # type: ignore[arg-type]
        character_interior=interior,  # type: ignore[arg-type]
    )

    first = await runtime.drain_background_once()

    assert first is interior.result
    assert commitment_worker.calls == 0
    assert interaction_act_worker.calls == 0


def _persist_wrapper_commits(ledger) -> None:
    original = ledger.commit_at_cursor

    def commit_at_cursor(events, *, expected_cursor, commit_id=None):
        result = original(
            events, expected_cursor=expected_cursor, commit_id=commit_id
        )
        for event in events:
            ledger._proof_commits[event.event_id] = (event, result)
        return result

    ledger.commit_at_cursor = commit_at_cursor


def _carry_relationship_state(
    ledger,
    *,
    policy_digest: str,
    relationship_id: str | None = None,
) -> None:
    state = RelationshipStateProjection(
        relationship_id=relationship_id
        or relationship_primary_id(subject_ref="user:test"),
        subject_ref="user:test",
        entity_revision=1,
        stage="stranger",
        policy_digest=policy_digest,
    )
    ledger._current = ledger._current.model_copy(
        update={"relationship_states": (state,)}
    )


def test_terminal_validator_accepts_foreign_digest_only_for_policy_uninstalled() -> None:
    _wrapped, proposal, _audit_cursor, _current_cursor = _compiler_fixture()
    change = next(
        item
        for item in proposal.proposed_changes
        if item.kind == "relationship_commitment"
    )
    foreign = RelationshipStateProjection(
        relationship_id=relationship_primary_id(subject_ref="user:test"),
        subject_ref="user:test",
        entity_revision=1,
        policy_digest="0" * 64,
    )

    validate_relationship_commitment_terminal_state(
        change=change,
        relationship_states=(foreign,),
        reason_code=RELATIONSHIP_COMMITMENT_POLICY_UNINSTALLED_REASON,
    )
    with pytest.raises(ValueError, match="policy is not installed"):
        validate_relationship_commitment_terminal_state(
            change=change,
            relationship_states=(foreign,),
            reason_code=RELATIONSHIP_COMMITMENT_TERMINAL_REASON,
        )


def test_terminal_validator_reads_retired_digest_for_transition_terminals() -> None:
    _wrapped, proposal, _audit_cursor, _current_cursor = _compiler_fixture(
        target_stage="close_friend"
    )
    change = next(
        item
        for item in proposal.proposed_changes
        if item.kind == "relationship_commitment"
    )
    retired = RelationshipStateProjection(
        relationship_id=relationship_primary_id(subject_ref="user:test"),
        subject_ref="user:test",
        entity_revision=1,
        policy_digest=(
            "13bfa71dd9f8377b968714eb3d4f9a927e587832c92d2381c6ecc772071deede"
        ),
    )

    validate_relationship_commitment_terminal_state(
        change=change,
        relationship_states=(retired,),
        reason_code=RELATIONSHIP_COMMITMENT_TERMINAL_REASON,
    )


def test_public_reducer_rejects_policy_uninstalled_terminal_when_state_is_readable() -> None:
    wrapped, proposal, audit_cursor, _current_cursor = _compiler_fixture(
        target_stage="close_friend"
    )
    ledger = wrapped._delegate
    audit = next(
        item
        for item in ledger.project_at(audit_cursor).proposal_audits
        if item.proposal_id == proposal.proposal_id
    )
    change = next(
        item
        for item in proposal.proposed_changes
        if item.kind == "relationship_commitment"
    )
    event = _crafted_terminal_event(
        ledger=ledger,
        audit=audit,
        change=change,
        reason_code=RELATIONSHIP_COMMITMENT_POLICY_UNINSTALLED_REASON,
    )

    with pytest.raises(ValueError, match="reason"):
        ledger.commit_at_cursor((event,), expected_cursor=audit_cursor)


@pytest.mark.asyncio
async def test_uninstalled_policy_commitment_settles_terminal_without_raising() -> None:
    ledger, proposal, _audit_cursor, _current_cursor = _compiler_fixture()
    _carry_relationship_state(ledger, policy_digest="0" * 64)
    _persist_wrapper_commits(ledger)
    worker = RelationshipCommitmentWorker(
        ledger=ledger,
        compiler=RelationshipProposalCompiler(ledger=ledger),
        acceptance=_Acceptance(ledger),
        actor="worker:relationship-commitment",
    )

    result = await worker.drain_one()

    assert result is not None
    assert result.status in {"rejected", "stale"}
    assert result.reason_code == RELATIONSHIP_COMMITMENT_POLICY_UNINSTALLED_REASON
    assert result.source_proposal_id == proposal.proposal_id
    second = await worker.drain_one()
    assert second is None


@pytest.mark.asyncio
async def test_uninstalled_policy_commitment_does_not_starve_later_background_workers() -> None:
    ledger, _proposal, _audit_cursor, _current_cursor = _compiler_fixture()
    _carry_relationship_state(ledger, policy_digest="0" * 64)
    _persist_wrapper_commits(ledger)
    worker = RelationshipCommitmentWorker(
        ledger=ledger,
        compiler=RelationshipProposalCompiler(ledger=ledger),
        acceptance=_Acceptance(ledger),
        actor="worker:relationship-commitment",
    )
    interaction_act_worker = _RuntimeInteractionActWorker(ledger)
    runtime = WorldRuntime(
        world_id=WORLD_ID,
        ledger=ledger,
        relationship_commitment_worker=worker,
        interaction_act_worker=interaction_act_worker,  # type: ignore[arg-type]
    )

    first = await runtime.drain_background_once()

    assert first is not None
    assert first.status in {"rejected", "stale"}
    assert interaction_act_worker.calls == 0

    second = await runtime.drain_background_once()

    assert second is interaction_act_worker.result
    assert interaction_act_worker.calls == 1


def test_terminal_validator_accepts_visible_span_only_when_said_as_is_not_exact() -> None:
    inexact, inexact_proposal, _audit_cursor, _current_cursor = _compiler_fixture(
        visible_text_span="我们是朋友"
    )
    inexact_change = next(
        item
        for item in inexact_proposal.proposed_changes
        if item.kind == "relationship_commitment"
    )
    current = inexact._current
    validate_relationship_commitment_terminal_state(
        change=inexact_change,
        relationship_states=(),
        reason_code=RELATIONSHIP_COMMITMENT_VISIBLE_SPAN_REASON,
        source_proposal_id=inexact_proposal.proposal_id,
        expression_plans=current.expression_plans,
        expression_beats=current.expression_beats,
        stored_message_payloads=current.stored_message_payloads,
    )

    exact, exact_proposal, _audit_cursor, _current_cursor = _compiler_fixture()
    exact_change = next(
        item
        for item in exact_proposal.proposed_changes
        if item.kind == "relationship_commitment"
    )
    exact_current = exact._current
    with pytest.raises(ValueError, match="visible span is exact"):
        validate_relationship_commitment_terminal_state(
            change=exact_change,
            relationship_states=(),
            reason_code=RELATIONSHIP_COMMITMENT_VISIBLE_SPAN_REASON,
            source_proposal_id=exact_proposal.proposal_id,
            expression_plans=exact_current.expression_plans,
            expression_beats=exact_current.expression_beats,
            stored_message_payloads=exact_current.stored_message_payloads,
        )


def test_terminal_validator_accepts_identity_invalid_only_when_primary_id_mismatches() -> None:
    _wrapped, proposal, _audit_cursor, _current_cursor = _compiler_fixture()
    change = next(
        item
        for item in proposal.proposed_changes
        if item.kind == "relationship_commitment"
    )
    mismatched = RelationshipStateProjection(
        relationship_id="relationship:forged-identity",
        subject_ref="user:test",
        entity_revision=1,
        policy_digest=RELATIONSHIP_POLICY_DIGEST,
    )
    validate_relationship_commitment_terminal_state(
        change=change,
        relationship_states=(mismatched,),
        reason_code=RELATIONSHIP_COMMITMENT_STATE_IDENTITY_REASON,
    )
    installed = mismatched.model_copy(
        update={"relationship_id": relationship_primary_id(subject_ref="user:test")}
    )
    with pytest.raises(ValueError, match="identity is installed"):
        validate_relationship_commitment_terminal_state(
            change=change,
            relationship_states=(installed,),
            reason_code=RELATIONSHIP_COMMITMENT_STATE_IDENTITY_REASON,
        )


def test_public_reducer_accepts_visible_span_terminal_when_said_as_is_not_exact() -> None:
    wrapped, proposal, audit_cursor, _current_cursor = _compiler_fixture(
        visible_text_span="我们是朋友"
    )
    ledger = wrapped._delegate
    audit = next(
        item
        for item in ledger.project_at(audit_cursor).proposal_audits
        if item.proposal_id == proposal.proposal_id
    )
    change = next(
        item
        for item in proposal.proposed_changes
        if item.kind == "relationship_commitment"
    )
    event = _crafted_terminal_event(
        ledger=ledger,
        audit=audit,
        change=change,
        reason_code=RELATIONSHIP_COMMITMENT_VISIBLE_SPAN_REASON,
        stage="stale",
    )

    reduce_event(_reducer_state_with_delivery(wrapped), event)


def test_public_reducer_rejects_visible_span_terminal_when_said_as_is_exact() -> None:
    wrapped, proposal, audit_cursor, _current_cursor = _compiler_fixture()
    ledger = wrapped._delegate
    audit = next(
        item
        for item in ledger.project_at(audit_cursor).proposal_audits
        if item.proposal_id == proposal.proposal_id
    )
    change = next(
        item
        for item in proposal.proposed_changes
        if item.kind == "relationship_commitment"
    )
    event = _crafted_terminal_event(
        ledger=ledger,
        audit=audit,
        change=change,
        reason_code=RELATIONSHIP_COMMITMENT_VISIBLE_SPAN_REASON,
        stage="stale",
    )

    with pytest.raises(ValueError, match="visible span is exact"):
        reduce_event(_reducer_state_with_delivery(wrapped), event)


def test_public_reducer_accepts_identity_invalid_terminal_when_primary_id_mismatches() -> None:
    wrapped, proposal, audit_cursor, _current_cursor = _compiler_fixture()
    ledger = wrapped._delegate
    audit = next(
        item
        for item in ledger.project_at(audit_cursor).proposal_audits
        if item.proposal_id == proposal.proposal_id
    )
    change = next(
        item
        for item in proposal.proposed_changes
        if item.kind == "relationship_commitment"
    )
    mismatched = RelationshipStateProjection(
        relationship_id="relationship:forged-identity",
        subject_ref="user:test",
        entity_revision=1,
        policy_digest=RELATIONSHIP_POLICY_DIGEST,
    )
    event = _crafted_terminal_event(
        ledger=ledger,
        audit=audit,
        change=change,
        reason_code=RELATIONSHIP_COMMITMENT_STATE_IDENTITY_REASON,
    )

    reduce_event(
        ledger._state.model_copy(update={"relationship_states": (mismatched,)}),
        event,
    )


def test_public_reducer_rejects_identity_invalid_terminal_when_primary_id_matches() -> None:
    wrapped, proposal, audit_cursor, _current_cursor = _compiler_fixture()
    ledger = wrapped._delegate
    audit = next(
        item
        for item in ledger.project_at(audit_cursor).proposal_audits
        if item.proposal_id == proposal.proposal_id
    )
    change = next(
        item
        for item in proposal.proposed_changes
        if item.kind == "relationship_commitment"
    )
    installed = RelationshipStateProjection(
        relationship_id=relationship_primary_id(subject_ref="user:test"),
        subject_ref="user:test",
        entity_revision=1,
        policy_digest=RELATIONSHIP_POLICY_DIGEST,
    )
    event = _crafted_terminal_event(
        ledger=ledger,
        audit=audit,
        change=change,
        reason_code=RELATIONSHIP_COMMITMENT_STATE_IDENTITY_REASON,
    )

    with pytest.raises(ValueError, match="identity is installed"):
        reduce_event(
            ledger._state.model_copy(update={"relationship_states": (installed,)}),
            event,
        )


@pytest.mark.asyncio
async def test_inexact_visible_span_commitment_settles_terminal_without_raising() -> None:
    ledger, proposal, _audit_cursor, _current_cursor = _compiler_fixture(
        visible_text_span="我们是朋友"
    )
    _persist_wrapper_commits(ledger)
    worker = RelationshipCommitmentWorker(
        ledger=ledger,
        compiler=RelationshipProposalCompiler(ledger=ledger),
        acceptance=_Acceptance(ledger),
        actor="worker:relationship-commitment",
    )

    result = await worker.drain_one()

    assert result is not None
    assert result.status in {"rejected", "stale"}
    assert result.reason_code == RELATIONSHIP_COMMITMENT_VISIBLE_SPAN_REASON
    assert result.source_proposal_id == proposal.proposal_id
    second = await worker.drain_one()
    assert second is None


@pytest.mark.asyncio
async def test_identity_invalid_commitment_settles_terminal_without_raising() -> None:
    ledger, proposal, _audit_cursor, _current_cursor = _compiler_fixture()
    _carry_relationship_state(
        ledger,
        policy_digest=RELATIONSHIP_POLICY_DIGEST,
        relationship_id="relationship:forged-identity",
    )
    _persist_wrapper_commits(ledger)
    worker = RelationshipCommitmentWorker(
        ledger=ledger,
        compiler=RelationshipProposalCompiler(ledger=ledger),
        acceptance=_Acceptance(ledger),
        actor="worker:relationship-commitment",
    )

    result = await worker.drain_one()

    assert result is not None
    assert result.status in {"rejected", "stale"}
    assert result.reason_code == RELATIONSHIP_COMMITMENT_STATE_IDENTITY_REASON
    assert result.source_proposal_id == proposal.proposal_id
    second = await worker.drain_one()
    assert second is None


class _ExplodingCommitmentWorker:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc
        self.calls = 0
        self.ledger = None

    async def drain_one(self):
        self.calls += 1
        raise self.exc


@pytest.mark.asyncio
async def test_unexpected_commitment_exception_does_not_abort_later_workers() -> None:
    ledger, _proposal, _audit_cursor, _current_cursor = _compiler_fixture()
    exploding = _ExplodingCommitmentWorker(RuntimeError("injected compiler crash"))
    exploding.ledger = ledger
    later = _RuntimeInteractionActWorker(ledger)
    runtime = WorldRuntime(
        world_id=WORLD_ID,
        ledger=ledger,
        relationship_commitment_worker=exploding,  # type: ignore[arg-type]
        interaction_act_worker=later,  # type: ignore[arg-type]
    )

    first = await runtime.drain_background_once()

    assert first is later.result
    assert exploding.calls == 1
    assert later.calls == 1

    second = await runtime.drain_background_once()

    assert exploding.calls == 2
    assert later.calls == 2
    assert second is later.result


@pytest.mark.asyncio
async def test_concurrency_conflict_still_returns_none_without_running_later_workers() -> None:
    ledger, _proposal, _audit_cursor, _current_cursor = _compiler_fixture()
    exploding = _ExplodingCommitmentWorker(ConcurrencyConflict("stale cursor"))
    exploding.ledger = ledger
    later = _RuntimeInteractionActWorker(ledger)
    runtime = WorldRuntime(
        world_id=WORLD_ID,
        ledger=ledger,
        relationship_commitment_worker=exploding,  # type: ignore[arg-type]
        interaction_act_worker=later,  # type: ignore[arg-type]
    )

    result = await runtime.drain_background_once()

    assert result is None
    assert exploding.calls == 1
    assert later.calls == 0


@pytest.mark.asyncio
async def test_idempotency_conflict_still_propagates_from_background_drain() -> None:
    ledger, _proposal, _audit_cursor, _current_cursor = _compiler_fixture()
    exploding = _ExplodingCommitmentWorker(IdempotencyConflict("duplicate identity"))
    exploding.ledger = ledger
    later = _RuntimeInteractionActWorker(ledger)
    runtime = WorldRuntime(
        world_id=WORLD_ID,
        ledger=ledger,
        relationship_commitment_worker=exploding,  # type: ignore[arg-type]
        interaction_act_worker=later,  # type: ignore[arg-type]
    )

    with pytest.raises(IdempotencyConflict, match="duplicate identity"):
        await runtime.drain_background_once()
    assert exploding.calls == 1
    assert later.calls == 0


@pytest.mark.asyncio
async def test_unexpected_exception_reraise_when_drain_would_be_idle() -> None:
    ledger, _proposal, _audit_cursor, _current_cursor = _compiler_fixture()
    exploding = _ExplodingCommitmentWorker(RuntimeError("injected compiler crash"))
    exploding.ledger = ledger
    runtime = WorldRuntime(
        world_id=WORLD_ID,
        ledger=ledger,
        relationship_commitment_worker=exploding,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="injected compiler crash"):
        await runtime.drain_background_once()
    assert exploding.calls == 1


@pytest.mark.asyncio
async def test_compiler_error_backs_off_without_starving_later_workers() -> None:
    ledger, _proposal, _audit_cursor, _current_cursor = _compiler_fixture()
    exploding = _ExplodingCommitmentWorker(
        RelationshipProposalCompilerError("commitment_payload_invalid")
    )
    exploding.ledger = ledger
    later = _RuntimeInteractionActWorker(ledger)
    runtime = WorldRuntime(
        world_id=WORLD_ID,
        ledger=ledger,
        relationship_commitment_worker=exploding,  # type: ignore[arg-type]
        interaction_act_worker=later,  # type: ignore[arg-type]
    )

    first = await runtime.drain_background_once()
    second = await runtime.drain_background_once()

    assert first is later.result
    assert second is later.result
    assert exploding.calls == 1
    assert later.calls == 2


def test_undelivered_commitment_is_not_a_terminal_reject() -> None:
    """She must actually send the sentence.  Missing delivery waits, it does not settle.

    ``commitment_expression_not_delivered`` is a hard host boundary.  Treating it
    as a typed terminal reject would burn the proposal before the beat can land.
    """

    delivered = "relationship_proposal_compiler.commitment_expression_not_delivered"
    assert delivered not in RELATIONSHIP_COMMITMENT_TERMINAL_REASONS
    assert RELATIONSHIP_COMMITMENT_TERMINAL_REASON in RELATIONSHIP_COMMITMENT_TERMINAL_REASONS
