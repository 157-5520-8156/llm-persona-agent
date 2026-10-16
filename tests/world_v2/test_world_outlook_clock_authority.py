"""A clock's event revision is not the tail revision of its atomic batch."""

from datetime import timedelta

import pytest

from companion_daemon.world_v2.schemas import WorldEvent, BudgetAccount
from companion_daemon.world_v2.event_identity import domain_idempotency_key
from test_world_outlook import setup, draft_value, install_executor
from test_world_outlook_continuity import npc_setup, npc_manifest, activity
from test_life_development_runtime import _manifest, _projection_cursor


def _batched_clock(ledger):
    state = ledger.project()
    now = state.logical_time + timedelta(minutes=5)
    common = dict(
        schema_version="world-v2.1",
        world_id=ledger.world_id,
        logical_time=now,
        created_at=now,
        actor="system:offline-clock",
        source="test",
        trace_id="batch",
        causation_id="batch",
        correlation_id="batch",
    )
    clock = WorldEvent.from_payload(
        **common,
        event_id="clock:batch",
        event_type="ClockAdvanced",
        idempotency_key="clock:batch",
        payload={
            "logical_time_from": state.logical_time.isoformat(),
            "logical_time_to": now.isoformat(),
            "reason": "test",
        },
    )
    payload = {
        "account": BudgetAccount(
            account_id="account:zero-batch-fixture",
            category="tool",
            window_id="window:fixture",
            limit=0,
        ).model_dump(mode="json")
    }
    extra = WorldEvent.from_payload(
        **common,
        event_id="operator:batch",
        event_type="BudgetAccountConfigured",
        idempotency_key=domain_idempotency_key(
            event_type="BudgetAccountConfigured", world_id=ledger.world_id, payload=payload
        )
        or "budget:batch",
        payload=payload,
    )
    result = ledger.commit_at_cursor((clock, extra), expected_cursor=_projection_cursor(ledger))
    actual = next(
        r for r in ledger.project().committed_world_event_refs if r.event_id == clock.event_id
    )
    assert actual.world_revision < result.world_revision
    return clock


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", ["event", "npc", "cleanup"])
async def test_outlook_uses_clock_event_revision_from_multi_event_commit(
    tmp_path, monkeypatch, lane
):
    if lane == "npc":
        ledger, wake, author, runtime, _, _ = npc_setup(
            tmp_path, lambda d: activity(d["opportunities"][0])
        )
        manifest = npc_manifest(ledger, wake)
    else:
        ledger, wake, _, author, runtime = setup(
            tmp_path, lambda w: [{"decision": "revise", "draft": draft_value(w)}]
        )
        install_executor(ledger, runtime)
        manifest = _manifest(wake, pinned_cursor=_projection_cursor(ledger))
    try:
        assert (await runtime.refresh(projection=ledger.project(), context={}, manifest=manifest))[
            "status"
        ] == "ready"
        node = runtime.ready()[0]
        if lane == "cleanup":

            class Crash(BaseException):
                pass

            original = ledger.commit_at_cursor

            def stop_activation(events, **kwargs):
                if events[0].event_type == "WorldOccurrenceActivated":
                    raise Crash()
                return original(events, **kwargs)

            monkeypatch.setattr(ledger, "commit_at_cursor", stop_activation)
            with pytest.raises(Crash):
                runtime.execute_offer(node, wake_event_ref=wake.event_id)
            monkeypatch.setattr(ledger, "commit_at_cursor", original)
        clock = _batched_clock(ledger)
        if lane == "cleanup":
            runtime._executor.cancel_pending(node=node, wake_event_ref=clock.event_id)
            assert ledger.project().world_occurrences[0].status == "cancelled"
        else:
            result = runtime.execute_offer(node, wake_event_ref=clock.event_id)
            assert result.status == (
                "npc_activity_started" if lane == "npc" else "occurrence_committed"
            )
        assert len(author.calls) == 1
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_retried_old_ecology_wake_does_not_release_prepaid_detail_with_stale_clock(tmp_path, monkeypatch):
    from test_world_outlook import production_application, ProductionAuthor
    app, _, now = production_application(tmp_path, ProductionAuthor())
    try:
        for ordinal in (1, 2):
            await app.tick(tick_id=f'late-prepaid-{ordinal}', logical_time_from=now+timedelta(minutes=ordinal-1),
                logical_time_to=now+timedelta(minutes=ordinal), observed_at=now+timedelta(minutes=ordinal),
                trace_id='prepaid',causation_id='clock',correlation_id='prepaid',reason='test',run_life_ecology=False)
        ecology=app._life_ecology
        monkeypatch.setattr(ecology,'_background_budget_paused',lambda:True)
        calls=[]
        original=ecology._world_outlook.event_details.advance
        def advance(**kwargs):
            calls.append(kwargs['wake_event_ref'])
            return original(**kwargs)
        monkeypatch.setattr(ecology._world_outlook.event_details,'advance',advance)
        await app.advance_life_ecology_once(wake_event_ref='event:trigger:clock:late-prepaid-1',trace_id='old',correlation_id='old')
        assert calls==[], 'old retry must not borrow current time or poison the prepaid runtime with a stale clock'
        await app.advance_life_ecology_once(wake_event_ref='event:trigger:clock:late-prepaid-2',trace_id='new',correlation_id='new')
        assert calls==['event:trigger:clock:late-prepaid-2']
        assert app.world_outlook_health()['status']!='technical_failure'
    finally:
        await app.aclose()
