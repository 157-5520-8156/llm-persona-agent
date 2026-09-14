"""Verbatim interpretation pins are not claims of semantic correctness."""
from copy import deepcopy
import hashlib
import json

import pytest

from companion_daemon.world_v2.visible_candidate_meaning import CONTRACT, prepare_candidate_meaning

TEXT = "你先说说你送我出发那会儿，家里什么样"


def _raw():
    return {"contract": CONTRACT, "decisions": [{"beat_index": 0, "parts": [{
        "text": TEXT,
        "interpretation": "角色要求用户描述用户送角色出发时的家中情况。",
        "factual_meanings": [{
            "proposition": "用户曾送角色出发。", "mode": "actual_event_or_state",
            "subject_role": "counterpart", "affected_roles": ["companion"],
            "time_expression": "过去那次", "polarity": "affirmative",
        }],
        "requested_unknowns": ["当时家里是什么情况"],
    }]}]}


def test_meaning_request_has_no_evidence_and_pins_role_resolved_question_premise():
    prep = prepare_candidate_meaning(beats=(TEXT,))
    body = json.loads(prep.request()["messages"][1]["content"])
    assert body == {"contract": CONTRACT, "visible_beats": [{"beat_index": 0, "text": TEXT}]}
    raw = json.dumps(_raw(), ensure_ascii=False)
    result = prep.inspect_response(raw)
    assert result["facts"] == [{
        "fact_id": "b0.p0.f0", "beat_index": 0, "part_index": 0, "original_text": TEXT,
        **_raw()["decisions"][0]["parts"][0]["factual_meanings"][0],
    }]
    assert result["raw_response_sha256"] == hashlib.sha256(raw.encode()).hexdigest()
    assert result["semantic_qualification"] == "unproven"
    assert result["receipt_authority"] is False


@pytest.mark.parametrize("fault", ["rewrite", "omit", "duplicate", "order", "old_contract", "extra_evidence"])
def test_invalid_interpretation_cannot_masquerade_as_a_complete_reading(fault):
    prep = prepare_candidate_meaning(beats=(TEXT,))
    raw = _raw()
    part = raw["decisions"][0]["parts"][0]
    if fault == "rewrite":
        part["text"] = "你家里人送你出发那会儿，家里什么样"
    elif fault == "omit":
        part["text"] = TEXT[:-1]
    elif fault == "duplicate":
        raw["decisions"][0]["parts"].append(deepcopy(part))
    elif fault == "order":
        raw["decisions"][0]["beat_index"] = 1
    elif fault == "old_contract":
        raw["contract"] = "visible-source-reading-experiment.3"
    else:
        part["source_ref"] = "invented:proof"
    with pytest.raises(ValueError):
        prep.inspect_response(json.dumps(raw, ensure_ascii=False))


def test_semantic_misreading_is_explicitly_not_structural_qualification():
    raw = _raw()
    raw["decisions"][0]["parts"][0]["factual_meanings"] = []
    result = prepare_candidate_meaning(beats=(TEXT,)).inspect_response(json.dumps(raw, ensure_ascii=False))
    assert result["facts"] == []
    assert result["semantic_qualification"] == "unproven" and result["receipt_authority"] is False


def test_json_members_and_candidate_boundaries_are_not_silently_repaired():
    prep = prepare_candidate_meaning(beats=(TEXT,))
    raw = json.dumps(_raw(), ensure_ascii=False)
    with pytest.raises(ValueError, match="duplicate"):
        prep.inspect_response(raw.replace('"contract":', '"contract":"duplicate", "contract":', 1))
    for beats in [(), ("",), ("a" * 4097,), ("a",) * 17]:
        with pytest.raises(ValueError):
            prepare_candidate_meaning(beats=beats)
