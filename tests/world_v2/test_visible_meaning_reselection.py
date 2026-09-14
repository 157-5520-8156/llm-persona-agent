import json

import pytest

from companion_daemon.world_v2.visible_meaning_reselection import PreparedMeaningReselection, prepare_meaning_reselection
from companion_daemon.world_v2.visible_source_witness_experiment import _json
from test_visible_complete_meaning_versions import preparation


def reading():
    return {'contract': 'visible-candidate-meaning.9', 'decisions': [{
        'beat_index': 0, 'reading_complete': True, 'unresolved_details': [], 'hypothetical_conditions': [],
        'meanings': [{'mode': 'actual_event_or_state', 'proposition': '原句未指明对象的修理已完成。', 'subject_role': 'none'}],
        'questions': [],
    }]}


@pytest.mark.parametrize('fault', ['missing', 'syntax', 'coverage'])
def test_same_reader_correction_keeps_original_input_and_precise_failure(fault):
    original = preparation('9')
    value = reading()
    if fault == 'missing':
        del value['decisions'][0]['reading_complete']
    elif fault == 'coverage':
        value['decisions'][0]['beat_index'] = 1
    raw = _json(value)
    if fault == 'syntax':
        raw = raw[:-1]
    correction = prepare_meaning_reselection(meaning=original, rejected_raw=raw)
    body = json.loads(correction.request()['messages'][1]['content'])
    assert set(body) == {'contract', 'visible_beats', 'invalid_prior_reading', 'structural_failure'}
    assert body['invalid_prior_reading'] == raw
    assert body['visible_beats'] == json.loads(original.request()['messages'][1]['content'])['visible_beats']
    assert correction.request()['tools'] == original.request()['tools']
    if fault == 'missing':
        assert body['structural_failure']['errors'][0]['loc'] == ['decisions', 0, 'reading_complete']
    accepted = correction.inspect_response(_json(reading()))
    assert accepted['facts'][0]['mode'] == 'actual_event_or_state'
    assert accepted == original.inspect_response(_json(reading()))
    assert PreparedMeaningReselection(correction.payload_json).inspect_response(_json(reading())) == accepted


@pytest.mark.parametrize('inconclusive', [False, True])
def test_valid_reading_cannot_retry_to_chase_a_more_convenient_semantic_judgment(inconclusive):
    value = reading()
    if inconclusive:
        value['decisions'][0].update(reading_complete=False, unresolved_details=['unclear language'])
    with pytest.raises(ValueError, match='structurally valid'):
        prepare_meaning_reselection(meaning=preparation('9'), rejected_raw=_json(value))


def test_reselection_rejects_replaced_original_and_still_rejects_bad_output():
    value = reading()
    del value['decisions'][0]['reading_complete']
    raw = _json(value)
    prepared = prepare_meaning_reselection(meaning=preparation('9'), rejected_raw=raw)
    with pytest.raises(ValueError):
        prepared.inspect_response(raw)
    pin = json.loads(prepared.payload_json)
    pin['request']['messages'][1]['content'] = '{}'
    with pytest.raises(ValueError, match='differs'):
        PreparedMeaningReselection(_json(pin)).inspect_response(_json(reading()))
