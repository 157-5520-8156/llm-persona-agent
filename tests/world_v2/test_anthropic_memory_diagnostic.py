import json

import pytest

from diagnose_anthropic_memory import evaluate, sha, variant_body
from companion_daemon.llm_anthropic import AnthropicChatModel
from companion_daemon.world_v2.character_interior.structured_role import (
    StructuredCharacterRoleFaculty,
)
from test_character_interior_structured_role import _RequiredToolQueueModel, _request, _result
from test_background_recovery_regressions import manifest, choices


def test_output_limit_differential_changes_no_semantic_input():
    body = {
        "model": "fixture",
        "temperature": 0.8,
        "messages": [{"role": "user", "content": "source"}],
        "max_tokens": 16384,
        "tools": [{"name": "report", "input_schema": {"type": "object"}}],
        "tool_choice": {"type": "tool", "name": "report"},
    }
    for name, limit in [("cap4096", 4096), ("cap16384", 16384), ("cap64000", 64000)]:
        result = variant_body(body, name)
        assert result.pop("max_tokens") == limit
        assert sha(result) == sha({k: v for k, v in body.items() if k != "max_tokens"})
    assert body["max_tokens"] == 16384


@pytest.mark.asyncio
async def test_diagnostic_uses_original_memory_host_validation():
    model = _RequiredToolQueueModel()
    role = StructuredCharacterRoleFaculty(model=model, model_id="fixture")
    request = await _request(purpose="memory_consolidation", capability_manifest=manifest())
    captured = {"role": role, "request": request, "body": {"tools": [{"name": "memory"}]}}
    parser = AnthropicChatModel("fixture", "http://localhost:8788", "fixture")
    value = {
        "type": "message",
        "model": "fixture",
        "stop_reason": "tool_use",
        "content": [
            {
                "type": "tool_use",
                "name": "memory",
                "input": json.loads(_result(status="decision", decision={"payload": choices()})),
            }
        ],
    }
    try:
        assert (await evaluate(captured, value, parser))["status"] == "decision"
        value["content"][0]["input"]["decision"]["payload"]["choices"].pop()
        with pytest.raises(ValueError):
            await evaluate(captured, value, parser)
        value["content"] = [{"type": "text", "text": "a fluent but unstructured answer"}]
        value["stop_reason"] = "end_turn"
        with pytest.raises(ValueError, match="required_tool_result_absent"):
            await evaluate(captured, value, parser)
    finally:
        await parser.aclose()


@pytest.mark.asyncio
async def test_real_text_carrier_preserves_core_schema_correction_and_sources():
    import httpx
    from companion_daemon.world_v2.character_interior.core import CharacterInterior
    from companion_daemon.world_v2.character_interior.contracts import InteriorOpportunity
    from test_character_interior_structured_role import _Projection, _CURSOR, _NOW

    calls = []
    good = _result(status="decision", decision={"payload": choices()})

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "fixture",
                "type": "message",
                "model": "fixture",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "content": [{"type": "text", "text": "{}" if len(calls) == 1 else good}],
            },
        )

    model = AnthropicChatModel(
        "fixture",
        "http://localhost:8788",
        "fixture",
        tool_result_carrier="native_or_json_object",
        usage_observer=lambda _: None,
        transport=httpx.MockTransport(handler),
    )
    role = StructuredCharacterRoleFaculty(model=model, model_id="fixture")
    core = CharacterInterior(projection=_Projection(), role=role)
    opportunity = InteriorOpportunity(
        opportunity_ref="memory:test",
        inner_turn_ref="turn:test",
        world_id="world:test",
        actor_ref="character:zhizhi",
        trigger_ref="trigger:test",
        cursor=_CURSOR,
        logical_time=_NOW,
        purpose="memory_consolidation",
        source_refs=("source:private_self",),
        capability_manifest=manifest(),
    )
    try:
        result = await core.consider(opportunity)
        assert result.status == "decided", result.failure_code
        assert len(calls) == 2
        assert result.author_lineage.attempt_ordinal == 1
        assert result.author_lineage.parent_model_call_id
        assert result.author_lineage.model_version.endswith(":json-object-carrier.1")
        assert list(result.decision['source_refs']) == list(manifest().source_refs)
        assert (
            json.loads(calls[1]["messages"][-1]["content"])["correction"]["failure_code"]
            == "role_result_schema_invalid"
        )
    finally:
        await model.aclose()


def test_response_carrier_changes_pinned_schema_identity_only_when_enabled():
    from types import SimpleNamespace
    from companion_daemon.world_v2.character_interior.single_tool_transport import (
        resolve_single_tool_transport,
    )

    tools = [{"type": "function", "function": {"name": "report", "parameters": {"type": "object"}}}]
    choice = {"type": "function", "function": {"name": "report"}}
    identity = {"tool_name": "report", "contract_id": "fixture", "version": "1"}
    native = resolve_single_tool_transport(
        provider=SimpleNamespace(), tools=tools, tool_choice=choice, identity=identity
    )
    adapted = resolve_single_tool_transport(
        provider=SimpleNamespace(single_tool_response_carrier="native_or_json_object"),
        tools=tools,
        tool_choice=choice,
        identity=identity,
    )
    assert native.identity == identity
    assert adapted.identity["schema_contract_identity_json"] == json.dumps(
        identity, sort_keys=True, separators=(",", ":")
    )
    assert adapted.identity != native.identity
    assert adapted.tool_choice == native.tool_choice
