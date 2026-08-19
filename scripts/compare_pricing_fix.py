#!/usr/bin/env python3
"""Read-only old-vs-new DeepSeek price comparison. Writes output/pricing-fix/.

Never writes data/. Never calls a model. Never sends QQ.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

from audit_deepseek_spend import (  # noqa: E402
    PRODUCTION_DB,
    collect_unique_usage,
)
from companion_daemon.usage_metrics import (  # noqa: E402
    estimate_legacy_flat_cny,
    estimate_model_cost,
)

OUTPUT = REPO / "output" / "pricing-fix"
SHANGHAI = ZoneInfo("Asia/Shanghai")
HIKE = datetime(2026, 8, 17, 0, 0, tzinfo=SHANGHAI)


def _yuan(value: float) -> str:
    return f"¥{value:.2f}"


def main() -> int:
    unique = collect_unique_usage(None)
    rows = [row for row in unique.values() if row.is_deepseek()]
    buckets: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {
            "calls": 0,
            "ledger_cny": 0.0,
            "legacy_flat_cny": 0.0,
            "new_official_cny": 0.0,
        }
    )
    days: dict[str, dict[str, float]] = defaultdict(
        lambda: {"ledger": 0.0, "legacy": 0.0, "new": 0.0}
    )
    windows = {"pre": 0.0, "peak": 0.0, "off-peak": 0.0}
    for row in rows:
        new = estimate_model_cost(
            model=row.model,
            prompt_tokens=row.prompt,
            completion_tokens=row.completion,
            cache_hit_tokens=row.hit,
            cache_miss_tokens=row.miss,
            at=row.recorded_at,
        )
        legacy = estimate_legacy_flat_cny(
            model=row.model,
            prompt_tokens=row.prompt,
            completion_tokens=row.completion,
            cache_hit_tokens=row.hit,
            cache_miss_tokens=row.miss,
        )
        bucket = buckets[row.bucket]
        bucket["calls"] += 1
        bucket["ledger_cny"] += row.ledger_cny
        bucket["legacy_flat_cny"] += legacy
        bucket["new_official_cny"] += new.cny
        day = row.local().date().isoformat()
        days[day]["ledger"] += row.ledger_cny
        days[day]["legacy"] += legacy
        days[day]["new"] += new.cny
        if row.local() < HIKE:
            windows["pre"] += new.cny
        elif new.window == "peak":
            windows["peak"] += new.cny
        else:
            windows["off-peak"] += new.cny

    settings_defaults = {
        "monthly_budget_cny": 80.0,
        "daily_budget_cny": 3.0,
        "soft_daily_budget_cny": 2.0,
        "source": "src/companion_daemon/config.py Field defaults",
    }
    summary = {
        "generated_at": datetime.now(tz=SHANGHAI).isoformat(),
        "sources": {
            "zh": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing",
            "en": "https://api-docs.deepseek.com/quick_start/pricing/",
            "updates": "https://api-docs.deepseek.com/zh-cn/updates",
        },
        "new_price_versions": [
            "deepseek-2026-07-13",
            "deepseek-2026-08-17-offpeak",
            "deepseek-2026-08-17-peak",
        ],
        "budget_defaults": settings_defaults,
        "totals": {
            name: {
                "calls": int(values["calls"]),
                "ledger_cny": round(float(values["ledger_cny"]), 4),
                "legacy_flat_cny": round(float(values["legacy_flat_cny"]), 4),
                "new_official_cny": round(float(values["new_official_cny"]), 4),
            }
            for name, values in buckets.items()
        },
        "grand": {
            "calls": len(rows),
            "ledger_cny": round(sum(float(b["ledger_cny"]) for b in buckets.values()), 4),
            "legacy_flat_cny": round(
                sum(float(b["legacy_flat_cny"]) for b in buckets.values()), 4
            ),
            "new_official_cny": round(
                sum(float(b["new_official_cny"]) for b in buckets.values()), 4
            ),
        },
        "windows_new_cny": {key: round(value, 4) for key, value in windows.items()},
        "days": {
            day: {key: round(value, 4) for key, value in payload.items()}
            for day, payload in sorted(days.items())
        },
        "production_db": str(PRODUCTION_DB),
        "production_usage_events_empty": _usage_events_empty(PRODUCTION_DB),
    }
    prod_days = {}
    for row in rows:
        if row.bucket != "production_epoch2":
            continue
        day = row.local().date().isoformat()
        prod_days.setdefault(day, 0.0)
        prod_days[day] += estimate_model_cost(
            model=row.model,
            prompt_tokens=row.prompt,
            completion_tokens=row.completion,
            cache_hit_tokens=row.hit,
            cache_miss_tokens=row.miss,
            at=row.recorded_at,
        ).cny
    summary["production_epoch2_days_new_cny"] = {
        day: round(value, 4) for day, value in sorted(prod_days.items())
    }
    summary["production_days_over_daily_cap_3"] = [
        day for day, value in sorted(prod_days.items()) if value > 3.0
    ]
    summary["production_days_over_soft_cap_2"] = [
        day for day, value in sorted(prod_days.items()) if value > 2.0
    ]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "comparison.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"legacy={summary['grand']['legacy_flat_cny']:.2f} "
        f"new={summary['grand']['new_official_cny']:.2f} "
        f"ledger={summary['grand']['ledger_cny']:.2f} "
        f"out={OUTPUT / 'comparison.json'}"
    )
    return 0


def _usage_events_empty(path: Path) -> bool:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "usage_events" not in tables:
            return True
        count = conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0]
        return int(count or 0) == 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
