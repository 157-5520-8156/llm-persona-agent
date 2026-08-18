"""Exact authority coordinates for one effect-free audited change terminal."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from .proposal_audit_schemas import ProposalAuditProjection
from .proposal_envelope import (
    DecisionProposal,
    RelationshipCommitmentPayload,
    TypedChange,
    validate_proposal_envelope,
)
from .interaction_act_identity import interaction_act_overlapping_occurrence_count
from .relationship_reducers import (
    RELATIONSHIP_COMMITMENT_STAGE_TRANSITIONS,
    relationship_primary_id,
    relationship_state_policy_is_readable,
)
from .schemas import (
    ExpressionBeatProjection,
    ExpressionPlanProjection,
    RelationshipStateProjection,
    StoredMessagePayloadProjection,
)


AUDITED_CHANGE_TERMINAL_ADVISORY_KIND = "typed_change_terminal"
RELATIONSHIP_COMMITMENT_TERMINAL_REASON = (
    "relationship_proposal_compiler.commitment_stage_transition_not_installed"
)
RELATIONSHIP_COMMITMENT_POLICY_UNINSTALLED_REASON = (
    "relationship_proposal_compiler.relationship_state_policy_uninstalled"
)
RELATIONSHIP_COMMITMENT_STATE_IDENTITY_REASON = (
    "relationship_proposal_compiler.relationship_state_identity_invalid"
)
RELATIONSHIP_COMMITMENT_VISIBLE_SPAN_REASON = (
    "relationship_proposal_compiler.commitment_visible_span_not_exact"
)
RELATIONSHIP_COMMITMENT_TERMINAL_REASONS = frozenset(
    {
        RELATIONSHIP_COMMITMENT_TERMINAL_REASON,
        RELATIONSHIP_COMMITMENT_POLICY_UNINSTALLED_REASON,
        RELATIONSHIP_COMMITMENT_STATE_IDENTITY_REASON,
        RELATIONSHIP_COMMITMENT_VISIBLE_SPAN_REASON,
    }
)
AuditedChangeTerminalStatus = Literal["rejected", "stale"]


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def terminal_relationship_commitment_payload(
    change: TypedChange,
) -> RelationshipCommitmentPayload:
    """Resolve the sole typed-change family owned by this terminal contract."""

    if (
        change.kind != "relationship_commitment"
        or change.transition != "commit"
        or change.payload.payload_schema != "relationship_commitment.v1"
    ):
        raise ValueError(
            "audited change terminal requires one relationship commitment"
        )
    return RelationshipCommitmentPayload.model_validate(
        change.payload.value(),
        strict=True,
    )


def _payload_text_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _commitment_visible_span_exact_matches(
    *,
    visible_text_span: str,
    source_proposal_id: str,
    expression_plans: tuple[ExpressionPlanProjection, ...],
    expression_beats: tuple[ExpressionBeatProjection, ...],
    stored_message_payloads: tuple[StoredMessagePayloadProjection, ...],
) -> tuple[int, int]:
    """Count delivered expression payloads and how many copy the span exactly once.

    Independently reproduces the compiler's span gate: a completed plan, a
    settled beat, and one stored payload whose text hash matches and whose
    exact overlapping occurrence count is 1.  The first return is the number
    of such delivered payloads that can be inspected; the second is how many
    of them carry the authored span exactly once.
    """

    inspectable = 0
    exact = 0
    for plan in expression_plans:
        if plan.proposal_id != source_proposal_id or plan.state != "completed":
            continue
        for beat in expression_beats:
            if (
                beat.proposal_id != source_proposal_id
                or beat.plan_id != plan.plan_id
                or beat.acceptance_id != plan.acceptance_id
                or beat.state != "settled"
            ):
                continue
            stored = tuple(
                item
                for item in stored_message_payloads
                if item.proposal_id == source_proposal_id
                and item.acceptance_id == plan.acceptance_id
                and item.payload_ref == beat.payload_ref
                and item.payload_hash == beat.payload_hash
            )
            if len(stored) != 1:
                continue
            payload = stored[0]
            if payload.payload_hash != _payload_text_hash(payload.text):
                continue
            inspectable += 1
            if (
                interaction_act_overlapping_occurrence_count(
                    source_text=payload.text,
                    selected_text=visible_text_span,
                )
                == 1
            ):
                exact += 1
    return inspectable, exact


def validate_relationship_commitment_terminal_state(
    *,
    change: TypedChange,
    relationship_states: tuple[RelationshipStateProjection, ...],
    reason_code: str,
    source_proposal_id: str | None = None,
    expression_plans: tuple[ExpressionPlanProjection, ...] = (),
    expression_beats: tuple[ExpressionBeatProjection, ...] = (),
    stored_message_payloads: tuple[StoredMessagePayloadProjection, ...] = (),
) -> None:
    """Re-prove the named compiler failure from current projection slices."""

    authored = terminal_relationship_commitment_payload(change)
    if reason_code == RELATIONSHIP_COMMITMENT_VISIBLE_SPAN_REASON:
        if not source_proposal_id:
            raise ValueError(
                "audited change terminal visible span proof requires the source proposal"
            )
        inspectable, exact = _commitment_visible_span_exact_matches(
            visible_text_span=authored.visible_text_span,
            source_proposal_id=source_proposal_id,
            expression_plans=expression_plans,
            expression_beats=expression_beats,
            stored_message_payloads=stored_message_payloads,
        )
        if inspectable < 1:
            raise ValueError(
                "audited change terminal visible span has no delivered expression"
            )
        if exact != 0:
            raise ValueError("audited change terminal visible span is exact")
        return

    matches = tuple(
        item
        for item in relationship_states
        if item.subject_ref == authored.subject_ref
    )
    if len(matches) > 1:
        raise ValueError("audited change terminal relationship state is ambiguous")
    if reason_code == RELATIONSHIP_COMMITMENT_STATE_IDENTITY_REASON:
        if not matches:
            raise ValueError(
                "audited change terminal relationship state identity is not present"
            )
        if matches[0].relationship_id == relationship_primary_id(
            subject_ref=authored.subject_ref
        ):
            raise ValueError(
                "audited change terminal relationship state identity is installed"
            )
        return
    if matches:
        current = matches[0]
        if current.relationship_id != relationship_primary_id(
            subject_ref=authored.subject_ref
        ):
            raise ValueError(
                "audited change terminal relationship state policy is not installed"
            )
        if not relationship_state_policy_is_readable(current):
            if reason_code != RELATIONSHIP_COMMITMENT_POLICY_UNINSTALLED_REASON:
                raise ValueError(
                    "audited change terminal relationship state policy is not installed"
                )
            return
        stage_before = current.stage
    else:
        stage_before = "stranger"
    if authored.target_stage in RELATIONSHIP_COMMITMENT_STAGE_TRANSITIONS.get(
        stage_before,
        frozenset(),
    ):
        raise ValueError(
            "audited change terminal relationship transition is installed"
        )
    if reason_code != RELATIONSHIP_COMMITMENT_TERMINAL_REASON:
        raise ValueError("audited change terminal reason is not installed")


def audited_change_authority_fingerprint(
    *,
    audit: ProposalAuditProjection,
    change: TypedChange,
) -> str:
    """Bind a source audit and one complete typed change, not its envelope peers."""

    terminal_relationship_commitment_payload(change)
    return _digest(
        {
            "contract": "audited-typed-change-authority.1",
            "proposal_event_ref": audit.event_ref,
            "proposal_event_payload_hash": audit.event_payload_hash,
            "change": change.model_dump(mode="json"),
        }
    )


def audited_change_terminal_proposal_id(
    *,
    audit: ProposalAuditProjection,
    change: TypedChange,
) -> str:
    return "proposal:audited-typed-change:" + audited_change_authority_fingerprint(
        audit=audit,
        change=change,
    )


def audited_change_terminal_event_id(
    *,
    audit: ProposalAuditProjection,
    change: TypedChange,
) -> str:
    return "event:audited-typed-change-terminal:" + _digest(
        {
            "contract": "audited-typed-change-terminal-event.1",
            "proposal_event_ref": audit.event_ref,
            "derived_proposal_id": audited_change_terminal_proposal_id(
                audit=audit,
                change=change,
            ),
        }
    )


def audited_change_terminal_payload(
    *,
    audit: ProposalAuditProjection,
    change: TypedChange,
    status: AuditedChangeTerminalStatus,
    reason_code: str = RELATIONSHIP_COMMITMENT_TERMINAL_REASON,
) -> dict[str, str]:
    if reason_code not in RELATIONSHIP_COMMITMENT_TERMINAL_REASONS:
        raise ValueError("audited change terminal reason is not installed")
    return {
        "proposal_id": audited_change_terminal_proposal_id(
            audit=audit,
            change=change,
        ),
        "source_event_ref": audit.event_ref,
        "advisory_kind": AUDITED_CHANGE_TERMINAL_ADVISORY_KIND,
        "stage": status,
        "reason_code": reason_code,
        "failure_fingerprint": audited_change_authority_fingerprint(
            audit=audit,
            change=change,
        ),
    }


def validate_audited_change_terminal_payload(
    *,
    payload: dict[str, object],
    audit: ProposalAuditProjection,
    current_world_revision: int,
) -> TypedChange:
    """Re-resolve exactly one typed change from immutable ProposalAudit bytes."""

    if payload.get("reason_code") not in RELATIONSHIP_COMMITMENT_TERMINAL_REASONS:
        raise ValueError("audited change terminal reason is not installed")
    if (
        payload.get("advisory_kind") != AUDITED_CHANGE_TERMINAL_ADVISORY_KIND
        or payload.get("source_event_ref") != audit.event_ref
        or payload.get("stage") not in {"rejected", "stale"}
    ):
        raise ValueError("audited change terminal coordinates are invalid")
    try:
        proposal = validate_proposal_envelope(json.loads(audit.proposal_json))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("audited change terminal source proposal is invalid") from exc
    if not isinstance(proposal, DecisionProposal):
        raise ValueError("audited change terminal source is not a decision proposal")
    matches = tuple(
        change
        for change in proposal.proposed_changes
        if change.kind == "relationship_commitment"
        and change.transition == "commit"
        and payload.get("proposal_id")
        == audited_change_terminal_proposal_id(audit=audit, change=change)
        and payload.get("failure_fingerprint")
        == audited_change_authority_fingerprint(audit=audit, change=change)
    )
    if len(matches) != 1:
        raise ValueError("audited change terminal does not bind one exact typed change")
    status = payload["stage"]
    if status == "rejected" and audit.evaluated_world_revision != current_world_revision:
        raise ValueError("rejected audited change must evaluate the current world")
    if status == "stale" and audit.evaluated_world_revision >= current_world_revision:
        raise ValueError("stale audited change must evaluate an older world revision")
    return matches[0]


__all__ = [
    "AUDITED_CHANGE_TERMINAL_ADVISORY_KIND",
    "AuditedChangeTerminalStatus",
    "RELATIONSHIP_COMMITMENT_POLICY_UNINSTALLED_REASON",
    "RELATIONSHIP_COMMITMENT_STATE_IDENTITY_REASON",
    "RELATIONSHIP_COMMITMENT_TERMINAL_REASON",
    "RELATIONSHIP_COMMITMENT_TERMINAL_REASONS",
    "RELATIONSHIP_COMMITMENT_VISIBLE_SPAN_REASON",
    "audited_change_authority_fingerprint",
    "audited_change_terminal_event_id",
    "audited_change_terminal_payload",
    "audited_change_terminal_proposal_id",
    "terminal_relationship_commitment_payload",
    "validate_relationship_commitment_terminal_state",
    "validate_audited_change_terminal_payload",
]
