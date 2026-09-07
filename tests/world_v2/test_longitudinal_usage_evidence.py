"""Primary usage evidence is independent of final World model-result events."""

from __future__ import annotations

import hashlib
import sqlite3

import pytest

from companion_daemon.llm import ModelCallUsage
from companion_daemon.world_v2.longitudinal_journey import read_provider_usage_evidence
from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore


def _record(store, *, status, billing_state):
    reservation = store.admit_provider_call(
        purpose="inbound_turn", actor="agent:companion", provider="deepseek",
        model="deepseek-v4-flash", prompt_characters=100, estimated_cny=0.25,
    )
    store.record(ModelCallUsage(
        purpose="inbound_turn", model="deepseek-v4-flash", provider="deepseek",
        status=status, billing_state=billing_state, latency_ms=20,
        error="caller_cancelled" if status == "failed" else "",
        budget_reservation_id=reservation,
        prompt_tokens=100 if billing_state == "known" else 0,
        cache_miss_tokens=100 if billing_state == "known" else 0,
        completion_tokens=10 if billing_state == "known" else 0,
        total_tokens=110 if billing_state == "known" else 0,
    ))
    return reservation


def test_unknown_failed_attempt_survives_later_success_without_repricing_or_mutation(tmp_path):
    path = tmp_path / "world.sqlite"
    store = WorldV2UsageStore(path=str(path))
    unknown = _record(store, status="failed", billing_state="unknown")
    _record(store, status="succeeded", billing_state="known")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    captured = read_provider_usage_evidence(path)
    assert captured["summary"] == {
        "status": "captured",
        "scope": "world_v2_model_usage_and_reservations",
        "usage_record_count": 2,
        "usage_status_counts": {"failed": 1, "succeeded": 1},
        "billing_state_counts": {"known": 1, "unknown": 1},
        "reservation_record_count": 2,
        "reservation_status_counts": {"billing_unknown": 1, "settled": 1},
        "world_event_linkage": "unverified",
    }
    assert captured["usage_records"][0]["error"] == "caller_cancelled"
    held = next(r for r in captured["reservation_records"] if r["reservation_id"] == unknown)
    assert held["status"] == "billing_unknown" and held["estimated_cny"] == 0.25
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        assert captured["usage_records"] == [dict(r) for r in db.execute(
            "SELECT * FROM world_v2_model_usage ORDER BY id"
        )]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("kind", ["missing", "empty", "corrupt", "partial_schema"])
def test_unavailable_billing_evidence_is_not_reported_as_zero_calls(tmp_path, kind):
    path = tmp_path / "world.sqlite"
    if kind == "empty":
        path.touch()
    elif kind == "corrupt":
        path.write_bytes(b"not a sqlite database")
    elif kind == "partial_schema":
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE world_v2_model_usage (id INTEGER, status TEXT)")
    before = path.read_bytes() if path.exists() else None
    captured = read_provider_usage_evidence(path)
    assert captured["summary"]["status"] == "unavailable"
    assert "usage_record_count" not in captured["summary"]
    assert "usage_records" not in captured
    assert (path.read_bytes() if path.exists() else None) == before


def test_actual_empty_usage_tables_are_distinct_from_missing_evidence(tmp_path):
    path = tmp_path / "world.sqlite"
    WorldV2UsageStore(path=str(path))
    captured = read_provider_usage_evidence(path)
    assert captured["summary"]["status"] == "captured"
    assert captured["summary"]["usage_record_count"] == 0
    assert captured["summary"]["reservation_record_count"] == 0


def test_budget_denied_record_is_distinct_from_a_provider_failure(tmp_path):
    path = tmp_path / "world.sqlite"
    store = WorldV2UsageStore(path=str(path), monthly_budget_cny=0.01)
    with pytest.raises(ValueError):
        _record(store, status="succeeded", billing_state="known")
    captured = read_provider_usage_evidence(path)
    assert captured["summary"]["usage_status_counts"] == {"budget_denied": 1}
    assert captured["summary"]["reservation_record_count"] == 0
