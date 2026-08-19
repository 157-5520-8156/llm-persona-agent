#!/usr/bin/env python3
"""Clone-only proof: later expressions re-check the world before dispatch.

Never writes ``data/``. Never talks to 8787 / NapCat. Overlay is used only to
freeze the morning's later payload; the other-lane send and the due refresh
are real model turns.

Usage::

    .venv/bin/python scripts/prove_lane_coherence.py
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in __import__("sys").path:
    __import__("sys").path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in __import__("sys").path:
    __import__("sys").path.insert(0, str(REPO / "scripts"))

import drive_production_lanes as drive

OUTPUT = (REPO / "output" / "lane-coherence").resolve()
SHANGHAI = ZoneInfo("Asia/Shanghai")
COST_CAP_CNY = 8.0
N_TRIALS = 6
LATER_SECONDS = 3_600
OTHER_LANE_AFTER = timedelta(seconds=960)
FROZEN = "早～外面好像要下雨了，你那边呢"
WORLD_ID = drive.WORLD_ID
_LOG = logging.getLogger("prove-lane-coherence")
REFRESH_PREFIX = "consideration:social-initiative:later-refresh:"


def _cost(clone: Path, since_id: int) -> float:
    return float((drive.cost_report(clone, since_id=since_id) or {}).get("cost_cny") or 0)


def extract_slim_fields(raw: object) -> dict[str, Any]:
    found: dict[str, Any] = {}

    def consider(node: object) -> None:
        if isinstance(node, str):
            try:
                consider(json.loads(node))
            except json.JSONDecodeError:
                return
            return
        if not isinstance(node, dict):
            return
        for key in (
            "messages",
            "later",
            "timing_choice",
            "impulse_summary",
            "felt",
        ):
            if key in node and key not in found:
                found[key] = node.get(key)
        for nested in (
            node.get("payload_json"),
            node.get("result"),
            node.get("payload"),
            node.get("expression_draft"),
        ):
            if nested is not None:
                consider(nested)

    consider(raw)
    return found


def _scan_prompt(messages: list[dict[str, object]]) -> dict[str, Any]:
    blob = "\n".join(
        str(item.get("content") or "") for item in messages if isinstance(item, dict)
    )
    return {
        "has_waiting_fact": "还没发出" in blob or "messages_waiting_to_send" in blob,
        "has_i_spoke_fact": "另外发过话" in blob or "没另外发过" in blob,
        "has_he_spoke_fact": "他还没回" in blob or "他又开口" in blob,
        "has_dont_repeat": "别重复" in blob or "不要再问两次" in blob,
        "excerpt": blob[:2000],
    }


class RecordingCharacterModel:
    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.calls: list[dict[str, Any]] = []
        self.prompts: list[dict[str, Any]] = []
        self.model = getattr(inner, "model", "recording")
        self.provider = getattr(inner, "provider", "recording")
        self.supports_required_tool_choice = True
        self.supports_strict_tool_choice = bool(
            getattr(inner, "supports_strict_tool_choice", True)
        )
        self.reports_exact_request_emission = bool(
            getattr(inner, "reports_exact_request_emission", False)
        )

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def _tool_name(self, tools: list[dict[str, object]] | None) -> str:
        if not tools:
            return ""
        function = tools[0].get("function") if isinstance(tools[0], dict) else None
        name = function.get("name") if isinstance(function, dict) else ""
        return str(name or "")

    def _record(
        self, *, tools: list[dict[str, object]] | None, text: str, prompt: dict[str, Any]
    ) -> None:
        self.calls.append(
            {
                "tool": self._tool_name(tools),
                "fields": extract_slim_fields(text),
                "excerpt": (text or "")[:800],
                "prompt": prompt,
            }
        )
        self.prompts.append(prompt)

    async def complete_json_with_usage(
        self,
        messages: list[dict[str, object]],
        *,
        temperature: float = 0.8,
        tools: list[dict[str, object]] | None = None,
        tool_choice: object | None = None,
    ) -> tuple[str, dict[str, object]]:
        prompt = _scan_prompt(messages)
        text, usage = await self._inner.complete_json_with_usage(
            messages, temperature=temperature, tools=tools, tool_choice=tool_choice
        )
        self._record(tools=tools, text=text, prompt=prompt)
        return text, usage

    async def complete_json_stream_with_usage(
        self,
        messages: list[dict[str, object]],
        *,
        temperature: float = 0.8,
        on_text_delta: Callable[[str], object] | None = None,
        tools: list[dict[str, object]] | None = None,
        tool_choice: object | None = None,
    ) -> tuple[str, dict[str, object] | None]:
        prompt = _scan_prompt(messages)
        text, usage = await self._inner.complete_json_stream_with_usage(
            messages,
            temperature=temperature,
            on_text_delta=on_text_delta,
            tools=tools,
            tool_choice=tool_choice,
        )
        self._record(tools=tools, text=text, prompt=prompt)
        return text, usage


async def open_recorded_session(
    *,
    database: Path,
    output_dir: Path,
    inbound_payload: dict[str, object] | None = None,
) -> tuple[drive.DriveSession, RecordingCharacterModel]:
    from companion_daemon.config import Settings
    from companion_daemon.llm import DeepSeekChatModel
    from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore
    from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host

    if drive._is_production_path(database):
        raise SystemExit(f"refusing to open production ledger for write: {database}")
    sidecar = output_dir / f"{database.stem}.sidecars"
    sidecar.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        database_path=database,
        world_v2_external_perception_mode="off",
        world_v2_external_perception_sidecar_path=sidecar / "perception.sqlite",
        attachment_cache_path=sidecar / "attachments",
        world_v2_text_endpoint_enabled=False,
    )
    recipient_id = drive._recipient_id(settings)
    delivery = drive.CaptureDelivery()
    usage_store = WorldV2UsageStore(path=str(database))
    inner = DeepSeekChatModel(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        thinking_enabled=False,
        max_completion_tokens=4_096,
        usage_observer=usage_store.record,
    )
    wrapped: object = inner
    if inbound_payload is not None:
        wrapped = drive.OverlayCharacterModel(inner, inbound_payload)
    recorder = RecordingCharacterModel(wrapped)
    host = build_qq_c2c_host(
        settings=settings,
        recipient_id=recipient_id,
        bootstrap_at=datetime.now(UTC),
        delivery=delivery,
        model=recorder,
        media_preview=None,
        media_transport=None,
        use_configured_recall_embedding=False,
    )
    session = drive.DriveSession(
        database=database,
        recipient_id=recipient_id,
        delivery=delivery,
        host=host,
        clock=datetime.now(UTC),
    )
    await session.logical_time()
    return session, recorder


async def inbound_actions_only(session: drive.DriveSession, text: str) -> dict[str, Any]:
    when = session.clock + timedelta(seconds=2)
    message_id = f"drive-later-{int(when.timestamp())}"
    before = len(session.delivery.sent)
    result = await session.host.inbound_text(
        message_id=message_id,
        recipient_id=session.recipient_id,
        text=text,
        observed_at=when,
    )
    session.clock = when
    await session.drain(actions=8, background=0)
    return {
        "status": getattr(result, "status", None),
        "visible": session.delivery.sent[before:],
        "observed_at": when.isoformat(),
    }


def _ledger(session: drive.DriveSession):
    platform = getattr(session.host, "_host", None)
    application = getattr(platform, "_application", None)
    return getattr(application, "_ledger", None)


def queued_laters(session: drive.DriveSession) -> list[dict[str, Any]]:
    from companion_daemon.world_v2.later_expression_freshness import queued_later_facts

    projection = session.projection()
    if projection is None:
        return []
    return [
        {
            "action_id": item.action_id,
            "text": item.text,
            "send_at": item.send_at.isoformat(),
            "written_at": item.written_at.isoformat(),
            "i_spoke_after": item.i_spoke_after,
            "he_spoke_after": item.he_spoke_after,
        }
        for item in queued_later_facts(projection, logical_time=projection.logical_time)
    ]


def later_action(session: drive.DriveSession):
    projection = session.projection()
    if projection is None:
        return None
    from companion_daemon.world_v2.later_expression_freshness import is_later_followup

    return next(
        (
            item
            for item in getattr(projection, "actions", ())
            if is_later_followup(item)
            and getattr(item, "state", None)
            not in {"failed", "cancelled", "expired", "delivered"}
        ),
        None,
    )


async def opportunity_kind(session: drive.DriveSession) -> str | None:
    from companion_daemon.world_v2.social_initiative import (
        SocialInitiativeCompiler,
        SocialInitiativePolicy,
    )

    ledger = _ledger(session)
    if ledger is None:
        return None
    compiler = SocialInitiativeCompiler(
        ledger=ledger, actor_ref="agent:companion", policy=SocialInitiativePolicy()
    )
    found = await compiler.next_opportunity(ledger.project())
    return None if found is None else found.source_kind


def visible_bodies(items: list[dict[str, object]]) -> list[str]:
    return [
        str(item.get("body") or "")
        for item in items
        if isinstance(item, dict) and item.get("kind") == "text"
    ]


def looks_like_fresh_morning_open(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith("早") or stripped.startswith("早安")


def continues_prior(text: str) -> bool:
    needles = ("在忙", "在吗", "人呢", "回我", "刚才", "那条", "看到", "没回", "忙完")
    return any(needle in text for needle in needles)


async def run_timeline(*, source: Path, dest: Path, index: int) -> dict[str, Any]:
    drive.clone_ledger(source, dest)
    usage_from = drive.current_usage_id(dest)
    started_seq = drive.current_seq(dest)
    slim = {
        "messages": [FROZEN],
        "felt": "想跟他说一声外面要下雨",
        "stuck_with_me": "这句先搁着",
        "wants": "过一会儿再发",
        "photo": False,
        "later": LATER_SECONDS,
    }
    payload = drive._authored_inbound_payload(slim)
    session, recorder = await open_recorded_session(
        database=dest, output_dir=OUTPUT, inbound_payload=payload
    )
    try:
        inbound = await inbound_actions_only(session, "我出门了")
        queued_after_write = queued_laters(session)
        later = later_action(session)
        if later is None or not queued_after_write:
            return {
                "status": "no_later_queued",
                "clone": str(dest),
                "inbound": inbound,
                "queued": queued_after_write,
                "cost": drive.cost_report(dest, since_id=usage_from),
            }
        due_at = later.not_before
        now = await session.logical_time()
        other_at = now + OTHER_LANE_AFTER
        await session.tick_to(other_at, reason=f"other-lane-{index}", run_life=False)
        before_other = len(session.delivery.sent)
        other_kind = await opportunity_kind(session)
        await session.drain_loop(rounds=12, background=16)
        other_visible = visible_bodies(session.delivery.sent[before_other:])
        queued_after_other = queued_laters(session)
        await session.tick_to(due_at, reason=f"later-due-{index}", run_life=False)
        refresh_kind = await opportunity_kind(session)
        before_refresh_calls = len(recorder.calls)
        before_refresh_sent = len(session.delivery.sent)
        await session.drain_loop(rounds=12, background=16)
        refresh_calls = recorder.calls[before_refresh_calls:]
        refresh_visible = visible_bodies(session.delivery.sent[before_refresh_sent:])
        refresh_prompts = [
            item.get("prompt") or {}
            for item in refresh_calls
            if isinstance(item.get("prompt"), dict)
        ]
        fields = [item.get("fields") or {} for item in refresh_calls]
        her_refresh = []
        for row in fields:
            messages = row.get("messages")
            if isinstance(messages, list):
                her_refresh.extend(str(part) for part in messages if part)
            timing = row.get("timing_choice")
            if timing:
                her_refresh.append(f"[timing_choice={timing}]")
        events = []
        for seq, event in _events_after(dest, started_seq):
            payload = event.get("_payload") or {}
            process = payload.get("process") if isinstance(payload.get("process"), dict) else {}
            action = payload.get("action") if isinstance(payload.get("action"), dict) else {}
            events.append(
                {
                    "seq": seq,
                    "event_type": event.get("event_type"),
                    "logical_time": event.get("logical_time"),
                    "process_kind": process.get("process_kind"),
                    "trigger_ref": process.get("trigger_ref"),
                    "action_kind": action.get("kind"),
                    "action_id": action.get("action_id"),
                    "action_state": action.get("state"),
                }
            )
        opened = [
            item
            for item in events
            if item.get("event_type") == "TriggerProcessOpened"
            and REFRESH_PREFIX in str(item.get("trigger_ref") or "")
        ]
        frozen_delivered = any(
            FROZEN in body for body in other_visible + refresh_visible
        )
        return {
            "status": "ran",
            "clone": str(dest),
            "queued_after_write": queued_after_write,
            "other_lane_kind": other_kind,
            "other_lane_visible": other_visible,
            "queued_after_other": queued_after_other,
            "refresh_kind": refresh_kind,
            "refresh_opened": bool(opened),
            "refresh_visible": refresh_visible,
            "her_refresh": her_refresh,
            "saw_waiting_fact": any(
                item.get("has_waiting_fact") for item in refresh_prompts
            ),
            "saw_i_spoke_fact": any(
                item.get("has_i_spoke_fact") for item in refresh_prompts
            ),
            "saw_dont_repeat": any(
                item.get("has_dont_repeat") for item in refresh_prompts
            ),
            "frozen_later_dispatched": frozen_delivered,
            "double_morning_open": any(
                looks_like_fresh_morning_open(body) for body in refresh_visible
            )
            and any(looks_like_fresh_morning_open(body) for body in other_visible),
            "continues_first": any(continues_prior(body) for body in refresh_visible)
            or any(
                continues_prior(part)
                for part in her_refresh
                if not str(part).startswith("[timing")
            ),
            "cost": drive.cost_report(dest, since_id=usage_from),
        }
    except Exception as exc:
        return {
            "status": "error",
            "clone": str(dest),
            "error": f"{type(exc).__name__}: {exc}"[:2000],
            "cost": drive.cost_report(dest, since_id=usage_from),
        }
    finally:
        await session.close()


def _events_after(database: Path, after_seq: int) -> list[tuple[int, dict[str, Any]]]:
    conn = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    try:
        return drive.events_after(conn, WORLD_ID, after_seq)
    finally:
        conn.close()


async def run_consecutive(*, source: Path, dest: Path) -> dict[str, Any]:
    drive.clone_ledger(source, dest)
    usage_from = drive.current_usage_id(dest)
    slim = {
        "messages": ["第一句，我先说这个。", "第二句接着。", "第三句也在。"],
        "felt": "想连着说完",
        "stuck_with_me": "这三句是一起的",
        "wants": "现在发完",
        "photo": False,
    }
    payload = drive._authored_inbound_payload(slim)
    session, _recorder = await open_recorded_session(
        database=dest, output_dir=OUTPUT, inbound_payload=payload
    )
    try:
        inbound = await inbound_actions_only(session, "在吗")
        now = await session.logical_time()
        await session.tick_to(now + timedelta(seconds=15), reason="cadence-siblings", run_life=False)
        await session.drain_loop(rounds=8, background=0)
        bodies = visible_bodies(session.delivery.sent)
        return {
            "status": "ran",
            "clone": str(dest),
            "inbound_status": inbound.get("status"),
            "visible": bodies,
            "count": len(bodies),
            "kept_consecutive": len(bodies) >= 3,
            "cost": drive.cost_report(dest, since_id=usage_from),
        }
    except Exception as exc:
        return {
            "status": "error",
            "clone": str(dest),
            "error": f"{type(exc).__name__}: {exc}"[:2000],
            "cost": drive.cost_report(dest, since_id=usage_from),
        }
    finally:
        await session.close()


def write_report(payload: Mapping[str, Any]) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    source = drive.PRODUCTION_DB
    trials: list[dict[str, Any]] = []
    spent = 0.0
    for index in range(1, N_TRIALS + 1):
        if spent >= COST_CAP_CNY:
            trials.append({"status": "skipped_budget", "index": index})
            continue
        dest = OUTPUT / f"timeline-{index}.sqlite"
        _LOG.info("timeline %s", index)
        row = await run_timeline(source=source, dest=dest, index=index)
        row["index"] = index
        trials.append(row)
        spent = round(spent + float((row.get("cost") or {}).get("cost_cny") or 0), 4)
        _LOG.info("timeline %s status=%s spent=%s", index, row.get("status"), spent)
    consecutive = None
    if spent < COST_CAP_CNY:
        consecutive = await run_consecutive(
            source=source, dest=OUTPUT / "consecutive-now.sqlite"
        )
        spent = round(spent + float((consecutive.get("cost") or {}).get("cost_cny") or 0), 4)
    payload = {
        "spent_cny": spent,
        "cost_cap_cny": COST_CAP_CNY,
        "frozen_later": FROZEN,
        "trials": trials,
        "consecutive_now": consecutive,
        "saw_refresh": sum(1 for item in trials if item.get("refresh_opened")),
        "saw_waiting_fact": sum(1 for item in trials if item.get("saw_waiting_fact")),
        "frozen_dispatched": sum(
            1 for item in trials if item.get("frozen_later_dispatched")
        ),
        "double_morning_open": sum(
            1 for item in trials if item.get("double_morning_open")
        ),
    }
    write_report(payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
