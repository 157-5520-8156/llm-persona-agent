"""Exact biography authority remains readable at the provider HTTP boundary."""

from __future__ import annotations

import json

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.biographical_claim_authority import (
    biographical_coordinate_authorities,
)
from companion_daemon.world_v2.character_interior.inbound_author import (
    _InboundCharacterAuthor as InboundCharacterAuthor,
)
from companion_daemon.world_v2.character_interior.snapshot_compiler import (
    compile_inner_life_snapshot,
)
from companion_daemon.world_v2.expression_draft import QQ_NAPCAT_EXPRESSION_CAPABILITIES
from companion_daemon.world_v2.model_facing_context import compact_model_facing_context
from test_character_interior_inbound_author import _request


_LOGICAL_AT = "2026-07-30T06:08:00+00:00"
_PARENT_REF = "biography:provider-presentation"
_VALUES = {
    "age": 21,
    "academic_phase": "summer_break",
    "academic_year": 3,
    "season": "summer",
    "calendar_context_tags": ["academic:summer_break", "calendar:summer"],
    "current_residence_context_tags": ["residence:family_home_jiaxing"],
}


def _context(*, snapshot: bool) -> dict:
    context = {
        "world_id": "world:biography-presentation",
        "actor_ref": "agent:companion",
        "world_revision": 3,
        "deliberation_revision": 0,
        "ledger_sequence": 3,
        "logical_time": _LOGICAL_AT,
        "slices": {
            "world_life": {
                "availability": "available",
                "items": [{
                    "item_ref": _PARENT_REF,
                    "source_hash": "a" * 64,
                    "value": {
                        "context_kind": "biographical_context",
                        "logical_at": _LOGICAL_AT,
                        **_VALUES,
                        "settled_biographical_coordinates": [{
                            "coordinate_ref": "biography:private-direction",
                            "authority_kind": "character_direction",
                            "summary": "还没想公开的长期打算",
                            "context_tags": ["direction.work:private"],
                            "privacy_class": "private",
                            "settlement_event_ref": "event:private-direction-settled",
                        }],
                    },
                }],
            },
        },
    }
    if snapshot:
        context["inner_life_snapshot"] = compile_inner_life_snapshot(context).model_view()
    return context


async def _provider_body(context, monkeypatch):
    captured = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.append(body)
        name = body["tool_choice"]["function"]["name"]
        arguments = json.dumps({
            "result_kind": "reply_only",
            "payload_json": json.dumps({
                "messages": ["嗯"],
                "meaning_of_this": "看到这条消息",
                "my_state": "想简单回应",
                "world_claims": [],
            }, ensure_ascii=False),
        }, ensure_ascii=False)
        chunks = [{
            "choices": [{
                "delta": {"tool_calls": [{
                    "index": 0, "id": "fixture-biography", "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }]},
                "finish_reason": "tool_calls",
            }],
        }, {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 30}}]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
            + "data: [DONE]\n\n",
        )

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    model = DeepSeekChatModel(
        "offline-fixture", "https://fixture.invalid", "deepseek-v4-flash",
        thinking_enabled=False, transport=httpx.MockTransport(respond),
    )
    author = InboundCharacterAuthor(
        flash_model=model,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
        require_explicit_authored_decision_fields=True,
    )
    request = _request(revision=3, call="call:biography-presentation").model_copy(
        update={"model_content_json": json.dumps(context, ensure_ascii=False)}
    )
    try:
        head = await author.propose_stream_head(request)
        await author.propose_stream_tail(request.model_copy(update={"call_id": "call:biography-tail"}))
        assert "嗯" in json.dumps(head.raw_proposal, ensure_ascii=False)
    finally:
        await model.aclose()
    assert len(captured) == 1
    return captured[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot", [False, True])
async def test_provider_can_bind_each_biography_alias_to_its_exact_visible_coordinate(monkeypatch, snapshot):
    context = _context(snapshot=snapshot)
    body = await _provider_body(context, monkeypatch)
    user = json.loads(body["messages"][1]["content"])
    boundary = user["expression_hard_boundaries"]
    catalog = boundary["biographical_coordinate_authority"]

    assert boundary["contract"] == "expression-hard-boundaries.present.2"
    assert {item["field_path"]: item["value"] for item in catalog} == {
        "/age": 21,
        "/academic_phase": "summer_break",
        "/academic_year": 3,
        "/season": "summer",
        "/calendar_context_tags": ["academic:summer_break", "calendar:summer"],
        "/current_residence_context_tags": ["residence:family_home_jiaxing"],
    }
    visible_authorities = {
        item.field_path: item
        for item in biographical_coordinate_authorities(
            json.loads(compact_model_facing_context(json.dumps(context, ensure_ascii=False)))
        )
    }
    for coordinate in catalog:
        assert set(coordinate) == {"source_ref", "scope", "field_path", "logical_at", "value"}
        assert coordinate["logical_at"] == _LOGICAL_AT
        assert coordinate["scope"] == "current_world"
        alias = coordinate["source_ref"]
        assert alias in boundary["world_claim_source_refs"]["current_world"]
        assert boundary["source_ref_aliases"][alias] == visible_authorities[coordinate["field_path"]].source_ref
        assert alias not in boundary["world_claim_source_refs"]["past_world"]
        assert alias not in boundary["world_claim_source_refs"]["stable_identity"]
    assert _PARENT_REF not in boundary["world_claim_source_refs"]["current_world"]
    assert boundary["companion_life_authority_availability"]["active_activity_source_refs"] == []
    assert boundary["companion_life_authority_availability"]["active_occurrence_source_refs"] == []
    assert "biography:private-direction" not in json.dumps(catalog, ensure_ascii=False)

    provider_context = json.loads(user["request"]["model_content_json"])
    if snapshot:
        assert "slices" not in provider_context
        biography = user["inner_life_snapshot"]["materials"]["biographical_context"][0]
    else:
        biography = provider_context["slices"]["world_life"]["items"][0]["value"]
    assert biography["age"] == 21
    assert biography["academic_phase"] == "summer_break"
    assert biography["current_residence_context_tags"] == ["residence:family_home_jiaxing"]


@pytest.mark.asyncio
@pytest.mark.parametrize("source_state", ["unavailable", "private_only", "empty"])
async def test_provider_does_not_restore_biography_authority_from_unavailable_or_private_material(
    monkeypatch, source_state,
):
    context = _context(snapshot=False)
    lane = context["slices"]["world_life"]
    if source_state == "unavailable":
        # The full input still contains the item, but it is outside the
        # provider-visible available lane and cannot be recovered as authority.
        lane["availability"] = "unavailable"
    elif source_state == "private_only":
        value = lane["items"][0]["value"]
        for field in _VALUES:
            del value[field]
    else:
        lane["items"] = []
    context["inner_life_snapshot"] = compile_inner_life_snapshot(context).model_view()

    body = await _provider_body(context, monkeypatch)
    user = json.loads(body["messages"][1]["content"])
    boundary = user["expression_hard_boundaries"]
    assert "biographical_coordinate_authority" not in boundary
    assert not any(
        ref.startswith("biography-coordinate:")
        for ref in boundary["source_ref_aliases"].values()
    )
    assert boundary["world_claim_source_refs"]["current_world"] == []
    assert boundary["world_claim_source_refs"]["past_world"] == []
    assert boundary["companion_life_authority_availability"]["active_activity_source_refs"] == []
    assert "current_counterpart_report_authority" not in boundary
