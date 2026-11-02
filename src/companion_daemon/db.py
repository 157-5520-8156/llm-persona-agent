import json
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from companion_daemon.models import IncomingMessage, LifeRuntimeState, MoodState, Platform
from companion_daemon.time import utc_now


USAGE_EVENTS_DDL = """
create table if not exists usage_events (
  id integer primary key autoincrement,
  kind text not null,
  estimated_cny real not null,
  note text not null,
  created_at text not null
);
"""


def ensure_usage_events_schema(path: Path | str) -> None:
    """Create usage_events on a World V2 ledger without CompanionStore's full schema."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target)
    try:
        connection.execute(USAGE_EVENTS_DDL)
        connection.commit()
    finally:
        connection.close()


def insert_usage_event(
    conn: sqlite3.Connection,
    *,
    kind: str,
    estimated_cny: float,
    note: str = "",
    created_at: str | None = None,
) -> None:
    """Insert one usage row, creating the table if this ledger never saw CompanionStore."""

    payload = (kind, float(estimated_cny), note, created_at or utc_now().isoformat())
    insert_sql = """
        insert into usage_events (kind, estimated_cny, note, created_at)
        values (?, ?, ?, ?)
    """
    try:
        conn.execute(insert_sql, payload)
    except sqlite3.OperationalError as exc:
        if "usage_events" not in str(exc).casefold():
            raise
        conn.rollback()
        conn.execute(USAGE_EVENTS_DDL)
        conn.execute(insert_sql, payload)


class UsageEventsLedger:
    """Narrow spend ledger for image CNY. Does not create the legacy CompanionStore schema."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        ensure_usage_events_schema(self.path)

    @contextmanager
    def connect(self) -> Iterable[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def record_usage(self, kind: str, estimated_cny: float, *, note: str = "") -> None:
        with self.connect() as conn:
            insert_usage_event(conn, kind=kind, estimated_cny=estimated_cny, note=note)

    def usage_total(self, window: str, now: datetime) -> float:
        prefix, where = _usage_window(window, now)
        with self.connect() as conn:
            row = conn.execute(
                f"select coalesce(sum(estimated_cny), 0) as total from usage_events where {where}",
                (prefix,),
            ).fetchone()
        return float(row["total"])

    def usage_count(self, kind: str, window: str, now: datetime) -> int:
        prefix, where = _usage_window(window, now)
        with self.connect() as conn:
            row = conn.execute(
                f"select count(*) as count from usage_events where kind = ? and {where}",
                (kind, prefix),
            ).fetchone()
        return int(row["count"])


def _usage_window(window: str, now: datetime) -> tuple[str, str]:
    if window == "day":
        return now.date().isoformat(), "substr(created_at, 1, 10) = ?"
    if window == "month":
        return now.strftime("%Y-%m"), "substr(created_at, 1, 7) = ?"
    raise ValueError(f"Unsupported usage window: {window}")


class CompanionStore:
    def __init__(self, path: Path, *, primary_user_id: str | None = "geoff"):
        self.path = path
        self.primary_user_id = primary_user_id
        self.world_mode_enabled = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()
        self.world_mode_enabled = self._world_mode_is_persisted()


    def _world_mode_is_persisted(self) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "select value from runtime_flags where key = 'world_mode_enabled'"
            ).fetchone()
        return bool(row and row["value"] == "1")

    def _assert_legacy_behavior_write_allowed(self, operation: str) -> None:
        if self.world_mode_enabled or self._world_mode_is_persisted():
            raise RuntimeError(f"world mode forbids legacy behaviour write: {operation}")

    @contextmanager
    def connect(self) -> Iterable[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                create table if not exists users (
                  id text primary key,
                  display_name text not null,
                  created_at text not null
                );
                create table if not exists runtime_flags (
                  key text primary key,
                  value text not null
                );

                create table if not exists platform_accounts (
                  platform text not null,
                  platform_user_id text not null,
                  canonical_user_id text not null references users(id),
                  created_at text not null,
                  primary key (platform, platform_user_id)
                );

                create table if not exists memories (
                  id integer primary key autoincrement,
                  canonical_user_id text not null references users(id),
                  kind text not null,
                  content text not null,
                  source text not null,
                  confidence real not null,
                  created_at text not null,
                  updated_at text not null,
                  unique (canonical_user_id, kind, content)
                );

                create table if not exists usage_events (
                  id integer primary key autoincrement,
                  kind text not null,
                  estimated_cny real not null,
                  note text not null,
                  created_at text not null
                );

                create table if not exists model_usage_events (
                  id integer primary key autoincrement,
                  purpose text not null,
                  provider text not null default '',
                  model text not null,
                  status text not null,
                  latency_ms integer not null,
                  prompt_tokens integer not null,
                  completion_tokens integer not null,
                  reasoning_tokens integer not null,
                  cache_hit_tokens integer not null,
                  cache_miss_tokens integer not null,
                  total_tokens integer not null,
                  error text not null,
                  world_id text not null default '',
                  turn_id text not null default '',
                  action_id text not null default '',
                  cadence text not null default '',
                  attempt integer not null default 1,
                  pricing_version text not null default '',
                  estimated_cost_usd real not null default 0,
                  estimated_cost_cny real not null default 0,
                  spend_account text not null default '',
                  budget_reservation_id text not null default '',
                  thinking_enabled integer not null default 0,
                  reasoning_effort text not null default '',
                  created_at text not null
                );
                create index if not exists idx_model_usage_created
                  on model_usage_events (created_at, purpose);

                create table if not exists model_budget_reservations (
                  reservation_id text primary key,
                  estimated_cny real not null,
                  actual_cny real,
                  automatic integer not null,
                  status text not null,
                  created_at text not null,
                  started_at text,
                  lease_expires_at text,
                  settled_at text
                );
                create index if not exists idx_model_budget_reservations_active
                  on model_budget_reservations (status, created_at);

                create table if not exists tool_proposals (
                  id integer primary key autoincrement,
                  canonical_user_id text not null references users(id),
                  kind text not null,
                  risk text not null,
                  summary text not null,
                  status text not null,
                  created_at text not null
                );
                """
            )
            self._ensure_column(conn, "model_usage_events", "world_id", "text not null default ''")
            self._ensure_column(conn, "model_usage_events", "provider", "text not null default ''")
            self._ensure_column(conn, "model_usage_events", "turn_id", "text not null default ''")
            self._ensure_column(conn, "model_usage_events", "action_id", "text not null default ''")
            self._ensure_column(conn, "model_usage_events", "cadence", "text not null default ''")
            self._ensure_column(conn, "model_usage_events", "attempt", "integer not null default 1")
            self._ensure_column(
                conn, "model_usage_events", "pricing_version", "text not null default ''"
            )
            self._ensure_column(
                conn, "model_usage_events", "estimated_cost_usd", "real not null default 0"
            )
            self._ensure_column(
                conn, "model_usage_events", "estimated_cost_cny", "real not null default 0"
            )
            self._ensure_column(
                conn, "model_usage_events", "spend_account", "text not null default ''"
            )
            self._ensure_column(
                conn,
                "model_usage_events",
                "budget_reservation_id",
                "text not null default ''",
            )
            self._ensure_column(
                conn, "model_usage_events", "thinking_enabled", "integer not null default 0"
            )
            self._ensure_column(
                conn, "model_usage_events", "reasoning_effort", "text not null default ''"
            )
            self._ensure_column(conn, "model_budget_reservations", "started_at", "text")
            self._ensure_column(
                conn, "model_budget_reservations", "lease_expires_at", "text"
            )
            # Pre-lease rows may have crossed a process boundary after their
            # provider call was emitted.  The old schema cannot prove that they
            # were unstarted, so migrate them conservatively rather than making
            # them silently free on startup.
            self._recover_expired_model_budget_reservations(conn, utc_now())


    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {row["name"] for row in conn.execute(f"pragma table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"alter table {table} add column {column} {definition}")

    def resolve_user(self, platform: Platform, platform_user_id: str) -> str:
        with self.connect() as conn:
            existing = conn.execute(
                """
                select canonical_user_id
                from platform_accounts
                where platform = ? and platform_user_id = ?
                """,
                (platform, platform_user_id),
            ).fetchone()
            if existing:
                return str(existing["canonical_user_id"])

            canonical_user_id = self.primary_user_id or (
                "geoff" if platform_user_id in {"geoff", "self", "me"} else platform_user_id
            )
            now = utc_now().isoformat()
            conn.execute(
                "insert or ignore into users (id, display_name, created_at) values (?, ?, ?)",
                (canonical_user_id, canonical_user_id, now),
            )
            conn.execute(
                """
                insert or ignore into platform_accounts
                  (platform, platform_user_id, canonical_user_id, created_at)
                values (?, ?, ?, ?)
                """,
                (platform, platform_user_id, canonical_user_id, now),
            )
            row = conn.execute(
                """
                select canonical_user_id
                from platform_accounts
                where platform = ? and platform_user_id = ?
                """,
                (platform, platform_user_id),
            ).fetchone()
            return str(row["canonical_user_id"]) if row else canonical_user_id

    def map_account(
        self, platform: Platform, platform_user_id: str, canonical_user_id: str
    ) -> None:
        now = utc_now().isoformat()
        with self.connect() as conn:
            conn.execute(
                "insert or ignore into users (id, display_name, created_at) values (?, ?, ?)",
                (canonical_user_id, canonical_user_id, now),
            )
            conn.execute(
                """
                insert or replace into platform_accounts
                  (platform, platform_user_id, canonical_user_id, created_at)
                values (?, ?, ?, ?)
                """,
                (platform, platform_user_id, canonical_user_id, now),
            )


































































    def record_tool_proposal(
        self,
        canonical_user_id: str,
        *,
        kind: str,
        risk: str,
        summary: str,
        status: str = "proposed",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into tool_proposals (
                  canonical_user_id, kind, risk, summary, status, created_at
                ) values (?, ?, ?, ?, ?, ?)
                """,
                (canonical_user_id, kind, risk, summary, status, utc_now().isoformat()),
            )

    def recent_tool_proposals(self, canonical_user_id: str, limit: int = 8) -> list[sqlite3.Row]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                select kind, risk, summary, status, created_at
                from tool_proposals
                where canonical_user_id = ?
                order by id desc
                limit ?
                """,
                (canonical_user_id, limit),
            ).fetchall()
        return list(reversed(rows))






    def platform_user_id(self, canonical_user_id: str, platform: Platform) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                select platform_user_id
                from platform_accounts
                where canonical_user_id = ? and platform = ?
                order by created_at desc
                limit 1
                """,
                (canonical_user_id, platform),
            ).fetchone()
        return str(row["platform_user_id"]) if row else None

    def upsert_memory(
        self,
        canonical_user_id: str,
        *,
        kind: str,
        content: str,
        source: str,
        confidence: float = 0.7,
    ) -> None:
        self._assert_legacy_behavior_write_allowed("upsert_memory")
        now = utc_now().isoformat()
        with self.connect() as conn:
            duplicates = conn.execute(
                """
                select content
                from memories
                where canonical_user_id = ? and kind = ?
                order by updated_at desc
                limit 30
                """,
                (canonical_user_id, kind),
            ).fetchall()
            for row in duplicates:
                existing = str(row["content"])
                if _text_similarity(existing, content) > 0.55:
                    conn.execute(
                        """
                        update memories
                        set source = ?, confidence = max(confidence, ?), updated_at = ?
                        where canonical_user_id = ? and kind = ? and content = ?
                        """,
                        (source, confidence, now, canonical_user_id, kind, existing),
                    )
                    return
            conn.execute(
                """
                insert into memories (
                  canonical_user_id, kind, content, source, confidence, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?)
                on conflict(canonical_user_id, kind, content)
                do update set
                  source = excluded.source,
                  confidence = max(memories.confidence, excluded.confidence),
                  updated_at = excluded.updated_at
                """,
                (canonical_user_id, kind, content, source, confidence, now, now),
            )




    def memories(self, canonical_user_id: str, limit: int = 12) -> list[sqlite3.Row]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                select kind, content, confidence, updated_at
                from memories
                where canonical_user_id = ?
                order by updated_at desc
                limit ?
                """,
                (canonical_user_id, limit),
            ).fetchall()
        return list(rows)











    def record_usage(self, kind: str, estimated_cny: float, *, note: str = "") -> None:
        with self.connect() as conn:
            insert_usage_event(conn, kind=kind, estimated_cny=estimated_cny, note=note)

    def usage_total(self, window: str, now: datetime) -> float:
        prefix, where = _usage_window(window, now)
        with self.connect() as conn:
            row = conn.execute(
                f"select coalesce(sum(estimated_cny), 0) as total from usage_events where {where}",
                (prefix,),
            ).fetchone()
        return float(row["total"])

    def usage_count(self, kind: str, window: str, now: datetime) -> int:
        prefix, where = _usage_window(window, now)
        with self.connect() as conn:
            row = conn.execute(
                f"select count(*) as count from usage_events where kind = ? and {where}",
                (kind, prefix),
            ).fetchone()
        return int(row["count"])

    def record_model_usage(
        self,
        *,
        purpose: str,
        model: str,
        status: str,
        latency_ms: int,
        provider: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        reasoning_tokens: int = 0,
        cache_hit_tokens: int = 0,
        cache_miss_tokens: int = 0,
        total_tokens: int = 0,
        error: str = "",
        world_id: str = "",
        turn_id: str = "",
        action_id: str = "",
        cadence: str = "",
        attempt: int = 1,
        budget_reservation_id: str = "",
        thinking_enabled: bool = False,
        reasoning_effort: str = "",
        billing_state: str = "",
    ) -> None:
        from companion_daemon.spend_account import classify_spend_account
        from companion_daemon.usage_metrics import estimate_model_cost

        created_at = utc_now().isoformat()
        priced = estimate_model_cost(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cache_hit_tokens=cache_hit_tokens,
            cache_miss_tokens=cache_miss_tokens,
            reasoning_tokens=reasoning_tokens,
            at=created_at,
        )
        estimated_cost_usd = priced.usd
        estimated_cost_cny = priced.cny
        pricing_version = priced.pricing_version
        spend_account = classify_spend_account(
            database_path=self.path, world_id=world_id
        )
        with self.connect() as conn:
            # Usage persistence and a matching preflight reservation must move
            # together. Otherwise a parallel reserve can briefly see both the
            # pending envelope and its just-completed real charge, or neither.
            conn.execute("begin immediate")
            conn.execute(
                """
                insert into model_usage_events (
                  purpose, provider, model, status, latency_ms, prompt_tokens,
                  completion_tokens, reasoning_tokens, cache_hit_tokens,
                  cache_miss_tokens, total_tokens, error, world_id, turn_id,
                  action_id, cadence, attempt, pricing_version,
                  estimated_cost_usd, estimated_cost_cny, spend_account,
                  budget_reservation_id, thinking_enabled,
                  reasoning_effort, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    purpose[:80],
                    provider[:80],
                    model[:120],
                    status[:40],
                    max(0, int(latency_ms)),
                    max(0, int(prompt_tokens)),
                    max(0, int(completion_tokens)),
                    max(0, int(reasoning_tokens)),
                    max(0, int(cache_hit_tokens)),
                    max(0, int(cache_miss_tokens)),
                    max(0, int(total_tokens)),
                    error[:2400],
                    world_id[:120],
                    turn_id[:120],
                    action_id[:120],
                    cadence[:20],
                    max(1, int(attempt)),
                    pricing_version[:80],
                    max(0.0, float(estimated_cost_usd)),
                    max(0.0, float(estimated_cost_cny)),
                    spend_account[:20],
                    budget_reservation_id[:160],
                    int(bool(thinking_enabled)),
                    reasoning_effort[:40],
                    created_at,
                ),
            )
            if budget_reservation_id:
                if status == "succeeded":
                    conn.execute(
                        """
                        update model_budget_reservations
                        set status = 'settled', actual_cny = ?, settled_at = ?
                        where reservation_id = ? and status in ('reserved', 'in_flight', 'unknown')
                        """,
                        (
                            max(0.0, float(estimated_cost_cny)),
                            utc_now().isoformat(),
                            budget_reservation_id,
                        ),
                    )
                elif billing_state == "not_billed":
                    conn.execute(
                        """
                        update model_budget_reservations
                        set status = 'released', settled_at = ?
                        where reservation_id = ? and status in ('reserved', 'in_flight', 'unknown')
                        """,
                        (utc_now().isoformat(), budget_reservation_id),
                    )
                else:
                    # A timeout, malformed response, caller cancellation, or
                    # a failed usage write cannot prove that the provider did
                    # not charge. Keep the envelope as a conservative charge
                    # until a later usage record settles it.
                    conn.execute(
                        """
                        update model_budget_reservations
                        set status = 'unknown', actual_cny = estimated_cny, settled_at = ?
                        where reservation_id = ? and status in ('reserved', 'in_flight')
                        """,
                        (utc_now().isoformat(), budget_reservation_id),
                    )

    def reserve_model_budget(
        self,
        *,
        reservation_id: str,
        estimated_cny: float,
        automatic: bool,
        monthly_budget_cny: float,
        daily_budget_cny: float,
        soft_daily_budget_cny: float,
        now: datetime,
        lease_seconds: int = 300,
    ) -> tuple[bool, str]:
        """Atomically reserve a provider-call envelope against shared spend.

        SQLite's immediate write transaction serializes the balance check with
        reservation insertion across workers and processes sharing this store.
        Completed provider usage is charged at its recorded real price; only
        still-reserved envelopes are added as provisional spend.
        """
        amount = max(0.0, float(estimated_cny))
        if not reservation_id or amount <= 0:
            return False, "invalid_model_reservation"
        observed_at = now.astimezone(UTC)
        day_prefix = observed_at.date().isoformat()
        month_prefix = observed_at.strftime("%Y-%m")
        created_at = observed_at.isoformat()
        lease_expires_at = (
            observed_at + timedelta(seconds=max(1, int(lease_seconds)))
        ).isoformat()
        with self.connect() as conn:
            conn.execute("begin immediate")
            self._recover_expired_model_budget_reservations(conn, observed_at)
            existing = conn.execute(
                "select status from model_budget_reservations where reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            if existing is not None:
                status = str(existing["status"])
                return (status in {"reserved", "settled"}, "reservation_reused")

            def spend(prefix: str) -> tuple[float, float]:
                from companion_daemon.usage_metrics import CNY_PER_USD, price_usage_row

                fixed = conn.execute(
                    """
                    select coalesce(sum(estimated_cny), 0) as total
                    from usage_events where substr(created_at, 1, ?) = ?
                    """,
                    (len(prefix), prefix),
                ).fetchone()
                model_rows = conn.execute(
                    """
                    select model, prompt_tokens, completion_tokens, reasoning_tokens,
                           cache_hit_tokens, cache_miss_tokens, estimated_cost_usd,
                           estimated_cost_cny, created_at, pricing_version
                    from model_usage_events where substr(created_at, 1, ?) = ?
                    """,
                    (len(prefix), prefix),
                ).fetchall()
                pending = conn.execute(
                    """
                    select coalesce(sum(estimated_cny), 0) as total
                    from model_budget_reservations
                    where status in ('reserved', 'in_flight', 'unknown')
                      and substr(created_at, 1, ?) = ?
                    """,
                    (len(prefix), prefix),
                ).fetchone()
                fixed_total = float(fixed["total"] or 0.0)
                model_total = 0.0
                for row in model_rows:
                    model_total += price_usage_row(row, cny_per_usd=CNY_PER_USD).cny
                return fixed_total + model_total + float(pending["total"] or 0.0), fixed_total + model_total

            daily, _ = spend(day_prefix)
            monthly, _ = spend(month_prefix)
            if monthly + amount > max(0.0, float(monthly_budget_cny)):
                return False, "monthly_budget_exceeded"
            if daily + amount > max(0.0, float(daily_budget_cny)):
                return False, "daily_budget_exceeded"
            if automatic and daily + amount > max(0.0, float(soft_daily_budget_cny)):
                return False, "soft_daily_budget_requires_manual"
            conn.execute(
                """
                insert into model_budget_reservations (
                  reservation_id, estimated_cny, actual_cny, automatic, status,
                  created_at, started_at, lease_expires_at, settled_at
                ) values (?, ?, null, ?, 'reserved', ?, null, ?, null)
                """,
                (
                    reservation_id[:160],
                    amount,
                    int(automatic),
                    created_at,
                    lease_expires_at,
                ),
            )
        return True, "reserved"

    def start_model_budget_reservation(
        self,
        reservation_id: str,
        *,
        now: datetime,
        lease_seconds: int = 300,
    ) -> bool:
        """Record that a provider request is about to be emitted.

        A process crash after this transition is not safe to release: expiry
        recovers it as ``unknown`` and keeps its full envelope charged.
        """
        if not reservation_id:
            return False
        observed_at = now.astimezone(UTC)
        with self.connect() as conn:
            conn.execute("begin immediate")
            self._recover_expired_model_budget_reservations(conn, observed_at)
            result = conn.execute(
                """
                update model_budget_reservations
                set status = 'in_flight', started_at = ?, lease_expires_at = ?
                where reservation_id = ? and status = 'reserved'
                """,
                (
                    observed_at.isoformat(),
                    (
                        observed_at + timedelta(seconds=max(1, int(lease_seconds)))
                    ).isoformat(),
                    reservation_id,
                ),
            )
        return result.rowcount == 1

    def finalize_model_budget_reservation(
        self,
        reservation_id: str,
        *,
        request_emitted: bool,
        usage_persisted: bool,
    ) -> None:
        """Close a reservation after a model scope without inventing billing facts."""
        if not reservation_id or usage_persisted:
            return
        with self.connect() as conn:
            conn.execute("begin immediate")
            if request_emitted:
                conn.execute(
                    """
                    update model_budget_reservations
                    set status = 'unknown', actual_cny = estimated_cny, settled_at = ?
                    where reservation_id = ? and status in ('reserved', 'in_flight')
                    """,
                    (utc_now().isoformat(), reservation_id),
                )
            else:
                conn.execute(
                    """
                    update model_budget_reservations
                    set status = 'released', settled_at = ?
                    where reservation_id = ? and status in ('reserved', 'in_flight')
                    """,
                    (utc_now().isoformat(), reservation_id),
                )

    def release_model_budget_reservation(self, reservation_id: str) -> None:
        """Idempotently return a preflight envelope when no provider usage exists."""
        if not reservation_id:
            return
        with self.connect() as conn:
            conn.execute("begin immediate")
            conn.execute(
                """
                update model_budget_reservations
                set status = 'released', settled_at = ?
                where reservation_id = ? and status = 'reserved'
                """,
                (utc_now().isoformat(), reservation_id),
            )

    def pending_model_budget_reservation_total(self, window: str, now: datetime) -> float:
        if window == "day":
            prefix = now.date().isoformat()
        elif window == "month":
            prefix = now.strftime("%Y-%m")
        else:
            raise ValueError(f"Unsupported usage window: {window}")
        with self.connect() as conn:
            conn.execute("begin immediate")
            self._recover_expired_model_budget_reservations(conn, now.astimezone(UTC))
            row = conn.execute(
                """
                select coalesce(sum(estimated_cny), 0) as total
                from model_budget_reservations
                where status in ('reserved', 'in_flight', 'unknown')
                  and substr(created_at, 1, ?) = ?
                """,
                (len(prefix), prefix),
            ).fetchone()
        return float(row["total"] or 0.0)

    @staticmethod
    def _recover_expired_model_budget_reservations(
        conn: sqlite3.Connection, observed_at: datetime
    ) -> None:
        """Recover only preflight work that is provably unstarted.

        ``in_flight`` and legacy rows lack a durable provider result, so they
        become conservative unknown charges rather than becoming available
        budget after a restart.
        """
        now_iso = observed_at.astimezone(UTC).isoformat()
        conn.execute(
            """
            update model_budget_reservations
            set status = 'released', settled_at = ?
            where status = 'reserved' and lease_expires_at is not null
              and lease_expires_at <= ?
            """,
            (now_iso, now_iso),
        )
        conn.execute(
            """
            update model_budget_reservations
            set status = 'unknown', actual_cny = estimated_cny, settled_at = ?
            where status = 'in_flight' and lease_expires_at is not null
              and lease_expires_at <= ?
            """,
            (now_iso, now_iso),
        )
        conn.execute(
            """
            update model_budget_reservations
            set status = 'unknown', actual_cny = estimated_cny, settled_at = ?
            where status = 'reserved' and lease_expires_at is null
            """,
            (now_iso,),
        )

    def recent_model_usage_samples(
        self,
        *,
        model: str,
        purpose: str | None = None,
        cadence: str | None = None,
        limit: int = 64,
    ) -> list[dict[str, int]]:
        """Return successful recent calls for a route-specific preflight reserve.

        The values are deliberately small and content-free: callers need only
        observed token envelopes, never prompt or reply text.
        """
        clauses = ["status = 'succeeded'", "model = ?"]
        args: list[object] = [model]
        if purpose is not None:
            clauses.append("purpose = ?")
            args.append(purpose)
        if cadence is not None:
            clauses.append("cadence = ?")
            args.append(cadence)
        args.append(max(1, int(limit)))
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                select completion_tokens, reasoning_tokens
                from model_usage_events
                where {' and '.join(clauses)}
                order by id desc
                limit ?
                """,
                args,
            ).fetchall()
        return [
            {
                "completion_tokens": int(row["completion_tokens"] or 0),
                "reasoning_tokens": int(row["reasoning_tokens"] or 0),
            }
            for row in rows
        ]

    def model_usage_summary(self, window: str, now: datetime) -> dict[str, dict[str, int]]:
        if window == "day":
            prefix = now.date().isoformat()
        elif window == "month":
            prefix = now.strftime("%Y-%m")
        else:
            raise ValueError(f"Unsupported usage window: {window}")
        with self.connect() as conn:
            rows = conn.execute(
                """
                select purpose, count(*) as calls,
                       sum(prompt_tokens) as prompt_tokens,
                       sum(completion_tokens) as completion_tokens,
                       sum(reasoning_tokens) as reasoning_tokens,
                       sum(cache_hit_tokens) as cache_hit_tokens,
                       sum(cache_miss_tokens) as cache_miss_tokens,
                       sum(total_tokens) as total_tokens,
                       sum(latency_ms) as latency_ms
                from model_usage_events
                where substr(created_at, 1, ?) = ?
                group by purpose
                order by purpose
                """,
                (len(prefix), prefix),
            ).fetchall()
        keys = (
            "calls",
            "prompt_tokens",
            "completion_tokens",
            "reasoning_tokens",
            "cache_hit_tokens",
            "cache_miss_tokens",
            "total_tokens",
            "latency_ms",
        )
        summary = {str(row["purpose"]): {key: int(row[key] or 0) for key in keys} for row in rows}
        summary["_total"] = {key: sum(item[key] for item in summary.values()) for key in keys}
        return summary

    def model_usage_report(
        self,
        window: str,
        now: datetime,
        *,
        cny_per_usd: float = 7.2,
    ) -> dict[str, object]:
        """Aggregate persisted provider usage by purpose/cadence/model and turn."""
        from companion_daemon.usage_metrics import aggregate_usage_rows

        if window == "day":
            prefix = now.date().isoformat()
        elif window == "month":
            prefix = now.strftime("%Y-%m")
        else:
            raise ValueError(f"Unsupported usage window: {window}")
        with self.connect() as conn:
            rows = conn.execute(
                """
                select * from model_usage_events
                where substr(created_at, 1, ?) = ?
                order by id
                """,
                (len(prefix), prefix),
            ).fetchall()
        groups: dict[str, list[sqlite3.Row]] = {}
        routes: dict[str, list[sqlite3.Row]] = {}
        turns: dict[str, list[sqlite3.Row]] = {}
        turn_routes: dict[str, dict[str, list[sqlite3.Row]]] = {}
        for row in rows:
            group_key = "|".join((str(row["purpose"]), str(row["cadence"]), str(row["model"])))
            groups.setdefault(group_key, []).append(row)
            route_key = "|".join(
                (
                    str(row["model"]),
                    f"thinking={int(bool(row['thinking_enabled']))}",
                    str(row["reasoning_effort"] or "default"),
                )
            )
            routes.setdefault(route_key, []).append(row)
            turn_id = str(row["turn_id"] or "")
            if turn_id:
                turns.setdefault(turn_id, []).append(row)
                turn_routes.setdefault(turn_id, {}).setdefault(route_key, []).append(row)
        return {
            "window": window,
            "currency_rate": {"cny_per_usd": max(0.0, float(cny_per_usd))},
            "total": aggregate_usage_rows(rows, cny_per_usd=cny_per_usd),
            "groups": {
                key: aggregate_usage_rows(items, cny_per_usd=cny_per_usd)
                for key, items in groups.items()
            },
            "routes": {
                key: aggregate_usage_rows(items, cny_per_usd=cny_per_usd)
                for key, items in routes.items()
            },
            "turns": {
                key: aggregate_usage_rows(items, cny_per_usd=cny_per_usd)
                for key, items in turns.items()
            },
            "turn_routes": {
                turn_id: {
                    route_key: aggregate_usage_rows(items, cny_per_usd=cny_per_usd)
                    for route_key, items in routes.items()
                }
                for turn_id, routes in turn_routes.items()
            },
        }





def _text_similarity(left: str, right: str) -> float:
    left = left.lower().strip()[:80]
    right = right.lower().strip()[:80]
    if not left or not right:
        return 0.0
    return _longest_common_substring(left, right) / max(len(left), len(right))


def _longest_common_substring(left: str, right: str) -> int:
    previous = [0] * (len(right) + 1)
    best = 0
    for left_char in left:
        current = [0]
        for index, right_char in enumerate(right, start=1):
            value = previous[index - 1] + 1 if left_char == right_char else 0
            current.append(value)
            best = max(best, value)
        previous = current
    return best
