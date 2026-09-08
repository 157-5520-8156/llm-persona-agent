"""A Clock-bound character decision may propose only future private self activity."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from .chat_life_intent_contract import LifeIntentDraft
from .schema_core import FrozenModel

DAY_OPEN_LIFE_INTENT_REGISTRY_VERSION = "world-v2-proposals.6"
DAY_OPEN_LIFE_INTENT_POLICY_REF = "policy:day-open-life-intent.1"
DAY_OPEN_CHOICE_CONTRACT = "character-interior-activity-lifecycle-choice.2"


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def day_open_opportunity_ref(*, world_id: str, actor_ref: str, day_key: str,
                             first_clock_ref: str) -> str:
    return "day-open:" + digest([world_id, actor_ref, day_key, first_clock_ref])


class DayOpenLifeIntentCapability(FrozenModel):
    contract: Literal["day-open-life-intent-capability.1"]
    execution_scope: Literal["self_directed"]
    opportunity_ref: str = Field(pattern=r"^day-open:[0-9a-f]{64}$")
    day_key: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    timezone_name: str = Field(min_length=1, max_length=128)
    first_clock_ref: str = Field(min_length=1, max_length=512)
    first_clock_world_revision: int = Field(ge=1)
    first_clock_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_clock_ref: str = Field(min_length=1, max_length=512)
    selected_clock_world_revision: int = Field(ge=1)
    selected_clock_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempt_ordinal: int = Field(ge=1, le=3)

    @model_validator(mode="after")
    def clock_sequence_is_possible(self):
        ZoneInfo(self.timezone_name)
        datetime.strptime(self.day_key, "%Y-%m-%d")
        first = (self.first_clock_ref, self.first_clock_world_revision, self.first_clock_payload_hash)
        selected = (
            self.selected_clock_ref, self.selected_clock_world_revision, self.selected_clock_payload_hash
        )
        if (self.attempt_ordinal == 1 and first != selected) or (
            self.attempt_ordinal > 1 and (
                self.first_clock_ref == self.selected_clock_ref
                or self.first_clock_world_revision >= self.selected_clock_world_revision
            )
        ):
            raise ValueError("day-open attempt clocks are inconsistent")
        return self


class DayOpenActivityCapability(FrozenModel):
    contract: Literal["character-interior-activity-lifecycle-capability.3"]
    catalog_version: str = Field(min_length=1, max_length=128)
    catalog_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    offered_tokens: tuple[str, ...] = Field(max_length=0)
    openings: tuple[dict, ...] = Field(max_length=0)
    self_directed_intent: DayOpenLifeIntentCapability


class DayOpenLifeIntentPayload(LifeIntentDraft):
    actor_ref: str = Field(min_length=1, max_length=256)
    role_decision_json: str = Field(min_length=2, max_length=50_000)
    capability_payload_json: str = Field(min_length=2, max_length=8_000)


class DayOpenEvaluatedCursor(FrozenModel):
    """Original audit coordinates, independent of the aggregate schema imports."""

    world_revision: int = Field(ge=0)
    deliberation_revision: int = Field(ge=0)
    ledger_sequence: int = Field(ge=0)


class DayOpenLifeIntentOrigin(FrozenModel):
    contract: Literal["day-open-life-intent-origin.1"] = "day-open-life-intent-origin.1"
    world_id: str = Field(min_length=1)
    actor_ref: str = Field(min_length=1)
    opportunity_ref: str = Field(min_length=1)
    day_key: str
    timezone_name: str
    first_clock_ref: str
    first_clock_world_revision: int = Field(ge=1)
    first_clock_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_event_ref: str
    source_world_revision: int = Field(ge=1)
    source_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempt_ordinal: int = Field(ge=1, le=3)
    selected_at: datetime
    evaluated_cursor: DayOpenEvaluatedCursor
    evaluated_world_revision: int = Field(ge=1)
    capability_ref: str
    capability_payload_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    proposal_id: str
    proposal_event_ref: str
    proposal_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    change_id: str
    model_result_ref: str
    model_result_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_call_id: str
    inner_turn_id: str
    role_opportunity_ref: str
    snapshot_id: str
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
