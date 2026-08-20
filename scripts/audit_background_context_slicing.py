#!/usr/bin/env python3
"""Measure background lane context slicing on the production head snapshot.

Artifacts: ``output/background-slicing/``. Never writes ``data/``.

Usage::

    .venv/bin/python scripts/audit_background_context_slicing.py
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

from companion_daemon.world_v2.background_context_profile import (
    BACKGROUND_CONTEXT_PROFILE_IDS,
    _PROFILES,
    background_context_profile_for_purpose,
    profile_audit_record,
    slice_background_capsule_context,
    slice_background_inner_life_snapshot,
)
from companion_daemon.world_v2.character_interior.structured_role import _BUILTIN_CONTRACTS
from companion_daemon.world_v2.context_capsule import ContextCapsuleBudgetPolicy
from companion_daemon.world_v2.ledger_context_resolver import context_capsule_compiler_from_ledger
from companion_daemon.world_v2.context_resolver import query_from_projection
from companion_daemon.world_v2.present_prompt import PRESENT_CAPSULE_HARD_MAX_CHARACTERS
from companion_daemon.world_v2.situation_compiler import SituationCompiler
from companion_daemon.world_v2.local_chronology import LocalChronology
from context_truth.compile_seen import compile_seen_at_head

OUTPUT = REPO / "output" / "background-slicing"
PRODUCTION_DB = REPO / "data" / "companion.epoch2.sqlite"
WORLD_ID = "world:companion-v2:qq-c2c:geoff"

BASELINE_PROMPT_BY_PURPOSE = {
    "world_stimulus_appraisal": 78334,
    "life_development_draft": 77734,
    "life_development_choice": 68246,
    "private_impression_reflection": 66177,
    "experience_memory_retention": 65601,
    "fact_memory_retention": 44341,
    "activity_lifecycle_choice": 24947,
    "outcome_selection": 16533,
    "life_development_novel_origin_review": 29538,
    "inbound_turn": 42058,
    "interaction_fact_draft": 747,
}


def _chars_to_tokens(chars: int) -> int:
    return max(1, int(chars / 3.8))


def _json_size(value: object) -> tuple[int, int]:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return len(text), _chars_to_tokens(len(text))


def _material_inventory(materials: dict[str, object]) -> list[str]:
    return sorted(materials.keys())


def _estimate_structured_role_user_payload(
    *,
    purpose: str,
    snapshot_view: dict[str, object],
) -> tuple[int, int]:
    profile = background_context_profile_for_purpose(purpose)
    sliced = slice_background_inner_life_snapshot(snapshot_view, profile)
    payload = {
        "background_context_profile": profile_audit_record(profile),
        "inner_life_snapshot": sliced,
        "inner_turn": {"purpose": purpose},
        "eight_facets": list(sliced.get("faculties", {})),
    }
    return _json_size(payload)


def _estimate_life_draft_payload(*, context: dict[str, object]) -> tuple[int, int]:
    profile = background_context_profile_for_purpose("life_development_draft")
    sliced = slice_background_capsule_context(context, profile)
    payload = {
        "logical_time": context.get("logical_time"),
        "pinned_world_context": sliced,
    }
    return _json_size(payload)


def _cache_prefix_stability(full: str, sliced: str) -> dict[str, object]:
    prefix = 0
    for left, right in zip(full, sliced, strict=False):
        if left != right:
            break
        prefix += 1
    return {
        "shared_prefix_chars": prefix,
        "shared_prefix_tokens_est": _chars_to_tokens(prefix),
        "full_chars": len(full),
        "sliced_chars": len(sliced),
    }


def _cost_projection_table(
    *,
    background_daily_cny_before: float,
    background_reduction_ratio: float,
    inbound_prompt_after: int,
    inbound_hit_rate: float,
) -> list[dict[str, object]]:
    bg_after = background_daily_cny_before * (1.0 - background_reduction_ratio)
    miss = 1.0 - inbound_hit_rate
    offpeak = miss * (1.5 / 1_000_000) + inbound_hit_rate * (0.05 / 1_000_000)
    rows: list[dict[str, object]] = []
    for msgs in (20, 50, 70, 100):
        chat = msgs * 3 * inbound_prompt_after * offpeak
        day = chat + bg_after
        rows.append(
            {
                "messages_per_day": msgs,
                "daily_offpeak_cny": round(day, 2),
                "monthly_offpeak_cny": round(day * 30, 2),
                "background_daily_cny": round(bg_after, 2),
            }
        )
    return rows


def _lane_requirements_table() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for profile in _PROFILES:
        for purpose in sorted(profile.purposes):
            rows.append(
                {
                    "purpose": purpose,
                    "profile_id": profile.profile_id,
                    "keeps_snapshot_materials": list(profile.snapshot_material_keys),
                    "keeps_capsule_slices": list(profile.capsule_slices),
                }
            )
    return rows


def _read_inbound_usage() -> dict[str, object]:
    for path in (
        REPO / "output" / "debug-spend" / "model_usage.sqlite",
        REPO / "data" / "model_usage.sqlite",
    ):
        if not path.exists():
            continue
        import sqlite3

        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "model_usage" not in tables:
                continue
            row = conn.execute(
                """
                SELECT AVG(prompt_tokens) AS avg_prompt,
                       AVG(CASE WHEN prompt_tokens>0
                           THEN 1.0*cache_hit_tokens/prompt_tokens END) AS hit_rate,
                       COUNT(*) AS n
                FROM model_usage
                WHERE purpose='inbound_turn' AND status='success'
                  AND recorded_at >= datetime('now', '-1 day')
                """
            ).fetchone()
            if row and row["n"]:
                return {
                    "source": str(path),
                    "avg_prompt_tokens": round(float(row["avg_prompt"] or 0)),
                    "cache_hit_rate": round(float(row["hit_rate"] or 0), 4),
                    "sample_count": int(row["n"]),
                }
        finally:
            conn.close()
    return {"status": "no_recent_inbound_usage_rows"}


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
        full_snapshot = compiled.model_view
        full_materials = full_snapshot.get("materials")
        assert isinstance(full_materials, dict)
        now_materials = _material_inventory(full_materials)

        prompt_rows: list[dict[str, object]] = []
        total_before = 0
        total_after = 0
        for contract in _BUILTIN_CONTRACTS:
            purpose = contract.purpose
            before = BASELINE_PROMPT_BY_PURPOSE.get(purpose)
            _, full_tokens = _json_size(full_snapshot)
            after_chars, after_tokens = _estimate_structured_role_user_payload(
                purpose=purpose,
                snapshot_view=full_snapshot,
            )
            sliced = slice_background_inner_life_snapshot(
                full_snapshot,
                background_context_profile_for_purpose(purpose),
            )
            sliced_materials = sliced.get("materials")
            assert isinstance(sliced_materials, dict)
            row: dict[str, object] = {
                "purpose": purpose,
                "profile_id": background_context_profile_for_purpose(purpose).profile_id,
                "production_avg_prompt_before": before,
                "head_full_snapshot_tokens_est": full_tokens,
                "head_sliced_payload_tokens_est": after_tokens,
                "head_sliced_payload_chars": after_chars,
                "reduction_vs_full_snapshot": round(
                    1.0 - (after_tokens / max(full_tokens, 1)),
                    4,
                ),
                "materials_before": now_materials,
                "materials_after": _material_inventory(sliced_materials),
                "dropped_materials": sorted(
                    set(now_materials) - set(sliced_materials.keys())
                ),
            }
            if before:
                row["reduction_vs_production_avg"] = round(1.0 - after_tokens / before, 4)
                total_before += before
                total_after += after_tokens
            prompt_rows.append(row)

        capsules = context_capsule_compiler_from_ledger(
            ledger=compiled.ledger,
            situation_compiler=SituationCompiler(local_chronology=LocalChronology("Asia/Shanghai")),
            policy=ContextCapsuleBudgetPolicy(
                hard_max_characters=PRESENT_CAPSULE_HARD_MAX_CHARACTERS
            ),
        )
        context = json.loads(
            capsules.compile_for_deliberation(
                query_from_projection(
                    compiled.projection,
                    actor_ref="agent:companion",
                    trigger_ref=compiled.trigger_ref,
                )
            ).capsule.model_content_json
        )
        assert isinstance(context, dict)
        _, full_context_tokens = _json_size(context)
        draft_chars, draft_tokens = _estimate_life_draft_payload(context=context)
        before = BASELINE_PROMPT_BY_PURPOSE["life_development_draft"]
        prompt_rows.append(
            {
                "purpose": "life_development_draft",
                "profile_id": "life_ecology_core",
                "production_avg_prompt_before": before,
                "head_full_context_tokens_est": full_context_tokens,
                "head_sliced_payload_tokens_est": draft_tokens,
                "head_sliced_payload_chars": draft_chars,
                "reduction_vs_full_context": round(
                    1.0 - draft_tokens / max(full_context_tokens, 1),
                    4,
                ),
                "reduction_vs_production_avg": round(1.0 - draft_tokens / before, 4),
            }
        )
        total_before += before
        total_after += draft_tokens

        full_text = json.dumps(full_snapshot, ensure_ascii=False, separators=(",", ":"))
        life_profile = background_context_profile_for_purpose("activity_lifecycle_choice")
        sliced_text = json.dumps(
            slice_background_inner_life_snapshot(full_snapshot, life_profile),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        cache = {
            "same_lane_prefix_stability": _cache_prefix_stability(full_text, sliced_text),
            "background_hit_rate_before_today": "1-15% (cost-next-level)",
            "expected_after_slicing": "stable materials share long prefix per lane",
        }

        inbound_usage = _read_inbound_usage()
        snapshot_chars = len(
            json.dumps(full_snapshot, ensure_ascii=False, separators=(",", ":"))
        )
        avg_bg_reduction = round(1.0 - total_after / total_before, 4)
        report = {
            "head_ledger_sequence": compiled.cursor.ledger_sequence,
            "profile_ids": list(BACKGROUND_CONTEXT_PROFILE_IDS),
            "lane_requirements": _lane_requirements_table(),
            "prompt_size_comparison": prompt_rows,
            "aggregate_background_reduction_vs_production_avg": avg_bg_reduction,
            "cache_stability": cache,
            "inbound_turn_usage": inbound_usage,
            "inbound_snapshot_materials_tokens_est": _chars_to_tokens(snapshot_chars),
            "cost_projection": _cost_projection_table(
                background_daily_cny_before=4.55,
                background_reduction_ratio=avg_bg_reduction,
                inbound_prompt_after=_chars_to_tokens(snapshot_chars),
                inbound_hit_rate=0.70,
            ),
        }
        (OUTPUT / "audit.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _write_report_md(report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        for store in compiled.stores_to_close:
            close = getattr(store, "close", None)
            if callable(close):
                close()


def _write_report_md(report: dict[str, object]) -> None:
    lines = [
        "# Background lane context slicing (2026-08-20)",
        "",
        f"Production head seq `{report['head_ledger_sequence']}`. "
        "Canonical snapshot unchanged; only the provider view is filtered per lane.",
        "",
        "## Prompt size: production avg vs sliced estimate",
        "",
        "| purpose | prod avg | sliced est | reduction |",
        "| --- | ---: | ---: | ---: |",
    ]
    for row in report["prompt_size_comparison"]:
        before = row.get("production_avg_prompt_before")
        after = row.get("head_sliced_payload_tokens_est")
        reduction = row.get("reduction_vs_production_avg") or row.get(
            "reduction_vs_full_snapshot"
        )
        if before and after:
            lines.append(
                f"| `{row['purpose']}` | {int(before):,} | {int(after):,} | "
                f"{100 * float(reduction):.1f}% |"
            )
    lines.extend(
        [
            "",
            f"Aggregate background reduction vs production averages: "
            f"**{100 * float(report['aggregate_background_reduction_vs_production_avg']):.1f}%**",
            "",
            "## inbound_turn materials (compaction reference)",
            "",
            f"Head snapshot model_view ≈ **{report['inbound_snapshot_materials_tokens_est']:,}** tokens.",
            "",
            "## Deployed inbound_turn usage",
            "",
            json.dumps(report["inbound_turn_usage"], ensure_ascii=False, indent=2),
        ]
    )
    (OUTPUT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
