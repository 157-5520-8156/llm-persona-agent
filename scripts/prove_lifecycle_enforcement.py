#!/usr/bin/env python3
"""Clone-only proof that declared appraisal expiry and affect residue close run.

Never writes ``data/``, never talks to 8787 or NapCat. Artifacts live in
``output/lifecycle-enforcement/``.

Usage::

    .venv/bin/python scripts/prove_lifecycle_enforcement.py
    .venv/bin/python scripts/prove_lifecycle_enforcement.py --skip-speak
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

REPO = Path(__file__).resolve().parents[1]
for path in (REPO / "scripts", REPO / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import drive_production_lanes as drive
from companion_daemon.world_v2.recall_corpus import RecallCorpusCompiler, RecallCorpusSources
from companion_daemon.world_v2.recall_index import RecallCursor, RecallSourceBinding

OUTPUT = (REPO / "output" / "lifecycle-enforcement").resolve()
WORLD_ID = drive.WORLD_ID
COST_CAP_CNY = 8.0
MESSAGES = (
    "还没睡，倒了杯水。",
    "你现在还好吗。",
    "我这边也还醒着。",
    "今天过得怎么样。",
    "有没有什么正在想的事。",
    "那就先这样聊着，不急。",
)


def _hypotheses(item: object) -> list[str]:
    values = []
    for hypothesis in getattr(item, "hypotheses", ()) or ():
        meaning = getattr(hypothesis, "meaning", None)
        if isinstance(meaning, str) and meaning.strip():
            values.append(meaning.strip())
    return values


def _appraisal_row(item: object, *, now: datetime) -> dict[str, Any]:
    expires_at = getattr(item, "expires_at", None)
    accepted_at = getattr(item, "accepted_at", None)
    overdue = False
    if isinstance(expires_at, datetime) and expires_at.tzinfo is not None:
        overdue = expires_at <= now
    return {
        "appraisal_id": getattr(item, "appraisal_id", None),
        "status": getattr(item, "status", None),
        "confidence_bp": getattr(item, "confidence_bp", None),
        "accepted_at": accepted_at.isoformat() if isinstance(accepted_at, datetime) else accepted_at,
        "expires_at": expires_at.isoformat() if isinstance(expires_at, datetime) else expires_at,
        "overdue_while_active": overdue and getattr(item, "status", None) == "active",
        "meanings": _hypotheses(item),
        "origin_event": getattr(getattr(item, "origin", None), "accepted_event_ref", None),
        "evidence_refs": [getattr(ref, "ref_id", None) for ref in getattr(item, "evidence_refs", ()) or ()],
    }


def _affect_row(item: object) -> dict[str, Any]:
    components = []
    for component in getattr(item, "components", ()) or ():
        components.append(
            {
                "component_id": getattr(component, "component_id", None),
                "dimension": getattr(component, "dimension", None),
                "intensity_bp": getattr(component, "intensity_bp", None),
                "residue_bp": getattr(component, "residue_bp", None),
            }
        )
    return {
        "episode_id": getattr(item, "episode_id", None),
        "status": getattr(item, "status", None),
        "opened_at": (
            item.opened_at.isoformat()
            if isinstance(getattr(item, "opened_at", None), datetime)
            else getattr(item, "opened_at", None)
        ),
        "components": components,
    }


def inventory(projection: object) -> dict[str, Any]:
    now = getattr(projection, "logical_time", None)
    appraisals = tuple(getattr(projection, "appraisals", ()) or ())
    episodes = tuple(getattr(projection, "affect_episodes", ()) or ())
    impressions = tuple(getattr(projection, "private_impressions", ()) or ())
    by_status: dict[str, int] = {}
    for item in appraisals:
        status = str(getattr(item, "status", "unknown"))
        by_status[status] = by_status.get(status, 0) + 1
    affect_status: dict[str, int] = {}
    for item in episodes:
        status = str(getattr(item, "status", "unknown"))
        affect_status[status] = affect_status.get(status, 0) + 1
    impression_status: dict[str, int] = {}
    impression_refs: list[str] = []
    for item in impressions:
        status = str(getattr(item, "status", "unknown"))
        impression_status[status] = impression_status.get(status, 0) + 1
        if status != "active":
            continue
        for ref in getattr(item, "interpretation_refs", ()) or ():
            if isinstance(ref, str) and ref.strip():
                impression_refs.append(ref)
    active = [_appraisal_row(item, now=now) for item in appraisals if getattr(item, "status", None) == "active"]
    overdue = [item for item in active if item["overdue_while_active"]]
    expired = [_appraisal_row(item, now=now) for item in appraisals if getattr(item, "status", None) == "expired"]
    active.sort(key=lambda item: (-int(item["confidence_bp"] or 0), str(item["appraisal_id"])))
    overdue_bound = [
        item["appraisal_id"]
        for item in overdue
        if any(str(item["appraisal_id"]) in ref for ref in impression_refs)
    ]
    return {
        "logical_time": now.isoformat() if isinstance(now, datetime) else now,
        "world_revision": getattr(projection, "world_revision", None),
        "ledger_sequence": getattr(projection, "ledger_sequence", None),
        "deliberation_revision": getattr(projection, "deliberation_revision", None),
        "appraisal_status": by_status,
        "affect_status": affect_status,
        "impression_status": impression_status,
        "active_appraisals": len(active),
        "overdue_active_appraisals": len(overdue),
        "expired_appraisals": len(expired),
        "overdue_bound_to_active_impressions": overdue_bound,
        "heaviest_active_meanings": [
            {
                "appraisal_id": item["appraisal_id"],
                "confidence_bp": item["confidence_bp"],
                "meanings": item["meanings"],
            }
            for item in active[:8]
        ],
        "overdue_meanings": [
            {
                "appraisal_id": item["appraisal_id"],
                "meanings": item["meanings"],
                "expires_at": item["expires_at"],
                "confidence_bp": item["confidence_bp"],
            }
            for item in overdue[:16]
        ],
        "expired_sample": [
            {
                "appraisal_id": item["appraisal_id"],
                "meanings": item["meanings"],
                "origin_event": item["origin_event"],
                "evidence_refs": item["evidence_refs"],
            }
            for item in expired[:8]
        ],
        "affect_episodes": [_affect_row(item) for item in episodes],
    }


def _her_texts(visible: list[dict[str, Any]]) -> list[str]:
    texts: list[str] = []
    for item in visible:
        if item.get("kind") != "text":
            continue
        body = item.get("body")
        if isinstance(body, str) and body.strip():
            texts.append(body.strip())
    return texts


def _mentions(text: str, needles: list[str]) -> list[str]:
    hit = []
    for needle in needles:
        if needle and needle in text:
            hit.append(needle)
    return hit


def _count_event_types(database: Path, *, after_seq: int) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            """
            SELECT json_extract(event_json, '$.event_type') AS event_type, COUNT(*)
            FROM world_v2_events
            WHERE world_id = ? AND ledger_sequence > ?
            GROUP BY 1
            """,
            (WORLD_ID, after_seq),
        ).fetchall()
        return {str(kind): int(count) for kind, count in rows if kind}
    finally:
        conn.close()


def _event_binding(database: Path, event_id: str) -> RecallSourceBinding | None:
    if not event_id:
        return None
    conn = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    try:
        row = conn.execute(
            """
            SELECT world_revision, event_json
            FROM world_v2_events
            WHERE world_id = ? AND json_extract(event_json, '$.event_id') = ?
            LIMIT 1
            """,
            (WORLD_ID, event_id),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    event = json.loads(row[1])
    digest = event.get("payload_hash")
    if not isinstance(digest, str) or len(digest) != 64:
        return None
    return RecallSourceBinding(
        source_kind="committed_event",
        authority_type=str(event.get("event_type") or "WorldEvent"),
        ref=event_id,
        source_world_revision=int(row[0]),
        immutable_hash=digest,
    )


def _recall_expired(database: Path, projection: object, expired_sample: list[dict[str, Any]]) -> dict[str, Any]:
    samples = []
    for item in getattr(projection, "appraisals", ()) or ():
        if getattr(item, "status", None) != "expired":
            continue
        samples.append(item)
        if len(samples) >= 4:
            break
    bindings: list[RecallSourceBinding] = []
    for item in samples:
        origin = getattr(getattr(item, "origin", None), "accepted_event_ref", None)
        binding = _event_binding(database, str(origin or ""))
        if binding is not None:
            bindings.append(binding)
        for ref in getattr(item, "evidence_refs", ()) or ():
            extra = _event_binding(database, str(getattr(ref, "ref_id", "") or ""))
            if extra is not None:
                bindings.append(extra)
    cursor = RecallCursor(
        world_revision=int(getattr(projection, "world_revision", 1) or 1),
        deliberation_revision=int(getattr(projection, "deliberation_revision", 0) or 0),
        ledger_sequence=int(getattr(projection, "ledger_sequence", 1) or 1),
    )
    try:
        documents = RecallCorpusCompiler().compile(
            cursor=cursor,
            actor_ref="agent:companion",
            subject_refs=("agent:companion", "user:geoff"),
            sources=RecallCorpusSources(
                appraisals=tuple(samples),
                authority_bindings=tuple(bindings),
            ),
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}"[:400],
            "expired_still_on_projection": len(expired_sample),
        }
    recalled = [
        {
            "source_item_ref": document.source_item_ref,
            "status": document.status,
            "text": document.text[:240],
        }
        for document in documents
        if document.status == "expired"
    ]
    return {
        "ok": bool(recalled),
        "recalled_expired": len(recalled),
        "documents": recalled,
        "expired_still_on_projection": len(
            [item for item in getattr(projection, "appraisals", ()) or () if getattr(item, "status", None) == "expired"]
        ),
    }


def _recent_production_lines(clone: Path, *, limit: int = 8) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{clone.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT authored_state_json, updated_at
            FROM world_v2_character_interior_turns
            WHERE world_id = ? AND purpose = 'inbound_turn'
              AND authored_state_json IS NOT NULL
            ORDER BY updated_at DESC LIMIT 1
            """,
            (WORLD_ID,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return []
    authored = json.loads(row["authored_state_json"] or "{}")
    snapshot = authored.get("snapshot") if isinstance(authored, dict) else {}
    materials = snapshot.get("materials") if isinstance(snapshot, dict) else {}
    recent = materials.get("recent_dialogue") if isinstance(materials, dict) else []
    lines: list[dict[str, Any]] = []
    if isinstance(recent, list):
        for entry in recent:
            if not isinstance(entry, dict):
                continue
            speaker = str(entry.get("speaker") or "")
            text = entry.get("text")
            if speaker in {"companion", "agent", "self"} and isinstance(text, str) and text.strip():
                lines.append({"speaker": speaker, "text": text.strip(), "occurred_at": entry.get("occurred_at")})
    return lines[-limit:]


def _snapshot_attention(clone: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{clone.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT authored_state_json, updated_at, trigger_ref
            FROM world_v2_character_interior_turns
            WHERE world_id = ? AND purpose = 'inbound_turn'
              AND authored_state_json IS NOT NULL
            ORDER BY updated_at DESC LIMIT 1
            """,
            (WORLD_ID,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {}
    authored = json.loads(row["authored_state_json"] or "{}")
    snapshot = authored.get("snapshot") if isinstance(authored, dict) else {}
    materials = snapshot.get("materials") if isinstance(snapshot, dict) else {}
    appraisals = materials.get("appraisals") if isinstance(materials, dict) else []
    affect = materials.get("affect") if isinstance(materials, dict) else []
    statuses: dict[str, int] = {}
    meanings: list[str] = []
    if isinstance(appraisals, list):
        for item in appraisals:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "unknown")
            statuses[status] = statuses.get(status, 0) + 1
            for hypothesis in item.get("hypotheses") or ():
                if isinstance(hypothesis, dict) and hypothesis.get("meaning"):
                    meanings.append(str(hypothesis["meaning"]))
    affect_status: dict[str, int] = {}
    if isinstance(affect, list):
        for item in affect:
            if isinstance(item, dict):
                status = str(item.get("status") or "unknown")
                affect_status[status] = affect_status.get(status, 0) + 1
    return {
        "trigger_ref": row["trigger_ref"],
        "updated_at": row["updated_at"],
        "appraisal_status_in_snapshot": statuses,
        "affect_status_in_snapshot": affect_status,
        "snapshot_meanings": meanings[:12],
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-speak", action="store_true")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    clone = OUTPUT / "clone.sqlite"
    drive.clone_ledger(drive.PRODUCTION_DB, clone)
    usage_before = drive.current_usage_id(clone)
    seq_before = drive.current_seq(clone)
    before_lines = _recent_production_lines(clone)

    session = await drive.open_session(
        database=clone,
        output_dir=OUTPUT,
        enable_media=False,
    )
    try:
        projection = session.projection()
        if projection is None:
            raise SystemExit("clone projection missing")
        before = inventory(projection)
        (OUTPUT / "inventory_before.json").write_text(
            json.dumps(before, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        now = datetime.now(UTC)
        tick = await session.tick_to(now, reason="lifecycle-enforcement", run_life=False)
        after_projection = session.projection()
        if after_projection is None:
            raise SystemExit("clone projection missing after tick")
        after = inventory(after_projection)
        (OUTPUT / "inventory_after.json").write_text(
            json.dumps(after, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        recall = _recall_expired(clone, after_projection, after.get("expired_sample") or [])
        conversation: list[dict[str, Any]] = []
        if not args.skip_speak:
            overdue_needles = [
                meaning
                for item in before.get("overdue_meanings") or ()
                for meaning in item.get("meanings") or ()
                if isinstance(meaning, str) and len(meaning.strip()) >= 4
            ]
            for index, text in enumerate(MESSAGES, start=1):
                cost = drive.cost_report(clone, since_id=usage_before)
                if float(cost["cost_cny"]) >= COST_CAP_CNY:
                    conversation.append({"stop": "cost_cap", "cost": cost})
                    break
                result = await session.inbound(text)
                her = _her_texts(list(result.get("visible") or []))
                conversation.append(
                    {
                        "turn": index,
                        "user": text,
                        "status": result.get("status"),
                        "her": her,
                        "old_reading_hits": [
                            hit for line in her for hit in _mentions(line, overdue_needles)
                        ],
                    }
                )
        cost = drive.cost_report(clone, since_id=usage_before)
        counts = _count_event_types(clone, after_seq=seq_before)
        snapshot = _snapshot_attention(clone) if not args.skip_speak else {}
        report = {
            "tick": {
                "status": str(tick.get("status")),
                "logical_time": tick.get("logical_time"),
                "tick_id": tick.get("tick_id"),
            },
            "before": {
                "active_appraisals": before["active_appraisals"],
                "overdue_active_appraisals": before["overdue_active_appraisals"],
                "affect_status": before["affect_status"],
                "impression_status": before["impression_status"],
                "heaviest_active_meanings": before["heaviest_active_meanings"],
                "overdue_meanings": before["overdue_meanings"],
                "recent_her_lines": before_lines,
            },
            "after": {
                "active_appraisals": after["active_appraisals"],
                "overdue_active_appraisals": after["overdue_active_appraisals"],
                "expired_appraisals": after["expired_appraisals"],
                "affect_status": after["affect_status"],
                "heaviest_active_meanings": after["heaviest_active_meanings"],
            },
            "new_event_types": {
                "AppraisalExpired": counts.get("AppraisalExpired", 0),
                "AffectEpisodeDecayed": counts.get("AffectEpisodeDecayed", 0),
                "AffectEpisodeResolved": counts.get("AffectEpisodeResolved", 0),
                "ClockAdvanced": counts.get("ClockAdvanced", 0),
            },
            "recall": recall,
            "post_conversation_snapshot": snapshot,
            "conversation": conversation,
            "cost": cost,
        }
        (OUTPUT / "proof.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        if after["overdue_active_appraisals"] != 0:
            return 3
        if float(cost["cost_cny"]) > COST_CAP_CNY:
            return 2
        return 0
    finally:
        await session.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
