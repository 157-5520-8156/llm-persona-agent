from datetime import UTC, datetime, timedelta
import asyncio
import json
from pathlib import Path

import pytest

from companion_daemon.world_v2.longitudinal_journey import (
    CaptureDelivery,
    Journey,
    JourneyClock,
    JourneyLimits,
)


NOW = datetime(2026, 9, 7, tzinfo=UTC)


@pytest.mark.asyncio
async def test_scheduler_timers_cannot_advance_past_future_user_input():
    clock = JourneyClock(NOW)
    task = asyncio.create_task(clock.timer_sleep(3600))
    await asyncio.sleep(0)
    assert clock.now() == NOW
    assert clock.next_timer() == NOW + timedelta(hours=1)
    clock.advance(NOW + timedelta(minutes=10))
    await asyncio.sleep(0)
    assert not task.done()
    clock.advance(NOW + timedelta(hours=1))
    await task
    assert clock.next_timer() is None


@pytest.mark.asyncio
async def test_restart_cancellation_removes_timer_and_preserves_clock():
    clock = JourneyClock(NOW)
    task = asyncio.create_task(clock.timer_sleep(60))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert clock.next_timer() is None
    assert clock.now() == NOW


@pytest.mark.asyncio
async def test_presentation_pacing_cannot_fire_action_timers_or_move_calendar():
    clock = JourneyClock(NOW)
    task = asyncio.create_task(clock.timer_sleep(10))
    await asyncio.sleep(0)
    await clock.presentation_sleep(30)
    assert clock.presentation_now() == NOW + timedelta(seconds=30)
    assert clock.now() == NOW
    assert not task.done()
    clock.advance(NOW + timedelta(seconds=10))
    await task


@pytest.mark.asyncio
async def test_capture_typing_is_not_visible_character_prose():
    delivery = CaptureDelivery(JourneyClock(NOW))
    await delivery.send_typing("fixture", state="start")
    await delivery.send_text("fixture", "这周过得有点快。")
    assert [row["kind"] for row in delivery.records] == ["typing", "text"]
    assert len({row["message_id"] for row in delivery.records}) == 2
    confirmation = await delivery.get_message(
        "fixture", message_id=delivery.records[1]["message_id"]
    )
    assert confirmation["data"]["message"] == "这周过得有点快。"
    assert (await delivery.get_message("fixture", message_id="never-sent"))["status"] == "failed"


@pytest.mark.asyncio
async def test_capture_wall_latency_does_not_follow_simulated_life_time(monkeypatch):
    wall = iter((100.0, 100.5, 103.0, 104.0))
    with monkeypatch.context() as scoped:
        scoped.setattr(
            "companion_daemon.world_v2.longitudinal_journey.time.monotonic", lambda: next(wall)
        )
        clock = JourneyClock(NOW)
        delivery = CaptureDelivery(clock)
        assert delivery.wall_elapsed_seconds() == 0.5
        clock.advance(NOW + timedelta(days=7))
        typing = await delivery.send_typing("fixture", state="start")
        text = await delivery.send_text("fixture", "你画好了吗？")
        assert [row["wall_elapsed_seconds"] for row in delivery.records] == [3.0, 4.0]
        assert all(row["virtual_at"] == clock.now().isoformat() for row in delivery.records)
        # Non-deterministic instrumentation must not alter transport receipts.
        assert typing == {"status": "ok", "data": {"message_id": "journey-message-1"}}
        assert text == {"status": "ok", "data": {"message_id": "journey-message-2"}}


def test_frozen_week_has_inputs_correction_silence_and_restart_without_character_script():
    path = Path(__file__).parents[2] / "fixtures/world_v2/longitudinal_week.json"
    data = json.loads(path.read_text())
    journey = Journey.parse(data)
    assert journey.duration_minutes == 7 * 1440
    assert len(journey.restart_minutes) == 2
    assert all(set(turn) == {"id", "text", "at_minutes"} for turn in journey.turns)


@pytest.mark.parametrize(
    "change",
    [
        {"started_at": "2026-09-07T08:00:00"},
        {"duration_minutes": True},
        {"restart_minutes": [30, 20]},
        {"turns": [{"id": "a", "at_minutes": 0, "text": "早", "must_reply": True}]},
        {
            "turns": [
                {"id": "a", "at_minutes": 0, "text": "早"},
                {"id": "a", "at_minutes": 1, "text": "又来了"},
            ]
        },
    ],
)
def test_scenario_rejects_ambiguous_time_and_role_script(change):
    document = {
        "scenario_id": "test",
        "started_at": NOW.isoformat(),
        "duration_minutes": 60,
        **change,
    }
    with pytest.raises(ValueError):
        Journey.parse(document)


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), 901])
def test_no_unbounded_clock_jump(value):
    with pytest.raises(ValueError):
        JourneyLimits(heartbeat_seconds=value)
