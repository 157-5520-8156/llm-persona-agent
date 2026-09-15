"""Complete-field mechanics do not assert semantic reading accuracy."""
from copy import deepcopy
import json

import pytest

from companion_daemon.world_v2.character_interior.life_candidate_reading import (
    CONTRACT,
    MAX_FACTS,
    PreparedLifeCandidateReading,
    prepare_life_candidate_reading,
)


def _candidate():
    return {
        "status": "transition", "summary": "我走完一圈回来了。",
        "attended_source_refs": ["s0"], "decision": None, "recall_query": None,
        "proposals": [{
            "proposal_type": "world_stimulus_appraisal_result", "decision": "activate",
            "brief_rationale": "上午坐过那里，现在有点舍不得。",
            "meaning_candidates": [{"meaning": "想再去看看。", "confidence": 5000}],
            "life_intent": {"intention": "明天去图书馆。", "duration_seconds": 120},
            "experience_transition": {"reason_summary": "刚才和他打过电话。"},
            "life_responses": [{"source_event_ref": "event:rain", "response_text": "我有点意外。"}],
            "future_nested_field": {"日/记~": "不能悄悄遗漏这个新增正文。"},
        }],
    }


def _prepare(value=None):
    return prepare_life_candidate_reading(candidate_json=json.dumps(value or _candidate(), ensure_ascii=False))


def _response(prepared):
    return {"contract": CONTRACT, "fields": [
        {"path": item["path"], "interpretation": "fixture reading, not semantic evidence", "readings": []}
        for item in json.loads(prepared.payload_json)["text_fields"]
    ]}


def _fact():
    return {"proposition": "角色走完一圈回来了。", "mode": "actual_event_or_state",
            "subject_role": "companion", "time_expression": "刚才", "polarity": "affirmative"}


def test_all_prose_and_new_nested_fields_are_preserved_with_original_context():
    candidate = _candidate()
    prepared = _prepare(candidate)
    pin = json.loads(prepared.payload_json)
    fields = {f["path"]: f["text"] for f in pin["text_fields"]}
    assert fields["/summary"] == candidate["summary"]
    assert fields["/proposals/0/brief_rationale"] == candidate["proposals"][0]["brief_rationale"]
    assert fields["/proposals/0/meaning_candidates/0/meaning"] == "想再去看看。"
    assert fields["/proposals/0/experience_transition/reason_summary"] == "刚才和他打过电话。"
    assert fields["/proposals/0/life_responses/0/response_text"] == "我有点意外。"
    assert fields["/proposals/0/future_nested_field/日~1记~0"] == "不能悄悄遗漏这个新增正文。"
    body = json.loads(prepared.request()["messages"][1]["content"])
    assert body["candidate"] == candidate
    assert "source_materials" not in body
    assert body["candidate"]["proposals"][0]["life_intent"]["duration_seconds"] == 120
    assert PreparedLifeCandidateReading(prepared.payload_json).request() == prepared.request()


def test_no_facts_is_not_semantic_or_write_approval_and_field_order_is_not_semantic():
    prepared = _prepare()
    response = _response(prepared)
    response["fields"].reverse()
    result = prepared.inspect_response(json.dumps(response))
    assert result["facts"] == []
    assert result["all_text_fields_represented"] is True
    assert result["semantic_coverage"] == "unproven"
    assert result["source_support"] == "not_assessed"
    assert result["life_write_authority"] is False


def test_facts_keep_field_and_original_text_even_when_same_claim_repeats():
    prepared = _prepare()
    response = _response(prepared)
    for field in response["fields"]:
        if field["path"] in {"/summary", "/proposals/0/brief_rationale"}:
            field["readings"] = [_fact()]
    result = prepared.inspect_response(json.dumps(response))
    assert len(result["facts"]) == 2
    assert {f["fact_id"] for f in result["facts"]} == {"f0", "f1"}
    assert len({f["original_text"] for f in result["facts"]}) == 2


@pytest.mark.parametrize("fault", ["omit", "duplicate", "replace", "extra", "authority", "wrong_contract"])
def test_reader_cannot_omit_repeat_replace_or_grant_authority(fault):
    prepared = _prepare()
    response = _response(prepared)
    if fault == "omit":
        response["fields"].pop()
    elif fault == "duplicate":
        response["fields"][-1] = deepcopy(response["fields"][0])
    elif fault == "replace":
        response["fields"][-1]["path"] = "/other-source"
    elif fault == "extra":
        response["fields"].append(deepcopy(response["fields"][0]))
    elif fault == "authority":
        response["life_write_authority"] = True
    else:
        response["contract"] = "unbound"
    with pytest.raises(ValueError):
        prepared.inspect_response(json.dumps(response))


def test_total_fact_limit_applies_across_all_fields():
    prepared = _prepare()
    response = _response(prepared)
    response["fields"][0]["readings"] = [_fact()] * MAX_FACTS
    response["fields"][1]["readings"] = [_fact()]
    with pytest.raises(ValueError, match="total fact"):
        prepared.inspect_response(json.dumps(response))


def test_explicit_current_and_future_readings_do_not_gain_source_or_write_authority():
    prepared = _prepare()
    response = _response(prepared)
    response["fields"][0]["readings"] = [
        {**_fact(), "mode": "current_expression", "proposition": "我有点舍不得。"},
        {**_fact(), "mode": "future_intention", "proposition": "明天我想去看看。"},
        _fact(),
    ]
    result = prepared.inspect_response(json.dumps(response))
    assert len(result["facts"]) == 1
    assert len(result["nonfactual_readings"]) == 2
    assert result["life_write_authority"] is False
    assert result["semantic_coverage"] == "unproven"


def test_consumer_does_not_reclassify_semantic_errors_using_temporal_keywords():
    prepared = _prepare()
    response = _response(prepared)
    response["fields"][0]["readings"] = [{
        **_fact(), "mode": "past_intention", "time_expression": "明天",
        "proposition": "明天我想去看看。",
    }]
    result = prepared.inspect_response(json.dumps(response))
    assert result["facts"][0]["mode"] == "past_intention"
    assert result["semantic_coverage"] == "unproven"


def test_legacy_compilation_and_inspection_remain_version_bound():
    raw = json.dumps(_candidate(), ensure_ascii=False)
    legacy = prepare_life_candidate_reading(candidate_json=raw, version="1")
    pin = json.loads(legacy.payload_json)
    response = {"contract": "life-candidate-reading.1", "fields": [
        {"path": f["path"], "interpretation": "fixture", "facts": []}
        for f in pin["text_fields"]
    ]}
    result = legacy.inspect_response(json.dumps(response))
    assert "nonfactual_readings" not in result
    assert legacy.sha256 != prepare_life_candidate_reading(candidate_json=raw).sha256
    assert legacy.request()["tool_choice"]["function"]["name"] == "read_life_candidate_fields_v1"


@pytest.mark.parametrize("field", ["candidate_sha256", "text_fields", "request"])
def test_cold_preparation_rejects_changed_candidate_index_or_request(field):
    prepared = _prepare()
    pin = json.loads(prepared.payload_json)
    pin[field] = "tampered"
    forged = PreparedLifeCandidateReading(json.dumps(pin))
    with pytest.raises(ValueError):
        forged.inspect_response(json.dumps(_response(prepared)))


def test_null_response_does_not_hide_other_candidate_prose():
    candidate = _candidate()
    candidate["proposals"][0]["life_responses"][0]["response_text"] = None
    prepared = _prepare(candidate)
    fields = json.loads(prepared.payload_json)["text_fields"]
    assert any(f["path"] == "/summary" for f in fields)
    assert not any(f["path"].endswith("/response_text") for f in fields)
    assert json.loads(prepared.request()["messages"][1]["content"])["candidate"] == candidate


@pytest.mark.parametrize("fault", ["missing_summary", "recall", "other_purpose", "duplicate_json", "overbound"])
def test_incomplete_control_transfer_ambiguous_or_large_candidate_rejected(fault):
    candidate = _candidate()
    if fault == "missing_summary":
        candidate.pop("summary")
    elif fault == "recall":
        candidate["status"] = "recall_request"
    elif fault == "other_purpose":
        candidate["proposals"][0]["proposal_type"] = "private_impression"
    elif fault == "overbound":
        candidate["summary"] = "我" * 50_000
    raw = json.dumps(candidate, ensure_ascii=False)
    if fault == "duplicate_json":
        raw = raw[:-1] + ',"summary":"hidden second summary"}'
    with pytest.raises(ValueError):
        prepare_life_candidate_reading(candidate_json=raw)
