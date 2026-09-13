"""Immutable initialization choices; imported history is not automatic memory."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field, model_validator

from .character_interior.audit import recorded_character_interior_model_result
from .character_interior.contracts import InnerDecision, InteriorOpportunity
from .character_interior.life_memory import (
    _FACT_MEMORY_PURPOSE, _materialize_memory_retention, _memory_opportunity, _memory_retention_capability,
)
from .character_interior.purpose_context import InteriorPurposeContext
from .character_prehistory import digest
from .prehistory_memory_source import prehistory_memory_reading, resolve_prehistory_memory_source
from .proposal_audit_schemas import ModelResultRecordedPayload
from .schema_core import FrozenModel
from .schemas import MemorySourceBinding

EVENT_TYPE = "PrehistoryMemoryDecisionRecorded"
ACTOR = "system:prehistory-memory"
SOURCE = "world-v2:prehistory-memory-initialization"


def initialization_opportunity(*, world_id, row, archive, cursor):
    capability = _memory_retention_capability(
        source_kind="character_prehistory", predicate_code="character.prehistory",
        source_text=row.record.statement,
    )
    capability["prehistory"] = prehistory_memory_reading(row, archive).model_dump(mode="json")
    capability["initialization_scope"] = "one reviewed historical record at its original import snapshot"
    return _memory_opportunity(
        world_id=world_id, actor_ref=row.actor_ref, purpose=_FACT_MEMORY_PURPOSE,
        context=InteriorPurposeContext(
            inner_turn_ref="prehistory-memory:" + row.accepted_event_ref,
            trigger_ref=row.accepted_event_ref, cursor=cursor, logical_time=row.accepted_at,
            source_refs=(row.accepted_event_ref, archive.accepted_event_ref),
        ),
        capability_payload=capability,
    )


def retention_draft(result, opportunity):
    return _materialize_memory_retention(result=result, opportunity=opportunity, purpose=_FACT_MEMORY_PURPOSE)


def retention_model_result(result, opportunity):
    draft = retention_draft(result, opportunity)
    return recorded_character_interior_model_result(
        result, purpose=_FACT_MEMORY_PURPOSE, subject_ref=opportunity.opportunity_ref,
        trigger_ref=opportunity.trigger_ref, capability_ref=opportunity.capability_manifest.capability_ref,
        route_tier="flash", route_reason_code="prehistory_memory.initialization",
        router_version="prehistory-memory-retention.1",
        proposal_hash="sha256:" + digest(draft.model_dump(mode="json") if draft is not None else {"retain": False}),
    )


def decision_event_id(world_id, source, *, failure_ordinal=None):
    key = digest([world_id, source.model_dump(mode="json")])
    return (f"event:prehistory-memory:decision:{key}" if failure_ordinal is None
            else f"event:prehistory-memory:failure:{key}:{failure_ordinal}")


class PrehistoryMemoryDecisionRecordedPayload(FrozenModel):
    contract: Literal["prehistory-memory-retention.1"] = "prehistory-memory-retention.1"
    source_binding: MemorySourceBinding
    opportunity: InteriorOpportunity
    attempt_ordinal: int = Field(ge=1, le=3)
    status: Literal["retain", "no_change", "technical_failure"]
    recorded_at: datetime
    result: InnerDecision | None = None
    character_interior_model_result: ModelResultRecordedPayload | None = None
    failure_code: str | None = Field(default=None, min_length=1, max_length=512)
    next_retry_at: datetime | None = None

    @model_validator(mode="after")
    def choice_and_technical_failure_are_distinct(self):
        if self.source_binding.source_kind != "prehistory":
            raise ValueError("prehistory retention requires historical source authority")
        if self.recorded_at.tzinfo is None or self.recorded_at < self.opportunity.logical_time:
            raise ValueError("prehistory retention recording time is invalid")
        if self.status == "technical_failure":
            due = None if self.attempt_ordinal == 3 else self.recorded_at + timedelta(seconds=(30, 120)[self.attempt_ordinal - 1])
            if self.result is not None or self.character_interior_model_result is not None or not self.failure_code or self.next_retry_at != due:
                raise ValueError("prehistory technical failure must preserve bounded retry, not a choice")
        else:
            if self.result is None or self.failure_code is not None or self.next_retry_at is not None:
                raise ValueError("prehistory retention requires a completed character choice")
            if (self.result.actor_ref != self.opportunity.actor_ref or self.result.cursor != self.opportunity.cursor
                or self.result.opportunity_ref != self.opportunity.opportunity_ref):
                raise ValueError("prehistory retention role identity differs from the offered opportunity")
            draft = retention_draft(self.result, self.opportunity)
            if (draft is not None) != (self.status == "retain"):
                raise ValueError("prehistory retention status differs from the character decision")
            if self.character_interior_model_result != retention_model_result(self.result, self.opportunity):
                raise ValueError("prehistory retention lost its exact character author audit")
        return self


def reduce_prehistory_memory_decision(state, event):
    payload = PrehistoryMemoryDecisionRecordedPayload.model_validate_json(event.payload_json)
    row, archive = resolve_prehistory_memory_source(
        payload.source_binding, records=state.prehistory_records, archives=state.prehistory_archives,
        committed_events=state.committed_world_event_refs,
    )
    expected = initialization_opportunity(world_id=event.world_id, row=row, archive=archive,
                                           cursor=payload.opportunity.cursor)
    if (
        event.actor != ACTOR or event.source != SOURCE or event.logical_time != state.logical_time
        or event.created_at < payload.recorded_at or event.logical_time != payload.recorded_at
        or event.causation_id != row.accepted_event_ref or payload.opportunity != expected
        or payload.opportunity.cursor.world_revision > len(state.committed_world_event_refs)
        or any(item.archive_id == row.archive_id and item.accepted_world_revision > payload.opportunity.cursor.world_revision
               for item in state.prehistory_records)
        or event.event_id != decision_event_id(event.world_id, payload.source_binding,
            failure_ordinal=payload.attempt_ordinal if payload.status == "technical_failure" else None)
    ):
        raise ValueError("prehistory retention event does not bind the imported source and character")
    # This is a durable author result. MemoryCandidate is accepted separately,
    # and crash recovery rejoins these exact bytes instead of asking again.
    return state
