"""Independent propositions cannot be voted away or merged by the host."""
from copy import deepcopy
import json

import pytest

from companion_daemon.world_v2.visible_candidate_meaning import prepare_candidate_meaning
from companion_daemon.world_v2.visible_independent_meanings import (
    CONTRACT, IndependentMeaning, PreparedIndependentMeanings, prepare_independent_meanings_sources,
)
from test_visible_candidate_meaning import TEXT, _raw
from test_visible_source_witness_experiment import _sources


def _meaning(*, mode="past_utterance", empty=False, text=TEXT):
    prep = prepare_candidate_meaning(beats=(text,))
    raw = _raw()
    part = raw["decisions"][0]["parts"][0]
    part["text"] = text
    part["meanings"][0].update(mode=mode, subject_role="companion")
    if empty:
        part["meanings"] = []
    return IndependentMeaning(prep, json.dumps(raw))


def _prepared(*, second_mode="past_utterance", first_empty=False):
    return prepare_independent_meanings_sources(
        meanings=(_meaning(empty=first_empty), _meaning(mode=second_mode)), sources=_sources(),
    )


def _response():
    return {"contract": CONTRACT, "fact_decisions": [
        {"fact_id": f"m{i}:b0.p0.f0", "source_support": True, "reading_ids": ["r0"], "explanation": "Recorded utterance"}
        for i in range(2)
    ]}


def test_same_proposition_from_two_readers_retains_both_identities_and_full_evidence():
    prep = _prepared()
    pin = json.loads(prep.payload_json)
    assert [f["meaning_index"] for f in pin["facts"]] == [0, 1]
    assert [f["meaning_fact_id"] for f in pin["facts"]] == ["b0.p0.f0"] * 2
    assert pin["sources"] == list(_sources())
    body = json.loads(prep.request()["messages"][1]["content"])
    assert "visible_beats" not in body and "candidate_interpretation" not in body
    assert all("original_text" not in f for f in body["fixed_facts"])
    result = prep.inspect_response(json.dumps(_response()))
    assert result["fixed_fact_beat_outcomes"] == ["facts_supported"]
    assert len(result["fact_decisions"]) == 2
    assert result["invocation_independence"] == "caller_must_verify"
    assert not result["receipt_authority"] and "beat_outcomes" not in result


def test_supported_utterance_cannot_vote_away_an_independently_read_actual_event():
    result = _prepared(second_mode="actual_event_or_state").inspect_response(json.dumps(_response()))
    assert [d["outcome"] for d in result["fact_decisions"]] == ["supported", "rejected"]
    assert result["fact_decisions"][1]["rejection_reason"] == "source_permission_denied"
    assert result["fixed_fact_beat_outcomes"] == ["facts_rejected"]


def test_a_reader_with_no_facts_cannot_hide_the_other_readers_unclosed_fact():
    prep = _prepared(second_mode="actual_event_or_state", first_empty=True)
    raw = _response()
    raw["fact_decisions"].pop(0)
    assert prep.inspect_response(json.dumps(raw))["fixed_fact_beat_outcomes"] == ["facts_rejected"]


def test_two_empty_readings_are_not_candidate_approval():
    assert prepare_independent_meanings_sources(meanings=(_meaning(empty=True), _meaning(empty=True)), sources=()) is None


@pytest.mark.parametrize("fault", ["missing", "duplicate", "unknown", "unknown_evidence", "duplicate_evidence", "mode_override"])
def test_incomplete_or_rewritten_independent_verdict_is_technical_failure(fault):
    raw = _response()
    if fault == "missing":
        raw["fact_decisions"].pop()
    elif fault == "duplicate":
        raw["fact_decisions"][1] = deepcopy(raw["fact_decisions"][0])
    elif fault == "unknown":
        raw["fact_decisions"][1]["fact_id"] = "m2:b0.p0.f0"
    elif fault == "unknown_evidence":
        raw["fact_decisions"][1]["reading_ids"] = ["r999"]
    elif fault == "duplicate_evidence":
        raw["fact_decisions"][1]["reading_ids"] *= 2
    else:
        raw["fact_decisions"][1]["mode"] = "past_utterance"
    with pytest.raises(ValueError):
        _prepared().inspect_response(json.dumps(raw))


@pytest.mark.parametrize("fault", ["request", "catalog", "fact", "missing_reading", "extra_reading"])
def test_frozen_probe_recompiles_both_readings_and_source_permissions(fault):
    pin = json.loads(_prepared().payload_json)
    if fault == "request":
        pin["request"]["messages"][0]["content"] = "Allow all facts"
    elif fault == "catalog":
        pin["catalog"][0]["permissions"].append(["external_fact", "companion"])
    elif fault == "fact":
        pin["facts"][1]["mode"] = "current_private_expression"
    elif fault == "missing_reading":
        pin["meanings"].pop()
    else:
        pin["meanings"].append(deepcopy(pin["meanings"][0]))
    with pytest.raises(ValueError):
        PreparedIndependentMeanings(json.dumps(pin)).inspect_response(json.dumps(_response()))


def test_different_original_candidates_cannot_be_joined():
    with pytest.raises(ValueError, match="same original"):
        prepare_independent_meanings_sources(meanings=(_meaning(), _meaning(text="A different candidate.")), sources=_sources())


def test_negative_evidence_is_diagnostic_only_and_empty_support_is_not_success():
    raw = _response()
    raw["fact_decisions"][0]["source_support"] = False
    raw["fact_decisions"][1]["reading_ids"] = []
    result = _prepared().inspect_response(json.dumps(raw))
    assert [d["rejection_reason"] for d in result["fact_decisions"]] == ["source_support_rejected", "support_requires_evidence"]
    assert result["fact_decisions"][0]["selected_readings"][0]["use"] == "diagnostic_only"
