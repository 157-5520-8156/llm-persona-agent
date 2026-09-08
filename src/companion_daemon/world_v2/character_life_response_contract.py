"""One explicit role reading of one settled world consequence.

Null is an authored absence of response, not a missing role decision. Neither
case performs an activity or proves new external facts. This leaf contract
deliberately does not import World schemas, which embed the accepted body.
"""

from datetime import datetime
from typing import Literal

from pydantic import Field

from .schema_core import FrozenModel


CHARACTER_LIFE_RESPONSE_REGISTRY_VERSION = "world-v2-proposals.5"
CHARACTER_LIFE_RESPONSE_POLICY_REF = "policy:character-life-response.1"


class CharacterLifeResponsePayload(FrozenModel):
    actor_ref: str = Field(min_length=1, max_length=512)
    source_event_ref: str = Field(min_length=1, max_length=512)
    response_text: str | None = Field(max_length=4000)


class CharacterLifeResponseOrigin(FrozenModel):
    contract: Literal["character-life-response-origin.1"] = "character-life-response-origin.1"
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


class CharacterLifeResponseRecordedPayload(FrozenModel):
    response_id: str = Field(min_length=1, max_length=256)
    actor_ref: str = Field(min_length=1, max_length=512)
    response_text: str | None = Field(max_length=4000)
    origin: CharacterLifeResponseOrigin


__all__ = [
    "CHARACTER_LIFE_RESPONSE_POLICY_REF",
    "CHARACTER_LIFE_RESPONSE_REGISTRY_VERSION",
    "CharacterLifeResponseOrigin",
    "CharacterLifeResponsePayload",
    "CharacterLifeResponseRecordedPayload",
]
