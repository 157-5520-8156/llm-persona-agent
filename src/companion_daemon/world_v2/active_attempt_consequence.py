"""Exact active lifecycle/time coordinates for a bounded attempt consequence.

These coordinates grant no successful action, perception, location or history.
The existing execution reader must separately prove the original role intention.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .schema_core import FrozenModel
from .schemas import ProjectionCursor, WorldEvent
from .world_consequence_contract import ActivityExecutionBinding, derive_world_consequence_authority


class ActiveAttemptConsequence(FrozenModel):
    contract: Literal["active-attempt-consequence.1"] = "active-attempt-consequence.1"
    execution_binding: ActivityExecutionBinding
    execution_started_at: datetime
    active_plan_revision: int = Field(ge=2)
    clock_event_ref: str
    clock_payload_hash: str
    clock_world_revision: int = Field(ge=1)
    current_logical_time: datetime
    authority_scope: Literal["active_attempt_and_elapsed_world_time_not_execution_success"] = (
        "active_attempt_and_elapsed_world_time_not_execution_success"
    )

    @model_validator(mode="after")
    def current_active_interval(self):
        if (self.active_plan_revision != self.execution_binding.plan_entity_revision
                or self.execution_started_at.tzinfo is None
                or self.current_logical_time.tzinfo is None
                or self.execution_started_at > self.current_logical_time):
            raise ValueError("active_attempt_consequence.invalid_active_interval")
        return self


def _exact_event(ledger, state, ref):
    found = ledger.lookup_event_commit(ref.event_id)
    if found is None:
        raise ValueError("active_attempt_consequence.event_unavailable")
    event, commit = found
    event = WorldEvent.model_validate_json(event.model_dump_json())
    if any((
        event.event_id != ref.event_id, event.world_id != state.world_id,
        event.event_type != ref.event_type, event.payload_hash != ref.payload_hash,
        event.payload_hash != hashlib.sha256(event.payload_json.encode()).hexdigest(),
        event.logical_time != ref.logical_time, state.logical_time is None,
        state.logical_time is not None and event.logical_time > state.logical_time,
        event.event_id not in commit.event_ids,
        commit.world_revision < ref.world_revision, commit.world_revision > state.world_revision,
        commit.deliberation_revision > state.deliberation_revision,
        commit.ledger_sequence > state.ledger_sequence,
    )):
        raise ValueError("active_attempt_consequence.event_not_exact")
    return event


def read_active_attempt_consequence(
    *, ledger, content_store, pinned_state, actor_ref: str,
    execution_event_ref: str, clock_event_ref: str,
) -> ActiveAttemptConsequence | None:
    """Read the current owned active head, never an older attempt of that Plan."""
    from .life_events import ActivityTransitionPayload
    from .world_consequence_execution_context import build_world_consequence_execution_materials

    cursor = ProjectionCursor(
        world_revision=pinned_state.world_revision,
        deliberation_revision=pinned_state.deliberation_revision,
        ledger_sequence=pinned_state.ledger_sequence,
    )
    if ledger.world_id != pinned_state.world_id or ledger.project_at(cursor) != pinned_state:
        raise ValueError("active_attempt_consequence.original_pin_required")
    refs = {item.event_id: item for item in pinned_state.committed_world_event_refs}
    source = refs.get(execution_event_ref)
    if source is None or source.event_type not in {"ActivityStarted", "ActivityResumed"}:
        return None
    execution = _exact_event(ledger, pinned_state, source)
    transition = ActivityTransitionPayload.model_validate_json(execution.payload_json)
    plan = next((item for item in pinned_state.plans if item.plan_id == transition.plan_id), None)
    if plan is None or any((
        plan.owner_actor_ref != actor_ref, plan.status != "active",
        plan.privacy_class == "withhold", plan.authority_origin is None,
    )):
        return None
    origin = plan.authority_origin
    if any((
        origin.accepted_event_ref != source.event_id,
        origin.accepted_event_type != source.event_type,
        origin.accepted_world_revision != source.world_revision,
        transition.expected_entity_revision + 1 != plan.entity_revision,
        transition.transitioned_at != execution.logical_time,
    )):
        return None
    clocks = [item for item in refs.values() if item.event_type == "ClockAdvanced"]
    clock_ref = max(clocks, key=lambda item: item.world_revision, default=None)
    if clock_ref is None or clock_ref.event_id != clock_event_ref:
        return None
    clock = _exact_event(ledger, pinned_state, clock_ref)
    if clock.logical_time != pinned_state.logical_time or execution.logical_time > clock.logical_time:
        return None
    authority = derive_world_consequence_authority(
        pinned_state=pinned_state, actor_ref=actor_ref, source_events=(execution,),
    )
    (binding,) = authority.execution_bindings
    (material,) = build_world_consequence_execution_materials(
        ledger=ledger, content_store=content_store, pinned_state=pinned_state,
        actor_ref=actor_ref, source_events=(execution,), intention_reader_version="2",
    )
    if material.status != "available" or material.execution_binding != binding:
        return None
    return ActiveAttemptConsequence(
        execution_binding=binding, execution_started_at=execution.logical_time,
        active_plan_revision=plan.entity_revision,
        clock_event_ref=clock.event_id, clock_payload_hash=clock.payload_hash,
        clock_world_revision=clock_ref.world_revision, current_logical_time=clock.logical_time,
    )


def validate_active_attempt_consequence(
    *, ledger, content_store, pinned_state, actor_ref: str, descriptor: ActiveAttemptConsequence,
) -> None:
    supplied = ActiveAttemptConsequence.model_validate_json(descriptor.model_dump_json())
    expected = read_active_attempt_consequence(
        ledger=ledger, content_store=content_store, pinned_state=pinned_state, actor_ref=actor_ref,
        execution_event_ref=supplied.execution_binding.source_event_ref,
        clock_event_ref=supplied.clock_event_ref,
    )
    if expected is None or expected != supplied:
        raise ValueError("active_attempt_consequence.original_active_head_mismatch")
