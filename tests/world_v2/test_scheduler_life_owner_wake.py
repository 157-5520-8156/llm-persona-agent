from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.qq_c2c_host import QQC2CHost
from companion_daemon.world_v2.qq_ingress_policy import SQLiteQQIngressStore


NOW = datetime(2026, 9, 8, 2, tzinfo=UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize("same_time_action", [False, True])
async def test_scheduler_drains_registered_life_owner_at_activity_boundary(
    tmp_path, same_time_action
):
    due = NOW + timedelta(minutes=3)

    class Platform:
        def __init__(self):
            self.logical_time = NOW + timedelta(minutes=1)
            self.ticks = []
            self.life_wakes = []

        async def current_logical_time(self):
            return self.logical_time

        async def life_ecology_next_due(self):
            return NOW + timedelta(hours=8)

        async def action_due_projection(self):
            plan = SimpleNamespace(
                status="active",
                scheduled_window=SimpleNamespace(opens_at=NOW, closes_at=due),
            )
            action = SimpleNamespace(state="scheduled", not_before=due)
            return SimpleNamespace(
                logical_time=self.logical_time,
                plans=(plan,),
                actions=(action,) if same_time_action else (),
            )

        async def tick(self, tick):
            self.ticks.append(tick)
            self.logical_time = tick.logical_time_to
            return SimpleNamespace(status="observed_only")

        async def advance_life_ecology_once(self, **kwargs):
            self.life_wakes.append((self.logical_time, kwargs["wake_event_ref"]))
            return SimpleNamespace(status="completed")

        async def drain_background_once(self):
            return None

        async def drain_scheduled_work(self, **kwargs):
            return SimpleNamespace(action_statuses=(), background_statuses=())

        def close(self):
            pass

    platform = Platform()
    host = QQC2CHost(
        host=platform,
        recipient_id="fixture",
        canonical_user_id="fixture",
        ingress_store=SQLiteQQIngressStore(tmp_path / "ingress.sqlite"),
        ingress_now=lambda: NOW,
    )
    try:
        await host.scheduler_once(observed_at=due)
        # Repeating the same pass cannot manufacture another clock or owner wake.
        await host.scheduler_once(observed_at=due)
    finally:
        await host.aclose()

    assert [tick.logical_time_to for tick in platform.ticks] == [due]
    assert platform.life_wakes == [
        (due, "event:trigger:clock:" + platform.ticks[0].tick_id)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("restart", [False, True])
async def test_activity_clock_wake_survives_visible_turn_preemption(tmp_path, restart):
    due = NOW + timedelta(minutes=3)

    class Platform:
        def __init__(self):
            self.logical_time = NOW + timedelta(minutes=1)
            self.ticks = []
            self.life_wakes = []
            self.visible = False
            self.finished = False

        async def current_logical_time(self):
            return self.logical_time

        async def life_ecology_next_due(self):
            return NOW + timedelta(hours=8)

        async def action_due_projection(self):
            return SimpleNamespace(
                logical_time=self.logical_time,
                plans=(SimpleNamespace(
                    status="completed" if self.finished else "active",
                    scheduled_window=SimpleNamespace(opens_at=NOW, closes_at=due),
                ),),
                clock_transition_history=tuple(SimpleNamespace(
                    logical_time_from=tick.logical_time_from,
                    logical_time_to=tick.logical_time_to,
                    clock_event_ref="event:trigger:clock:" + tick.tick_id,
                ) for tick in self.ticks),
            )

        async def tick(self, tick):
            self.ticks.append(tick)
            self.logical_time = tick.logical_time_to
            # A visible request arrives after the clock commit, before Life.
            self.visible = True
            return SimpleNamespace(status="observed_only")

        async def advance_life_ecology_once(self, **kwargs):
            self.life_wakes.append(kwargs["wake_event_ref"])
            self.finished = True
            return SimpleNamespace(status="completed")

        async def drain_background_once(self):
            return None

        async def drain_scheduled_work(self, **kwargs):
            return SimpleNamespace(action_statuses=(), background_statuses=())

        def close(self):
            pass

    platform = Platform()

    def build():
        host = QQC2CHost(
            host=platform,
            recipient_id="fixture",
            canonical_user_id="fixture",
            ingress_store=SQLiteQQIngressStore(tmp_path / "preempt.sqlite"),
            ingress_now=lambda: NOW,
        )
        host._visible_turn_in_flight = lambda: platform.visible
        return host

    host = build()
    try:
        await host.scheduler_once(observed_at=due)
        assert not platform.life_wakes
        if restart:
            await host.aclose()
            host = build()
        platform.visible = False
        await host.scheduler_once(observed_at=due)
        await host.scheduler_once(observed_at=due)
    finally:
        await host.aclose()

    assert len(platform.ticks) == 1
    assert platform.life_wakes == [
        "event:trigger:clock:" + platform.ticks[0].tick_id
    ]
