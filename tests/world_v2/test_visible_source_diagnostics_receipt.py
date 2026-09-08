"""Versioned source diagnostics retain full review and independent audit binding."""

import json

import pytest

from companion_daemon.world_v2.visible_source_composer import compile_visible_source_table
from companion_daemon.world_v2.visible_source_review_receipt import (
    VisibleSourceReviewReceipt,
    VisibleSourceReviewRejected,
    prepare_visible_source_review,
    record_visible_source_review,
    verify_visible_source_review_receipt,
)
from test_visible_selected_source_context import _sources
from test_visible_source_review_receipt import _bindings, _candidate, _hash, _json, _raw


def _prepare_v2(case, *, texts=None):
    from companion_daemon.world_v2.expression_draft import TEXT_ONLY_EXPRESSION_CAPABILITIES, materialize_expression_draft

    candidate = _candidate(case) if texts is None else materialize_expression_draft(
        value={"timing_choice": "now", "stance": "present", "brief_rationale": "完整候选边界测试。",
               "beats": [{"modality": "text", "text": text} for text in texts], "world_claims": []},
        request=case.request,
        capabilities=TEXT_ONLY_EXPRESSION_CAPABILITIES.model_copy(update={"max_beats": 16}),
    )
    return prepare_visible_source_review(
        candidate=candidate,
        source_table=compile_visible_source_table(request=case.request, capsule=case.capsule),
        source_ref_aliases={"S1": case.request.trigger_ref},
        review_version="2",
    )


def _raw_v2(prepared, *, rejected=False):
    value = json.loads(_raw(prepared, verdict="unclosed" if rejected else "closed"))
    value["contract"] = "visible-beat-source-verdict.2"
    value["rejections"] = [{
        "beat_index": 0, "char_start": 1, "char_end": 4,
        "related_source_ref_indexes": [], "source_problem": "原材料没有支持这段陈述。",
    }] if rejected else []
    return _json(value)


@pytest.mark.asyncio
async def test_v2_receipt_and_rejection_bind_complete_preparation(tmp_path):
    async with _sources(tmp_path) as case:
        prepared = _prepare_v2(case)
        assert prepared.as_dict()["contract"] == "visible-source-review-request.2"
        raw = _raw_v2(prepared)
        author, review = _bindings(prepared, raw)
        receipt = record_visible_source_review(
            prepared=prepared, author=author, review=review, raw_verdict=raw,
        )
        assert receipt.contract == "visible-source-review-receipt.2"
        assert verify_visible_source_review_receipt(
            receipt=receipt, expected_prepared=prepared,
            expected_author=author, expected_review=review,
        ) == receipt
        raw = _raw_v2(prepared, rejected=True)
        author, review = _bindings(prepared, raw)
        with pytest.raises(VisibleSourceReviewRejected) as error:
            record_visible_source_review(
                prepared=prepared, author=author, review=review, raw_verdict=raw,
            )
        assert error.value.rejections[0].char_start == 1
        assert error.value.verdict.segments[0].locator.text == prepared.as_dict()["beat_mapping"][0]["text"]


@pytest.mark.asyncio
async def test_receipt_version_relabel_cannot_change_static_compiler(tmp_path):
    async with _sources(tmp_path) as case:
        prepared = _prepare_v2(case)
        raw = _raw_v2(prepared)
        author, review = _bindings(prepared, raw)
        receipt = record_visible_source_review(
            prepared=prepared, author=author, review=review, raw_verdict=raw,
        )
        value = receipt.model_dump(mode="json")
        value["contract"] = "visible-source-review-receipt.1"
        value["receipt_hash"] = _hash(_json({k: v for k, v in value.items() if k != "receipt_hash"}))
        with pytest.raises(ValueError):
            VisibleSourceReviewReceipt.model_validate_json(_json(value), strict=True)
        with pytest.raises(ValueError):
            record_visible_source_review(
                prepared=prepared, author=author,
                review=review.model_copy(update={"response_hash": _hash(_raw(prepared))}),
                raw_verdict=_raw(prepared),
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("texts", [("界" * 4096, "嗯"), tuple('"' * 1024 for _ in range(16))])
async def test_full_legal_spans_and_all_diagnostics_survive_feedback_limits(tmp_path, texts):
    from companion_daemon.world_v2.visible_source_rejection_feedback import rejection_feedback

    async with _sources(tmp_path) as case:
        prepared = _prepare_v2(case, texts=texts)
        raw = _json({
            "contract": "visible-beat-source-verdict.2",
            "decisions": [{
                "beat_index": i, "verdict": "unclosed", "semantic_role": "external_proposition",
                "subject_role": "companion", "source_ref_indexes": [],
            } for i in range(len(texts))],
            "rejections": [{
                "beat_index": i, "char_start": 0, "char_end": len(text),
                "related_source_ref_indexes": [], "source_problem": '"' * 47,
            } for i, text in enumerate(texts)],
        })
        author, review = _bindings(prepared, raw)
        with pytest.raises(VisibleSourceReviewRejected) as failure:
            record_visible_source_review(prepared=prepared, author=author, review=review, raw_verdict=raw)
        detail = rejection_feedback(prepared=prepared, rejection=failure.value, review=review)
        assert len(detail) <= 3900
        assert detail[:4000] == detail[:4096] == detail
        payload = json.loads(detail.split("\n", 1)[1])
        assert len(payload["rows"]) == len(texts)
        for i, row in enumerate(payload["rows"]):
            assert row[:3] == [i, 0, len(texts[i])]
            assert texts[i].startswith(row[3])
            assert len(json.dumps(row[3], ensure_ascii=False)) <= 32
            assert row[4] == '"' * 47
        assert payload["bindings"] == [
            _hash(prepared.as_dict()["candidate_json"]),
            _hash(prepared.as_dict()["source_table_json"]), review.request_hash, review.response_hash,
        ]
