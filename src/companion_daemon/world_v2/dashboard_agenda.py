"""Bounded owner week projection. No scheduling, model calls or new authority."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from bisect import bisect_right
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field

from .dashboard_life_intention import read_dashboard_life_intention
from .dashboard_world_occurrence import read_dashboard_world_occurrence
from .schema_core import FrozenModel

TIMEZONE = "Asia/Shanghai"
ENTRY_LIMIT = 96


class DashboardAgendaEntry(FrozenModel):
    # Snapshot-local display indices, never domain identities or source refs.
    index: int = Field(ge=0)
    kind: Literal["plan", "world", "outlook", "commitment", "revisit", "expectation", "planning", "experience"]
    time_kind: Literal["timed", "window", "period"] = "timed"
    title: str = Field(max_length=80)
    status: str
    starts_at: datetime
    ends_at: datetime
    detail: str = Field(max_length=4000)
    content_state: Literal["read", "unavailable", "withheld", "not_settled"] = "read"
    truncated: bool = False
    related: tuple[int, ...] = ()


class DashboardWorldlineThread(FrozenModel):
    title: str = Field(max_length=120)
    status: str
    situation: str = Field(max_length=600)
    open_question: str = Field(max_length=600)
    developments: tuple[str, ...] = ()


class DashboardWorldlineStage(FrozenModel):
    title: str = Field(max_length=120)
    starts_on: date
    ends_on: date
    situation: str = Field(max_length=600)
    status: str


class DashboardWorldlineOpportunity(FrozenModel):
    # Display-only indices. Omission preserves the bytes of legacy snapshots.
    index: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    after_indexes: tuple[int, ...] | None = Field(default=None, exclude_if=lambda value: value is None)
    thread_indexes: tuple[int, ...] | None = Field(default=None, exclude_if=lambda value: value is None)
    title: str = Field(max_length=300)
    detail: str = Field(max_length=2400)
    lane_label: str
    thread_titles: tuple[str, ...] = ()
    opens_at: datetime
    closes_at: datetime
    status: str


class DashboardWorldlineConvergence(FrozenModel):
    decision_label: str
    thread_titles: tuple[str, ...] = ()
    reason: str = Field(max_length=600)


class DashboardWorldline(FrozenModel):
    review_at: datetime
    horizon_end: datetime
    threads: tuple[DashboardWorldlineThread, ...] = ()
    stages: tuple[DashboardWorldlineStage, ...] = ()
    opportunities: tuple[DashboardWorldlineOpportunity, ...] = ()
    convergences: tuple[DashboardWorldlineConvergence, ...] = ()


_THREAD_TITLES: dict[str, str] = {
    "campus-reading-life": "校园读书与周末旧书市场",
    "dorm-quiet-life": "宿舍楼与换季日常运转",
    "jiaxing-bookstore": "嘉兴家里的小书店经营季",
}

_STAGE_TITLES: dict[str, str] = {
    "late-autumn-2026": "秋学期后半段",
    "finals-2026": "期末与考试季",
    "winter-break-2027": "寒假与春节",
    "spring-term-2027": "春学期与店庆月",
    "early-summer-2027": "初夏至暑期",
    "autumn-2027": "新学年秋季",
}


def _clause_title(text: str, fallback: str) -> str:
    for sep in ("：", "；", "，", "。"):
        if sep in text:
            head = text.split(sep, 1)[0].strip()
            if 2 <= len(head) <= 28:
                return head
    return text[:24].strip() or fallback


class DashboardWeekSlot(FrozenModel):
    """Whole-week metadata, before bounded detail pagination; no new content reads."""

    date: date
    hour: int = Field(ge=0, le=22, multiple_of=2)
    known_count: int = Field(ge=1)
    kinds: dict[str, int]
    labels: tuple[str, ...] = ()
    first_page: int = Field(ge=0)
    last_page: int = Field(ge=0)


class DashboardAgenda(FrozenModel):
    week_slots: tuple[DashboardWeekSlot, ...] | None = Field(default=None, exclude_if=lambda value: value is None)
    week_start: date
    timezone: Literal["Asia/Shanghai"] = TIMEZONE
    state: Literal["ready", "empty", "unavailable"]
    outlook_state: Literal["ready", "empty", "unavailable", "disabled"]
    planning_state: Literal["ready", "unavailable"] = "ready"
    known_count: int = Field(ge=0)
    withheld_count: int = Field(ge=0)
    truncated: bool
    entries: tuple[DashboardAgendaEntry, ...] = Field(default=(), max_length=ENTRY_LIMIT)
    # Omit absent pagination in legacy snapshots so their visible hash is unchanged.
    page: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    page_count: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    worldline: DashboardWorldline | None = Field(default=None, exclude_if=lambda value: value is None)


def week_start_date(value: date) -> date:
    if not isinstance(value, date) or isinstance(value, datetime) or not 2 <= value.year <= 9998:
        raise ValueError("agenda week must be a supported calendar date")
    return value - timedelta(days=value.weekday())


def requested_week(query) -> date | None:
    """Display filters only; no audience or pin controls."""
    requested_page(query)
    if "week" not in query:
        return None
    raw = query["week"]
    value = date.fromisoformat(raw)
    if value.isoformat() != raw:
        raise ValueError("week must use YYYY-MM-DD")
    return week_start_date(value)


def requested_page(query) -> int | None:
    if set(query) - {"week", "page"} or any(len(query.getlist(k)) != 1 for k in query):
        raise ValueError("only one week and page filter are accepted")
    if "page" not in query:
        return None
    raw = query["page"]
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 7:
        raise ValueError("page must be a nonnegative integer")
    value = int(raw)
    if value > 1_000_000 or str(value) != raw:
        raise ValueError("page is outside the display range")
    return value


def compile_dashboard_agenda(*, ledger, projection, cursor, store, actor_ref,
                             week_start, outlook_head=None, outlook_state="disabled", privacy_ceiling="shareable", page=None):
    from .dashboard_home_snapshot import _activity_title, _plan_display_status, _STATUS_LABELS
    from .dashboard_expression_reading import expectation_display_status, window_display_status

    if not actor_ref:
        return DashboardAgenda(week_start=week_start, state="unavailable", outlook_state="unavailable",
                               known_count=0, withheld_count=0, truncated=False)
    zone = ZoneInfo(TIMEZONE)
    start = datetime.combine(week_start, time(), zone)
    end = start + timedelta(days=7)
    now = projection.logical_time
    rows = []
    withheld = 0
    planning_state, planning_truncated = "ready", False
    limited = None


    def add(kind, identity, opens, closes, privacy, item):
        nonlocal withheld
        if opens is None:
            return
        closes = closes or opens
        if not (opens < end and (closes > start or opens == closes and opens >= start)):
            return
        if privacy == "withhold" or {"public": 0, "shareable": 1, "personal": 2, "private": 3, "withhold": 4}[privacy] > {"public": 0, "shareable": 1, "personal": 2, "private": 3}[privacy_ceiling]:
            withheld += 1
            return
        rows.append((opens, closes, kind, identity, item))

    for item in projection.plans:
        if item.owner_actor_ref != actor_ref or item.scheduled_window is None:
            continue
        add("plan", item.plan_id, item.scheduled_window.opens_at, item.scheduled_window.closes_at,
            item.privacy_class, item)
    for item in projection.world_occurrences:
        if actor_ref in item.participant_refs:
            at = item.settled_at or item.activated_at
            add("world", item.occurrence_id, at, at, item.visibility, item)
    for item in projection.experiences:
        if actor_ref in item.values.participant_refs:
            add("experience", item.experience_id, item.values.occurred_from, item.values.occurred_to,
                item.values.privacy_class, item)
    if store is not None and now is not None:
        from .life_content import collect_user_channel_limited_content_refs
        from .world_planning import PlanningPhase, iter_planning_publications, expand_planning
        limited = collect_user_channel_limited_content_refs(ledger=ledger, projection=projection)
        try:
            publications = iter_planning_publications(projection=projection, store=store, actor_ref=actor_ref,
                user_channel_limited_content_refs=limited, privacy_ceiling=privacy_ceiling,
                starts_at=start, ends_at=end)
            for publication in publications:
                p = publication.planning
                period_start = max(publication.published_at, datetime.combine(p.starts_on, time(), zone))
                period_end = datetime.combine(p.ends_on + timedelta(days=1), time(), zone)
                if publication.effective_until is not None:
                    period_end = min(period_end, publication.effective_until)
                if period_end > period_start:
                    add("planning", publication.occurrence_ref, period_start, period_end,
                        publication.privacy_class, (publication, None))
                for phase in p.phases:
                    phase_start = max(period_start, datetime.combine(phase.starts_on, time(), zone))
                    phase_end = min(period_end, datetime.combine(phase.ends_on + timedelta(days=1), time(), zone))
                    if phase_end > phase_start:
                        add("planning", publication.occurrence_ref + ":phase:" + phase.phase_id,
                            phase_start, phase_end, publication.privacy_class, (publication, phase))
                instances = expand_planning(p, world_id=projection.world_id, starts_at=start, ends_at=end,
                    effective_from=publication.published_at, effective_until=publication.effective_until, limit=256)
                # 28 slots across seven days plus 32 moved exceptions fit in
                # the existing 256-instance reader bound. Page their metadata
                # together with the other records; only selected details read.
                for instance in instances:
                    add("planning", instance.instance_ref, instance.starts_at, instance.ends_at,
                        publication.privacy_class, (publication, instance))
        except ValueError:
            planning_state = "unavailable"
    for item in projection.commitments:
        if item.values.owner_ref == actor_ref:
            add("commitment", item.commitment_id, item.values.due_window.opens_at,
                item.values.due_window.closes_at, item.values.privacy_class, item)
    for item in projection.expression_plan_manifests:
        # Like the existing owner summary, these are authored intentions, not
        # automatic contact tasks or proof that a message has been received.
        if not all(beat.action.actor == actor_ref for beat in item.beats):
            continue
        for kind, intention in (("expectation", item.response_expectation), ("revisit", item.revisit)):
            if intention is not None:
                add(kind, item.plan_id, intention.not_before, intention.expires_at,
                    "withhold" if any(b.privacy_class == "withhold" for b in item.beats) else "private", item)
    worldline = None
    if outlook_head is not None:
        _, draft, evidence = outlook_head
        if (draft.world_id != projection.world_id or draft.actor_ref != actor_ref
                or any(evidence["cursor"][k] > v for k, v in cursor.model_dump().items())):
            outlook_state = "unavailable"
        else:
            from .world_outlook import OutlookStage
            from .world_outlook_execution import occurrence_ref
            for stage in draft.long_term:
                add("outlook", "stage:" + stage.stage_id,
                    datetime.combine(stage.starts_on, time(), zone),
                    datetime.combine(stage.ends_on + timedelta(days=1), time(), zone), "private", stage)
            for item in draft.opportunities:
                add("outlook", item.opportunity_id, item.opens_at, item.closes_at,
                    "withhold" if {"public": 0, "shareable": 1, "personal": 2, "private": 3, "withhold": 4}[item.execution.privacy_class]
                    > {"public": 0, "shareable": 1, "personal": 2, "private": 3}[privacy_ceiling]
                    else item.execution.privacy_class, item)
            if privacy_ceiling == "private":
                today_local = (now.astimezone(zone).date() if now is not None else week_start)
                thread_map = {
                    t.thread_id: _THREAD_TITLES.get(t.thread_id, _clause_title(t.situation, "生活主线"))
                    for t in draft.threads
                }
                wl_threads = tuple(
                    DashboardWorldlineThread(
                        title=thread_map[t.thread_id],
                        status={"open": "展开中", "paused": "暂缓", "closed": "已收束"}.get(t.status, "展开中"),
                        situation=t.situation,
                        open_question=t.open_question,
                        developments=tuple(t.developments),
                    )
                    for t in draft.threads
                )
                wl_stages = tuple(
                    DashboardWorldlineStage(
                        title=_STAGE_TITLES.get(s.stage_id, _clause_title(s.situation, "远期阶段")),
                        starts_on=s.starts_on,
                        ends_on=s.ends_on,
                        situation=s.situation,
                        status=(
                            "当前章节" if s.starts_on <= today_local <= s.ends_on
                            else "已走过" if s.ends_on < today_local
                            else "远期章节"
                        ),
                    )
                    for s in draft.long_term
                )
                visible_opps = [opp for opp in draft.opportunities if opp.execution.privacy_class != "withhold"]
                opp_indexes = {opp.opportunity_id: index for index, opp in enumerate(visible_opps)}
                thread_indexes = {thread.thread_id: index for index, thread in enumerate(draft.threads)}
                wl_opps = []
                for opp in draft.opportunities:
                    if opp.execution.privacy_class == "withhold":
                        continue
                    r_ref = occurrence_ref(projection.world_id, actor_ref, opp.opportunity_id)
                    realized = next(
                        (o for o in projection.world_occurrences if o.occurrence_id == r_ref and o.status in {"active", "settled"}),
                        None,
                    )
                    if realized is not None:
                        opp_status = "已落实为世界事件"
                    elif now is not None and opp.closes_at <= now:
                        opp_status = "候选窗口已结束"
                    elif now is not None and opp.opens_at <= now < opp.closes_at:
                        opp_status = "窗口开启中 · 待触发"
                    else:
                        opp_status = "待进入窗口"
                    wl_opps.append(
                        DashboardWorldlineOpportunity(
                            index=opp_indexes[opp.opportunity_id],
                            after_indexes=tuple(opp_indexes[ref] for ref in opp.after_opportunities if ref in opp_indexes),
                            thread_indexes=tuple(thread_indexes[ref] for ref in opp.thread_refs if ref in thread_indexes),
                            title=opp.external_possibility[:300],
                            detail=opp.execution.text[:2400],
                            lane_label="人物机缘" if opp.lane == "npc" else "环境机缘",
                            thread_titles=tuple(thread_map.get(tid, "生活主线") for tid in opp.thread_refs),
                            opens_at=opp.opens_at,
                            closes_at=opp.closes_at,
                            status=opp_status,
                        )
                    )
                wl_convs = tuple(
                    DashboardWorldlineConvergence(
                        decision_label="延续原世界线" if c.decision == "retain" else "触发世界线改写",
                        thread_titles=tuple(thread_map.get(tid, "生活主线") for tid in c.thread_refs),
                        reason=c.reason[:600],
                    )
                    for c in getattr(draft, "change_responses", ())
                )
                worldline = DashboardWorldline(
                    review_at=draft.review_at,
                    horizon_end=draft.horizon_end,
                    threads=wl_threads,
                    stages=wl_stages,
                    opportunities=tuple(wl_opps),
                    convergences=wl_convs,
                )
    rows.sort(key=lambda row: (row[0], row[2], row[3]))
    # Summarize authorized timed rows only. Periods/windows have a separate lane;
    # crossing midnight is clipped to each day, never inferred as free time.
    slot_counts = {}
    for position, (opens, closes, kind, _, item) in enumerate(rows):
        if kind in {"outlook", "commitment", "revisit", "expectation"}:
            continue
        if kind == "planning" and (item[1] is None or isinstance(item[1], PlanningPhase)):
            continue
        for offset in range(7):
            day_start = start + timedelta(days=offset)
            day_end = day_start + timedelta(days=1)
            if not (opens < day_end and (closes > day_start or opens == closes and opens >= day_start)):
                continue
            minute = int((max(opens, day_start) - day_start).total_seconds() // 60)
            key = (day_start.date(), minute // 120 * 2)
            slot = slot_counts.setdefault(key, {"count": 0, "kinds": {}, "first_page": position // ENTRY_LIMIT, "last_page": position // ENTRY_LIMIT, "labels": []})
            slot["count"] += 1
            slot["kinds"][kind] = slot["kinds"].get(kind, 0) + 1
            slot["last_page"] = position // ENTRY_LIMIT
            if kind == "planning" and len(slot["labels"]) < 2:
                title = item[1].title[:80]
                if title not in slot["labels"]:
                    slot["labels"].append(title)
    week_slots = tuple(DashboardWeekSlot(date=day, hour=hour, known_count=value["count"],
        kinds=value["kinds"], labels=tuple(value["labels"]), first_page=value["first_page"], last_page=value["last_page"])
        for (day, hour), value in sorted(slot_counts.items()))
    page_count = max(1, (len(rows) + ENTRY_LIMIT - 1) // ENTRY_LIMIT)
    if page is None:
        page = max(0, bisect_right([row[0] for row in rows], now or start) - 1) // ENTRY_LIMIT
    if type(page) is not int or page < 0:
        raise ValueError("invalid agenda page")
    page = min(page, page_count - 1)
    selected = rows[page * ENTRY_LIMIT:(page + 1) * ENTRY_LIMIT]
    lookup = {(row[2], row[3]): i for i, row in enumerate(selected)}
    entries = []
    for index, (opens, closes, kind, identity, item) in enumerate(selected):
        title, detail, status, content_state, truncated = "", "", "", "read", False
        related = []
        time_kind = "window" if kind in {"outlook", "commitment", "revisit", "expectation"} else "timed"
        if kind == "plan":
            intent = read_dashboard_life_intention(ledger=ledger, projection=projection, plan=item)
            title = intent.intention if intent else _activity_title(item.activity_kind)
            detail = intent.intention if intent else "此记录没有可核验的原始意图正文。"
            content_state = "read" if intent else "unavailable"
            status = _STATUS_LABELS.get(_plan_display_status(item, now), "计划状态未识别")
        elif kind == "world":
            reading = read_dashboard_world_occurrence(
                ledger=ledger, store=store, projection=projection, cursor=cursor,
                occurrence=item, actor_ref=actor_ref, viewer_privacy_ceiling=privacy_ceiling,
                max_characters=1800,
            )
            content_state = reading.status
            if reading.status == "withheld":
                # Content-scoped restrictions also hide titles and associations.
                withheld += 1
                continue
            headline_src = (reading.attempt_text or reading.text or "").strip()
            title = headline_src or ("活动的实际结果" if reading.attempt_text else "世界事件")
            status = _STATUS_LABELS.get(item.status, "事件状态未识别")
            detail = ("环境：" + reading.text if reading.text else "尚无可读取的已结算正文。")
            if reading.attempt_text:
                detail += "\n\n活动结果：" + reading.attempt_text
            truncated = reading.truncated
            if reading.plan_id is not None:
                related.append(("plan", reading.plan_id))
        elif kind == "planning":
            from .world_planning import PlanningPhase
            publication, instance = item
            if instance is None:
                title, detail = publication.planning.title, publication.planning.summary
                time_kind = "period"
                status = "客观规划 · 已公布"
            elif isinstance(instance, PlanningPhase):
                title, detail = instance.title, instance.summary
                time_kind, status = "period", "阶段安排 · 已公布"
                related.append(("planning", publication.occurrence_ref))
            else:
                title, detail = instance.title, instance.detail
                status = "已取消的安排" if instance.status == "cancelled" else "已公布的安排"
                if now and instance.ends_at <= now and instance.status != "cancelled":
                    status = "安排时间已过 · 执行另记"
                related.append(("planning", publication.occurrence_ref))
            detail += "\n\n这是已公布的客观安排；角色可以改变自己的行动，实际参加和结果由经历另行记录。"
            related.append(("world", publication.occurrence_ref))
        elif kind == "experience":
            from .dashboard_world_occurrence import experience_world_occurrence, _event_at
            from .life_content_reading import read_character_life_experience_content
            from .life_content import collect_user_channel_limited_content_refs
            title, status, detail, content_state = "角色经历", "已发生", "原始经历正文暂时不可读取。", "unavailable"
            try:
                if limited is None:
                    limited = collect_user_channel_limited_content_refs(ledger=ledger, projection=projection)
                if item.values.summary_ref in limited:
                    withheld += 1
                    continue
                _event_at(ledger, projection, item.origin.accepted_event_ref, "ExperienceCommitted")
                reading = read_character_life_experience_content(store=store, projection=projection, experience=item,
                    actor_ref=actor_ref, viewer_privacy_ceiling=privacy_ceiling, max_characters=3000,
                    user_channel_limited_content_refs=limited)
                world = reading.world_consequence
                detail = "当时的环境：" + world.environment.text
                if world.authorized_attempt_result:
                    detail += "\n\n活动结果：" + world.authorized_attempt_result.text
                response = reading.character_response
                if response and response.response_text is not None:
                    detail += "\n\n她当时的感受：" + response.response_text
                else:
                    detail += "\n\n她没有留下对此事的文字感受。"
                headline_src = (
                    response.response_text
                    if response and response.response_text
                    else (
                        world.authorized_attempt_result.text
                        if world.authorized_attempt_result
                        else world.environment.text
                    )
                ).strip()
                if headline_src:
                    title = headline_src
                content_state, truncated = "read", reading.truncated
                source = experience_world_occurrence(projection, item)
                if source:
                    related.append(("world", source.occurrence_id))
                if world.authorized_attempt_result:
                    related.append(("plan", world.authorized_attempt_result.execution_binding.plan_id))
            except ValueError:
                pass
        elif kind == "outlook":
            from .world_outlook import OutlookStage
            title = item.situation if isinstance(item, OutlookStage) else item.external_possibility
            if isinstance(item, OutlookStage):
                time_kind = "period"
            status = "候选窗口已结束" if now and closes <= now else "候选机会 · 未定"
            detail = title + "\n\n这是命运机制保留的外部可能性，受条件和角色选择影响，不代表已发生或角色已经知晓。"
            from .world_outlook_execution import occurrence_ref
            realized_ref = None if isinstance(item, OutlookStage) else occurrence_ref(projection.world_id, actor_ref, item.opportunity_id)
            realized = next((o for o in projection.world_occurrences if o.occurrence_id == realized_ref
                             and o.status in {"active", "settled"}), None)
            if realized is not None:
                status = "已落实为世界事件"
                detail = item.external_possibility + "\n\n这项机会已经产生世界事件；具体结果和角色的经历分别记录。"
                related.append(("world", realized_ref))
            if not isinstance(item, OutlookStage):
                related.extend(("outlook", ref) for ref in item.after_opportunities)
        elif kind == "commitment":
            title, detail = "约定的时间窗口", "约定的状态与期限；此处没有可核验的约定正文。"
            status = _STATUS_LABELS.get(item.values.status, "约定状态未识别")
            content_state = "unavailable"
        elif kind == "expectation":
            title = item.response_expectation.hoped_response
            status = _STATUS_LABELS.get(expectation_display_status(projection, item), "回应状态未识别")
            detail = title + "\n\n这是角色的回应期待；窗口到期不等于用户故意冷落，也不会自动发出追问。"
        else:
            title = item.revisit.thought
            status = _STATUS_LABELS.get(window_display_status(now, item.revisit), "再次考虑窗口")
            detail = title + "\n\n到时由角色重新判断是否联系，不是已安排发送的消息。"
        entries.append(DashboardAgendaEntry(
            index=index, kind=kind, time_kind=time_kind, title=title[:80], detail=detail[:4000], status=status,
            starts_at=opens, ends_at=closes, content_state=content_state,
            truncated=truncated or len(detail) > 4000,
            related=tuple(lookup[ref] for ref in related if ref in lookup),
        ))
    visible = {entry.index for entry in entries}
    entries = tuple(entry.model_copy(update={"related": tuple(i for i in entry.related if i in visible)})
                    for entry in entries)
    return DashboardAgenda(
        week_start=week_start, state="ready" if entries else "empty" if now else "unavailable",
        outlook_state=outlook_state, known_count=len(rows), withheld_count=withheld,
        truncated=len(rows) > ENTRY_LIMIT or planning_truncated, entries=entries, planning_state=planning_state,
        page=page, page_count=page_count, worldline=worldline, week_slots=week_slots,
    )
