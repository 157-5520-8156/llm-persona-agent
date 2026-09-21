"""Explicit World JSON transport through the real provider adapter, offline."""

import copy
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.life_development_model_adapter import RoleBoundLifeDevelopmentModelAdapter
from companion_daemon.world_v2.life_development_runtime import LifeDevelopmentRuntime
from companion_daemon.world_v2.production_turn_application import WorldV2TurnApplicationConfig
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.world_consequence_author_tool import (
    recover_world_consequence_author_tool, world_consequence_author_tool_contract,
)
from test_life_development_runtime import (
    OWNER, WORLD_ID, _PinnedCapsuleCompiler, _SequenceModel,
    _novel_origin_review, _source_closure_review, _seed_clock,
)
from test_longitudinal_cli import _cli
from test_world_author_request_audit import _CurrentManifest, _advance, _json
from test_world_consequence_author_tool import _full_propose, _model, _tool_response


def _runtime(ledger, store, wake, author, *, transport, general=None, focused=None):
    return LifeDevelopmentRuntime(
        ledger=ledger, content_store=store, world_author=author,
        world_author_transport=transport,
        character_interior=_SequenceModel(model="fixture:unused-character", outputs=()),
        source_closure_reviewer=general, novel_origin_critic=focused,
        capsule_compiler=_PinnedCapsuleCompiler(ledger=ledger),
        capability_manifest_compiler=_CurrentManifest(wake=wake), owner_actor_ref=OWNER,
    )


def _json_response(value):
    return httpx.Response(200, json={
        "choices": [{"message": {"content": _json(value)}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["no_op", "propose"])
async def test_new_json_request_is_stored_before_plain_json_http(tmp_path, decision):
    path = tmp_path / "world.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    value = {"decision": "no_op"} if decision == "no_op" else _full_propose(wake)
    requests = []

    def respond(request):
        wire = json.loads(request.content)
        requests.append(wire)
        assert request.url.path == "/chat/completions"
        assert wire["response_format"] == {"type": "json_object"}
        assert "tools" not in wire and "tool_choice" not in wire
        user = json.loads(wire["messages"][1]["content"])
        assert user["world_author_json_transport"] == "json_object"
        assert "world_author_wire" not in user
        assert user["output_contract"] and user["cross_field_authority"]
        raw = _json(wire["messages"])
        digest = hashlib.sha256(raw.encode()).hexdigest()
        assert store.read_exact(content_ref="content:world-author-request:" + digest).text == raw
        return _json_response(value)

    provider = _model(respond)
    adapter = RoleBoundLifeDevelopmentModelAdapter(model=provider, role="world_author")
    try:
        runtime = _runtime(ledger, store, wake, adapter, transport="json_object",
            general=_SequenceModel(model="fixture:general", outputs=(_source_closure_review(decision="supported"),)),
            focused=_SequenceModel(model="fixture:focused", outputs=(_novel_origin_review(decision="supported"),)))
        result = await _advance(runtime, wake)
        assert result.status == ("no_op" if decision == "no_op" else "occurrence_committed")
        assert len(requests) == 1 and provider.supports_strict_tool_choice is True
    finally:
        await provider.aclose()
        store.close()
        ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["auto", "json_object"])
async def test_same_author_source_rewrite_keeps_saved_transport(tmp_path, monkeypatch, transport):
    monkeypatch.setattr(
        "companion_daemon.world_v2.life_development_runtime.world_consequence_author_tool_contract",
        lambda *, provider: world_consequence_author_tool_contract(
            provider=provider, contract_id="world-consequence-author-tool.2"),
    )
    path = tmp_path / "rewrite.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    rejected = copy.deepcopy(_full_propose(wake))
    fragment = "她及时收回了手账。"
    rejected["outcomes"][0]["world_consequence"]["environment_text"] += fragment
    requests = []

    def respond(request):
        wire = json.loads(request.content)
        requests.append(wire)
        runtime._world_author_transport = "auto" if transport == "json_object" else "json_object"
        value = rejected if len(requests) == 1 else {"decision": "no_op"}
        if transport == "auto":
            assert request.url.path == "/beta/chat/completions"
            assert wire["tools"] == requests[0]["tools"]
            assert wire["tool_choice"] == requests[0]["tool_choice"]
            return _tool_response(wire, _json({"replacement": value}))
        assert request.url.path == "/chat/completions"
        assert wire["response_format"] == {"type": "json_object"}
        assert "tools" not in wire
        return _json_response(value)

    provider = _model(respond)
    try:
        runtime = _runtime(ledger, store, wake, provider, transport=transport,
            general=_SequenceModel(model="fixture:general", outputs=(_source_closure_review(decision="supported"),)),
            focused=_SequenceModel(model="fixture:focused", outputs=(
                _novel_origin_review(decision="unsupported", unsupported_outcome_prerequisites=({
                    "prose_path": "outcomes.0.world_consequence.environment_text",
                    "violation_kinds": ["character_interior_authorship"], "exact_fragments": [fragment],
                },)),)))
        result = await _advance(runtime, wake)
        assert result.status == "no_op" and len(requests) == 2
        assert requests[1]["messages"][:-2] == requests[0]["messages"]
    finally:
        await provider.aclose()
        store.close()
        ledger.close()


@pytest.mark.parametrize("wrapper", ["plain", "role_adapter", "origin_only"])
def test_explicit_json_rejects_plain_completion_including_wrapped_recovery(wrapper):
    plain = _SequenceModel(model="fixture:plain", outputs=())
    if wrapper == "role_adapter":
        wrapped = RoleBoundLifeDevelopmentModelAdapter(model=plain, role="world_author")
    elif wrapper == "origin_only":
        # A provider origin cannot make an outer plain-completion wrapper JSON-capable.
        wrapped = SimpleNamespace(
            authority_origin=SimpleNamespace(complete_json=plain.complete),
            complete=plain.complete,
        )
    else:
        wrapped = plain
    with pytest.raises(TypeError, match="requires complete_json"):
        _runtime(None, None, None, wrapped, transport="json_object")
    messages = [{"role": "system", "content": "json"}, {"role": "user", "content": _json({
        "world_author_json_transport": "json_object",
    })}]
    with pytest.raises(TypeError, match="JSON transport is unavailable"):
        recover_world_consequence_author_tool(messages=messages, provider=wrapped)
    messages[1]["content"] = "{}"
    assert recover_world_consequence_author_tool(messages=messages, provider=wrapped) == {}


def test_config_and_cli_keep_auto_default_and_accept_explicit_json(tmp_path):
    options = ["--output", str(tmp_path / "audit")]
    assert _cli().parse_options(options).world_author_transport == "auto"
    assert _cli().parse_options(options + ["--world-author-transport", "json_object"]).world_author_transport == "json_object"
    config = dict(world_id=WORLD_ID, companion_actor_ref=OWNER, reply_target="target", action_pump_owner="pump")
    assert WorldV2TurnApplicationConfig(**config).world_author_transport == "auto"
    assert WorldV2TurnApplicationConfig(**config, world_author_transport="json_object").world_author_transport == "json_object"
    with pytest.raises(ValueError, match="world author transport"):
        WorldV2TurnApplicationConfig(**config, world_author_transport="unsupported")
