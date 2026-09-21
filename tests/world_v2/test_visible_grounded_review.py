"""Grounded-review boundaries with scripted verdicts, not semantic qualification."""

from copy import deepcopy
import hashlib
import json

import pytest

from companion_daemon.llm import provider_invocation_request_hash
from companion_daemon.world_v2.shared_string_view import unpack_shared_strings
from companion_daemon.world_v2.visible_source_composer import compile_visible_source_table
from companion_daemon.world_v2.visible_source_review_receipt import (
    VisibleReviewAuthorBinding, VisibleReviewInvocationBinding,
)
from companion_daemon.world_v2.visible_grounded_review import (
    PROTOCOL, GroundedReviewInconclusive, GroundedVisibleReviewReceipt,
    GroundedVisibleReviewRejected, PreparedGroundedReview, prepare_grounded_review,
    record_grounded_review_receipt, verify_grounded_review_receipt,
)
from test_visible_selected_source_context import _sources
from test_visible_lifecycle_receipt import ended_sources as ended_sources
from test_visible_source_review_receipt import _candidate


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


async def _prepare(tmp_path, *, texts=("你取消了周五的报告。", "我想先听你说。"), retained_value=None):
    async with _sources(tmp_path, retained_value=retained_value) as case:
        candidate = _candidate(case, texts=texts)
        return prepare_grounded_review(
            candidate=candidate,
            source_table=compile_visible_source_table(request=case.request, capsule=case.capsule),
            source_ref_aliases={"S1": case.request.trigger_ref},
        )


def _response(prepared, *, factual=True, supported=True):
    pin = prepared.as_dict()
    report = next(r for r in pin["catalog"] if ["report_uptake", "counterpart"] in r["permissions"])
    beats = []
    for index, beat in enumerate(pin["beat_mapping"]):
        text = beat["text"]
        facts = [{"text": text, "proposition": "用户取消周五的报告", "claim_scope": "report_uptake",
                  "subject_role": "counterpart", "reading_ids": [report["reading_id"]] if supported else [],
                  "fact_value_selections": [], "source_support": supported,
                  "rationale": "Fixture reports support cancellation" if supported else "Fixture finds no support"}] if factual and index == 0 else []
        beats.append({"beat_index": index, "review_complete": True, "facts": facts,
            "non_record_expressions": [] if facts else [{"text": text, "rationale": "Fixture current expression"}],
            "unresolved_details": []})
    return {"contract": PROTOCOL, "beat_decisions": beats}


def _record_args(prepared, response):
    raw = _json(response)
    return dict(prepared=prepared,
        author=VisibleReviewAuthorBinding(model_call_id="call:author", request_hash="a" * 64,
            proposal_material_hash=_hash(prepared.as_dict()["candidate_json"])),
        review=VisibleReviewInvocationBinding(parent_model_call_id="call:author", model_call_id="call:grounded",
            model_id="fixture:review", model_version="fixture.1", request_hash=prepared.request_hash,
            response_hash=_hash(raw)), raw_response=raw)


@pytest.mark.asyncio
async def test_single_json_request_contains_complete_context_and_all_source_cards(tmp_path):
    prepared = await _prepare(tmp_path)
    pin, request = prepared.as_dict(), prepared.request()
    body = json.loads(request["messages"][1]["content"])
    assert len(request["messages"]) == 2 and "tools" not in request and "tool_choice" not in request
    assert body["visible_beats"] == [{"beat_index": b["beat_index"], "text": b["text"]} for b in pin["beat_mapping"]]
    assert pin["world_claims"] == [] and body["output_schema"]["properties"]["beat_decisions"]
    assert "fixed_facts" not in body and "independent_readings" not in body
    assert body["dialogue_context"]
    assert "取消周五的报告" in _json(body["dialogue_context"])
    cards = unpack_shared_strings(body["source_materials"])
    shown = {r["reading_id"] for card in cards for r in card["readings"]}
    assert shown == {r["reading_id"] for r in pin["catalog"]}
    assert prepared.request_hash == provider_invocation_request_hash(**request, identity_extras=prepared.identity_extras)
    assert PreparedGroundedReview(prepared.payload_json).request() == request


@pytest.mark.asyncio
@pytest.mark.parametrize("factual", [True, False])
async def test_passing_receipt_requires_original_single_invocation_and_cold_replays(tmp_path, factual):
    prepared = await _prepare(tmp_path)
    args = _record_args(prepared, _response(prepared, factual=factual))
    receipt = record_grounded_review_receipt(**args)
    assert receipt.beat_outcomes == (("closed", "source_free") if factual else ("source_free", "source_free"))
    assert "meaning_reviews" not in receipt.model_dump()
    cold = GroundedVisibleReviewReceipt.model_validate_json(receipt.model_dump_json(), strict=True)
    assert verify_grounded_review_receipt(receipt=cold, expected_prepared=prepared,
        expected_author=args["author"], expected_invocation=args["review"]) == receipt
    with pytest.raises(ValueError, match="immutable invocation"):
        verify_grounded_review_receipt(receipt=cold, expected_prepared=prepared,
            expected_author=args["author"], expected_invocation=args["review"].model_copy(update={"model_call_id": "call:other"}))


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing_beat", "duplicate_beat", "empty_representation", "invented_span", "blank_rationale", "unknown_reading", "duplicate_reading", "foreign_member"])
async def test_missing_coverage_or_malformed_evidence_never_passes(tmp_path, fault):
    prepared = await _prepare(tmp_path)
    value = _response(prepared)
    first = value["beat_decisions"][0]
    fact = first["facts"][0]
    if fault == "missing_beat":
        value["beat_decisions"].pop()
    elif fault == "duplicate_beat":
        value["beat_decisions"].append(deepcopy(first))
    elif fault == "empty_representation":
        first["facts"] = []
    elif fault == "invented_span":
        fact["text"] = "not present in the original beat"
    elif fault == "blank_rationale":
        fact["rationale"] = " "
    elif fault == "unknown_reading":
        fact["reading_ids"] = ["r-unavailable"]
    elif fault == "duplicate_reading":
        fact["reading_ids"] *= 2
    else:
        fact["mode"] = "past_subjective_state"
    with pytest.raises(ValueError):
        record_grounded_review_receipt(**_record_args(prepared, value))


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing_support", "empty_support", "subject_swap", "report_as_objective", "report_as_old_feeling"])
async def test_real_missing_support_and_permission_overreach_retain_exact_diagnostics(tmp_path, fault):
    prepared = await _prepare(tmp_path)
    value = _response(prepared)
    fact = value["beat_decisions"][0]["facts"][0]
    if fault == "missing_support":
        fact.update(source_support=False, reading_ids=[])
    elif fault == "empty_support":
        fact["reading_ids"] = []
    elif fault == "subject_swap":
        fact["subject_role"] = "companion"
    elif fault == "report_as_objective":
        fact["claim_scope"] = "external_fact"
    else:
        fact["claim_scope"] = "subjective_history"
    with pytest.raises(GroundedVisibleReviewRejected) as caught:
        record_grounded_review_receipt(**_record_args(prepared, value))
    assert caught.value.outcomes == ("unclosed", "source_free")
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic["proposition"] == fact["proposition"] and diagnostic["rationale"] == fact["rationale"]
    assert _json(json.loads(caught.value.feedback)) == caught.value.feedback
    assert len(caught.value.feedback) <= 3900


@pytest.mark.asyncio
async def test_uncertainty_is_not_source_free_and_definite_rejection_remains_visible(tmp_path):
    prepared = await _prepare(tmp_path)
    value = _response(prepared, factual=False)
    value["beat_decisions"][1]["unresolved_details"] = ["Fixture genuinely unresolved reference"]
    with pytest.raises(GroundedReviewInconclusive):
        record_grounded_review_receipt(**_record_args(prepared, value))
    value["beat_decisions"][0] = _response(prepared, supported=False)["beat_decisions"][0]
    with pytest.raises(GroundedVisibleReviewRejected) as caught:
        record_grounded_review_receipt(**_record_args(prepared, value))
    assert [d["reason"] for d in caught.value.diagnostics] == ["source_support_rejected", "review_inconclusive"]


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["source_table_json", "candidate_json", "catalog", "request"])
async def test_canonical_but_modified_preparation_is_not_a_new_authority(tmp_path, field):
    prepared = await _prepare(tmp_path)
    pin = prepared.as_dict()
    if field in {"source_table_json", "candidate_json"}:
        pin[field] += " "
    elif field == "catalog":
        pin[field][0]["permissions"].append(["external_fact", "companion"])
    else:
        pin[field]["messages"][0]["content"] += " changed"
    changed = PreparedGroundedReview(_json(pin))
    with pytest.raises(ValueError):
        changed.inspect_response(_json(_response(prepared)))


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["wrong_parent", "same_author_call", "wrong_request", "wrong_raw", "wrong_candidate"])
async def test_receipt_cannot_relabel_author_or_provider_evidence(tmp_path, fault):
    prepared = await _prepare(tmp_path)
    args = _record_args(prepared, _response(prepared))
    if fault == "wrong_candidate":
        args["author"] = args["author"].model_copy(update={"proposal_material_hash": "b" * 64})
    elif fault == "wrong_raw":
        args["raw_response"] += " "
    else:
        field, value = {"wrong_parent": ("parent_model_call_id", "call:other"),
                        "same_author_call": ("model_call_id", "call:author"),
                        "wrong_request": ("request_hash", "b" * 64)}[fault]
        args["review"] = args["review"].model_copy(update={field: value})
    with pytest.raises(ValueError, match="invocation differs"):
        record_grounded_review_receipt(**args)


@pytest.mark.asyncio
async def test_accepted_fact_requires_exact_value_subject_and_scope(tmp_path):
    value = "保留周四的约定"
    prepared = await _prepare(tmp_path, texts=("你保留了周四的约定。",), retained_value=value)
    reading = next(r for r in prepared.as_dict()["catalog"] if r.get("source_family") == "accepted_fact_value")
    response = _response(prepared)
    fact = response["beat_decisions"][0]["facts"][0]
    fact.update(proposition="用户保留周四约定", claim_scope="accepted_fact", reading_ids=[],
        fact_value_selections=[{"reading_id": reading["reading_id"], "quoted_value": value,
                                "subject_ref": reading["source_owner_ref"]}])
    assert record_grounded_review_receipt(**_record_args(prepared, response)).beat_outcomes == ("closed",)
    for field, bad in (("quoted_value", reading["value"]), ("subject_ref", "actor:other")):
        changed = deepcopy(response)
        changed["beat_decisions"][0]["facts"][0]["fact_value_selections"][0][field] = bad
        with pytest.raises(GroundedVisibleReviewRejected):
            record_grounded_review_receipt(**_record_args(prepared, changed))
    fact.update(reading_ids=[reading["reading_id"]], fact_value_selections=[])
    with pytest.raises(GroundedVisibleReviewRejected):
        record_grounded_review_receipt(**_record_args(prepared, response))


@pytest.mark.asyncio
async def test_feedback_overflow_is_explicit_failure_not_truncated_rejection(tmp_path):
    prepared = await _prepare(tmp_path)
    response = _response(prepared, supported=False)
    fact = response["beat_decisions"][0]["facts"][0]
    fact["rationale"] = "详细解释" * 200
    response["beat_decisions"][0]["facts"] = [deepcopy(fact) for _ in range(5)]
    with pytest.raises(ValueError, match="complete diagnostic bound"):
        record_grounded_review_receipt(**_record_args(prepared, response))


@pytest.mark.asyncio
async def test_real_lifecycle_fields_keep_state_authority_without_intention_or_outcome(ended_sources):
    case, table = ended_sources
    prepared = prepare_grounded_review(candidate=_candidate(case, texts=("那项活动已经结束了。",)),
        source_table=table, source_ref_aliases={})
    catalog = prepared.as_dict()["catalog"]
    status = next(r for r in catalog if r["pointer"] == "/item/value/status"
                  and ["activity_lifecycle", "companion"] in r["permissions"])
    intention = next(r for r in catalog if r["pointer"] == "/item/value/accepted_intention/text")
    response = {"contract": PROTOCOL, "beat_decisions": [{"beat_index": 0, "review_complete": True,
        "facts": [{"text": "那项活动已经结束了。", "proposition": "同一活动的生命周期已结束",
                   "claim_scope": "activity_lifecycle", "subject_role": "companion",
                   "reading_ids": [status["reading_id"]], "fact_value_selections": [],
                   "source_support": True, "rationale": "Fixture permits only the typed ended state"}],
        "non_record_expressions": [], "unresolved_details": []}]}
    assert record_grounded_review_receipt(**_record_args(prepared, response)).beat_outcomes == ("closed",)
    fact = response["beat_decisions"][0]["facts"][0]
    fact["claim_scope"] = "external_fact"
    with pytest.raises(GroundedVisibleReviewRejected):
        record_grounded_review_receipt(**_record_args(prepared, response))
    fact.update(claim_scope="activity_lifecycle", reading_ids=[intention["reading_id"]])
    with pytest.raises(GroundedVisibleReviewRejected):
        record_grounded_review_receipt(**_record_args(prepared, response))
    body = json.loads(prepared.request()["messages"][1]["content"])
    displayed = next(r for card in unpack_shared_strings(body["source_materials"])
                     for r in card["readings"] if r["reading_id"] == intention["reading_id"])
    assert ["activity_lifecycle", "companion"] not in displayed["allowed_claims"]
