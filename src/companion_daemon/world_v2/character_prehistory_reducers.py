"""Replay the reviewed archive grant and each exact historical record."""
from .character_prehistory import (
    IMPORT_ACTOR, SOURCE, PrehistoryArchiveAcceptedPayload, PrehistoryArchiveProjection,
    PrehistoryRecordImportedPayload, PrehistoryRecordProjection, digest, prehistory_event_id,
    validate_historical_references,
)


def _envelope(state, event, payload):
    if (
        event.actor != IMPORT_ACTOR or event.source != SOURCE
        or event.logical_time != state.logical_time
        or event.event_id != prehistory_event_id(world_id=event.world_id, event_type=event.event_type, payload=payload)
        or event.idempotency_key != event.event_id
    ):
        raise ValueError("prehistory import envelope is not the reviewed importer")


def archive_accepted(state, event):
    payload = PrehistoryArchiveAcceptedPayload.model_validate_json(event.payload_json)
    _envelope(state, event, payload)
    starts = [x for x in state.committed_world_event_refs if x.event_type == "WorldStarted"]
    if len(starts) != 1:
        raise ValueError("prehistory archive requires one original WorldStarted")
    start = starts[0]
    if (
        payload.manifest.world_id != event.world_id
        or (payload.world_started_ref, payload.world_started_revision, payload.world_started_hash, payload.world_started_at)
        != (start.event_id, start.world_revision, start.payload_hash, start.logical_time)
        or event.created_at < payload.review.reviewed_at
        or event.causation_id != start.event_id
        or event.correlation_id != payload.manifest.archive_id
        or any(x.manifest.archive_id == payload.manifest.archive_id for x in state.prehistory_archives)
    ):
        raise ValueError("prehistory archive start, review or identity mismatch")
    entities = {e.entity_ref: e for a in state.prehistory_archives for e in a.manifest.entities}
    if any(x.entity_ref in entities and entities[x.entity_ref] != x for x in payload.manifest.entities):
        raise ValueError("prehistory cannot rewrite an existing historical identity")
    record_ids = {x.record.record_id for x in state.prehistory_records}
    if any(x.record_id in record_ids for x in payload.manifest.records):
        raise ValueError("prehistory archive reuses an accepted record identity")
    projection = PrehistoryArchiveProjection(
        **payload.model_dump(), accepted_event_ref=event.event_id,
        accepted_world_revision=len(state.committed_world_event_refs) + 1,
        accepted_payload_hash=event.payload_hash, accepted_at=event.logical_time,
    )
    return state.model_copy(update={"prehistory_archives": (*state.prehistory_archives, projection)})


def record_imported(state, event):
    payload = PrehistoryRecordImportedPayload.model_validate_json(event.payload_json)
    _envelope(state, event, payload)
    archive = next((x for x in state.prehistory_archives if x.manifest.archive_id == payload.archive_id), None)
    if archive is None or archive.review.manifest_hash != payload.archive_manifest_hash:
        raise ValueError("prehistory record has no exact accepted archive")
    pin = next((x for x in archive.manifest.records if x.record_id == payload.record.record_id), None)
    known = {x.record.record_id for x in state.prehistory_records}
    related = {x.record.record_id for x in state.prehistory_records if x.actor_ref == archive.manifest.actor_ref}
    proposed = {x.record_id for x in archive.manifest.records}
    if (
        pin is None or pin.record_hash != digest(payload.record)
        or payload.record.occurred_until >= archive.world_started_at
        or payload.record.record_id in known
        or set(payload.record.related_record_refs) - related - proposed
        or event.causation_id != archive.world_started_ref
        or event.correlation_id != payload.archive_id
        or event.created_at < archive.review.reviewed_at
    ):
        raise ValueError("prehistory record differs from reviewed history or crosses runtime start")
    validate_historical_references(payload.record, {x.entity_ref: x for x in archive.manifest.entities})
    projection = PrehistoryRecordProjection(
        **payload.model_dump(), actor_ref=archive.manifest.actor_ref,
        world_started_at=archive.world_started_at, accepted_event_ref=event.event_id,
        accepted_world_revision=len(state.committed_world_event_refs) + 1,
        accepted_payload_hash=event.payload_hash, accepted_at=event.logical_time,
    )
    return state.model_copy(update={"prehistory_records": (*state.prehistory_records, projection)})
