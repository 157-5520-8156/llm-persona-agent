"""Shared lease, event and CAS mechanics for source-bound memory reviews."""
from __future__ import annotations
import asyncio
from datetime import timedelta
import hashlib
import json
from .errors import ConcurrencyConflict, IdempotencyConflict
from .event_identity import domain_idempotency_key
from .schemas import ClaimLease, ProjectionCursor, WorldEvent
from .sqlite_ledger import SQLiteWorldLedger
from .character_interior.core import CharacterInterior

def _digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",", ":")).encode()).hexdigest()

class MemoryReviewLease:
    """No semantic decision or candidate selection; both remain with callers."""
    def __init__(
        self,
        *,
        ledger: SQLiteWorldLedger,
        character_interior: CharacterInterior,
        actor_ref: str,
        owner_id: str,
        lease_seconds: int = 120,
        source: str = "world-v2:memory-withdrawal-review",
    ) -> None:
        if type(ledger) is not SQLiteWorldLedger:
            raise ValueError("memory withdrawal review requires the production SQLite ledger")
        if not actor_ref or not owner_id or not source or lease_seconds <= 0:
            raise ValueError(
                "memory withdrawal review requires character, owner, source, and positive lease"
            )
        if not callable(getattr(character_interior, "consider", None)):
            raise TypeError("memory withdrawal review requires CharacterInterior.consider")
        self._ledger = ledger
        self._character_interior = character_interior
        self._actor_ref = actor_ref
        self._owner_id = owner_id
        self._lease_seconds = lease_seconds
        self._source = source


    @property
    def ledger(self) -> SQLiteWorldLedger:
        return self._ledger


    async def _claim_or_reclaim(self, *, process, source_event, projection):
        at = projection.logical_time or source_event.logical_time
        if process.state == "claimed" and process.claim_lease is not None:
            if (
                process.claim_lease.owner_id == self._owner_id
                and at < process.claim_lease.expires_at
            ):
                return process
            if at < process.claim_lease.expires_at:
                return None
        attempt_id = "attempt:memory-review:" + _digest(
            {"trigger_id": process.trigger_id, "attempt": len(process.attempt_ids) + 1}
        )
        claimed = process.model_copy(
            update={
                "state": "claimed",
                "claim_lease": ClaimLease(
                    owner_id=self._owner_id,
                    attempt_id=attempt_id,
                    acquired_at=at,
                    expires_at=at + timedelta(seconds=self._lease_seconds),
                ),
                "attempt_ids": (*process.attempt_ids, attempt_id),
            }
        )
        event_type = (
            "TriggerProcessClaimed" if process.state == "open" else "TriggerProcessReclaimed"
        )
        event = self._event(
            event_id=f"event:memory-review:{event_type.lower()}:"
            + _digest([process.trigger_id, attempt_id]),
            event_type=event_type,
            payload={"process": claimed.model_dump(mode="json")},
            logical_time=at,
            created_at=source_event.created_at,
            trace_id=source_event.trace_id,
            causation_id=source_event.event_id,
            correlation_id=source_event.correlation_id,
        )
        try:
            await self._commit_at_cursor(
                (event,),
                cursor=self._cursor(projection),
                commit_id=f"commit:memory-review:{event_type.lower()}:"
                + _digest([process.trigger_id, attempt_id]),
            )
        except (ConcurrencyConflict, IdempotencyConflict):
            joined = await self._project()
            current = next(
                item for item in joined.trigger_processes if item.trigger_id == process.trigger_id
            )
            if (
                current.state == "claimed"
                and current.claim_lease is not None
                and current.claim_lease.owner_id == self._owner_id
            ):
                return current
            return None
        return claimed


    async def _complete(
        self,
        *,
        process,
        source_event,
        outcome_ref,
        model_result_audit=None,
    ) -> None:
        projection = await self._project()
        event = self._completion_event(
            process=process,
            source_event=source_event,
            at=projection.logical_time or source_event.logical_time,
            outcome_ref=outcome_ref,
            causation_id=source_event.event_id,
            model_result_audit=model_result_audit,
        )
        await self._commit_at_cursor(
            (event,),
            cursor=self._cursor(projection),
            commit_id="commit:memory-review:complete:" + _digest([process.trigger_id, outcome_ref]),
        )


    def _completion_event(
        self,
        *,
        process,
        source_event,
        at,
        outcome_ref,
        causation_id,
        model_result_audit=None,
    ):
        if process.claim_lease is None or at > process.claim_lease.expires_at:
            raise ValueError("memory review completion requires a live claim")
        payload = {
            "trigger_id": process.trigger_id,
            "owner_id": process.claim_lease.owner_id,
            "attempt_id": process.claim_lease.attempt_id,
            "completed_at": max(at, process.claim_lease.acquired_at).isoformat(),
            "runtime_outcome_ref": outcome_ref,
            **(
                {
                    "character_interior_model_result": (
                        model_result_audit.model_dump(mode="json")
                    )
                }
                if model_result_audit is not None
                else {}
            ),
        }
        return self._event(
            event_id="event:memory-review:completed:"
            + _digest([process.trigger_id, process.claim_lease.attempt_id]),
            event_type="TriggerProcessCompleted",
            payload=payload,
            logical_time=max(at, process.claim_lease.acquired_at),
            created_at=source_event.created_at,
            trace_id=source_event.trace_id,
            causation_id=causation_id,
            correlation_id=source_event.correlation_id,
            idempotency_key="world-v2:memory-review:completed:"
            + _digest([self._ledger.world_id, process.trigger_id, process.claim_lease.attempt_id]),
        )


    def _event(
        self,
        *,
        event_id,
        event_type,
        payload,
        logical_time,
        created_at,
        trace_id,
        causation_id,
        correlation_id,
        idempotency_key=None,
    ) -> WorldEvent:
        identity = idempotency_key or domain_idempotency_key(
            event_type=event_type, world_id=self._ledger.world_id, payload=payload
        )
        if identity is None:
            raise ValueError(f"memory review has no identity for {event_type}")
        return WorldEvent.from_payload(
            schema_version="world-v2.1",
            event_id=event_id,
            world_id=self._ledger.world_id,
            event_type=event_type,
            logical_time=logical_time,
            created_at=created_at,
            actor=self._owner_id,
            source=self._source,
            trace_id=trace_id,
            causation_id=causation_id,
            correlation_id=correlation_id,
            idempotency_key=identity,
            payload=payload,
        )


    @staticmethod
    def _cursor(projection) -> ProjectionCursor:
        return ProjectionCursor(
            world_revision=projection.world_revision,
            deliberation_revision=projection.deliberation_revision,
            ledger_sequence=projection.ledger_sequence,
        )


    async def _project(self):
        return await asyncio.to_thread(self._ledger.project)


    async def _lookup(self, event_id):
        return await asyncio.to_thread(self._ledger.lookup_event_commit, event_id)


    async def _commit_at_cursor(self, events, *, cursor, commit_id):
        return await asyncio.to_thread(
            self._ledger.commit_at_cursor,
            events,
            expected_cursor=cursor,
            commit_id=commit_id,
        )
