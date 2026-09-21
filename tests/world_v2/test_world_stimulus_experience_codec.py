"""World appraisal choices retain exact source identity across the provider wire."""

from copy import deepcopy
import hashlib
import json

import httpx
from jsonschema import Draft202012Validator
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.character_interior.contracts import _InteriorCapabilityManifest
from companion_daemon.world_v2.character_interior import CharacterInterior, InteriorStimulus
from companion_daemon.world_v2.character_interior.structured_role import (
    StructuredCharacterRoleFaculty,
    StructuredRoleResultError,
)
from test_character_interior_structured_role import (
    _CURSOR,
    _NOW,
    _Projection,
    _RequiredToolQueueModel,
    _request,
    _world_stimulus_no_change_result,
)
from companion_daemon.world_v2.character_interior.structured_role_tool_contract import (
    StructuredRoleToolContracts,
)
from test_character_interior_experience_transitions import _capability

SOURCE = "source:private_self"
EXTRA = "source:continuity"


def _manifest(capability=None):
    payload = {
        "contract": "character-interior-world-stimulus-capability.1",
        "experience_transitions": {
            "contract": "character-interior-experience-transitions.1",
            "current_source_ref": SOURCE,
            "thread_open_available": True,
            "thread_heads": [],
            "goal_heads": [],
            "commitment_heads": [],
            "commitment_open_threads": [],
            "memory_sources": [],
        },
    }
    if capability is not None:
        payload["experience_transitions"] = capability
    wire = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _InteriorCapabilityManifest(
        capability_ref="capability:world-stimulus:codec",
        capability_kind="world_stimulus_appraisal",
        payload_json=wire,
        payload_hash="sha256:" + hashlib.sha256(wire.encode()).hexdigest(),
        source_refs=(SOURCE,),
    )


def _result(refs):
    value = json.loads(_world_stimulus_no_change_result())
    value["status"] = "transition"
    value["proposals"][0]["experience_transition"] = {
        "domain": "thread",
        "operation": "open",
        "target_id": None,
        "expected_entity_revision": 0,
        "thread_kind": "topic_open",
        "importance_bp": 3200,
        "source_refs": refs,
        "reason_summary": "I want to leave this matter open.",
    }
    return value


async def _run(value, *, manifest=None):
    model = _RequiredToolQueueModel(json.dumps(value, ensure_ascii=False))
    role = StructuredCharacterRoleFaculty(model=model, model_id="offline-codec-test")
    request = await _request(
        phase="experience",
        purpose="world_stimulus_appraisal",
        capability_manifest=manifest or _manifest(),
    )
    return await role.experience(request), model


@pytest.mark.asyncio
async def test_current_stimulus_token_crosses_real_http_adapter_unchanged_semantically():
    raw = json.dumps({"result": _result(["s0"])})
    captured = []

    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "function": {
                                        "name": "character_role_world_stimulus_appraisal_v2",
                                        "arguments": raw,
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        )

    model = DeepSeekChatModel(
        "offline-test-key",
        "https://api.deepseek.com",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(handle),
    )
    try:
        result = await StructuredCharacterRoleFaculty(
            model=model, model_id="deepseek-v4-flash"
        ).experience(
            await _request(
                phase="experience",
                purpose="world_stimulus_appraisal",
                capability_manifest=_manifest(),
            )
        )
    finally:
        await model.aclose()
    expected = _result([SOURCE])["proposals"][0]["experience_transition"]
    actual = result["proposals"][0]["payload"]["experience_transition"]
    assert {key: actual[key] for key in expected} == expected
    assert len(captured) == 1


@pytest.mark.asyncio
async def test_live_schema_refuses_extra_reference_allowed_by_generic_wire():
    _valid, model = await _run(_result([SOURCE]))
    schema = model.tool_calls[0][0][0]["function"]["parameters"]
    validator = Draft202012Validator(schema)
    assert validator.is_valid(_result([SOURCE]))
    assert validator.is_valid(_result(["s0"]))
    assert not validator.is_valid(_result([SOURCE, EXTRA]))


@pytest.mark.asyncio
async def test_json_syntax_failure_reports_bounded_position_without_repair():
    raw = '{"status":"transition","summary":"I said "hello"."}'
    model = _RequiredToolQueueModel(raw)
    role = StructuredCharacterRoleFaculty(model=model, model_id="offline-codec-test")
    with pytest.raises(StructuredRoleResultError) as failure:
        await role.experience(
            await _request(
                phase="experience",
                purpose="world_stimulus_appraisal",
                capability_manifest=_manifest(),
            )
        )
    assert failure.value.code == "role_result_not_json"
    assert "line 1" in failure.value.detail
    assert "column" in failure.value.detail
    assert "Expecting ',' delimiter" in failure.value.detail
    assert len(failure.value.detail) < 512
    assert failure.value.rejected_raw == raw
    assert len(model.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "refs",
    [
        ["s0", "s9"],
        [SOURCE, EXTRA],
        ["s0", SOURCE],
        ["source:actor:other"],
        ["s999"],
        ["unoffered-short-hash"],
    ],
)
async def test_extra_unpinned_duplicate_or_other_actor_sources_still_fail(refs):
    original = _result(refs)
    frozen = deepcopy(original)
    with pytest.raises(StructuredRoleResultError):
        await _run(original)
    assert original == frozen


@pytest.mark.asyncio
async def test_canonical_and_token_produce_same_authorized_payload():
    canonical, _ = await _run(_result([SOURCE]))
    token, _ = await _run(_result(["s0"]))
    assert canonical["proposals"] == token["proposals"]
    # Keep the original author response hashes distinct; transport restoration
    # does not claim that the provider wrote identical response bytes.
    assert canonical["author_lineage"]["response_hash"] != token["author_lineage"]["response_hash"]


@pytest.mark.asyncio
async def test_wrong_operation_and_unoffered_actor_head_fail_in_schema_and_parser():
    good = _result([SOURCE])
    _, model = await _run(good)
    validator = Draft202012Validator(model.tool_calls[0][0][0]["function"]["parameters"])
    wrong = deepcopy(good)
    wrong["proposals"][0]["experience_transition"].update(
        operation="resolve",
        target_id="thread:actor:other",
        expected_entity_revision=1,
        thread_kind=None,
        importance_bp=None,
        resolution_kind="answered",
    )
    assert not validator.is_valid(wrong)
    with pytest.raises(StructuredRoleResultError):
        await _run(wrong)


def test_exact_head_source_sets_keep_each_domain_and_forbid_alias_double_count():
    capability = _capability().model_dump(mode="json")
    source = capability["current_source_ref"]
    contract = StructuredRoleToolContracts().world_stimulus_appraisal(
        capability_payload={"experience_transitions": capability},
        recall_allowed=True,
        source_tokens=(("s0", source), ("s1", "event:head")),
    )
    validator = Draft202012Validator(contract.provider_tools[0]["function"]["parameters"])
    examples = [
        {
            "domain": "goal",
            "operation": "pause",
            "target_id": "goal:existing",
            "expected_entity_revision": 3,
            "reason_kind": "priority_shift",
        },
        {
            "domain": "thread",
            "operation": "resolve",
            "target_id": "thread:existing",
            "expected_entity_revision": 2,
            "resolution_kind": "answered",
        },
        {
            "domain": "commitment",
            "operation": "release",
            "target_id": "commitment:existing",
            "expected_entity_revision": 4,
            "release_reason_code": "obsolete",
        },
    ]
    for example in examples:
        for refs in ([source, "event:head"], ["s1", "s0"], [source, "s1"]):
            value = _result([source])
            value["proposals"][0]["experience_transition"] = {
                **example,
                "source_refs": refs,
                "reason_summary": "I chose this transition.",
            }
            assert validator.is_valid(value), value
            duplicate_authority = deepcopy(value)
            duplicate_authority["proposals"][0]["experience_transition"]["source_refs"] = [
                source,
                "s0",
            ]
            assert not validator.is_valid(duplicate_authority)
            wrong_actor_head = deepcopy(value)
            wrong_actor_head["proposals"][0]["experience_transition"]["target_id"] = (
                "other-actor:head"
            )
            assert not validator.is_valid(wrong_actor_head)


def test_memory_and_commitment_open_capabilities_survive_specialization():
    capability = _capability().model_dump(mode="json")
    source = capability["current_source_ref"]
    contract = StructuredRoleToolContracts().world_stimulus_appraisal(
        capability_payload={"experience_transitions": capability},
        recall_allowed=False,
        source_tokens=(("s0", source), ("s1", "event:head")),
    )
    validator = Draft202012Validator(contract.provider_tools[0]["function"]["parameters"])
    choices = [
        {
            "domain": "commitment",
            "operation": "open",
            "target_id": None,
            "expected_entity_revision": 0,
            "thread_id": "thread:existing",
            "importance_bp": 3200,
            "due_at": "2026-08-06T12:00:00Z",
            "persistence": "durable",
            "source_refs": ["s0", "s1"],
            "reason_summary": "I choose this commitment.",
        },
        {
            "domain": "memory_candidate",
            "operation": "retain",
            "source_token": "memory-source:exact",
            "cue_kind": "world_continuity",
            "retention_rationales": ["world_continuity"],
            "source_refs": ["s0"],
            "reason_summary": "I choose to remember this.",
            "salience": {
                name: 4000
                for name in (
                    "autobiographical_relevance_bp",
                    "relationship_relevance_bp",
                    "emotional_residue_bp",
                    "unfinished_business_bp",
                    "recurrence_bp",
                    "novelty_bp",
                    "future_utility_bp",
                    "world_continuity_bp",
                )
            },
        },
    ]
    for choice in choices:
        value = _result([source])
        value["proposals"][0]["experience_transition"] = choice
        assert validator.is_valid(value)
        wrong_source = deepcopy(value)
        wrong_source["proposals"][0]["experience_transition"]["source_refs"].append(
            "actor:other:authority"
        )
        assert not validator.is_valid(wrong_source)


def test_same_stimulus_and_head_authority_requires_one_reference_only():
    capability = _capability().model_dump(mode="json")
    source = capability["current_source_ref"]
    capability["goal_heads"][0]["authority_source_ref"] = source
    contract = StructuredRoleToolContracts().world_stimulus_appraisal(
        capability_payload={"experience_transitions": capability},
        recall_allowed=True,
        source_tokens=(("s0", source),),
    )
    validator = Draft202012Validator(contract.provider_tools[0]["function"]["parameters"])
    value = _result([source])
    transition = {
        "domain": "goal",
        "operation": "pause",
        "target_id": "goal:existing",
        "expected_entity_revision": 3,
        "reason_kind": "priority_shift",
        "source_refs": ["s0"],
        "reason_summary": "I choose to pause this goal.",
    }
    value["proposals"][0]["experience_transition"] = transition
    assert validator.is_valid(value)
    transition["source_refs"] = [source, "s0"]
    assert not validator.is_valid(value)


@pytest.mark.asyncio
async def test_omitted_nullable_open_target_keeps_canonical_wire_compatibility():
    value = _result([SOURCE])
    del value["proposals"][0]["experience_transition"]["target_id"]
    result, model = await _run(value)
    assert result["proposals"][0]["payload"]["experience_transition"]["target_id"] is None
    assert Draft202012Validator(model.tool_calls[0][0][0]["function"]["parameters"]).is_valid(value)


def test_opaque_capability_authority_cannot_collide_with_another_catalogue_token():
    capability = _capability().model_dump(mode="json")
    capability["goal_heads"][0]["authority_source_ref"] = "s0"
    with pytest.raises(ValueError, match="ambiguous"):
        StructuredRoleToolContracts().world_stimulus_appraisal(
            capability_payload={"experience_transitions": capability},
            recall_allowed=True,
            source_tokens=(("s0", capability["current_source_ref"]),),
        )


def test_many_heads_add_only_thin_constraints_not_repeated_domain_shapes():
    capability = _capability().model_dump(mode="json")
    for field in ("goal_heads", "thread_heads", "commitment_open_threads", "commitment_heads"):
        seed = capability[field][0]
        capability[field] = []
        for index in range(16):
            head = deepcopy(seed)
            field_id = "thread_id" if field == "commitment_open_threads" else "target_id"
            suffix = hashlib.sha256(f"{field}{index}".encode()).hexdigest()
            head[field_id] += ":" + suffix
            head["authority_source_ref"] = "event:authority:" + suffix
            capability[field].append(head)
    contract = StructuredRoleToolContracts().world_stimulus_appraisal(
        capability_payload={"experience_transitions": capability},
        recall_allowed=True,
        source_tokens=(("s0", capability["current_source_ref"]),),
    )
    schema = contract.provider_tools[0]["function"]["parameters"]
    encoded = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    assert len(encoded.encode()) < 40000
    # This field belongs to each canonical domain once, independently of the
    # number of heads offered within it. Repeating it inflated paid requests.
    field = schema["anyOf"][0]["properties"]["proposals"]["items"]["properties"]["experience_transition"]
    assert json.dumps(field, separators=(",", ":")).count('"reason_summary":') == 4


@pytest.mark.parametrize(
    "tokens",
    [
        (("s0", SOURCE), ("s0", EXTRA)),
        (("s0", SOURCE), ("s1", SOURCE)),
        (("s0", SOURCE), (SOURCE, EXTRA)),
    ],
)
def test_ambiguous_catalogue_fails_closed_without_choosing_one(tokens):
    with pytest.raises(ValueError, match="ambiguous"):
        StructuredRoleToolContracts().world_stimulus_appraisal(
            capability_payload=_manifest().payload, recall_allowed=True, source_tokens=tokens
        )


def test_new_pin_changes_schema_identity_while_legacy_compilation_stays_byte_identical():
    compiler = StructuredRoleToolContracts()
    legacy = compiler.world_stimulus_appraisal(
        capability_payload=_manifest().payload, recall_allowed=True
    )
    assert (
        legacy.identity.schema_sha256
        == "sha256:f6ad74f30c7a76f3c519b914eebbcdbd946ab736eab74d09d6be9f9695b9dcc2"
    )
    assert (
        legacy.identity.contract_sha256
        == "sha256:27a6989eab300988731098b08b73ed328f70d61279fbf96ace213e4719150b4a"
    )
    current = compiler.world_stimulus_appraisal(
        capability_payload=_manifest().payload, recall_allowed=True, source_tokens=(("s0", SOURCE),)
    )
    other_pin = compiler.world_stimulus_appraisal(
        capability_payload=_manifest().payload,
        recall_allowed=True,
        source_tokens=(("s0", EXTRA), ("s1", SOURCE)),
    )
    assert len({c.identity.contract_sha256 for c in (legacy, current, other_pin)}) == 3
    validator = Draft202012Validator(other_pin.provider_tools[0]["function"]["parameters"])
    assert not validator.is_valid(_result(["s0"]))
    assert validator.is_valid(_result(["s1"]))


@pytest.mark.asyncio
async def test_core_keeps_one_same_pin_json_correction_then_technical_failure():
    invalid = '{"status":"transition","summary":"I said "hello"."}'
    model = _RequiredToolQueueModel(invalid, invalid)
    interior = CharacterInterior(
        projection=_Projection(),
        role=StructuredCharacterRoleFaculty(model=model, model_id="offline-codec-test"),
    )
    stimulus = InteriorStimulus(
        inner_turn_ref="turn:codec-json",
        stimulus_ref="stimulus:codec-json",
        world_id="world:test",
        actor_ref="character:zhizhi",
        trigger_ref="trigger:codec-json",
        cursor=_CURSOR,
        logical_time=_NOW,
        purpose="world_stimulus_appraisal",
        source_refs=(SOURCE,),
        capability_manifest=_manifest(),
    )
    result = await interior.experience(stimulus)
    assert result.status == "technical_failure"
    assert result.failure_code == "invalid_role_result_after_correction"
    assert len(model.calls) == 2
    first, second = [json.loads(call[0][-1]["content"]) for call in model.calls]
    assert first == {key: value for key, value in second.items() if key != "correction"}
    assert second["correction"]["ordinal"] == 1
    assert "line 1" in second["correction"]["failure_detail"]
    assert model.tool_calls[0] == model.tool_calls[1]


@pytest.mark.parametrize("operation", ["update", "resolve", "cancel"])
def test_existing_thread_cannot_redeclare_creation_kind_in_provider_schema(operation):
    """Real life-response retries repeated this field despite a valid offered head."""
    from pydantic import TypeAdapter, ValidationError
    from companion_daemon.world_v2.character_interior.experience_transitions import (
        ExperienceTransitionDraft,
    )

    capability = _capability().model_dump(mode="json")
    source = capability["current_source_ref"]
    contract = StructuredRoleToolContracts().world_stimulus_appraisal(
        capability_payload={"experience_transitions": capability},
        recall_allowed=False,
        source_tokens=(("s0", source), ("s1", "event:head")),
    )
    validator = Draft202012Validator(contract.provider_tools[0]["function"]["parameters"])
    value = _result([source, "event:head"])
    transition = value["proposals"][0]["experience_transition"]
    transition.update(
        operation=operation,
        target_id="thread:existing",
        expected_entity_revision=2,
        thread_kind=None,
        importance_bp=4400 if operation == "update" else None,
        due_at="2026-09-18T01:00:00Z" if operation == "update" else None,
        expires_at="2026-09-18T12:00:00Z" if operation == "update" else None,
        resolution_kind="answered" if operation == "resolve" else None,
        cancellation_reason_code="obsolete" if operation == "cancel" else None,
    )
    assert validator.is_valid(value)
    TypeAdapter(ExperienceTransitionDraft).validate_json(json.dumps(transition))
    transition["thread_kind"] = "external_result_pending"
    with pytest.raises(ValidationError, match="thread_kind must be null"):
        TypeAdapter(ExperienceTransitionDraft).validate_json(json.dumps(transition))
    assert not validator.is_valid(value)
