import json

import httpx
import pytest

from companion_daemon.llm_anthropic import AnthropicChatModel


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "report",
            "description": "Return the selected result",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    }
]
CHOICE = {"type": "function", "function": {"name": "report"}}
MESSAGES = [{"role": "system", "content": "原始规则。"}, {"role": "user", "content": "原始输入。"}]


def response(name="report", usage=None, stop="tool_use"):
    return {
        "id": "msg-1",
        "model": "fixture",
        "type": "message",
        "role": "assistant",
        "stop_reason": stop,
        "content": [
            {"type": "tool_use", "id": "tool-1", "name": name, "input": {"text": "原话。"}}
        ],
        "usage": usage
        if usage is not None
        else {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_creation_input_tokens": 20,
            "cache_read_input_tokens": 30,
        },
    }


@pytest.mark.asyncio
async def test_native_request_tool_result_and_usage_have_one_provider_identity():
    captured, observed = [], []

    def handle(request):
        captured.append(request)
        return httpx.Response(200, json=response())

    model = AnthropicChatModel(
        "fixture-key",
        "http://localhost:8788",
        "fixture",
        transport=httpx.MockTransport(handle),
        usage_observer=observed.append,
    )
    try:
        text, usage = await model.complete_json_with_usage(
            MESSAGES, tools=TOOLS, tool_choice=CHOICE
        )
    finally:
        await model.aclose()
    wire = json.loads(captured[0].content)
    assert str(captured[0].url) == "http://localhost:8788/v1/messages"
    assert captured[0].headers["x-api-key"] == "fixture-key"
    assert "authorization" not in captured[0].headers
    assert wire["system"] == [{"type": "text", "text": "原始规则。"}]
    assert wire["messages"] == MESSAGES[1:]
    assert wire["tools"][0]["input_schema"] == TOOLS[0]["function"]["parameters"]
    assert wire["tool_choice"] == {
        "type": "tool",
        "name": "report",
        "disable_parallel_tool_use": True,
    }
    assert not {"response_format", "reasoning_effort", "thinking"}.intersection(wire)
    assert json.loads(text) == {"text": "原话。"}
    assert usage["provider"] == "anthropic" and usage["input_tokens"] == 60
    assert observed[0].prompt_tokens == 60 and observed[0].cache_hit_tokens == 30
    assert observed[0].uncached_input_tokens == 10 and observed[0].cache_creation_tokens == 20
    assert observed[0].completion_tokens == 5 and observed[0].billing_state == "known"
    assert len(captured) == len(observed) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["wrong_tool", "multiple_tools", "truncated", "wrong_model"])
@pytest.mark.parametrize("choice", [CHOICE, None, "auto", "required"])
async def test_invalid_native_result_is_failure_with_billing_evidence(kind, choice):
    value = response()
    if kind == "wrong_tool":
        value["content"][0]["name"] = "different"
    if kind == "multiple_tools":
        value["content"].append(value["content"][0])
    if kind == "truncated":
        value["stop_reason"] = "max_tokens"
    if kind == "wrong_model":
        value["model"] = "different"
    observed = []
    model = AnthropicChatModel(
        "fixture",
        "http://localhost:8788/v1",
        "fixture",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=value)),
        usage_observer=observed.append,
    )
    try:
        with pytest.raises(ValueError):
            await model.complete_json_with_usage(MESSAGES, tools=TOOLS, tool_choice=choice)
    finally:
        await model.aclose()
    assert observed[0].status == "failed" and observed[0].billing_state == "known"
    assert observed[0].prompt_tokens == 60


def events(*, complete=True, wrong_name=False, final_usage=True):
    frames = [
        {
            "type": "message_start",
            "message": {
                "id": "msg-1",
                "model": "fixture",
                "usage": {"input_tokens": 10, "output_tokens": 0, "cache_read_input_tokens": 30},
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "private reasoning"},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {
                "type": "tool_use",
                "id": "tool-1",
                "name": "bad" if wrong_name else "report",
                "input": {},
            },
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"text":'},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '"原话。"}'},
        },
        {"type": "content_block_stop", "index": 1},
    ]
    if complete:
        frames += [
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"output_tokens": 5} if final_usage else {},
            },
            {"type": "message_stop"},
        ]
    return b"".join(
        ("event: " + f["type"] + "\ndata: " + json.dumps(f) + "\n\n").encode() for f in frames
    )


@pytest.mark.asyncio
async def test_stream_uses_native_events_preserves_delta_bytes_and_final_usage():
    captured, chunks, observed = [], [], []

    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=events())

    model = AnthropicChatModel(
        "fixture",
        "http://localhost:8788",
        "fixture",
        transport=httpx.MockTransport(handle),
        usage_observer=observed.append,
    )
    try:
        text, usage = await model.complete_json_stream_with_usage(
            MESSAGES, tools=TOOLS, tool_choice=CHOICE, on_text_delta=chunks.append
        )
    finally:
        await model.aclose()
    assert "".join(chunks) == text == '{"text":"原话。"}'
    assert "stream_options" not in captured[0] and captured[0]["stream"] is True
    assert usage["input_tokens"] == 40 and usage["output_tokens"] == 5
    assert observed[0].billing_state == "known"


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_name", [False, True])
async def test_incomplete_or_wrong_tool_stream_is_not_a_success(wrong_name):
    observed = []
    model = AnthropicChatModel(
        "fixture",
        "http://localhost:8788",
        "fixture",
        usage_observer=observed.append,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=events(complete=False, wrong_name=wrong_name),
            )
        ),
    )
    try:
        with pytest.raises(ValueError):
            await model.complete_json_stream_with_usage(MESSAGES, tools=TOOLS, tool_choice=CHOICE)
    finally:
        await model.aclose()
    assert observed[0].status == "failed" and observed[0].billing_state == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", [None, "auto", "required"])
async def test_stream_binds_all_supported_single_tool_selection_modes(choice):
    model = AnthropicChatModel(
        "fixture",
        "http://localhost:8788",
        "fixture",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=events(wrong_name=True)
            )
        ),
    )
    try:
        with pytest.raises(ValueError, match="unexpected tool identity"):
            await model.complete_json_stream_with_usage(MESSAGES, tools=TOOLS, tool_choice=choice)
    finally:
        await model.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_none_mode_is_rejected_before_a_single_tool_json_request(stream):
    sent, observed = [], []

    def handle(request):
        sent.append(request)
        return httpx.Response(200, json=response())

    model = AnthropicChatModel(
        "fixture",
        "http://localhost:8788",
        "fixture",
        transport=httpx.MockTransport(handle),
        usage_observer=observed.append,
    )
    try:
        method = model.complete_json_stream_with_usage if stream else model.complete_json_with_usage
        with pytest.raises(ValueError, match="tool_choice=none"):
            await method(MESSAGES, tools=TOOLS, tool_choice="none")
    finally:
        await model.aclose()
    assert sent == []
    assert observed[0].billing_state == "not_billed"


@pytest.mark.asyncio
async def test_start_usage_is_not_a_final_output_token_count():
    observed = []
    model = AnthropicChatModel(
        "fixture",
        "http://localhost:8788",
        "fixture",
        usage_observer=observed.append,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=events(final_usage=False),
            )
        ),
    )
    try:
        text, usage = await model.complete_json_stream_with_usage(
            MESSAGES, tools=TOOLS, tool_choice=CHOICE
        )
    finally:
        await model.aclose()
    assert json.loads(text) == {"text": "原话。"}
    assert usage is None
    assert observed[0].billing_state == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_plain_request_rejects_unrequested_tool_even_with_valid_text(stream):
    value = response()
    value["content"].insert(0, {"type": "text", "text": '{"ok":true}'})
    frames = [
        {
            "type": "message_start",
            "message": {
                "id": "msg-1",
                "model": "fixture",
                "usage": {"input_tokens": 10, "output_tokens": 0},
            },
        },
        {"type": "content_block_start", "index": 0, "content_block": value["content"][0]},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": value["content"][1]},
        {"type": "content_block_stop", "index": 1},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use"},
            "usage": {"output_tokens": 5},
        },
        {"type": "message_stop"},
    ]
    wire = b"".join(("data: " + json.dumps(x) + "\n\n").encode() for x in frames)
    model = AnthropicChatModel(
        "fixture",
        "http://localhost:8788",
        "fixture",
        transport=httpx.MockTransport(
            lambda _: (
                httpx.Response(200, headers={"content-type": "text/event-stream"}, content=wire)
                if stream
                else httpx.Response(200, json=value)
            )
        ),
    )
    try:
        with pytest.raises(ValueError, match="unrequested tool"):
            if stream:
                await model.complete_json_stream_with_usage(MESSAGES)
            else:
                await model.complete_json_with_usage(MESSAGES)
    finally:
        await model.aclose()


@pytest.mark.parametrize("where", ["start", "delta"])
def test_stream_cannot_silently_discard_non_text_fragments(where):
    from companion_daemon.llm_anthropic import _MessageStream

    decoder = _MessageStream("fixture")
    decoder({"type": "message_start", "message": {"model": "fixture"}})
    if where == "start":
        malformed = {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": 123},
        }
    else:
        decoder(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": "left"},
            }
        )
        malformed = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": 123},
        }
    with pytest.raises(ValueError, match="text"):
        decoder(malformed)


@pytest.mark.asyncio
async def test_interleaved_system_and_unknown_roles_are_rejected_without_reordering():
    model = AnthropicChatModel("fixture", "http://localhost:8788", "fixture")
    try:
        for messages in (
            [*MESSAGES, {"role": "system", "content": "later"}],
            [{"role": "tool", "content": "unknown result"}],
        ):
            with pytest.raises(ValueError):
                model.request_payload(messages, temperature=0.5)
    finally:
        await model.aclose()
