"""Batched character choices at the same public Memory initialization seam."""
import pytest

from test_prehistory_memory_runtime import Role, interior
from test_scene_recall import _two_lines
from test_character_prehistory import ACTOR, START, started_ledger
from test_character_interior import _author_lineage
from test_memory_candidate_authority import salience
from test_prehistory_memory_source import _read
from companion_daemon.world_v2.character_prehistory_runtime import PrehistoryArchiveRuntime
from companion_daemon.world_v2.prehistory_memory_runtime import PrehistoryMemoryRuntime


class BatchRole(Role):
    purposes = ('prehistory_memory_batch',)

    async def consider(self, request):
        self.calls.append(request)
        manifest = request.capability_manifest
        choices = []
        for i, item in enumerate(manifest.payload['items']):
            choice = {'retain': i != 1}
            if choice['retain']:
                choice.update(cue_kind='identity', retention_rationales=['identity_relevance'],
                    salience=salience().model_dump(mode='json', exclude={'matrix_digest','matrix_version'}))
            choices.append({'source_id': item['source_id'], 'retention': choice})
        return {'status': 'decision', 'summary': 'fixture independent per-source choices',
            'attended_source_refs': request.subject_source_refs, 'proposals': (),
            'author_lineage': _author_lineage(request),
            'decision': {'contract': 'character-interior-purpose-decision.1', 'purpose': request.purpose,
                'capability_ref': manifest.capability_ref, 'capability_payload_hash': manifest.payload_hash,
                'source_refs': list(manifest.source_refs), 'payload': {
                    'contract': 'character-interior-prehistory-memory-batch.1', 'choices': choices}}}


@pytest.mark.asyncio
async def test_one_role_call_keeps_independent_retention_and_recovers_without_call(tmp_path):
    path = tmp_path / 'batch.sqlite'
    ledger = started_ledger(path)
    PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(_two_lines(), created_at=START)
    role = BatchRole()
    character = interior(path, role)
    runtime = PrehistoryMemoryRuntime(ledger=ledger, owner_actor_ref=ACTOR, character_interior=character)
    try:
        result = await runtime.advance_batch_once(batch_size=7, allow_model_call=True)
        assert result['retained'] == 6 and result['no_change'] == 1
        assert len(role.calls) == 1
        assert len(_read(ledger).items) == 6
        assert all('scene-entry-1' not in e.source_id for item in _read(ledger).items for e in item.source_excerpts)
        before = ledger.project().semantic_hash
        assert ledger.rebuild().semantic_hash == before
        assert (await runtime.advance_batch_once(batch_size=3))['status'] == 'idle'
        assert (await runtime.advance_once())['status'] == 'idle'
        assert len(role.calls) == 1 and ledger.project().semantic_hash == before
    finally:
        character._turn_store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_missing_item_is_failure_and_only_missing_source_is_reconsidered(tmp_path):
    from datetime import timedelta
    from test_life_projection import commit, event
    class MissingOnce(BatchRole):
        async def consider(self, request):
            response = await super().consider(request)
            if len(self.calls) == 1:
                response['decision']['payload']['choices'].pop()
            return response
    path = tmp_path / 'partial.sqlite'
    ledger = started_ledger(path)
    PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(_two_lines(), created_at=START)
    role = MissingOnce()
    character = interior(path, role)
    runtime = PrehistoryMemoryRuntime(ledger=ledger, owner_actor_ref=ACTOR, character_interior=character)
    try:
        first = await runtime.advance_batch_once(batch_size=7, allow_model_call=True)
        assert (first['retained'], first['no_change'], first['technical_failures']) == (5,1,1)
        assert (await runtime.advance_batch_once(batch_size=7, allow_model_call=True))['status'] == 'retry_wait'
        assert len(role.calls) == 1
        later = START + timedelta(seconds=30)
        commit(ledger, [event('batch-retry-clock','ClockAdvanced',{
            'logical_time_from':START.isoformat(),'logical_time_to':later.isoformat()},at=later)])
        second = await runtime.advance_batch_once(batch_size=7, allow_model_call=True)
        assert second['retained'] == 1 and len(role.calls) == 2
        assert len(role.calls[-1].capability_manifest.payload['items']) == 1
        assert role.calls[-1].capability_manifest.payload['items'][0]['source_id'].endswith('scene-entry-6')
        assert ledger.rebuild().semantic_hash == ledger.project().semantic_hash
    finally:
        character._turn_store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_completed_batch_survives_commit_failure_and_switch_to_single_mode(tmp_path, monkeypatch):
    path = tmp_path / 'batch-recover.sqlite'
    ledger = started_ledger(path)
    PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(_two_lines(), created_at=START)
    role = BatchRole()
    character = interior(path, role)
    runtime = PrehistoryMemoryRuntime(ledger=ledger, owner_actor_ref=ACTOR, character_interior=character)
    commit = ledger.commit
    def unavailable(events, **kwargs):
        if any(e.event_type == 'PrehistoryMemoryDecisionRecorded' for e in events):
            raise OSError('fixture durable storage unavailable')
        return commit(events, **kwargs)
    monkeypatch.setattr(ledger, 'commit', unavailable)
    try:
        with pytest.raises(OSError, match='storage unavailable'):
            await runtime.advance_batch_once(batch_size=7, allow_model_call=True)
        assert len(role.calls) == 1 and not _read(ledger).items
        monkeypatch.setattr(ledger, 'commit', commit)
        for _ in range(7):
            await runtime.advance_once()
        assert len(role.calls) == 1 and len(_read(ledger).items) == 6
        assert (await runtime.advance_batch_once(batch_size=3))['status'] == 'idle'
        assert ledger.rebuild().semantic_hash == ledger.project().semantic_hash
    finally:
        character._turn_store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_public_batch_initializer_uses_real_role_tool_transport(tmp_path, monkeypatch):
    import json
    import httpx
    import test_world_stimulus_life_intent as shared
    from companion_daemon.llm import DeepSeekChatModel
    from companion_daemon.world_v2.character_prehistory import PrehistoryArchiveDocument
    from test_character_prehistory import reapprove
    monkeypatch.setenv('COMPANION_DISABLE_DEBUG_USAGE_LEDGER','1')
    data = _two_lines().document.model_dump(mode='json')
    data.update(world_id=shared.WORLD, actor_ref=shared.ACTOR)
    archive = reapprove(PrehistoryArchiveDocument.model_validate_json(json.dumps(data)))
    requests = []
    async def provider(request):
        body = json.loads(request.content)
        requests.append(body)
        material = json.loads(body['messages'][-1]['content'])
        capability=material['capability_manifest']
        assert material['inner_turn']['purpose'] == 'prehistory_memory_batch'
        assert material['background_context_profile']['profile_id'] == 'memory_retention'
        assert len(capability['payload']['items']) == 7
        assert body['tool_choice']['function']['name'] == 'character_role_prehistory_memory_batch_v1'
        choices=[{'source_id':item['source_id'],'retention':{'retain':False}}
                 for item in capability['payload']['items']]
        return shared._http_result(body, {'status':'decision','summary':'fixture chooses separately',
            'attended_source_refs':capability['source_refs'],'recall_query':None,'proposals':[],
            'decision':{'source_refs':capability['source_refs'],'payload':{'choices':choices}}})
    model=DeepSeekChatModel('offline-fixture','https://fixture.invalid','deepseek-v4-flash',
        thinking_enabled=False,transport=httpx.MockTransport(provider))
    app=shared._build(tmp_path/'public.sqlite',model,reviewed_prehistory=archive,prehistory_initialization_mode="character_select")
    try:
        result=await app.initialize_prehistory_once(batch_size=7,allow_model_call=True)
        assert result['no_change']==7 and result['technical_failures']==0, result
        assert len(requests)==1
        assert (await app.initialize_prehistory_once(batch_size=2,allow_model_call=True))['status']=='idle'
        assert len(requests)==1
    finally:
        await app.aclose()
        await model.aclose()


@pytest.mark.asyncio
async def test_empty_batch_reply_does_not_pin_all_future_retries_to_empty_result(tmp_path):
    from datetime import timedelta
    from test_life_projection import commit, event
    class EmptyOnce(BatchRole):
        async def consider(self, request):
            response=await super().consider(request)
            if len(self.calls)==1:
                response['decision']['payload']['choices']=[]
            return response
    path=tmp_path/'empty.sqlite'
    ledger=started_ledger(path)
    PrehistoryArchiveRuntime(ledger=ledger,owner_actor_ref=ACTOR).import_reviewed(_two_lines(),created_at=START)
    role=EmptyOnce()
    character=interior(path,role)
    runtime=PrehistoryMemoryRuntime(ledger=ledger,owner_actor_ref=ACTOR,character_interior=character)
    try:
        assert (await runtime.advance_batch_once(batch_size=7,allow_model_call=True))['technical_failures']==7
        later=START+timedelta(seconds=30)
        commit(ledger,[event('empty-batch-clock','ClockAdvanced',{
            'logical_time_from':START.isoformat(),'logical_time_to':later.isoformat()},at=later)])
        outcome=await runtime.advance_batch_once(batch_size=7,allow_model_call=True)
        assert len(role.calls)==2
        assert (outcome['retained'],outcome['no_change'],outcome['technical_failures'])==(6,1,0)
    finally:
        character._turn_store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_retry_success_recovery_preserves_each_sources_attempt_ordinal(tmp_path, monkeypatch):
    import json
    from datetime import timedelta
    from test_life_projection import commit, event
    from companion_daemon.world_v2.prehistory_memory_decision import decision_event_id, PrehistoryMemoryDecisionRecordedPayload
    from companion_daemon.world_v2.prehistory_memory_source import prehistory_memory_binding
    class EmptyOnce(BatchRole):
        async def consider(self, request):
            result=await super().consider(request)
            if len(self.calls)==1:
                result['decision']['payload']['choices']=[]
            return result
    path=tmp_path/'recover-retry.sqlite'
    ledger=started_ledger(path)
    rows=PrehistoryArchiveRuntime(ledger=ledger,owner_actor_ref=ACTOR).import_reviewed(_two_lines(),created_at=START)
    role=EmptyOnce()
    character=interior(path,role)
    runtime=PrehistoryMemoryRuntime(ledger=ledger,owner_actor_ref=ACTOR,character_interior=character)
    original_commit=ledger.commit
    def fail_success(events, **kwargs):
        if any(e.event_type=='PrehistoryMemoryDecisionRecorded' and json.loads(e.payload_json)['status']!='technical_failure' for e in events):
            raise OSError('fixture storage failed after paid retry')
        return original_commit(events,**kwargs)
    try:
        await runtime.advance_batch_once(batch_size=7,allow_model_call=True)
        later=START+timedelta(seconds=30)
        commit(ledger,[event('retry-clock','ClockAdvanced',{'logical_time_from':START.isoformat(),
            'logical_time_to':later.isoformat()},at=later)])
        monkeypatch.setattr(ledger,'commit',fail_success)
        with pytest.raises(OSError):
            await runtime.advance_batch_once(batch_size=7,allow_model_call=True)
        monkeypatch.setattr(ledger,'commit',original_commit)
        assert (await runtime.advance_batch_once(batch_size=7))['status']=='recovered'
        assert len(role.calls)==2
        for row in rows:
            source=prehistory_memory_binding(row)
            recorded,_=ledger.lookup_event_commit(decision_event_id(ledger.world_id,source))
            payload=PrehistoryMemoryDecisionRecordedPayload.model_validate_json(recorded.payload_json)
            assert payload.attempt_ordinal==2
    finally:
        character._turn_store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_batch_reports_logical_considerations_without_claiming_provider_call_count(tmp_path):
    class Corrected(BatchRole):
        async def consider(self, request):
            result=await super().consider(request)
            if len(self.calls)==1:
                result['status']='invalid_status'
            return result
    path=tmp_path/'correction-count.sqlite'
    ledger=started_ledger(path)
    PrehistoryArchiveRuntime(ledger=ledger,owner_actor_ref=ACTOR).import_reviewed(_two_lines(),created_at=START)
    role=Corrected()
    character=interior(path,role)
    runtime=PrehistoryMemoryRuntime(ledger=ledger,owner_actor_ref=ACTOR,character_interior=character)
    try:
        result=await runtime.advance_batch_once(batch_size=7,allow_model_call=True)
        assert result['status']=='completed' and len(role.calls)==2
        assert result['consider_calls']==1 and 'model_calls' not in result
    finally:
        character._turn_store.close()
        ledger.close()
