"""World V2 model usage recording and monthly budget gating.

The legacy daemon budget gate never covered World V2 calls, so monthly cost
was invisible and unbounded.  This module records every model call through a
usage observer and exposes monthly/daily CNY aggregates plus a monthly gate
the turn entry can consult before spending another provider call.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone

from ..usage_metrics import estimate_model_cost_usd, estimate_routed_model_reserve_cny

_USD_TO_CNY = 7.2

GENERIC_MODEL_PURPOSES = frozenset(
    {
        "",
        "unclassified",
        "world_v2_character_interior",
    }
)

_REPAIR_PURPOSE_MARKERS = (
    "reselection",
    "corrective",
    "recovery",
    "retry",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS world_v2_model_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    world_id TEXT NOT NULL DEFAULT '',
    turn_id TEXT NOT NULL DEFAULT '',
    purpose TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL,
    status TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
    cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    cost_cny REAL NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0
)
"""

_RESERVATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS world_v2_model_reservations (
    reservation_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    purpose TEXT NOT NULL,
    actor TEXT NOT NULL,
    provider TEXT NOT NULL,
    estimated_cny REAL NOT NULL,
    world_id TEXT NOT NULL DEFAULT '',
    turn_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL
)
"""

_USAGE_COLUMN_MIGRATIONS = (
    ("actor", "TEXT NOT NULL DEFAULT ''"),
    ("reservation_id", "TEXT NOT NULL DEFAULT ''"),
    ("estimated_cny", "REAL NOT NULL DEFAULT 0"),
    ("attempt", "INTEGER NOT NULL DEFAULT 1"),
)


class ModelUsageAdmissionError(ValueError):
    """Raised when a World V2 provider call has no attributable reservation."""


def _is_invalid_cost(*, purpose: str, attempt: int) -> bool:
    if attempt > 1:
        return True
    lowered = purpose.casefold()
    return any(marker in lowered for marker in _REPAIR_PURPOSE_MARKERS)


class WorldV2UsageStore:
    """SQLite-backed usage recording plus cost aggregates."""

    def __init__(self, *, path: str, usd_to_cny: float = _USD_TO_CNY) -> None:
        if not path:
            raise ValueError("world v2 usage store requires a database path")
        self._path = path
        self._usd_to_cny = usd_to_cny
        self._lock = threading.RLock()
        connection = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        try:
            connection.execute(_SCHEMA)
            connection.execute(_RESERVATION_SCHEMA)
            self._migrate_usage_columns(connection)
        finally:
            connection.close()

    def _migrate_usage_columns(self, connection: sqlite3.Connection) -> None:
        existing = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(world_v2_model_usage)")
        }
        for name, declaration in _USAGE_COLUMN_MIGRATIONS:
            if name not in existing:
                connection.execute(
                    f"ALTER TABLE world_v2_model_usage ADD COLUMN {name} {declaration}"
                )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, isolation_level=None, check_same_thread=False)

    def admit_provider_call(
        self,
        *,
        purpose: str,
        actor: str,
        provider: str,
        model: str,
        prompt_characters: int,
        estimated_cny: float | None = None,
        world_id: str = "",
        turn_id: str = "",
        reservation_id: str = "",
    ) -> str:
        resolved_purpose = str(purpose or "")
        if resolved_purpose in GENERIC_MODEL_PURPOSES:
            raise ModelUsageAdmissionError(
                f"world v2 model call purpose {resolved_purpose!r} is not attributable"
            )
        resolved_actor = str(actor or "").strip() or "agent:companion"
        resolved_provider = str(provider or "").strip()
        if not resolved_provider:
            raise ModelUsageAdmissionError("world v2 model call requires a provider")
        if estimated_cny is None:
            reserved_cny = estimate_routed_model_reserve_cny(
                model=model or "__unpriced__",
                prompt_characters=prompt_characters,
                cny_per_usd=self._usd_to_cny,
            )
        else:
            reserved_cny = float(estimated_cny)
        if reserved_cny < 0:
            raise ModelUsageAdmissionError("world v2 model call estimated CNY is invalid")
        token = reservation_id.strip() or f"reservation:{uuid.uuid4().hex}"
        created_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            connection = self._connect()
            try:
                connection.execute(
                    """
                    INSERT INTO world_v2_model_reservations (
                        reservation_id, created_at, purpose, actor, provider,
                        estimated_cny, world_id, turn_id, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                    """,
                    (
                        token,
                        created_at,
                        resolved_purpose,
                        resolved_actor,
                        resolved_provider,
                        reserved_cny,
                        world_id,
                        turn_id,
                    ),
                )
            finally:
                connection.close()
        return token

    def record(self, usage: object) -> None:
        """usage_observer-compatible callback; never raises on telemetry gaps."""
        try:
            self._record_usage(usage)
        except Exception:
            # Observability must never turn a model response into a failure.
            pass

    def _record_usage(self, usage: object) -> None:
        model = str(getattr(usage, "model", "") or "")
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        cache_hit_tokens = int(getattr(usage, "cache_hit_tokens", 0) or 0)
        cache_miss_tokens = int(getattr(usage, "cache_miss_tokens", 0) or 0)
        usd, _version = estimate_model_cost_usd(
            model=model or "__unpriced__",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cache_hit_tokens=cache_hit_tokens,
            cache_miss_tokens=cache_miss_tokens,
        )
        cost_cny = round(usd * self._usd_to_cny, 4)
        recorded_at = datetime.now(timezone.utc).isoformat()
        reservation_id = str(
            getattr(usage, "budget_reservation_id", "")
            or getattr(usage, "reservation_id", "")
            or ""
        )
        actor = str(getattr(usage, "actor", "") or "")
        attempt = int(getattr(usage, "attempt", 1) or 1)
        estimated_cny = 0.0
        with self._lock:
            connection = self._connect()
            try:
                if reservation_id:
                    row = connection.execute(
                        "SELECT actor, estimated_cny FROM world_v2_model_reservations "
                        "WHERE reservation_id = ?",
                        (reservation_id,),
                    ).fetchone()
                    if row is not None:
                        if not actor:
                            actor = str(row[0] or "")
                        estimated_cny = float(row[1] or 0.0)
                    connection.execute(
                        "UPDATE world_v2_model_reservations SET status = 'settled' "
                        "WHERE reservation_id = ?",
                        (reservation_id,),
                    )
                connection.execute(
                    """
                    INSERT INTO world_v2_model_usage (
                        recorded_at, world_id, turn_id, purpose, model, status,
                        provider, prompt_tokens, completion_tokens, cache_hit_tokens,
                        cache_miss_tokens, total_tokens, error, cost_cny, latency_ms,
                        actor, reservation_id, estimated_cny, attempt
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        recorded_at,
                        str(getattr(usage, "world_id", "") or ""),
                        str(getattr(usage, "turn_id", "") or ""),
                        str(getattr(usage, "purpose", "") or ""),
                        model,
                        str(getattr(usage, "status", "") or ""),
                        str(getattr(usage, "provider", "") or ""),
                        prompt_tokens,
                        completion_tokens,
                        cache_hit_tokens,
                        cache_miss_tokens,
                        int(getattr(usage, "total_tokens", 0) or 0),
                        str(getattr(usage, "error", "") or "")[:512],
                        cost_cny,
                        int(getattr(usage, "latency_ms", 0) or 0),
                        actor,
                        reservation_id,
                        estimated_cny,
                        attempt,
                    ),
                )
            finally:
                connection.close()

    def cost_since(self, *, since: datetime) -> float:
        """Sum cost_cny for records recorded after ``since`` (UTC)."""
        with self._lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    "SELECT COALESCE(SUM(cost_cny), 0) FROM world_v2_model_usage "
                    "WHERE recorded_at >= ?",
                    (since.isoformat(),),
                ).fetchone()
                return float(row[0] if row is not None else 0.0)
            finally:
                connection.close()

    def monthly_cost_cny(self) -> float:
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return self.cost_since(since=month_start)

    def daily_cost_cny(self) -> float:
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return self.cost_since(since=day_start)

    def budget_state(
        self,
        *,
        monthly_budget_cny: float | None,
        daily_budget_cny: float | None,
    ) -> dict[str, object]:
        monthly = self.monthly_cost_cny()
        daily = self.daily_cost_cny()
        attribution = self._daily_attribution()
        warning_reasons = list(attribution["warning_reasons"])
        if monthly_budget_cny is not None and monthly >= monthly_budget_cny:
            warning_reasons.append("monthly_exhausted")
        if daily_budget_cny is not None and daily >= daily_budget_cny:
            warning_reasons.append("daily_exhausted")
        return {
            "monthly_cost_cny": round(monthly, 2),
            "monthly_budget_cny": monthly_budget_cny,
            "monthly_exhausted": (
                monthly_budget_cny is not None and monthly >= monthly_budget_cny
            ),
            "daily_cost_cny": round(daily, 2),
            "daily_budget_cny": daily_budget_cny,
            "daily_exhausted": (
                daily_budget_cny is not None and daily >= daily_budget_cny
            ),
            "purpose_counts": attribution["purpose_counts"],
            "calls_per_user_message": attribution["calls_per_user_message"],
            "calls_per_user_message_alert": attribution["calls_per_user_message_alert"],
            "cache_hit_rate": attribution["cache_hit_rate"],
            "cache_hit_rate_alert": attribution["cache_hit_rate_alert"],
            "invalid_cost_rate": attribution["invalid_cost_rate"],
            "invalid_cost_rate_alert": attribution["invalid_cost_rate_alert"],
            "warning": bool(warning_reasons),
            "warning_reasons": warning_reasons,
        }

    def _daily_attribution(self) -> dict[str, object]:
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_key = day_start.strftime("%Y-%m-%d")
        with self._lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    """
                    SELECT purpose, attempt, cost_cny, prompt_tokens, cache_hit_tokens
                    FROM world_v2_model_usage
                    WHERE recorded_at >= ?
                    """,
                    (day_start.isoformat(),),
                ).fetchall()
                observation_count = self._today_observation_count(connection, day_key)
            finally:
                connection.close()
        purpose_counts: dict[str, int] = {}
        prompt_tokens = 0
        cache_hit_tokens = 0
        total_cost = 0.0
        invalid_cost = 0.0
        for purpose, attempt, cost_cny, prompts, hits in rows:
            label = str(purpose or "")
            purpose_counts[label] = purpose_counts.get(label, 0) + 1
            prompt_tokens += int(prompts or 0)
            cache_hit_tokens += int(hits or 0)
            amount = float(cost_cny or 0.0)
            total_cost += amount
            if _is_invalid_cost(purpose=label, attempt=int(attempt or 1)):
                invalid_cost += amount
        call_count = len(rows)
        calls_per_user_message: float | None
        calls_alert = False
        if observation_count > 0:
            calls_per_user_message = call_count / observation_count
            calls_alert = calls_per_user_message > 3
        else:
            calls_per_user_message = None
            calls_alert = call_count > 3
        cache_hit_rate = (
            cache_hit_tokens / prompt_tokens if prompt_tokens > 0 else None
        )
        cache_alert = cache_hit_rate is not None and cache_hit_rate < 0.5
        invalid_cost_rate = invalid_cost / total_cost if total_cost > 0 else None
        invalid_alert = invalid_cost_rate is not None and invalid_cost_rate > 0.1
        warning_reasons: list[str] = []
        if calls_alert:
            warning_reasons.append("calls_per_user_message")
        if cache_alert:
            warning_reasons.append("cache_hit_rate")
        if invalid_alert:
            warning_reasons.append("invalid_cost_rate")
        return {
            "purpose_counts": purpose_counts,
            "calls_per_user_message": calls_per_user_message,
            "calls_per_user_message_alert": calls_alert,
            "cache_hit_rate": cache_hit_rate,
            "cache_hit_rate_alert": cache_alert,
            "invalid_cost_rate": invalid_cost_rate,
            "invalid_cost_rate_alert": invalid_alert,
            "warning_reasons": warning_reasons,
        }

    def _today_observation_count(
        self, connection: sqlite3.Connection, day_key: str
    ) -> int:
        try:
            row = connection.execute(
                """
                SELECT COUNT(*) FROM world_v2_events
                WHERE json_extract(event_json, '$.event_type') = 'ObservationRecorded'
                  AND substr(json_extract(event_json, '$.created_at'), 1, 10) = ?
                """,
                (day_key,),
            ).fetchone()
        except sqlite3.DatabaseError:
            return 0
        return int(row[0] if row is not None else 0)


__all__ = [
    "GENERIC_MODEL_PURPOSES",
    "ModelUsageAdmissionError",
    "WorldV2UsageStore",
]
