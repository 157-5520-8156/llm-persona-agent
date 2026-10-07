"""Exact historical source proof for the existing MemoryCandidate authority.

This module neither selects memories nor grants access to an archive. The
retrieval compiler calls it only after candidate status/privacy selection.
"""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from typing import TYPE_CHECKING, Literal

from pydantic import Field, model_validator

from .character_prehistory import (
    HistoricalEntity,
    PrehistoryArchiveProjection,
    PrehistoryArchiveManifest,
    PrehistoryRecord,
    PrehistoryRecordImportedPayload,
    PrehistoryRecordProjection,
    digest, validate_story_boundary,
)
from .schema_core import FrozenModel
from .prehistory_story import CompactStoryMemoryReading, StoryMemoryReading, story_memory_reading
if TYPE_CHECKING:
    from .schemas import CommittedWorldEventRef, MemorySourceBinding


class PrehistoryMemoryReading(FrozenModel):
    """Historical scope, without a second unbounded copy of the source text."""

    story: StoryMemoryReading | CompactStoryMemoryReading | None = Field(default=None, exclude_if=lambda value: value is None)
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


@lru_cache(maxsize=1024)
def _record_hash(record: PrehistoryRecord) -> str:
    return digest(record)


@lru_cache(maxsize=128)
def _manifest_hash(manifest: PrehistoryArchiveManifest) -> str:
    return digest(manifest)


@lru_cache(maxsize=1024)
def _record_payload_hash(row: PrehistoryRecordProjection) -> str:
    return digest(PrehistoryRecordImportedPayload(
        archive_id=row.archive_id, archive_manifest_hash=row.archive_manifest_hash,
        record=row.record,
    ))


@lru_cache(maxsize=128)
def _archive_payload_hash(archive: PrehistoryArchiveProjection) -> str:
    return digest(archive.model_dump(mode="json", exclude={
        "accepted_event_ref", "accepted_world_revision", "accepted_payload_hash", "accepted_at",
    }))


def prehistory_memory_binding(record: PrehistoryRecordProjection) -> MemorySourceBinding:
    from .schemas import MemorySourceBinding

    return MemorySourceBinding(
        source_kind="prehistory", source_id=record.record.record_id,
        source_entity_revision=1, authority_event_ref=record.accepted_event_ref,
        authority_world_revision=record.accepted_world_revision,
        authority_payload_hash=record.accepted_payload_hash,
        source_values_hash=_record_hash(record.record),
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
        or _manifest_hash(archive.manifest) != row.archive_manifest_hash
        or archive.world_started_at != row.world_started_at
        or row.record.occurred_until >= row.world_started_at
        or row.accepted_at < archive.accepted_at
        or not any(pin.record_id == binding.source_id and pin.record_hash == binding.source_values_hash
                   for pin in archive.manifest.records)
    ):
        raise ValueError("prehistory memory source lost its reviewed archive")
    # Cache only pure hashes of fully immutable values. Source presence, actor,
    # privacy/candidate eligibility and the current committed event closure are
    # still checked on every read; a prior successful read grants no authority.
    if _record_payload_hash(row) != row.accepted_payload_hash or _archive_payload_hash(archive) != archive.accepted_payload_hash:
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
    validate_story_boundary(row.record, actor_ref=row.actor_ref, world_started_at=row.world_started_at)
    return row, archive


def prehistory_memory_reading(
    row: PrehistoryRecordProjection, archive: PrehistoryArchiveProjection,
    *, initialization_snapshot: bool = False,
) -> PrehistoryMemoryReading:
    record: PrehistoryRecord = row.record
    entity_refs = {*record.participant_refs, record.location_ref}
    story = story_memory_reading(record.story, statement=record.statement) if record.story else None
    related = record.related_record_refs
    # Durable retention opportunities are replayed byte-for-byte. Their old .2
    # reading has no canonical coordinates; .3 has no later edge-proof fields.
    if story is not None and (not initialization_snapshot or archive.manifest.import_context is not None):
        from .story_import import canonical_event_ref
        from .prehistory_story import StoryRelationReading, StoryRelationAuthority
        context = archive.manifest.import_context
        edges = tuple(edge for edge in context.cross_story_links
                      if record.record_id in {edge.left_record_id, edge.right_record_id}) if context else ()
        related = tuple(sorted({*related, *(ref for edge in edges
            for ref in (edge.left_record_id, edge.right_record_id) if ref != record.record_id)}))
        story = story.model_copy(update={
            "blocks": tuple(block.model_copy(update={"canonical_event_ref": canonical_event_ref(
                archive.manifest, block.event_ref,
            )}) for block in story.blocks),
            "relations": (*story.relations, *(StoryRelationReading(
                left_record_id=e.left_record_id, right_record_id=e.right_record_id, relation_kind=e.relation_kind,
                authority=StoryRelationAuthority(event_ref=archive.accepted_event_ref,
                    world_revision=archive.accepted_world_revision, payload_hash=archive.accepted_payload_hash)
                    if not initialization_snapshot else None,
            ) for e in edges)),
        })
    return PrehistoryMemoryReading(
        story=story,
        actor_ref=row.actor_ref, occurred_from=record.occurred_from,
        occurred_until=record.occurred_until, time_precision=record.time_precision,
        timezone_name=record.timezone_name, world_started_at=row.world_started_at,
        participant_refs=record.participant_refs, location_ref=record.location_ref,
        related_record_refs=related,
        entities=tuple(item for item in archive.manifest.entities if item.entity_ref in entity_refs),
        archive_event_ref=archive.accepted_event_ref, archive_world_revision=archive.accepted_world_revision,
        archive_payload_hash=archive.accepted_payload_hash,
    )


def restrict_story_reading(reading: PrehistoryMemoryReading, visible_record_ids: set[str], *,
                           record_id: str | None = None, cross_story_relations=()) -> PrehistoryMemoryReading:
    """No metadata route around the current readable memory set."""
    if reading.story is None:
        return reading
    relations = tuple(dict.fromkeys((*reading.story.relations, *(
        edge for edge in cross_story_relations if record_id in {edge.left_record_id, edge.right_record_id}
    ))))
    related = set(reading.related_record_refs)
    if record_id is not None:
        related.update(ref for edge in relations for ref in (edge.left_record_id, edge.right_record_id)
                       if ref != record_id)
    return reading.model_copy(update={
        "story": reading.story.model_copy(update={'relations': relations}).restrict_relations(visible_record_ids),
        "related_record_refs": tuple(sorted(ref for ref in related if ref in visible_record_ids)),
    })
