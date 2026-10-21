import hashlib
import json

from jsonschema import Draft202012Validator
import pytest

from companion_daemon.world_v2.life_development_output_schema import life_possibility_output_schema
from companion_daemon.world_v2.world_consequence_author_tool import (
    _host_constraints, bind_world_consequence_author_tool, recover_world_consequence_author_tool,
    world_consequence_author_tool_contract,
)
from companion_daemon.world_v2.character_interior.local_schema_references import expand_local_schema_references


def test_original_world_tool_three_remains_byte_identical():
    contract = world_consequence_author_tool_contract(provider=object(), contract_id="world-consequence-author-tool.3")
    raw = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(raw.encode()).hexdigest() == "fe4ea7e9800f3114b270ef717cd4fd313ab42322c34d0cd4cc685ead9f08ef0c"


def test_new_world_tool_advertises_the_same_now_timing_as_domain_validation():
    old = world_consequence_author_tool_contract(provider=object(),
        contract_id='world-consequence-author-tool.6')
    new = world_consequence_author_tool_contract(provider=object(),
        contract_id='world-consequence-author-tool.6', exact_now_timing=True)
    assert old != new
    schema = expand_local_schema_references(new['tools'][0]['function']['parameters'])
    timing = schema['properties']['replacement']['anyOf'][1]['properties']['timing']
    validator = Draft202012Validator(timing)
    assert validator.is_valid({'mode': 'now', 'duration_minutes': 10,
                               'opens_at': None, 'closes_at': None})
    assert not validator.is_valid({'mode': 'now', 'duration_minutes': 10,
                                   'opens_at': '2026-10-21T09:37:30+08:00',
                                   'closes_at': '2026-10-21T09:48:01+08:00'})


def test_new_timing_wire_recovery_uses_the_pinned_manifest_marker():
    class Provider:
        supports_strict_tool_choice = False
        supports_single_tool_result = True
        single_tool_response_carrier = 'json_object'
        single_tool_selection_mode = 'json_object'
        provider = 'glm'

    provider = Provider()
    contract = world_consequence_author_tool_contract(provider=provider,
        contract_id='world-consequence-author-tool.6', exact_now_timing=True)
    messages = [
        {'role': 'system', 'content': 'World authority'},
        {'role': 'user', 'content': json.dumps({
            'capability_manifest': {'world_author_output_contract': '2',
                                    'active_attempt_consequence': {'source_event_ref': 'event:started'}},
            'output_contract': {'propose': life_possibility_output_schema(
                outcome_contract='world-consequence.2', event_details=True)},
        })},
    ]
    bound = bind_world_consequence_author_tool(messages=messages, tool_contract=contract,
        response_carrier='json_object')
    assert recover_world_consequence_author_tool(messages=bound, provider=provider) == contract


@pytest.mark.parametrize("field", ["provisional_npcs", "provisional_places"])
def test_native_world_tool_exposes_the_existing_tag_namespace(field):
    contract = world_consequence_author_tool_contract(provider=object())
    schema = expand_local_schema_references(contract["tools"][0]["function"]["parameters"])
    propose = schema["properties"]["replacement"]["anyOf"][1]
    outcome = propose["properties"]["outcomes"]["items"]["anyOf"][0]
    tags = outcome["properties"][field]
    if "anyOf" in tags:
        tags = next(branch for branch in tags["anyOf"] if branch.get("type") == "array")
    validator = Draft202012Validator(tags["items"]["properties"]["narrative_tags"]["items"])
    assert validator.is_valid("narrative:an-unplanned-change")
    assert not validator.is_valid("an-unplanned-change")
    assert not validator.is_valid("narrative:UPPERCASE")


def test_compact_contract_keeps_all_evidence_and_unenforced_scalar_limits():
    schema = life_possibility_output_schema(outcome_contract="world-consequence.2")
    user = {"output_contract": {"no_op": {"decision": "no_op"}, "propose": schema},
            "pinned_world_context": {"facts": ["原始完整事实"]},
            "cross_field_authority": {"unchanged": True}}
    messages = [{"role": "system", "content": "World authority"},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]
    contract = world_consequence_author_tool_contract(provider=object())
    bound = bind_world_consequence_author_tool(messages=messages, tool_contract=contract)
    actual = json.loads(bound[1]["content"])
    assert actual["pinned_world_context"] == user["pinned_world_context"]
    assert actual["cross_field_authority"] == user["cross_field_authority"]
    assert actual["output_contract"]["propose"]["host_constraints"] == _host_constraints(schema)
    assert actual["output_contract"]["propose"]["schema_source"] == "complete_forced_tool_parameters"
    assert len(json.dumps(actual["output_contract"])) < len(json.dumps(user["output_contract"])) / 2
    assert bound[0] == messages[0]


def test_host_limits_never_treat_default_objects_as_schema_rules():
    schema = {"type": "string", "maxLength": 10,
              "default": {"maxLength": 500}, "examples": [{"minItems": 700}]}
    assert _host_constraints(schema) == {"#": {"maxLength": 10}}
