"""Mechanical Appraisal expiry for ``WorldRuntime.advance``.

The deadline is already on the appraisal (authored or compiler-derived).
This lane only executes that recorded ``expires_at``.  It does not choose
which reading still matters.
"""

from __future__ import annotations

from .appraisal_events import AppraisalExpiredPayload
from .event_identity import domain_idempotency_key
from .schemas import AppraisalProjection, ClockObservation, EvidenceRef, WorldEvent


APPRAISAL_EXPIRY_POLICY_REFS = ("policy:appraisal-v1",)


def build_due_appraisal_expiry_events(
    *,
    world_id: str,
    appraisals: tuple[AppraisalProjection, ...],
    clock: ClockObservation,
    clock_event: WorldEvent,
) -> list[WorldEvent]:
    """Expire every active appraisal whose own deadline has been reached."""

    if clock.world_id != world_id:
        raise ValueError("appraisal expiry clock belongs to another world")
    due = [
        item
        for item in appraisals
        if item.status == "active" and clock.logical_time_to >= item.expires_at
    ]
    due.sort(key=lambda item: (item.expires_at, item.appraisal_id))
    return [
        build_appraisal_expiry_event(
            world_id=world_id,
            appraisal=item,
            clock=clock,
            clock_event=clock_event,
        )
        for item in due
    ]


def build_appraisal_expiry_event(
    *,
    world_id: str,
    appraisal: AppraisalProjection,
    clock: ClockObservation,
    clock_event: WorldEvent,
) -> WorldEvent:
    """Build the one canonical expiry event for a due, still-active appraisal."""

    if clock.world_id != world_id:
        raise ValueError("appraisal expiry clock belongs to another world")
    if clock_event.world_id != world_id or clock_event.event_type != "ClockAdvanced":
        raise ValueError("appraisal expiry requires its ClockAdvanced authority")
    if clock_event.logical_time != clock.logical_time_to:
        raise ValueError("appraisal expiry clock event does not match the observation")

    transition_id = (
        f"transition:appraisal-expired:{appraisal.appraisal_id}:"
        f"{appraisal.entity_revision}:{clock_event.event_id}"
    )
    payload = AppraisalExpiredPayload(
        change_id=f"change:appraisal-expired:{appraisal.appraisal_id}:{appraisal.entity_revision}",
        transition_id=transition_id,
        expected_entity_revision=appraisal.entity_revision,
        evidence_refs=(
            EvidenceRef(
                ref_id=f"clock:{clock.logical_time_to.isoformat()}",
                evidence_type="clock_observation",
                claim_purpose="current_fact",
            ),
        ),
        policy_refs=APPRAISAL_EXPIRY_POLICY_REFS,
        appraisal_id=appraisal.appraisal_id,
        expired_at=clock.logical_time_to,
    ).model_dump(mode="json")
    event_type = "AppraisalExpired"
    return WorldEvent.from_payload(
        schema_version=clock.schema_version,
        event_id=f"event:appraisal-expired:{appraisal.appraisal_id}:{appraisal.entity_revision}:{clock_event.event_id}",
        world_id=world_id,
        event_type=event_type,
        logical_time=clock.logical_time_to,
        created_at=clock.created_at,
        actor="system:appraisal-clock",
        source="scheduler",
        trace_id=clock.trace_id,
        causation_id=clock_event.event_id,
        correlation_id=clock.correlation_id,
        idempotency_key=domain_idempotency_key(
            event_type=event_type, world_id=world_id, payload=payload
        )
        or transition_id,
        payload=payload,
    )


__all__ = [
    "APPRAISAL_EXPIRY_POLICY_REFS",
    "build_appraisal_expiry_event",
    "build_due_appraisal_expiry_events",
]
