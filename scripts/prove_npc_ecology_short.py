#!/usr/bin/env python3
"""Short clone drive for NPC ecology mint evidence. Budget hard cap ¥1."""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import drive_production_lanes as drive

OUTPUT = (REPO / "output" / "npc-ecology-proof").resolve()
PRODUCTION_DB = drive.PRODUCTION_DB
BUDGET_CNY = 1.0
MAX_TICKS = 8
TICK_HOURS = 3


def _session_cost_cny(usage_db: Path, *, since_id: int) -> float:
    if not usage_db.exists():
        return 0.0
    return float((drive.cost_report(usage_db, since_id=since_id) or {}).get("cost_cny") or 0)


def _npc_events(rows: list[tuple[int, dict]]) -> list[dict]:
    interesting = {
        "NpcStateChanged",
        "ActivityPlanned",
        "ActivityStarted",
        "WorldOccurrenceActivated",
        "ModelResultRecorded",
    }
    out: list[dict] = []
    for seq, event in rows:
        if drive.event_type(event) not in interesting:
            continue
        payload = event.get("_payload") or {}
        audit = payload.get("audit_json") if drive.event_type(event) == "ModelResultRecorded" else None
        if isinstance(audit, str) and "npc_ecology" not in audit:
            continue
        out.append(
            {
                "seq": seq,
                "event_type": drive.event_type(event),
                "event_id": event.get("event_id"),
                "payload": payload,
            }
        )
    return out


def _occurrence_texts(rows: list[tuple[int, dict]]) -> list[str]:
    texts: list[str] = []
    for _seq, event in rows:
        if drive.event_type(event) != "WorldOccurrenceActivated":
            continue
        payload = event.get("_payload") or {}
        outcomes = payload.get("candidate_outcomes") or []
        for item in outcomes:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                texts.append(item["text"])
    return texts


async def main(*, budget_cny: float = BUDGET_CNY) -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    clone = OUTPUT / "npc-short.sqlite"
    drive.clone_ledger(PRODUCTION_DB, clone)
    started_seq = drive.current_seq(clone)
    usage_from = drive.current_usage_id(clone)

    session = await drive.open_session(database=clone, output_dir=OUTPUT, enable_media=False)
    ticks: list[dict] = []
    try:
        now = await session.logical_time()
        for index in range(MAX_TICKS):
            if _session_cost_cny(clone, since_id=usage_from) >= budget_cny:
                break
            target = now + timedelta(hours=TICK_HOURS * (index + 1))
            tick = await session.tick_to(target, reason=f"npc-proof-{index}")
            ticks.append(tick)
            await session.drain_loop(rounds=6, background=8)
            if _session_cost_cny(clone, since_id=usage_from) >= budget_cny:
                break
    finally:
        await session.close()

    rows = drive.new_events(clone, started_seq)
    npc_rows = _npc_events(rows)
    usage = drive.cost_report(clone, since_id=usage_from)
    spent = float(usage.get("cost_cny") or 0)
    report = {
        "budget_cny": budget_cny,
        "spent_cny": spent,
        "ticks": ticks,
        "npc_events": npc_rows,
        "occurrence_texts": _occurrence_texts(rows),
        "usage": usage,
        "started_seq": started_seq,
        "final_seq": drive.current_seq(clone),
    }
    (OUTPUT / "REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md = [
        "# NPC ecology short clone proof",
        "",
        f"- Budget cap: ¥{budget_cny:.2f}",
        f"- Spent: ¥{report['spent_cny']:.4f}",
        f"- Ticks: {len(ticks)}",
        f"- NPC-related events: {len(npc_rows)}",
        "",
    ]
    if npc_rows:
        md.append("## Event samples")
        for item in npc_rows[:6]:
            md.append(f"- `{item['event_type']}` seq {item['seq']}")
    if report["occurrence_texts"]:
        md.append("")
        md.append("## Occurrence text")
        for text in report["occurrence_texts"][:4]:
            md.append(f"- {text}")
    else:
        md.append("")
        md.append(
            "No NPC ecology occurrence minted in this short drive. "
            f"At ~16% ambient occasion and cap 8/week, expect ~1 mint per "
            f"{MAX_TICKS * TICK_HOURS}h window only if wakes align; longer "
            "drive (~3–5 days simulated, ~¥0.3–0.8) likely needed."
        )
    (OUTPUT / "REPORT.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print((OUTPUT / "REPORT.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget-cny", type=float, default=BUDGET_CNY)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(budget_cny=args.budget_cny)))
