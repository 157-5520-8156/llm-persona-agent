"""Resolve epoch-migrated Fact sources when live observation projection is empty."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Mapping

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

    return lookup_archive_observations(
        archive_ledger,
        observation_ids=(observation_id,),
    ).get(observation_id)


def lookup_archive_observations(
    archive_ledger: object,
    *,
    observation_ids: tuple[str, ...],
) -> dict[str, tuple[Observation, WorldEvent, int]]:
    """Resolve archive observations with one locator-backed read.

    The locator rows are immutable consequences of the archived accepted
    events.  We still validate each complete WorldEvent and Observation below,
    so batching changes only access cost, not source-closure authority.
    """

    database_path = getattr(archive_ledger, "_database_path", None)
    world_id = getattr(archive_ledger, "_world_id", None)
    ordered_ids = tuple(dict.fromkeys(item for item in observation_ids if item))
    if database_path is None or not world_id or not ordered_ids:
        return {}
    connection = sqlite3.connect(f"file:{Path(database_path)}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" for _ in ordered_ids)
        rows = connection.execute(
            f"""
            SELECT locator.observation_id, event.world_revision, event.event_json
            FROM world_v2_prefix_locator_values AS locator
            JOIN world_v2_events AS event
              ON event.world_id = locator.world_id
             AND event.event_id = locator.event_id
            WHERE locator.world_id = ?
              AND locator.event_type = 'ObservationRecorded'
              AND locator.observation_id IN ({placeholders})
            ORDER BY locator.ledger_sequence
            """,  # noqa: S608 - placeholders are generated only from argument count.
            (world_id, *ordered_ids),
        ).fetchall()
    finally:
        connection.close()
    wanted = set(ordered_ids)
    resolved: dict[str, tuple[Observation, WorldEvent, int]] = {}
    for item in rows:
        try:
            event = WorldEvent.model_validate_json(item["event_json"])
        except ValueError:
            continue
        if event.event_type != "ObservationRecorded":
            continue
        try:
            observation = Observation.model_validate_json(event.payload_json)
        except ValueError:
            continue
        observation_id = observation.observation_id
        if (
            observation_id not in wanted
            or observation_id != item["observation_id"]
            or not observation.text
            or observation_id in resolved
        ):
            continue
        resolved[observation_id] = (observation, event, int(item["world_revision"]))
    return resolved


def archive_observation_ids(fact: FactProjection) -> tuple[str, ...]:
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
    archive_observations: Mapping[str, tuple[Observation, WorldEvent, int]] | None = None,
) -> tuple[str, Observation, WorldEvent, int] | None:
    """Close a genesis-carried Fact over its archive observation when possible."""

    if not is_epoch_genesis_fact(fact) or archive_ledger is None:
        return None
    for observation_id in archive_observation_ids(fact):
        resolved = (
            archive_observations.get(observation_id)
            if archive_observations is not None
            else lookup_archive_observation(
                archive_ledger,
                observation_id=observation_id,
            )
        )
        if resolved is None:
            continue
        observation, event, world_revision = resolved
        return observation.text, observation, event, world_revision
    return None


__all__ = [
    "EPOCH_GENESIS_TRANSITION_PREFIX",
    "archive_observation_ids",
    "default_epoch_archive_path",
    "epoch_migration_source_excerpt",
    "is_epoch_genesis_fact",
    "lookup_archive_observation",
    "lookup_archive_observations",
]
