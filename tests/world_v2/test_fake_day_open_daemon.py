"""The daemon's real offline provider must support its installed day opportunity."""

from datetime import timedelta
import json
import sqlite3

import pytest

from companion_daemon.llm import FakeCompanionModel
from test_day_open_self_directed_intent import build_app  # noqa: F401
from test_world_stimulus_life_intent import NOW


@pytest.mark.asyncio
async def test_daemon_fake_handles_actual_empty_day_open_without_degrading(tmp_path, build_app):
    model = FakeCompanionModel()
    model.model = "offline-daemon-fixture"
    path = tmp_path / "fake-day.sqlite"
    app = build_app(path, model, ecology=True)
    try:
        at = NOW + timedelta(minutes=1)
        await app.tick(
            tick_id="fake-day",
            logical_time_from=NOW,
            logical_time_to=at,
            observed_at=at,
            trace_id="trace:fake-day",
            causation_id="clock:fake-day",
            correlation_id="fake-day",
            reason="test_clock",
        )
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            journal = json.loads(
                connection.execute("SELECT body FROM world_v2_day_open_opportunities").fetchone()[0]
            )
        assert journal["terminal_reason"] == "role_no_op", journal
        assert len(model.calls) == 1
        assert app.export_replay_evidence().projection.plans == ()
    finally:
        app.close()
