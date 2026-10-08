"""Durable private outlook revisions and operations, separate from World facts."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from .sqlite_coordination import configure_shared_sqlite_connection, sqlite_write_lock
from .world_outlook import WorldOutlookDraft, digest


class SQLiteWorldOutlookStore:
    def __init__(self, *, path: str | Path, world_id: str, actor_ref: str):
        self.world_id, self.actor_ref = world_id, actor_ref
        self._lock = sqlite_write_lock(path)
        self._db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        with self._lock:
            configure_shared_sqlite_connection(self._db)
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS world_outlook_revisions(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, world_id TEXT NOT NULL,
                    actor_ref TEXT NOT NULL, revision TEXT NOT NULL UNIQUE,
                    parent_revision TEXT, payload TEXT NOT NULL, evidence TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS world_outlook_owner ON world_outlook_revisions(world_id,actor_ref,sequence);
                CREATE TABLE IF NOT EXISTS world_outlook_operations(
                    world_id TEXT NOT NULL, actor_ref TEXT NOT NULL, operation_id TEXT NOT NULL,
                    kind TEXT NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(world_id,actor_ref,operation_id));
                CREATE TABLE IF NOT EXISTS world_outlook_attempts(
                    world_id TEXT NOT NULL, actor_ref TEXT NOT NULL, operation_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL, payload TEXT NOT NULL, payload_hash TEXT NOT NULL,
                    PRIMARY KEY(world_id,actor_ref,operation_id,ordinal));
            """)

    def _scope(self):
        return self.world_id, self.actor_ref

    def head(self, *, as_of=None):
        with self._lock:
            rows = self._db.execute(
                "SELECT revision,payload,evidence FROM world_outlook_revisions WHERE world_id=? AND actor_ref=? ORDER BY sequence DESC",
                self._scope(),
            ).fetchall()
        for row in rows:
            draft = WorldOutlookDraft.model_validate_json(row[1])
            evidence = json.loads(row[2])
            if digest({"draft": draft.model_dump(mode="json"), "evidence": evidence}) != row[0]:
                raise ValueError("outlook revision content binding mismatch")
            if (draft.world_id, draft.actor_ref) != self._scope():
                raise ValueError("outlook revision owner mismatch")
            if as_of is not None and any(evidence["cursor"][k] > v for k, v in as_of.items()):
                continue
            return row[0], draft, evidence
        return None

    def install(self, draft: WorldOutlookDraft, *, evidence: dict):
        revision = digest({"draft": draft.model_dump(mode="json"), "evidence": evidence})
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                previous = self.head()
                if previous and previous[0] == revision:
                    self._db.execute("COMMIT")
                    return revision
                if draft.parent_revision != (previous[0] if previous else None):
                    raise ValueError("outlook predecessor changed")
                if (draft.world_id, draft.actor_ref) != self._scope():
                    raise ValueError("outlook install owner mismatch")
                self._db.execute(
                    "INSERT INTO world_outlook_revisions(world_id,actor_ref,revision,parent_revision,payload,evidence) VALUES(?,?,?,?,?,?)",
                    (
                        *self._scope(),
                        revision,
                        draft.parent_revision,
                        draft.model_dump_json(),
                        json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                    ),
                )
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
        return revision

    def claim(self, operation_id: str, *, kind: str, payload: dict) -> bool:
        with self._lock:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO world_outlook_operations VALUES(?,?,?,?,?,?)",
                (
                    *self._scope(),
                    operation_id,
                    kind,
                    "running",
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ),
            )
            return cur.rowcount == 1

    def operation(self, operation_id: str):
        with self._lock:
            row = self._db.execute(
                "SELECT state,payload FROM world_outlook_operations WHERE world_id=? AND actor_ref=? AND operation_id=?",
                (*self._scope(), operation_id),
            ).fetchone()
        return {"state": row[0], "payload": json.loads(row[1])} if row else None

    def finish(self, operation_id: str, *, state: str, payload: dict):
        with self._lock:
            cur = self._db.execute(
                "UPDATE world_outlook_operations SET state=?,payload=? WHERE world_id=? AND actor_ref=? AND operation_id=? AND state=?",
                (
                    state,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    *self._scope(),
                    operation_id,
                    "running",
                ),
            )
            if cur.rowcount != 1:
                existing = self.operation(operation_id)
                if existing != {"state": state, "payload": payload}:
                    raise ValueError("outlook operation terminal conflict")

    def attempt(self, operation_id: str, *, ordinal: int, payload: dict):
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO world_outlook_attempts VALUES(?,?,?,?,?,?)",
                (*self._scope(), operation_id, ordinal, encoded, digest(payload)),
            )
            row = self._db.execute(
                "SELECT payload FROM world_outlook_attempts WHERE world_id=? AND actor_ref=? AND operation_id=? AND ordinal=?",
                (*self._scope(), operation_id, ordinal),
            ).fetchone()
            if row[0] != encoded:
                raise ValueError("outlook attempt identity conflict")

    def offers(self):
        with self._lock:
            rows = self._db.execute(
                "SELECT state,payload,operation_id FROM world_outlook_operations WHERE world_id=? AND actor_ref=? AND kind=? ORDER BY rowid",
                (*self._scope(), "offer"),
            ).fetchall()
        return tuple(
            {"state": s, "payload": json.loads(p), "operation_ref": op} for s, p, op in rows
        )

    def revisions(self):
        with self._lock:
            rows = self._db.execute(
                "SELECT revision,payload,evidence FROM world_outlook_revisions WHERE world_id=? AND actor_ref=? ORDER BY sequence",
                self._scope(),
            ).fetchall()
        return tuple(
            {"revision": r, "draft": json.loads(p), "evidence": json.loads(e)} for r, p, e in rows
        )

    def close(self):
        self._db.close()
