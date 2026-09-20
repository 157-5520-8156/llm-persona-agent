"""Shared scope is pinned; host coverage/evidence gates remain complete.

Responses are structural fixtures, not evidence of model semantic quality.
"""
from copy import deepcopy
import hashlib
import json

import pytest

from companion_daemon.world_v2.character_interior.inbound_wire import _ExpressionDraftWire
from companion_daemon.world_v2.private_cognition_scope import INSTRUCTION
from companion_daemon.world_v2.visible_candidate_meaning import (
    PreparedCandidateMeaning, verify_candidate_meaning_preparation,
)
from companion_daemon.world_v2.visible_contextual_source_review import (
    PreparedContextualSourceReview, prepare_contextual_source_review,
)
from companion_daemon.world_v2.visible_independent_meanings import IndependentMeaning
from companion_daemon.world_v2.visible_independent_review_receipt import (
    IndependentVisibleReviewReceipt, IndependentVisibleReviewRejected,
    meaning_preparation, prepare_independent_visible_review, prepare_meaning_call,
    prepare_source_call, record_independent_visible_review,
    verify_independent_visible_review_receipt,
)
from companion_daemon.world_v2.visible_review_protocols import PRIVATE_COGNITION_PROTOCOL, RECORD_DEPENDENCY_PROTOCOL
from companion_daemon.world_v2.visible_source_review_receipt import VisibleReviewAuthorBinding
from companion_daemon.world_v2.visible_source_witness_experiment import _json
from test_visible_selected_source_context import _sources
from test_visible_fact_value_receipt import _args
from test_visible_independent_review_receipt import _binding, _expected


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', [PRIVATE_COGNITION_PROTOCOL, RECORD_DEPENDENCY_PROTOCOL])
async def test_author_scope_is_opt_in_and_removes_ambiguous_legacy_permission(tmp_path, protocol):
    from companion_daemon.world_v2.visible_source_runtime import compile_requirement
    wire = _ExpressionDraftWire(model=object())
    async with _sources(tmp_path) as case:
        request = case.request
        requirement = compile_requirement(request=request, capsule=case.capsule,
                                          review_protocol=protocol)
    legacy = wire._messages(request=request, quick_recovery=False, failure_code=None)[0]['content']
    assert INSTRUCTION not in legacy
    assert 'immediate retrospective continuity' in legacy
    request = request.model_copy(update={'visible_source_requirement_json': requirement})
    current = wire._messages(request=request, quick_recovery=False, failure_code=None)[0]['content']
    assert current.count(INSTRUCTION) == 1
    assert 'immediate retrospective continuity' not in current
    assert 'world_claims' in current


async def _v20_args(tmp_path):
    args, _ = await _args(tmp_path, response_mode='json_object')
    pin = args['prepared'].as_dict()
    from companion_daemon.world_v2.proposal_envelope import DecisionProposal
    from companion_daemon.world_v2.visible_source_composer import VisibleSourceTable
    prepared = prepare_independent_visible_review(
        candidate=DecisionProposal.model_validate_json(pin['candidate_json'], strict=True),
        source_table=VisibleSourceTable(pin['source_table_json']),
        source_ref_aliases=pin['source_ref_aliases'], review_protocol=PRIVATE_COGNITION_PROTOCOL,
        source_response_mode='json_object',
    )
    raws = []
    for raw in args['meaning_raw_responses']:
        value = json.loads(raw)
        value['contract'] = 'visible-candidate-meaning.16'
        raws.append(_json(value))
    reviews = tuple(_binding(prepare_meaning_call(prepared=prepared, meaning_index=i), raw, i)
                    for i, raw in enumerate(raws))
    source_call = prepare_source_call(prepared=prepared, meaning_raw_responses=raws)
    source = json.loads(args['source_raw_response'])
    source['contract'] = 'visible-contextual-source-review.4'
    raw = _json(source)
    author = VisibleReviewAuthorBinding(
        model_call_id='model-call:author', request_hash='a' * 64,
        proposal_material_hash=hashlib.sha256(pin['candidate_json'].encode()).hexdigest(),
    )
    return dict(prepared=prepared, author=author, meaning_reviews=reviews,
                meaning_raw_responses=tuple(raws), source_review=_binding(source_call, raw, 2), source_raw_response=raw)


@pytest.mark.asyncio
async def test_v20_pins_shared_boundary_and_cold_restores_exact_fact_receipt(tmp_path):
    args = await _v20_args(tmp_path)
    meaning = meaning_preparation(args['prepared'])
    assert INSTRUCTION in meaning.request()['messages'][0]['content']
    assert verify_candidate_meaning_preparation(meaning)['contract'] == 'visible-candidate-meaning.16'
    call = prepare_source_call(prepared=args['prepared'], meaning_raw_responses=args['meaning_raw_responses'])
    assert INSTRUCTION in call.request['messages'][0]['content']
    receipt = record_independent_visible_review(**args)
    assert receipt.contract == 'visible-source-review-receipt.20'
    restored = IndependentVisibleReviewReceipt.model_validate_json(receipt.model_dump_json(), strict=True)
    assert verify_independent_visible_review_receipt(receipt=restored, **_expected(args)) == receipt
    packet = json.loads(meaning.payload_json)
    packet['request']['messages'][0]['content'] = packet['request']['messages'][0]['content'].replace(INSTRUCTION, '')
    with pytest.raises(ValueError, match='fixed compiler'):
        verify_candidate_meaning_preparation(PreparedCandidateMeaning(_json(packet)))


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['wrong_subject', 'whole_beat_omission', 'missing_beat', 'history_unsupported', 'downgrade'])
async def test_shared_scope_does_not_bypass_existing_receipt_gates(tmp_path, fault):
    args = await _v20_args(tmp_path)
    value = json.loads(args['source_raw_response'])
    if fault == 'wrong_subject':
        value['fact_decisions'][0]['fact_value_selections'][0]['subject_ref'] = 'user:other'
    elif fault == 'whole_beat_omission':
        value['beat_decisions'][0]['unaccounted_record_bound_assertions'] = ['角色上周已去报名']
    elif fault == 'missing_beat':
        value['beat_decisions'] = []
    elif fault == 'history_unsupported':
        value['fact_decisions'][0].update(source_support=False, fact_value_selections=[])
    else:
        value['contract'] = 'visible-contextual-source-review.3'
    raw = _json(value)
    args['source_raw_response'] = raw
    args['source_review'] = args['source_review'].model_copy(update={'response_hash': hashlib.sha256(raw.encode()).hexdigest()})
    with pytest.raises((ValueError, IndependentVisibleReviewRejected)):
        record_independent_visible_review(**args)


@pytest.mark.asyncio
async def test_empty_fact_inventory_still_requires_whole_beat_review(tmp_path):
    args = await _v20_args(tmp_path)
    meaning = meaning_preparation(args['prepared'])
    response = json.loads(args['meaning_raw_responses'][0])
    response['decisions'][0]['meanings'][0]['mode'] = 'current_private_expression'
    member = IndependentMeaning(meaning, _json(response))
    prep = prepare_contextual_source_review(meanings=(member, member), sources=(),
        scoped_coverage=True, fact_value_authority=True, private_cognition_scope=True)
    pin = json.loads(prep.payload_json)
    assert pin['facts'] == []
    raw = {'contract': 'visible-contextual-source-review.4', 'fact_decisions': [],
           'beat_decisions': [{'beat_index': 0, 'review_complete': True,
              'non_record_expressions': ['当前想法'], 'blocking_scope_ambiguities': [],
              'unaccounted_record_bound_assertions': ['角色一周内反复做过此事']} ]}
    assert prep.inspect_response(_json(raw))['beat_outcomes'] == ['unclosed']
    bad = deepcopy(pin)
    bad['request']['messages'][0]['content'] = bad['request']['messages'][0]['content'].replace(INSTRUCTION, '')
    with pytest.raises(ValueError, match='original compilation'):
        PreparedContextualSourceReview(_json(bad)).inspect_response(_json(raw))


@pytest.mark.asyncio
async def test_same_character_correction_uses_same_scope_after_definite_rejection(tmp_path):
    from companion_daemon.world_v2.visible_independent_review_runtime import rejection_feedback
    args = await _v20_args(tmp_path)
    value = json.loads(args['source_raw_response'])
    for fact in value['fact_decisions']:
        fact.update(source_support=False, fact_value_selections=[])
    raw = _json(value)
    args['source_raw_response'] = raw
    args['source_review'] = args['source_review'].model_copy(update={'response_hash': hashlib.sha256(raw.encode()).hexdigest()})
    with pytest.raises(IndependentVisibleReviewRejected) as failure:
        record_independent_visible_review(**args)
    feedback = rejection_feedback(args['prepared'], failure.value,
                                  (*args['meaning_reviews'], args['source_review']))
    assert INSTRUCTION in feedback
    assert '不能借此证明长期习惯、过去想法' not in feedback
    assert len(feedback) <= 3900
