"""Published external arrangements. Recurrence describes schedules, never attendance."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from itertools import islice
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
    event_text: str | None = Field(default=None, min_length=1, max_length=600, exclude_if=lambda v: v is None,
        description="External scene at this appointment, never protagonist attendance, choice or experience.")
    prepare_details: bool = Field(default=False, exclude_if=lambda v: not v)

    @model_validator(mode="after")
    def unique_weekdays(self):
        if self.prepare_details and self.event_text is None:
            raise ValueError("prepared details need an authored external event")
        if len(set(self.weekdays)) != len(self.weekdays) or any(type(d) is not int or not 0 <= d <= 6 for d in self.weekdays):
            raise ValueError("planning weekdays must be distinct Monday=0..Sunday=6")
        return self


class PlanningPhase(FrozenModel):
    phase_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    starts_on: date
    ends_on: date
    title: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=400)
    slot_ids: tuple[str, ...] = Field(default=(), max_length=28,
        description="Replaces the ordinary weekly rules during this phase. Empty means no recurring appointments.")

    @model_validator(mode="after")
    def valid_window(self):
        if self.ends_on < self.starts_on or len(set(self.slot_ids)) != len(self.slot_ids):
            raise ValueError("planning phase must have a nonempty period and distinct slots")
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
    contract: Literal["world-planning.1", "world-planning.2", "world-planning.3"] = "world-planning.1"
    planning_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,95}$")
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=1200)
    starts_on: date
    ends_on: date
    timezone_name: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    slots: tuple[PlanningSlot, ...] = Field(default=(), max_length=28)
    exceptions: tuple[PlanningException, ...] = Field(default=(), max_length=32)
    phases: tuple[PlanningPhase, ...] = Field(default=(), max_length=16, exclude_if=lambda v: not v)
    detailed_through: date | None = Field(default=None, exclude_if=lambda v: v is None)
    review_at: datetime | None = Field(default=None, exclude_if=lambda v: v is None)
    continues: bool = Field(default=True, exclude_if=lambda v: v)
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
        if self.contract == "world-planning.1":
            if self.phases or self.detailed_through is not None or self.review_at is not None or not self.continues or any(s.event_text for s in self.slots):
                raise ValueError("rolling planning fields require world-planning.2")
        elif (self.detailed_through is None or not self.starts_on <= self.detailed_through <= self.ends_on
              or self.review_at is None or self.review_at.tzinfo is None or self.review_at.utcoffset() is None):
            raise ValueError("rolling planning needs explicit detail coverage and an aware review deadline")
        if self.contract == "world-planning.3" and (
            any(not s.event_text or not s.prepare_details or s.duration_minutes < 2 for s in self.slots)
            or any(e.duration_minutes is not None and e.duration_minutes < 2 for e in self.exceptions)
        ):
            raise ValueError("world-planning.3 requires a script and at least two minutes for every prepared event detail")
        if any(s.event_text and s.duration_minutes > 720 for s in self.slots):
            raise ValueError("one authored planning event can last at most twelve hours")
        prior_end = None
        phase_ids = set()
        for phase in sorted(self.phases, key=lambda p: p.starts_on):
            if (not self.starts_on <= phase.starts_on <= phase.ends_on <= self.ends_on
                    or prior_end is not None and phase.starts_on <= prior_end
                    or phase.phase_id in phase_ids or not set(phase.slot_ids) <= slots.keys()):
                raise ValueError("planning phases must be distinct, disjoint, inside the period and use known slots")
            prior_end = phase.ends_on
            phase_ids.add(phase.phase_id)
        exceptions = set()
        for item in self.exceptions:
            slot = slots.get(item.slot_id)
            key = (item.slot_id, item.original_date)
            if slot is None or key in exceptions or not self.slot_applies(slot, item.original_date):
                raise ValueError("exception must identify one real original recurrence")
            if item.new_start is not None and not self.starts_on <= item.new_start.astimezone(ZoneInfo(self.timezone_name)).date() <= self.ends_on:
                raise ValueError("replacement must remain inside the planning period")
            exceptions.add(key)
        if len(self.model_dump_json()) > 5_500:
            raise ValueError("planning document exceeds its bounded publication size")
        return self

    def slot_applies(self, slot, day):
        if not self.starts_on <= day <= (self.detailed_through or self.ends_on) or day.weekday() not in slot.weekdays:
            return False
        phase = next((p for p in self.phases if p.starts_on <= day <= p.ends_on), None)
        # Slots named by a phase are phase-specific, not a second ordinary timetable.
        return slot.slot_id in phase.slot_ids if phase else not any(slot.slot_id in p.slot_ids for p in self.phases)

    def preparation_due(self):
        zone = ZoneInfo(self.timezone_name)
        dates = [datetime.combine(self.ends_on - timedelta(days=7), time(), zone)] if self.continues else []
        if self.review_at is not None:
            dates.append(self.review_at)
            if self.detailed_through < self.ends_on:
                dates.append(datetime.combine(self.detailed_through - timedelta(days=2), time(), zone))
        return min(dates, default=None)

    def public_text(self) -> str:
        """Exact field presentation of a World publication, never character speech."""
        lines = [self.title, self.summary, f"规划期间：{self.starts_on} 至 {self.ends_on}（北京时间）。",
                 "这是客观安排，不证明角色参加、完成或同意了其中的活动。"]
        for slot in self.slots:
            days = "、".join("周" + "一二三四五六日"[day] for day in slot.weekdays)
            hour, minute = divmod(slot.start_minute, 60)
            lines.append(f"{days} {hour:02}:{minute:02}，持续 {slot.duration_minutes} 分钟：{slot.title}。{slot.detail}")
        if self.phases:
            lines.append("以下阶段替代对应日期的普通周安排；无列出课程的阶段不安排周期课程。")
            for phase in self.phases:
                titles = "、".join(s.title for s in self.slots if s.slot_id in phase.slot_ids) or "无周期课程"
                lines.append(f"{phase.starts_on} 至 {phase.ends_on}：{phase.title}。{phase.summary}。{titles}")
        if self.detailed_through is not None:
            lines.append(f"具体时段已细化至 {self.detailed_through}，之后仅有阶段安排，等待进一步公布。")
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
    slot_id: str | None = Field(default=None, exclude_if=lambda v: v is None)


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
            if not planning.slot_applies(slot, day):
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
            # One slot has at most one occurrence per original date. In v2 its
            # identity survives a rule-time edit as well as an explicit exception.
            identity = json.dumps([world_id, planning.planning_key, slot.slot_id,
                                   day.isoformat() if planning.contract != "world-planning.1" else original.isoformat()], separators=(",", ":"))
            instances.append(PlanningInstance(
                instance_ref="planning-instance:" + hashlib.sha256(identity.encode()).hexdigest(),
                title=slot.title, detail=slot.detail + ("\n" + exception.explanation if exception else ""),
                original_start=original, starts_at=actual, ends_at=end,
                status="cancelled" if exception and exception.action == "cancel" else "scheduled",
                **({"slot_id": slot.slot_id} if planning.contract != "world-planning.1" else {}),
            ))
    return tuple(sorted(instances, key=lambda i: (i.starts_at, i.instance_ref))[:limit])


class WorldPlanningIndex(FrozenModel):
    """Bounded candidate-selection metadata, rechecked against immutable content."""
    planning_key: str
    starts_on: date
    ends_on: date
    supersedes_occurrence_ref: str | None = None
    continues: bool = Field(default=True, exclude_if=lambda v: v)


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
                          starts_at=None, ends_at=None, limit=8, include_elapsed_heads=False):
    if not 1 <= limit <= 256:
        raise ValueError("planning publication read must be bounded")
    return tuple(islice(iter_planning_publications(projection=projection, store=store, actor_ref=actor_ref,
        user_channel_limited_content_refs=user_channel_limited_content_refs, privacy_ceiling=privacy_ceiling,
        starts_at=starts_at, ends_at=ends_at, include_elapsed_heads=include_elapsed_heads), limit))


def iter_planning_publications(*, projection, store, actor_ref, user_channel_limited_content_refs,
                               starts_at=None, ends_at=None, privacy_ceiling="private", include_elapsed_heads=False):
    """Read current/week-relevant revisions, without scanning historical bodies."""
    from .life_content_reading import selected_world_consequence
    from .occurrence_result_content_runtime import require_published_world_consequence

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
                    or index.ends_on < starts_at.astimezone(zone).date() and not (include_elapsed_heads and until is None and index.continues)
                    or index.starts_on > ends_at.astimezone(zone).date()):
                continue
            if any(ranks[p] > ranks[privacy_ceiling] or p == "withhold" for p in (occurrence.visibility, candidate.privacy_class)):
                continue
            eligible.append((occurrence, candidate, until))
    eligible.sort(key=lambda row: row[0].settlement_world_revision, reverse=True)
    # Source restrictions are supplied by the caller's ledger-backed compiler;
    # no raw candidate or unpublished document is a fallback.
    limited = user_channel_limited_content_refs
    eligible = [row for row in eligible if row[1].content_ref not in limited and row[0].result_payload_ref not in limited]
    for occurrence, candidate, until in eligible:
        require_published_world_consequence(projection=projection, occurrence=occurrence, content_store=store)
        value = selected_world_consequence(store=store, projection=projection, occurrence=occurrence,
            actor_ref=actor_ref, viewer_privacy_ceiling=privacy_ceiling, user_channel_limited_content_refs=limited)
        if value.world_planning is None or planning_index(value.world_planning) != candidate.world_planning_index:
            raise ValueError("planning metadata differs from its published source")
        yield PlanningPublication(planning=value.world_planning, occurrence_ref=occurrence.occurrence_id,
            source_event_ref=occurrence.settlement_event_ref, source_world_revision=occurrence.settlement_world_revision,
            source_payload_hash=occurrence.settlement_payload_hash, privacy_class=max((occurrence.visibility,candidate.privacy_class),key=ranks.__getitem__),
            published_at=occurrence.settled_at, effective_until=until)


def validate_planning_publication(planning, *, projection, actor_ref, occurrence_ref, store, ledger=None):
    """A revision names the exact prior publication; concurrent branches are rejected."""
    heads = []
    domain_heads = {}
    for occurrence in projection.world_occurrences:
        if occurrence.occurrence_id == occurrence_ref or actor_ref not in occurrence.participant_refs or occurrence.status in {"cancelled", "expired"}:
            continue
        for candidate in occurrence.candidate_outcomes:
            if candidate.result_contract == "world-consequence.3" and candidate.candidate_result_ref == occurrence.settled_outcome_ref:
                key = candidate.world_planning_index.planning_key
                old = domain_heads.get(key)
                if old is None or old[0] < occurrence.settlement_world_revision:
                    domain_heads[key] = (occurrence.settlement_world_revision, candidate.world_planning_index)
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
        executed = set()
        if previous.contract in {"world-planning.2", "world-planning.3"} and ledger is not None:
            # Earlier starts are already protected by the time boundary. Only
            # this exact-clock race needs activation evidence; never traverse
            # every historical publication for each occurrence.
            consumed = set()
            for occurrence in projection.world_occurrences:
                if occurrence.activated_at != projection.logical_time:
                    continue
                source = ledger.lookup_event_commit(occurrence.trigger_ref)
                if source and source[0].source == "world-outlook-execution.1":
                    origin = source[0].payload().get("central_authorship", {})
                    if origin.get("actor_ref") == actor_ref:
                        consumed.add(origin.get("planning_instance_origin", {}).get("instance_ref"))
            day = projection.logical_time.astimezone(ZoneInfo(previous.timezone_name)).date()
            days = {day, *(e.original_date for e in previous.exceptions), *(e.original_date for e in planning.exceptions)}
            for original_day in days:
                for slot in previous.slots:
                    identity = json.dumps([projection.world_id, previous.planning_key, slot.slot_id, original_day.isoformat()], separators=(",", ":"))
                    if "planning-instance:" + hashlib.sha256(identity.encode()).hexdigest() in consumed:
                        executed.add((slot.slot_id, original_day))
        validate_planning_revision(previous, planning, logical_time=projection.logical_time, executed_instances=executed)
    today = projection.logical_time.astimezone(ZoneInfo(planning.timezone_name)).date()
    live_keys = {key for key, (_, index) in domain_heads.items() if index.continues or index.ends_on >= today}
    if planning.planning_key not in live_keys and len(live_keys) >= 128:
        raise ValueError("at most 128 continuing planning domains; retire completed domains before publishing another")
    if planning.ends_on < today or planning.ends_on > today + timedelta(days=730):
        raise ValueError("planning publication must cover a bounded current or future period")


def validate_planning_revision(previous, current, *, logical_time, executed_instances=frozenset()):
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
        if slot is None or not value.slot_applies(slot, day):
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
        elif old[0] < logical_time <= new[0] or key in executed_instances and old[0] <= logical_time < new[0]:
            raise ValueError("a planning revision cannot move an already elapsed instance into the future")


class WorldPlanningReading(FrozenModel):
    timezone_name: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    epistemic_scope: Literal["published_external_arrangement_not_attendance"] = "published_external_arrangement_not_attendance"
    title: str
    summary: str
    starts_on: date
    ends_on: date
    upcoming: tuple[PlanningInstance, ...] = Field(max_length=12)
    phases: tuple[PlanningPhase, ...] = Field(default=(), max_length=16, exclude_if=lambda v: not v)
    detailed_through: date | None = Field(default=None, exclude_if=lambda v: v is None)


def planning_event_offers(*, projection, publications, ledger, actor_ref):
    """Bounded clock work from exact published model scripts, never attendance."""
    from .world_outlook import digest
    result = {}
    now = projection.logical_time
    for publication in publications:
        planning = publication.planning
        if planning.contract not in {"world-planning.2", "world-planning.3"} or publication.effective_until is not None:
            continue
        # Earliest bounded batch, re-expanded at each clock: elapsed instances leave
        # the window, so a dense later week cannot block today's valid work.
        instances = expand_planning(planning, world_id=projection.world_id,
            starts_at=now, ends_at=now + timedelta(days=7), effective_from=publication.published_at, limit=96)
        if not instances:
            continue
        occurrence = next(o for o in projection.world_occurrences if o.occurrence_id == publication.occurrence_ref)
        source = ledger.lookup_event_commit(occurrence.trigger_ref)
        if source is None or source[1].ledger_sequence > projection.ledger_sequence:
            raise ValueError("planning event has no original author")
        original = source[0].payload()
        if (source[0].source != "world-outlook-execution.1"
                or original.get("script", {}).get("planning") != planning.model_dump(mode="json")):
            raise ValueError("planning script differs from its original author")
        slots = {s.slot_id: s for s in planning.slots}
        occurrence_token = digest(occurrence)
        for instance in instances:
            slot = slots[instance.slot_id]
            if instance.status != "scheduled" or slot.event_text is None:
                continue
            node_id = "planning-" + digest([instance.instance_ref, publication.occurrence_ref])
            node = {
                "opportunity_id": node_id, "thread_refs": ["planning:" + planning.planning_key],
                "external_possibility": slot.event_text, "lane": "world", "npc_ref": None,
                "execution": {"kind": "environment", "text": slot.event_text,
                              "privacy_class": publication.privacy_class,
                              "duration_minutes": int((instance.ends_at - instance.starts_at).total_seconds() // 60),
                              **({"prepare_details": True} if slot.prepare_details else {})},
                "opens_at": instance.starts_at.isoformat(), "closes_at": instance.ends_at.isoformat(),
                "basis_refs": [publication.source_event_ref],
                "conditions": [{"source_ref": occurrence.occurrence_id, "ready_statuses": ["settled"],
                                "invalid_statuses": [], "unchanged_token": occurrence_token}],
                "after_opportunities": [],
            }
            origin = {**original["central_authorship"], "actor_ref": actor_ref,
                "planning_instance_origin": {"publication_ref": occurrence.occurrence_id,
                    "publication_event_ref": publication.source_event_ref, "publication_hash": publication.source_payload_hash,
                    "instance_ref": instance.instance_ref, "ends_at": instance.ends_at.isoformat(),
                    **({"planning_contract": "world-planning.3"} if planning.contract == "world-planning.3" else {})}}
            result[node_id] = (node, origin)
    return result
