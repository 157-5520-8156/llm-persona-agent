"""Read the role's exact authorized attempt, never infer it from a Plan label.

All readers are pinned to the start/resume's committed prefix, bounded by the
World Author's original prefix. Missing text or model lineage yields no usable
execution material. Receipt content needs its own installed reader; a terminal
state alone does not describe an action or grant one.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field, model_validator

from .schema_core import FrozenModel
from .world_consequence_contract import (
    WorldConsequenceExecutionBinding,
    derive_world_consequence_authority,
)


class WorldConsequenceAuthorizedIntention(FrozenModel):
    text: str = Field(min_length=1, max_length=4_000)
    content_ref: str = Field(min_length=1)
    content_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_event_ref: str = Field(min_length=1)
    proposal_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_result_event_ref: str = Field(min_length=1)
    model_result_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_result_ref: str = Field(min_length=1)
    epistemic_scope: Literal["authorized_intention_not_embedded_history_or_execution_success"] = (
        "authorized_intention_not_embedded_history_or_execution_success"
    )


class WorldConsequenceExecutionMaterial(FrozenModel):
    contract: Literal["world-consequence-execution-material.1"] = (
        "world-consequence-execution-material.1"
    )
    execution_binding: WorldConsequenceExecutionBinding
    status: Literal["available", "unavailable"]
    authorized_intention: WorldConsequenceAuthorizedIntention | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    unavailable_reason: Literal[
        "receipt_content_reader_not_installed", "source_snapshot_unavailable",
        "activity_intention_unavailable", "character_audit_unavailable", "withheld",
    ] | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def availability_has_one_meaning(self):
        if self.status == "available":
            if self.authorized_intention is None or self.unavailable_reason is not None:
                raise ValueError("available execution material requires exact readable intention")
        elif self.authorized_intention is not None or self.unavailable_reason is None:
            raise ValueError("unavailable execution material cannot grant a readable intention")
        return self


def _cursor(state):
    from .schemas import ProjectionCursor

    return ProjectionCursor(
        world_revision=state.world_revision, deliberation_revision=state.deliberation_revision,
        ledger_sequence=state.ledger_sequence,
    )


class _PinnedReadLedger:
    """The two read operations existing role readers need, without current fallback."""

    def __init__(self, ledger, state):
        self._ledger = ledger
        self._state = state
        self.world_id = state.world_id

    def project(self):
        return self._state

    def lookup_event_commit(self, event_id):
        found = self._ledger.lookup_event_commit(event_id)
        if found is None:
            return None
        event, commit = found
        if any((
            event.world_id != self.world_id,
            event.event_id not in commit.event_ids,
            commit.world_revision > self._state.world_revision,
            commit.deliberation_revision > self._state.deliberation_revision,
            commit.ledger_sequence > self._state.ledger_sequence,
        )):
            return None
        return found


class _Unavailable(ValueError):
    pass


def _exact_event(reader, event_ref, event_type, payload_hash):
    found = reader.lookup_event_commit(event_ref)
    if found is None or any((
        found[0].event_type != event_type, found[0].payload_hash != payload_hash,
    )):
        raise _Unavailable("character_audit_unavailable")
    return found[0]


def _role_model(reader, *, model_result_ref, model_result_payload_hash):
    model = next((item for item in reader.project().model_result_audits
                  if item.model_result_ref == model_result_ref), None)
    if model is None or model.event_payload_hash != model_result_payload_hash:
        raise _Unavailable("character_audit_unavailable")
    _exact_event(reader, model.event_ref, "ModelResultRecorded", model.event_payload_hash)
    return model


def _read_intention(reader, content_store, binding, *, intention_reader_version):
    from .chat_life_intent_runtime import ChatLifeIntentActiveReader
    from .day_open_life_intent_runtime import DayOpenLifeIntentActiveReader
    from .life_development_runtime import LifeDevelopmentProposalReader
    from .life_events import ActivityPlannedPayload
    from .world_life_intent_runtime import WorldLifeIntentActiveReader

    read = None
    source_readers = (
        ChatLifeIntentActiveReader(ledger=reader),
        WorldLifeIntentActiveReader(ledger=reader),
        LifeDevelopmentProposalReader(ledger=reader, content_store=content_store),
    )
    if intention_reader_version == "2":
        source_readers += (DayOpenLifeIntentActiveReader(ledger=reader),)
    for source_reader in source_readers:
        read = source_reader.read_active_plan(
            plan_id=binding.plan_id, expected_cursor=_cursor(reader.project()),
            actor_ref=binding.actor_ref, viewer_privacy_ceiling="private",
        )
        if read is not None:
            break
    if read is None or read.activity_event_ref != binding.source_event_ref:
        raise _Unavailable("activity_intention_unavailable")
    proposal = _exact_event(
        reader, read.proposal_source.authority_event_ref, "ProposalRecorded",
        read.proposal_source.authority_payload_hash,
    )
    planned = next((source for source in read.source_bindings
                    if source.authority_event_ref != binding.source_event_ref), None)
    if planned is None:
        raise _Unavailable("activity_intention_unavailable")
    plan_event = _exact_event(
        reader, planned.authority_event_ref, "ActivityPlanned", planned.authority_payload_hash,
    )
    payload = ActivityPlannedPayload.model_validate_json(plan_event.payload_json)
    origin = payload.chat_intent_origin or payload.world_intent_origin
    if intention_reader_version == "2":
        origin = origin or payload.day_open_intent_origin
    descriptor = read.accepted_intention
    if origin is not None:
        # The public source-specific reader has already reverse-derived the
        # exact role proposal/ModelResult and original Plan payload.
        if descriptor.truncated or origin.proposal_event_ref != proposal.event_id:
            raise _Unavailable("activity_intention_unavailable")
        text = descriptor.text
        model = _role_model(
            reader, model_result_ref=origin.model_result_ref,
            model_result_payload_hash=origin.model_result_payload_hash,
        )
    else:
        # Open-life's reader validates its accepted intention descriptor, but
        # its display text is intentionally capped at 480 characters. Recover
        # the full same-hash sidecar, and reuse the existing original-role audit
        # verifier rather than treating the World Author's option as intention.
        from .reducers import _validate_life_development_character_interior_binding

        value = proposal.payload()
        interior = value.get("character_interior_decision")
        if not isinstance(interior, dict):
            raise _Unavailable("character_audit_unavailable")
        _validate_life_development_character_interior_binding(reader.project(), proposal=value)
        if interior.get("inner_decision", {}).get("actor_ref") != binding.actor_ref:
            raise _Unavailable("character_audit_unavailable")
        stored = content_store.read_exact(content_ref=descriptor.content_ref)
        if stored is None or any((
            stored.content_kind != "outcome_candidate",
            stored.content_payload_hash != descriptor.content_payload_hash,
            hashlib.sha256(stored.text.encode()).hexdigest() != descriptor.content_payload_hash,
        )):
            raise _Unavailable("activity_intention_unavailable")
        text = stored.text
        choice = interior.get("decision", {}).get("payload", {}).get("completion", {})
        if choice.get("decision") != "accept" or choice.get("intention_summary") != text:
            raise _Unavailable("character_audit_unavailable")
        model = _role_model(
            reader, model_result_ref=interior["final_model_result_ref"],
            model_result_payload_hash=interior["model_result_event_hash"],
        )
        _exact_event(
            reader, interior["audit_proposal_event_ref"], "ProposalRecorded",
            interior["audit_proposal_event_hash"],
        )
    if hashlib.sha256(text.encode()).hexdigest() != descriptor.content_payload_hash:
        raise _Unavailable("activity_intention_unavailable")
    return WorldConsequenceAuthorizedIntention(
        text=text, content_ref=descriptor.content_ref,
        content_payload_hash=descriptor.content_payload_hash,
        proposal_event_ref=proposal.event_id, proposal_payload_hash=proposal.payload_hash,
        model_result_event_ref=model.event_ref, model_result_payload_hash=model.event_payload_hash,
        model_result_ref=model.model_result_ref,
    )


def build_world_consequence_execution_materials(
    *, ledger, content_store, pinned_state, actor_ref: str, source_events,
    intention_reader_version: Literal["1", "2"] = "1",
) -> tuple[WorldConsequenceExecutionMaterial, ...]:
    """Return available or explicitly unavailable material for each exact source.

    Only ``available`` bindings may be offered as objective-attempt authority.
    No prefix, kind, or identifier is an action description. For receipts this
    version intentionally reports unavailable: a configured Action payload /
    receipt-content reader with its own source closure is still needed.
    An immutable sidecar can be read later only at the already-committed hash;
    a descriptor or model audit first introduced after either pin is excluded.
    """
    from .schemas import ProjectionCursor

    if intention_reader_version not in {"1", "2"}:
        raise ValueError("execution intention reader version must be 1 or 2")
    author_cursor = _cursor(pinned_state)
    if ledger.world_id != pinned_state.world_id or ledger.project_at(author_cursor) != pinned_state:
        raise ValueError("execution materials require the original ledger projection")
    authority = derive_world_consequence_authority(
        pinned_state=pinned_state, actor_ref=actor_ref, source_events=tuple(source_events),
    )
    author_reader = _PinnedReadLedger(ledger, pinned_state)
    result = []
    for binding in authority.execution_bindings:
        reason = None
        intention = None
        if binding.source_kind == "execution_receipt":
            reason = "receipt_content_reader_not_installed"
        elif binding.privacy_class == "withhold":
            reason = "withheld"
        else:
            source = author_reader.lookup_event_commit(binding.source_event_ref)
            if source is None:
                reason = "source_snapshot_unavailable"
            else:
                try:
                    commit = source[1]
                    execution_state = ledger.project_at(ProjectionCursor(
                        world_revision=commit.world_revision,
                        deliberation_revision=commit.deliberation_revision,
                        ledger_sequence=commit.ledger_sequence,
                    ))
                    intention = _read_intention(
                        _PinnedReadLedger(ledger, execution_state), content_store, binding,
                        intention_reader_version=intention_reader_version,
                    )
                except _Unavailable as exc:
                    reason = str(exc)
                except (ValueError, TypeError, KeyError, OSError):
                    reason = "activity_intention_unavailable"
        result.append(WorldConsequenceExecutionMaterial(
            execution_binding=binding, status="available" if intention is not None else "unavailable",
            authorized_intention=intention, unavailable_reason=reason,
        ))
    return tuple(result)


__all__ = [
    "WorldConsequenceAuthorizedIntention", "WorldConsequenceExecutionMaterial",
    "build_world_consequence_execution_materials",
]
