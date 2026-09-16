"""Permission-scoped presentation keeps all usable evidence and whole-text gates."""
from copy import deepcopy
import json

import pytest

from companion_daemon.world_v2.shared_string_view import unpack_shared_strings
from companion_daemon.world_v2.visible_contextual_source_review import PreparedContextualSourceReview, prepare_contextual_source_review
from companion_daemon.world_v2.visible_meaning_source_review import _eligible_readings
from companion_daemon.world_v2.visible_source_reading_experiment import _catalog
from companion_daemon.world_v2.visible_source_scope_selection import select_subjective_history
from companion_daemon.world_v2.visible_source_witness_experiment import prepare_witness_experiment
from test_visible_contextual_source_review import _meaning
from test_visible_subjective_source import accepted_sources


def _pair(mode='actual_event_or_state'):
    _, _, table = accepted_sources()
    meaning = _meaning(mode=mode)
    args = dict(meanings=(meaning, meaning), sources=table.source_references(), scoped_coverage=True)
    return prepare_contextual_source_review(**args), prepare_contextual_source_review(**args, scope_subjective_history=True)


@pytest.mark.parametrize('mode', ['actual_event_or_state', 'past_utterance', 'past_intention', 'current_private_expression', 'past_subjective_state'])
def test_every_fixed_fact_retains_exact_permissions_and_required_materials(mode):
    complete, selected = _pair(mode)
    old, new = json.loads(complete.payload_json), json.loads(selected.payload_json)
    assert new['sources'] == old['sources'] and new['facts'] == old['facts']
    for fact in old['facts']:
        assert _eligible_readings(fact, old['catalog']) == _eligible_readings(fact, new['catalog'])
    old_body, new_body = (json.loads(p.request()['messages'][1]['content']) for p in (complete, selected))
    before = unpack_shared_strings(old_body['source_materials'])
    after = unpack_shared_strings(new_body['source_materials'])
    proof = new['source_selection']
    assert after == [before[i] for i in proof['retained_material_indexes']]
    assert old_body['visible_beats'] == new_body['visible_beats']
    assert old_body['independent_readings'] == new_body['independent_readings']
    assert old_body['fixed_facts'] == new_body['fixed_facts']
    if mode == 'past_subjective_state':
        assert not proof['omitted_material_indexes']
        assert old['catalog'] == new['catalog']
    else:
        assert proof['omitted_material_indexes']
        assert all(before[i]['material']['authority'] == 'accepted_subjective_history_not_external_fact'
                   for i in proof['omitted_material_indexes'])
        hidden = {r['reading_id'] for r in old['catalog']} - {r['reading_id'] for r in new['catalog']}
        schema = selected.request()['tools'][0]['function']['parameters']
        allowed = schema['properties']['fact_decisions']['items']['properties']['reading_ids']['items'].get('enum', [])
        assert hidden.isdisjoint(allowed)


@pytest.mark.parametrize('fault', ['eligibility', 'proof', 'owner', 'new_permission'])
def test_unverified_or_wider_authority_cannot_be_silently_omitted(fault):
    _, _, table = accepted_sources()
    witness = prepare_witness_experiment(beats=('现在想静一静。',), sources=table.source_references(), source_owner_semantics=True, prehistory_authority=True)
    pin = json.loads(witness.payload_json)
    catalog = _catalog(pin, report_uptake=True, content_fields_only=True, prehistory_authority=True)
    target = next(r for r in catalog if r['permissions'] == [['subjective_history', 'companion']])
    index = target['material_index']
    if fault == 'new_permission':
        target['permissions'].append(['external_fact', 'companion'])
    else:
        row = next(row for row, i in zip(pin['sources'], pin['material_indexes'], strict=True) if i == index and row.get('support_eligibility') == 'eligible')
        if fault == 'eligibility':
            for alias, position in zip(pin['sources'], pin['material_indexes'], strict=True):
                if position == index:
                    alias['support_eligibility'] = 'baseline_only'
        elif fault == 'proof':
            row['review_material']['item']['source_hash'] = '0' * 64
        else:
            row['support_subject_ref'] = 'actor:other'
    retained, _, _ = select_subjective_history(witness_pin=pin, catalog=catalog, facts=[])
    assert index in retained


def test_hidden_subjective_sources_do_not_exempt_newly_discovered_past_claims():
    _, selected = _pair('current_private_expression')
    raw = {'contract': 'visible-contextual-source-review.2', 'fact_decisions': [], 'beat_decisions': [{
        'beat_index': 0, 'review_complete': True, 'non_record_expressions': ['当前表达'],
        'unaccounted_record_bound_assertions': ['角色昨天感到失落'], 'blocking_scope_ambiguities': [],
    }]}
    result = selected.inspect_response(json.dumps(raw))
    assert result['beat_outcomes'] == ['unclosed']
    assert result['unaccounted_assertions'][0]['proposition'] == '角色昨天感到失落'


@pytest.mark.parametrize('fault', ['flag', 'selection_proof', 'hidden_fact', 'hidden_permission'])
def test_selection_recompiles_against_full_original_sources_and_both_readers(fault):
    _, selected = _pair()
    pin = json.loads(selected.payload_json)
    if fault == 'flag':
        pin.pop('scope_subjective_history')
    elif fault == 'selection_proof':
        pin['source_selection']['omitted_material_indexes'] = []
    elif fault == 'hidden_fact':
        reading = json.loads(pin['meanings'][1]['raw_response'])
        reading['decisions'][0]['meanings'][0]['mode'] = 'past_subjective_state'
        pin['meanings'][1]['raw_response'] = json.dumps(reading)
    else:
        pin['catalog'] = deepcopy(json.loads(_pair('past_subjective_state')[0].payload_json)['catalog'])
    with pytest.raises(ValueError, match='original compilation'):
        PreparedContextualSourceReview(json.dumps(pin)).inspect_response('{}')


def test_one_readers_past_inner_claim_keeps_sources_despite_other_readers_current_expression():
    _, _, table = accepted_sources()
    mixed = (_meaning(mode='current_private_expression'), _meaning(mode='past_subjective_state'))
    prep = prepare_contextual_source_review(meanings=mixed, sources=table.source_references(),
        scoped_coverage=True, scope_subjective_history=True)
    pin = json.loads(prep.payload_json)
    assert not pin['source_selection']['omitted_material_indexes']
    assert {f['meaning_index'] for f in pin['facts']} == {1}
