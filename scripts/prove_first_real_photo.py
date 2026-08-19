#!/usr/bin/env python3
"""Drive one real ordinary-lane photo on a production-follow clone.

Never writes ``data/``, never talks to 8787 / NapCat, never POSTs Civitai.
Uses the live OpenAI ordinary image path. Caps: 2 paid images, ¥10 model.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
import json
import logging
from pathlib import Path
import shutil
import sqlite3
import sys
import time
import traceback
from typing import Any

REPO = Path(__file__).resolve().parents[1]
for path in (REPO / "src", REPO / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import drive_production_lanes as drive
from prove_first_photo import ProveSession, ledger_media_evidence

OUTPUT = (REPO / "output" / "first-real-photo-2").resolve()
PRODUCTION_DB = drive.PRODUCTION_DB
WORLD_ID = drive.WORLD_ID
COST_CAP_CNY = 10.0
MAX_IMAGES = 2
ASK_TEXT = "书店那张照片你说过要整理好发我的，现在能发一张吗"
_LOG = logging.getLogger("prove-first-real-photo")

WATCH = (
    "PhotoCandidateOpened",
    "MediaSelectionAttemptRecorded",
    "MediaSelectionProposalRecorded",
    "MediaOpportunityFrozen",
    "MediaPlanRecorded",
    "MediaNotRenderableRecorded",
    "MediaRenderArtifactRecorded",
    "MediaInspectionRecorded",
    "MediaPreviewGenerated",
    "MediaPreviewFailed",
    "MediaAutomaticDeliveryApproved",
    "MediaDeliveryShared",
    "TriggerProcessOpened",
    "ImageEvidenceDeclared",
    "WorldOccurrenceSettled",
    "ActivityStarted",
    "ActivityCompleted",
    "TechnicalFailureRecorded",
)


def dump_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def extract_payload_images(database: Path, dest: Path) -> list[dict[str, Any]]:
    dest.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    out: list[dict[str, Any]] = []
    try:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "world_v2_media_payload" not in tables:
            return out
        cols = [item[1] for item in conn.execute("PRAGMA table_info(world_v2_media_payload)")]
        for row in conn.execute("SELECT * FROM world_v2_media_payload"):
            item = dict(zip(cols, row))
            body = item.get("body")
            if not isinstance(body, (bytes, memoryview)):
                continue
            blob = bytes(body)
            suffix = ".bin"
            if blob.startswith(b"\x89PNG"):
                suffix = ".png"
            elif blob[:3] == b"\xff\xd8\xff":
                suffix = ".jpg"
            elif blob[:4] == b"RIFF" and b"WEBP" in blob[:16]:
                suffix = ".webp"
            name = str(item.get("payload_ref") or item.get("payload_hash") or "payload")
            safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)[-80:]
            path = dest / f"{safe}{suffix}"
            path.write_bytes(blob)
            out.append(
                {
                    "path": str(path.resolve()),
                    "bytes": len(blob),
                    "suffix": suffix,
                    "content_type": item.get("content_type"),
                    "payload_ref": item.get("payload_ref"),
                }
            )
    finally:
        conn.close()
    return out


EVENT_MEDIA_DIR = REPO / "output" / "event-media"
RUN_STARTED_AT = time.time()


def collect_rendered_images(dest: Path) -> list[dict[str, Any]]:
    """Copy every artefact the renderer wrote to disk during this run.

    The ledger sidecar table only holds plan/artifact/inspection JSON; the
    bytes themselves land in the event-media store.
    """

    dest.mkdir(parents=True, exist_ok=True)
    out: list[dict[str, Any]] = []
    if not EVENT_MEDIA_DIR.is_dir():
        return out
    for source in sorted(EVENT_MEDIA_DIR.iterdir()):
        if not source.is_file() or source.stat().st_mtime < RUN_STARTED_AT:
            continue
        target = dest / source.name
        shutil.copy2(source, target)
        out.append(
            {
                "path": str(target.resolve()),
                "bytes": target.stat().st_size,
                "source": str(source.resolve()),
                "origin": "event_media_store",
            }
        )
    return out


def copy_capture_images(sent: list[dict[str, Any]], dest: Path) -> list[dict[str, Any]]:
    dest.mkdir(parents=True, exist_ok=True)
    out: list[dict[str, Any]] = []
    for index, unit in enumerate(sent):
        if unit.get("kind") != "image":
            continue
        source = Path(str(unit.get("body") or ""))
        if not source.is_file():
            out.append({"missing": str(source), "unit": unit})
            continue
        target = dest / f"captured-{index + 1}{source.suffix.lower() or '.png'}"
        shutil.copy2(source, target)
        out.append(
            {
                "path": str(target.resolve()),
                "bytes": target.stat().st_size,
                "source": str(source.resolve()),
            }
        )
    return out


def inspect_projection(session: ProveSession) -> dict[str, Any]:
    projection = session.projection()
    if projection is None:
        return {"error": "no_projection"}
    candidates = []
    for item in getattr(projection, "photo_candidates", ()) or ():
        candidates.append(
            {
                "candidate_id": getattr(item, "candidate_id", None),
                "status": getattr(item, "status", None),
                "family": getattr(item, "family", None),
                "privacy_ceiling": getattr(item, "privacy_ceiling", None),
                "opened_at": getattr(item, "opened_at", None),
                "expires_at": getattr(item, "expires_at", None),
                "ecology_category": getattr(item, "ecology_category", None),
                "source_event_refs": list(getattr(item, "source_event_refs", ()) or ()),
            }
        )
    plans = []
    for item in getattr(projection, "plans", ()) or ():
        plans.append(
            {
                "plan_id": getattr(item, "plan_id", None),
                "status": getattr(item, "status", None),
                "activity_kind": getattr(item, "activity_kind", None),
            }
        )
    relationship = None
    for item in getattr(projection, "relationships", ()) or ():
        relationship = {
            "stage": getattr(item, "stage", None) or getattr(item, "we_are", None),
            "trust_bp": getattr(item, "trust_bp", None),
            "closeness_bp": getattr(item, "closeness_bp", None),
        }
        break
    declined = [
        {
            "candidate_id": getattr(item, "candidate_id", None),
            "entity_revision": getattr(item, "entity_revision", None),
        }
        for item in getattr(projection, "media_declined_candidate_revisions", ()) or ()
    ]
    return {
        "logical_time": getattr(projection, "logical_time", None),
        "ledger_sequence": getattr(projection, "ledger_sequence", None),
        "world_revision": getattr(projection, "world_revision", None),
        "candidates": candidates,
        "declined": declined,
        "plans": plans,
        "relationship": relationship,
        "photo_candidate_count": len(candidates),
        "media_deliveries": len(getattr(projection, "media_deliveries", ()) or ()),
        "media_previews": len(getattr(projection, "media_previews", ()) or ()),
    }


def media_request_opened(database: Path, after_seq: int) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    out: list[dict[str, Any]] = []
    try:
        for seq, raw in conn.execute(
            "SELECT ledger_sequence, event_json FROM world_v2_events "
            "WHERE world_id = ? AND ledger_sequence > ? "
            "AND json_extract(event_json,'$.event_type') = 'TriggerProcessOpened' "
            "ORDER BY ledger_sequence",
            (WORLD_ID, after_seq),
        ):
            event = json.loads(raw)
            payload = event.get("payload_json")
            if isinstance(payload, str):
                payload = json.loads(payload)
            process = payload.get("process") if isinstance(payload, dict) else {}
            kind = (process or {}).get("process_kind") if isinstance(process, dict) else None
            if kind == "media_request" or "media" in str(kind or ""):
                out.append({"seq": int(seq), "kind": kind, "process": process})
    finally:
        conn.close()
    return out


def her_visible_texts(sent: list[dict[str, Any]]) -> list[str]:
    return [
        str(unit.get("body") or "")
        for unit in sent
        if unit.get("kind") == "text" and unit.get("body")
    ]


async def open_session(*, database: Path, output_dir: Path) -> ProveSession:
    session = await drive.open_session(
        database=database, output_dir=output_dir, enable_media=True
    )
    return ProveSession(
        database=session.database,
        recipient_id=session.recipient_id,
        delivery=session.delivery,
        host=session.host,
        clock=session.clock,
    )


async def inbound_at_now(session: ProveSession, text: str) -> dict[str, Any]:
    """Commit his line at the current logical time (no ClockAdvanced)."""

    when = await session.logical_time()
    message_id = f"drive-{time.time_ns()}"
    before = len(session.delivery.sent)
    result = await session.host.inbound_text(
        message_id=message_id,
        recipient_id=session.recipient_id,
        text=text,
        observed_at=when,
    )
    session.clock = when
    return {
        "status": getattr(result, "status", None),
        "action_id": getattr(result, "action_id", None),
        "visible": session.delivery.sent[before:],
        "observed_at": when.isoformat() if hasattr(when, "isoformat") else str(when),
        "message_id": message_id,
    }


async def pump_until_image(
    session: ProveSession,
    *,
    clone: Path,
    started_seq: int,
    generated_dir: Path,
    max_rounds: int = 24,
) -> dict[str, Any]:
    drains: list[dict[str, Any]] = []
    preview_steps: list[dict[str, Any]] = []
    for index in range(4):
        platform = getattr(session.host, "_host", None)
        advance = getattr(platform, "drain_media_preview_once", None)
        if not callable(advance):
            break
        result = await advance(
            trace_id=f"trace:first-real:{index}",
            correlation_id=f"correlation:first-real:{index}",
        )
        preview_steps.append(
            {
                "status": getattr(result, "status", None),
                "reason_code": getattr(result, "reason_code", None),
                "selection": None
                if getattr(result, "selection", None) is None
                else {
                    "status": getattr(result.selection, "status", None),
                    "reason_code": getattr(result.selection, "reason_code", None),
                    "proposal_event_ref": getattr(
                        result.selection, "proposal_event_ref", None
                    ),
                },
            }
        )
        status = getattr(result, "status", None)
        reason = getattr(result, "reason_code", None)
        if status in {"idle", "blocked"} and reason in {
            "media_selection.no_conversation_occasion",
            "media_selection.no_available_candidates",
            "media_selection.recovered_decline",
            "media_preview.conductor_unavailable",
        }:
            break
        if status in {"planned", "not_renderable"}:
            break
    for round_id in range(1, max_rounds + 1):
        item = await session.drain(actions=8, background=8)
        drains.append({"round": round_id, **item})
        evidence = ledger_media_evidence(clone, after_seq=started_seq)
        kinds = {row["event_type"] for row in evidence["events"]}
        capture_images = [unit for unit in session.delivery.sent if unit.get("kind") == "image"]
        blobs = extract_payload_images(clone, generated_dir)
        blobs.extend(collect_rendered_images(generated_dir))
        if "MediaAutomaticDeliveryApproved" in kinds and (capture_images or blobs):
            return {
                "status": "delivered",
                "preview_steps": preview_steps,
                "drains_tail": drains[-6:],
                "evidence": evidence,
                "capture_images": capture_images,
                "payload_images": blobs,
            }
        if "MediaPreviewFailed" in kinds or "MediaNotRenderableRecorded" in kinds:
            return {
                "status": "failed",
                "preview_steps": preview_steps,
                "drains_tail": drains[-6:],
                "evidence": evidence,
            }
        if not item["action_statuses"] and not item["background_statuses"] and round_id >= 3:
            break
    evidence = ledger_media_evidence(clone, after_seq=started_seq)
    return {
        "status": "incomplete",
        "preview_steps": preview_steps,
        "drains_tail": drains[-8:],
        "evidence": evidence,
        "capture_images": [unit for unit in session.delivery.sent if unit.get("kind") == "image"],
        "payload_images": extract_payload_images(clone, generated_dir)
        + collect_rendered_images(generated_dir),
    }


async def maybe_tick_for_candidate(
    session: ProveSession,
    *,
    clone: Path,
    started_seq: int,
    usage_from: int,
    hours: float = 6.0,
    step_minutes: int = 45,
) -> dict[str, Any]:
    """Advance world time without raising lottery odds. Stop on a new candidate or cap."""

    ticks: list[dict[str, Any]] = []
    before = {
        row.get("event_id") or row.get("seq")
        for row in ledger_media_evidence(clone, after_seq=0)["events"]
        if row["event_type"] == "PhotoCandidateOpened"
    }
    try:
        now = await session.logical_time()
        deadline = now + timedelta(hours=hours)
        step = timedelta(minutes=step_minutes)
        while now < deadline:
            cost = drive.cost_report(clone, since_id=usage_from)
            if float(cost["cost_cny"]) >= COST_CAP_CNY:
                ticks.append({"stop": "cost_cap", "cost": cost})
                break
            target = min(now + step, deadline)
            ticks.append(await session.tick_to(target, reason="life-for-candidate"))
            await session.drain_loop(rounds=10, background=12)
            now = await session.logical_time()
            opened = [
                row
                for row in ledger_media_evidence(clone, after_seq=started_seq)["events"]
                if row["event_type"] == "PhotoCandidateOpened"
                and (row.get("event_id") or row.get("seq")) not in before
            ]
            if opened:
                return {"status": "opened", "ticks": ticks[-4:], "new_candidates": opened}
        return {"status": "no_new_candidate", "ticks": ticks[-6:]}
    except Exception as exc:
        return {
            "status": "tick_failed",
            "error": {"type": type(exc).__name__, "message": str(exc)[:2000]},
            "traceback": traceback.format_exc()[-2500:],
            "ticks": ticks[-4:],
        }


async def run_drive(*, label: str) -> dict[str, Any]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    generated = OUTPUT / f"generated-{label}"
    captured = OUTPUT / f"captured-{label}"
    generated.mkdir(parents=True, exist_ok=True)
    captured.mkdir(parents=True, exist_ok=True)
    clone = OUTPUT / f"e2e-{label}.sqlite"
    drive.clone_ledger(PRODUCTION_DB, clone)
    started_seq = drive.current_seq(clone)
    usage_from = drive.current_usage_id(clone)
    session = await open_session(database=clone, output_dir=OUTPUT)
    report: dict[str, Any] = {
        "label": label,
        "clone": str(clone),
        "started_seq": started_seq,
        "ask_text": ASK_TEXT,
        "patched_occasion": False,
    }
    try:
        report["before"] = inspect_projection(session)
        asked = await inbound_at_now(session, ASK_TEXT)
        report["inbound"] = {
            "status": asked["status"],
            "action_id": asked["action_id"],
            "observed_at": asked["observed_at"],
            "visible_texts": her_visible_texts(asked["visible"]),
            "visible": asked["visible"],
        }
        report["media_request_after_inbound"] = media_request_opened(clone, started_seq)
        cost = drive.cost_report(clone, since_id=usage_from)
        report["cost_after_inbound"] = cost
        if float(cost["cost_cny"]) >= COST_CAP_CNY:
            report["status"] = "stopped_cost_cap"
            return report
        pumped = await pump_until_image(
            session, clone=clone, started_seq=started_seq, generated_dir=generated
        )
        report["first_pump"] = {
            "status": pumped["status"],
            "preview_steps": pumped.get("preview_steps"),
            "drains_tail": pumped.get("drains_tail"),
            "event_types": sorted(
                {row["event_type"] for row in pumped.get("evidence", {}).get("events", [])}
            ),
        }
        reasons = [
            (step.get("selection") or {}).get("reason_code") or step.get("reason_code")
            for step in pumped.get("preview_steps") or []
        ]
        report["first_pump"]["reason_codes"] = reasons
        images = copy_capture_images(session.delivery.sent, captured)
        images.extend(pumped.get("payload_images") or [])
        if pumped["status"] == "delivered" and images:
            report["status"] = "photo"
            report["images"] = images
            report["after"] = inspect_projection(session)
            report["cost"] = drive.cost_report(clone, since_id=usage_from)
            return report

        declined = any(
            code in {"media_selection.model_declined", "media_selection.recovered_decline"}
            or (isinstance(code, str) and "declined" in code)
            for code in reasons
        )
        no_occasion = any(
            code == "media_selection.no_conversation_occasion" for code in reasons
        )
        report["she_declined"] = declined
        report["no_conversation_occasion"] = no_occasion

        if declined:
            report["status"] = "she_declined"
            report["note"] = "She was asked and chose no_op. Not prying her decision."
            report["after"] = inspect_projection(session)
            report["cost"] = drive.cost_report(clone, since_id=usage_from)
            return report

        # Unpatched path: keep last_spoke, tick until a new candidate opens
        # inside the 2h |last_spoke - opened_at| window, then ask again.
        ticked = await maybe_tick_for_candidate(
            session,
            clone=clone,
            started_seq=started_seq,
            usage_from=usage_from,
            hours=2.0,
            step_minutes=30,
        )
        report["tick_for_candidate"] = {
            "status": ticked.get("status"),
            "ticks_tail": ticked.get("ticks"),
            "error": ticked.get("error"),
            "new_candidates": ticked.get("new_candidates"),
        }
        cost = drive.cost_report(clone, since_id=usage_from)
        if float(cost["cost_cny"]) >= COST_CAP_CNY:
            report["status"] = "stopped_cost_cap"
            report["cost"] = cost
            return report
        asked2 = await inbound_at_now(session, "那张还在的话，现在发我一张呗")
        report["inbound2"] = {
            "status": asked2["status"],
            "visible_texts": her_visible_texts(asked2["visible"]),
        }
        pumped2 = await pump_until_image(
            session, clone=clone, started_seq=started_seq, generated_dir=generated
        )
        report["second_pump"] = {
            "status": pumped2["status"],
            "preview_steps": pumped2.get("preview_steps"),
            "event_types": sorted(
                {row["event_type"] for row in pumped2.get("evidence", {}).get("events", [])}
            ),
            "reason_codes": [
                (step.get("selection") or {}).get("reason_code") or step.get("reason_code")
                for step in pumped2.get("preview_steps") or []
            ],
        }
        images = copy_capture_images(session.delivery.sent, captured)
        images.extend(extract_payload_images(clone, generated))
        images.extend(collect_rendered_images(generated))
        report["images"] = images
        report["after"] = inspect_projection(session)
        report["cost"] = drive.cost_report(clone, since_id=usage_from)
        report["her_texts"] = her_visible_texts(session.delivery.sent)
        if images and pumped2["status"] == "delivered":
            report["status"] = "photo"
        elif report["second_pump"]["reason_codes"]:
            report["status"] = "stuck:" + ",".join(
                str(code) for code in report["second_pump"]["reason_codes"] if code
            )
        else:
            report["status"] = pumped2["status"]
        return report
    except Exception as exc:
        report["status"] = "error"
        report["error"] = {"type": type(exc).__name__, "message": str(exc)[:3000]}
        report["traceback"] = traceback.format_exc()[-4000:]
        report["cost"] = drive.cost_report(clone, since_id=usage_from)
        return report
    finally:
        await session.close()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="unpatched")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    report = await run_drive(label=args.label)
    dump_json(OUTPUT / f"e2e-{args.label}.json", report)
    print(json.dumps(
        {
            "status": report.get("status"),
            "cost": report.get("cost") or report.get("cost_after_inbound"),
            "no_conversation_occasion": report.get("no_conversation_occasion"),
            "she_declined": report.get("she_declined"),
            "inbound_texts": (report.get("inbound") or {}).get("visible_texts"),
            "images": report.get("images"),
            "first_reasons": (report.get("first_pump") or {}).get("reason_codes"),
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    ))


if __name__ == "__main__":
    asyncio.run(main())
