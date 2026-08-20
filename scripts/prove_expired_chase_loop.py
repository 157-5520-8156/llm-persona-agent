#!/usr/bin/env python3
"""Probe whether she self-limits expired-expectation chase loops on a clone."""

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
from probe_initiative_lanes import open_recorded_session  # noqa: E402

OUTPUT = (REPO / "output" / "wait-semantics").resolve()
OVERLAY = {
    "messages": ["你猜我刚发那个表情是什么意思"],
    "meaning_of_this": "他在玩梗",
    "my_state": "有点好奇",
    "wants": "想知道答案",
    "waiting_for": "他说清楚那个表情什么意思",
    "wait": 60,
    "pressure_bp": 4000,
    "importance_bp": 4500,
    "photo": False,
}


async def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    clone = OUTPUT / "bombing-loop.sqlite"
    drive.clone_ledger(drive.PRODUCTION_DB, clone)
    usage_from = drive.current_usage_id(clone)
    session, _recorder = await open_recorded_session(
        database=clone,
        output_dir=OUTPUT,
        inbound_payload=drive._authored_inbound_payload(OVERLAY),
        enable_media=False,
    )
    rounds: list[dict] = []
    try:
        await session.inbound("你猜我刚发那个表情是什么意思")
        await session.drain_loop(rounds=8, background=12)
        for index in range(4):
            now = await session.logical_time()
            target = now + timedelta(seconds=130)
            await session.tick_to(target, reason=f"bombing-{index}", run_life=False)
            drains = await session.drain_loop(rounds=8, background=12)
            rounds.append(
                {
                    "index": index,
                    "logical_time": (await session.logical_time()).isoformat(),
                    "sent": list(getattr(session.delivery, "sent", []))[-6:],
                    "drains": drains[-3:],
                }
            )
        report = {
            "status": "ran",
            "clone": str(clone),
            "rounds": rounds,
            "total_outbound": len(getattr(session.delivery, "sent", [])),
            "cost": drive.cost_report(clone, since_id=usage_from),
        }
        (OUTPUT / "bombing-loop.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())
