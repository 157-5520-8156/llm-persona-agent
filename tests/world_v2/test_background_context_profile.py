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
    assert record["contract"] == "background-context-profile.1"
    assert record["profile_id"] == "memory_retention"
    json.dumps(record, ensure_ascii=False, sort_keys=True)


def test_missing_purpose_fails_closed() -> None:
    with pytest.raises(KeyError, match="no BackgroundContextProfile"):
        background_context_profile_for_purpose("inbound_turn")
