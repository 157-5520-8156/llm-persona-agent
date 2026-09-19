"""v19 exact-value receipts cold-check source, protocol and invocation bindings.

Semantic verdicts and immutable audit anchors here are fixtures, not real
provider qualification or evidence that the companion's prose is faithful.
"""
from copy import deepcopy
import hashlib
import json

import pytest

from companion_daemon.world_v2.visible_independent_review_receipt import (
    IndependentVisibleReviewReceipt,
    IndependentVisibleReviewRejected,
    prepare_independent_visible_review,
    prepare_meaning_call,
    prepare_source_call,
    record_independent_visible_review,
    verify_independent_visible_review_receipt,
)
from companion_daemon.world_v2.visible_review_protocols import FACT_VALUE_PROTOCOL
from companion_daemon.world_v2.visible_source_composer import compile_visible_source_table
from companion_daemon.world_v2.visible_source_review_receipt import VisibleReviewAuthorBinding
from companion_daemon.world_v2.visible_source_witness_experiment import _json
from test_visible_contextual_fact_values import VALUE, _meaning
from test_visible_independent_review_receipt import _binding, _expected
from test_visible_selected_source_context import _sources
from test_visible_source_review_receipt import _candidate


def _hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


async def _args(tmp_path, *, response_mode):
    async with _sources(tmp_path, retained_value=VALUE) as case:
        prepared = prepare_independent_visible_review(
            candidate=_candidate(case, texts=('你保留了周四的约定。',)),
            source_table=compile_visible_source_table(request=case.request, capsule=case.capsule),
            source_ref_aliases={'S1': case.request.trigger_ref},
            review_protocol=FACT_VALUE_PROTOCOL,
            source_response_mode=response_mode,
        )
        subject_ref = case.observation.actor
        observation_text = case.observation.text
    # Build and restore after closing the ledger, using only its pinned source
    # material and independently supplied (fixture) invocation bindings.
    author = VisibleReviewAuthorBinding(
        model_call_id='model-call:author', request_hash='a' * 64,
        proposal_material_hash=_hash(prepared.as_dict()['candidate_json']),
    )
    raws = (_meaning().raw_response, _meaning().raw_response)
    reviews = tuple(_binding(prepare_meaning_call(prepared=prepared, meaning_index=i), raw, i)
                    for i, raw in enumerate(raws))
    source_call = prepare_source_call(prepared=prepared, meaning_raw_responses=raws)
    body = json.loads(source_call.request['messages'][1]['content'])
    facts = body['fixed_facts']
    reading_id = facts[0]['eligible_fact_value_ids'][0]
    assert all(reading_id in fact['eligible_fact_value_ids'] for fact in facts)
    assert all(reading_id not in fact['eligible_reading_ids'] for fact in facts)
    raw = _json({
        'contract': 'visible-contextual-source-review.3',
        'fact_decisions': [{
            'fact_id': fact['fact_id'], 'assertion_status': 'asserted',
            'source_support': True, 'reading_ids': [],
            'explanation': 'Fixture claim matches the accepted value and predicate.',
            'fact_value_selections': [{
                'reading_id': reading_id, 'quoted_value': VALUE,
                'claim_scope': 'accepted_fact', 'subject_ref': subject_ref,
            }],
        } for fact in facts],
        'beat_decisions': [{
            'beat_index': 0, 'review_complete': True,
            'unaccounted_record_bound_assertions': [],
            'blocking_scope_ambiguities': [], 'non_record_expressions': [],
        }],
    })
    return dict(
        prepared=prepared, author=author, meaning_reviews=reviews,
        meaning_raw_responses=raws, source_review=_binding(source_call, raw, 2),
        source_raw_response=raw,
    ), observation_text


@pytest.mark.asyncio
@pytest.mark.parametrize('response_mode', ['tool', 'json_object'])
async def test_fact_value_receipt_cold_restores_complete_v19_pin(tmp_path, response_mode):
    args, _ = await _args(tmp_path, response_mode=response_mode)
    receipt = record_independent_visible_review(**args)
    assert receipt.contract == 'visible-source-review-receipt.19'
    assert receipt.beat_outcomes == ('closed',)
    restored = IndependentVisibleReviewReceipt.model_validate_json(receipt.model_dump_json(), strict=True)
    assert verify_independent_visible_review_receipt(receipt=restored, **_expected(args)) == receipt
    assert json.loads(restored.prepared_json)['protocol'] == FACT_VALUE_PROTOCOL
    decisions = json.loads(restored.source_raw_response)['fact_decisions']
    assert all(decision['fact_value_selections'][0]['quoted_value'] == VALUE for decision in decisions)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['whole_observation', 'wrong_subject', 'wrong_scope', 'ordinary_reading'])
async def test_fresh_invocation_binding_cannot_bypass_exact_value_receipt_gate(tmp_path, fault):
    args, observation_text = await _args(tmp_path, response_mode='json_object')
    response = json.loads(args['source_raw_response'])
    first = response['fact_decisions'][0]
    selection = first['fact_value_selections'][0]
    if fault == 'whole_observation':
        selection['quoted_value'] = observation_text
    elif fault == 'wrong_subject':
        selection['subject_ref'] = 'user:unrelated'
    elif fault == 'wrong_scope':
        selection['claim_scope'] = 'historical_accepted_fact'
    else:
        first['reading_ids'] = [selection['reading_id']]
        first['fact_value_selections'] = []
    raw = _json(response)
    args['source_raw_response'] = raw
    args['source_review'] = args['source_review'].model_copy(update={'response_hash': _hash(raw)})
    with pytest.raises(ValueError if fault in {'ordinary_reading', 'whole_observation'} else IndependentVisibleReviewRejected):
        record_independent_visible_review(**args)


@pytest.mark.asyncio
async def test_fact_value_receipt_cannot_downgrade_protocol_or_replace_audit(tmp_path):
    args, _ = await _args(tmp_path, response_mode='json_object')
    receipt = record_independent_visible_review(**args)
    downgraded = json.loads(receipt.model_dump_json())
    downgraded['contract'] = 'visible-source-review-receipt.18'
    downgraded['receipt_hash'] = _hash(_json({k: v for k, v in downgraded.items() if k != 'receipt_hash'}))
    with pytest.raises(ValueError, match='contract differs from original protocol'):
        IndependentVisibleReviewReceipt.model_validate_json(_json(downgraded), strict=True)
    expected = deepcopy(_expected(args))
    invocations = list(expected['expected_invocations'])
    invocations[-1] = invocations[-1].model_copy(update={'response_hash': '0' * 64})
    expected['expected_invocations'] = tuple(invocations)
    with pytest.raises(ValueError, match='immutable invocation audits'):
        verify_independent_visible_review_receipt(receipt=receipt, **expected)
