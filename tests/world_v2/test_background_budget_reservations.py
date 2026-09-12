from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import multiprocessing
import sqlite3
import threading

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel, ModelCallUsage, model_call_scope
from companion_daemon.world_v2 import model_usage_budget
from companion_daemon.world_v2.model_usage_budget import (
    BackgroundSpendCapDenied,
    VISIBLE_INBOUND_PURPOSES,
    WorldV2UsageStore,
)


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 13, 10, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(model_usage_budget, "datetime", Clock)


def _store(path) -> WorldV2UsageStore:
    return WorldV2UsageStore(
        path=str(path),
        monthly_budget_cny=100.0,
        daily_budget_cny=8.0,
        soft_daily_budget_cny=6.0,
        background_daily_budget_cny=1.5,
    )


def _admit(
    store: WorldV2UsageStore,
    *,
    amount: float = 0.9,
    purpose: str = "life_development_draft",
) -> str:
    return store.admit_provider_call(
        purpose=purpose,
        actor="agent:companion",
        provider="deepseek",
        model="deepseek-v4-flash",
        prompt_characters=100,
        estimated_cny=amount,
    )


def _usage(token, *, billing_state="unknown", prompt_tokens=0, purpose="life_development_draft"):
    return ModelCallUsage(
        purpose=purpose,
        model="deepseek-v4-flash",
        status="succeeded" if billing_state == "known" else "failed",
        latency_ms=1,
        provider="deepseek",
        budget_reservation_id=token,
        billing_state=billing_state,
        prompt_tokens=prompt_tokens,
        total_tokens=prompt_tokens,
    )


def test_background_cap_counts_pending_reservations_across_stores(tmp_path) -> None:
    path = tmp_path / "usage.sqlite"
    first, second = _store(path), _store(path)

    _admit(first)
    with pytest.raises(BackgroundSpendCapDenied, match="background_daily_budget_exceeded"):
        _admit(second)

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*), SUM(estimated_cny) FROM world_v2_model_reservations"
        ).fetchone() == (1, 0.9)
        assert connection.execute("SELECT status, error FROM world_v2_model_usage").fetchall() == [
            ("budget_denied", "background_daily_budget_exceeded")
        ]


def test_concurrent_background_stores_share_one_atomic_balance(tmp_path) -> None:
    path = tmp_path / "usage.sqlite"
    stores = [_store(path) for _ in range(6)]
    ready = threading.Barrier(len(stores))

    def admit(store):
        ready.wait(timeout=10)
        try:
            return _admit(store)
        except BackgroundSpendCapDenied as exc:
            assert exc.reason == "background_daily_budget_exceeded"
            return None

    with ThreadPoolExecutor(max_workers=len(stores)) as executor:
        results = list(executor.map(admit, stores))
    assert sum(result is not None for result in results) == 1


def _admit_from_process(path, ready, result):
    store = _store(path)
    ready.wait(timeout=15)
    try:
        _admit(store)
        result.put("admitted")
    except BackgroundSpendCapDenied as exc:
        result.put(exc.reason)


def test_background_balance_is_atomic_between_processes(tmp_path) -> None:
    path = tmp_path / "usage.sqlite"
    _store(path)
    context = multiprocessing.get_context("spawn")
    ready, result = context.Barrier(3), context.Queue()
    processes = [
        context.Process(target=_admit_from_process, args=(path, ready, result)) for _ in range(3)
    ]
    try:
        for process in processes:
            process.start()
        outcomes = [result.get(timeout=30) for _ in processes]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=10)
        result.close()
    assert sorted(outcomes) == [
        "admitted",
        "background_daily_budget_exceeded",
        "background_daily_budget_exceeded",
    ]


@pytest.mark.parametrize("state", ["pending", "unknown"])
def test_unresolved_background_bill_survives_restart_and_calendar_rollover(tmp_path, state):
    path = tmp_path / "usage.sqlite"
    store = _store(path)
    token = _admit(store)
    if state == "unknown":
        store.record(_usage(token, prompt_tokens=100))
    old = (model_usage_budget.datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE world_v2_model_reservations SET created_at=?", (old,))
        connection.execute("UPDATE world_v2_model_usage SET recorded_at=?", (old,))
    restarted = _store(path)
    with pytest.raises(BackgroundSpendCapDenied, match="background_daily_budget_exceeded"):
        _admit(restarted)
    assert restarted.background_daily_cost_cny() == 0
    assert restarted.background_daily_committed_cny() == pytest.approx(0.9)


def test_partial_unknown_and_known_settlement_are_not_double_counted(tmp_path):
    path = tmp_path / "usage.sqlite"
    store = _store(path)
    token = _admit(store)
    # An unrounded token price makes subtracting the rounded database cost
    # observably wrong; the hold must subtract the same repriced lower bound.
    store.record(_usage(token, prompt_tokens=113))
    assert store.background_daily_cost_cny() > 0
    assert store.background_daily_committed_cny() == pytest.approx(0.9, abs=1e-12)
    with pytest.raises(BackgroundSpendCapDenied, match="background_daily_budget_exceeded"):
        _admit(_store(path))
    final = _usage(token, billing_state="known", prompt_tokens=113)
    store.record(final)
    store.record(final)
    assert store.background_daily_committed_cny() == store.background_daily_cost_cny()
    assert _admit(_store(path))
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM world_v2_model_usage WHERE reservation_id=?", (token,)
            ).fetchone()[0]
            == 1
        )


def test_not_billed_background_call_releases_capacity(tmp_path):
    store = _store(tmp_path / "usage.sqlite")
    token = _admit(store, amount=1.5)
    store.record(_usage(token, billing_state="not_billed"))
    assert store.background_daily_committed_cny() == 0
    assert _admit(store, amount=1.5)


@pytest.mark.parametrize("purpose", sorted(VISIBLE_INBOUND_PURPOSES))
def test_visible_purposes_neither_consume_nor_obey_background_capacity(tmp_path, purpose):
    store = _store(tmp_path / "usage.sqlite")
    token = _admit(store, amount=1.5, purpose=purpose)
    assert store.background_daily_committed_cny() == 0
    _admit(store, amount=1.5)
    assert _admit(store, amount=0.1, purpose=purpose)
    store.record(_usage(token, purpose=purpose, prompt_tokens=100, billing_state="unknown"))
    assert store.background_daily_committed_cny() == pytest.approx(1.5)


@pytest.mark.parametrize(
    "limit,reason",
    [
        ({"monthly_budget_cny": 1}, "monthly_budget_exceeded"),
        ({"daily_budget_cny": 1}, "daily_budget_exceeded"),
    ],
)
def test_visible_purposes_still_obey_explicit_hard_caps(tmp_path, limit, reason):
    store = WorldV2UsageStore(
        path=str(tmp_path / "usage.sqlite"), background_daily_budget_cny=0.1, **limit
    )
    _admit(store, purpose="inbound_turn")
    with pytest.raises(BackgroundSpendCapDenied, match=reason):
        _admit(store, purpose="inbound_turn")


def test_capacity_health_keeps_recorded_cost_separate_from_holds(tmp_path):
    store = _store(tmp_path / "usage.sqlite")
    token = _admit(store, amount=0.9)
    store.record(_usage(token))
    _admit(store, amount=0.6)
    state = store.budget_state()
    assert state["background_daily_cost_cny"] == 0
    assert state["background_daily_committed_cny"] == 1.5
    assert state["background_pending_cost_cny"] == 0.6
    assert state["background_unknown_cost_hold_cny"] == 0.9
    assert state["background_daily_exhausted"] is True


def test_scheduler_capacity_probe_includes_pending_and_unknown(tmp_path):
    from companion_daemon.world_v2.qq_c2c_host import _background_budget_paused

    path = tmp_path / "usage.sqlite"
    store = _store(path)
    paused = _background_budget_paused(_store(path))
    assert paused() is False
    token = _admit(store, amount=1.5)
    assert store.background_daily_cost_cny() == 0
    assert paused() is True
    store.record(_usage(token))
    assert paused() is True
    store.record(_usage(token, billing_state="not_billed"))
    assert paused() is False


def test_external_background_bill_retains_capacity_after_settlement(tmp_path):
    path = tmp_path / "usage.sqlite"
    store = _store(path)
    token = _admit(store, amount=0.9, purpose="image_generation")
    assert store.background_daily_committed_cny() == pytest.approx(0.9)
    bill = dict(
        reservation_id=token, kind="image_generation", estimated_cny=0.8, billing_state="known"
    )
    store.record_external_usage(**bill)
    store.record_external_usage(**bill)
    # Image token telemetry is a mirror. usage_events owns its actual CNY.
    store.record(_usage("", purpose="image_generation", billing_state="known", prompt_tokens=100))
    assert store.background_daily_cost_cny() == 0
    assert store.background_daily_committed_cny() == pytest.approx(0.8)
    assert store.budget_state()["background_external_cost_cny"] == pytest.approx(0.8)
    with pytest.raises(BackgroundSpendCapDenied, match="background_daily_budget_exceeded"):
        _admit(_store(path))


def test_unknown_external_background_bill_uses_larger_reported_hold(tmp_path):
    store = _store(tmp_path / "usage.sqlite")
    token = _admit(store, purpose="image_generation")
    store.record_external_usage(
        reservation_id=token, kind="image_generation", estimated_cny=1.0, billing_state="unknown"
    )
    assert store.background_daily_committed_cny() == 1.0
    with pytest.raises(BackgroundSpendCapDenied, match="background_daily_budget_exceeded"):
        _admit(store)


def test_partial_unknown_above_reserve_survives_calendar_rollover(tmp_path, monkeypatch):
    path = tmp_path / "usage.sqlite"
    store = _store(path)
    token = _admit(store, amount=0.1)
    store.record(_usage(token, prompt_tokens=1_000_000))
    lower_bound = store.background_daily_committed_cny()
    assert lower_bound > 0.1

    class LaterClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 23, 10, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(model_usage_budget, "datetime", LaterClock)
    restarted = _store(path)
    assert restarted.background_daily_cost_cny() == 0
    assert restarted.background_daily_committed_cny() == lower_bound


def test_failed_usage_write_keeps_background_reservation_occupied(tmp_path):
    path = tmp_path / "usage.sqlite"
    store = _store(path)
    token = _admit(store)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_usage BEFORE INSERT ON world_v2_model_usage "
            "BEGIN SELECT RAISE(ABORT, 'storage failure'); END"
        )
    store.record(_usage(token, billing_state="not_billed"))
    assert _store(path).background_daily_committed_cny() == pytest.approx(0.9)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER fail_usage")
    with pytest.raises(BackgroundSpendCapDenied, match="background_daily_budget_exceeded"):
        _admit(_store(path))


def test_unreserved_external_spend_keeps_existing_outer_budget_scope(tmp_path):
    from companion_daemon.db import UsageEventsLedger

    path = tmp_path / "usage.sqlite"
    store = _store(path)
    UsageEventsLedger(path).record_usage("image_generation", 7)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE usage_events SET created_at=?",
            (model_usage_budget.datetime.now(timezone.utc).isoformat(),),
        )
    assert store.background_daily_committed_cny() == 0
    with pytest.raises(BackgroundSpendCapDenied, match="soft_daily_budget_exceeded"):
        _admit(store)
    assert _admit(store, purpose="inbound_turn")
    with pytest.raises(BackgroundSpendCapDenied, match="daily_budget_exceeded"):
        _admit(store, purpose="inbound_turn")


@pytest.mark.asyncio
async def test_background_reservation_denies_before_http_but_visible_call_can_send(tmp_path):
    path = tmp_path / "usage.sqlite"
    store = _store(path)
    _admit(store, amount=1.5)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 1},
            },
        )

    model = DeepSeekChatModel(
        "test-key",
        "https://api.deepseek.com",
        "deepseek-v4-flash",
        transport=httpx.MockTransport(handler),
        usage_observer=_store(path).record,
    )
    with model_call_scope("life_development_draft", actor="agent:companion"):
        with pytest.raises(BackgroundSpendCapDenied, match="background_daily_budget_exceeded"):
            await model.complete([{"role": "user", "content": "bounded test"}])
    assert requests == []
    with model_call_scope("inbound_turn", actor="agent:companion"):
        assert await model.complete([{"role": "user", "content": "bounded test"}]) == "ok"
    assert len(requests) == 1
    assert store.background_daily_committed_cny() == 1.5
