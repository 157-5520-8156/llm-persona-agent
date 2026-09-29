#!/usr/bin/env python3
"""Clone-only proof: S18 long_silence + situation-independent outreach.

Never writes ``data/``, never talks to NapCat / 8787. Output:
``output/long-silence-wired/``.

Usage::

    .venv/bin/python scripts/prove_long_silence_wired.py
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
BUDGET_CNY = 8.0
TARGET_DECISIONS = 4
MAX_WAKES = 48
_LOG = logging.getLogger("prove-long-silence")


def _cost(clone: Path, since_id: int) -> float:
    return float((drive.cost_report(clone, since_id=since_id) or {}).get("cost_cny") or 0)


def _dump(item: object) -> Any:
    if item is None or isinstance(item, (dict, list, str, int, float, bool)):
        return item
    dump = getattr(item, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    iso = getattr(item, "isoformat", None)
    if callable(iso):
        return iso()
    return str(item)[:800]


async def open_session(
    database: Path, output_dir: Path
) -> tuple[drive.DriveSession, initiative.RecordingCharacterModel]:
    from companion_daemon.config import Settings
    from companion_daemon.llm import DeepSeekChatModel
    from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore
    from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host, qq_c2c_world_id

    if drive._is_production_path(database):  # noqa: SLF001
        raise SystemExit(f"refusing production ledger: {database}")
    sidecar = output_dir / f"{database.stem}.sidecars"
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


async def _initiative_due(session: drive.DriveSession) -> datetime | None:
    host = session.host
    for obj in (host, getattr(host, "_host", None)):
        reader = getattr(obj, "social_initiative_next_due", None)
        if callable(reader):
            due = await reader()
            return due if isinstance(due, datetime) else None
    return None


def _daytime(instant: datetime) -> datetime:
    local = instant.astimezone(SHANGHAI)
    if local.hour < 7:
        local = local.replace(hour=10, minute=0, second=0, microsecond=0)
    return local.astimezone(UTC)


def _extract_proactive_fields(calls: list[dict[str, Any]]) -> dict[str, Any]:
    for row in reversed(calls):
        tool = str(row.get("tool") or "")
        if "proactive" not in tool and "character_role_proactive" not in tool:
            fields = row.get("fields") if isinstance(row.get("fields"), dict) else {}
            if not fields.get("timing_choice") and not fields.get("impulse_summary"):
                continue
        fields = row.get("fields") if isinstance(row.get("fields"), dict) else {}
        if not fields:
            fields = initiative.extract_slim_fields(str(row.get("excerpt") or ""))
        timing = fields.get("timing_choice")
        impulse = fields.get("impulse_summary") or fields.get("how_it_landed")
        if timing or impulse:
            return {
                "tool": tool,
                "timing_choice": timing,
                "impulse_summary": impulse,
                "excerpt": row.get("excerpt"),
                "fields": fields,
            }
    return {}


def _shared_process_snapshot(projection) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for process in getattr(projection, "trigger_processes", ()) or ():
        if getattr(process, "process_kind", None) != "proactive_action_deliberation":
            continue
        ref = str(getattr(process, "trigger_ref", "") or "")
        if "long-silence" not in ref and "situation-independent" not in ref:
            continue
        out.append(
            {
                "trigger_ref": ref,
                "state": getattr(process, "state", None),
                "runtime_outcome_ref": getattr(process, "runtime_outcome_ref", None),
                "source_evidence_ref": getattr(process, "source_evidence_ref", None),
            }
        )
    return out


def _new_actions(clone: Path, *, after_seq: int) -> list[dict[str, Any]]:
    conn = drive.open_ro(clone)
    try:
        rows = drive.events_after(conn, drive.WORLD_ID, after_seq)
    finally:
        conn.close()
    interesting = {
        "ActionAuthorized",
        "ActionDispatchStarted",
        "ActionDelivered",
        "TriggerProcessOpened",
        "TriggerProcessCompleted",
        "ProposalRecorded",
        "ModelResultRecorded",
        "RandomDrawRecorded",
        "ProactiveOpportunityDecisionRecorded",
    }
    out: list[dict[str, Any]] = []
    for seq, event in rows:
        kind = drive.event_type(event)
        if kind not in interesting:
            continue
        payload = event.get("_payload") if isinstance(event, dict) else {}
        if not isinstance(payload, dict):
            payload = {}
        action = payload.get("action") if isinstance(payload.get("action"), dict) else {}
        process = payload.get("process") if isinstance(payload.get("process"), dict) else {}
        out.append(
            {
                "seq": seq,
                "event_type": kind,
                "logical_time": event.get("logical_time"),
                "action_kind": action.get("kind"),
                "action_id": action.get("action_id") or payload.get("action_id"),
                "process_kind": process.get("process_kind") or payload.get("process_kind"),
                "trigger_ref": process.get("trigger_ref") or payload.get("trigger_ref"),
                "runtime_outcome_ref": process.get("runtime_outcome_ref")
                or payload.get("runtime_outcome_ref"),
                "disposition": payload.get("disposition"),
                "source_kind": payload.get("source_kind"),
            }
        )
    return out


async def prove() -> dict[str, Any]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    clone = OUTPUT / "clone.sqlite"
    drive.assert_clone_is_safe(source=PRODUCTION_DB, target=clone)
    drive.clone_ledger(PRODUCTION_DB, clone)
    usage_from = drive.current_usage_id(clone)
    copy_seq = drive.current_seq(clone)
    session, recorder = await open_session(clone, OUTPUT)
    decisions: list[dict[str, Any]] = []
    wakes: list[dict[str, Any]] = []
    try:
        origin = await session.logical_time()
        deadline = origin + timedelta(hours=84)
        # Jump past the short ambient window first so S18 can open.
        first_target = _daytime(origin + timedelta(hours=20))
        await session.tick_to(first_target, reason="past-ambient", run_life=True)
        await session.drain_loop(rounds=6, background=12)

        wake_index = 0
        while (
            len(decisions) < TARGET_DECISIONS
            and wake_index < MAX_WAKES
            and _cost(clone, usage_from) < BUDGET_CNY
        ):
            wake_index += 1
            now = await session.logical_time()
            if now >= deadline:
                break
            due = await _initiative_due(session)
            if due is None or due <= now:
                # Nudge forward; life ticks may mint situation-independent.
                due = _daytime(now + timedelta(hours=6))
            target = _daytime(max(due, now + timedelta(minutes=5)))
            if target > deadline:
                break
            before_seq = drive.current_seq(clone)
            before_calls = len(recorder.calls)
            before_sent = len(session.delivery.sent)
            tick = await session.tick_to(
                target, reason=f"s18-wake-{wake_index}", run_life=True
            )
            drains = await session.drain_loop(rounds=14, background=16)
            projection = session.projection()
            fields = _extract_proactive_fields(recorder.calls[before_calls:])
            events = _new_actions(clone, after_seq=before_seq)
            shared = _shared_process_snapshot(projection)
            authorized = [
                item
                for item in events
                if item["event_type"] == "ActionAuthorized"
                and item.get("action_kind") in {"proactive_message", "followup"}
            ]
            visible = session.delivery.sent[before_sent:]
            row = {
                "wake": wake_index,
                "tick": tick,
                "logical_time": (await session.logical_time()).isoformat(),
                "initiative_due": due.isoformat() if isinstance(due, datetime) else None,
                "decision": fields,
                "shared_processes": shared,
                "authorized_actions": authorized,
                "visible_capture": visible,
                "event_summary": events[-12:],
                "drain_tail": drains[-3:] if drains else [],
                "cost_cny": _cost(clone, usage_from),
            }
            wakes.append(row)
            timing = fields.get("timing_choice")
            if timing in {"now", "later", "silent"} or authorized or any(
                "long-silence" in str(p.get("trigger_ref"))
                or "situation-independent" in str(p.get("trigger_ref"))
                for p in shared
            ):
                decisions.append(row)
                _LOG.info(
                    "decision#%s timing=%s authorized=%s cost=%.4f",
                    len(decisions),
                    timing,
                    bool(authorized),
                    row["cost_cny"],
                )
            # After a shared-budget consider, jump toward next local day to
            # respect daily limit while still collecting more samples.
            if shared and any(p.get("state") == "terminal" for p in shared):
                jump = _daytime((await session.logical_time()) + timedelta(hours=26))
                await session.tick_to(jump, reason=f"s18-day-hop-{wake_index}", run_life=True)
                await session.drain_loop(rounds=4, background=8)

        # Explicit daily-limit probe on a fresh hop the same local day as a
        # completed shared consideration, if we have one.
        limit_probe: dict[str, Any] | None = None
        projection = session.projection()
        shared_now = _shared_process_snapshot(projection)
        if shared_now:
            same_day = _daytime(await session.logical_time())
            before_seq = drive.current_seq(clone)
            await session.tick_to(
                same_day + timedelta(hours=1),
                reason="s18-limit-probe",
                run_life=False,
            )
            await session.drain_loop(rounds=8, background=8)
            after_shared = _shared_process_snapshot(session.projection())
            new_opens = [
                item
                for item in _new_actions(clone, after_seq=before_seq)
                if item["event_type"] == "TriggerProcessOpened"
                and (
                    "long-silence" in str(item.get("trigger_ref") or "")
                    or "situation-independent" in str(item.get("trigger_ref") or "")
                )
            ]
            limit_probe = {
                "shared_before": len(shared_now),
                "shared_after": len(after_shared),
                "new_shared_opens_same_day": new_opens,
                "blocked": len(new_opens) == 0,
            }

        speak = [d for d in decisions if d.get("decision", {}).get("timing_choice") == "now"]
        silent = [
            d for d in decisions if d.get("decision", {}).get("timing_choice") == "silent"
        ]
        speak_authorized = [
            d for d in speak if d.get("authorized_actions") or d.get("visible_capture")
        ]
        timings = [
            d.get("decision", {}).get("timing_choice")
            for d in decisions
            if d.get("decision", {}).get("timing_choice")
        ]
        impulses = [
            d.get("decision", {}).get("impulse_summary")
            for d in decisions
            if d.get("decision", {}).get("impulse_summary")
        ]
        return {
            "status": "ran",
            "clone": str(clone),
            "copy_seq": copy_seq,
            "origin": origin.isoformat(),
            "final_logical_time": (await session.logical_time()).isoformat(),
            "cost": drive.cost_report(clone, since_id=usage_from),
            "decision_count": len(decisions),
            "decisions": decisions,
            "wakes": wakes,
            "limit_probe": limit_probe,
            "proofs": {
                "got_opportunity": len(decisions) >= 1,
                "speak_example": bool(speak),
                "silent_example": bool(silent),
                "speak_reached_send": bool(speak_authorized),
                "daily_limit_blocked": bool(
                    limit_probe and limit_probe.get("blocked")
                ),
                "not_a_timer": len(set(timings)) > 1 or len(set(impulses)) > 1,
                "n_decisions": len(decisions),
            },
            "captured_outbound_tail": session.delivery.sent[-12:],
        }
    except Exception as exc:
        return {
            "status": "error",
            "clone": str(clone),
            "error": {"type": type(exc).__name__, "message": str(exc)[:3000]},
            "cost": drive.cost_report(clone, since_id=usage_from),
            "decisions": decisions,
            "wakes": wakes,
        }
    finally:
        await session.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    # Force debug spend account for clones under output/.
    os.environ.setdefault("WORLD_V2_SPEND_ACCOUNT", "debug")
    report = asyncio.run(prove())
    (OUTPUT / "REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    proofs = report.get("proofs") or {}
    summary = {
        "status": report.get("status"),
        "cost": report.get("cost"),
        "decision_count": report.get("decision_count"),
        "proofs": proofs,
        "limit_probe": report.get("limit_probe"),
        "error": report.get("error"),
        "decision_timings": [
            {
                "timing": d.get("decision", {}).get("timing_choice"),
                "impulse": (d.get("decision", {}).get("impulse_summary") or "")[:200],
                "authorized": bool(d.get("authorized_actions")),
                "visible": [
                    item.get("body")
                    for item in (d.get("visible_capture") or [])
                    if item.get("kind") == "text"
                ][:3],
            }
            for d in (report.get("decisions") or [])
        ],
    }
    (OUTPUT / "SUMMARY.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
