"""Closed ledger payload for authorizing lived-world content reads."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from .schema_core import FrozenModel, PrivacyClass


LIFE_CONTENT_USER_CHANNEL_AUTHORITY_LIMITED = (
    "LifeContentUserChannelAuthorityLimited"
)


class LifeContentRecordedPayload(FrozenModel):
    """Bind exact sidecar bytes to an already committed life authority."""

    content_id: str = Field(min_length=1)
    content_kind: Literal[
        "occurrence_result",
        "experience_summary",
        "npc_inner_state",
        "npc_goal",
    ]
    content_ref: str = Field(min_length=1)
    content_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    privacy_class: PrivacyClass
    source_kind: Literal["occurrence_settlement", "experience", "npc_state"]
    source_event_ref: str = Field(min_length=1)
    source_world_revision: int = Field(ge=1)
    source_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_entity_id: str = Field(min_length=1)
    source_entity_revision: int = Field(ge=1)


class LifeContentUserChannelAuthorityLimitedPayload(FrozenModel):
    """Later compensating event: named life-content refs are not user-channel facts.

    Append-only. Does not rewrite the original sidecar. A later projection
    simply stops treating those refs as authority that something reached him.
    ``inspected_*`` counts freeze the Action-ledger snapshot at commit time.
    """

    content_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    limitation: Literal["not_user_channel_authority"] = "not_user_channel_authority"
    reason_code: Literal["life_prose_is_not_user_channel_authority"] = (
        "life_prose_is_not_user_channel_authority"
    )
    inspected_media_delivery_count: int = Field(ge=0)
    inspected_media_delivery_action_count: int = Field(ge=0)

    @field_validator("content_refs", mode="before")
    @classmethod
    def canonicalize_content_refs(cls, value: object) -> object:
        if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
            return tuple(sorted({item for item in value if item}))
        return value

    @field_validator("content_refs")
    @classmethod
    def content_refs_are_sorted_and_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or value != tuple(sorted(set(value))) or any(not item for item in value):
            raise ValueError("user-channel authority limit refs must be sorted unique non-empty")
        return value


LIFE_CONTENT_PAYLOAD_MODELS = {
    "LifeContentRecorded": LifeContentRecordedPayload,
    LIFE_CONTENT_USER_CHANNEL_AUTHORITY_LIMITED: (
        LifeContentUserChannelAuthorityLimitedPayload
    ),
}


__all__ = [
    "LIFE_CONTENT_PAYLOAD_MODELS",
    "LIFE_CONTENT_USER_CHANNEL_AUTHORITY_LIMITED",
    "LifeContentRecordedPayload",
    "LifeContentUserChannelAuthorityLimitedPayload",
]
