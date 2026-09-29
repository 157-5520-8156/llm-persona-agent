"""One bounded development opportunity per wake, with completion priority."""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2 import active_attempt_consequence
from companion_daemon.world_v2.life_development_runtime import LifeDevelopmentRuntime
from companion_daemon.world_v2.life_ecology_runtime import LifeEcologyAvailability, LifeEcologyRuntime
from companion_daemon.world_v2.schemas import LifeEcologyScheduleProjection
from test_life_ecology_runtime import NOW, _Activity, _event, _Ledger, _LifeDevelopment, _Media, _TriggerStore


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["started", "resumed", "completed"])
async def test_new_transition_uses_one_focused_attempt_and_preserves_failure_backoff(phase):
    event = _event("new-" + phase)
    ledger = _Ledger(event)
    initial = ledger.project().world_revision
    ledger._projection.life_ecology_schedule = LifeEcologyScheduleProjection(
        last_trigger_id="trigger:previous", last_wake_event_ref="event:previous",
        last_outcome_ref="life-ecology:life_development_no_op",
        last_completed_at=NOW - timedelta(minutes=1), next_consideration_at=NOW + timedelta(hours=3),
        consecutive_failures=0,
    )
    ledger._projection.plans = (SimpleNamespace(
        status="active", scheduled_window=SimpleNamespace(opens_at=NOW, closes_at=NOW),
    ),)
    ledger._projection.world_occurrences = ()
    ledger._projection.experiences = ()
    ledger._projection.pending_biographical_settlements = ()

    class Transition(_Activity):
        async def advance_once(self, **kwargs):
            result = await super().advance_once(**kwargs)
            ledger._projection = SimpleNamespace(**{**vars(ledger.project()), "world_revision": initial + 1})
            return result

    class Development(_LifeDevelopment):
        def __init__(self):
            super().__init__("technical_failure", reason_code="life_development.world_author_unavailable")
            self.selections, self.focused = [], []

        def pending_completed_activity_ref(self, *, after_world_revision):
            assert after_world_revision == initial
            self.selections.append("completed")
            return "event:completed" if phase == "completed" else None

        def pending_active_attempt_ref(self, *, after_world_revision):
            assert after_world_revision == initial
            assert ledger.project().world_revision > initial
            self.selections.append("active")
            return "event:" + phase

        async def advance_active_attempt_once(self, **kwargs):
            self.focused.append(kwargs)
            return SimpleNamespace(status=self.status, reason_code=self.reason_code)

        async def advance_completed_activity_once(self, **kwargs):
            self.focused.append(kwargs)
            return SimpleNamespace(status=self.status, reason_code=self.reason_code)

    development = Development()
    trigger_store = _TriggerStore()
    runtime = LifeEcologyRuntime(
        ledger=ledger, trigger_store=trigger_store, media_followup=_Media(),
        activity_followup=Transition(status="transitioned"), life_development_followup=development,
        availability=LifeEcologyAvailability(state="installed_and_active"),
    )
    result = await runtime.advance_once(
        wake_event_ref=event.event_id, trace_id="trace:focused", correlation_id="focused",
    )
    assert development.selections == (["completed"] if phase == "completed" else ["completed", "active"])
    assert not development.calls and len(development.focused) == 1
    assert result.status == "deferred" and result.activity_followup_status == "transitioned"
    assert result.technical_failure_code == "life_development.world_author_unavailable"
    assert trigger_store.completed[0][2] == "technical_failure.consequence.life_development.world_author_unavailable"


def test_consumed_and_unreadable_active_heads_do_not_starve_another_current_plan(monkeypatch):
    from companion_daemon.world_v2.life_development_runtime import _digest

    def plan(name, revision):
        return SimpleNamespace(
            plan_id="plan:" + name, owner_actor_ref="actor:companion", status="active",
            authority_origin=SimpleNamespace(accepted_world_revision=revision, accepted_event_ref="event:" + name),
        )

    state = SimpleNamespace(
        plans=(plan("old-current", 4), plan("unreadable", 5), plan("consumed", 6)),
        committed_world_event_refs=(SimpleNamespace(event_id="clock", event_type="ClockAdvanced", world_revision=7),),
    )
    runtime = object.__new__(LifeDevelopmentRuntime)
    runtime._owner = "actor:companion"
    runtime._store = object()
    runtime._ledger = SimpleNamespace(world_id="world:active", project=lambda: state)
    consumed = "event:life-development:proposal:" + _digest(runtime._active_attempt_proposal_id("event:consumed"))
    runtime._ledger.lookup_event_commit = lambda ref: object() if ref == consumed else None
    reads = []

    def read(**kwargs):
        source = kwargs["execution_event_ref"]
        reads.append(source)
        return None if source == "event:unreadable" else SimpleNamespace()

    monkeypatch.setattr(active_attempt_consequence, "read_active_attempt_consequence", read)
    assert runtime.pending_active_attempt_ref() == "event:old-current"
    assert reads == ["event:consumed", "event:unreadable", "event:old-current"]
    assert runtime.pending_active_attempt_ref(after_world_revision=4) is None
