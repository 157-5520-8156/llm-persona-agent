"""Real typed lifecycle sources with fixture semantic judgments, not qualification."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio

from companion_daemon.world_v2.context_resolver import query_from_projection
from companion_daemon.world_v2.deliberation import TriggerMessage
from companion_daemon.world_v2.ledger_context_resolver import ContextRelevanceScope, context_capsule_compiler_from_ledger
from companion_daemon.world_v2.schemas import Observation
from companion_daemon.world_v2.visible_independent_review_receipt import (
    IndependentVisibleReviewReceipt, IndependentVisibleReviewRejected,
    meaning_preparation, prepare_independent_visible_review, prepare_meaning_call,
    prepare_source_call, record_independent_visible_review,
    verify_independent_visible_review_receipt,
)
from companion_daemon.world_v2.visible_lifecycle_readings import INSTRUCTION, LIFECYCLE_FIELDS
from companion_daemon.world_v2.visible_review_protocols import LIFECYCLE_FIELD_PROTOCOL, RECORD_DEPENDENCY_PROTOCOL
from companion_daemon.world_v2.visible_source_composer import compile_visible_source_table
from companion_daemon.world_v2.visible_source_review_receipt import VisibleReviewAuthorBinding
from companion_daemon.world_v2.visible_source_witness_experiment import _json
from companion_daemon.world_v2.shared_string_view import unpack_shared_strings
from test_chat_life_intent_runtime import INTENT, _run_http_journey
from test_completed_activity_context import open_journey_ledger
from test_visible_independent_review_receipt import _binding, _expected
from test_visible_source_composer import _request
from test_visible_source_review_receipt import _candidate


@pytest_asyncio.fixture
async def ended_sources(tmp_path, monkeypatch):
    monkeypatch.setenv('COMPANION_DISABLE_DEBUG_USAGE_LEDGER', '1')
    result, events, _, output, _ = await _run_http_journey(
        tmp_path, monkeypatch, intent={**INTENT, 'duration_seconds': 180},
        duration_minutes=5, prefer_complete=True,
    )
    assert result['completed']
    ledger = open_journey_ledger(output)
    try:
        inbound = next(event for event in events if event['event_type'] == 'ObservationRecorded')
        event, commit = ledger.lookup_event_commit(inbound['event_id'])
        observation = Observation.model_validate_json(event.payload_json, strict=True)
        capsule = context_capsule_compiler_from_ledger(
            ledger=ledger, relevance_scope=ContextRelevanceScope(actor_ref='agent:companion'),
        ).compile(query_from_projection(ledger.project(), actor_ref='agent:companion', trigger_ref=event.event_id))
        request = _request(capsule).model_copy(update={'trigger_message': TriggerMessage(
            event_ref=event.event_id, event_payload_hash='sha256:' + event.payload_hash,
            source_world_revision=commit.world_revision, observation_ref=observation.observation_id,
            actor=observation.actor, channel=observation.channel, reply_target='fixture:local',
            text=observation.text,
        )})
        table = compile_visible_source_table(request=request, capsule=capsule)
        return SimpleNamespace(request=request), table
    finally:
        ledger.close()


def _prepare(case, table, protocol, subject='companion'):
    prepared = prepare_independent_visible_review(
        candidate=_candidate(case, texts=('我先前那项活动已经结束了。',)),
        source_table=table, source_ref_aliases={}, review_protocol=protocol,
        source_response_mode='json_object', scope_permission_context=True,
    )
    raw = _json({'contract': 'visible-candidate-meaning.16', 'decisions': [{
        'beat_index': 0, 'reading_complete': True, 'unresolved_details': [],
        'meanings': [{'proposition': '角色先前那项活动的生命周期已经结束',
                      'mode': 'actual_event_or_state', 'subject_role': subject}],
        'presuppositions': [], 'questions': [], 'hypothetical_conditions': [],
    }]})
    raws = (raw, raw)
    call = prepare_source_call(prepared=prepared, meaning_raw_responses=raws)
    return prepared, raws, call, json.loads(call.request['messages'][1]['content'])


def _response(body, reading):
    return {'contract': body['output_contract']['contract'],
        'fact_decisions': [{'fact_id': fact['fact_id'], 'record_dependency': 'record_bound',
            'source_support': True, 'reading_ids': [reading], 'fact_value_selections': [],
            'explanation': 'Fixture judgment: the exact status establishes only the same activity ended.'}
            for fact in body['fixed_facts']],
        'beat_decisions': [{'beat_index': 0, 'review_complete': True,
            'unaccounted_record_bound_assertions': [], 'blocking_scope_ambiguities': [],
            'non_record_expressions': []}]}


def _arguments(prepared, raws, call, response):
    raw = _json(response)
    return dict(prepared=prepared,
        author=VisibleReviewAuthorBinding(model_call_id='model-call:author', request_hash='a' * 64,
            proposal_material_hash=hashlib.sha256(prepared.as_dict()['candidate_json'].encode()).hexdigest()),
        meaning_raw_responses=raws,
        meaning_reviews=tuple(_binding(prepare_meaning_call(prepared=prepared, meaning_index=i), value, i)
                              for i, value in enumerate(raws)),
        source_review=_binding(call, raw, 2), source_raw_response=raw,
        rejected_meanings=(None, None))


def _lifecycle(body):
    return next(item for item in unpack_shared_strings(body['source_materials'])
        if item['material'].get('item', {}).get('value', {}).get('context_kind') == 'completed_activity')


@pytest.mark.asyncio
async def test_lifecycle_fields_gain_explicit_permission_and_cold_receipt(ended_sources):
    case, table = ended_sources
    before = table.payload_json
    old, raws, old_call, old_body = _prepare(case, table, RECORD_DEPENDENCY_PROTOCOL)
    prepared, _, call, body = _prepare(case, table, LIFECYCLE_FIELD_PROTOCOL)
    assert meaning_preparation(old) == meaning_preparation(prepared)
    assert INSTRUCTION in call.request['messages'][0]['content']
    assert INSTRUCTION not in old_call.request['messages'][0]['content']
    assert old_body['source_selection_contract']['contract'] == 'visible-permission-context-selection.1'
    assert body['source_selection_contract']['contract'] == 'visible-permission-context-selection.2'
    assert not any(f['eligible_reading_ids'] for f in old_body['fixed_facts'])
    card = _lifecycle(body)
    by_id = {r['reading_id']: r for r in card['readings']}
    allowed = set(body['fixed_facts'][0]['eligible_reading_ids'])
    assert allowed and all(by_id[r]['field'] in LIFECYCLE_FIELDS for r in allowed)
    status_id = next(r for r in allowed if by_id[r]['field'] == '/item/value/status')
    args = _arguments(prepared, raws, call, _response(body, status_id))
    receipt = record_independent_visible_review(**args)
    assert receipt.contract == 'visible-source-review-receipt.22'
    restored = IndependentVisibleReviewReceipt.model_validate_json(receipt.model_dump_json(), strict=True)
    assert verify_independent_visible_review_receipt(receipt=restored, **_expected(args)) == receipt
    assert table.payload_json == before
    assert prepare_source_call(prepared=old, meaning_raw_responses=raws) == old_call
    legacy_args = _arguments(old, raws, old_call, _response(old_body, status_id))
    with pytest.raises((ValueError, IndependentVisibleReviewRejected)):
        record_independent_visible_review(**legacy_args)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['intention_text', 'wrong_subject', 'omission', 'unsupported_result', 'downgrade'])
async def test_lifecycle_permission_cannot_authorize_intention_or_omit_world_results(ended_sources, fault):
    case, table = ended_sources
    prepared, raws, call, body = _prepare(case, table, LIFECYCLE_FIELD_PROTOCOL,
        subject='counterpart' if fault == 'wrong_subject' else 'companion')
    # Wrong-subject cards may be omitted, so retain the genuine original card's
    # reading ID for the attempted substitution rather than inventing evidence.
    _, _, _, proper_body = _prepare(case, table, LIFECYCLE_FIELD_PROTOCOL)
    card = _lifecycle(proper_body)
    status_id = next(r['reading_id'] for r in card['readings'] if r['field'] == '/item/value/status')
    response = _response(body, status_id)
    if fault == 'intention_text':
        intention_id = next(r['reading_id'] for r in card['readings']
            if r['field'] == '/item/value/accepted_intention/text')
        assert intention_id not in body['fixed_facts'][0]['eligible_reading_ids']
        response['fact_decisions'][0]['reading_ids'] = [intention_id]
    elif fault == 'omission':
        response['beat_decisions'][0]['unaccounted_record_bound_assertions'] = ['这次已经完整走了一圈']
    elif fault == 'unsupported_result':
        response['fact_decisions'][0].update(source_support=False, reading_ids=[],
            explanation='Fixture rejection: ended does not establish successful fulfillment.')
    elif fault == 'downgrade':
        response['contract'] = 'visible-contextual-source-review.5'
    with pytest.raises((ValueError, IndependentVisibleReviewRejected)):
        record_independent_visible_review(**_arguments(prepared, raws, call, response))


@pytest.mark.asyncio
async def test_nonrecord_scope_cannot_claim_lifecycle_support(ended_sources):
    prepared, raws, call, body = _prepare(*ended_sources, LIFECYCLE_FIELD_PROTOCOL)
    card = _lifecycle(body)
    status_id = next(r['reading_id'] for r in card['readings'] if r['field'] == '/item/value/status')
    response = _response(body, status_id)
    changed = deepcopy(response)
    changed['fact_decisions'][0]['record_dependency'] = 'not_record_bound'
    with pytest.raises(ValueError, match='cannot claim evidence'):
        record_independent_visible_review(**_arguments(prepared, raws, call, changed))


@pytest.mark.asyncio
async def test_v22_preserves_exact_accepted_fact_value_receipt(tmp_path):
    from companion_daemon.world_v2.proposal_envelope import DecisionProposal
    from companion_daemon.world_v2.visible_source_composer import VisibleSourceTable
    from test_visible_record_dependency import _v21_args
    args = await _v21_args(tmp_path)
    pin = args['prepared'].as_dict()
    prepared = prepare_independent_visible_review(
        candidate=DecisionProposal.model_validate_json(pin['candidate_json'], strict=True),
        source_table=VisibleSourceTable(pin['source_table_json']),
        source_ref_aliases=pin['source_ref_aliases'], review_protocol=LIFECYCLE_FIELD_PROTOCOL,
        source_response_mode='json_object', scope_permission_context=True,
    )
    raws = args['meaning_raw_responses']
    call = prepare_source_call(prepared=prepared, meaning_raw_responses=raws)
    response = json.loads(args['source_raw_response'])
    response['contract'] = 'visible-contextual-source-review.6'
    current = _arguments(prepared, raws, call, response)
    receipt = record_independent_visible_review(**current)
    assert verify_independent_visible_review_receipt(receipt=receipt, **_expected(current)) == receipt
    response['fact_decisions'][0]['fact_value_selections'][0]['quoted_value'] = '原文不存在的值'
    with pytest.raises((ValueError, IndependentVisibleReviewRejected)):
        record_independent_visible_review(**_arguments(prepared, raws, call, response))
