"""Provider admission covers the complete billed request before any HTTP effect."""

import json
import sqlite3

import httpx
import pytest

from companion_daemon.llm import (
    DeepSeekChatModel,
    OpenAICompatibleChatModel,
    ProviderCircuitBreaker,
    model_call_scope,
)
from companion_daemon.usage_metrics import ProviderRequestPricingUnavailable
from companion_daemon.world_v2.model_usage_budget import (
    BackgroundSpendCapDenied,
    WorldV2UsageStore,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("caller_estimate", [None, 0.0])
@pytest.mark.parametrize(
    "model_type, model_id",
    [
        (DeepSeekChatModel, "deepseek-v4-flash"),
        (OpenAICompatibleChatModel, "gpt-4.1-mini"),
    ],
)
async def test_request_output_ceiling_is_reserved_before_http(
    tmp_path,
    streaming,
    caller_estimate,
    model_type,
    model_id,
):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 1},
            },
        )

    store = WorldV2UsageStore(path=str(tmp_path / "world.sqlite"), monthly_budget_cny=0.01)
    model = model_type(
        "fixture-key",
        "https://fixture.invalid",
        model_id,
        max_completion_tokens=4096,
        transport=httpx.MockTransport(respond),
        usage_observer=store.record,
        circuit_breaker=ProviderCircuitBreaker(),
    )
    try:
        with (
            model_call_scope("inbound_turn", estimated_cny=caller_estimate),
            pytest.raises(BackgroundSpendCapDenied),
        ):
            if streaming:
                await model.complete_json_stream_with_usage([{"role": "user", "content": "hi"}])
            else:
                await model.complete_json([{"role": "user", "content": "hi"}])
    finally:
        await model.aclose()
    assert requests == []
    assert store.budget_state()["pending_cost_cny"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_tool_schema_is_reserved_even_for_a_tiny_message(tmp_path, streaming):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 80, "completion_tokens": 1},
            },
        )

    store = WorldV2UsageStore(path=str(tmp_path / "world.sqlite"), monthly_budget_cny=0.02)
    model = DeepSeekChatModel(
        "fixture-key",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        max_completion_tokens=1,
        transport=httpx.MockTransport(respond),
        usage_observer=store.record,
        circuit_breaker=ProviderCircuitBreaker(),
    )
    messages = [{"role": "user", "content": "hi"}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "choose",
                "description": "evidence " * 6000,
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    try:
        with model_call_scope("inbound_turn"):
            assert await model.complete_json(messages) == "{}"
        assert len(requests) == 1
        with model_call_scope("inbound_turn"), pytest.raises(BackgroundSpendCapDenied):
            if streaming:
                await model.complete_json_stream_with_usage(messages, tools=tools)
            else:
                await model.complete_json(messages, tools=tools)
    finally:
        await model.aclose()
    assert len(requests) == 1
    assert store.budget_state()["pending_cost_cny"] == 0
    assert store.budget_state()["unknown_cost_hold_cny"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_unpriced_request_cannot_use_a_caller_estimate_to_emit(tmp_path, streaming):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(500)

    store = WorldV2UsageStore(path=str(tmp_path / "world.sqlite"), monthly_budget_cny=1)
    model = OpenAICompatibleChatModel(
        "fixture-key",
        "https://fixture.invalid",
        "unpriced-model",
        transport=httpx.MockTransport(respond),
        usage_observer=store.record,
        circuit_breaker=ProviderCircuitBreaker(),
    )
    try:
        with (
            model_call_scope("inbound_turn", estimated_cny=0.0001),
            pytest.raises(ProviderRequestPricingUnavailable),
        ):
            if streaming:
                await model.complete_json_stream_with_usage([{"role": "user", "content": "hi"}])
            else:
                await model.complete_json([{"role": "user", "content": "hi"}])
    finally:
        await model.aclose()
    assert requests == []
    assert store.budget_state()["pending_cost_cny"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_post_emission_timeout_keeps_full_output_reservation(tmp_path, streaming):
    requests = []

    def timeout(request):
        requests.append(request)
        raise httpx.ReadTimeout("fixture response unavailable", request=request)

    store = WorldV2UsageStore(path=str(tmp_path / "world.sqlite"), monthly_budget_cny=1)
    model = DeepSeekChatModel(
        "fixture-key",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        max_completion_tokens=4096,
        transport=httpx.MockTransport(timeout),
        usage_observer=store.record,
        circuit_breaker=ProviderCircuitBreaker(),
    )
    try:
        with model_call_scope("inbound_turn"), pytest.raises(httpx.ReadTimeout):
            if streaming:
                await model.complete_json_stream_with_usage([{"role": "user", "content": "hi"}])
            else:
                await model.complete_json([{"role": "user", "content": "hi"}])
    finally:
        await model.aclose()
    assert len(requests) == 1
    assert store.budget_state()["pending_cost_cny"] == 0
    assert store.budget_state()["unknown_cost_hold_cny"] > 0.01


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_existing_hermes_openrouter_route_has_a_priced_ceiling(tmp_path, streaming):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 400},
            },
        )

    database = tmp_path / "world.sqlite"
    store = WorldV2UsageStore(path=str(database), monthly_budget_cny=0.5)
    # The existing QQ media private-prompt route uses this model, 400-token
    # output ceiling, empty reasoning option, and the shared bound observer.
    model = OpenAICompatibleChatModel(
        "fixture-key",
        "https://openrouter.ai/api/v1",
        "nousresearch/hermes-4-70b",
        max_completion_tokens=400,
        reasoning_effort="",
        transport=httpx.MockTransport(respond),
        usage_observer=store.record,
        circuit_breaker=ProviderCircuitBreaker(),
    )
    model.provider = "openrouter"
    messages = [{"role": "user", "content": "fixture private prompt"}]
    try:
        with model_call_scope("media_private_prompt_author"):
            if streaming:
                await model.complete_json_stream_with_usage(messages)
            else:
                await model.complete_json(messages)
    finally:
        await model.aclose()
    assert len(requests) == 1
    assert requests[0]["messages"] == messages
    assert requests[0]["max_completion_tokens"] == 400
    assert requests[0]["provider"]["max_price"] == {
        "prompt": 0.13,
        "completion": 0.40,
        "request": 0,
        "image": 0,
    }
    with sqlite3.connect(database) as connection:
        version, cost = connection.execute(
            "SELECT pricing_version, cost_cny FROM world_v2_model_usage"
        ).fetchone()
    assert version == "openrouter-hermes-2026-09-07"
    assert cost == pytest.approx((1000 * 0.13 + 400 * 0.40) / 1_000_000 * 7.2, abs=0.00005)
    assert store.budget_state()["pending_cost_cny"] == 0
    assert store.budget_state()["unknown_cost_hold_cny"] == 0
