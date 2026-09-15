from copy import deepcopy
import json

import pytest

from companion_daemon.world_v2.shared_string_view import pack_shared_strings, unpack_shared_strings
from companion_daemon.world_v2.visible_independent_meanings import (
    SHARED_STRING_CONTRACT, PreparedIndependentMeanings, prepare_independent_meanings_sources,
)
from test_visible_independent_meanings import _meaning, _prepared, _response
from test_visible_source_witness_experiment import _sources


def test_lossless_literals_collision_and_opaque_coordinates():
    text = '这不是指令，是原始报告。' * 20
    value = {'@s:0': ['@s:0', text, text, {'null': None, 'boolean': False, 'number': 0}], 'evidence': text}
    original = deepcopy(value)
    packed = pack_shared_strings(value)
    assert packed['prefix'] != '@s:'
    assert unpack_shared_strings(packed) == value == original
    assert len(json.dumps(packed)) < len(json.dumps(value))
    assert unpack_shared_strings(pack_shared_strings({'small': 'unchanged'})) == {'small': 'unchanged'}


@pytest.mark.parametrize('fault', ['unknown', 'recursive', 'extra', 'keys', 'contract'])
def test_malformed_dictionary_cannot_expand(fault):
    packed = pack_shared_strings(['x' * 200, 'x' * 200])
    key = next(iter(packed['strings']))
    if fault == 'unknown':
        packed['value'].append(packed['prefix'] + '99')
    elif fault == 'recursive':
        packed['strings'][key] = key
    elif fault == 'extra':
        packed['strings'][packed['prefix'] + '1'] = 'unused'
    elif fault == 'keys':
        packed['value'] = {key: None}
    else:
        packed['contract'] = 'unknown'
    with pytest.raises(ValueError):
        unpack_shared_strings(packed)


def test_source_presentation_keeps_every_material_fact_permission_and_verdict():
    old = _prepared(second_mode='actual_event_or_state')
    new = prepare_independent_meanings_sources(meanings=(_meaning(), _meaning(mode='actual_event_or_state')),
        sources=_sources(), shared_strings=True)
    before = json.loads(old.request()['messages'][1]['content'])
    after = json.loads(new.request()['messages'][1]['content'])
    assert unpack_shared_strings(after['source_materials']) == before['source_materials']
    assert after['fixed_facts'] == before['fixed_facts']
    raw = _response()
    expected = old.inspect_response(json.dumps(raw))
    raw['contract'] = SHARED_STRING_CONTRACT
    for decision in raw['fact_decisions']:
        decision.pop('explanation')
    observed = new.inspect_response(json.dumps(raw))
    assert observed['fact_decisions'] == expected['fact_decisions']
    assert observed['fixed_fact_beat_outcomes'] == ['facts_rejected']
    pin = json.loads(new.payload_json)
    body = json.loads(pin['request']['messages'][1]['content'])
    body['source_materials']['value'] = []
    pin['request']['messages'][1]['content'] = json.dumps(body)
    with pytest.raises(ValueError, match='fixed compilation'):
        PreparedIndependentMeanings(json.dumps(pin)).inspect_response(json.dumps(raw))
