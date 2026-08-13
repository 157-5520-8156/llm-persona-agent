"""Durable, disposable claim/lease/due overlay for Life Ecology.

Claim, lease, retry and silent (no-new-fact) completions live here rather than
on the immutable ledger.  Semantic outcomes still finish through the ledger
TriggerProcess path so historical replay and schedule projection stay intact.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Literal
from weakref import WeakKeyDictionary

import sqlite3
from pathlib import Path

from .sqlite_coordination import configure_shared_sqlite_connection, sqlite_write_lock


LifeEcologyLeaseState = Literal["claimed", "completed"]

SILENT_LIFE_ECOLOGY_OUTCOMES = frozenset(
    {
        "idle",
        "author_idle",
        "author_no_opening",
        "life_development_no_op",
        "cooldown",
    }
)


@dataclass(frozen=True, slots=True)
class LifeEcologyLeaseRecord:
    world_id: str
    trigger_id: str
    wake_event_ref: str
    catalog_version: str
    owner_id: str
    attempt_id: str
    attempt_ordinal: int
    acquired_at: datetime
    expires_at: datetime
    state: LifeEcologyLeaseState
    outcome: str | None = None


@dataclass(frozen=True, slots=True)
class LifeEcologyScheduleOverlay:
    world_id: str
    next_consideration_at: datetime | None
    last_silent_outcome: str | None
    last_completed_at: datetime | None


class LifeEcologyLeaseStore:
    def get(self, trigger_id: str) -> LifeEcologyLeaseRecord | None:
        raise NotImplementedError

    def overlay(self) -> LifeEcologyScheduleOverlay | None:
        raise NotImplementedError

    def occupy(
        self,
        *,
        record: LifeEcologyLeaseRecord,
        logical_time: datetime,
    ) -> tuple[str, LifeEcologyLeaseRecord]:
        raise NotImplementedError

    def complete(
        self,
        *,
        trigger_id: str,
        outcome: str,
        completed_at: datetime,
        next_consideration_at: datetime | None,
    ) -> None:
        raise NotImplementedError


class InMemoryLifeEcologyLeaseStore(LifeEcologyLeaseStore):
    def __init__(self, *, world_id: str) -> None:
        self._world_id = world_id
        self._lock = RLock()
        self._leases: dict[str, LifeEcologyLeaseRecord] = {}
        self._overlay: LifeEcologyScheduleOverlay | None = None

    def get(self, trigger_id: str) -> LifeEcologyLeaseRecord | None:
        with self._lock:
            return self._leases.get(trigger_id)

    def overlay(self) -> LifeEcologyScheduleOverlay | None:
        with self._lock:
            return self._overlay

    def occupy(
        self,
        *,
        record: LifeEcologyLeaseRecord,
        logical_time: datetime,
    ) -> tuple[str, LifeEcologyLeaseRecord]:
        if record.world_id != self._world_id:
            raise ValueError("life ecology lease belongs to another world")
        with self._lock:
            current = self._leases.get(record.trigger_id)
            if current is not None and current.state == "completed":
                return "completed", current
            if (
                current is not None
                and current.state == "claimed"
                and logical_time <= current.expires_at
            ):
                return "joined", current
            self._leases[record.trigger_id] = record
            return "owned", record

    def complete(
        self,
        *,
        trigger_id: str,
        outcome: str,
        completed_at: datetime,
        next_consideration_at: datetime | None,
    ) -> None:
        with self._lock:
            current = self._leases.get(trigger_id)
            if current is None:
                raise ValueError("life ecology trigger is unavailable")
            self._leases[trigger_id] = LifeEcologyLeaseRecord(
                world_id=current.world_id,
                trigger_id=current.trigger_id,
                wake_event_ref=current.wake_event_ref,
                catalog_version=current.catalog_version,
                owner_id=current.owner_id,
                attempt_id=current.attempt_id,
                attempt_ordinal=current.attempt_ordinal,
                acquired_at=current.acquired_at,
                expires_at=current.expires_at,
                state="completed",
                outcome=outcome,
            )
            if outcome != "cooldown":
                self._overlay = LifeEcologyScheduleOverlay(
                    world_id=self._world_id,
                    next_consideration_at=next_consideration_at,
                    last_silent_outcome=outcome,
                    last_completed_at=completed_at,
                )


_LEASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS world_v2_life_ecology_leases (
    world_id TEXT NOT NULL,
    trigger_id TEXT NOT NULL,
    wake_event_ref TEXT NOT NULL,
    catalog_version TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    attempt_ordinal INTEGER NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    state TEXT NOT NULL,
    outcome TEXT,
    PRIMARY KEY (world_id, trigger_id)
)
"""

_OVERLAY_SCHEMA = """
CREATE TABLE IF NOT EXISTS world_v2_life_ecology_schedule_overlay (
    world_id TEXT PRIMARY KEY,
    next_consideration_at TEXT,
    last_silent_outcome TEXT,
    last_completed_at TEXT
)
"""


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


class SQLiteLifeEcologyLeaseStore(LifeEcologyLeaseStore):
    def __init__(self, *, path: str | Path, world_id: str) -> None:
        if not world_id:
            raise ValueError("life ecology lease store requires world_id")
        self._world_id = world_id
        self._path = Path(path).expanduser().absolute()
        self._lock = RLock()
        self._database_write_lock = sqlite_write_lock(self._path)
        self._connection = sqlite3.connect(
            self._path, isolation_level=None, check_same_thread=False
        )
        with self._database_write_lock:
            configure_shared_sqlite_connection(self._connection)
            self._connection.execute(_LEASE_SCHEMA)
            self._connection.execute(_OVERLAY_SCHEMA)

    def get(self, trigger_id: str) -> LifeEcologyLeaseRecord | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT world_id, trigger_id, wake_event_ref, catalog_version,
                       owner_id, attempt_id, attempt_ordinal, acquired_at,
                       expires_at, state, outcome
                FROM world_v2_life_ecology_leases
                WHERE world_id = ? AND trigger_id = ?
                """,
                (self._world_id, trigger_id),
            ).fetchone()
        if row is None:
            return None
        return LifeEcologyLeaseRecord(
            world_id=str(row[0]),
            trigger_id=str(row[1]),
            wake_event_ref=str(row[2]),
            catalog_version=str(row[3]),
            owner_id=str(row[4]),
            attempt_id=str(row[5]),
            attempt_ordinal=int(row[6]),
            acquired_at=_parse_datetime(str(row[7])),
            expires_at=_parse_datetime(str(row[8])),
            state=str(row[9]),  # type: ignore[arg-type]
            outcome=None if row[10] is None else str(row[10]),
        )

    def overlay(self) -> LifeEcologyScheduleOverlay | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT world_id, next_consideration_at, last_silent_outcome,
                       last_completed_at
                FROM world_v2_life_ecology_schedule_overlay
                WHERE world_id = ?
                """,
                (self._world_id,),
            ).fetchone()
        if row is None:
            return None
        due = None if row[1] is None else _parse_datetime(str(row[1]))
        completed = None if row[3] is None else _parse_datetime(str(row[3]))
        return LifeEcologyScheduleOverlay(
            world_id=str(row[0]),
            next_consideration_at=due,
            last_silent_outcome=None if row[2] is None else str(row[2]),
            last_completed_at=completed,
        )

    def occupy(
        self,
        *,
        record: LifeEcologyLeaseRecord,
        logical_time: datetime,
    ) -> tuple[str, LifeEcologyLeaseRecord]:
        if record.world_id != self._world_id:
            raise ValueError("life ecology lease belongs to another world")
        with self._database_write_lock, self._lock:
            current = self.get(record.trigger_id)
            if current is not None and current.state == "completed":
                return "completed", current
            if (
                current is not None
                and current.state == "claimed"
                and logical_time <= current.expires_at
            ):
                return "joined", current
            self._connection.execute(
                """
                INSERT INTO world_v2_life_ecology_leases (
                    world_id, trigger_id, wake_event_ref, catalog_version,
                    owner_id, attempt_id, attempt_ordinal, acquired_at,
                    expires_at, state, outcome
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(world_id, trigger_id) DO UPDATE SET
                    owner_id = excluded.owner_id,
                    attempt_id = excluded.attempt_id,
                    attempt_ordinal = excluded.attempt_ordinal,
                    acquired_at = excluded.acquired_at,
                    expires_at = excluded.expires_at,
                    state = excluded.state,
                    outcome = excluded.outcome
                """,
                (
                    record.world_id,
                    record.trigger_id,
                    record.wake_event_ref,
                    record.catalog_version,
                    record.owner_id,
                    record.attempt_id,
                    record.attempt_ordinal,
                    record.acquired_at.isoformat(),
                    record.expires_at.isoformat(),
                    record.state,
                    record.outcome,
                ),
            )
            return "owned", record

    def complete(
        self,
        *,
        trigger_id: str,
        outcome: str,
        completed_at: datetime,
        next_consideration_at: datetime | None,
    ) -> None:
        with self._database_write_lock, self._lock:
            current = self._connection.execute(
                """
                SELECT trigger_id FROM world_v2_life_ecology_leases
                WHERE world_id = ? AND trigger_id = ?
                """,
                (self._world_id, trigger_id),
            ).fetchone()
            if current is None:
                raise ValueError("life ecology trigger is unavailable")
            self._connection.execute(
                """
                UPDATE world_v2_life_ecology_leases
                SET state = 'completed', outcome = ?
                WHERE world_id = ? AND trigger_id = ?
                """,
                (outcome, self._world_id, trigger_id),
            )
            if outcome != "cooldown":
                self._connection.execute(
                    """
                    INSERT INTO world_v2_life_ecology_schedule_overlay (
                        world_id, next_consideration_at, last_silent_outcome,
                        last_completed_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(world_id) DO UPDATE SET
                        next_consideration_at = excluded.next_consideration_at,
                        last_silent_outcome = excluded.last_silent_outcome,
                        last_completed_at = excluded.last_completed_at
                    """,
                    (
                        self._world_id,
                        None
                        if next_consideration_at is None
                        else next_consideration_at.isoformat(),
                        outcome,
                        completed_at.isoformat(),
                    ),
                )


_MEMORY_STORES: WeakKeyDictionary[object, InMemoryLifeEcologyLeaseStore] = WeakKeyDictionary()


def lease_store_for_ledger(ledger: object) -> LifeEcologyLeaseStore:
    world_id = str(getattr(ledger, "world_id"))
    path = getattr(ledger, "_database_path", None)
    if path is not None:
        return SQLiteLifeEcologyLeaseStore(path=path, world_id=world_id)
    existing = _MEMORY_STORES.get(ledger)
    if existing is None:
        existing = InMemoryLifeEcologyLeaseStore(world_id=world_id)
        _MEMORY_STORES[ledger] = existing
    return existing
