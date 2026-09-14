import json

import pytest

from companion_daemon.world_v2.proposal_envelope import DecisionProposal
from companion_daemon.world_v2.visible_source_composer import VisibleSourceTable
from companion_daemon.world_v2.visible_independent_review_receipt import (
    RESELECTING_PROTOCOL, RejectedMeaningAttempt, IndependentVisibleReviewReceipt,
    prepare_independent_visible_review, prepare_meaning_call, prepare_source_call,
    record_independent_visible_review, receipt_invocations, verify_independent_visible_review_receipt,
)
from companion_daemon.world_v2.visible_source_witness_experiment import _json
from test_visible_independent_review_receipt import _args, _binding, _hash


async def arguments(tmp_path):
    args = await _args(tmp_path)
    pin = args['prepared'].as_dict()
    prepared = prepare_independent_visible_review(candidate=DecisionProposal.model_validate_json(pin['candidate_json']),
        source_table=VisibleSourceTable(payload_json=pin['source_table_json']), source_ref_aliases=pin['source_ref_aliases'],
        review_protocol=RESELECTING_PROTOCOL)
    raws = tuple(raw.replace('visible-candidate-meaning.7', 'visible-candidate-meaning.9') for raw in args['meaning_raw_responses'])
    value = json.loads(raws[0])
    del value['decisions'][0]['reading_complete']
    rejected = _json(value)
    previous = _binding(prepare_meaning_call(prepared=prepared, meaning_index=0), rejected, 0)
    replacement = _binding(prepare_meaning_call(prepared=prepared, meaning_index=0, rejected_raw=rejected), raws[0], 3)
    replacement = replacement.model_copy(update={'model_id': previous.model_id})
    second = _binding(prepare_meaning_call(prepared=prepared, meaning_index=1), raws[1], 1)
    source = _binding(prepare_source_call(prepared=prepared, meaning_raw_responses=raws), args['source_raw_response'], 2)
    args.update(prepared=prepared, meaning_reviews=(replacement, second), meaning_raw_responses=raws,
        source_review=source, rejected_meanings=(RejectedMeaningAttempt(review=previous, raw_response=rejected), None))
    return args


@pytest.mark.asyncio
async def test_reselecting_receipt_requires_original_failed_call_and_same_reader_replacement(tmp_path):
    args = await arguments(tmp_path)
    receipt = record_independent_visible_review(**args)
    assert receipt.contract == 'visible-source-review-receipt.10'
    calls = receipt_invocations(receipt)
    assert len(calls) == 4 and calls[0] == args['rejected_meanings'][0].review
    cold = IndependentVisibleReviewReceipt.model_validate_json(receipt.model_dump_json(), strict=True)
    assert verify_independent_visible_review_receipt(receipt=cold, expected_prepared=args['prepared'],
        expected_author=args['author'], expected_invocations=calls) == receipt
    with pytest.raises(ValueError):
        verify_independent_visible_review_receipt(receipt=cold, expected_prepared=args['prepared'],
            expected_author=args['author'], expected_invocations=calls[1:])


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['valid_prior', 'another_reader', 'same_call', 'wrong_prior_hash', 'missing_prior', 'wrong_receipt_version'])
async def test_trace_cannot_retry_a_valid_reading_or_reuse_unanchored_results(tmp_path, fault):
    args = await arguments(tmp_path)
    previous = args['rejected_meanings'][0]
    if fault == 'valid_prior':
        previous = previous.model_copy(update={'raw_response': args['meaning_raw_responses'][0],
            'review': previous.review.model_copy(update={'response_hash': _hash(args['meaning_raw_responses'][0])})})
        args['rejected_meanings'] = (previous, None)
    elif fault == 'another_reader':
        first = args['meaning_reviews'][0].model_copy(update={'model_id': 'another-model'})
        args['meaning_reviews'] = (first, args['meaning_reviews'][1])
    elif fault == 'same_call':
        args['meaning_reviews'] = (args['meaning_reviews'][0].model_copy(update={'model_call_id': previous.review.model_call_id}), args['meaning_reviews'][1])
    elif fault == 'wrong_prior_hash':
        args['rejected_meanings'] = (previous.model_copy(update={'review': previous.review.model_copy(update={'response_hash': 'b' * 64})}), None)
    elif fault == 'missing_prior':
        args['rejected_meanings'] = None
    else:
        receipt = record_independent_visible_review(**args)
        value = receipt.model_dump(mode='json')
        value['contract'] = 'visible-source-review-receipt.9'
        value['receipt_hash'] = _hash(_json({k: v for k, v in value.items() if k != 'receipt_hash'}))
        with pytest.raises(ValueError, match='contract differs'):
            IndependentVisibleReviewReceipt.model_validate_json(_json(value), strict=True)
        return
    with pytest.raises(ValueError):
        record_independent_visible_review(**args)
