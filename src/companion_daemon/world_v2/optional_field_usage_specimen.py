"""Registry gate: optional model-facing fields must have a filled usage specimen.

The recurring failure is a null-only canonical shape with no paired example of
when the field is worth writing.  This module enumerates those fields and fails
closed when a new optional surface ships without a concrete usage block.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .character_interior.inbound_author import PRIVATE_TURN_STATE_OPTIONAL_SPECIMEN_KEYS
from .life_development_runtime import disturbance_consequence_usage_specimen
from .present_prompt import (
    SLIM_OPTIONAL_SPECIMEN_KEYS,
    affect_usage_specimen,
    calm_affect_usage_specimen,
    come_back_usage_specimen,
    declared_display_usage_specimen,
    later_omission_specimen,
    later_usage_specimen,
    matters_bp_usage_specimen,
    photo_prose_only_specimen,
    photo_usage_specimen,
    proactive_contact_usage_specimens,
    relationship_commitment_usage_specimen,
    relationship_delta_usage_specimen,
    relationship_reading_only_specimen,
    silent_usage_specimen,
    stuck_impression_usage_specimen,
    waiting_for_omission_specimen,
    waiting_for_usage_specimen,
)


@dataclass(frozen=True)
class OptionalFieldSpec:
    field_id: str
    prove: Callable[[], None]
    notes: str = ""


# Fields that are legitimately empty most turns and do not require a filled
# usage specimen beyond the null shape advertisement.
LEGITIMATELY_MOSTLY_EMPTY: frozenset[str] = frozenset(
    {
        "episode_id",
        "resolution_summary",
        "wants",
        "how_it_landed",
        "noticed",
        "interaction_act",
        "aspiration_transition",
        "character_life_direction",
    }
)


def _require_non_null(specimen_fn: Callable[[], Mapping[str, Any]], *keys: str) -> None:
    specimen = specimen_fn()
    if not keys:
        if not any(value is not None for value in specimen.values()):
            raise AssertionError(f"{specimen_fn.__name__} has no non-null values")
        return
    if not any(specimen.get(key) is not None for key in keys):
        raise AssertionError(
            f"{specimen_fn.__name__} missing non-null for: {', '.join(keys)}"
        )


def _prove_disturbance_dynamic_life_direction() -> None:
    specimen = disturbance_consequence_usage_specimen()
    if specimen.get("dynamic_life_direction") is None:
        raise AssertionError("disturbance specimen missing dynamic_life_direction")


def _prove_disturbance_objective_biographical_transition() -> None:
    specimen = disturbance_consequence_usage_specimen()
    if specimen.get("objective_biographical_transition_example") is None:
        raise AssertionError(
            "disturbance specimen missing objective_biographical_transition_example"
        )


def _prove_proactive_waiting_for() -> None:
    specimen = proactive_contact_usage_specimens()["waiting_for"]
    _require_non_null(lambda: specimen, "waiting_for", "wait")


def _prove_proactive_we_are() -> None:
    specimen = proactive_contact_usage_specimens()["we_are"]
    _require_non_null(lambda: specimen, "we_are", "calling_it", "said_as")


def _prove_proactive_affect() -> None:
    specimen = proactive_contact_usage_specimens()["affect"]
    draft = specimen.get("appraisal_draft")
    if not isinstance(draft, dict) or draft.get("affect") != "open" or not draft.get(
        "components"
    ):
        raise AssertionError("proactive affect specimen missing open+components")


OPTIONAL_FIELD_SPECS: tuple[OptionalFieldSpec, ...] = (
    OptionalFieldSpec(
        "we_are",
        lambda: _require_non_null(relationship_commitment_usage_specimen, "we_are"),
    ),
    OptionalFieldSpec(
        "calling_it",
        lambda: _require_non_null(relationship_commitment_usage_specimen, "calling_it"),
    ),
    OptionalFieldSpec(
        "said_as",
        lambda: _require_non_null(relationship_commitment_usage_specimen, "said_as"),
    ),
    OptionalFieldSpec(
        "affect",
        lambda: _require_non_null(affect_usage_specimen, "affect"),
    ),
    OptionalFieldSpec(
        "components",
        lambda: _require_non_null(affect_usage_specimen, "components"),
    ),
    OptionalFieldSpec(
        "photo",
        lambda: _require_non_null(photo_usage_specimen, "photo"),
    ),
    OptionalFieldSpec(
        "declared_display",
        lambda: _require_non_null(declared_display_usage_specimen, "declared_display"),
    ),
    OptionalFieldSpec(
        "waiting_for",
        lambda: _require_non_null(waiting_for_usage_specimen, "waiting_for"),
    ),
    OptionalFieldSpec(
        "wait",
        lambda: _require_non_null(waiting_for_usage_specimen, "wait"),
    ),
    OptionalFieldSpec(
        "pressure_bp",
        lambda: _require_non_null(waiting_for_usage_specimen, "pressure_bp"),
    ),
    OptionalFieldSpec(
        "importance_bp",
        lambda: _require_non_null(waiting_for_usage_specimen, "importance_bp"),
    ),
    OptionalFieldSpec(
        "later",
        lambda: _require_non_null(later_usage_specimen, "later"),
    ),
    OptionalFieldSpec(
        "come_back",
        lambda: _require_non_null(come_back_usage_specimen, "come_back"),
    ),
    OptionalFieldSpec(
        "come_back_in",
        lambda: _require_non_null(come_back_usage_specimen, "come_back_in"),
    ),
    OptionalFieldSpec(
        "us_deltas",
        lambda: _require_non_null(relationship_delta_usage_specimen, "us_deltas"),
    ),
    OptionalFieldSpec(
        "about_us",
        lambda: _require_non_null(
            relationship_delta_usage_specimen,
            "about_us",
        ),
    ),
    OptionalFieldSpec(
        "why_us",
        lambda: _require_non_null(relationship_delta_usage_specimen, "why_us"),
    ),
    OptionalFieldSpec(
        "matters_bp",
        lambda: _require_non_null(matters_bp_usage_specimen, "matters_bp"),
    ),
    OptionalFieldSpec(
        "stuck_with_me",
        lambda: _require_non_null(stuck_impression_usage_specimen, "stuck_with_me"),
    ),
    OptionalFieldSpec(
        "keep_impression",
        lambda: _require_non_null(stuck_impression_usage_specimen, "keep_impression"),
    ),
    OptionalFieldSpec(
        "messages",
        lambda: _require_non_null(silent_usage_specimen, "messages"),
        notes="silent empty-array usage",
    ),
    OptionalFieldSpec(
        "dynamic_life_direction",
        _prove_disturbance_dynamic_life_direction,
    ),
    OptionalFieldSpec(
        "objective_biographical_transition",
        _prove_disturbance_objective_biographical_transition,
    ),
    OptionalFieldSpec("proactive_waiting_for", _prove_proactive_waiting_for),
    OptionalFieldSpec("proactive_we_are", _prove_proactive_we_are),
    OptionalFieldSpec("proactive_affect", _prove_proactive_affect),
)


def assert_optional_field_usage_specimen_coverage() -> None:
    """Fail when an installed optional field has no filled usage specimen."""

    covered = {item.field_id for item in OPTIONAL_FIELD_SPECS}
    slim_private = frozenset(SLIM_OPTIONAL_SPECIMEN_KEYS) | frozenset(
        PRIVATE_TURN_STATE_OPTIONAL_SPECIMEN_KEYS
    )
    missing_registry = sorted(
        key
        for key in slim_private
        if key not in covered and key not in LEGITIMATELY_MOSTLY_EMPTY
    )
    if missing_registry:
        raise AssertionError(
            "optional fields missing registry entries: "
            + ", ".join(missing_registry)
        )

    failures: list[str] = []
    for spec in OPTIONAL_FIELD_SPECS:
        try:
            spec.prove()
        except Exception as exc:  # noqa: BLE001 - gate must surface every miss
            failures.append(f"{spec.field_id} ({spec.notes or spec.prove.__name__}): {exc}")
    if failures:
        raise AssertionError(
            "optional field usage specimen gaps:\n- " + "\n- ".join(failures)
        )

    # Omission specimens remain reachable teaching tools, not registry keys.
    _require_non_null(calm_affect_usage_specimen)
    _require_non_null(later_omission_specimen)
    _require_non_null(waiting_for_omission_specimen)
    _require_non_null(photo_prose_only_specimen)
    _require_non_null(relationship_reading_only_specimen)


__all__ = (
    "LEGITIMATELY_MOSTLY_EMPTY",
    "OPTIONAL_FIELD_SPECS",
    "assert_optional_field_usage_specimen_coverage",
)
