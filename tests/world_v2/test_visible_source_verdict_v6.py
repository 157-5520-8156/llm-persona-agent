"""Model-selected diagnostic codes keep whole-Beat authority and frozen receipts."""
import json
from pathlib import Path

import pytest

from companion_daemon.world_v2 import visible_source_closure_protocol as protocol
from companion_daemon.world_v2.visible_source_review_receipt import VisibleSourceReviewReceipt, _hash, _json

BEAT = "我想出去。甲😀乙e\u0301已经到了。"


def _wire():
    return {
        "contract": "visible-beat-source-verdict.6",
        "decisions": [{"beat_index": 0, "verdict": "unclosed", "semantic_role": "mixed", "subject_role": "companion"}],
        "rejections": [{"beat_index": 0, "related_source_ref_indexes": [], "source_problem": "status_mismatch"}],
    }


def _parse(value):
    return protocol.parse_visible_source_verdict(json.dumps(value), version="6", visible_beats=(BEAT,), source_ref_kinds=())


def test_v5_receipt_remains_byte_exact_and_cold_verifies():
    raw = (Path(__file__).parent / "fixtures/visible_source_review_receipt_v5.json").read_text().removesuffix("\n")
    assert _hash(raw) == "d098fd8ce87cd8af7c49a613e057b04dd8fe55cc856bc239ed1300e901c58b31"
    receipt = VisibleSourceReviewReceipt.model_validate_json(raw, strict=True)
    assert _json(receipt.model_dump(mode="json")) == raw


def test_v6_tool_and_parser_accept_same_codes_without_prose_or_offsets():
    schema = protocol.visible_source_closure_schema(version="6")
    diagnostic = schema["properties"]["rejections"]["items"]
    assert set(diagnostic["required"]) == {"beat_index", "related_source_ref_indexes", "source_problem"}
    assert set(diagnostic["properties"]) == set(diagnostic["required"])
    assert diagnostic["additionalProperties"] is False
    for code in diagnostic["properties"]["source_problem"]["enum"]:
        value = _wire()
        value["rejections"][0]["source_problem"] = code
        result = _parse(value)
        assert result.rejections[0].source_problem == code
        assert (result.rejections[0].char_start, result.rejections[0].char_end) == (0, len(BEAT))
        assert result.verdict.segments[0].decision == "unclosed"
        assert result.verdict.segments[0].locator.text == BEAT
        assert result.verdict.segments[0].source_ref_indexes == ()


@pytest.mark.parametrize("change", ["prose", "offset", "index", "ref", "duplicate", "missing", "closed", "old_contract"])
def test_v6_rejects_bad_diagnostics_without_granting_authority(change):
    value = _wire()
    rejection = value["rejections"][0]
    if change == "prose":
        rejection["source_problem"] = "Counterpart report supports Friday afternoon sharing prep, not today's date being Friday"
    elif change == "offset":
        rejection["char_end"] = 1
    elif change == "index":
        rejection["beat_index"] = 1
    elif change == "ref":
        rejection["related_source_ref_indexes"] = [0]
    elif change == "duplicate":
        value["rejections"].append(dict(rejection))
    elif change == "missing":
        value["rejections"] = []
    elif change == "closed":
        value["decisions"][0].update(verdict="source_free", semantic_role="private_state")
    elif change == "old_contract":
        value["contract"] = "visible-beat-source-verdict.5"
    with pytest.raises(protocol.VisibleSourceClosureWireFailure):
        _parse(value)


def test_v6_prompt_does_not_request_old_diagnostic_text_or_offsets():
    messages = protocol.visible_source_closure_messages(visible_beats=(BEAT,), world_claims=(), source_references=(), version="6")
    system = messages[0]["content"]
    assert "Unicode code point" not in system
    assert "at most 64" not in system
    assert "other_source_problem" in system
