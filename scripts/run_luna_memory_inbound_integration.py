#!/usr/bin/env python3
"""One capture-only real inbound turn against an isolated complete World clone."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/private-audits/luna-memory-integration-20260929"
DB = OUT / "world.sqlite"
WORLD = "world:companion-v2:qq-c2c:geoff"
PROMPT = "最近我有点闷，周末想找个地方坐坐。你自己会更愿意出去，还是留在宿舍？两种都行。"


def event_rows(path: Path, after_sequence: int) -> list[dict[str, object]]:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT ledger_sequence,event_json FROM world_v2_events "
            "WHERE world_id=? AND ledger_sequence>? ORDER BY ledger_sequence",
            (WORLD, after_sequence),
        ).fetchall()
    finally:
        conn.close()
    result = []
    for sequence, raw in rows:
        envelope = json.loads(raw)
        payload = envelope.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        if not isinstance(payload, dict):
            payload = {}
        result.append({
            "ledger_sequence": sequence,
            "event_id": envelope.get("event_id"),
            "event_type": envelope.get("event_type"),
            "event_payload_hash": envelope.get("payload_hash"),
            "idempotency_key": envelope.get("idempotency_key"),
            "purpose": payload.get("purpose"),
            "source_refs": payload.get("source_refs"),
            "inner_turn_lineage": payload.get("character_interior_turn_lineage"),
        })
    return result


async def main() -> None:
    if not DB.is_file():
        raise SystemExit("private clone is missing; create it from grounding-repair source.sqlite first")
    OUT.chmod(0o700)
    # Use the configured debug balance as this private ledger's API credential.
    from dotenv import dotenv_values
    from companion_daemon.config import Settings

    values = dotenv_values(ROOT / ".env")
    debug_key = values.get("DEEPSEEK_DEBUG_API_KEY")
    if not debug_key:
        raise SystemExit("DEEPSEEK_DEBUG_API_KEY is missing")
    os.environ["DEEPSEEK_API_KEY"] = debug_key
    os.environ["DEEPSEEK_MODEL"] = "deepseek-flash"
    os.environ.pop("WORLD_V2_PREHISTORY_PACKAGE_PATH", None)
    settings = Settings(_env_file=ROOT / ".env")
    if settings.world_v2_recall_embedding_model != "bge-m3" or not settings.world_v2_recall_semantic_enabled:
        raise SystemExit("configured BGE-M3 semantic recall is not enabled")

    from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
    from companion_daemon.world_v2.longitudinal_model_input_capture import (
        ModelInputCaptureTransport,
        PrivateModelInputCapture,
    )

    # Use the existing capture transport at the real DeepSeek HTTP boundary.
    import httpx
    import companion_daemon.llm as llm

    capture = PrivateModelInputCapture(OUT / "model-inputs.jsonl")
    real_model = llm.DeepSeekChatModel

    class CapturedDeepSeekChatModel(real_model):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = ModelInputCaptureTransport(
                inner=httpx.AsyncHTTPTransport(trust_env=False),
                capture=capture,
                model_role="character_or_world_support",
            )
            super().__init__(*args, **kwargs)

    llm.DeepSeekChatModel = CapturedDeepSeekChatModel
    from drive_production_lanes import open_session
    from companion_daemon.world_v2.longitudinal_journey import JourneyClock

    clock_ledger = SQLiteWorldLedger(path=DB, world_id=WORLD)
    try:
        before = clock_ledger.project()
        cursor_before = {
            "world_revision": before.world_revision,
            "deliberation_revision": before.deliberation_revision,
            "ledger_sequence": before.ledger_sequence,
            "logical_time": before.logical_time.isoformat() if before.logical_time else None,
            "active_memories": sum(m.values.status == "active" for m in before.memory_candidates),
            "appraisals": len(before.appraisals),
            "affects": len(before.affect_episodes),
        }
    finally:
        clock_ledger.close()

    started = time.monotonic()
    session = await open_session(
        database=DB,
        output_dir=OUT,
        enable_media=False,
        primary_user_id="geoff",
        journey_clock=JourneyClock(datetime.fromisoformat(cursor_before["logical_time"])),
        use_configured_recall_embedding=True,
    )
    try:
        result = await session.inbound(PROMPT, background_units=0)
        after_ledger = SQLiteWorldLedger(path=DB, world_id=WORLD)
        try:
            after = after_ledger.project()
            cursor_after = {
                "world_revision": after.world_revision,
                "deliberation_revision": after.deliberation_revision,
                "ledger_sequence": after.ledger_sequence,
                "logical_time": after.logical_time.isoformat() if after.logical_time else None,
                "active_memories": sum(m.values.status == "active" for m in after.memory_candidates),
                "appraisals": len(after.appraisals),
                "affects": len(after.affect_episodes),
            }
            events = event_rows(DB, cursor_before["ledger_sequence"])
        finally:
            after_ledger.close()
        records = capture.read_since()[1]
        result_doc = {
            "contract": "luna-memory-real-inbound.1",
            "scope": "one isolated capture-only inbound turn; complete same-World clone; no QQ transport",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "model": "deepseek-flash (DeepSeek V4.1)",
            "recall_embedding": {
                "enabled": settings.world_v2_recall_semantic_enabled,
                "model": settings.world_v2_recall_embedding_model,
                "base_url": settings.world_v2_recall_embedding_base_url,
            },
            "input": PROMPT,
            "input_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
            "before": cursor_before,
            "inbound_result": result,
            "after": cursor_after,
            "new_events": events,
            "model_input_capture_health": capture.health(),
            "model_input_capture_records": list(records),
            "seconds": round(time.monotonic() - started, 3),
            "delivery": session.delivery.sent,
            "claim_boundary": "transport request proves supplied input; event refs prove source binding; provider attention and causal influence require separate evidence",
        }
        path = OUT / "real-inbound.json"
        path.write_text(json.dumps(result_doc, ensure_ascii=False, indent=2), encoding="utf-8")
        path.chmod(0o600)
        print(json.dumps({
            "inbound_status": result.get("status"),
            "visible_count": len(result.get("visible", [])),
            "new_event_count": len(events),
            "event_types": sorted({str(item["event_type"]) for item in events}),
            "capture_health": capture.health(),
            "seconds": result_doc["seconds"],
        }, ensure_ascii=False, indent=2), flush=True)
    finally:
        await session.close()


if __name__ == "__main__":
    os.chdir(ROOT)
    asyncio.run(main())
