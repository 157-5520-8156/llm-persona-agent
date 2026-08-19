#!/usr/bin/env python3
"""Read-only production funnel audit for the first real photo.

Never writes ``data/``. Clones the production ledger into
``output/first-real-photo/clone.sqlite`` via sqlite backup from a URI
``mode=ro`` connection.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3
import sys

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from companion_daemon.world_v2.media_selection_occasion import CONVERSATION_OCCASION_WINDOW

PRODUCTION_DB = (REPO / "data" / "companion.epoch2.sqlite").resolve()
OUTPUT = (REPO / "output" / "first-real-photo-2").resolve()
WORLD_ID = "world:companion-v2:qq-c2c:geoff"
RESTART_WALL = datetime(2026, 8, 19, 2, 37, 59, tzinfo=UTC)  # production napcat start (report)

FUNNEL_TYPES = (
    "WorldOccurrenceActivated",
    "WorldOccurrenceSettled",
    "ActivityStarted",
    "ActivityCompleted",
    "ActivityAbandoned",
    "ImageEvidenceDeclared",
    "RecipientScopedImageEvidenceDeclared",
    "PhotoCandidateOpened",
    "PhotoCandidateUnrenderable",
    "RandomDrawRecorded",
    "MediaSelectionAttemptRecorded",
    "MediaSelectionProposalRecorded",
    "MediaOpportunityFrozen",
    "MediaOpportunityAuthorized",
    "MediaPlanRecorded",
    "MediaNotRenderableRecorded",
    "MediaRenderArtifactRecorded",
    "MediaRenderFailure",
    "MediaInspectionRecorded",
    "MediaPreviewGenerated",
    "MediaPreviewFailed",
    "MediaRepairAuthorized",
    "MediaAutomaticDeliveryApproved",
    "MediaDeliveryShared",
    "ActionAuthorized",
    "ActionDelivered",
    "ActionFailed",
    "TechnicalFailureRecorded",
)

PROMISE_NEEDLES = (
    "发你",
    "发给你",
    "发一张",
    "发张",
    "发照片",
    "发图",
    "整理好",
    "拍给你",
    "拍一张",
    "照片",
    "自拍",
    "相册",
)


def payload_of(event: dict) -> dict:
    payload = event.get("payload")
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    raw = event.get("payload_json")
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def parse_dt(value: object) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def clone_ledger() -> Path:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / "clone.sqlite"
    target.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(target) + suffix).unlink(missing_ok=True)
    with sqlite3.connect(f"file:{PRODUCTION_DB}?mode=ro", uri=True) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)
    return target


def load_events(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT ledger_sequence, event_json FROM world_v2_events "
        "WHERE world_id = ? ORDER BY ledger_sequence",
        (WORLD_ID,),
    ).fetchall()
    events = []
    for seq, raw in rows:
        event = json.loads(raw)
        event["_seq"] = int(seq)
        event["_type"] = event.get("event_type")
        event["_payload"] = payload_of(event)
        event["_logical"] = parse_dt(event.get("logical_time"))
        events.append(event)
    return events


def extract_text_blobs(payload: dict) -> list[str]:
    blobs: list[str] = []
    for key in ("text", "body", "content", "message", "utterance"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            blobs.append(value)
    messages = payload.get("messages")
    if isinstance(messages, list):
        for item in messages:
            if isinstance(item, str):
                blobs.append(item)
            elif isinstance(item, dict):
                for key in ("text", "body", "content"):
                    value = item.get(key)
                    if isinstance(value, str) and value.strip():
                        blobs.append(value)
    beats = payload.get("beats")
    if isinstance(beats, list):
        for beat in beats:
            if not isinstance(beat, dict):
                continue
            for key in ("text", "body", "content"):
                value = beat.get(key)
                if isinstance(value, str) and value.strip():
                    blobs.append(value)
    draft = payload.get("draft")
    if isinstance(draft, dict):
        blobs.extend(extract_text_blobs(draft))
    return blobs


def reason_of(payload: dict) -> str | None:
    for key in (
        "reason_code",
        "outcome",
        "status",
        "failure_code",
        "error_code",
        "code",
    ):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    result = payload.get("result")
    if isinstance(result, dict):
        return reason_of(result)
    inspection = payload.get("inspection")
    if isinstance(inspection, dict):
        return reason_of(inspection)
    return None


def gate_stats(events: list[dict], type_name: str, *, after_seq: int = 0) -> dict:
    matched = [e for e in events if e["_type"] == type_name and e["_seq"] > after_seq]
    outcomes: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    samples = []
    for event in matched:
        payload = event["_payload"]
        outcome = payload.get("outcome") or payload.get("status") or payload.get("passed")
        if isinstance(outcome, bool):
            outcome = "passed" if outcome else "failed"
        if isinstance(outcome, str):
            outcomes[outcome] += 1
        reason = reason_of(payload)
        if reason:
            reasons[reason] += 1
        if len(samples) < 8:
            samples.append(
                {
                    "seq": event["_seq"],
                    "logical_time": event.get("logical_time"),
                    "event_id": event.get("event_id"),
                    "outcome": outcome,
                    "reason": reason,
                    "kind": payload.get("kind") or payload.get("taxonomy") or payload.get("category"),
                    "candidate_id": payload.get("candidate_id"),
                    "plan_id": payload.get("plan_id"),
                    "action_kind": payload.get("kind") if type_name.startswith("Action") else None,
                }
            )
    return {
        "type": type_name,
        "count": len(matched),
        "outcomes": dict(outcomes),
        "reasons": dict(reasons),
        "samples": samples,
    }


def dump_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def main() -> None:
    global OUTPUT
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    OUTPUT = parser.parse_args().output_dir.resolve()
    clone = clone_ledger()
    conn = sqlite3.connect(f"file:{clone}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        events = load_events(conn)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        usage_rows = []
        if "world_v2_model_usage" in tables:
            usage_rows = [
                dict(row)
                for row in conn.execute(
                    "SELECT id, purpose, model, status, cost_cny, recorded_at, error "
                    "FROM world_v2_model_usage ORDER BY id"
                )
            ]
        interior_rows = []
        if "world_v2_character_interior_turns" in tables:
            interior_rows = [
                dict(row)
                for row in conn.execute(
                    "SELECT inner_turn_id AS turn_id, purpose, state AS status, "
                    "updated_at, authored_state_json, terminal_result_json "
                    "FROM world_v2_character_interior_turns ORDER BY updated_at"
                )
            ]
        life_texts = {}
        if "world_v2_life_content" in tables:
            life_texts = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT content_ref, text FROM world_v2_life_content WHERE world_id = ?",
                    (WORLD_ID,),
                )
            }
        media_tables = {}
        for name in (
            "world_v2_media_payload",
            "world_v2_media_provider_dispatch",
            "world_v2_media_pending_inspection",
            "world_v2_event_media_planning_result",
            "usage_events",
        ):
            if name in tables:
                media_tables[name] = int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
            else:
                media_tables[name] = -1
    finally:
        conn.close()

    type_counts = Counter(e["_type"] for e in events)
    last = events[-1] if events else None
    head_seq = last["_seq"] if last else 0
    logical_now = last["_logical"] if last else None

    # Restart cut: first event after production start wall, or first ClockAdvanced
    # after 02:37. Use recorded_at / created if present, else logical_time.
    restart_seq = 0
    for event in events:
        recorded = parse_dt(event.get("recorded_at")) or parse_dt(
            event.get("created_at")
        )
        wall = recorded or event["_logical"]
        if wall is not None and wall >= RESTART_WALL:
            restart_seq = event["_seq"] - 1
            break

    funnel_all = {name: gate_stats(events, name) for name in FUNNEL_TYPES}
    funnel_since_restart = {
        name: gate_stats(events, name, after_seq=restart_seq) for name in FUNNEL_TYPES
    }

    action_kinds = Counter()
    action_states = Counter()
    for event in events:
        if event["_type"] != "ActionAuthorized":
            continue
        payload = event["_payload"]
        kind = payload.get("kind") or payload.get("action_kind")
        if isinstance(kind, str):
            action_kinds[kind] += 1
        state = payload.get("state")
        if isinstance(state, str):
            action_states[state] += 1

    media_action_kinds = (
        "media_planning",
        "media_render",
        "media_inspection",
        "media_delivery",
        "media_repair",
        "image_generation",
    )
    media_actions = []
    for event in events:
        if event["_type"] not in {"ActionAuthorized", "ActionDelivered", "ActionFailed"}:
            continue
        payload = event["_payload"]
        kind = payload.get("kind") or payload.get("action_kind")
        if kind not in media_action_kinds:
            continue
        media_actions.append(
            {
                "seq": event["_seq"],
                "type": event["_type"],
                "kind": kind,
                "action_id": payload.get("action_id") or payload.get("id"),
                "state": payload.get("state"),
                "reason": reason_of(payload),
                "logical_time": event.get("logical_time"),
            }
        )

    # Candidates
    candidates = []
    for event in events:
        if event["_type"] != "PhotoCandidateOpened":
            continue
        payload = event["_payload"]
        opened_at = parse_dt(payload.get("opened_at")) or event["_logical"]
        expires_at = parse_dt(payload.get("expires_at"))
        candidates.append(
            {
                "seq": event["_seq"],
                "event_id": event.get("event_id"),
                "candidate_id": payload.get("candidate_id"),
                "entity_revision": payload.get("entity_revision"),
                "taxonomy": payload.get("taxonomy") or payload.get("category"),
                "privacy_ceiling": payload.get("privacy_ceiling"),
                "opened_at": payload.get("opened_at") or event.get("logical_time"),
                "expires_at": payload.get("expires_at"),
                "source_event_refs": payload.get("source_event_refs")
                or [
                    item.get("event_ref") if isinstance(item, dict) else item
                    for item in (payload.get("source_events") or [])
                ],
                "expired_now": bool(
                    expires_at and logical_now and expires_at <= logical_now
                ),
                "fresh_vs_now_2h": bool(
                    opened_at
                    and logical_now
                    and abs(logical_now - opened_at) <= CONVERSATION_OCCASION_WINDOW
                ),
            }
        )

    declined = []
    for event in events:
        if event["_type"] != "MediaSelectionAttemptRecorded":
            continue
        payload = event["_payload"]
        declined.append(
            {
                "seq": event["_seq"],
                "outcome": payload.get("outcome"),
                "reason_code": payload.get("reason_code") or payload.get("reason"),
                "decision": payload.get("decision"),
                "candidate_id": payload.get("candidate_id"),
                "logical_time": event.get("logical_time"),
                "payload_keys": sorted(payload.keys()),
            }
        )

    # Observations / expressions mentioning photos
    photo_mentions = []
    last_user_obs = None
    last_agent_obs = None
    for event in events:
        if event["_type"] not in {
            "ObservationRecorded",
            "ExpressionAccepted",
            "ActionAuthorized",
        }:
            continue
        payload = event["_payload"]
        actor = payload.get("actor") or payload.get("actor_ref") or event.get("actor")
        blobs = extract_text_blobs(payload)
        joined = "\n".join(blobs)
        is_user = isinstance(actor, str) and actor.startswith("user:")
        is_agent = actor in {"agent:companion", "character:celia"} or (
            isinstance(actor, str) and "companion" in actor
        )
        if event["_type"] == "ObservationRecorded" and is_user:
            last_user_obs = event
        if event["_type"] == "ObservationRecorded" and blobs and not is_user:
            last_agent_obs = event
        if not any(needle in joined for needle in PROMISE_NEEDLES):
            continue
        photo_mentions.append(
            {
                "seq": event["_seq"],
                "type": event["_type"],
                "actor": actor,
                "kind": payload.get("kind"),
                "logical_time": event.get("logical_time"),
                "texts": blobs[:8],
                "is_user": is_user,
                "is_agent": is_agent,
            }
        )

    # Interior media_selection + inbound snapshots for promises
    interior_photo = []
    for row in interior_rows:
        authored = row.get("authored_state_json") or ""
        terminal = row.get("terminal_result_json") or ""
        blob = f"{authored}\n{terminal}"
        if not any(needle in blob for needle in PROMISE_NEEDLES) and row.get("purpose") not in {
            "media_selection",
        }:
            continue
        materials = {}
        try:
            authored_obj = json.loads(authored) if authored else {}
        except json.JSONDecodeError:
            authored_obj = {}
        snapshot = authored_obj.get("snapshot") or authored_obj
        if isinstance(snapshot, dict):
            mats = snapshot.get("materials") or snapshot.get("materials_json")
            if isinstance(mats, str):
                try:
                    mats = json.loads(mats)
                except json.JSONDecodeError:
                    mats = {}
            if isinstance(mats, dict):
                materials = {
                    "photos_i_shared": mats.get("photos_i_shared"),
                    "moments_i_can_share": mats.get("moments_i_can_share"),
                }
        felt = None
        if isinstance(authored_obj, dict):
            felt = authored_obj.get("felt") or (
                (authored_obj.get("decision") or {}).get("felt")
                if isinstance(authored_obj.get("decision"), dict)
                else None
            )
        interior_photo.append(
            {
                "turn_id": row.get("turn_id"),
                "purpose": row.get("purpose"),
                "status": row.get("status"),
                "updated_at": row.get("updated_at"),
                "materials": materials,
                "felt_excerpt": (felt[:400] if isinstance(felt, str) else felt),
                "mentions_photo": any(needle in blob for needle in PROMISE_NEEDLES),
            }
        )

    # Settled occurrences + visual annex
    settled = []
    for event in events:
        if event["_type"] != "WorldOccurrenceSettled":
            continue
        payload = event["_payload"]
        result_ref = payload.get("result_payload_ref") or payload.get("outcome_ref")
        text = life_texts.get(result_ref) if isinstance(result_ref, str) else None
        settled.append(
            {
                "seq": event["_seq"],
                "event_id": event.get("event_id"),
                "occurrence_id": payload.get("occurrence_id"),
                "logical_time": event.get("logical_time"),
                "result_ref": result_ref,
                "text_excerpt": (text[:240] if isinstance(text, str) else None),
                "since_restart": event["_seq"] > restart_seq,
            }
        )

    draws = []
    for event in events:
        if event["_type"] != "RandomDrawRecorded":
            continue
        payload = event["_payload"]
        purpose = payload.get("purpose") or payload.get("draw_kind") or payload.get("stream")
        if purpose and "visual" not in str(purpose) and "media" not in str(purpose) and "photo" not in str(purpose):
            # keep all if few; else filter later
            pass
        draws.append(
            {
                "seq": event["_seq"],
                "purpose": purpose,
                "payload_keys": sorted(payload.keys()),
                "threshold_bp": payload.get("threshold_bp"),
                "draw_bp": payload.get("draw_bp") or payload.get("value_bp"),
                "hit": payload.get("hit") or payload.get("selected"),
                "reason": reason_of(payload),
                "logical_time": event.get("logical_time"),
                "since_restart": event["_seq"] > restart_seq,
            }
        )

    tech_failures = []
    for event in events:
        if event["_type"] != "TechnicalFailureRecorded":
            continue
        payload = event["_payload"]
        code = payload.get("reason_code") or payload.get("code") or ""
        if any(
            token in str(code).lower()
            for token in ("media", "photo", "image", "render", "visual")
        ) or any(
            token in json.dumps(payload, ensure_ascii=False).lower()
            for token in ("media", "photo", "image", "render")
        ):
            tech_failures.append(
                {
                    "seq": event["_seq"],
                    "reason": code,
                    "logical_time": event.get("logical_time"),
                    "payload": {
                        k: payload.get(k)
                        for k in (
                            "reason_code",
                            "code",
                            "lane",
                            "purpose",
                            "detail",
                            "error",
                        )
                        if k in payload
                    },
                }
            )

    last_user_at = last_user_obs["_logical"] if last_user_obs else None
    available_now = [
        c
        for c in candidates
        if not c["expired_now"]
    ]
    declined_ids = {
        item.get("candidate_id")
        for item in declined
        if item.get("outcome") in {"declined", "no_op"}
    }

    # Usage by purpose
    usage_by_purpose = Counter()
    usage_cost = 0.0
    image_usage = []
    for row in usage_rows:
        purpose = row.get("purpose") or ""
        usage_by_purpose[purpose] += 1
        usage_cost += float(row.get("cost_cny") or 0)
        if "image" in purpose or (row.get("model") or "").startswith("gpt-image"):
            image_usage.append(dict(row))

    report = {
        "cloned_from": str(PRODUCTION_DB),
        "clone": str(clone),
        "world_id": WORLD_ID,
        "head_seq": head_seq,
        "logical_time": last.get("logical_time") if last else None,
        "event_count": len(events),
        "restart_wall_utc": RESTART_WALL.isoformat(),
        "restart_cut_seq": restart_seq,
        "events_since_restart": sum(1 for e in events if e["_seq"] > restart_seq),
        "type_counts_top": type_counts.most_common(80),
        "funnel_all": funnel_all,
        "funnel_since_restart": funnel_since_restart,
        "action_kinds": dict(action_kinds),
        "media_actions": media_actions,
        "candidates": candidates,
        "available_unexpired_now": available_now,
        "declined_selection": declined,
        "settled_occurrences": settled,
        "visual_draws_sample": draws[-20:],
        "visual_draw_count": len(draws),
        "tech_failures_mediaish": tech_failures,
        "photo_mentions": photo_mentions,
        "interior_photo_turns": interior_photo[-40:],
        "last_user_observation": (
            {
                "seq": last_user_obs["_seq"],
                "logical_time": last_user_obs.get("logical_time"),
                "texts": extract_text_blobs(last_user_obs["_payload"])[:6],
                "age_vs_logical_now_seconds": (
                    (logical_now - last_user_at).total_seconds()
                    if logical_now and last_user_at
                    else None
                ),
            }
            if last_user_obs
            else None
        ),
        "last_agent_observation": (
            {
                "seq": last_agent_obs["_seq"],
                "logical_time": last_agent_obs.get("logical_time"),
                "texts": extract_text_blobs(last_agent_obs["_payload"])[:6],
            }
            if last_agent_obs
            else None
        ),
        "conversation_occasion_window_hours": CONVERSATION_OCCASION_WINDOW.total_seconds() / 3600,
        "fresh_candidate_vs_last_user": [
            {
                **c,
                "abs_delta_seconds": (
                    abs((last_user_at - parse_dt(c["opened_at"])).total_seconds())
                    if last_user_at and parse_dt(c["opened_at"])
                    else None
                ),
                "within_2h_of_last_user": bool(
                    last_user_at
                    and parse_dt(c["opened_at"])
                    and abs(last_user_at - parse_dt(c["opened_at"]))
                    <= CONVERSATION_OCCASION_WINDOW
                ),
            }
            for c in candidates
        ],
        "media_sidecar_tables": media_tables,
        "usage_cost_cny": round(usage_cost, 4),
        "usage_by_purpose": dict(usage_by_purpose.most_common()),
        "image_usage": image_usage,
        "declined_candidate_ids": sorted(x for x in declined_ids if x),
    }
    dump_json(OUTPUT / "funnel.json", report)

    # Compact stdout
    print(f"clone={clone}")
    print(f"head_seq={head_seq} logical={last.get('logical_time') if last else None}")
    print(f"restart_cut_seq={restart_seq} since_restart={report['events_since_restart']}")
    print("--- funnel all / since_restart ---")
    for name in FUNNEL_TYPES:
        a = funnel_all[name]["count"]
        b = funnel_since_restart[name]["count"]
        if a or b:
            print(f"  {name:40} {a:5}  +{b}")
            if funnel_all[name]["outcomes"]:
                print(f"    outcomes={funnel_all[name]['outcomes']}")
            if funnel_all[name]["reasons"]:
                print(f"    reasons={funnel_all[name]['reasons']}")
    print("--- candidates ---")
    for c in candidates:
        print(
            f"  seq={c['seq']} id={c['candidate_id']} tax={c['taxonomy']} "
            f"opened={c['opened_at']} exp={c['expires_at']} expired={c['expired_now']}"
        )
    print(f"unexpired={len(available_now)} declined_attempts={len(declined)}")
    print(f"photo_mentions={len(photo_mentions)} last_user={report['last_user_observation']}")
    print(f"media_tables={media_tables} image_usage={len(image_usage)}")


if __name__ == "__main__":
    main()
