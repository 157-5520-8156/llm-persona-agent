#!/usr/bin/env python3
"""Three-turn clone dialogue proving appraisal continuity after compaction.

Never writes ``data/``. Artifacts: ``output/appraisal-compaction/dialogue/``.
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

import drive_production_lanes as drive
import prove_reaction_names as reaction_proof
from companion_daemon.world_v2.qq_ingress_policy import QQIngressFragment

OUTPUT = (REPO / "output" / "appraisal-compaction" / "dialogue").resolve()


def _visible_text(session: drive.DriveSession, *, after: int) -> list[str]:
    return [
        str(item["body"])
        for item in session.delivery.sent[after:]
        if item.get("kind") == "text"
    ]


async def _run_turns() -> dict[str, Any]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    clone = OUTPUT / "continuity.sqlite"
    drive.clone_ledger(drive.PRODUCTION_DB, clone)
    usage_from = drive.current_usage_id(clone)
    session, _capturing = await reaction_proof._open_capturing_session(clone=clone)
    turns: list[dict[str, Any]] = []
    try:
        when = await session.logical_time()
        cases = (
            {
                "name": "sun_reaction",
                "kind": "reaction",
                "payload": QQIngressFragment(
                    source_event_id=f"appraisal-clone-sun-{time.time_ns()}",
                    recipient_id=session.recipient_id,
                    observed_at=when + timedelta(seconds=3),
                    content_shape="reaction",
                    reaction_refs=("qq-face:74",),
                ),
            },
            {
                "name": "mood_followup",
                "kind": "text",
                "text": "你今天心情好像不错？",
            },
            {
                "name": "book_thread",
                "kind": "text",
                "text": "你上次说高三看张嘉佳那本书，后来有再翻吗",
            },
        )
        for case in cases:
            before = len(session.delivery.sent)
            when = when + timedelta(seconds=5)
            if case["kind"] == "reaction":
                result = await session.host.inbound_fragment(case["payload"])
                user_input = {"content_shape": "reaction", "reaction_refs": ["qq-face:74"]}
            else:
                text = str(case["text"])
                result = await session.host.inbound_text(
                    message_id=f"appraisal-clone-{case['name']}-{time.time_ns()}",
                    recipient_id=session.recipient_id,
                    text=text,
                    observed_at=when,
                )
                user_input = {"content_shape": "text", "text": text}
            turns.append(
                {
                    "name": case["name"],
                    "input": user_input,
                    "status": getattr(result, "status", None),
                    "her_text": _visible_text(session, after=before),
                }
            )
        return {
            "turns": turns,
            "cost": drive.cost_report(clone, since_id=usage_from),
            "ended_seq": drive.current_seq(clone),
        }
    finally:
        await session.close()


def main() -> None:
    report = asyncio.run(_run_turns())
    path = OUTPUT / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
