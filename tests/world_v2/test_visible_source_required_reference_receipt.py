"""V3 structural references retain earlier receipt identities and source authority."""

import json
from pathlib import Path

import pytest

from companion_daemon.world_v2.proposal_envelope import DecisionProposal
from companion_daemon.world_v2.visible_source_composer import VisibleSourceTable
from companion_daemon.world_v2.visible_source_review_receipt import (
    VisibleSourceReviewReceipt, prepare_visible_source_review, record_visible_source_review,
    verify_visible_source_review_receipt,
)
from test_visible_source_review_receipt import _hash, _json


def test_frozen_v2_receipt_keeps_exact_complete_preparation_and_bytes():
    raw = (Path(__file__).parent / "fixtures/visible_source_review_receipt_v2.json").read_text().removesuffix("\n")
    assert _hash(raw) == "b70f48470a6712716ed7df2d9cb4f18c576221bf7426bdfd475d463660d1243a"
    receipt = VisibleSourceReviewReceipt.model_validate_json(raw, strict=True)
    assert _json(receipt.model_dump(mode="json")) == raw


@pytest.mark.parametrize("review_version", ["3", "4", "5", "6", "7"])
def test_passing_receipt_requires_model_selected_first_source(review_version):
    fixture = Path(__file__).parent / "fixtures/visible_source_review_receipt_v2.json"
    old = VisibleSourceReviewReceipt.model_validate_json(fixture.read_text(), strict=True)
    value = json.loads(old.prepared_json)
    prepared = prepare_visible_source_review(
        candidate=DecisionProposal.model_validate_json(value["candidate_json"]),
        source_table=VisibleSourceTable(payload_json=value["source_table_json"]),
        source_ref_aliases=value["source_ref_aliases"], review_version=review_version,
    )
    raw = json.loads(old.raw_verdict)
    raw["contract"] = f"visible-beat-source-verdict.{review_version}"
    for decision in raw["decisions"]:
        refs = decision.pop("source_ref_indexes")
        if decision["verdict"] == "closed":
            decision.update(first_source_ref_index=refs[0], additional_source_ref_indexes=refs[1:])
    raw = _json(raw)
    review = old.review.model_copy(update={"request_hash": prepared.as_dict()["request_hash"], "response_hash": _hash(raw)})
    receipt = record_visible_source_review(prepared=prepared, author=old.author, review=review, raw_verdict=raw)
    assert receipt.contract == f"visible-source-review-receipt.{review_version}"
    assert receipt.verdict == old.verdict
    assert verify_visible_source_review_receipt(
        receipt=receipt, expected_prepared=prepared, expected_author=old.author, expected_review=review,
    ) == receipt
    changed = json.loads(raw)
    for decision in changed["decisions"]:
        if decision["verdict"] == "closed":
            decision.pop("first_source_ref_index")
    invalid = _json(changed)
    with pytest.raises(ValueError):
        record_visible_source_review(
            prepared=prepared, author=old.author,
            review=review.model_copy(update={"response_hash": _hash(invalid)}), raw_verdict=invalid,
        )
