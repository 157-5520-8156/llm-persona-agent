"""Complete review records require all independently anchored invocations."""
from copy import deepcopy
import hashlib
import json

import pytest

from companion_daemon.world_v2.visible_candidate_meaning import COMPLETE_READING_CONTRACT
from companion_daemon.world_v2.visible_source_composer import compile_visible_source_table
from companion_daemon.world_v2.visible_source_review_receipt import VisibleReviewAuthorBinding, VisibleReviewInvocationBinding
from companion_daemon.world_v2.visible_independent_review_receipt import (
    IndependentReviewInconclusive, IndependentVisibleReviewReceipt, IndependentVisibleReviewRejected,
    prepare_independent_visible_review, prepare_meaning_call, prepare_source_call,
    record_independent_visible_review, verify_independent_visible_review_receipt,
)
from companion_daemon.world_v2.visible_source_witness_experiment import _json
from test_visible_selected_source_context import _sources
from test_visible_source_review_receipt import _candidate


def _hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def _reading(*, factual=True):
    return _json({"contract": COMPLETE_READING_CONTRACT, "decisions": [{
        "beat_index": 0, "reading_complete": True, "unresolved_details": [],
        "hypothetical_conditions": [], "questions": [],
        "meanings": [{"proposition": "用户取消了周五的报告。" if factual else "角色此刻想先听用户说。",
                      "mode": "actual_event_or_state" if factual else "current_private_expression",
                      "subject_role": "counterpart" if factual else "companion"}],
    }]})


def _binding(call, raw, index):
    return VisibleReviewInvocationBinding(
        parent_model_call_id="model-call:author", model_call_id=f"model-call:review:{index}",
        model_id=f"reader:{index}", model_version="fixture.1", request_hash=call.request_hash, response_hash=_hash(raw),
    )


async def _args(tmp_path, *, factual=True):
    async with _sources(tmp_path) as case:
        candidate = _candidate(case, texts=("你取消了周五的报告。" if factual else "我想先听你说。",))
        prepared = prepare_independent_visible_review(
            candidate=candidate, source_table=compile_visible_source_table(request=case.request, capsule=case.capsule),
            source_ref_aliases={"S1": case.request.trigger_ref},
        )
    author = VisibleReviewAuthorBinding(model_call_id="model-call:author", request_hash="a" * 64,
                                       proposal_material_hash=_hash(prepared.as_dict()["candidate_json"]))
    raws = (_reading(factual=factual), _reading(factual=factual))
    bindings = tuple(_binding(prepare_meaning_call(prepared=prepared, meaning_index=i), raw, i) for i, raw in enumerate(raws))
    source_call = prepare_source_call(prepared=prepared, meaning_raw_responses=raws)
    source_raw = None
    source_binding = None
    if source_call:
        body = json.loads(source_call.request["messages"][1]["content"])
        facts = body["fixed_facts"]
        reading_id = facts[0]["eligible_reading_ids"][0]
        source_raw = _json({"contract": body["output_contract"]["contract"], "fact_decisions": [{
            "fact_id": f["fact_id"], "source_support": True, "reading_ids": [reading_id], "explanation": "Fixture report supports cancellation",
        } for f in facts]})
        source_binding = _binding(source_call, source_raw, 2)
    return dict(prepared=prepared, author=author, meaning_reviews=bindings, meaning_raw_responses=raws,
                source_review=source_binding, source_raw_response=source_raw)


def _expected(args):
    return dict(expected_prepared=args["prepared"], expected_author=args["author"],
                expected_invocations=(*args["meaning_reviews"], *((args["source_review"],) if args["source_review"] else ())))


@pytest.mark.asyncio
@pytest.mark.parametrize("factual", [True, False])
async def test_complete_record_cold_replays_and_joins_original_invocations(tmp_path, factual):
    args = await _args(tmp_path, factual=factual)
    receipt = record_independent_visible_review(**args)
    assert receipt.beat_outcomes == (("closed",) if factual else ("source_free",))
    restored = IndependentVisibleReviewReceipt.model_validate_json(receipt.model_dump_json(), strict=True)
    assert verify_independent_visible_review_receipt(receipt=restored, **_expected(args)) == receipt
    first = prepare_meaning_call(prepared=args["prepared"], meaning_index=0)
    second = prepare_meaning_call(prepared=args["prepared"], meaning_index=1)
    assert first.request == second.request and first.request_hash != second.request_hash
    assert set(json.loads(first.request["messages"][1]["content"])) == {"contract", "visible_beats"}
    assert (args["source_review"] is None) is not factual


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing_meaning", "duplicate_call", "same_model", "wrong_parent", "wrong_request", "changed_raw", "missing_source", "reused_source_call", "wrong_author"])
async def test_invalid_stage_binding_cannot_become_a_passing_receipt(tmp_path, fault):
    args = await _args(tmp_path)
    reviews = list(args["meaning_reviews"])
    if fault == "missing_meaning":
        reviews.pop()
    elif fault == "duplicate_call":
        reviews[1] = reviews[1].model_copy(update={"model_call_id": reviews[0].model_call_id})
    elif fault == "same_model":
        reviews[1] = reviews[1].model_copy(update={"model_id": reviews[0].model_id})
    elif fault == "wrong_parent":
        reviews[0] = reviews[0].model_copy(update={"parent_model_call_id": "model-call:other-author"})
    elif fault == "wrong_request":
        reviews[0] = reviews[0].model_copy(update={"request_hash": "b" * 64})
    elif fault == "changed_raw":
        args["meaning_raw_responses"] = (args["meaning_raw_responses"][0] + " ", args["meaning_raw_responses"][1])
    elif fault == "missing_source":
        args["source_review"] = None
    elif fault == "reused_source_call":
        args["source_review"] = args["source_review"].model_copy(update={"model_call_id": reviews[0].model_call_id})
    else:
        args["author"] = args["author"].model_copy(update={"proposal_material_hash": "b" * 64})
    args["meaning_reviews"] = tuple(reviews)
    with pytest.raises(ValueError):
        record_independent_visible_review(**args)


@pytest.mark.asyncio
async def test_legal_source_rejection_keeps_both_readings_and_diagnostic_source_result(tmp_path):
    args = await _args(tmp_path)
    value = json.loads(args["source_raw_response"])
    value["fact_decisions"][1].update(source_support=False, reading_ids=[])
    raw = _json(value)
    args["source_raw_response"] = raw
    args["source_review"] = args["source_review"].model_copy(update={"response_hash": _hash(raw)})
    with pytest.raises(IndependentVisibleReviewRejected) as caught:
        record_independent_visible_review(**args)
    assert caught.value.outcomes == ("unclosed",)
    assert len(caught.value.readings) == 2
    assert caught.value.support["fact_decisions"][1]["outcome"] == "rejected"


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["unresolved", "false_complete", "all_empty", "old_contract"])
async def test_source_free_requires_positive_complete_readings_not_two_empty_fact_arrays(tmp_path, fault):
    args = await _args(tmp_path, factual=False)
    value = json.loads(args["meaning_raw_responses"][0])
    decision = value["decisions"][0]
    if fault == "unresolved":
        decision["unresolved_details"] = ["Participant uncertain"]
    elif fault == "false_complete":
        decision["reading_complete"] = False
    elif fault == "all_empty":
        decision["meanings"] = []
    else:
        value["contract"] = "visible-candidate-meaning.6"
        del decision["reading_complete"], decision["unresolved_details"]
    raw = _json(value)
    args["meaning_raw_responses"] = (raw, args["meaning_raw_responses"][1])
    args["meaning_reviews"] = (args["meaning_reviews"][0].model_copy(update={"response_hash": _hash(raw)}), args["meaning_reviews"][1])
    with pytest.raises(IndependentReviewInconclusive if fault in {"unresolved", "false_complete"} else ValueError):
        record_independent_visible_review(**args)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing_audit", "different_audit", "different_candidate", "different_author"])
async def test_self_consistent_receipt_is_not_an_independent_audit_anchor(tmp_path, fault):
    args = await _args(tmp_path)
    receipt = record_independent_visible_review(**args)
    expected = _expected(args)
    if fault == "missing_audit":
        expected["expected_invocations"] = expected["expected_invocations"][:-1]
    elif fault == "different_audit":
        bindings = list(expected["expected_invocations"])
        bindings[0] = bindings[0].model_copy(update={"response_hash": "b" * 64})
        expected["expected_invocations"] = tuple(bindings)
    elif fault == "different_candidate":
        other = await _args(tmp_path / "other", factual=False)
        expected["expected_prepared"] = other["prepared"]
    else:
        expected["expected_author"] = args["author"].model_copy(update={"request_hash": "b" * 64})
    with pytest.raises(ValueError):
        verify_independent_visible_review_receipt(receipt=receipt, **expected)


@pytest.mark.asyncio
async def test_source_invocation_hash_binds_both_exact_meaning_response_bytes(tmp_path):
    args = await _args(tmp_path)
    original = prepare_source_call(prepared=args["prepared"], meaning_raw_responses=args["meaning_raw_responses"])
    changed = deepcopy(args["meaning_raw_responses"])
    changed = (changed[0], changed[1] + " ")
    other = prepare_source_call(prepared=args["prepared"], meaning_raw_responses=changed)
    assert original.request == other.request
    assert original.request_hash != other.request_hash
