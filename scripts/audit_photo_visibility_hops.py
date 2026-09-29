#!/usr/bin/env python3
"""Trace 3 photo candidates → moments_i_can_share 0 on a production clone.

Never writes ``data/``. Never talks to NapCat. Artifacts: ``output/photo-visibility/``.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import drive_production_lanes as drive
from companion_daemon.world_v2.character_interior.contracts import InteriorOpportunity
from companion_daemon.world_v2.character_interior.snapshot_compiler import (
    _experience_entry,
    _moments_i_can_share,
    _slice_items,
    compile_inner_life_snapshot,
)
from companion_daemon.world_v2.context_resolver import query_from_projection
from companion_daemon.world_v2.media_conversation_window import is_reask_eligible
from companion_daemon.world_v2.photographable_inventory import (
    available_photo_source_refs,
    install_shareable_photos_context,
)
from companion_daemon.world_v2.schemas import ProjectionCursor
from companion_daemon.world_v2.world_life_context import (
    ActiveWorldOccurrenceContextItem,
    BiographicalWorldContextItem,
    WorldLifeContextCompiler,
    WorldLifeContextItem,
)

WORLD_ID = drive.WORLD_ID
ACTOR = "agent:companion"
PRODUCTION_DB = drive.PRODUCTION_DB
OUTPUT = (REPO / "output" / "photo-visibility").resolve()


def dump_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def payload_of(event: dict[str, Any]) -> dict[str, Any]:
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


def clone_production() -> Path:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / "clone.sqlite"
    drive.clone_ledger(PRODUCTION_DB, target)
    return target


def dump_ledger_events(clone: Path) -> dict[str, Any]:
    conn = drive.open_ro(clone)
    try:
        texts = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT content_ref, text FROM world_v2_life_content WHERE world_id = ?",
                (WORLD_ID,),
            )
        }
        types = (
            "PhotoCandidateOpened",
            "PhotoCandidateUnrenderable",
            "ImageEvidenceDeclared",
            "RecipientScopedImageEvidenceDeclared",
            "WorldOccurrenceSettled",
            "ActivityCompleted",
            "MediaSelectionAttemptRecorded",
            "MediaSelectionProposalRecorded",
            "MediaDeliveryShared",
            "LifeContentRecorded",
        )
        by_type: dict[str, list[dict[str, Any]]] = {name: [] for name in types}
        for seq, raw in conn.execute(
            "SELECT ledger_sequence, event_json FROM world_v2_events "
            "WHERE world_id = ? ORDER BY ledger_sequence",
            (WORLD_ID,),
        ):
            event = json.loads(raw)
            kind = event.get("event_type")
            if kind not in by_type:
                continue
            payload = payload_of(event)
            candidate = payload.get("candidate") if isinstance(payload.get("candidate"), dict) else {}
            item = {
                "seq": int(seq),
                "event_id": event.get("event_id"),
                "logical_time": event.get("logical_time"),
                "type": kind,
            }
            if kind == "PhotoCandidateOpened":
                item.update(
                    {
                        "candidate_id": candidate.get("candidate_id"),
                        "status": candidate.get("status"),
                        "family": candidate.get("family"),
                        "privacy_ceiling": candidate.get("privacy_ceiling"),
                        "ecology_category": candidate.get("ecology_category"),
                        "opened_at": candidate.get("opened_at"),
                        "expires_at": candidate.get("expires_at"),
                        "source_event_refs": candidate.get("source_event_refs"),
                    }
                )
            elif kind == "WorldOccurrenceSettled":
                item.update(
                    {
                        "occurrence_id": payload.get("occurrence_id"),
                        "result_payload_ref": payload.get("result_payload_ref"),
                        "settled_at": payload.get("settled_at"),
                    }
                )
            elif kind in {"ImageEvidenceDeclared", "RecipientScopedImageEvidenceDeclared"}:
                evidence = payload.get("image_evidence")
                if not isinstance(evidence, dict):
                    evidence = payload
                item.update(
                    {
                        "visibility": evidence.get("visibility"),
                        "source_event_refs": payload.get("source_event_refs")
                        or evidence.get("source_event_refs"),
                    }
                )
            by_type[kind].append(item)

        seen_turns: list[dict[str, Any]] = []
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "world_v2_character_interior_turns" in tables:
            for row in conn.execute(
                "SELECT purpose, snapshot_id, authored_state_json, updated_at "
                "FROM world_v2_character_interior_turns "
                "WHERE world_id = ? AND authored_state_json IS NOT NULL "
                "ORDER BY updated_at DESC LIMIT 40",
                (WORLD_ID,),
            ):
                authored = json.loads(row[2]) if row[2] else {}
                snapshot = authored.get("snapshot") if isinstance(authored, dict) else {}
                materials = {}
                if isinstance(snapshot, dict):
                    materials = snapshot.get("materials") or {}
                    if not materials and isinstance(snapshot.get("materials_json"), str):
                        try:
                            materials = json.loads(snapshot["materials_json"])
                        except json.JSONDecodeError:
                            materials = {}
                moments = materials.get("moments_i_can_share") if isinstance(materials, dict) else None
                if moments is None:
                    continue
                seen_turns.append(
                    {
                        "purpose": row[0],
                        "snapshot_id": row[1],
                        "updated_at": row[2] and row[3],
                        "moments_i_can_share": moments,
                        "material_keys": sorted(materials) if isinstance(materials, dict) else [],
                    }
                )
    finally:
        conn.close()
    out = {
        "life_content_rows": len(texts),
        "counts": {name: len(items) for name, items in by_type.items()},
        "events": by_type,
        "stored_moments": seen_turns[:20],
    }
    dump_json(OUTPUT / "ledger-events.json", out)
    return out


def candidate_row(item: object) -> dict[str, Any]:
    return {
        "candidate_id": getattr(item, "candidate_id", None),
        "status": getattr(item, "status", None),
        "family": getattr(item, "family", None),
        "privacy_ceiling": getattr(item, "privacy_ceiling", None),
        "ecology_category": getattr(item, "ecology_category", None),
        "opened_at": getattr(item, "opened_at", None),
        "expires_at": getattr(item, "expires_at", None),
        "source_event_refs": list(getattr(item, "source_event_refs", ()) or ()),
        "source_events": [
            {
                "event_ref": getattr(source, "event_ref", None),
                "payload_hash": getattr(source, "payload_hash", None),
            }
            for source in getattr(item, "source_events", ()) or ()
        ],
        "opened_event_ref": getattr(item, "opened_event_ref", None),
    }


def hop_filter_candidates(projection: object) -> dict[str, Any]:
    logical_time = getattr(projection, "logical_time", None)
    hops: list[dict[str, Any]] = []
    rows = [candidate_row(item) for item in getattr(projection, "photo_candidates", ()) or ()]
    hops.append({"hop": "1_projection_photo_candidates", "count": len(rows), "items": rows})

    status_kept: list[dict[str, Any]] = []
    status_dropped: list[dict[str, Any]] = []
    for item in getattr(projection, "photo_candidates", ()) or ():
        row = candidate_row(item)
        available = getattr(item, "status", None) == "available"
        reask = isinstance(logical_time, datetime) and is_reask_eligible(
            projection, candidate=item, logical_time=logical_time
        )
        if available or reask:
            row["kept_because"] = "available" if available else "reask_eligible"
            status_kept.append(row)
        else:
            row["dropped_because"] = f"status={row['status']} reask={reask}"
            status_dropped.append(row)
    hops.append(
        {
            "hop": "2_status_or_reask",
            "count": len(status_kept),
            "kept": status_kept,
            "dropped": status_dropped,
        }
    )

    unexpired: list[dict[str, Any]] = []
    expired: list[dict[str, Any]] = []
    for item in getattr(projection, "photo_candidates", ()) or ():
        row = candidate_row(item)
        available = getattr(item, "status", None) == "available"
        reask = isinstance(logical_time, datetime) and is_reask_eligible(
            projection, candidate=item, logical_time=logical_time
        )
        if not available and not reask:
            continue
        expires_at = getattr(item, "expires_at", None)
        if (
            isinstance(logical_time, datetime)
            and isinstance(expires_at, datetime)
            and expires_at <= logical_time
        ):
            row["dropped_because"] = f"expires_at={expires_at} <= logical_time={logical_time}"
            expired.append(row)
            continue
        unexpired.append(row)
    hops.append(
        {
            "hop": "3_unexpired",
            "count": len(unexpired),
            "kept": unexpired,
            "dropped": expired,
        }
    )

    available_refs = available_photo_source_refs(projection, logical_time=logical_time)
    hops.append(
        {
            "hop": "4_available_photo_source_refs",
            "count": len(available_refs),
            "refs": sorted(available_refs),
        }
    )
    return {
        "logical_time": logical_time,
        "ledger_sequence": getattr(projection, "ledger_sequence", None),
        "world_revision": getattr(projection, "world_revision", None),
        "hops": hops,
        "available_refs": sorted(available_refs),
    }


def hop_world_life(projection: object, *, available_refs: set[str]) -> dict[str, Any]:
    compiler = WorldLifeContextCompiler()
    cursor = ProjectionCursor(
        world_revision=projection.world_revision,
        deliberation_revision=projection.deliberation_revision,
        ledger_sequence=projection.ledger_sequence,
    )
    items = compiler.compile(projection=projection, actor_ref=ACTOR, cursor=cursor)
    rows: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, BiographicalWorldContextItem):
            rows.append(
                {
                    "kind": "biographical",
                    "photo_in_hand": None,
                    "privacy_class": None,
                    "identity": getattr(item, "biography_id", None),
                }
            )
            continue
        if isinstance(item, ActiveWorldOccurrenceContextItem):
            rows.append(
                {
                    "kind": "active",
                    "occurrence_id": item.occurrence_id,
                    "privacy_class": item.privacy_class,
                    "photo_in_hand": None,
                    "status": item.status,
                }
            )
            continue
        if isinstance(item, WorldLifeContextItem):
            settlement = item.source.authority_event_ref
            rows.append(
                {
                    "kind": "settled",
                    "occurrence_id": item.occurrence_id,
                    "privacy_class": item.privacy_class,
                    "settled_at": item.settled_at,
                    "location_ref": item.location_ref,
                    "result_payload_ref": item.result_payload_ref,
                    "settlement_ref": settlement,
                    "photo_in_hand": item.photo_in_hand,
                    "in_available_refs": settlement in available_refs,
                    "content_text": (
                        item.content.text[:240] if item.content is not None else None
                    ),
                }
            )
    in_hand = [row for row in rows if row.get("photo_in_hand") is True]
    settled = [row for row in rows if row.get("kind") == "settled"]
    unmatched_refs = sorted(
        ref
        for ref in available_refs
        if not any(row.get("settlement_ref") == ref for row in settled)
    )
    return {
        "hop": "5_world_life_compiler",
        "count_all": len(rows),
        "count_settled": len(settled),
        "count_photo_in_hand": len(in_hand),
        "items": rows,
        "available_refs_not_on_any_settlement": unmatched_refs,
    }


async def hop_capsule_and_snapshot(
    *,
    projection: object,
    capsules: object,
    interior: object,
) -> dict[str, Any]:
    trigger = None
    for ref in reversed(getattr(projection, "committed_world_event_refs", ()) or ()):
        event_id = getattr(ref, "event_id", None)
        if isinstance(event_id, str) and event_id:
            trigger = event_id
            break
    if trigger is None:
        raise RuntimeError("projection has no committed event to pin a capsule")
    query = query_from_projection(projection, actor_ref=ACTOR, trigger_ref=trigger)
    capsule = capsules.compile(query)
    context = json.loads(capsule.model_content_json)
    slices = context.get("slices") if isinstance(context, dict) else {}
    world_life = slices.get("world_life") if isinstance(slices, dict) else {}
    raw_items = world_life.get("items") if isinstance(world_life, dict) else []
    capsule_items: list[dict[str, Any]] = []
    for item in raw_items or []:
        if not isinstance(item, dict):
            continue
        value = item.get("value") if isinstance(item.get("value"), dict) else {}
        capsule_items.append(
            {
                "item_ref": item.get("item_ref"),
                "privacy_class": item.get("privacy_class"),
                "context_kind": value.get("context_kind"),
                "occurrence_id": value.get("occurrence_id"),
                "photo_in_hand": value.get("photo_in_hand"),
                "settlement_ref": ((value.get("source") or {}).get("authority_event_ref")),
                "result_payload_ref": value.get("result_payload_ref"),
            }
        )

    experience_entries = []
    experience_dropped = []
    for item in _slice_items(slices, "world_life"):
        entry = _experience_entry(item, lane="world_life")
        value = item.get("value") if isinstance(item.get("value"), dict) else {}
        if entry is None:
            experience_dropped.append(
                {
                    "item_ref": item.get("item_ref") or item.get("source_ref"),
                    "context_kind": value.get("context_kind"),
                    "reason": "experience_entry_none",
                }
            )
            continue
        experience_entries.append(
            {
                "source_ref": entry.get("source_ref"),
                "privacy_class": entry.get("privacy_class"),
                "photo_in_hand": entry.get("photo_in_hand"),
                "settled_at": entry.get("settled_at"),
                "what_happened_keys": sorted(entry),
            }
        )

    shareable = _moments_i_can_share(
        slices,
        recent=experience_entries,
        already_sent_count=0,
    )
    typed = compile_inner_life_snapshot(context)
    materials = json.loads(typed.materials_json)
    inventory = materials.get("moments_i_can_share")
    installed_context = install_shareable_photos_context(context, projection)
    installed_shareable = (installed_context.get("slices") or {}).get("shareable_photos")
    installed_typed = compile_inner_life_snapshot(installed_context)
    installed_inventory = json.loads(installed_typed.materials_json).get(
        "moments_i_can_share"
    )

    cursor = ProjectionCursor(
        world_revision=projection.world_revision,
        deliberation_revision=projection.deliberation_revision,
        ledger_sequence=projection.ledger_sequence,
    )
    subject = InteriorOpportunity(
        opportunity_ref="audit:photo-visibility",
        inner_turn_ref="audit:photo-visibility",
        world_id=WORLD_ID,
        actor_ref=ACTOR,
        trigger_ref=trigger,
        cursor=cursor,
        logical_time=projection.logical_time,
        purpose="inbound_turn",
        source_refs=(trigger,),
    )
    live_inventory = None
    live_error = None
    try:
        snapshot = await interior.project(subject)
        live_materials = json.loads(snapshot.materials_json)
        live_inventory = live_materials.get("moments_i_can_share")
    except Exception as exc:  # noqa: BLE001 — diagnostic
        live_error = f"{type(exc).__name__}: {exc}"

    truncation = [
        entry.model_dump(mode="json") if hasattr(entry, "model_dump") else str(entry)
        for entry in getattr(capsule, "truncation_log", ()) or ()
        if getattr(entry, "slice_name", None) == "world_life"
        or (isinstance(entry, dict) and entry.get("slice_name") == "world_life")
    ]
    return {
        "trigger_ref": trigger,
        "world_life_availability": world_life.get("availability") if isinstance(world_life, dict) else None,
        "world_life_truncated": getattr(capsule.world_life, "truncated", None),
        "world_life_budget": (
            capsule.world_life.budget.model_dump(mode="json")
            if hasattr(capsule.world_life, "budget")
            else None
        ),
        "truncation_log_world_life": truncation,
        "hop_6_capsule_world_life_items": {
            "count": len(capsule_items),
            "photo_in_hand_true": sum(1 for item in capsule_items if item.get("photo_in_hand") is True),
            "items": capsule_items,
        },
        "hop_7_experience_entry": {
            "count": len(experience_entries),
            "dropped": experience_dropped,
            "items": experience_entries,
        },
        "hop_8_moments_i_can_share_direct": shareable,
        "hop_9_compile_inner_life_snapshot": inventory,
        "hop_10_interior_project": live_inventory,
        "hop_11_installed_shareable_photos": {
            "slice": installed_shareable,
            "moments_i_can_share": installed_inventory,
        },
        "interior_project_error": live_error,
        "material_keys": sorted(materials),
    }


def application_of(session: drive.DriveSession):
    platform = getattr(session.host, "_host", None)
    return getattr(platform, "_application", None)


async def run_async(clone: Path) -> dict[str, Any]:
    session = await drive.open_session(
        database=clone, output_dir=OUTPUT, enable_media=False
    )
    try:
        projection = session.projection()
        if projection is None:
            raise RuntimeError("clone host has no projection")
        candidate_hops = hop_filter_candidates(projection)
        available_refs = set(candidate_hops["available_refs"])
        world_life = hop_world_life(projection, available_refs=available_refs)
        application = application_of(session)
        interior = getattr(application, "_character_interior", None)
        delegate = getattr(getattr(interior, "_projection", None), "_delegate", None)
        capsules = getattr(delegate, "capsules", None)
        if capsules is None:
            raise RuntimeError("character interior capsule compiler is not bound")
        snapshot_hops = await hop_capsule_and_snapshot(
            projection=projection, capsules=capsules, interior=interior
        )
        deliveries = len(getattr(projection, "media_deliveries", ()) or ())
        return {
            "clone": str(clone),
            "logical_time": projection.logical_time,
            "ledger_sequence": projection.ledger_sequence,
            "world_revision": projection.world_revision,
            "media_deliveries": deliveries,
            "candidate_hops": candidate_hops,
            "world_life": world_life,
            "snapshot": snapshot_hops,
        }
    finally:
        await session.close()


def summarize(report: dict[str, Any]) -> dict[str, Any]:
    hops = report["candidate_hops"]["hops"]
    snapshot = report["snapshot"]
    inventory = snapshot.get("hop_9_compile_inner_life_snapshot") or {}
    return {
        "logical_time": report.get("logical_time"),
        "ledger_sequence": report.get("ledger_sequence"),
        "projection_candidates": hops[0]["count"] if hops else None,
        "after_status_or_reask": hops[1]["count"] if len(hops) > 1 else None,
        "after_expiry": hops[2]["count"] if len(hops) > 2 else None,
        "available_source_refs": hops[3]["count"] if len(hops) > 3 else None,
        "world_life_settled": report["world_life"]["count_settled"],
        "world_life_photo_in_hand": report["world_life"]["count_photo_in_hand"],
        "refs_not_on_settlement": report["world_life"]["available_refs_not_on_any_settlement"],
        "capsule_world_life_items": snapshot["hop_6_capsule_world_life_items"]["count"],
        "capsule_photo_in_hand_true": snapshot["hop_6_capsule_world_life_items"]["photo_in_hand_true"],
        "experience_entries": snapshot["hop_7_experience_entry"]["count"],
        "available_count": inventory.get("available_count"),
        "inventory_items": len(inventory.get("items") or []) if isinstance(inventory, dict) else None,
        "now": inventory.get("now") if isinstance(inventory, dict) else None,
        "installed_available_count": (
            (snapshot.get("hop_11_installed_shareable_photos") or {})
            .get("moments_i_can_share")
            or {}
        ).get("available_count"),
        "live_available_count": (
            (snapshot.get("hop_10_interior_project") or {}).get("available_count")
            if isinstance(snapshot.get("hop_10_interior_project"), dict)
            else None
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-clone", action="store_true")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    clone = OUTPUT / "clone.sqlite"
    if not args.skip_clone or not clone.exists():
        clone = clone_production()
    events = dump_ledger_events(clone)
    report = asyncio.run(run_async(clone))
    report["ledger_event_counts"] = events["counts"]
    report["stored_moments"] = events["stored_moments"]
    report["summary"] = summarize(report)
    dump_json(OUTPUT / "hops.json", report)
    dump_json(OUTPUT / "summary.json", report["summary"])
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
