"""Inventory transport and downstream scope; fixtures do not prove model quality."""
import json

import pytest

from companion_daemon.world_v2.visible_candidate_meaning import prepare_candidate_meaning, verify_candidate_meaning_preparation
from companion_daemon.world_v2.visible_independent_meanings import IndependentMeaning, prepare_independent_meanings_sources
from companion_daemon.world_v2.visible_meaning_reselection import prepare_meaning_reselection
from test_visible_source_witness_experiment import _sources


def preparation():
    return prepare_candidate_meaning(beats=('谢谢你上次陪我等车。',), compact=True,
        explicit_questions=True, question_conditions=True, beat_conditions=True,
        require_complete_reading=True, complete_reading_version='14')


def response():
    return {'contract': 'visible-candidate-meaning.14', 'decisions': [{
        'beat_index': 0, 'reading_complete': True, 'unresolved_details': [],
        'non_factual_reading': '角色当前表达感谢',
        'facts': [{'proposition': '用户上次陪角色等车', 'mode': 'actual_event_or_state', 'subject_role': 'counterpart'}],
    }]}


def test_background_fact_enters_existing_source_scope_without_duplicate_inventories():
    prep = preparation()
    verify_candidate_meaning_preparation(prep)
    raw = json.dumps(response())
    facts = prep.inspect_response(raw)['facts']
    assert len(facts) == 1 and facts[0]['original_text'] == '谢谢你上次陪我等车。'
    assert facts[0]['subject_role'] == 'counterpart'
    probe = prepare_independent_meanings_sources(
        meanings=(IndependentMeaning(prep, raw), IndependentMeaning(prep, raw)),
        sources=_sources(), shared_strings=True, content_fields_only=True,
    )
    pin = json.loads(probe.payload_json)
    assert len(pin['facts']) == 2  # Independent readings remain separate.
    inspected = probe.inspect_response(json.dumps({'contract': pin['contract'], 'fact_decisions': [
        {'fact_id': f['fact_id'], 'source_support': True, 'reading_ids': []} for f in pin['facts']
    ]}))
    assert inspected['fixed_fact_beat_outcomes'] == ['facts_rejected']


@pytest.mark.parametrize('fault', ['missing_facts', 'missing_rest', 'current_mode', 'incomplete', 'omitted_beat', 'too_many'])
def test_invalid_inventory_uses_same_reader_structural_correction(fault):
    value = response()
    beat = value['decisions'][0]
    if fault == 'missing_facts':
        del beat['facts']
    elif fault == 'missing_rest':
        del beat['non_factual_reading']
    elif fault == 'current_mode':
        beat['facts'][0]['mode'] = 'current_private_expression'
    elif fault == 'incomplete':
        beat['unresolved_details'] = ['无法确定指代']
    elif fault == 'omitted_beat':
        value['decisions'] = []
    else:
        beat['facts'] *= 33
    raw = json.dumps(value)
    with pytest.raises(ValueError):
        preparation().inspect_response(raw)
    correction = prepare_meaning_reselection(meaning=preparation(), rejected_raw=raw)
    assert len(correction.inspect_response(json.dumps(response()))['facts']) == 1


def test_empty_inventory_is_not_a_semantic_qualification_or_an_eraser():
    first = json.dumps(response())
    second = response()
    second['decisions'][0]['facts'] = []
    raw = json.dumps(second)
    reading = preparation().inspect_response(raw)
    assert reading['semantic_qualification'] == 'unproven'
    assert reading['receipt_authority'] is False
    probe = prepare_independent_meanings_sources(
        meanings=(IndependentMeaning(preparation(), first), IndependentMeaning(preparation(), raw)),
        sources=_sources(), shared_strings=True, content_fields_only=True,
    )
    assert len(json.loads(probe.payload_json)['facts']) == 1
    second['decisions'][0]['non_factual_reading'] = '  '
    with pytest.raises(ValueError):
        preparation().inspect_response(json.dumps(second))
