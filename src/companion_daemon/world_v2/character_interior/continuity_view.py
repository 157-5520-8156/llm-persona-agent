"""Lossless additions to current private-choice views, not memory decisions."""

from dataclasses import replace


CONTRACT = "private-choice-continuity.1"
CHOICE_PURPOSES = frozenset({"activity_lifecycle_choice", "life_development_choice", "outcome_selection"})


def continuity_profile(profile, *, lifecycle_states: bool):
    additions = ("selected_recall", "activity_lifecycle_states") if lifecycle_states else ("selected_recall",)
    return replace(
        profile,
        profile_id=profile.profile_id + "/" + CONTRACT,
        snapshot_material_keys=tuple(dict.fromkeys((*profile.snapshot_material_keys, *additions))),
    )
