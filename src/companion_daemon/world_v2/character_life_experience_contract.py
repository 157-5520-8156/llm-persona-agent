"""Recomputable Experience summary, keeping its two authors separate.

The World part is an immutable result locator, not duplicated prose. Readers
resolve that exact result separately. Character text records a response only;
external assertions inside it receive no World authority.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import Field

from .schema_core import FrozenModel
from .schemas import ExperienceOccurrenceSettlementBinding, ExperienceWorldLifeResponseBinding


CHARACTER_LIFE_EXPERIENCE_POLICY_REFS = ("policy:experience-world-life-response.1",)
CHARACTER_LIFE_EXPERIENCE_SUMMARY_CONTRACT = "character-life-experience-summary.1"


class CharacterLifeExperienceResponse(FrozenModel):
    response_event_ref: str = Field(min_length=1)
    actor_ref: str = Field(min_length=1)
    response_text: str | None = Field(max_length=4000)


class CharacterLifeExperienceSummary(FrozenModel):
    contract: Literal["character-life-experience-summary.1"] = (
        CHARACTER_LIFE_EXPERIENCE_SUMMARY_CONTRACT
    )
    world_consequence: ExperienceOccurrenceSettlementBinding
    character_response: CharacterLifeExperienceResponse

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )


def derive_character_life_experience_summary(
    binding: ExperienceWorldLifeResponseBinding,
) -> CharacterLifeExperienceSummary:
    """Render exact binding values; callers separately prove source authority."""
    binding = ExperienceWorldLifeResponseBinding.model_validate_json(binding.model_dump_json())
    return CharacterLifeExperienceSummary(
        world_consequence=binding.settlement,
        character_response=CharacterLifeExperienceResponse(
            response_event_ref=binding.response_event_ref,
            actor_ref=binding.response.actor_ref,
            response_text=binding.response.response_text,
        ),
    )


def validate_character_life_experience_summary(
    text: str,
    binding: ExperienceWorldLifeResponseBinding,
) -> CharacterLifeExperienceSummary:
    """Reject missing, altered or extra text, including an implicit null choice."""
    expected = derive_character_life_experience_summary(binding)
    if text != expected.canonical_json():
        raise ValueError("character_life_experience.summary_mismatch")
    return expected


__all__ = [
    "CHARACTER_LIFE_EXPERIENCE_POLICY_REFS",
    "CHARACTER_LIFE_EXPERIENCE_SUMMARY_CONTRACT",
    "CharacterLifeExperienceSummary",
    "derive_character_life_experience_summary",
    "validate_character_life_experience_summary",
]
