"""Single-tool auto transport preserves caller choice and validates reply identity."""

from __future__ import annotations

import json

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel, OpenAICompatibleChatModel


_MESSAGES = [{"role": "user", "content": "choose"}]
_FORCED_CHOICE = {"type": "function", "function": {"name": "one_role"}}
_USAGE = {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4}


def _tools():
    return [
        {
            "type": "function",
            "function": {
                "name": "one_role",
                "parameters": {"type": "object"},
            },
        }
    ]


def _tool_call(*, name="one_role", arguments='{"chosen":true}', index=0):
    return {
        "index": index,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _model_with_response(response):
    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        return response

    model = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=True,
        transport=httpx.MockTransport(handler),
    )
    return model, captured


def _delta_frame(delta):
    return ("data: " + json.dumps({"choices": [{"delta": delta}]}) + "\n\n").encode()


def _sse_response(deltas):
    usage = ("data: " + json.dumps({"choices": [], "usage": _USAGE}) + "\n\n").encode()
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=b"".join([*(_delta_frame(delta) for delta in deltas), usage, b"data: [DONE]\n\n"]),
    )


@pytest.fixture(autouse=True)
def _disable_debug_usage_ledger(monkeypatch):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,thinking,expected",
    [
        ("deepseek", True, "auto"),
        ("deepseek", False, "forced"),
        ("openai", False, "forced"),
        ("qwen", False, "forced"),
    ],
)
async def test_single_tool_selection_mode_is_read_only(provider, thinking, expected):
    arguments = ("offline-fixture", "https://fixture.invalid", provider)
    transport = httpx.MockTransport(lambda _request: httpx.Response(500))
    model = (
        DeepSeekChatModel(*arguments, thinking_enabled=thinking, transport=transport)
        if provider == "deepseek"
        else OpenAICompatibleChatModel(*arguments, transport=transport)
    )
    try:
        assert model.single_tool_selection_mode == expected
        with pytest.raises(AttributeError):
            model.single_tool_selection_mode = "forced"
    finally:
        await model.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", ["auto", _FORCED_CHOICE])
async def test_atomic_single_tool_preserves_caller_selection_and_reply_bytes(choice):
    model, captured = _model_with_response(
        httpx.Response(
            200,
            json={"choices": [{"message": {"tool_calls": [_tool_call()]}}], "usage": _USAGE},
        )
    )
    try:
        text, _usage = await model.complete_json_with_usage(
            _MESSAGES, tools=_tools(), tool_choice=choice
        )
    finally:
        await model.aclose()
    assert text == '{"chosen":true}'
    assert len(captured) == 1
    assert captured[0]["tool_choice"] == choice
    assert captured[0]["tools"] == _tools()
    assert captured[0]["thinking"] == {"type": "enabled"}
    assert "response_format" not in captured[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message,error",
    [
        ({"tool_calls": [_tool_call(name="wrong_role")]}, "unexpected tool identity"),
        ({"tool_calls": [_tool_call(), _tool_call(index=1)]}, "exactly one tool call"),
        ({"content": '{"chosen":true}'}, "non-empty"),
    ],
)
async def test_atomic_auto_rejects_wrong_multiple_or_absent_tool_without_retry(message, error):
    model, captured = _model_with_response(
        httpx.Response(200, json={"choices": [{"message": message}], "usage": _USAGE})
    )
    try:
        with pytest.raises(ValueError, match=error):
            await model.complete_json_with_usage(_MESSAGES, tools=_tools(), tool_choice="auto")
    finally:
        await model.aclose()
    assert len(captured) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "tools,choice",
    [
        (None, "auto"),
        ([], "auto"),
        (_tools() + _tools(), "auto"),
        ([{"type": "custom", "function": {"name": "one_role"}}], "auto"),
        ([{"type": "function", "function": {"name": ""}}], "auto"),
        ([{"type": "function", "function": {"name": " "}}], "auto"),
        ([{"type": "function", "function": {"name": "invalid/name"}}], "auto"),
        ([{"type": "function", "function": {"name": "a" * 65}}], "auto"),
        (_tools(), {"type": "function", "function": {"name": "wrong_role"}}),
    ],
)
async def test_single_tool_request_rejects_invalid_declaration_before_http(tools, choice, stream):
    model, captured = _model_with_response(
        httpx.Response(
            200,
            json={"choices": [{"message": {"tool_calls": [_tool_call()]}}], "usage": _USAGE},
        )
    )
    try:
        complete = (
            model.complete_json_stream_with_usage if stream else model.complete_json_with_usage
        )
        with pytest.raises(ValueError, match="declared function"):
            await complete(_MESSAGES, tools=tools, tool_choice=choice)
    finally:
        await model.aclose()
    assert captured == []


@pytest.mark.asyncio
async def test_auto_stream_releases_argument_deltas_only_after_late_matching_name():
    delivered = []

    class DelayedNameStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield _delta_frame(
                {"tool_calls": [{"index": 0, "function": {"arguments": '{"chosen":'}}]}
            )
            assert delivered == []
            yield _delta_frame({"tool_calls": [{"index": 0, "function": {"arguments": "true}"}}]})
            assert delivered == []
            yield _delta_frame({"tool_calls": [{"index": 0, "function": {"name": "one_role"}}]})
            assert delivered == ['{"chosen":', "true}"]
            yield ("data: " + json.dumps({"choices": [], "usage": _USAGE}) + "\n\n").encode()
            yield b"data: [DONE]\n\n"

    model, captured = _model_with_response(
        httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=DelayedNameStream()
        )
    )
    try:
        text, _usage = await model.complete_json_stream_with_usage(
            _MESSAGES, tools=_tools(), tool_choice="auto", on_text_delta=delivered.append
        )
    finally:
        await model.aclose()
    assert text == '{"chosen":true}'
    assert delivered == ['{"chosen":', "true}"]
    assert len(captured) == 1
    assert captured[0]["tool_choice"] == "auto"
    assert captured[0]["tools"] == _tools()
    assert captured[0]["thinking"] == {"type": "enabled"}
    assert "response_format" not in captured[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "deltas,error,expected_deltas",
    [
        ([{"tool_calls": [_tool_call(name="wrong_role")]}], "unexpected tool identity", []),
        (
            [
                {"tool_calls": [{"index": 0, "function": {"arguments": '{"chosen":true}'}}]},
                {"tool_calls": [{"index": 0, "function": {"name": "wrong_role"}}]},
            ],
            "unexpected tool identity",
            [],
        ),
        (
            [{"tool_calls": [_tool_call(), _tool_call(index=1)]}],
            "exactly one tool call",
            [],
        ),
        (
            [{"tool_calls": [_tool_call(), _tool_call()]}],
            "exactly one tool call",
            [],
        ),
        (
            [{"tool_calls": [_tool_call()]}, {"tool_calls": [_tool_call(index=1)]}],
            "exactly one tool call",
            ['{"chosen":true}'],
        ),
        ([{"content": '{"chosen":true}'}], "non-empty", []),
        (
            [{"tool_calls": [{"index": 0, "function": {"arguments": '{"chosen":true}'}}]}],
            "unexpected tool identity",
            [],
        ),
    ],
)
async def test_auto_stream_rejects_unvalidated_tool_response(deltas, error, expected_deltas):
    delivered = []
    model, captured = _model_with_response(_sse_response(deltas))
    try:
        with pytest.raises(ValueError, match=error):
            await model.complete_json_stream_with_usage(
                _MESSAGES, tools=_tools(), tool_choice="auto", on_text_delta=delivered.append
            )
    finally:
        await model.aclose()
    assert delivered == expected_deltas
    assert len(captured) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", ["auto", _FORCED_CHOICE])
async def test_stream_accepts_single_valid_tool_in_a_non_sse_response(choice):
    delivered = []
    model, captured = _model_with_response(
        httpx.Response(
            200,
            json={"choices": [{"message": {"tool_calls": [_tool_call()]}}], "usage": _USAGE},
        )
    )
    try:
        text, _usage = await model.complete_json_stream_with_usage(
            _MESSAGES, tools=_tools(), tool_choice=choice, on_text_delta=delivered.append
        )
    finally:
        await model.aclose()
    assert text == '{"chosen":true}'
    assert delivered == ['{"chosen":true}']
    assert len(captured) == 1


@pytest.mark.asyncio
async def test_auto_stream_rejects_an_extra_tool_after_consumer_releases_a_valid_head():
    delivered = []

    def release_head(delta):
        delivered.append(delta)
        return True

    model, captured = _model_with_response(
        _sse_response(
            [
                {"tool_calls": [_tool_call()]},
                {"tool_calls": [{"index": 1, "function": {"name": "one_role"}}]},
            ]
        )
    )
    try:
        with pytest.raises(ValueError, match="exactly one tool call"):
            await model.complete_json_stream_with_usage(
                _MESSAGES, tools=_tools(), tool_choice="auto", on_text_delta=release_head
            )
    finally:
        await model.aclose()
    assert delivered == ['{"chosen":true}']
    assert len(captured) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_forced_selection_still_allows_other_declared_functions(stream):
    tools = [
        {"type": "function", "function": {"name": "other_role", "parameters": {"type": "object"}}},
        *_tools(),
    ]
    response = (
        _sse_response([{"tool_calls": [_tool_call()]}])
        if stream
        else httpx.Response(
            200,
            json={"choices": [{"message": {"tool_calls": [_tool_call()]}}], "usage": _USAGE},
        )
    )
    model, captured = _model_with_response(response)
    model.thinking_enabled = False
    try:
        complete = (
            model.complete_json_stream_with_usage if stream else model.complete_json_with_usage
        )
        text, _usage = await complete(_MESSAGES, tools=tools, tool_choice=_FORCED_CHOICE)
    finally:
        await model.aclose()
    assert text == '{"chosen":true}'
    assert len(captured) == 1
    assert captured[0]["tool_choice"] == _FORCED_CHOICE
    assert captured[0]["tools"] == tools
    assert captured[0]["thinking"] == {"type": "disabled"}
