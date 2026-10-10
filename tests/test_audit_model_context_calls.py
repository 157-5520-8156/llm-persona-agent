import hashlib
import json

import pytest

from audit_model_context_calls import audit


def inputs(tmp_path):
    captures = tmp_path / "captures.jsonl"
    usage = tmp_path / "usage.json"
    rows = []
    for reservation in ("chat", "life", "missing"):
        raw = json.dumps({"model": "fixture", "messages": [{"role": "user", "content": "中文"}]})
        rows.append({"kind": "request", "usage_reservation_id": reservation,
                     "model_content_json": raw, "content_hash": hashlib.sha256(raw.encode()).hexdigest()})
    captures.write_text("\n".join(json.dumps(row) for row in rows))
    usage.write_text(json.dumps([
        {"reservation_id": "chat", "purpose": "inbound_turn", "billing_state": "known",
         "cost_cny": "0.02", "status": "succeeded", "prompt_tokens": 12,
         "completion_tokens": 3, "cache_hit_tokens": 8, "cache_miss_tokens": 4},
        {"reservation_id": "life", "purpose": "life_development_draft", "billing_state": "unknown",
         "status": "failed"},
    ]))
    return captures, usage


def test_all_purposes_failures_and_unknown_billing_remain_visible(tmp_path):
    result = audit(*inputs(tmp_path))
    assert result["captured_requests"] == 3
    assert result["matched_bills"] == 2
    assert result["missing_usage_requests"] == 1
    assert result["known_cost_cny"] == "0.02"
    life = next(row for row in result["purposes"] if row["purpose"] == "life_development_draft")
    assert life["statuses"] == {"failed": 1}
    assert life["unpriced_usage"] == 1
    assert life["missing_token_fields"]["prompt_tokens"] == 1


@pytest.mark.parametrize("fault", ["hash", "duplicate_bill", "duplicate_request"])
def test_bad_hash_or_ambiguous_billing_join_rejected(tmp_path, fault):
    captures, usage = inputs(tmp_path)
    if fault == "duplicate_bill":
        bills = json.loads(usage.read_text())
        usage.write_text(json.dumps([*bills, bills[0]]))
    else:
        rows = [json.loads(line) for line in captures.read_text().splitlines()]
        if fault == "hash":
            rows[0]["content_hash"] = "0" * 64
        else:
            rows.append(rows[0])
        captures.write_text("\n".join(json.dumps(row) for row in rows))
    with pytest.raises(ValueError):
        audit(captures, usage)
