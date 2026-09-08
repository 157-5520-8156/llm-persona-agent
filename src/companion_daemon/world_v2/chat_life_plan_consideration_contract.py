"""One source-bound initial consideration of an accepted chat activity plan."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from pydantic import Field

from .chat_life_intent_contract import ChatLifeIntentOrigin
from .world_life_intent_contract import WorldLifeIntentOrigin
from .proposal_audit_schemas import ModelResultRecordedPayload
from .schema_core import FrozenModel


class ChatLifePlanOpportunity(FrozenModel):
    plan_id: str = Field(min_length=1, max_length=256)
    plan_event_ref: str = Field(min_length=1, max_length=256)
    plan_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    owner_actor_ref: str = Field(min_length=1, max_length=256)
    # The historical event container is retained. World-origin plans carry
    # their own author contract and never acquire an inbound-chat authority.
    origin: ChatLifeIntentOrigin | WorldLifeIntentOrigin
    due_at: datetime
    attempt_ordinal: int = Field(ge=1, le=3)


class ChatLifePlanConsideration(FrozenModel):
    consideration_ref: str = Field(min_length=1, max_length=256)
    opportunity: ChatLifePlanOpportunity
    clock_event_ref: str = Field(min_length=1, max_length=512)
    clock_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    considered_at: datetime
    recorded_at: datetime
    recording_clock_event_ref: str = Field(min_length=1, max_length=512)
    recording_clock_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["selected", "declined", "technical_failure"]
    terminal: bool
    terminal_reason: Literal["role_decision", "attempts_exhausted", "window_expired"] | None
    next_retry_at: datetime | None
    character_interior_model_result: ModelResultRecordedPayload | None = None
    character_decision_json: str | None = Field(default=None, max_length=262144)
    lifecycle_proposal_ref: str | None = None
    lifecycle_proposal_json: str | None = Field(default=None, max_length=262144)
    failure_code: str | None = Field(default=None, min_length=1, max_length=128)
