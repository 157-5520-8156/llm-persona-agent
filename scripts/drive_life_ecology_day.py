#!/usr/bin/env python3
"""Advance one simulated day of Life Ecology on a production-ledger clone.

Never writes ``data/``, never talks to 8787 or NapCat, never renders images.
Artifacts go to ``output/life-ecology/``.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import sqlite3
import sys
from typing import Any

REPO = Path(__file__).resolve().parents[1]
for path in (REPO / "scripts", REPO / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from drive_production_lanes import (  # noqa: E402
    PRODUCTION_DB,
    WORLD_ID,
    clone_ledger,
    cost_report,
    current_seq,
    current_usage_id,
    event_type,
    events_after,
    open_session,
)

OUTPUT = (REPO / "output" / "life-ecology").resolve()
COST_CAP_CNY = 4.5
MAX_WAKES = 36
SIMULATED_SPAN = timedelta(hours=24)
LIFE_TYPES = {
    "ActivityPlanned",
    "ActivityStarted",
    "ActivityCompleted",
    "ActivityAbandoned",
    "WorldOccurrenceActivated",
    "WorldOccurrenceCommitted",
    "WorldOccurrenceSettled",
    "ExperienceCommitted",
    "ImageEvidenceDeclared",
    "RecipientScopedImageEvidenceDeclared",
    "PhotoCandidateOpened",
    "RandomDrawRecorded",
    "NpcRegistered",
    "NpcStatusChanged",
    "NpcStateChanged",
    "LifeArcOpened",
    "ModelResultRecorded",
}
_LOG = logging.getLogger("drive_life_ecology_day")


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("_payload")
    return payload if isinstance(payload, dict) else {}


def _life_events(database: Path, after_seq: int) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        rows = events_after(conn, WORLD_ID, after_seq)
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for seq, event in rows:
        kind = event_type(event)
        if kind not in LIFE_TYPES and not kind.startswith("Activity"):
            continue
        payload = _payload(event)
        model = payload.get("model")
        out.append(
            {
                "seq": seq,
                "event_type": kind,
                "event_id": event.get("event_id"),
                "logical_time": event.get("logical_time"),
                "activity_kind": payload.get("activity_kind"),
                "plan_id": payload.get("plan_id"),
                "occurrence_id": payload.get("occurrence_id"),
                "opening_id": payload.get("opening_id"),
                "source": payload.get("source") or event.get("source"),
                "model": model,
                "purpose": payload.get("purpose"),
            }
        )
    return out


def _usage_purposes(database: Path, since_id: int) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT purpose FROM world_v2_model_usage WHERE id > ?",
            (since_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()
    counts: Counter[str] = Counter()
    for row in rows:
        counts[str(row["purpose"] or "unknown")] += 1
    return dict(counts)


async def _next_due(session) -> datetime | None:
    host = session.host
    reader = getattr(host, "life_ecology_next_due", None)
    if not callable(reader):
        inner = getattr(host, "_host", None)
        reader = getattr(inner, "life_ecology_next_due", None)
    if not callable(reader):
        return None
    due = await reader()
    return due if isinstance(due, datetime) else None


def _summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    types = Counter(item["event_type"] for item in events)
    starts = [item for item in events if item["event_type"] == "ActivityStarted"]
    completes = [item for item in events if item["event_type"] == "ActivityCompleted"]
    settled = [item for item in events if item["event_type"] == "WorldOccurrenceSettled"]
    photos = [item for item in events if item["event_type"] == "PhotoCandidateOpened"]
    private_kinds = {
        "routine.morning_settle",
        "sleep.prepare_for_bed",
        "sleep.late_wind_down",
    }
    private_hits = [
        item
        for item in events
        if item.get("activity_kind") in private_kinds
        and item["event_type"] in {"ActivityStarted", "ActivityCompleted", "ActivityPlanned"}
    ]
    weighted = [
        item
        for item in events
        if item["event_type"] == "ModelResultRecorded"
        and str(item.get("model") or "").endswith("weighted-table")
    ]
    return {
        "counts": dict(types),
        "activity_started": starts,
        "activity_completed": completes,
        "settled_occurrences": settled,
        "photo_candidates": photos,
        "private_transition_hits": private_hits,
        "weighted_table_model_results": len(weighted),
        "full_cycle": bool(starts and completes and settled),
        "visual_alive": bool(photos),
    }


async def drive(
    *,
    source: Path,
    output_dir: Path,
    cost_cap: float,
    resume: bool = False,
) -> dict[str, Any]:
    clone = output_dir / "clone.sqlite"
    origin_path = output_dir / "drive-origin.txt"
    if resume:
        if not clone.exists():
            raise SystemExit(f"resume requested but clone missing: {clone}")
    else:
        clone_ledger(source, clone)
    baseline_seq = current_seq(source)
    usage_from = current_usage_id(source)
    session = await open_session(
        database=clone, output_dir=output_dir, enable_media=False
    )
    wakes: list[dict[str, Any]] = []
    try:
        now = await session.logical_time()
        if resume and origin_path.exists():
            origin = datetime.fromisoformat(origin_path.read_text().strip())
            if origin.tzinfo is None:
                from datetime import UTC

                origin = origin.replace(tzinfo=UTC)
        else:
            origin = now
            origin_path.write_text(origin.isoformat(), encoding="utf-8")
        deadline = origin + SIMULATED_SPAN
        finish_horizon = deadline + timedelta(hours=8)
        start_index = 0 if not resume else 100
        for index in range(start_index, start_index + MAX_WAKES):
            cost = cost_report(clone, since_id=usage_from)
            if float(cost["cost_cny"]) >= cost_cap:
                wakes.append({"stop": "cost_cap", "cost": cost})
                break
            now = await session.logical_time()
            events = _life_events(clone, baseline_seq)
            summary = _summarize(events)
            if summary["full_cycle"] and summary["visual_alive"] and now >= deadline:
                wakes.append({"stop": "acceptance_and_day", "logical_time": now.isoformat()})
                break
            if now >= finish_horizon:
                wakes.append({"stop": "finish_horizon", "logical_time": now.isoformat()})
                break
            due = await _next_due(session)
            if due is None or due <= now:
                target = now + timedelta(minutes=15)
            else:
                target = due
            if target > finish_horizon:
                target = finish_horizon
            if target <= now:
                target = now + timedelta(seconds=90)
            try:
                tick = await session.tick_to(
                    target, reason=f"life-ecology-{index:02d}", run_life=True
                )
                drains = await session.drain_loop(rounds=4, background=6)
            except Exception as exc:
                wakes.append(
                    {
                        "index": index,
                        "tick_error": {"type": type(exc).__name__, "message": str(exc)[:800]},
                        "logical_time": now.isoformat(),
                        "cost_cny": cost_report(clone, since_id=usage_from)["cost_cny"],
                    }
                )
                _LOG.exception("life ecology drive wake %s failed", index)
                continue
            after = await session.logical_time()
            wakes.append(
                {
                    "index": index,
                    "tick": tick,
                    "due": due.isoformat() if isinstance(due, datetime) else None,
                    "logical_time": after.isoformat(),
                    "drain_tail": drains[-2:],
                    "cost_cny": cost_report(clone, since_id=usage_from)["cost_cny"],
                }
            )
        events = _life_events(clone, baseline_seq)
        summary = _summarize(events)
        return {
            "status": "ran",
            "resumed": resume,
            "clone": str(clone),
            "origin": origin.isoformat(),
            "final_logical_time": (await session.logical_time()).isoformat(),
            "wakes": wakes,
            "summary": summary,
            "events": events,
            "usage_purposes": _usage_purposes(clone, usage_from),
            "cost": cost_report(clone, since_id=usage_from),
        }
    except Exception as exc:
        return {
            "status": "error",
            "clone": str(clone),
            "error": {"type": type(exc).__name__, "message": str(exc)[:2000]},
            "events": _life_events(clone, baseline_seq),
            "cost": cost_report(clone, since_id=usage_from),
        }
    finally:
        await session.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cost-cap", type=float, default=COST_CAP_CNY)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(
        drive(
            source=PRODUCTION_DB,
            output_dir=OUTPUT,
            cost_cap=args.cost_cap,
            resume=args.resume,
        )
    )
    (OUTPUT / "day-drive.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": report.get("status"),
                "origin": report.get("origin"),
                "final_logical_time": report.get("final_logical_time"),
                "wake_count": len(report.get("wakes") or []),
                "summary": report.get("summary"),
                "cost": report.get("cost"),
                "usage_purposes": report.get("usage_purposes"),
                "error": report.get("error"),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
