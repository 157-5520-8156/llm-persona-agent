"""Bounded, recoverable CharacterInterior initialization of imported memory."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from .character_prehistory import digest
from .event_identity import domain_idempotency_key
from .fact_memory_candidate_lifecycle import FactMemoryCandidateLifecycle
from .fact_memory_draft import FactMemoryDraftTechnicalFailure
from .prehistory_memory_decision import (
    ACTOR, EVENT_TYPE, SOURCE, PrehistoryMemoryDecisionRecordedPayload, decision_event_id,
    initialization_opportunity, retention_draft, retention_model_result,
)
from .prehistory_memory_source import prehistory_memory_binding, resolve_prehistory_memory_source
from .schemas import ProjectionCursor, WorldEvent


class PrehistoryMemoryRuntime:
    """Consider at most one source per call; completed choices need no model."""

    def __init__(self, *, ledger, owner_actor_ref, character_interior):
        self.ledger = ledger
        self.actor_ref = owner_actor_ref
        self.interior = character_interior
        self._lock = asyncio.Lock()
        self._lifecycle = FactMemoryCandidateLifecycle(ledger=ledger, actor=ACTOR, source=SOURCE)

    def _recorded(self, source, failure_ordinal=None):
        found = self.ledger.lookup_event_commit(decision_event_id(
            self.ledger.world_id, source, failure_ordinal=failure_ordinal,
        ))
        if found is None:
            return None
        event, _ = found
        payload = PrehistoryMemoryDecisionRecordedPayload.model_validate_json(event.payload_json)
        if event.event_type != EVENT_TYPE or payload.source_binding != source:
            raise ValueError("prehistory retention durable decision identity mismatch")
        if payload.opportunity != self._opportunity(source):
            raise ValueError("prehistory retention no longer binds its exact import commit cursor")
        return payload

    def _opportunity(self, source):
        found = self.ledger.lookup_event_commit(source.authority_event_ref)
        if found is None:
            raise ValueError("prehistory retention import commit is missing")
        _, commit = found
        cursor = ProjectionCursor(world_revision=commit.world_revision,
            deliberation_revision=commit.deliberation_revision, ledger_sequence=commit.ledger_sequence)
        pinned = self.ledger.project_at(cursor)
        row, archive = resolve_prehistory_memory_source(source, records=pinned.prehistory_records,
            archives=pinned.prehistory_archives, committed_events=pinned.committed_world_event_refs)
        if row.actor_ref != self.actor_ref:
            raise ValueError("prehistory retention belongs to another character")
        return initialization_opportunity(world_id=self.ledger.world_id, row=row, archive=archive, cursor=cursor)

    def _recovered(self, source, opportunity):
        values = self.interior.completed_considerations_for_source(
            world_id=self.ledger.world_id, actor_ref=self.actor_ref,
            purpose=opportunity.purpose, source_ref=source.authority_event_ref,
        )
        matches = [value for value in values if value.opportunity_ref == opportunity.opportunity_ref
                   and value.cursor == opportunity.cursor]
        if len(matches) > 1:
            raise ValueError("prehistory retention has multiple terminal choices")
        return matches[0] if matches else None

    async def advance_once(self, *, allow_model_call: bool = False):
        """The caller opens paid initialization explicitly, using the same model budget.

        Default calls recover recorded/paid choices only. They never interpret
        a paused budget, provider failure or missing choice as role no_change.
        """
        async with self._lock:
            projection = self.ledger.project()
            pending_status = "idle"
            for row in projection.prehistory_records:
                if row.actor_ref != self.actor_ref:
                    continue
                source = prehistory_memory_binding(row)
                recorded = self._recorded(source)
                if recorded is not None:
                    candidate = self._apply(recorded)
                    if candidate is not None:
                        return {"status": "retained", "record_id": source.source_id}
                    continue
                failures = tuple(value for ordinal in range(1, 4)
                                 if (value := self._recorded(source, ordinal)) is not None)
                if tuple(item.attempt_ordinal for item in failures) != tuple(range(1, len(failures) + 1)):
                    raise ValueError("prehistory retention retry audit has a missing predecessor")
                opportunity = self._opportunity(source)
                result = self._recovered(source, opportunity)
                ordinal = len(failures) + 1
                if result is None:
                    if ordinal > 3:
                        pending_status = "retry_exhausted"
                        continue
                    if not allow_model_call:
                        pending_status = "model_call_disabled"
                        continue
                    if failures and failures[-1].next_retry_at > projection.logical_time:
                        pending_status = "retry_wait"
                        continue
                    result = await self.interior.consider(opportunity)
                # A paid terminal can still be materialized after retry exhaustion.
                ordinal = min(ordinal, 3)
                try:
                    draft = retention_draft(result, opportunity)
                    audit = retention_model_result(result, opportunity)
                except (FactMemoryDraftTechnicalFailure, ValueError) as exc:
                    code = getattr(exc, "failure_code", "prehistory_memory.invalid_role_lineage")
                    self._record(source, opportunity, ordinal, status="technical_failure", failure_code=code)
                    return {"status": "technical_failure", "record_id": source.source_id, "failure_code": code}
                payload = self._record(source, opportunity, ordinal,
                    status="retain" if draft is not None else "no_change", result=result, audit=audit)
                self._apply(payload)
                return {"status": "retained" if payload.status == "retain" else "no_change", "record_id": source.source_id}
            return {"status": pending_status}

    def _record(self, source, opportunity, ordinal, *, status, result=None, audit=None, failure_code=None):
        if opportunity != self._opportunity(source):
            raise ValueError("prehistory retention must use its exact import commit snapshot")
        with self.ledger.serialized_commit_sequence():
            existing = self._recorded(source, ordinal if status == "technical_failure" else None)
            if existing is not None:
                return existing
            projection = self.ledger.project()
            now = projection.logical_time
            payload = PrehistoryMemoryDecisionRecordedPayload(
                source_binding=source, opportunity=opportunity, attempt_ordinal=ordinal, status=status,
                recorded_at=now, result=result, character_interior_model_result=audit, failure_code=failure_code,
                next_retry_at=(now + timedelta(seconds=(30, 120)[ordinal - 1])
                               if status == "technical_failure" and ordinal < 3 else None),
            )
            event_id = decision_event_id(self.ledger.world_id, source,
                failure_ordinal=ordinal if status == "technical_failure" else None)
            raw = payload.model_dump(mode="json")
            event = WorldEvent.from_payload(schema_version="world-v2.1", event_id=event_id,
                world_id=self.ledger.world_id, event_type=EVENT_TYPE, logical_time=now,
                created_at=max(datetime.now(UTC), now),
                actor=ACTOR, source=SOURCE, trace_id="trace:" + event_id,
                causation_id=source.authority_event_ref, correlation_id=source.source_id,
                idempotency_key=domain_idempotency_key(event_type=EVENT_TYPE, world_id=self.ledger.world_id, payload=raw),
                payload=raw)
            self.ledger.commit((event,), expected_world_revision=projection.world_revision,
                               expected_deliberation_revision=projection.deliberation_revision)
            return payload

    def _apply(self, payload):
        if payload.status != "retain":
            return None
        source = payload.source_binding
        with self.ledger.serialized_commit_sequence():
            projection = self.ledger.project()
            row, _ = resolve_prehistory_memory_source(source, records=projection.prehistory_records,
                archives=projection.prehistory_archives, committed_events=projection.committed_world_event_refs)
            if row.actor_ref != self.actor_ref or self._recorded(source) != payload:
                raise ValueError("prehistory retention cannot apply an unrecorded or foreign choice")
            existing = next((item for item in projection.memory_candidates
                             if source in item.values.source_bindings), None)
            if existing is not None and existing.values.status != "pending":
                return None  # A later rejection or forgetting is never reopened.
            candidate_id = "memory:prehistory:" + digest(source)
            if existing is not None and existing.candidate_id != candidate_id:
                raise ValueError("prehistory source already has another pending memory identity")
            draft = retention_draft(payload.result, payload.opportunity)
            now = projection.logical_time
            before = existing
            for revision, status, operation in ((1, "pending", "open"), (2, "active", "accept")):
                if revision == 1 and existing is not None:
                    continue
                after = self._lifecycle._candidate(
                    candidate_id=candidate_id, source=source, draft=draft, privacy_ceiling=row.record.privacy_class,
                    entity_revision=revision, status=status, opened_at=before.opened_at if before else now,
                    updated_at=now, reviewed_at=now if status == "active" else None,
                    accepted_event_ref=f"event:memory:prehistory:{digest(source)}:{operation}",
                )
                self._lifecycle._record_and_accept(after=after, before=before, operation=operation,
                    logical_time=now, created_at=now, trace_id="trace:prehistory-memory:" + digest(source),
                    correlation_id=source.source_id)
                before = after
            return before
