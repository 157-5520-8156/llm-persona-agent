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
