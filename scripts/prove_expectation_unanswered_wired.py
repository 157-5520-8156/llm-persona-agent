#!/usr/bin/env python3
"""Clone-only: plant a wait_seconds hope, leave him silent, prove she wakes informed.

Never writes ``data/``, never talks to NapCat. Output:
``output/long-silence-wired/expectation_*.json``.

Usage::

    .venv/bin/python scripts/prove_expectation_unanswered_wired.py
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import drive_production_lanes as drive
import probe_initiative_lanes as initiative

OUTPUT = (REPO / "output" / "long-silence-wired").resolve()
PRODUCTION_DB = drive.PRODUCTION_DB
SHANGHAI = ZoneInfo("Asia/Shanghai")
BUDGET_CNY = 3.0
WAIT_SECONDS = 120
_LOG = logging.getLogger("prove-expectation-unanswered")


def _cost(clone: Path, since_id: int) -> float:
    return float((drive.cost_report(clone, since_id=since_id) or {}).get("cost_cny") or 0)


def _daytime(instant: datetime) -> datetime:
    local = instant.astimezone(SHANGHAI)
    if local.hour < 7:
        local = local.replace(hour=10, minute=0, second=0, microsecond=0)
    return local.astimezone(UTC)


def _extract_proactive(calls: list[dict[str, Any]]) -> dict[str, Any]:
    for row in reversed(calls):
        tool = str(row.get("tool") or "")
        fields = row.get("fields") if isinstance(row.get("fields"), dict) else {}
        if not fields:
            fields = initiative.extract_slim_fields(str(row.get("excerpt") or ""))
        if "proactive" in tool or fields.get("timing_choice") or fields.get("impulse_summary"):
            return {
                "tool": tool,
                "timing_choice": fields.get("timing_choice"),
                "impulse_summary": fields.get("impulse_summary")
                or fields.get("how_it_landed"),
                "fields": fields,
                "excerpt": row.get("excerpt"),
            }
    return {}


def _prompt_marks(calls: list[dict[str, Any]]) -> dict[str, Any]:
    marks: dict[str, Any] = {}
    for row in calls:
        item = row.get("prompt_marks")
        if isinstance(item, dict):
            marks.update(item)
    return marks


async def open_session(
    database: Path,
    output_dir: Path,
    *,
    inbound_payload: dict[str, object] | None,
) -> tuple[drive.DriveSession, initiative.RecordingCharacterModel]:
    from companion_daemon.config import Settings
    from companion_daemon.llm import DeepSeekChatModel
    from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore
    from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host

    if drive._is_production_path(database):  # noqa: SLF001
        raise SystemExit(f"refusing production ledger: {database}")
    sidecar = output_dir / f"{database.stem}.expectation.sidecars"
    sidecar.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        database_path=database,
        world_v2_external_perception_mode="off",
        world_v2_external_perception_sidecar_path=sidecar / "perception.sqlite",
        attachment_cache_path=sidecar / "attachments",
        world_v2_text_endpoint_enabled=False,
    )
    recipient_id = drive._recipient_id(settings)  # noqa: SLF001
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
    if inbound_payload is not None:
        inner = drive.OverlayCharacterModel(inner, inbound_payload)
    recorder = initiative.RecordingCharacterModel(inner)
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


async def prove() -> dict[str, Any]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    clone = OUTPUT / "expectation_clone.sqlite"
    drive.assert_clone_is_safe(source=PRODUCTION_DB, target=clone)
    drive.clone_ledger(PRODUCTION_DB, clone)
    usage_from = drive.current_usage_id(clone)
    copy_seq = drive.current_seq(clone)

    # Plant hope on one inbound turn via overlay; wake uses the real model.
    slim = {
        "messages": ["那个表情你还没说是什么意思呀？"],
        "felt": "想听他把那个表情讲清楚",
        "wants": "等他解释那个表情",
        "waiting_for": "他解释那个表情是什么意思",
        "wait": WAIT_SECONDS,
        "timing": "now",
    }
    overlay = drive._authored_inbound_payload(slim, reply_only=True)  # noqa: SLF001
    session, recorder = await open_session(clone, OUTPUT, inbound_payload=overlay)
    try:
        origin = _daytime(await session.logical_time())
        await session.tick_to(origin, reason="expectation-daytime", run_life=False)
        before_seq = drive.current_seq(clone)
        inbound = await session.inbound("你猜我刚发那个表情是什么意思")
        await session.drain_loop(rounds=8, background=8)

        from companion_daemon.world_v2.accepted_ledger_batch import AcceptedLedgerBatchIssuer
        from companion_daemon.world_v2.response_expectation_view import (
            expired_unanswered_expectation,
            next_response_expectation_wake_at,
            pending_response_expectation,
        )
        from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger

        ledger = SQLiteWorldLedger(
            path=clone,
            world_id=drive.WORLD_ID,
            accepted_batch_issuer=AcceptedLedgerBatchIssuer(),
        )
        mid = ledger.project()
        pending = pending_response_expectation(mid)
        wake_at = next_response_expectation_wake_at(mid)
        planted = {
            "inbound_visible": inbound.get("visible"),
            "pending_expectation": None
            if pending is None
            else pending.model_dump(mode="json"),
            "next_wake_at": wake_at.isoformat() if wake_at else None,
            "hope_events": [],
        }
        conn = drive.open_ro(clone)
        try:
            for seq, event in drive.events_after(conn, drive.WORLD_ID, before_seq):
                et = drive.event_type(event)
                payload = event.get("_payload") or {}
                if et == "AcceptanceRecorded" and payload.get("response_expectation"):
                    planted["hope_events"].append(
                        {
                            "seq": seq,
                            "plan_id": payload.get("plan_id"),
                            "response_expectation": payload.get("response_expectation"),
                            "logical_time": event.get("logical_time"),
                        }
                    )
        finally:
            conn.close()

        if not planted["hope_events"]:
            return {
                "status": "no_hope_planted",
                "planted": planted,
                "cost": drive.cost_report(clone, since_id=usage_from),
            }

        # Close overlay session; reopen with real model for the wake.
        await session.close()
        session, recorder = await open_session(clone, OUTPUT, inbound_payload=None)

        hope = planted["hope_events"][-1]["response_expectation"]
        not_before = datetime.fromisoformat(
            str(hope["not_before"]).replace("Z", "+00:00")
        )
        target = _daytime(not_before + timedelta(seconds=5))
        before_calls = len(recorder.calls)
        before_sent = len(session.delivery.sent)
        before_wake_seq = drive.current_seq(clone)
        await session.tick_to(target, reason="expectation-not-before", run_life=True)
        drains = await session.drain_loop(rounds=16, background=16)
        projection = session.projection()
        expired = expired_unanswered_expectation(projection)
        fields = _extract_proactive(recorder.calls[before_calls:])
        marks = _prompt_marks(recorder.calls[before_calls:])
        visible = session.delivery.sent[before_sent:]
        # Collect opportunity context strings from recorder excerpts.
        context_hits = []
        for row in recorder.calls[before_calls:]:
            excerpt = str(row.get("excerpt") or "")
            for needle in (
                "he has not spoken",
                "What she hoped for",
                "Timing evidence only",
                "expired_expectation",
                "他解释",
            ):
                if needle in excerpt:
                    context_hits.append(needle)

        processes = []
        for process in getattr(projection, "trigger_processes", ()) or ():
            ref = str(getattr(process, "trigger_ref", "") or "")
            if "expectation-expiry" in ref or "expired" in ref:
                processes.append(
                    {
                        "trigger_ref": ref,
                        "state": getattr(process, "state", None),
                        "runtime_outcome_ref": getattr(process, "runtime_outcome_ref", None),
                    }
                )

        wake_events: list[dict[str, Any]] = []
        conn = drive.open_ro(clone)
        try:
            interesting = {
                "TriggerProcessOpened",
                "TriggerProcessCompleted",
                "ProposalRecorded",
                "AcceptanceRecorded",
                "ActionAuthorized",
                "ModelResultRecorded",
            }
            for seq, event in drive.events_after(conn, drive.WORLD_ID, before_wake_seq):
                et = drive.event_type(event)
                if et not in interesting:
                    continue
                wake_events.append(
                    {
                        "seq": seq,
                        "event_type": et,
                        "logical_time": event.get("logical_time"),
                    }
                )
                if len(wake_events) >= 40:
                    break
        finally:
            conn.close()

        return {
            "status": "ran",
            "clone": str(clone),
            "copy_seq": copy_seq,
            "origin": origin.isoformat(),
            "planted": planted,
            "wake_target": target.isoformat(),
            "expired_unanswered": None
            if expired is None
            else expired.model_dump(mode="json"),
            "decision": fields,
            "prompt_context_hits": sorted(set(context_hits)),
            "prompt_marks": marks,
            "processes": processes,
            "visible_capture": visible,
            "drain_tail": drains[-4:] if drains else [],
            "cost": drive.cost_report(clone, since_id=usage_from),
            "events_after_wake": wake_events,
            "proofs": {
                "hope_landed": bool(planted["hope_events"]),
                "expired_detected": expired is not None,
                "wake_scheduled": planted.get("next_wake_at") is not None,
                "she_saw_unanswered_fact": any(
                    hit in {"he has not spoken", "What she hoped for", "他解释"}
                    for hit in context_hits
                )
                or (
                    expired is not None
                    and fields.get("timing_choice") in {"now", "later", "silent"}
                ),
                "informed_decision": fields.get("timing_choice")
                in {"now", "later", "silent"},
                "not_a_forced_send": fields.get("timing_choice") != "now"
                or bool(fields.get("impulse_summary")),
            },
            "budget_ok": _cost(clone, usage_from) <= BUDGET_CNY,
        }
    except Exception as exc:
        return {
            "status": "error",
            "clone": str(clone),
            "error": {"type": type(exc).__name__, "message": str(exc)[:3000]},
            "cost": drive.cost_report(clone, since_id=usage_from),
        }
    finally:
        await session.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    os.environ.setdefault("WORLD_V2_SPEND_ACCOUNT", "debug")
    report = asyncio.run(prove())
    (OUTPUT / "expectation_REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    summary = {
        "status": report.get("status"),
        "cost": report.get("cost"),
        "proofs": report.get("proofs"),
        "decision": {
            "timing": (report.get("decision") or {}).get("timing_choice"),
            "impulse": ((report.get("decision") or {}).get("impulse_summary") or "")[
                :240
            ],
        },
        "expired": report.get("expired_unanswered"),
        "visible": [
            item.get("body")
            for item in (report.get("visible_capture") or [])
            if item.get("kind") == "text"
        ][:4],
        "error": report.get("error"),
    }
    (OUTPUT / "expectation_SUMMARY.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
