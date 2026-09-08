"""Shared proof of an exact world-stimulus role choice, not a write grant.

Response and Plan compilers retain their own payload and effect boundaries.
This module only proves their common immutable proposal, final role audit,
settled source and original selection time.
"""

from dataclasses import dataclass
import json

from .proposal_audit_schemas import RecordedModelResultAudit
from .proposal_envelope import DecisionProposal, validate_proposal_envelope
from .world_life_intent_contract import (
    world_life_intent_capability,
    world_life_intent_source_authority,
)


def world_life_response_source_refs(
    *, state, source_refs, owner_actor_ref: str, evaluated_world_revision: int
) -> tuple[str, ...]:
    """Only the originally selected, explicitly versioned consequences need a response.

    A settled occurrence's candidate matrix is immutable. The source revision
    cutoff prevents recovery from borrowing a settlement newer than the role's
    original pin, even if the current projection contains it.
    """
    allowed = []
    for ref in source_refs:
        source = world_life_intent_source_authority(
            state=state, source_event_ref=ref, owner_actor_ref=owner_actor_ref
        )
        if source is None or source.world_revision > evaluated_world_revision:
            continue
        occurrence = next(x for x in state.world_occurrences if x.settlement_event_ref == ref)
        selected = next(
            (
                x
                for x in occurrence.candidate_outcomes
                if x.candidate_result_ref == occurrence.settled_outcome_ref
            ),
            None,
        )
        if selected is not None and selected.result_contract == "world-consequence.2":
            allowed.append(ref)
    return tuple(sorted(set(allowed)))


def world_life_response_capability(*, state, source_events, owner_actor_ref: str) -> dict | None:
    # Reuse exact event/world/hash/privacy participation checks before exposing
    # the narrower response capability. An unselected candidate grants nothing.
    life = world_life_intent_capability(
        state=state, source_events=source_events, owner_actor_ref=owner_actor_ref
    )
    if life is None:
        return None
    refs = world_life_response_source_refs(
        state=state,
        source_refs=life["source_event_refs"],
        owner_actor_ref=owner_actor_ref,
        evaluated_world_revision=state.world_revision,
    )
    if not refs:
        return None
    return {"contract": "world-life-response-capability.1", "source_event_refs": list(refs)}


@dataclass(frozen=True)
class WorldStimulusChoiceAuthority:
    proposal: DecisionProposal
    audit: object
    model: object
    lineage: object


def read_world_stimulus_choice_authority(
    *,
    state,
    world_id: str,
    proposal_id: str,
    owner_actor_ref: str,
    registry_versions: tuple[str, ...],
    error_type: type[ValueError],
) -> WorldStimulusChoiceAuthority:
    audit = next((x for x in state.proposal_audits if x.proposal_id == proposal_id), None)
    if audit is None:
        raise error_type("proposal_missing")
    proposal = validate_proposal_envelope(json.loads(audit.proposal_json))
    if (
        not isinstance(proposal, DecisionProposal)
        or proposal.proposal_hash != audit.proposal_hash
        or proposal.schema_registry_version not in registry_versions
    ):
        raise error_type("proposal_binding_invalid")
    # Keep the domain's existing actor mismatch diagnostic before checking
    # the model lineage. These are typed actor fields, never prose inference.
    if any(
        json.loads(change.payload.canonical_json).get("actor_ref") != owner_actor_ref
        for change in proposal.proposed_changes
        if change.kind in {"world_life_intent", "world_life_response"}
    ):
        raise error_type("actor_mismatch")
    model = next(
        (x for x in state.model_result_audits if x.model_result_ref == audit.model_result_ref), None
    )
    if model is None or any(
        (
            model.proposal_hash != audit.proposal_hash,
            model.model_call_id != audit.model_call_id,
            model.attempt_id != audit.attempt_id,
            model.capsule_id != audit.capsule_id,
            model.deliberation_result_id != audit.deliberation_result_id,
            model.trigger_ref != audit.trigger_ref,
            model.evaluated_world_revision != audit.evaluated_world_revision,
            model.attempt_index != model.attempt_count - 1,
        )
    ):
        raise error_type("model_binding_invalid")
    recorded = RecordedModelResultAudit.model_validate_json(model.audit_json)
    lineage = recorded.character_interior_lineage
    if lineage is None or any(
        (
            lineage.purpose != "world_stimulus_appraisal",
            lineage.author_model_call_id != model.model_call_id,
            lineage.inner_turn_id != model.attempt_id,
            lineage.snapshot_hash != model.capsule_id,
            lineage.causal_world_id != world_id,
            lineage.causal_actor_ref != owner_actor_ref,
            audit.trigger_ref not in lineage.causal_source_refs,
        )
    ):
        raise error_type("inner_turn_authority_invalid")
    return WorldStimulusChoiceAuthority(proposal, audit, model, lineage)


def world_stimulus_source_origin(
    *,
    state,
    authority: WorldStimulusChoiceAuthority,
    source_event_ref: str,
    change_id: str,
    error_type: type[ValueError],
) -> tuple[object, dict[str, object]]:
    """Prove one supplied settlement and return its original audit coordinates."""
    audit, model, lineage = authority.audit, authority.model, authority.lineage
    if source_event_ref not in lineage.causal_source_refs:
        raise error_type("inner_turn_authority_invalid")
    source = world_life_intent_source_authority(
        state=state,
        source_event_ref=source_event_ref,
        owner_actor_ref=lineage.causal_actor_ref,
    )
    if source is None or source.world_revision > audit.evaluated_world_revision:
        raise error_type("settlement_authority_invalid")
    declared = next(
        (x for x in authority.proposal.evidence_refs if x.ref_id == source.event_id), None
    )
    if declared is None or any(
        (
            declared.evidence_kind != "settled_world_event",
            declared.source_world_revision != source.world_revision,
            declared.immutable_hash != "sha256:" + source.payload_hash,
        )
    ):
        raise error_type("source_binding_invalid")
    selected_clock = next(
        (
            x
            for x in state.committed_world_event_refs
            if x.world_revision == audit.evaluated_world_revision
        ),
        None,
    )
    if selected_clock is None or selected_clock.logical_time < source.logical_time:
        raise error_type("selection_clock_unavailable")
    return source, {
        "source_event_ref": source.event_id,
        "source_world_revision": source.world_revision,
        "source_payload_hash": source.payload_hash,
        "proposal_id": audit.proposal_id,
        "proposal_event_ref": audit.event_ref,
        "proposal_payload_hash": audit.event_payload_hash,
        "proposal_hash": audit.proposal_hash,
        "change_id": change_id,
        "evaluated_world_revision": audit.evaluated_world_revision,
        "selected_at": selected_clock.logical_time,
        "model_result_ref": model.model_result_ref,
        "model_result_payload_hash": model.event_payload_hash,
        "model_call_id": model.model_call_id,
        "inner_turn_id": lineage.inner_turn_id,
        "opportunity_ref": lineage.opportunity_ref,
        "snapshot_id": lineage.snapshot_id,
        "snapshot_hash": lineage.snapshot_hash,
    }


__all__ = [
    "WorldStimulusChoiceAuthority",
    "world_life_response_source_refs",
    "world_life_response_capability",
    "read_world_stimulus_choice_authority",
    "world_stimulus_source_origin",
]
