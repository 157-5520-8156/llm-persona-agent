"""Routine context may describe possibilities, never prove present activity."""

from __future__ import annotations

from datetime import datetime

from companion_daemon.world_v2.character_interior.snapshot_compiler import (
    compile_inner_life_snapshot,
)
from companion_daemon.world_v2.day_skeleton import (
    DaySlot,
    WeeklyTheme,
    WorldDaySkeleton,
    compile_day_sheet,
)


def test_a_routine_window_cannot_become_a_lived_moment() -> None:
    snapshot = compile_inner_life_snapshot(
        {
            "world_id": "world:routine-authority",
            "actor_ref": "agent:companion",
            "world_revision": 0,
            "deliberation_revision": 0,
            "ledger_sequence": 0,
            "logical_time": "2026-09-07T11:00:00+08:00",
            "slices": {},
        }
    )

    materials = snapshot.model_view()["materials"]

    assert "图书馆看书" in materials["day_sheet"]
    assert "lived_moment" not in materials


def test_day_sheet_labels_seed_windows_as_routine_background() -> None:
    sheet = compile_day_sheet(
        logical_at=datetime.fromisoformat("2026-09-07T11:00:00+08:00"),
        skeleton=WorldDaySkeleton(
            slots=(DaySlot("study", "看书", 9, 12, "图书馆"),),
            themes=(WeeklyTheme("photo", "整理照片", (0,), 18, 1, "宿舍"),),
        ),
    )

    assert "今天是2026-09-07，当地11:00" in sheet
    assert "惯常作息（背景，不代表今天的计划或实际活动）" in sheet
    assert "09:00–12:00 看书 @ 图书馆" in sheet
    assert "惯常周安排（背景，不代表已采纳的计划）" in sheet
    assert "18:00 整理照片 @ 宿舍" in sheet
    assert "（现在）" not in sheet
    assert "此刻窗口" not in sheet


def test_clock_and_biography_do_not_supply_weather_evidence() -> None:
    sheet = compile_day_sheet(
        logical_at=datetime.fromisoformat("2026-09-07T11:00:00+08:00"),
        skeleton=WorldDaySkeleton(slots=(), themes=()),
        academic_phase="term",
        academic_year=3,
        age=21,
        season="autumn",
    )

    assert sheet == "今天是2026-09-07，当地11:00。21岁，第3学年term，autumn。"
