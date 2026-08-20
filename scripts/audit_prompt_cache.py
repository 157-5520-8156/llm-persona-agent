#!/usr/bin/env python3
"""Read-only prompt cache audit: token breakdown and prefix overlap.

Never writes ``data/``. Artifacts: ``output/cost-control/cache-audit.json``.

Usage::

    .venv/bin/python scripts/audit_prompt_cache.py
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

from companion_daemon.usage_metrics import estimate_model_cost, is_deepseek_peak
from context_truth.compile_seen import compile_seen_at_head

OUTPUT = REPO / "output" / "cost-control"
PRODUCTION_DB = REPO / "data" / "companion.epoch2.sqlite"
WORLD_ID = "world:companion-v2:qq-c2c:geoff"
SH = ZoneInfo("Asia/Shanghai")
CHARS_PER_TOKEN = 3.8  # conservative CJK-heavy estimate


def _chars_to_tokens(chars: int) -> int:
    return max(1, int(chars / CHARS_PER_TOKEN))


def _common_prefix(left: str, right: str) -> str:
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    return left[:index]


@dataclass
class UsageStats:
    calls: int = 0
    prompt: int = 0
    hit: int = 0
    miss: int = 0
    cost: float = 0.0


def load_usage_today() -> list[sqlite3.Row]:
    conn = sqlite3.connect(f"file:{PRODUCTION_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT purpose, status, prompt_tokens, completion_tokens,
               cache_hit_tokens, cache_miss_tokens, recorded_at
        FROM world_v2_model_usage
        WHERE (spend_account='production' OR spend_account='' OR spend_account IS NULL)
          AND status != 'budget_denied'
        """
    ).fetchall()
    conn.close()
    today = datetime(2026, 8, 20, tzinfo=SH).date()

    def sh_day(value: str) -> datetime.date:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(SH).date()

    return [row for row in rows if sh_day(row["recorded_at"]) == today]


def material_breakdown(materials: dict[str, object]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for key, value in materials.items():
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        chars = len(text)
        rows.append(
            {
                "segment": key,
                "chars": chars,
                "est_tokens": _chars_to_tokens(chars),
            }
        )
    rows.sort(key=lambda item: -int(item["chars"]))
    return rows


async def compile_head_materials() -> dict[str, object]:
    compiled = await compile_seen_at_head(
        database=PRODUCTION_DB,
        world_id=WORLD_ID,
        actor_ref="agent:companion",
        counterpart_actor_ref="user:geoff",
        timezone_name="Asia/Shanghai",
        seed_path=REPO / "configs" / "world_seed.yaml",
    )
    try:
        return {
            "materials": material_breakdown(compiled.materials),
            "total_material_chars": sum(
                len(json.dumps(v, ensure_ascii=False, separators=(",", ":")))
                for v in compiled.materials.values()
            ),
            "compile_ms": compiled.compile_ms,
        }
    finally:
        for store in compiled.stores_to_close:
            close = getattr(store, "close", None)
            if callable(close):
                close()


def projection_table(
    *,
    hit_rate: float,
    avg_prompt_per_call: float,
    calls_per_message: float,
    bg_daily: float,
) -> list[dict[str, float]]:
    """Chat input cost scales with cache hit rate (DeepSeek ¥0.05 vs ¥1.5 per M)."""

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


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    rows = load_usage_today()
    by_purpose: dict[str, UsageStats] = defaultdict(UsageStats)
    total = UsageStats()
    for row in rows:
        bucket = by_purpose[row["purpose"]]
        bucket.calls += 1
        bucket.prompt += int(row["prompt_tokens"] or 0)
        bucket.hit += int(row["cache_hit_tokens"] or 0)
        bucket.miss += int(row["cache_miss_tokens"] or 0)
        bucket.cost += estimate_model_cost(
            model="deepseek-v4-flash",
            prompt_tokens=int(row["prompt_tokens"] or 0),
            completion_tokens=int(row["completion_tokens"] or 0),
            cache_hit_tokens=int(row["cache_hit_tokens"] or 0),
            cache_miss_tokens=int(row["cache_miss_tokens"] or 0),
            at=row["recorded_at"],
        ).cny
        total.calls += 1
        total.prompt += int(row["prompt_tokens"] or 0)
        total.hit += int(row["cache_hit_tokens"] or 0)
        total.miss += int(row["cache_miss_tokens"] or 0)
        total.cost += estimate_model_cost(
            model="deepseek-v4-flash",
            prompt_tokens=int(row["prompt_tokens"] or 0),
            completion_tokens=int(row["completion_tokens"] or 0),
            cache_hit_tokens=int(row["cache_hit_tokens"] or 0),
            cache_miss_tokens=int(row["cache_miss_tokens"] or 0),
            at=row["recorded_at"],
        ).cny

    hit_rate = total.hit / (total.hit + total.miss) if (total.hit + total.miss) else 0.0
    avg_prompt = total.prompt / total.calls if total.calls else 0

    # Inbound + appraisal + fact ≈ 3 calls; use measured inbound avg as anchor.
    inbound_avg = by_purpose.get("inbound_turn", UsageStats())
    avg_inbound = inbound_avg.prompt / inbound_avg.calls if inbound_avg.calls else avg_prompt
    calls_per_message = 3.0

    head = asyncio.run(compile_head_materials())

    report = {
        "date": "2026-08-20",
        "usage": {
            "calls": total.calls,
            "prompt_tokens": total.prompt,
            "cache_hit_tokens": total.hit,
            "cache_miss_tokens": total.miss,
            "cache_hit_rate": round(hit_rate, 4),
            "avg_prompt_tokens": round(avg_prompt, 1),
            "official_cny": round(total.cost, 4),
            "by_purpose": {
                purpose: {
                    "calls": stats.calls,
                    "avg_prompt": round(stats.prompt / stats.calls, 1) if stats.calls else 0,
                    "hit_rate": round(
                        stats.hit / (stats.hit + stats.miss) if (stats.hit + stats.miss) else 0,
                        4,
                    ),
                    "cny": round(stats.cost, 4),
                }
                for purpose, stats in sorted(by_purpose.items(), key=lambda x: -x[1].prompt)
            },
        },
        "head_material_breakdown": head,
        "cache_diagnosis": {
            "root_causes": [
                "402 was not classified as provider outage (fixed: trips shared circuit breaker)",
                "Per-model circuit breakers did not share state (fixed: shared_deepseek_circuit_breaker)",
                "sort_keys=True alphabetized compact context, placing logical_time before stable slices",
                "expression_hard_boundaries aliases sat before inner_life_snapshot stable core",
                "inbound system schema (mode-specific) sat mid-prompt before static tail",
                "inbound/appraisal/fact use different system prefixes — no cross-lane cache sharing",
            ],
            "reorder_fixes_applied": [
                "present_prompt: snapshot before per-turn alias tables",
                "model_facing_context: stable-first root and slice ordering",
                "structured_role: stable-first user payload ordering",
                "inbound_wire: mode schema appended after static system contract",
            ],
        },
        "projections": {
            "assumptions": {
                "calls_per_message": calls_per_message,
                "avg_prompt_tokens_per_call": round(avg_inbound, 1),
                "background_daily_cny": 2.03,
            },
            "current_hit_rate": projection_table(
                hit_rate=hit_rate,
                avg_prompt_per_call=avg_inbound,
                calls_per_message=calls_per_message,
                bg_daily=2.03,
            ),
            "target_70_hit_rate": projection_table(
                hit_rate=0.70,
                avg_prompt_per_call=avg_inbound,
                calls_per_message=calls_per_message,
                bg_daily=2.03,
            ),
            "target_80_hit_rate": projection_table(
                hit_rate=0.80,
                avg_prompt_per_call=avg_inbound,
                calls_per_message=calls_per_message,
                bg_daily=2.03,
            ),
        },
    }
    (OUTPUT / "cache-audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"hit_rate={hit_rate:.1%} avg_prompt={avg_prompt:.0f} report={OUTPUT / 'cache-audit.json'}")


if __name__ == "__main__":
    main()
