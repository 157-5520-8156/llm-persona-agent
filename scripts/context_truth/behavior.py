"""Behaviour-level pass: her decision vs the facts she was told at that cursor.

This does not call a model. It reads ``world_v2_character_interior_turns``
authored snapshots (what she actually saw) and pairs them with the decision
payload plus a ledger prefix count at that cursor.

If authored snapshots are missing, we cannot recover Path A historically
without replaying ``project_at`` for every turn — that is called out as a
blocker rather than approximated.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import sqlite3
from pathlib import Path
from typing import Any

from .slots import PHOTO_SENT_FRAGMENTS, _week_diary_claims_photo_sent
from .types import SeenView

PHOTO_PROMISE_FRAGMENTS = (
    "发你",
    "发给你",
    "发一张",
    "发几张",
    "把照片",
    "翻相册",
    "给你看",
    "拍一张",
    "发过去",
)


@dataclass
class BehaviorCase:
    inner_turn_id: str
    purpose: str
    cursor_seq: int | None
    logical_time: str | None
    decision_texts: list[str]
    seen_available_count: int | None
    seen_already_sent: int | None
    seen_photo_sent_claim: bool
    ledger_deliveries_at_cursor: int
    ledger_photo_opened_at_cursor: int
    mismatch: str | None
    severity: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_json(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _snapshot_materials(authored: dict[str, Any]) -> dict[str, Any]:
    snapshot = authored.get("snapshot")
    if not isinstance(snapshot, dict):
        return {}
    materials = snapshot.get("materials")
    if isinstance(materials, dict):
        return materials
    blob = snapshot.get("materials_json")
    if isinstance(blob, str) and blob:
        try:
            decoded = json.loads(blob)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _decision_texts(authored: dict[str, Any], terminal: dict[str, Any] | None) -> list[str]:
    texts: list[str] = []
    blobs = [authored, terminal or {}]
    for blob in blobs:
        for root_key in ("result", "decision", "payload"):
            root = blob.get(root_key) if isinstance(blob.get(root_key), dict) else blob
            if not isinstance(root, dict):
                continue
            decision = root.get("decision") if isinstance(root.get("decision"), dict) else root
            payload = (
                decision.get("payload") if isinstance(decision.get("payload"), dict) else decision
            )
            beats = payload.get("beats") if isinstance(payload, dict) else None
            if not isinstance(beats, list):
                continue
            for beat in beats:
                if not isinstance(beat, dict):
                    continue
                text = beat.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
    summary = authored.get("result") if isinstance(authored.get("result"), dict) else {}
    inner = summary.get("summary")
    if isinstance(inner, str) and inner.strip():
        texts.append(inner.strip())
    return list(dict.fromkeys(texts))


def _media_prefix_index(
    conn: sqlite3.Connection, world_id: str, needed: set[int]
) -> dict[int, tuple[int, int]]:
    if not needed:
        return {}
    deliveries = opened = 0
    found: dict[int, tuple[int, int]] = {}
    remaining = set(needed)
    rows = conn.execute(
        "SELECT ledger_sequence, event_json FROM world_v2_events "
        "WHERE world_id = ? ORDER BY ledger_sequence",
        (world_id,),
    )
    for seq, raw in rows:
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        kind = str(event.get("event_type") or "")
        if kind == "MediaDeliveryShared":
            deliveries += 1
        elif kind == "PhotoCandidateOpened":
            opened += 1
        if seq in remaining:
            found[int(seq)] = (deliveries, opened)
            remaining.remove(seq)
            if not remaining:
                break
    return found


def _available_count(materials: dict[str, Any]) -> int | None:
    inventory = materials.get("moments_i_can_share")
    if not isinstance(inventory, dict):
        return None
    count = inventory.get("available_count")
    return int(count) if isinstance(count, int) and not isinstance(count, bool) else None


def _already_sent(materials: dict[str, Any]) -> int | None:
    inventory = materials.get("moments_i_can_share")
    if not isinstance(inventory, dict):
        return None
    count = inventory.get("already_sent_count")
    return int(count) if isinstance(count, int) and not isinstance(count, bool) else None


def audit_behavior(
    conn: sqlite3.Connection,
    *,
    world_id: str,
    limit: int = 48,
) -> dict[str, Any]:
    try:
        rows = conn.execute(
            """
            SELECT inner_turn_id, purpose, phase, cursor_json, authored_state_json,
                   terminal_result_json, updated_at
            FROM world_v2_character_interior_turns
            WHERE world_id = ? AND authored_state_json IS NOT NULL
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (world_id, limit),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        return {
            "status": "blocked",
            "blocker": (
                "character interior turn sidecar is missing or unreadable: "
                f"{exc}. Historical Path A (what she was told) is stored there."
            ),
            "cases": [],
        }
    cases: list[BehaviorCase] = []
    skipped_no_snapshot = 0
    pending: list[dict[str, Any]] = []
    needed_seqs: set[int] = set()
    for row in rows:
        authored = _load_json(row["authored_state_json"])
        if authored is None:
            skipped_no_snapshot += 1
            continue
        materials = _snapshot_materials(authored)
        if not materials:
            skipped_no_snapshot += 1
            continue
        terminal = _load_json(row["terminal_result_json"])
        texts = _decision_texts(authored, terminal)
        cursor = _load_json(row["cursor_json"]) or {}
        seq = cursor.get("ledger_sequence")
        seq_n = int(seq) if isinstance(seq, int) else None
        if seq_n is not None:
            needed_seqs.add(seq_n)
        pending.append(
            {
                "row": row,
                "authored": authored,
                "materials": materials,
                "texts": texts,
                "seq_n": seq_n,
            }
        )
    prefixes = _media_prefix_index(conn, world_id, needed_seqs)
    for item in pending:
        texts = item["texts"]
        seq_n = item["seq_n"]
        materials = item["materials"]
        row = item["row"]
        deliveries, opened = prefixes.get(seq_n, (0, 0)) if seq_n is not None else (0, 0)
        seen = SeenView.from_mapping({"materials": materials})
        available = _available_count(materials)
        already = _already_sent(materials)
        claimed_sent = _week_diary_claims_photo_sent(seen) or any(
            fragment in " ".join(texts) for fragment in PHOTO_SENT_FRAGMENTS
        )
        promised = any(
            fragment in " ".join(texts) for fragment in PHOTO_PROMISE_FRAGMENTS
        )
        mismatch = None
        severity = "info"
        if claimed_sent and deliveries == 0:
            mismatch = "她当时把「已经发出照片」说成既成事实，该 cursor 的 MediaDeliveryShared=0"
            severity = "critical"
        elif promised and available == 0 and opened > 0:
            mismatch = (
                f"她当时承诺发照片，快照 available_count=0，但账本已打开 {opened} 张候选"
            )
            severity = "critical"
        elif promised and available == 0 and opened == 0:
            mismatch = "她当时承诺发照片，快照与账本都显示相册为空"
            severity = "high"
        if mismatch is None and not promised and not claimed_sent:
            continue
        if mismatch is None:
            continue
        logical = materials.get("logical_time")
        cases.append(
            BehaviorCase(
                inner_turn_id=str(row["inner_turn_id"]),
                purpose=str(row["purpose"] or ""),
                cursor_seq=seq_n,
                logical_time=str(logical) if logical else None,
                decision_texts=texts[:6],
                seen_available_count=available,
                seen_already_sent=already,
                seen_photo_sent_claim=claimed_sent,
                ledger_deliveries_at_cursor=deliveries,
                ledger_photo_opened_at_cursor=opened,
                mismatch=mismatch,
                severity=severity,
                evidence={"phase": row["phase"], "updated_at": row["updated_at"]},
            )
        )
    cases.sort(key=lambda item: SEVERITY_RANK.get(item.severity, 9))
    return {
        "status": "ok",
        "turns_scanned": len(rows),
        "skipped_no_snapshot": skipped_no_snapshot,
        "mismatch_cases": len(cases),
        "cases": [item.as_dict() for item in cases],
        "note": (
            "Path A here is the authored InnerLifeSnapshot stored on the turn, "
            "not a recompile. Path B is an event-prefix count at that cursor."
        ),
    }


SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def audit_behavior_file(database: Path, *, world_id: str, limit: int = 48) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return audit_behavior(conn, world_id=world_id, limit=limit)
    finally:
        conn.close()
