#!/usr/bin/env python3
"""Clone-only audit: dump the InnerLifeSnapshot she actually sees.

Never writes ``data/``, never talks to 8787 or NapCat. Artifacts live in
``output/redaction-audit/``.

Usage::

    .venv/bin/python scripts/audit_redaction_model_view.py
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

OUTPUT = (REPO / "output" / "redaction-audit").resolve()
PRODUCTION_DB = (REPO / "data" / "companion.epoch2.sqlite").resolve()

_DRIVE_PATH = REPO / "scripts" / "drive_production_lanes.py"
_spec = importlib.util.spec_from_file_location("drive_production_lanes", _DRIVE_PATH)
drive = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["drive_production_lanes"] = drive
_spec.loader.exec_module(drive)


def _read_appraisal_meanings(database: Path) -> list[str]:
    conn = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    texts: list[str] = []
    try:
        for (raw,) in conn.execute("SELECT event_json FROM world_v2_events"):
            event = drive.parse_event(raw)
            if event.get("event_type") != "AppraisalAccepted":
                continue
            payload = event.get("_payload") or {}
            blob = json.dumps(payload, ensure_ascii=False)
            if "meaning" in blob:
                texts.append(blob[:240])
    finally:
        conn.close()
    return texts[:12]


def _patch_compiler(dump_dir: Path) -> None:
    from companion_daemon.world_v2.character_interior import production as production_mod
    from companion_daemon.world_v2.character_interior import snapshot_compiler as compiler_mod

    original = compiler_mod.compile_inner_life_snapshot
    dump_dir.mkdir(parents=True, exist_ok=True)

    def wrapped(context, *, source_envelopes=None):
        snapshot = original(context, source_envelopes=source_envelopes)
        hashed = json.loads(snapshot.materials_json)
        full = snapshot.model_view()
        impression_refs = {
            item.source_ref
            for item in snapshot.source_inventory
            if item.scope == "private_impressions"
        }
        hidden = snapshot.model_view(
            visible_source_refs=frozenset(snapshot.source_refs) - impression_refs
        )
        impression_texts = [
            item["reflection_summary"]
            for item in hashed.get("private_impressions") or []
            if isinstance(item, dict) and isinstance(item.get("reflection_summary"), str)
        ]
        hidden_blob = json.dumps(hidden, ensure_ascii=False)
        leak = [text for text in impression_texts if text and text in hidden_blob]
        (dump_dir / "compile_time_keys.json").write_text(
            json.dumps(sorted(hashed), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (dump_dir / "model_view.json").write_text(
            json.dumps(full, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (dump_dir / "model_view_without_impressions.json").write_text(
            json.dumps(hidden, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        materials = full.get("materials") if isinstance(full.get("materials"), dict) else {}
        (dump_dir / "continuity_excerpt.json").write_text(
            json.dumps(
                {
                    "compile_time_keys": sorted(hashed),
                    "view_keys": sorted(materials),
                    "day_sheet": materials.get("day_sheet"),
                    "week_diary": materials.get("week_diary"),
                    "lived_moment": materials.get("lived_moment"),
                    "conversation_tail": (materials.get("conversation") or [])[-8:],
                    "since_he_last_spoke": materials.get("since_he_last_spoke"),
                    "我最近留下的": materials.get("我最近留下的"),
                    "private_impression_count": len(
                        materials.get("private_impressions") or []
                    ),
                    "hidden_impression_leak": leak,
                    "hidden_view_has_impressions": "private_impressions"
                    in (hidden.get("materials") or {}),
                    "hidden_lived_moment": (hidden.get("materials") or {}).get(
                        "lived_moment"
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return snapshot

    compiler_mod.compile_inner_life_snapshot = wrapped
    production_mod.compile_inner_life_snapshot = wrapped


async def _open_session(database: Path, output_dir: Path) -> drive.DriveSession:
    from companion_daemon.config import Settings
    from companion_daemon.llm import DeepSeekChatModel
    from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore
    from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host

    if drive._is_production_path(database):
        raise SystemExit(f"refusing to open production ledger for write: {database}")
    sidecar = output_dir / f"{database.stem}.sidecars"
    sidecar.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        database_path=database,
        world_v2_external_perception_mode="off",
        world_v2_external_perception_sidecar_path=sidecar / "perception.sqlite",
        attachment_cache_path=sidecar / "attachments",
        world_v2_text_endpoint_enabled=False,
    )
    usage_store = WorldV2UsageStore(path=str(database))
    inner = DeepSeekChatModel(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        thinking_enabled=False,
        max_completion_tokens=2_048,
        usage_observer=usage_store.record,
    )
    delivery = drive.CaptureDelivery()
    host = build_qq_c2c_host(
        settings=settings,
        recipient_id=drive._recipient_id(settings),
        bootstrap_at=datetime.now(UTC),
        delivery=delivery,
        model=inner,
        media_preview=None,
        media_transport=None,
        use_configured_recall_embedding=False,
    )
    session = drive.DriveSession(
        database=database,
        recipient_id=drive._recipient_id(settings),
        delivery=delivery,
        host=host,
        clock=datetime.now(UTC),
    )
    await session.logical_time()
    return session


async def _run() -> dict[str, Any]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    clone = OUTPUT / "clone.sqlite"
    dump_dir = OUTPUT / "snapshot"
    drive.clone_ledger(PRODUCTION_DB, clone)
    (OUTPUT / "production_appraisal_samples.json").write_text(
        json.dumps(_read_appraisal_meanings(PRODUCTION_DB), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    _patch_compiler(dump_dir)
    usage_from = drive.current_usage_id(clone)
    session = await _open_session(clone, OUTPUT)
    inbound: dict[str, Any] | None = None
    try:
        inbound = await session.inbound("刚忙完，你这边呢")
    finally:
        await session.close()
    cost = drive.cost_report(clone, since_id=usage_from)
    report = {
        "status": "ran",
        "clone": str(clone),
        "inbound_status": (inbound or {}).get("status"),
        "visible": (inbound or {}).get("visible"),
        "cost": cost,
        "dump_dir": str(dump_dir),
        "dump_files": sorted(path.name for path in dump_dir.glob("*"))
        if dump_dir.exists()
        else [],
    }
    (OUTPUT / "run.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    report = asyncio.run(_run())
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    cost = float((report.get("cost") or {}).get("cost_cny") or 0)
    if cost > 2:
        print(f"cost {cost} exceeds ¥2", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
