from types import SimpleNamespace
import asyncio
import json

import httpx
import pytest

from companion_daemon.config import Settings
from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.subscription_usage import BackgroundGLMModel, SubscriptionAnthropicModel
from companion_daemon.world_v2.character_interior.background_route import (
    BACKGROUND_CHARACTER_PURPOSES,
)
from companion_daemon.world_v2.semantic_chat_composition import build_semantic_chat_composition


def settings(tmp_path, **kwargs):
    return Settings(_env_file=None, database_path=tmp_path / 'world.sqlite',
                    DEEPSEEK_DEBUG_API_KEY='fixture-ds',
                    **{'WORLD_V2_BACKGROUND_PROVIDER':'anthropic_subscription',
                       'WORLD_V2_BACKGROUND_API_KEY':'local-proxy', **kwargs})


@pytest.mark.asyncio
@pytest.mark.parametrize('route,adapter,model_name', [
    ('anthropic_subscription', SubscriptionAnthropicModel, 'gemini-3.8-flash-medium'),
    ('glm', BackgroundGLMModel, 'GLM-5.3-Flash'),
])
async def test_configured_split_has_one_logical_author_and_actual_provider_lineage(tmp_path, route, adapter, model_name):
    comp = build_semantic_chat_composition(settings=settings(tmp_path,
        WORLD_V2_BACKGROUND_PROVIDER=route, WORLD_V2_BACKGROUND_MODEL=model_name), model_id_prefix='hybrid')
    try:
        assert isinstance(comp.world_support_model, adapter)
        registry = comp.character_interior._registry
        interactive = registry.for_purpose('proactive_contact')
        background = registry.for_purpose('memory_consolidation')
        assert type(interactive._model) is DeepSeekChatModel
        assert background._model is comp.world_support_model
        assert background._model.circuit_breaker is not interactive._model.circuit_breaker
        assert background._model_id == model_name
        assert background.requires_author_lineage
        assert background.author_identity['semantic_author_id'] == interactive.author_identity['semantic_author_id']
        assert background.author_identity['background_author_route']['model_id'] == model_name
        assert registry.semantic_author_count == 1
        assert registry.duplicate_purpose_owner_count == 0
        assert comp.character_interior._turn_lease_seconds >= 6 * 90
        for purpose in BACKGROUND_CHARACTER_PURPOSES:
            assert registry.for_purpose(purpose) is background
        for purpose in ('proactive_contact','expression_reconsideration','qq_attachment_perception',
                        'external_perception_attention','media_selection'):
            assert registry.for_purpose(purpose) is interactive
        assert registry.for_purpose('inbound_turn') is not background
        assert comp.character_interior_health()['background_compute']['usage']['cny_cost'] is None
    finally:
        await comp.aclose()
    assert comp.world_support_model.client.is_closed


@pytest.mark.asyncio
async def test_slow_background_lifecycle_keeps_lease_through_review_and_recall(tmp_path):
    from datetime import UTC, datetime, timedelta
    from test_character_interior_turn_store import _request
    from companion_daemon.world_v2.character_interior.turn_store import (
        open_sqlite_character_interior_turn_store,
    )
    comp = build_semantic_chat_composition(settings=settings(tmp_path,
        WORLD_V2_BACKGROUND_TIMEOUT_SECONDS=300), model_id_prefix='hybrid')
    store = open_sqlite_character_interior_turn_store(path=tmp_path/'leases.sqlite', world_id='world:one')
    request, now = _request(), datetime(2026,10,13,tzinfo=UTC)
    try:
        acquired = store.acquire(request=request, owner_id='first', now=now,
            lease_seconds=comp.character_interior._turn_lease_seconds)
        after_work = now + timedelta(seconds=6 * 280)
        competing = store.acquire(request=request, owner_id='second', now=after_work, lease_seconds=120)
        assert competing.status == 'owned_elsewhere'
        store.checkpoint(request=request, owner_id='first', now=after_work,
            lease_token=acquired.record.lease_token, attempt_ordinal=acquired.record.attempt_ordinal,
            authored_state_json='{}', authored_state_hash='d'*64)
    finally:
        store.close()
        await comp.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('route,provider,expected', [('anthropic_subscription','anthropic',90.0),('glm','glm',90.0),('deepseek','deepseek',8.0)])
async def test_real_host_passes_background_budget_to_fact_adapter(tmp_path, route, provider, expected):
    from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host
    from drive_production_lanes import CaptureDelivery
    config = settings(tmp_path)
    config.world_v2_background_provider = route
    host = build_qq_c2c_host(settings=config, recipient_id='fixture-user',
        reviewed_prehistory=None, use_configured_recall_embedding=False, delivery=CaptureDelivery())
    try:
        adapter = host._host._application._turns._runtime._fact_adapter
        assert adapter._timeout_seconds == expected
        assert adapter._model.provider == provider
    finally:
        await host.aclose()


@pytest.mark.asyncio
async def test_core_invalid_memory_result_corrects_on_same_background_model(tmp_path):
    from test_character_interior_structured_role import (
        _QueueModel, _RequiredToolQueueModel, _result, _Projection, _CURSOR, _NOW,
    )
    from test_background_recovery_regressions import manifest, choices
    from companion_daemon.world_v2.character_interior.core import CharacterInterior
    from companion_daemon.world_v2.character_interior.contracts import InteriorOpportunity
    from companion_daemon.world_v2.character_interior.structured_role import StructuredCharacterRoleFaculty
    from companion_daemon.world_v2.character_interior.background_route import BackgroundStructuredRoleFaculty
    invalid = _result(status='decision', decision={'payload':choices()}).replace(
        '"proposals": []', '"proposals": [{"proposal_type":"affect","source_refs":["source:private_self"]}]')
    model = _RequiredToolQueueModel(invalid, _result(status='decision', decision={'payload':choices()}))
    model.model = 'fixture-background'
    interactive_model = _QueueModel()
    interactive = StructuredCharacterRoleFaculty(model=interactive_model, model_id='fixture-interactive')
    background = BackgroundStructuredRoleFaculty(interactive=interactive, background_model=model,
        character_disposition=None, life_source_reviewer=None, reference_wire=False)
    interior = CharacterInterior(projection=_Projection(), role=background)
    opportunity = InteriorOpportunity(opportunity_ref='opportunity:memory:1', inner_turn_ref='turn:memory:1',
        world_id='world:test',actor_ref='character:zhizhi',trigger_ref='trigger:memory:1',cursor=_CURSOR,
        logical_time=_NOW,purpose='memory_consolidation',source_refs=('source:private_self',),
        capability_manifest=manifest())
    result = await interior.consider(opportunity)
    assert result.status == 'decided', (result.failure_code, len(model.calls))
    assert len(model.calls) == 2 and not interactive_model.calls
    correction = json.loads(model.calls[1][0][-1]['content'])['correction']
    assert correction['failure_code'] == 'purpose_proposals_not_allowed'
    assert result.author_lineage.model_id == 'fixture-background'


@pytest.mark.asyncio
@pytest.mark.parametrize('route', ['anthropic_subscription','glm'])
async def test_background_failure_and_correction_stay_on_background_route(tmp_path, monkeypatch, route):
    comp = build_semantic_chat_composition(settings=settings(tmp_path, WORLD_V2_BACKGROUND_PROVIDER=route), model_id_prefix='hybrid')
    calls = []
    async def fail(request, **kwargs):
        calls.append((request.purpose, request.correction_ordinal))
        raise httpx.ReadTimeout('fixture timeout')
    try:
        registry = comp.character_interior._registry
        background = registry.for_purpose('memory_consolidation')
        monkeypatch.setattr(background, '_complete', fail)
        for ordinal in (0, 1):
            request = SimpleNamespace(purpose='memory_consolidation', correction_ordinal=ordinal,
                                      phase='consider')
            with pytest.raises(httpx.ReadTimeout):
                await registry.for_purpose(request.purpose).consider(request)
        assert calls == [('memory_consolidation',0), ('memory_consolidation',1)]
    finally:
        await comp.aclose()


def test_missing_key_and_ambiguous_injection_fail_before_model_construction(tmp_path):
    config = settings(tmp_path)
    config.world_v2_background_api_key = None
    with pytest.raises(ValueError, match='WORLD_V2_BACKGROUND_API_KEY'):
        build_semantic_chat_composition(settings=config, model_id_prefix='hybrid')
    with pytest.raises(ValueError, match='conflicts'):
        build_semantic_chat_composition(settings=settings(tmp_path),
                                        world_support_model=object(), model_id_prefix='hybrid')


@pytest.mark.asyncio
@pytest.mark.parametrize('route', ['anthropic_subscription','glm'])
async def test_real_memory_contract_uses_native_proxy_while_interactive_can_complete(tmp_path, route):
    from test_background_recovery_regressions import choices, manifest
    from test_character_interior_structured_role import _request, _result
    from companion_daemon.llm import model_call_scope
    from companion_daemon.world_v2.character_interior.core import CharacterInterior

    model_name = 'GLM-5.3-Flash' if route == 'glm' else 'gemini-3.8-flash-medium'
    comp = build_semantic_chat_composition(settings=settings(tmp_path, WORLD_V2_BACKGROUND_PROVIDER=route,
        WORLD_V2_BACKGROUND_MODEL=model_name), model_id_prefix='hybrid')
    entered, release = asyncio.Event(), asyncio.Event()
    requests = []
    async def handler(request):
        body = json.loads(request.content)
        requests.append((str(request.url), body))
        if request.url.host == 'localhost':
            entered.set()
            await release.wait()
            if route == 'glm':
                assert request.url.path == '/chat/completions'
                assert body['thinking'] == {'type':'enabled'} and body['reasoning_effort'] == 'low'
                assert body['tool_choice'] == 'auto' and body['max_tokens'] == 64000
                return httpx.Response(200, json={'choices':[{'finish_reason':'tool_calls','message':{
                    'content':None,'tool_calls':[{'id':'t1','type':'function','function':{
                        'name':body['tools'][0]['function']['name'],
                        'arguments':json.dumps({'result':json.loads(_result(status='decision', decision={'payload':choices()}))})}}]}}],
                    'usage':{'prompt_tokens':12,'completion_tokens':20,'total_tokens':32}})
            return httpx.Response(200, json={'id':'memory1', 'type':'message',
                'model':body['model'], 'stop_reason':'tool_use',
                'content':[{'type':'tool_use','id':'t1','name':body['tools'][0]['name'],
                            'input':json.loads(_result(status='decision', decision={'payload':choices()}))}],
                'usage':{'input_tokens':12,'output_tokens':20}})
        return httpx.Response(200, json={'choices':[{'message':{'content':'即时响应'},
                                                    'finish_reason':'stop'}],
                                        'usage':{'prompt_tokens':3,'completion_tokens':2}})
    registry = comp.character_interior._registry
    background = registry.for_purpose('memory_consolidation')
    interactive = registry.for_purpose('proactive_contact')._model
    for model in (background._model, interactive):
        await model.client.aclose()
        model.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    request = await _request(purpose='memory_consolidation', capability_manifest=manifest())
    task = asyncio.create_task(background.consider(request))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        with model_call_scope('inbound_turn'):
            assert await asyncio.wait_for(interactive.complete([{'role':'user','content':'hello'}]), 2) == '即时响应'
        assert not task.done()
        release.set()
        result = await task
        validated = CharacterInterior._validate_role_result(result, snapshot=request.snapshot,
            request=request, allowed_statuses={'decision'}, require_author_lineage=True)
        assert validated.author_lineage.model_id == model_name
        assert result['decision']['payload']['choices'] == choices()['choices']
        assert requests[0][0] == ('http://localhost:8788/v1/messages' if route == 'anthropic_subscription' else 'http://localhost:8788/chat/completions')
        assert requests[1][1]['model'] == 'deepseek-v4-flash'
        assert comp.world_support_model.token_ledger.snapshot()['known_reported_tokens'] == 32
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await comp.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('valid_correction', [True, False])
async def test_glm_bad_envelope_gets_one_same_role_correction(tmp_path, valid_correction):
    from companion_daemon.subscription_usage import SubscriptionTokenLedger
    from test_character_interior_structured_role import _QueueModel, _result, _Projection, _CURSOR, _NOW
    from test_background_recovery_regressions import manifest, choices
    from companion_daemon.world_v2.character_interior.core import CharacterInterior
    from companion_daemon.world_v2.character_interior.contracts import InteriorOpportunity
    from companion_daemon.world_v2.character_interior.structured_role import StructuredCharacterRoleFaculty
    from companion_daemon.world_v2.character_interior.background_route import BackgroundStructuredRoleFaculty
    valid = _result(status='decision', decision={'payload':choices()})
    bad = valid  # Valid role contents, but missing the declared transport wrapper.
    calls = []
    def handler(request):
        body=json.loads(request.content)
        calls.append(body)
        raw=json.dumps({'result':json.loads(valid)}) if valid_correction and len(calls)==2 else bad
        return httpx.Response(200,json={'choices':[{'finish_reason':'tool_calls','message':{
            'content':None,'tool_calls':[{'id':'t1','type':'function','function':{
                'name':body['tools'][0]['function']['name'],'arguments':raw}}]}}],
            'usage':{'prompt_tokens':20,'completion_tokens':5,'total_tokens':25}})
    model=BackgroundGLMModel('fixture','https://fixture.invalid/v1','GLM-5.3-Flash',
        token_ledger=SubscriptionTokenLedger(tmp_path/'glm.sqlite'),transport=httpx.MockTransport(handler))
    interactive_model=_QueueModel()
    interactive=StructuredCharacterRoleFaculty(model=interactive_model,model_id='fixture-interactive')
    background=BackgroundStructuredRoleFaculty(interactive=interactive,background_model=model,
        character_disposition=None,life_source_reviewer=None,reference_wire=False)
    interior=CharacterInterior(projection=_Projection(),role=background)
    opportunity=InteriorOpportunity(opportunity_ref='opportunity:memory:1',inner_turn_ref='turn:memory:1',
        world_id='world:test',actor_ref='character:zhizhi',trigger_ref='trigger:memory:1',cursor=_CURSOR,
        logical_time=_NOW,purpose='memory_consolidation',source_refs=('source:private_self',),capability_manifest=manifest())
    try:
        result=await interior.consider(opportunity)
        assert len(calls)==2 and not interactive_model.calls
        assert all(c['model']=='GLM-5.3-Flash' for c in calls)
        correction=json.loads(calls[1]['messages'][-1]['content'])['correction']
        assert correction['failure_code']=='role_result_schema_invalid'
        assert 'result object envelope' in json.dumps(correction)
        if valid_correction:
            assert result.status=='decided'
        else:
            assert result.status!='decided' and result.failure_code
    finally:
        await model.aclose()
