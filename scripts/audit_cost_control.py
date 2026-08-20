#!/usr/bin/env python3
"""Read-only cost-control audit: today's call attribution, projections, budget gaps.

Never writes ``data/``. Artifacts go to ``output/cost-control/``.

Usage::

    .venv/bin/python scripts/audit_cost_control.py
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
import sqlite3
import sys
from typing import Any
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from companion_daemon.usage_metrics import estimate_model_cost, is_deepseek_peak

PRODUCTION_DB = (REPO / "data" / "companion.epoch2.sqlite").resolve()
OUTPUT = (REPO / "output" / "cost-control").resolve()
SHANGHAI = ZoneInfo("Asia/Shanghai")
TODAY = date(2026, 8, 20)

LANES: dict[str, tuple[str, ...]] = {
    "入站回话": (
        "inbound_turn",
        "paired_cognition_initial",
        "paired_cognition_stream",
        "expression_stream_tail",
        "validation_reselection",
        "qq_attachment_perception",
    ),
    "主动开口": ("proactive_contact",),
    "生活生态": (
        "life_development_draft",
        "life_development_choice",
        "activity_lifecycle_choice",
        "outcome_selection",
        "experience_memory_retention",
        "fact_memory_retention",
        "npc_ecology",
        "open_world",
        "life_development_source_closure_review",
        "life_development_novel_origin_review",
    ),
    "私人印象农场": ("private_impression_reflection",),
    "评价 appraisal": ("world_stimulus_appraisal",),
    "媒体选片/生图": ("media_selection", "image_generation"),
    "对话事实抽取": ("interaction_fact_draft",),
}

PURPOSE_TO_LANE: dict[str, str] = {}
for lane, purposes in LANES.items():
    for purpose in purposes:
        PURPOSE_TO_LANE[purpose] = lane


def parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(SHANGHAI)


def official_cny(row: sqlite3.Row) -> float:
    return estimate_model_cost(
        model=row["model"],
        prompt_tokens=row["prompt_tokens"],
        completion_tokens=row["completion_tokens"],
        cache_hit_tokens=row["cache_hit_tokens"],
        cache_miss_tokens=row["cache_miss_tokens"],
        at=row["recorded_at"],
    ).cny


@dataclass
class UsageSlice:
    rows: int = 0
    api_attempts: int = 0
    budget_denied: int = 0
    succeeded: int = 0
    failed: int = 0
    official_cny: float = 0.0
    charged_cny: float = 0.0

    def add(self, row: sqlite3.Row) -> None:
        self.rows += 1
        cost = official_cny(row)
        if row["status"] == "budget_denied":
            self.budget_denied += 1
            return
        self.api_attempts += 1
        if row["status"] == "succeeded":
            self.succeeded += 1
            self.official_cny += cost
            self.charged_cny += cost
        else:
            self.failed += 1
            if row["prompt_tokens"] or row["completion_tokens"]:
                self.official_cny += cost
                self.charged_cny += cost


def load_production_usage() -> list[sqlite3.Row]:
    conn = sqlite3.connect(f"file:{PRODUCTION_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT recorded_at, purpose, model, status, prompt_tokens, completion_tokens,
               cache_hit_tokens, cache_miss_tokens, cost_cny, error, spend_account
        FROM world_v2_model_usage
        WHERE spend_account = 'production' OR spend_account = '' OR spend_account IS NULL
        ORDER BY recorded_at
        """
    ).fetchall()
    conn.close()
    return rows


def projection_table(epoch_rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    """Per-message cost model from epoch2 succeeded inbound + facts."""

    inbound_succ = [
        r
        for r in epoch_rows
        if r["purpose"] == "inbound_turn" and r["status"] == "succeeded"
    ]
    fact_succ = [
        r
        for r in epoch_rows
        if r["purpose"] == "interaction_fact_draft" and r["status"] == "succeeded"
    ]
    inbound_cost = sum(official_cny(r) for r in inbound_succ)
    fact_cost = sum(official_cny(r) for r in fact_succ)
    inbound_n = len(inbound_succ)
    fact_n = len(fact_succ)

    # Background fixed cost: succeeded non-inbound/fact per calendar day average
    daily_bg: dict[date, float] = defaultdict(float)
    for r in epoch_rows:
        if r["status"] != "succeeded":
            continue
        if r["purpose"] in {"inbound_turn", "interaction_fact_draft"}:
            continue
        daily_bg[parse_dt(r["recorded_at"]).date()] += official_cny(r)
    bg_days = len(daily_bg) or 1
    bg_daily = sum(daily_bg.values()) / bg_days

    per_inbound = inbound_cost / inbound_n if inbound_n else 0.0
    per_fact = fact_cost / fact_n if inbound_n else 0.0
    # facts roughly track inbound messages
    per_msg_offpeak = per_inbound + per_fact
    per_msg_peak = per_msg_offpeak * 2.0  # output doubles; input ~2x on peak

    rows_out: list[dict[str, Any]] = []
    for msgs in (20, 50, 100, 200):
        chat_off = msgs * per_msg_offpeak
        chat_peak = msgs * per_msg_peak
        day_off = chat_off + bg_daily
        day_peak = chat_peak + bg_daily
        rows_out.append(
            {
                "messages_per_day": msgs,
                "chat_offpeak_cny": round(chat_off, 2),
                "chat_peak_cny": round(chat_peak, 2),
                "background_fixed_daily_cny": round(bg_daily, 2),
                "total_daily_offpeak_cny": round(day_off, 2),
                "total_daily_peak_cny": round(day_peak, 2),
                "monthly_offpeak_cny": round(day_off * 30, 2),
                "monthly_peak_cny": round(day_peak * 30, 2),
            }
        )
    return rows_out


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    all_rows = load_production_usage()
    today_rows = [r for r in all_rows if parse_dt(r["recorded_at"]).date() == TODAY]

    by_lane: dict[str, UsageSlice] = defaultdict(UsageSlice)
    by_purpose: dict[str, UsageSlice] = defaultdict(UsageSlice)
    by_hour: Counter[int] = Counter()
    errors: Counter[str] = Counter()

    for row in today_rows:
        lane = PURPOSE_TO_LANE.get(row["purpose"], f"其他:{row['purpose']}")
        by_lane[lane].add(row)
        by_purpose[row["purpose"]].add(row)
        by_hour[parse_dt(row["recorded_at"]).hour] += 1
        if row["status"] != "succeeded" and row["error"]:
            errors[(row["purpose"], row["error"][:100])] += 1

    summary = {
        "date": str(TODAY),
        "ledger_rows": len(today_rows),
        "api_attempts": sum(s.api_attempts for s in by_lane.values()),
        "budget_denied_rows": sum(s.budget_denied for s in by_lane.values()),
        "charged_official_cny": round(
            sum(s.charged_cny for s in by_lane.values()), 4
        ),
        "by_lane": {
            lane: {
                "rows": s.rows,
                "api_attempts": s.api_attempts,
                "budget_denied": s.budget_denied,
                "succeeded": s.succeeded,
                "failed": s.failed,
                "charged_cny": round(s.charged_cny, 4),
            }
            for lane, s in sorted(by_lane.items(), key=lambda x: -x[1].rows)
        },
        "by_purpose": {
            p: {
                "rows": s.rows,
                "api_attempts": s.api_attempts,
                "budget_denied": s.budget_denied,
                "succeeded": s.succeeded,
                "failed": s.failed,
                "charged_cny": round(s.charged_cny, 4),
            }
            for p, s in sorted(by_purpose.items(), key=lambda x: -x[1].rows)
        },
        "by_hour_shanghai": dict(sorted(by_hour.items())),
        "top_errors": [
            {"purpose": p, "error": e, "count": c}
            for (p, e), c in errors.most_common(15)
        ],
        "projection": projection_table(all_rows),
        "epoch2_inbound_per_message_cny": round(
            sum(official_cny(r) for r in all_rows if r["purpose"] == "inbound_turn" and r["status"] == "succeeded")
            / max(
                1,
                sum(
                    1
                    for r in all_rows
                    if r["purpose"] == "inbound_turn" and r["status"] == "succeeded"
                ),
            ),
            4,
        ),
    }

    (OUTPUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    report_lines = [
        "# 成本管控审计",
        "",
        f"生产账本只读分析，日期 **{TODAY}**。",
        "",
        "## 今天 747 行账本 ≠ 747 次 API",
        "",
        f"- 账本行数：**{summary['ledger_rows']}**",
        f"- 真实 API 尝试：**{summary['api_attempts']}**（`budget_denied` 未调模型：**{summary['budget_denied_rows']}**）",
        f"- 控制台应扣（官方峰谷价）：**¥{summary['charged_official_cny']:.2f}**",
        "",
        "### 按车道",
        "",
        "| 车道 | 账本行 | API尝试 | budget_denied | 成功 | 失败 | 扣费¥ |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for lane, data in summary["by_lane"].items():
        report_lines.append(
            f"| {lane} | {data['rows']} | {data['api_attempts']} | "
            f"{data['budget_denied']} | {data['succeeded']} | {data['failed']} | "
            f"¥{data['charged_cny']:.2f} |"
        )

    report_lines.extend(
        [
            "",
            "## 不该发生的部分",
            "",
            "1. **media_selection 空转**：余额耗尽后约每 60s 重试 402（125 次失败、token=0 不扣费但污染账本）。",
            "2. **interaction_fact_draft 配对重试**：余额为 0 时仍尝试 26 次。",
            "3. **498 行 budget_denied**：软日门生效后的审计噪音（不扣费）。",
            "4. **FactCorrected 仅 1 条**：不是今天暴增主因。",
            "",
            "## 消息量预测（epoch2 实测）",
            "",
            "| 条/天 | 聊天闲时 | 聊天高峰 | 后台固定/日 | 合计闲时/日 | 合计高峰/日 | 月闲时 | 月高峰 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in summary["projection"]:
        report_lines.append(
            f"| {row['messages_per_day']} | ¥{row['chat_offpeak_cny']:.2f} | "
            f"¥{row['chat_peak_cny']:.2f} | ¥{row['background_fixed_daily_cny']:.2f} | "
            f"¥{row['total_daily_offpeak_cny']:.2f} | ¥{row['total_daily_peak_cny']:.2f} | "
            f"¥{row['monthly_offpeak_cny']:.2f} | ¥{row['monthly_peak_cny']:.2f} |"
        )

    report_lines.append(
        f"\n入站单次成功 inbound_turn 均值：**¥{summary['epoch2_inbound_per_message_cny']:.4f}**"
    )
    (OUTPUT / "REPORT.md").write_text("\n".join(report_lines), encoding="utf-8")
    print(f"report={OUTPUT / 'REPORT.md'}")


if __name__ == "__main__":
    main()
