from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from companion_daemon.world_v2.daily_occasion import (
    InMemoryDailyOccasionStore,
    local_day_key,
)
from companion_daemon.world_v2.day_skeleton import (
    DaySlot,
    WorldDaySkeleton,
    compile_day_sheet,
    load_world_day_skeleton,
)


NOW = datetime(2026, 8, 13, 3, 0, tzinfo=UTC)  # 11:00 Asia/Shanghai


def test_world_seed_daily_schedule_compiles_without_a_model() -> None:
    skeleton = load_world_day_skeleton(
        Path(__file__).resolve().parents[2] / "configs" / "world_seed.yaml"
    )
    assert skeleton.slots
    sheet = compile_day_sheet(
        logical_at=NOW,
        skeleton=skeleton,
        academic_phase="term",
        academic_year=3,
        age=21,
        season="summer",
    )
    assert "惯常作息" in sheet
    assert "（现在）" not in sheet
    assert "图书馆看书" in sheet or "整理" in sheet
    assert "天气" not in sheet


def test_day_sheet_preserves_routine_windows_without_claiming_attendance() -> None:
    skeleton = WorldDaySkeleton(
        slots=(
            DaySlot("sleep", "睡觉", 0, 8, "宿舍", "rest"),
            DaySlot("study", "看书", 9, 12, "图书馆"),
        ),
        themes=(),
    )
    sheet = compile_day_sheet(logical_at=NOW, skeleton=skeleton)
    assert "00:00–08:00 睡觉 @ 宿舍" in sheet
    assert "09:00–12:00 看书 @ 图书馆" in sheet
    assert "不代表今天的计划或实际活动" in sheet


def test_day_open_is_spent_once_per_local_day() -> None:
    store = InMemoryDailyOccasionStore()
    day_key = local_day_key(NOW)
    assert store.spent("day_open", day_key) is False
    store.mark("day_open", day_key)
    assert store.spent("day_open", day_key) is True
    assert store.spent("day_open", "2026-08-14") is False
