"""Bounded owner week projection. No scheduling, model calls or new authority."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
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


class DashboardAgenda(FrozenModel):
    week_start: date
    timezone: Literal["Asia/Shanghai"] = TIMEZONE
    state: Literal["ready", "empty", "unavailable"]
    outlook_state: Literal["ready", "empty", "unavailable", "disabled"]
    planning_state: Literal["ready", "unavailable"] = "ready"
    known_count: int = Field(ge=0)
    withheld_count: int = Field(ge=0)
    truncated: bool
    entries: tuple[DashboardAgendaEntry, ...] = Field(default=(), max_length=ENTRY_LIMIT)


def week_start_date(value: date) -> date:
    if not isinstance(value, date) or isinstance(value, datetime) or not 2 <= value.year <= 9998:
        raise ValueError("agenda week must be a supported calendar date")
    return value - timedelta(days=value.weekday())


def requested_week(query) -> date | None:
    """Only a date filter may cross the owner route; no audience or pin controls."""
    if not query:
        return None
    if set(query) != {"week"} or len(query.getlist("week")) != 1:
        raise ValueError("only one week date is accepted")
    raw = query["week"]
    value = date.fromisoformat(raw)
    if value.isoformat() != raw:
        raise ValueError("week must use YYYY-MM-DD")
    return week_start_date(value)


def compile_dashboard_agenda(*, ledger, projection, cursor, store, actor_ref,
                             week_start, outlook_head=None, outlook_state="disabled", privacy_ceiling="shareable"):
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
        from .world_planning import planning_publications, expand_planning
        limited = collect_user_channel_limited_content_refs(ledger=ledger, projection=projection)
        try:
            publications = planning_publications(projection=projection, store=store, actor_ref=actor_ref,
                user_channel_limited_content_refs=limited, privacy_ceiling=privacy_ceiling,
                starts_at=start, ends_at=end, limit=17)
            planning_truncated = len(publications) > 16
            for publication in publications[:16]:
                p = publication.planning
                period_start = max(publication.published_at, datetime.combine(p.starts_on, time(), zone))
                period_end = datetime.combine(p.ends_on + timedelta(days=1), time(), zone)
                if publication.effective_until is not None:
                    period_end = min(period_end, publication.effective_until)
                if period_end > period_start:
                    add("planning", publication.occurrence_ref, period_start, period_end,
                        publication.privacy_class, (publication, None))
                instances = expand_planning(p, world_id=projection.world_id, starts_at=start, ends_at=end,
                    effective_from=publication.published_at, effective_until=publication.effective_until, limit=97)
                planning_truncated |= len(instances) > 96
                for instance in instances[:96]:
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
    if outlook_head is not None:
        _, draft, evidence = outlook_head
        if (draft.world_id != projection.world_id or draft.actor_ref != actor_ref
                or any(evidence["cursor"][k] > v for k, v in cursor.model_dump().items())):
            outlook_state = "unavailable"
        else:
            for item in draft.opportunities:
                add("outlook", item.opportunity_id, item.opens_at, item.closes_at,
                    "withhold" if {"public": 0, "shareable": 1, "personal": 2, "private": 3, "withhold": 4}[item.execution.privacy_class]
                    > {"public": 0, "shareable": 1, "personal": 2, "private": 3}[privacy_ceiling]
                    else item.execution.privacy_class, item)
    rows.sort(key=lambda row: (row[0], row[2], row[3]))
    selected = rows[:ENTRY_LIMIT]
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
            title = "活动的实际结果" if reading.attempt_text else "世界事件"
            status = _STATUS_LABELS.get(item.status, "事件状态未识别")
            detail = ("环境：" + reading.text if reading.text else "尚无可读取的已结算正文。")
            if reading.attempt_text:
                detail += "\n\n活动结果：" + reading.attempt_text
            truncated = reading.truncated
            if reading.plan_id is not None:
                related.append(("plan", reading.plan_id))
        elif kind == "planning":
            publication, instance = item
            if instance is None:
                title, detail = publication.planning.title, publication.planning.summary
                time_kind = "period"
                status = "客观规划 · 已公布"
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
                content_state, truncated = "read", reading.truncated
                source = experience_world_occurrence(projection, item)
                if source:
                    related.append(("world", source.occurrence_id))
                if world.authorized_attempt_result:
                    related.append(("plan", world.authorized_attempt_result.execution_binding.plan_id))
            except ValueError:
                pass
        elif kind == "outlook":
            title = item.external_possibility
            status = "候选窗口已结束" if now and closes <= now else "候选机会 · 未定"
            detail = item.external_possibility + "\n\n这是命运机制保留的外部可能性，受条件和角色选择影响，不代表已发生或角色已经知晓。"
            from .world_outlook_execution import occurrence_ref
            realized_ref = occurrence_ref(projection.world_id, actor_ref, item.opportunity_id)
            realized = next((o for o in projection.world_occurrences if o.occurrence_id == realized_ref
                             and o.status in {"active", "settled"}), None)
            if realized is not None:
                status = "已落实为世界事件"
                detail = item.external_possibility + "\n\n这项机会已经产生世界事件；具体结果和角色的经历分别记录。"
                related.append(("world", realized_ref))
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
    )
