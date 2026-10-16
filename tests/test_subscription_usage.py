import asyncio
import json

import httpx
import pytest

from companion_daemon.llm import model_call_scope
from companion_daemon.subscription_usage import SubscriptionTokenLedger, SubscriptionAnthropicModel, BackgroundGLMModel


def ledger(tmp_path, **kwargs):
    return SubscriptionTokenLedger(tmp_path / 'subscription.sqlite', **kwargs)


def test_reservations_survive_restart_and_unknown_usage_keeps_capacity(tmp_path):
    store = ledger(tmp_path, five_hour_tokens=1200, weekly_tokens=1500)
    token = store.reserve(purpose='memory_consolidation', model='fixture', tokens=1100)
    reopened = ledger(tmp_path, five_hour_tokens=1200, weekly_tokens=1500)
    with pytest.raises(ValueError, match='subscription_token_limit'):
        reopened.reserve(purpose='memory_consolidation', model='fixture', tokens=101)
    assert reopened.snapshot()['reserved_or_used_tokens_5h'] == 1100
    assert reopened.snapshot()['unknown_or_pending_requests'] == 1
    assert token


def test_rolling_windows_and_cross_connection_admission_are_atomic(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    now = [1_000_000.0]
    first = ledger(tmp_path, five_hour_tokens=100, weekly_tokens=150, clock=lambda:now[0])
    second = ledger(tmp_path, five_hour_tokens=100, weekly_tokens=150, clock=lambda:now[0])
    def reserve(store):
        try:
            return store.reserve(purpose='memory_consolidation', model='fixture', tokens=90)
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, [first,second]))
    assert sum(r is not None for r in results) == 1
    now[0] += 5 * 3600 + 1
    with pytest.raises(ValueError, match='subscription_token_limit'):
        first.reserve(purpose='memory_consolidation',model='fixture',tokens=61)
    now[0] += 7 * 86400
    assert first.reserve(purpose='memory_consolidation',model='fixture',tokens=90)


@pytest.mark.asyncio
async def test_native_subscription_call_is_metered_without_monetary_pricing(tmp_path, monkeypatch):
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={'id':'m1', 'type':'message', 'model':'fixture',
            'stop_reason':'end_turn', 'content':[{'type':'text','text':'{}'}],
            'usage':{'input_tokens':20, 'output_tokens':3}})
    def forbidden(*a, **kw):
        raise AssertionError('subscription must not enter the CNY ledger')
    monkeypatch.setattr('companion_daemon.spend_account.maybe_record_debug_usage', forbidden)
    store = ledger(tmp_path)
    model = SubscriptionAnthropicModel('local-proxy','http://localhost:8788','fixture',
        token_ledger=store, transport=httpx.MockTransport(handler), timeout_seconds=30)
    try:
        with model_call_scope('life_development_draft'):
            assert await model.complete([{'role':'user','content':'test'}]) == '{}'
    finally:
        await model.aclose()
    assert len(calls) == 1
    assert store.snapshot()['reserved_or_used_tokens_5h'] == 23
    assert store.snapshot()['known_reported_tokens'] == 23
    assert store.snapshot()['unknown_or_pending_requests'] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", [SubscriptionAnthropicModel, BackgroundGLMModel])
async def test_cancelled_sent_request_retains_token_reservation(tmp_path, adapter):
    entered = asyncio.Event()
    async def handler(request):
        entered.set()
        await asyncio.Event().wait()
    store = ledger(tmp_path)
    model = adapter('local-proxy','http://localhost:8788','fixture',
        token_ledger=store, transport=httpx.MockTransport(handler), timeout_seconds=30)
    async def call():
        with model_call_scope('memory_consolidation'):
            await model.complete([{'role':'user','content':'test'}])
    task = asyncio.create_task(call())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await model.aclose()
    assert ledger(tmp_path).snapshot()['unknown_or_pending_requests'] == 1
    assert ledger(tmp_path).snapshot()['reserved_or_used_tokens_5h'] > 0


@pytest.mark.asyncio
@pytest.mark.parametrize('response_kind', ['valid', 'bare', 'extra', 'duplicate', 'wrong_tool'])
async def test_glm_root_union_transport_preserves_schema_and_exact_tool_result(tmp_path, response_kind):
    from copy import deepcopy
    from jsonschema import Draft202012Validator
    schema = {'type':'object', 'anyOf': [
        {'type':'object','properties':{'decision':{'enum':['keep']}},'required':['decision'],'additionalProperties':False},
        {'type':'object','properties':{'decision':{'enum':['change']},'value':{'type':'string'}},'required':['decision','value'],'additionalProperties':False},
    ]}
    tools = [{'type':'function','function':{'name':'role_result','description':'Choose a complete result.','parameters':schema}}]
    before = deepcopy(tools)
    seen = []
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        wrapped = body['tools'][0]['function']['parameters']
        assert wrapped['type'] == 'object'
        assert wrapped['properties']['result'] == schema
        assert wrapped['required'] == ['result'] and wrapped['additionalProperties'] is False
        for value in ({'decision':'keep'}, {'decision':'change','value':'later'}):
            Draft202012Validator(schema).validate(value)
            Draft202012Validator(wrapped).validate({'result':value})
        raw = {'valid':'{"result":{"decision":"keep"}}',
               'bare':'{"decision":"keep"}',
               'extra':'{"result":{"decision":"keep"},"extra":true}',
               'duplicate':'{"result":{"decision":"change","value":"now"},"result":{"decision":"keep"}}',
               'wrong_tool':'{"result":{"decision":"keep"}}'}[response_kind]
        return httpx.Response(200,json={'choices':[{'finish_reason':'tool_calls','message':{
            'content':None,'tool_calls':[{'id':'t1','type':'function','function':{
                'name':'wrong' if response_kind=='wrong_tool' else 'role_result','arguments':raw}}]}}],
            'usage':{'prompt_tokens':20,'completion_tokens':5,'total_tokens':25}})
    model = BackgroundGLMModel('fixture','https://fixture.invalid/v1','GLM-5.3-Flash',
        token_ledger=ledger(tmp_path),transport=httpx.MockTransport(handler))
    try:
        with model_call_scope('memory_consolidation'):
            call = model.complete_json([{'role':'user','content':'Choose.'}],tools=tools,tool_choice='auto')
            if response_kind == 'valid':
                assert json.loads(await call) == {'decision':'keep'}
            else:
                with pytest.raises(ValueError):
                    await call
        assert len(seen) == 1 and tools == before
        assert model.model_version.endswith(':object-tool-root.2')
    finally:
        await model.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("choice,count", [("none", 1), ("required", 1),
    ({"type":"function","function":{"name":"wrong"}}, 1),
    ({"type":"function","function":{"name":"result"}}, 2)])
async def test_glm_does_not_guess_a_different_tool_selection(tmp_path, choice, count):
    model = BackgroundGLMModel('fixture','https://fixture.invalid/v1','GLM-5.3-Flash',
        token_ledger=ledger(tmp_path))
    tools = [{'type':'function','function':{'name':name,'parameters':{'type':'object'}}}
             for name in ('result', 'other')[:count]]
    try:
        with pytest.raises(ValueError):
            model.request_payload([], temperature=0, tools=tools, tool_choice=choice)
        assert model.token_ledger.snapshot()['requests'] == 0
    finally:
        await model.aclose()
