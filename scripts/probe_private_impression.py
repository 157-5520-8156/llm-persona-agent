#!/usr/bin/env python3
"""Clone-only probe of the background private-impression farm.

Never writes data/, never talks to 8787 or NapCat, never restarts launchd.

Usage::

    .venv/bin/python scripts/probe_private_impression.py
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import importlib.util
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

OUTPUT = (REPO / "output" / "private-impression").resolve()
WORLD_ID = "world:companion-v2:qq-c2c:geoff"
_LOG = logging.getLogger("probe_private_impression")

_DRIVE_PATH = REPO / "scripts" / "drive_production_lanes.py"
_spec = importlib.util.spec_from_file_location("drive_production_lanes", _DRIVE_PATH)
drive = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["drive_production_lanes"] = drive
_spec.loader.exec_module(drive)


def _open_probe_session(database: Path, output_dir: Path):
    """Host session with tight, auditable farm gates for one clone run."""

    from companion_daemon.config import Settings
    from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host, qq_c2c_world_id

    if drive._is_production_path(database):
        raise SystemExit(f"refusing to open production ledger for write: {database}")
    sidecar = output_dir / f"{database.stem}.sidecars"
    sidecar.mkdir(parents=True, exist_ok=True)
    os.environ["WORLD_V2_PRIVATE_IMPRESSION_DAILY_MODEL_CALL_LIMIT"] = "2"
    os.environ["WORLD_V2_PRIVATE_IMPRESSION_MIN_INTERVAL_SECONDS"] = "90"
    os.environ["WORLD_V2_PRIVATE_IMPRESSION_IDLE_AFTER_USER_SECONDS"] = "30"
    settings = Settings(
        database_path=database,
        world_v2_external_perception_mode="off",
        world_v2_external_perception_sidecar_path=sidecar / "perception.sqlite",
        attachment_cache_path=sidecar / "attachments",
        world_v2_text_endpoint_enabled=False,
    )
    recipient_id = drive._recipient_id(settings)
    delivery = drive.CaptureDelivery()
    host = build_qq_c2c_host(
        settings=settings,
        recipient_id=recipient_id,
        bootstrap_at=datetime.now(UTC),
        delivery=delivery,
        model=None,
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
    return session, settings


def _impression_events(clone: Path, after_seq: int) -> list[dict[str, Any]]:
    conn = drive.open_ro(clone)
    try:
        rows = drive.events_after(conn, WORLD_ID, after_seq)
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for seq, event in rows:
        kind = drive.event_type(event)
        payload = event.get("_payload") or {}
        process = payload.get("process") if isinstance(payload.get("process"), dict) else {}
        if kind == "PrivateImpressionAccepted":
            impression = payload.get("impression") if isinstance(payload.get("impression"), dict) else {}
            out.append(
                {
                    "seq": seq,
                    "event_type": kind,
                    "reflection_summary": impression.get("reflection_summary")
                    or payload.get("reflection_summary"),
                    "decision": payload.get("reflection_decision"),
                    "confidence_bp": impression.get("confidence_bp"),
                    "logical_time": event.get("logical_time"),
                }
            )
            continue
        if kind == "TriggerProcessOpened" and process.get("process_kind") == "private_impression_deliberation":
            out.append(
                {
                    "seq": seq,
                    "event_type": kind,
                    "process_kind": process.get("process_kind"),
                    "trigger_id": process.get("trigger_id"),
                    "state": process.get("state"),
                    "logical_time": event.get("logical_time"),
                }
            )
            continue
        if kind == "TriggerProcessCompleted":
            trigger_id = str(payload.get("trigger_id") or "")
            outcome = str(payload.get("runtime_outcome_ref") or "")
            if "private-impression" in trigger_id or "impression:" in outcome:
                out.append(
                    {
                        "seq": seq,
                        "event_type": kind,
                        "trigger_id": trigger_id,
                        "runtime_outcome_ref": outcome,
                        "logical_time": event.get("logical_time"),
                    }
                )
    return out


def _usage_rows(clone: Path, since_id: int) -> list[dict[str, Any]]:
    conn = drive.open_ro(clone)
    try:
        _, _, items = drive.usage_sum_cny(conn, since_id=since_id)
    finally:
        conn.close()
    return items


def _gate_rows(clone: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{clone.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT world_id, local_day, trigger_id, reason, recorded_at, "
            "daily_calls, daily_limit FROM world_v2_private_impression_gates "
            "ORDER BY recorded_at"
        ).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _purpose_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in rows:
        purpose = str(item.get("purpose") or "")
        counts[purpose] = counts.get(purpose, 0) + 1
    return counts


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    source = drive.PRODUCTION_DB
    clone = OUTPUT / "farm.sqlite"
    drive.clone_ledger(source, clone)
    started_seq = drive.current_seq(clone)
    usage_from = drive.current_usage_id(clone)
    log_path = OUTPUT / "probe.log"
    log_handler = logging.FileHandler(log_path, encoding="utf-8")
    log_handler.setLevel(logging.INFO)
    logging.getLogger("companion_daemon.world_v2.private_impression_producer").addHandler(log_handler)
    logging.getLogger("companion_daemon.world_v2.private_impression_producer").setLevel(logging.INFO)

    session, settings = _open_probe_session(clone, OUTPUT)
    report: dict[str, Any] = {
        "clone": str(clone),
        "started_seq": started_seq,
        "settings": {
            "daily_model_call_limit": settings.world_v2_private_impression_daily_model_call_limit,
            "min_interval_seconds": settings.world_v2_private_impression_min_interval_seconds,
            "idle_after_user_seconds": settings.world_v2_private_impression_idle_after_user_seconds,
        },
        "inbound_messages": 0,
        "steps": [],
    }
    try:
        clock = await session.logical_time()
        report["logical_time_at_open"] = clock.isoformat()
        first = await session.drain_loop(rounds=8, background=12)
        after_first = _impression_events(clone, started_seq)
        usage_after_first = _usage_rows(clone, usage_from)
        report["steps"].append(
            {
                "name": "first_quiet_drain",
                "inbound": False,
                "logical_time": (await session.logical_time()).isoformat(),
                "drain": first,
                "impression_events": after_first,
                "usage": usage_after_first,
                "purpose_counts": _purpose_counts(usage_after_first),
            }
        )
        impression_calls = [
            item
            for item in usage_after_first
            if item.get("purpose") == "private_impression_reflection"
        ]
        if not impression_calls:
            later = clock + timedelta(seconds=120)
            await session.tick_to(later, reason="idle-for-impression", run_life=False)
            second_try = await session.drain_loop(rounds=8, background=12)
            after_first = _impression_events(clone, started_seq)
            usage_after_first = _usage_rows(clone, usage_from)
            impression_calls = [
                item
                for item in usage_after_first
                if item.get("purpose") == "private_impression_reflection"
            ]
            report["steps"].append(
                {
                    "name": "idle_tick_then_drain",
                    "inbound": False,
                    "drain": second_try,
                    "impression_events": after_first,
                    "usage": usage_after_first,
                    "purpose_counts": _purpose_counts(usage_after_first),
                }
            )

        clock = await session.logical_time()
        await session.tick_to(
            clock + timedelta(seconds=40),
            reason="inside-min-interval",
            run_life=False,
        )
        interval_drain = await session.drain_loop(rounds=4, background=8)
        usage_after_interval = _usage_rows(clone, usage_from)
        gates_after_interval = _gate_rows(clone)
        report["steps"].append(
            {
                "name": "inside_min_interval",
                "inbound": False,
                "drain": interval_drain,
                "impression_calls": [
                    item
                    for item in usage_after_interval
                    if item.get("purpose") == "private_impression_reflection"
                ],
                "gates": gates_after_interval,
            }
        )

        clock = await session.logical_time()
        await session.tick_to(
            clock + timedelta(seconds=100),
            reason="past-min-interval",
            run_life=False,
        )
        second_ask = await session.drain_loop(rounds=8, background=12)
        usage_after_second = _usage_rows(clone, usage_from)
        report["steps"].append(
            {
                "name": "second_ask_after_interval",
                "inbound": False,
                "drain": second_ask,
                "impression_events": _impression_events(clone, started_seq),
                "impression_calls": [
                    item
                    for item in usage_after_second
                    if item.get("purpose") == "private_impression_reflection"
                ],
                "gates": _gate_rows(clone),
            }
        )

        clock = await session.logical_time()
        await session.tick_to(
            clock + timedelta(seconds=100),
            reason="past-interval-at-cap",
            run_life=False,
        )
        cap_drain = await session.drain_loop(rounds=4, background=8)
        usage_final = _usage_rows(clone, usage_from)
        gates_final = _gate_rows(clone)
        impression_calls_final = [
            item
            for item in usage_final
            if item.get("purpose") == "private_impression_reflection"
        ]
        report["steps"].append(
            {
                "name": "daily_cap",
                "inbound": False,
                "drain": cap_drain,
                "impression_calls": impression_calls_final,
                "gates": gates_final,
            }
        )
        accepted = [
            item
            for item in _impression_events(clone, started_seq)
            if item.get("event_type") == "PrivateImpressionAccepted"
        ]
        no_ops = [
            item
            for item in _impression_events(clone, started_seq)
            if item.get("event_type") == "TriggerProcessCompleted"
            and str(item.get("runtime_outcome_ref") or "").endswith(":no-change")
        ]
        cost = drive.cost_report(clone, since_id=usage_from)
        report["accepted_impressions"] = accepted
        report["no_ops"] = no_ops
        report["gates"] = gates_final
        report["impression_model_calls"] = impression_calls_final
        report["cost"] = cost
        report["outcome"] = {
            "asked_without_inbound": bool(impression_calls_final),
            "produced_impression": bool(accepted),
            "hit_min_interval": any(
                item.get("reason") == "min_interval" for item in gates_final
            ),
            "hit_daily_cap": any(item.get("reason") == "daily_cap" for item in gates_final),
            "call_count": len(impression_calls_final),
        }
    except Exception as exc:
        report["status"] = "error"
        report["error"] = {"type": type(exc).__name__, "message": str(exc)[:4000]}
        _LOG.exception("private impression probe failed")
    else:
        report["status"] = "ran"
    finally:
        await session.close()

    (OUTPUT / "probe.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    try:
        import shutil

        shutil.copy2(clone, OUTPUT / "farm-acceptance.sqlite")
    except OSError:
        _LOG.exception("could not copy farm-acceptance.sqlite")
    _LOG.info("wrote %s", OUTPUT / "probe.json")
    print(json.dumps(report.get("outcome") or report.get("error"), ensure_ascii=False, indent=2))
    return 0 if report.get("status") == "ran" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
