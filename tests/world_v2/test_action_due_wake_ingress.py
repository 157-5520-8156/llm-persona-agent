from datetime import UTC, datetime, timedelta
import asyncio
import json
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.action_due_wake import ActionDueWake
from companion_daemon.world_v2.action_pump import ActionPumpResult
from companion_daemon.world_v2.qq_c2c_host import QQC2CHost


@pytest.mark.asyncio
async def test_pending_ingress_suspends_due_timer_until_an_explicit_refresh():
    now = datetime(2026, 10, 26, 16, 18, tzinfo=UTC)
    action = SimpleNamespace(state="scheduled", not_before=now, claim_lease=None)
    pending = True
    attempts = 0

    async def wake():
        nonlocal attempts
        attempts += 1
        if pending:
            return ActionPumpResult(status="deferred_ingress")
        action.state = "delivered"
        return ActionPumpResult(status="settled")

    async def yield_once(_seconds):
        await asyncio.sleep(0)

    timer = ActionDueWake(project=lambda: SimpleNamespace(actions=(action,)), wake=wake,
                          now=lambda: now, sleep=yield_once, coalesce_seconds=0)
    try:
        await timer.refresh()
        for _ in range(12):
            await asyncio.sleep(0)
        assert attempts == 1
        assert timer.diagnostics()["failure_count"] == 0
        pending = False
        await timer.refresh()
        for _ in range(12):
            await asyncio.sleep(0)
            if action.state == "delivered":
                break
        assert attempts == 2
        assert action.state == "delivered"
    finally:
        await timer.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("disposition", ["continue", "supersede"])
async def test_host_refresh_after_role_decision_rearms_only_eligible_work(disposition):
    now = datetime(2026, 10, 26, 16, 18, tzinfo=UTC)
    due = now + timedelta(hours=1)
    lease = SimpleNamespace(expires_at=due + timedelta(hours=1))
    actions = [
        SimpleNamespace(action_id=state, state=state, expression_plan_id="plan:old",
                        expression_beat_id="beat:old", not_before=due, claim_lease=lease)
        for state in ("authorized", "scheduled", "claimed", "dispatch_started", "provider_accepted")
    ]
    gate = SimpleNamespace(
        process_kind="expression_reconsideration",
        trigger_ref="expression-reconsideration:" + json.dumps({"plan_id": "plan:old", "beat_id": "beat:old", "observation_id": "observation:new"}),
        state="open", runtime_outcome_ref=None,
    )

    class Platform:
        async def action_due_projection(self):
            return SimpleNamespace(actions=tuple(actions), trigger_processes=(gate,))

        async def drain_scheduled_work(self, **_kwargs):
            gate.state = "terminal"
            gate.runtime_outcome_ref = "expression-reconsideration-decision:" + json.dumps({"decision": {"disposition": disposition}})
            if disposition == "supersede":
                for a in actions:
                    if a.state in {"authorized", "scheduled", "claimed"}:
                        a.state = "cancelled"
                actions.append(SimpleNamespace(action_id="replacement", state="scheduled", expression_plan_id="plan:new",
                                               expression_beat_id="beat:new", not_before=due, claim_lease=None))
            return SimpleNamespace(action_statuses=(), background_statuses=(disposition,))

        def close(self):
            pass

    host = QQC2CHost(host=Platform(), recipient_id="10001", canonical_user_id="10001", action_due_now=lambda: now)
    try:
        before = await host._action_due_wake_projection()
        assert {a.state for a in before.actions} == {"dispatch_started", "provider_accepted"}
        # This is the normal host drain, whose completion must refresh the
        # timer after the explicit role decision or its replacement.
        await host.drain(max_action_units=0, max_background_units=1)
        after = await host._action_due_wake_projection()
        if disposition == "continue":
            assert {a.state for a in after.actions} == {"authorized", "scheduled", "claimed", "dispatch_started", "provider_accepted"}
        else:
            assert any(a.action_id == "replacement" for a in after.actions)
        assert ActionDueWake.nearest_due(after) == due
        assert host._action_due_wake._task is not None
    finally:
        await host.aclose()
