#!/usr/bin/env python3
"""Clone-only: after a silent auto-delivery, she must know she sent the photo.

Copies ``output/long-chat/clone.sqlite`` (already has MediaDeliveryShared).
Five inbound turns that previously produced contradictory timelines.
``enable_media=False`` so this does not generate or POST images.

Never writes ``data/``. Never talks to 8787/NapCat.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import importlib.util
import json
import logging
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

_DRIVE_PATH = REPO / "scripts" / "drive_production_lanes.py"
_spec = importlib.util.spec_from_file_location("drive_production_lanes", _DRIVE_PATH)
drive = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["drive_production_lanes"] = drive
_spec.loader.exec_module(drive)

SOURCE = (REPO / "output" / "long-chat" / "clone.sqlite").resolve()
OUTPUT = (REPO / "output" / "media-memory").resolve()
CLONE = OUTPUT / "clone.sqlite"
SHANGHAI = ZoneInfo("Asia/Shanghai")
COST_CAP_CNY = 5.5
WORLD_ID = "world:companion-v2:qq-c2c:geoff"
_LOG = logging.getLogger("prove-media-memory")

PROBES = (
    ("P1", "你下午不是说晚上整理照片嘛"),
    ("P2", "现在方便给我看一张不"),
    ("P3", "那张书店的呢 你不是说还没打开相册"),
    ("P4", "你还没翻相册吗"),
    ("P5", "那明天再给我看呗"),
)

DENIALS = ("还没打开", "还没翻", "晚点再", "明天再给", "明天白天我再给")


def usage_cost(database: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(estimated_cost_cny), 0), COUNT(*) "
            "FROM world_v2_model_usage"
        ).fetchone()
    except sqlite3.OperationalError:
        return {"cost_cny": 0.0, "calls": 0}
    finally:
        conn.close()
    return {"cost_cny": float(row[0] or 0), "calls": int(row[1] or 0)}


def visible_texts(visible: object) -> list[str]:
    if not isinstance(visible, list):
        return []
    texts: list[str] = []
    for item in visible:
        if isinstance(item, dict) and isinstance(item.get("body"), str):
            texts.append(item["body"])
        elif isinstance(item, str):
            texts.append(item)
    return texts


async def main() -> dict[str, Any]:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if not SOURCE.is_file():
        raise SystemExit(f"missing long-chat clone: {SOURCE}")
    if CLONE.exists():
        CLONE.unlink()
    shutil.copy2(SOURCE, CLONE)
    before = usage_cost(CLONE)
    snapshots: list[dict[str, Any]] = []
    from companion_daemon.world_v2.character_interior import production as production_mod
    from companion_daemon.world_v2.character_interior import snapshot_compiler as compiler_mod

    original = compiler_mod.compile_inner_life_snapshot

    def wrapped(context, *, source_envelopes=None):
        snapshot = original(context, source_envelopes=source_envelopes)
        try:
            view = snapshot.model_view()
        except Exception as exc:
            view = {"error": f"{type(exc).__name__}: {exc}"}
        snapshots.append({"view": view})
        return snapshot

    compiler_mod.compile_inner_life_snapshot = wrapped
    production_mod.compile_inner_life_snapshot = wrapped

    session = await drive.open_session(
        database=CLONE, output_dir=OUTPUT, enable_media=False
    )
    results: list[dict[str, Any]] = []
    try:
        for label, text in PROBES:
            spent = usage_cost(CLONE)["cost_cny"] - before["cost_cny"]
            if spent >= COST_CAP_CNY:
                raise SystemExit(f"cost cap {COST_CAP_CNY} reached before {label}")
            before_snaps = len(snapshots)
            inbound = await session.inbound(text)
            view = snapshots[-1]["view"] if len(snapshots) > before_snaps else {}
            materials = view.get("materials") if isinstance(view, dict) else {}
            if not isinstance(materials, dict):
                materials = {}
            photos = materials.get("photos_i_shared")
            lines = visible_texts(inbound.get("visible"))
            blob = "\n".join(lines)
            denied = [token for token in DENIALS if token in blob]
            acknowledged = any(
                token in blob
                for token in ("发过", "发给", "刚发", "已经发", "自拍", "那张")
            )
            rec = {
                "label": label,
                "he": text,
                "she": lines,
                "photos_i_shared": photos,
                "line": (
                    photos[0].get("line")
                    if isinstance(photos, list) and photos and isinstance(photos[0], dict)
                    else None
                ),
                "denied": denied,
                "acknowledged": acknowledged,
                "inbound_status": inbound.get("status"),
            }
            results.append(rec)
            _LOG.info("%s she=%s denied=%s line=%s", label, lines, denied, rec["line"])
    finally:
        compiler_mod.compile_inner_life_snapshot = original
        production_mod.compile_inner_life_snapshot = original
        await session.close()
    after = usage_cost(CLONE)
    report = {
        "source_seq_hint": "long-chat clone after silent bookstore selfie",
        "probes": results,
        "all_have_photos_material": all(
            isinstance(item.get("photos_i_shared"), list)
            and item["photos_i_shared"]
            for item in results
        ),
        "any_denial": any(item["denied"] for item in results),
        "cost_cny": round(after["cost_cny"] - before["cost_cny"], 4),
        "calls": after["calls"] - before["calls"],
        "finished_at": datetime.now(SHANGHAI).isoformat(),
    }
    (OUTPUT / "proof.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report


if __name__ == "__main__":
    asyncio.run(main())
