"""Residue-floor Affect close for ``WorldRuntime.advance``.

H10 computes intensity at read time, so clock ticks do not rewrite the decay
curve.  Once every live component is at its recorded residue/floor, the
episode leaves the unsettled working set.  That is a natural process, not
``resolve`` (her decision to let go).
"""

from __future__ import annotations

from .affect_events import AffectComponentDecay, AffectEpisodeDecayedPayload
from .affect_live import episode_at_residue_floor, live_component_intensity_bp
from .event_identity import domain_idempotency_key
from .schemas import (
    AffectBaselineProjection,
    AffectEpisodeProjection,
    ClockObservation,
    EvidenceRef,
    WorldEvent,
)


def build_due_affect_residue_close_events(
    *,
    world_id: str,
    episodes: tuple[AffectEpisodeProjection, ...],
    baselines: tuple[AffectBaselineProjection, ...],
    clock: ClockObservation,
    clock_event: WorldEvent,
) -> list[WorldEvent]:
    """Close active episodes whose live intensity has reached residue/floor."""

    if clock.world_id != world_id:
        raise ValueError("affect residue close clock belongs to another world")
    due = [
        item
        for item in episodes
        if episode_at_residue_floor(
            item,
            logical_time=clock.logical_time_to,
            baselines=baselines,
        )
    ]
    due.sort(key=lambda item: (item.opened_at, item.episode_id))
    return [
        build_affect_residue_close_event(
            world_id=world_id,
            episode=item,
            baselines=baselines,
            clock=clock,
            clock_event=clock_event,
        )
        for item in due
    ]


def build_affect_residue_close_event(
    *,
    world_id: str,
    episode: AffectEpisodeProjection,
    baselines: tuple[AffectBaselineProjection, ...],
    clock: ClockObservation,
    clock_event: WorldEvent,
) -> WorldEvent:
    """Materialize residue intensity once and close the active slot."""

    if clock.world_id != world_id:
        raise ValueError("affect residue close clock belongs to another world")
    if clock_event.world_id != world_id or clock_event.event_type != "ClockAdvanced":
        raise ValueError("affect residue close requires its ClockAdvanced authority")
    if clock_event.logical_time != clock.logical_time_to:
        raise ValueError("affect residue close clock event does not match the observation")

    baseline_by_dimension = {item.dimension: item.baseline_bp for item in baselines}
    results = tuple(
        AffectComponentDecay(
            component_id=component.component_id,
            before_intensity_bp=component.decay_anchor_intensity_bp,
            after_intensity_bp=live_component_intensity_bp(
                component,
                baseline_bp=baseline_by_dimension.get(component.dimension, 0),
                at=clock.logical_time_to,
            ),
            config_version=component.decay_profile.config_version,
            table_digest=component.decay_profile.table_digest,
            config_digest=component.decay_profile.config_digest,
        )
        for component in episode.components
    )
    payload = AffectEpisodeDecayedPayload(
        change_id=f"change:affect-decay:{episode.episode_id}:{episode.entity_revision}",
        transition_id=(
            f"transition:affect-decay:{episode.episode_id}:"
            f"{episode.entity_revision}:{clock_event.event_id}"
        ),
        expected_entity_revision=episode.entity_revision,
        evidence_refs=(
            EvidenceRef(
                ref_id=f"clock:{clock.logical_time_to.isoformat()}",
                evidence_type="clock_observation",
                claim_purpose="current_fact",
            ),
        ),
        policy_refs=episode.origin.policy_refs,
        episode_id=episode.episode_id,
        from_logical_time=episode.updated_at,
        to_logical_time=clock.logical_time_to,
        component_results=results,
    ).model_dump(mode="json")
    event_type = "AffectEpisodeDecayed"
    return WorldEvent.from_payload(
        schema_version=clock.schema_version,
        event_id=(
            f"event:affect-decay:{episode.episode_id}:"
            f"{episode.entity_revision}:{clock_event.event_id}"
        ),
        world_id=world_id,
        event_type=event_type,
        logical_time=clock.logical_time_to,
        created_at=clock.created_at,
        actor="system:affect-clock",
        source="scheduler",
        trace_id=clock.trace_id,
        causation_id=clock_event.event_id,
        correlation_id=clock.correlation_id,
        idempotency_key=domain_idempotency_key(
            event_type=event_type, world_id=world_id, payload=payload
        )
        or f"affect-decay:{episode.episode_id}:{episode.entity_revision}:{clock.tick_id}",
        payload=payload,
    )


__all__ = [
    "build_affect_residue_close_event",
    "build_due_affect_residue_close_events",
]
