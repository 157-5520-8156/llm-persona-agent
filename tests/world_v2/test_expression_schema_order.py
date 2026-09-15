"""Wire ordering cannot change source authority, accepted grammar or replay."""
from copy import deepcopy
import json

import pytest
from jsonschema import Draft202012Validator

from companion_daemon.llm import provider_invocation_request_hash
from companion_daemon.world_v2.character_interior.expression_schema_order import (
    CONTRACT, evidence_first_expression_schema, ordered_schema_sha256,
)
from companion_daemon.world_v2.character_interior.inbound_tool_contract import InboundToolContracts
from companion_daemon.world_v2.expression_draft import qq_expression_capabilities
from test_inbound_atomic_result_v3 import _branch_value


def expression_orders(node):
    if isinstance(node, dict):
        if {'beats', 'world_claims', 'timing_choice'} <= node.get('properties', {}).keys():
            yield list(node['properties'])
        for child in node.values():
            yield from expression_orders(child)
    elif isinstance(node, list):
        for child in node:
            yield from expression_orders(child)


@pytest.mark.parametrize('phase,recall', [('initial', True), ('initial', False), ('after_recall', False), ('final', False)])
@pytest.mark.parametrize('references', [False, True])
def test_all_available_phase_and_timing_branches_keep_grammar_and_bind_wire_order(phase, recall, references):
    args = dict(phase=phase, recall_allowed=recall, capabilities=qq_expression_capabilities('napcat'),
                atomic_envelope_version='3', schema_dialect='deepseek-strict', use_schema_references=references)
    factory = InboundToolContracts()
    old = factory.contract_for(**args)
    before = json.dumps(old.provider_tools)
    new = factory.contract_for(**args, evidence_first_schema=True)
    old_schema = old.provider_tools[0]['function']['parameters']
    schema = new.provider_tools[0]['function']['parameters']
    assert schema == old_schema
    assert all(order[-1] == 'beats' for order in expression_orders(schema))
    assert any(order[-1] != 'beats' for order in expression_orders(old_schema))
    assert ordered_schema_sha256(schema) != ordered_schema_sha256(old_schema)
    assert new.identity.schema_sha256 == old.identity.schema_sha256
    assert new.identity.capabilities_sha256 == old.identity.capabilities_sha256
    assert new.identity.contract_sha256 != old.identity.contract_sha256
    assert CONTRACT in new.provider_tools[0]['function']['description']
    assert before == json.dumps(factory.contract_for(**args).provider_tools)
    assert new.result_branch_fields() == old.result_branch_fields()
    for kind in ('decision', 'recall'):
        value = {'result': _branch_value(kind)}
        assert Draft202012Validator(schema).is_valid(value) == (kind == 'decision' or recall)
        if kind == 'decision' or recall:
            assert old.unwrap(json.dumps(value)) == new.unwrap(json.dumps(value))
        if kind == 'decision':
            # Output key order stays the character model's serialization choice.
            draft = value['result']['expression_draft']
            value['result']['expression_draft'] = dict(reversed(list(draft.items())))
            assert old.unwrap(json.dumps(value)) == new.unwrap(json.dumps(value))
            value['result']['expression_draft']['world_claims'] = 'not an array'
            assert not Draft202012Validator(schema).is_valid(value)
    common = dict(messages=[{'role': 'user', 'content': 'fixture'}], temperature=0.0)
    assert provider_invocation_request_hash(**common, tools=list(old.provider_tools), tool_choice=old.provider_tool_choice) != provider_invocation_request_hash(
        **common, tools=list(new.provider_tools), tool_choice=new.provider_tool_choice)


def test_literal_objects_and_input_bytes_are_not_reordered():
    literal = {'properties': {'beats': {'type': 'array'}, 'world_claims': {'type': 'array'}, 'timing_choice': {'type': 'string'}}}
    schema = {'type': 'object', 'properties': deepcopy(literal['properties']), 'enum': [literal], 'default': literal, 'examples': [literal]}
    before = json.dumps(schema)
    new = evidence_first_expression_schema(schema)
    assert json.dumps(schema) == before
    assert json.dumps(new['enum']) == json.dumps(schema['enum'])
    assert json.dumps(new['default']) == json.dumps(schema['default'])
    assert json.dumps(new['examples']) == json.dumps(schema['examples'])
    assert new == schema and ordered_schema_sha256(new) != ordered_schema_sha256(schema)


@pytest.mark.parametrize('version,flag', [('1', True), ('2', True), ('3', 'true'), ('3', 1)])
def test_ordering_is_explicit_and_cannot_leak_into_legacy_transports(version, flag):
    with pytest.raises((ValueError, TypeError)):
        InboundToolContracts().contract_for(phase='initial', capabilities=qq_expression_capabilities('napcat'),
            recall_allowed=True, atomic_envelope_version=version, schema_dialect='deepseek-strict', evidence_first_schema=flag)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', [None, 'reselect'])
async def test_real_author_port_preserves_order_on_correction_and_cold_verification(tmp_path, fault):
    from dataclasses import replace
    from test_visible_independent_review_runtime import application, ReviewHTTP, _inbound, _audits
    from companion_daemon.world_v2.visible_source_runtime import verify_recorded_candidate
    handler = ReviewHTTP(fault, '15')
    async with application(tmp_path / 'world.sqlite', handler, evidence_first_schema=True) as app:
        result = await app.respond(replace(_inbound(), text='我取消了周五的报告。'))
        assert result.status == 'action_authorized'
        requests = [r for r in handler.requests if r['tool_choice']['function']['name'] == 'character_inbound_initial_v3']
        assert len(requests) == (2 if fault else 1)
        for request in requests:
            schema = request['tools'][0]['function']['parameters']
            assert all(order[-1] == 'beats' for order in expression_orders(schema))
        assert any(a.visible_source_review_json for a in _audits(app))
        evidence = app.export_replay_evidence()
        audit = next(a for a in evidence.projection.proposal_audits if a.proposal_kind == 'decision')
        assert verify_recorded_candidate(audit=audit, model_result_audits=evidence.projection.model_result_audits)
