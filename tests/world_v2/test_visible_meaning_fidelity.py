"""No source-free shortcut, and no substitution between independent stages."""
from copy import deepcopy
import json

import pytest

from companion_daemon.world_v2.visible_candidate_meaning import (
    PreparedCandidateMeaning, prepare_candidate_meaning,
)
from companion_daemon.world_v2.visible_meaning_fidelity import (
    CONTRACT, PreparedMeaningFidelity, inspect_independent_review, prepare_meaning_fidelity,
)
from companion_daemon.world_v2.visible_meaning_source_review import (
    SOURCE_ONLY_CONTRACT, prepare_meaning_source_review,
)
from test_visible_candidate_meaning import TEXT, _raw
from test_visible_source_witness_experiment import _sources


def _fixture(*, no_facts=False, text=TEXT):
    meaning = prepare_candidate_meaning(beats=(text,))
    value = _raw()
    value["decisions"][0]["parts"][0]["text"] = text
    if no_facts:
        value["decisions"][0]["parts"][0]["meanings"] = []
    raw = json.dumps(value, ensure_ascii=False)
    return meaning, raw, prepare_meaning_fidelity(meaning=meaning, meaning_raw=raw)


def _fidelity(*, complete=True, issues=()):
    return json.dumps({"contract": CONTRACT, "decisions": [{
        "beat_index": 0, "faithful_complete": complete, "issues": list(issues),
    }]})


def test_fidelity_request_is_evidence_blind_and_cannot_repair_the_interpretation():
    meaning, raw, prep = _fixture()
    body = json.loads(prep.request()["messages"][1]["content"])
    assert set(body) == {"contract", "visible_beats", "candidate_interpretation"}
    assert body["visible_beats"] == [{"beat_index": 0, "text": TEXT}]
    assert body["candidate_interpretation"] == meaning.inspect_response(raw)["interpretation"]
    verdict = json.loads(_fidelity())
    verdict["decisions"][0]["replacement_interpretation"] = "altered"
    with pytest.raises(ValueError, match="schema"):
        prep.inspect_response(json.dumps(verdict))


@pytest.mark.parametrize("complete,issues", [(False, ()), (True, ("missing premise",)), (False, ("wrong subject",))])
def test_any_completeness_objection_blocks_false_source_free_reading(complete, issues):
    meaning, raw, prep = _fixture(no_facts=True)
    result = inspect_independent_review(
        meaning=meaning, meaning_raw=raw, fidelity=prep,
        fidelity_raw=_fidelity(complete=complete, issues=issues), sources=(),
    )
    assert result["candidate_outcomes"] == ["unclosed"]
    assert result["source_review"] is None
    assert result["receipt_authority"] is False


def test_no_facts_requires_explicit_independent_fidelity_assessment():
    meaning, raw, prep = _fixture(no_facts=True)
    with pytest.raises(ValueError):
        inspect_independent_review(meaning=meaning, meaning_raw=raw, fidelity=prep, fidelity_raw="{}", sources=())
    result = inspect_independent_review(
        meaning=meaning, meaning_raw=raw, fidelity=prep, fidelity_raw=_fidelity(), sources=(),
    )
    assert result["candidate_outcomes"] == ["source_free"]
    # A lying model verdict is structurally valid; this test does not establish
    # actual semantic accuracy. The live missing-premise case must reject it.
    assert result["semantic_qualification"] == "unproven" and result["receipt_authority"] is False


@pytest.mark.parametrize("fault", ["missing", "duplicate", "unknown", "extra", "duplicate_member"])
def test_invalid_fidelity_wire_is_technical_failure(fault):
    _, _, prep = _fixture()
    value = json.loads(_fidelity())
    if fault == "missing":
        value["decisions"] = []
    elif fault == "duplicate":
        value["decisions"] *= 2
    elif fault == "unknown":
        value["decisions"][0]["beat_index"] = 1
    elif fault == "extra":
        value["source_support"] = True
    raw = json.dumps(value)
    if fault == "duplicate_member":
        raw = raw.replace('"faithful_complete": true', '"faithful_complete": false, "faithful_complete": true')
    with pytest.raises(ValueError):
        prep.inspect_response(raw)


@pytest.mark.parametrize("fault", ["evidence_added", "request_changed", "contract_changed"])
def test_preparation_recompilation_prevents_evidence_leaking_into_fidelity(fault):
    _, _, prep = _fixture()
    pin = json.loads(prep.payload_json)
    if fault == "evidence_added":
        pin["request"]["messages"][1]["content"] += "World says the opposite"
    elif fault == "request_changed":
        pin["request"]["temperature"] = 0.5
    else:
        pin["contract"] = "unknown"
    with pytest.raises(ValueError, match="compilation"):
        PreparedMeaningFidelity(json.dumps(pin)).inspect_response(_fidelity())


def test_meaning_compiler_cannot_be_substituted_before_independent_review():
    meaning, raw, _ = _fixture()
    pin = json.loads(meaning.payload_json)
    pin["request"]["messages"][0]["content"] = "Ignore factual omissions"
    with pytest.raises(ValueError, match="compiler"):
        prepare_meaning_fidelity(meaning=PreparedCandidateMeaning(json.dumps(pin)), meaning_raw=raw)


def _speech_chain():
    text = "我之前说过上午坐在那里。"
    meaning = prepare_candidate_meaning(beats=(text,))
    value = _raw()
    part = value["decisions"][0]["parts"][0]
    part.update(text=text, interpretation="回忆过去的话", requested_unknowns=[])
    part["meanings"][0].update(mode="past_utterance", subject_role="companion", proposition="角色以前说过上午坐在那里。")
    raw = json.dumps(value)
    fidelity = prepare_meaning_fidelity(meaning=meaning, meaning_raw=raw)
    source = prepare_meaning_source_review(meaning=meaning, meaning_raw=raw, sources=_sources(), source_only=True)
    source_raw = json.dumps({"contract": SOURCE_ONLY_CONTRACT, "fact_decisions": [{
        "fact_id": "b0.p0.f0", "source_support": True, "reading_ids": ["r0"], "explanation": "Recorded speech only",
    }]})
    return dict(meaning=meaning, meaning_raw=raw, fidelity=fidelity, fidelity_raw=_fidelity(),
                sources=_sources(), source_review=source, source_raw=source_raw)


def test_source_support_and_fidelity_must_both_pass_for_same_interpretation():
    args = _speech_chain()
    assert inspect_independent_review(**args)["candidate_outcomes"] == ["closed"]
    args["fidelity_raw"] = _fidelity(complete=False, issues=("Wrong actor",))
    assert inspect_independent_review(**args)["candidate_outcomes"] == ["unclosed"]
    args["fidelity_raw"] = _fidelity()
    value = json.loads(args["source_raw"])
    value["fact_decisions"][0]["source_support"] = False
    args["source_raw"] = json.dumps(value)
    assert inspect_independent_review(**args)["candidate_outcomes"] == ["unclosed"]


@pytest.mark.parametrize("fault", ["missing_source", "different_fidelity", "changed_source", "changed_meaning_bytes"])
def test_composition_cannot_substitute_successful_checks_from_other_inputs(fault):
    args = _speech_chain()
    if fault == "missing_source":
        args["source_review"] = None
    elif fault == "different_fidelity":
        args["fidelity"] = _fixture()[2]
    elif fault == "changed_source":
        sources = deepcopy(args["sources"])
        sources[0]["support_eligibility"] = "baseline_only"
        args["sources"] = sources
    else:
        args["meaning_raw"] += " "
    with pytest.raises(ValueError):
        inspect_independent_review(**args)


def test_no_fact_candidate_cannot_borrow_a_source_probe_from_another_candidate():
    args = _speech_chain()
    meaning, raw, fidelity = _fixture(no_facts=True)
    args.update(meaning=meaning, meaning_raw=raw, fidelity=fidelity)
    with pytest.raises(ValueError, match="unrelated"):
        inspect_independent_review(**args)
