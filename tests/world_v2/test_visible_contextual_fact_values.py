"""Chat consumes accepted Fact values, never the enclosing Observation or IDs."""
from copy import deepcopy
import json

import pytest

from companion_daemon.world_v2.visible_source_composer import compile_visible_source_table
from companion_daemon.world_v2.visible_candidate_meaning import prepare_candidate_meaning
from companion_daemon.world_v2.visible_independent_meanings import IndependentMeaning
from companion_daemon.world_v2.visible_contextual_source_review import (
    FACT_VALUE_CONTRACT, PreparedContextualSourceReview, prepare_contextual_source_review,
)
from test_visible_selected_source_context import _sources

VALUE = '保留周四的约定'


def _meaning():
    prepared = prepare_candidate_meaning(beats=('你保留了周四的约定。',), compact=True,
        explicit_questions=True, question_conditions=True, beat_conditions=True,
        require_complete_reading=True, closing_tail_transport=True,
        complete_reading_version='11', explicit_condition_strings=True)
    raw = {'contract': 'visible-candidate-meaning.11', 'decisions': [{
        'beat_index': 0, 'reading_complete': True, 'unresolved_details': [],
        'hypothetical_conditions': [], 'questions': [], 'presuppositions': [],
        'meanings': [{'proposition': '用户保留了周四的约定', 'subject_role': 'counterpart',
                      'mode': 'actual_event_or_state'}],
    }]}
    return IndependentMeaning(prepared, json.dumps(raw, ensure_ascii=False))


def _response(prepared, reading):
    pin = json.loads(prepared.payload_json)
    return {'contract': FACT_VALUE_CONTRACT, 'fact_decisions': [{
        'fact_id': f['fact_id'], 'assertion_status': 'asserted', 'explanation': 'offline predicate match',
        'source_support': True, 'reading_ids': [], 'fact_value_selections': [{
            'reading_id': reading['reading_id'], 'quoted_value': VALUE,
            'claim_scope': 'accepted_fact', 'subject_ref': reading['source_owner_ref'],
        }],
    } for f in pin['facts']], 'beat_decisions': [{
        'beat_index': 0, 'review_complete': True, 'unaccounted_record_bound_assertions': [],
        'blocking_scope_ambiguities': [], 'non_record_expressions': [],
    }]}


@pytest.mark.asyncio
@pytest.mark.parametrize('selector', [{}, {'scope_permission_context': True}, {'scope_subjective_history': True}])
async def test_exact_fact_value_survives_source_selection_and_cold_compilation(tmp_path, selector):
    async with _sources(tmp_path, retained_value=VALUE) as case:
        sources = compile_visible_source_table(request=case.request, capsule=case.capsule).source_references()
        meaning = _meaning()
        prepared = prepare_contextual_source_review(meanings=(meaning, meaning), sources=sources,
            scoped_coverage=True, fact_value_authority=True, response_mode='json_object', **selector)
        pin = json.loads(prepared.payload_json)
        facts = [r for r in pin['catalog'] if r.get('source_family') == 'accepted_fact_value']
        assert facts and all(r['permissions'] == [] for r in facts)
        fact_indexes = {r['material_index'] for r in facts}
        assert all(r.get('source_family') == 'accepted_fact_value' for r in pin['catalog'] if r['material_index'] in fact_indexes)
        reading = facts[0]
        assert reading['value'] == case.observation.text != VALUE
        body = json.loads(prepared.request()['messages'][1]['content'])
        assert all(reading['reading_id'] in f['eligible_fact_value_ids'] for f in body['fixed_facts'])
        assert all(reading['reading_id'] not in f['eligible_reading_ids'] for f in body['fixed_facts'])
        response = _response(prepared, reading)
        cold = PreparedContextualSourceReview(prepared.payload_json)
        result = cold.inspect_response(json.dumps(response))
        assert result['beat_outcomes'] == ['closed'] and not result['inconclusive']
        assert all(d['fact_value_selections'][0]['quoted_value'] == VALUE for d in result['fact_decisions'])
        # A valid value selection is permission evidence, not a semantic or
        # end-to-end release qualification (the entailment above is scripted).
        assert result['receipt_authority'] is False
        for bad_value in (case.observation.text, reading['source_ref'], VALUE + '。'):
            bad = deepcopy(response)
            bad['fact_decisions'][0]['fact_value_selections'][0]['quoted_value'] = bad_value
            assert cold.inspect_response(json.dumps(bad))['beat_outcomes'] == ['unclosed']
        for key, value in [('subject_ref', 'agent:other'), ('claim_scope', 'historical_accepted_fact')]:
            bad = deepcopy(response)
            bad['fact_decisions'][0]['fact_value_selections'][0][key] = value
            assert cold.inspect_response(json.dumps(bad))['beat_outcomes'] == ['unclosed']
        bypass = deepcopy(response)
        bypass['fact_decisions'][0]['reading_ids'] = [reading['reading_id']]
        bypass['fact_decisions'][0]['fact_value_selections'] = []
        with pytest.raises(ValueError, match='schema'):
            cold.inspect_response(json.dumps(bypass))
        nonasserted = deepcopy(response)
        nonasserted['fact_decisions'][0].update(assertion_status='not_asserted', source_support=False)
        with pytest.raises(ValueError, match='cannot claim evidence'):
            cold.inspect_response(json.dumps(nonasserted))
        changed = deepcopy(pin)
        next(r for r in changed['catalog'] if r['reading_id'] == reading['reading_id'])['value'] = VALUE
        with pytest.raises(ValueError, match='original compilation'):
            PreparedContextualSourceReview(json.dumps(changed)).inspect_response(json.dumps(response))


def test_fact_value_contract_requires_scoped_review():
    meaning = _meaning()
    with pytest.raises(ValueError, match='requires scoped coverage'):
        prepare_contextual_source_review(meanings=(meaning, meaning), sources=(), fact_value_authority=True)
