"""Read exact owned Plan lifecycle heads without granting intention or outcome truth."""
from __future__ import annotations

from .schemas import ProjectionCursor, validate_plan_authority_state
from .world_life_context import ActivityLifecycleStateContextItem, WorldLifeSourceBinding


MAX_LIFECYCLE_STATES = 3


def lifecycle_states_from_projection(*, projection, actor_ref, cursor, viewer_privacy_ceiling):
    if cursor is None or cursor != ProjectionCursor(
        world_revision=projection.world_revision,
        deliberation_revision=projection.deliberation_revision,
        ledger_sequence=projection.ledger_sequence,
    ) or viewer_privacy_ceiling not in {"private", "withhold"}:
        return ()
    candidates = [plan for plan in projection.plans
                  if plan.owner_actor_ref == actor_ref and plan.status in {"paused", "abandoned"}
                  and plan.privacy_class != "withhold"]
    if any(plan.authority_origin is None for plan in candidates):
        raise ValueError("owned lifecycle state lacks its accepted Plan authority")
    selected = tuple(sorted(candidates, key=lambda plan: (
        -plan.authority_origin.accepted_at.timestamp(), plan.plan_id,
    ))[:MAX_LIFECYCLE_STATES])
    if not selected:
        return ()
    # Bound attention before validation/material construction. The complete
    # projection is already reducer-validated; verify this read's exact heads
    # together so the committed-event index is built only once.
    validate_plan_authority_state(selected, projection.committed_world_event_refs,
                                  logical_time=projection.logical_time)
    states = []
    for plan in selected:
        origin = plan.authority_origin
        states.append(ActivityLifecycleStateContextItem(
            activity_event_ref=origin.accepted_event_ref, plan_id=plan.plan_id,
            plan_entity_revision=plan.entity_revision, owner_actor_ref=actor_ref,
            status=plan.status, transitioned_at=origin.accepted_at,
            event_type=origin.accepted_event_type, transition_id=origin.transition_id,
            plan_projection_hash=origin.authority_projection_hash,
            plan_binding_hash=origin.binding_hash, privacy_class=plan.privacy_class,
            source_bindings=(WorldLifeSourceBinding(
                authority_event_ref=origin.accepted_event_ref,
                authority_world_revision=origin.accepted_world_revision,
                authority_payload_hash=origin.accepted_payload_hash,
            ),),
        ))
    return tuple(states)
