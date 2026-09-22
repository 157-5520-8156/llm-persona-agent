"""Purpose-specific background context profiles for CharacterInterior lanes.

Each background model call declares which snapshot materials and Context Capsule
slices it needs.  Undeclared content is withheld from the provider view only;
the canonical InnerLifeSnapshot and Context Capsule remain unchanged for replay
and acceptance.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .present_prompt import (
    _MATERIAL_ORDER,
    appraisal_material_rows,
    ordered_mapping,
    present_inner_life,
    recent_dialogue_material_entries,
)

BackgroundPurpose = str

# Stable-first slice ordering for DeepSeek prefix cache within one lane.
_BACKGROUND_CAPSULE_SLICE_ORDER = (
    "character_core",
    "current_situation",
    "relationship_slice",
    "relevant_facts",
    "world_life",
    "recent_experiences",
    "affect_episodes",
    "open_threads",
    "private_impressions",
    "appraisals",
    "recent_dialogue",
    "active_memory_candidates",
    "advisories",
    "media_deliveries",
    "shareable_photos",
    "pending_outbound",
    "perception_results",
    "pinned_time",
)


@dataclass(frozen=True, slots=True)
class BackgroundContextProfile:
    """One auditable background-lane provider view."""

    profile_id: str
    purposes: frozenset[BackgroundPurpose]
    snapshot_material_keys: tuple[str, ...] = ()
    snapshot_material_limits: Mapping[str, int] = ()
    capsule_slices: tuple[str, ...] = ()
    capsule_slice_limits: Mapping[str, int] = ()
    capsule_item_exclusions: Mapping[str, frozenset[str]] = ()


def _limit_material_items(value: object, limit: int, *, key: str) -> object:
    if limit <= 0 or not isinstance(limit, int):
        return value
    if key == "appraisals" and isinstance(value, dict) and "columns" in value:
        # The provider view uses a compact table, sometimes already split for
        # prefix caching.  Its rows have the same upstream ranking as the
        # canonical items and must obey the same budget and source closure.
        rows = appraisal_material_rows(value)
        return {
            **{k: v for k, v in value.items()
               if k not in {"rows", "stable_rows", "volatile_last_row"}},
            "rows": rows[:limit],
        }
    if key == "recent_dialogue" and isinstance(value, dict) and "stable_turns" in value:
        return recent_dialogue_material_entries(value)[-limit:]
    # Dialogue is chronological, unlike ranked facts/experiences.  Taking its
    # first N items erased the most recent answer while retaining the question.
    keep_tail = key in {"recent_dialogue", "folded_dialogue"}
    if isinstance(value, dict):
        items = value.get("items")
        if isinstance(items, list) and len(items) > limit:
            return {**value, "items": items[-limit:] if keep_tail else items[:limit]}
    if isinstance(value, list) and len(value) > limit:
        return value[-limit:] if keep_tail else value[:limit]
    return value


def _material_source_refs(materials: Mapping[str, object]) -> set[str]:
    refs: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, dict):
            source_ref = value.get("source_ref")
            if isinstance(source_ref, str) and source_ref:
                refs.add(source_ref)
            columns = value.get("columns")
            if isinstance(columns, list) and "ref" in columns:
                index = columns.index("ref")
                for row in appraisal_material_rows(value):
                    if len(row) > index and isinstance(row[index], str):
                        refs.add(row[index])
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for item in materials.values():
        walk(item)
    return refs


def _order_materials(materials: Mapping[str, object]) -> dict[str, object]:
    ordered: dict[str, object] = {}
    for key in _MATERIAL_ORDER:
        if key in materials:
            ordered[key] = materials[key]
    for key, value in materials.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def _filter_faculties(
    faculties: Mapping[str, object],
    kept_material_keys: set[str],
) -> dict[str, object]:
    filtered: dict[str, object] = {}
    for facet_name, facet_info in faculties.items():
        if not isinstance(facet_info, dict):
            continue
        raw_keys = facet_info.get("material_keys")
        keys = (
            [key for key in raw_keys if isinstance(key, str) and key in kept_material_keys]
            if isinstance(raw_keys, list)
            else []
        )
        filtered[facet_name] = {
            "availability": "available" if keys else "unavailable",
            "material_keys": keys,
        }
    return filtered


_LIFE_ECOLOGY_MATERIALS = (
    "stable_self",
    "biographical_context",
    "day_sheet",
    "routine_background",
    "week_diary",
    "situation",
    "relationship",
    "protagonist_npc_relationships",
    "npc_observable_attitudes",
    "affect",
    "change_phase",
    "unresolved",
    "relevant_facts",
    "recent_self_experiences",
)

_LIFE_ECOLOGY_CAPSULE_SLICES = (
    "character_core",
    "current_situation",
    "relationship_slice",
    "relevant_facts",
    "world_life",
    "recent_experiences",
    "affect_episodes",
    "open_threads",
)

# The protagonist keeps the same life, memories and unfinished concerns when
# the purpose changes.  A transport budget may bound these materials, but must
# not turn life choices into an amnesiac scheduler or contact into a separate
# chat-only self.  These are her source-bound context, never behavior rules.
# World Author and source-review profiles deliberately do not inherit this:
# access to the protagonist's private readings is actor-scoped.
_CHARACTER_CONTINUITY_MATERIALS = (
    "stable_self",
    "biographical_context",
    "day_sheet",
    "routine_background",
    "week_diary",
    "situation",
    "current_activities",
    "planned_activities",
    "recently_ended_activities",
    "relationship",
    "protagonist_npc_relationships",
    "npc_observable_attitudes",
    "unresolved",
    "aspirations",
    "recent_self_experiences",
    "pending_world_occurrences",
    "remembered_material",
    "recalled_emotional_associations",
    "private_impressions",
    "relevant_facts",
    "appraisals",
    "affect",
    "change_phase",
    "interruption",
    "perception",
    "interaction_acts",
    "messages_waiting_to_send",
    "folded_dialogue",
    "recent_dialogue",
    "since_he_last_spoke",
    "logical_time",
)
_CHARACTER_CONTINUITY_LIMITS = {
    "recent_dialogue": 12,
    "folded_dialogue": 2,
    "recent_self_experiences": 4,
    "pending_world_occurrences": 4,
    "remembered_material": 4,
    "appraisals": 4,
    "private_impressions": 8,
    "relevant_facts": 8,
}

_PROFILES: tuple[BackgroundContextProfile, ...] = (
    BackgroundContextProfile(
        profile_id="life_ecology_core",
        purposes=frozenset(
            {
                "life_development_choice",
                "activity_lifecycle_choice",
                "outcome_selection",
            }
        ),
        snapshot_material_keys=_CHARACTER_CONTINUITY_MATERIALS,
        snapshot_material_limits=_CHARACTER_CONTINUITY_LIMITS,
    ),
    BackgroundContextProfile(
        profile_id="life_world_author",
        purposes=frozenset({"life_development_draft"}),
        snapshot_material_keys=_LIFE_ECOLOGY_MATERIALS,
        snapshot_material_limits={"recent_self_experiences": 4, "relevant_facts": 8},
        capsule_slices=_LIFE_ECOLOGY_CAPSULE_SLICES,
        # This typed item carries the protagonist's accepted inner intention.
        # Her own continuity may read it; an external World Author may not.
        capsule_item_exclusions={"world_life": frozenset({"active_activity", "completed_activity", "planned_activity"})},
        capsule_slice_limits={
            "recent_experiences": 4,
            "world_life": 6,
            "relevant_facts": 8,
            "open_threads": 4,
        },
    ),
    BackgroundContextProfile(
        profile_id="stimulus_appraisal",
        purposes=frozenset({"world_stimulus_appraisal"}),
        snapshot_material_keys=_CHARACTER_CONTINUITY_MATERIALS,
        snapshot_material_limits=_CHARACTER_CONTINUITY_LIMITS,
    ),
    BackgroundContextProfile(
        profile_id="private_impression",
        purposes=frozenset({"private_impression_reflection"}),
        snapshot_material_keys=_CHARACTER_CONTINUITY_MATERIALS,
        snapshot_material_limits=_CHARACTER_CONTINUITY_LIMITS,
    ),
    BackgroundContextProfile(
        profile_id="proactive_contact",
        purposes=frozenset({"proactive_contact"}),
        snapshot_material_keys=_CHARACTER_CONTINUITY_MATERIALS,
        snapshot_material_limits=_CHARACTER_CONTINUITY_LIMITS,
    ),
    BackgroundContextProfile(
        profile_id="memory_retention",
        purposes=frozenset(
            {
                "fact_memory_retention",
                "experience_memory_retention",
                "memory_withdrawal_review",
            }
        ),
        snapshot_material_keys=(
            "stable_self",
            "situation",
            "current_activities",
            "recently_ended_activities",
            "relationship",
            "relevant_facts",
            "remembered_material",
            "recent_self_experiences",
        ),
        snapshot_material_limits={
            "relevant_facts": 12,
            "remembered_material": 4,
            "recent_self_experiences": 2,
        },
    ),
    BackgroundContextProfile(
        profile_id="interaction_background",
        purposes=frozenset(
            {
                "media_selection",
                "qq_attachment_perception",
                "expression_reconsideration",
                "external_perception_attention",
            }
        ),
        snapshot_material_keys=(
            "stable_self",
            "situation",
            "current_activities",
            "recently_ended_activities",
            "relationship",
            "affect",
            "relevant_facts",
            "recent_dialogue",
            "private_impressions",
            "moments_i_can_share",
            "photos_i_shared",
            "interaction_acts",
            "perception",
        ),
        snapshot_material_limits={
            "recent_dialogue": 6,
            "relevant_facts": 8,
            "private_impressions": 4,
        },
    ),
    BackgroundContextProfile(
        profile_id="novel_origin_review",
        purposes=frozenset({"life_development_novel_origin_review"}),
        capsule_slices=(
            "character_core",
            "current_situation",
            "relationship_slice",
            "relevant_facts",
            "world_life",
            "recent_dialogue",
        ),
        capsule_slice_limits={
            "relationship_slice": 12,
            "relevant_facts": 12,
            "world_life": 8,
            "recent_dialogue": 12,
        },
    ),
)

_PURPOSE_TO_PROFILE: dict[BackgroundPurpose, BackgroundContextProfile] = {}
for profile in _PROFILES:
    for purpose in profile.purposes:
        if purpose in _PURPOSE_TO_PROFILE:
            raise ValueError(f"duplicate background context profile for purpose {purpose!r}")
        _PURPOSE_TO_PROFILE[purpose] = profile

BACKGROUND_CONTEXT_PROFILE_IDS: tuple[str, ...] = tuple(
    profile.profile_id for profile in _PROFILES
)
REGISTERED_BACKGROUND_PURPOSES: frozenset[BackgroundPurpose] = frozenset(
    _PURPOSE_TO_PROFILE
)


def background_context_profile_for_purpose(purpose: BackgroundPurpose) -> BackgroundContextProfile:
    try:
        return _PURPOSE_TO_PROFILE[purpose]
    except KeyError as exc:
        raise KeyError(f"no BackgroundContextProfile registered for purpose {purpose!r}") from exc


# The rendered inventory is read only for identity and permission bounds.
_PRESENTED_INVENTORY_COLUMNS = ("source_ref", "scope", "privacy_class", "expires_at")


def slice_background_inner_life_snapshot(
    snapshot: Mapping[str, object],
    profile: BackgroundContextProfile,
    *,
    lean_source_inventory: bool = False,
) -> dict[str, object]:
    """Filter one InnerLifeSnapshot provider view to a lane profile."""

    if not profile.snapshot_material_keys:
        return {
            **dict(snapshot),
            "background_context_profile": profile.profile_id,
        }

    materials = snapshot.get("materials")
    if not isinstance(materials, dict):
        return {
            **dict(snapshot),
            "background_context_profile": profile.profile_id,
        }

    filtered: dict[str, object] = {}
    for key in profile.snapshot_material_keys:
        if key not in materials:
            continue
        value = materials[key]
        limit = profile.snapshot_material_limits.get(key)
        if limit is not None:
            value = _limit_material_items(value, limit, key=key)
        filtered[key] = value

    visible_refs = _material_source_refs(filtered)
    result = dict(snapshot)
    result["materials"] = _order_materials(filtered)
    result["background_context_profile"] = profile.profile_id

    faculties = snapshot.get("faculties")
    if isinstance(faculties, dict):
        result["faculties"] = _filter_faculties(faculties, set(filtered.keys()))

    source_refs = snapshot.get("source_refs")
    if isinstance(source_refs, list):
        result["source_refs"] = [ref for ref in source_refs if ref in visible_refs]

    source_inventory = snapshot.get("source_inventory")
    if isinstance(source_inventory, list):
        # Keep only the columns the rendered inventory is actually read for.
        # Every reader of it - affect, biographical, fact, source-family and
        # lifecycle - asks whether a given (source_ref, scope) was presented,
        # plus the privacy bound. content_hash, direct_source_refs and
        # authority_refs have no reader at all: they are the host's own
        # bookkeeping, and they were over half of this block.
        #
        # Only the lean wire sets this, so every existing presentation keeps its
        # exact bytes.
        kept = [
            item
            for item in source_inventory
            if isinstance(item, dict) and item.get("source_ref") in visible_refs
        ]
        result["source_inventory"] = (
            [
                {key: item[key] for key in _PRESENTED_INVENTORY_COLUMNS if key in item}
                for item in kept
            ]
            if lean_source_inventory
            else kept
        )

    return present_inner_life(result)


def slice_background_capsule_context(
    context: Mapping[str, object],
    profile: BackgroundContextProfile,
) -> dict[str, object]:
    """Filter one deliberation Context object to a lane profile."""

    if not profile.capsule_slices:
        return {
            **dict(context),
            "background_context_profile": profile.profile_id,
        }

    slices = context.get("slices")
    if not isinstance(slices, dict):
        return {
            **dict(context),
            "background_context_profile": profile.profile_id,
        }

    filtered_slices: dict[str, object] = {}
    for name in profile.capsule_slices:
        lane = slices.get(name)
        if not isinstance(lane, dict):
            continue
        excluded_kinds = dict(profile.capsule_item_exclusions).get(name, frozenset())
        items = lane.get("items")
        if excluded_kinds and isinstance(items, list):
            lane = {
                **lane,
                "items": [
                    item
                    for item in items
                    if not (
                        isinstance(item, dict)
                        and isinstance(item.get("value"), dict)
                        and item["value"].get("context_kind") in excluded_kinds
                    )
                ],
            }
        limit = profile.capsule_slice_limits.get(name)
        if (
            limit is not None
            and lane.get("availability") == "available"
            and isinstance(lane.get("items"), list)
            and len(lane["items"]) > limit
        ):
            lane = {**lane, "items": lane["items"][:limit]}
        filtered_slices[name] = lane

    ordered_slices = ordered_mapping(
        filtered_slices,
        key_order=_BACKGROUND_CAPSULE_SLICE_ORDER,
    )
    result = dict(context)
    result["slices"] = ordered_slices
    result["background_context_profile"] = profile.profile_id
    return result


def structured_role_background_purposes() -> frozenset[BackgroundPurpose]:
    from .character_interior.structured_role import _BUILTIN_CONTRACTS

    return frozenset(contract.purpose for contract in _BUILTIN_CONTRACTS)


def life_development_background_purposes() -> frozenset[BackgroundPurpose]:
    return frozenset({"life_development_draft", "life_development_novel_origin_review"})


def required_background_context_purposes() -> frozenset[BackgroundPurpose]:
    return structured_role_background_purposes() | life_development_background_purposes()


def assert_background_context_profile_coverage() -> None:
    """Fail closed when a background purpose lacks an explicit profile."""

    missing = sorted(required_background_context_purposes() - REGISTERED_BACKGROUND_PURPOSES)
    if missing:
        raise AssertionError(
            "background context profile coverage incomplete; missing purposes: "
            + ", ".join(missing)
        )


def profile_audit_record(profile: BackgroundContextProfile) -> dict[str, object]:
    return {
        "contract": "background-context-profile.3",
        "profile_id": profile.profile_id,
        "snapshot_material_keys": list(profile.snapshot_material_keys),
        "snapshot_material_limits": dict(profile.snapshot_material_limits),
        "capsule_slices": list(profile.capsule_slices),
        "capsule_slice_limits": dict(profile.capsule_slice_limits),
        "capsule_item_exclusions": {
            key: sorted(kinds) for key, kinds in dict(profile.capsule_item_exclusions).items()
        },
    }


__all__ = [
    "BACKGROUND_CONTEXT_PROFILE_IDS",
    "BackgroundContextProfile",
    "BackgroundPurpose",
    "REGISTERED_BACKGROUND_PURPOSES",
    "assert_background_context_profile_coverage",
    "background_context_profile_for_purpose",
    "life_development_background_purposes",
    "profile_audit_record",
    "required_background_context_purposes",
    "slice_background_capsule_context",
    "slice_background_inner_life_snapshot",
    "structured_role_background_purposes",
]
