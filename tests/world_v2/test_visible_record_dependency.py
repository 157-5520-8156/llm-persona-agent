"""Record-dependency wire gates; fixtures do not prove model semantics."""
import hashlib
import json

import pytest

from companion_daemon.world_v2.proposal_envelope import DecisionProposal
from companion_daemon.world_v2.visible_independent_review_receipt import (
    IndependentReviewInconclusive, IndependentVisibleReviewReceipt,
    IndependentVisibleReviewRejected, meaning_preparation,
    prepare_independent_visible_review, prepare_meaning_call, prepare_source_call,
    record_independent_visible_review, verify_independent_visible_review_receipt,
)
from companion_daemon.world_v2.visible_record_dependency_scope import INSTRUCTION
from companion_daemon.world_v2.visible_review_protocols import RECORD_DEPENDENCY_PROTOCOL
from companion_daemon.world_v2.visible_source_composer import VisibleSourceTable
from companion_daemon.world_v2.visible_source_witness_experiment import _json
from test_private_cognition_scope import _v20_args
from test_visible_independent_review_receipt import _binding, _expected


async def _v21_args(tmp_path):
    args = await _v20_args(tmp_path)
    pin = args['prepared'].as_dict()
    prepared = prepare_independent_visible_review(
        candidate=DecisionProposal.model_validate_json(pin['candidate_json'], strict=True),
        source_table=VisibleSourceTable(pin['source_table_json']),
        source_ref_aliases=pin['source_ref_aliases'], review_protocol=RECORD_DEPENDENCY_PROTOCOL,
        source_response_mode='json_object', scope_permission_context=True,
    )
    raw_meanings = args['meaning_raw_responses']
    reviews = tuple(_binding(prepare_meaning_call(prepared=prepared, meaning_index=i), raw, i)
                    for i, raw in enumerate(raw_meanings))
    args.update(prepared=prepared, meaning_reviews=reviews)
    value = json.loads(args['source_raw_response'])
    value['contract'] = 'visible-contextual-source-review.5'
    for decision in value['fact_decisions']:
        decision['record_dependency'] = {'asserted': 'record_bound', 'not_asserted': 'not_record_bound',
                                         'uncertain': 'uncertain'}[decision.pop('assertion_status')]
    return _source_response(args, value)


def _source_response(args, value):
    raw = _json(value)
    call = prepare_source_call(prepared=args['prepared'], meaning_raw_responses=args['meaning_raw_responses'])
    return {**args, 'source_review': _binding(call, raw, 2), 'source_raw_response': raw}


@pytest.mark.asyncio
async def test_v21_retains_fact_value_authority_and_cold_receipt_binding(tmp_path):
    args = await _v21_args(tmp_path)
    assert json.loads(meaning_preparation(args['prepared']).payload_json)['contract'] == 'visible-candidate-meaning.16'
    call = prepare_source_call(prepared=args['prepared'], meaning_raw_responses=args['meaning_raw_responses'])
    assert INSTRUCTION in call.request['messages'][0]['content']
    assert 'assertion_status' not in call.request['messages'][0]['content']
    schema = json.loads(call.request['messages'][1]['content'])['output_schema']
    fact = schema['properties']['fact_decisions']['items']
    assert 'assertion_status' not in fact['properties']
    assert 'record_dependency' in fact['required']
    receipt = record_independent_visible_review(**args)
    assert receipt.contract == 'visible-source-review-receipt.21'
    restored = IndependentVisibleReviewReceipt.model_validate_json(receipt.model_dump_json(), strict=True)
    assert verify_independent_visible_review_receipt(receipt=restored, **_expected(args)) == receipt


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['unsupported', 'wrong_subject', 'wrong_value', 'omission',
                                  'missing_beat', 'missing_fact', 'duplicate_fact', 'old_field', 'downgrade'])
async def test_v21_keeps_complete_source_and_coverage_gates(tmp_path, fault):
    args = await _v21_args(tmp_path)
    value = json.loads(args['source_raw_response'])
    first = value['fact_decisions'][0]
    if fault == 'unsupported':
        first.update(source_support=False, reading_ids=[], fact_value_selections=[])
    elif fault == 'wrong_subject':
        first['fact_value_selections'][0]['subject_ref'] = 'user:other'
    elif fault == 'wrong_value':
        first['fact_value_selections'][0]['quoted_value'] = '没有接受的内容'
    elif fault == 'omission':
        value['beat_decisions'][0]['unaccounted_record_bound_assertions'] = ['过去已完成的行动']
    elif fault == 'missing_beat':
        value['beat_decisions'] = []
    elif fault == 'missing_fact':
        value['fact_decisions'].pop()
    elif fault == 'duplicate_fact':
        value['fact_decisions'].append(first.copy())
    elif fault == 'old_field':
        first['assertion_status'] = first.pop('record_dependency')
    else:
        value['contract'] = 'visible-contextual-source-review.4'
    with pytest.raises((ValueError, IndependentVisibleReviewRejected)):
        record_independent_visible_review(**_source_response(args, value))


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', [None, 'support', 'references', 'uncertain', 'embedded_history', 'other_reader_history'])
async def test_nonrecord_judgment_grants_no_evidence_or_whole_beat_exemption(tmp_path, fault):
    args = await _v21_args(tmp_path)
    value = json.loads(args['source_raw_response'])
    original_selection = value['fact_decisions'][0]['fact_value_selections']
    for item in value['fact_decisions']:
        item.update(record_dependency='not_record_bound', source_support=False, reading_ids=[],
                    fact_value_selections=[], explanation='本命题说明正在进行的表达，不依赖先前事件。')
    first = value['fact_decisions'][0]
    if fault == 'support':
        first['source_support'] = True
    elif fault == 'references':
        first['fact_value_selections'] = original_selection
    elif fault == 'uncertain':
        first['record_dependency'] = 'uncertain'
    elif fault == 'embedded_history':
        value['beat_decisions'][0]['unaccounted_record_bound_assertions'] = ['本次表达所预设的过去共同经历']
    elif fault == 'other_reader_history':
        value['fact_decisions'][-1]['record_dependency'] = 'record_bound'
    args = _source_response(args, value)
    if fault is None:
        receipt = record_independent_visible_review(**args)
        assert verify_independent_visible_review_receipt(receipt=receipt, **_expected(args)) == receipt
        assert all(outcome == 'source_free' for outcome in receipt.beat_outcomes)
    else:
        with pytest.raises((ValueError, IndependentReviewInconclusive, IndependentVisibleReviewRejected)):
            record_independent_visible_review(**args)


@pytest.mark.asyncio
async def test_rejection_keeps_exact_review_explanation_and_private_scope(tmp_path):
    from companion_daemon.world_v2.visible_independent_review_runtime import rejection_feedback
    from companion_daemon.world_v2.private_cognition_scope import INSTRUCTION as private_scope
    args = await _v21_args(tmp_path)
    value = json.loads(args['source_raw_response'])
    for item in value['fact_decisions']:
        item.update(source_support=False, fact_value_selections=[], explanation='确切缺口：原材料只有计划，没有完成记录。')
    args = _source_response(args, value)
    with pytest.raises(IndependentVisibleReviewRejected) as failure:
        record_independent_visible_review(**args)
    detail = rejection_feedback(args['prepared'], failure.value, (*args['meaning_reviews'], args['source_review']))
    assert value['fact_decisions'][0]['explanation'] in detail
    assert private_scope in detail
    assert 'visible-independent-rejection.2' in detail
    assert hashlib.sha256(args['prepared'].as_dict()['candidate_json'].encode()).hexdigest() in detail


@pytest.mark.asyncio
async def test_no_fixed_facts_still_requires_complete_beat_review(tmp_path):
    from companion_daemon.world_v2.visible_independent_meanings import IndependentMeaning
    from companion_daemon.world_v2.visible_contextual_source_review import prepare_contextual_source_review
    args = await _v21_args(tmp_path)
    meaning = meaning_preparation(args['prepared'])
    value = json.loads(args['meaning_raw_responses'][0])
    value['decisions'][0]['meanings'][0]['mode'] = 'current_private_expression'
    member = IndependentMeaning(meaning, _json(value))
    prepared = prepare_contextual_source_review(meanings=(member, member), sources=(),
        scoped_coverage=True, fact_value_authority=True, private_cognition_scope=True,
        record_dependency_scope=True)
    assert json.loads(prepared.payload_json)['facts'] == []
    response = {'contract': 'visible-contextual-source-review.5', 'fact_decisions': [],
        'beat_decisions': [{'beat_index': 0, 'review_complete': True,
            'non_record_expressions': ['当前想法'], 'blocking_scope_ambiguities': [],
            'unaccounted_record_bound_assertions': ['两读取器都漏掉的既往经历']}]}
    assert prepared.inspect_response(_json(response))['beat_outcomes'] == ['unclosed']
    response['beat_decisions'][0]['unaccounted_record_bound_assertions'] = []
    response['beat_decisions'][0]['review_complete'] = False
    assert prepared.inspect_response(_json(response))['inconclusive'] is True
