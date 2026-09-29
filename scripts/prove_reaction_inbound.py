#!/usr/bin/env python3
"""Prove non-text inbound cognition on a production ledger clone.

Never writes ``data/``, never talks to 8787 or NapCat. Artifacts live in
``output/reaction-fix/``.

Usage::

    .venv/bin/python scripts/prove_reaction_inbound.py
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
from companion_daemon.world_v2.qq_ingress_policy import QQIngressFragment
from companion_daemon.world_v2.system_notice import SYSTEM_NOTICE_TEXT

OUTPUT = (REPO / "output" / "reaction-fix").resolve()
PRODUCTION_DB = drive.PRODUCTION_DB
WORLD_ID = drive.WORLD_ID
N_REACTION_TRIALS = 6


class CapturingChat:
    """Forward every provider call while recording inbound trigger packets."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.model = getattr(inner, "model", type(inner).__name__)
        self.captures: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def _record(self, messages: list[dict[str, Any]], raw: str) -> None:
        user = next((item for item in messages if item.get("role") == "user"), None)
        content = user.get("content") if isinstance(user, dict) else None
        trigger = None
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                trigger = parsed.get("current_trigger_message")
        if not isinstance(trigger, dict):
            return
        if not (
            trigger.get("reaction_refs")
            or trigger.get("sticker_refs")
            or trigger.get("reply_refs")
            or (
                trigger.get("text") is None
                and trigger.get("attachment_refs")
            )
        ):
            return
        self.captures.append(
            {
                "captured_at": datetime.now(UTC).isoformat(),
                "trigger": trigger,
                "system_excerpt": str(messages[0].get("content") if messages else "")[:1200],
                "raw_excerpt": raw[:4000],
            }
        )

    async def complete_json_with_usage(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        raw, usage = await self._inner.complete_json_with_usage(messages, **kwargs)
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
    }


async def run() -> dict[str, Any]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    clone = OUTPUT / "clone.sqlite"
    drive.clone_ledger(PRODUCTION_DB, clone)
    started_seq = drive.current_seq(clone)
    usage_from = drive.current_usage_id(clone)
    session, capturing = await _open_capturing_session(clone=clone)
    trials: list[dict[str, Any]] = []
    try:
        clock = await session.logical_time()
        for index in range(N_REACTION_TRIALS):
            when = clock + timedelta(seconds=3 + index * 4)
            seq_before = drive.current_seq(clone)
            captures_before = len(capturing.captures)
            inbound = await _inbound_fragment(
                session,
                QQIngressFragment(
                    source_event_id=f"reaction-fix-{time.time_ns()}",
                    recipient_id=session.recipient_id,
                    observed_at=when,
                    content_shape="reaction",
                    reaction_refs=("qq-face:74",),
                ),
            )
            rows = _event_rows(clone, after_seq=seq_before)
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
            trial = {
                "index": index,
                "inbound": inbound,
                "failure_codes": _failure_codes(rows),
                "timing_choices": _timing_choices(rows),
                "system_notices": notices,
                "her_text": her_text,
                "visible": inbound["visible"],
                "captures": capturing.captures[captures_before:],
                "event_types": [event.get("event_type") for _seq, event in rows],
            }
            trials.append(trial)
            (OUTPUT / f"trial-{index}.json").write_text(
                json.dumps(trial, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            clock = when

        sticker_when = clock + timedelta(seconds=4)
        seq_before = drive.current_seq(clone)
        sticker = await _inbound_fragment(
            session,
            QQIngressFragment(
                source_event_id=f"sticker-fix-{time.time_ns()}",
                recipient_id=session.recipient_id,
                observed_at=sticker_when,
                content_shape="sticker",
                sticker_ref="qq-sticker:sha256:" + "a" * 64,
            ),
        )
        sticker_rows = _event_rows(clone, after_seq=seq_before)
        control_when = sticker_when + timedelta(seconds=2)
        control = await _inbound_fragment(
            session,
            QQIngressFragment(
                source_event_id=f"control-fix-{time.time_ns()}",
                recipient_id=session.recipient_id,
                observed_at=control_when,
                content_shape="control",
                control_kind="typing_started",
            ),
        )
    finally:
        await session.close()

    cost = drive.cost_report(clone, since_id=usage_from)
    replied = sum(1 for item in trials if item["her_text"])
    silent = sum(
        1
        for item in trials
        if not item["her_text"] and "silent" in item["timing_choices"]
    )
    failed = sum(1 for item in trials if item["failure_codes"] or item["system_notices"])
    report = {
        "clone": str(clone),
        "started_seq": started_seq,
        "ended_seq": drive.current_seq(clone),
        "reaction_trials": trials,
        "sticker": {
            "inbound": sticker,
            "failure_codes": _failure_codes(sticker_rows),
            "timing_choices": _timing_choices(sticker_rows),
        },
        "control": control,
        "ratio": {
            "n": N_REACTION_TRIALS,
            "replied": replied,
            "silent": silent,
            "failed": failed,
        },
        "system_notice_triggered": any(item["system_notices"] for item in trials),
        "cost": cost,
    }
    (OUTPUT / "clone-run.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return report


def main() -> None:
    report = asyncio.run(run())
    ratio = report["ratio"]
    print(
        json.dumps(
            {
                "n": ratio["n"],
                "replied": ratio["replied"],
                "silent": ratio["silent"],
                "failed": ratio["failed"],
                "system_notice_triggered": report["system_notice_triggered"],
                "cost": report["cost"],
                "artifact": str(OUTPUT / "clone-run.json"),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    if ratio["failed"]:
        raise SystemExit("reaction inbound still failed on the clone")


if __name__ == "__main__":
    main()
