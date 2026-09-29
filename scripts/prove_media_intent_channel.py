#!/usr/bin/env python3
"""Prove media intent rides reply_only and full_turn on a photo-ready clone.

Never writes ``data/``, never talks to NapCat / production QQ.
Artifacts: ``output/media-intent-channel/``. Budget cap ¥6 (debug spend).
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
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
from companion_daemon.world_v2.expression_decision_channel import (
    assert_expression_decision_channel_coverage,
)
from companion_daemon.world_v2.present_prompt import (
    compile_slim_interior_envelope,
    reply_only_completion_clause,
    slim_consider_instruction,
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
from prove_photo_visibility import (
    compile_visible_inventory,
    latest_turn_moments,
    media_event_types,
)

WORLD_ID = drive.WORLD_ID
OUTPUT = (REPO / "output" / "media-intent-channel").resolve()
SOURCE_CLONE = (REPO / "output" / "photo-visibility" / "clone.sqlite").resolve()
COST_CAP_CNY = 6.0
_LOG = logging.getLogger("prove-media-intent-channel")

ASK_TURNS = (
    "书店那张照片你说过要整理好发我的，现在能发一张吗",
    "你说发我，图呢",
    "那就现在发一张，别再说明天",
    "相册里还有的话再发一张就行",
)
REFUSE_TURNS = (
    "如果你这会儿不想发图也完全没事，咱们随便聊聊你在干嘛就行",
)

MEDIA_CHAIN = (
    "MediaSelectionProposalRecorded",
    "MediaOpportunityFrozen",
    "MediaPlanRecorded",
    "MediaPreviewGenerated",
    "MediaAutomaticDeliveryApproved",
    "MediaDeliveryShared",
)


def dump_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def estimate_tokens(text: str) -> int:
    """Rough mixed CJK/ASCII token estimate for prompt delta only."""

    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = max(0, len(text) - cjk)
    return cjk + (other + 3) // 4


def measure_token_overhead() -> dict[str, Any]:
    instruction = slim_consider_instruction()
    bare = {
        "messages": ["嗯"],
        "felt": "随口应一声",
    }
    with_photo = {**bare, "photo": True}
    bare_env = compile_slim_interior_envelope(bare, reply_only=True)
    photo_env = compile_slim_interior_envelope(with_photo, reply_only=True)
    bare_json = json.dumps(bare, ensure_ascii=False, separators=(",", ":"))
    photo_json = json.dumps(with_photo, ensure_ascii=False, separators=(",", ":"))
    bare_env_json = json.dumps(bare_env, ensure_ascii=False, separators=(",", ":"))
    photo_env_json = json.dumps(photo_env, ensure_ascii=False, separators=(",", ":"))
    return {
        "slim_instruction_chars": len(instruction),
        "slim_instruction_token_est": estimate_tokens(instruction),
        "reply_only_completion_clause_chars": len(reply_only_completion_clause()),
        "payload_json_chars": {
            "bare": len(bare_json),
            "with_photo_true": len(photo_json),
            "delta": len(photo_json) - len(bare_json),
        },
        "payload_json_token_est": {
            "bare": estimate_tokens(bare_json),
            "with_photo_true": estimate_tokens(photo_json),
            "delta": estimate_tokens(photo_json) - estimate_tokens(bare_json),
        },
        "compiled_envelope_chars": {
            "bare": len(bare_env_json),
            "with_photo_true": len(photo_env_json),
            "delta": len(photo_env_json) - len(bare_env_json),
        },
        "note": (
            "photo was already an optional slim key; widening reply_only only "
            "stops rejecting it. Delta is one boolean field plus media_request "
            "on the compiled head."
        ),
    }


def interior_turn_decisions(database: Path, *, after_updated_at: str | None = None) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    try:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "world_v2_character_interior_turns" not in tables:
            return []
        rows = conn.execute(
            "SELECT purpose, snapshot_id, authored_state_json, updated_at "
            "FROM world_v2_character_interior_turns "
            "WHERE world_id = ? AND authored_state_json IS NOT NULL "
            "ORDER BY updated_at ASC",
            (WORLD_ID,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for purpose, snapshot_id, authored_json, updated_at in rows:
            if after_updated_at and str(updated_at) <= after_updated_at:
                continue
            authored = json.loads(authored_json) if authored_json else {}
            decision = authored.get("decision") if isinstance(authored, dict) else None
            media_request = None
            result_kind = None
            photo = None
            if isinstance(decision, dict):
                media_request = decision.get("media_request")
                expression = decision.get("expression_draft")
                if isinstance(expression, dict) and media_request is None:
                    media_request = expression.get("media_request")
                result_kind = decision.get("result_kind")
                photo = decision.get("photo")
                if photo is None and isinstance(expression, dict):
                    photo = expression.get("photo")
            out.append(
                {
                    "purpose": purpose,
                    "snapshot_id": snapshot_id,
                    "updated_at": updated_at,
                    "result_kind": result_kind,
                    "photo": photo,
                    "media_request": media_request,
                }
            )
        return out
    finally:
        conn.close()


def her_beat_texts(database: Path, *, after_seq: int) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    try:
        out: list[dict[str, Any]] = []
        for seq, raw in conn.execute(
            "SELECT ledger_sequence, event_json FROM world_v2_events "
            "WHERE world_id = ? AND ledger_sequence > ? ORDER BY ledger_sequence",
            (WORLD_ID, after_seq),
        ):
            event = json.loads(raw)
            if event.get("event_type") != "ExpressionBeatAuthorized":
                continue
            payload = event.get("payload_json")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    continue
            if not isinstance(payload, dict):
                continue
            beat = payload.get("beat")
            if not isinstance(beat, dict):
                continue
            text = beat.get("text")
            if not isinstance(text, str) or not text.strip():
                nested = beat.get("payload")
                if isinstance(nested, dict):
                    text = nested.get("text")
            if isinstance(text, str) and text.strip():
                out.append({"seq": int(seq), "text": text})
        return out
    finally:
        conn.close()


def event_chain(database: Path, *, after_seq: int) -> list[dict[str, Any]]:
    evidence = ledger_media_evidence(database, after_seq=after_seq)
    events = []
    for row in evidence.get("events") or []:
        events.append(
            {
                "seq": row.get("seq") or row.get("ledger_sequence"),
                "event_type": row.get("event_type"),
                "recorded_at": row.get("recorded_at"),
            }
        )
    # Also pull Action* around the same window for text-vs-media contrast.
    conn = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    try:
        for row in conn.execute(
            "SELECT ledger_sequence, event_json FROM world_v2_events "
            "WHERE world_id = ? AND ledger_sequence > ? "
            "ORDER BY ledger_sequence ASC LIMIT 200",
            (WORLD_ID, after_seq),
        ):
            seq, raw = row
            try:
                payload = json.loads(raw) if isinstance(raw, str) else {}
            except json.JSONDecodeError:
                continue
            event_type = payload.get("event_type") if isinstance(payload, dict) else None
            if event_type not in {
                "ActionAuthorized",
                "ActionDelivered",
                "ExpressionPlanAccepted",
                "TypedChangeAccepted",
            }:
                continue
            events.append(
                {
                    "seq": seq,
                    "event_type": event_type,
                    "recorded_at": (
                        payload.get("recorded_at")
                        if isinstance(payload, dict)
                        else None
                    ),
                }
            )
    finally:
        conn.close()
    events.sort(key=lambda item: int(item.get("seq") or 0))
    return events


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
        "inventory_before": {
            "available_count": before.get("available_count"),
            "already_sent_count": before.get("already_sent_count"),
        },
        "inventory_after": {
            "available_count": after.get("available_count"),
            "already_sent_count": after.get("already_sent_count"),
        },
        "turn_snapshot": latest_turn_moments(clone),
        "cost_cny": cost.get("cost_cny"),
    }


async def run_ask_lane(*, source: Path) -> dict[str, Any]:
    clone = OUTPUT / "ask.sqlite"
    drive.clone_ledger(source, clone)
    started_seq = drive.current_seq(clone)
    usage_from = drive.current_usage_id(clone)
    session = await open_session(database=clone, output_dir=OUTPUT)
    generated = OUTPUT / "generated-ask"
    captured = OUTPUT / "captured-ask"
    generated.mkdir(parents=True, exist_ok=True)
    captured.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "clone": str(clone),
        "started_seq": started_seq,
        "turns": [],
        "pump": None,
    }
    try:
        report["inventory_before"] = compile_visible_inventory(session)
        report["projection_before"] = inspect_projection(session)
        for index, text in enumerate(ASK_TURNS):
            cost = drive.cost_report(clone, since_id=usage_from)
            if float(cost["cost_cny"]) >= COST_CAP_CNY:
                report["stopped"] = "cost_cap"
                break
            turn = await one_turn(session, clone=clone, text=text, usage_from=usage_from)
            report["turns"].append(turn)
            _LOG.info("ask[%s] her=%s", index, turn["her_texts"])
            # After the first promise, try to finish one image within budget.
            if index == 0 and float(cost["cost_cny"]) < COST_CAP_CNY - 0.8:
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
                        {
                            row["event_type"]
                            for row in pumped.get("evidence", {}).get("events", [])
                        }
                    ),
                }
                images = copy_capture_images(session.delivery.sent, captured)
                images.extend(pumped.get("payload_images") or [])
                images.extend(extract_payload_images(clone, generated))
                report["images"] = images
                # If first pump delivered, later asks may be follow-ups.
        report["media_request_events"] = media_request_opened(clone, started_seq)
        report["media_event_types"] = media_event_types(clone, after_seq=started_seq)
        report["event_chain"] = event_chain(clone, after_seq=started_seq)
        report["interior_decisions"] = interior_turn_decisions(clone)
        report["her_texts"] = her_visible_texts(session.delivery.sent)
        report["cost"] = drive.cost_report(clone, since_id=usage_from)
        kinds = set(report.get("media_event_types") or [])
        missing = [name for name in MEDIA_CHAIN if name not in kinds]
        if not missing and report.get("images"):
            report["status"] = "delivered_full_chain"
        elif any(name.startswith("MediaSelection") for name in kinds):
            report["status"] = "selected_partial"
            report["missing_chain"] = missing
        else:
            report["status"] = "talk_only"
            report["missing_chain"] = missing
        return report
    finally:
        await session.close()


async def run_refuse_lane(*, source: Path, prior_spend: float) -> dict[str, Any]:
    clone = OUTPUT / "refuse.sqlite"
    drive.clone_ledger(source, clone)
    started_seq = drive.current_seq(clone)
    usage_from = drive.current_usage_id(clone)
    session = await open_session(database=clone, output_dir=OUTPUT)
    report: dict[str, Any] = {
        "clone": str(clone),
        "started_seq": started_seq,
        "turns": [],
    }
    try:
        report["inventory_before"] = compile_visible_inventory(session)
        for text in REFUSE_TURNS:
            cost = drive.cost_report(clone, since_id=usage_from)
            if prior_spend + float(cost["cost_cny"]) >= COST_CAP_CNY:
                report["stopped"] = "cost_cap"
                break
            turn = await one_turn(session, clone=clone, text=text, usage_from=usage_from)
            report["turns"].append(turn)
            _LOG.info("refuse her=%s", turn["her_texts"])
        report["media_event_types"] = media_event_types(clone, after_seq=started_seq)
        report["event_chain"] = event_chain(clone, after_seq=started_seq)
        report["interior_decisions"] = interior_turn_decisions(clone)
        report["her_texts"] = her_visible_texts(session.delivery.sent)
        report["cost"] = drive.cost_report(clone, since_id=usage_from)
        kinds = set(report.get("media_event_types") or [])
        opened = any(name.startswith("MediaSelection") for name in kinds) or (
            "MediaDeliveryShared" in kinds
        )
        # Success for refuse lane: she can answer without being forced into media.
        report["status"] = "forced_media" if opened else "no_media_forced"
        return report
    finally:
        await session.close()


def slim_capability_inventory() -> dict[str, Any]:
    """What reply_only can / cannot express after this change."""

    return {
        "reply_only_can": [
            "messages / text beats (now, multi-bubble up to limit)",
            "later + delay window (text only)",
            "silent (empty messages)",
            "felt / stuck_with_me / wants / mood / matters_bp / affect lifecycle basics",
            "waiting_for + wait",
            "come_back + come_back_in",
            "how_it_landed",
            "noticed",
            "keep_impression",
            "about_us / why_us / us_deltas",
            "we_are / calling_it / said_as",
            "declared_display",
            "photo / media_request (+ optional media_source_refs) on timing=now",
        ],
        "reply_only_cannot": [
            "interaction protocol updates",
            "typing / reaction / sticker modalities",
            "turn supersession / continuation stream",
            "photo / media_request on later or silent (visible reject)",
            "world_claims (reply_only head keeps empty list)",
        ],
        "full_turn_extra": [
            "full character-interior-events envelope",
            "interaction protocol",
            "typing / reaction / sticker",
            "continuation",
            "full affect lifecycle surface",
            "media_request with richer private_turn_state",
        ],
        "branching": (
            "result_kind is model-chosen (reply_only | full_turn | recall). "
            "_is_lossless_minimal_reply_draft is quick-recovery only, not production routing."
        ),
    }


async def main_async(*, skip_clone: bool) -> dict[str, Any]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    assert_expression_decision_channel_coverage()
    token = measure_token_overhead()
    dump_json(OUTPUT / "token_overhead.json", token)

    source = SOURCE_CLONE
    if not source.exists():
        if skip_clone:
            raise SystemExit(f"missing source clone: {source}")
        drive.clone_ledger(drive.PRODUCTION_DB, source)

    ask = await run_ask_lane(source=source)
    spent = float((ask.get("cost") or {}).get("cost_cny") or 0)
    refuse = None
    if spent < COST_CAP_CNY - 0.3:
        refuse = await run_refuse_lane(source=source, prior_spend=spent)
    out = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "capability_inventory": slim_capability_inventory(),
        "token_overhead": token,
        "ask": ask,
        "refuse": refuse,
        "cost_cny": round(
            spent + float(((refuse or {}).get("cost") or {}).get("cost_cny") or 0),
            4,
        ),
        "gate": "assert_expression_decision_channel_coverage ok",
    }
    dump_json(OUTPUT / "conversations.json", out)
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-clone", action="store_true", default=True)
    parser.add_argument("--refresh-source", action="store_true")
    args = parser.parse_args()
    started = time.time()
    report = asyncio.run(main_async(skip_clone=not args.refresh_source))
    ask = report.get("ask") or {}
    refuse = report.get("refuse") or {}
    summary = {
        "elapsed_s": round(time.time() - started, 1),
        "ask_status": ask.get("status"),
        "ask_her_texts": ask.get("her_texts"),
        "ask_media_events": ask.get("media_event_types"),
        "ask_missing_chain": ask.get("missing_chain"),
        "refuse_status": refuse.get("status"),
        "refuse_her_texts": refuse.get("her_texts"),
        "refuse_media_events": refuse.get("media_event_types"),
        "token_delta_payload": (report.get("token_overhead") or {})
        .get("payload_json_token_est", {})
        .get("delta"),
        "cost_cny": report.get("cost_cny"),
    }
    dump_json(OUTPUT / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
