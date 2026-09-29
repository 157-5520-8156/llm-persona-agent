"""Large time jumps must still give an accepted future Plan its start window."""
from datetime import datetime, timedelta, UTC
import importlib.util
from pathlib import Path
import sys

import pytest

from companion_daemon.world_v2.declared_due import DeclaredDueTarget, SchedulerWakeSnapshot


@pytest.mark.asyncio
async def test_acceleration_repeeks_new_dues_and_cannot_silently_hit_step_bound():
    scripts = Path(__file__).parents[1] / "scripts"
    sys.path.insert(0, str(scripts))
    try:
        spec = importlib.util.spec_from_file_location("runtime_due_driver", scripts / "drive_production_lanes.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(scripts))
    start = datetime(2026, 9, 27, tzinfo=UTC)

    class FakeSession:
        advance_with_runtime_dues = module.DriveSession.advance_with_runtime_dues

        def __init__(self):
            self.clock = start
            self.host = self
            self.visited = []
            self.life_requests = []

        async def logical_time(self):
            return self.clock

        async def scheduler_wake_snapshot(self):
            # Starting at +1h causes a new completion boundary at +2h.
            hour = 1 if self.clock == start else 2
            return SchedulerWakeSnapshot(self.clock, (
                DeclaredDueTarget("life.activity_occurrence", start + timedelta(hours=hour), "activity"),
                DeclaredDueTarget("action.authorized_due", start + timedelta(hours=1, minutes=30), "action"),
            ))

        async def tick_to(self, at, *, reason, run_life=True):
            self.clock = at
            self.visited.append(at)
            self.life_requests.append(run_life)
            return {"logical_time": at.isoformat()}

        async def drain_loop(self, **kwargs):
            return []

    session = FakeSession()
    steps = await session.advance_with_runtime_dues(start + timedelta(hours=5), reason="test")
    assert session.visited == [start + timedelta(hours=h) for h in (1, 1.5, 2, 5)]
    assert session.life_requests == [True, False, True, True]
    assert len(steps) == 4
    session = FakeSession()
    await session.advance_with_runtime_dues(start + timedelta(hours=5), reason="chat", run_life_at_target=False)
    assert session.life_requests == [True, False, True, False]
    session = FakeSession()
    with pytest.raises(RuntimeError, match="step bound"):
        await session.advance_with_runtime_dues(start + timedelta(hours=5), reason="test", max_steps=1)
    assert session.clock == start + timedelta(hours=1)
