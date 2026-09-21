"""Appraisal strict transport preserves choices, rejected bytes and old replay."""

import json

import httpx
from jsonschema import Draft202012Validator, ValidationError
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
    _world_stimulus_strict_v3_schema,
)
from test_character_interior_structured_role import _request
from test_world_stimulus_experience_codec import SOURCE, _manifest
import test_life_source_view as life_fixture


def _contract(dialect="standard"):
    return StructuredRoleToolContracts().world_stimulus_appraisal(
        capability_payload=_manifest().payload, recall_allowed=True,
        source_tokens=(("s0", SOURCE),), schema_dialect=dialect,
    )


def test_old_v1_v2_identities_are_frozen_and_strict_v3_is_separate():
    old = _contract()
    assert old.identity.schema_sha256 == "sha256:4f91274feb0d8f5b9402733e30917d09666b16c5e4a9fbefbb3177ab04801ff2"
    assert old.identity.contract_sha256 == "sha256:1a9df0274f004656ecb170276e96b114f67f19ecd57abe36d86b9746333a3372"
    assert old.identity.version == "1"
    assert "strict" not in old.provider_tools[0]["function"]
    strict = _contract("deepseek-strict")
    assert strict.identity.version == "2"
    assert strict.identity.schema_sha256 == "sha256:d6cc070e3d1a568173c79c700856eaed7f0e57944974be82ce01d4f7f5d02ef3"
    assert strict.identity.contract_sha256 == "sha256:fa0c1bf58a2034404dd475de5f3ebf750ff422efe8d4983a26c8a0f8481d5e73"
    assert strict.identity.contract_sha256 != old.identity.contract_sha256
    assert strict.provider_tools[0]["function"]["strict"] is True
    assert strict.identity.tool_name == "character_role_world_stimulus_appraisal_v2"
    current = _contract("deepseek-strict-v3")
    assert current.identity.version == "3"
    assert current.identity.tool_name == "character_role_world_stimulus_appraisal_v3"
    assert current.identity.contract_sha256 != strict.identity.contract_sha256


def test_strict_schema_is_closed_and_wrapper_never_repairs_json():
    contract = _contract("deepseek-strict-v3")
    schema = contract.provider_tools[0]["function"]["parameters"]

    def check(node):
        if isinstance(node, dict):
            assert not {
                "minItems", "maxItems", "minLength", "maxLength", "oneOf", "discriminator", "contains",
            } & node.keys()
            if node.get("type") == "object":
                assert node.get("properties"), "DeepSeek rejects an object with no properties"
                assert set(node["required"]) == set(node["properties"])
                assert node["additionalProperties"] is False
            if "anyOf" in node:
                assert all("type" in branch for branch in node["anyOf"])
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


def test_strict_v3_preserves_all_four_affect_operations_without_open_json():
    schema = _contract("deepseek-strict-v3").provider_tools[0]["function"]["parameters"]
    affect = schema["properties"]["result"]["anyOf"][0]["properties"]["proposals"]["items"]["properties"]["affect_transition"]
    validator = Draft202012Validator(affect)
    component = {"dimension": "warmth", "target_intensity_bp": 2500}
    values = [
        {"operation": "open", "component_targets": [component]},
        {"operation": "update", "episode_id": "affect:existing", "component_targets": [
            {**component, "component_id": "component:existing"},
        ]},
        {"operation": "resolve", "episode_id": "affect:existing", "resolution_summary": "暂告一段落。"},
        {"operation": "supersede", "episode_id": "affect:existing", "component_targets": [component]},
    ]
    for value in [None, *values]:
        validator.validate(value)
    for bad in ({}, {"operation": "invented"}, {**values[0], "new_authority": True}):
        with pytest.raises(ValidationError):
            validator.validate(bad)


def test_v3_flattens_only_pure_nested_unions_from_the_rejected_affect_shape():
    # Actual first v3 probe: affect_transition.anyOf[0] had no direct type.
    branches = [
        {"type": "object", "properties": {"operation": {"const": operation}},
         "required": ["operation"], "additionalProperties": False}
        for operation in ("open", "update", "resolve", "supersede")
    ]
    nullable = {"anyOf": [{"anyOf": branches}, {"type": "null"}]}
    assert _world_stimulus_strict_v3_schema(nullable) == {
        "anyOf": [*branches, {"type": "null"}],
    }
    # A branch carrying another constraint is not a pure union container.
    constrained = {"anyOf": [{"anyOf": branches, "required": ["operation"]}, {"type": "null"}]}
    assert _world_stimulus_strict_v3_schema(constrained) == constrained


@pytest.mark.asyncio
async def test_recorded_v2_empty_object_400_is_preserved_by_real_http_adapter(monkeypatch):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    # Exact body from c00ecfa0f283432ab7784effda92a685; no model output exists.
    error = b'{"error":{"message":"An object with no properties is not allowed.","type":"invalid_request_error","param":null,"code":"invalid_request_error"}}'
    old = _contract("deepseek-strict")
    calls = []

    def respond(request):
        calls.append(request)
        wire = json.loads(request.content)
        assert request.url.path == "/beta/chat/completions"
        assert wire["tools"] == list(old.provider_tools)
        result = wire["tools"][0]["function"]["parameters"]["properties"]["result"]
        assert set(result) == {"type", "anyOf"} and result["type"] == "object"
        return httpx.Response(400, content=error, headers={"content-type": "application/json"})

    model = DeepSeekChatModel(
        "offline-fixture", "https://api.deepseek.com", "deepseek-v4-flash",
        thinking_enabled=False, transport=httpx.MockTransport(respond),
    )
    try:
        with pytest.raises(httpx.HTTPStatusError) as raised:
            await model.complete_json(
                [{"role": "user", "content": "Return one JSON role result."}],
                tools=list(old.provider_tools), tool_choice=old.provider_tool_choice,
            )
        assert raised.value.response.content == error
        assert len(calls) == 1
    finally:
        await model.aclose()


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
            if body["tools"][0]["function"]["name"] == "character_role_world_stimulus_appraisal_v3":
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
@pytest.mark.parametrize("dialect", ["standard", "deepseek-strict"])
async def test_saved_v1_v2_preparation_restores_without_recompiling_tool(tmp_path, monkeypatch, dialect):
    compile_tool = StructuredRoleToolContracts.world_stimulus_appraisal
    with monkeypatch.context() as legacy:
        legacy.setattr(
            StructuredRoleToolContracts, "world_stimulus_appraisal",
            lambda self, **kwargs: compile_tool(self, **{**kwargs, "schema_dialect": dialect}),
        )
        payload, request, provider = await life_fixture._prepared(tmp_path, legacy)
    old_wire = provider.stimulus_requests[0]
    version = "1" if dialect == "standard" else "2"
    assert old_wire["tools"][0]["function"]["name"].endswith("_v" + version)
    assert (old_wire["tools"][0]["function"].get("strict") is True) == (version == "2")

    def no_compile(*args, **kwargs):
        raise AssertionError("saved role preparation must use its saved provider controls")

    monkeypatch.setattr(StructuredRoleToolContracts, "world_stimulus_appraisal", no_compile)
    result, snapshot, _, _ = _restore_prepared_turn(canonical(payload), purpose="world_stimulus_appraisal")
    view = result.life_source_view
    assert view.verify_request(request) == view
    assert view.verify_snapshot(snapshot) == view
    assert json.loads(view.provider_controls_json)["tools"] == old_wire["tools"]
    assert json.loads(view.messages_json) == old_wire["messages"]
