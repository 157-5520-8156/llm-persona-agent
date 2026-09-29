#!/usr/bin/env python3
"""Clone production conversations to prove facts shape understanding, not recitation.

Never writes ``data/`` and never opens a QQ/NapCat transport. Every case starts
from the same production-ledger head and uses CaptureDelivery.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import json
from pathlib import Path
import sys
import time
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import drive_production_lanes as drive  # noqa: E402
import prove_reaction_names as reaction_proof  # noqa: E402
from companion_daemon.world_v2.qq_ingress_policy import QQIngressFragment  # noqa: E402

OUTPUT = (REPO / "output" / "facts-too-loud" / "dialogue-after").resolve()


def _visible_text(session: drive.DriveSession, *, after: int) -> list[str]:
    return [
        str(item["body"])
        for item in session.delivery.sent[after:]
        if item.get("kind") == "text"
    ]


async def _run_reaction_case(*, name: str, face_ref: str) -> dict[str, Any]:
    clone = OUTPUT / f"{name}.sqlite"
    drive.clone_ledger(drive.PRODUCTION_DB, clone)
    usage_from = drive.current_usage_id(clone)
    started_seq = drive.current_seq(clone)
    session, capturing = await reaction_proof._open_capturing_session(clone=clone)
    try:
        when = await session.logical_time() + timedelta(seconds=3)
        before = len(session.delivery.sent)
        result = await session.host.inbound_fragment(
            QQIngressFragment(
                source_event_id=f"facts-background-{name}-{time.time_ns()}",
                recipient_id=session.recipient_id,
                observed_at=when,
                content_shape="reaction",
                reaction_refs=(face_ref,),
            )
        )
        return {
            "name": name,
            "input": {"content_shape": "reaction", "reaction_refs": [face_ref]},
            "status": getattr(result, "status", None),
            "her_text": _visible_text(session, after=before),
            "captures": capturing.captures,
            "started_seq": started_seq,
            "ended_seq": drive.current_seq(clone),
            "cost": drive.cost_report(clone, since_id=usage_from),
        }
    finally:
        await session.close()


async def _run_memory_case() -> dict[str, Any]:
    name = "important-fact-name"
    clone = OUTPUT / f"{name}.sqlite"
    drive.clone_ledger(drive.PRODUCTION_DB, clone)
    usage_from = drive.current_usage_id(clone)
    started_seq = drive.current_seq(clone)
    session, _capturing = await reaction_proof._open_capturing_session(clone=clone)
    try:
        when = await session.logical_time() + timedelta(seconds=3)
        before = len(session.delivery.sent)
        text = "我今天本来约朋友做什么来着？"
        result = await session.host.inbound_text(
            message_id=f"facts-background-{name}-{time.time_ns()}",
            recipient_id=session.recipient_id,
            text=text,
            observed_at=when,
        )
        return {
            "name": name,
            "input": {"content_shape": "text", "text": text},
            "status": getattr(result, "status", None),
            "her_text": _visible_text(session, after=before),
            "started_seq": started_seq,
            "ended_seq": drive.current_seq(clone),
            "cost": drive.cost_report(clone, since_id=usage_from),
        }
    finally:
        await session.close()


async def run() -> dict[str, Any]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    cases: list[dict[str, Any]] = []
    for index in range(4):
        cases.append(await _run_reaction_case(name=f"sun-{index}", face_ref="qq-face:74"))
    cases.append(await _run_reaction_case(name="other-face-tears", face_ref="qq-face:5"))
    cases.append(await _run_memory_case())
    report = {
        "method": "six independent clones of the same production ledger head; CaptureDelivery only",
        "sun_trials": cases[:4],
        "other_face_trial": cases[4],
        "important_fact_trial": cases[5],
        "total_cost_cny": round(
            sum(float(item["cost"]["cost_cny"]) for item in cases),
            6,
        ),
        "total_calls": sum(int(item["cost"]["calls"]) for item in cases),
    }
    (OUTPUT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return report


if __name__ == "__main__":
    result = asyncio.run(run())
    print(
        json.dumps(
            {
                "sun": [item["her_text"] for item in result["sun_trials"]],
                "other_face": result["other_face_trial"]["her_text"],
                "important_fact": result["important_fact_trial"]["her_text"],
                "total_cost_cny": result["total_cost_cny"],
                "total_calls": result["total_calls"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
