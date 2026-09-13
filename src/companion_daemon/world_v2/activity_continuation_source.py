"""A completed owned activity can offer thought, never dictate the next act."""

from pydantic import Field

from .schema_core import FrozenModel


class ActivityCompletionSource(FrozenModel):
    event_ref: str = Field(min_length=1)
    world_revision: int = Field(ge=1)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_id: str = Field(min_length=1)
    plan_revision: int = Field(ge=1)


def validate_completion_source(state, source, *, actor_ref, cursor_revision):
    """Validate the same exact terminal authority at acceptance and replay."""
    from .schemas import validate_plan_authority_state

    event = next((x for x in state.committed_world_event_refs if x.event_id == source.event_ref), None)
    plan = next((x for x in state.plans if x.plan_id == source.plan_id), None)
    if event is None or plan is None or any((
        event.event_type != "ActivityCompleted",
        event.world_revision != source.world_revision,
        event.payload_hash != source.payload_hash,
        event.world_revision > cursor_revision,
        plan.owner_actor_ref != actor_ref,
        plan.entity_revision != source.plan_revision,
        plan.status != "completed",
        plan.authority_origin is None,
    )):
        raise ValueError("activity_continuation.completion_authority_invalid")
    if plan.authority_origin.accepted_event_ref != event.event_id:
        raise ValueError("activity_continuation.completion_plan_mismatch")
    validate_plan_authority_state(
        (plan,), state.committed_world_event_refs, logical_time=state.logical_time,
    )
    return event


def latest_completion_source(state, *, actor_ref):
    """Offer only the latest owned completion, without reviving an old backlog."""
    candidates = [
        plan for plan in state.plans
        if plan.owner_actor_ref == actor_ref and plan.status == "completed"
        and plan.authority_origin is not None
    ]
    if not candidates:
        return None
    plan = max(candidates, key=lambda x: x.authority_origin.accepted_world_revision)
    origin = plan.authority_origin
    # The existing activity catalog owns remaining live plans. Wait for that
    # work to end instead of spinning fresh empty-catalog opportunities while
    # another owned plan still requires a lifecycle choice.
    if any(
        x.owner_actor_ref == actor_ref and x.status in {"planned", "active", "paused"}
        for x in state.plans
    ):
        return None
    source = ActivityCompletionSource(
        event_ref=origin.accepted_event_ref,
        world_revision=origin.accepted_world_revision,
        payload_hash=origin.accepted_payload_hash,
        plan_id=plan.plan_id,
        plan_revision=plan.entity_revision,
    )
    validate_completion_source(state, source, actor_ref=actor_ref, cursor_revision=state.world_revision)
    return source
