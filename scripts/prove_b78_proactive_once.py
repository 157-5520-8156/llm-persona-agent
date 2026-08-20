#!/usr/bin/env python3
"""Minimal B78 proactive proof: mint tier-B consider and capture outbound text."""

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

os.environ["SOFT_DAILY_BUDGET_CNY"] = "50"
os.environ["DAILY_BUDGET_CNY"] = "60"

import drive_production_lanes as drive  # noqa: E402
from probe_initiative_lanes import (  # noqa: E402
    _calls_for,
    inspect_lanes,
    interesting_events,
    open_recorded_session,
)

OUTPUT = (REPO / "output" / "flat-world-validation").resolve()


async def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    clone = OUTPUT / "b78-proactive.sqlite"
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
    steps: list[dict] = []
    try:
        await session.drain_loop(rounds=4, background=6)
        inbound_at = drive.BOOK_MARKET_DRIVE_AT - timedelta(minutes=10)
        await session.inbound("我先去忙一会儿", observed_at=inbound_at)
        steps.append({"step": "inbound", "logical_time": (await session.logical_time()).isoformat()})
        tick_life = await session.tick_to(
            drive.BOOK_MARKET_DRIVE_AT, reason="b78-life-window", run_life=True
        )
        steps.append({"step": "life_tick", "tick": tick_life, "logical_time": (await session.logical_time()).isoformat()})
        await session.drain_loop(rounds=6, background=8)
        consider_at = inbound_at + timedelta(hours=13)
        now = await session.logical_time()
        if consider_at > now:
            tick_consider = await session.tick_to(
                consider_at, reason="b78-ambient-closed", run_life=False
            )
            steps.append(
                {
                    "step": "consider_tick",
                    "tick": tick_consider,
                    "logical_time": (await session.logical_time()).isoformat(),
                }
            )
        before = await inspect_lanes(session)
        steps.append({"step": "before_drain", "opportunity": before.get("opportunity")})
        await session.drain_loop(rounds=12, background=14)
        after = await inspect_lanes(session)
        events = interesting_events(clone, started_seq)
        proactive_calls = _calls_for(recorder, "proactive")
        outbound = list(getattr(session.delivery, "sent", []))
        her_words = [
            item.get("text") or item.get("content")
            for item in outbound
            if item.get("kind") in {"proactive_message", "followup", "message", "text"}
        ]
        report = {
            "status": "ran",
            "clone": str(clone),
            "steps": steps,
            "independent_mint": "occasion:situation_independent"
            in ((after.get("opportunity") or before.get("opportunity") or {}).get("cadence_reason_codes") or []),
            "opportunity": after.get("opportunity") or before.get("opportunity"),
            "proactive_calls": proactive_calls,
            "her_words": [w for w in her_words if isinstance(w, str) and w.strip()],
            "outbound": outbound[-8:],
            "authorized": [
                item
                for item in events
                if item["event_type"] == "ActionAuthorized"
                and item.get("action_kind") == "proactive_message"
            ],
            "deliberation_failures": [
                item
                for item in events
                if str(item.get("runtime_outcome_ref") or "").startswith("proactive:deliberation-failed:")
            ],
            "cost": drive.cost_report(clone, since_id=usage_from),
        }
        (OUTPUT / "b78-proactive.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())
