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
