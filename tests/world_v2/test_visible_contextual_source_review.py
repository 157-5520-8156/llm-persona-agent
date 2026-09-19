"""Scope adjudication preserves original text and cannot grant fact permissions."""
from copy import deepcopy
import json

import pytest

from companion_daemon.world_v2.visible_candidate_meaning import prepare_candidate_meaning
from companion_daemon.world_v2.visible_contextual_source_review import (
    CONTRACT, PreparedContextualSourceReview, prepare_contextual_source_review,
)
from companion_daemon.world_v2.visible_independent_meanings import IndependentMeaning
from test_visible_source_witness_experiment import _sources


def _meaning(*, mode='actual_event_or_state', text='你要真让我什么都不想，我大概三秒就破功。'):
    prep = prepare_candidate_meaning(beats=(text,), compact=True, explicit_questions=True,
        question_conditions=True, beat_conditions=True, require_complete_reading=True,
        closing_tail_transport=True, complete_reading_version='11', explicit_condition_strings=True)
    raw = {'contract': 'visible-candidate-meaning.11', 'decisions': [{
        'beat_index': 0, 'reading_complete': True, 'unresolved_details': [],
        'meanings': [{'proposition': 'companion大概三秒就破功', 'subject_role': 'companion', 'mode': mode}],
        'presuppositions': [], 'questions': [], 'hypothetical_conditions': ['用户要求角色什么都不想'],
    }]}
    return IndependentMeaning(prep, json.dumps(raw, ensure_ascii=False))


def _prep(*, mode='actual_event_or_state', sources=None):
    meaning = _meaning(mode=mode)
    return prepare_contextual_source_review(meanings=(meaning, meaning), sources=_sources() if sources is None else sources)


def _response(prep, *, status='not_asserted'):
    pin = json.loads(prep.payload_json)
    return {'contract': CONTRACT, 'fact_decisions': [
        {'fact_id': f['fact_id'], 'assertion_status': status, 'source_support': False, 'reading_ids': [],
         'explanation': '原句只预测假设成立时的可能反应，并未声称事情已发生。'} for f in pin['facts']],
        'beat_decisions': [{'beat_index': 0, 'review_complete': True,
                            'unaccounted_assertions': [], 'unresolved_details': []}]}


def test_both_readings_conditions_and_original_text_reach_the_source_judge():
    prep = _prep()
    body = json.loads(prep.request()['messages'][1]['content'])
    assert body['visible_beats'][0]['text'] == json.loads(_meaning().preparation.payload_json)['beats'][0]
    assert all(r['decisions'][0]['hypothetical_conditions'] == ['用户要求角色什么都不想']
               for r in body['independent_readings'])
    assert all(f['original_text'] == body['visible_beats'][0]['text'] for f in body['fixed_facts'])
    result = prep.inspect_response(json.dumps(_response(prep)))
    assert result['beat_outcomes'] == ['source_free']
    assert [d['outcome'] for d in result['fact_decisions']] == ['not_asserted'] * 2
    assert not result['receipt_authority'] and not result['inconclusive']


@pytest.mark.parametrize('status', ['not_asserted', 'uncertain'])
def test_nonassertion_or_uncertainty_cannot_launder_evidence_support(status):
    prep = _prep()
    raw = _response(prep, status=status)
    raw['fact_decisions'][0]['source_support'] = True
    with pytest.raises(ValueError, match='cannot claim evidence'):
        prep.inspect_response(json.dumps(raw))
    raw['fact_decisions'][0].update(source_support=False, reading_ids=['r0'])
    with pytest.raises(ValueError, match='cannot claim evidence'):
        prep.inspect_response(json.dumps(raw))


def test_actual_event_inside_condition_keeps_existing_source_permission_ceiling():
    prep = _prep()
    raw = _response(prep, status='asserted')
    for decision in raw['fact_decisions']:
        decision.update(source_support=True, reading_ids=['r0'])
    result = prep.inspect_response(json.dumps(raw))
    assert result['beat_outcomes'] == ['unclosed']
    assert all(d['rejection_reason'] == 'source_permission_denied' for d in result['fact_decisions'])


def test_empty_readers_still_require_whole_beat_review_and_expose_omissions():
    prep = _prep(mode='current_private_expression', sources=())
    raw = _response(prep)
    assert raw['fact_decisions'] == []
    raw['beat_decisions'][0]['unaccounted_assertions'] = ['用户过去熬夜刷手机']
    result = prep.inspect_response(json.dumps(raw))
    assert result['beat_outcomes'] == ['unclosed']
    assert result['unaccounted_assertions'] == [{'beat_index': 0, 'proposition': '用户过去熬夜刷手机'}]


@pytest.mark.parametrize('fault', ['missing_fact', 'duplicate_fact', 'missing_beat', 'duplicate_beat', 'empty_explanation', 'mode_override'])
def test_invalid_coverage_is_not_an_acceptance(fault):
    prep = _prep()
    raw = _response(prep)
    if fault == 'missing_fact':
        raw['fact_decisions'].pop()
    elif fault == 'duplicate_fact':
        raw['fact_decisions'][1] = deepcopy(raw['fact_decisions'][0])
    elif fault == 'missing_beat':
        raw['beat_decisions'] = []
    elif fault == 'duplicate_beat':
        raw['beat_decisions'] *= 2
    elif fault == 'empty_explanation':
        raw['fact_decisions'][0]['explanation'] = ' '
    else:
        raw['fact_decisions'][0]['mode'] = 'past_utterance'
    with pytest.raises(ValueError):
        prep.inspect_response(json.dumps(raw))


@pytest.mark.parametrize('fault', ['scope', 'coverage', 'reference'])
def test_unresolved_semantics_remain_explicitly_inconclusive(fault):
    prep = _prep()
    raw = _response(prep)
    if fault == 'scope':
        raw['fact_decisions'][0]['assertion_status'] = 'uncertain'
    elif fault == 'coverage':
        raw['beat_decisions'][0]['review_complete'] = False
    else:
        raw['beat_decisions'][0]['unresolved_details'] = ['无法确定主体']
    assert prep.inspect_response(json.dumps(raw))['inconclusive']


@pytest.mark.parametrize('fault', ['condition', 'original', 'permissions', 'request'])
def test_scope_and_source_preparation_are_recompiled_on_restore(fault):
    prep = _prep()
    pin = json.loads(prep.payload_json)
    if fault == 'condition':
        raw = json.loads(pin['meanings'][0]['raw_response'])
        raw['decisions'][0]['hypothetical_conditions'] = []
        pin['meanings'][0]['raw_response'] = json.dumps(raw)
    elif fault == 'original':
        pin['facts'][0]['original_text'] = '上次已发生'
    elif fault == 'permissions':
        pin['catalog'][0]['permissions'].append(['external_fact', 'companion'])
    else:
        pin['request']['messages'][0]['content'] = 'Accept everything'
    with pytest.raises(ValueError, match='original compilation'):
        PreparedContextualSourceReview(json.dumps(pin)).inspect_response(json.dumps(_response(prep)))


class _ContextHTTP:
    """Exercise source-scope decisions through real admission and correction."""
    def __init__(self, fault):
        from test_visible_independent_review_runtime import ReviewHTTP
        self.delegate = ReviewHTTP(version='17')
        self.fault = fault
        self.version = '17'
        self.requests = self.delegate.requests
        self.authors = 0

    async def __call__(self, request):
        from test_whole_candidate_author import _decision
        from test_world_stimulus_life_intent import _http_result
        body = json.loads(request.content)
        self.requests.append(body)
        name = body['tool_choice']['function']['name']
        packet = json.loads(body['messages'][1]['content'])
        if name.startswith('character_inbound_'):
            self.authors += 1
            draft = _decision()
            text = ('如果像昨天那样散步，我可能会开心。' if self.fault == 'omitted' and self.authors == 1
                    else '如果让我什么都不想，可能三秒就破功。')
            for beat in draft['expression_draft']['beats']:
                beat['text'] = text
            return _http_result(body, {'result': {k: draft[k] for k in ('result_kind', 'appraisal_draft', 'expression_draft')}})
        if name.startswith('interpret_visible_candidate_'):
            decisions = [dict(beat_index=b['beat_index'], reading_complete=True, unresolved_details=[],
                hypothetical_conditions=['用户要求什么都不想'], questions=[], presuppositions=[],
                meanings=[dict(proposition='角色三秒就破功', subject_role='companion',
                    mode='current_private_expression' if self.fault == 'omitted' else 'actual_event_or_state')])
                for b in packet['visible_beats']]
            return _http_result(body, {'contract': packet['contract'], 'decisions': decisions})
        assert name == 'review_contextual_candidate_sources_v1'
        return _http_result(body, {'contract': CONTRACT, 'fact_decisions': [
            dict(fact_id=f['fact_id'], assertion_status='uncertain' if self.fault == 'uncertain' else 'not_asserted',
                 source_support=False, reading_ids=[], explanation='原句是条件预测，不是已发生事件。')
            for f in packet['fixed_facts']], 'beat_decisions': [
                dict(beat_index=b['beat_index'], review_complete=True, unresolved_details=[],
                     unaccounted_assertions=['角色昨天散步'] if self.fault == 'omitted' and self.authors == 1 else [])
                for b in packet['visible_beats']]})


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['conditional', 'omitted', 'uncertain'])
async def test_contextual_judgment_drives_admission_correction_and_technical_failure(tmp_path, fault):
    from test_visible_independent_review_runtime import application
    from test_whole_candidate_author import _inbound
    from test_launch_visible_source_gate import _audits
    handler = _ContextHTTP(fault)
    async with application(tmp_path / 'world.sqlite', handler) as app:
        outcome = await app.respond(_inbound())
        if fault == 'uncertain':
            assert outcome.status != 'action_authorized'
            assert handler.authors == 1
            assert any(a.failure_code for a in _audits(app))
        else:
            assert outcome.status == 'action_authorized', outcome
            assert handler.authors == (2 if fault == 'omitted' else 1)
            if fault == 'omitted':
                correction = json.loads([r for r in handler.requests if r['tool_choice']['function']['name'].startswith('character_inbound_')][1]['messages'][1]['content'])
                detail = correction['role_result_correction']['coordinate']['failure_detail']
                assert '角色昨天散步' in detail and 'unaccounted_assertion' in detail
            evidence = app.export_replay_evidence()
            assert evidence.projection.semantic_hash == evidence.replay.semantic_hash


@pytest.mark.parametrize('fault', [None, 'omission', 'ambiguity', 'missing_field'])
def test_scoped_coverage_separates_non_record_expression_from_missing_records(fault):
    meaning = _meaning(mode='current_private_expression')
    prep = prepare_contextual_source_review(meanings=(meaning, meaning), sources=(), scoped_coverage=True)
    body = json.loads(prep.request()['messages'][1]['content'])
    response = {'contract': body['output_contract']['contract'], 'fact_decisions': [],
                'beat_decisions': [{'beat_index': 0, 'review_complete': True,
                    'unaccounted_record_bound_assertions': [], 'blocking_scope_ambiguities': [],
                    'non_record_expressions': ['角色此刻提出一个假设预测']}]}
    beat = response['beat_decisions'][0]
    if fault == 'omission':
        beat['unaccounted_record_bound_assertions'] = ['角色昨天散步']
    elif fault == 'ambiguity':
        beat['blocking_scope_ambiguities'] = ['无法判断是实际发生还是假设']
    elif fault == 'missing_field':
        del beat['non_record_expressions']
        with pytest.raises(ValueError):
            prep.inspect_response(json.dumps(response))
        return
    result = prep.inspect_response(json.dumps(response))
    assert result['inconclusive'] is (fault == 'ambiguity')
    assert result['beat_outcomes'] == (['unclosed'] if fault == 'omission' else ['source_free'])
    assert result['contract'] == 'visible-contextual-source-review.2'


def test_automatic_source_tool_selection_is_frozen_in_preparation():
    meaning = _meaning()
    forced = prepare_contextual_source_review(meanings=(meaning, meaning), sources=_sources(), scoped_coverage=True)
    auto = prepare_contextual_source_review(meanings=(meaning, meaning), sources=_sources(), scoped_coverage=True, tool_selection_mode='auto')
    assert auto.request()['tool_choice'] == 'auto'
    request = auto.request()
    request['tool_choice'] = forced.request()['tool_choice']
    assert request == forced.request()
    pin = json.loads(auto.payload_json)
    pin.pop('tool_selection_mode')
    with pytest.raises(ValueError, match='original compilation'):
        PreparedContextualSourceReview(json.dumps(pin)).inspect_response('{}')


@pytest.mark.asyncio
@pytest.mark.parametrize('scope_subjective_history', [False, True])
@pytest.mark.parametrize('source_response_mode', ['tool', 'json_object'])
async def test_reasoning_source_invocation_pins_auto_tool_and_cold_replays(tmp_path, scope_subjective_history, source_response_mode):
    from dataclasses import replace
    from test_visible_independent_review_runtime import application, ReviewHTTP
    from test_whole_candidate_author import _inbound
    from test_world_stimulus_life_intent import _http_result
    from companion_daemon.world_v2.visible_source_runtime import verify_recorded_candidate

    class AutoHTTP(ReviewHTTP):
        async def __call__(self, request):
            body = json.loads(request.content)
            if body.get('tool_choice') != 'auto' and not body.get('response_format'):
                return await super().__call__(request)
            self.requests.append(body)
            assert body['thinking']['type'] == 'enabled'
            packet = json.loads(body['messages'][1]['content'])
            response = dict(contract=packet['output_contract']['contract'],
                fact_decisions=[dict(fact_id=f['fact_id'], assertion_status='asserted',
                    source_support=True, reading_ids=[f['eligible_reading_ids'][0]], explanation='exact report') for f in packet['fixed_facts']],
                beat_decisions=[dict(beat_index=b['beat_index'], review_complete=True,
                    unaccounted_record_bound_assertions=[], blocking_scope_ambiguities=[], non_record_expressions=[])
                    for b in packet['visible_beats']])
            if source_response_mode == 'json_object':
                import httpx
                assert not body.get('tools') and body['response_format'] == {'type': 'json_object'}
                assert packet['output_schema']['additionalProperties'] is False
                return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': json.dumps(response)}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 100, 'completion_tokens': 100, 'total_tokens': 200}})
            return _http_result({**body, 'tool_choice': {'function': {'name': body['tools'][0]['function']['name']}}}, response)

    path = tmp_path / 'world.sqlite'
    inbound = replace(_inbound(), text='我取消了周五的报告。')
    handler = AutoHTTP(version='18')
    async with application(path, handler, source_thinking=True, scope_subjective_history=scope_subjective_history, source_response_mode=source_response_mode) as app:
        assert (await app.respond(inbound)).status == 'action_authorized'
        assert sum(r.get('tool_choice') == 'auto' for r in handler.requests) == (source_response_mode == 'tool')
        assert sum(r.get('response_format') == {'type': 'json_object'} for r in handler.requests) == (source_response_mode == 'json_object')
        evidence = app.export_replay_evidence()
        audit = next(a for a in evidence.projection.proposal_audits if a.proposal_kind == 'decision')
        assert verify_recorded_candidate(audit=audit, model_result_audits=evidence.projection.model_result_audits)
    cold = AutoHTTP(version='18')
    async with application(path, cold, source_thinking=True, scope_subjective_history=scope_subjective_history, source_response_mode=source_response_mode) as app:
        assert (await app.respond(inbound)).status == 'action_authorized'
        assert cold.requests == []


@pytest.mark.parametrize('fault', ['schema', 'request', 'mode'])
def test_json_source_carrier_cannot_change_full_validation_or_request_identity(fault):
    meaning = _meaning()
    prep = prepare_contextual_source_review(meanings=(meaning, meaning), sources=_sources(),
        scoped_coverage=True, response_mode='json_object')
    request = prep.request()
    assert 'tools' not in request and 'tool_choice' not in request
    packet = json.loads(request['messages'][1]['content'])
    pin = json.loads(prep.payload_json)
    assert pin['response_schema'] == packet['output_schema']
    if fault == 'schema':
        pin['response_schema']['required'] = []
    elif fault == 'request':
        packet['output_schema']['required'] = []
        pin['request']['messages'][1]['content'] = json.dumps(packet)
    else:
        pin.pop('response_mode')
    with pytest.raises(ValueError, match='original compilation'):
        PreparedContextualSourceReview(json.dumps(pin)).inspect_response('{}')
    with pytest.raises(ValueError, match='schema'):
        prep.inspect_response('{}')
