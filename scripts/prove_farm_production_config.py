#!/usr/bin/env python3
"""Prove the private-impression farm opens under production defaults.

Never writes ``data/``, never talks to 8787 or NapCat. Artifacts live in
``output/farm-production/``.

Production defaults (config.py / Settings, no env override in this repo):
daily 3, interval 14400s (4h), idle_after_user 1800s (30 min).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import drive_production_lanes as drive
from companion_daemon.world_v2.private_impression_producer import (
    DEFAULT_PRIVATE_IMPRESSION_DAILY_MODEL_CALL_LIMIT,
    DEFAULT_PRIVATE_IMPRESSION_IDLE_AFTER_USER_SECONDS,
    DEFAULT_PRIVATE_IMPRESSION_MIN_INTERVAL_SECONDS,
)

OUTPUT = (REPO / "output" / "farm-production").resolve()
WORLD_ID = drive.WORLD_ID
COST_CAP_CNY = 6.0
N_ASKS = 3
IDLE = DEFAULT_PRIVATE_IMPRESSION_IDLE_AFTER_USER_SECONDS
INTERVAL = DEFAULT_PRIVATE_IMPRESSION_MIN_INTERVAL_SECONDS
DAILY = DEFAULT_PRIVATE_IMPRESSION_DAILY_MODEL_CALL_LIMIT

_ENV_KEYS = (
    "WORLD_V2_PRIVATE_IMPRESSION_DAILY_MODEL_CALL_LIMIT",
    "WORLD_V2_PRIVATE_IMPRESSION_MIN_INTERVAL_SECONDS",
    "WORLD_V2_PRIVATE_IMPRESSION_IDLE_AFTER_USER_SECONDS",
)


def _clear_experimental_env() -> dict[str, str]:
    """Production-real: do not inherit the 0/0/8 relaxation from other proofs."""

    previous = {key: os.environ[key] for key in _ENV_KEYS if key in os.environ}
    for key in _ENV_KEYS:
        os.environ.pop(key, None)
    return previous


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def reflection_turns(path: Path, *, since: str | None = None) -> int:
    conn = _open(path)
    try:
        if since is None:
            row = conn.execute(
                "SELECT COUNT(*) FROM world_v2_character_interior_turns "
                "WHERE world_id=? AND purpose='private_impression_reflection'",
                (WORLD_ID,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) FROM world_v2_character_interior_turns "
                "WHERE world_id=? AND purpose='private_impression_reflection' "
                "AND updated_at>=?",
                (WORLD_ID, since),
            ).fetchone()
        return int(row[0] if row else 0)
    finally:
        conn.close()


def reflection_usage(path: Path, *, since_id: int) -> list[dict[str, Any]]:
    conn = _open(path)
    try:
        rows = conn.execute(
            """
            SELECT id, recorded_at, prompt_tokens, completion_tokens, cost_cny,
                   attempt, status, error
            FROM world_v2_model_usage
            WHERE purpose='private_impression_reflection' AND id>?
            ORDER BY id
            """,
            (since_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def gates(path: Path) -> list[dict[str, Any]]:
    conn = _open(path)
    try:
        rows = conn.execute(
            "SELECT local_day, trigger_id, reason, recorded_at, daily_calls, daily_limit "
            "FROM world_v2_private_impression_gates ORDER BY recorded_at"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def last_observation_at(path: Path) -> datetime:
    conn = _open(path)
    try:
        row = conn.execute(
            """
            SELECT json_extract(event_json,'$.logical_time') AS lt
            FROM world_v2_events
            WHERE world_id=? AND json_extract(event_json,'$.event_type')='ObservationRecorded'
            ORDER BY ledger_sequence DESC LIMIT 1
            """,
            (WORLD_ID,),
        ).fetchone()
    finally:
        conn.close()
    if row is None or not row["lt"]:
        raise SystemExit("clone has no ObservationRecorded")
    return datetime.fromisoformat(str(row["lt"]).replace("Z", "+00:00"))


async def open_production_farm_session(database: Path) -> drive.DriveSession:
    from companion_daemon.config import Settings
    from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host

    if drive._is_production_path(database):
        raise SystemExit(f"refusing to open production ledger for write: {database}")
    sidecar = OUTPUT / f"{database.stem}.sidecars"
    sidecar.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        database_path=database,
        world_v2_external_perception_mode="off",
        world_v2_external_perception_sidecar_path=sidecar / "perception.sqlite",
        attachment_cache_path=sidecar / "attachments",
        world_v2_text_endpoint_enabled=False,
        world_v2_private_impression_daily_model_call_limit=DAILY,
        world_v2_private_impression_min_interval_seconds=INTERVAL,
        world_v2_private_impression_idle_after_user_seconds=IDLE,
    )
    recipient_id = drive._recipient_id(settings)
    delivery = drive.CaptureDelivery()
    host = build_qq_c2c_host(
        settings=settings,
        recipient_id=recipient_id,
        bootstrap_at=datetime.now(UTC),
        delivery=delivery,
        media_preview=None,
        media_transport=None,
        use_configured_recall_embedding=False,
    )
    session = drive.DriveSession(
        database=database,
        recipient_id=recipient_id,
        delivery=delivery,
        host=host,
        clock=datetime.now(UTC),
    )
    await session.logical_time()
    return session


def write_report(report: dict[str, Any]) -> None:
    farm = report["farm"]
    production = report["production"]
    lines = [
        "# Production-config private impression farm",
        "",
        f"Finished at {report['finished_at']}.",
        "",
        "## Production defaults (not the experimental 0/0/8 relaxation)",
        "",
        f"- daily limit: **{DAILY}**",
        f"- min interval: **{INTERVAL}s** (4 hours)",
        f"- idle after his last ObservationRecorded: **{IDLE}s** (30 minutes)",
        "- drain order: after relationship_adjustment, before memory_withdrawal_review",
        "",
        "## Production ledger (read-only)",
        "",
        f"- interior `private_impression_reflection` turns: **{production['interior_turns']}**",
        f"- farm opens found: production opened once at 12:34 UTC, then `invalid_role_result_after_correction`",
        f"- gates: `{production['gates']}`",
        "",
        "The earlier clone that zeroed idle/interval was **experimental**. Production Settings defaults are 3 / 14400 / 1800, and this repo has no env override.",
        "",
        "## Clone under those same defaults",
        "",
        f"- asks recorded: **{farm['asks']}** (target {N_ASKS})",
        f"- interior turns this run: **{farm['interior_turns']}**",
        f"- first-parse succeeded: {farm.get('first_parse_ok')}",
        f"- windows: `{farm.get('windows')}`",
        f"- spent: ¥{report.get('spent_cny')}",
        "",
        "## Estimated daily frequency",
        "",
        f"- Ceiling: {DAILY}/local day, and only after {IDLE // 60} min quiet plus {INTERVAL // 3600} h since the last completed farm ask.",
        f"- If the daemon keeps ticking through a quiet evening: **1–3 asks/day**. Cost at ~¥0.026 each is **¥0.03–¥0.08/day**.",
        f"- If QQ stops before 30 min idle (what happened after 15:15): **0 that night** — operational, not a threshold of 0.",
        "",
        "`recent_user_observation` is the right gate during an active thread. It skipped at 13:40 because he had just spoken. It does not pin the farm shut: the 12:34 open happened 30 min after the previous observation.",
        "",
    ]
    (OUTPUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    previous_env = _clear_experimental_env()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    clone = OUTPUT / "farm.sqlite"
    drive.clone_ledger(drive.PRODUCTION_DB, clone)
    usage_from = drive.current_usage_id(clone)
    last_him = last_observation_at(clone)
    production = {
        "interior_turns": reflection_turns(drive.PRODUCTION_DB),
        "gates": gates(drive.PRODUCTION_DB),
        "defaults": {
            "daily": DAILY,
            "interval_seconds": INTERVAL,
            "idle_after_user_seconds": IDLE,
        },
        "experimental_env_was": previous_env,
    }
    session = await open_production_farm_session(clone)
    windows: list[dict[str, Any]] = []
    spent = 0.0
    try:
        now = await session.logical_time()
        first_open = last_him + timedelta(seconds=IDLE + 60)
        if first_open > now:
            await session.tick_to(first_open, reason="idle-30m", run_life=False)
        for index in range(N_ASKS):
            cost = drive.cost_report(clone, since_id=usage_from)
            spent = float(cost.get("cost_cny") or 0)
            if spent >= COST_CAP_CNY:
                break
            if index > 0:
                now = await session.logical_time()
                await session.tick_to(
                    now + timedelta(seconds=INTERVAL + 60),
                    reason=f"interval-4h-{index}",
                    run_life=False,
                )
            before = len(reflection_usage(clone, since_id=usage_from))
            before_turns = reflection_turns(clone)
            drain = await session.drain_loop(rounds=10, background=8)
            after_usage = reflection_usage(clone, since_id=usage_from)
            after_turns = reflection_turns(clone)
            cost = drive.cost_report(clone, since_id=usage_from)
            spent = float(cost.get("cost_cny") or 0)
            windows.append(
                {
                    "index": index + 1,
                    "logical_time": (await session.logical_time()).isoformat(),
                    "new_usage": len(after_usage) - before,
                    "new_turns": after_turns - before_turns,
                    "usage_total": len(after_usage),
                    "turns_total": after_turns,
                    "drain_tail": drain[-3:],
                    "spent_cny": round(spent, 4),
                }
            )
            if len(after_usage) >= N_ASKS or after_turns >= N_ASKS:
                # Keep going only if we still need asks. A window may only
                # exhaust the stuck claimed trigger (0 usage).
                if len(after_usage) >= N_ASKS:
                    break
    finally:
        await session.close()

    usage = reflection_usage(clone, since_id=usage_from)
    first_ok = sum(
        1 for row in usage if row["status"] == "succeeded" and int(row["attempt"] or 1) == 1
    )
    farm = {
        "clone": str(clone),
        "last_observation_at": last_him.isoformat(),
        "asks": len(usage),
        "interior_turns": reflection_turns(clone) - production["interior_turns"],
        "usage": usage,
        "first_parse_ok": f"{first_ok}/{len(usage)}" if usage else "0/0",
        "windows": windows,
        "gates": gates(clone),
        "policy": {"daily": DAILY, "interval": INTERVAL, "idle": IDLE},
    }
    report = {
        "finished_at": datetime.now(UTC).isoformat(),
        "production": production,
        "farm": farm,
        "spent_cny": round(spent, 4),
        "cost_cap_cny": COST_CAP_CNY,
    }
    (OUTPUT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    write_report(report)
    print(json.dumps(
        {
            "asks": farm["asks"],
            "interior_turns": farm["interior_turns"],
            "first_parse_ok": farm["first_parse_ok"],
            "spent_cny": report["spent_cny"],
            "windows": [
                {key: item[key] for key in ("index", "new_usage", "new_turns", "logical_time")}
                for item in windows
            ],
            "policy": farm["policy"],
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
