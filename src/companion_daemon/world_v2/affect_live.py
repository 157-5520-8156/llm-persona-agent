"""Read-time affect intensity: a pure function of the last authored anchor and time.

Stored ``intensity_bp`` stays at the last authored or decay-event value so the
reducer head hash does not move with the clock.  Readers see the live value.
"""

from __future__ import annotations

from datetime import datetime

from .affect_math import (
    ALGORITHM_VERSION,
    FACTOR_TABLE_DIGEST,
    DecayAnchor,
    DecayProfile,
    decay_intensity_bp,
)
from .schemas import (
    AffectBaselineProjection,
    AffectComponentProjection,
    AffectEpisodeProjection,
)


def live_component_intensity_bp(
    component: AffectComponentProjection,
    *,
    baseline_bp: int,
    at: datetime,
) -> int:
    profile = component.decay_profile
    if (
        profile.algorithm_version != ALGORITHM_VERSION
        or profile.table_digest != FACTOR_TABLE_DIGEST
    ):
        return component.intensity_bp
    try:
        return decay_intensity_bp(
            DecayAnchor(
                intensity_bp=component.decay_anchor_intensity_bp,
                anchored_at=component.decay_anchor_at,
                baseline_bp=baseline_bp,
                residue_bp=component.residue_bp,
                decay_not_before=component.decay_not_before,
            ),
            DecayProfile(
                half_life_seconds=profile.half_life_seconds,
                floor_bp=profile.floor_bp,
                delay_seconds=profile.delay_seconds,
                config_version=profile.config_version,
                kind=profile.kind,
            ),
            at,
        )
    except (TypeError, ValueError):
        return component.intensity_bp


def materialize_affect_episodes(
    episodes: tuple[AffectEpisodeProjection, ...],
    *,
    logical_time: datetime | None,
    baselines: tuple[AffectBaselineProjection, ...] = (),
) -> tuple[AffectEpisodeProjection, ...]:
    """Copy episodes with intensity_bp computed at logical_time. Anchors stay put."""

    if logical_time is None or not episodes:
        return episodes
    baseline_by_dimension = {item.dimension: item.baseline_bp for item in baselines}
    materialized: list[AffectEpisodeProjection] = []
    for episode in episodes:
        components = []
        changed = False
        for component in episode.components:
            live = live_component_intensity_bp(
                component,
                baseline_bp=baseline_by_dimension.get(component.dimension, 0),
                at=logical_time,
            )
            if live != component.intensity_bp:
                changed = True
                components.append(component.model_copy(update={"intensity_bp": live}))
            else:
                components.append(component)
        if changed:
            materialized.append(
                episode.model_copy(update={"components": tuple(components)})
            )
        else:
            materialized.append(episode)
    return tuple(materialized)


__all__ = ["live_component_intensity_bp", "materialize_affect_episodes"]
