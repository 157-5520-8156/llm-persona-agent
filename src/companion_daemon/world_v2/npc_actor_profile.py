"""Minimal source-bound capsule for one NPC actor decision."""

from __future__ import annotations

from .npc_identity_view import NpcIdentityView
from .npc_civil_time import NpcCivilTime


def compile_npc_actor_profile(
    *,
    identity: NpcIdentityView,
    logical_time: object,
    pending_impulse_summary: str | None = None,
    civil_time: NpcCivilTime | None = None,
) -> dict[str, object]:
    """Compile the NPC Actor Profile: one actor, no protagonist interior."""

    logical_time_text = (
        logical_time.isoformat()
        if hasattr(logical_time, "isoformat")
        else logical_time
    )
    shared_history = identity.shared_history_with_protagonist
    return {
        "who_i_am": {
            "npc_ref": identity.npc_ref,
            "descriptor": identity.descriptor,
            "lifecycle_state": identity.lifecycle_state,
        },
        "relationship_to_her": (
            identity.npc_relationship_to_protagonist.model_dump(mode="json")
            if identity.npc_relationship_to_protagonist is not None
            else None
        ),
        "shared_history_with_her": (
            shared_history.model_dump(mode="json") if shared_history is not None else None
        ),
        "shared_experience_summaries": list(identity.shared_experience_summaries[-4:]),
        "my_last_state": identity.inner_state,
        "my_goals": list(identity.goal_summaries[:4]),
        "open_plans": list(identity.active_plan_refs),
        "pending_impulse_summary": pending_impulse_summary,
        "now": {
            "logical_time": logical_time_text,
            "current_location_ref": identity.current_location_ref,
            "civil_time": (civil_time or NpcCivilTime()).model_dump(mode="json"),
        },
        "private_source_refs": list(identity.private_source_refs),
    }


__all__ = ["compile_npc_actor_profile"]
