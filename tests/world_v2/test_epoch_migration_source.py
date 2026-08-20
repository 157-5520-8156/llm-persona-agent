from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace

from companion_daemon.world_v2 import epoch_migration_source
from companion_daemon.world_v2.schemas import Observation, WorldEvent


class _Rows:
    def __init__(self, values: list[dict[str, object]]) -> None:
        self._values = values

    def fetchall(self) -> list[dict[str, object]]:
        return self._values


class _ArchiveConnection:
    row_factory = None

    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.executions: list[tuple[str, tuple[object, ...]]] = []
        self.closed = False

    def execute(self, statement: str, parameters: tuple[object, ...]) -> _Rows:
        self.executions.append((statement, parameters))
        return _Rows(self.rows)

    def close(self) -> None:
        self.closed = True


def _event_json(observation_id: str, *, text: str) -> str:
    now = datetime(2026, 8, 20, tzinfo=UTC)
    payload = Observation(
        schema_version="world-v2.1",
        observation_id=observation_id,
        world_id="world:test",
        logical_time=now,
        created_at=now,
        trace_id=f"trace:{observation_id}",
        causation_id=f"cause:{observation_id}",
        correlation_id=f"correlation:{observation_id}",
        source="test",
        source_event_id=f"source:{observation_id}",
        actor="user:test",
        channel="qq",
        payload_ref=f"payload:{observation_id}",
        payload_hash=hashlib.sha256(text.encode()).hexdigest(),
        text=text,
        received_at=now,
    )
    return WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id=f"event:{observation_id}",
        world_id="world:test",
        event_type="ObservationRecorded",
        logical_time=now,
        created_at=now,
        actor="user:test",
        source="test",
        trace_id=f"trace:{observation_id}",
        causation_id=f"cause:{observation_id}",
        correlation_id=f"correlation:{observation_id}",
        idempotency_key=f"identity:{observation_id}",
        payload=payload.model_dump(mode="json"),
    ).model_dump_json()


def test_archive_observations_are_loaded_in_one_indexed_batch(monkeypatch) -> None:
    wanted = tuple(f"observation:{index}" for index in range(20))
    connection = _ArchiveConnection(
        [
            {
                "observation_id": observation_id,
                "world_revision": index + 1,
                "event_json": _event_json(observation_id, text=f"message {index}"),
            }
            for index, observation_id in enumerate(wanted)
        ]
    )
    connects: list[str] = []

    def connect(database: str, *, uri: bool) -> _ArchiveConnection:  # noqa: FBT001
        connects.append(database)
        return connection

    monkeypatch.setattr(epoch_migration_source.sqlite3, "connect", connect)

    loaded = epoch_migration_source.lookup_archive_observations(
        SimpleNamespace(_database_path="/archive.sqlite", _world_id="world:test"),
        observation_ids=wanted,
    )

    assert tuple(loaded) == wanted
    assert [loaded[item][0].text for item in wanted] == [
        f"message {index}" for index in range(20)
    ]
    assert len(connects) == 1
    assert len(connection.executions) == 1
    statement, parameters = connection.executions[0]
    assert "world_v2_prefix_locator_values" in statement
    assert " LIKE " not in statement.upper()
    assert parameters == ("world:test", *wanted)
    assert connection.closed is True
