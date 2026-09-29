#!/usr/bin/env python3
"""Read-only: can production world facts source a present-moment photo?

Never writes ``data/``. Uses ``sqlite3.connect(..., uri=True) + mode=ro``.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
import json
import sqlite3
import sys
from typing import Any
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

PRODUCTION_DB = (REPO / "data" / "companion.epoch2.sqlite").resolve()
WORLD_ID = "world:companion-v2:qq-c2c:geoff"
OUTPUT = (REPO / "output" / "present-moment-photo").resolve()
SHANGHAI = ZoneInfo("Asia/Shanghai")

INTERESTING = {
    "ActivityPlanned",
    "ActivityStarted",
    "ActivityPaused",
    "ActivityResumed",
    "ActivityCompleted",
    "ActivityAbandoned",
    "WorldOccurrenceSettled",
    "WorldOccurrenceActivated",
    "ImageEvidenceDeclared",
    "RecipientScopedImageEvidenceDeclared",
    "AppearanceStateRecorded",
    "VisiblePhysicalStateRecorded",
    "VisualFactRecorded",
    "FactCommitted",
    "FactCorrected",
    "FactCommitMaterializedV2",
    "ClockAdvanced",
    "BiographicalTimelineConfigured",
    "LifeArcChanged",
    "LifeArcOpened",
    "PhotoCandidateOpened",
    "ProviderMediaGrantRecorded",
}


def parse_event(raw: str | bytes) -> dict[str, Any]:
    event = json.loads(raw)
    payload = event.get("payload")
    if isinstance(payload, str):
        try:
            event["_payload"] = json.loads(payload)
        except json.JSONDecodeError:
            event["_payload"] = {}
    elif isinstance(payload, dict):
        event["_payload"] = payload
    else:
        payload_json = event.get("payload_json")
        if isinstance(payload_json, str):
            try:
                event["_payload"] = json.loads(payload_json)
            except json.JSONDecodeError:
                event["_payload"] = {}
        else:
            event["_payload"] = payload_json if isinstance(payload_json, dict) else {}
    return event


def event_type(event: dict[str, Any]) -> str:
    return str(event.get("event_type") or event["_payload"].get("event_type") or "")


def as_dt(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed


def brief(obj: object, *, limit: int = 240) -> object:
    text = json.dumps(obj, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return obj
    if isinstance(obj, dict):
        return {key: brief(value, limit=80) for key, value in list(obj.items())[:20]}
    if isinstance(obj, list):
        return [brief(item, limit=80) for item in obj[:8]]
    return text[:limit] + "…"


def main() -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{PRODUCTION_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        head = conn.execute(
            "SELECT MAX(ledger_sequence) FROM world_v2_events WHERE world_id = ?",
            (WORLD_ID,),
        ).fetchone()[0]
        types = Counter()
        interesting: list[dict[str, Any]] = []
        clocks: list[dict[str, Any]] = []
        activities: list[dict[str, Any]] = []
        facts: list[dict[str, Any]] = []
        last_obs: dict[str, Any] | None = None
        last_proposal: dict[str, Any] | None = None
        rows = conn.execute(
            "SELECT ledger_sequence, event_json FROM world_v2_events "
            "WHERE world_id = ? ORDER BY ledger_sequence",
            (WORLD_ID,),
        )
        for seq, raw in rows:
            event = parse_event(raw)
            kind = event_type(event)
            types[kind] += 1
            payload = event.get("_payload") or {}
            record = {
                "seq": seq,
                "event_type": kind,
                "event_id": event.get("event_id"),
                "logical_time": event.get("logical_time"),
                "payload": payload,
            }
            if kind == "ClockAdvanced":
                clocks.append(
                    {
                        "seq": seq,
                        "logical_time": event.get("logical_time"),
                        "to": payload.get("to") or payload.get("logical_time") or payload.get("now"),
                        "keys": sorted(payload.keys())[:20],
                    }
                )
            if kind.startswith("Activity") or kind.startswith("Plan"):
                activities.append(
                    {
                        "seq": seq,
                        "event_type": kind,
                        "logical_time": event.get("logical_time"),
                        "event_id": event.get("event_id"),
                        "plan_id": payload.get("plan_id")
                        or (payload.get("plan") or {}).get("plan_id")
                        if isinstance(payload.get("plan"), dict)
                        else payload.get("plan_id"),
                        "status": payload.get("status")
                        or (payload.get("plan") or {}).get("status")
                        if isinstance(payload.get("plan"), dict)
                        else None,
                        "activity_kind": payload.get("activity_kind")
                        or (payload.get("plan") or {}).get("activity_kind")
                        if isinstance(payload.get("plan"), dict)
                        else None,
                        "location_ref": payload.get("location_ref")
                        or (payload.get("plan") or {}).get("location_ref")
                        if isinstance(payload.get("plan"), dict)
                        else None,
                        "privacy_class": payload.get("privacy_class"),
                        "payload_keys": sorted(payload.keys()),
                    }
                )
            if kind in {"FactCommitted", "FactCorrected", "FactCommitMaterializedV2", "VisualFactRecorded"}:
                values = payload.get("values") if isinstance(payload.get("values"), dict) else payload
                facts.append(
                    {
                        "seq": seq,
                        "event_type": kind,
                        "logical_time": event.get("logical_time"),
                        "predicate": values.get("predicate_code") if isinstance(values, dict) else None,
                        "subject": values.get("subject_ref") if isinstance(values, dict) else None,
                        "status": values.get("status") if isinstance(values, dict) else None,
                        "visibility": values.get("visibility")
                        or payload.get("visibility")
                        if isinstance(values, dict)
                        else payload.get("visibility"),
                        "payload_keys": sorted(payload.keys()),
                    }
                )
            if kind == "ObservationRecorded":
                last_obs = {
                    "seq": seq,
                    "logical_time": event.get("logical_time"),
                    "text": (payload.get("text") or payload.get("body") or "")[:160],
                }
            if kind == "ProposalRecorded":
                last_proposal = {
                    "seq": seq,
                    "logical_time": event.get("logical_time"),
                    "keys": sorted((payload.get("proposal") or payload).keys())
                    if isinstance(payload.get("proposal") or payload, dict)
                    else [],
                }
            if kind in INTERESTING:
                interesting.append(record)
    finally:
        conn.close()

    last_clock = clocks[-1] if clocks else None
    logical = as_dt((last_clock or {}).get("logical_time"))
    now_cst = datetime.now(SHANGHAI)

    from companion_daemon.world_v2.day_skeleton import compile_day_sheet, load_world_day_skeleton
    from companion_daemon.world_v2.character_interior.contracts import _current_window_title

    day_sheet = compile_day_sheet(
        logical_at=logical or now_cst,
        skeleton=load_world_day_skeleton(),
        academic_phase="暑假",
        season="summer",
    )
    window = _current_window_title(day_sheet)

    plan_events = [item for item in activities if item["event_type"].startswith("Activity")]
    latest_by_plan: dict[str, dict[str, Any]] = {}
    for item in plan_events:
        plan_id = str(item.get("plan_id") or item.get("event_id") or item["seq"])
        latest_by_plan[plan_id] = item
    statuses = Counter(str(item.get("status") or item["event_type"]) for item in latest_by_plan.values())

    visual_predicates = Counter(
        str(item.get("predicate") or "none") for item in facts if item["event_type"] == "VisualFactRecorded"
    )
    fact_predicates = Counter(
        str(item.get("predicate") or "none")
        for item in facts
        if item["event_type"].startswith("Fact")
    )

    report = {
        "ledger_head": head,
        "clock_count": types.get("ClockAdvanced", 0),
        "last_clock": last_clock,
        "logical_time_cst": logical.astimezone(SHANGHAI).isoformat() if logical else None,
        "wall_now_cst": now_cst.isoformat(),
        "day_sheet": day_sheet,
        "current_window_title": window,
        "event_type_counts": {
            key: types[key]
            for key in sorted(INTERESTING | set(types))
            if key in INTERESTING or key.startswith("Activity") or "Location" in key or "Appear" in key
        },
        "activity_event_count": len(plan_events),
        "distinct_plans": len(latest_by_plan),
        "latest_plan_statuses": dict(statuses),
        "latest_plans": [
            {
                k: v
                for k, v in item.items()
                if k != "payload_keys"
            }
            for item in latest_by_plan.values()
        ],
        "appearance_count": types.get("AppearanceStateRecorded", 0),
        "vps_count": types.get("VisiblePhysicalStateRecorded", 0),
        "visual_fact_count": types.get("VisualFactRecorded", 0),
        "image_evidence_count": types.get("ImageEvidenceDeclared", 0),
        "photo_candidate_count": types.get("PhotoCandidateOpened", 0),
        "visual_fact_predicates": dict(visual_predicates),
        "fact_predicates_top": fact_predicates.most_common(20),
        "last_observation": last_obs,
        "last_proposal_keys": last_proposal,
        "activity_events": [
            {k: v for k, v in item.items() if k != "payload_keys"} for item in plan_events
        ],
    }

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "evidence.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps({k: report[k] for k in (
        "ledger_head",
        "logical_time_cst",
        "wall_now_cst",
        "current_window_title",
        "latest_plan_statuses",
        "appearance_count",
        "vps_count",
        "visual_fact_count",
        "image_evidence_count",
        "photo_candidate_count",
        "event_type_counts",
        "fact_predicates_top",
        "last_observation",
    )}, ensure_ascii=False, indent=2, default=str))
    print("--- day_sheet ---")
    print(day_sheet)
    print("--- latest plans ---")
    print(json.dumps(report["latest_plans"], ensure_ascii=False, indent=2, default=str))
    return report


if __name__ == "__main__":
    main()
