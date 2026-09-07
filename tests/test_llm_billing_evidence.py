import asyncio
import json

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel, ProviderCircuitBreaker


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
