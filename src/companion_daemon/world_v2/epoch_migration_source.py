"""Resolve epoch-migrated Fact sources when live observation projection is empty."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .schemas import FactProjection, Observation, WorldEvent


EPOCH_GENESIS_TRANSITION_PREFIX = "transition:epoch-genesis:fact:"


def is_epoch_genesis_fact(fact: FactProjection) -> bool:
    return fact.origin.transition_id.startswith(EPOCH_GENESIS_TRANSITION_PREFIX)


def default_epoch_archive_path(*, live_database: Path) -> Path | None:
    """Best-effort sibling archive for ``companion.epoch2.sqlite`` clones."""

    live = live_database.expanduser().resolve()
    if live.name == "companion.epoch2.sqlite":
        candidate = live.with_name("companion.epoch1.sqlite")
        if candidate.is_file():
            return candidate
    return None


def lookup_archive_observation(
    archive_ledger: object,
    *,
    observation_id: str,
) -> tuple[Observation, WorldEvent, int] | None:
    """Return observation, its ObservationRecorded event, and world revision."""

    database_path = getattr(archive_ledger, "_database_path", None)
    world_id = getattr(archive_ledger, "_world_id", None)
    if database_path is None or not world_id:
        return None
    connection = sqlite3.connect(f"file:{Path(database_path)}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT ledger_sequence, world_revision, event_json
            FROM world_v2_events
            WHERE world_id = ?
              AND json_extract(event_json, '$.event_type') = 'ObservationRecorded'
              AND event_json LIKE ?
            ORDER BY ledger_sequence
            LIMIT 8
            """,
            (world_id, f"%{observation_id}%"),
        ).fetchall()
    finally:
        connection.close()
    for item in rows:
        event = WorldEvent.model_validate_json(item["event_json"])
        if event.event_type != "ObservationRecorded":
            continue
        try:
            observation = Observation.model_validate_json(event.payload_json)
        except ValueError:
            continue
        if observation.observation_id != observation_id or not observation.text:
            continue
        return observation, event, int(item["world_revision"])
    return None


def _archive_observation_ids(fact: FactProjection) -> tuple[str, ...]:
    binding = fact.values.assertion_binding
    candidates: list[str] = []
    for evidence in fact.values.source_evidence_refs:
        if evidence.evidence_type == "observed_message":
            candidates.append(evidence.ref_id)
    if binding.source_kind == "observed_message":
        candidates.append(binding.source_ref)
    seen: set[str] = set()
    ordered: list[str] = []
    for item in candidates:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return tuple(ordered)


def epoch_migration_source_excerpt(
    fact: FactProjection,
    *,
    archive_ledger: object | None,
) -> tuple[str, Observation, WorldEvent, int] | None:
    """Close a genesis-carried Fact over its archive observation when possible."""

    if not is_epoch_genesis_fact(fact) or archive_ledger is None:
        return None
    for observation_id in _archive_observation_ids(fact):
        resolved = lookup_archive_observation(
            archive_ledger,
            observation_id=observation_id,
        )
        if resolved is None:
            continue
        observation, event, world_revision = resolved
        return observation.text, observation, event, world_revision
    return None


__all__ = [
    "EPOCH_GENESIS_TRANSITION_PREFIX",
    "default_epoch_archive_path",
    "epoch_migration_source_excerpt",
    "is_epoch_genesis_fact",
    "lookup_archive_observation",
]
