"""World V2 model usage recording and CNY budget gating.

The legacy daemon ``BudgetGate`` never covered World V2 text calls.  Visible
inbound turns still must not go silent because of money; background workers
(private impressions, proactive contact, life ecology) share the same
monthly/daily/soft-daily envelope and are skipped before the provider call.
Image CNY still lives in ``usage_events`` on this same sqlite path.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..db import ensure_usage_events_schema
from ..spend_account import classify_spend_account
from ..usage_metrics import (
    CNY_PER_USD,
    estimate_model_cost,
    estimate_routed_model_reserve_cny,
    price_usage_row,
)

_LOG = logging.getLogger(__name__)

_USD_TO_CNY = CNY_PER_USD

GENERIC_MODEL_PURPOSES = frozenset(
    {
        "",
        "unclassified",
        "world_v2_character_interior",
    }
)

# Answering him is not a cost-control lever.  These purposes ride the visible
# inbound turn; a CNY skip would look like she chose not to reply.
VISIBLE_INBOUND_PURPOSES = frozenset(
    {
        "inbound_turn",
        "paired_cognition_initial",
        "paired_cognition_stream",
        "expression_stream_tail",
        "paired_recall_followup",
        "recall_followup",
        "recall_control_transfer",
        "validation_reselection",
        "qq_attachment_perception",
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
    ("reasoning_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("pricing_version", "TEXT NOT NULL DEFAULT ''"),
    ("spend_account", "TEXT NOT NULL DEFAULT ''"),
)


class ModelUsageAdmissionError(ValueError):
    """Raised when a World V2 provider call has no attributable reservation."""


class BackgroundSpendCapDenied(ModelUsageAdmissionError):
    """Background model work hit the CNY envelope; do not call the provider."""


def _is_invalid_cost(*, purpose: str, attempt: int) -> bool:
    if attempt > 1:
        return True
    lowered = purpose.casefold()
    return any(marker in lowered for marker in _REPAIR_PURPOSE_MARKERS)


def usage_store_for_settings(settings: object) -> WorldV2UsageStore:
    """Bind World V2 usage to the same sqlite Settings uses for the ledger."""

    return WorldV2UsageStore(
        path=str(getattr(settings, "database_path")),
        monthly_budget_cny=_optional_float(getattr(settings, "monthly_budget_cny", None)),
        daily_budget_cny=_optional_float(getattr(settings, "daily_budget_cny", None)),
        soft_daily_budget_cny=_optional_float(
            getattr(settings, "soft_daily_budget_cny", None)
        ),
    )


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


class WorldV2UsageStore:
    """SQLite-backed usage recording plus cost aggregates."""

    def __init__(
        self,
        *,
        path: str,
        usd_to_cny: float = _USD_TO_CNY,
        monthly_budget_cny: float | None = None,
        daily_budget_cny: float | None = None,
        soft_daily_budget_cny: float | None = None,
    ) -> None:
        if not path:
            raise ValueError("world v2 usage store requires a database path")
        self._path = path
        self._usd_to_cny = usd_to_cny
        self._monthly_budget_cny = monthly_budget_cny
        self._daily_budget_cny = daily_budget_cny
        self._soft_daily_budget_cny = soft_daily_budget_cny
        self._lock = threading.RLock()
        self._spend_account = classify_spend_account(database_path=path)
        ensure_usage_events_schema(Path(path))
        connection = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        try:
            connection.execute(_SCHEMA)
            connection.execute(_RESERVATION_SCHEMA)
            self._migrate_usage_columns(connection)
        finally:
            connection.close()

    def _migrate_usage_columns(self, connection: sqlite3.Connection) -> None:
        existing = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(world_v2_model_usage)")
        }
        for name, declaration in _USAGE_COLUMN_MIGRATIONS:
            if name not in existing:
                connection.execute(
                    f"ALTER TABLE world_v2_model_usage ADD COLUMN {name} {declaration}"
                )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, isolation_level=None, check_same_thread=False)

    def _gates_background_cny(self, purpose: str) -> bool:
        if purpose in VISIBLE_INBOUND_PURPOSES:
            return False
        return any(
            limit is not None
            for limit in (
                self._monthly_budget_cny,
                self._daily_budget_cny,
                self._soft_daily_budget_cny,
            )
        )

    def _utc_window_start(self, *, month: bool) -> datetime:
        now = datetime.now(timezone.utc)
        if month:
            return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    def _priced_model_spend_cny(
        self, connection: sqlite3.Connection, *, since: datetime
    ) -> float:
        """Sum DeepSeek/OpenAI spend at the price table that applied at call time.

        Stored ``cost_cny`` on rows written before 2026-08-17 peak/off-peak is
        the old USD×7.2 half-price. Repricing from tokens does not rewrite the
        ledger and is what the budget gate must see.
        """

        rows = connection.execute(
            """
            SELECT recorded_at, model, prompt_tokens, completion_tokens,
                   cache_hit_tokens, cache_miss_tokens, cost_cny,
                   COALESCE(reasoning_tokens, 0)
            FROM world_v2_model_usage
            WHERE recorded_at >= ?
              AND purpose != 'image_generation'
              AND status != 'budget_denied'
            """,
            (since.isoformat(),),
        ).fetchall()
        total = 0.0
        for row in rows:
            total += price_usage_row(
                {
                    "recorded_at": row[0],
                    "model": row[1],
                    "prompt_tokens": row[2],
                    "completion_tokens": row[3],
                    "cache_hit_tokens": row[4],
                    "cache_miss_tokens": row[5],
                    "cost_cny": row[6],
                    "reasoning_tokens": row[7],
                },
                cny_per_usd=self._usd_to_cny,
            ).cny
        return total

    def _combined_spend_cny(self, *, since: datetime) -> float:
        iso = since.isoformat()
        with self._lock:
            connection = self._connect()
            try:
                model_total = self._priced_model_spend_cny(connection, since=since)
                pending_row = connection.execute(
                    """
                    SELECT COALESCE(SUM(estimated_cny), 0)
                    FROM world_v2_model_reservations
                    WHERE created_at >= ? AND status = 'pending'
                    """,
                    (iso,),
                ).fetchone()
                try:
                    image_row = connection.execute(
                        """
                        SELECT COALESCE(SUM(estimated_cny), 0) FROM usage_events
                        WHERE created_at >= ?
                        """,
                        (iso,),
                    ).fetchone()
                    images = float(image_row[0] if image_row is not None else 0.0)
                except sqlite3.DatabaseError:
                    images = 0.0
                pending = float(pending_row[0] if pending_row is not None else 0.0)
                return model_total + pending + images
            finally:
                connection.close()

    def _background_spend_cap_reason(self, estimated_cny: float) -> str | None:
        amount = max(0.0, float(estimated_cny))
        monthly = self._combined_spend_cny(since=self._utc_window_start(month=True))
        daily = self._combined_spend_cny(since=self._utc_window_start(month=False))
        if (
            self._monthly_budget_cny is not None
            and monthly + amount > self._monthly_budget_cny
        ):
            return "monthly_budget_exceeded"
        if self._daily_budget_cny is not None and daily + amount > self._daily_budget_cny:
            return "daily_budget_exceeded"
        if (
            self._soft_daily_budget_cny is not None
            and daily + amount > self._soft_daily_budget_cny
        ):
            return "soft_daily_budget_exceeded"
        return None

    def _record_budget_denial(
        self,
        *,
        purpose: str,
        actor: str,
        provider: str,
        model: str,
        reason: str,
        estimated_cny: float,
        world_id: str,
        turn_id: str,
    ) -> None:
        recorded_at = datetime.now(timezone.utc).isoformat()
        _LOG.warning(
            "world v2 background model call skipped reason=%s purpose=%s",
            reason,
            purpose,
        )
        with self._lock:
            connection = self._connect()
            try:
                connection.execute(
                    """
                    INSERT INTO world_v2_model_usage (
                        recorded_at, world_id, turn_id, purpose, model, status,
                        provider, prompt_tokens, completion_tokens, cache_hit_tokens,
                        cache_miss_tokens, total_tokens, error, cost_cny, latency_ms,
                        actor, reservation_id, estimated_cny, attempt
                    ) VALUES (?, ?, ?, ?, ?, 'budget_denied', ?, 0, 0, 0, 0, 0, ?, 0, 0, ?, '', ?, 1)
                    """,
                    (
                        recorded_at,
                        world_id,
                        turn_id,
                        purpose,
                        model,
                        provider,
                        reason[:2_400],
                        actor,
                        float(estimated_cny),
                    ),
                )
            finally:
                connection.close()

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
        if self._gates_background_cny(resolved_purpose):
            reason = self._background_spend_cap_reason(reserved_cny)
            if reason is not None:
                self._record_budget_denial(
                    purpose=resolved_purpose,
                    actor=resolved_actor,
                    provider=resolved_provider,
                    model=model or "",
                    reason=reason,
                    estimated_cny=reserved_cny,
                    world_id=world_id,
                    turn_id=turn_id,
                )
                raise BackgroundSpendCapDenied(
                    f"world v2 background model call skipped: {reason}"
                )
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
            _LOG.exception(
                "world v2 model usage record failed purpose=%s provider=%s",
                getattr(usage, "purpose", ""),
                getattr(usage, "provider", ""),
            )

    def _record_usage(self, usage: object) -> None:
        model = str(getattr(usage, "model", "") or "")
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        reasoning_tokens = int(getattr(usage, "reasoning_tokens", 0) or 0)
        cache_hit_tokens = int(getattr(usage, "cache_hit_tokens", 0) or 0)
        cache_miss_tokens = int(getattr(usage, "cache_miss_tokens", 0) or 0)
        recorded_at = datetime.now(timezone.utc).isoformat()
        priced = estimate_model_cost(
            model=model or "__unpriced__",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cache_hit_tokens=cache_hit_tokens,
            cache_miss_tokens=cache_miss_tokens,
            reasoning_tokens=reasoning_tokens,
            at=recorded_at,
            cny_per_usd=self._usd_to_cny,
        )
        cost_cny = round(priced.cny, 4)
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
                        actor, reservation_id, estimated_cny, attempt,
                        reasoning_tokens, pricing_version, spend_account
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        str(getattr(usage, "error", "") or "")[:2_400],
                        cost_cny,
                        int(getattr(usage, "latency_ms", 0) or 0),
                        actor,
                        reservation_id,
                        estimated_cny,
                        attempt,
                        reasoning_tokens,
                        priced.pricing_version[:80],
                        self._spend_account,
                    ),
                )
            finally:
                connection.close()

    def cost_since(self, *, since: datetime) -> float:
        """Sum repriced cost_cny for records recorded after ``since`` (UTC)."""
        with self._lock:
            connection = self._connect()
            try:
                return self._priced_model_spend_cny(connection, since=since)
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
        monthly_budget_cny: float | None = None,
        daily_budget_cny: float | None = None,
        soft_daily_budget_cny: float | None = None,
    ) -> dict[str, object]:
        if monthly_budget_cny is None:
            monthly_budget_cny = self._monthly_budget_cny
        if daily_budget_cny is None:
            daily_budget_cny = self._daily_budget_cny
        if soft_daily_budget_cny is None:
            soft_daily_budget_cny = self._soft_daily_budget_cny
        monthly = self.monthly_cost_cny()
        daily = self.daily_cost_cny()
        attribution = self._daily_attribution()
        warning_reasons = list(attribution["warning_reasons"])
        monthly_exhausted = (
            monthly_budget_cny is not None and monthly >= monthly_budget_cny
        )
        daily_exhausted = daily_budget_cny is not None and daily >= daily_budget_cny
        soft_daily_exhausted = (
            soft_daily_budget_cny is not None and daily >= soft_daily_budget_cny
        )
        if monthly_exhausted:
            warning_reasons.append("monthly_exhausted")
        if daily_exhausted:
            warning_reasons.append("daily_exhausted")
        if soft_daily_exhausted:
            warning_reasons.append("soft_daily_exhausted")
        return {
            "monthly_cost_cny": round(monthly, 2),
            "monthly_budget_cny": monthly_budget_cny,
            "monthly_exhausted": monthly_exhausted,
            "daily_cost_cny": round(daily, 2),
            "daily_budget_cny": daily_budget_cny,
            "daily_exhausted": daily_exhausted,
            "soft_daily_budget_cny": soft_daily_budget_cny,
            "soft_daily_exhausted": soft_daily_exhausted,
            "purpose_counts": attribution["purpose_counts"],
            "calls_per_user_message": attribution["calls_per_user_message"],
            "calls_per_user_message_alert": attribution["calls_per_user_message_alert"],
            "cache_hit_rate": attribution["cache_hit_rate"],
            "cache_hit_rate_alert": attribution["cache_hit_rate_alert"],
            "invalid_cost_rate": attribution["invalid_cost_rate"],
            "invalid_cost_rate_alert": attribution["invalid_cost_rate_alert"],
            "cny_per_delivered_message": attribution["cny_per_delivered_message"],
            "last_memory_write_at": attribution["last_memory_write_at"],
            "chat_recall_authority": attribution["chat_recall_authority"],
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
                observation_count = self._today_event_count(
                    connection, "ObservationRecorded", day_key
                )
                delivered_count = self._today_event_count(connection, "ActionDelivered", day_key)
                last_memory_write_at = self._latest_memory_write_at(connection)
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
        cache_hit_rate = cache_hit_tokens / prompt_tokens if prompt_tokens > 0 else None
        cache_alert = cache_hit_rate is not None and cache_hit_rate < 0.5
        invalid_cost_rate = invalid_cost / total_cost if total_cost > 0 else None
        invalid_alert = invalid_cost_rate is not None and invalid_cost_rate > 0.1
        cny_per_delivered_message = total_cost / delivered_count if delivered_count > 0 else None
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
            "cny_per_delivered_message": cny_per_delivered_message,
            "last_memory_write_at": last_memory_write_at,
            "chat_recall_authority": "FactCommittedV2",
            "warning_reasons": warning_reasons,
        }

    def _today_event_count(
        self, connection: sqlite3.Connection, event_type: str, day_key: str
    ) -> int:
        try:
            row = connection.execute(
                """
                SELECT COUNT(*) FROM world_v2_events
                WHERE json_extract(event_json, '$.event_type') = ?
                  AND substr(json_extract(event_json, '$.created_at'), 1, 10) = ?
                """,
                (event_type, day_key),
            ).fetchone()
        except sqlite3.DatabaseError:
            return 0
        return int(row[0] if row is not None else 0)

    def _latest_memory_write_at(self, connection: sqlite3.Connection) -> str | None:
        try:
            row = connection.execute(
                """
                SELECT MAX(json_extract(event_json, '$.created_at'))
                FROM world_v2_events
                WHERE json_extract(event_json, '$.event_type') IN
                    ('MemoryCandidateAccepted', 'FactCommittedV2')
                """
            ).fetchone()
        except sqlite3.DatabaseError:
            return None
        value = row[0] if row is not None else None
        return str(value) if value else None

    def _today_observation_count(self, connection: sqlite3.Connection, day_key: str) -> int:
        return self._today_event_count(connection, "ObservationRecorded", day_key)


__all__ = [
    "BackgroundSpendCapDenied",
    "GENERIC_MODEL_PURPOSES",
    "ModelUsageAdmissionError",
    "VISIBLE_INBOUND_PURPOSES",
    "WorldV2UsageStore",
    "usage_store_for_settings",
]
