"""Exact rejection diagnostics reach one same-character correction and re-review.

Provider verdicts are fixtures here; this tests transport, authority and recovery.
"""
from dataclasses import replace
import json

import pytest

from companion_daemon.world_v2.visible_independent_review_receipt import (
    IndependentVisibleReviewRejected, record_independent_visible_review,
)
from companion_daemon.world_v2.visible_independent_review_runtime import rejection_feedback
from companion_daemon.world_v2.visible_source_runtime import digest, verify_recorded_candidate
from test_private_cognition_scope import _v20_args
from test_visible_independent_review_runtime import application
from test_launch_visible_source_gate import _audits
from test_whole_candidate_author import _decision, _inbound
from test_world_stimulus_life_intent import _http_result

EXPLANATION = '活动材料只证明散步正在进行，没有记录认不出树；这是具体经历缺少支持，并非当下感受不能表达。'


class ExplanationHTTP:
    version = '20'

    def __init__(self):
        self.requests = []
        self.authors = 0

    async def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        name = body['tool_choice']['function']['name']
        packet = json.loads(body['messages'][1]['content'])
        if name.startswith('character_inbound_'):
            self.authors += 1
            authored = _decision()
            texts = ('走一路只认得绿色。' if self.authors == 1 else '你取消了周五的报告。', '我想先听你说。')
            for beat, text in zip(authored['expression_draft']['beats'], texts, strict=True):
                beat['text'] = text
            return _http_result(body, {'result': {k: authored[k] for k in ('result_kind', 'appraisal_draft', 'expression_draft')}})
        if name == 'interpret_visible_candidate_complete_v16':
            return _http_result(body, {'contract': packet['contract'], 'decisions': [dict(
                beat_index=i, reading_complete=True, unresolved_details=[], hypothetical_conditions=[],
                presuppositions=[], questions=[], meanings=[dict(proposition=beat['text'],
                    subject_role='counterpart' if self.authors > 1 and i == 0 else 'companion',
                    mode='actual_event_or_state' if i == 0 else 'current_private_expression')],
            ) for i, beat in enumerate(packet['visible_beats'])]})
        assert name == 'review_contextual_candidate_sources_v4'
        reject = self.authors == 1
        return _http_result(body, dict(contract=packet['output_contract']['contract'],
            fact_decisions=[dict(fact_id=f['fact_id'], assertion_status='asserted', source_support=not reject,
                                reading_ids=[] if reject else [f['eligible_reading_ids'][0]],
                                fact_value_selections=[], explanation=EXPLANATION if reject else '本轮用户报告支持该命题。')
                            for f in packet['fixed_facts']],
            beat_decisions=[dict(beat_index=b['beat_index'], review_complete=True,
                                 unaccounted_record_bound_assertions=[], blocking_scope_ambiguities=[],
                                 non_record_expressions=[]) for b in packet['visible_beats']]))


@pytest.mark.asyncio
async def test_exact_explanation_is_sent_once_as_data_then_entire_correction_is_reviewed(tmp_path):
    handler = ExplanationHTTP()
    path = tmp_path / 'world.sqlite'
    inbound = replace(_inbound(), text='我取消了周五的报告。')
    async with application(path, handler) as app:
        assert (await app.respond(inbound)).status == 'action_authorized'
        assert handler.authors == 2 and len(handler.requests) == 8
        requests = [r for r in handler.requests if r['tool_choice']['function']['name'].startswith('character_inbound_')]
        original, corrective = [json.loads(r['messages'][1]['content']) for r in requests]
        correction = corrective.pop('role_result_correction')
        assert 'role_result_correction' not in corrective['inner_life_snapshot']
        assert EXPLANATION not in correction['instruction']
        coordinate = correction['coordinate']
        feedback = json.loads(coordinate['failure_detail'].split('\n', 1)[1])
        assert feedback['contract'] == 'visible-independent-rejection.2'
        assert feedback['columns'][-1] == 'reviewer_explanation'
        assert [row[-1] for row in feedback['rows']] == [EXPLANATION, EXPLANATION]
        assert '不是角色可以直接引用的 source_ref' in feedback['explanation_authority']
        assert coordinate['rejected_expression']['candidate_sha256'] == feedback['candidate_sha256']
        assert coordinate['rejected_expression']['beats'][0]['text'] == '走一路只认得绿色。'
        # Full pinned snapshot is identical; only the validated correction is
        # presented at the tail. Neither diagnostics nor a failed candidate
        # became a memory, World assertion, or extra source permission.
        assert corrective['inner_life_snapshot'] == original['inner_life_snapshot']
        evidence = app.export_replay_evidence()
        audit = next(a for a in evidence.projection.proposal_audits if a.proposal_kind == 'decision')
        winner = next(a for a in _audits(app) if a.visible_source_review_json)
        assert verify_recorded_candidate(audit=audit, model_result_audits=evidence.projection.model_result_audits)
        assert 'visible-source-review-receipt.20' in winner.visible_source_review_json
    cold = ExplanationHTTP()
    async with application(path, cold) as app:
        assert (await app.respond(inbound)).status == 'action_authorized'
        assert cold.requests == []


@pytest.mark.asyncio
async def test_oversized_exact_explanation_is_not_silently_replaced_by_generic_reason(tmp_path):
    args = await _v20_args(tmp_path)
    response = json.loads(args['source_raw_response'])
    for fact in response['fact_decisions']:
        fact.update(source_support=False, fact_value_selections=[], explanation='完整审核说明。' * 1000)
    raw = json.dumps(response, ensure_ascii=False)
    args['source_raw_response'] = raw
    args['source_review'] = args['source_review'].model_copy(update={'response_hash': digest(raw)})
    with pytest.raises(IndependentVisibleReviewRejected) as rejected:
        record_independent_visible_review(**args)
    with pytest.raises(ValueError, match='complete diagnostic bound'):
        rejection_feedback(args['prepared'], rejected.value, (*args['meaning_reviews'], args['source_review']))
