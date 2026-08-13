"""Deterministic civil-day skeleton: environment, not her behavior."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

_WEATHER = (
    "偏热，空气有点闷",
    "清爽，风不大",
    "阴天，像要下雨",
    "雨意很淡，路面还是干的",
    "太阳很好，晒得人想躲一躲",
)


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
    """Readable today-sheet from reviewed seed + biography. Zero model calls."""

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
    weather_digest = hashlib.sha256(local.date().isoformat().encode("utf-8")).digest()
    parts.append("天气：" + _WEATHER[int.from_bytes(weather_digest[:8], "big") % len(_WEATHER)])
    if skeleton.slots:
        lines = []
        current = None
        for slot in skeleton.slots:
            marker = ""
            if slot.starts_hour <= local.hour < slot.ends_hour or (
                slot.starts_hour > slot.ends_hour
                and (local.hour >= slot.starts_hour or local.hour < slot.ends_hour)
            ):
                marker = "（现在）"
                current = slot.title
            place = f" @ {slot.location}" if slot.location else ""
            lines.append(
                f"{slot.starts_hour:02d}:00–{slot.ends_hour:02d}:00 {slot.title}{place}{marker}"
            )
        parts.append("今日作息：" + "；".join(lines))
        if current:
            parts.append(f"此刻窗口是{current}")
    weekday = local.weekday()
    due_themes = [theme for theme in skeleton.themes if weekday in theme.weekdays]
    if due_themes:
        parts.append(
            "本周今日主题："
            + "；".join(
                f"{theme.starts_hour:02d}:00 {theme.title}"
                + (f" @ {theme.location}" if theme.location else "")
                for theme in due_themes
            )
        )
    return "。".join(parts) + "。"
