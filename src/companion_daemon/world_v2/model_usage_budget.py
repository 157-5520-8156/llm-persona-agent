"""World V2 model usage recording and CNY budget gating.

Text, image and vision admissions share one transactional cost envelope.
Explicit hard spend caps also gate visible inbound work; the CNY100 design
target is a separate forecast and never a reason to invent character silence.
Optional background workers share the monthly/daily/soft-daily envelope.
Image CNY remains in ``usage_events`` on the same SQLite path.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..db import ensure_usage_events_schema
from ..spend_account import classify_spend_account
from ..usage_metrics import (
    CNY_PER_USD,
    estimate_model_cost,
    estimate_routed_model_reserve_cny,
    price_usage_row,
)

from .cost_forecast import forecast_monthly_cost

_LOG = logging.getLogger(__name__)

_USD_TO_CNY = CNY_PER_USD
_PROVIDER_IMPORT_PREFIX = "usage-import:"

GENERIC_MODEL_PURPOSES = frozenset(
    {
        "",
        "unclassified",
        "world_v2_character_interior",
    }
)

# Visible-turn purposes bypass optional background soft limits. Explicit
# operator hard caps still apply; denial remains a technical result, not silence.
VISIBLE_INBOUND_PURPOSES = frozenset(
    {
        "inbound_turn",
        "inbound_source_review",
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
    ("billing_state", "TEXT NOT NULL DEFAULT 'legacy'"),
)


class ModelUsageAdmissionError(ValueError):
    """Raised when a World V2 provider call has no attributable reservation."""


class BackgroundSpendCapDenied(ModelUsageAdmissionError):
    """Background model work hit the CNY envelope; do not call the provider."""

    def __init__(self, reason: str, *, message: str | None = None) -> None:
        normalized = (
            str(reason or "background_spend_cap_denied").strip() or "background_spend_cap_denied"
        )
        self.reason = normalized[:128]
        super().__init__(message or f"world v2 background model call skipped: {self.reason}")


def _is_invalid_cost(*, purpose: str, attempt: int) -> bool:
    if attempt > 1:
        return True
    lowered = purpose.casefold()
    return any(marker in lowered for marker in _REPAIR_PURPOSE_MARKERS)


def usage_store_for_settings(settings: object) -> WorldV2UsageStore:
    """Bind World V2 usage to the same sqlite Settings uses for the ledger."""

    background_daily = _optional_float(
        getattr(settings, "world_v2_background_daily_budget_cny", None)
    )
    if background_daily is not None and background_daily <= 0:
        # Zero is the documented "no separate background ceiling" value.  It
        # must not become a ceiling of zero that denies every background call.
        background_daily = None
    return WorldV2UsageStore(
        path=str(getattr(settings, "database_path")),
        monthly_cost_target_cny=float(getattr(settings, "world_v2_monthly_cost_target_cny", 100.0)),
        monthly_budget_cny=_optional_float(getattr(settings, "monthly_budget_cny", None)),
        daily_budget_cny=_optional_float(getattr(settings, "daily_budget_cny", None)),
        soft_daily_budget_cny=_optional_float(getattr(settings, "soft_daily_budget_cny", None)),
        background_daily_budget_cny=background_daily,
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
        monthly_cost_target_cny: float = 100.0,
        monthly_budget_cny: float | None = None,
        daily_budget_cny: float | None = None,
        soft_daily_budget_cny: float | None = None,
        background_daily_budget_cny: float | None = None,
    ) -> None:
        if not path:
            raise ValueError("world v2 usage store requires a database path")
        if not math.isfinite(monthly_cost_target_cny) or monthly_cost_target_cny <= 0:
            raise ValueError("monthly cost target must be finite and positive")
        self._monthly_cost_target_cny = monthly_cost_target_cny
        self._path = path
        self._usd_to_cny = usd_to_cny
        self._monthly_budget_cny = monthly_budget_cny
        self._daily_budget_cny = daily_budget_cny
        self._soft_daily_budget_cny = soft_daily_budget_cny
        self._background_daily_budget_cny = background_daily_budget_cny
        self._lock = threading.RLock()
        self._spend_account = classify_spend_account(database_path=path)
        ensure_usage_events_schema(Path(path))
        connection = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(_SCHEMA)
            connection.execute(_RESERVATION_SCHEMA)
            connection.execute(
                """CREATE TABLE IF NOT EXISTS world_v2_model_usage_imports (
                    import_id TEXT PRIMARY KEY,
                    source_ledger_path TEXT NOT NULL,
                    source_reservation_id TEXT NOT NULL,
                    source_usage_id INTEGER NOT NULL,
                    source_usage_hash TEXT NOT NULL,
                    target_usage_id INTEGER NOT NULL UNIQUE,
                    imported_at TEXT NOT NULL,
                    UNIQUE(source_ledger_path, source_reservation_id)
                )"""
            )
            self._migrate_usage_columns(connection)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS model_usage_time ON world_v2_model_usage(recorded_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS model_usage_reservation ON world_v2_model_usage(reservation_id)"
            )
            external_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(usage_events)")
            }
            for name, declaration in (
                ("reservation_id", "TEXT NOT NULL DEFAULT ''"),
                ("billing_state", "TEXT NOT NULL DEFAULT 'legacy'"),
            ):
                if name not in external_columns:
                    connection.execute(f"ALTER TABLE usage_events ADD COLUMN {name} {declaration}")
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS usage_event_reservation "
                "ON usage_events(reservation_id) WHERE reservation_id != ''"
            )
            connection.commit()
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

    def _gates_cny(self, purpose: str) -> bool:
        """Return whether this purpose participates in any configured CNY envelope."""

        if purpose in VISIBLE_INBOUND_PURPOSES:
            return self._monthly_budget_cny is not None or self._daily_budget_cny is not None
        return any(
            limit is not None
            for limit in (
                self._monthly_budget_cny,
                self._daily_budget_cny,
                self._soft_daily_budget_cny,
                self._background_daily_budget_cny,
            )
        )

    def _utc_window_start(self, *, month: bool) -> datetime:
        now = datetime.now(timezone.utc)
        if month:
            return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    def _priced_model_spend_cny(self, connection: sqlite3.Connection, *, since: datetime) -> float:
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

    def _background_model_spend_cny(
        self, connection: sqlite3.Connection, *, since: datetime
    ) -> float:
        """Sum today's spend for non-visible lanes only.

        Visible inbound turns are excluded on purpose: the background ceiling
        exists to keep the life machine affordable next to a waiting human, and
        never to silence a reply they are watching for.
        """

        placeholders = ", ".join("?" for _ in VISIBLE_INBOUND_PURPOSES)
        rows = connection.execute(
            f"""
            SELECT recorded_at, model, prompt_tokens, completion_tokens,
                   cache_hit_tokens, cache_miss_tokens, cost_cny,
                   COALESCE(reasoning_tokens, 0)
            FROM world_v2_model_usage
            WHERE recorded_at >= ?
              AND purpose != 'image_generation'
              AND status != 'budget_denied'
              AND purpose NOT IN ({placeholders})
            """,
            (since.isoformat(), *sorted(VISIBLE_INBOUND_PURPOSES)),
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

    def _spend_snapshot(
        self, connection: sqlite3.Connection, *, since: datetime
    ) -> dict[str, float]:
        """One connection/snapshot for admission and health, including open bills.

        An unknown bill retains only the part of its reserve not already
        represented by usage. Unresolved calls survive calendar rollover: they
        could still settle in this window and must not become fresh capacity.
        """
        iso = since.isoformat()
        model = self._priced_model_spend_cny(connection, since=since)
        external = float(
            connection.execute(
                "SELECT COALESCE(SUM(estimated_cny), 0) FROM usage_events "
                "WHERE created_at >= ? AND billing_state != 'unknown'",
                (iso,),
            ).fetchone()[0]
        )
        embedding = 0.0
        if self._has_table(connection, "world_recall_embedding_usage_daily"):
            # Embedding intentionally folds requests into conservative day
            # buckets. A partial-day query is an upper bound, not exact 24h usage.
            embedding = float(
                connection.execute(
                    "SELECT COALESCE(SUM(estimated_cost_cny), 0) "
                    "FROM world_recall_embedding_usage_daily WHERE usage_day >= ?",
                    (since.date().isoformat(),),
                ).fetchone()[0]
            )
        pending = 0.0
        unknown = 0.0
        for amount, status, accounted in connection.execute(
            """
            SELECT MAX(r.estimated_cny, COALESCE((
                       SELECT estimated_cny FROM usage_events e
                       WHERE e.reservation_id = r.reservation_id AND e.billing_state = 'unknown'
                   ), 0)), r.status,
                COALESCE((SELECT SUM(u.cost_cny) FROM world_v2_model_usage u
                          WHERE u.reservation_id = r.reservation_id
                          AND u.recorded_at >= ? AND u.purpose != 'image_generation'), 0)
            FROM world_v2_model_reservations r
            WHERE r.status IN ('pending', 'billing_unknown')
            """,
            (iso,),
        ):
            remainder = max(0.0, float(amount) - float(accounted))
            if status == "billing_unknown":
                unknown += remainder
            else:
                pending += remainder
        return {
            "model_cny": model,
            "external_cny": external,
            "pending_cny": pending,
            "unknown_cny": unknown,
            "embedding_cny": embedding,
            "settled_cny": model + external + embedding,
            "committed_cny": model + external + embedding + pending + unknown,
        }

    def _background_spend_snapshot(
        self, connection: sqlite3.Connection, *, since: datetime
    ) -> dict[str, float]:
        """Capacity already occupied by calls subject to the background cap.

        Keep the historical token-cost readout separate from external bills and
        open reservations. External bills join their admission's purpose, so a
        non-visible call cannot release capacity merely by settling in
        ``usage_events``. Image token telemetry remains a non-billing mirror.
        Unreserved external/embedding ledgers retain their existing outer
        monthly/daily accounting; this is not a full-instance cost aggregate.
        """

        iso = since.isoformat()
        purposes = tuple(sorted(VISIBLE_INBOUND_PURPOSES))
        placeholders = ", ".join("?" for _ in purposes)
        model = self._background_model_spend_cny(connection, since=since)
        external = float(
            connection.execute(
                f"""
                SELECT COALESCE(SUM(e.estimated_cny), 0)
                FROM usage_events e JOIN world_v2_model_reservations r
                  ON r.reservation_id = e.reservation_id
                WHERE e.created_at >= ? AND e.billing_state != 'unknown'
                  AND r.purpose NOT IN ({placeholders})
                """,
                (iso, *purposes),
            ).fetchone()[0]
        )
        pending = unknown = 0.0
        reservations = connection.execute(
            f"""
            SELECT r.reservation_id, r.status,
                   MAX(r.estimated_cny, COALESCE((
                       SELECT e.estimated_cny FROM usage_events e
                       WHERE e.reservation_id = r.reservation_id
                         AND e.billing_state = 'unknown'
                   ), 0))
            FROM world_v2_model_reservations r
            WHERE r.status IN ('pending', 'billing_unknown')
              AND r.purpose NOT IN ({placeholders})
            """,
            purposes,
        ).fetchall()
        for reservation_id, status, amount in reservations:
            # Partial usage contributes a repriced lower bound, not a second
            # whole charge. Use the same price as the model aggregate rather
            # than its rounded cost_cny cell. An old unresolved lower bound
            # above the reservation also survives calendar rollover.
            recorded_total = recorded_in_window = 0.0
            cursor = connection.execute(
                "SELECT * FROM world_v2_model_usage WHERE reservation_id = ? "
                "AND status != 'budget_denied' AND purpose != 'image_generation'",
                (reservation_id,),
            )
            cursor.row_factory = sqlite3.Row
            for row in cursor:
                cost = price_usage_row(dict(row), cny_per_usd=self._usd_to_cny).cny
                recorded_total += cost
                if row["recorded_at"] >= iso:
                    recorded_in_window += cost
            remainder = max(0.0, max(float(amount), recorded_total) - recorded_in_window)
            if status == "billing_unknown":
                unknown += remainder
            else:
                pending += remainder
        return {
            "model_cny": model,
            "external_cny": external,
            "pending_cny": pending,
            "unknown_cny": unknown,
            "committed_cny": model + external + pending + unknown,
        }

    @staticmethod
    def _has_table(connection: sqlite3.Connection, name: str) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (name,),
            ).fetchone()
            is not None
        )

    def _cost_health(self) -> dict[str, object]:
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN")
                month = self._spend_snapshot(connection, since=month_start)
                day = self._spend_snapshot(
                    connection, since=now.replace(hour=0, minute=0, second=0, microsecond=0)
                )
                recent = self._spend_snapshot(connection, since=now - timedelta(hours=24))
                background_day = self._background_spend_snapshot(
                    connection, since=now.replace(hour=0, minute=0, second=0, microsecond=0)
                )
                times = [
                    row[0]
                    for row in connection.execute(
                        "SELECT MIN(recorded_at) FROM world_v2_model_usage WHERE status != 'budget_denied' "
                        "UNION ALL SELECT MIN(created_at) FROM world_v2_model_reservations "
                        "UNION ALL SELECT MIN(created_at) FROM usage_events"
                    )
                    if row[0]
                ]
                if self._has_table(connection, "world_recall_embedding_usage_daily"):
                    times.extend(
                        row[0]
                        for row in connection.execute(
                            "SELECT MIN(usage_day) || 'T00:00:00+00:00' "
                            "FROM world_recall_embedding_usage_daily"
                        )
                        if row[0]
                    )
                observed = [datetime.fromisoformat(value) for value in times]
                observed = [
                    value for value in observed if value.tzinfo is not None and value <= now
                ]
                unresolved_count = connection.execute(
                    "SELECT COUNT(*) FROM world_v2_model_reservations WHERE status = 'billing_unknown'"
                ).fetchone()[0]
                unpriced_external_count = connection.execute(
                    "SELECT COUNT(*) FROM usage_events WHERE kind = 'civitai_buzz' AND created_at >= ?",
                    (month_start.isoformat(),),
                ).fetchone()[0]
                # Purpose breakdown uses the same repricing as admission; image
                # mirror telemetry is excluded because usage_events owns its CNY.
                purpose_costs: dict[str, float] = {}
                connection.row_factory = sqlite3.Row
                for row in connection.execute(
                    "SELECT * FROM world_v2_model_usage WHERE recorded_at >= ? "
                    "AND status != 'budget_denied' AND purpose != 'image_generation'",
                    (month_start.isoformat(),),
                ):
                    label = row["purpose"]
                    purpose_costs[label] = (
                        purpose_costs.get(label, 0.0)
                        + price_usage_row(dict(row), cny_per_usd=self._usd_to_cny).cny
                    )
                for row in connection.execute(
                    "SELECT kind, SUM(estimated_cny) amount FROM usage_events "
                    "WHERE created_at >= ? AND billing_state != 'unknown' GROUP BY kind",
                    (month_start.isoformat(),),
                ):
                    purpose_costs[row["kind"]] = purpose_costs.get(row["kind"], 0.0) + row["amount"]
                if month["embedding_cny"]:
                    purpose_costs["recall_embedding"] = month["embedding_cny"]
            finally:
                connection.close()
        forecast = forecast_monthly_cost(
            now=now,
            observed_since=min(observed) if observed else None,
            month_settled_cny=month["settled_cny"],
            pending_cny=month["pending_cny"] + month["unknown_cny"],
            last24h_settled_cny=recent["model_cny"] + recent["external_cny"],
            target_monthly_cny=self._monthly_cost_target_cny,
        )
        warnings = []
        if forecast["forecast_pressure_threshold_percent"]:
            warnings.append("monthly_cost_forecast_pressure")
        if unresolved_count:
            warnings.append("provider_billing_unknown")
        if unpriced_external_count:
            warnings.append("external_usage_unpriced")
        return {
            "month": month,
            "day": day,
            "background_day": background_day,
            "cost_forecast": forecast,
            "monthly_purpose_cost_cny": {
                key: round(value, 6) for key, value in purpose_costs.items()
            },
            "unresolved_billing_count": unresolved_count,
            "unpriced_external_usage_count": unpriced_external_count,
            "recent_embedding_cost_upper_bound_cny": recent["embedding_cny"],
            "cost_observation_scope": "current_database_recorded_usage",
            # A numeric forecast never certifies historical missing bills, native
            # currencies without a price basis, or accounts in other databases.
            "instance_cost_qualification": "incomplete",
            "warning_reasons": warnings,
        }

    def _combined_spend_cny(self, *, since: datetime) -> float:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN")
                return self._spend_snapshot(connection, since=since)["committed_cny"]
            finally:
                connection.close()

    def _spend_cap_reason(
        self, connection: sqlite3.Connection, estimated_cny: float, *, purpose: str
    ) -> str | None:
        monthly = self._spend_snapshot(connection, since=self._utc_window_start(month=True))[
            "committed_cny"
        ]
        daily = self._spend_snapshot(connection, since=self._utc_window_start(month=False))[
            "committed_cny"
        ]
        if (
            self._monthly_budget_cny is not None
            and monthly + estimated_cny > self._monthly_budget_cny
        ):
            return "monthly_budget_exceeded"
        if self._daily_budget_cny is not None and daily + estimated_cny > self._daily_budget_cny:
            return "daily_budget_exceeded"
        if purpose in VISIBLE_INBOUND_PURPOSES:
            return None
        if (
            self._soft_daily_budget_cny is not None
            and daily + estimated_cny > self._soft_daily_budget_cny
        ):
            return "soft_daily_budget_exceeded"
        if self._background_daily_budget_cny is not None:
            background = self._background_spend_snapshot(
                connection, since=self._utc_window_start(month=False)
            )["committed_cny"]
            if background + estimated_cny > self._background_daily_budget_cny:
                # The visible lane keeps its own explicit caps above; this one
                # bounds non-visible work and its unresolved reservations.
                return "background_daily_budget_exceeded"
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
        if not math.isfinite(reserved_cny) or reserved_cny < 0:
            raise ModelUsageAdmissionError("world v2 model call estimated CNY is invalid")
        token = reservation_id.strip() or f"reservation:{uuid.uuid4().hex}"
        if token.startswith(_PROVIDER_IMPORT_PREFIX):
            raise ModelUsageAdmissionError("provider import identity cannot become an admission")
        created_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            connection = self._connect()
            try:
                # SQLite's write lease coordinates all stores/processes sharing
                # this ledger. No await or provider operation occurs in it.
                connection.execute("BEGIN IMMEDIATE")
                if self._gates_cny(resolved_purpose):
                    reason = self._spend_cap_reason(
                        connection, reserved_cny, purpose=resolved_purpose
                    )
                    if reason is not None:
                        connection.rollback()
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
                        raise BackgroundSpendCapDenied(reason)
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
                connection.commit()
            finally:
                connection.close()
        return token

    def record_external_usage(
        self,
        *,
        reservation_id: str,
        kind: str,
        estimated_cny: float | None,
        billing_state: str,
        note: str = "",
    ) -> None:
        """Settle one reserved external call and its CNY row atomically.

        Unknown billing keeps the reserve (or a larger provisional estimate).
        An optional provisional row preserves delivered-image counts but is
        excluded from settled CNY. Only provider evidence can finalize it. This method intentionally
        raises on persistence failure so callers retain a retryable bill.
        """
        if billing_state not in {"known", "unknown", "not_billed"}:
            raise ModelUsageAdmissionError("invalid external billing state")
        if estimated_cny is not None and (not math.isfinite(estimated_cny) or estimated_cny < 0):
            raise ModelUsageAdmissionError("external CNY must be finite and nonnegative")
        if billing_state == "known" and estimated_cny is None:
            raise ModelUsageAdmissionError("known external bill requires CNY")
        if billing_state == "not_billed" and estimated_cny not in {None, 0.0}:
            raise ModelUsageAdmissionError("not_billed cannot carry charged CNY")
        if billing_state == "not_billed":
            estimated_cny = 0.0
        with self._lock:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("BEGIN IMMEDIATE")
                reservation = connection.execute(
                    "SELECT * FROM world_v2_model_reservations WHERE reservation_id = ?",
                    (reservation_id,),
                ).fetchone()
                if (
                    connection.execute(
                        "SELECT 1 FROM world_v2_model_usage WHERE reservation_id = ?",
                        (reservation_id,),
                    ).fetchone()
                    is not None
                ):
                    raise ModelUsageAdmissionError("external bill cannot replace a token bill")
                if reservation is None or reservation["purpose"] != kind:
                    raise ModelUsageAdmissionError(
                        "external bill has no matching purpose reservation"
                    )
                existing = connection.execute(
                    "SELECT * FROM usage_events WHERE reservation_id = ?",
                    (reservation_id,),
                ).fetchone()
                if existing is not None and existing["billing_state"] != "unknown":
                    if (
                        existing["billing_state"] != billing_state
                        or existing["estimated_cny"] != estimated_cny
                    ):
                        raise ModelUsageAdmissionError("conflicting final external bill")
                    return
                if reservation["status"] == "settled":
                    if billing_state == "not_billed":
                        return
                    raise ModelUsageAdmissionError(
                        "external bill conflicts with completed reservation"
                    )
                if existing is not None:
                    amount = estimated_cny
                    if billing_state == "unknown":
                        amount = max(float(existing["estimated_cny"]), amount or 0.0)
                    connection.execute(
                        "UPDATE usage_events SET estimated_cny = ?, billing_state = ?, note = ? "
                        "WHERE reservation_id = ?",
                        (amount or 0.0, billing_state, note[:2400], reservation_id),
                    )
                elif estimated_cny is not None and billing_state != "not_billed":
                    connection.execute(
                        "INSERT INTO usage_events "
                        "(kind, estimated_cny, note, created_at, reservation_id, billing_state) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            kind,
                            estimated_cny,
                            note[:2400],
                            reservation["created_at"],
                            reservation_id,
                            billing_state,
                        ),
                    )
                connection.execute(
                    "UPDATE world_v2_model_reservations SET status = ? WHERE reservation_id = ?",
                    (
                        "billing_unknown" if billing_state == "unknown" else "settled",
                        reservation_id,
                    ),
                )
                connection.commit()
            finally:
                connection.close()

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

    def import_settled_provider_usage(
        self, *, source: WorldV2UsageStore, reservation_id: str
    ) -> bool:
        """Mirror a persisted debug bill without creating a local admission.

        The source reservation and its known provider bill are the receipt;
        callback success is not proof that ``record`` persisted anything.
        Unknown/legacy/non-billed callbacks cannot create a settled mirror.
        The source call time and bill bytes survive delayed or repeated imports.
        """
        if self._spend_account != "debug" or source._spend_account != "debug":
            raise ModelUsageAdmissionError("provider bill imports require debug ledgers")
        if not reservation_id:
            return False
        with source._lock:
            connection = source._connect()
            connection.row_factory = sqlite3.Row
            try:
                rows = connection.execute(
                    """SELECT u.*, r.created_at AS reservation_created_at,
                              r.purpose AS reservation_purpose,
                              r.provider AS reservation_provider,
                              r.actor AS reservation_actor,
                              r.world_id AS reservation_world_id,
                              r.turn_id AS reservation_turn_id
                       FROM world_v2_model_usage u
                       JOIN world_v2_model_reservations r
                         ON r.reservation_id = u.reservation_id
                       WHERE r.reservation_id = ? AND r.status = 'settled'
                         AND u.billing_state = 'known'
                         AND NOT EXISTS (
                             SELECT 1 FROM usage_events e
                             WHERE e.reservation_id = r.reservation_id
                         )
                       LIMIT 2""",
                    (reservation_id,),
                ).fetchall()
            finally:
                connection.close()
        if len(rows) != 1:
            return False
        bill = dict(rows[0])
        if bill.pop("reservation_created_at") != bill["recorded_at"]:
            raise ModelUsageAdmissionError("source provider bill call time mismatch")
        for key in ("purpose", "provider", "actor", "world_id", "turn_id"):
            reserved_value = bill.pop(f"reservation_{key}")
            # Admission may omit attribution which the final provider bill
            # supplies; preserve the primary store's existing validation rule.
            if reserved_value and reserved_value != bill[key]:
                raise ModelUsageAdmissionError(f"source provider bill {key} mismatch")
        source_path = str(Path(source._path).resolve())
        if Path(self._path).samefile(source_path):
            return True  # The original bill already occupies this physical ledger.
        if source._usd_to_cny != self._usd_to_cny:
            # Aggregate readers reprice USD models using their configured rate.
            # Copying a rounded CNY cell would not preserve the source's bill.
            raise ModelUsageAdmissionError("provider bill import exchange rate mismatch")
        source_hash = hashlib.sha256(
            json.dumps(bill, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        import_id = (
            _PROVIDER_IMPORT_PREFIX
            + hashlib.sha256(
                json.dumps([source_path, reservation_id], separators=(",", ":")).encode()
            ).hexdigest()
        )
        source_usage_id = bill.pop("id")
        # Imported tokens must never join a target-local reservation with the
        # same source token. The original identity remains in the import receipt.
        bill["reservation_id"] = import_id
        bill["spend_account"] = self._spend_account
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                previous = connection.execute(
                    "SELECT source_usage_hash FROM world_v2_model_usage_imports "
                    "WHERE import_id = ?",
                    (import_id,),
                ).fetchone()
                if previous is not None:
                    if previous[0] != source_hash:
                        raise ModelUsageAdmissionError("conflicting imported provider bill")
                    return True
                if (
                    connection.execute(
                        "SELECT 1 FROM world_v2_model_reservations WHERE reservation_id = ?",
                        (import_id,),
                    ).fetchone()
                    is not None
                ):
                    raise ModelUsageAdmissionError("provider import collides with local admission")
                columns = tuple(bill)
                cursor = connection.execute(
                    f"INSERT INTO world_v2_model_usage ({', '.join(columns)}) "
                    f"VALUES ({', '.join('?' for _ in columns)})",
                    tuple(bill.values()),
                )
                connection.execute(
                    "INSERT INTO world_v2_model_usage_imports VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        import_id,
                        source_path,
                        reservation_id,
                        source_usage_id,
                        source_hash,
                        cursor.lastrowid,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                connection.commit()
                return True
            finally:
                connection.close()

    def _record_usage(self, usage: object) -> None:
        reservation_id = str(
            getattr(usage, "budget_reservation_id", "")
            or getattr(usage, "reservation_id", "")
            or ""
        )
        model = str(getattr(usage, "model", "") or "")
        tokens = {
            name: int(getattr(usage, name, 0) or 0)
            for name in (
                "prompt_tokens",
                "completion_tokens",
                "reasoning_tokens",
                "cache_hit_tokens",
                "cache_miss_tokens",
                "total_tokens",
            )
        }
        billing = str(getattr(usage, "billing_state", "legacy") or "unknown")
        if billing not in {"known", "unknown", "not_billed", "legacy"}:
            raise ModelUsageAdmissionError("invalid provider billing state")
        if any(value < 0 for value in tokens.values()):
            raise ModelUsageAdmissionError("negative provider usage")
        if billing == "not_billed" and any(tokens.values()):
            raise ModelUsageAdmissionError("not_billed usage carries charged tokens")
        values = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "world_id": str(getattr(usage, "world_id", "") or ""),
            "turn_id": str(getattr(usage, "turn_id", "") or ""),
            "purpose": str(getattr(usage, "purpose", "") or ""),
            "model": model,
            "status": str(getattr(usage, "status", "") or ""),
            "provider": str(getattr(usage, "provider", "") or ""),
            **tokens,
            "error": str(getattr(usage, "error", "") or "")[:2_400],
            "latency_ms": int(getattr(usage, "latency_ms", 0) or 0),
            "actor": str(getattr(usage, "actor", "") or ""),
            "reservation_id": reservation_id,
            "estimated_cny": 0.0,
            "attempt": int(getattr(usage, "attempt", 1) or 1),
            "spend_account": self._spend_account,
            "billing_state": billing,
        }
        with self._lock:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("BEGIN IMMEDIATE")
                previous = None
                if reservation_id:
                    reservation = connection.execute(
                        "SELECT * FROM world_v2_model_reservations WHERE reservation_id = ?",
                        (reservation_id,),
                    ).fetchone()
                    if (
                        connection.execute(
                            "SELECT 1 FROM usage_events WHERE reservation_id = ?",
                            (reservation_id,),
                        ).fetchone()
                        is not None
                    ):
                        raise ModelUsageAdmissionError("token bill cannot replace an external bill")
                    if reservation is None:
                        raise ModelUsageAdmissionError("usage has no matching reservation")
                    for key in ("purpose", "provider", "actor", "world_id", "turn_id"):
                        if reservation[key] and values[key] and reservation[key] != values[key]:
                            raise ModelUsageAdmissionError(f"usage reservation {key} mismatch")
                        values[key] = reservation[key] or values[key]
                    values["estimated_cny"] = float(reservation["estimated_cny"])
                    # Reconciliation uses the original call's pricing/time,
                    # never the time a delayed observer or bill arrives.
                    values["recorded_at"] = reservation["created_at"]
                    previous = connection.execute(
                        "SELECT * FROM world_v2_model_usage WHERE reservation_id = ? ORDER BY id LIMIT 1",
                        (reservation_id,),
                    ).fetchone()
                    if previous is not None and previous["billing_state"] != "unknown":
                        if any(
                            previous[key] != values[key]
                            for key in (
                                "model",
                                "billing_state",
                                *tokens,
                            )
                        ):
                            raise ModelUsageAdmissionError("conflicting final provider bill")
                        return  # Same completed bill is effect-once.
                    if previous is not None and billing == "unknown":
                        # Partial telemetry can improve a lower bound, not erase it.
                        for key in tokens:
                            values[key] = max(values[key], previous[key])
                priced = estimate_model_cost(
                    model=model or "__unpriced__",
                    at=values["recorded_at"],
                    cny_per_usd=self._usd_to_cny,
                    **{key: values[key] for key in tokens if key != "total_tokens"},
                )
                values["cost_cny"] = round(priced.cny, 4)
                values["pricing_version"] = priced.pricing_version[:80]
                columns = tuple(values)
                if previous is None:
                    connection.execute(
                        f"INSERT INTO world_v2_model_usage ({', '.join(columns)}) "
                        f"VALUES ({', '.join('?' for _ in columns)})",
                        tuple(values.values()),
                    )
                else:
                    connection.execute(
                        f"UPDATE world_v2_model_usage SET {', '.join(key + ' = ?' for key in columns)} "
                        "WHERE id = ?",
                        (*values.values(), previous["id"]),
                    )
                if reservation_id:
                    connection.execute(
                        "UPDATE world_v2_model_reservations SET status = ? WHERE reservation_id = ?",
                        ("billing_unknown" if billing == "unknown" else "settled", reservation_id),
                    )
                connection.commit()
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

    def background_daily_cost_cny(self) -> float:
        """Today's recorded non-visible token cost, excluding open bill holds."""

        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        with self._lock:
            connection = self._connect()
            try:
                return self._background_model_spend_cny(connection, since=day_start)
            finally:
                connection.close()

    def background_daily_committed_cny(self) -> float:
        """Recorded background charges plus unresolved capacity, including old calls."""

        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN")
                return self._background_spend_snapshot(
                    connection, since=self._utc_window_start(month=False)
                )["committed_cny"]
            finally:
                connection.close()

    @property
    def background_daily_budget_cny(self) -> float | None:
        """The configured daily background ceiling, or ``None`` when unset."""

        return self._background_daily_budget_cny

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
        cost_health = self._cost_health()
        month = cost_health.pop("month")
        day = cost_health.pop("day")
        background_day = cost_health.pop("background_day")
        monthly = month["committed_cny"]
        daily = day["committed_cny"]
        attribution = self._daily_attribution()
        warning_reasons = list(attribution["warning_reasons"])
        warning_reasons.extend(cost_health.pop("warning_reasons"))
        monthly_exhausted = monthly_budget_cny is not None and monthly >= monthly_budget_cny
        daily_exhausted = daily_budget_cny is not None and daily >= daily_budget_cny
        soft_daily_exhausted = soft_daily_budget_cny is not None and daily >= soft_daily_budget_cny
        background_daily = background_day["committed_cny"]
        background_daily_exhausted = (
            self._background_daily_budget_cny is not None
            and background_daily >= self._background_daily_budget_cny
        )
        if monthly_exhausted:
            warning_reasons.append("monthly_exhausted")
        if daily_exhausted:
            warning_reasons.append("daily_exhausted")
        if soft_daily_exhausted:
            warning_reasons.append("soft_daily_exhausted")
        if background_daily_exhausted:
            warning_reasons.append("background_daily_exhausted")
        return {
            **cost_health,
            "monthly_cost_cny": round(month["settled_cny"], 4),
            "monthly_committed_cny": round(monthly, 4),
            "pending_cost_cny": round(month["pending_cny"], 4),
            "unknown_cost_hold_cny": round(month["unknown_cny"], 4),
            "monthly_budget_cny": monthly_budget_cny,
            "monthly_exhausted": monthly_exhausted,
            "daily_cost_cny": round(day["settled_cny"], 4),
            "daily_committed_cny": round(daily, 4),
            "daily_budget_cny": daily_budget_cny,
            "daily_exhausted": daily_exhausted,
            "soft_daily_budget_cny": soft_daily_budget_cny,
            "soft_daily_exhausted": soft_daily_exhausted,
            "background_daily_budget_cny": self._background_daily_budget_cny,
            "background_daily_cost_cny": round(background_day["model_cny"], 4),
            "background_external_cost_cny": round(background_day["external_cny"], 4),
            "background_pending_cost_cny": round(background_day["pending_cny"], 4),
            "background_unknown_cost_hold_cny": round(background_day["unknown_cny"], 4),
            "background_daily_committed_cny": round(background_daily, 4),
            "background_daily_exhausted": background_daily_exhausted,
            "token_usage_metrics_scope": "text_and_vision_token_calls",
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
                    WHERE recorded_at >= ? AND status != 'budget_denied'
                      AND purpose != 'image_generation'
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
