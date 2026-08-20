#!/usr/bin/env python3
"""Measure appraisal compaction, cache stability, and cost projection.

Never writes ``data/``. Artifacts: ``output/appraisal-compaction/``.

Usage::

    .venv/bin/python scripts/audit_appraisal_compaction.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

from companion_daemon.world_v2.character_interior.appraisal_model_view import (
    appraisal_meanings,
)
from context_truth.compile_seen import compile_seen_at_head

OUTPUT = REPO / "output" / "appraisal-compaction"
PRODUCTION_DB = REPO / "data" / "companion.epoch2.sqlite"
WORLD_ID = "world:companion-v2:qq-c2c:geoff"


def _projection_table(
    *,
    hit_rate: float,
    avg_prompt_per_call: float,
    calls_per_message: float,
    bg_daily: float,
) -> list[dict[str, float]]:
    miss_rate = 1.0 - hit_rate
    input_cost_per_token_offpeak = (
        miss_rate * (1.5 / 1_000_000) + hit_rate * (0.05 / 1_000_000)
    )
    input_cost_per_token_peak = (
        miss_rate * (3.0 / 1_000_000) + hit_rate * (0.10 / 1_000_000)
    )
    chat_off = calls_per_message * avg_prompt_per_call * input_cost_per_token_offpeak
    chat_peak = calls_per_message * avg_prompt_per_call * input_cost_per_token_peak
    rows: list[dict[str, float]] = []
    for msgs in (20, 50, 100, 200):
        day_off = msgs * chat_off + bg_daily
        day_peak = msgs * chat_peak + bg_daily
        rows.append(
            {
                "messages_per_day": msgs,
                "daily_offpeak_cny": round(day_off, 2),
                "daily_peak_cny": round(day_peak, 2),
                "monthly_offpeak_cny": round(day_off * 30, 2),
                "monthly_peak_cny": round(day_peak * 30, 2),
            }
        )
    return rows


def _chars_to_tokens(chars: int) -> int:
    return max(1, int(chars / 3.8))


def _field_breakdown(appraisals: list[dict[str, object]]) -> list[dict[str, object]]:
    from collections import defaultdict

    field_chars: dict[str, int] = defaultdict(int)
    field_counts: dict[str, int] = defaultdict(int)
    for entry in appraisals:
        for key, value in entry.items():
            size = len(json.dumps({key: value}, ensure_ascii=False, separators=(",", ":")))
            field_chars[key] += size
            field_counts[key] += 1
    rows = [
        {
            "field": key,
            "chars": field_chars[key],
            "est_tokens": _chars_to_tokens(field_chars[key]),
            "count": field_counts[key],
        }
        for key in field_chars
    ]
    rows.sort(key=lambda item: -int(item["chars"]))
    return rows


def _material_breakdown(materials: dict[str, object]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for key, value in materials.items():
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        rows.append(
            {
                "segment": key,
                "chars": len(text),
                "est_tokens": _chars_to_tokens(len(text)),
            }
        )
    rows.sort(key=lambda item: -int(item["chars"]))
    return rows


async def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    compiled = await compile_seen_at_head(
        database=PRODUCTION_DB,
        world_id=WORLD_ID,
        actor_ref="agent:companion",
        counterpart_actor_ref="user:geoff",
        timezone_name="Asia/Shanghai",
        seed_path=REPO / "configs" / "world_seed.yaml",
    )
    try:
        canonical = json.loads(compiled.snapshot.materials_json)
        raw_appraisals = canonical.get("appraisals")
        if not isinstance(raw_appraisals, list):
            raise RuntimeError("canonical appraisals missing")
        compact = compiled.model_view["materials"]["appraisals"]
        raw_chars = len(json.dumps(raw_appraisals, ensure_ascii=False, separators=(",", ":")))
        compact_chars = len(json.dumps(compact, ensure_ascii=False, separators=(",", ":")))
        materials = dict(compiled.materials)
        total_chars = len(
            json.dumps(materials, ensure_ascii=False, separators=(",", ":"))
        )
        report = {
            "head_cursor": compiled.cursor.ledger_sequence,
            "appraisal_count": len(raw_appraisals),
            "canonical_appraisals": {
                "chars": raw_chars,
                "est_tokens": _chars_to_tokens(raw_chars),
                "field_breakdown": _field_breakdown(raw_appraisals),
            },
            "model_view_appraisals": {
                "chars": compact_chars,
                "est_tokens": _chars_to_tokens(compact_chars),
                "ratio_of_canonical": round(compact_chars / raw_chars, 4),
            },
            "materials_total": {
                "chars": total_chars,
                "est_tokens": _chars_to_tokens(total_chars),
                "segments": _material_breakdown(materials),
            },
            "semantic_preservation": {
                "canonical_meanings": appraisal_meanings(raw_appraisals),
                "model_view_meanings": appraisal_meanings(compact),
                "meanings_match": appraisal_meanings(raw_appraisals)
                == appraisal_meanings(compact),
            },
        }
        # Cost table: assume 70% cache hit after warm prefix.
        avg_prompt = report["materials_total"]["est_tokens"] + 5_500
        report["cost_projection_70pct_hit"] = _projection_table(
            hit_rate=0.70,
            avg_prompt_per_call=avg_prompt,
            calls_per_message=3.0,
            bg_daily=2.03,
        )
        (OUTPUT / "audit.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        for store in compiled.stores_to_close:
            close = getattr(store, "close", None)
            if callable(close):
                close()


if __name__ == "__main__":
    asyncio.run(main())
