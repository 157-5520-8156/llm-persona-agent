"""Durable private outlook revisions and operations, separate from World facts."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
import sqlite3

from .sqlite_coordination import configure_shared_sqlite_connection, sqlite_write_lock
from .world_outlook import WorldOutlookDraft, digest


_INVALID_OFFER_SHAPE = """(json_type(payload,'$.node.thread_refs') IS NOT 'array'
    OR json_array_length(payload,'$.node.thread_refs') NOT BETWEEN 1 AND 6
    OR (json_type(payload,'$.effect_refs') IS NOT NULL
        AND json_type(payload,'$.effect_refs') IS NOT 'array')
    OR json_array_length(payload,'$.effect_refs')>3)"""


class SQLiteWorldOutlookStore:
    def __init__(self, *, path: str | Path, world_id: str, actor_ref: str):
        self.world_id, self.actor_ref = world_id, actor_ref
        self._lock = sqlite_write_lock(path)
        self._db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self._thread_heads = []
        self._thread_sequence = 0
        self._thread_cursor = None
        self._thread_generation = None
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
                CREATE TABLE IF NOT EXISTS world_outlook_rewrite_epoch(
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision_epoch INTEGER NOT NULL);
                INSERT OR IGNORE INTO world_outlook_rewrite_epoch(singleton,revision_epoch) VALUES(1,0);
                CREATE TRIGGER IF NOT EXISTS world_outlook_revision_update_epoch
                AFTER UPDATE ON world_outlook_revisions BEGIN
                    UPDATE world_outlook_rewrite_epoch SET revision_epoch=revision_epoch+1;
                END;
                CREATE TRIGGER IF NOT EXISTS world_outlook_revision_delete_epoch
                AFTER DELETE ON world_outlook_revisions BEGIN
                    UPDATE world_outlook_rewrite_epoch SET revision_epoch=revision_epoch+1;
                END;
                CREATE TRIGGER IF NOT EXISTS world_outlook_revision_replace_epoch
                BEFORE INSERT ON world_outlook_revisions
                WHEN EXISTS(SELECT 1 FROM world_outlook_revisions
                            WHERE revision=NEW.revision OR sequence=NEW.sequence) BEGIN
                    UPDATE world_outlook_rewrite_epoch SET revision_epoch=revision_epoch+1;
                END;
                CREATE INDEX IF NOT EXISTS world_outlook_offer_node ON world_outlook_operations(
                    world_id,actor_ref,json_extract(payload,'$.node.opportunity_id')) WHERE kind='offer';
                CREATE INDEX IF NOT EXISTS world_outlook_work ON world_outlook_operations(
                    world_id,actor_ref,kind,state);
                CREATE INDEX IF NOT EXISTS world_outlook_offer_owner ON world_outlook_operations(
                    world_id,actor_ref) WHERE kind='offer';
            """)
            columns = {row[1] for row in self._db.execute("PRAGMA table_info(world_outlook_rewrite_epoch)")}
            if "operation_epoch" not in columns:
                self._db.execute("ALTER TABLE world_outlook_rewrite_epoch ADD COLUMN operation_epoch INTEGER NOT NULL DEFAULT 0")
            self._db.executescript("""
                CREATE TRIGGER IF NOT EXISTS world_outlook_operation_update_epoch
                AFTER UPDATE ON world_outlook_operations WHEN OLD.state!='running' OR NEW.state='running' BEGIN
                    UPDATE world_outlook_rewrite_epoch SET operation_epoch=operation_epoch+1;
                END;
                CREATE TRIGGER IF NOT EXISTS world_outlook_operation_delete_epoch
                AFTER DELETE ON world_outlook_operations BEGIN
                    UPDATE world_outlook_rewrite_epoch SET operation_epoch=operation_epoch+1;
                END;
                CREATE TRIGGER IF NOT EXISTS world_outlook_operation_replace_epoch
                BEFORE INSERT ON world_outlook_operations
                WHEN EXISTS(SELECT 1 FROM world_outlook_operations WHERE world_id=NEW.world_id
                            AND actor_ref=NEW.actor_ref AND operation_id=NEW.operation_id) BEGIN
                    UPDATE world_outlook_rewrite_epoch SET operation_epoch=operation_epoch+1;
                END;
            """)
            self._db.execute("CREATE INDEX IF NOT EXISTS world_outlook_offer_invalid_shape ON world_outlook_operations(world_id,actor_ref) WHERE kind='offer' AND " + _INVALID_OFFER_SHAPE)
            for group, path, count in (("thread", "node.thread_refs", 6), ("effect", "effect_refs", 3)):
                for slot in range(count):
                    self._db.execute(f"""CREATE INDEX IF NOT EXISTS world_outlook_offer_{group}_{slot}
                        ON world_outlook_operations(world_id,actor_ref,json_extract(payload,'$.{path}[{slot}]'))
                        WHERE kind='offer' AND state='considered'""")  # noqa: S608 - fixed schema slots

            for slot in range(6):
                self._db.execute(f"""CREATE INDEX IF NOT EXISTS world_outlook_navigation_{slot}
                    ON world_outlook_operations(world_id,actor_ref,json_extract(payload,'$.node.thread_refs[{slot}]'))
                    WHERE kind='offer'""")  # noqa: S608 - fixed slots

    def navigation_offer_page(self, thread_refs, *, before=None, limit=32):
        """Global reverse offer order for these threads, with bounded pages.

        Include failed receipts too: an actual effect can precede a technical
        failure, and only the caller's exact anchor reader decides visibility.
        """
        refs = tuple(set(thread_refs))
        if not refs or len(refs) > 6 or not 1 <= limit <= 256:
            raise ValueError("invalid outlook navigation page")
        rows = {}
        with self._lock:
            owns_transaction = not self._db.in_transaction
            try:
                if owns_transaction:
                    self._db.execute("BEGIN")
                if self._db.execute("SELECT 1 FROM world_outlook_operations WHERE world_id=? AND actor_ref=? AND kind='offer' AND "
                                    + _INVALID_OFFER_SHAPE + " LIMIT 1", self._scope()).fetchone():
                    raise ValueError("outlook offer exceeds the indexed thread/effect contract")
                marks = ",".join("?" for _ in refs)
                for slot in range(6):
                    found = self._db.execute(f"""SELECT rowid,state,payload,operation_id FROM world_outlook_operations
                        INDEXED BY world_outlook_navigation_{slot}
                        WHERE world_id=? AND actor_ref=? AND kind='offer'
                          AND json_extract(payload,'$.node.thread_refs[{slot}]') IN ({marks})
                          AND rowid<? ORDER BY rowid DESC LIMIT ?""",
                        (*self._scope(), *refs, before if before is not None else 9223372036854775807, limit))  # noqa: S608 - fixed slots
                    rows.update((row[0], row) for row in found)
            finally:
                if owns_transaction:
                    self._db.execute("ROLLBACK")
        return tuple((row[0], {"state": row[1], "payload": json.loads(row[2]), "operation_ref": row[3]})
                     for row in sorted(rows.values(), reverse=True)[:limit])

    def _scope(self):
        return self.world_id, self.actor_ref

    def head(self, *, as_of=None):
        with self._lock:
            rows = self._db.execute(
                "SELECT revision,payload,evidence FROM world_outlook_revisions WHERE world_id=? AND actor_ref=? ORDER BY sequence DESC",
                self._scope(),
            )
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

    def running_refresh(self, *, cursor):
        """Locate already paid/unknown work before a new compiler derives a key."""
        with self._lock:
            rows = self._db.execute("""SELECT operation_id,payload FROM world_outlook_operations
                WHERE world_id=? AND actor_ref=? AND kind='refresh' AND state='running'
                  AND json_extract(payload,'$.cursor.world_revision')=?
                  AND json_extract(payload,'$.cursor.deliberation_revision')=?
                  AND json_extract(payload,'$.cursor.ledger_sequence')=? LIMIT 2""",
                (*self._scope(), cursor["world_revision"], cursor["deliberation_revision"], cursor["ledger_sequence"]),
            ).fetchall()
        if len(rows) > 1:
            raise ValueError("multiple running outlook refreshes share one pinned cursor")
        return {"operation_ref": rows[0][0], "payload": json.loads(rows[0][1])} if rows else None

    def recent_threads(self, *, as_of):
        """The exact latest twelve distinct thread heads, with bounded hot storage."""
        with self._lock:
            owns_transaction = not self._db.in_transaction
            try:
                if owns_transaction:
                    self._db.execute("BEGIN")
                generation = self._db.execute("SELECT revision_epoch FROM world_outlook_rewrite_epoch WHERE singleton=1").fetchone()[0]
                if (self._thread_generation != generation or self._thread_cursor is None
                        or any(as_of[key] < self._thread_cursor[key] for key in as_of)):
                    self._thread_heads = []
                    self._thread_sequence = 0
                skipped_future = False
                while True:
                    rows = self._db.execute("""SELECT sequence,revision,payload,evidence FROM world_outlook_revisions
                        WHERE world_id=? AND actor_ref=? AND sequence>? ORDER BY sequence LIMIT 64""",
                        (*self._scope(), self._thread_sequence)).fetchall()
                    if not rows:
                        break
                    for sequence, revision, encoded, proof in rows:
                        draft, evidence = json.loads(encoded), json.loads(proof)
                        if digest({"draft": draft, "evidence": evidence}) != revision:
                            raise ValueError("story history revision binding mismatch")
                        if (draft["world_id"], draft["actor_ref"]) != self._scope():
                            raise ValueError("story history owner mismatch")
                        self._thread_sequence = sequence
                        if any(evidence["cursor"][key] > value for key, value in as_of.items()):
                            skipped_future = True
                            continue
                        ids = {thread["thread_id"] for thread in draft["threads"]}
                        self._thread_heads = ([{"thread": thread, "revision": revision} for thread in draft["threads"]]
                            + [head for head in self._thread_heads if head["thread"]["thread_id"] not in ids])[:12]
                # Stored source revisions, not the cache, remain the authority.
                for head in self._thread_heads:
                    row = self._db.execute("SELECT payload,evidence FROM world_outlook_revisions WHERE revision=? AND world_id=? AND actor_ref=?",
                                           (head["revision"], *self._scope())).fetchone()
                    if row is None:
                        raise ValueError("story history source revision disappeared")
                    draft, evidence = json.loads(row[0]), json.loads(row[1])
                    if (digest({"draft": draft, "evidence": evidence}) != head["revision"]
                            or head["thread"] not in draft["threads"]):
                        raise ValueError("story history source revision changed")
                self._thread_generation = generation
                self._thread_cursor = None if skipped_future else dict(as_of)
                return tuple(deepcopy(head["thread"]) for head in self._thread_heads)
            except BaseException:
                self._thread_cursor = None
                raise
            finally:
                if owns_transaction:
                    self._db.execute("ROLLBACK")

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
            if self.operation(operation_id) is not None:
                return False
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

    def offers(self, *, node_ids=None, states=None):
        if node_ids is not None and not node_ids:
            return ()
        where, params = "", [*self._scope()]
        if node_ids is not None:
            where += " AND json_extract(payload,'$.node.opportunity_id') IN (" + ",".join("?" for _ in node_ids) + ")"
            params.extend(node_ids)
        if states is not None:
            if not states:
                return ()
            where += " AND state IN (" + ",".join("?" for _ in states) + ")"
            params.extend(states)
        with self._lock:
            rows = self._db.execute(
                "SELECT state,payload,operation_id FROM world_outlook_operations WHERE world_id=? AND actor_ref=? AND kind='offer'" + where + " ORDER BY rowid",
                params,
            ).fetchall()
        return tuple(
            {"state": s, "payload": json.loads(p), "operation_ref": op} for s, p, op in rows
        )

    def pending_event_details(self, *, due_at=None, limit=64):
        if not 1 <= limit <= 128:
            raise ValueError("event detail queue read must be bounded")
        with self._lock:
            rows = self._db.execute(
                "SELECT operation_id,payload FROM world_outlook_operations WHERE world_id=? AND actor_ref=? "
                "AND kind='event_detail' AND state='running' "
                "AND (? IS NULL OR json_extract(payload,'$.next_due_at') IS NULL "
                "OR julianday(json_extract(payload,'$.next_due_at'))<=julianday(?)) ORDER BY rowid LIMIT ?",
                (*self._scope(), due_at, due_at, limit),
            ).fetchall()
        return tuple({"operation_ref": op, "payload": json.loads(payload)} for op, payload in rows)

    def next_event_detail_due(self):
        with self._lock:
            row = self._db.execute(
                "SELECT json_extract(payload,'$.next_due_at') FROM world_outlook_operations "
                "WHERE world_id=? AND actor_ref=? AND kind='event_detail' AND state='running' "
                "ORDER BY julianday(json_extract(payload,'$.next_due_at')) LIMIT 1", self._scope(),
            ).fetchone()
        if row and row[0]:
            from datetime import datetime
            return datetime.fromisoformat(row[0])
        return None

    def update_event_detail(self, operation_id, *, previous, payload):
        with self._lock:
            changed = self._db.execute(
                "UPDATE world_outlook_operations SET payload=? WHERE world_id=? AND actor_ref=? "
                "AND operation_id=? AND kind='event_detail' AND state='running' AND json(payload)=json(?)",
                (json.dumps(payload, ensure_ascii=False, sort_keys=True), *self._scope(), operation_id,
                 json.dumps(previous, ensure_ascii=False, sort_keys=True)),
            ).rowcount
        if changed != 1:
            raise ValueError("event detail progress changed concurrently")

    def wake_event_details(self, *, entity_ref, at):
        # Source transitions wake existing preparations; they do not write facts.
        with self._lock:
            self._db.execute(
                "UPDATE world_outlook_operations SET payload=json_set(payload,'$.next_due_at',?) "
                "WHERE world_id=? AND actor_ref=? AND kind='event_detail' AND state='running' "
                "AND (json_extract(payload,'$.program_index.capability.entity_ref')=? "
                "OR json_extract(payload,'$.occurrence_ref')=?)",
                (at, *self._scope(), entity_ref, entity_ref),
            )

    def operation_generation(self):
        with self._lock:
            return self._db.execute("SELECT operation_epoch FROM world_outlook_rewrite_epoch WHERE singleton=1").fetchone()[0]

    def offer_page(self, *, after=0, limit=256):
        if after < 0 or not 1 <= limit <= 512:
            raise ValueError("invalid outlook offer page")
        with self._lock:
            rows = self._db.execute("""SELECT rowid,state,payload,operation_id FROM world_outlook_operations
                INDEXED BY world_outlook_offer_owner
                WHERE world_id=? AND actor_ref=? AND kind='offer' AND rowid>? ORDER BY rowid LIMIT ?""",
                (*self._scope(), after, limit)).fetchall()
        return tuple((position, {"state": state, "payload": json.loads(payload), "operation_ref": op})
                     for position, state, payload, op in rows)

    def _offers_by_slot(self, refs, *, group, path, slots, limit, as_of=None):
        refs = tuple(set(refs))
        if not refs:
            return ()
        if len(refs) > 2048 or not 1 <= limit <= 2049:
            raise ValueError("outlook offer lookup exceeds its working-set bound")
        rows_by_position = {}
        with self._lock:
            owns_transaction = not self._db.in_transaction
            try:
                if owns_transaction:
                    self._db.execute("BEGIN")
                if self._db.execute("SELECT 1 FROM world_outlook_operations WHERE world_id=? AND actor_ref=? AND kind='offer' AND "
                                    + _INVALID_OFFER_SHAPE + " LIMIT 1", self._scope()).fetchone():
                    raise ValueError("outlook offer exceeds the indexed thread/effect contract")
                marks = ",".join("?" for _ in refs)
                for slot in range(slots):
                    where, params = "", [*self._scope(), *refs]
                    if as_of is not None:
                        for key in ("world_revision", "deliberation_revision", "ledger_sequence"):
                            where += f" AND COALESCE(json_extract(payload,'$.result_cursor.{key}'),json_extract(payload,'$.claim_cursor.{key}'),0)<=?"
                            params.append(as_of[key])
                    rows = self._db.execute(f"""SELECT rowid,state,payload,operation_id FROM world_outlook_operations
                        INDEXED BY world_outlook_offer_{group}_{slot}
                        WHERE world_id=? AND actor_ref=? AND kind='offer' AND state='considered'
                          AND json_extract(payload,'$.{path}[{slot}]') IN ({marks}) {where}
                        ORDER BY rowid DESC LIMIT ?""", (*params, limit)).fetchall()  # noqa: S608 - fixed schema slots/placeholder values
                    rows_by_position.update((row[0], row) for row in rows)
            finally:
                if owns_transaction:
                    self._db.execute("ROLLBACK")
        return tuple({"state": row[1], "payload": json.loads(row[2]), "operation_ref": row[3]}
                     for row in sorted(rows_by_position.values(), reverse=True)[:limit])

    def offers_for_thread(self, thread_ref, *, as_of, limit=12):
        if not 1 <= limit <= 144:
            raise ValueError("invalid story offer limit")
        return self._offers_by_slot((thread_ref,), group="thread", path="node.thread_refs", slots=6, limit=limit, as_of=as_of)

    def offers_for_effects(self, refs, *, as_of=None):
        offers = self._offers_by_slot(refs, group="effect", path="effect_refs", slots=3, limit=2049, as_of=as_of)
        if as_of is not None:
            offers = tuple(o for o in offers if all(
                o["payload"].get("result_cursor", o["payload"].get("claim_cursor", {})).get(key, value + 1) <= value
                for key, value in as_of.items()))
        if len(offers) > 2048:
            raise ValueError("outlook effect working set exceeds its bound")
        return tuple(reversed(offers))

    def attempts(self, operation_id: str):
        with self._lock:
            rows = self._db.execute(
                "SELECT ordinal,payload,payload_hash FROM world_outlook_attempts "
                "WHERE world_id=? AND actor_ref=? AND operation_id=? ORDER BY ordinal",
                (*self._scope(), operation_id),
            ).fetchall()
        result = {}
        for ordinal, encoded, expected in rows:
            payload = json.loads(encoded)
            if digest(payload) != expected:
                raise ValueError("outlook attempt content binding mismatch")
            result[ordinal] = payload
        return result

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
