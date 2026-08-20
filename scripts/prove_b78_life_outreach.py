#!/usr/bin/env python3
"""Prove tier-B life-event proactive outreach on a production clone."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
for path in (REPO / "scripts", REPO / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import os  # noqa: E402

os.environ.setdefault("SOFT_DAILY_BUDGET_CNY", "12")
os.environ.setdefault("DAILY_BUDGET_CNY", "20")

import drive_production_lanes as drive  # noqa: E402
from probe_initiative_lanes import (  # noqa: E402
    inspect_lanes,
    interesting_events,
    open_recorded_session,
)

OUTPUT = (REPO / "output" / "flat-world-validation").resolve()


async def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    clone = OUTPUT / "b78-life.sqlite"
    clone.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(clone) + suffix).unlink(missing_ok=True)
    drive.clone_ledger(drive.PRODUCTION_DB, clone)
    started_seq = drive.current_seq(clone)
    usage_from = drive.current_usage_id(clone)
    slim = {
        "messages": ["我先去忙一会儿"],
        "meaning_of_this": "他去忙了",
        "my_state": "先应一声",
        "wants": "先这样",
        "photo": False,
    }
    session, recorder = await open_recorded_session(
        database=clone,
        output_dir=OUTPUT,
        inbound_payload=drive._authored_inbound_payload(slim),
        enable_media=False,
    )
    try:
        await session.drain_loop(rounds=6, background=10)
        inbound_at = drive.BOOK_MARKET_DRIVE_AT - timedelta(minutes=10)
        await session.inbound("我先去忙一会儿", observed_at=inbound_at)
        await session.tick_to(
            drive.BOOK_MARKET_DRIVE_AT, reason="b78-life-window", run_life=True
        )
        await session.drain_loop(rounds=8, background=12)
        consider_at = inbound_at + timedelta(hours=13)
        now = await session.logical_time()
        if consider_at > now:
            await session.tick_to(consider_at, reason="b78-ambient-closed", run_life=False)
        before = await inspect_lanes(session)
        await session.drain_loop(rounds=10, background=14)
        after = await inspect_lanes(session)
        events = interesting_events(clone, started_seq)
        life_events = [
            item
            for item in events
            if item["event_type"]
            in {
                "ActivityCompleted",
                "WorldOccurrenceSettled",
                "ExperienceCommitted",
            }
        ]
        opp = after.get("opportunity") or before.get("opportunity") or {}
        outbound = [
            item
            for item in getattr(session.delivery, "sent", [])
            if item.get("kind") in {"proactive_message", "followup", "message"}
        ]
        report = {
            "status": "ran",
            "clone": str(clone),
            "life_events": life_events[-8:],
            "opportunity": opp,
            "independent_mint": "occasion:situation_independent"
            in (opp.get("cadence_reason_codes") or []),
            "source_kind": opp.get("source_kind"),
            "stimulus_event_refs": opp.get("stimulus_event_refs"),
            "outbound": outbound[-5:],
            "proactive_calls": len(
                [c for c in recorder.calls if c.get("purpose") == "proactive"]
            ),
            "cost": drive.cost_report(clone, since_id=usage_from),
        }
        (OUTPUT / "b78-life.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())
