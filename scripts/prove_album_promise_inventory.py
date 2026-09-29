#!/usr/bin/env python3
"""Accept the empty-album fact against the live production ledger (read-only).

Never writes ``data/``. Never generates images. Artifacts: ``output/album-promise/``.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
import sys

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from companion_daemon.world_v2.character_interior.contracts import (
    assert_compile_time_materials_are_source_bound,
)
from companion_daemon.world_v2.character_interior.snapshot_compiler import (
    compile_inner_life_snapshot,
)
from companion_daemon.world_v2.photographable_inventory import (
    available_photo_source_refs,
)

PRODUCTION_DB = (REPO / "data" / "companion.epoch2.sqlite").resolve()
WORLD_ID = "world:companion-v2:qq-c2c:geoff"
OUTPUT = (REPO / "output" / "album-promise").resolve()
MEDIA_TYPES = frozenset(
    {
        "PhotoCandidateOpened",
        "MediaSelectionAttemptRecorded",
        "MediaSelectionProposalRecorded",
        "MediaPlanRecorded",
        "MediaPreviewGenerated",
        "ImageEvidenceDeclared",
        "RecipientScopedImageEvidenceDeclared",
    }
)


def _payload(event: dict) -> dict:
    payload = event.get("payload")
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return {}
    if isinstance(payload, dict):
        return payload
    raw = event.get("payload_json")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{PRODUCTION_DB}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT ledger_sequence, event_json FROM world_v2_events "
            "WHERE world_id = ? ORDER BY ledger_sequence",
            (WORLD_ID,),
        ).fetchall()
        texts = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT content_ref, text FROM world_v2_life_content WHERE world_id = ?",
                (WORLD_ID,),
            )
        }
    finally:
        conn.close()

    events = []
    media_in_ask_window = []
    for seq, raw in rows:
        event = json.loads(raw)
        event["_seq"] = seq
        event["_type"] = event.get("event_type")
        events.append(event)
        if 4496 <= seq <= 4713 and event["_type"] in MEDIA_TYPES:
            media_in_ask_window.append({"seq": seq, "type": event["_type"]})

    settled = [item for item in events if item["_type"] == "WorldOccurrenceSettled"]
    opened = [item for item in events if item["_type"] == "PhotoCandidateOpened"]
    projection = type("P", (), {"photo_candidates": ()})()
    available = available_photo_source_refs(projection, logical_time=datetime.now(UTC))

    world_life_items = []
    for event in settled:
        payload = _payload(event)
        ref = event.get("event_id")
        result_ref = payload.get("result_payload_ref")
        text = texts.get(result_ref) if isinstance(result_ref, str) else None
        world_life_items.append(
            {
                "source_ref": ref,
                "privacy_class": "shareable",
                "value": {
                    "occurrence_id": payload.get("occurrence_id"),
                    "occurrence_entity_revision": 4,
                    "participant_refs": ["agent:companion"],
                    "result_id": payload.get("result_id"),
                    "settled_at": payload.get("settled_at") or event.get("logical_time"),
                    "privacy_class": "shareable",
                    "photo_in_hand": False,
                    **({"content": {"text": text}} if text else {}),
                },
            }
        )

    snapshot = compile_inner_life_snapshot(
        {
            "world_id": WORLD_ID,
            "actor_ref": "agent:companion",
            "world_revision": 1,
            "deliberation_revision": 1,
            "ledger_sequence": events[-1]["_seq"] if events else 1,
            "logical_time": events[-1].get("logical_time") if events else datetime.now(UTC).isoformat(),
            "slices": {
                "world_life": {
                    "availability": "available",
                    "source_refs": [item["source_ref"] for item in world_life_items],
                    "items": world_life_items,
                }
            },
        }
    )
    materials = json.loads(snapshot.materials_json)
    inventory = materials["moments_i_can_share"]
    assert_compile_time_materials_are_source_bound({"moments_i_can_share": inventory})
    assert opened == []
    assert available == frozenset()
    assert inventory["available_count"] == 0
    assert inventory["already_sent_count"] == 0
    assert media_in_ask_window == []
    assert "值得分享" not in json.dumps(inventory, ensure_ascii=False)

    proof = {
        "head_seq": events[-1]["_seq"] if events else None,
        "photo_candidate_opened": len(opened),
        "world_occurrence_settled": len(settled),
        "media_events_seq_4496_to_4713": media_in_ask_window,
        "available_photo_source_refs": [],
        "moments_i_can_share": inventory,
        "accepted": True,
    }
    (OUTPUT / "inventory-proof.json").write_text(
        json.dumps(proof, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "accepted": True,
        "available_count": inventory["available_count"],
        "moments": len(inventory["items"]),
        "media_in_ask_window": 0,
        "out": str(OUTPUT / "inventory-proof.json"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
