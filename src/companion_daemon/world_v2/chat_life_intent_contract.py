"""Role-authored future activity and its immutable same-turn acceptance origin.

Self-directed is an execution capability, not a motivation taxonomy. It grants
no authority over a place, other people, generated events, or activity results.
"""

from __future__ import annotations

from typing import Literal
from datetime import datetime

from pydantic import Field, model_validator

from .schema_core import FrozenModel


class LifeIntentDraft(FrozenModel):
    execution_scope: Literal["self_directed"]
    intention: str = Field(min_length=1, max_length=480)
    start_after_seconds: int = Field(ge=0, le=86400)
    duration_seconds: int = Field(ge=60, le=21600)
    importance_bp: int = Field(ge=0, le=10000)

    @model_validator(mode="after")
    def intention_is_not_empty(self) -> "LifeIntentDraft":
        if not self.intention.strip() or self.intention != self.intention.strip():
            raise ValueError("life_intent intention must be nonempty and trimmed")
        return self


class LifeIntentPayload(LifeIntentDraft):
    actor_ref: str = Field(min_length=1, max_length=512)
    source_observation_ref: str = Field(min_length=1, max_length=512)


class ChatLifeIntentOrigin(FrozenModel):
    contract: Literal["chat-life-intent-origin.1"] = "chat-life-intent-origin.1"
    proposal_id: str = Field(min_length=1)
    proposal_event_ref: str = Field(min_length=1)
    proposal_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    change_id: str = Field(min_length=1)
    evaluated_world_revision: int = Field(ge=1)
    selected_at: datetime
    model_result_ref: str = Field(min_length=1)
    model_result_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_call_id: str = Field(min_length=1)
    inner_turn_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    snapshot_hash: str = Field(min_length=1)


class ChatLifeIntentFailure(FrozenModel):
    failure_event_ref: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    proposal_event_ref: str = Field(min_length=1)
    proposal_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    change_id: str = Field(min_length=1)
    change_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_id: str = Field(min_length=1)
    actor_ref: str = Field(min_length=1)
    evaluated_world_revision: int = Field(ge=1)
    retry_ordinal: int = Field(ge=1, le=3)
    failure_code: Literal["cursor_conflict", "authority_invalid"]
    reason_code: Literal[
        "cursor_conflict",
        "proposal_missing",
        "proposal_binding_invalid",
        "explicit_intent_missing",
        "actor_mismatch",
        "model_binding_invalid",
        "inner_turn_authority_missing",
        "source_observation_invalid",
        "source_binding_invalid",
        "selection_clock_unavailable",
        "clock_unavailable",
        "origin_missing",
        "accepted_effect_mismatch",
        "effect_identity_conflict",
        "validation_failure",
        "proposal_authority_invalid",
    ]
    failed_at: datetime
    next_retry_at: datetime | None
    terminal: bool
