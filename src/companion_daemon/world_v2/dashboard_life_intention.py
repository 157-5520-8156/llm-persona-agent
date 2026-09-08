"""Read an accepted role intention at the owner's captured ledger prefix.

This display reader grants no activity or source authority. Domain validators
re-prove the original role choice; only its text and closed source kind leave
this module. It never projects a newer head or searches nearby events.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .life_events import ActivityPlannedPayload
from .schemas import LedgerProjection, PlanStateProjection, WorldEvent, validate_plan_authority_state


@dataclass(frozen=True)
class DashboardLifeIntention:
    intention: str
    source_kind: Literal["chat", "world", "day_open"]
    selected_at: datetime


def read_dashboard_life_intention(
    *, ledger, projection: LedgerProjection, plan: PlanStateProjection,
) -> DashboardLifeIntention | None:
    """Return only a verified original intention; missing proof stays absent."""
    if (
        not isinstance(plan, PlanStateProjection)
        or plan.privacy_class == "withhold"
        or not plan.owner_actor_ref
        or plan not in projection.plans
        or ledger.world_id != projection.world_id
        or not callable(getattr(ledger, "lookup_event_commit", None))
    ):
        return None
    from .chat_life_intent_runtime import derive_chat_life_plan, validate_chat_life_plan_event
    from .day_open_life_intent_runtime import (
        derive_day_open_life_plan, validate_day_open_life_plan_event,
    )
    from .world_life_intent_runtime import derive_world_life_plan, validate_world_life_plan_event

    families = (
        ("chat", "chat_intent_origin", derive_chat_life_plan, validate_chat_life_plan_event),
        ("world", "world_intent_origin", derive_world_life_plan, validate_world_life_plan_event),
        ("day_open", "day_open_intent_origin", derive_day_open_life_plan, validate_day_open_life_plan_event),
    )
    for kind, origin_field, derive, validate in families:
        identity = "day-open" if kind == "day_open" else kind
        prefix = f"plan:{identity}-life-intent:"
        if not plan.plan_id.startswith(prefix):
            continue
        try:
            validate_plan_authority_state(
                (plan,), projection.committed_world_event_refs,
                logical_time=projection.logical_time,
            )
            creation_ref = f"event:{identity}-life-intent:" + plan.plan_id.removeprefix(prefix)
            created = _event_at(ledger, projection, creation_ref, "ActivityPlanned")
            if created is None or plan.authority_origin is None:
                return None
            head = _event_at(
                ledger, projection, plan.authority_origin.accepted_event_ref,
                plan.authority_origin.accepted_event_type,
            )
            if head is None:
                return None
            if head.event_type != "ActivityPlanned" and head.payload().get("plan_id") != plan.plan_id:
                return None
            payload = ActivityPlannedPayload.model_validate_json(created.payload_json)
            validate(state=projection, event=created, payload=payload)
            # The lifecycle may change status and transition time, never the
            # meaning, participants or place of the original accepted Plan.
            if any(getattr(payload.plan, name) != getattr(plan, name) for name in (
                "plan_id", "activity_id", "activity_kind", "owner_actor_ref",
                "location_ref", "participant_refs", "privacy_class", "scheduled_window",
                "importance_bp", "evidence_refs", "goal_ref", "supersedes_plan_id",
            )):
                return None
            origin = getattr(payload, origin_field)
            if origin is None:
                return None
            proposal = ledger.lookup_event_commit(origin.proposal_event_ref)
            if proposal is None:
                return None
            proposal_event = WorldEvent.model_validate_json(proposal[0].model_dump_json())
            if (
                proposal_event.event_id != origin.proposal_event_ref
                or proposal_event.event_type != "ProposalRecorded"
                or proposal_event.world_id != projection.world_id
                or proposal_event.payload_hash != origin.proposal_payload_hash
                or proposal[1].ledger_sequence > projection.ledger_sequence
                or proposal[1].world_revision > projection.world_revision
                or proposal[1].deliberation_revision > projection.deliberation_revision
            ):
                return None
            _, intent = derive(
                state=projection, world_id=projection.world_id,
                proposal_id=origin.proposal_id, owner_actor_ref=plan.owner_actor_ref,
            )
            return DashboardLifeIntention(intent.intention, kind, origin.selected_at)
        except (ValueError, TypeError, KeyError):
            return None
    return None


def _event_at(ledger, projection, event_ref, event_type) -> WorldEvent | None:
    reference = next((
        item for item in projection.committed_world_event_refs if item.event_id == event_ref
    ), None)
    located = ledger.lookup_event_commit(event_ref)
    if reference is None or located is None:
        return None
    event, commit = located
    # Revalidate the complete event instead of treating a returned hash alone
    # as proof of the body the reader is about to inspect.
    event = WorldEvent.model_validate_json(event.model_dump_json())
    if (
        event.event_id != event_ref
        or event.event_type != event_type
        or reference.event_type != event_type
        or event.world_id != projection.world_id
        or event.payload_hash != reference.payload_hash
        or event.logical_time != reference.logical_time
        or commit.world_revision > projection.world_revision
        or commit.deliberation_revision > projection.deliberation_revision
        or commit.ledger_sequence > projection.ledger_sequence
    ):
        return None
    return event
