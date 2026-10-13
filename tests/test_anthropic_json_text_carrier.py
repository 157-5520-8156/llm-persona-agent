import json

import httpx
import pytest

from companion_daemon.llm_anthropic import AnthropicChatModel


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "report",
            "parameters": {
                "type": "object",
                "properties": {"status": {"type": "string"}},
                "required": ["status"],
                "additionalProperties": False,
            },
        },
    }
]
CHOICE = {"type": "function", "function": {"name": "report"}}


def response(text):
    return {
        "id": "fixture",
        "model": "fixture",
        "type": "message",
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


@pytest.mark.asyncio
async def test_explicit_text_carrier_returns_exact_json_without_inventing_tool_call():
    raw = '{ "status" : "decision" }'
    value = response(raw)
    model = AnthropicChatModel(
        "fixture",
        "http://localhost:8788",
        "fixture",
        tool_result_carrier="native_or_json_object",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=value)),
        usage_observer=lambda _: None,
    )
    try:
        assert (
            await model.complete_json(
                [{"role": "user", "content": "input"}], tools=TOOLS, tool_choice=CHOICE
            )
            == raw
        )
        assert model._response_message(value)["tool_calls"] == []
        assert model.model_version.endswith(":json-object-carrier.1")
    finally:
        await model.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        "plain prose",
        '```json\n{"status":"ok"}\n```',
        '{"status":"ok"} trailing',
        '{"status":"ok","status":"bad"}',
        '{"status":NaN}',
        "[]",
        '{"status":1e999}',
        '{"status":-1e999}',
        '{"nested":[{"number":1e999}]}',
    ],
)
async def test_text_carrier_rejects_non_object_ambiguous_or_repaired_text(raw):
    model = AnthropicChatModel(
        "fixture",
        "http://localhost:8788",
        "fixture",
        tool_result_carrier="native_or_json_object",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response(raw))),
        usage_observer=lambda _: None,
    )
    try:
        with pytest.raises(ValueError):
            await model.complete_json(
                [{"role": "user", "content": "input"}], tools=TOOLS, tool_choice=CHOICE
            )
    finally:
        await model.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("carrier,choice", [("native", CHOICE), ("native_or_json_object", "auto")])
async def test_native_default_and_unforced_selection_do_not_accept_text(carrier, choice):
    model = AnthropicChatModel(
        "fixture",
        "http://localhost:8788",
        "fixture",
        tool_result_carrier=carrier,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=response('{"status":"ok"}'))
        ),
        usage_observer=lambda _: None,
    )
    try:
        with pytest.raises(ValueError):
            await model.complete_json(
                [{"role": "user", "content": "input"}], tools=TOOLS, tool_choice=choice
            )
    finally:
        await model.aclose()


@pytest.mark.asyncio
async def test_wrong_native_tool_never_falls_back_to_valid_text():
    value = response('{"status":"ok"}')
    value["content"].append({"type": "tool_use", "name": "wrong", "input": {"status": "ok"}})
    value["stop_reason"] = "tool_use"
    model = AnthropicChatModel(
        "fixture",
        "http://localhost:8788",
        "fixture",
        tool_result_carrier="native_or_json_object",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=value)),
        usage_observer=lambda _: None,
    )
    try:
        with pytest.raises(ValueError):
            await model.complete_json(
                [{"role": "user", "content": "input"}], tools=TOOLS, tool_choice=CHOICE
            )
    finally:
        await model.aclose()


@pytest.mark.asyncio
async def test_streamed_text_is_released_only_after_complete_object_and_message_stop():
    frames = [
        {
            "type": "message_start",
            "message": {
                "id": "m",
                "type": "message",
                "model": "fixture",
                "usage": {"input_tokens": 10, "output_tokens": 0},
            },
        },
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": '{"status":'},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": '"ok"}'},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 5},
        },
        {"type": "message_stop"},
    ]
    chunks = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for frame in frames:
                assert chunks == []
                yield ("data: " + json.dumps(frame) + "\n\n").encode()

    model = AnthropicChatModel(
        "fixture",
        "http://localhost:8788",
        "fixture",
        tool_result_carrier="native_or_json_object",
        usage_observer=lambda _: None,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=Stream()
            )
        ),
    )
    try:
        text, _ = await model.complete_json_stream_with_usage(
            [{"role": "user", "content": "input"}],
            tools=TOOLS,
            tool_choice=CHOICE,
            on_text_delta=chunks.append,
        )
        assert chunks == [text] == ['{"status":"ok"}']
    finally:
        await model.aclose()
