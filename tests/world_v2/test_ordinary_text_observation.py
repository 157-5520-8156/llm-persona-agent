"""Direct text is explicitly unreviewed, source-bound, replayable, and not a Fact."""
import json
from copy import deepcopy
from dataclasses import replace

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.character_interior.production import compose_production_character_interior
from companion_daemon.world_v2.companion_identity import CompanionIdentityFrame
from companion_daemon.world_v2.expression_draft import QQ_NAPCAT_EXPRESSION_CAPABILITIES
from companion_daemon.world_v2.production_turn_application import build_sqlite_world_v2_turn_application
from companion_daemon.world_v2.proposal_audit_schemas import RecordedModelResultAudit
from companion_daemon.world_v2.proposal_envelope import validate_proposal_envelope
from companion_daemon.world_v2.visible_independent_review_configuration import GroundedVisibleReviewer
from companion_daemon.world_v2.visible_review_evidence_storage import read_review_evidence
from companion_daemon.world_v2.visible_source_runtime import verify_recorded_candidate, verify_evidence, result_recall_audits
from companion_daemon.world_v2.ordinary_text_observation import EVIDENCE, eligible_text
from companion_daemon.world_v2.text_shadow_observer import TextShadowObserver
from test_production_turn_application import NOW, _config, _DeliveredTransport, _Identities, _Router
from test_whole_candidate_author import _decision, _inbound
from test_world_stimulus_life_intent import _http_result


class ObserverModel:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    async def complete_json_with_usage(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError('observer unavailable')
        return json.dumps({'severity':'major','findings':[{'quote':'冰岛','reason':'没有此经历来源'}]}, ensure_ascii=False), {}


@pytest.mark.asyncio
@pytest.mark.parametrize('observer_failure', [False, True])
async def test_unreviewed_text_delivers_once_observer_is_non_authoritative_and_replays(tmp_path, monkeypatch, observer_failure):
    monkeypatch.setenv('COMPANION_DISABLE_DEBUG_USAGE_LEDGER', '1')
    path = tmp_path/'world.sqlite'
    calls = []
    async def reply(request):
        body = json.loads(request.content)
        calls.append(body)
        assert body['tool_choice']['function']['name'].startswith('character_inbound_')
        value = _decision()
        # Deliberately unsupported speech proves this new receipt does NOT
        # assert a semantic pass, nor mint an Experience/Fact from a reply.
        value['expression_draft']['beats'] = [{'modality':'text','text':'我昨天在冰岛见到了一头北极熊。'}]
        return _http_result(body, {'result':value})
    model = DeepSeekChatModel('offline', 'https://fixture.invalid', 'deepseek-flash',
                             thinking_enabled=False, transport=httpx.MockTransport(reply))
    observation_model = ObserverModel(observer_failure)
    observer = TextShadowObserver(database=path, world_id=_config().world_id, model=observation_model, sample_every=1)
    capabilities = QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(update={'private_turn_state_mode':'required'})
    interior = compose_production_character_interior(
        flash_model=model, thinking_model=None, source_closure_model=None, report_relative_source_closure_model=None,
        source_closure_reselection_lane=None, expression_episode_observer_model=None,
        flash_model_id=model.model, thinking_model_id=None, expression_capabilities=capabilities,
        identity_frame=CompanionIdentityFrame(companion_name='小满',counterpart_name='用户'),
        whole_candidate_mode=True, visible_source_review_model=GroundedVisibleReviewer(source_model=observation_model),
        atomic_tool_envelope_version='3', visible_source_review_version='25',
        ordinary_text_review_mode='sampled', text_shadow_observer=observer,
    )
    transport = _DeliveredTransport()
    app = build_sqlite_world_v2_turn_application(path=path,
        config=replace(_config(), expression_capabilities=capabilities, visible_source_review_required=True,
                       expression_episode_mode='off', character_memory_enabled=False),
        identities=_Identities(), router=_Router(), character_interior=interior, transport=transport, now=NOW)
    try:
        before = app.export_replay_evidence().projection
        assert (await app.respond(_inbound())).status == 'action_authorized'
        assert len(calls) == 1 and observation_model.calls == 0
        assert not await observer.observe_once()  # Undelivered candidates never reviewed.
        assert (await app.drain_actions_once()).status == 'settled'
        sent = app.export_replay_evidence().projection
        assert sent.facts == before.facts and sent.experiences == before.experiences
        assert await observer.observe_once()
        assert observation_model.calls == 1
        assert not await observer.observe_once()
        after = app.export_replay_evidence().projection
        assert after.semantic_hash == sent.semantic_hash
        assert len(transport.bodies) == 1
        assert observer.health_snapshot()['counts'] == ({'failed':1} if observer_failure else {'observed':1})
        parent = next(RecordedModelResultAudit.model_validate_json(r.audit_json) for r in after.model_result_audits
                      if RecordedModelResultAudit.model_validate_json(r.audit_json).visible_source_review_json is not None)
        evidence = read_review_evidence(parent.visible_source_review_json)
        assert evidence['contract'] == EVIDENCE and evidence['semantic_review'] == 'not_performed'
        candidate = next(a for a in after.proposal_audits if a.model_call_id == parent.model_call_id)
        assert verify_recorded_candidate(audit=candidate,model_result_audits=after.model_result_audits)
        proposal = validate_proposal_envelope(json.loads(candidate.proposal_json))
        assert eligible_text(proposal)
        for field in ['candidate_hash','author_request_hash','source_table_hash']:
            bad = deepcopy(evidence)
            bad[field] = 'f'*64
            with pytest.raises(ValueError):
                verify_evidence(raw=json.dumps(bad,ensure_ascii=False,sort_keys=True,separators=(',',':')),
                    proposal=proposal, requirement=evidence['requirement_json'],author_call=parent.model_call_id,
                    author_request_hash=parent.request_hash,subcalls=(),recall_audits=result_recall_audits(parent))
        assert app._ledger.rebuild().semantic_hash == after.semantic_hash
        changed = proposal.model_copy(update={'action_intents':tuple(a.model_copy(update={'kind':'image'})for a in proposal.action_intents)})
        assert not eligible_text(changed)
        for kind in ('fact_transition', 'experience_transition', 'memory_candidate_transition', 'activity_transition'):
            injected = proposal.proposed_changes[0].model_copy(update={'kind':kind})
            assert not eligible_text(proposal.model_copy(update={
                'proposed_changes': (*proposal.proposed_changes, injected),
            }))

    finally:
        await observer.aclose()
        await app.aclose()
        await model.aclose()


@pytest.mark.asyncio
async def test_observation_daily_admission_is_shared_and_effect_once(tmp_path):
    import time
    first = TextShadowObserver(database=tmp_path/'world.sqlite', world_id='test', model=ObserverModel(), daily_limit=1)
    second = TextShadowObserver(database=tmp_path/'world.sqlite', world_id='test', model=ObserverModel(), daily_limit=1)
    try:
        first._connection.executemany('INSERT INTO text_observations VALUES (?,?,?,?,?,NULL,NULL,NULL,NULL,NULL)',
                                      [('a','test','plan:a','pending',time.time()),('b','test','plan:b','pending',time.time())])
        first._connection.commit()
        today = time.time()-60
        assert first._claim('a', today)
        assert not second._claim('a', today)
        assert not second._claim('b', today)
        assert first.health_snapshot()['counts'] == {'pending':1,'running':1}
    finally:
        await first.aclose()
        await second.aclose()
