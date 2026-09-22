"""Cost must not grow with age: every block sent to a lane must be bounded.

Dynamic loading is only meaningful if what a lane receives depends on the
current situation rather than on how long the ledger is.  Measured across 64
runs the rendered payload follows history^0.38 (r=0.74), and along one
continuation chain the uncapped blocks doubled while the budgeted slices stayed
flat (routine_background 1304 -> 1304, situation 1478 -> 1460).

This module is the ratchet.  A material with no item cap must be listed below
with the reason it cannot grow, or the suite fails and someone has to decide.
It does not claim the listed blocks are small - only that they have a bound.
"""

from __future__ import annotations

import pytest

from companion_daemon.world_v2.background_context_profile import (
    background_context_profile_for_purpose,
)

# A material with no ``snapshot_material_limits`` entry must appear here.
# ``single`` means the block is one object or one value, so it cannot grow with
# history.  Anything else needs an argument, not an entry.
_STRUCTURALLY_SINGLE = frozenset({
    'stable_self',
    'biographical_context',
    'day_sheet',
    'routine_background',
    'week_diary',
    'situation',
    'relationship',
    'since_he_last_spoke',
    'logical_time',
    'change_phase',
    'interruption',
    'perception',
    'messages_waiting_to_send',
    'recalled_emotional_associations',
    'automatic_prefetch',
})

# Bounded by how many such things the character can have at once, which is
# itself bounded by her own plan and relationship count rather than by ledger
# length.  Listed separately because the bound is a product invariant, not a
# structural property of the block.
_BOUNDED_BY_CHARACTER_STATE = frozenset({
    'current_activities',
    'planned_activities',
    'recently_ended_activities',
    'protagonist_npc_relationships',
    'npc_observable_attitudes',
    'unresolved',
    'aspirations',
})

# Known to accumulate.  These are the leak, named so they cannot be forgotten,
# and they are what a fix has to bound.
_KNOWN_ACCUMULATING = frozenset({
    # Her emotional history has no item cap and measured 3653 -> 8431 characters
    # along one continuation chain.  Bounding it is a semantic decision about
    # what she remembers, so it needs its own evidence before it changes.
    'affect',
    # Suspected, not verified.  It measured 6472 characters in the appraisal
    # snapshot, and the only construction site found was a column mapping - no
    # window was confirmed.  It is listed here rather than declared bounded so
    # the uncertainty is visible instead of assumed away.
    'interaction_acts',
})


@pytest.mark.parametrize('purpose', ['world_stimulus_appraisal', 'activity_lifecycle_choice'])
def test_every_uncapped_material_is_declared(purpose: str) -> None:
    profile = background_context_profile_for_purpose(purpose)
    uncapped = set(profile.snapshot_material_keys) - set(profile.snapshot_material_limits)
    declared = _STRUCTURALLY_SINGLE | _BOUNDED_BY_CHARACTER_STATE | _KNOWN_ACCUMULATING
    undeclared = uncapped - declared
    assert not undeclared, (
        f'{purpose} sends uncapped materials that nothing declares: {sorted(undeclared)}. '
        'Either cap it in snapshot_material_limits or add it to this module with a reason.'
    )


@pytest.mark.parametrize('purpose', ['world_stimulus_appraisal', 'activity_lifecycle_choice'])
def test_declared_uncapped_materials_are_still_uncapped(purpose: str) -> None:
    """A declaration must not outlive the condition it describes."""

    profile = background_context_profile_for_purpose(purpose)
    capped = set(profile.snapshot_material_limits)
    stale = (_STRUCTURALLY_SINGLE | _BOUNDED_BY_CHARACTER_STATE | _KNOWN_ACCUMULATING) & capped
    # A block that gained a cap is progress; the entry must move out of the
    # accumulating set rather than stay as a stale claim.
    assert not (stale & _KNOWN_ACCUMULATING), (
        f'{sorted(stale & _KNOWN_ACCUMULATING)} now has a cap; remove it from _KNOWN_ACCUMULATING'
    )


def test_the_inventory_compaction_stays_off_until_a_presentation_carries_it() -> None:
    """The compaction is built and measured, but no lane renders it yet.

    Wiring it into the appraisal lane changes bytes that 36 legacy goldens pin,
    because those cases copy the current production view instead of rendering
    their own frozen presentation. Whether to re-baseline them is a decision
    about what the guard is for, not a code detail, so the flag stays off and
    this test keeps the fact visible instead of letting it be forgotten.
    """

    import inspect

    from companion_daemon.world_v2.background_context_profile import (
        collapse_presented_source_inventory,
        slice_background_inner_life_snapshot,
    )

    default = inspect.signature(
        slice_background_inner_life_snapshot
    ).parameters['compact_source_inventory'].default
    assert default is False
    assert callable(collapse_presented_source_inventory)
