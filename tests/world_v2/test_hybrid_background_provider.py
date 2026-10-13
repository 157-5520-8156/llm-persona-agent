from types import SimpleNamespace
import asyncio
import json

import httpx
import pytest

from companion_daemon.config import Settings
from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.subscription_usage import SubscriptionAnthropicModel
from companion_daemon.world_v2.character_interior.background_route import (
    BACKGROUND_CHARACTER_PURPOSES,
)
from companion_daemon.world_v2.semantic_chat_composition import build_semantic_chat_composition


def settings(tmp_path, **kwargs):
    return Settings(_env_file=None, database_path=tmp_path / 'world.sqlite',
                    DEEPSEEK_DEBUG_API_KEY='fixture-ds',
                    WORLD_V2_BACKGROUND_PROVIDER='anthropic_subscription',
                    WORLD_V2_BACKGROUND_API_KEY='local-proxy', **kwargs)


@pytest.mark.asyncio
async def test_configured_split_has_one_logical_author_and_actual_provider_lineage(tmp_path):
    comp = build_semantic_chat_composition(settings=settings(tmp_path), model_id_prefix='hybrid')
    try:
        assert isinstance(comp.world_support_model, SubscriptionAnthropicModel)
        registry = comp.character_interior._registry
        interactive = registry.for_purpose('proactive_contact')
        background = registry.for_purpose('memory_consolidation')
        assert type(interactive._model) is DeepSeekChatModel
        assert background._model is comp.world_support_model
        assert background._model_id == 'gemini-3.8-flash-medium'
        assert background.requires_author_lineage
        assert background.author_identity['semantic_author_id'] == interactive.author_identity['semantic_author_id']
        assert background.author_identity['background_author_route']['model_id'] == 'gemini-3.8-flash-medium'
        assert registry.semantic_author_count == 1
        assert registry.duplicate_purpose_owner_count == 0
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
async def test_background_failure_and_correction_stay_on_background_route(tmp_path, monkeypatch):
    comp = build_semantic_chat_composition(settings=settings(tmp_path), model_id_prefix='hybrid')
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
async def test_real_memory_contract_uses_native_proxy_while_interactive_can_complete(tmp_path):
    from test_background_recovery_regressions import choices, manifest
    from test_character_interior_structured_role import _request, _result
    from companion_daemon.llm import model_call_scope
    from companion_daemon.world_v2.character_interior.core import CharacterInterior

    comp = build_semantic_chat_composition(settings=settings(tmp_path), model_id_prefix='hybrid')
    entered, release = asyncio.Event(), asyncio.Event()
    requests = []
    async def handler(request):
        body = json.loads(request.content)
        requests.append((str(request.url), body))
        if request.url.host == 'localhost':
            entered.set()
            await release.wait()
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
        assert validated.author_lineage.model_id == 'gemini-3.8-flash-medium'
        assert result['decision']['payload']['choices'] == choices()['choices']
        assert requests[0][0] == 'http://localhost:8788/v1/messages'
        assert requests[1][1]['model'] == 'deepseek-v4-flash'
        assert comp.world_support_model.token_ledger.snapshot()['known_reported_tokens'] == 32
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await comp.aclose()
