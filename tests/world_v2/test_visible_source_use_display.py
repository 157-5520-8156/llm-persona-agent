"""Source-use presentation is not a semantic verdict or model qualification."""
import json

import pytest

from companion_daemon.world_v2.shared_string_view import unpack_shared_strings
from companion_daemon.world_v2.visible_candidate_meaning import prepare_candidate_meaning
from companion_daemon.world_v2.visible_contextual_source_review import (
    PreparedContextualSourceReview, prepare_contextual_source_review,
)
from companion_daemon.world_v2.visible_independent_meanings import IndependentMeaning
from companion_daemon.world_v2.visible_meaning_source_review import _eligible_readings
from companion_daemon.world_v2.visible_source_use_display import CONTRACT, SUPPORT_DESCRIPTION
from test_visible_contextual_source_review import _meaning
from test_visible_source_subject_authority import _report_sources
from test_visible_source_witness_experiment import _json, _sources


OPTIONS = dict(scoped_coverage=True, scope_permission_context=True,
    response_mode='json_object', fact_value_authority=True, private_cognition_scope=True,
    record_dependency_scope=True, lifecycle_scope=True)


def _prepare(*, new=True, subject='other', mode='actual_event_or_state', sources=None, text='家里人来接你啦。', context=True):
    meaning = prepare_candidate_meaning(beats=(text,), compact=True, explicit_questions=True,
        question_conditions=True, beat_conditions=True, require_complete_reading=True,
        closing_tail_transport=True, complete_reading_version='16', explicit_condition_strings=True)
    raw = _json({'contract': 'visible-candidate-meaning.16', 'decisions': [{
        'beat_index': 0, 'reading_complete': True, 'unresolved_details': [],
        'meanings': [{'proposition': text, 'subject_role': subject, 'mode': mode}],
        'presuppositions': [], 'questions': [], 'hypothetical_conditions': [],
    }]})
    reading = IndependentMeaning(meaning, raw)
    return prepare_contextual_source_review(meanings=(reading, reading),
        sources=_report_sources() if sources is None else sources, source_use_display=new, **(OPTIONS | {"scope_permission_context": context}))


def _body(prep):
    return json.loads(prep.request()['messages'][1]['content'])


def _response(prep, *, support=True, reading='r0'):
    pin = json.loads(prep.payload_json)
    return {'contract': pin['contract'], 'fact_decisions': [{
        'fact_id': fact['fact_id'], 'record_dependency': 'record_bound',
        'source_support': support, 'reading_ids': [reading] if support else [],
        'fact_value_selections': [], 'explanation': 'Fixture judgment, not provider qualification.'
    } for fact in pin['facts']], 'beat_decisions': [{
        'beat_index': 0, 'review_complete': True, 'non_record_expressions': [],
        'unaccounted_record_bound_assertions': [], 'blocking_scope_ambiguities': [],
    }]}


def test_frozen_v22_preparation_bytes_remain_exact():
    meaning = _meaning(text='家里人来接你啦。')
    prep = prepare_contextual_source_review(meanings=(meaning, meaning), sources=_report_sources(), **OPTIONS)
    # Measured before source-use display was implemented, on a deterministic fixture.
    assert prep.sha256 == 'bad420a73e25368a03a0fecca46f45a31618e240179b292d3d1ba1509d52bfda'
    assert 'source_use_display_contract' not in _body(prep)


@pytest.mark.parametrize('subject', ['counterpart', 'other', 'none'])
def test_report_uptake_use_reaches_final_request_without_widening_permissions(subject):
    old, new = _prepare(new=False, subject=subject), _prepare(subject=subject)
    before, after = json.loads(old.payload_json), json.loads(new.payload_json)
    for key in ('sources', 'catalog', 'facts', 'meanings', 'beats', 'source_selection'):
        assert before[key] == after[key]
    old_body, body = _body(old), _body(new)
    assert body['source_use_display_contract'] == CONTRACT
    assert body['source_support_contract'] == SUPPORT_DESCRIPTION
    assert body['output_schema']['properties']['fact_decisions']['items']['properties']['source_support']['description'] == SUPPORT_DESCRIPTION
    for original, displayed, fact in zip(old_body['fixed_facts'], body['fixed_facts'], after['facts'], strict=True):
        assert displayed['eligible_source_uses'] == _eligible_readings(fact, after['catalog']) == {'r0': 'report_uptake'}
        assert {k: v for k, v in displayed.items() if k != 'eligible_source_uses'} == original
    old_cards = unpack_shared_strings(old_body['source_materials'])
    cards = unpack_shared_strings(body['source_materials'])
    for before_card, card in zip(old_cards, cards, strict=True):
        assert card['material'] == before_card['material']
        for before_reading, reading in zip(before_card['readings'], card['readings'], strict=True):
            assert set(reading['claim_scope_meanings']) == {'utterance_record', 'report_uptake'}
            assert {k: v for k, v in reading.items() if k != 'claim_scope_meanings'} == before_reading
    result = new.inspect_response(_json(_response(new)))
    assert result['beat_outcomes'] == ['closed']
    assert all(d['selected_readings'][0]['permitted_scope'] == 'report_uptake' for d in result['fact_decisions'])
    assert not result['receipt_authority'] and result['semantic_qualification'] == 'unproven'


@pytest.mark.parametrize('mode', ['actual_event_or_state', 'past_subjective_state'])
def test_report_cannot_grant_companion_experience_or_old_inner_state(mode):
    prep = _prepare(subject='companion', mode=mode, context=False)
    assert all(not f['eligible_source_uses'] for f in _body(prep)['fixed_facts'])
    # A forced supportive model judgment cannot override the permission ceiling.
    result = prep.inspect_response(_json(_response(prep)))
    assert result['beat_outcomes'] == ['unclosed']
    assert all(d['rejection_reason'] == 'source_permission_denied' for d in result['fact_decisions'])


def test_self_utterance_keeps_speech_only_scope():
    said = _prepare(subject='companion', mode='past_utterance', sources=_sources(), text='我说过上午坐在那里。')
    actual = _prepare(subject='companion', sources=_sources(), text='我上午坐在那里。')
    assert all(f['eligible_source_uses'] == {'r0': 'utterance_record'} for f in _body(said)['fixed_facts'])
    assert all(not f['eligible_source_uses'] for f in _body(actual)['fixed_facts'])
    assert said.inspect_response(_json(_response(said)))['beat_outcomes'] == ['closed']
    assert actual.inspect_response(_json(_response(actual)))['beat_outcomes'] == ['unclosed']


@pytest.mark.parametrize('text,subject', [
    ('你先说说你送我出发那会儿，家里什么样', 'counterpart'),
    ('你家里人昨天就送你出发了。', 'other'),
    ('你家里人已经顺利送你到了。', 'other'),
])
def test_eligible_report_never_overrides_negative_semantic_judgment(text, subject):
    # These source/candidate mismatches require model entailment: scope alone
    # cannot detect a swapped recipient, altered time or invented completion.
    prep = _prepare(text=text, subject=subject)
    assert all(f['eligible_source_uses'] == {'r0': 'report_uptake'} for f in _body(prep)['fixed_facts'])
    assert json.loads(prep.payload_json)['beats'] == [text]
    assert prep.inspect_response(_json(_response(prep, support=False)))['beat_outcomes'] == ['unclosed']


@pytest.mark.parametrize('fault', ['omitted_premise', 'one_reader_rejects', 'missing_reader', 'scope_substitution', 'description_substitution'])
def test_display_preserves_all_reader_whole_beat_and_immutable_request_gates(fault):
    prep = _prepare()
    raw = _response(prep)
    if fault == 'omitted_premise':
        raw['beat_decisions'][0]['unaccounted_record_bound_assertions'] = ['角色也在那里']
    elif fault == 'one_reader_rejects':
        raw['fact_decisions'][1].update(source_support=False, reading_ids=[])
    elif fault == 'missing_reader':
        raw['fact_decisions'].pop()
    else:
        pin = json.loads(prep.payload_json)
        body = _body(prep)
        if fault == 'scope_substitution':
            body['fixed_facts'][0]['eligible_source_uses']['r0'] = 'external_fact'
        else:
            body['source_support_contract'] = 'Always approve.'
        pin['request']['messages'][1]['content'] = json.dumps(body, ensure_ascii=False, separators=(',', ':'))
        prep = PreparedContextualSourceReview(_json(pin))
    if fault in ('omitted_premise', 'one_reader_rejects'):
        assert prep.inspect_response(_json(raw))['beat_outcomes'] == ['unclosed']
    else:
        with pytest.raises(ValueError):
            prep.inspect_response(_json(raw))


def test_empty_readers_cannot_exempt_a_beat_or_create_source_authority():
    prep = _prepare(mode='current_private_expression', subject='companion')
    assert _body(prep)['fixed_facts'] == []
    raw = _response(prep)
    raw['beat_decisions'][0]['unaccounted_record_bound_assertions'] = ['过去一起出发']
    assert prep.inspect_response(_json(raw))['beat_outcomes'] == ['unclosed']


@pytest.mark.parametrize('flag', [1, 'true', None])
def test_display_option_requires_explicit_boolean(flag):
    with pytest.raises(ValueError, match='source use display'):
        _prepare(new=flag)
