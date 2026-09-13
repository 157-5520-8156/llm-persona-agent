"""Small durable journal for the existing day_open opportunity.

The journal coordinates attempts; it grants no Plan or character authority.
Accepted Plan authority remains the original role/model/Clock proof.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
from typing import Literal
from weakref import WeakKeyDictionary

from pydantic import Field, model_validator

from .character_interior.contracts import InteriorOpportunity
from .schema_core import FrozenModel
from .activity_continuation_source import ActivityCompletionSource
from .sqlite_coordination import configure_shared_sqlite_connection, sqlite_write_lock


class DayOpenAttempt(FrozenModel):
    ordinal: int = Field(ge=1, le=3)
    opportunity: InteriorOpportunity
    failure_code: str | None = None
    next_retry_at: datetime | None = None


class DayOpenJournal(FrozenModel):
    contract: Literal["day-open-opportunity-journal.1", "day-open-opportunity-journal.2"] = "day-open-opportunity-journal.1"
    actor_ref: str
    day_key: str
    completion_source: ActivityCompletionSource | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    attempts: tuple[DayOpenAttempt, ...] = Field(min_length=1, max_length=3)
    terminal: bool = False
    terminal_reason: str | None = None
    proposal_id: str | None = None

    @model_validator(mode="after")
    def explicit_continuation_version(self):
        if self.contract.endswith(".2") != (self.completion_source is not None):
            raise ValueError("journal version disagrees with continuation source")
        return self

    @property
    def storage_key(self):
        # Keep old daily rows byte-compatible. The existing column stores a
        # coordination key; continuation keys are disjoint from ISO dates.
        return ("continuation:" + self.completion_source.event_ref
                if self.completion_source is not None else self.day_key)


_SCHEMA = """CREATE TABLE IF NOT EXISTS world_v2_day_open_opportunities (
world_id TEXT NOT NULL, actor_ref TEXT NOT NULL, day_key TEXT NOT NULL,
body TEXT NOT NULL, PRIMARY KEY(world_id,actor_ref,day_key))"""


class DayOpenOpportunityStore:
    def __init__(self, ledger):
        self._world = ledger.world_id
        path = getattr(ledger, "_database_path", None)
        self._path = Path(path) if path is not None else None
        self._memory = {}
        if self._path is not None:
            with sqlite_write_lock(self._path), closing(self._connect()) as connection:
                connection.execute(_SCHEMA)

    def _connect(self):
        connection = sqlite3.connect(self._path, isolation_level=None)
        configure_shared_sqlite_connection(connection)
        return connection

    def records(self, actor_ref):
        if self._path is None:
            values = [value for (actor, _), value in self._memory.items() if actor == actor_ref]
        else:
            with closing(self._connect()) as connection:
                values = [
                    row[0]
                    for row in connection.execute(
                        "SELECT body FROM world_v2_day_open_opportunities WHERE world_id=? AND actor_ref=? ORDER BY day_key",
                        (self._world, actor_ref),
                    )
                ]
        return tuple(DayOpenJournal.model_validate_json(value) for value in values)

    def save(self, value, *, expected=None):
        """Exact compare-and-swap; a second worker cannot replace an original pin."""
        body = value.model_dump_json()
        old = expected.model_dump_json() if expected is not None else None
        key = (value.actor_ref, value.storage_key)
        if self._path is None:
            if self._memory.get(key) != old:
                raise ValueError("day_open.journal_conflict")
            self._memory[key] = body
            return value
        with sqlite_write_lock(self._path), closing(self._connect()) as connection:
            if expected is None:
                written = connection.execute(
                    "INSERT OR IGNORE INTO world_v2_day_open_opportunities VALUES(?,?,?,?)",
                    (self._world, *key, body),
                ).rowcount
            else:
                written = connection.execute(
                    "UPDATE world_v2_day_open_opportunities SET body=? WHERE world_id=? AND actor_ref=? AND day_key=? AND body=?",
                    (body, self._world, *key, old),
                ).rowcount
            if written != 1:
                raise ValueError("day_open.journal_conflict")
        return value


_STORES = WeakKeyDictionary()


def day_open_store_for_ledger(ledger):
    store = _STORES.get(ledger)
    if store is None:
        store = DayOpenOpportunityStore(ledger)
        _STORES[ledger] = store
    return store


def day_open_retry_due(ledger, *, actor_ref):
    try:
        store = _STORES.get(ledger)
    except TypeError:
        store = None
    if store is None:
        return None
    return min(
        (
            row.attempts[-1].next_retry_at or row.attempts[-1].opportunity.logical_time
            for row in store.records(actor_ref)
            if not row.terminal
        ),
        default=None,
    )


def activity_continuation_due(ledger, *, actor_ref, projection):
    """A new completion needs a fresh Clock, without installing a second worker."""
    from .activity_continuation_source import latest_completion_source

    try:
        store = _STORES.get(ledger)
    except TypeError:
        store = None
    if store is None or projection.logical_time is None:
        return None
    records = store.records(actor_ref)
    if any(not row.terminal for row in records):
        return None  # Existing retries retain their own pin and deadline.
    source = latest_completion_source(projection, actor_ref=actor_ref)
    if source is None or any(
        row.completion_source is not None and row.completion_source.event_ref == source.event_ref
        for row in records
    ):
        return None
    return projection.logical_time + timedelta(seconds=1)
