"""Bind an ended role-authored activity to its last authorized attempt.

The completion proves only that the lifecycle ended. Execution authority stays
with the original Started/Resumed event; neither record establishes success.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import model_validator

from .activity_continuation_source import ActivityCompletionSource, validate_completion_source
from .schema_core import FrozenModel
from .world_consequence_contract import (
    ActivityExecutionBinding,
    derive_world_consequence_authority,
)


class CompletedActivityConsequence(FrozenModel):
    contract: Literal["completed-activity-consequence.1"] = "completed-activity-consequence.1"
    completion: ActivityCompletionSource
    execution_binding: ActivityExecutionBinding

    @model_validator(mode="after")
    def same_activity_in_execution_order(self):
        if any((
            self.completion.plan_id != self.execution_binding.plan_id,
            self.completion.event_ref == self.execution_binding.source_event_ref,
            self.completion.world_revision <= self.execution_binding.source_world_revision,
            self.completion.plan_revision <= self.execution_binding.plan_entity_revision,
        )):
            raise ValueError("completed_activity_consequence.execution_pair_invalid")
        return self


def _cursor(state):
    from .schemas import ProjectionCursor

    return ProjectionCursor(
        world_revision=state.world_revision,
        deliberation_revision=state.deliberation_revision,
        ledger_sequence=state.ledger_sequence,
    )


def _exact_event(ledger, state, ref):
    from .schemas import WorldEvent

    located = ledger.lookup_event_commit(ref.event_id)
    if located is None:
        raise ValueError("completed_activity_consequence.event_unavailable")
    event, commit = located
    event = WorldEvent.model_validate_json(event.model_dump_json())
    if any((
        event.event_id != ref.event_id,
        event.world_id != state.world_id,
        event.event_type != ref.event_type,
        event.payload_hash != ref.payload_hash,
        event.payload_hash != hashlib.sha256(event.payload_json.encode()).hexdigest(),
        event.logical_time != ref.logical_time,
        state.logical_time is None,
        state.logical_time is not None and event.logical_time > state.logical_time,
        event.event_id not in commit.event_ids,
        commit.world_revision < ref.world_revision,
        commit.world_revision > state.world_revision,
        commit.deliberation_revision > state.deliberation_revision,
        commit.ledger_sequence > state.ledger_sequence,
    )):
        raise ValueError("completed_activity_consequence.event_not_exact")
    return event


def read_completed_activity_consequence(
    *, ledger, pinned_state, actor_ref: str, completion_event_ref: str,
) -> CompletedActivityConsequence | None:
    """Read one exact completed self-directed activity from the supplied prefix.

Unsupported activity origins, other actors and withheld material are unavailable.
Corrupt source identity or a substituted projection raises ValueError. Later
ledger advances do not replace the caller's original pinned Plan head.
"""
    from .chat_life_intent_runtime import ChatLifeIntentCompletedReader
    from .day_open_life_intent_runtime import DayOpenLifeIntentCompletedReader
    from .life_events import ActivityTransitionPayload
    from .world_consequence_execution_context import _PinnedReadLedger
    from .world_life_intent_runtime import WorldLifeIntentCompletedReader

    cursor = _cursor(pinned_state)
    if ledger.world_id != pinned_state.world_id or ledger.project_at(cursor) != pinned_state:
        raise ValueError("completed_activity_consequence.original_pin_required")
    terminal_ref = next((ref for ref in pinned_state.committed_world_event_refs
                         if ref.event_id == completion_event_ref), None)
    if terminal_ref is None or terminal_ref.event_type != "ActivityCompleted":
        return None
    terminal = _exact_event(ledger, pinned_state, terminal_ref)
    payload = ActivityTransitionPayload.model_validate_json(terminal.payload_json)
    plan = next((item for item in pinned_state.plans if item.plan_id == payload.plan_id), None)
    if plan is None or any((
        plan.owner_actor_ref != actor_ref,
        plan.status != "completed",
        plan.privacy_class == "withhold",
        plan.authority_origin is None,
    )):
        return None
    completion = ActivityCompletionSource(
        event_ref=terminal.event_id, world_revision=terminal_ref.world_revision,
        payload_hash=terminal.payload_hash, plan_id=plan.plan_id,
        plan_revision=plan.entity_revision,
    )
    validate_completion_source(
        pinned_state, completion, actor_ref=actor_ref, cursor_revision=cursor.world_revision,
    )
    if any((
        payload.expected_entity_revision + 1 != plan.entity_revision,
        payload.transitioned_at != terminal.logical_time,
    )):
        raise ValueError("completed_activity_consequence.terminal_revision_invalid")

    reader = _PinnedReadLedger(ledger, pinned_state)
    for reader_type in (
        DayOpenLifeIntentCompletedReader, ChatLifeIntentCompletedReader,
        WorldLifeIntentCompletedReader,
    ):
        material = reader_type(ledger=reader).read_completed_plan(
            plan_id=plan.plan_id, expected_cursor=cursor, actor_ref=actor_ref,
            viewer_privacy_ceiling="private",
        )
        if material is not None:
            break
    else:
        return None
    if material.activity_event_ref != completion.event_ref:
        raise ValueError("completed_activity_consequence.role_completion_mismatch")

    sources = sorted(
        (ref for ref in pinned_state.committed_world_event_refs
         if ref.event_type in {"ActivityStarted", "ActivityResumed"}
         and ref.world_revision < completion.world_revision),
        key=lambda ref: ref.world_revision, reverse=True,
    )
    for ref in sources:
        event = _exact_event(ledger, pinned_state, ref)
        transition = ActivityTransitionPayload.model_validate_json(event.payload_json)
        if transition.plan_id != plan.plan_id:
            continue
        authority = derive_world_consequence_authority(
            pinned_state=pinned_state, actor_ref=actor_ref, source_events=(event,),
        )
        (binding,) = authority.execution_bindings
        return CompletedActivityConsequence(completion=completion, execution_binding=binding)
    return None


def validate_completed_activity_consequence(
    *, ledger, pinned_state, actor_ref: str, descriptor: CompletedActivityConsequence,
) -> None:
    """Re-read the exact pair; internally plausible substitute refs are not proof."""
    supplied = CompletedActivityConsequence.model_validate_json(descriptor.model_dump_json())
    expected = read_completed_activity_consequence(
        ledger=ledger, pinned_state=pinned_state, actor_ref=actor_ref,
        completion_event_ref=supplied.completion.event_ref,
    )
    if expected is None or supplied != expected:
        raise ValueError("completed_activity_consequence.original_pair_mismatch")


__all__ = [
    "CompletedActivityConsequence", "read_completed_activity_consequence",
    "validate_completed_activity_consequence",
]
