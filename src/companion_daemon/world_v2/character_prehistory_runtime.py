"""Atomic history admission and source-bound initial-memory installation."""
from __future__ import annotations

import json
from dataclasses import dataclass
from .schema_core import canonicalize_json_value

from .character_prehistory import (
    IMPORT_ACTOR, SOURCE, COMPILED_SOURCE, PrehistoryArchiveAcceptedPayload, PrehistoryRecordImportedPayload,
    ReviewedPrehistoryArchive, CompiledPrehistoryArchive, digest, prehistory_event_id, validate_story_boundary,
)


@dataclass(frozen=True, slots=True)
class PrehistoryInstallation:
    records: tuple
    memory_initialization: dict


class PrehistoryArchiveRuntime:
    def __init__(self, *, ledger, owner_actor_ref):
        self.ledger = ledger
        self.owner_actor_ref = owner_actor_ref

    def import_reviewed(self, archive: ReviewedPrehistoryArchive, *, created_at):
        archive = ReviewedPrehistoryArchive.model_validate_json(archive.model_dump_json())
        return self._import(archive, created_at=created_at)

    def import_compiled(self, archive: CompiledPrehistoryArchive, *, created_at):
        archive = CompiledPrehistoryArchive.model_validate_json(archive.model_dump_json())
        return self._import(archive, created_at=created_at)

    def install_compiled(self, archive, *, created_at, memory_mode="direct"):
        """User-facing installation; raw import_compiled remains archive admission."""
        return self._install(archive, created_at=created_at, memory_mode=memory_mode)

    def install_reviewed(self, archive, *, created_at, memory_mode="direct"):
        return self._install(archive, created_at=created_at, memory_mode=memory_mode)

    def _install(self, archive, *, created_at, memory_mode):
        if memory_mode not in {"direct", "archive_only"}:
            raise ValueError("installation memory_mode must be direct or archive_only")
        rows = (self.import_compiled(archive, created_at=created_at) if isinstance(archive, CompiledPrehistoryArchive)
                else self.import_reviewed(archive, created_at=created_at))
        report = {"status": "archive_only", "mode": memory_mode, "initialized": 0, "model_calls": 0}
        if memory_mode == "direct":
            from .prehistory_memory_initialization import PrehistoryMemoryInitializer
            report = PrehistoryMemoryInitializer(ledger=self.ledger, owner_actor_ref=self.owner_actor_ref).advance_once(
                limit=256, record_refs=tuple(row.record.record_id for row in rows))
            if report['existing_pending']:
                raise ValueError('initialization contains a legacy pending candidate; recover its original workflow')
        return PrehistoryInstallation(records=rows, memory_initialization=report)

    def _import(self, archive, *, created_at):
        """Accept operator-authorized reviewed history or compiled native source.

        Review checks happen before this interface. The importer rechecks its
        exact content binding, World/actor, original start, identities and CAS.
        One transaction accepts all missing records; repeated import is inert.
        """
        from .schemas import WorldEvent

        archive = type(archive).model_validate_json(json.dumps(
            canonicalize_json_value(archive.model_dump(mode="json")), ensure_ascii=False,
        ))
        document = archive.document
        review = getattr(archive, 'review', None)
        compilation = getattr(archive, 'compilation', None)
        evidence_time = review.reviewed_at if review is not None else compilation.source_authored_at
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
                validate_story_boundary(record, actor_ref=document.actor_ref, world_started_at=start.logical_time)
                if record.occurred_until >= start.logical_time:
                    raise ValueError("prehistory cannot cover runtime life")
            known = {x.record.record_id for x in state.prehistory_records if x.actor_ref == document.actor_ref}
            proposed = {x.record_id for x in records}
            if any(set(x.related_record_refs) - known - proposed for x in records):
                raise ValueError("prehistory has unknown related records")
            manifest = document.manifest()
            accepted = PrehistoryArchiveAcceptedPayload(
                contract='character-prehistory-acceptance.1' if review is not None else 'character-prehistory-acceptance.2',
                manifest=manifest, review=review, compilation=compilation, world_started_ref=start.event_id,
                world_started_revision=start.world_revision, world_started_hash=start.payload_hash,
                world_started_at=start.logical_time,
            )
            previous = next((x for x in state.prehistory_archives if x.manifest.archive_id == manifest.archive_id), None)
            if previous is not None and any(
                getattr(previous, key) != getattr(accepted, key)
                for key in PrehistoryArchiveAcceptedPayload.model_fields
            ):
                raise ValueError("prehistory archive identity already has different content")
            if previous is None:
                from .story_import import validate_import_basis
                validate_import_basis(manifest, state=state)
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
                    created_at=max(created_at, evidence_time, state.logical_time),
                    actor=IMPORT_ACTOR, source=SOURCE if review is not None else COMPILED_SOURCE, trace_id="trace:" + event_id,
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
