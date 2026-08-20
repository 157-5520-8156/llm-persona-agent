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


def test_life_ecology_profile_drops_chat_and_appraisals_but_keeps_facts() -> None:
    profile = background_context_profile_for_purpose("activity_lifecycle_choice")
    sliced = slice_background_inner_life_snapshot(_sample_snapshot(), profile)
    materials = sliced["materials"]
    assert isinstance(materials, dict)
    assert "relevant_facts" in materials
    assert "situation" in materials
    assert "appraisals" not in materials
    assert "recent_dialogue" not in materials
    assert "private_impressions" not in materials
    assert sliced["background_context_profile"] == "life_ecology_core"
    experiences = materials.get("recent_self_experiences")
    assert isinstance(experiences, dict)
    assert len(experiences["items"]) == 1


def test_stimulus_profile_keeps_short_dialogue_not_full_appraisals() -> None:
    profile = background_context_profile_for_purpose("world_stimulus_appraisal")
    sliced = slice_background_inner_life_snapshot(_sample_snapshot(), profile)
    materials = sliced["materials"]
    assert isinstance(materials, dict)
    assert "recent_dialogue" in materials
    assert "appraisals" not in materials
    dialogue = materials["recent_dialogue"]
    assert isinstance(dialogue, dict)
    assert len(dialogue["items"]) == 2


def test_life_development_capsule_profile_trims_slices() -> None:
    profile = background_context_profile_for_purpose("life_development_draft")
    sliced = slice_background_capsule_context(_sample_capsule_context(), profile)
    slices = sliced["slices"]
    assert isinstance(slices, dict)
    assert "recent_dialogue" not in slices
    assert "appraisals" not in slices
    assert "relevant_facts" in slices
    assert len(slices["world_life"]["items"]) == 6
    assert sliced["background_context_profile"] == "life_ecology_core"


def test_profile_audit_record_is_stable_json() -> None:
    profile = background_context_profile_for_purpose("fact_memory_retention")
    record = profile_audit_record(profile)
    assert record["contract"] == "background-context-profile.1"
    assert record["profile_id"] == "memory_retention"
    json.dumps(record, ensure_ascii=False, sort_keys=True)


def test_missing_purpose_fails_closed() -> None:
    with pytest.raises(KeyError, match="no BackgroundContextProfile"):
        background_context_profile_for_purpose("inbound_turn")
