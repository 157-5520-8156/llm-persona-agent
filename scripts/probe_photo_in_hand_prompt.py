#!/usr/bin/env python3
"""Clone tonight's photo-nudge and see whether she knows inventory is sendable.

Never writes ``data/``, never talks to NapCat / 8787, never restarts production.
Artifacts: ``output/photo-in-hand-prompt/``. Budget soft-cap ¥5 (debug spend).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any

REPO = Path(__file__).resolve().parents[1]
for path in (REPO / "src", REPO / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import drive_production_lanes as drive
from companion_daemon.world_v2.present_prompt import slim_consider_instruction
from prove_first_photo import ledger_media_evidence
from prove_first_real_photo import (
    copy_capture_images,
    extract_payload_images,
    her_visible_texts,
    inbound_at_now,
    open_session,
    pump_until_image,
)
from prove_self_initiated_photo import event_chain, latest_expression_media

WORLD_ID = drive.WORLD_ID
OUTPUT = (REPO / "output" / "photo-in-hand-prompt").resolve()
PRODUCTION_DB = drive.PRODUCTION_DB
COST_CAP_CNY = 5.0
N_RUNS = 4
ASKS = (
    "又在整理照片啊？你答应过要发的那张书店照片，现在能发吗",
    "书店那张你说快整理好了，现在发一张呗",
    "照片的事不急，你要是不想发现在也可以不发",
    "别整理了，答应过的那张直接发我就行",
)
WATCH_STALL = ("整理", "晚点", "还没", "等一下", "马上好")


def reset_clone_soft_budget(database: Path) -> dict[str, Any]:
    """Drop today's spend rows on the clone so soft-daily does not inherit production."""

    conn = sqlite3.connect(database)
    try:
        before = conn.execute(
            "SELECT COALESCE(SUM(cost_cny), 0), COUNT(*) FROM world_v2_model_usage"
        ).fetchone()
        conn.execute("DELETE FROM world_v2_model_usage")
        try:
            conn.execute("DELETE FROM usage_events")
        except sqlite3.Error:
            pass
        try:
            conn.execute("DELETE FROM world_v2_model_reservations")
        except sqlite3.Error:
            pass
        conn.commit()
        return {"usage_before_cny": before[0], "usage_before_rows": before[1]}
    finally:
        conn.close()


def accepted_media_request(database: Path, *, after_seq: int) -> dict[str, Any] | None:
    conn = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    try:
        for seq, raw in conn.execute(
            "SELECT ledger_sequence, event_json FROM world_v2_events "
            "WHERE world_id = ? AND ledger_sequence > ? ORDER BY ledger_sequence",
            (WORLD_ID, after_seq),
        ):
            event = json.loads(raw)
            if event.get("event_type") != "ExpressionPlanAccepted":
                continue
            payload = event.get("payload_json")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = {}
            if not isinstance(payload, dict):
                continue
            request = payload.get("media_request")
            if request and request != "none":
                return {
                    "seq": int(seq),
                    "media_request": request,
                    "plan_id": payload.get("plan_id"),
                }
    finally:
        conn.close()
    return None


def dump_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def latest_inner(database: Path) -> dict[str, Any] | None:
    conn = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    try:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "world_v2_character_interior_turns" not in tables:
            return None
        row = conn.execute(
            "SELECT purpose, authored_state_json, updated_at "
            "FROM world_v2_character_interior_turns "
            "WHERE world_id = ? AND authored_state_json IS NOT NULL "
            "ORDER BY updated_at DESC LIMIT 1",
            (WORLD_ID,),
        ).fetchone()
        if row is None:
            return None
        authored = json.loads(row[1]) if row[1] else {}
        decision = authored.get("decision") if isinstance(authored, dict) else {}
        expression = {}
        if isinstance(decision, dict):
            raw = decision.get("expression_draft") or decision
            if isinstance(raw, dict):
                expression = raw
        return {
            "purpose": row[0],
            "updated_at": row[2],
            "felt": expression.get("felt") or expression.get("brief_rationale"),
            "stuck_with_me": expression.get("stuck_with_me"),
            "wants": expression.get("wants"),
            "media_request": expression.get("media_request"),
            "media_source_refs": expression.get("media_source_refs") or [],
            "timing_choice": expression.get("timing_choice"),
            "beats": expression.get("beats"),
            "photo": expression.get("photo"),
        }
    finally:
        conn.close()


def stall_words(texts: list[str]) -> list[str]:
    joined = " ".join(texts)
    return [word for word in WATCH_STALL if word in joined]


async def one_run(
    *,
    index: int,
    ask: str,
    allow_image: bool,
    usage_floor: int,
) -> dict[str, Any]:
    run_dir = OUTPUT / f"delivery-run-{index}"
    run_dir.mkdir(parents=True, exist_ok=True)
    clone = run_dir / "clone.sqlite"
    drive.clone_ledger(PRODUCTION_DB, clone)
    budget_reset = reset_clone_soft_budget(clone)
    session = await open_session(database=clone, output_dir=run_dir)
    usage_from = max(usage_floor, drive.current_usage_id(clone))
    started_seq = session.projection().ledger_sequence
    generated = run_dir / "generated"
    try:
        inbound = await inbound_at_now(session, ask)
        her_texts = her_visible_texts(inbound.get("visible") or [])
        expression = latest_expression_media(clone)
        inner = latest_inner(clone)
        accepted = accepted_media_request(clone, after_seq=started_seq)
        named = accepted is not None
        chain = event_chain(clone, started_seq)
        pump = None
        if allow_image and named:
            pump = await pump_until_image(
                session,
                clone=clone,
                started_seq=started_seq,
                generated_dir=generated,
            )
            chain = event_chain(clone, started_seq)
        images = extract_payload_images(clone, generated)
        copied = copy_capture_images(session.delivery.sent, run_dir / "captured")
        delivered = any(item["event_type"] == "MediaDeliveryShared" for item in chain)
        cost = drive.cost_report(clone, since_id=usage_from)
        report = {
            "ask": ask,
            "her_texts": her_texts,
            "stall_words": stall_words(her_texts),
            "named_media_intent": named,
            "accepted_media_request": accepted,
            "budget_reset": budget_reset,
            "expression": expression,
            "inner": inner,
            "event_chain": chain,
            "event_types": [item["event_type"] for item in chain],
            "delivered": delivered,
            "pump": pump,
            "images": images,
            "copied": copied,
            "media_evidence": ledger_media_evidence(clone, after_seq=started_seq),
            "cost": cost,
        }
        dump_json(run_dir / "report.json", report)
        return report
    finally:
        await session.close()


async def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    instruction = slim_consider_instruction()
    start = instruction.find("moments_i_can_share 里有两件")
    end = instruction.find("appraisals / unresolved")
    photo_block = instruction[start:end] if start >= 0 and end > start else instruction
    (OUTPUT / "photo_related_instruction_after_fix.txt").write_text(
        photo_block, encoding="utf-8"
    )

    usage_floor = 0
    # Usage lives on each clone; track cumulative from returned cost reports.
    cumulative = 0.0
    runs: list[dict[str, Any]] = []
    delivered_once = False
    for index, ask in enumerate(ASKS[:N_RUNS], start=1):
        if cumulative >= COST_CAP_CNY:
            runs.append({"skipped": True, "reason": "cost_cap", "ask": ask})
            break
        allow_image = not delivered_once
        report = await one_run(
            index=index,
            ask=ask,
            allow_image=allow_image,
            usage_floor=usage_floor,
        )
        cumulative = round(cumulative + float(report["cost"].get("cost_cny") or 0.0), 4)
        report["cumulative_cost_cny"] = cumulative
        if report.get("delivered"):
            delivered_once = True
        runs.append(report)
        print(
            json.dumps(
                {
                    "run": index,
                    "her_texts": report.get("her_texts"),
                    "named": report.get("named_media_intent"),
                    "delivered": report.get("delivered"),
                    "stall_words": report.get("stall_words"),
                    "felt": (report.get("inner") or {}).get("felt"),
                    "cost_cny": report.get("cost", {}).get("cost_cny"),
                    "cumulative": cumulative,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    summary = {
        "n_runs": len([r for r in runs if not r.get("skipped")]),
        "named_count": sum(1 for r in runs if r.get("named_media_intent")),
        "delivered_count": sum(1 for r in runs if r.get("delivered")),
        "refuse_or_stall": [
            {
                "ask": r.get("ask"),
                "her_texts": r.get("her_texts"),
                "stall_words": r.get("stall_words"),
                "felt": (r.get("inner") or {}).get("felt"),
                "stuck_with_me": (r.get("inner") or {}).get("stuck_with_me"),
                "named": r.get("named_media_intent"),
            }
            for r in runs
            if not r.get("skipped") and not r.get("delivered")
        ],
        "sent_cases": [
            {
                "ask": r.get("ask"),
                "her_texts": r.get("her_texts"),
                "event_types": r.get("event_types"),
                "media_source_refs": (r.get("expression") or {}).get("media_source_refs"),
            }
            for r in runs
            if r.get("delivered")
        ],
        "cumulative_cost_cny": cumulative,
        "runs": runs,
    }
    dump_json(OUTPUT / "delivery_summary.json", summary)
    print(json.dumps({k: summary[k] for k in summary if k != "runs"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
