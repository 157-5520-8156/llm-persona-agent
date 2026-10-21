"""New World author wire through the real adapter, with offline HTTP responses."""

import copy
import hashlib
import json
from itertools import product
from types import SimpleNamespace

import httpx
from jsonschema import Draft202012Validator, ValidationError
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.life_development_draft import (
    ORDINARY_LIFE_PHOTO_PRIVACY, LifeDevelopmentPossibilityDraft,
    LifeDevelopmentVisualEnvironmentDraft,
)
from companion_daemon.world_v2.character_interior.local_schema_references import (
    expand_local_schema_references,
)
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


@pytest.mark.asyncio
@pytest.mark.parametrize("carrier", ["json_object", "json_schema"])
async def test_text_author_structure_correction_preserves_failure_and_same_carrier(tmp_path, carrier):
    from companion_daemon.subscription_usage import BackgroundGLMModel, SubscriptionTokenLedger

    path = tmp_path / "structure.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    requests = []
    rejected = _json({"replacement": {"decision": "no_op", "unoffered_extra": True}})

    def respond(request):
        wire = json.loads(request.content)
        requests.append(wire)
        assert wire["response_format"]["type"] == carrier and "tools" not in wire
        raw = rejected if len(requests) == 1 else _json({"replacement": {"decision": "no_op"}})
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": raw}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}})

    provider = BackgroundGLMModel("fixture", "https://fixture.invalid/v1", "GLM-5.3-Flash",
        token_ledger=SubscriptionTokenLedger(tmp_path / "usage.sqlite"), tool_result_carrier=carrier,
        transport=httpx.MockTransport(respond))
    try:
        result = await _advance(_runtime(ledger, store, wake, provider), wake)
        assert result.status == "no_op" and len(requests) == 2
        assistant, correction = requests[1]["messages"][-3:-1]
        assert assistant == {"role": "assistant", "content": rejected}
        assert correction["role"] == "user"
        assert "do not return it as plain assistant text" not in correction["content"]
        assert requests[1]["messages"][:-3] == requests[0]["messages"][:-1]
        assert requests[1]["messages"][-1] == requests[0]["messages"][-1]
        failures = [json.loads(a.audit_json) for a in ledger.project().model_result_audits]
        failed = next(a for a in failures if a["status"] == "main_invalid")
        assert store.read_exact(content_ref=failed["response_storage"]["content_ref"]).text == rejected
        assert not ledger.project().world_occurrences
    finally:
        await provider.aclose()
        store.close()
        ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("carrier", ["json_object", "json_schema"])
async def test_text_author_paid_pin_survives_cold_reopen(tmp_path, monkeypatch, carrier):
    from companion_daemon.subscription_usage import BackgroundGLMModel, SubscriptionTokenLedger

    path = tmp_path / "paid-text.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        assert len(requests) == 1, "paid result must recover without another request"
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": _json({"replacement": {"decision": "no_op"}})}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}})

    def model(mode=carrier):
        return BackgroundGLMModel("fixture", "https://fixture.invalid/v1", "GLM-5.3-Flash",
            token_ledger=SubscriptionTokenLedger(tmp_path / "usage.sqlite"), tool_result_carrier=mode,
            transport=httpx.MockTransport(respond))

    provider = model()
    await _interrupt_before_final(ledger, _runtime(ledger, store, wake, provider), wake, monkeypatch)
    metadata, _ = _audited(ledger)
    original = store.read_exact(content_ref=metadata["request_bindings"][0]["content_ref"])
    marker = json.loads(json.loads(original.text)[1]["content"])["world_author_wire"]
    assert marker["response_carrier"] == carrier
    assert marker["arguments_contract"] == "exact_replacement_envelope_of_response_schema"
    await provider.aclose()
    store.close()
    ledger.close()
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    provider = model("json_schema")
    try:
        result = await _advance(_runtime(ledger, store, wake, provider), wake)
        assert result.status == "no_op" and len(requests) == 1
        assert store.read_exact(content_ref=original.content_ref) == original
        assert _audited(ledger)[0]["request_bindings"] == metadata["request_bindings"]
    finally:
        await provider.aclose()
        store.close()
        ledger.close()


def _tool_response(wire, raw):
    return httpx.Response(200, json={
        "id": "offline-world-tool", "model": wire["model"],
        "choices": [{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "world-tool", "type": "function", "function": {
                "name": wire["tools"][0]["function"]["name"], "arguments": raw,
            }}],
        }}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
    })


def _model(respond, *, thinking=False):
    return DeepSeekChatModel(
        "offline-fixture", "https://fixture.invalid", "deepseek-v4-flash",
        thinking_enabled=thinking, transport=httpx.MockTransport(respond),
    )


@pytest.mark.parametrize("contract_id", [f"world-consequence-author-tool.{i}" for i in range(1, 6)])
def test_static_tool_cache_preserves_fresh_schema_and_provider_changes(contract_id):
    from companion_daemon.world_v2.world_consequence_author_tool import _static_author_tools

    provider = SimpleNamespace(single_tool_selection_mode="forced")
    fresh_tools = _static_author_tools.__wrapped__(contract_id)
    first = world_consequence_author_tool_contract(provider=provider, contract_id=contract_id)
    assert first["tools"] == fresh_tools
    first["tools"][0]["function"]["parameters"].clear()
    first["tools"].clear()
    provider.single_tool_selection_mode = "auto"
    second = world_consequence_author_tool_contract(provider=provider, contract_id=contract_id)
    assert second["tools"] == fresh_tools
    assert second["tool_choice"] == "auto"
    provider.single_tool_selection_mode = "forced"
    third = world_consequence_author_tool_contract(provider=provider, contract_id=contract_id)
    assert third["tools"] == fresh_tools
    assert third["tool_choice"]["function"]["name"] == fresh_tools[0]["function"]["name"]
    provider.single_tool_selection_mode = "unsupported"
    with pytest.raises(ValueError, match="selection mode"):
        world_consequence_author_tool_contract(provider=provider, contract_id=contract_id)


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

    expanded = expand_local_schema_references(schema)
    check(expanded)
    assert len(_json(schema)) < len(_json(expanded))
    # Complete nonempty-environment alternatives add under 2 KB to tool.2.
    assert len(_json(contract).encode()) < 18_500
    propose = expanded["properties"]["replacement"]["anyOf"][2]
    outcome = propose["properties"]["outcomes"]["items"]["anyOf"][0]
    consequence = outcome["properties"]["world_consequence"]
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


@pytest.mark.parametrize("mode,expected_hash", [
    ("forced", "1a2630b9f816324c8a9b5ce03313c2bf52eadf9d78f9c4d084dbbc2b58b9b65b"),
    ("auto", "59f1135c574afc6d9802a0d0eef18a6b32be9eb65e3acbc2ce19b2575c29c932"),
])
def test_tool1_frozen_wire_recovers_without_upgrading(mode, expected_hash):
    provider = SimpleNamespace(supports_strict_tool_choice=True, single_tool_selection_mode=mode)
    contract = world_consequence_author_tool_contract(
        provider=provider, contract_id="world-consequence-author-tool.1",
    )
    assert hashlib.sha256(_json(contract).encode()).hexdigest() == expected_hash
    original = [{"role": "system", "content": "unchanged"}, {"role": "user", "content": "{}"}]
    pinned = bind_world_consequence_author_tool(messages=original, tool_contract=contract)
    assert json.loads(pinned[1]["content"])["world_author_wire"]["contract"] == "world-consequence-author-tool.1"
    assert recover_world_consequence_author_tool(messages=pinned, provider=provider) == contract
    assert contract != world_consequence_author_tool_contract(provider=provider)


@pytest.mark.parametrize("mode,expected_hash", [
    ("forced", "d227508f9891be5e4fac1ce5b89086f6166e5f16e584daba971ef0cc072863c3"),
    ("auto", "c181c3a41e7f50b44cba35013d16129e97072b1ff57026a443fe68afcc8bf6b2"),
])
def test_tool2_frozen_wire_recovers_without_upgrading(mode, expected_hash):
    provider = SimpleNamespace(supports_strict_tool_choice=True, single_tool_selection_mode=mode)
    contract = world_consequence_author_tool_contract(
        provider=provider, contract_id="world-consequence-author-tool.2",
    )
    assert hashlib.sha256(_json(contract).encode()).hexdigest() == expected_hash
    messages = [{"role": "system", "content": "unchanged"}, {"role": "user", "content": "{}"}]
    pinned = bind_world_consequence_author_tool(messages=messages, tool_contract=contract)
    assert recover_world_consequence_author_tool(messages=pinned, provider=provider) == contract
    assert contract != world_consequence_author_tool_contract(provider=provider)


def test_environment_presence_matches_canonical_without_choosing_a_fact(tmp_path):
    ledger = SQLiteWorldLedger(path=tmp_path / "environment.sqlite", world_id=WORLD_ID)
    try:
        value = _full_propose(_seed_clock(ledger))
    finally:
        ledger.close()
    current = world_consequence_author_tool_contract(provider=object())
    old = world_consequence_author_tool_contract(
        provider=object(), contract_id="world-consequence-author-tool.2",
    )
    validator = Draft202012Validator(current["tools"][0]["function"]["parameters"])
    prior = Draft202012Validator(old["tools"][0]["function"]["parameters"])
    visual = value["outcomes"][0]["visual_evidence"]
    names = tuple(LifeDevelopmentVisualEnvironmentDraft.model_fields)
    for present in product((None, "source-bound text"), repeat=len(names)):
        visual["environment"] = dict(zip(names, present, strict=True))
        canonical = True
        try:
            LifeDevelopmentPossibilityDraft.model_validate_json(_json(value))
        except ValueError:
            canonical = False
        assert validator.is_valid({"replacement": value}) is canonical is any(present)
        if not canonical:
            # Reproduces the native-accepted/canonical-rejected real failure.
            assert prior.is_valid({"replacement": value})
    for name in names:
        visual["environment"] = dict.fromkeys(names)
        visual["environment"][name] = ""
        assert not validator.is_valid({"replacement": value})
        visual["environment"][name] = "\n"
        assert validator.is_valid({"replacement": value})
        LifeDevelopmentPossibilityDraft.model_validate_json(_json(value))
    # No environment information is valid absence, not a request to invent weather.
    visual["environment"] = None
    assert validator.is_valid({"replacement": value})
    LifeDevelopmentPossibilityDraft.model_validate_json(_json(value))


@pytest.mark.parametrize("located", [False, True])
@pytest.mark.parametrize("privacy", [*ORDINARY_LIFE_PHOTO_PRIVACY, "withhold"])
@pytest.mark.parametrize("has_visual", [False, True])
def test_location_privacy_visual_matrix_matches_existing_parser(
    tmp_path, located, privacy, has_visual,
):
    ledger = SQLiteWorldLedger(path=tmp_path / "schema.sqlite", world_id=WORLD_ID)
    try:
        value = _full_propose(_seed_clock(ledger))
    finally:
        ledger.close()
    value["privacy_class"] = privacy
    if not located:
        value["location_ref"] = value["location_capability_ref"] = None
    for outcome in value["outcomes"]:
        outcome["privacy_class"] = privacy
        if not has_visual:
            outcome["visual_evidence"] = None
        elif not located:
            outcome["visual_evidence"]["location"] = None
    expected = not (located and privacy != "withhold" and not has_visual) and not (
        privacy == "withhold" and has_visual
    )
    schema = world_consequence_author_tool_contract(provider=object())["tools"][0]["function"]["parameters"]
    assert Draft202012Validator(schema).is_valid({"replacement": value}) is expected
    if expected:
        LifeDevelopmentPossibilityDraft.model_validate_json(_json(value))
    else:
        with pytest.raises(ValueError):
            LifeDevelopmentPossibilityDraft.model_validate_json(_json(value))


def test_probe_missing_visual_shape_is_rejected_before_business_parser(tmp_path):
    ledger = SQLiteWorldLedger(path=tmp_path / "probe-shape.sqlite", world_id=WORLD_ID)
    try:
        value = _full_propose(_seed_clock(ledger))
    finally:
        ledger.close()
    # Same actual probe relationship: a located private proposal whose ordinary
    # outcomes all explicitly supplied visual_evidence=null. No private prose or
    # generated evidence is needed to reproduce the interface mismatch.
    value["privacy_class"] = "private"
    for outcome in value["outcomes"]:
        outcome["privacy_class"] = "private"
        outcome["visual_evidence"] = None
    envelope = {"replacement": value}
    old = world_consequence_author_tool_contract(provider=object(), contract_id="world-consequence-author-tool.1")
    current = world_consequence_author_tool_contract(provider=object())
    assert Draft202012Validator(old["tools"][0]["function"]["parameters"]).is_valid(envelope)
    assert not Draft202012Validator(current["tools"][0]["function"]["parameters"]).is_valid(envelope)
    with pytest.raises(ValueError, match="location-bound ordinary-privacy"):
        LifeDevelopmentPossibilityDraft.model_validate_json(_json(value))


@pytest.mark.parametrize("missing_field", ["location_ref", "location_capability_ref"])
def test_location_pair_cannot_be_half_null(tmp_path, missing_field):
    ledger = SQLiteWorldLedger(path=tmp_path / "pair.sqlite", world_id=WORLD_ID)
    try:
        value = _full_propose(_seed_clock(ledger))
    finally:
        ledger.close()
    value[missing_field] = None
    schema = world_consequence_author_tool_contract(provider=object())["tools"][0]["function"]["parameters"]
    assert not Draft202012Validator(schema).is_valid({"replacement": value})


def test_unlocated_visual_cannot_introduce_a_location(tmp_path):
    ledger = SQLiteWorldLedger(path=tmp_path / "location.sqlite", world_id=WORLD_ID)
    try:
        value = _full_propose(_seed_clock(ledger))
    finally:
        ledger.close()
    value["location_ref"] = value["location_capability_ref"] = None
    assert value["outcomes"][0]["visual_evidence"]["location"] is not None
    schema = world_consequence_author_tool_contract(provider=object())["tools"][0]["function"]["parameters"]
    assert not Draft202012Validator(schema).is_valid({"replacement": value})


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


def test_required_course_detail_tool_has_no_no_op_and_recovers_its_original_offset():
    from companion_daemon.world_v2.world_consequence_author_tool import REQUIRED_EVENT_CONTRACT

    provider = SimpleNamespace(supports_strict_tool_choice=True, single_tool_selection_mode="forced")
    contract = world_consequence_author_tool_contract(provider=provider,
        contract_id=REQUIRED_EVENT_CONTRACT, closing_offset_seconds=5340)
    schema = contract["tools"][0]["function"]["parameters"]
    Draft202012Validator.check_schema(schema)
    assert not Draft202012Validator(schema).is_valid({"replacement": {"decision": "no_op"}})
    assert "no_op" not in contract["tools"][0]["function"]["description"]
    original = [{"role": "system", "content": "unchanged"}, {"role": "user", "content": _json({
        "capability_manifest": {"event_detail_capability": {"closing_offset_seconds": 5340}},
    })}]
    bound = bind_world_consequence_author_tool(messages=original, tool_contract=contract)
    assert recover_world_consequence_author_tool(messages=bound, provider=provider) == contract
    tampered = copy.deepcopy(bound)
    body = json.loads(tampered[1]["content"])
    body["capability_manifest"]["event_detail_capability"]["closing_offset_seconds"] = 5280
    tampered[1]["content"] = _json(body)
    with pytest.raises(ValueError, match="identity changed"):
        recover_world_consequence_author_tool(messages=tampered, provider=provider)


def test_ordinary_course_tool_versions_its_source_privacy_and_small_shape():
    from companion_daemon.world_v2.world_consequence_author_tool import COURSE_EVENT_CONTRACT

    provider = SimpleNamespace(supports_strict_tool_choice=True, single_tool_selection_mode="forced")
    contract = world_consequence_author_tool_contract(provider=provider,
        contract_id=COURSE_EVENT_CONTRACT, closing_offset_seconds=5940,
        event_detail_privacy_class="private")
    schema = contract["tools"][0]["function"]["parameters"]
    Draft202012Validator.check_schema(schema)
    assert not Draft202012Validator(schema).is_valid({"replacement": {"decision": "no_op"}})
    assert "private" in contract["tools"][0]["function"]["description"]
    original = [{"role": "system", "content": "unchanged"}, {"role": "user", "content": _json({
        "capability_manifest": {"event_detail_capability": {
            "contract": "event-detail-capability.2", "closing_offset_seconds": 5940,
            "privacy_class": "private"}},
    })}]
    bound = bind_world_consequence_author_tool(messages=original, tool_contract=contract)
    assert recover_world_consequence_author_tool(messages=bound, provider=provider) == contract
    tampered = copy.deepcopy(bound)
    body = json.loads(tampered[1]["content"])
    body["capability_manifest"]["event_detail_capability"]["privacy_class"] = "personal"
    tampered[1]["content"] = _json(body)
    with pytest.raises(ValueError, match="identity changed"):
        recover_world_consequence_author_tool(messages=tampered, provider=provider)


@pytest.mark.parametrize("contract_id", [None, [], {}, "world-consequence-author-tool.unknown"])
def test_invalid_pinned_contract_id_is_an_explicit_identity_failure(contract_id):
    provider = SimpleNamespace(supports_strict_tool_choice=True)
    messages = [
        {"role": "system", "content": "unchanged"},
        {"role": "user", "content": _json({"world_author_wire": {"contract": contract_id}})},
    ]
    with pytest.raises(ValueError, match="identity changed"):
        recover_world_consequence_author_tool(messages=messages, provider=provider)


@pytest.mark.asyncio
@pytest.mark.parametrize("strict_original", [False, True, "legacy", "glm-json", "glm-schema"])
@pytest.mark.parametrize("rewrite_decision", ["no_op", "propose"])
async def test_source_rewrite_keeps_original_wire_and_review_permissions(
    tmp_path, monkeypatch, strict_original, rewrite_decision,
):
    text_carrier = {"glm-json": "json_object", "glm-schema": "json_schema"}.get(strict_original)
    if strict_original == "legacy":
        monkeypatch.setattr(
            "companion_daemon.world_v2.life_development_runtime.world_consequence_author_tool_contract",
            lambda *, provider: world_consequence_author_tool_contract(
                provider=provider, contract_id="world-consequence-author-tool.1",
            ),
        )
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
        if text_carrier:
            assert wire["response_format"]["type"] == text_carrier and "tools" not in wire
            schema = json.loads(wire["messages"][-1]["content"])
            if text_carrier == "json_schema":
                assert wire["response_format"]["json_schema"] == {
                    "name": schema["schema_name"], "strict": True, "schema": schema["response_schema"]}
            raw = _json({"replacement": authored})
            Draft202012Validator(schema["response_schema"]).validate(json.loads(raw))
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": raw}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300}})
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

    if text_carrier:
        from companion_daemon.subscription_usage import BackgroundGLMModel, SubscriptionTokenLedger
        provider = BackgroundGLMModel("fixture", "https://fixture.invalid/v1", "GLM-5.3-Flash",
            token_ledger=SubscriptionTokenLedger(tmp_path / "usage.sqlite"),
            tool_result_carrier=text_carrier, transport=httpx.MockTransport(respond))
    else:
        provider = _model(respond)
        provider.supports_strict_tool_choice = bool(strict_original)
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
        if text_carrier:
            assert requests[1]["messages"][:-3] == requests[0]["messages"][:-1]
            assert requests[1]["messages"][-1] == requests[0]["messages"][-1]
            assistant, correction = requests[1]["messages"][-3:-1]
            assert assistant["role"] == "assistant" and assistant["content"] == _json({"replacement": rejected})
            assert correction["role"] == "user" and "tool_calls" not in assistant
            assert "do not return it as plain assistant text" not in correction["content"]
        else:
            assert requests[1]["messages"][:-2] == requests[0]["messages"]
        original_user = json.loads(requests[0]["messages"][1]["content"])
        assert ("world_author_wire" in original_user) is bool(strict_original)
        if strict_original:
            assert original_user["world_author_wire"]["contract"] == (
                "world-consequence-author-tool.1" if strict_original == "legacy" else CONTRACT
            )
        if text_carrier:
            assert original_user["world_author_wire"]["response_carrier"] == text_carrier
        for wire in requests:
            raw_messages = _json(wire["messages"][:-1] if text_carrier else wire["messages"])
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
        feedback = requests[1]["messages"][-1]
        assert feedback["role"] == "tool"
        rejected_call = requests[1]["messages"][-2]["tool_calls"][0]
        assert feedback["tool_call_id"] == rejected_call["id"]
        assert rejected_call["function"]["arguments"] == malformed
        correction = json.loads(feedback["content"])["correction"]
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


@pytest.mark.asyncio
@pytest.mark.parametrize("original_marker", [False, True], ids=["legacy-unmarked", "explicit-json-object"])
@pytest.mark.parametrize("current_carrier", ["json_object", "json_schema"])
@pytest.mark.parametrize("rewrite_decision", ["no_op", "propose"])
async def test_paid_propose_reopens_before_first_source_rewrite(
    tmp_path, monkeypatch, original_marker, current_carrier, rewrite_decision,
):
    from companion_daemon.subscription_usage import BackgroundGLMModel, SubscriptionTokenLedger
    import companion_daemon.world_v2.life_development_runtime as life

    class InterruptedAfterPaidAuthor(BaseException):
        pass
    path = tmp_path / "isolated.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    rejected = copy.deepcopy(_full_propose(wake))
    fragment = "她及时收回了手账。"
    rejected["outcomes"][0]["world_consequence"]["environment_text"] += fragment
    wires = []

    def http(request):
        wire = json.loads(request.content)
        wires.append(wire)
        value = (rejected if len(wires) == 1 else _full_propose(wake)
                 if rewrite_decision == "propose" else {"decision": "no_op"})
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": _json({"replacement": value})}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300}})

    def model(mode):
        return BackgroundGLMModel("fixture", "https://fixture.invalid/v1", "GLM-5.3-Flash",
            token_ledger=SubscriptionTokenLedger(tmp_path / "usage.sqlite"),
            tool_result_carrier=mode, transport=httpx.MockTransport(http))

    old = model("json_object")
    general = _SequenceModel(model="fixture:general", outputs=())
    focused = _SequenceModel(model="fixture:focused", outputs=())
    runtime = _producer_runtime(ledger, store, wake, old, general, focused)
    async def stop_before_review(**kwargs):
        raise InterruptedAfterPaidAuthor()
    runtime._review_world_author_candidate = stop_before_review
    real_bind = life.bind_world_consequence_author_tool
    def old_binding(*, messages, tool_contract, response_carrier=None):
        return real_bind(messages=messages, tool_contract=tool_contract)
    try:
        with monkeypatch.context() as local:
            if not original_marker:
                local.setattr(life, "bind_world_consequence_author_tool", old_binding)
            with pytest.raises(InterruptedAfterPaidAuthor):
                await _advance(runtime, wake)
        assert len(wires) == 1 and not general.calls and not focused.calls
        original_metadata, _ = _audited(ledger)
        original_binding = original_metadata["request_bindings"][0]
        original_content = store.read_exact(content_ref=original_binding["content_ref"])
        marker = json.loads(json.loads(original_content.text)[1]["content"])["world_author_wire"]
        assert (marker.get("response_carrier") == "json_object") == original_marker
        paid_audits = tuple((x.event_ref, x.audit_json) for x in ledger.project().model_result_audits)
    finally:
        await old.aclose()
        store.close()
        ledger.close()

    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    current = model(current_carrier)
    general = _SequenceModel(model="fixture:general", outputs=(_source_closure_review(decision="supported"),) * 2)
    focused = _SequenceModel(model="fixture:focused", outputs=(_novel_origin_review(
        decision="unsupported", unsupported_outcome_prerequisites=({
            "prose_path": "outcomes.0.world_consequence.environment_text",
            "violation_kinds": ["character_interior_authorship"], "exact_fragments": [fragment]},)),
        _novel_origin_review(decision="supported")))
    try:
        result = await _advance(_producer_runtime(ledger, store, wake, current, general, focused), wake)
        assert result.status == ("no_op" if rewrite_decision == "no_op" else "occurrence_committed")
        assert len(wires) == 2
        assert general.calls == focused.calls == (1 if rewrite_decision == "no_op" else 2)
        assert store.read_exact(content_ref=original_content.content_ref) == original_content
        metadata_after = [json.loads(value["response_text"])
            for audit in ledger.project().proposal_audits
            if isinstance((value := json.loads(audit.proposal_json)).get("response_text"), str)]
        assert original_metadata in metadata_after
        audit_map = {x.event_ref: x.audit_json for x in ledger.project().model_result_audits}
        assert all(audit_map[event] == raw for event, raw in paid_audits)
        correction_wire = wires[1]
        assert correction_wire["response_format"]["type"] == current_carrier
        assert "tools" not in correction_wire
        marker2 = json.loads(correction_wire["messages"][1]["content"])["world_author_wire"]
        assert marker2 == marker
        messages = correction_wire["messages"]
        uses_tool_history = any("tool_calls" in x or x["role"] == "tool" for x in messages)
        forbids_text = "do not return it as plain assistant text" in json.dumps(messages, ensure_ascii=False)
        assert not uses_tool_history and not forbids_text
        assert messages[:-3] == json.loads(original_content.text)
        assert messages[-3] == {"role": "assistant", "content": _json({"replacement": rejected})}
        feedback = json.loads(messages[-2]["content"])
        if not original_marker:
            assert feedback["correction_transport"] == {
                "contract": "world-author-correction-carrier.1",
                "response_carrier": current_carrier,
                "original_request_hash": original_metadata["request_hashes"][0],
                "original_tool_contract_sha256": marker["tool_contract_sha256"],
                "rejected_raw_sha256": hashlib.sha256(_json({"replacement": rejected}).encode()).hexdigest(),
            }
        else:
            assert "correction_transport" not in feedback
        before = ledger.export_replay_evidence()
        original_result = result
    finally:
        await current.aclose()
        store.close()
        ledger.close()

    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    current = model(current_carrier)
    try:
        recovered = await _advance(_producer_runtime(ledger, store, wake, current, general, focused), wake)
        assert recovered.status == original_result.status
        assert recovered.proposal_event_ref == original_result.proposal_event_ref
        assert ledger.export_replay_evidence() == before and len(wires) == 2
    finally:
        await current.aclose()
        store.close()
        ledger.close()
