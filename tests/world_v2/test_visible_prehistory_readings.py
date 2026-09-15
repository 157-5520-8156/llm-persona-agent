"""Real imported/retained sources -> versioned text authority -> fixed review.

Mock judgments test transport and scope, not semantic entailment quality.
"""
from copy import deepcopy
import json

import pytest

from companion_daemon.world_v2.visible_source_reading_experiment import (
    PREHISTORY_CONTRACT, PreparedReadingExperiment, prepare_reading_experiment,
)
from companion_daemon.world_v2.visible_source_subject_authority import permits_source_subject
from companion_daemon.world_v2.visible_source_composer import compile_visible_source_table
from companion_daemon.world_v2.visible_independent_meanings import (
    IndependentMeaning, prepare_independent_meanings_sources,
)
from companion_daemon.world_v2.visible_candidate_meaning import prepare_candidate_meaning
from companion_daemon.world_v2.prehistory_memory_source import prehistory_memory_binding
from test_prehistory_memory_source import _install, _choice
from test_prehistory_chat_context import _capsule
from test_character_prehistory import started_ledger
from test_visible_source_composer import _request
from test_visible_source_reading_experiment import _reply
from test_visible_candidate_meaning import _raw


def check_historical_reading(rows, *, lane, text, pointer):
    """Also exercised on actual Core pull/prefetch source tables."""
    old = prepare_reading_experiment(beats=(text,), sources=rows, content_fields_only=True)
    new = prepare_reading_experiment(beats=(text,), sources=rows, content_fields_only=True, prehistory_authority=True)
    pin = json.loads(new.payload_json)
    old_pin = json.loads(old.payload_json)
    old_witness = json.loads(old_pin['witness_preparation_json'])
    witness = json.loads(pin['witness_preparation_json'])
    indexes = {index for index, material in enumerate(witness['shown_materials']) if material.get('lane') == lane}
    readings = [r for r in pin['catalog'] if r['material_index'] in indexes]
    reading = next(r for r in readings if r['value'] == text)
    assert reading['pointer'] == pointer
    assert all(r['pointer'] == pointer for r in readings)
    assert set(map(tuple, reading['permissions'])) == {
        ('external_fact', 'companion'), ('external_fact', 'other'),
        ('utterance_record', 'companion'), ('utterance_record', 'other'),
    }
    assert any(r['material_index'] in indexes and r['pointer'] != pointer for r in old_pin['catalog'])
    assert all(['external_fact', 'other'] not in r['permissions'] for r in old_pin['catalog'] if r['material_index'] in indexes)
    for key in ('sources', 'shown_materials', 'material_indexes'):
        assert witness[key] == old_witness[key]
    answer = _reply(text, reading['reading_id'], 'external_fact', 'other')
    answer['contract'] = PREHISTORY_CONTRACT
    result = new.inspect_response(json.dumps(answer))
    assert result['model_verdicts'] == ['closed'] and not result['receipt_authority']
    # The intermediate witness consumer must use the same new permissions.
    assert result['inspection_contract'] == 'visible-source-witness-inspection.4'
    assert prepare_reading_experiment(beats=(text,), sources=rows, content_fields_only=True) == old
    for scope, role in [('external_fact', 'counterpart'), ('environment', 'other'),
                        ('accepted_intention', 'companion'), ('activity_lifecycle', 'companion')]:
        changed = deepcopy(answer)
        changed['decisions'][0]['parts'][0].update(claim_scope=scope, subject_role=role)
        with pytest.raises(ValueError):
            new.inspect_response(json.dumps(changed))
    changed = deepcopy(pin)
    old_metadata = next(r for r in old_pin['catalog'] if r['material_index'] == reading['material_index'] and r['pointer'] != pointer)
    target = next(r for r in changed['catalog'] if r['reading_id'] == reading['reading_id'])
    target.update(pointer=old_metadata['pointer'], value=old_metadata['value'])
    with pytest.raises(ValueError, match='pinned compilation'):
        PreparedReadingExperiment(json.dumps(changed)).inspect_response(json.dumps(answer))
    return new, reading


@pytest.fixture
def retained(tmp_path):
    ledger = started_ledger(tmp_path / 'history.sqlite')
    try:
        row = _install(ledger)
        binding = prehistory_memory_binding(row)
        pending = _choice(ledger, binding)
        active = _choice(ledger, binding, before=pending, status='active')
        capsule = _capsule(ledger)
        rows = compile_visible_source_table(request=_request(capsule), capsule=capsule).source_references()
        yield ledger, row, binding, active, rows
    finally:
        ledger.close()


def test_retained_memory_narrative_supports_declared_historical_people(retained):
    _, row, _, _, rows = retained
    check_historical_reading(rows, lane='active_memory_candidates', text=row.record.statement,
                             pointer='/item/value/source_excerpts/0/text')


def test_forgotten_record_does_not_keep_direct_text_authority(retained):
    ledger, _, binding, active, _ = retained
    _choice(ledger, binding, before=active, status='forgotten')
    capsule = _capsule(ledger)
    assert not capsule.active_memory_candidates.items
    rows = compile_visible_source_table(request=_request(capsule), capsule=capsule).source_references()
    assert not any(r['review_material'].get('authority') == 'retained_character_prehistory_exact_excerpt_only' for r in rows)


@pytest.mark.parametrize('fault', ['metadata', 'text', 'participant', 'owner', 'withhold', 'binding'])
def test_authority_cannot_be_restored_by_changing_a_source_row(retained, fault):
    *_, rows = retained
    row = deepcopy(next(r for r in rows if r['review_material'].get('lane') == 'active_memory_candidates'))
    pointer = '/item/value/source_excerpts/0/text'
    if fault == 'metadata':
        pointer = '/item/value/source_excerpts/0/prehistory/entities/0/label'
    elif fault == 'owner':
        row['support_subject_ref'] = 'actor:other'
    elif fault == 'withhold':
        row['review_material']['privacy_class'] = 'withhold'
    elif fault == 'binding':
        row['review_material']['item']['source_bindings'] = []
    else:
        source = row['review_material']['item']['value']['source_excerpts'][0]
        if fault == 'text':
            source['text'] += '今天又做了一次。'
        else:
            source['prehistory']['participant_refs'] = ['user:counterpart']
    assert not permits_source_subject(row=row, pointer=pointer, claim_scope='external_fact',
                                      subject_role='other', prehistory_authority=True)


def test_fixed_facts_use_new_authority_without_changing_interpretations(retained):
    _, row, _, _, rows = retained
    text = row.record.statement
    prep = prepare_candidate_meaning(beats=(text,))
    raw = _raw()
    part = raw['decisions'][0]['parts'][0]
    part.update(text=text, interpretation='校刊同学参与核对稿件。', requested_unknowns=[])
    part['meanings'][0].update(proposition='校刊同学参与核对稿件。', subject_role='other')
    meanings = (IndependentMeaning(prep, json.dumps(raw)),) * 2
    for authority in (False, True):
        compiled = prepare_independent_meanings_sources(meanings=meanings, sources=rows,
            shared_strings=True, content_fields_only=True, prehistory_authority=authority)
        pin = json.loads(compiled.payload_json)
        reading = next(r for r in pin['catalog'] if r['pointer'] == '/item/value/source_excerpts/0/text')
        response = {'contract': pin['contract'], 'fact_decisions': [
            {'fact_id': f['fact_id'], 'source_support': True, 'reading_ids': [reading['reading_id']]}
            for f in pin['facts']]}
        result = compiled.inspect_response(json.dumps(response))
        assert result['fixed_fact_beat_outcomes'] == ['facts_supported' if authority else 'facts_rejected']
        assert result['semantic_qualification'] == 'unproven'
