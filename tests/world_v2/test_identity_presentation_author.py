"""Identity presentation stays lossless at the actual provider request boundary."""

from __future__ import annotations

import json

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.character_interior.inbound_author import (
    _InboundCharacterAuthor as InboundCharacterAuthor,
)
from companion_daemon.world_v2.companion_identity import (
    CompanionIdentityFrame,
    companion_identity_source_refs,
)
from companion_daemon.world_v2.expression_draft import QQ_NAPCAT_EXPRESSION_CAPABILITIES
from test_character_interior_inbound_author import _request


_DUPLICATE_FIELDS = {
    "personality_frame",
    "speech_frame",
    "speech_examples",
    "style_rules",
    "values",
    "boundaries",
}
_IDENTITY_MARKER = (
    "Private identity frame (authoritative only within the exact source lanes below): "
)
_SOURCES_MARKER = ". Its exact scoped identity sources are "


def _identity() -> CompanionIdentityFrame:
    return CompanionIdentityFrame(
        companion_name="测试角色",
        counterpart_name="测试用户",
        base_prompt="测试角色有自己的生活。",
        personality_frame="性格甲。\n性格乙。\n",
        speech_frame="说话甲。\n说话乙。\n",
        speech_examples=("示例甲。", "示例乙。"),
        style_rules=("规则甲。", "规则乙。"),
        values=("价值甲。", "价值乙。"),
        boundaries=("边界甲。", "边界乙。"),
        stable_identity_facts=("来自测试城市。",),
        shared_history_facts=("在测试读书群认识。",),
    )


async def _provider_request(identity, monkeypatch):
    captured = []

    def respond(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        name = captured[-1]["tool_choice"]["function"]["name"]
        raw = json.dumps(
            {
                "result_kind": "reply_only",
                "payload_json": json.dumps(
                    {
                        "messages": ["嗯"],
                        "meaning_of_this": "看到了这句话",
                        "my_state": "想简单回应",
                        "world_claims": [],
                    },
                    ensure_ascii=False,
                ),
            },
            ensure_ascii=False,
        )
        chunks = [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "fixture-tool",
                                    "type": "function",
                                    "function": {"name": name, "arguments": raw},
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
            {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 30}},
        ]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="".join("data: " + json.dumps(x) + "\n\n" for x in chunks) + "data: [DONE]\n\n",
        )

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    model = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(respond),
    )
    author = InboundCharacterAuthor(
        flash_model=model,
        identity_frame=identity,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
        require_explicit_authored_decision_fields=True,
    )
    try:
        request = _request(revision=3, call="call:identity-presentation")
        head = await author.propose_stream_head(request)
        await author.propose_stream_tail(
            request.model_copy(update={"call_id": "call:identity-tail"})
        )
        assert "嗯" in json.dumps(head.raw_proposal, ensure_ascii=False)
    finally:
        await model.client.aclose()
    assert len(captured) == 1
    return captured[0]


def _identity_parts(body):
    prose, remainder = body["messages"][0]["content"].split(_IDENTITY_MARKER, 1)
    frame, end = json.JSONDecoder().raw_decode(remainder)
    assert remainder[end:].startswith(_SOURCES_MARKER)
    sources, _ = json.JSONDecoder().raw_decode(remainder[end + len(_SOURCES_MARKER) :])
    return prose, frame, sources


@pytest.mark.asyncio
async def test_author_removes_only_duplicate_identity_values_from_http_request(monkeypatch):
    identity = _identity()
    original = identity.model_dump(mode="json")
    source_refs = companion_identity_source_refs(identity)
    body = await _provider_request(identity, monkeypatch)
    prose, displayed, sources = _identity_parts(body)

    assert _DUPLICATE_FIELDS.isdisjoint(displayed)
    for field in _DUPLICATE_FIELDS:
        value = original[field]
        for text in [value] if isinstance(value, str) else value:
            assert text in prose
    assert sources == [
        {
            "scope": "stable_identity",
            "source_ref": source_refs["stable_identity"],
            "facts": ["来自测试城市。"],
        },
        {
            "scope": "shared_history",
            "source_ref": source_refs["shared_history"],
            "facts": ["在测试读书群认识。"],
        },
    ]
    assert displayed["stable_identity_facts"] == ["来自测试城市。"]
    assert displayed["shared_history_facts"] == ["在测试读书群认识。"]
    assert identity.model_dump(mode="json") == original
    assert companion_identity_source_refs(identity) == source_refs

    # Restore the old, redundant presentation using the unchanged source object.
    # The actual outgoing request must be smaller; nothing outside this JSON changes.
    def encode(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    previous_frame = {**displayed, **{key: original[key] for key in _DUPLICATE_FIELDS}}
    old_body = json.loads(json.dumps(body))
    old_body["messages"][0]["content"] = body["messages"][0]["content"].replace(
        _IDENTITY_MARKER + encode(displayed),
        _IDENTITY_MARKER + encode(previous_frame),
        1,
    )
    assert len(encode(body).encode()) < len(encode(old_body).encode())
    system = body["messages"][0]["content"]
    assert "REPLY_ONLY PAYLOAD_JSON CANONICAL SPECIMEN JSON:" in system
    assert "FULL_TURN PAYLOAD_JSON CANONICAL SPECIMEN JSON:" in system


@pytest.mark.asyncio
@pytest.mark.parametrize("field", sorted(_DUPLICATE_FIELDS))
@pytest.mark.parametrize("case", ["whitespace_not_preserved", "empty"])
async def test_author_keeps_identity_field_without_complete_exact_prose_coverage(
    monkeypatch,
    field,
    case,
):
    if field in {"personality_frame", "speech_frame"}:
        value = "  空白也属于原值。  " if case == "whitespace_not_preserved" else ""
    else:
        value = (
            ("这一项可见。", "  空白也属于原值。  ") if case == "whitespace_not_preserved" else ()
        )
    identity = _identity().model_copy(update={field: value})
    body = await _provider_request(identity, monkeypatch)
    prose, displayed, _ = _identity_parts(body)

    assert displayed[field] == identity.model_dump(mode="json")[field]
    if case == "whitespace_not_preserved":
        assert "  空白也属于原值。  " not in prose
    assert identity.model_dump(mode="json")[field] == (
        list(value) if isinstance(value, tuple) else value
    )
