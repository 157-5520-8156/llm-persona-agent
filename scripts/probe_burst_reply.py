#!/usr/bin/env python3
"""Burst inbound on a production-host clone. Never writes data/, never QQ.

Sends 2/3/4 consecutive texts the way a person double-taps at night, then
records whether the later bubbles were deferred. Artifacts: output/burst-reply/.

Usage::

    .venv/bin/python scripts/probe_burst_reply.py --phase before --burst 2 --trials 8
    .venv/bin/python scripts/probe_burst_reply.py --phase after --burst 2 --trials 8
    .venv/bin/python scripts/probe_burst_reply.py --phase after --burst 3 --trials 4
    .venv/bin/python scripts/probe_burst_reply.py --phase after --burst 4 --trials 4
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import drive_production_lanes as drive

WORLD_ID = drive.WORLD_ID
OUTPUT = (REPO / "output" / "burst-reply").resolve()

BURST_SCRIPTS: dict[int, list[tuple[str, ...]]] = {
    2: [
        ("还没睡？我倒了杯水。", "你刚才那句我看见了。"),
        ("在吗", "我刚发的那条你看到没"),
        ("今晚风有点大", "你回我一下就行"),
        ("我到家了", "刚才那句别当没看见"),
        ("困了但还不想睡", "你人呢"),
        ("倒了杯热水", "嗯就是跟你说一声"),
        ("你还醒着吗", "我看见你上一条了"),
        ("随便聊两句", "第二句也在"),
    ],
    3: [
        ("还没睡？", "我倒了杯水。", "你刚才那句我看见了。"),
        ("在吗", "今晚有点吵", "回我一下"),
        ("到家了", "先洗把脸", "你呢"),
        ("困了", "但还不睡", "陪我两句"),
    ],
    4: [
        ("还没睡？", "倒了杯水", "你刚才那句我看见了", "就这样"),
        ("在吗", "今晚风大", "我到家了", "回一下"),
        ("困了", "还不睡", "你呢", "第二句也在"),
        ("热水倒好了", "坐着发呆", "你醒着吗", "看见就回"),
    ],
}


def _dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _texts(visible: list[object]) -> list[str]:
    out: list[str] = []
    for item in visible:
        if isinstance(item, dict) and item.get("kind") == "text":
            body = item.get("body")
            if isinstance(body, str) and body.strip():
                out.append(body.strip())
    return out


def _usage_cost(clone: Path) -> float:
    conn = sqlite3.connect(f"file:{clone.resolve()}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_cny), 0) FROM world_v2_model_usage"
        ).fetchone()
        return float(row[0] or 0)
    finally:
        conn.close()


def _extract_turn(clone: Path, *, after_seq: int) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{clone.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        max_seq = int(
            conn.execute(
                "SELECT COALESCE(MAX(ledger_sequence), 0) FROM world_v2_events WHERE world_id = ?",
                (WORLD_ID,),
            ).fetchone()[0]
            or 0
        )
        batch = conn.execute(
            """
            SELECT batch_id, outcome_status, batch_json
            FROM world_v2_qq_ingress_batches
            ORDER BY rowid DESC LIMIT 1
            """
        ).fetchone()
        rows = conn.execute(
            """
            SELECT ledger_sequence, event_json
            FROM world_v2_events
            WHERE world_id = ? AND ledger_sequence > ?
            ORDER BY ledger_sequence
            """,
            (WORLD_ID, after_seq),
        ).fetchall()
    finally:
        conn.close()
    observation_text = None
    model_results: list[dict[str, Any]] = []
    authorized_texts: list[str] = []
    event_types: list[str] = []
    for row in rows:
        event = json.loads(row["event_json"])
        et = str(event.get("event_type") or "")
        event_types.append(et)
        payload = event.get("payload_json")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        payload = payload if isinstance(payload, dict) else {}
        if et == "ObservationRecorded":
            text = payload.get("text")
            if isinstance(text, str):
                observation_text = text
        if et == "ModelResultRecorded":
            audit = payload.get("audit_json")
            if isinstance(audit, str):
                try:
                    audit = json.loads(audit)
                except json.JSONDecodeError:
                    audit = {}
            if isinstance(audit, dict):
                rejection = audit.get("role_rejection")
                excerpt = None
                detail = None
                original = None
                if isinstance(rejection, dict):
                    excerpt = rejection.get("rejected_raw_excerpt")
                    detail = rejection.get("failure_detail")
                    original = rejection.get("original_failure_code")
                model_results.append(
                    {
                        "ledger_sequence": row["ledger_sequence"],
                        "failure_code": audit.get("failure_code"),
                        "status": audit.get("status"),
                        "outcome": audit.get("outcome"),
                        "attempt_count": audit.get("attempt_count"),
                        "original_failure_code": original,
                        "failure_detail": detail,
                        "rejected_raw_excerpt": excerpt,
                    }
                )
        if et == "ExpressionPlanAccepted":
            beats = payload.get("beats")
            if isinstance(beats, list):
                for item in beats:
                    if not isinstance(item, dict):
                        continue
                    beat = item.get("beat")
                    if not isinstance(beat, dict):
                        continue
                    inner = beat.get("payload")
                    if not isinstance(inner, dict):
                        inner = beat
                    text = inner.get("text")
                    if isinstance(text, str) and text.strip():
                        authorized_texts.append(text.strip())
    batch_status = batch["outcome_status"] if batch is not None else None
    failures = [item for item in model_results if item.get("failure_code")]
    return {
        "max_seq": max_seq,
        "observation_text": observation_text,
        "batch_status": batch_status,
        "batch_id": batch["batch_id"] if batch is not None else None,
        "authorized_texts": authorized_texts,
        "model_results": model_results,
        "failures": failures,
        "event_types": event_types[-24:],
    }


async def _inbound(
    session: drive.DriveSession, text: str, *, background: int = 0
) -> dict[str, Any]:
    when = session.clock + timedelta(seconds=2)
    message_id = f"burst-{time.time_ns()}"
    before = len(session.delivery.sent)
    result = await session.host.inbound_text(
        message_id=message_id,
        recipient_id=session.recipient_id,
        text=text,
        observed_at=when,
    )
    session.clock = when
    drain = await session.drain(actions=8, background=background)
    visible = session.delivery.sent[before:]
    return {
        "status": getattr(result, "status", None),
        "action_id": getattr(result, "action_id", None),
        "message_id": message_id,
        "observed_at": when.isoformat(),
        "visible": visible,
        "her_texts": _texts(visible),
        "drain": drain,
    }


async def _run_trial(
    *,
    phase: str,
    burst: int,
    trial: int,
    lines: tuple[str, ...],
    source: Path,
) -> dict[str, Any]:
    clone = OUTPUT / f"{phase}-burst{burst}-t{trial}.sqlite"
    drive.clone_ledger(source, clone)
    cost_before = _usage_cost(clone)
    session = await drive.open_session(
        database=clone,
        output_dir=OUTPUT,
        enable_media=False,
    )
    steps: list[dict[str, Any]] = []
    seq = 0
    conn = sqlite3.connect(f"file:{clone.resolve()}?mode=ro", uri=True)
    try:
        seq = int(
            conn.execute(
                "SELECT COALESCE(MAX(ledger_sequence), 0) FROM world_v2_events WHERE world_id = ?",
                (WORLD_ID,),
            ).fetchone()[0]
            or 0
        )
    finally:
        conn.close()
    try:
        for index, text in enumerate(lines):
            started = time.time()
            inbound = await _inbound(session, text, background=0)
            extracted = _extract_turn(clone, after_seq=seq)
            seq = int(extracted["max_seq"])
            steps.append(
                {
                    "index": index,
                    "user_text": text,
                    "elapsed_s": round(time.time() - started, 3),
                    **inbound,
                    "ledger": extracted,
                }
            )
    except Exception as exc:
        steps.append({"error": f"{type(exc).__name__}: {exc}"})
        raise
    finally:
        await session.close()
    later = steps[1:] if len(steps) > 1 else []
    deferred = [
        step
        for step in later
        if step.get("status") == "deferred" or not step.get("her_texts")
    ]
    return {
        "phase": phase,
        "burst": burst,
        "trial": trial,
        "clone": str(clone),
        "cost_cny": round(_usage_cost(clone) - cost_before, 4),
        "lines": list(lines),
        "steps": steps,
        "later_visible": all(bool(step.get("her_texts")) for step in later) if later else False,
        "later_deferred_count": len(deferred),
        "second_status": steps[1].get("status") if len(steps) > 1 else None,
        "second_her_texts": steps[1].get("her_texts") if len(steps) > 1 else None,
        "second_failure": (steps[1].get("ledger") or {}).get("failures") if len(steps) > 1 else None,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True, choices=("before", "after"))
    parser.add_argument("--burst", type=int, required=True, choices=(2, 3, 4))
    parser.add_argument("--trials", type=int, default=8)
    parser.add_argument(
        "--source",
        type=Path,
        default=drive.PRODUCTION_DB,
        help="Read-only production ledger (copied, never opened for write).",
    )
    args = parser.parse_args()
    scripts = BURST_SCRIPTS[args.burst]
    if args.trials > len(scripts):
        raise SystemExit(f"only {len(scripts)} scripts for burst={args.burst}")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "phase": args.phase,
        "burst": args.burst,
        "trials": args.trials,
        "started_at": datetime.now(UTC).isoformat(),
        "source": str(args.source),
        "results": [],
    }
    for trial in range(1, args.trials + 1):
        print(f"=== {args.phase} burst={args.burst} trial={trial} ===", flush=True)
        result = await _run_trial(
            phase=args.phase,
            burst=args.burst,
            trial=trial,
            lines=scripts[trial - 1],
            source=args.source,
        )
        summary["results"].append(result)
        _dump(OUTPUT / f"{args.phase}-burst{args.burst}-t{trial}.json", result)
        print(
            json.dumps(
                {
                    "trial": trial,
                    "statuses": [step.get("status") for step in result["steps"]],
                    "second_status": result.get("second_status"),
                    "second_her_texts": result.get("second_her_texts"),
                    "second_failure": result.get("second_failure"),
                    "cost_cny": result.get("cost_cny"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    later_n = max(1, args.trials)
    visible_later = sum(1 for item in summary["results"] if item.get("later_visible"))
    second_visible = sum(
        1
        for item in summary["results"]
        if item.get("second_status") != "deferred" and item.get("second_her_texts")
    )
    second_deferred = sum(
        1 for item in summary["results"] if item.get("second_status") == "deferred"
    )
    summary["finished_at"] = datetime.now(UTC).isoformat()
    summary["later_visible_rate"] = f"{visible_later}/{later_n}"
    summary["second_visible_rate"] = f"{second_visible}/{later_n}"
    summary["second_deferred_rate"] = f"{second_deferred}/{later_n}"
    summary["cost_cny"] = round(sum(item.get("cost_cny") or 0 for item in summary["results"]), 4)
    _dump(OUTPUT / f"{args.phase}-burst{args.burst}-summary.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
