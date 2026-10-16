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
    kind: Literal["plan", "world", "outlook", "commitment", "revisit", "expectation"]
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

    def add(kind, identity, opens, closes, privacy, item):
        nonlocal withheld
        if opens is None:
            return
        closes = closes or opens
        if not (opens < end and (closes > start or opens == closes and opens >= start)):
            return
        if privacy == "withhold":
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
        elif kind == "outlook":
            title = item.external_possibility
            status = "候选窗口已结束" if now and closes <= now else "候选机会 · 未定"
            detail = item.external_possibility + "\n\n这是命运机制保留的外部可能性，受条件和角色选择影响，不代表已发生或角色已经知晓。"
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
            index=index, kind=kind, title=title[:80], detail=detail[:4000], status=status,
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
        truncated=len(rows) > ENTRY_LIMIT, entries=entries,
    )
