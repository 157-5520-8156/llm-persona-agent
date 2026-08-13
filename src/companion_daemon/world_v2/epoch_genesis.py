"""Write a new World v2 epoch ledger from a continuity snapshot.

The archive file is a byte copy and is never opened by this toolchain. The new
database starts at WorldStarted with the snapshot in its payload. Observation
idempotency keys from the archive are not copied.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import hashlib
import json
import sqlite3

from .epoch_continuity import ContinuitySnapshot
from .event_identity import domain_idempotency_key
from .schemas import WorldEvent
from .sqlite_ledger import SQLiteWorldLedger


def archive_sqlite_file(*, source: Path, destination: Path) -> None:
    """Copy the live database with SQLite backup, then freeze the archive."""

    source = Path(source)
    destination = Path(destination)
    if not source.is_file():
        raise FileNotFoundError(source)
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    incoming = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        outgoing = sqlite3.connect(destination)
        try:
            incoming.backup(outgoing)
        finally:
            outgoing.close()
    finally:
        incoming.close()
    destination.chmod(0o444)


def world_started_event(
    *,
    world_id: str,
    now: datetime,
    snapshot: ContinuitySnapshot | None = None,
    epoch_id: str = "epoch:2",
) -> WorldEvent:
    payload: dict[str, object] = {}
    if snapshot is not None:
        payload["continuity"] = snapshot.model_dump(mode="json")
    material = json.dumps(
        {"world_id": world_id, "event_type": "WorldStarted", "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id=f"event:world-v2-epoch:{epoch_id}:WorldStarted:{digest}",
        world_id=world_id,
        event_type="WorldStarted",
        logical_time=now,
        created_at=now,
        actor="system:world-v2-epoch",
        source="world-v2:epoch",
        trace_id=f"trace:world-v2-epoch:{epoch_id}:{digest}",
        causation_id=f"epoch:{epoch_id}:{world_id}",
        correlation_id=f"epoch:{epoch_id}:{world_id}",
        idempotency_key=domain_idempotency_key(
            event_type="WorldStarted", world_id=world_id, payload=payload
        )
        or f"epoch:{epoch_id}:WorldStarted:{digest}",
        payload=payload,
    )


def write_epoch_ledger(
    *,
    path: Path,
    world_id: str,
    now: datetime,
    snapshot: ContinuitySnapshot,
) -> SQLiteWorldLedger:
    path = Path(path)
    if path.exists():
        raise FileExistsError(path)
    ledger = SQLiteWorldLedger(path=path, world_id=world_id)
    started = world_started_event(
        world_id=world_id, now=now, snapshot=snapshot, epoch_id=snapshot.epoch_id
    )
    ledger.commit(
        [started],
        expected_world_revision=0,
        expected_deliberation_revision=0,
    )
    return ledger


__all__ = ["archive_sqlite_file", "world_started_event", "write_epoch_ledger"]
