"""Occasion identity: when the character may consider, and only once.

This is a system timing seam, not a behavior script.  It does not choose what
she says, feels, or does.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Literal
from weakref import WeakKeyDictionary

import sqlite3

from .schema_core import FrozenModel
from .sqlite_coordination import configure_shared_sqlite_connection, sqlite_write_lock


OCCASION_KINDS = (
    "user_message",
    "quiet_gap",
    "unsettled_feeling",
    "life_beat",
    "day_open",
)
OccasionKind = Literal[
    "user_message",
    "quiet_gap",
    "unsettled_feeling",
    "life_beat",
    "day_open",
]

PURPOSE_OCCASION_KIND: dict[str, OccasionKind] = {
    "inbound_turn": "user_message",
    "proactive_contact": "quiet_gap",
    "activity_lifecycle_choice": "day_open",
}

_DEFAULT_TTL = {
    "user_message": timedelta(days=7),
    "quiet_gap": timedelta(seconds=43_200),
    "unsettled_feeling": timedelta(hours=24),
    "life_beat": timedelta(hours=24),
    "day_open": timedelta(hours=36),
}

_SPEND_SCHEMA = """
CREATE TABLE IF NOT EXISTS world_v2_occasion_spends (
    world_id TEXT NOT NULL,
    occasion_id TEXT NOT NULL,
    spent_at TEXT NOT NULL,
    PRIMARY KEY (world_id, occasion_id)
)
"""


class OccasionAlreadyConsidered(ValueError):
    """G2: the same Occasion identity already spent its one consider()."""


class OccasionExpired(ValueError):
    """G7: a stale Occasion must be dropped, not backfilled after downtime."""


class OccasionIdentity(FrozenModel):
    kind: OccasionKind
    source_event_ref: str
    merge_key: str
    expires_at: datetime | None = None

    @property
    def occasion_id(self) -> str:
        return f"occasion:{self.kind}:{self.merge_key}"


class OccasionSpendStore:
    def spent(self, occasion_id: str) -> bool:
        raise NotImplementedError

    def mark(self, occasion_id: str) -> None:
        raise NotImplementedError


class InMemoryOccasionSpendStore(OccasionSpendStore):
    def __init__(self, *, world_id: str = "world:test") -> None:
        self._world_id = world_id
        self._lock = RLock()
        self._spent: set[str] = set()

    def spent(self, occasion_id: str) -> bool:
        with self._lock:
            return occasion_id in self._spent

    def mark(self, occasion_id: str) -> None:
        with self._lock:
            self._spent.add(occasion_id)


class SQLiteOccasionSpendStore(OccasionSpendStore):
    def __init__(self, *, path: str | Path, world_id: str) -> None:
        if not world_id:
            raise ValueError("occasion spend store requires world_id")
        self._world_id = world_id
        self._path = Path(path).expanduser().absolute()
        self._lock = RLock()
        self._database_write_lock = sqlite_write_lock(self._path)
        self._connection = sqlite3.connect(
            self._path, isolation_level=None, check_same_thread=False
        )
        with self._database_write_lock:
            configure_shared_sqlite_connection(self._connection)
            self._connection.execute(_SPEND_SCHEMA)

    def spent(self, occasion_id: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT 1 FROM world_v2_occasion_spends
                WHERE world_id = ? AND occasion_id = ?
                """,
                (self._world_id, occasion_id),
            ).fetchone()
            return row is not None

    def mark(self, occasion_id: str) -> None:
        with self._lock, self._database_write_lock:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO world_v2_occasion_spends
                (world_id, occasion_id, spent_at)
                VALUES (?, ?, datetime('now'))
                """,
                (self._world_id, occasion_id),
            )


_MEMORY_STORES: WeakKeyDictionary[object, InMemoryOccasionSpendStore] = WeakKeyDictionary()


def occasion_spend_store_for_ledger(ledger: object) -> OccasionSpendStore:
    world_id = str(getattr(ledger, "world_id"))
    path = getattr(ledger, "_database_path", None)
    if path is not None:
        return SQLiteOccasionSpendStore(path=path, world_id=world_id)
    existing = _MEMORY_STORES.get(ledger)
    if existing is None:
        existing = InMemoryOccasionSpendStore(world_id=world_id)
        _MEMORY_STORES[ledger] = existing
    return existing


class OccasionConsiderGate:
    """One-consider ledger. Optional sidecar survives process restart."""

    def __init__(self, store: OccasionSpendStore | None = None) -> None:
        self._spent: set[str] = set()
        self._store = store

    def admit(self, occasion_id: str) -> None:
        if not occasion_id:
            raise ValueError("occasion identity is required")
        if occasion_id in self._spent or (
            self._store is not None and self._store.spent(occasion_id)
        ):
            raise OccasionAlreadyConsidered(occasion_id)

    def mark_spent(self, occasion_id: str) -> None:
        if not occasion_id:
            raise ValueError("occasion identity is required")
        self._spent.add(occasion_id)
        if self._store is not None:
            self._store.mark(occasion_id)

    def already_spent(self, occasion_id: str) -> bool:
        if occasion_id in self._spent:
            return True
        return self._store is not None and self._store.spent(occasion_id)


def occasion_is_expired(
    *,
    now: datetime,
    expires_at: datetime | None,
) -> bool:
    return expires_at is not None and now >= expires_at


def quiet_gap_expires_at(*, created_at: datetime, expiry_seconds: int) -> datetime:
    return created_at + timedelta(seconds=expiry_seconds)


def mint_occasion(
    *,
    kind: OccasionKind,
    source_event_ref: str,
    created_at: datetime,
    merge_key: str | None = None,
    ttl: timedelta | None = None,
    expires_at: datetime | None = None,
) -> OccasionIdentity:
    if kind not in OCCASION_KINDS:
        raise ValueError("unknown Occasion kind")
    if not source_event_ref:
        raise ValueError("Occasion source_event_ref is required")
    key = merge_key or source_event_ref
    if not key:
        raise ValueError("Occasion merge_key is required")
    expiry = expires_at if expires_at is not None else created_at + (ttl or _DEFAULT_TTL[kind])
    return OccasionIdentity(
        kind=kind,
        source_event_ref=source_event_ref,
        merge_key=key,
        expires_at=expiry,
    )


def mint_user_message(
    *,
    source_event_ref: str,
    created_at: datetime,
    merge_key: str | None = None,
) -> OccasionIdentity:
    return mint_occasion(
        kind="user_message",
        source_event_ref=source_event_ref,
        created_at=created_at,
        merge_key=merge_key,
    )


def mint_quiet_gap(
    *,
    source_event_ref: str,
    created_at: datetime,
    merge_key: str | None = None,
    expiry_seconds: int | None = None,
    ttl: timedelta | None = None,
) -> OccasionIdentity:
    resolved = ttl
    if resolved is None and expiry_seconds is not None:
        resolved = timedelta(seconds=expiry_seconds)
    return mint_occasion(
        kind="quiet_gap",
        source_event_ref=source_event_ref,
        created_at=created_at,
        merge_key=merge_key,
        ttl=resolved,
    )


def mint_unsettled_feeling(
    *,
    source_event_ref: str,
    created_at: datetime,
    merge_key: str | None = None,
) -> OccasionIdentity:
    return mint_occasion(
        kind="unsettled_feeling",
        source_event_ref=source_event_ref,
        created_at=created_at,
        merge_key=merge_key,
    )


def mint_life_beat(
    *,
    source_event_ref: str,
    created_at: datetime,
    merge_key: str | None = None,
) -> OccasionIdentity:
    return mint_occasion(
        kind="life_beat",
        source_event_ref=source_event_ref,
        created_at=created_at,
        merge_key=merge_key,
    )


def mint_day_open(
    *,
    source_event_ref: str,
    created_at: datetime,
    merge_key: str,
) -> OccasionIdentity:
    return mint_occasion(
        kind="day_open",
        source_event_ref=source_event_ref,
        created_at=created_at,
        merge_key=merge_key,
    )


def newly_accepted_head_refs(
    committed_world_event_refs: tuple[object, ...] | list[object],
    *,
    event_type: str,
) -> frozenset[str]:
    """G7: derive candidates from the head event only, never a historical table."""

    if not committed_world_event_refs:
        return frozenset()
    head = committed_world_event_refs[-1]
    if getattr(head, "event_type", None) != event_type:
        return frozenset()
    event_id = getattr(head, "event_id", None)
    if not isinstance(event_id, str) or not event_id:
        return frozenset()
    return frozenset({event_id})
