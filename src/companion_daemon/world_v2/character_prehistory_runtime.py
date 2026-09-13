"""Atomic reviewed import; history remains separate from remembered material."""
from __future__ import annotations

import json
from .schema_core import canonicalize_json_value

from .character_prehistory import (
    IMPORT_ACTOR, SOURCE, PrehistoryArchiveAcceptedPayload, PrehistoryRecordImportedPayload,
    ReviewedPrehistoryArchive, digest, prehistory_event_id,
)


class PrehistoryArchiveRuntime:
    def __init__(self, *, ledger, owner_actor_ref):
        self.ledger = ledger
        self.owner_actor_ref = owner_actor_ref

    def import_reviewed(self, archive: ReviewedPrehistoryArchive, *, created_at):
        """Accept trusted operator-reviewed input, never an unreviewed role draft.

        Review checks happen before this interface. The importer rechecks its
        exact content binding, World/actor, original start, identities and CAS.
        One transaction accepts all missing records; repeated import is inert.
        """
        from .schemas import WorldEvent

        archive = ReviewedPrehistoryArchive.model_validate_json(json.dumps(
            canonicalize_json_value(archive.model_dump(mode="json")), ensure_ascii=False,
        ))
        document, review = archive.document, archive.review
        records = tuple(sorted(document.records, key=lambda value: value.record_id))
        if document.world_id != self.ledger.world_id or document.actor_ref != self.owner_actor_ref:
            raise ValueError("prehistory import World or actor mismatch")
        with self.ledger.serialized_commit_sequence():
            state = self.ledger.project()
            starts = [x for x in state.committed_world_event_refs if x.event_type == "WorldStarted"]
            if len(starts) != 1 or state.logical_time is None:
                raise ValueError("prehistory import requires the original WorldStarted")
            start = starts[0]
            # Validate all records before assembling or committing any event.
            for record in records:
                if record.occurred_until >= start.logical_time:
                    raise ValueError("prehistory cannot cover runtime life")
            known = {x.record.record_id for x in state.prehistory_records if x.actor_ref == document.actor_ref}
            proposed = {x.record_id for x in records}
            if any(set(x.related_record_refs) - known - proposed for x in records):
                raise ValueError("prehistory has unknown related records")
            manifest = document.manifest()
            accepted = PrehistoryArchiveAcceptedPayload(
                manifest=manifest, review=review, world_started_ref=start.event_id,
                world_started_revision=start.world_revision, world_started_hash=start.payload_hash,
                world_started_at=start.logical_time,
            )
            previous = next((x for x in state.prehistory_archives if x.manifest.archive_id == manifest.archive_id), None)
            if previous is not None and any(
                getattr(previous, key) != getattr(accepted, key)
                for key in PrehistoryArchiveAcceptedPayload.model_fields
            ):
                raise ValueError("prehistory archive identity already has different content")
            payloads = [] if previous is not None else [("CharacterPrehistoryArchiveAccepted", accepted)]
            existing = {x.record.record_id: x for x in state.prehistory_records}
            for record in records:
                current = existing.get(record.record_id)
                if current is not None:
                    if current.archive_id != manifest.archive_id or current.record != record:
                        raise ValueError("prehistory record identity already has different content")
                    continue
                payloads.append(("CharacterPrehistoryRecordImported", PrehistoryRecordImportedPayload(
                    archive_id=manifest.archive_id, archive_manifest_hash=digest(manifest), record=record,
                )))
            events = []
            for event_type, payload in payloads:
                event_id = prehistory_event_id(world_id=document.world_id, event_type=event_type, payload=payload)
                events.append(WorldEvent.from_payload(
                    schema_version="world-v2.1", event_id=event_id, world_id=document.world_id,
                    event_type=event_type, logical_time=state.logical_time,
                    created_at=max(created_at, review.reviewed_at, state.logical_time),
                    actor=IMPORT_ACTOR, source=SOURCE, trace_id="trace:" + event_id,
                    causation_id=start.event_id, correlation_id=manifest.archive_id,
                    idempotency_key=event_id, payload=payload.model_dump(mode="json"),
                ))
            if events:
                self.ledger.commit(
                    expected_world_revision=state.world_revision,
                    expected_deliberation_revision=state.deliberation_revision,
                    events=events,
                )
            by_id = {x.record.record_id: x for x in self.ledger.project().prehistory_records}
            return tuple(by_id[x.record_id] for x in records)
