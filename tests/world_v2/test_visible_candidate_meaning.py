"""Verbatim interpretation pins are not claims of semantic correctness."""
from copy import deepcopy
import hashlib
import json

import pytest

from companion_daemon.world_v2.visible_candidate_meaning import CONTRACT, COMPACT_CONTRACT, QUESTION_CONTRACT, prepare_candidate_meaning

TEXT = "你先说说你送我出发那会儿，家里什么样"


def _raw():
    return {"contract": CONTRACT, "decisions": [{"beat_index": 0, "parts": [{
        "text": TEXT,
        "interpretation": "角色要求用户描述用户送角色出发时的家中情况。",
        "meanings": [{
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
        **_raw()["decisions"][0]["parts"][0]["meanings"][0],
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
    raw["decisions"][0]["parts"][0]["meanings"] = []
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


def test_current_expression_and_embedded_past_event_remain_separate_model_readings():
    text = "我上午坐在那里，现在想想还挺开心的。"
    raw = _raw()
    part = raw["decisions"][0]["parts"][0]
    part.update(text=text, interpretation="角色说上午坐在那里，并表达此刻想起时的开心。", requested_unknowns=[])
    past = part["meanings"][0]
    past.update(proposition="角色上午坐在那里。", subject_role="companion", affected_roles=[], time_expression="上午")
    private = {**past, "proposition": "角色此刻想起时感到开心。", "mode": "current_private_expression", "time_expression": "此刻"}
    part["meanings"].append(private)
    result = prepare_candidate_meaning(beats=(text,)).inspect_response(json.dumps(raw, ensure_ascii=False))
    assert len(result["facts"]) == len(result["private_meanings"]) == 1
    assert result["facts"][0]["mode"] == "actual_event_or_state"
    assert result["private_meanings"][0]["mode"] == "current_private_expression"
    assert result["receipt_authority"] is False


def _compact_raw():
    fact = _raw()["decisions"][0]["parts"][0]["meanings"][0]
    return {"contract": COMPACT_CONTRACT, "decisions": [{
        "beat_index": 0, "meanings": [{k:fact[k] for k in ("proposition", "mode", "subject_role")}],
        "requested_unknowns": ["当时家里是什么情况"],
    }]}


def test_compact_reading_binds_original_text_by_index_without_recopy_or_reclassification():
    prep = prepare_candidate_meaning(beats=(TEXT,), compact=True)
    raw = _compact_raw()
    result = prep.inspect_response(json.dumps(raw))
    assert result["facts"][0]["original_text"] == TEXT
    assert result["facts"][0]["proposition"] == "用户曾送角色出发。"
    assert result["facts"][0]["mode"] == "actual_event_or_state"
    assert "affected_roles" not in result["facts"][0]  # not invented by the host
    system = prep.request()["messages"][0]["content"]
    for obsolete in ("text拼接", "interpretation用", "affected_roles", "time_expression", "polarity"):
        assert obsolete not in system
    assert result["receipt_authority"] is False and result["semantic_qualification"] == "unproven"


@pytest.mark.parametrize("fault", ["missing_beat", "duplicate_beat", "wrong_beat", "extra_original"])
def test_compact_wire_still_requires_complete_unique_beat_mapping(fault):
    prep = prepare_candidate_meaning(beats=(TEXT,), compact=True)
    raw = _compact_raw()
    if fault == "missing_beat":
        raw["decisions"] = []
    elif fault == "duplicate_beat":
        raw["decisions"] *= 2
    elif fault == "wrong_beat":
        raw["decisions"][0]["beat_index"] = 1
    else:
        raw["decisions"][0]["text"] = TEXT
    with pytest.raises(ValueError):
        prep.inspect_response(json.dumps(raw))


def test_compact_omission_is_not_hidden_as_semantic_qualification():
    raw = _compact_raw()
    raw["decisions"][0]["meanings"] = []
    result = prepare_candidate_meaning(beats=(TEXT,), compact=True).inspect_response(json.dumps(raw))
    assert result["facts"] == []
    assert result["semantic_qualification"] == "unproven"


def _question_raw():
    return {"contract": QUESTION_CONTRACT, "decisions": [{
        "beat_index": 0, "meanings": [], "questions": [{
            "requested_information": "当时家里是什么情况", "premises": _compact_raw()["decisions"][0]["meanings"],
        }],
    }]}


def test_question_premise_has_its_own_binding_and_cannot_disappear_into_requested_answer():
    prep = prepare_candidate_meaning(beats=(TEXT,), compact=True, explicit_questions=True)
    result = prep.inspect_response(json.dumps(_question_raw()))
    assert result["facts"][0]["fact_id"] == "b0.q0.f0"
    assert result["facts"][0]["assertion_status"] == "presupposed"
    assert result["facts"][0]["proposition"] == "用户曾送角色出发。"
    assert result["facts"][0]["original_text"] == TEXT
    assert result["receipt_authority"] is False
    assert "requested_unknowns" not in prep.request()["messages"][0]["content"]
    raw = _question_raw()
    raw["decisions"][0]["questions"][0]["premises"][0]["mode"] = "current_private_expression"
    with pytest.raises(ValueError):
        prep.inspect_response(json.dumps(raw))


def test_question_premise_mapping_is_usable_without_giving_source_probe_original_text():
    from companion_daemon.world_v2.visible_meaning_source_review import prepare_meaning_source_review
    from test_visible_source_subject_authority import _report_sources

    meaning = prepare_candidate_meaning(beats=(TEXT,), compact=True, explicit_questions=True)
    source = prepare_meaning_source_review(meaning=meaning, meaning_raw=json.dumps(_question_raw()), sources=_report_sources(), source_only=True)
    body = json.loads(source.request()["messages"][1]["content"])
    assert body["fixed_facts"][0]["assertion_status"] == "presupposed"
    assert body["fixed_facts"][0]["eligible_reading_ids"] == ["r0"]
    assert "original_text" not in body["fixed_facts"][0]


def test_open_question_has_empty_premises_but_does_not_acquire_semantic_qualification():
    raw = _question_raw()
    raw["decisions"][0]["questions"][0]["premises"] = []
    result = prepare_candidate_meaning(beats=("想换个话题吗？",), compact=True, explicit_questions=True).inspect_response(json.dumps(raw))
    assert result["facts"] == []
    assert result["semantic_qualification"] == "unproven"
    with pytest.raises(ValueError, match="require the compact"):
        prepare_candidate_meaning(beats=(TEXT,), explicit_questions=True)
