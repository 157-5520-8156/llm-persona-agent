"""A Fact job already in flight competes with the next visible reply."""

import asyncio
from datetime import timedelta
import json
import threading

import pytest

from companion_daemon.config import Settings
from companion_daemon.world_v2.interactive_turn_budget import InteractiveTurnBudgetPolicy
from companion_daemon.world_v2.interaction_fact_trigger_runtime import InteractionFactTriggerRuntime
from companion_daemon.world_v2 import interaction_fact_trigger_runtime as fact_module
from companion_daemon.world_v2.errors import ConcurrencyConflict
from companion_daemon.world_v2.fact_v2_acceptance_runtime import FactV2AcceptanceRuntime
from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host
from test_expression_retry_regressions import (
    NOW, _BatchUpdatingFactModel, _Delivery, _MonotonicClock,
    _SecondTurnBlockingExpressionModel, _runtime,
)


class _BlockedFact(_BatchUpdatingFactModel):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def complete(self, messages, *, temperature=0.8):
        if "long-term user-fact memory" in str(messages[0]["content"]):
            self.entered.set()
            await self.release.wait()
            request = json.loads(messages[-1]["content"])
            if not isinstance(request.get("observations"), list):
                self.fact_calls += 1
                if "深圳" not in json.dumps(request, ensure_ascii=False):
                    return '{"retain":false}'
                return json.dumps({"retain": True, "predicate_code": "location.home",
                                   "value": "深圳", "privacy_class": "personal",
                                   "confidence": 9000, "rationale": "Explicit residence."})
        return await super().complete(messages, temperature=temperature)


class _Author(_SecondTurnBlockingExpressionModel):
    async def complete(self, messages, *, temperature=0.8):
        raw = await super().complete(messages, temperature=temperature)
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return raw
        expression = value.get("expression_draft", value)
        if isinstance(expression, dict) and "timing_choice" in expression:
            expression["turn_posture"] = "continue"
            return json.dumps(value, ensure_ascii=False)
        return raw


async def _pair(tmp_path):
    author, fact, clock = _Author(), _BlockedFact(), _MonotonicClock()
    budget = InteractiveTurnBudgetPolicy(
        total_seconds=1.0, hedge_after_seconds=0.2,
        acceptance_dispatch_reserve_seconds=0.1, technical_recovery_seconds=0.5,
        validation_recovery_seconds=0.5, validation_reselection_seconds=0.5,
        clock=clock, wall_clock=lambda: NOW,
    )
    host = build_qq_c2c_host(
        settings=Settings(database_path=tmp_path / "pair.sqlite", PRIMARY_USER_ID="geoff",
                          WORLD_V2_EXPRESSION_EPISODE_MODE="off"),
        recipient_id="10001", bootstrap_at=NOW, model=author, world_support_model=fact,
        delivery=_Delivery(), interactive_turn_budget_policy=budget,
        action_due_now=lambda: NOW,
        use_configured_recall_embedding=False,
    )
    runtime = _runtime(host)
    assert runtime._fact_background_commit_gate is host._provider_reconciliation_gate
    assert host._host._application._life_ecology._aftermath_followup._background_visible_gate is host._provider_reconciliation_gate
    worker = InteractionFactTriggerRuntime(
        ledger=runtime._fact_acceptance.ledger, acceptance=runtime._fact_acceptance,
        adapter=runtime._fact_adapter, owner_id=runtime._interaction_fact_owner,
        commit_gate=runtime._fact_background_commit_gate,
    )
    first = await host.inbound_text(message_id="old-fact", recipient_id="10001",
                                    text="我现在住在深圳", observed_at=NOW)
    assert first.status == "action_authorized"
    task = asyncio.create_task(worker.drain_one())
    await asyncio.wait_for(fact.entered.wait(), 5)
    reply = asyncio.create_task(host.inbound_text(
        message_id="new-sensitive", recipient_id="10001", text="最近有些心事",
        observed_at=NOW + timedelta(seconds=1)))
    await asyncio.wait_for(author.second_entered.wait(), 5)
    return host, runtime, worker, author, fact, clock, task, reply


async def _cleanup(host, author, fact, *tasks):
    author.second_release.set()
    fact.release.set()
    for task in tasks:
        if task and not task.done():
            task.cancel()
    await asyncio.gather(*(task for task in tasks if task), return_exceptions=True)
    await host.aclose()


@pytest.mark.asyncio
async def test_already_running_fact_must_not_invalidate_current_visible_turn(tmp_path, monkeypatch):
    clock = _MonotonicClock()
    budget = InteractiveTurnBudgetPolicy(
        total_seconds=1.0, hedge_after_seconds=0.2,
        acceptance_dispatch_reserve_seconds=0.1, technical_recovery_seconds=0.5,
        validation_recovery_seconds=0.5, validation_reselection_seconds=0.5,
        clock=clock, wall_clock=lambda: NOW,
    )
    author = _Author()
    fact = _BlockedFact()
    host = build_qq_c2c_host(
        settings=Settings(database_path=tmp_path / "fact-foreground.sqlite", PRIMARY_USER_ID="geoff",
                          WORLD_V2_EXPRESSION_EPISODE_MODE="off"),
        recipient_id="10001", bootstrap_at=NOW, model=author, world_support_model=fact,
        delivery=_Delivery(), interactive_turn_budget_policy=budget,
        action_due_now=lambda: NOW,
        use_configured_recall_embedding=False,
    )
    runtime = _runtime(host)
    worker = InteractionFactTriggerRuntime(
        ledger=runtime._fact_acceptance.ledger, acceptance=runtime._fact_acceptance,
        adapter=runtime._fact_adapter, character_interior=runtime._character_interior,
        memory_lifecycle=runtime._fact_memory_lifecycle,
        memory_actor_ref=runtime._fact_memory_actor_ref, owner_id=runtime._interaction_fact_owner,
        commit_gate=runtime._fact_background_commit_gate,
    )
    fact_task = reply_task = None
    try:
        first = await host.inbound_text(message_id="old-fact", recipient_id="10001",
                                        text="我现在住在深圳", observed_at=NOW)
        assert first.status == "action_authorized"
        fact_task = asyncio.create_task(worker.drain_one())
        await asyncio.wait_for(fact.entered.wait(), 5)
        reply_task = asyncio.create_task(host.inbound_text(
            message_id="new-sensitive", recipient_id="10001", text="最近有些心事",
            observed_at=NOW + timedelta(seconds=1)))
        await asyncio.wait_for(author.second_entered.wait(), 5)
        fact.release.set()
        # The already-running background call has returned. Its durable
        # decision can be preserved, but its world effect must not overtake
        # the pinned user turn and spend the same finite author budget twice.
        try:
            await asyncio.wait_for(asyncio.shield(fact_task), 0.5)
        except TimeoutError:
            pass
        projection = await host._host.action_due_projection()
        raced = any(a.proposal_kind == "fact_commit" for a in projection.proposal_audits) or bool(projection.facts)
        if raced:
            clock.value = 2.0
        author.second_release.set()
        reply = await asyncio.wait_for(reply_task, 5)
        assert reply.status == "action_authorized", (reply.status, raced)
        assert not raced
        assert author.expression_calls == 2
        await asyncio.wait_for(fact_task, 5)
        projection = await host._host.action_due_projection()
        assert len(projection.facts) == 1
    finally:
        fact.release.set()
        author.second_release.set()
        for task in (fact_task, reply_task):
            if task and not task.done():
                task.cancel()
        await asyncio.gather(*(x for x in (fact_task, reply_task) if x), return_exceptions=True)
        await host.aclose()


@pytest.mark.asyncio
async def test_cancellation_after_paid_fact_return_records_then_recovers(tmp_path):
    host, _runtime_instance, worker, author, fact, _clock, task, reply = await _pair(tmp_path)
    try:
        fact.release.set()
        await asyncio.sleep(0.1)
        task.cancel()
        await asyncio.sleep(0.05)
        assert not task.done(), "cancellation must not discard the returned decision"
        task.cancel()
        await asyncio.sleep(0.05)
        assert not task.done(), "repeated cancellation must also await the durable record"
        author.second_release.set()
        assert (await asyncio.wait_for(reply, 5)).status == "action_authorized"
        cancelled = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(cancelled[0], asyncio.CancelledError)
        projection = await host._host.action_due_projection()
        assert len(projection.interaction_fact_decisions) == 1
        assert not projection.facts
        decision = projection.interaction_fact_decisions[0]
        frozen_decision = (decision.request_hash, decision.decision_json, decision.decision_hash)
        await host.aclose()
        await host.wait_for_shutdown_quiescence()
        host = build_qq_c2c_host(
            settings=Settings(database_path=tmp_path / "pair.sqlite", PRIMARY_USER_ID="geoff",
                              WORLD_V2_EXPRESSION_EPISODE_MODE="off"),
            recipient_id="10001", bootstrap_at=NOW, model=author, world_support_model=fact,
            delivery=_Delivery(), action_due_now=lambda: NOW,
            use_configured_recall_embedding=False,
        )
        runtime = _runtime(host)
        worker = InteractionFactTriggerRuntime(
            ledger=runtime._fact_acceptance.ledger, acceptance=runtime._fact_acceptance,
            adapter=runtime._fact_adapter, owner_id=runtime._interaction_fact_owner,
            commit_gate=runtime._fact_background_commit_gate,
        )
        reopened_decision = (await host._host.action_due_projection()).interaction_fact_decisions[0]
        assert (reopened_decision.request_hash, reopened_decision.decision_json,
                reopened_decision.decision_hash) == frozen_decision
        # New source work is selected before recovery of the claimed older
        # source; it is distinct, and must not relaunch the old paid request.
        assert (await worker.drain_one()).work_status == "no_change"
        resumed = await worker.drain_one()
        assert resumed.work_status == "accepted"
        assert fact.fact_calls == 2
        assert len((await host._host.action_due_projection()).facts) == 1
    finally:
        await _cleanup(host, author, fact, task, reply)


@pytest.mark.asyncio
async def test_continuous_visible_turn_has_bounded_fact_wait_and_keeps_cas(tmp_path, monkeypatch):
    monkeypatch.setattr(fact_module, "_VISIBLE_COMMIT_WAIT_SECONDS", 0.05)
    host, _runtime_instance, _worker, author, fact, clock, task, reply = await _pair(tmp_path)
    try:
        fact.release.set()
        result = await asyncio.wait_for(task, 3)
        assert result.work_status == "accepted"
        assert not reply.done()
        assert len((await host._host.action_due_projection()).facts) == 1
        clock.value = 2.0
        author.second_release.set()
        result = await asyncio.wait_for(reply, 5)
        assert result.status == "deferred"
        projection = await host._host.action_due_projection()
        assert any(json.loads(a.audit_json).get("failure_code") == "primary_timeout"
                   for a in projection.model_result_audits)
        assert fact.fact_calls == 1
    finally:
        await _cleanup(host, author, fact, task, reply)


@pytest.mark.asyncio
async def test_new_observation_can_supersede_while_fact_is_waiting(tmp_path):
    host, _runtime_instance, _worker, author, fact, _clock, task, old_reply = await _pair(tmp_path)
    newest = None
    try:
        fact.release.set()
        await asyncio.sleep(0.1)
        newest = asyncio.create_task(host.inbound_text(
            message_id="newest", recipient_id="10001", text="我还在接着讲",
            observed_at=NOW + timedelta(seconds=2)))
        new_result = await asyncio.wait_for(newest, 5)
        old_result = await asyncio.wait_for(old_reply, 5)
        assert new_result.status == "action_authorized"
        assert old_result.status != "action_authorized"
        result = await asyncio.wait_for(task, 5)
        assert result.work_status == "accepted"
        projection = await host._host.action_due_projection()
        assert len(projection.facts) == 1
        assert fact.fact_calls == 1
    finally:
        await _cleanup(host, author, fact, task, old_reply, newest)


@pytest.mark.asyncio
async def test_slow_fact_batch_preparation_does_not_hold_visible_gate(tmp_path, monkeypatch):
    started, release = threading.Event(), threading.Event()
    original = FactV2AcceptanceRuntime.prepare_batch

    def slow_prepare(self, **kwargs):
        started.set()
        assert release.wait(5)
        return original(self, **kwargs)

    monkeypatch.setattr(FactV2AcceptanceRuntime, "prepare_batch", slow_prepare)
    host, _runtime_instance, _worker, author, fact, _clock, task, reply = await _pair(tmp_path)
    newest = None
    try:
        fact.release.set()
        author.second_release.set()
        assert (await asyncio.wait_for(reply, 5)).status == "action_authorized"
        assert await asyncio.to_thread(started.wait, 5)
        newest = asyncio.create_task(host.inbound_text(
            message_id="during-fact-prepare", recipient_id="10001", text="我继续说",
            observed_at=NOW + timedelta(seconds=2)))
        assert (await asyncio.wait_for(newest, 5)).status == "action_authorized"
        assert not task.done(), "slow preparation is still blocked in its own worker"
        release.set()
        with pytest.raises(ConcurrencyConflict):
            await asyncio.wait_for(task, 5)
        projection = await host._host.action_due_projection()
        assert len(projection.interaction_fact_decisions) == 1
        assert fact.fact_calls == 1
        assert not projection.facts
    finally:
        release.set()
        await _cleanup(host, author, fact, task, reply, newest)
