"""Native accepted inner state is a private-history source with narrow scope."""
from copy import deepcopy
import json

import pytest

from companion_daemon.world_v2.context_resolver import query_from_projection
from companion_daemon.world_v2.ledger_context_resolver import ContextRelevanceScope, context_capsule_compiler_from_ledger
from companion_daemon.world_v2.visible_source_composer import compile_visible_source_table
from companion_daemon.world_v2.visible_subjective_source import subjective_source_support
from companion_daemon.world_v2.visible_source_subject_authority import permits_source_subject
from companion_daemon.world_v2.visible_source_reading_experiment import prepare_reading_experiment
from companion_daemon.world_v2.visible_meaning_source_review import _eligible_readings
from test_affect_acceptance_runtime import _runtime, _cursor
from test_visible_source_composer import _request


def accepted_sources():
    runtime, payload = _runtime()
    runtime.accept_runtime_owned(
        handle=runtime.pin_proposal(cursor=_cursor(runtime), proposal_id=payload['proposal_id']),
        actor='worker:affect', source='test:subjective-reading',
    )
    ledger = runtime.ledger
    capsule = context_capsule_compiler_from_ledger(
        ledger=ledger, relevance_scope=ContextRelevanceScope(
            actor_ref='actor:companion', related_subject_refs=('interaction:user:1',),
        ),
    ).compile(query_from_projection(ledger.project(), actor_ref='actor:companion', trigger_ref='event:next-turn'))
    table = compile_visible_source_table(request=_request(capsule), capsule=capsule, include_subjective_history=True)
    return runtime, capsule, table


def test_native_subjective_records_have_owner_time_proof_and_no_external_authority():
    runtime, capsule, table = accepted_sources()
    assert table.as_dict()['contract'] == 'visible-source-row-table.6'
    original = compile_visible_source_table(request=_request(capsule), capsule=capsule)
    assert not any('subjective_history_support' in r for r in original.source_references())
    rows = table.source_references()
    subjective = [r for r in rows if 'subjective_history_support' in r]
    assert {r['review_material']['lane'] for r in subjective} == {'appraisals', 'affect_episodes'}
    preparation = prepare_reading_experiment(beats=('刚才有点难受。',), sources=rows)
    catalog = json.loads(preparation.payload_json)['catalog']
    selections = [r for r in catalog if ['subjective_history', 'companion'] in r['permissions']]
    assert {r['value'] for r in selections} == {'disappointment', 'misunderstanding', 'hurt'}
    for selected in selections:
        assert selected['permissions'] == [['subjective_history', 'companion']]
        assert selected['source_owner_ref'] == 'actor:companion'
        row = rows[selected['source_ref_indexes'][0]]
        material = row['review_material']
        owner, support = subjective_source_support(material, row['source_ref'])
        assert owner == 'actor:companion'
        # subject_ref is the interpreted user; it must never become ownership.
        if material['lane'] == 'appraisals':
            assert material['item']['value']['subject_ref'] == 'interaction:user:1'
        event, _ = runtime.ledger.lookup_event_commit(material['item']['value']['origin']['accepted_event_ref'])
        assert event.event_type == support['source_event_type']
        for mode in ('actual_event_or_state', 'past_intention', 'past_utterance'):
            assert selected['reading_id'] not in _eligible_readings({'mode': mode, 'subject_role': 'companion'}, catalog)
    assert _eligible_readings({'mode': 'past_subjective_state', 'subject_role': 'companion'}, catalog)
    assert not _eligible_readings({'mode': 'past_subjective_state', 'subject_role': 'counterpart'}, catalog)


@pytest.mark.parametrize('fault', ['body', 'proof', 'owner', 'event_type', 'future', 'withhold', 'baseline_alias'])
def test_changed_or_ineligible_subjective_material_never_keeps_reading_authority(fault):
    _, _, table = accepted_sources()
    row = deepcopy(next(r for r in table.source_references() if 'subjective_history_support' in r and r['review_material']['lane'] == 'appraisals'))
    material = row['review_material']
    if fault == 'body':
        material['item']['value']['hypotheses'][0]['meaning'] = 'altered belief'
    elif fault == 'proof':
        material['item']['source_hash'] = 'f' * 64
    elif fault == 'owner':
        material['actor_ref'] = 'other:companion'
    elif fault == 'event_type':
        material['item']['source_bindings'][0]['authority_type'] = 'ObservationRecorded'
    elif fault == 'future':
        material['scope']['logical_at'] = '2000-01-01T00:00:00+00:00'
    elif fault == 'withhold':
        material['privacy_class'] = 'withhold'
    else:
        row['support_eligibility'] = 'baseline_only'
    assert not permits_source_subject(row=row, pointer='/item/value/hypotheses/0/meaning', claim_scope='subjective_history', subject_role='companion')


def test_subjective_reading_cannot_support_an_external_cause_action_or_user_mind():
    from companion_daemon.world_v2.visible_candidate_meaning import prepare_candidate_meaning
    from companion_daemon.world_v2.visible_independent_meanings import IndependentMeaning, prepare_independent_meanings_sources
    from companion_daemon.world_v2.visible_source_witness_experiment import _json

    _, _, table = accepted_sources()
    meaning = prepare_candidate_meaning(
        beats=('我当时失落，所以没有回你。你当时也难受。',), compact=True, explicit_questions=True,
        question_conditions=True, beat_conditions=True, require_complete_reading=True, complete_reading_version='10',
    )
    raw = _json({'contract': 'visible-candidate-meaning.10', 'decisions': [{
        'beat_index': 0, 'reading_complete': True, 'unresolved_details': [], 'hypothetical_conditions': [], 'questions': [],
        'meanings': [
            {'proposition': 'companion当时感到失落', 'mode': 'past_subjective_state', 'subject_role': 'companion'},
            {'proposition': 'companion当时没有回复counterpart', 'mode': 'actual_event_or_state', 'subject_role': 'companion'},
            {'proposition': 'counterpart当时感到难受', 'mode': 'past_subjective_state', 'subject_role': 'counterpart'},
        ],
    }]})
    assert len(meaning.inspect_response(raw)['facts']) == 3
    prepared = prepare_independent_meanings_sources(
        meanings=(IndependentMeaning(meaning, raw), IndependentMeaning(meaning, raw)),
        sources=table.source_references(), shared_strings=True,
    )
    pin = json.loads(prepared.payload_json)
    selected = next(r['reading_id'] for r in pin['catalog'] if r['value'] == 'disappointment')
    answer = _json({'contract': pin['contract'], 'fact_decisions': [
        {'fact_id': f['fact_id'], 'source_support': True, 'reading_ids': [selected]} for f in pin['facts']
    ]})
    inspected = prepared.inspect_response(answer)
    assert [d['outcome'] for d in inspected['fact_decisions']] == ['supported', 'rejected', 'rejected'] * 2
    assert all(d['rejection_reason'] == 'source_permission_denied' for d in inspected['fact_decisions'] if d['outcome'] == 'rejected')
    assert inspected['fixed_fact_beat_outcomes'] == ['facts_rejected']
