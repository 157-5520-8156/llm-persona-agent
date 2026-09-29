#!/usr/bin/env python3
"""Read-only production audit: album promise vs zero photo candidates.

Never writes ``data/``. Uses ``sqlite3.connect(..., uri=True) + mode=ro``.
Ledger: ``data/companion.epoch2.sqlite``. Artifacts: ``output/album-promise/``.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
import json
import sqlite3
import sys
from typing import Any
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

PRODUCTION_DB = (REPO / "data" / "companion.epoch2.sqlite").resolve()
WORLD_ID = "world:companion-v2:qq-c2c:geoff"
OUTPUT = (REPO / "output" / "album-promise").resolve()
SHANGHAI = ZoneInfo("Asia/Shanghai")

PHOTO_HINTS = (
    "照片",
    "相册",
    "自拍",
    "翻一下",
    "翻相册",
    "发给你",
    "发你",
    "拍的",
    "拍了",
    "拍一张",
    "给你看",
    "要看吗",
    "整理了一下",
)


def parse_event(raw: str | bytes) -> dict[str, Any]:
    event = json.loads(raw)
    payload = event.get("payload")
    if isinstance(payload, str):
        try:
            event["_payload"] = json.loads(payload)
        except json.JSONDecodeError:
            event["_payload"] = {}
    elif isinstance(payload, dict):
        event["_payload"] = payload
    else:
        payload_json = event.get("payload_json")
        if isinstance(payload_json, str):
            try:
                event["_payload"] = json.loads(payload_json)
            except json.JSONDecodeError:
                event["_payload"] = {}
        else:
            event["_payload"] = payload_json if isinstance(payload_json, dict) else {}
    return event


def event_type(event: dict[str, Any]) -> str:
    return str(event.get("event_type") or event["_payload"].get("event_type") or "")


def as_dt(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def iso(value: object) -> str | None:
    parsed = as_dt(value)
    return parsed.isoformat() if parsed is not None else (str(value) if value else None)


def payload_of(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("_payload")
    return payload if isinstance(payload, dict) else {}


def nested_text(payload: dict[str, Any]) -> str | None:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    observation = payload.get("observation") if isinstance(payload.get("observation"), dict) else {}
    for candidate in (
        message.get("text") if isinstance(message, dict) else None,
        observation.get("text") if isinstance(observation, dict) else None,
        payload.get("text"),
        payload.get("visible_text"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    beats = payload.get("beats")
    if isinstance(beats, list):
        parts: list[str] = []
        for beat in beats:
            if not isinstance(beat, dict):
                continue
            text = beat.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        if parts:
            return " / ".join(parts)
    return None


def mentions_photo(text: str) -> bool:
    return any(hint in text for hint in PHOTO_HINTS)


def load_events(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT ledger_sequence, event_json FROM world_v2_events "
        "WHERE world_id = ? ORDER BY ledger_sequence",
        (WORLD_ID,),
    ).fetchall()
    events: list[dict[str, Any]] = []
    for row in rows:
        event = parse_event(row[1])
        event["_seq"] = int(row[0])
        event["_type"] = event_type(event)
        event["_lt"] = event.get("logical_time")
        events.append(event)
    return events


def extract_expression_fields(payload: dict[str, Any]) -> dict[str, Any]:
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else payload
    manifest = plan.get("manifest") if isinstance(plan.get("manifest"), dict) else plan
    draft = payload.get("draft") if isinstance(payload.get("draft"), dict) else {}
    sources = [
        payload,
        plan if isinstance(plan, dict) else {},
        manifest if isinstance(manifest, dict) else {},
        draft if isinstance(draft, dict) else {},
    ]
    fields = (
        "media_request",
        "media_source_refs",
        "response_expectation",
        "waiting_for",
        "wait_seconds",
        "come_back",
        "come_back_in",
        "revisit",
        "revisit_intention",
        "leftover",
        "timing_choice",
        "turn_posture",
        "cadence",
        "overall_intent",
        "we_are",
        "about_us",
        "keep_impression",
        "hoped_response",
        "photo",
    )
    found: dict[str, Any] = {}
    for field in fields:
        for source in sources:
            if field in source and source[field] not in (None, "", [], {}):
                found[field] = source[field]
                break
    return found


def walk_for_keys(obj: object, wanted: frozenset[str], found: dict[str, Any], *, path: str = "") -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            child = f"{path}.{key}" if path else key
            if key in wanted and key not in found and value not in (None, "", [], {}):
                found[key] = {"path": child, "value": value}
            if key in wanted or isinstance(value, (dict, list)):
                walk_for_keys(value, wanted, found, path=child)
    elif isinstance(obj, list):
        for index, item in enumerate(obj[:40]):
            walk_for_keys(item, wanted, found, path=f"{path}[{index}]")


def compact_json(value: object, *, limit: int = 800) -> object:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    if isinstance(value, dict):
        return {str(k): compact_json(v, limit=limit) for k, v in list(value.items())[:40]}
    if isinstance(value, list):
        return [compact_json(item, limit=limit) for item in value[:20]]
    return value


def audit(conn: sqlite3.Connection) -> dict[str, Any]:
    events = load_events(conn)
    types = Counter(item["_type"] for item in events)
    head = events[-1] if events else {}
    overlay = None
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='world_v2_life_ecology_schedule_overlay'"
    ).fetchone():
        overlay_row = conn.execute(
            "SELECT * FROM world_v2_life_ecology_schedule_overlay WHERE world_id = ?",
            (WORLD_ID,),
        ).fetchone()
        if overlay_row is not None:
            overlay = dict(overlay_row) if hasattr(overlay_row, "keys") else None
            if overlay is None:
                cols = [d[0] for d in conn.execute(
                    "PRAGMA table_info(world_v2_life_ecology_schedule_overlay)"
                )]
                overlay = dict(zip(cols, overlay_row, strict=True))

    leases: list[dict[str, Any]] = []
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='world_v2_life_ecology_leases'"
    ).fetchone():
        lease_cols = [d[0] for d in conn.execute("PRAGMA table_info(world_v2_life_ecology_leases)")]
        for row in conn.execute(
            "SELECT * FROM world_v2_life_ecology_leases WHERE world_id = ? ORDER BY rowid",
            (WORLD_ID,),
        ):
            leases.append(dict(zip(lease_cols, row, strict=True)))

    payloads: dict[str, dict[str, Any]] = {}
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='world_v2_expression_payload'"
    ).fetchone():
        for row in conn.execute(
            "SELECT payload_ref, payload_kind, encoded_payload FROM world_v2_expression_payload "
            "WHERE world_id = ?",
            (WORLD_ID,),
        ):
            encoded = row[2]
            parsed: object
            try:
                parsed = json.loads(encoded)
            except (TypeError, json.JSONDecodeError):
                parsed = encoded
            payloads[str(row[0])] = {"kind": row[1], "payload": parsed}

    interior_rows: list[dict[str, Any]] = []
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='world_v2_character_interior_turns'"
    ).fetchone():
        cols = [d[0] for d in conn.execute("PRAGMA table_info(world_v2_character_interior_turns)")]
        for row in conn.execute(
            "SELECT * FROM world_v2_character_interior_turns WHERE world_id = ?",
            (WORLD_ID,),
        ):
            interior_rows.append(dict(zip(cols, row, strict=True)))

    her_messages: list[dict[str, Any]] = []
    his_messages: list[dict[str, Any]] = []
    photo_her: list[dict[str, Any]] = []
    photo_his: list[dict[str, Any]] = []
    for event in events:
        kind = event["_type"]
        payload = payload_of(event)
        text = nested_text(payload)
        if kind == "MessagePayloadStored":
            item = {
                "seq": event["_seq"],
                "logical_time": event.get("logical_time"),
                "event_id": event.get("event_id"),
                "text": text,
                "payload_ref": payload.get("payload_ref") or payload.get("message_id"),
            }
            her_messages.append(item)
            if isinstance(text, str) and mentions_photo(text):
                photo_her.append(item)
        elif kind == "ObservationRecorded":
            observation = payload.get("observation") if isinstance(payload.get("observation"), dict) else payload
            speaker = ""
            if isinstance(observation, dict):
                speaker = str(observation.get("speaker") or observation.get("actor_ref") or "")
                if not text:
                    text = nested_text(observation if isinstance(observation, dict) else {})
            item = {
                "seq": event["_seq"],
                "logical_time": event.get("logical_time"),
                "event_id": event.get("event_id"),
                "speaker": speaker,
                "text": text,
                "source_event_id": (
                    observation.get("source_event_id") if isinstance(observation, dict) else None
                ),
            }
            his_messages.append(item)
            if isinstance(text, str) and mentions_photo(text):
                photo_his.append(item)

    expression_events: list[dict[str, Any]] = []
    wanted = frozenset(
        {
            "media_request",
            "media_source_refs",
            "response_expectation",
            "waiting_for",
            "wait",
            "wait_seconds",
            "come_back",
            "come_back_in",
            "revisit",
            "revisit_intention",
            "leftover",
            "hoped_response",
            "we_are",
            "photo",
            "keep_impression",
        }
    )
    for event in events:
        if event["_type"] not in {
            "ExpressionPlanAccepted",
            "ExpressionPlanProposed",
            "ProposalRecorded",
            "DecisionProposalRecorded",
            "PrivateTurnStateRecorded",
        }:
            continue
        payload = payload_of(event)
        found: dict[str, Any] = {}
        walk_for_keys(payload, wanted, found)
        text = nested_text(payload)
        interesting = bool(found) or (isinstance(text, str) and mentions_photo(text))
        if event["_seq"] >= 3500 or interesting:
            expression_events.append(
                {
                    "seq": event["_seq"],
                    "type": event["_type"],
                    "logical_time": event.get("logical_time"),
                    "event_id": event.get("event_id"),
                    "fields": {k: compact_json(v) for k, v in found.items()},
                    "text": text,
                    "extracted": extract_expression_fields(payload),
                }
            )

    settled: list[dict[str, Any]] = []
    for event in events:
        if event["_type"] != "WorldOccurrenceSettled":
            continue
        payload = payload_of(event)
        occurrence = payload.get("occurrence") if isinstance(payload.get("occurrence"), dict) else payload
        settled.append(
            {
                "seq": event["_seq"],
                "logical_time": event.get("logical_time"),
                "event_id": event.get("event_id"),
                "occurrence_id": occurrence.get("occurrence_id") if isinstance(occurrence, dict) else None,
                "status": occurrence.get("status") if isinstance(occurrence, dict) else payload.get("status"),
                "visibility": (
                    occurrence.get("visibility") if isinstance(occurrence, dict) else payload.get("visibility")
                ),
                "location_ref": (
                    occurrence.get("location_ref") if isinstance(occurrence, dict) else payload.get("location_ref")
                ),
                "participant_refs": (
                    occurrence.get("participant_refs")
                    if isinstance(occurrence, dict)
                    else payload.get("participant_refs")
                ),
                "settled_at": (
                    occurrence.get("settled_at") if isinstance(occurrence, dict) else payload.get("settled_at")
                ),
                "trigger_ref": (
                    occurrence.get("trigger_ref") if isinstance(occurrence, dict) else payload.get("trigger_ref")
                ),
                "settlement_event_ref": event.get("event_id"),
                "outcome_text": compact_json(
                    occurrence.get("settled_outcome")
                    if isinstance(occurrence, dict)
                    else payload.get("settled_outcome")
                    or payload.get("summary")
                    or payload.get("outcome_text"),
                    limit=400,
                ),
                "keys": sorted(payload.keys())[:40],
                "occurrence_keys": sorted(occurrence.keys())[:40] if isinstance(occurrence, dict) else [],
            }
        )

    visual_draws: list[dict[str, Any]] = []
    for event in events:
        if event["_type"] != "RandomDrawRecorded":
            continue
        payload = payload_of(event)
        source = str(payload.get("source") or event.get("source") or "")
        catalog = str(payload.get("catalog_version") or "")
        attempt = str(payload.get("attempt_id") or "")
        if "visual" in source or "visual" in catalog or "visual-evidence" in attempt:
            visual_draws.append(
                {
                    "seq": event["_seq"],
                    "logical_time": event.get("logical_time"),
                    "source": source,
                    "catalog_version": catalog,
                    "attempt_id": attempt,
                    "selected": payload.get("selected_candidate_ref"),
                    "actor": event.get("actor") or payload.get("actor"),
                }
            )

    trigger_kinds = Counter()
    media_triggers: list[dict[str, Any]] = []
    for event in events:
        if event["_type"] not in {"TriggerProcessOpened", "TriggerProcessCompleted"}:
            continue
        payload = payload_of(event)
        process = payload.get("process") if isinstance(payload.get("process"), dict) else payload
        kind = str(process.get("process_kind") or payload.get("process_kind") or "")
        if event["_type"] == "TriggerProcessOpened":
            trigger_kinds[kind] += 1
        if "media" in kind or "visual" in kind or "life_ecology" in kind:
            media_triggers.append(
                {
                    "seq": event["_seq"],
                    "type": event["_type"],
                    "logical_time": event.get("logical_time"),
                    "process_kind": kind,
                    "state": process.get("state"),
                    "outcome": process.get("runtime_outcome_ref") or process.get("outcome"),
                    "trigger_id": process.get("trigger_id") or payload.get("trigger_id"),
                }
            )

    activity_events: list[dict[str, Any]] = []
    for event in events:
        if event["_type"] not in {
            "ActivityPlanned",
            "ActivityStarted",
            "ActivityCompleted",
            "ActivityPaused",
            "ActivityResumed",
            "ActivityAbandoned",
        }:
            continue
        payload = payload_of(event)
        plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else payload
        activity_events.append(
            {
                "seq": event["_seq"],
                "type": event["_type"],
                "logical_time": event.get("logical_time"),
                "plan_id": plan.get("plan_id") if isinstance(plan, dict) else payload.get("plan_id"),
                "activity_kind": (
                    plan.get("activity_kind") if isinstance(plan, dict) else payload.get("activity_kind")
                ),
                "location_ref": (
                    plan.get("location_ref") if isinstance(plan, dict) else payload.get("location_ref")
                ),
                "window": {
                    "opens_at": plan.get("window_opens_at") if isinstance(plan, dict) else None,
                    "closes_at": plan.get("window_closes_at") if isinstance(plan, dict) else None,
                },
            }
        )

    ecology_wakes = [
        {
            "seq": event["_seq"],
            "logical_time": event.get("logical_time"),
            "event_id": event.get("event_id"),
            "wake_kind": payload_of(event).get("wake_kind") or payload_of(event).get("reason"),
        }
        for event in events
        if event["_type"] == "ClockAdvanced"
        and "life_ecology" in json.dumps(payload_of(event), ensure_ascii=False)
    ]

    relationship = None
    for event in reversed(events):
        if event["_type"] in {"RelationshipStateRecorded", "RelationshipCommitmentAccepted"}:
            relationship = {
                "seq": event["_seq"],
                "type": event["_type"],
                "payload_keys": sorted(payload_of(event).keys())[:40],
                "payload": compact_json(payload_of(event), limit=300),
            }
            break

    interior_photo_turns: list[dict[str, Any]] = []
    for row in interior_rows:
        purpose = str(row.get("purpose") or "")
        authored = row.get("authored_state_json")
        terminal = row.get("terminal_result_json")
        parsed_authored = None
        parsed_terminal = None
        for raw, dest in ((authored, "authored"), (terminal, "terminal")):
            if not isinstance(raw, str) or not raw:
                continue
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if dest == "authored":
                parsed_authored = parsed
            else:
                parsed_terminal = parsed
        blob = json.dumps([parsed_authored, parsed_terminal], ensure_ascii=False)
        if not any(hint in blob for hint in PHOTO_HINTS) and purpose not in {
            "inbound_turn",
            "proactive_contact",
        }:
            continue
        materials_keys: list[str] = []
        snapshot_bits: dict[str, Any] = {}
        for parsed in (parsed_authored, parsed_terminal):
            if not isinstance(parsed, dict):
                continue
            found: dict[str, Any] = {}
            walk_for_keys(
                parsed,
                frozenset(
                    {
                        "inner_state_summary",
                        "media_request",
                        "media_source_refs",
                        "come_back",
                        "response_expectation",
                        "photos_i_shared",
                        "photo_candidates",
                        "recent_self_experiences",
                        "week_diary",
                        "situation",
                        "media_request_mode",
                        "expression_capabilities",
                        "materials_json",
                    }
                ),
                found,
            )
            snapshot_bits.update({k: compact_json(v, limit=500) for k, v in found.items()})
            materials = parsed.get("materials") or parsed.get("materials_json")
            if isinstance(materials, str):
                try:
                    materials = json.loads(materials)
                except json.JSONDecodeError:
                    materials = None
            if isinstance(materials, dict):
                materials_keys = sorted(materials.keys())
                snapshot_bits["material_keys"] = materials_keys
                for key in (
                    "photos_i_shared",
                    "recent_self_experiences",
                    "week_diary",
                    "situation",
                    "capability_evidence",
                ):
                    if key in materials:
                        snapshot_bits[f"material.{key}"] = compact_json(materials[key], limit=600)
        if purpose in {"inbound_turn", "proactive_contact"} or any(
            hint in blob for hint in PHOTO_HINTS
        ):
            interior_photo_turns.append(
                {
                    "inner_turn_id": row.get("inner_turn_id"),
                    "purpose": purpose,
                    "updated_at": row.get("updated_at"),
                    "snapshot_id": row.get("snapshot_id"),
                    "state": row.get("state"),
                    "bits": snapshot_bits,
                    "authored_summary": (
                        parsed_authored.get("inner_state_summary")
                        if isinstance(parsed_authored, dict)
                        else None
                    ),
                }
            )

    usage = []
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='world_v2_model_usage'"
    ).fetchone():
        usage_cols = [d[0] for d in conn.execute("PRAGMA table_info(world_v2_model_usage)")]
        for row in conn.execute(
            "SELECT * FROM world_v2_model_usage ORDER BY rowid DESC LIMIT 80"
        ):
            usage.append(dict(zip(usage_cols, row, strict=True)))

    type_counts = {
        name: types.get(name, 0)
        for name in (
            "WorldOccurrenceSettled",
            "WorldOccurrenceActivated",
            "WorldOccurrenceCommitted",
            "ImageEvidenceDeclared",
            "RecipientScopedImageEvidenceDeclared",
            "PhotoCandidateOpened",
            "MediaSelectionAttemptRecorded",
            "MediaSelectionProposalRecorded",
            "MediaPlanRecorded",
            "MediaPreviewGenerated",
            "RandomDrawRecorded",
            "ProviderMediaGrantRecorded",
            "AppearanceStateRecorded",
            "VisiblePhysicalStateRecorded",
            "ActivityPlanned",
            "ActivityStarted",
            "ActivityCompleted",
            "ExperienceCommitted",
            "ClockAdvanced",
        )
    }

    recent_dialogue = []
    for item in her_messages[-30:] + [x for x in his_messages if x["seq"] >= 3800]:
        recent_dialogue.append(item)
    recent_dialogue.sort(key=lambda item: item["seq"])

    return {
        "investigated_at_utc": datetime.now(UTC).isoformat(),
        "ledger": str(PRODUCTION_DB),
        "world_id": WORLD_ID,
        "head_seq": head.get("_seq"),
        "head_logical_time": head.get("logical_time"),
        "head_type": head.get("_type"),
        "event_count": len(events),
        "type_counts": type_counts,
        "overlay": overlay,
        "lease_outcomes": dict(Counter(str(item.get("outcome") or item.get("last_outcome") or "") for item in leases)),
        "lease_count": len(leases),
        "leases_tail": leases[-12:],
        "photo_her_messages": photo_her,
        "photo_his_messages": photo_his,
        "her_messages_from_seq_3800": [item for item in her_messages if item["seq"] >= 3800],
        "his_messages_from_seq_3800": [item for item in his_messages if item["seq"] >= 3800],
        "expression_interesting": [
            item
            for item in expression_events
            if item["seq"] >= 3800
            or item["fields"]
            or (isinstance(item["text"], str) and mentions_photo(item["text"]))
        ],
        "settled_occurrences": settled,
        "visual_draws": visual_draws,
        "random_draw_total": types.get("RandomDrawRecorded", 0),
        "trigger_kinds": dict(trigger_kinds),
        "media_or_ecology_triggers": media_triggers[-40:],
        "activity_events": activity_events,
        "ecology_wakes": ecology_wakes[-20:],
        "ecology_wake_count": len(ecology_wakes),
        "relationship_last": relationship,
        "interior_consider_count": len(interior_rows),
        "interior_photo_or_recent": interior_photo_turns[-30:],
        "payload_kinds": dict(Counter(item["kind"] for item in payloads.values())),
        "usage_tail": usage[:20],
        "recent_dialogue": recent_dialogue,
    }


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{PRODUCTION_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        result = audit(conn)
    finally:
        conn.close()
    (OUTPUT / "evidence.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "head_seq": result["head_seq"],
        "head_logical_time": result["head_logical_time"],
        "type_counts": result["type_counts"],
        "photo_her": len(result["photo_her_messages"]),
        "photo_his": len(result["photo_his_messages"]),
        "settled": len(result["settled_occurrences"]),
        "visual_draws": len(result["visual_draws"]),
        "overlay": result["overlay"],
        "out": str(OUTPUT / "evidence.json"),
    }, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
