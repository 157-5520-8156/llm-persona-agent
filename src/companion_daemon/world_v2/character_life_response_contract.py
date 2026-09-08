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


def validate_character_life_response_coverage(responses, capability) -> None:
    """Close explicit author entries over the offered source set, not their meaning."""
    if capability is None:
        if responses is not None:
            raise ValueError("life_responses unavailable without world_life_response capability")
        return
    refs = capability.get("source_event_refs") if isinstance(capability, dict) else None
    if (
        not isinstance(capability, dict)
        or capability.get("contract") != "world-life-response-capability.1"
        or not isinstance(refs, list)
        or not refs
        or any(not isinstance(ref, str) or not ref for ref in refs)
        or len(refs) != len(set(refs))
    ):
        raise ValueError("world_life_response capability is invalid")
    if responses is None:
        raise ValueError(
            "life_responses is required: explicitly provide response_text (text or null) "
            "once for every offered source_event_ref: " + ", ".join(refs)
        )
    selected = [entry.source_event_ref for entry in responses]
    if len(selected) != len(set(selected)):
        raise ValueError("life_responses has duplicate source_event_ref entries")
    missing = sorted(set(refs) - set(selected))
    extra = sorted(set(selected) - set(refs))
    if missing or extra:
        raise ValueError(
            "life_responses source coverage differs from capability; missing="
            + repr(missing)
            + "; unoffered="
            + repr(extra)
            + "; provide one explicit text or null per offered source"
        )


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
    "validate_character_life_response_coverage",
    "CHARACTER_LIFE_RESPONSE_POLICY_REF",
    "CHARACTER_LIFE_RESPONSE_REGISTRY_VERSION",
    "CharacterLifeResponseOrigin",
    "CharacterLifeResponsePayload",
    "CharacterLifeResponseRecordedPayload",
]
