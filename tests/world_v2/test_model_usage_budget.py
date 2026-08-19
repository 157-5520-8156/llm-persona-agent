from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

import companion_daemon.app as app_module
from companion_daemon.config import Settings
from companion_daemon.llm import DeepSeekChatModel, model_call_scope
from companion_daemon.world_v2.model_usage_budget import (
    GENERIC_MODEL_PURPOSES,
    BackgroundSpendCapDenied,
    ModelUsageAdmissionError,
    VISIBLE_INBOUND_PURPOSES,
    WorldV2UsageStore,
)
from companion_daemon.usage_metrics import estimate_model_cost


class _Usage:
    def __init__(
        self,
        *,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cache_hit_tokens: int = 0,
        cache_miss_tokens: int = 0,
        purpose: str = "inbound_turn",
        status: str = "succeeded",
        provider: str = "deepseek",
        actor: str = "agent:companion",
        attempt: int = 1,
        reservation_id: str = "",
    ) -> None:
        self.model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.cache_hit_tokens = cache_hit_tokens
        self.cache_miss_tokens = cache_miss_tokens
        self.total_tokens = prompt_tokens + completion_tokens
        self.status = status
        self.purpose = purpose
        self.world_id = "world:test"
        self.turn_id = "turn:test"
        self.provider = provider
        self.actor = actor
        self.attempt = attempt
        self.budget_reservation_id = reservation_id
        self.error = ""
        self.latency_ms = 10


def test_usage_store_records_and_aggregates_cost(tmp_path) -> None:
    store = WorldV2UsageStore(path=str(tmp_path / "usage.sqlite"))
    store.record(
        _Usage(model="deepseek-v4-flash", prompt_tokens=1_000_000, completion_tokens=0)
    )
    connection = sqlite3.connect(tmp_path / "usage.sqlite")
    try:
        recorded_at, cost_cny, version, account = connection.execute(
            "SELECT recorded_at, cost_cny, pricing_version, spend_account "
            "FROM world_v2_model_usage"
        ).fetchone()
    finally:
        connection.close()
    expected = estimate_model_cost(
        model="deepseek-v4-flash",
        prompt_tokens=1_000_000,
        completion_tokens=0,
        cache_hit_tokens=0,
        cache_miss_tokens=1_000_000,
        at=recorded_at,
    )
    monthly = store.monthly_cost_cny()
    daily = store.daily_cost_cny()
    assert monthly == pytest.approx(expected.cny, abs=0.01)
    assert daily == pytest.approx(expected.cny, abs=0.01)
    assert cost_cny == pytest.approx(expected.cny, abs=0.01)
    assert version.startswith("deepseek-2026-08-17-")
    assert account == "debug"


def test_usage_store_records_failed_calls_without_raising(tmp_path) -> None:
    store = WorldV2UsageStore(path=str(tmp_path / "usage.sqlite"))
    # record() must never raise on malformed observer payloads.
    store.record(object())
    store.record(None)
    assert store.monthly_cost_cny() == 0.0


def test_usage_store_budget_state_reports_exhaustion(tmp_path) -> None:
    store = WorldV2UsageStore(path=str(tmp_path / "usage.sqlite"))
    store.record(
        _Usage(model="deepseek-v4-flash", prompt_tokens=10_000_000, completion_tokens=0)
    )
    state = store.budget_state(monthly_budget_cny=1.0, daily_budget_cny=1.0)
    assert state["monthly_exhausted"] is True
    assert state["daily_exhausted"] is True
    assert state["monthly_cost_cny"] >= 1.0


def test_usage_store_missing_budget_never_exhausts(tmp_path) -> None:
    store = WorldV2UsageStore(path=str(tmp_path / "usage.sqlite"))
    state = store.budget_state(monthly_budget_cny=None, daily_budget_cny=None)
    assert state["monthly_exhausted"] is False
    assert state["daily_exhausted"] is False


def test_generic_purposes_cannot_be_reserved(tmp_path) -> None:
    store = WorldV2UsageStore(path=str(tmp_path / "usage.sqlite"))
    assert "world_v2_character_interior" in GENERIC_MODEL_PURPOSES
    assert "unclassified" in GENERIC_MODEL_PURPOSES
    for purpose in ("", "unclassified", "world_v2_character_interior"):
        with pytest.raises(ModelUsageAdmissionError):
            store.admit_provider_call(
                purpose=purpose,
                actor="agent:companion",
                provider="deepseek",
                model="deepseek-v4-flash",
                prompt_characters=100,
            )


def test_specific_purpose_is_reserved_before_the_call_and_recorded(tmp_path) -> None:
    store = WorldV2UsageStore(path=str(tmp_path / "usage.sqlite"))
    reservation_id = store.admit_provider_call(
        purpose="inbound_turn",
        actor="agent:companion",
        provider="deepseek",
        model="deepseek-v4-flash",
        prompt_characters=1_000,
    )
    assert reservation_id
    store.record(
        _Usage(
            model="deepseek-v4-flash",
            prompt_tokens=1_000,
            completion_tokens=40,
            purpose="inbound_turn",
            reservation_id=reservation_id,
        )
    )
    state = store.budget_state(monthly_budget_cny=100.0, daily_budget_cny=10.0)
    assert state["purpose_counts"] == {"inbound_turn": 1}
    assert "world_v2_character_interior" not in state["purpose_counts"]


@pytest.mark.asyncio
async def test_usage_observer_refuses_unreserved_provider_call(tmp_path) -> None:
    requested: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "should not run"}}]}
        )

    store = WorldV2UsageStore(path=str(tmp_path / "usage.sqlite"))
    model = DeepSeekChatModel(
        "key",
        "https://api.deepseek.com",
        "deepseek-v4-flash",
        transport=httpx.MockTransport(handler),
        usage_observer=store.record,
    )
    with pytest.raises(ModelUsageAdmissionError):
        await model.complete([{"role": "user", "content": "hi"}])
    assert requested == []
    assert store.monthly_cost_cny() == 0.0


@pytest.mark.asyncio
async def test_attributed_scope_lets_the_provider_call_proceed(tmp_path) -> None:
    store = WorldV2UsageStore(path=str(tmp_path / "usage.sqlite"))
    model = DeepSeekChatModel(
        "key",
        "https://api.deepseek.com",
        "deepseek-v4-flash",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 8,
                        "prompt_cache_hit_tokens": 60,
                        "prompt_cache_miss_tokens": 40,
                    },
                },
            )
        ),
        usage_observer=store.record,
    )
    with model_call_scope("inbound_turn", actor="agent:companion"):
        assert await model.complete([{"role": "user", "content": "hi"}]) == "ok"
    state = store.budget_state(monthly_budget_cny=100.0, daily_budget_cny=10.0)
    assert state["purpose_counts"] == {"inbound_turn": 1}


def test_health_alerts_when_each_user_message_costs_more_than_three_calls(
    tmp_path,
) -> None:
    path = tmp_path / "usage.sqlite"
    store = WorldV2UsageStore(path=str(path))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE world_v2_events (event_json TEXT NOT NULL)")
        connection.execute(
            "INSERT INTO world_v2_events (event_json) VALUES (?)",
            (
                json.dumps(
                    {
                        "event_type": "ObservationRecorded",
                        "created_at": f"{today}T08:00:00+00:00",
                    }
                ),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    for index in range(4):
        store.record(
            _Usage(
                model="deepseek-v4-flash",
                prompt_tokens=100,
                completion_tokens=10,
                purpose="private_impression_reflection",
                reservation_id=f"res:{index}",
            )
        )
    state = store.budget_state(monthly_budget_cny=100.0, daily_budget_cny=10.0)
    assert state["calls_per_user_message"] == 4.0
    assert state["calls_per_user_message_alert"] is True
    assert "calls_per_user_message" in state["warning_reasons"]


def test_health_alerts_on_invalid_cost_rate_and_reports_cache_hit(tmp_path) -> None:
    store = WorldV2UsageStore(path=str(tmp_path / "usage.sqlite"))
    store.record(
        _Usage(
            model="deepseek-v4-flash",
            prompt_tokens=1_000_000,
            completion_tokens=0,
            cache_hit_tokens=0,
            cache_miss_tokens=1_000_000,
            purpose="inbound_turn",
        )
    )
    store.record(
        _Usage(
            model="deepseek-v4-flash",
            prompt_tokens=1_000_000,
            completion_tokens=0,
            cache_hit_tokens=0,
            cache_miss_tokens=1_000_000,
            purpose="validation_reselection",
        )
    )
    state = store.budget_state(monthly_budget_cny=100.0, daily_budget_cny=10.0)
    assert state["cache_hit_rate"] == pytest.approx(0.0)
    assert state["cache_hit_rate_alert"] is True
    assert state["invalid_cost_rate"] == pytest.approx(0.5, abs=0.01)
    assert state["invalid_cost_rate_alert"] is True
    assert "invalid_cost_rate" in state["warning_reasons"]
    assert "cache_hit_rate" in state["warning_reasons"]


def test_health_reports_cny_per_delivered_message(tmp_path) -> None:
    path = tmp_path / "usage.sqlite"
    store = WorldV2UsageStore(path=str(path))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE world_v2_events (event_json TEXT NOT NULL)")
        for index in range(2):
            connection.execute(
                "INSERT INTO world_v2_events (event_json) VALUES (?)",
                (
                    json.dumps(
                        {
                            "event_type": "ActionDelivered",
                            "created_at": f"{today}T08:0{index}:00+00:00",
                        }
                    ),
                ),
            )
        connection.commit()
    finally:
        connection.close()
    store.record(
        _Usage(model="deepseek-v4-flash", prompt_tokens=1_000_000, completion_tokens=0)
    )
    connection = sqlite3.connect(path)
    try:
        cost = float(
            connection.execute("SELECT cost_cny FROM world_v2_model_usage").fetchone()[0]
        )
    finally:
        connection.close()
    state = store.budget_state(monthly_budget_cny=100.0, daily_budget_cny=10.0)
    assert state["cny_per_delivered_message"] == pytest.approx(cost / 2, abs=0.01)


def test_http_health_exposes_model_usage_attribution(tmp_path) -> None:
    configured = app_module.create_http_asgi_app(
        settings=Settings(
            _env_file=None,
            database_path=tmp_path / "http-usage-health.sqlite",
        )
    )

    class _Capture:
        def character_interior_health(self) -> dict[str, object]:
            return {"status": "unavailable", "installed": False}

        def proactive_source_authority_health(self) -> dict[str, object]:
            return {"status": "ready"}

        async def aclose(self) -> None:
            return None

    configured.state.http_v2_capture = _Capture()
    with TestClient(configured) as client:
        body = client.get("/health").json()
    usage = body["model_usage"]
    assert "calls_per_user_message" in usage
    assert "cache_hit_rate" in usage
    assert "invalid_cost_rate" in usage
    assert "purpose_counts" in usage


def test_usage_store_creates_usage_events_without_legacy_schema(tmp_path) -> None:
    path = tmp_path / "world-v2-ledger.sqlite"
    WorldV2UsageStore(path=str(path))
    connection = sqlite3.connect(path)
    try:
        tables = {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        connection.close()
    assert "usage_events" in tables
    assert "world_v2_model_usage" in tables
    assert "users" not in tables
    assert "mood_state" not in tables


def test_budget_state_reports_soft_daily_exhaustion(tmp_path) -> None:
    store = WorldV2UsageStore(
        path=str(tmp_path / "usage.sqlite"),
        monthly_budget_cny=100.0,
        daily_budget_cny=4.0,
        soft_daily_budget_cny=0.5,
    )
    store.record(
        _Usage(model="deepseek-v4-flash", prompt_tokens=10_000_000, completion_tokens=0)
    )
    state = store.budget_state()
    assert state["soft_daily_exhausted"] is True
    assert state["daily_exhausted"] is True
    assert "soft_daily_exhausted" in state["warning_reasons"]


def test_background_purpose_is_denied_before_the_provider_call(tmp_path) -> None:
    store = WorldV2UsageStore(
        path=str(tmp_path / "usage.sqlite"),
        monthly_budget_cny=100.0,
        daily_budget_cny=20.0,
        soft_daily_budget_cny=0.01,
    )
    store.record(
        _Usage(
            model="deepseek-v4-flash",
            prompt_tokens=10_000_000,
            completion_tokens=0,
            purpose="private_impression_reflection",
        )
    )
    with pytest.raises(BackgroundSpendCapDenied, match="soft_daily_budget_exceeded"):
        store.admit_provider_call(
            purpose="private_impression_reflection",
            actor="agent:companion",
            provider="deepseek",
            model="deepseek-v4-flash",
            prompt_characters=100,
        )
    connection = sqlite3.connect(tmp_path / "usage.sqlite")
    try:
        row = connection.execute(
            "SELECT status, error FROM world_v2_model_usage WHERE status = 'budget_denied'"
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    assert row[0] == "budget_denied"
    assert row[1] == "soft_daily_budget_exceeded"


def test_inbound_purpose_is_never_blocked_by_cny_envelope(tmp_path) -> None:
    store = WorldV2UsageStore(
        path=str(tmp_path / "usage.sqlite"),
        monthly_budget_cny=0.01,
        daily_budget_cny=0.01,
        soft_daily_budget_cny=0.01,
    )
    store.record(
        _Usage(model="deepseek-v4-flash", prompt_tokens=10_000_000, completion_tokens=0)
    )
    assert "inbound_turn" in VISIBLE_INBOUND_PURPOSES
    reservation = store.admit_provider_call(
        purpose="inbound_turn",
        actor="agent:companion",
        provider="deepseek",
        model="deepseek-v4-flash",
        prompt_characters=100,
    )
    assert reservation


@pytest.mark.asyncio
async def test_background_cny_cap_does_not_emit_http(tmp_path) -> None:
    requested: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "should not run"}}]}
        )

    store = WorldV2UsageStore(
        path=str(tmp_path / "usage.sqlite"),
        monthly_budget_cny=100.0,
        daily_budget_cny=20.0,
        soft_daily_budget_cny=0.01,
    )
    store.record(
        _Usage(
            model="deepseek-v4-flash",
            prompt_tokens=10_000_000,
            completion_tokens=0,
            purpose="proactive_contact",
        )
    )
    model = DeepSeekChatModel(
        "key",
        "https://api.deepseek.com",
        "deepseek-v4-flash",
        transport=httpx.MockTransport(handler),
        usage_observer=store.record,
    )
    with pytest.raises(BackgroundSpendCapDenied):
        with model_call_scope("proactive_contact", actor="agent:companion"):
            await model.complete([{"role": "user", "content": "hi"}])
    assert requested == []


def test_image_usage_events_count_against_background_cny_cap(tmp_path) -> None:
    from companion_daemon.db import UsageEventsLedger

    path = tmp_path / "usage.sqlite"
    store = WorldV2UsageStore(
        path=str(path),
        monthly_budget_cny=100.0,
        daily_budget_cny=4.0,
        soft_daily_budget_cny=3.0,
    )
    UsageEventsLedger(path).record_usage("image_generation", 3.5, note="paid-render")
    with pytest.raises(BackgroundSpendCapDenied, match="soft_daily_budget_exceeded"):
        store.admit_provider_call(
            purpose="private_impression_reflection",
            actor="agent:companion",
            provider="deepseek",
            model="deepseek-v4-flash",
            prompt_characters=80,
        )


def test_background_cap_reprices_legacy_half_price_rows(tmp_path) -> None:
    """Stored cost_cny=1.008 (old USD×7.2) must not sneak under a ¥1.2 soft cap.

    1M Flash cache-miss is ¥1.5 off-peak / ¥3.0 peak after 2026-08-17. The old
    table priced that same row at ¥1.008, which would have admitted the next
    background call.
    """

    path = tmp_path / "usage.sqlite"
    store = WorldV2UsageStore(
        path=str(path),
        monthly_budget_cny=100.0,
        daily_budget_cny=4.0,
        soft_daily_budget_cny=1.2,
    )
    recorded_at = datetime.now(timezone.utc).isoformat()
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            INSERT INTO world_v2_model_usage (
                recorded_at, world_id, turn_id, purpose, model, status,
                provider, prompt_tokens, completion_tokens, cache_hit_tokens,
                cache_miss_tokens, total_tokens, error, cost_cny, latency_ms
            ) VALUES (?, '', '', 'private_impression_reflection', 'deepseek-v4-flash',
                      'succeeded', 'deepseek', 1000000, 0, 0, 1000000, 1000000, '', 1.008, 10)
            """,
            (recorded_at,),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(BackgroundSpendCapDenied, match="soft_daily_budget_exceeded"):
        store.admit_provider_call(
            purpose="private_impression_reflection",
            actor="agent:companion",
            provider="deepseek",
            model="deepseek-v4-flash",
            prompt_characters=80,
        )
