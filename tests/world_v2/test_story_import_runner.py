"""Whole preparation uses the same seams as live providers, with explicit fixture verdicts."""
import json
import pytest
from companion_daemon.world_v2.character_prehistory import digest
from companion_daemon.world_v2.story_import_runner import prepare_story_import
from test_prehistory_life_materials_ingestion import brief, materials_for, NARRATIVE


@pytest.mark.asyncio
async def test_whole_story_preparation_resumes_without_repeating_any_completed_call(tmp_path):
    b = brief().model_copy(update={'world_start_payload_hash': digest({})})
    calls = []
    async def completion(*, messages, **_):
        request = json.loads(messages[1]['content'])
        contract = request['contract']
        calls.append(contract)
        if 'ingestion' in contract:
            value = materials_for().model_dump(mode='json')
        elif contract == 'prehistory-story-link-request.1':
            value = {'contract': 'prehistory-story-link-response.1', 'relations': []}
        elif contract.startswith('story-import-plan-request.'):
            value = {'contract': 'story-import-plan.2', 'event_identities': [
                {'event_ref': ref, 'status': 'new_or_distinct', 'prior_record_id': None,
                 'prior_event_ref': None, 'rationale': 'Fixture source is distinct.'}
                for ref in request['new_event_inventory']], 'cross_story_links': []}
        else:
            assert contract == 'prehistory-semantic-review-request.4'
            value = {'contract': 'prehistory-import-assessment.4', 'records': [
                {'record_index': 0, 'verdict': 'approve', 'rationale': 'Fixture source assessment.'}],
                'story_links': [], 'cross_record_findings': [], 'event_matches': [], 'cross_story_links': [], 'missing_connections': []}
        return json.dumps(value, ensure_ascii=False), {'input_tokens': 100, 'output_tokens': 50}
    params = dict(brief=b, narrative_text=NARRATIVE, archive_id='prehistory-archive:prepared',
                  run_dir=tmp_path / 'run', api_key='fixture', completion=completion)
    first = await prepare_story_import(**params)
    second = await prepare_story_import(**params)
    assert len(calls) == 4 and first == second
    assert first.document.contract == 'character-prehistory-archive.3'
    assert first.review.decision == 'approved'
    with pytest.raises(ValueError, match='bound to a different'):
        await prepare_story_import(**{**params, 'narrative_text': NARRATIVE + ' 后来又发生了一件事。'})
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_rejected_structured_record_is_revised_and_all_stages_recover(tmp_path):
    b = brief().model_copy(update={'world_start_payload_hash': digest({})})
    calls = []
    reviewed = 0
    original = materials_for()
    revised = original.model_copy(update={'blocks': (
        original.blocks[0].model_copy(update={'text': '2015年开学后，她和嘉禾成了朋友。'}),
    )})
    async def completion(*, messages, **_):
        nonlocal reviewed
        packet = json.loads(messages[1]['content'])
        contract = packet['contract']
        calls.append(contract)
        if 'revision-request' in contract:
            assert packet['prior_candidate']['archive']['contract'] == 'character-prehistory-archive.3'
            value = revised.model_dump(mode='json')
        elif 'ingestion' in contract:
            value = original.model_dump(mode='json')
        elif contract == 'prehistory-story-link-request.1':
            value = {'contract': 'prehistory-story-link-response.1', 'relations': []}
        elif contract.startswith('story-import-plan-request.'):
            value = {'contract': 'story-import-plan.2', 'event_identities': [
                {'event_ref': ref, 'status': 'new_or_distinct', 'prior_record_id': None,
                 'prior_event_ref': None, 'rationale': 'Fixture source is distinct.'}
                for ref in packet['new_event_inventory']], 'cross_story_links': []}
        else:
            reviewed += 1
            value = {'contract': 'prehistory-import-assessment.4', 'records': [
                {'record_index': 0, 'verdict': 'reject' if reviewed == 1 else 'approve',
                 'rationale': 'Fixture requests a narrower supported statement.'}],
                'story_links': [], 'cross_record_findings': [], 'event_matches': [], 'cross_story_links': [], 'missing_connections': []}
        return json.dumps(value, ensure_ascii=False), {'input_tokens': 100, 'output_tokens': 50}
    params = dict(brief=b, narrative_text=NARRATIVE, archive_id='prehistory-archive:revise',
                  run_dir=tmp_path / 'run', api_key='fixture', completion=completion)
    package = await prepare_story_import(**params)
    assert package.document.records[0].story.blocks[0].text == revised.blocks[0].text
    assert package == await prepare_story_import(**params)
    assert len(calls) == 8 and reviewed == 2


@pytest.mark.asyncio
async def test_ambiguous_issued_plan_request_is_not_automatically_repeated(tmp_path):
    b = brief().model_copy(update={'world_start_payload_hash': digest({})})
    calls = []
    async def completion(*, messages, **_):
        contract = json.loads(messages[1]['content'])['contract']
        calls.append(contract)
        if 'ingestion' in contract:
            value = materials_for().model_dump(mode='json')
        elif contract == 'prehistory-story-link-request.1':
            value = {'contract': 'prehistory-story-link-response.1', 'relations': []}
        else:
            raise ConnectionError('fixture loses response after request intent')
        return json.dumps(value), {'input_tokens': 100, 'output_tokens': 50}
    params = dict(brief=b, narrative_text=NARRATIVE, archive_id='prehistory-archive:unknown',
                  run_dir=tmp_path / 'run', api_key='fixture', completion=completion)
    with pytest.raises(ConnectionError):
        await prepare_story_import(**params)
    with pytest.raises(RuntimeError, match='no automatic repeat'):
        await prepare_story_import(**params)
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_missing_import_basis_fails_before_any_model_request(tmp_path):
    calls = []
    async def completion(**kwargs):
        calls.append(kwargs)
        raise AssertionError('invalid local basis must not invoke a provider')
    with pytest.raises(ValueError, match='fresh brief'):
        await prepare_story_import(brief=brief(), narrative_text=NARRATIVE,
            archive_id='prehistory-archive:no-basis', run_dir=tmp_path / 'run',
            api_key='fixture', completion=completion)
    assert calls == []


@pytest.mark.asyncio
async def test_stage_recovers_strict_tool_wire_capture_without_a_second_call(tmp_path, monkeypatch):
    from companion_daemon.world_v2.story_import_runner import _run_stage
    from companion_daemon.world_v2 import story_import_runner as runner
    packet = {'instruction': 'fixture stage', 'output_schema': {'type': 'object', 'properties': {
        'required_judgment': {'type': 'array', 'items': {'type': 'string'}, 'default': []}},
        'additionalProperties': False}}
    directory = tmp_path / 'stage'
    calls = []
    async def provider(**kwargs):
        calls.append(kwargs)
        assert kwargs['tools'][0]['function']['parameters']['required'] == ['required_judgment']
        wire = {'choices': [{'finish_reason': 'stop', 'message': {'tool_calls': [
            {'function': {'name': 'story_stage_result', 'arguments': '{"required_judgment": []}'}}]}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 10}}
        (kwargs['attempt_dir'] / 'provider-response.json').write_text(json.dumps({'body_utf8': json.dumps(wire)}))
        raise RuntimeError('fixture interruption after durable wire capture')
    monkeypatch.setattr(runner, '_captured_completion', provider)
    def validate(raw, **_):
        return json.loads(raw)
    args = dict(packet=packet, model_name='fixture', base_url='https://fixture.invalid', api_key='fixture',
                directory=directory, validate=validate)
    with pytest.raises(RuntimeError, match='fixture interruption'):
        await _run_stage(**args)
    assert await _run_stage(**args) == {'required_judgment': []}
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('resumed_capture', [False, True])
async def test_wrong_captured_tool_gets_one_bounded_correction(tmp_path, monkeypatch, resumed_capture):
    from companion_daemon.world_v2.story_import_runner import _run_stage
    from companion_daemon.world_v2 import story_import_runner as runner
    packet = {'instruction': 'fixture source', 'output_schema': {'type': 'object',
        'properties': {'ok': {'type': 'boolean'}}}}
    calls = []
    async def provider(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            wire = {'status_code': 200, 'body_utf8': json.dumps({'choices': [{'finish_reason': 'stop',
                'message': {'tool_calls': [{'function': {'name': 'wrong_tool', 'arguments': '{"ok": true}'}}]}}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 10}})}
            (kwargs['attempt_dir'] / 'provider-response.json').write_text(json.dumps(wire))
            if resumed_capture:
                raise RuntimeError('fixture crash after capture')
            raise ValueError('returned the wrong tool')
        correction = json.loads(kwargs['messages'][-1]['content'])
        assert 'required story tool' in correction['previous_attempt']['failure']
        assert 'wrong_tool' in correction['previous_attempt']['content']
        return '{"ok": true}', {'input_tokens': 100, 'output_tokens': 10}
    monkeypatch.setattr(runner, '_captured_completion', provider)
    params = dict(packet=packet, model_name='deepseek-flash', base_url='https://fixture.invalid',
        api_key='fixture', directory=tmp_path / 'stage', validate=lambda raw, **_: json.loads(raw))
    if resumed_capture:
        with pytest.raises(RuntimeError, match='fixture crash'):
            await _run_stage(**params)
    assert await _run_stage(**params) == {'ok': True}
    assert await _run_stage(**params) == {'ok': True}
    assert len(calls) == 2
    first = json.loads((params['directory'] / 'attempt-01/response.json').read_text())
    assert first['usage_audit']['cost']['estimated_cost_cny'] > 0
