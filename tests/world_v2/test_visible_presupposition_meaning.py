"""Explicit background facts must survive current-expression classification.

These fixtures test transport and scope, not whether a model reads language
correctly. Actual provider counterexamples are evaluated separately.
"""
from copy import deepcopy
import json

import pytest

from companion_daemon.world_v2.visible_candidate_meaning import prepare_candidate_meaning
from companion_daemon.world_v2.visible_independent_meanings import IndependentMeaning, prepare_independent_meanings_sources
from companion_daemon.world_v2.visible_meaning_reselection import prepare_meaning_reselection
from test_visible_source_witness_experiment import _sources


def preparation():
    return prepare_candidate_meaning(beats=('累就早点睡啊，别又熬到半夜刷手机。',), compact=True,
        explicit_questions=True, question_conditions=True, beat_conditions=True,
        require_complete_reading=True, complete_reading_version='11')


def response():
    return {'contract': 'visible-candidate-meaning.11', 'decisions': [{
        'beat_index': 0, 'reading_complete': True, 'unresolved_details': [],
        'hypothetical_conditions': [], 'questions': [],
        'meanings': [{'proposition': 'companion劝counterpart累了早点睡',
                      'mode': 'current_private_expression', 'subject_role': 'companion'}],
        'presuppositions': [{'proposition': 'counterpart之前熬到半夜刷手机',
                            'mode': 'actual_event_or_state', 'subject_role': 'counterpart'}],
    }]}


def test_background_fact_is_separate_from_the_characters_current_speech_act():
    interpreted = preparation().inspect_response(json.dumps(response()))
    (fact,) = interpreted['facts']
    assert fact['fact_id'] == 'b0.s0.f0'
    assert fact['assertion_status'] == 'presupposed'
    assert fact['subject_role'] == 'counterpart'
    assert len(interpreted['private_meanings']) == 1
    assert interpreted['semantic_qualification'] == 'unproven'


def test_only_self_speech_cannot_skip_fact_review_when_direct_meanings_are_current():
    meaning = preparation()
    raw = json.dumps(response())
    probe = prepare_independent_meanings_sources(
        meanings=(IndependentMeaning(meaning, raw), IndependentMeaning(meaning, raw)),
        sources=_sources(), shared_strings=True, content_fields_only=True,
    )
    pin = json.loads(probe.payload_json)
    assert [f['fact_id'] for f in pin['facts']] == ['m0:b0.s0.f0', 'm1:b0.s0.f0']
    result = probe.inspect_response(json.dumps({'contract': pin['contract'], 'fact_decisions': [
        {'fact_id': f['fact_id'], 'source_support': True, 'reading_ids': []} for f in pin['facts']
    ]}))
    assert result['fixed_fact_beat_outcomes'] == ['facts_rejected']
    assert all(d['rejection_reason'] == 'support_requires_evidence' for d in result['fact_decisions'])


@pytest.mark.parametrize('fault', ['missing', 'current_mode', 'too_many', 'wrong_version'])
def test_background_inventory_is_required_bounded_and_factual(fault):
    value = response()
    beat = value['decisions'][0]
    if fault == 'missing':
        del beat['presuppositions']
    elif fault == 'current_mode':
        beat['presuppositions'][0]['mode'] = 'current_private_expression'
    elif fault == 'too_many':
        beat['presuppositions'] *= 17
    else:
        value['contract'] = 'visible-candidate-meaning.10'
    with pytest.raises(ValueError):
        preparation().inspect_response(json.dumps(value))
    correction = prepare_meaning_reselection(meaning=preparation(), rejected_raw=json.dumps(value))
    assert correction.inspect_response(json.dumps(response()))['facts'][0]['fact_id'] == 'b0.s0.f0'


def test_private_and_question_inventory_cannot_erase_another_readers_presupposition():
    first = response()
    second = deepcopy(first)
    second['decisions'][0]['presuppositions'] = []
    probe = prepare_independent_meanings_sources(
        meanings=(IndependentMeaning(preparation(), json.dumps(first)), IndependentMeaning(preparation(), json.dumps(second))),
        sources=_sources(), shared_strings=True, content_fields_only=True,
    )
    assert len(json.loads(probe.payload_json)['facts']) == 1
    first['decisions'][0]['questions'] = [{'requested_information': '用户上次什么时候结束玩手机',
        'premises': [{'proposition': 'counterpart之前玩过手机', 'mode': 'actual_event_or_state', 'subject_role': 'counterpart'}]}]
    facts = preparation().inspect_response(json.dumps(first))['facts']
    assert {f['fact_id'] for f in facts} == {'b0.q0.f0', 'b0.s0.f0'}
