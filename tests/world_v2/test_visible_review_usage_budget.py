"""Visible review billing lanes, with real adapters and no external transport."""
from contextlib import asynccontextmanager
from dataclasses import replace
import asyncio
import json
import sqlite3

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.character_interior.inbound_author import _InboundCharacterAuthor
from companion_daemon.world_v2.deliberation import ValidationTechnicalFailure
from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore
from companion_daemon.world_v2.visible_independent_review_runtime import IndependentVisibleReviewer
from companion_daemon.world_v2 import visible_source_runtime as review_runtime
from test_launch_visible_source_gate import _ReviewHTTP, _audits
from test_proactive_visible_source_gate import _run_scenario
from test_production_turn_application import _config, _Identities, _Router, _DeliveredTransport, NOW
from test_whole_candidate_author import _decision, _inbound
from test_world_stimulus_life_intent import _http_result
from world_v2_application import build_sqlite_world_v2_test_application, compose_fixture_character_interior


class ReviewHTTP:
    def __init__(self, *, repair=False):
        self.requests = []
        self.repair = repair

    async def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        name = body['tool_choice']['function']['name']
        packet = json.loads(body['messages'][1]['content'])
        if name.startswith('character_inbound_'):
            authored = _decision()
            for beat, text in zip(authored['expression_draft']['beats'],
                                  ('你取消了周五的报告。', '我想先听你说。'), strict=True):
                beat['text'] = text
            return _http_result(body, {'result': {key: authored[key] for key in
                ('result_kind', 'appraisal_draft', 'expression_draft')}})
        if name == 'interpret_visible_candidate_complete_v16':
            decisions = [dict(beat_index=i, reading_complete=True, unresolved_details=[],
                hypothetical_conditions=[], presuppositions=[], questions=[], meanings=[dict(
                    proposition=beat['text'], subject_role='counterpart' if i == 0 else 'companion',
                    mode='actual_event_or_state' if i == 0 else 'current_private_expression')])
                for i, beat in enumerate(packet['visible_beats'])]
            if self.repair and body['model'] == 'deepseek-v4-flash' and 'invalid_prior_reading' not in packet:
                decisions[0].pop('reading_complete')
            return _http_result(body, {'contract': packet['contract'], 'decisions': decisions})
        assert name == 'review_contextual_candidate_sources_v6'
        return _http_result(body, dict(contract=packet['output_contract']['contract'],
            fact_decisions=[dict(fact_id=f['fact_id'], record_dependency='record_bound',
                source_support=True, reading_ids=[f['eligible_reading_ids'][0]],
                fact_value_selections=[], explanation='The current counterpart report supports this claim.')
                for f in packet['fixed_facts']],
            beat_decisions=[dict(beat_index=b['beat_index'], review_complete=True,
                unaccounted_record_bound_assertions=[], blocking_scope_ambiguities=[],
                non_record_expressions=[]) for b in packet['visible_beats']]))


def store_for(path, cap):
    limits = dict(monthly_budget_cny=80, daily_budget_cny=3,
                  soft_daily_budget_cny=2, background_daily_budget_cny=1.5)
    store = WorldV2UsageStore(path=str(path), **limits)
    seed_purpose, amount = ('life_development_draft', 1.5) if cap == 'background' else ('inbound_turn', 2)
    store.admit_provider_call(purpose=seed_purpose, actor='agent:companion', provider='fixture',
        model='offline-seed', prompt_characters=0, estimated_cny=amount)
    return store


@asynccontextmanager
async def application(path, handler, store, *, version='22'):
    models = [DeepSeekChatModel('offline-fixture', 'https://fixture.invalid', model,
        thinking_enabled=False, transport=httpx.MockTransport(handler), usage_observer=store.record)
        for model in ('deepseek-v4-flash', 'deepseek-v4-pro', 'deepseek-v4-flash')]
    reviewer = models[2] if version == '1' else IndependentVisibleReviewer(
        meaning_models=(models[1], models[2]), source_model=models[2])
    author = _InboundCharacterAuthor(flash_model=models[0], whole_candidate_mode=True,
        visible_source_review_model=reviewer, visible_source_review_version=version,
        atomic_tool_envelope_version='3')
    app = build_sqlite_world_v2_test_application(path=path,
        config=replace(_config(), visible_source_review_required=True),
        identities=_Identities(), router=_Router(),
        character_interior=compose_fixture_character_interior(inbound_author=author),
        transport=_DeliveredTransport(), now=NOW)
    try:
        yield app
    finally:
        app.close()
        await asyncio.gather(*(model.aclose() for model in models))


def rows(path, table):
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]


@pytest.mark.asyncio
@pytest.mark.parametrize('cap', ['background', 'soft'])
@pytest.mark.parametrize('version,repair,reviews', [('1', False, 1), ('22', False, 3), ('22', True, 4)])
async def test_user_reply_finishes_required_review_at_background_limit(tmp_path, cap, version, repair, reviews):
    usage_path, world_path = tmp_path / 'usage.sqlite', tmp_path / 'world.sqlite'
    store = store_for(usage_path, cap)
    baseline = rows(usage_path, 'world_v2_model_reservations')
    handler = _ReviewHTTP(['closed'], tool_version='3') if version == '1' else ReviewHTTP(repair=repair)
    inbound = replace(_inbound(), text='我取消了周五的报告。')
    async with application(world_path, handler, store, version=version) as app:
        assert (await app.respond(inbound)).status == 'action_authorized'
        evidence = app.export_replay_evidence()
        accepted = next(a for a in evidence.projection.proposal_audits if a.proposal_kind == 'decision')
        assert review_runtime.verify_recorded_candidate(audit=accepted,
            model_result_audits=evidence.projection.model_result_audits)
        audits = _audits(app)
        assert sum(a.route.reason_code == 'validation.source_review' for a in audits) == reviews
        assert evidence.projection.semantic_hash == evidence.replay.semantic_hash
    billed = rows(usage_path, 'world_v2_model_usage')
    assert len(billed) == 1 + reviews == len(handler.requests)
    assert sum(row['purpose'] == 'inbound_source_review' for row in billed) == reviews
    assert all(row['billing_state'] == 'known' for row in billed)
    assert store.background_daily_committed_cny() == pytest.approx(1.5 if cap == 'background' else 0)
    reservations = rows(usage_path, 'world_v2_model_reservations')
    assert reservations[:len(baseline)] == baseline  # Existing holds retain all original fields.
    by_id = {row['reservation_id']: row for row in reservations}
    assert all(by_id[row['reservation_id']]['purpose'] == row['purpose'] for row in billed)
    cold = _ReviewHTTP([], tool_version='3') if version == '1' else ReviewHTTP()
    async with application(world_path, cold, store, version=version) as app:
        assert (await app.respond(inbound)).status == 'action_authorized'
        assert cold.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize('version', ['1', '22'])
async def test_billing_lane_does_not_change_request_or_receipt_and_default_stays_capped(tmp_path, monkeypatch, version):
    store = WorldV2UsageStore(path=str(tmp_path / 'usage.sqlite'))
    handler = _ReviewHTTP(['closed', 'closed'], tool_version='3') if version == '1' else ReviewHTTP()
    original = review_runtime.review_candidate
    calls = []

    async def capture(**kwargs):
        result = await original(**kwargs)
        calls.append((kwargs, result))
        return result

    monkeypatch.setattr(review_runtime, 'review_candidate', capture)
    async with application(tmp_path / 'world.sqlite', handler, store, version=version) as app:
        assert (await app.respond(replace(_inbound(), text='我取消了周五的报告。'))).status == 'action_authorized'
        (kwargs, accepted), = calls
        assert kwargs.pop('usage_purpose') == 'inbound_source_review'
        first_requests = handler.requests[1:]
        repeated = await original(**kwargs)  # Shared default is the proactive/background lane.
        assert handler.requests[1 + len(first_requests):] == first_requests
        assert repeated.visible_source_review_json == accepted.visible_source_review_json
        assert all(a.purpose == 'source_review' for a in repeated.provider_subcall_audits)
        store._background_daily_budget_cny = 0
        before = len(handler.requests)
        with pytest.raises(ValidationTechnicalFailure, match='source_review_exception') as denied:
            await original(**kwargs)
        assert 'admission.background_daily_budget_exceeded' in denied.value.failure_detail
        assert len(handler.requests) == before
        # Hard caps must also stop the explicitly visible review before transport.
        for field, reason in (('_daily_budget_cny', 'daily'), ('_monthly_budget_cny', 'monthly')):
            setattr(store, field, 0)
            with pytest.raises(ValidationTechnicalFailure) as denied:
                await original(**kwargs, usage_purpose='inbound_source_review')
            assert 'admission.' + reason + '_budget_exceeded' in denied.value.failure_detail
            assert len(handler.requests) == before
            setattr(store, field, None)


@pytest.mark.asyncio
async def test_real_proactive_owner_keeps_background_review_purpose(tmp_path, monkeypatch):
    await _run_scenario(tmp_path, monkeypatch, 'source_free')
    billed = rows(tmp_path / 'world.sqlite', 'world_v2_model_usage')
    assert [row['purpose'] for row in billed].count('inbound_source_review') == 1
    assert [row['purpose'] for row in billed].count('source_review') == 1
    assert [row['purpose'] for row in billed].count('proactive_contact') == 1
