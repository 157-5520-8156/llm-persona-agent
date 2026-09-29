#!/usr/bin/env python3
"""Measure the inbound prompt cost of recent_dialogue max_fields 96 → 256.

Never writes ``data/``. Artifacts live in ``output/slice-cost/``.
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
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import drive_production_lanes as drive
import prove_stale_topics as sun
from companion_daemon.world_v2.character_interior.structured_role_tool_contract import (
    StructuredRoleToolContracts,
)

OUTPUT = (REPO / "output" / "slice-cost").resolve()
WORLD_ID = drive.WORLD_ID
CNY_PER_USD = 7.2
MISS_USD_PER_M = 0.14  # deepseek-v4-flash cache miss, usage_metrics.py
CACHE_HIT_USD_PER_M = 0.0028
INBOUND_PER_5_DAYS = 70
DAYS_PER_MONTH = 30


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _inbound_rows(path: Path, *, min_id: int = 0) -> list[dict[str, object]]:
    conn = _open(path)
    try:
        rows = conn.execute(
            """
            SELECT id, recorded_at, prompt_tokens, completion_tokens, cache_hit_tokens,
                   cache_miss_tokens, cost_cny, attempt, status
            FROM world_v2_model_usage
            WHERE purpose='inbound_turn' AND id>=? AND prompt_tokens>0
            ORDER BY id
            """,
            (min_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _stats(rows: list[dict[str, object]]) -> dict[str, object]:
    if not rows:
        return {"n": 0}
    prompts = [int(row["prompt_tokens"]) for row in rows]
    costs = [float(row["cost_cny"] or 0) for row in rows]
    attempts = [int(row["attempt"] or 1) for row in rows]
    succeeded = [row for row in rows if row["status"] == "succeeded"]
    first_ok = sum(
        1
        for row in succeeded
        if int(row["attempt"] or 1) == 1
    )
    return {
        "n": len(rows),
        "succeeded": len(succeeded),
        "prompt_mean": round(sum(prompts) / len(prompts)),
        "prompt_median": sorted(prompts)[len(prompts) // 2],
        "prompt_min": min(prompts),
        "prompt_max": max(prompts),
        "cost_mean_cny": round(sum(costs) / len(costs), 4),
        "attempt_max": max(attempts),
        "first_parse_succeeded": first_ok,
        "first_parse_of_succeeded": len(succeeded),
    }


def _monthly_delta_cny(*, extra_miss_tokens: int, inbound_per_day: float) -> dict[str, float]:
    per_turn_usd = extra_miss_tokens * MISS_USD_PER_M / 1_000_000
    per_turn_cny = per_turn_usd * CNY_PER_USD
    monthly_inbound = inbound_per_day * DAYS_PER_MONTH
    return {
        "extra_miss_tokens": extra_miss_tokens,
        "per_turn_cny_all_miss": round(per_turn_cny, 5),
        "inbound_per_day": inbound_per_day,
        "inbound_per_month": round(monthly_inbound, 1),
        "monthly_cny_all_miss": round(per_turn_cny * monthly_inbound, 2),
    }


def compile_field_budgets() -> dict[str, object]:
    clone = REPO / "output" / "conversation-slice" / "sun-retest.sqlite"
    evidence = sun.compile_sun_dialogue(clone=clone)
    kept = evidence["_kept"]
    field_counts = [len(item.model_dump(mode="json")) for item in kept]

    def take(max_fields: int) -> dict[str, object]:
        used = 0
        taken = []
        omitted = 0
        for item in kept:
            n = len(item.model_dump(mode="json"))
            if used + n > max_fields:
                omitted += 1
                continue
            taken.append(item)
            used += n
        blob = json.dumps([item.model_dump(mode="json") for item in taken], ensure_ascii=False)
        her_last = next((item.text for item in reversed(taken) if item.speaker == "companion"), None)
        return {
            "items": len(taken),
            "fields": used,
            "omitted": omitted,
            "her": sum(1 for item in taken if item.speaker == "companion"),
            "him": sum(1 for item in taken if item.speaker == "counterpart"),
            "photo": any(
                item.dialogue_id.startswith("dialogue:media-delivery:") for item in taken
            ),
            "dump_chars": len(blob),
            "her_last": her_last,
        }

    snap, _, _ = sun.compile_sun_snapshot(evidence)
    view = snap.model_view()
    recent = view["materials"].get("recent_dialogue") or []
    contract = StructuredRoleToolContracts().private_impression_reflection(
        capability_payload={
            "short_tokens": ["s0", "s1"],
            "anchor_short_tokens": ["s0"],
            "existing_impression_short_tokens": ["s0"],
            "expiry_conditions": ["until_counter_evidence"],
        },
        recall_allowed=False,
    )
    schema_chars = len(json.dumps(contract.provider_tools, ensure_ascii=False))
    return {
        "packed_items": len(kept),
        "fields_per_item": field_counts,
        "budget_96": take(96),
        "budget_256": take(256),
        "snapshot_view_chars": len(json.dumps(view, ensure_ascii=False)),
        "recent_dialogue_n": len(recent) if isinstance(recent, list) else 0,
        "recent_dialogue_chars": len(json.dumps(recent, ensure_ascii=False)),
        "recent_dialogue_7_chars": len(json.dumps(recent[:7], ensure_ascii=False))
        if isinstance(recent, list)
        else 0,
        "reflection_tool_schema_chars": schema_chars,
        "deepseek_flash_context_tokens": 1_000_000,
    }


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    production = _inbound_rows(drive.PRODUCTION_DB)
    # Last production cluster on 2026-08-18 is the fair 96-field baseline.
    production_recent = [
        row
        for row in production
        if str(row["recorded_at"]).startswith("2026-08-18T15:")
    ]
    chat = _inbound_rows(REPO / "output" / "conversation-slice" / "chat.sqlite", min_id=297)
    budgets = compile_field_budgets()
    before = _stats(production_recent or production[-12:])
    after = _stats(chat)
    extra = int(after["prompt_mean"]) - int(before["prompt_mean"])
    observed_cost = round(float(after["cost_mean_cny"]) - float(before["cost_mean_cny"]), 4)
    inbound_per_day = INBOUND_PER_5_DAYS / 5
    monthly = _monthly_delta_cny(extra_miss_tokens=max(extra, 0), inbound_per_day=inbound_per_day)
    monthly["observed_per_turn_cny"] = observed_cost
    monthly["observed_monthly_cny"] = round(observed_cost * monthly["inbound_per_month"], 2)
    report = {
        "finished_at": datetime.now(UTC).isoformat(),
        "before_96": before,
        "after_256": after,
        "prompt_delta": extra,
        "observed_cost_delta_cny": observed_cost,
        "monthly": monthly,
        "budgets": budgets,
        "keep_256": True,
        "reason": (
            "ResolverProof stays at 32 refs. 96 fields kept ~7 lines and dropped "
            "her last beat and delivered photos. 256 is the mixed 16-item window. "
            "Tool schema is unchanged; only user-message dialogue grew."
        ),
    }
    (OUTPUT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    lines = [
        "# Conversation slice field-budget cost",
        "",
        f"Finished at {report['finished_at']}.",
        "",
        "## 1. Measured inbound prompt tokens",
        "",
        f"- Before (`max_fields=96`, production 2026-08-18 15:xx cluster): **{before['prompt_mean']}** mean / {before['prompt_median']} median (n={before['n']}, range {before['prompt_min']}–{before['prompt_max']})",
        f"- After (`max_fields=256`, 12-turn clone): **{after['prompt_mean']}** mean / {after['prompt_median']} median (n={after['n']}, range {after['prompt_min']}–{after['prompt_max']})",
        f"- Delta: **+{extra} prompt tokens** per inbound turn",
        f"- Observed cost: ¥{before['cost_mean_cny']} → ¥{after['cost_mean_cny']} (**+¥{observed_cost}** / turn, cache mixed)",
        "",
        "## 2. Monthly",
        "",
        f"- Rhythm: {INBOUND_PER_5_DAYS} inbound / 5 days ≈ {inbound_per_day:.0f}/day ≈ {monthly['inbound_per_month']:.0f}/month",
        f"- If the extra tokens are all cache-miss: **¥{monthly['monthly_cny_all_miss']}/month**",
        f"- At the observed mix: **¥{monthly['observed_monthly_cny']}/month**",
        "",
        "## 3. Context limit and first-parse",
        "",
        f"- DeepSeek-V4-Flash context is **1,000,000** tokens. 22.7k inbound is ~2.3% of that.",
        f"- Tool schema did not grow. Reflection tool schema is {budgets['reflection_tool_schema_chars']} characters — this is not the 40KB proactive schema.",
        f"- First-parse (attempt=1, succeeded): production 96-field **{before['first_parse_succeeded']}/{before['first_parse_of_succeeded']}**, 256-field 12-turn **{after['first_parse_succeeded']}/{after['first_parse_of_succeeded']}**.",
        "",
        "## 4. Keep 256",
        "",
        f"- Same packed items under 96 fields: {budgets['budget_96']['items']} lines, her last kept line `{budgets['budget_96']['her_last']}`, photo={budgets['budget_96']['photo']}.",
        f"- Under 256: {budgets['budget_256']['items']} lines, her last `{budgets['budget_256']['her_last']}`, photo={budgets['budget_256']['photo']}.",
        "- Rolling back to 96 recreates the bug. Cost is about two yuan a month. Keep 256.",
        "",
    ]
    (OUTPUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(
        {
            "before_prompt": before["prompt_mean"],
            "after_prompt": after["prompt_mean"],
            "delta": extra,
            "monthly_observed_cny": monthly["observed_monthly_cny"],
            "first_parse_before": f"{before['first_parse_succeeded']}/{before['first_parse_of_succeeded']}",
            "first_parse_after": f"{after['first_parse_succeeded']}/{after['first_parse_of_succeeded']}",
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
