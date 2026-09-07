"""The chosen compute route keeps one strict single-tool response and honest identity."""

from __future__ import annotations

import json

import httpx
import pytest

import companion_daemon.llm as llm
from companion_daemon.llm import DeepSeekChatModel, provider_invocation_request_hash
from companion_daemon.world_v2.character_interior.inbound_author import (
    _InboundCharacterAuthor as InboundCharacterAuthor,
)
from companion_daemon.world_v2.character_interior.structured_role import (
    StructuredCharacterRoleFaculty,
)
from companion_daemon.world_v2.expression_draft import QQ_NAPCAT_EXPRESSION_CAPABILITIES
from companion_daemon.world_v2.route_hints import RouteHints
from companion_daemon.world_v2.semantic_compute_router import SemanticComputeRouter
from test_character_interior_inbound_author import _request
from test_character_interior_structured_role import (
    _life_choice_result,
    _life_development_manifest,
    _request as _role_request,
)
from test_semantic_compute_router import _request_for


class ProviderBoundary:
    def __init__(self, *, raw, streaming):
        self.raw = raw
        self.streaming = streaming
        self.calls = []
        self.identities = []

    def respond(self, request):
        body = json.loads(request.content)
        self.calls.append(body)
        # Observe the audit carrier at the physical MockTransport boundary;
        # do not derive it from the contract compiler under test.
        identity = llm._MODEL_PROVIDER_REQUEST_IDENTITY.get()
        self.identities.append(identity)
        if body.get("thinking") == {"type": "enabled"} and body.get("tool_choice") != "auto":
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "Thinking mode does not support this tool_choice",
                        "type": "invalid_request_error",
                        "code": "invalid_request_error",
                    }
                },
            )
        name = body["tools"][0]["function"]["name"]
        function = {"name": name, "arguments": self.raw}
        if self.streaming:
            chunks = [
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": function}]}}]},
                {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 30}},
            ]
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content="".join("data: " + json.dumps(x) + "\n\n" for x in chunks)
                + "data: [DONE]\n\n",
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"tool_calls": [{"type": "function", "function": function}]}}
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            },
        )

    def assert_identity(self, *, thinking):
        assert len(self.calls) == len(self.identities) == 1
        body, identity = self.calls[0], self.identities[0]
        assert identity is not None
        transport = identity.identity_extras["tool_contract_identity"]
        if thinking:
            assert body["tool_choice"] == "auto"
            assert transport["contract_id"] == "single-tool-auto-transport"
            assert transport["version"] == "1"
            assert transport["expected_tool_name"] == body["tools"][0]["function"]["name"]
            assert transport["selection_mode"] == "auto"
        else:
            assert body["tool_choice"]["type"] == "function"
            assert "selection_mode" not in transport
            assert transport["contract_id"] != "single-tool-auto-transport"
        expected_hash = provider_invocation_request_hash(
            messages=body["messages"],
            temperature=0.8,
            tools=body["tools"],
            tool_choice=body["tool_choice"],
            identity_extras=identity.identity_extras,
        )
        assert identity.request_hash.removeprefix("sha256:") == expected_hash
        return expected_hash


@pytest.mark.asyncio
@pytest.mark.parametrize("thinking", [False, True])
async def test_public_inbound_route_uses_actual_single_tool_mode_before_audit(
    thinking, monkeypatch
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
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
    boundary = ProviderBoundary(raw=raw, streaming=True)
    model = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=thinking,
        transport=httpx.MockTransport(boundary.respond),
    )
    author = InboundCharacterAuthor(
        flash_model=model,
        thinking_model=model if thinking else None,
        temperature=0.8,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
        require_explicit_authored_decision_fields=True,
    )
    route = await SemanticComputeRouter(thinking_available=thinking).route(
        _request_for(RouteHints(severity="high"))
    )
    request = _request(revision=3, call="call:single-tool-transport").model_copy(
        update={"route": route}
    )
    try:
        head = await author.propose_stream_head(request)
        tail = await author.propose_stream_tail(
            request.model_copy(update={"call_id": "call:single-tool-tail"})
        )
    finally:
        await model.aclose()
    assert "嗯" in json.dumps(head.raw_proposal, ensure_ascii=False)
    assert route.tier == ("thinking" if thinking else "flash")
    expected_hash = boundary.assert_identity(thinking=thinking)
    assert head.winning_request_hash == expected_hash
    assert tail.physical_provider_audits[0].request_hash == expected_hash


@pytest.mark.asyncio
@pytest.mark.parametrize("thinking", [False, True])
async def test_public_structured_role_uses_same_transport_with_original_no_op(
    thinking, monkeypatch
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    boundary = ProviderBoundary(raw=_life_choice_result({"decision": "no_op"}), streaming=False)
    model = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=thinking,
        transport=httpx.MockTransport(boundary.respond),
    )
    role = StructuredCharacterRoleFaculty(
        model=model, model_id="deepseek-v4-flash", temperature=0.8
    )
    try:
        result = await role.consider(
            await _role_request(
                purpose="life_development_choice",
                capability_manifest=_life_development_manifest(),
            )
        )
    finally:
        await model.aclose()
    assert result["decision"]["payload"]["completion"] == {"decision": "no_op"}
    expected_hash = boundary.assert_identity(thinking=thinking)
    assert result["author_lineage"]["request_hash"].removeprefix("sha256:") == expected_hash


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["wrong_name", "multiple", "plain_content"])
async def test_thinking_inbound_transport_failure_never_opens_plain_or_forced_retry(
    fault, monkeypatch
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    boundary = ProviderBoundary(raw="{}", streaming=True)

    def reject_response(request):
        boundary.respond(request)
        name = boundary.calls[-1]["tools"][0]["function"]["name"]
        if fault == "plain_content":
            delta = {"content": "{}"}
        else:
            calls = [
                {
                    "index": 0,
                    "function": {
                        "name": "wrong_function" if fault == "wrong_name" else name,
                        "arguments": "{}",
                    },
                }
            ]
            if fault == "multiple":
                calls.append({"index": 1, "function": {"name": name, "arguments": "{}"}})
            delta = {"tool_calls": calls}
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                "data: " + json.dumps({"choices": [{"delta": delta}]}) + "\n\ndata: [DONE]\n\n"
            ),
        )

    model = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=True,
        transport=httpx.MockTransport(reject_response),
    )
    author = InboundCharacterAuthor(
        flash_model=model,
        thinking_model=model,
        temperature=0.8,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
        require_explicit_authored_decision_fields=True,
    )
    route = await SemanticComputeRouter(thinking_available=True).route(
        _request_for(RouteHints(severity="high"))
    )
    try:
        with pytest.raises(ValueError):
            await author.propose_stream_head(
                _request(revision=3, call="call:rejected-auto").model_copy(update={"route": route})
            )
    finally:
        await model.aclose()
    boundary.assert_identity(thinking=True)


def test_auto_transport_cannot_change_the_original_schema_function():
    from companion_daemon.world_v2.character_interior.single_tool_transport import (
        resolve_single_tool_transport,
    )
    from types import SimpleNamespace

    with pytest.raises(ValueError, match="expected function"):
        resolve_single_tool_transport(
            provider=SimpleNamespace(single_tool_selection_mode="auto"),
            tools=[{"type": "function", "function": {"name": "allowed"}}],
            tool_choice={"type": "function", "function": {"name": "other"}},
            identity={"tool_name": "allowed", "contract_id": "original-schema"},
        )


@pytest.mark.parametrize("thinking", [False, True])
@pytest.mark.parametrize("phase", ["final", "expression_repair"])
def test_final_and_expression_repair_keep_the_selected_transport(thinking, phase):
    from types import SimpleNamespace
    from companion_daemon.world_v2.character_interior.inbound_wire import (
        _expression_tool_reselection_kwargs,
    )

    provider = SimpleNamespace(
        supports_required_tool_choice=True,
        supports_strict_tool_choice=True,
        single_tool_selection_mode="auto" if thinking else "forced",
    )
    request = _request(revision=3, call="call:transport-followup")
    if phase == "final":
        author = InboundCharacterAuthor(flash_model=provider)
        invocation = author._final_tool_reselection_kwargs(request=request, provider=provider)
    else:
        invocation = _expression_tool_reselection_kwargs(
            request=request,
            provider=provider,
            capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
            stable_identity_source_refs=frozenset(),
            source_ref_aliases=None,
        )
    assert callable(invocation["unwrap_tool_result"])
    name = invocation["tools"][0]["function"]["name"]
    identity = invocation["tool_contract_identity"]
    if thinking:
        assert invocation["tool_choice"] == "auto"
        assert identity["expected_tool_name"] == name
        base = json.loads(identity["schema_contract_identity_json"])
        assert base["tool_name"] == name
    else:
        assert invocation["tool_choice"] == {"type": "function", "function": {"name": name}}
        assert identity["tool_name"] == name
        assert "selection_mode" not in identity
