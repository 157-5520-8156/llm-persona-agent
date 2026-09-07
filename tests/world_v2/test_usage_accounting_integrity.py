from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import sqlite3
import threading

import pytest

from companion_daemon.llm import ModelCallUsage
from companion_daemon.world_v2.model_usage_budget import (
    BackgroundSpendCapDenied, ModelUsageAdmissionError, WorldV2UsageStore,
)


def _reserve(store, *, amount=0.7):
    return store.admit_provider_call(
        purpose="proactive_contact", actor="agent:companion", provider="deepseek",
        model="deepseek-v4-flash", prompt_characters=100, estimated_cny=amount,
    )


def _usage(reservation, **changes):
    return replace(ModelCallUsage(
        purpose="proactive_contact", model="deepseek-v4-flash", status="failed",
        latency_ms=10, provider="deepseek", budget_reservation_id=reservation,
        billing_state="unknown",
    ), **changes)


def _rows(path, query):
    with sqlite3.connect(path) as connection:
        return connection.execute(query).fetchall()


def test_concurrent_stores_cannot_spend_the_same_balance(tmp_path):
    path = tmp_path / "usage.sqlite"
    stores = [WorldV2UsageStore(path=str(path), monthly_budget_cny=1) for _ in range(8)]
    ready = threading.Barrier(len(stores))

    def admit(store):
        ready.wait(timeout=5)
        try:
            return _reserve(store)
        except BackgroundSpendCapDenied:
            return None

    with ThreadPoolExecutor(max_workers=len(stores)) as pool:
        admitted = list(pool.map(admit, stores))
    assert sum(value is not None for value in admitted) == 1


def test_usage_write_failure_does_not_release_reservation(tmp_path):
    path = tmp_path / "usage.sqlite"
    store = WorldV2UsageStore(path=str(path))
    token = _reserve(store)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TRIGGER fail_usage BEFORE INSERT ON world_v2_model_usage "
                           "BEGIN SELECT RAISE(ABORT, 'storage cut'); END")
    store.record(_usage(token, billing_state="known", prompt_tokens=100))
    assert _rows(path, "SELECT status FROM world_v2_model_reservations") == [("pending",)]
    assert _rows(path, "SELECT COUNT(*) FROM world_v2_model_usage") == [(0,)]
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER fail_usage")
    WorldV2UsageStore(path=str(path)).record(_usage(token, billing_state="known", prompt_tokens=100))
    assert _rows(path, "SELECT status FROM world_v2_model_reservations") == [("settled",)]


def test_unknown_bill_survives_restart_until_real_usage_arrives(tmp_path):
    path = tmp_path / "usage.sqlite"
    store = WorldV2UsageStore(path=str(path), monthly_budget_cny=1)
    token = _reserve(store)
    store.record(_usage(token))
    restarted = WorldV2UsageStore(path=str(path), monthly_budget_cny=1)
    with pytest.raises(BackgroundSpendCapDenied):
        _reserve(restarted)
    assert _rows(path, "SELECT billing_state FROM world_v2_model_usage WHERE status != 'budget_denied'") == [("unknown",)]
    # A provider reconciliation completes the SAME bill, not a second charge.
    final = _usage(token, billing_state="known", prompt_tokens=100)
    restarted.record(final)
    restarted.record(final)
    assert _rows(path, "SELECT COUNT(*) FROM world_v2_model_usage WHERE status != 'budget_denied'") == [(1,)]
    assert _reserve(restarted)


def test_partial_unknown_usage_is_not_added_to_the_whole_reserve_twice(tmp_path):
    path = tmp_path / "usage.sqlite"
    store = WorldV2UsageStore(path=str(path), monthly_budget_cny=1)
    token = _reserve(store)
    store.record(_usage(token, prompt_tokens=100_000))
    since = datetime.now(timezone.utc) - timedelta(days=1)
    assert store._combined_spend_cny(since=since) == pytest.approx(0.7)
    assert _reserve(store, amount=0.29)
    with pytest.raises(BackgroundSpendCapDenied):
        _reserve(store, amount=0.02)


def test_confirmed_not_billed_releases_the_reservation(tmp_path):
    path = tmp_path / "usage.sqlite"
    store = WorldV2UsageStore(path=str(path), monthly_budget_cny=1)
    token = _reserve(store)
    store.record(_usage(token, billing_state="not_billed"))
    assert _reserve(store)


def test_unresolved_call_is_not_forgotten_on_calendar_rollover(tmp_path):
    path = tmp_path / "usage.sqlite"
    store = WorldV2UsageStore(path=str(path), daily_budget_cny=1)
    token = _reserve(store)
    store.record(_usage(token))
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE world_v2_model_reservations SET created_at=?", (old,))
        connection.execute("UPDATE world_v2_model_usage SET recorded_at=?", (old,))
    with pytest.raises(BackgroundSpendCapDenied):
        _reserve(store)


@pytest.mark.parametrize("amount", [float("nan"), float("inf"), -1])
def test_nonfinite_or_negative_estimate_cannot_bypass_budget(tmp_path, amount):
    store = WorldV2UsageStore(path=str(tmp_path / "usage.sqlite"), monthly_budget_cny=1)
    with pytest.raises(ModelUsageAdmissionError):
        _reserve(store, amount=amount)


def test_health_counts_images_and_open_bills_without_mirror_double_count(tmp_path):
    from companion_daemon.db import UsageEventsLedger
    path = tmp_path / "usage.sqlite"
    store = WorldV2UsageStore(path=str(path), monthly_budget_cny=1)
    UsageEventsLedger(path).record_usage("image_generation", 0.2)
    token = _reserve(store)
    store.record(_usage(token))
    store.record(_usage("", purpose="image_generation", billing_state="known", prompt_tokens=1000))
    state = store.budget_state()
    assert state["monthly_cost_cny"] == pytest.approx(0.2)
    assert state["monthly_committed_cny"] == pytest.approx(0.9)
    assert state["unknown_cost_hold_cny"] == pytest.approx(0.7)
    assert state["cost_forecast"]["target_monthly_cny"] == 100
    assert state["cost_forecast"]["projection_status"] == "insufficient_history"
    assert state["unresolved_billing_count"] == 1
    assert "provider_billing_unknown" in state["warning_reasons"]
    assert state["monthly_purpose_cost_cny"]["image_generation"] == pytest.approx(0.2)


def test_health_does_not_treat_budget_denials_as_model_calls(tmp_path):
    store = WorldV2UsageStore(path=str(tmp_path / "usage.sqlite"), monthly_budget_cny=0.1)
    for _ in range(4):
        with pytest.raises(BackgroundSpendCapDenied):
            _reserve(store)
    assert store.budget_state()["purpose_counts"] == {}
    assert store.budget_state()["calls_per_user_message_alert"] is False


def test_unpriced_currency_is_a_coverage_warning_not_a_free_call(tmp_path):
    from companion_daemon.db import UsageEventsLedger
    path = tmp_path / "usage.sqlite"
    store = WorldV2UsageStore(path=str(path))
    UsageEventsLedger(path).record_usage("civitai_buzz", 0, note="currency evidence")
    state = store.budget_state()
    assert state["unpriced_external_usage_count"] == 1
    assert "external_usage_unpriced" in state["warning_reasons"]
    assert state["instance_cost_qualification"] == "incomplete"


def test_monthly_forecast_is_connected_to_the_same_accounting_snapshot(tmp_path, monkeypatch):
    from companion_daemon.db import UsageEventsLedger
    from companion_daemon.world_v2 import model_usage_budget
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 16, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(model_usage_budget, "datetime", Clock)
    path = tmp_path / "usage.sqlite"
    store = WorldV2UsageStore(path=str(path))
    UsageEventsLedger(path).record_usage("image_generation", 40)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE usage_events SET created_at='2026-09-01T00:00:00+00:00'")
    _reserve(store, amount=5)
    state = store.budget_state()
    assert state["cost_forecast"]["month_end_projected_cny"] == pytest.approx(85)
    assert state["cost_forecast"]["forecast_pressure_threshold_percent"] == 80
    assert "monthly_cost_forecast_pressure" in state["warning_reasons"]
    assert state["monthly_committed_cny"] == 45


def test_embedding_day_book_is_included_in_total_admission(tmp_path):
    path = tmp_path / "usage.sqlite"
    store = WorldV2UsageStore(path=str(path), monthly_budget_cny=1)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE world_recall_embedding_usage_daily "
                           "(usage_day TEXT, estimated_cost_cny REAL)")
        connection.execute("INSERT INTO world_recall_embedding_usage_daily VALUES (?, ?)",
                           (datetime.now(timezone.utc).date().isoformat(), 0.5))
    with pytest.raises(BackgroundSpendCapDenied):
        _reserve(store)
    state = store.budget_state()
    assert state["monthly_committed_cny"] == pytest.approx(0.5)
    assert state["monthly_purpose_cost_cny"]["recall_embedding"] == pytest.approx(0.5)
    assert state["recent_embedding_cost_upper_bound_cny"] == pytest.approx(0.5)


def _image_reserve(store):
    return store.admit_provider_call(
        purpose="image_generation", actor="agent:companion", provider="openai",
        model="gpt-image-2", prompt_characters=100, estimated_cny=0.7,
    )


def test_external_bill_and_reservation_settle_once_in_one_transaction(tmp_path):
    path = tmp_path / "usage.sqlite"
    store = WorldV2UsageStore(path=str(path), monthly_budget_cny=1)
    token = _image_reserve(store)
    kwargs = dict(reservation_id=token, kind="image_generation", estimated_cny=0.5, billing_state="known")
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TRIGGER fail_external BEFORE UPDATE ON world_v2_model_reservations "
                           "BEGIN SELECT RAISE(ABORT, 'storage cut'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.record_external_usage(**kwargs)
    assert _rows(path, "SELECT COUNT(*) FROM usage_events") == [(0,)]
    assert _rows(path, "SELECT status FROM world_v2_model_reservations") == [("pending",)]
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER fail_external")
    restarted = WorldV2UsageStore(path=str(path), monthly_budget_cny=1)
    restarted.record_external_usage(**kwargs)
    restarted.record_external_usage(**kwargs)
    assert _rows(path, "SELECT COUNT(*) FROM usage_events") == [(1,)]
    assert restarted.budget_state()["monthly_committed_cny"] == 0.5
    with pytest.raises(BackgroundSpendCapDenied):
        _reserve(restarted)


def test_external_unknown_bill_can_be_reconciled_but_not_rebound(tmp_path):
    path = tmp_path / "usage.sqlite"
    store = WorldV2UsageStore(path=str(path), monthly_budget_cny=1)
    token = _image_reserve(store)
    store.record_external_usage(reservation_id=token, kind="image_generation",
                                estimated_cny=None, billing_state="unknown")
    assert store.budget_state()["unknown_cost_hold_cny"] == 0.7
    assert _rows(path, "SELECT COUNT(*) FROM usage_events") == [(0,)]
    with pytest.raises(ModelUsageAdmissionError):
        store.record_external_usage(reservation_id=token, kind="other",
                                    estimated_cny=0.1, billing_state="known")
    store.record_external_usage(reservation_id=token, kind="image_generation",
                                estimated_cny=0.5, billing_state="known")
    with pytest.raises(ModelUsageAdmissionError):
        store.record_external_usage(reservation_id=token, kind="image_generation",
                                    estimated_cny=0.1, billing_state="known")
    assert store.budget_state()["monthly_committed_cny"] == 0.5
