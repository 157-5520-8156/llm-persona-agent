"""Publish exact versioned consequence bytes after their durable settlement."""

from __future__ import annotations

from .event_identity import domain_idempotency_key
from .life_content_store import StoredLifeContent, life_content_payload_hash
from .life_content_events import LifeContentRecordedPayload
from .proposal_audit_schemas import canonical_json
from .schemas import ProjectionCursor, WorldEvent
from .world_consequence_contract import WorldConsequenceV2


def read_world_consequence_candidate(*, content_store, candidate) -> StoredLifeContent:
    if candidate.result_contract != "world-consequence.2" or content_store is None:
        raise ValueError("world consequence requires its configured immutable content store")
    stored = content_store.read_exact(content_ref=candidate.content_ref)
    if (
        stored is None or stored.content_kind != "outcome_candidate"
        or stored.content_payload_hash != candidate.content_payload_hash
        or life_content_payload_hash(stored.text) != candidate.content_payload_hash
        or candidate.result_payload_hash.removeprefix("sha256:") != candidate.content_payload_hash
    ):
        raise ValueError("world consequence candidate content is unavailable")
    parsed = WorldConsequenceV2.model_validate_json(stored.text)
    if canonical_json(parsed.model_dump(mode="json")) != stored.text:
        raise ValueError("world consequence candidate content is not canonical")
    return stored


class OccurrenceResultContentRuntime:
    """Recoverable sidecar publication; it creates no Experience or character decision."""

    def __init__(self, *, ledger, content_store) -> None:
        self._ledger = ledger
        self._store = content_store

    def materialize(self, *, occurrence_id: str):
        projection = self._ledger.project()
        occurrence = next(
            (item for item in projection.world_occurrences if item.occurrence_id == occurrence_id),
            None,
        )
        if occurrence is None or occurrence.status != "settled":
            raise ValueError("consequence publication requires an exact settled occurrence")
        candidate = next(
            (item for item in occurrence.candidate_outcomes if item.result_id == occurrence.result_id),
            None,
        )
        if candidate is None or candidate.result_contract != "world-consequence.2":
            raise ValueError("consequence publication cannot upgrade a legacy occurrence")
        stored = read_world_consequence_candidate(content_store=self._store, candidate=candidate)
        if (
            occurrence.result_payload_ref != candidate.result_payload_ref
            or occurrence.result_payload_hash.removeprefix("sha256:") != stored.content_payload_hash
        ):
            raise ValueError("consequence publication changed its selected result")
        located = self._ledger.lookup_event_commit(occurrence.settlement_event_ref)
        if (
            located is None or located[0].event_type != "WorldOccurrenceSettled"
            or located[0].payload_hash != occurrence.settlement_payload_hash
            or located[1].world_revision != occurrence.settlement_world_revision
        ):
            raise ValueError("consequence publication lacks its original settlement")
        settlement, commit = located
        record = StoredLifeContent(
            content_ref=occurrence.result_payload_ref, content_kind="occurrence_result",
            content_payload_hash=stored.content_payload_hash, text=stored.text,
        )
        self._store.put_if_absent(record)
        suffix = life_content_payload_hash(canonical_json({
            "world_id": self._ledger.world_id, "settlement_event_ref": settlement.event_id,
        }))
        existing = next((item for item in projection.life_content_descriptors
                         if item.source_kind == "occurrence_settlement"
                         and item.source_event_ref == settlement.event_id), None)
        if existing is not None:
            if (existing.content_ref != record.content_ref
                    or existing.content_payload_hash != record.content_payload_hash):
                raise ValueError("consequence publication conflicts with its original descriptor")
            return None
        payload = LifeContentRecordedPayload(
            content_id="life-content:world-consequence:" + suffix,
            content_kind="occurrence_result", content_ref=record.content_ref,
            content_payload_hash=record.content_payload_hash, privacy_class=occurrence.visibility,
            source_kind="occurrence_settlement", source_event_ref=settlement.event_id,
            source_world_revision=commit.world_revision, source_payload_hash=settlement.payload_hash,
            source_entity_id=occurrence.occurrence_id,
            source_entity_revision=occurrence.entity_revision,
        ).model_dump(mode="json")
        event = WorldEvent.from_payload(
            schema_version="world-v2.1", event_id="event:world-consequence-content:" + suffix,
            world_id=self._ledger.world_id, event_type="LifeContentRecorded",
            logical_time=projection.logical_time, created_at=projection.logical_time,
            actor=settlement.actor, source="world-v2:world-consequence-content",
            trace_id=settlement.trace_id, causation_id=settlement.event_id,
            correlation_id=settlement.correlation_id,
            idempotency_key=domain_idempotency_key(
                event_type="LifeContentRecorded", world_id=self._ledger.world_id, payload=payload,
            ) or "world-consequence-content:" + suffix,
            payload=payload,
        )
        return self._ledger.commit_at_cursor(
            (event,), expected_cursor=ProjectionCursor(
                world_revision=projection.world_revision,
                deliberation_revision=projection.deliberation_revision,
                ledger_sequence=projection.ledger_sequence,
            ), commit_id="commit:world-consequence-content:" + suffix,
        )
