#!/usr/bin/env python3
"""Drive the media tail: inspection -> preview -> delivery approval -> shared.

Resumes an existing clone that already holds a rendered artifact and its
provider inspection receipt.  Only the three media tail seams and the Action
pump run, so no candidate is re-selected, nothing is re-planned, and no image
provider is called.  Never writes ``data/``.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import traceback
from typing import Any

REPO = Path(__file__).resolve().parents[1]
for path in (REPO / "src", REPO / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import drive_production_lanes as drive

WORLD_ID = drive.WORLD_ID
TAIL_EVENT_TYPES = (
    "MediaRenderArtifactRecorded",
    "MediaInspectionRecorded",
    "MediaPreviewGenerated",
    "MediaPreviewFailed",
    "MediaAutomaticDeliveryApproved",
    "MediaDeliveryShared",
    "ActionAuthorized",
    "ActionDelivered",
    "ActionFailed",
)


def dump_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def tail_events(database: Path, after_seq: int) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    out: list[dict[str, Any]] = []
    try:
        for seq, raw in conn.execute(
            "SELECT ledger_sequence, event_json FROM world_v2_events "
            "WHERE world_id = ? AND ledger_sequence > ? ORDER BY ledger_sequence",
            (WORLD_ID, after_seq),
        ):
            event = json.loads(raw)
            kind = event.get("event_type")
            if kind not in TAIL_EVENT_TYPES:
                continue
            payload = event.get("payload") or event.get("payload_json")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = {}
            if not isinstance(payload, dict):
                payload = {}
            out.append(
                {
                    "seq": int(seq),
                    "event_type": kind,
                    "logical_time": event.get("logical_time"),
                    "plan_id": payload.get("plan_id"),
                    "kind": payload.get("kind"),
                    "state": payload.get("state") or payload.get("status"),
                    "reason_code": payload.get("reason_code"),
                    "passed": payload.get("passed"),
                    "delivery": payload.get("delivery"),
                    "approval": payload.get("approval"),
                }
            )
    finally:
        conn.close()
    return out


async def run(*, source: Path, output_dir: Path, rounds: int) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    clone = output_dir / "last-mile.sqlite"
    for suffix in ("", "-wal", "-shm"):
        Path(str(clone) + suffix).unlink(missing_ok=True)
    shutil.copy2(source, clone)
    started_seq = drive.current_seq(clone)
    usage_from = drive.current_usage_id(clone)
    session = await drive.open_session(
        database=clone, output_dir=output_dir, enable_media=True
    )
    host = session.host._host  # noqa: SLF001
    steps: list[dict[str, Any]] = []
    report: dict[str, Any] = {"clone": str(clone), "started_seq": started_seq}
    try:
        for index in range(rounds):
            if index:
                # Action leases expire on the world clock.  A frozen clock
                # keeps a lease taken by the previous process alive forever,
                # so nudge time far less than the send window's 30 minutes.
                await session.tick_to(
                    (await session.logical_time()) + timedelta(seconds=45),
                    reason=f"last-mile-{index}",
                    run_life=False,
                )
            logical_time = await session.logical_time()
            trace = f"trace:last-mile:{index}"
            correlation = f"correlation:last-mile:{index}"
            results = await host.drain_media_results_once(logical_time=logical_time)
            continuation = await host.drain_media_continuation_once(
                logical_time=logical_time, trace_id=trace, correlation_id=correlation
            )
            delivery = await host.drain_media_auto_delivery_once(
                trace_id=trace, correlation_id=correlation
            )
            pumped = await session.drain(actions=4, background=0)
            steps.append(
                {
                    "round": index + 1,
                    "results": results,
                    "continuation": getattr(continuation, "status", continuation),
                    "delivery_status": getattr(delivery, "status", None),
                    "delivery_shared": getattr(delivery, "delivery_shared", None),
                    "delivery_action": getattr(delivery, "action_id", None),
                    "delivery_action_status": getattr(delivery, "action_status", None),
                    "action_statuses": pumped["action_statuses"],
                }
            )
            events = tail_events(clone, started_seq)
            if any(item["event_type"] == "MediaDeliveryShared" for item in events):
                break
        report["steps"] = steps
        report["events"] = tail_events(clone, started_seq)
        report["visible"] = session.delivery.sent
        images = []
        for position, unit in enumerate(session.delivery.sent):
            if unit.get("kind") != "image":
                continue
            source_path = Path(str(unit.get("body") or ""))
            if not source_path.is_file():
                images.append({"missing": str(source_path)})
                continue
            target = output_dir / "delivered" / f"delivered-{position + 1}{source_path.suffix or '.png'}"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target)
            images.append(
                {
                    "path": str(target.resolve()),
                    "bytes": target.stat().st_size,
                    "provider_path": str(source_path.resolve()),
                }
            )
        report["images"] = images
        report["cost"] = drive.cost_report(clone, since_id=usage_from)
        kinds = {item["event_type"] for item in report["events"]}
        report["status"] = (
            "shared"
            if "MediaDeliveryShared" in kinds
            else "approved"
            if "MediaAutomaticDeliveryApproved" in kinds
            else "stuck"
        )
        return report
    except Exception as exc:
        report["status"] = "error"
        report["error"] = {"type": type(exc).__name__, "message": str(exc)[:3000]}
        report["traceback"] = traceback.format_exc()[-4000:]
        report["steps"] = steps
        report["events"] = tail_events(clone, started_seq)
        return report
    finally:
        await session.close()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument(
        "--output-dir", type=Path, default=REPO / "output" / "first-real-photo-2"
    )
    parser.add_argument("--rounds", type=int, default=8)
    args = parser.parse_args()
    report = await run(
        source=args.source.resolve(),
        output_dir=args.output_dir.resolve(),
        rounds=args.rounds,
    )
    dump_json(args.output_dir.resolve() / "last-mile.json", report)
    print(
        json.dumps(
            {
                "status": report.get("status"),
                "images": report.get("images"),
                "cost": report.get("cost"),
                "event_types": sorted({e["event_type"] for e in report.get("events", [])}),
                "error": report.get("error"),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
