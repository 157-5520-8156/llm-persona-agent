#!/usr/bin/env python3
"""Prove inbound QQ face names reach her on a production ledger clone.

Never writes ``data/``, never talks to 8787 or NapCat. Artifacts live in
``output/reaction-names/``.

Usage::

    .venv/bin/python scripts/prove_reaction_names.py
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import drive_production_lanes as drive
from companion_daemon.world_v2.qq_face_render_catalog import INBOUND_SURFACE_PROMPT_CLAUSE
from companion_daemon.world_v2.qq_ingress_policy import QQIngressFragment
from companion_daemon.world_v2.system_notice import SYSTEM_NOTICE_TEXT

OUTPUT = (REPO / "output" / "reaction-names").resolve()
PRODUCTION_DB = drive.PRODUCTION_DB
WORLD_ID = drive.WORLD_ID
N_SUN_TRIALS = 6
UNKNOWN_FACE = "qq-face:99999"


class CapturingChat:
    """Forward every provider call, including the stream API used in production."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.model = getattr(inner, "model", type(inner).__name__)
        self.captures: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def _record(self, messages: list[dict[str, Any]], raw: str) -> None:
        user = next((item for item in messages if item.get("role") == "user"), None)
        content = user.get("content") if isinstance(user, dict) else None
        parsed = None
        trigger = None
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                trigger = parsed.get("current_trigger_message")
                if not isinstance(trigger, dict):
                    request = parsed.get("request")
                    if isinstance(request, dict):
                        trigger = request.get("trigger_message")
        if not isinstance(trigger, dict):
            return
        if not (
            trigger.get("reaction_refs")
            or trigger.get("sticker_refs")
            or trigger.get("reply_refs")
            or trigger.get("inbound_surfaces")
        ):
            return
        system = str(messages[0].get("content") if messages else "")
        self.captures.append(
            {
                "captured_at": datetime.now(UTC).isoformat(),
                "trigger": trigger,
                "system_clause_present": INBOUND_SURFACE_PROMPT_CLAUSE.strip() in system,
                "system_excerpt": system[system.find("The current trigger") : system.find("The current trigger") + 700]
                if "The current trigger" in system
                else system[:700],
                "raw_excerpt": raw[:4000],
            }
        )

    async def complete_json_with_usage(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        raw, usage = await self._inner.complete_json_with_usage(messages, **kwargs)
        self._record(list(messages), str(raw))
        return raw, usage

    async def complete_json_stream_with_usage(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        raw, usage = await self._inner.complete_json_stream_with_usage(messages, **kwargs)
        self._record(list(messages), str(raw))
        return raw, usage

    async def complete(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        raw = await self._inner.complete(messages, **kwargs)
        self._record(list(messages), str(raw))
        return raw


def _open_events(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _event_rows(path: Path, *, after_seq: int) -> list[tuple[int, dict[str, Any]]]:
    conn = _open_events(path)
    try:
        return drive.events_after(conn, WORLD_ID, after_seq)
    finally:
        conn.close()


def _failure_codes(rows: list[tuple[int, dict[str, Any]]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for seq, event in rows:
        payload = event.get("_payload") or {}
        audit = payload.get("audit_json")
        if isinstance(audit, str):
            try:
                audit = json.loads(audit)
            except json.JSONDecodeError:
                audit = None
        code = None
        if isinstance(audit, dict):
            code = audit.get("failure_code")
        if code is None:
            code = payload.get("failure_code")
        if code:
            found.append(
                {
                    "seq": seq,
                    "event_type": event.get("event_type"),
                    "failure_code": code,
                    "failure_detail": (
                        audit.get("failure_detail") if isinstance(audit, dict) else None
                    ),
                }
            )
    return found


def _timing_choices(rows: list[tuple[int, dict[str, Any]]]) -> list[str]:
    choices: list[str] = []
    for _seq, event in rows:
        if event.get("event_type") != "ProposalRecorded":
            continue
        blob = json.dumps(event.get("_payload") or {}, ensure_ascii=False)
        for token in ("silent", "now", "later"):
            if f'"timing_choice": "{token}"' in blob or f'"timing_choice":"{token}"' in blob:
                choices.append(token)
                break
    return choices


def _count_types(rows: list[tuple[int, dict[str, Any]]], event_type: str) -> int:
    return sum(1 for _seq, event in rows if event.get("event_type") == event_type)


async def _open_capturing_session(*, clone: Path) -> tuple[drive.DriveSession, CapturingChat]:
    from companion_daemon.config import Settings
    from companion_daemon.llm import DeepSeekChatModel
    from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore
    from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host

    settings = Settings(
        database_path=clone,
        world_v2_external_perception_mode="off",
        world_v2_external_perception_sidecar_path=clone.parent / f"{clone.stem}.sidecars" / "perception.sqlite",
        attachment_cache_path=clone.parent / f"{clone.stem}.sidecars" / "attachments",
        world_v2_text_endpoint_enabled=False,
    )
    (clone.parent / f"{clone.stem}.sidecars").mkdir(parents=True, exist_ok=True)
    recipient_id = drive._recipient_id(settings)
    usage_store = WorldV2UsageStore(path=str(clone))
    inner = DeepSeekChatModel(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        thinking_enabled=False,
        max_completion_tokens=4_096,
        usage_observer=usage_store.record,
    )
    capturing = CapturingChat(inner)
    delivery = drive.CaptureDelivery()
    host = build_qq_c2c_host(
        settings=settings,
        recipient_id=recipient_id,
        bootstrap_at=datetime.now(UTC),
        delivery=delivery,
        model=capturing,
        media_preview=None,
        media_transport=None,
        use_configured_recall_embedding=False,
    )
    session = drive.DriveSession(
        database=clone,
        recipient_id=recipient_id,
        delivery=delivery,
        host=host,
        clock=datetime.now(UTC),
    )
    await session.logical_time()
    return session, capturing


async def _inbound_fragment(
    session: drive.DriveSession,
    fragment: QQIngressFragment,
) -> dict[str, Any]:
    before = len(session.delivery.sent)
    result = await session.host.inbound_fragment(fragment)
    session.clock = fragment.observed_at
    await session.drain(actions=8, background=8)
    return {
        "status": getattr(result, "status", None),
        "action_id": getattr(result, "action_id", None),
        "visible": list(session.delivery.sent[before:]),
        "source_event_id": fragment.source_event_id,
        "content_shape": fragment.content_shape,
        "reaction_refs": list(fragment.reaction_refs),
    }


def _summarize_trial(
    *,
    index: str,
    inbound: dict[str, Any],
    rows: list[tuple[int, dict[str, Any]]],
    captures: list[dict[str, Any]],
) -> dict[str, Any]:
    notices = [
        item
        for item in inbound["visible"]
        if item.get("kind") == "text" and item.get("body") == SYSTEM_NOTICE_TEXT
    ]
    her_text = [
        item.get("body")
        for item in inbound["visible"]
        if item.get("kind") == "text" and item.get("body") != SYSTEM_NOTICE_TEXT
    ]
    return {
        "index": index,
        "inbound": inbound,
        "failure_codes": _failure_codes(rows),
        "timing_choices": _timing_choices(rows),
        "system_notices": notices,
        "her_text": her_text,
        "visible": inbound["visible"],
        "captures": captures,
        "reclaimed": _count_types(rows, "TriggerProcessReclaimed"),
        "fact_technical_failures": _count_types(rows, "InteractionFactTechnicalFailureRecorded"),
        "event_types": [event.get("event_type") for _seq, event in rows],
    }


async def run() -> dict[str, Any]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    clone = OUTPUT / "clone.sqlite"
    drive.clone_ledger(PRODUCTION_DB, clone)
    started_seq = drive.current_seq(clone)
    usage_from = drive.current_usage_id(clone)
    session, capturing = await _open_capturing_session(clone=clone)
    sun_trials: list[dict[str, Any]] = []
    unknown_trial: dict[str, Any] | None = None
    try:
        clock = await session.logical_time()
        for index in range(N_SUN_TRIALS):
            when = clock + timedelta(seconds=3 + index * 4)
            seq_before = drive.current_seq(clone)
            captures_before = len(capturing.captures)
            inbound = await _inbound_fragment(
                session,
                QQIngressFragment(
                    source_event_id=f"reaction-names-sun-{time.time_ns()}",
                    recipient_id=session.recipient_id,
                    observed_at=when,
                    content_shape="reaction",
                    reaction_refs=("qq-face:74",),
                ),
            )
            trial = _summarize_trial(
                index=str(index),
                inbound=inbound,
                rows=_event_rows(clone, after_seq=seq_before),
                captures=capturing.captures[captures_before:],
            )
            sun_trials.append(trial)
            (OUTPUT / f"trial-sun-{index}.json").write_text(
                json.dumps(trial, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            clock = when

        unknown_when = clock + timedelta(seconds=4)
        seq_before = drive.current_seq(clone)
        captures_before = len(capturing.captures)
        unknown_inbound = await _inbound_fragment(
            session,
            QQIngressFragment(
                source_event_id=f"reaction-names-unknown-{time.time_ns()}",
                recipient_id=session.recipient_id,
                observed_at=unknown_when,
                content_shape="reaction",
                reaction_refs=(UNKNOWN_FACE,),
            ),
        )
        unknown_trial = _summarize_trial(
            index="unknown",
            inbound=unknown_inbound,
            rows=_event_rows(clone, after_seq=seq_before),
            captures=capturing.captures[captures_before:],
        )
        (OUTPUT / "trial-unknown.json").write_text(
            json.dumps(unknown_trial, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    finally:
        await session.close()

    cost = drive.cost_report(clone, since_id=usage_from)
    all_trials = [*sun_trials, unknown_trial] if unknown_trial is not None else sun_trials
    presented = next(
        (item["captures"][0]["trigger"] for item in sun_trials if item["captures"]),
        None,
    )
    unknown_presented = None
    if unknown_trial and unknown_trial["captures"]:
        unknown_presented = unknown_trial["captures"][0]["trigger"]
    report = {
        "clone": str(clone),
        "started_seq": started_seq,
        "ended_seq": drive.current_seq(clone),
        "sun_trials": [
            {
                "index": item["index"],
                "status": item["inbound"]["status"],
                "her_text": item["her_text"],
                "timing_choices": item["timing_choices"],
                "system_notices": item["system_notices"],
                "failure_codes": item["failure_codes"],
                "reclaimed": item["reclaimed"],
                "capture_count": len(item["captures"]),
            }
            for item in sun_trials
        ],
        "presented_sun_trigger": presented,
        "presented_unknown_trigger": unknown_presented,
        "unknown_trial": {
            "status": unknown_trial["inbound"]["status"] if unknown_trial else None,
            "her_text": unknown_trial["her_text"] if unknown_trial else None,
            "system_notices": unknown_trial["system_notices"] if unknown_trial else None,
            "failure_codes": unknown_trial["failure_codes"] if unknown_trial else None,
            "reclaimed": unknown_trial["reclaimed"] if unknown_trial else None,
        },
        "system_clause": INBOUND_SURFACE_PROMPT_CLAUSE.strip(),
        "ratio": {
            "n": N_SUN_TRIALS,
            "replied": sum(1 for item in sun_trials if item["her_text"]),
            "silent": sum(
                1
                for item in sun_trials
                if not item["her_text"] and "silent" in item["timing_choices"]
            ),
            "failed": sum(
                1 for item in sun_trials if item["failure_codes"] or item["system_notices"]
            ),
        },
        "system_notice_triggered": any(item["system_notices"] for item in all_trials),
        "reclaimed_total": sum(item["reclaimed"] for item in all_trials),
        "fact_technical_failures_total": sum(
            item["fact_technical_failures"] for item in all_trials
        ),
        "cost": cost,
    }
    (OUTPUT / "clone-run.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return report


def main() -> None:
    report = asyncio.run(run())
    print(json.dumps(
        {
            "started_seq": report["started_seq"],
            "ended_seq": report["ended_seq"],
            "ratio": report["ratio"],
            "system_notice_triggered": report["system_notice_triggered"],
            "reclaimed_total": report["reclaimed_total"],
            "presented_sun_name": (
                (report["presented_sun_trigger"] or {}).get("inbound_surfaces") or [{}]
            )[0].get("platform_render_name")
            if report["presented_sun_trigger"]
            else None,
            "presented_unknown_status": (
                (report["presented_unknown_trigger"] or {}).get("inbound_surfaces") or [{}]
            )[0].get("epistemic_status")
            if report["presented_unknown_trigger"]
            else None,
            "her_text": [item["her_text"] for item in report["sun_trials"]],
            "cost": report["cost"],
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    ))


if __name__ == "__main__":
    main()
