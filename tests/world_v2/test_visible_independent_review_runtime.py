"""Public author to Action/replay chain with only HTTP and delivery replaced."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import json
import sqlite3

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.character_interior.inbound_author import _InboundCharacterAuthor
from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore
from companion_daemon.world_v2.visible_independent_review_runtime import IndependentVisibleReviewer
from companion_daemon.world_v2.visible_source_runtime import verify_recorded_candidate
from world_v2_application import build_sqlite_world_v2_test_application, compose_fixture_character_interior
from test_launch_visible_source_gate import _audits
from test_production_turn_application import _config, _Identities, _Router, _DeliveredTransport, NOW
from test_whole_candidate_author import _decision, _inbound
from test_world_stimulus_life_intent import _http_result


class ReviewHTTP:
    def __init__(self, fault=None, version="9"):
        self.version = version
        self.fault = fault
        self.requests = []
        self.authors = 0
        self.readers_started = asyncio.Event()
        self.pending = asyncio.Event()
        self.reader_count = 0

    async def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        name = body['tool_choice']['function']['name']
        packet = json.loads(body['messages'][1]['content'])
        if name.startswith('character_inbound_'):
            self.authors += 1
            authored = _decision()
            for beat, text in zip(authored['expression_draft']['beats'],
                                  ('我想听你说。' if self.fault == 'source_free' else '你取消了周五的报告。', '我想先听你说。'), strict=True):
                beat['text'] = text
            return _http_result(body, {'result': {k: authored[k] for k in ('result_kind', 'appraisal_draft', 'expression_draft')}})
        if name == ('interpret_visible_candidate_complete_v7' if self.version == "9" else 'interpret_visible_candidate_complete_v9'):
            is_reselection = 'invalid_prior_reading' in packet
            assert set(packet) == ({'contract', 'visible_beats', 'invalid_prior_reading', 'structural_failure'} if is_reselection else {'contract', 'visible_beats'})
            self.reader_count += 1
            if self.reader_count == 2:
                self.readers_started.set()
            await asyncio.wait_for(self.readers_started.wait(), 2)
            if self.fault == 'cancel' and body['model'] == 'deepseek-v4-pro':
                self.pending.set()
                await asyncio.Future()
            if self.fault == 'timeout' and body['model'] == 'deepseek-v4-pro':
                raise TimeoutError('fixture reader timeout')
            factual = self.fault != 'source_free'
            decisions = []
            for i, beat in enumerate(packet['visible_beats']):
                decisions.append(dict(beat_index=i, reading_complete=True, unresolved_details=[],
                    hypothetical_conditions=[], questions=[], meanings=[dict(
                        proposition=beat['text'], subject_role='counterpart' if i == 0 and factual else 'companion',
                        mode='actual_event_or_state' if i == 0 and factual else 'current_private_expression')]))
            if body['model'] == 'deepseek-v4-pro':
                if self.fault == 'malformed':
                    decisions.pop()
                if self.fault == 'inconclusive':
                    decisions[0].update(reading_complete=False, unresolved_details=['uncertain reference'])
            if self.fault in {'repair_once', 'repair_twice'} and body['model'] == 'deepseek-v4-flash' and (not is_reselection or self.fault == 'repair_twice'):
                del decisions[0]['reading_complete']
            return _http_result(body, {'contract': 'visible-candidate-meaning.7' if self.version == '9' else 'visible-candidate-meaning.9', 'decisions': decisions})
        assert name == ('review_independent_fixed_meanings_v2' if self.version == '11' else 'review_independent_fixed_meanings_v1')
        assert 'visible_beats' not in packet
        reject = self.fault == 'reselect' and self.authors == 1
        return _http_result(body, dict(contract=packet['output_contract']['contract'], fact_decisions=[dict(
            fact_id=f['fact_id'], source_support=not reject,
            reading_ids=[] if reject else [f['eligible_reading_ids'][0]],
            **({} if self.version == '11' else {'explanation': 'offline fixture'}))
            for f in packet['fixed_facts']]))


@asynccontextmanager
async def application(path, handler):
    usage = WorldV2UsageStore(path=str(path.with_name('usage.sqlite')))
    models = [DeepSeekChatModel('offline-fixture', 'https://fixture.invalid', name,
               thinking_enabled=False, transport=httpx.MockTransport(handler), usage_observer=usage.record)
              for name in ('deepseek-v4-flash', 'deepseek-v4-pro', 'deepseek-v4-flash')]
    reviewer = IndependentVisibleReviewer(meaning_models=(models[1], models[2]), source_model=models[2])
    author = _InboundCharacterAuthor(flash_model=models[0], whole_candidate_mode=True,
        visible_source_review_model=reviewer, visible_source_review_version=handler.version, atomic_tool_envelope_version='3')
    app = build_sqlite_world_v2_test_application(path=path, config=replace(_config(), visible_source_review_required=True),
        identities=_Identities(), router=_Router(), character_interior=compose_fixture_character_interior(inbound_author=author),
        transport=_DeliveredTransport(), now=NOW)
    try:
        yield app
    finally:
        app.close()
        await asyncio.gather(*(m.aclose() for m in models))


@pytest.mark.asyncio
@pytest.mark.parametrize('fault,calls', [(None, 4), ('source_free', 3), ('reselect', 8)])
@pytest.mark.parametrize('version', ['9', '10', '11'])
async def test_independent_runtime_accepts_only_complete_bound_review_and_cold_replays(tmp_path, fault, calls, version):
    handler = ReviewHTTP(fault, version)
    path = tmp_path / 'world.sqlite'
    inbound = replace(_inbound(), text='我取消了周五的报告。')
    async with application(path, handler) as app:
        outcome = await app.respond(inbound)
        assert outcome.status == 'action_authorized', (outcome, [(a.outcome, a.failure_code) for a in _audits(app)])
        assert len(handler.requests) == calls
        evidence = app.export_replay_evidence()
        audit = next(a for a in evidence.projection.proposal_audits if a.proposal_kind == 'decision')
        winner = next(a for a in _audits(app) if a.visible_source_review_json)
        data = json.loads(winner.visible_source_review_json)
        assert json.loads(data['requirement_json'])['review_protocol'] == {'9': 'visible-independent-review.1', '10': 'visible-independent-review.2', '11': 'visible-independent-review.3'}[version]
        assert data['receipt']['contract'] == f'visible-source-review-receipt.{version}'
        assert verify_recorded_candidate(audit=audit, model_result_audits=evidence.projection.model_result_audits) == data['receipt']['receipt_hash']
        assert evidence.projection.semantic_hash == evidence.replay.semantic_hash
        if fault == 'reselect':
            request = [b for b in handler.requests if b['tool_choice']['function']['name'].startswith('character_inbound_')][1]
            context = json.loads(request['messages'][1]['content'])
            detail = context['role_result_correction']['coordinate']['failure_detail']
            assert len(detail) <= 3900
            assert len(json.loads(detail.split('\n', 1)[1])['rows']) == 2
        with sqlite3.connect(path.with_name('usage.sqlite')) as db:
            assert db.execute("SELECT COUNT(*) FROM world_v2_model_usage WHERE billing_state='known'").fetchone() == (calls,)
    cold = ReviewHTTP(version=version)
    async with application(path, cold) as app:
        assert (await app.respond(inbound)).status == 'action_authorized'
        assert cold.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['malformed', 'inconclusive', 'timeout'])
async def test_partial_or_failed_reading_retains_calls_and_cannot_authorize(tmp_path, fault):
    handler = ReviewHTTP(fault)
    async with application(tmp_path / 'world.sqlite', handler) as app:
        outcome = await app.respond(_inbound())
        assert outcome.status != 'action_authorized'
        assert not app.export_replay_evidence().projection.actions
        audits = _audits(app)
        assert not any(a.visible_source_review_json for a in audits)
        review_calls = {a.model_call_id: a for a in audits if a.route.reason_code == 'validation.source_review'}
        assert len(review_calls) >= 2
        assert any(s.model_id == 'deepseek-v4-flash' and s.usage is not None for s in review_calls.values())
        assert not any(b['tool_choice']['function']['name'] == 'review_independent_fixed_meanings_v1' for b in handler.requests)


@pytest.mark.asyncio
async def test_external_cancellation_keeps_completed_and_pending_stage_audits(tmp_path):
    handler = ReviewHTTP('cancel')
    async with application(tmp_path / 'world.sqlite', handler) as app:
        task = asyncio.create_task(app.respond(_inbound()))
        await asyncio.wait_for(handler.pending.wait(), 4)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not app.export_replay_evidence().projection.actions
    with sqlite3.connect(tmp_path / 'usage.sqlite') as db:
        assert db.execute('SELECT COUNT(*) FROM world_v2_model_usage').fetchone()[0] >= 2


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['missing_reader', 'changed_response', 'downgraded_protocol'])
async def test_cold_runtime_requires_original_protocol_and_each_actual_review_call(tmp_path, fault):
    from companion_daemon.world_v2.proposal_audit_schemas import RecordedModelResultAudit
    from companion_daemon.world_v2.visible_source_runtime import canonical, digest
    async with application(tmp_path / 'world.sqlite', ReviewHTTP()) as app:
        assert (await app.respond(_inbound())).status == 'action_authorized'
        projection = app.export_replay_evidence().projection
    audit = next(a for a in projection.proposal_audits if a.proposal_kind == 'decision')
    rows = list(projection.model_result_audits)
    parent_index = next(i for i, r in enumerate(rows) if r.model_result_ref == audit.model_result_ref)
    parent = RecordedModelResultAudit.model_validate_json(rows[parent_index].audit_json)
    data = json.loads(parent.visible_source_review_json)
    reader_id = data['receipt']['meaning_reviews'][0]['model_call_id']
    if fault == 'missing_reader':
        rows = [r for r in rows if r.model_call_id != reader_id]
    elif fault == 'changed_response':
        index = next(i for i, r in enumerate(rows) if r.model_call_id == reader_id)
        changed = RecordedModelResultAudit.model_validate_json(rows[index].audit_json).model_copy(update={'response_hash': 'b' * 64})
        raw = changed.model_dump_json()
        rows[index] = rows[index].model_copy(update={'audit_json': raw, 'audit_hash': digest(raw)})
    else:
        requirement = json.loads(data['requirement_json'])
        requirement.pop('review_protocol')
        requirement['contract'] = 'visible-source-review-required.1'
        data['requirement_json'] = canonical(requirement)
        data['contract'] = 'visible-source-runtime-evidence.1'
        changed = parent.model_copy(update={'visible_source_review_json': canonical(data)})
        raw = changed.model_dump_json()
        rows[parent_index] = rows[parent_index].model_copy(update={'audit_json': raw, 'audit_hash': digest(raw)})
    with pytest.raises(ValueError):
        verify_recorded_candidate(audit=audit, model_result_audits=tuple(rows))


@pytest.mark.asyncio
@pytest.mark.parametrize('version', ['9', '10', '11'])
@pytest.mark.parametrize('schema_references', [False, True])
async def test_public_proactive_contact_pins_and_replays_independent_protocol(tmp_path, monkeypatch, version, schema_references):
    from datetime import timedelta
    import companion_daemon.config as config_module
    from companion_daemon.config import Settings
    from companion_daemon.llm import FakeCompanionModel
    from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host
    from test_delayed_trigger_proactive_host_qualification import _DeliveredQQ, _ProactiveRoleScript, NOW as START
    monkeypatch.setenv('COMPANION_DISABLE_DEBUG_USAGE_LEDGER', '1')
    monkeypatch.setattr(config_module, '_macos_launchctl_env', lambda _name: None)
    handler = ReviewHTTP('source_free', version)
    script = _ProactiveRoleScript((dict(timing_choice='now', cadence='conversational',
        beats=[dict(modality='text', text='我想和你说句话。')], stance='主动分享',
        brief_rationale='fixture character choice', impulse_summary='想说句话', confidence=7000, world_claims=[]),))

    async def http(request):
        body = json.loads(request.content)
        if body['tool_choice']['function']['name'] == 'character_role_proactive_contact_v1':
            return _http_result(body, json.loads(await script.complete_json(body['messages'], tools=body['tools'], tool_choice=body['tool_choice'])))
        return await handler(request)

    usage = WorldV2UsageStore(path=str(tmp_path / 'world.sqlite'))
    models = [DeepSeekChatModel('offline-fixture', 'https://fixture.invalid', name,
        thinking_enabled=False, transport=httpx.MockTransport(http), usage_observer=usage.record)
        for name in ('deepseek-v4-flash', 'deepseek-v4-pro', 'deepseek-v4-flash')]
    host = build_qq_c2c_host(settings=Settings(_env_file=None, database_path=tmp_path / 'world.sqlite',
        PRIMARY_USER_ID='geoff', WORLD_V2_EXPRESSION_EPISODE_MODE='off', WORLD_V2_TEXT_ENDPOINT_ENABLED=False),
        recipient_id='10001', bootstrap_at=START, model=models[0], world_support_model=FakeCompanionModel(),
        visible_source_review_required=True, visible_source_review_version=version, visible_author_tool_version='3',
        visible_author_schema_references=schema_references,
        visible_source_review_model=IndependentVisibleReviewer(meaning_models=(models[1], models[2]), source_model=models[2]),
        delivery=_DeliveredQQ(), use_configured_recall_embedding=False)
    try:
        assert (await host.inbound_text(message_id='independent-proactive', recipient_id='10001', text='我先去忙一会儿。', observed_at=START)).status == 'action_authorized'
        author_request = next(b for b in handler.requests if b['tool_choice']['function']['name'] == 'character_inbound_initial_v3')
        parameters = author_request['tools'][0]['function']['parameters']
        assert ('$def' in parameters) is schema_references
        assert ('"$ref"' in json.dumps(parameters)) is schema_references
        if schema_references:
            from companion_daemon.world_v2.character_interior.local_schema_references import expand_local_schema_references
            expanded = expand_local_schema_references(parameters)
            assert '"$ref"' not in json.dumps(expanded)
            assert len(json.dumps(parameters)) < len(json.dumps(expanded)) * .6
        due = START + timedelta(hours=12, seconds=1)
        await host.tick(tick_id='independent-proactive-due', logical_time_from=START, logical_time_to=due,
            observed_at=due, reason='offline_protocol_test', run_life_ecology=False)
        await host.drain(max_action_units=8, max_background_units=2)
        projection = host.export_replay_evidence().projection
        assert any(a.kind == 'proactive_message' for a in projection.actions)
        assert script.proactive_calls == 1 and handler.reader_count == 4
        audits = [a for a in projection.proposal_audits if a.proposal_kind == 'decision']
        assert len(audits) == 2
        for audit in audits:
            assert verify_recorded_candidate(audit=audit, model_result_audits=projection.model_result_audits)
    finally:
        await host.aclose()
        await asyncio.gather(*(m.aclose() for m in models))


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['repair_once', 'repair_twice'])
async def test_structural_reselection_records_failed_and_replacement_calls_without_local_filling(tmp_path, fault):
    handler = ReviewHTTP(fault, '10')
    async with application(tmp_path / 'world.sqlite', handler) as app:
        outcome = await app.respond(_inbound())
        assert (outcome.status == 'action_authorized') == (fault == 'repair_once')
        audits = _audits(app)
        sources = [a for a in audits if a.route.reason_code == 'validation.source_review']
        assert len(sources) == (4 if fault == 'repair_once' else 3)
        assert all(a.usage is not None for a in sources)
        retries = [b for b in handler.requests if 'invalid_prior_reading' in json.loads(b['messages'][1]['content'])]
        assert len(retries) == 1 and retries[0]['model'] == 'deepseek-v4-flash'
        if fault == 'repair_once':
            projection = app.export_replay_evidence().projection
            audit = next(a for a in projection.proposal_audits if a.proposal_kind == 'decision')
            assert verify_recorded_candidate(audit=audit, model_result_audits=projection.model_result_audits)
            winner = next(a for a in audits if a.visible_source_review_json)
            receipt = json.loads(winner.visible_source_review_json)['receipt']
            previous = receipt['rejected_meanings'][1]
            assert previous['review']['model_call_id'] != receipt['meaning_reviews'][1]['model_call_id']
            assert 'reading_complete' not in json.loads(previous['raw_response'])['decisions'][0]
            without_failed = tuple(r for r in projection.model_result_audits if r.model_call_id != previous['review']['model_call_id'])
            with pytest.raises(ValueError):
                verify_recorded_candidate(audit=audit, model_result_audits=without_failed)
        else:
            assert not app.export_replay_evidence().projection.actions
            assert not any(a.visible_source_review_json for a in audits)
