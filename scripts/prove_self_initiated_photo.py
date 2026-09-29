#!/usr/bin/env python3
"""Prove she can name a photo herself, without a host-initiated occasion.

Never writes ``data/``. Artifacts: ``output/self-initiated-photo/``.
Budget cap ¥8. At most one paid image.
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
from prove_first_photo import ledger_media_evidence
from prove_first_real_photo import (
    copy_capture_images,
    extract_payload_images,
    her_visible_texts,
    inbound_at_now,
    media_request_opened,
    open_session,
    pump_until_image,
)

WORLD_ID = drive.WORLD_ID
OUTPUT = (REPO / "output" / "self-initiated-photo").resolve()
SOURCE_CLONE = REPO / "output" / "photo-visibility" / "clone.sqlite"
ASK = "书店那张照片你说过要整理好发我的，现在能发一张吗"
WATCH = (
    "ExpressionPlanAccepted",
    "TriggerProcessOpened",
    "MediaSelectionAttemptRecorded",
    "MediaSelectionProposalRecorded",
    "MediaOpportunityFrozen",
    "MediaPlanRecorded",
    "MediaRenderArtifactRecorded",
    "MediaInspectionRecorded",
    "MediaAutomaticDeliveryApproved",
    "MediaDeliveryShared",
    "TechnicalFailureRecorded",
)


def dump_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def latest_expression_media(database: Path) -> dict[str, Any] | None:
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
            "media_request": expression.get("media_request"),
            "media_source_refs": expression.get("media_source_refs") or [],
            "timing_choice": expression.get("timing_choice"),
            "beats": expression.get("beats"),
        }
    finally:
        conn.close()


def event_chain(database: Path, after_seq: int) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    out: list[dict[str, Any]] = []
    try:
        for seq, raw in conn.execute(
            "SELECT ledger_sequence, event_json FROM world_v2_events "
            "WHERE world_id = ? AND ledger_sequence > ? ORDER BY ledger_sequence",
            (WORLD_ID, after_seq),
        ):
            event = json.loads(raw)
            event_type = event.get("event_type")
            if event_type not in WATCH:
                continue
            payload = event.get("payload_json")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = {}
            row: dict[str, Any] = {"seq": int(seq), "event_type": event_type}
            if event_type == "TriggerProcessOpened":
                process = payload.get("process") if isinstance(payload, dict) else {}
                row["process_kind"] = (
                    process.get("process_kind") if isinstance(process, dict) else None
                )
            if event_type == "TechnicalFailureRecorded":
                row["reason"] = (
                    payload.get("reason_code")
                    or payload.get("failure_code")
                    or payload.get("detail")
                    if isinstance(payload, dict)
                    else None
                )
            if event_type in {
                "MediaSelectionAttemptRecorded",
                "MediaSelectionProposalRecorded",
            }:
                occasion = payload.get("occasion") if isinstance(payload, dict) else None
                if isinstance(occasion, dict):
                    row["occasion_kind"] = occasion.get("kind")
                elif isinstance(payload, dict):
                    row["occasion_kind"] = payload.get("occasion_kind") or payload.get(
                        "kind"
                    )
            out.append(row)
    finally:
        conn.close()
    return out


async def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    clone = OUTPUT / "clone.sqlite"
    drive.clone_ledger(SOURCE_CLONE, clone)
    session = await open_session(database=clone, output_dir=OUTPUT)
    usage_from = drive.current_usage_id(clone)
    started_seq = session.projection().ledger_sequence
    generated = OUTPUT / "captured"
    try:
        inbound = await inbound_at_now(session, ASK)
        projection = session.projection()
        manifests = [
            {
                "acceptance_event_ref": getattr(item, "acceptance_event_ref", None),
                "media_request": getattr(item, "media_request", None),
            }
            for item in getattr(projection, "expression_plan_manifests", ()) or ()
            if getattr(item, "media_request", "none") != "none"
        ]
        expression = latest_expression_media(clone)
        named = bool(
            expression
            and expression.get("media_request") == "consider_available_candidate"
            and expression.get("media_source_refs")
        ) or any(item.get("media_request") == "consider_available_candidate" for item in manifests)
        after_inbound = {
            "his_text": ASK,
            "inbound": {
                "status": inbound.get("status"),
                "her_texts": her_visible_texts(inbound.get("visible") or []),
            },
            "expression": expression,
            "manifests_with_media_request": manifests[-6:],
            "media_request_processes": media_request_opened(clone, started_seq),
            "events_after_inbound": event_chain(clone, started_seq),
        }
        dump_json(OUTPUT / "after_inbound.json", after_inbound)
        pump = None
        if named or after_inbound["media_request_processes"]:
            pump = await pump_until_image(
                session,
                clone=clone,
                started_seq=started_seq,
                generated_dir=generated,
            )
        images = extract_payload_images(clone, generated)
        copied = copy_capture_images(session.delivery.sent, OUTPUT / "captured-named")
        report = {
            "his_text": ASK,
            "her_texts": after_inbound["inbound"]["her_texts"],
            "she_named_a_candidate": named,
            "expression": expression,
            "manifests_with_media_request": manifests[-6:],
            "media_request_processes": after_inbound["media_request_processes"],
            "event_chain": event_chain(clone, started_seq),
            "media_evidence": ledger_media_evidence(clone, after_seq=started_seq),
            "pump": pump,
            "images": images,
            "copied": copied,
            "cost": drive.cost_report(clone, since_id=usage_from),
        }
        dump_json(OUTPUT / "report.json", report)
        print(json.dumps(
            {
                "she_named_a_candidate": named,
                "her_texts": report["her_texts"],
                "intents": expression,
                "event_types": [item["event_type"] for item in report["event_chain"]],
                "occasion_kinds": [
                    item.get("occasion_kind")
                    for item in report["event_chain"]
                    if item.get("occasion_kind")
                ],
                "delivered": any(
                    item["event_type"] == "MediaDeliveryShared"
                    for item in report["event_chain"]
                ),
                "cost": report["cost"],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ))
    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())
