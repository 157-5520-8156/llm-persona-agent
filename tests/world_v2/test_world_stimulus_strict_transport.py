"""Appraisal strict transport preserves choices, rejected bytes and old replay."""

import json

import httpx
from jsonschema import Draft202012Validator
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.character_interior.core import _restore_prepared_turn
from companion_daemon.world_v2.character_interior.life_source_origin import canonical
from companion_daemon.world_v2.character_interior.structured_role import (
    StructuredCharacterRoleFaculty,
    StructuredRoleResultError,
)
from companion_daemon.world_v2.character_interior.structured_role_tool_contract import (
    StructuredRoleToolContracts,
)
from test_character_interior_structured_role import _request
from test_world_stimulus_experience_codec import SOURCE, _manifest
import test_life_source_view as life_fixture


def _contract(dialect="standard"):
    return StructuredRoleToolContracts().world_stimulus_appraisal(
        capability_payload=_manifest().payload, recall_allowed=True,
        source_tokens=(("s0", SOURCE),), schema_dialect=dialect,
    )


def test_standard_v1_identity_is_frozen_and_strict_v2_is_separate():
    old = _contract()
    assert old.identity.schema_sha256 == "sha256:4f91274feb0d8f5b9402733e30917d09666b16c5e4a9fbefbb3177ab04801ff2"
    assert old.identity.contract_sha256 == "sha256:1a9df0274f004656ecb170276e96b114f67f19ecd57abe36d86b9746333a3372"
    assert old.identity.version == "1"
    assert "strict" not in old.provider_tools[0]["function"]
    strict = _contract("deepseek-strict")
    assert strict.identity.version == "2"
    assert strict.identity.contract_sha256 != old.identity.contract_sha256
    assert strict.provider_tools[0]["function"]["strict"] is True
    assert strict.identity.tool_name == "character_role_world_stimulus_appraisal_v2"


def test_strict_schema_is_closed_and_wrapper_never_repairs_json():
    contract = _contract("deepseek-strict")
    schema = contract.provider_tools[0]["function"]["parameters"]

    def check(node):
        if isinstance(node, dict):
            assert not {"minItems", "maxItems", "minLength", "maxLength", "oneOf"} & node.keys()
            if node.get("type") == "object" and "properties" in node:
                assert set(node["required"]) == set(node["properties"])
                assert node["additionalProperties"] is False
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(schema)
    role = {
        "status": "recall_request", "summary": '我想起他说的"留下来"。',
        "attended_source_refs": [], "decision": None, "recall_query": "那句话", "proposals": [],
    }
    wrapped = {"result": role}
    Draft202012Validator(schema).validate(wrapped)
    raw = json.dumps(wrapped, ensure_ascii=False)
    assert json.loads(contract.unwrap(raw)) == role
    for bad in (raw.replace('\\"留下来\\"', '"留下来"'), raw + "}", "```json\n" + raw + "\n```", json.dumps(role)):
        with pytest.raises(ValueError):
            contract.unwrap(bad)


@pytest.mark.asyncio
async def test_invalid_native_quotes_stay_exact_and_fail_closed(monkeypatch):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    raw = '{"result":{"status":"transition","summary":"他说的"留下来"。"}}'
    calls = []

    def respond(request):
        wire = json.loads(request.content)
        calls.append((str(request.url), wire))
        return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
            "tool_calls": [{"type": "function", "function": {
                "name": wire["tools"][0]["function"]["name"], "arguments": raw,
            }}],
        }}]})

    model = DeepSeekChatModel(
        "offline-fixture", "https://api.deepseek.com", "deepseek-v4-flash",
        thinking_enabled=False, transport=httpx.MockTransport(respond),
    )
    try:
        role = StructuredCharacterRoleFaculty(model=model, model_id=model.model)
        request = await _request(
            phase="experience", purpose="world_stimulus_appraisal", capability_manifest=_manifest(),
        )
        with pytest.raises(StructuredRoleResultError) as raised:
            await role.experience(request)
    finally:
        await model.aclose()
    assert len(calls) == 1
    url, wire = calls[0]
    assert url == "https://api.deepseek.com/beta/chat/completions"
    assert wire["tools"][0]["function"]["strict"] is True
    assert raised.value.rejected_raw == raw
    assert raised.value.rejected_role_result.raw_result == raw


@pytest.mark.asyncio
async def test_strict_correction_keeps_raw_candidate_and_durable_actual_request(tmp_path, monkeypatch):
    original_provider = life_fixture._ResponseHTTP
    urls, originals = [], []

    class CapturingProvider(original_provider):
        async def __call__(self, request):
            response = await super().__call__(request)
            body = json.loads(request.content)
            if body["tools"][0]["function"]["name"] == "character_role_world_stimulus_appraisal_v2":
                urls.append(str(request.url))
                originals.append(response.json()["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"])
            return response

    monkeypatch.setattr(life_fixture, "_ResponseHTTP", CapturingProvider)
    payload, request, provider = await life_fixture._prepared(tmp_path, monkeypatch, fault="missing_once")
    result, snapshot, _, _ = _restore_prepared_turn(canonical(payload), purpose="world_stimulus_appraisal")
    view = result.life_source_view
    assert view.verify_request(request) == view
    assert len(provider.stimulus_requests) == len(urls) == 2
    assert all(url.endswith("/beta/chat/completions") for url in urls)
    first, second = provider.stimulus_requests
    assert first["tools"] == second["tools"]
    assert first["tools"][0]["function"]["strict"] is True
    correction = json.loads(second["messages"][-1]["content"])["correction"]
    assert correction["rejected_role_result"]["raw_result"] == originals[0]
    Draft202012Validator(second["tools"][0]["function"]["parameters"]).validate(json.loads(originals[1]))
    assert json.loads(view.provider_controls_json)["tools"] == second["tools"]
    assert json.loads(view.messages_json) == second["messages"]
    assert view.verify_snapshot(snapshot) == view


@pytest.mark.asyncio
async def test_saved_standard_v1_preparation_restores_without_recompiling_tool(tmp_path, monkeypatch):
    with monkeypatch.context() as legacy:
        legacy.setattr(DeepSeekChatModel, "supports_strict_tool_choice", False)
        payload, request, provider = await life_fixture._prepared(tmp_path, legacy)
    old_wire = provider.stimulus_requests[0]
    assert old_wire["tools"][0]["function"]["name"].endswith("_v1")
    assert "strict" not in old_wire["tools"][0]["function"]

    def no_compile(*args, **kwargs):
        raise AssertionError("saved role preparation must use its saved provider controls")

    monkeypatch.setattr(StructuredRoleToolContracts, "world_stimulus_appraisal", no_compile)
    result, snapshot, _, _ = _restore_prepared_turn(canonical(payload), purpose="world_stimulus_appraisal")
    view = result.life_source_view
    assert view.verify_request(request) == view
    assert view.verify_snapshot(snapshot) == view
    assert json.loads(view.provider_controls_json)["tools"] == old_wire["tools"]
    assert json.loads(view.messages_json) == old_wire["messages"]
