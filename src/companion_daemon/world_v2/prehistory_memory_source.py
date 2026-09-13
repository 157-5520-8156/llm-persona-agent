"""Exact historical source proof for the existing MemoryCandidate authority.

This module neither selects memories nor grants access to an archive. The
retrieval compiler calls it only after candidate status/privacy selection.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import Field, model_validator

from .character_prehistory import (
    HistoricalEntity,
    PrehistoryArchiveProjection,
    PrehistoryRecord,
    PrehistoryRecordImportedPayload,
    PrehistoryRecordProjection,
    digest,
)
from .schema_core import FrozenModel
if TYPE_CHECKING:
    from .schemas import CommittedWorldEventRef, MemorySourceBinding


class PrehistoryMemoryReading(FrozenModel):
    """Historical scope, without a second unbounded copy of the source text."""

    actor_ref: str = Field(min_length=1, max_length=256)
    occurred_from: datetime
    occurred_until: datetime
    time_precision: Literal["day", "month", "year", "interval"]
    timezone_name: str = Field(min_length=1, max_length=128)
    world_started_at: datetime
    participant_refs: tuple[str, ...] = Field(max_length=32)
    location_ref: str | None
    related_record_refs: tuple[str, ...] = Field(max_length=32)
    entities: tuple[HistoricalEntity, ...] = Field(max_length=33)
    archive_event_ref: str = Field(min_length=1, max_length=256)
    archive_world_revision: int = Field(ge=1)
    archive_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def historical_scope_is_closed(self):
        for value in (self.occurred_from, self.occurred_until, self.world_started_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("prehistory memory dates must be timezone aware")
        if not self.occurred_from <= self.occurred_until < self.world_started_at:
            raise ValueError("prehistory memory interval must precede original World start")
        entity_refs = {item.entity_ref for item in self.entities}
        expected_refs = set(self.participant_refs) | ({self.location_ref} if self.location_ref else set())
        if entity_refs != expected_refs or len(entity_refs) != len(self.entities):
            raise ValueError("prehistory memory must expose only exact referenced historical identities")
        return self


def prehistory_memory_binding(record: PrehistoryRecordProjection) -> MemorySourceBinding:
    from .schemas import MemorySourceBinding

    return MemorySourceBinding(
        source_kind="prehistory", source_id=record.record.record_id,
        source_entity_revision=1, authority_event_ref=record.accepted_event_ref,
        authority_world_revision=record.accepted_world_revision,
        authority_payload_hash=record.accepted_payload_hash,
        source_values_hash=digest(record.record),
    )


def resolve_prehistory_memory_source(
    binding: MemorySourceBinding,
    *,
    records: tuple[PrehistoryRecordProjection, ...],
    archives: tuple[PrehistoryArchiveProjection, ...],
    committed_events: tuple[CommittedWorldEventRef, ...],
) -> tuple[PrehistoryRecordProjection, PrehistoryArchiveProjection]:
    """Require the reviewed manifest and its exact separately committed record."""
    row = next((item for item in records if item.record.record_id == binding.source_id), None)
    if row is None or binding != prehistory_memory_binding(row):
        raise ValueError("memory source does not resolve exact prehistory authority")
    archive = next((item for item in archives if item.manifest.archive_id == row.archive_id), None)
    if archive is None or (
        archive.manifest.actor_ref != row.actor_ref
        or digest(archive.manifest) != row.archive_manifest_hash
        or archive.world_started_at != row.world_started_at
        or row.record.occurred_until >= row.world_started_at
        or row.accepted_at < archive.accepted_at
        or not any(pin.record_id == binding.source_id and pin.record_hash == binding.source_values_hash
                   for pin in archive.manifest.records)
    ):
        raise ValueError("prehistory memory source lost its reviewed archive")
    record_payload = PrehistoryRecordImportedPayload(
        archive_id=row.archive_id, archive_manifest_hash=row.archive_manifest_hash, record=row.record,
    )
    archive_payload = archive.model_dump(mode="json", exclude={
        "accepted_event_ref", "accepted_world_revision", "accepted_payload_hash", "accepted_at",
    })
    if digest(record_payload) != row.accepted_payload_hash or digest(archive_payload) != archive.accepted_payload_hash:
        raise ValueError("prehistory memory source body differs from its authority")
    for source, event_type in ((row, "CharacterPrehistoryRecordImported"),
                               (archive, "CharacterPrehistoryArchiveAccepted")):
        authority = next((item for item in committed_events if item.event_id == source.accepted_event_ref), None)
        if authority is None or (
            authority.event_type != event_type
            or authority.world_revision != source.accepted_world_revision
            or authority.payload_hash != source.accepted_payload_hash
            or authority.logical_time != source.accepted_at
        ):
            raise ValueError("prehistory memory source event is unavailable")
    if archive.accepted_world_revision >= row.accepted_world_revision:
        raise ValueError("prehistory memory source precedes its archive")
    return row, archive


def prehistory_memory_reading(
    row: PrehistoryRecordProjection, archive: PrehistoryArchiveProjection,
) -> PrehistoryMemoryReading:
    record: PrehistoryRecord = row.record
    entity_refs = {*record.participant_refs, record.location_ref}
    return PrehistoryMemoryReading(
        actor_ref=row.actor_ref, occurred_from=record.occurred_from,
        occurred_until=record.occurred_until, time_precision=record.time_precision,
        timezone_name=record.timezone_name, world_started_at=row.world_started_at,
        participant_refs=record.participant_refs, location_ref=record.location_ref,
        related_record_refs=record.related_record_refs,
        entities=tuple(item for item in archive.manifest.entities if item.entity_ref in entity_refs),
        archive_event_ref=archive.accepted_event_ref, archive_world_revision=archive.accepted_world_revision,
        archive_payload_hash=archive.accepted_payload_hash,
    )
