"""Gate and slice behavior for background lane context profiles."""

from __future__ import annotations

import json

import pytest

from companion_daemon.world_v2.background_context_profile import (
    assert_background_context_profile_coverage,
    background_context_profile_for_purpose,
    profile_audit_record,
    required_background_context_purposes,
    slice_background_capsule_context,
    slice_background_inner_life_snapshot,
)
from companion_daemon.world_v2.present_prompt import (
    appraisal_material_rows,
    recent_dialogue_material_entries,
)


def _sample_snapshot(*, include_chat: bool = True) -> dict[str, object]:
    materials: dict[str, object] = {
        "stable_self": {"summary": "沈知栀"},
        "situation": {"activity": "在书店"},
        "relationship": {"stage": "close_friend"},
        "relevant_facts": {
            "items": [
                {"source_ref": "fact:1", "value": {"predicate_code": "residence.city"}},
                {"source_ref": "fact:2", "value": {"predicate_code": "study.major"}},
            ]
        },
        "affect": {"warmth_bp": 5000},
        "appraisals": {
            "items": [
                {"source_ref": f"appraisal:{index}", "value": {"summary": f"a{index}"}}
                for index in range(16)
            ]
        },
        "recent_self_experiences": {
            "items": [
                {"source_ref": f"exp:{index}", "value": {"summary": f"e{index}"}}
                for index in range(5)
            ]
        },
    }
    if include_chat:
        materials["recent_dialogue"] = {
            "items": [
                {"source_ref": f"dlg:{index}", "value": {"text": f"m{index}"}}
                for index in range(12)
            ]
        }
        materials["private_impressions"] = {
            "items": [{"source_ref": "imp:1", "value": {"summary": "quiet"}}]
        }
    return {
        "contract": "inner-life-snapshot.1",
        "availability": "available",
        "materials": materials,
        "faculties": {
            "appraisal_affect": {
                "availability": "available",
                "material_keys": ["appraisals", "affect"],
            },
            "expression_stance": {
                "availability": "available",
                "material_keys": ["recent_dialogue", "appraisals"],
            },
        },
        "source_refs": ["fact:1", "fact:2", "appraisal:0", "dlg:0", "exp:0"],
        "source_inventory": [
            {"source_ref": "fact:1", "scope": "relevant_facts"},
            {"source_ref": "fact:2", "scope": "relevant_facts"},
        ],
    }


def _sample_capsule_context() -> dict[str, object]:
    def lane(name: str, count: int) -> dict[str, object]:
        return {
            "availability": "available",
            "items": [
                {"item_ref": f"{name}:{index}", "value": {"n": index}}
                for index in range(count)
            ],
        }

    return {
        "world_id": "world:test",
        "logical_time": "2026-08-20T08:00:00+00:00",
        "slices": {
            "character_core": lane("core", 2),
            "current_situation": lane("situation", 1),
            "relevant_facts": lane("fact", 10),
            "world_life": lane("life", 12),
            "recent_experiences": lane("experience", 8),
            "recent_dialogue": lane("dialogue", 20),
            "appraisals": lane("appraisal", 16),
            "private_impressions": lane("impression", 6),
        },
    }


def test_background_context_profile_coverage_gate() -> None:
    assert_background_context_profile_coverage()
    assert "life_development_draft" in required_background_context_purposes()
    assert "world_stimulus_appraisal" in required_background_context_purposes()


def test_inventory_projection_preserves_each_scope_revision_and_privacy():
    from copy import deepcopy
    from dataclasses import replace

    snapshot = _sample_snapshot()
    rows = [
        {"source_ref": "fact:1", "scope": scope, "privacy_class": privacy,
         "entity_revision": revision, "expires_at": "2026-09-30T00:00:00Z",
         "content_hash": "a" * 64, "authority_refs": ["event:authority"],
         "direct_source_refs": ["event:direct"]}
        for scope, privacy, revision in (("relevant_facts", "personal", 1), ("situation", "private", 2))
    ]
    snapshot["source_inventory"] = deepcopy(rows)
    profile = background_context_profile_for_purpose("activity_lifecycle_choice")
    original = slice_background_inner_life_snapshot(snapshot, replace(profile, omit_inventory_proofs=False))
    compact = slice_background_inner_life_snapshot(snapshot, profile)
    assert compact["materials"] == original["materials"]
    assert compact["source_inventory"] == [
        {k: v for k, v in row.items() if k not in {"content_hash", "authority_refs", "direct_source_refs"}}
        for row in rows
    ]
    assert snapshot["source_inventory"] == rows


def test_life_choice_keeps_her_prior_readings_and_relationship_history() -> None:
    profile = background_context_profile_for_purpose("activity_lifecycle_choice")
    sliced = slice_background_inner_life_snapshot(_sample_snapshot(), profile)
    materials = sliced["materials"]
    assert isinstance(materials, dict)
    assert "relevant_facts" in materials
    assert "situation" in materials
    assert "appraisals" in materials
    assert "recent_dialogue" in materials
    assert "private_impressions" in materials
    assert sliced["background_context_profile"] == "life_ecology_core"
    experiences = materials.get("recent_self_experiences")
    assert isinstance(experiences, dict)
    assert len(experiences["items"]) >= 2


def test_proactive_choice_keeps_her_life_and_unfinished_matters() -> None:
    snapshot = _sample_snapshot()
    snapshot["materials"].update({
        "day_sheet": {"today": "The interview is this afternoon."},
        "week_diary": {"Monday": ["The bookshop closed."]},
        "unresolved": [{"source_ref": "thread:interview", "status": "active"}],
        "aspirations": [{"source_ref": "event:wish", "text": "Try editorial work."}],
        "remembered_material": [{"source_ref": "memory:promise", "text": "I wanted to try."}],
    })
    original = json.dumps(snapshot, sort_keys=True)
    sliced = slice_background_inner_life_snapshot(
        snapshot, background_context_profile_for_purpose("proactive_contact")
    )
    materials = sliced["materials"]
    for key in ("day_sheet", "week_diary", "unresolved", "aspirations", "remembered_material"):
        assert materials.get(key) == snapshot["materials"][key]
    assert materials["recent_self_experiences"]["items"]
    assert json.dumps(snapshot, sort_keys=True) == original


def test_stimulus_profile_keeps_prior_readings_as_revisable_context() -> None:
    profile = background_context_profile_for_purpose("world_stimulus_appraisal")
    sliced = slice_background_inner_life_snapshot(_sample_snapshot(), profile)
    materials = sliced["materials"]
    assert isinstance(materials, dict)
    assert "recent_dialogue" in materials
    assert "appraisals" in materials
    dialogue = materials["recent_dialogue"]
    assert isinstance(dialogue, dict)
    assert len(dialogue["items"]) == 12


def test_life_development_capsule_profile_trims_slices() -> None:
    profile = background_context_profile_for_purpose("life_development_draft")
    sliced = slice_background_capsule_context(_sample_capsule_context(), profile)
    slices = sliced["slices"]
    assert isinstance(slices, dict)
    assert "recent_dialogue" not in slices
    assert "appraisals" not in slices
    assert "relevant_facts" in slices
    assert len(slices["world_life"]["items"]) == 6
    assert sliced["background_context_profile"] == "life_world_author"


@pytest.mark.parametrize("cache_split", [False, True])
def test_background_dialogue_budget_keeps_the_latest_exchange(cache_split: bool) -> None:
    snapshot = _sample_snapshot()
    entries = [
        {"source_ref": f"dlg:{index}", "text": f"message {index}"}
        for index in range(24)
    ]
    snapshot["materials"]["recent_dialogue"] = (
        {"stable_turns": entries[:-1], "volatile_last_turn": entries[-1]}
        if cache_split else entries
    )
    snapshot["source_refs"] = [item["source_ref"] for item in entries]
    sliced = slice_background_inner_life_snapshot(
        snapshot, background_context_profile_for_purpose("proactive_contact")
    )
    delivered = recent_dialogue_material_entries(sliced["materials"]["recent_dialogue"])
    assert [item["text"] for item in delivered] == [f"message {i}" for i in range(12, 24)]
    assert sliced["source_refs"] == [f"dlg:{i}" for i in range(12, 24)]


@pytest.mark.parametrize("cache_split", [False, True])
def test_compact_appraisals_keep_a_bounded_source_inventory(cache_split: bool) -> None:
    snapshot = _sample_snapshot(include_chat=False)
    rows = [[f"appraisal:{i}", 5000, None, None, [[f"reading {i}"]]] for i in range(10)]
    snapshot["materials"]["appraisals"] = {
        "columns": ["ref", "conf", "since", "until", "readings"],
        **({"stable_rows": rows[:-1], "volatile_last_row": rows[-1]}
           if cache_split else {"rows": rows}),
    }
    snapshot["source_refs"] = [f"appraisal:{i}" for i in range(10)]
    snapshot["source_inventory"] = [
        {"source_ref": ref, "scope": "appraisals"} for ref in snapshot["source_refs"]
    ]
    sliced = slice_background_inner_life_snapshot(
        snapshot, background_context_profile_for_purpose("proactive_contact")
    )
    assert appraisal_material_rows(sliced["materials"]["appraisals"]) == rows[:4]
    assert sliced["source_refs"] == [f"appraisal:{i}" for i in range(4)]
    assert len(sliced["source_inventory"]) == 4


@pytest.mark.parametrize("hidden_ref", [None, "experience:5"])
def test_rendered_diary_keeps_exact_sources_after_background_budgeting(hidden_ref) -> None:
    from companion_daemon.world_v2.character_interior.snapshot_compiler import (
        compile_inner_life_snapshot,
    )

    entries = [
        (f"experience:{i}", f"2026-08-{11 + i:02d}", f"DAY-{i} happened")
        for i in range(6)
    ] + [("experience:old", "2026-08-09", "EXCLUDED TOO OLD")]
    snapshot = compile_inner_life_snapshot({
        "world_id": "world:diary-background",
        "actor_ref": "agent:companion",
        "world_revision": 4,
        "deliberation_revision": 2,
        "ledger_sequence": 4,
        "logical_time": "2026-08-16T16:00:00+08:00",
        "slices": {
            "recent_experiences": {
                "availability": "available",
                "items": [
                    {
                        "item_ref": ref,
                        "source_ref": ref,
                        "privacy_class": "private",
                        "value": {
                            "experience_id": ref,
                            "values": {
                                "occurred_from": day + "T11:00:00+08:00",
                                "occurred_to": day + "T11:00:00+08:00",
                                "participant_refs": ["agent:companion"],
                                "privacy_class": "private",
                            },
                            "content": {"text": text},
                        },
                    }
                    for ref, day, text in entries
                ],
            },
        },
    })
    view = snapshot.model_view(
        visible_source_refs=frozenset(snapshot.source_refs) - {hidden_ref}
    )
    sliced = slice_background_inner_life_snapshot(
        view, background_context_profile_for_purpose("proactive_contact")
    )

    expected_refs = ["experience:0", "experience:1", "experience:2", "experience:3", "experience:4"]
    if hidden_ref is None:
        expected_refs.append("experience:5")
    assert sliced["source_refs"] == expected_refs
    assert {item["source_ref"] for item in sliced["source_inventory"]} == set(expected_refs)
    assert len(sliced["materials"]["recent_self_experiences"]["items"]) == 4
    assert "DAY-4 happened" in json.dumps(sliced)
    assert "EXCLUDED TOO OLD" not in json.dumps(sliced)
    if hidden_ref is not None:
        assert "DAY-5 happened" not in json.dumps(sliced)


def test_profile_audit_record_is_stable_json() -> None:
    profile = background_context_profile_for_purpose("fact_memory_retention")
    record = profile_audit_record(profile)
    assert record["contract"] == "background-context-profile.3"
    assert record["profile_id"] == "memory_retention"
    json.dumps(record, ensure_ascii=False, sort_keys=True)


def test_missing_purpose_fails_closed() -> None:
    with pytest.raises(KeyError, match="no BackgroundContextProfile"):
        background_context_profile_for_purpose("inbound_turn")


def test_presented_inventory_keeps_identity_bounds_and_brakes_growth():
    """The rendered inventory must keep what readers use and stop restating refs.

    Measured on 89 real appraisal requests: the block is 33825 characters, of
    which the three columns nothing reads (content_hash, direct_source_refs,
    entity_revision) are 71 percent. Deduplication and the cap contribute only
    1.5 percent today, because a snapshot holds about 51 entries with 3 repeats;
    they are a brake on growth, not the saving.
    """

    from companion_daemon.world_v2.background_context_profile import (
        _PRESENTED_INVENTORY_LIMIT,
        collapse_presented_source_inventory,
    )

    first = {
        'source_ref': 'affect:compiled:abc',
        'scope': 'affect',
        'privacy_class': 'private',
        'expires_at': '2026-09-22T00:00:00Z',
        'content_hash': 'a' * 64,
        'direct_source_refs': ['event:000001', 'event:000002'],
        'entity_revision': 41,
    }
    duplicate = {**first, 'content_hash': 'b' * 64}
    other = {'source_ref': 'dialogue:observation:qq:1', 'scope': 'recent_dialogue'}

    collapsed = collapse_presented_source_inventory([first, duplicate, other])

    assert [item['source_ref'] for item in collapsed] == [
        'affect:compiled:abc', 'dialogue:observation:qq:1',
    ]
    # Every column a reader asks for survives; the unread ones do not.
    assert collapsed[0] == {
        'source_ref': 'affect:compiled:abc', 'scope': 'affect',
        'privacy_class': 'private', 'expires_at': '2026-09-22T00:00:00Z',
    }
    assert collapsed[1] == {'source_ref': 'dialogue:observation:qq:1', 'scope': 'recent_dialogue'}

    # The cap bounds the block, and the first entries keep their order.
    bounded = collapse_presented_source_inventory(
        [{'source_ref': f'source:{index}', 'scope': 'affect'} for index in range(500)]
    )
    assert len(bounded) == _PRESENTED_INVENTORY_LIMIT
    assert bounded[0]['source_ref'] == 'source:0'


def test_the_appraisal_lane_sends_only_the_inventory_columns_it_can_use() -> None:
    """The lane production actually runs must drop what nothing reads.

    Measured on 89 real appraisal requests: the rendered inventory is 33825
    characters and content_hash, direct_source_refs and authority_refs are 71
    percent of it. A structural diff against the full rendering removes exactly
    those three, adds nothing and changes no other path. None of the three is
    referenced in structured_role, so the model's output contract has no field
    for them, and every host reader of the inventory asks only for source_ref
    and scope. Without the reviewer configured - which is how production runs -
    the author lane takes the registered profile directly.
    """

    snapshot = _sample_snapshot(include_chat=False)
    snapshot["source_inventory"] = [
        {
            "source_ref": "fact:1",
            "scope": "relevant_facts",
            "privacy_class": "personal",
            "expires_at": "2026-09-24T00:00:00Z",
            "content_hash": "a" * 64,
            "direct_source_refs": ["event:000001"],
            "authority_refs": ["authority:1"],
            "entity_revision": 41,
        },
        {"source_ref": "fact:2", "scope": "relevant_facts", "content_hash": "b" * 64},
    ]
    sliced = slice_background_inner_life_snapshot(
        snapshot, background_context_profile_for_purpose("world_stimulus_appraisal")
    )

    assert sliced["source_inventory"] == [
        {
            "source_ref": "fact:1",
            "scope": "relevant_facts",
            "privacy_class": "personal",
            "expires_at": "2026-09-24T00:00:00Z",
        },
        {"source_ref": "fact:2", "scope": "relevant_facts"},
    ]


def test_current_world_author_does_not_inherit_character_private_continuity():
    from copy import deepcopy

    context = _sample_capsule_context()
    for key in ("affect_episodes", "relationship_slice", "open_threads"):
        context["slices"][key] = {"items": [{"item_ref": key, "value": {"text": "她惦记着没说完的话"}}]}
    before = deepcopy(context)
    world = slice_background_capsule_context(
        context, background_context_profile_for_purpose("world_consequence_draft"),
    )
    assert context == before
    assert set(world["slices"]) == {"character_core", "current_situation", "relevant_facts", "world_life"}
    assert world["slices"]["current_situation"] == before["slices"]["current_situation"]
    assert world["slices"]["world_life"]["items"] == before["slices"]["world_life"]["items"][:6]
    legacy = slice_background_capsule_context(
        context, background_context_profile_for_purpose("life_development_draft"),
    )
    assert "affect_episodes" in legacy["slices"]
    character = slice_background_inner_life_snapshot(
        _sample_snapshot(), background_context_profile_for_purpose("world_stimulus_appraisal"),
    )
    assert "affect" in character["materials"]
    assert "relationship" in character["materials"]
