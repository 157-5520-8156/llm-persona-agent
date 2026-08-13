"""Durable once-per-local-day Occasion marks. Timing only, not behavior."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import RLock
from weakref import WeakKeyDictionary
from zoneinfo import ZoneInfo

import sqlite3

from .sqlite_coordination import configure_shared_sqlite_connection, sqlite_write_lock


DEFAULT_LOCAL_TIMEZONE = "Asia/Shanghai"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS world_v2_daily_occasions (
    world_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    day_key TEXT NOT NULL,
    PRIMARY KEY (world_id, kind, day_key)
)
"""


def local_day_key(instant: datetime, timezone: ZoneInfo | str = DEFAULT_LOCAL_TIMEZONE) -> str:
    zone = timezone if isinstance(timezone, ZoneInfo) else ZoneInfo(timezone)
    return instant.astimezone(zone).date().isoformat()


class DailyOccasionStore:
    def spent(self, kind: str, day_key: str) -> bool:
        raise NotImplementedError

    def mark(self, kind: str, day_key: str) -> None:
        raise NotImplementedError


class InMemoryDailyOccasionStore(DailyOccasionStore):
    def __init__(self, *, world_id: str = "world:test") -> None:
        self._world_id = world_id
        self._lock = RLock()
        self._spent: set[tuple[str, str]] = set()

    def spent(self, kind: str, day_key: str) -> bool:
        with self._lock:
            return (kind, day_key) in self._spent

    def mark(self, kind: str, day_key: str) -> None:
        with self._lock:
            self._spent.add((kind, day_key))


class SQLiteDailyOccasionStore(DailyOccasionStore):
    def __init__(self, *, path: str | Path, world_id: str) -> None:
        if not world_id:
            raise ValueError("daily occasion store requires world_id")
        self._world_id = world_id
        self._path = Path(path).expanduser().absolute()
        self._lock = RLock()
        self._database_write_lock = sqlite_write_lock(self._path)
        self._connection = sqlite3.connect(
            self._path, isolation_level=None, check_same_thread=False
        )
        with self._database_write_lock:
            configure_shared_sqlite_connection(self._connection)
            self._connection.execute(_SCHEMA)

    def spent(self, kind: str, day_key: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT 1 FROM world_v2_daily_occasions
                WHERE world_id = ? AND kind = ? AND day_key = ?
                """,
                (self._world_id, kind, day_key),
            ).fetchone()
            return row is not None

    def mark(self, kind: str, day_key: str) -> None:
        with self._lock, self._database_write_lock:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO world_v2_daily_occasions (world_id, kind, day_key)
                VALUES (?, ?, ?)
                """,
                (self._world_id, kind, day_key),
            )


_MEMORY_STORES: WeakKeyDictionary[object, InMemoryDailyOccasionStore] = WeakKeyDictionary()


def daily_occasion_store_for_ledger(ledger: object) -> DailyOccasionStore:
    world_id = str(getattr(ledger, "world_id"))
    path = getattr(ledger, "_database_path", None)
    if path is not None:
        return SQLiteDailyOccasionStore(path=path, world_id=world_id)
    existing = _MEMORY_STORES.get(ledger)
    if existing is None:
        existing = InMemoryDailyOccasionStore(world_id=world_id)
        _MEMORY_STORES[ledger] = existing
    return existing
