"""Lossless additions to current private-choice views, not memory decisions."""

from dataclasses import replace

from .life_context_presentation import LIFE_CONTEXT_COMPILER_VERSION, LIVED_PREFETCH_COMPILER_VERSIONS


CONTRACT = "private-choice-continuity.1"
CHOICE_PURPOSES = frozenset({"activity_lifecycle_choice", "life_development_choice", "outcome_selection"})


def continuity_profile(profile, *, lifecycle_states: bool):
    additions = ("selected_recall", "activity_lifecycle_states") if lifecycle_states else ("selected_recall",)
    return replace(
        profile,
        profile_id=profile.profile_id + "/" + CONTRACT,
        snapshot_material_keys=tuple(dict.fromkeys((*profile.snapshot_material_keys, *additions))),
    )


AUTOMATIC_PREFETCH_CONTRACT = "lived-moment-recall.1"
# Purposes in which an unbidden memory may surface beside what she just lived.
LIVED_RECALL_PURPOSES = frozenset({
    "world_stimulus_appraisal", "proactive_contact", "private_impression_reflection",
})


def with_automatic_prefetch(profile, materials, *, compiler_version=LIFE_CONTEXT_COMPILER_VERSION):
    """Keep an automatic association the CharacterInterior already bound.

    Purpose whitelists bound cost; they must not silently discard the memory
    that surfaced for this very turn.  Decided only from the snapshot itself,
    so the sent view and every later re-derivation of it agree.
    """

    if compiler_version not in LIVED_PREFETCH_COMPILER_VERSIONS:
        return profile
    if not profile.snapshot_material_keys or "automatic_prefetch" not in (materials or {}):
        return profile
    if "automatic_prefetch" in profile.snapshot_material_keys:
        return profile
    return replace(
        profile,
        profile_id=profile.profile_id + "/" + AUTOMATIC_PREFETCH_CONTRACT,
        snapshot_material_keys=(*profile.snapshot_material_keys, "automatic_prefetch"),
    )
