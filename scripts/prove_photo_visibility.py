#!/usr/bin/env python3
"""Clone conversations proving she can see real shareable-photo state.

Never writes ``data/``, never talks to NapCat. Artifacts: ``output/photo-visibility/``.
Budget cap ¥12. At most one paid image.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta
import json
import logging
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
from companion_daemon.world_v2.character_interior.snapshot_compiler import (
    compile_inner_life_snapshot,
)
from companion_daemon.world_v2.context_resolver import query_from_projection
from companion_daemon.world_v2.photographable_inventory import (
    install_shareable_photos_context,
)
from prove_first_photo import ProveSession, ledger_media_evidence
from prove_first_real_photo import (
    copy_capture_images,
    extract_payload_images,
    her_visible_texts,
    inbound_at_now,
    inspect_projection,
    media_request_opened,
    open_session,
    pump_until_image,
)

WORLD_ID = drive.WORLD_ID
OUTPUT = (REPO / "output" / "photo-visibility").resolve()
HOPS_CLONE = OUTPUT / "clone.sqlite"
COST_CAP_CNY = 12.0
MAX_IMAGES = 1
_LOG = logging.getLogger("prove-photo-visibility")

SENDABLE_TURNS = (
    "书店那张照片你说过要整理好发我的，现在能发一张吗",
    "你说整理好了，图呢",
    "那就现在发，别再说明天",
    "还在相册里的话发一张就行",
    "我还在等那张图",
)
EXPIRED_TURNS = (
    "你不是说照片整理好要发我吗，现在到底能不能发",
)


def dump_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def application_of(session: drive.DriveSession):
    platform = getattr(session.host, "_host", None)
    return getattr(platform, "_application", None)


def compile_visible_inventory(session: drive.DriveSession) -> dict[str, Any]:
    projection = session.projection()
    if projection is None:
        return {"error": "no_projection"}
    application = application_of(session)
    interior = getattr(application, "_character_interior", None)
    delegate = getattr(getattr(interior, "_projection", None), "_delegate", None)
    capsules = getattr(delegate, "capsules", None)
    if capsules is None:
        return {"error": "no_capsule_compiler"}
    trigger = None
    for ref in reversed(getattr(projection, "committed_world_event_refs", ()) or ()):
        event_id = getattr(ref, "event_id", None)
        if isinstance(event_id, str) and event_id:
            trigger = event_id
            break
    if trigger is None:
        return {"error": "no_trigger"}
    query = query_from_projection(
        projection, actor_ref="agent:companion", trigger_ref=trigger
    )
    capsule = capsules.compile(query)
    context = json.loads(capsule.model_content_json)
    context = install_shareable_photos_context(context, projection)
    typed = compile_inner_life_snapshot(context)
    materials = json.loads(typed.materials_json)
    inventory = materials.get("moments_i_can_share") or {}
    return {
        "available_count": inventory.get("available_count"),
        "already_sent_count": inventory.get("already_sent_count"),
        "items": inventory.get("items") or [],
        "now": inventory.get("now"),
        "world_life": (context.get("slices") or {}).get("world_life", {}).get("availability"),
    }


def latest_turn_moments(database: Path) -> dict[str, Any] | None:
    conn = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    try:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "world_v2_character_interior_turns" not in tables:
            return None
        row = conn.execute(
            "SELECT purpose, snapshot_id, authored_state_json, updated_at "
            "FROM world_v2_character_interior_turns "
            "WHERE world_id = ? AND authored_state_json IS NOT NULL "
            "ORDER BY updated_at DESC LIMIT 1",
            (WORLD_ID,),
        ).fetchone()
        if row is None:
            return None
        authored = json.loads(row[2]) if row[2] else {}
        snapshot = authored.get("snapshot") if isinstance(authored, dict) else {}
        materials = {}
        if isinstance(snapshot, dict):
            materials = snapshot.get("materials") or {}
            if not materials and isinstance(snapshot.get("materials_json"), str):
                materials = json.loads(snapshot["materials_json"])
        moments = materials.get("moments_i_can_share") if isinstance(materials, dict) else None
        decision = authored.get("decision") if isinstance(authored, dict) else None
        media_request = None
        if isinstance(decision, dict):
            media_request = decision.get("media_request")
            expression = decision.get("expression_draft")
            if isinstance(expression, dict) and media_request is None:
                media_request = expression.get("media_request")
        return {
            "purpose": row[0],
            "snapshot_id": row[1],
            "updated_at": row[3],
            "moments_i_can_share": moments,
            "media_request": media_request,
        }
    finally:
        conn.close()


def media_event_types(database: Path, *, after_seq: int) -> list[str]:
    evidence = ledger_media_evidence(database, after_seq=after_seq)
    return [row["event_type"] for row in evidence.get("events") or []]


def latest_expiry(session: drive.DriveSession):
    projection = session.projection()
    times = [
        getattr(item, "expires_at", None)
        for item in getattr(projection, "photo_candidates", ()) or ()
    ]
    valid = [item for item in times if item is not None]
    return max(valid) if valid else None


async def one_turn(
    session: ProveSession,
    *,
    clone: Path,
    text: str,
    usage_from: int,
) -> dict[str, Any]:
    before = compile_visible_inventory(session)
    asked = await inbound_at_now(session, text)
    after = compile_visible_inventory(session)
    cost = drive.cost_report(clone, since_id=usage_from)
    return {
        "text": text,
        "status": asked["status"],
        "her_texts": her_visible_texts(asked["visible"]),
        "visible_kinds": [unit.get("kind") for unit in asked["visible"]],
        "inventory_before": before,
        "inventory_after": after,
        "turn_snapshot": latest_turn_moments(clone),
        "cost_cny": cost.get("cost_cny"),
    }


async def run_sendable(*, source: Path) -> dict[str, Any]:
    clone = OUTPUT / "conv.sqlite"
    drive.clone_ledger(source, clone)
    started_seq = drive.current_seq(clone)
    usage_from = drive.current_usage_id(clone)
    session = await open_session(database=clone, output_dir=OUTPUT)
    generated = OUTPUT / "generated-conv"
    captured = OUTPUT / "captured-conv"
    generated.mkdir(parents=True, exist_ok=True)
    captured.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "clone": str(clone),
        "started_seq": started_seq,
        "turns": [],
        "pump": None,
    }
    try:
        report["before"] = inspect_projection(session)
        report["inventory_before"] = compile_visible_inventory(session)
        first, *later = SENDABLE_TURNS
        turn = await one_turn(session, clone=clone, text=first, usage_from=usage_from)
        report["turns"].append(turn)
        _LOG.info("sendable turn her=%s", turn["her_texts"])
        report["media_request_events"] = media_request_opened(clone, started_seq)
        cost = drive.cost_report(clone, since_id=usage_from)
        if float(cost["cost_cny"]) < COST_CAP_CNY:
            pumped = await pump_until_image(
                session,
                clone=clone,
                started_seq=started_seq,
                generated_dir=generated,
            )
            report["pump"] = {
                "status": pumped["status"],
                "preview_steps": pumped.get("preview_steps"),
                "event_types": sorted(
                    {row["event_type"] for row in pumped.get("evidence", {}).get("events", [])}
                ),
                "reason_codes": [
                    (step.get("selection") or {}).get("reason_code") or step.get("reason_code")
                    for step in pumped.get("preview_steps") or []
                ],
            }
            images = copy_capture_images(session.delivery.sent, captured)
            images.extend(pumped.get("payload_images") or [])
            images.extend(extract_payload_images(clone, generated))
            report["images"] = images
        for text in later:
            cost = drive.cost_report(clone, since_id=usage_from)
            if float(cost["cost_cny"]) >= COST_CAP_CNY:
                report["stopped"] = "cost_cap"
                break
            turn = await one_turn(
                session, clone=clone, text=text, usage_from=usage_from
            )
            report["turns"].append(turn)
            _LOG.info("sendable turn her=%s", turn["her_texts"])
        report["media_request_events"] = media_request_opened(clone, started_seq)
        report["after"] = inspect_projection(session)
        report["media_event_types"] = media_event_types(clone, after_seq=started_seq)
        report["her_texts"] = her_visible_texts(session.delivery.sent)
        report["cost"] = drive.cost_report(clone, since_id=usage_from)
        kinds = set(report.get("media_event_types") or [])
        if report.get("images") and "MediaAutomaticDeliveryApproved" in kinds:
            report["status"] = "delivered"
        elif any(name.startswith("MediaSelection") for name in kinds):
            report["status"] = "selected_not_delivered"
        else:
            report["status"] = "talk_only"
        return report
    finally:
        await session.close()


async def run_expired(*, source: Path, usage_from_offset: int) -> dict[str, Any]:
    clone = OUTPUT / "expired.sqlite"
    drive.clone_ledger(source, clone)
    started_seq = drive.current_seq(clone)
    usage_from = drive.current_usage_id(clone)
    raw = await drive.open_session(
        database=clone, output_dir=OUTPUT, enable_media=False
    )
    session = ProveSession(
        database=raw.database,
        recipient_id=raw.recipient_id,
        delivery=raw.delivery,
        host=raw.host,
        clock=raw.clock,
    )
    report: dict[str, Any] = {
        "clone": str(clone),
        "started_seq": started_seq,
        "turns": [],
    }
    try:
        report["inventory_before_tick"] = compile_visible_inventory(session)
        expires = latest_expiry(session)
        now = await session.logical_time()
        if expires is None:
            report["status"] = "no_expiry"
            return report
        target = expires + timedelta(minutes=5)
        if target <= now:
            report["tick"] = {"status": "already_past_expiry", "logical_time": now.isoformat()}
        else:
            report["tick"] = await session.tick_to(
                target, reason="expire-shareable-photos", run_life=False
            )
        report["inventory_after_tick"] = compile_visible_inventory(session)
        for text in EXPIRED_TURNS:
            cost = drive.cost_report(clone, since_id=usage_from)
            if float(cost["cost_cny"]) + float(usage_from_offset) >= COST_CAP_CNY:
                report["status"] = "stopped_cost_cap"
                break
            turn = await one_turn(
                session, clone=clone, text=text, usage_from=usage_from
            )
            report["turns"].append(turn)
        report["her_texts"] = her_visible_texts(session.delivery.sent)
        report["cost"] = drive.cost_report(clone, since_id=usage_from)
        report["status"] = "expired_asked"
        return report
    finally:
        await session.close()


async def main_async(*, skip_clone: bool) -> dict[str, Any]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    source = HOPS_CLONE
    if not skip_clone or not source.exists():
        drive.clone_ledger(drive.PRODUCTION_DB, source)
    sendable = await run_sendable(source=source)
    spent = float((sendable.get("cost") or {}).get("cost_cny") or 0)
    expired = None
    if spent < COST_CAP_CNY - 0.4:
        expired = await run_expired(source=source, usage_from_offset=spent)
    out = {
        "sendable": sendable,
        "expired": expired,
        "cost_cny": round(
            spent + float(((expired or {}).get("cost") or {}).get("cost_cny") or 0),
            4,
        ),
    }
    dump_json(OUTPUT / "conversations.json", out)
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-clone", action="store_true")
    args = parser.parse_args()
    started = time.time()
    report = asyncio.run(main_async(skip_clone=args.skip_clone))
    summary = {
        "elapsed_s": round(time.time() - started, 1),
        "sendable_status": (report.get("sendable") or {}).get("status"),
        "sendable_her_texts": (report.get("sendable") or {}).get("her_texts"),
        "sendable_media_events": (report.get("sendable") or {}).get("media_event_types"),
        "expired_her_texts": (report.get("expired") or {}).get("her_texts"),
        "expired_available_after_tick": (
            ((report.get("expired") or {}).get("inventory_after_tick") or {}).get(
                "available_count"
            )
        ),
        "cost_cny": report.get("cost_cny"),
    }
    dump_json(OUTPUT / "conversations-summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
