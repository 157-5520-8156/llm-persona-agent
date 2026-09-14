"""Bind semantic judgments to submitted evidence, without model-copied hashes."""
from datetime import timedelta
import json

import pytest

from companion_daemon.world_v2.character_prehistory import digest
from companion_daemon.world_v2.prehistory_authoring import (
    PrehistorySemanticReview, bind_semantic_review, package_reviewed, semantic_review_request,
)
from test_prehistory_authoring import brief
from test_character_prehistory import START, reviewed_archive


def materials():
    document = reviewed_archive().document
    second = document.records[0].model_copy(update={"record_id": "prehistory-record:second",
        "statement": "另一条单独接受审核的童年片段。"})
    document = document.model_copy(update={"records": (*document.records, second)})
    return brief(), document


def parsed(value):
    return PrehistorySemanticReview.model_validate_json(json.dumps(value))


def response():
    return parsed({"decision": "approved", "records": [
        {"record_index": 1, "verdict": "approve", "rationale": "Second record fixture rationale."},
        {"record_index": 0, "verdict": "approve", "rationale": "First record fixture rationale."},
    ]})


def bind(packet, reply=None, **kwargs):
    return bind_semantic_review(packet, reply or response(), reviewer_ref=kwargs.get("reviewer_ref", "reviewer:fixture"),
                                reviewed_at=kwargs.get("reviewed_at", START))


def test_out_of_order_judgments_bind_each_exact_submitted_record():
    b, document = materials()
    packet = semantic_review_request(b, document)
    result = bind(packet)
    assert result.document_hash == digest(document)
    assert [r.record_hash for r in result.records] == [digest(r) for r in document.records]
    assert [r.rationale for r in result.records] == ["First record fixture rationale.", "Second record fixture rationale."]
    approved = package_reviewed(b, document, result, review_artifact_ref="fixture:semantic-review")
    assert approved.review.review_artifact_hash == digest(result)
    changed = document.model_copy(update={"records": tuple(reversed(document.records))})
    with pytest.raises(ValueError, match="current brief and document"):
        package_reviewed(b, changed, result, review_artifact_ref="fixture:semantic-review")


@pytest.mark.parametrize("mutation", ["document_hash", "brief_hash", "record_hash", "document", "instruction", "contract"])
def test_modified_submitted_packet_cannot_be_rebound(mutation):
    b, document = materials()
    packet = semantic_review_request(b, document)
    if mutation in {"document_hash", "brief_hash"}:
        packet[mutation] = "e" * 64
    elif mutation == "record_hash":
        packet["record_hashes"][document.records[0].record_id] = "e" * 64
    elif mutation == "document":
        packet["document"]["records"][0]["statement"] = "后来改写的不同事件。"
    else:
        packet[mutation] = "different-contract-or-instruction"
    with pytest.raises(ValueError, match="submitted semantic review packet"):
        bind(packet)


@pytest.mark.parametrize("indexes", [[0], [0, 2], [0, 0], [-1, 1], [False, 1], [0, "1"]])
def test_missing_duplicate_out_of_range_and_coerced_indexes_never_approve(indexes):
    b, document = materials()
    with pytest.raises(ValueError):
        reply = parsed({"decision": "approved", "records": [
            {"record_index": i, "verdict": "approve", "rationale": "Fixture."} for i in indexes]})
        bind(semantic_review_request(b, document), reply)


def test_rejections_survive_binding_and_old_invalid_hash_reply_is_not_reinterpreted():
    b, document = materials()
    raw = response().model_dump(mode="json")
    raw.update(decision="rejected", cross_record_findings=["These two events contradict each other."])
    result = bind(semantic_review_request(b, document), parsed(raw))
    assert result.decision == "rejected" and result.cross_record_findings == tuple(raw["cross_record_findings"])
    with pytest.raises(ValueError, match="rejected draft"):
        package_reviewed(b, document, result, review_artifact_ref="fixture:rejected")
    with pytest.raises(ValueError):
        parsed(result.model_dump(mode="json"))
    raw["decision"] = "approved"
    with pytest.raises(ValueError, match="decision disagrees"):
        parsed(raw)


@pytest.mark.parametrize("kwargs", [{"reviewer_ref": "author:fixture"},
    {"reviewed_at": START - timedelta(seconds=1)}, {"reviewed_at": START.replace(tzinfo=None)}])
def test_binding_still_checks_operator_provenance(kwargs):
    b, document = materials()
    with pytest.raises(ValueError):
        bind(semantic_review_request(b, document), **kwargs)


def test_cli_uses_the_saved_request_and_preserves_original_judgment(tmp_path, capsys):
    from prepare_character_prehistory import main
    b, document = materials()
    bpath, dpath, request_path, response_path, review_path = [tmp_path / f"{name}.json" for name in
        ("brief", "draft", "request", "response", "review")]
    bpath.write_text(b.model_dump_json())
    dpath.write_text(document.model_dump_json())
    main(["semantic-review-request", "--brief", str(bpath), "--draft", str(dpath), "--out", str(request_path)])
    response_path.write_text(response().model_dump_json())
    main(["bind-semantic-review", "--request", str(request_path), "--response", str(response_path),
          "--reviewer-ref", "reviewer:fixture", "--reviewed-at", START.isoformat(), "--out", str(review_path)])
    assert json.loads(review_path.read_text())["document_hash"] == digest(document)
    assert json.loads(response_path.read_text()) == response().model_dump(mode="json")
    capsys.readouterr()
