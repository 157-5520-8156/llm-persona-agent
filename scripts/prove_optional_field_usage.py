#!/usr/bin/env python3
"""Clone verification: do optional slim fields start getting used after usage lift?

Never writes ``data/``. Output: ``output/optional-field-usage/``. Budget cap ¥2.5.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import timedelta
import json
from pathlib import Path
import sys
import time
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import drive_production_lanes as drive  # noqa: E402

import importlib.util

_SWITCH_PATH = REPO / "scripts" / "probe_her_switches.py"
_switch_spec = importlib.util.spec_from_file_location("probe_her_switches", _SWITCH_PATH)
switches = importlib.util.module_from_spec(_switch_spec)
assert _switch_spec.loader is not None
_switch_spec.loader.exec_module(switches)
extract_switch_fields = switches.extract_switch_fields

OUTPUT = (REPO / "output" / "optional-field-usage").resolve()
COST_CAP_CNY = 2.5

TARGET_FIELDS = (
    "we_are",
    "calling_it",
    "said_as",
    "photo",
    "waiting_for",
    "wait",
    "later",
    "come_back",
    "us_deltas",
    "declared_display",
    "matters_bp",
    "stuck_with_me",
)

TRIALS = (
    ("later_thoughtful", "你刚才那段话我需要认真想一下，不是不想回你"),
    ("waiting_emoji", "你猜我刚发那个表情是什么意思"),
    ("photo_request", "有张合适的照片吗，发我看看？"),
    ("relationship_stage", "我们现在到底算什么关系啊"),
    ("matters_to_her", "你别把这件事当成随口一说，对我真的很重要"),
    ("relationship_move", "你刚才那样对我，我确实更靠近你一点了"),
    ("calm_plain", "嗯 知道啦"),
    ("come_back_thread", "书店那件事我先不说了，回头再跟你讲"),
)


def _parsed_events(path: Path, *, after_sequence: int = 0) -> list[dict[str, Any]]:
    import sqlite3

    out: list[dict[str, Any]] = []
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        for row in conn.execute(
            "SELECT ledger_sequence, event_json FROM world_v2_events "
            "WHERE ledger_sequence > ? ORDER BY ledger_sequence",
            (after_sequence,),
        ):
            event = json.loads(row["event_json"])
            raw_payload = event.get("payload_json")
            try:
                payload = json.loads(raw_payload) if isinstance(raw_payload, str) else {}
            except json.JSONDecodeError:
                payload = {}
            out.append(
                {
                    "ledger_sequence": int(row["ledger_sequence"]),
                    "event_type": event.get("event_type"),
                    "payload": payload,
                }
            )
    finally:
        conn.close()
    return out


def _field_hits(events: list[dict[str, Any]]) -> dict[str, Any]:
    hits: dict[str, Any] = {}
    visible: list[str] = []
    for event in events:
        if event.get("event_type") != "ProposalRecorded":
            continue
        payload = event.get("payload") or {}
        if payload.get("proposal_kind") != "decision":
            continue
        try:
            proposal = json.loads(payload.get("proposal_json") or "{}")
        except json.JSONDecodeError:
            continue
        merged = extract_switch_fields(proposal)
        for key in TARGET_FIELDS:
            value = merged.get(key)
            if value is not None and value != "" and value != []:
                hits.setdefault(key, value)
        for change in proposal.get("proposed_changes") or []:
            if change.get("kind") != "expression_plan_transition":
                continue
            canonical = json.loads((change.get("payload") or {}).get("canonical_json") or "{}")
            for beat in canonical.get("beat_drafts") or []:
                text = beat.get("inline_text")
                if isinstance(text, str) and text.strip():
                    visible.append(text.strip())
    return {"fields": hits, "visible_texts": visible}


async def _capture(session: drive.DriveSession, message: str) -> dict[str, Any]:
    when = session.clock + timedelta(seconds=2)
    message_id = f"optional-field-{time.time_ns()}"
    before = len(session.delivery.sent)
    result = await session.host.inbound_text(
        message_id=message_id,
        recipient_id=session.recipient_id,
        text=message,
        observed_at=when,
    )
    session.clock = when
    await session.drain(actions=8, background=0)
    return {
        "status": getattr(result, "status", None),
        "visible": session.delivery.sent[before:],
        "message_id": message_id,
    }


async def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    source = drive.PRODUCTION_DB.resolve()
    trials: list[dict[str, Any]] = []
    spent = 0.0
    aggregate_hits: Counter[str] = Counter()

    for index, (category, message) in enumerate(TRIALS, start=1):
        if spent >= COST_CAP_CNY:
            break
        trial_dir = OUTPUT / f"trial-{index:02d}"
        clone = trial_dir / "clone.sqlite"
        drive.clone_ledger(source, clone)
        before_sequence = drive.open_ro(clone).execute(
            "SELECT COALESCE(MAX(ledger_sequence), 0) FROM world_v2_events"
        ).fetchone()[0]
        usage_from = drive.current_usage_id(clone)
        session = await drive.open_session(
            database=clone,
            output_dir=trial_dir,
            enable_media=False,
        )
        try:
            inbound = await _capture(session, message)
        finally:
            try:
                await asyncio.wait_for(session.close(), timeout=5)
            except TimeoutError:
                pass
        events = _parsed_events(clone, after_sequence=before_sequence)
        evidence = _field_hits(events)
        cost = float((drive.cost_report(clone, since_id=usage_from) or {}).get("cost_cny") or 0)
        spent += cost
        for key in evidence["fields"]:
            aggregate_hits[key] += 1
        visible_texts = evidence["visible_texts"] or [
            item.get("text") if isinstance(item, dict) else str(item)
            for item in inbound.get("visible") or []
        ]
        trial = {
            "index": index,
            "category": category,
            "message": message,
            "status": inbound.get("status"),
            "visible_texts": visible_texts,
            "authored_fields": evidence["fields"],
            "cost_cny": cost,
        }
        trials.append(trial)
        (trial_dir / "evidence.json").write_text(
            json.dumps(trial, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    calm = next((item for item in trials if item["category"] == "calm_plain"), None)
    report = {
        "trials_completed": len(trials),
        "spent_cny": round(spent, 4),
        "cost_cap_cny": COST_CAP_CNY,
        "target_fields": list(TARGET_FIELDS),
        "fields_used_at_least_once": dict(sorted(aggregate_hits.items())),
        "fields_used_count": len(aggregate_hits),
        "calm_trial_left_empty": bool(calm and not calm.get("authored_fields")),
        "trials": trials,
    }
    (OUTPUT / "REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
