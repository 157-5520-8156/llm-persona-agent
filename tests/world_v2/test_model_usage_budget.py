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
    ModelUsageAdmissionError,
    WorldV2UsageStore,
)


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
    # 1M miss tokens at $0.14/M = $0.14 * 7.2 = 1.008 CNY
    monthly = store.monthly_cost_cny()
    daily = store.daily_cost_cny()
    assert monthly == pytest.approx(1.008, abs=0.01)
    assert daily == pytest.approx(1.008, abs=0.01)


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
    assert state["monthly_cost_cny"] == pytest.approx(10.08, abs=0.05)


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
    state = store.budget_state(monthly_budget_cny=100.0, daily_budget_cny=10.0)
    assert state["cny_per_delivered_message"] == pytest.approx(0.504, abs=0.01)


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
