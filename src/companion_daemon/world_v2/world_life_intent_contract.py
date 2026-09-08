"""A character's future self activity chosen in response to a settled event.

This authority does not perform the intention, create a result, move anyone,
or grant participation by another actor. It is distinct from inbound intent.
"""

from datetime import datetime
from typing import Literal

from pydantic import Field

from .chat_life_intent_contract import LifeIntentDraft
from .schema_core import FrozenModel

WORLD_LIFE_INTENT_REGISTRY_VERSION = "world-v2-proposals.4"
WORLD_LIFE_INTENT_POLICY_REF = "policy:world-life-intent.1"


def world_life_intent_source_authority(*, state, source_event_ref: str, owner_actor_ref: str):
    """Return only an exact settled occurrence in which this actor participated."""
    source = next((x for x in state.committed_world_event_refs
                   if x.event_id == source_event_ref), None)
    occurrence = next((x for x in state.world_occurrences
                       if x.settlement_event_ref == source_event_ref), None)
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
