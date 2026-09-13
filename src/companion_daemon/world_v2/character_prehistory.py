"""Reviewed fictional history before a World's immutable runtime boundary.

Archive review is an explicit trusted operator input, like reviewed World
seeds. Hash checks bind that input; they do not perform semantic review.
These records grant no current presence, NPC action, or remembered content.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, model_validator

from .schema_core import FrozenModel, PrivacyClass, canonicalize_json_value

_HASH = r"^[0-9a-f]{64}$"
_ENTITY = r"^history:(person|place|object|group):[a-z0-9][a-z0-9._-]*$"
_RECORD = r"^prehistory-record:[a-z0-9][a-z0-9._-]*$"
SOURCE = "world-v2:reviewed-prehistory"
IMPORT_ACTOR = "system:prehistory-import"


def digest(value) -> str:
    if isinstance(value, FrozenModel):
        value = value.model_dump(mode="json")
    return hashlib.sha256(json.dumps(
        canonicalize_json_value(value), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def _aware(*values):
    if any(value.tzinfo is None or value.utcoffset() is None for value in values):
        raise ValueError("prehistory times must be timezone aware")


class HistoricalEntity(FrozenModel):
    entity_ref: str = Field(pattern=_ENTITY, max_length=256)
    kind: Literal["person", "place", "object", "group"]
    label: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def namespace_is_explicit(self):
        if not self.entity_ref.startswith(f"history:{self.kind}:"):
            raise ValueError("historical identity kind disagrees with namespace")
        return self


class PrehistoryRecord(FrozenModel):
    record_id: str = Field(pattern=_RECORD, max_length=256)
    occurred_from: datetime
    occurred_until: datetime
    time_precision: Literal["day", "month", "year", "interval"]
    timezone_name: str = Field(default="UTC", min_length=1, max_length=128)
    statement: str = Field(min_length=1, max_length=1600)
    participant_refs: tuple[str, ...] = Field(default=(), max_length=32)
    location_ref: str | None = None
    related_record_refs: tuple[str, ...] = Field(default=(), max_length=32)
    privacy_class: PrivacyClass

    @model_validator(mode="after")
    def history_is_well_formed(self):
        _aware(self.occurred_from, self.occurred_until)
        if self.occurred_until < self.occurred_from or not self.statement.strip():
            raise ValueError("prehistory interval or statement is invalid")
        try:
            zone = ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("prehistory timezone is unknown") from exc
        start, end = self.occurred_from.astimezone(zone), self.occurred_until.astimezone(zone)
        formats = {"day": "%Y-%m-%d", "month": "%Y-%m", "year": "%Y"}
        if self.time_precision in formats and start.strftime(formats[self.time_precision]) != end.strftime(formats[self.time_precision]):
            raise ValueError("prehistory time precision disagrees with interval")
        for refs in (self.participant_refs, self.related_record_refs):
            if len(refs) != len(set(refs)):
                raise ValueError("prehistory references must be unique")
        if self.record_id in self.related_record_refs:
            raise ValueError("prehistory record cannot relate to itself")
        return self


class PrehistoryRecordPin(FrozenModel):
    record_id: str = Field(pattern=_RECORD, max_length=256)
    record_hash: str = Field(pattern=_HASH)


class PrehistoryArchiveManifest(FrozenModel):
    archive_id: str = Field(pattern=r"^prehistory-archive:[a-z0-9][a-z0-9._-]*$", max_length=256)
    world_id: str = Field(min_length=1, max_length=256)
    actor_ref: str = Field(min_length=1, max_length=256)
    source_artifact_ref: str = Field(min_length=1, max_length=512)
    entities: tuple[HistoricalEntity, ...] = Field(default=(), max_length=256)
    records: tuple[PrehistoryRecordPin, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def identities_are_unique(self):
        for refs in (tuple(x.entity_ref for x in self.entities), tuple(x.record_id for x in self.records)):
            if tuple(sorted(set(refs))) != refs:
                raise ValueError("prehistory manifest identities must be unique and sorted")
        return self


class PrehistoryArchiveDocument(FrozenModel):
    contract: Literal["character-prehistory-archive.1"]
    archive_id: str
    world_id: str
    actor_ref: str
    source_artifact_ref: str
    entities: tuple[HistoricalEntity, ...] = Field(default=(), max_length=256)
    records: tuple[PrehistoryRecord, ...] = Field(min_length=1, max_length=256)

    def manifest(self) -> PrehistoryArchiveManifest:
        return PrehistoryArchiveManifest(
            archive_id=self.archive_id, world_id=self.world_id, actor_ref=self.actor_ref,
            source_artifact_ref=self.source_artifact_ref,
            entities=tuple(sorted(self.entities, key=lambda x: x.entity_ref)),
            records=tuple(PrehistoryRecordPin(record_id=x.record_id, record_hash=digest(x))
                          for x in sorted(self.records, key=lambda x: x.record_id)),
        )

    @model_validator(mode="after")
    def records_match_manifest(self):
        self.manifest()
        entities = {x.entity_ref: x for x in self.entities}
        for record in self.records:
            validate_historical_references(record, entities)
        return self


def validate_historical_references(record, entities):
    for ref in record.participant_refs:
        if ref not in entities or entities[ref].kind not in {"person", "group"}:
            raise ValueError("prehistory participant must be a declared historical identity")
    if record.location_ref is not None and (
        record.location_ref not in entities or entities[record.location_ref].kind != "place"
    ):
        raise ValueError("prehistory location must be a declared historical place")


class PrehistoryReview(FrozenModel):
    manifest_hash: str = Field(pattern=_HASH)
    reviewer_ref: str = Field(min_length=1, max_length=256)
    review_artifact_ref: str = Field(min_length=1, max_length=512)
    review_artifact_hash: str = Field(pattern=_HASH)
    reviewed_at: datetime
    decision: Literal["approved"]

    @model_validator(mode="after")
    def review_has_actual_time(self):
        _aware(self.reviewed_at)
        return self


class ReviewedPrehistoryArchive(FrozenModel):
    document: PrehistoryArchiveDocument
    review: PrehistoryReview

    @model_validator(mode="after")
    def review_covers_exact_document(self):
        if digest(self.document.manifest()) != self.review.manifest_hash:
            raise ValueError("prehistory review does not cover the exact document")
        return self


class PrehistoryArchiveAcceptedPayload(FrozenModel):
    contract: Literal["character-prehistory-acceptance.1"] = "character-prehistory-acceptance.1"
    manifest: PrehistoryArchiveManifest
    review: PrehistoryReview
    world_started_ref: str
    world_started_revision: int = Field(ge=1)
    world_started_hash: str = Field(pattern=_HASH)
    world_started_at: datetime

    @model_validator(mode="after")
    def acceptance_is_bound(self):
        _aware(self.world_started_at)
        if digest(self.manifest) != self.review.manifest_hash:
            raise ValueError("prehistory acceptance review mismatch")
        return self


class PrehistoryArchiveProjection(PrehistoryArchiveAcceptedPayload):
    accepted_event_ref: str
    accepted_world_revision: int = Field(ge=1)
    accepted_payload_hash: str = Field(pattern=_HASH)
    accepted_at: datetime


class PrehistoryRecordImportedPayload(FrozenModel):
    contract: Literal["character-prehistory-record-import.1"] = "character-prehistory-record-import.1"
    archive_id: str
    archive_manifest_hash: str = Field(pattern=_HASH)
    record: PrehistoryRecord


class PrehistoryRecordProjection(PrehistoryRecordImportedPayload):
    actor_ref: str
    world_started_at: datetime
    accepted_event_ref: str
    accepted_world_revision: int = Field(ge=1)
    accepted_payload_hash: str = Field(pattern=_HASH)
    accepted_at: datetime


PREHISTORY_PAYLOAD_MODELS = {
    "CharacterPrehistoryArchiveAccepted": PrehistoryArchiveAcceptedPayload,
    "CharacterPrehistoryRecordImported": PrehistoryRecordImportedPayload,
}


def prehistory_event_id(*, world_id, event_type, payload):
    return "event:prehistory:" + digest([world_id, event_type, payload.model_dump(mode="json")])


def validate_prehistory_state(archives, records, events, *, logical_time, world_id=None):
    """A cached projection cannot invent or silently drop accepted history."""
    by_ref = {x.event_id: x for x in events}
    expected_archives = {x.event_id for x in events if x.event_type == "CharacterPrehistoryArchiveAccepted"}
    expected_records = {x.event_id for x in events if x.event_type == "CharacterPrehistoryRecordImported"}
    if expected_archives != {x.accepted_event_ref for x in archives} or expected_records != {x.accepted_event_ref for x in records}:
        raise ValueError("prehistory projection has missing or uncommitted authority")
    if len({x.manifest.archive_id for x in archives}) != len(archives) or len({x.record.record_id for x in records}) != len(records):
        raise ValueError("prehistory projection duplicates identities")
    for row, payload_type, event_type in (
        *((x, PrehistoryArchiveAcceptedPayload, "CharacterPrehistoryArchiveAccepted") for x in archives),
        *((x, PrehistoryRecordImportedPayload, "CharacterPrehistoryRecordImported") for x in records),
    ):
        source = by_ref.get(row.accepted_event_ref)
        payload = payload_type(**{key: getattr(row, key) for key in payload_type.model_fields})
        if source is None or (
            source.event_type, source.world_revision, source.payload_hash, source.logical_time
        ) != (event_type, row.accepted_world_revision, row.accepted_payload_hash, row.accepted_at) or (
            digest(payload) != source.payload_hash or logical_time is None or row.accepted_at > logical_time
        ):
            raise ValueError("prehistory projection changed its exact accepted source")
    manifests = {x.manifest.archive_id: x for x in archives}
    entities = {}
    for archive in archives:
        start = by_ref.get(archive.world_started_ref)
        if start is None or (
            start.event_type, start.world_revision, start.payload_hash, start.logical_time
        ) != ("WorldStarted", archive.world_started_revision, archive.world_started_hash, archive.world_started_at):
            raise ValueError("prehistory projection changed the World start")
        if world_id is not None and archive.manifest.world_id != world_id:
            raise ValueError("prehistory projection belongs to another World")
        for entity in archive.manifest.entities:
            if entity.entity_ref in entities and entity != entities[entity.entity_ref]:
                raise ValueError("prehistory projection changed a historical identity")
            entities[entity.entity_ref] = entity
    for row in records:
        archive = manifests.get(row.archive_id)
        if archive is None:
            raise ValueError("prehistory record projection lost its archive")
        pins = {x.record_id: x.record_hash for x in archive.manifest.records}
        if (
            row.actor_ref != archive.manifest.actor_ref
            or row.world_started_at != archive.world_started_at
            or row.archive_manifest_hash != archive.review.manifest_hash
            or pins.get(row.record.record_id) != digest(row.record)
            or row.record.occurred_until >= row.world_started_at
        ):
            raise ValueError("prehistory record projection differs from reviewed history")
        validate_historical_references(row.record, {x.entity_ref: x for x in archive.manifest.entities})
