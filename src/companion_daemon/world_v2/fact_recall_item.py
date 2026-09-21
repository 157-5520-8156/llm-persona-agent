"""Cycle-free source-bound Fact read models shared by Context and recall."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .fact_observation_value import FactObservationValueBinding
from .schema_core import PrivacyClass


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _validate_hex_digest(value: str, *, label: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{label} must be a lowercase SHA-256 hex digest")
    return value


class FactRecallItem(_FrozenModel):
    """Model-facing Fact semantics closed over Fact + Observation authority.

    The persistent Fact deliberately retains an opaque value ref/hash.  This
    read model recovers no value by inference: it exposes only the exact text
    of the Observation which the accepted Fact assertion binds. An optional
    accepted-value binding can verify a consumer-selected exact substring;
    it does not elevate the enclosing Observation to the accepted value.
    """

    fact_id: str = Field(min_length=1)
    subject_ref: str = Field(min_length=1)
    predicate_code: str = Field(min_length=1, max_length=128)
    source_excerpt: str = Field(min_length=1, max_length=4_096)
    confidence_bp: int = Field(ge=1, le=10_000)
    privacy_class: PrivacyClass
    status: Literal["active"] = "active"
    occurred_at: datetime
    committed_at: datetime
    updated_at: datetime
    accepted_fact_event_ref: str = Field(min_length=1)
    accepted_fact_world_revision: int = Field(ge=1)
    accepted_fact_payload_hash: str = Field(min_length=64, max_length=64)
    observation_event_ref: str = Field(min_length=1)
    observation_world_revision: int = Field(ge=1)
    observation_event_payload_hash: str = Field(min_length=64, max_length=64)
    source_observation_id: str = Field(min_length=1)
    assertion_payload_ref: str = Field(min_length=1)
    assertion_payload_hash: str = Field(min_length=64, max_length=64)
    # Optional for old capsules and other opaque value producers. This is a
    # binding, not a reconstruction or semantic endorsement of the whole text.
    accepted_value_binding: FactObservationValueBinding | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def authority_is_distinct_and_ordered(self) -> "FactRecallItem":
        for value in (
            self.accepted_fact_payload_hash,
            self.observation_event_payload_hash,
            self.assertion_payload_hash,
        ):
            _validate_hex_digest(value, label="Fact recall authority hash")
        if self.accepted_fact_event_ref == self.observation_event_ref:
            raise ValueError("Fact recall requires distinct Fact and Observation events")
        if self.observation_world_revision >= self.accepted_fact_world_revision:
            genesis_carried = (
                "WorldStarted" in self.accepted_fact_event_ref
                and self.accepted_fact_world_revision == 1
            )
            if not genesis_carried:
                raise ValueError("Fact recall Observation must precede its accepted Fact")
        return self


class HistoricalFactRecallItem(FactRecallItem):
    """One superseded Fact image with an exact validity interval."""

    status: Literal["historical"] = "historical"
    valid_from: datetime
    valid_to: datetime

    @model_validator(mode="after")
    def validity_interval_is_forward(self) -> "HistoricalFactRecallItem":
        if self.valid_to < self.valid_from:
            raise ValueError("historical Fact validity interval must not be reversed")
        return self

