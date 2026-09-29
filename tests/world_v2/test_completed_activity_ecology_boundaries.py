"""Completion opportunities retain failure backoff and never substitute old work."""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2 import completed_activity_consequence
from companion_daemon.world_v2.life_development_runtime import LifeDevelopmentRuntime
from companion_daemon.world_v2.life_ecology_runtime import (
    LifeEcologyAvailability,
    LifeEcologyRuntime,
)
from companion_daemon.world_v2.schemas import LifeEcologyScheduleProjection
from test_life_ecology_runtime import (
    NOW, _Activity, _event, _Ledger, _LifeDevelopment, _Media, _TriggerStore,
)


@pytest.mark.asyncio
async def test_new_completion_failure_overrides_activity_success_for_retry_outcome():
    event = _event("clock-new-completion-failure")
    ledger = _Ledger(event)
    initial_revision = ledger.project().world_revision
    ledger._projection.life_ecology_schedule = LifeEcologyScheduleProjection(
        last_trigger_id="trigger:previous",
        last_wake_event_ref="event:previous-clock",
        last_outcome_ref="life-ecology:life_development_no_op",
        last_completed_at=NOW - timedelta(minutes=1),
        next_consideration_at=NOW + timedelta(hours=3),
        consecutive_failures=0,
    )
    ledger._projection.plans = (SimpleNamespace(
        status="active",
        scheduled_window=SimpleNamespace(opens_at=NOW, closes_at=NOW),
    ),)
    ledger._projection.world_occurrences = ()
    ledger._projection.experiences = ()
    ledger._projection.pending_biographical_settlements = ()
    completion_ref = "event:new-completion"

    class CompletingActivity(_Activity):
        async def advance_once(self, **kwargs):
            result = await super().advance_once(**kwargs)
            # A new immutable head lets the scheduler distinguish this
            # completion from activity work that existed before the wake.
            ledger._projection = SimpleNamespace(**{
                **vars(ledger.project()), "world_revision": initial_revision + 1,
            })
            return result

    class CompletionDevelopment(_LifeDevelopment):
        def __init__(self):
            super().__init__(
                "technical_failure", reason_code="life_development.world_author_unavailable",
            )
            self.selection_calls = []
            self.completion_calls = []

        def pending_completed_activity_ref(self, *, after_world_revision):
            self.selection_calls.append(after_world_revision)
            assert ledger.project().world_revision > after_world_revision
            return completion_ref

        async def advance_completed_activity_once(self, **kwargs):
            self.completion_calls.append(kwargs)
            return SimpleNamespace(status=self.status, reason_code=self.reason_code)

    activity = CompletingActivity(status="transitioned")
    development = CompletionDevelopment()
    trigger_store = _TriggerStore()
    runtime = LifeEcologyRuntime(
        ledger=ledger, trigger_store=trigger_store, media_followup=_Media(),
        activity_followup=activity, life_development_followup=development,
        availability=LifeEcologyAvailability(state="installed_and_active"),
    )
    result = await runtime.advance_once(
        wake_event_ref=event.event_id, trace_id="trace:completion-failure",
        correlation_id="correlation:completion-failure",
    )
    assert len(activity.calls) == 1
    assert development.selection_calls == [initial_revision]
    assert development.calls == []
    assert development.completion_calls == [{
        "completion_event_ref": completion_ref, "wake_event_ref": event.event_id,
        "trace_id": "trace:completion-failure",
        "correlation_id": "correlation:completion-failure",
    }]
    assert result.status == "deferred"
    assert result.activity_followup_status == "transitioned"
    assert result.life_development_followup_status == "technical_failure"
    assert result.technical_failure_code == "life_development.world_author_unavailable"
    assert trigger_store.completed[0][2] == (
        "technical_failure.consequence.life_development.world_author_unavailable"
    )


def test_unreadable_latest_completion_does_not_revive_older_activity(monkeypatch):
    def plan(name, revision):
        return SimpleNamespace(
            plan_id="plan:" + name, owner_actor_ref="actor:companion", status="completed",
            authority_origin=SimpleNamespace(
                accepted_world_revision=revision, accepted_event_ref="event:" + name,
            ),
        )

    state = SimpleNamespace(plans=(plan("older", 7), plan("latest", 11)))
    runtime = object.__new__(LifeDevelopmentRuntime)
    runtime._owner = "actor:companion"
    runtime._ledger = SimpleNamespace(
        world_id="world:completion-selection", project=lambda: state,
        lookup_event_commit=lambda ref: None,
    )
    reads = []

    def read(**kwargs):
        reads.append(kwargs["completion_event_ref"])
        assert kwargs["pinned_state"] is state
        return None if kwargs["completion_event_ref"] == "event:latest" else SimpleNamespace()

    monkeypatch.setattr(completed_activity_consequence, "read_completed_activity_consequence", read)
    assert runtime.pending_completed_activity_ref() is None
    assert reads == ["event:latest"]
