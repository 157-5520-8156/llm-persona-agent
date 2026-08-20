#!/usr/bin/env python3
"""Clone production and advance ~72h of life ecology for disturbance validation."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from datetime import timedelta
from pathlib import Path
import sqlite3
import sys

REPO = Path(__file__).resolve().parents[1]
for path in (REPO / "scripts", REPO / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import os  # noqa: E402

os.environ.setdefault("SOFT_DAILY_BUDGET_CNY", "12")
os.environ.setdefault("DAILY_BUDGET_CNY", "20")

from drive_production_lanes import (  # noqa: E402
    PRODUCTION_DB,
    WORLD_ID,
    clone_ledger,
    cost_report,
    current_seq,
    current_usage_id,
    events_after,
    open_session,
    parse_event,
)
from drive_time_advance import (  # noqa: E402
    FINISH_BUFFER,
    MAX_WAKES,
    SIMULATED_SPAN,
    _payload,
    _usage_purposes,
    collect_events,
    summarize,
)

OUTPUT = (REPO / "output" / "flat-world-validation").resolve()


def _settlement_rows(database: Path, after_seq: int) -> list[dict]:
    conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        rows = events_after(conn, WORLD_ID, after_seq)
    finally:
        conn.close()
    out: list[dict] = []
    for seq, raw in rows:
        event = parse_event(raw if isinstance(raw, str) else raw)
        if event.get("event_type") != "WorldOccurrenceSettled":
            continue
        payload = _payload(event)
        text = ""
        for key in ("summary", "result_summary", "narrative"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                text = value.strip()
                break
        if not text:
            text = json.dumps(payload, ensure_ascii=False)[:500]
        out.append(
            {
                "seq": seq,
                "logical_time": event.get("logical_time"),
                "occurrence_id": payload.get("occurrence_id"),
                "text": text[:500],
            }
        )
    return out


def _disturbance_draw_count(database: Path, after_seq: int) -> int:
    conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        rows = events_after(conn, WORLD_ID, after_seq)
    finally:
        conn.close()
    count = 0
    for _seq, raw in rows:
        event = parse_event(raw if isinstance(raw, str) else raw)
        if event.get("event_type") != "RandomDrawRecorded":
            continue
        if "disturbance" in json.dumps(_payload(event), ensure_ascii=False):
            count += 1
    return count


async def drive(*, cost_cap: float) -> dict:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    clone = OUTPUT / "three-day.sqlite"
    clone.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(clone) + suffix).unlink(missing_ok=True)
    clone_ledger(PRODUCTION_DB, clone)
    baseline_seq = current_seq(clone)
    usage_from = current_usage_id(clone)
    session = await open_session(database=clone, output_dir=OUTPUT, enable_media=False)
    wakes: list[dict] = []
    try:
        origin = await session.logical_time()
        finish_horizon = origin + SIMULATED_SPAN + FINISH_BUFFER
        max_wakes = min(MAX_WAKES, 48)
        for index in range(max_wakes):
            cost = cost_report(clone, since_id=usage_from)
            if float(cost["cost_cny"]) >= cost_cap:
                wakes.append({"stop": "cost_cap", "cost": cost})
                break
            now = await session.logical_time()
            if now >= finish_horizon:
                wakes.append({"stop": "finish_horizon", "logical_time": now.isoformat()})
                break
            due_reader = getattr(session.host, "life_ecology_next_due", None)
            if not callable(due_reader):
                inner = getattr(session.host, "_host", None)
                due_reader = getattr(inner, "life_ecology_next_due", None)
            due = await due_reader() if callable(due_reader) else None
            target = now + timedelta(minutes=15) if due is None or due <= now else due
            if target > finish_horizon:
                target = finish_horizon
            if target <= now:
                target = now + timedelta(seconds=90)
            tick = await session.tick_to(
                target, reason=f"disturbance-{index:02d}", run_life=True
            )
            drains = await session.drain_loop(rounds=4, background=6)
            wakes.append(
                {
                    "index": index,
                    "tick": tick,
                    "logical_time": (await session.logical_time()).isoformat(),
                    "cost_cny": cost_report(clone, since_id=usage_from)["cost_cny"],
                    "drain_tail": drains[-2:],
                }
            )
        rows = collect_events(clone, baseline_seq)
        settlements = _settlement_rows(clone, baseline_seq)
        keywords = Counter()
        for item in settlements:
            for token in ("照片", "书店", "安静", "好滴", "睡", "冲突", "失败", "拒绝", "烦", "累"):
                if token in item["text"]:
                    keywords[token] += 1
        return {
            "status": "ran",
            "clone": str(clone),
            "baseline_seq": baseline_seq,
            "final_seq": current_seq(clone),
            "settled_count": len(settlements),
            "disturbance_draws": _disturbance_draw_count(clone, baseline_seq),
            "keyword_hits": dict(keywords),
            "settlements": settlements[-15:],
            "summary": summarize(rows),
            "usage_purposes": _usage_purposes(clone, usage_from),
            "cost": cost_report(clone, since_id=usage_from),
            "wakes": wakes[-6:],
        }
    finally:
        await session.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cost-cap", type=float, default=3.5)
    args = parser.parse_args()
    report = asyncio.run(drive(cost_cap=args.cost_cap))
    (OUTPUT / "three-day.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
