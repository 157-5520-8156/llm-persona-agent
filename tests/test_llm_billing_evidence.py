import asyncio
import json

import httpx
import pytest

from companion_daemon.llm import (
    DeepSeekChatModel,
    ModelCapacityBusyError,
    ModelCircuitOpenError,
    ProviderCapacityGate,
    ProviderCircuitBreaker,
    model_call_scope,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_usage",
    [
        None,
        {},
        {"prompt_tokens": 8},
        {"prompt_tokens": True, "completion_tokens": 2},
        {"prompt_tokens": 8, "completion_tokens": -1},
        {"prompt_tokens": "8", "completion_tokens": 2},
    ],
)
async def test_unmetered_success_with_incomplete_usage_keeps_billing_unknown(
    provider_usage: object,
) -> None:
    captured = []
    payload = {"choices": [{"message": {"content": "hello"}}], "usage": provider_usage}
    model = DeepSeekChatModel(
        "fixture-key",
        "https://fixture.invalid",
        "deepseek-chat",
        thinking_enabled=False,
        circuit_breaker=ProviderCircuitBreaker(),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
        usage_observer=captured.append,
    )
    try:
        assert await model.complete([{"role": "user", "content": "hello"}]) == "hello"
    finally:
        await model.aclose()
    assert len(captured) == 1
    assert captured[0].status == "succeeded"
    assert captured[0].billing_state == "unknown"


@pytest.mark.asyncio
async def test_metered_missing_usage_records_unknown_before_provenance_rejection() -> None:
    captured = []
    model = DeepSeekChatModel(
        "fixture-key",
        "https://fixture.invalid",
        "deepseek-chat",
        thinking_enabled=False,
        circuit_breaker=ProviderCircuitBreaker(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"choices": [{"message": {"content": "hello"}}]},
            )
        ),
        usage_observer=captured.append,
    )
    try:
        with pytest.raises(ValueError, match="prompt_tokens"):
            await model.complete_with_usage([{"role": "user", "content": "hello"}])
    finally:
        await model.aclose()
    assert len(captured) == 1
    assert captured[0].billing_state == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_provider_reported_zero_usage_is_known(streaming: bool) -> None:
    captured = []
    payload = {
        "choices": [{"message": {"content": "{}"}}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
    model = DeepSeekChatModel(
        "fixture-key",
        "https://fixture.invalid",
        "deepseek-chat",
        thinking_enabled=False,
        circuit_breaker=ProviderCircuitBreaker(),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
        usage_observer=captured.append,
    )
    try:
        if streaming:
            await model.complete_json_stream_with_usage([{"role": "user", "content": "JSON"}])
        else:
            await model.complete([{"role": "user", "content": "JSON"}])
    finally:
        await model.aclose()
    assert len(captured) == 1
    assert captured[0].billing_state == "known"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["invalid_content", "transport_error", "cancelled"])
async def test_failed_stream_preserves_usage_already_received(terminal: str) -> None:
    captured = []

    class ResponseStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            event = {
                "choices": [],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 4,
                    "total_tokens": 15,
                    "prompt_cache_hit_tokens": 8,
                    "prompt_cache_miss_tokens": 3,
                    "completion_tokens_details": {"reasoning_tokens": 2},
                },
            }
            yield ("data: " + json.dumps(event) + "\n\n").encode()
            if terminal == "transport_error":
                raise httpx.ReadError("fixture stream interrupted")
            if terminal == "cancelled":
                raise asyncio.CancelledError()
            yield b"data: [DONE]\n\n"

    model = DeepSeekChatModel(
        "fixture-key",
        "https://fixture.invalid",
        "deepseek-chat",
        thinking_enabled=False,
        circuit_breaker=ProviderCircuitBreaker(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=ResponseStream(),
            )
        ),
        usage_observer=captured.append,
    )
    expected_error = {
        "invalid_content": ValueError,
        "transport_error": httpx.ReadError,
        "cancelled": asyncio.CancelledError,
    }[terminal]
    try:
        with pytest.raises(expected_error):
            await model.complete_json_stream_with_usage([{"role": "user", "content": "JSON"}])
    finally:
        await model.aclose()
    assert len(captured) == 1
    usage = captured[0]
    assert usage.status == "failed"
    assert usage.billing_state == "known"
    assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == (11, 4, 15)
    assert (usage.cache_hit_tokens, usage.cache_miss_tokens, usage.reasoning_tokens) == (8, 3, 2)


@pytest.mark.asyncio
async def test_nonstream_invalid_content_still_records_provider_usage() -> None:
    captured = []
    model = DeepSeekChatModel(
        "fixture-key",
        "https://fixture.invalid",
        "deepseek-chat",
        thinking_enabled=False,
        circuit_breaker=ProviderCircuitBreaker(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 4},
                },
            )
        ),
        usage_observer=captured.append,
    )
    try:
        with pytest.raises(ValueError, match="choices"):
            await model.complete([{"role": "user", "content": "hello"}])
    finally:
        await model.aclose()
    assert len(captured) == 1
    assert captured[0].status == "failed"
    assert captured[0].billing_state == "known"
    assert captured[0].prompt_tokens == 11
    assert captured[0].completion_tokens == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("failure", ["circuit", "capacity", "payload", "cancelled"])
async def test_local_rejection_without_emission_is_not_billed(
    streaming: bool,
    failure: str,
) -> None:
    captured = []
    requests = []
    circuit = ProviderCircuitBreaker(failure_threshold=1)
    capacity = ProviderCapacityGate()
    occupied = capacity.acquire() if failure == "capacity" else None
    if failure == "circuit":
        circuit.record_failure()

    class LocalFailureModel(DeepSeekChatModel):
        def request_payload(self, *args, **kwargs):
            if failure == "payload":
                raise ValueError("fixture payload construction failed")
            if failure == "cancelled":
                raise asyncio.CancelledError()
            return super().request_payload(*args, **kwargs)

    def handler(request):
        requests.append(request)
        raise AssertionError("local rejection must not call transport")

    model = LocalFailureModel(
        "fixture-key",
        "https://fixture.invalid",
        "deepseek-chat",
        thinking_enabled=False,
        circuit_breaker=circuit,
        capacity_gate=capacity,
        transport=httpx.MockTransport(handler),
        usage_observer=captured.append,
    )
    expected = {
        "circuit": ModelCircuitOpenError,
        "capacity": ModelCapacityBusyError,
        "payload": ValueError,
        "cancelled": asyncio.CancelledError,
    }[failure]
    try:
        with model_call_scope("inbound_turn") as scope, pytest.raises(expected):
            if streaming:
                await model.complete_json_stream_with_usage([{"role": "user", "content": "hi"}])
            else:
                await model.complete([{"role": "user", "content": "hi"}])
    finally:
        if occupied:
            capacity.release(occupied)
        await model.aclose()
    assert scope.request_emitted is False
    assert requests == []
    assert len(captured) == 1
    assert captured[0].status == "failed"
    assert captured[0].billing_state == "not_billed"
    assert capacity.snapshot().status == "idle"


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "error", [httpx.ReadTimeout, asyncio.CancelledError, ModelCircuitOpenError]
)
async def test_after_emission_failure_without_usage_keeps_unknown_billing(streaming, error):
    captured = []
    requests = []

    def handler(request):
        requests.append(request)
        raise error("fixture interruption after request emission")

    model = DeepSeekChatModel(
        "fixture-key",
        "https://fixture.invalid",
        "deepseek-chat",
        thinking_enabled=False,
        circuit_breaker=ProviderCircuitBreaker(),
        transport=httpx.MockTransport(handler),
        usage_observer=captured.append,
    )
    try:
        with model_call_scope("inbound_turn") as scope, pytest.raises(error):
            if streaming:
                await model.complete_json_stream_with_usage([{"role": "user", "content": "hi"}])
            else:
                await model.complete([{"role": "user", "content": "hi"}])
    finally:
        await model.aclose()
    assert scope.request_emitted is True
    assert len(requests) == 1
    assert len(captured) == 1
    assert captured[0].billing_state == "unknown"


@pytest.mark.asyncio
async def test_second_request_rejection_does_not_inherit_first_request_emission():
    captured = []
    requests = []
    circuit = ProviderCircuitBreaker(failure_threshold=1)

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1},
            },
        )

    model = DeepSeekChatModel(
        "fixture-key",
        "https://fixture.invalid",
        "deepseek-chat",
        thinking_enabled=False,
        circuit_breaker=circuit,
        transport=httpx.MockTransport(handler),
        usage_observer=captured.append,
    )
    try:
        with model_call_scope("inbound_turn") as scope:
            await model.complete([{"role": "user", "content": "hi"}])
            circuit.record_failure()
            with pytest.raises(ModelCircuitOpenError):
                await model.complete_json_stream_with_usage([{"role": "user", "content": "hi"}])
    finally:
        await model.aclose()
    assert scope.request_emitted is True
    assert len(requests) == 1
    assert [usage.billing_state for usage in captured] == ["known", "not_billed"]
