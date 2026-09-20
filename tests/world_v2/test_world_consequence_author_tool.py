"""New World author wire through the real adapter, with offline HTTP responses."""

import copy
import hashlib
import json
from types import SimpleNamespace

import httpx
from jsonschema import Draft202012Validator, ValidationError
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.life_development_draft import LifeDevelopmentPossibilityDraft
from companion_daemon.world_v2.life_development_model_adapter import RoleBoundLifeDevelopmentModelAdapter
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.world_consequence_author_tool import (
    CONTRACT, TOOL_NAME, bind_world_consequence_author_tool, world_consequence_author_tool_contract,
    recover_world_consequence_author_tool,
)
from test_life_development_runtime import (
    WORLD_ID, _SequenceModel, _novel_origin_review, _source_closure_review, _seed_clock,
)
from test_world_author_request_audit import (
    _ReceivedAuthor, _advance, _audited, _interrupt_before_final, _json, _runtime,
)
from test_world_consequence_producer import _draft, _runtime as _producer_runtime


def _full_propose(wake):
    value = LifeDevelopmentPossibilityDraft.model_validate_json(_json(_draft(wake))).model_dump(mode="json")
    for outcome in value["outcomes"]:
        outcome.pop("text", None)
        outcome["world_consequence"].setdefault("authorized_attempt_result", None)
    return value


def _tool_response(wire, raw):
    return httpx.Response(200, json={
        "id": "offline-world-tool", "model": wire["model"],
        "choices": [{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "world-tool", "type": "function", "function": {
                "name": TOOL_NAME, "arguments": raw,
            }}],
        }}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
    })


def _model(respond, *, thinking=False):
    return DeepSeekChatModel(
        "offline-fixture", "https://fixture.invalid", "deepseek-v4-flash",
        thinking_enabled=thinking, transport=httpx.MockTransport(respond),
    )


def test_strict_schema_retains_execution_union_and_binds_exact_transport():
    contract = world_consequence_author_tool_contract(provider=object())
    schema = contract["tools"][0]["function"]["parameters"]
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate({"replacement": {"decision": "no_op"}})
    forbidden = {"$ref", "$defs", "oneOf", "allOf", "discriminator", "default", "const"}

    def check(node):
        if isinstance(node, dict):
            assert not forbidden.intersection(node)
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(schema)
    propose = schema["properties"]["replacement"]["anyOf"][1]
    consequence = propose["properties"]["outcomes"]["items"]["properties"]["world_consequence"]
    result = consequence["properties"]["authorized_attempt_result"]["anyOf"][0]
    bindings = result["properties"]["execution_binding"]["anyOf"]
    assert [branch["properties"]["source_kind"]["enum"] for branch in bindings] == [
        ["activity_execution"], ["execution_receipt"],
    ]
    for branch in bindings:
        with pytest.raises(ValidationError):
            Draft202012Validator(branch).validate({"source_kind": "activity_execution"})
    original = [{"role": "system", "content": "unchanged"}, {"role": "user", "content": "{}"}]
    before = copy.deepcopy(original)
    bound = bind_world_consequence_author_tool(messages=original, tool_contract=contract)
    assert original == before
    marker = json.loads(bound[1]["content"])["world_author_wire"]
    assert marker["contract"] == CONTRACT
    assert marker["tool_contract_sha256"] == hashlib.sha256(_json(contract).encode()).hexdigest()
    changed = copy.deepcopy(contract)
    changed["tools"][0]["function"]["description"] += " changed"
    assert bind_world_consequence_author_tool(messages=original, tool_contract=changed) != bound
    auto = world_consequence_author_tool_contract(
        provider=SimpleNamespace(single_tool_selection_mode="auto")
    )
    assert auto["tool_choice"] == "auto"
    assert bind_world_consequence_author_tool(messages=original, tool_contract=auto) != bound


def test_source_correction_recovers_exact_wire_or_retains_unmarked_json():
    provider = SimpleNamespace(supports_strict_tool_choice=True, single_tool_selection_mode="forced")
    original = [{"role": "system", "content": "unchanged"}, {"role": "user", "content": "{}"}]
    assert recover_world_consequence_author_tool(messages=original, provider=provider) == {}
    contract = world_consequence_author_tool_contract(provider=provider)
    bound = bind_world_consequence_author_tool(messages=original, tool_contract=contract)
    before = copy.deepcopy(bound)
    assert recover_world_consequence_author_tool(messages=bound, provider=provider) == contract
    assert bound == before
    provider.single_tool_selection_mode = "auto"
    with pytest.raises(ValueError, match="identity changed"):
        recover_world_consequence_author_tool(messages=bound, provider=provider)
    provider.single_tool_selection_mode = "forced"
    changed = json.loads(bound[1]["content"])
    changed["world_author_wire"]["tool_contract_sha256"] = "0" * 64
    bound[1]["content"] = _json(changed)
    with pytest.raises(ValueError, match="identity changed"):
        recover_world_consequence_author_tool(messages=bound, provider=provider)
    provider.supports_strict_tool_choice = False
    with pytest.raises(ValueError, match="unavailable"):
        recover_world_consequence_author_tool(messages=before, provider=provider)


@pytest.mark.asyncio
@pytest.mark.parametrize("strict_original", [False, True])
@pytest.mark.parametrize("rewrite_decision", ["no_op", "propose"])
async def test_source_rewrite_keeps_original_wire_and_review_permissions(
    tmp_path, strict_original, rewrite_decision,
):
    path = tmp_path / "rewrite.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    corrected = _full_propose(wake)
    rejected = copy.deepcopy(corrected)
    fragment = "她及时收回了手账。"
    rejected["outcomes"][0]["world_consequence"]["environment_text"] += fragment
    requests = []

    def respond(request):
        wire = json.loads(request.content)
        requests.append(wire)
        authored = (rejected if len(requests) == 1 else
                    corrected if rewrite_decision == "propose" else {"decision": "no_op"})
        # The provider may gain capability since an old pin was recorded. Its
        # old JSON carrier must still be used on the correction request.
        provider.supports_strict_tool_choice = True
        if strict_original:
            assert request.url.path == "/beta/chat/completions"
            assert "response_format" not in wire
            assert wire["tools"] == requests[0]["tools"]
            assert wire["tool_choice"] == requests[0]["tool_choice"]
            raw = _json({"replacement": authored})
            Draft202012Validator(wire["tools"][0]["function"]["parameters"]).validate(json.loads(raw))
            return _tool_response(wire, raw)
        assert request.url.path == "/chat/completions"
        assert wire["response_format"] == {"type": "json_object"}
        assert "tools" not in wire and "tool_choice" not in wire
        return httpx.Response(200, json={"choices": [{"message": {"content": _json(authored)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300}})

    provider = _model(respond)
    provider.supports_strict_tool_choice = strict_original
    general = _SequenceModel(model="fixture:general", outputs=tuple(
        _source_closure_review(decision="supported") for _ in range(2)))
    focused = _SequenceModel(model="fixture:focused", outputs=(
        _novel_origin_review(decision="unsupported", unsupported_outcome_prerequisites=({
            "prose_path": "outcomes.0.world_consequence.environment_text",
            "violation_kinds": ["character_interior_authorship"], "exact_fragments": [fragment],
        },)), _novel_origin_review(decision="supported")))
    try:
        runtime = _producer_runtime(ledger, store, wake, provider, general, focused)
        result = await _advance(runtime, wake)
        assert result.status == ("no_op" if rewrite_decision == "no_op" else "occurrence_committed")
        assert len(requests) == 2
        assert general.calls == focused.calls == (1 if rewrite_decision == "no_op" else 2)
        assert requests[1]["messages"][:-2] == requests[0]["messages"]
        original_user = json.loads(requests[0]["messages"][1]["content"])
        assert ("world_author_wire" in original_user) is strict_original
        for wire in requests:
            raw_messages = _json(wire["messages"])
            digest = hashlib.sha256(raw_messages.encode()).hexdigest()
            assert store.read_exact(content_ref="content:world-author-request:" + digest).text == raw_messages
        before = ledger.export_replay_evidence()
        repeated = await _advance(runtime, wake)
        assert repeated.proposal_event_ref == result.proposal_event_ref
        assert ledger.export_replay_evidence() == before and len(requests) == 2
    finally:
        await provider.aclose()
        store.close()
        ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["no_op", "propose"])
@pytest.mark.parametrize("thinking", [False, True])
async def test_new_wire_reaches_adapter_and_unchanged_acceptance(tmp_path, decision, thinking):
    path = tmp_path / "world.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    value = {"decision": "no_op"} if decision == "no_op" else _full_propose(wake)
    raw = _json({"replacement": value})
    requests = []

    def respond(request):
        wire = json.loads(request.content)
        requests.append(wire)
        assert request.url.path == "/beta/chat/completions"
        assert "response_format" not in wire
        assert wire["tool_choice"] == (
            "auto" if thinking else {"type": "function", "function": {"name": TOOL_NAME}}
        )
        function = wire["tools"][0]["function"]
        assert function["strict"] is True
        Draft202012Validator(function["parameters"]).validate(json.loads(raw))
        digest = hashlib.sha256(_json(wire["messages"]).encode()).hexdigest()
        stored = store.read_exact(content_ref="content:world-author-request:" + digest)
        assert stored.text == _json(wire["messages"])
        marker = json.loads(wire["messages"][1]["content"])["world_author_wire"]
        transport = {"tools": wire["tools"], "tool_choice": wire["tool_choice"]}
        assert marker["tool_contract_sha256"] == hashlib.sha256(_json(transport).encode()).hexdigest()
        return _tool_response(wire, raw)

    provider = _model(respond, thinking=thinking)
    adapter = RoleBoundLifeDevelopmentModelAdapter(model=provider, role="world_author")
    general = _SequenceModel(model="fixture:general", outputs=(_source_closure_review(decision="supported"),))
    focused = _SequenceModel(model="fixture:focused", outputs=(_novel_origin_review(decision="supported"),))
    try:
        runtime = _producer_runtime(ledger, store, wake, adapter, general, focused)
        result = await _advance(runtime, wake)
        assert result.status == ("no_op" if decision == "no_op" else "occurrence_committed")
        assert len(requests) == 1
        assert general.calls == focused.calls == int(decision == "propose")
        assert len(ledger.project().world_occurrences) == int(decision == "propose")
        before = ledger.project()
        repeated = await _advance(runtime, wake)
        assert repeated.proposal_event_ref == result.proposal_event_ref
        assert ledger.project() == before
        assert len(requests) == 1
    finally:
        await provider.aclose()
        store.close()
        ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_count", [1, 2])
async def test_invalid_json_is_preserved_and_only_the_author_can_choose_no_op(tmp_path, invalid_count):
    path = tmp_path / "corrective.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    # Same invalid_json class as trial08: a missing object-closing brace.
    valid = _json({"replacement": _full_propose(wake)})
    position = valid.rfind("}")
    malformed = valid[:position] + valid[position + 1:]
    requests = []

    def respond(request):
        wire = json.loads(request.content)
        requests.append(wire)
        return _tool_response(
            wire, malformed if len(requests) <= invalid_count
            else '{"replacement":{"decision":"no_op"}}',
        )

    provider = _model(respond)
    adapter = RoleBoundLifeDevelopmentModelAdapter(model=provider, role="world_author")
    try:
        result = await _advance(_runtime(ledger, store, wake, adapter), wake)
        assert result.status == ("no_op" if invalid_count == 1 else "technical_failure")
        assert len(requests) == 2
        assert requests[1]["messages"][:2] == requests[0]["messages"]
        assert requests[1]["tools"] == requests[0]["tools"]
        correction = json.loads(requests[1]["messages"][-1]["content"])
        assert correction["validation_failure"]["code"] == "invalid_json"
        if invalid_count == 1:
            metadata, _ = _audited(ledger)
            assert len(metadata["request_bindings"]) == 2
        attempts = sorted(ledger.project().model_result_audits, key=lambda item: item.attempt_index)
        audits = [json.loads(item.audit_json) for item in attempts]
        assert [item["status"] for item in audits] == [
            "main_invalid", "main_invalid_recovered" if invalid_count == 1 else "recovery_failed",
        ]
        assert audits[0]["response_hash"] == hashlib.sha256(malformed.encode()).hexdigest()
        assert store.read_exact(content_ref=audits[0]["response_storage"]["content_ref"]).text == malformed
        assert not ledger.project().world_occurrences
    finally:
        await provider.aclose()
        store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_old_world2_saved_request_recovers_without_new_tool_or_bytes(tmp_path, monkeypatch):
    path = tmp_path / "legacy.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    old = _ReceivedAuthor(store, ('{"decision":"no_op"}',))
    await _interrupt_before_final(ledger, _runtime(ledger, store, wake, old), wake, monkeypatch)
    metadata, _ = _audited(ledger)
    original = store.read_exact(content_ref=metadata["request_bindings"][0]["content_ref"])
    assert "world_author_wire" not in json.loads(json.loads(original.text)[1]["content"])
    store.close()
    ledger.close()
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)

    def unexpected(request):
        raise AssertionError("old pinned response must recover without a new tool request")

    provider = _model(unexpected)
    try:
        result = await _advance(_runtime(ledger, store, wake, provider), wake)
        assert result.status == "no_op"
        assert store.read_exact(content_ref=original.content_ref) == original
        assert _audited(ledger)[0]["request_bindings"] == metadata["request_bindings"]
    finally:
        await provider.aclose()
        store.close()
        ledger.close()
