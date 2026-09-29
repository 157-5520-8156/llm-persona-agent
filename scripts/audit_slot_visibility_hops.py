#!/usr/bin/env python3
"""Hop-trace appraisals / open_threads / private_impressions on a clone.

Never writes ``data/``. Artifacts: ``output/slot-visibility/``.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any

REPO = Path(__file__).resolve().parents[1]
for path in (REPO / "src", REPO / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import drive_production_lanes as drive
from companion_daemon.world_v2.context_resolver import query_from_projection
from companion_daemon.world_v2.living_state_inventory import (
    install_living_state_context,
)
from companion_daemon.world_v2.photographable_inventory import (
    install_shareable_photos_context,
)
from companion_daemon.world_v2.private_impression_events import (
    collect_user_channel_limited_impression_ids,
)
from companion_daemon.world_v2.character_interior.snapshot_compiler import (
    compile_inner_life_snapshot,
    _slice_items,
)

WORLD_ID = drive.WORLD_ID
ACTOR = "agent:companion"
OUTPUT = (REPO / "output" / "slot-visibility").resolve()
CLONE_CANDIDATES = (
    REPO / "output" / "photo-visibility" / "clone.sqlite",
    REPO / "output" / "context-audit" / "clone.sqlite",
)


def dump_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def application_of(session: drive.DriveSession):
    platform = getattr(session.host, "_host", None)
    return getattr(platform, "_application", None)


def row_appraisal(item: object) -> dict[str, Any]:
    origin = getattr(item, "origin", None)
    return {
        "appraisal_id": getattr(item, "appraisal_id", None),
        "status": getattr(item, "status", None),
        "subject_ref": getattr(item, "subject_ref", None),
        "accepted_at": getattr(item, "accepted_at", None),
        "expires_at": getattr(item, "expires_at", None),
        "accepted_event_ref": getattr(origin, "accepted_event_ref", None) if origin else None,
        "expired_now": (
            isinstance(getattr(item, "expires_at", None), datetime)
            and isinstance(getattr(item, "accepted_at", None), datetime) is not None
            and item.expires_at <= getattr(item, "_now", item.expires_at)
        ),
    }


async def run(clone: Path) -> dict[str, Any]:
    session = await drive.open_session(
        database=clone, output_dir=OUTPUT, enable_media=False
    )
    try:
        projection = session.projection()
        if projection is None:
            raise RuntimeError("no projection")
        now = projection.logical_time
        application = application_of(session)
        ledger = getattr(application, "_ledger", None)
        interior = getattr(application, "_character_interior", None)
        delegate = getattr(getattr(interior, "_projection", None), "_delegate", None)
        capsules = getattr(delegate, "capsules", None)
        limited = collect_user_channel_limited_impression_ids(
            ledger=ledger, projection=projection
        )

        appraisals = list(getattr(projection, "appraisals", ()) or ())
        active_appraisals = [
            item
            for item in appraisals
            if item.status == "active" and (now is None or item.expires_at > now)
        ]
        expired_active = [
            item
            for item in appraisals
            if item.status == "active" and now is not None and item.expires_at <= now
        ]
        threads = list(getattr(projection, "threads", ()) or ())
        open_threads = [item for item in threads if item.values.status == "open"]
        impressions = list(getattr(projection, "private_impressions", ()) or ())
        active_impressions = [item for item in impressions if item.status == "active"]

        hop1 = {
            "logical_time": now,
            "ledger_sequence": projection.ledger_sequence,
            "appraisals_total": len(appraisals),
            "appraisals_active_unexpired": len(active_appraisals),
            "appraisals_active_expired": len(expired_active),
            "appraisal_subjects": sorted(
                {item.subject_ref for item in active_appraisals if item.subject_ref}
            ),
            "appraisal_ids": [item.appraisal_id for item in active_appraisals],
            "threads_total": len(threads),
            "threads_open": [
                {
                    "thread_id": item.thread_id,
                    "status": item.values.status,
                    "subject_ref": item.values.subject_ref,
                    "kind": getattr(item.values, "kind", None),
                    "importance_bp": getattr(item.values, "importance_bp", None),
                    "accepted_event_ref": getattr(
                        getattr(item, "origin", None), "accepted_event_ref", None
                    ),
                }
                for item in open_threads
            ],
            "impressions_total": len(impressions),
            "impressions_active": [
                {
                    "impression_id": item.impression_id,
                    "status": item.status,
                    "subject_ref": item.subject_ref,
                    "origin": getattr(getattr(item, "origin", None), "accepted_event_ref", None),
                    "user_channel_limited": item.impression_id in limited,
                    "summary_excerpt": (item.reflection_summary or "")[:80],
                }
                for item in active_impressions
            ],
            "user_channel_limited_ids": sorted(limited),
        }

        trigger = None
        for ref in reversed(getattr(projection, "committed_world_event_refs", ()) or ()):
            event_id = getattr(ref, "event_id", None)
            if isinstance(event_id, str) and event_id:
                trigger = event_id
                break
        query = query_from_projection(
            projection, actor_ref=ACTOR, trigger_ref=trigger
        )
        capsule = capsules.compile(query)
        context = json.loads(capsule.model_content_json)
        slices = context.get("slices") if isinstance(context, dict) else {}

        def slice_hop(name: str) -> dict[str, Any]:
            lane = slices.get(name) if isinstance(slices, dict) else None
            budget = getattr(getattr(capsule, name, None), "budget", None)
            items = _slice_items(slices, name) if isinstance(slices, dict) else []
            trunc = [
                entry.model_dump(mode="json") if hasattr(entry, "model_dump") else str(entry)
                for entry in getattr(capsule, "truncation_log", ()) or ()
                if getattr(entry, "slice_name", None) == name
                or (isinstance(entry, dict) and entry.get("slice_name") == name)
            ]
            return {
                "availability": lane.get("availability") if isinstance(lane, dict) else None,
                "item_count": len(items),
                "source_refs": (lane.get("source_refs") if isinstance(lane, dict) else None),
                "budget": budget.model_dump(mode="json") if hasattr(budget, "model_dump") else None,
                "truncated": getattr(getattr(capsule, name, None), "truncated", None),
                "truncation_log": trunc,
                "item_refs": [
                    item.get("item_ref") or item.get("source_ref") for item in items
                ],
            }

        hop2 = {
            "appraisals": slice_hop("appraisals"),
            "open_threads": slice_hop("open_threads"),
            "private_impressions": slice_hop("private_impressions"),
            "truncation_all": [
                entry.model_dump(mode="json") if hasattr(entry, "model_dump") else str(entry)
                for entry in getattr(capsule, "truncation_log", ()) or ()
            ],
        }

        installed = install_shareable_photos_context(context, projection)
        installed = install_living_state_context(installed, projection)
        typed = compile_inner_life_snapshot(installed)
        materials = json.loads(typed.materials_json)
        hop3 = {
            "appraisals": len(materials.get("appraisals") or [])
            if isinstance(materials.get("appraisals"), list)
            else materials.get("appraisals"),
            "unresolved": len(materials.get("unresolved") or [])
            if isinstance(materials.get("unresolved"), list)
            else materials.get("unresolved"),
            "private_impressions": len(materials.get("private_impressions") or [])
            if isinstance(materials.get("private_impressions"), list)
            else materials.get("private_impressions"),
            "living_appraisals": len(_slice_items(installed.get("slices") or {}, "living_appraisals")),
            "living_threads": len(_slice_items(installed.get("slices") or {}, "living_threads")),
            "living_impressions": len(_slice_items(installed.get("slices") or {}, "living_impressions")),
            "appraisal_source_refs": [
                item.get("source_ref")
                for item in (materials.get("appraisals") or [])
                if isinstance(item, dict)
            ],
            "unresolved_source_refs": [
                item.get("source_ref")
                for item in (materials.get("unresolved") or [])
                if isinstance(item, dict)
            ],
            "impression_source_refs": [
                item.get("source_ref")
                for item in (materials.get("private_impressions") or [])
                if isinstance(item, dict)
            ],
        }
        hop2["budget_truncation_log"] = [
            entry.model_dump(mode="json") if hasattr(entry, "model_dump") else str(entry)
            for entry in getattr(getattr(capsule, "budget", None), "truncation_log", ()) or ()
        ]
        return {"hop1_projection": hop1, "hop2_capsule": hop2, "hop3_snapshot": hop3}
    finally:
        await session.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clone", type=Path, default=None)
    args = parser.parse_args()
    clone = args.clone
    if clone is None:
        clone = next((path for path in CLONE_CANDIDATES if path.is_file()), None)
    if clone is None:
        OUTPUT.mkdir(parents=True, exist_ok=True)
        clone = OUTPUT / "clone.sqlite"
        drive.clone_ledger(drive.PRODUCTION_DB, clone)
    report = asyncio.run(run(clone))
    dump_json(OUTPUT / "hops.json", report)
    summary = {
        "appraisals": {
            "projection_active": report["hop1_projection"]["appraisals_active_unexpired"],
            "capsule": report["hop2_capsule"]["appraisals"],
            "snapshot": report["hop3_snapshot"]["appraisals"],
        },
        "open_threads": {
            "projection_open": len(report["hop1_projection"]["threads_open"]),
            "capsule": report["hop2_capsule"]["open_threads"],
            "snapshot": report["hop3_snapshot"]["unresolved"],
        },
        "private_impressions": {
            "projection_active": len(report["hop1_projection"]["impressions_active"]),
            "limited": report["hop1_projection"]["user_channel_limited_ids"],
            "capsule": report["hop2_capsule"]["private_impressions"],
            "snapshot": report["hop3_snapshot"]["private_impressions"],
        },
        "living_installed": {
            "appraisals": report["hop3_snapshot"].get("living_appraisals"),
            "threads": report["hop3_snapshot"].get("living_threads"),
            "impressions": report["hop3_snapshot"].get("living_impressions"),
        },
        "budget_truncation_log": report["hop2_capsule"].get("budget_truncation_log"),
    }
    dump_json(OUTPUT / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
