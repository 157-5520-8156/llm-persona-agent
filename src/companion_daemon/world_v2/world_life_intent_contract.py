"""A character's future self activity chosen in response to a settled event.

This authority does not perform the intention, create a result, move anyone,
or grant participation by another actor. It is distinct from inbound intent.
"""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .chat_life_intent_contract import LifeIntentDraft, PlanResponseDraft
from .schema_core import FrozenModel

WORLD_LIFE_INTENT_REGISTRY_VERSION = "world-v2-proposals.4"
WORLD_LIFE_INTENT_POLICY_REF = "policy:world-life-intent.1"


def world_life_intent_source_authority(*, state, source_event_ref: str, owner_actor_ref: str):
    """Return only an exact settled occurrence in which this actor participated."""
    from .projection_lookup import lookup_first

    source = lookup_first(state.committed_world_event_refs, "event_id", source_event_ref)
    occurrence = lookup_first(state.world_occurrences, "settlement_event_ref", source_event_ref)
    if source is None or occurrence is None or any((
        source.event_type != "WorldOccurrenceSettled",
        occurrence.status != "settled",
        owner_actor_ref not in occurrence.participant_refs,
        occurrence.settlement_world_revision != source.world_revision,
        occurrence.settlement_payload_hash != source.payload_hash,
    )):
        return None
    return source


def world_life_intent_capability(*, state, source_events, owner_actor_ref: str) -> dict | None:
    """Expose this capability only for exact, currently supplied settled sources.

    This is future self-activity permission, not permission to rewrite any
    source outcome, claim it succeeded, or act on another actor's behalf.
    """
    from .schemas import WorldEvent

    allowed = []
    for event in source_events:
        event = WorldEvent.model_validate_json(event.model_dump_json())
        authority = world_life_intent_source_authority(
            state=state, source_event_ref=event.event_id, owner_actor_ref=owner_actor_ref
        )
        if authority is not None and all((
            event.world_id == state.world_id,
            event.event_type == authority.event_type,
            event.payload_hash == authority.payload_hash,
            event.logical_time == authority.logical_time,
        )):
            allowed.append(event.event_id)
    if not allowed:
        return None
    return {
        "contract": "world-life-intent-capability.1",
        "source_event_refs": sorted(set(allowed)),
        "execution_scope": "self_directed",
    }


class WorldLifeIntentPayload(LifeIntentDraft):
    actor_ref: str = Field(min_length=1, max_length=512)
    source_event_ref: str = Field(min_length=1, max_length=512)


class WorldPlanResponseDraft(FrozenModel):
    # The historical World wire has its own field order (strict required arrays
    # participate in paid request identity); semantics use the common validator.
    operation: Literal["continue", "revise", "end"]
    plan_id: str = Field(min_length=1, max_length=512)
    source_event_ref: str = Field(min_length=1, max_length=512)
    replacement: LifeIntentDraft | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def replacement_only_for_revision(self):
        return PlanResponseDraft.replacement_only_for_revision(self)


class WorldPlanResponsePayload(WorldPlanResponseDraft):
    actor_ref: str = Field(min_length=1, max_length=512)
    expected_plan_json: str = Field(min_length=2, max_length=8192)
    head_event_json: str = Field(min_length=2, max_length=16384)
    author_purpose: Literal["inbound_turn", "activity_lifecycle_choice", "life_development_choice", "world_stimulus_appraisal"] | None = Field(default=None, exclude_if=lambda v: v is None)
    author_context_json: str | None = Field(default=None, exclude_if=lambda v: v is None)


class WorldPlanResponseRecordedPayload(FrozenModel):
    proposal_id: str
    change_id: str
    actor_ref: str
    plan_id: str
    operation: Literal['continue', 'revise', 'end']
    response_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


class WorldLifeIntentOrigin(FrozenModel):
    contract: Literal["world-life-intent-origin.1"] = "world-life-intent-origin.1"
    source_event_ref: str = Field(min_length=1, max_length=512)
    source_world_revision: int = Field(ge=1)
    source_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_id: str = Field(min_length=1, max_length=256)
    proposal_event_ref: str = Field(min_length=1, max_length=512)
    proposal_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    change_id: str = Field(min_length=1, max_length=256)
    evaluated_world_revision: int = Field(ge=1)
    selected_at: datetime
    model_result_ref: str = Field(min_length=1, max_length=256)
    model_result_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_call_id: str = Field(min_length=1, max_length=256)
    inner_turn_id: str = Field(min_length=1, max_length=256)
    opportunity_ref: str = Field(min_length=1, max_length=512)
    snapshot_id: str = Field(min_length=1, max_length=128)
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
