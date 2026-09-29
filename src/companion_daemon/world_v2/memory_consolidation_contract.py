"""Character-owned periodic retention decisions, not automatic forgetting."""
from typing import Literal
import json

from pydantic import Field

from .schema_core import FrozenModel

PURPOSE = "memory_consolidation"
CONTRACT = "character-interior-memory-consolidation.1"
HOST_SOURCE_BINDING = "memory-review-host-source-binding.1"


class MemoryReviewChoice(FrozenModel):
    candidate_token: str = Field(min_length=1, max_length=32)
    disposition: Literal["retain", "forget"]
    next_review_hours: int = Field(ge=24, le=720)


class MemoryReviewBatch(FrozenModel):
    contract: Literal["character-interior-memory-consolidation.1"] = CONTRACT
    choices: tuple[MemoryReviewChoice, ...] = Field(min_length=1, max_length=8)


def validate_payload(payload, offered):
    batch = MemoryReviewBatch.model_validate_json(json.dumps(dict(payload)), strict=True)
    tokens = [c.candidate_token for c in batch.choices]
    if len(set(tokens)) != len(tokens) or set(tokens) != set(offered):
        raise ValueError("periodic memory review must choose once for each offered candidate")


def tool_contract(*, capability_payload, source_refs, recall_allowed):
    from .character_interior.structured_role_tool_contract import (
        _compile_generic_decision_contract, _provider_schema,
    )

    schema = _provider_schema(MemoryReviewBatch)
    schema["properties"].pop("contract", None)
    schema["required"] = [key for key in schema.get("required", []) if key != "contract"]
    host_bound = capability_payload.get("source_binding") == HOST_SOURCE_BINDING
    return _compile_generic_decision_contract(
        purpose=PURPOSE, tool_name=("character_memory_consolidation_v2" if host_bound
                                   else "character_memory_consolidation_v1"),
        payload_schema=schema, capability_identity=capability_payload,
        source_refs=source_refs, recall_allowed=recall_allowed,
        description="Review each offered memory once. The character chooses retention and a later review; elapsed time never forces forgetting.",
        bind_sources_in_host=host_bound,
        expanded_attention=host_bound,
    )
