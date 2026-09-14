"""Fixed propositions cannot borrow a weaker source scope during review."""
import json

import pytest

from companion_daemon.world_v2.visible_candidate_meaning import prepare_candidate_meaning
from companion_daemon.world_v2.visible_meaning_source_review import (
    CONTRACT, PreparedMeaningSourceReview, prepare_meaning_source_review,
)
from test_visible_candidate_meaning import _raw, TEXT
from test_visible_source_witness_experiment import _sources
from test_visible_source_subject_authority import _report_sources


def _prepared(*, mode="actual_event_or_state", sources=None):
    raw = _raw()
    fact = raw["decisions"][0]["parts"][0]["meanings"][0]
    fact.update(mode=mode, subject_role="companion", affected_roles=[])
    return prepare_meaning_source_review(
        meaning=prepare_candidate_meaning(beats=(TEXT,)),
        meaning_raw=json.dumps(raw, ensure_ascii=False), sources=sources or _sources(),
    )


def _review(*, support=True, ids=None):
    return {
        "contract": CONTRACT, "candidate_reading_faithful": True,
        "unrepresented_facts": [], "interpretation_explanation": "The supplied interpretation is faithful.",
        "fact_decisions": {"b0.p0.f0": {
            "source_support": support, "reading_ids": ["r0"] if ids is None else ids,
            "explanation": "The selected record only shows a prior utterance.",
        }},
    }


def test_fixed_actual_occurrence_cannot_be_reclassified_as_a_prior_utterance():
    prep = _prepared()
    result = prep.inspect_response(json.dumps(_review()))
    assert result["beat_outcomes"] == ["unclosed"]
    assert result["fact_decisions"][0]["rejection_reason"] == "source_permission_denied"
    assert result["fact_decisions"][0]["selected_readings"][0]["use"] == "diagnostic_only"
    # The model can disagree semantically, but cannot replace the fixed mode.
    assert result["reviewer_response"]["fact_decisions"]["b0.p0.f0"]["source_support"] is True
    raw = _review()
    raw["fact_decisions"]["b0.p0.f0"]["mode"] = "past_utterance"
    with pytest.raises(ValueError, match="schema"):
        prep.inspect_response(json.dumps(raw))


def test_pinned_utterance_meaning_can_use_speech_without_acquiring_release_authority():
    result = _prepared(mode="past_utterance").inspect_response(json.dumps(_review()))
    assert result["beat_outcomes"] == ["closed"]
    assert result["fact_decisions"][0]["selected_readings"][0]["permitted_scope"] == "utterance_record"
    assert result["semantic_qualification"] == "unproven" and result["receipt_authority"] is False


@pytest.mark.parametrize("ids", [[], ["r0"]])
def test_rejection_can_carry_diagnostic_readings_without_becoming_technical_failure(ids):
    result = _prepared().inspect_response(json.dumps(_review(support=False, ids=ids)))
    assert result["beat_outcomes"] == ["unclosed"]
    assert result["fact_decisions"][0]["rejection_reason"] == "source_support_rejected"
    assert all(r["use"] == "diagnostic_only" for r in result["fact_decisions"][0]["selected_readings"])


@pytest.mark.parametrize("fault", ["unknown_id", "duplicate_id", "missing_fact", "extra_fact"])
def test_invalid_evidence_transport_is_not_a_support_decision(fault):
    prep = _prepared()
    raw = _review()
    if fault == "unknown_id":
        raw["fact_decisions"]["b0.p0.f0"]["reading_ids"] = ["r999"]
    elif fault == "duplicate_id":
        raw["fact_decisions"]["b0.p0.f0"]["reading_ids"] *= 2
    elif fault == "missing_fact":
        raw["fact_decisions"] = {}
    else:
        raw["fact_decisions"]["invented"] = raw["fact_decisions"]["b0.p0.f0"]
    with pytest.raises(ValueError):
        prep.inspect_response(json.dumps(raw))


def test_interpretation_rejection_or_omission_blocks_otherwise_supported_facts():
    prep = _prepared(mode="past_utterance")
    raw = _review()
    raw["candidate_reading_faithful"] = False
    assert prep.inspect_response(json.dumps(raw))["beat_outcomes"] == ["unclosed"]
    raw["candidate_reading_faithful"] = True
    raw["unrepresented_facts"] = ["The interpretation omitted an event premise."]
    assert prep.inspect_response(json.dumps(raw))["beat_outcomes"] == ["unclosed"]


@pytest.mark.parametrize("field", ["interpreted", "reading_preparation_json"])
def test_pin_changes_cannot_silently_change_the_fixed_meaning_or_evidence(field):
    prep = _prepared()
    pin = json.loads(prep.payload_json)
    if field == "interpreted":
        pin[field]["facts"][0]["mode"] = "past_utterance"
    else:
        reading = json.loads(pin[field])
        reading["catalog"][0]["value"] = "Substituted evidence."
        pin[field] = json.dumps(reading)
    with pytest.raises(ValueError, match="pinned"):
        PreparedMeaningSourceReview(json.dumps(pin)).inspect_response(json.dumps(_review()))


def test_source_free_interpretation_is_still_reviewed_for_omitted_factual_meanings():
    raw = _raw()
    raw["decisions"][0]["parts"][0]["meanings"] = []
    prep = prepare_meaning_source_review(
        meaning=prepare_candidate_meaning(beats=(TEXT,)), meaning_raw=json.dumps(raw), sources=_sources(),
    )
    review = _review()
    del review["fact_decisions"]
    schema = prep.request()["tools"][0]["function"]["parameters"]
    assert "fact_decisions" not in schema["properties"]
    assert "fact_decisions" not in schema["required"]
    assert prep.inspect_response(json.dumps(review))["beat_outcomes"] == ["source_free"]
    review["candidate_reading_faithful"] = False
    assert prep.inspect_response(json.dumps(review))["beat_outcomes"] == ["unclosed"]


def test_report_uptake_preserves_explicit_actor_and_object_in_the_reviewer_packet():
    meaning_raw = _raw()
    fact = meaning_raw["decisions"][0]["parts"][0]["meanings"][0]
    fact.update(proposition="用户的妈妈来车站接用户。", subject_role="other", affected_roles=["counterpart"])
    prep = prepare_meaning_source_review(
        meaning=prepare_candidate_meaning(beats=(TEXT,)), meaning_raw=json.dumps(meaning_raw), sources=_report_sources(),
    )
    body = json.loads(prep.request()["messages"][1]["content"])
    assert body["fixed_facts"][0]["proposition"] == fact["proposition"]
    assert body["fixed_facts"][0]["affected_roles"] == ["counterpart"]
    assert body["fixed_facts"][0]["eligible_reading_ids"] == ["r0"]
    # The deliberately bad original-to-meaning interpretation is not magically
    # detected by structural tests; both semantic stages remain unqualified.
    assert prep.inspect_response(json.dumps(_review()))["receipt_authority"] is False
