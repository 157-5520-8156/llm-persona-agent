"""Published external arrangements. Recurrence describes schedules, never attendance."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
import hashlib
import json
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from .schema_core import FrozenModel


class PlanningSlot(FrozenModel):
    slot_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    title: str = Field(min_length=1, max_length=100)
    detail: str = Field(min_length=1, max_length=400)
    weekdays: tuple[int, ...] = Field(min_length=1, max_length=7,
        description="Monday=0, Tuesday=1, Wednesday=2, Thursday=3, Friday=4, Saturday=5, Sunday=6. Titles and details must describe these same days.")
    start_minute: int = Field(ge=0, lt=1440, strict=True)
    duration_minutes: int = Field(ge=1, le=1440, strict=True)

    @model_validator(mode="after")
    def unique_weekdays(self):
        if len(set(self.weekdays)) != len(self.weekdays) or any(type(d) is not int or not 0 <= d <= 6 for d in self.weekdays):
            raise ValueError("planning weekdays must be distinct Monday=0..Sunday=6")
        return self


class PlanningException(FrozenModel):
    slot_id: str
    original_date: date
    action: Literal["cancel", "reschedule"]
    new_start: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=1, le=1440, strict=True)
    explanation: str = Field(min_length=1, max_length=400)

    @model_validator(mode="after")
    def replacement_is_explicit(self):
        if (self.new_start is None) != (self.duration_minutes is None) or self.action == "reschedule" and self.new_start is None:
            raise ValueError("rescheduling needs a complete window; cancellation may name its prior moved window")
        if self.new_start is not None and (self.new_start.tzinfo is None or self.new_start.utcoffset() is None):
            raise ValueError("planning exception needs timezone-aware time")
        return self


class WorldPlanningDraft(FrozenModel):
    contract: Literal["world-planning.1"] = "world-planning.1"
    planning_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,95}$")
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=1200)
    starts_on: date
    ends_on: date
    timezone_name: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    slots: tuple[PlanningSlot, ...] = Field(default=(), max_length=28)
    exceptions: tuple[PlanningException, ...] = Field(default=(), max_length=32)
    # A new publication supersedes an exact prior publication, never old history.
    supersedes_occurrence_ref: str | None = Field(default=None, min_length=1, max_length=512,
                                                exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def bounded_series(self):
        if not 0 <= (self.ends_on - self.starts_on).days <= 730:
            raise ValueError("a planning revision covers at most two years")
        slots = {s.slot_id: s for s in self.slots}
        if len(slots) != len(self.slots):
            raise ValueError("planning slot identities must be unique")
        exceptions = set()
        for item in self.exceptions:
            slot = slots.get(item.slot_id)
            key = (item.slot_id, item.original_date)
            if slot is None or key in exceptions or not self.starts_on <= item.original_date <= self.ends_on or item.original_date.weekday() not in slot.weekdays:
                raise ValueError("exception must identify one real original recurrence")
            if item.new_start is not None and not self.starts_on <= item.new_start.astimezone(ZoneInfo(self.timezone_name)).date() <= self.ends_on:
                raise ValueError("replacement must remain inside the planning period")
            exceptions.add(key)
        if len(self.model_dump_json()) > 5_500:
            raise ValueError("planning document exceeds its bounded publication size")
        return self

    def public_text(self) -> str:
        """Exact field presentation of a World publication, never character speech."""
        lines = [self.title, self.summary, f"规划期间：{self.starts_on} 至 {self.ends_on}（北京时间）。",
                 "这是客观安排，不证明角色参加、完成或同意了其中的活动。"]
        for slot in self.slots:
            days = "、".join("周" + "一二三四五六日"[day] for day in slot.weekdays)
            hour, minute = divmod(slot.start_minute, 60)
            lines.append(f"{days} {hour:02}:{minute:02}，持续 {slot.duration_minutes} 分钟：{slot.title}。{slot.detail}")
        for item in self.exceptions:
            title = next(s.title for s in self.slots if s.slot_id == item.slot_id)
            change = "取消" if item.action == "cancel" else f"改至 {item.new_start.astimezone(ZoneInfo(self.timezone_name)).isoformat()}，持续 {item.duration_minutes} 分钟"
            lines.append(f"{item.original_date} 的 {title}：{change}。{item.explanation}")
        return "\n".join(lines)


class PlanningInstance(FrozenModel):
    instance_ref: str
    title: str
    detail: str
    original_start: datetime
    starts_at: datetime
    ends_at: datetime
    status: Literal["scheduled", "cancelled"]


def expand_planning(planning: WorldPlanningDraft, *, world_id: str, starts_at: datetime,
                    ends_at: datetime, effective_from: datetime, effective_until: datetime | None = None,
                    limit: int = 96) -> tuple[PlanningInstance, ...]:
    """Expand only a requested window; moved items retain their original identity."""
    if any(t.tzinfo is None or t.utcoffset() is None for t in (starts_at, ends_at, effective_from)):
        raise ValueError("planning expansion needs aware bounds")
    if not 0 < (ends_at - starts_at).total_seconds() <= 32 * 86400 or not 1 <= limit <= 256:
        raise ValueError("planning expansion must be a bounded window")
    zone = ZoneInfo(planning.timezone_name)
    exceptions = {(e.slot_id, e.original_date): e for e in planning.exceptions}
    dates = set()
    day = max(planning.starts_on, starts_at.astimezone(zone).date() - timedelta(days=1))
    last = min(planning.ends_on, ends_at.astimezone(zone).date())
    while day <= last:
        dates.add(day)
        day += timedelta(days=1)
    # A moved occurrence may have its original identity outside the requested week.
    dates.update(e.original_date for e in planning.exceptions if e.new_start is not None
                 and starts_at - timedelta(days=1) <= e.new_start < ends_at)
    instances = []
    for day in sorted(dates):
        for slot in planning.slots:
            if day.weekday() not in slot.weekdays:
                continue
            original = datetime.combine(day, time(), zone) + timedelta(minutes=slot.start_minute)
            exception = exceptions.get((slot.slot_id, day))
            actual = exception.new_start if exception and exception.new_start is not None else original
            duration = exception.duration_minutes if exception and exception.duration_minutes is not None else slot.duration_minutes
            end = actual + timedelta(minutes=duration)
            if actual < effective_from or effective_until is not None and actual >= effective_until:
                continue  # Publishing/revising now cannot rewrite a previous occurrence.
            if actual >= ends_at or end <= starts_at:
                continue
            identity = json.dumps([world_id, planning.planning_key, slot.slot_id, original.isoformat()], separators=(",", ":"))
            instances.append(PlanningInstance(
                instance_ref="planning-instance:" + hashlib.sha256(identity.encode()).hexdigest(),
                title=slot.title, detail=slot.detail + ("\n" + exception.explanation if exception else ""),
                original_start=original, starts_at=actual, ends_at=end,
                status="cancelled" if exception and exception.action == "cancel" else "scheduled",
            ))
    return tuple(sorted(instances, key=lambda i: (i.starts_at, i.instance_ref))[:limit])


class WorldPlanningIndex(FrozenModel):
    """Bounded candidate-selection metadata, rechecked against immutable content."""
    planning_key: str
    starts_on: date
    ends_on: date
    supersedes_occurrence_ref: str | None = None


def planning_index(planning: WorldPlanningDraft | None) -> WorldPlanningIndex | None:
    if planning is None:
        return None
    return WorldPlanningIndex(**{key: getattr(planning, key) for key in WorldPlanningIndex.model_fields})


class PlanningPublication(FrozenModel):
    planning: WorldPlanningDraft
    occurrence_ref: str
    source_event_ref: str
    source_world_revision: int
    source_payload_hash: str
    privacy_class: str
    published_at: datetime
    effective_until: datetime | None = None


def planning_publications(*, projection, store, actor_ref, user_channel_limited_content_refs, privacy_ceiling="private",
                          starts_at=None, ends_at=None, limit=8):
    """Read current/week-relevant revisions, without scanning historical bodies."""
    from .life_content_reading import selected_world_consequence
    from .occurrence_result_content_runtime import require_published_world_consequence

    if not 1 <= limit <= 128:
        raise ValueError("planning publication read must be bounded")
    ranks = {"public": 0, "shareable": 1, "personal": 2, "private": 3, "withhold": 4}
    starts_at = starts_at or projection.logical_time
    ends_at = ends_at or starts_at + timedelta(days=7)
    grouped = {}
    for occurrence in projection.world_occurrences:
        if occurrence.status != "settled" or actor_ref not in occurrence.participant_refs:
            continue
        candidate = next((c for c in occurrence.candidate_outcomes if c.candidate_result_ref == occurrence.settled_outcome_ref), None)
        if candidate is None or candidate.result_contract != "world-consequence.3":
            continue
        index = candidate.world_planning_index
        grouped.setdefault(index.planning_key, []).append((occurrence, candidate))
    eligible = []
    zone = ZoneInfo("Asia/Shanghai")
    for revisions in grouped.values():
        revisions.sort(key=lambda pair: pair[0].settlement_world_revision)
        for position, (occurrence, candidate) in enumerate(revisions):
            if position and candidate.world_planning_index.supersedes_occurrence_ref != revisions[position - 1][0].occurrence_id:
                raise ValueError("published planning revisions do not form one lineage")
            until = revisions[position + 1][0].settled_at if position + 1 < len(revisions) else None
            index = candidate.world_planning_index
            if (occurrence.settled_at >= ends_at or until is not None and until <= starts_at
                    or index.ends_on < starts_at.astimezone(zone).date()
                    or index.starts_on > ends_at.astimezone(zone).date()):
                continue
            if any(ranks[p] > ranks[privacy_ceiling] or p == "withhold" for p in (occurrence.visibility, candidate.privacy_class)):
                continue
            eligible.append((occurrence, candidate, until))
    eligible.sort(key=lambda row: row[0].settlement_world_revision, reverse=True)
    # Source restrictions are supplied by the caller's ledger-backed compiler;
    # no raw candidate or unpublished document is a fallback.
    result = []
    limited = user_channel_limited_content_refs
    eligible = [row for row in eligible if row[1].content_ref not in limited and row[0].result_payload_ref not in limited]
    for occurrence, candidate, until in eligible[:limit]:
        require_published_world_consequence(projection=projection, occurrence=occurrence, content_store=store)
        value = selected_world_consequence(store=store, projection=projection, occurrence=occurrence,
            actor_ref=actor_ref, viewer_privacy_ceiling=privacy_ceiling, user_channel_limited_content_refs=limited)
        if value.world_planning is None or planning_index(value.world_planning) != candidate.world_planning_index:
            raise ValueError("planning metadata differs from its published source")
        result.append(PlanningPublication(planning=value.world_planning, occurrence_ref=occurrence.occurrence_id,
            source_event_ref=occurrence.settlement_event_ref, source_world_revision=occurrence.settlement_world_revision,
            source_payload_hash=occurrence.settlement_payload_hash, privacy_class=max((occurrence.visibility,candidate.privacy_class),key=ranks.__getitem__),
            published_at=occurrence.settled_at, effective_until=until))
    return tuple(result)


def validate_planning_publication(planning, *, projection, actor_ref, occurrence_ref, store):
    """A revision names the exact prior publication; concurrent branches are rejected."""
    heads = []
    for occurrence in projection.world_occurrences:
        if occurrence.occurrence_id == occurrence_ref or actor_ref not in occurrence.participant_refs or occurrence.status in {"cancelled", "expired"}:
            continue
        for candidate in occurrence.candidate_outcomes:
            if candidate.result_contract != "world-consequence.3" or candidate.world_planning_index.planning_key != planning.planning_key:
                continue
            if occurrence.status != "settled":
                raise ValueError("another publication of this planning is still pending")
            if candidate.candidate_result_ref == occurrence.settled_outcome_ref:
                heads.append(occurrence)
    head = max(heads, key=lambda item: item.settlement_world_revision, default=None)
    if planning.supersedes_occurrence_ref != (head.occurrence_id if head else None):
        raise ValueError(
            "planning revision must supersede its exact current publication: "
            f"planning_key={planning.planning_key!r} requires supersedes_occurrence_ref="
            f"{head.occurrence_id if head else 'null (first publication)'}. "
            "Ordinary environment notices are not planning predecessors."
        )
    if head is not None:
        from .life_content_reading import selected_world_consequence
        previous = selected_world_consequence(store=store, projection=projection, occurrence=head,
            actor_ref=actor_ref, viewer_privacy_ceiling="private").world_planning
        validate_planning_revision(previous, planning, logical_time=projection.logical_time)
    today = projection.logical_time.astimezone(ZoneInfo(planning.timezone_name)).date()
    if planning.ends_on < today or planning.ends_on > today + timedelta(days=730):
        raise ValueError("planning publication must cover a bounded current or future period")


def validate_planning_revision(previous, current, *, logical_time):
    """Compare effective instances, including removed exceptions and today's rule."""
    old_slots, new_slots = ({slot.slot_id: slot for slot in value.slots} for value in (previous, current))
    old_exceptions, new_exceptions = ({(e.slot_id, e.original_date): e for e in value.exceptions}
                                      for value in (previous, current))
    zone = ZoneInfo(previous.timezone_name)
    today = logical_time.astimezone(zone).date()
    keys = set(old_exceptions) | set(new_exceptions) | {(key, today) for key in old_slots.keys() & new_slots.keys()}

    def window(value, slots, exceptions, key):
        slot_id, day = key
        slot = slots.get(slot_id)
        if slot is None or not value.starts_on <= day <= value.ends_on or day.weekday() not in slot.weekdays:
            return None
        original = datetime.combine(day, time(), zone) + timedelta(minutes=slot.start_minute)
        exception = exceptions.get(key)
        return (exception.new_start if exception and exception.new_start is not None else original,
                exception.duration_minutes if exception and exception.duration_minutes else slot.duration_minutes)

    for key in keys:
        old = window(previous, old_slots, old_exceptions, key)
        new = window(current, new_slots, new_exceptions, key)
        if old is None or new is None:
            continue
        exception = new_exceptions.get(key)
        if exception and exception.action == "cancel":
            if new != old:
                raise ValueError("cancelling a moved instance must name its exact prior window")
        elif old[0] < logical_time <= new[0]:
            raise ValueError("a planning revision cannot move an already elapsed instance into the future")


class WorldPlanningReading(FrozenModel):
    timezone_name: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    epistemic_scope: Literal["published_external_arrangement_not_attendance"] = "published_external_arrangement_not_attendance"
    title: str
    summary: str
    starts_on: date
    ends_on: date
    upcoming: tuple[PlanningInstance, ...] = Field(max_length=12)
