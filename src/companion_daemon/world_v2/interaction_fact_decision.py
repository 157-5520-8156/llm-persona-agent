"""Durable, replayable result of one interaction-fact model decision.

The event is deliberation audit only: it grants no Fact authority.  It closes
the crash window between a validated model answer and the later Fact,
correction, withdrawal, or no-change branch by making every branch rejoin the
same immutable semantic result.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from .schema_core import FrozenModel


_HASH = r"^[0-9a-f]{64}$"
_MAX_DECISION_BYTES = 262_144
FACT_MEMBER_WITHDRAWAL_ADAPTER_VERSION = "fact-observation-draft.4"
FACT_MEMBER_WITHDRAWAL_POLICY_REFS = ("policy:fact-commit.2", "policy:fact-member-withdraw.1")


def interaction_fact_source_context(facts, *, subject_ref: str) -> dict[str, object]:
    """Versioned Fact authority epoch used by the .4 request and decision CAS."""
    return {
        "contract": "interaction-fact-source-context.2",
        "facts": tuple(
            fact.model_dump(mode="json")
            for fact in sorted(
                (
                    fact
                    for fact in facts
                    if fact.values.status == "active" and fact.values.subject_ref == subject_ref
                ),
                key=lambda fact: (fact.values.predicate_code, fact.fact_id),
            )
        ),
    }


class FactWithdrawalTargetBinding(FrozenModel):
    """Host-bound head of the exact set member selected in the model input."""

    entity_revision: int = Field(ge=1)
    authority_event_ref: str = Field(min_length=1)
    authority_payload_hash: str = Field(pattern=_HASH)
    value_hash: str = Field(pattern=_HASH)


class FactMemberWithdrawalBinding(FrozenModel):
    """Exact durable model choice authorizing this member's withdrawal."""

    decision_id: str = Field(min_length=1)
    decision_hash: str = Field(pattern=_HASH)


def canonical_interaction_fact_decision_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def interaction_fact_decision_hash(decision_json: str) -> str:
    return hashlib.sha256(decision_json.encode("utf-8")).hexdigest()


class InteractionFactDecisionRecordedPayload(FrozenModel):
    """One validated model-owned choice, bound to its exact trigger input."""

    decision_id: str = Field(min_length=1, max_length=256)
    trigger_id: str = Field(min_length=1, max_length=256)
    attempt_id: str = Field(min_length=1, max_length=256)
    source_event_ref: str = Field(min_length=1, max_length=512)
    source_observation_ref: str = Field(min_length=1, max_length=512)
    evaluated_world_revision: int = Field(ge=0)
    evaluated_deliberation_revision: int = Field(ge=0)
    evaluated_ledger_sequence: int = Field(ge=0)
    adapter_version: str = Field(min_length=1, max_length=128)
    model_id: str = Field(min_length=1, max_length=256)
    request_hash: str = Field(pattern=_HASH)
    batch_size: int = Field(default=1, ge=1, le=8)
    fact_context_hash: str = Field(pattern=_HASH)
    decision_kind: Literal["retain", "withdraw", "no_change"]
    decision_json: str = Field(min_length=2, max_length=_MAX_DECISION_BYTES)
    decision_hash: str = Field(pattern=_HASH)
    recorded_at: datetime

    @model_validator(mode="after")
    def decision_bytes_are_canonical_and_bound(self) -> Self:
        if len(self.decision_json.encode("utf-8")) > _MAX_DECISION_BYTES:
            raise ValueError("interaction Fact decision exceeds byte limit")
        try:
            value = json.loads(self.decision_json)
        except json.JSONDecodeError as exc:
            raise ValueError("interaction Fact decision must be JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("interaction Fact decision must be an object")
        canonical = canonical_interaction_fact_decision_json(value)
        if canonical != self.decision_json:
            raise ValueError("interaction Fact decision bytes are not canonical")
        if interaction_fact_decision_hash(canonical) != self.decision_hash:
            raise ValueError("interaction Fact decision hash is invalid")
        if self.decision_kind == "no_change" and value != {"decision": "no_change"}:
            raise ValueError("no-change interaction Fact decision has invalid bytes")
        return self


def require_fact_member_withdrawal_decision(
    decision: InteractionFactDecisionRecordedPayload,
    *,
    before,
    source_observation_ref: str,
    source_event_ref: str,
    committed_world_event_refs,
    message_observations,
) -> None:
    """Validate only exact mechanical links; retraction meaning belongs to the model."""
    value = json.loads(decision.decision_json)
    target = FactWithdrawalTargetBinding.model_validate(value.get("target_binding"))
    authority = next(
        (
            ref
            for ref in committed_world_event_refs
            if ref.event_id == before.origin.accepted_event_ref
        ),
        None,
    )
    source = next(
        (ref for ref in committed_world_event_refs if ref.event_id == source_event_ref), None
    )
    observation = next(
        (ref for ref in message_observations if ref.observation_id == source_observation_ref), None
    )
    if (
        decision.adapter_version != FACT_MEMBER_WITHDRAWAL_ADAPTER_VERSION
        or decision.decision_kind != "withdraw"
        or before.values.cardinality != "set"
        or before.values.status != "active"
        or value.get("target_fact_ref") != before.fact_id
        or value.get("predicate_code") != before.values.predicate_code
        or value.get("assertion_source_ref") != source_observation_ref
        or decision.source_observation_ref != source_observation_ref
        or decision.source_event_ref != source_event_ref
        or target.entity_revision != before.entity_revision
        or target.authority_event_ref != before.origin.accepted_event_ref
        or target.value_hash != before.values.value_hash
        or authority is None
        or authority.payload_hash != target.authority_payload_hash
        or authority.world_revision > decision.evaluated_world_revision
        or source is None
        or observation is None
        or source.event_type != "ObservationRecorded"
        or source.world_revision != observation.world_revision
        or source.payload_hash != observation.event_payload_hash
        or source.world_revision > decision.evaluated_world_revision
        or observation.actor != before.values.subject_ref
    ):
        raise ValueError(
            "Fact member withdrawal does not bind its exact model-selected source head"
        )


__all__ = [
    "FACT_MEMBER_WITHDRAWAL_ADAPTER_VERSION",
    "FACT_MEMBER_WITHDRAWAL_POLICY_REFS",
    "FactWithdrawalTargetBinding",
    "FactMemberWithdrawalBinding",
    "InteractionFactDecisionRecordedPayload",
    "canonical_interaction_fact_decision_json",
    "interaction_fact_decision_hash",
    "interaction_fact_source_context",
    "require_fact_member_withdrawal_decision",
]
