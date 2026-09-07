"""Reviewed routine background at one civil day, not evidence of activity.

The schedule table describes habitual windows. Character-owned Plans and
accepted World Events determine what she chooses and what actually occurs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

_DEFAULT_SEED = Path(__file__).resolve().parents[3] / "configs" / "world_seed.yaml"
_DEFAULT_TIMEZONE = "Asia/Shanghai"


@dataclass(frozen=True, slots=True)
class DaySlot:
    slot: str
    title: str
    starts_hour: int
    ends_hour: int
    location: str = ""
    kind: str = ""


@dataclass(frozen=True, slots=True)
class WeeklyTheme:
    theme_id: str
    title: str
    weekdays: tuple[int, ...]
    starts_hour: int
    duration_hours: int
    location: str = ""


@dataclass(frozen=True, slots=True)
class WorldDaySkeleton:
    slots: tuple[DaySlot, ...]
    themes: tuple[WeeklyTheme, ...]


def load_world_day_skeleton(path: Path | None = None) -> WorldDaySkeleton:
    seed_path = path or _DEFAULT_SEED
    raw = yaml.safe_load(seed_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return WorldDaySkeleton(slots=(), themes=())
    slots: list[DaySlot] = []
    for item in raw.get("daily_schedule") or ():
        if not isinstance(item, dict):
            continue
        slots.append(
            DaySlot(
                slot=str(item.get("slot") or ""),
                title=str(item.get("title") or ""),
                starts_hour=int(item.get("starts_hour") or 0),
                ends_hour=int(item.get("ends_hour") or 0),
                location=str(item.get("location") or ""),
                kind=str(item.get("kind") or ""),
            )
        )
    themes: list[WeeklyTheme] = []
    for item in raw.get("weekly_themes") or ():
        if not isinstance(item, dict):
            continue
        weekdays = item.get("weekdays") or ()
        if not isinstance(weekdays, (list, tuple)):
            continue
        themes.append(
            WeeklyTheme(
                theme_id=str(item.get("id") or ""),
                title=str(item.get("title") or ""),
                weekdays=tuple(int(value) for value in weekdays),
                starts_hour=int(item.get("starts_hour") or 0),
                duration_hours=int(item.get("duration_hours") or 1),
                location=str(item.get("location") or ""),
            )
        )
    return WorldDaySkeleton(slots=tuple(slots), themes=tuple(themes))


def compile_day_sheet(
    *,
    logical_at: datetime,
    skeleton: WorldDaySkeleton,
    academic_phase: str | None = None,
    academic_year: int | None = None,
    age: int | None = None,
    season: str | None = None,
    timezone_name: str = _DEFAULT_TIMEZONE,
) -> str:
    """Render clock, biography, and habitual windows without claiming activity."""

    local = logical_at.astimezone(ZoneInfo(timezone_name))
    parts: list[str] = [f"今天是{local.strftime('%Y-%m-%d')}，当地{local.strftime('%H:%M')}"]
    identity: list[str] = []
    if age is not None:
        identity.append(f"{age}岁")
    if academic_phase:
        year = f"第{academic_year}学年" if academic_year is not None else ""
        identity.append(f"{year}{academic_phase}".strip())
    if season:
        identity.append(season)
    if identity:
        parts.append("，".join(identity))
    if skeleton.slots:
        lines = []
        for slot in skeleton.slots:
            place = f" @ {slot.location}" if slot.location else ""
            lines.append(
                f"{slot.starts_hour:02d}:00–{slot.ends_hour:02d}:00 {slot.title}{place}"
            )
        parts.append("惯常作息（背景，不代表今天的计划或实际活动）：" + "；".join(lines))
    weekday = local.weekday()
    due_themes = [theme for theme in skeleton.themes if weekday in theme.weekdays]
    if due_themes:
        parts.append(
            "惯常周安排（背景，不代表已采纳的计划）："
            + "；".join(
                f"{theme.starts_hour:02d}:00 {theme.title}"
                + (f" @ {theme.location}" if theme.location else "")
                for theme in due_themes
            )
        )
    return "。".join(parts) + "。"
