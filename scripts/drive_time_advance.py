#!/usr/bin/env python3
"""Advance at least three simulated days of background life on a ledger clone.

Copies the overnight production backup into ``output/time-advance/``. Never
writes ``data/``, never talks to 8787 or NapCat, never renders images, never
POSTs Civitai. Cost cap is ¥25.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta
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
    WORLD_ID,
    cost_report,
    current_seq,
    current_usage_id,
    event_type,
    events_after,
    open_session,
)

BACKUP = (
    REPO / "data" / "backups" / "companion.epoch2.pre-overnight-20260818T234617.sqlite"
).resolve()
OUTPUT = (REPO / "output" / "time-advance").resolve()
COST_CAP_CNY = 24.0
MAX_WAKES = 140
SIMULATED_SPAN = timedelta(hours=72)
FINISH_BUFFER = timedelta(hours=12)
WATCH_TYPES = {
    "ActivityPlanned",
    "ActivityStarted",
    "ActivityCompleted",
    "ActivityPaused",
    "ActivityResumed",
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
    "LifeArcChanged",
    "LifeArcClosed",
    "ActionAuthorized",
    "PrivateImpressionAccepted",
    "AppraisalAccepted",
    "AffectEpisodeOpened",
    "AffectEpisodeUpdated",
    "RelationshipSlowVariableAdjusted",
    "TriggerProcessOpened",
    "TriggerProcessCompleted",
    "ExternalPerceptionRecorded",
    "ModelResultRecorded",
    "TechnicalFailureRecorded",
    "CharacterInteriorTechnicalFailureRecorded",
}
_LOG = logging.getLogger("drive_time_advance")


def clone_backup(source: Path, target: Path) -> None:
    source = source.expanduser().resolve()
    target = target.expanduser().resolve()
    if source != BACKUP and "data/backups" not in str(source):
        raise SystemExit(f"refusing unknown backup source: {source}")
    if "output" not in str(target) or str(target).startswith(str((REPO / "data").resolve())):
        raise SystemExit(f"clone target must live under output/: {target}")
    if target == source:
        raise SystemExit("clone target must differ from source")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(target) + suffix).unlink(missing_ok=True)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("_payload")
    return payload if isinstance(payload, dict) else {}


def _process_kind(payload: dict[str, Any]) -> str:
    process = payload.get("process")
    if isinstance(process, dict) and process.get("process_kind"):
        return str(process["process_kind"])
    return str(payload.get("process_kind") or "")


def _action_kind(payload: dict[str, Any]) -> str:
    action = payload.get("action")
    if isinstance(action, dict) and action.get("kind"):
        return str(action["kind"])
    return str(payload.get("kind") or "")


def collect_events(database: Path, after_seq: int) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        rows = events_after(conn, WORLD_ID, after_seq)
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for seq, event in rows:
        kind = event_type(event)
        if kind not in WATCH_TYPES and not kind.startswith("Activity"):
            continue
        payload = _payload(event)
        out.append(
            {
                "seq": seq,
                "event_type": kind,
                "logical_time": event.get("logical_time"),
                "activity_kind": payload.get("activity_kind"),
                "plan_id": payload.get("plan_id"),
                "occurrence_id": payload.get("occurrence_id"),
                "opening_id": payload.get("opening_id"),
                "operation": payload.get("operation"),
                "process_kind": _process_kind(payload),
                "action_kind": _action_kind(payload),
                "reason_code": payload.get("reason_code"),
                "status": payload.get("status"),
                "model": payload.get("model"),
                "npc_id": (
                    (payload.get("npc") or {}).get("npc_id")
                    if isinstance(payload.get("npc"), dict)
                    else payload.get("npc_id")
                ),
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


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    types = Counter(item["event_type"] for item in events)
    process = Counter(
        item["process_kind"]
        for item in events
        if item["event_type"] == "TriggerProcessOpened" and item.get("process_kind")
    )
    actions = Counter(
        item["action_kind"]
        for item in events
        if item["event_type"] == "ActionAuthorized" and item.get("action_kind")
    )
    npcs = [
        {"seq": item["seq"], "npc_id": item.get("npc_id"), "logical_time": item["logical_time"]}
        for item in events
        if item["event_type"] == "NpcRegistered"
    ]
    arcs = [
        {
            "seq": item["seq"],
            "operation": item.get("operation"),
            "logical_time": item["logical_time"],
        }
        for item in events
        if item["event_type"] in {"LifeArcOpened", "LifeArcChanged", "LifeArcClosed"}
    ]
    photos = [item for item in events if item["event_type"] == "PhotoCandidateOpened"]
    evidence = [item for item in events if item["event_type"] == "ImageEvidenceDeclared"]
    farm_opened = process.get("private_impression_deliberation", 0)
    farm_accepted = types.get("PrivateImpressionAccepted", 0)
    proactive_opened = process.get("proactive_action_deliberation", 0)
    return {
        "counts": dict(types),
        "process_kinds": dict(process),
        "action_kinds": dict(actions),
        "activity_started": types.get("ActivityStarted", 0),
        "activity_completed": types.get("ActivityCompleted", 0),
        "activity_planned": types.get("ActivityPlanned", 0),
        "occurrences_settled": types.get("WorldOccurrenceSettled", 0),
        "life_arc_events": arcs,
        "npcs_registered": npcs,
        "image_evidence_declared": len(evidence),
        "photo_candidates": len(photos),
        "private_impression_asked": farm_opened,
        "private_impression_accepted": farm_accepted,
        "proactive_considerations": proactive_opened,
        "proactive_authorized": actions.get("proactive_message", 0),
        "external_perception_recorded": types.get("ExternalPerceptionRecorded", 0),
        "appraisals_accepted": types.get("AppraisalAccepted", 0),
        "affect_opened": types.get("AffectEpisodeOpened", 0),
        "relationship_adjustments": types.get("RelationshipSlowVariableAdjusted", 0),
    }


def _relationship_snapshot(projection: object | None) -> dict[str, Any] | None:
    if projection is None:
        return None
    states = getattr(projection, "relationship_states", ()) or ()
    if not states:
        return None
    state = states[0]
    dump = state.model_dump(mode="json") if hasattr(state, "model_dump") else {}
    slow = dump.get("slow_variables") if isinstance(dump, dict) else None
    return {
        "stage": dump.get("stage") or getattr(state, "stage", None),
        "temperature": dump.get("temperature") or getattr(state, "temperature", None),
        "slow_variables": slow,
    }


def _npc_snapshot(projection: object | None) -> list[dict[str, Any]]:
    if projection is None:
        return []
    out: list[dict[str, Any]] = []
    for item in getattr(projection, "npcs", ()) or ():
        out.append(
            {
                "npc_id": getattr(item, "npc_id", None),
                "status": getattr(item, "status", None),
                "location": getattr(item, "current_location_ref", None),
            }
        )
    return out


async def _next_due(session: Any) -> datetime | None:
    host = session.host
    reader = getattr(host, "life_ecology_next_due", None)
    if not callable(reader):
        inner = getattr(host, "_host", None)
        reader = getattr(inner, "life_ecology_next_due", None)
    if not callable(reader):
        return None
    due = await reader()
    return due if isinstance(due, datetime) else None


async def drive(
    *,
    source: Path,
    output_dir: Path,
    cost_cap: float,
    resume: bool = False,
) -> dict[str, Any]:
    clone = output_dir / "clone.sqlite"
    origin_path = output_dir / "drive-origin.txt"
    baseline_path = output_dir / "baseline.json"
    if resume:
        if not clone.exists():
            raise SystemExit(f"resume requested but clone missing: {clone}")
        if not baseline_path.exists():
            raise SystemExit(f"resume requested but baseline missing: {baseline_path}")
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        copy_seq = int(baseline["copy_seq"])
        usage_from = int(baseline["usage_from"])
        stored_bootstrap_seq = int(baseline.get("bootstrap_seq") or 0)
    else:
        clone_backup(source, clone)
        copy_seq = current_seq(clone)
        usage_from = current_usage_id(clone)
        stored_bootstrap_seq = 0
        baseline_path.write_text(
            json.dumps(
                {"copy_seq": copy_seq, "usage_from": usage_from},
                indent=2,
            ),
            encoding="utf-8",
        )
    session = await open_session(
        database=clone, output_dir=output_dir, enable_media=False
    )
    wakes: list[dict[str, Any]] = []
    try:
        now = await session.logical_time()
        if resume and origin_path.exists():
            origin = datetime.fromisoformat(origin_path.read_text().strip())
            if origin.tzinfo is None:
                origin = origin.replace(tzinfo=UTC)
        else:
            origin = now
            origin_path.write_text(origin.isoformat(), encoding="utf-8")
        deadline = origin + SIMULATED_SPAN
        finish_horizon = deadline + FINISH_BUFFER
        bootstrap_seq = stored_bootstrap_seq or current_seq(clone)
        if not resume or not stored_bootstrap_seq:
            bootstrap_seq = current_seq(clone)
            baseline_path.write_text(
                json.dumps(
                    {
                        "copy_seq": copy_seq,
                        "usage_from": usage_from,
                        "bootstrap_seq": bootstrap_seq,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        bootstrap_events = collect_events(clone, copy_seq)
        bootstrap_only = [item for item in bootstrap_events if item["seq"] <= bootstrap_seq]
        start_projection = session.projection()
        start_index = 0
        if resume:
            progress = output_dir / "wakes.jsonl"
            if progress.exists():
                start_index = sum(1 for _ in progress.read_text(encoding="utf-8").splitlines() if _)
        progress_path = output_dir / "wakes.jsonl"
        if not resume and progress_path.exists():
            progress_path.unlink()
        for index in range(start_index, MAX_WAKES):
            cost = cost_report(clone, since_id=usage_from)
            if float(cost["cost_cny"]) >= cost_cap:
                wakes.append({"stop": "cost_cap", "cost": cost, "index": index})
                break
            now = await session.logical_time()
            if now >= finish_horizon:
                wakes.append({"stop": "finish_horizon", "logical_time": now.isoformat()})
                break
            due = await _next_due(session)
            if due is None or due <= now:
                target = now + timedelta(minutes=20)
            else:
                target = due
            if target > finish_horizon:
                target = finish_horizon
            if target <= now:
                target = now + timedelta(seconds=90)
            try:
                tick = await session.tick_to(
                    target, reason=f"time-advance-{index:03d}", run_life=True
                )
                drains = await session.drain_loop(rounds=6, background=8)
            except Exception as exc:
                row = {
                    "index": index,
                    "tick_error": {"type": type(exc).__name__, "message": str(exc)[:800]},
                    "logical_time": now.isoformat(),
                    "cost_cny": cost_report(clone, since_id=usage_from)["cost_cny"],
                }
                wakes.append(row)
                progress_path.open("a", encoding="utf-8").write(
                    json.dumps(row, ensure_ascii=False, default=str) + "\n"
                )
                _LOG.exception("time-advance wake %s failed", index)
                continue
            after = await session.logical_time()
            row = {
                "index": index,
                "tick": tick,
                "due": due.isoformat() if isinstance(due, datetime) else None,
                "logical_time": after.isoformat(),
                "drain_tail": drains[-2:],
                "cost_cny": cost_report(clone, since_id=usage_from)["cost_cny"],
            }
            wakes.append(row)
            progress_path.open("a", encoding="utf-8").write(
                json.dumps(row, ensure_ascii=False, default=str) + "\n"
            )
            _LOG.info(
                "wake %s logical=%s cost=%s",
                index,
                after.isoformat(),
                row["cost_cny"],
            )
        end_projection = session.projection()
        simulated = collect_events(clone, bootstrap_seq)
        all_new = collect_events(clone, copy_seq)
        return {
            "status": "ran",
            "resumed": resume,
            "clone": str(clone),
            "source": str(source),
            "copy_seq": copy_seq,
            "bootstrap_seq": bootstrap_seq,
            "final_seq": current_seq(clone),
            "origin": origin.isoformat(),
            "deadline": deadline.isoformat(),
            "final_logical_time": (await session.logical_time()).isoformat(),
            "wake_count": len([item for item in wakes if "index" in item]),
            "wakes": wakes,
            "bootstrap": summarize(bootstrap_only),
            "simulated": summarize(simulated),
            "all_new": summarize(all_new),
            "npcs_after_bootstrap": _npc_snapshot(start_projection),
            "npcs_final": _npc_snapshot(end_projection),
            "relationship_after_bootstrap": _relationship_snapshot(start_projection),
            "relationship_final": _relationship_snapshot(end_projection),
            "captured_outbound": getattr(session.delivery, "sent", [])[-20:],
            "usage_purposes": _usage_purposes(clone, usage_from),
            "cost": cost_report(clone, since_id=usage_from),
        }
    except Exception as exc:
        return {
            "status": "error",
            "clone": str(clone),
            "error": {"type": type(exc).__name__, "message": str(exc)[:2000]},
            "cost": cost_report(clone, since_id=usage_from),
        }
    finally:
        await session.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cost-cap", type=float, default=COST_CAP_CNY)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--source", type=Path, default=BACKUP)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(
        drive(
            source=args.source.resolve(),
            output_dir=OUTPUT,
            cost_cap=args.cost_cap,
            resume=args.resume,
        )
    )
    (OUTPUT / "time-advance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": report.get("status"),
                "origin": report.get("origin"),
                "final_logical_time": report.get("final_logical_time"),
                "wake_count": report.get("wake_count"),
                "bootstrap": report.get("bootstrap"),
                "simulated": report.get("simulated"),
                "npcs_after_bootstrap": report.get("npcs_after_bootstrap"),
                "npcs_final": report.get("npcs_final"),
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
