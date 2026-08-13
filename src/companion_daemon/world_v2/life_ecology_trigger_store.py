"""Ownership for one source-bound Life Ecology wake.

Claim, lease, retry, and silent no-new-fact completions live in a disposable
sidecar.  Semantic outcomes still finish as ledger TriggerProcess events so
replay and schedule projection remain intact.  Competing workers converge on
the sidecar lease rather than appending a second ecology run to the ledger.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import hashlib
import json
import re

from .errors import ConcurrencyConflict, IdempotencyConflict
from .event_identity import domain_idempotency_key
from .life_ecology_lease_store import (
    LifeEcologyLeaseRecord,
    LifeEcologyLeaseStore,
    SILENT_LIFE_ECOLOGY_OUTCOMES,
    lease_store_for_ledger,
)
from .life_ecology_contract import (
    LIFE_ECOLOGY_PROCESS_KIND,
    LIFE_ECOLOGY_WAKE_EVENT_TYPES,
    LifeEcologyRunClaim,
    LifeEcologyRunKey,
    life_ecology_trigger_id,
    life_ecology_trigger_ref,
    parse_life_ecology_trigger_ref,
    validate_life_ecology_run_key,
)
from .random_authority import RandomAuthority
from .schemas import ClaimLease, TriggerProcess, WorldEvent


_OUTCOME = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_MAX_CAS_RETRIES = 8
_AMBIENT_CADENCE_OUTCOMES = frozenset(
    {
        "idle",
        "author_idle",
        "author_no_opening",
        "life_development_no_op",
        "life_development_plan_committed",
        "life_development_occurrence_committed",
        "life_development_recovered",
    }
)
_AMBIENT_CADENCE_SECONDS = (2700, 7200, 14_400, 21_600, 28_800)
_SEMANTIC_STIMULUS_OUTCOMES = frozenset(
    {
        "activity_transitioned",
        "biographical_transitioned",
        "aftermath_occurrence_opened",
        "aftermath_settled",
        "aftermath_recovered_experience",
        "aftermath_recovered_memory",
    }
)
_SEMANTIC_STIMULUS_SECONDS = (120, 900, 2700)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _silent_cadence_seconds(trigger_id: str, outcome: str) -> int:
    if outcome == "cooldown":
        return 0
    digest = hashlib.sha256(
        f"{trigger_id}:{outcome}:life-ecology-ambient-cadence.1".encode()
    ).digest()
    return _AMBIENT_CADENCE_SECONDS[int.from_bytes(digest[:8], "big") % len(_AMBIENT_CADENCE_SECONDS)]


class LedgerLifeEcologyTriggerStore:
    """Durable Life Ecology trigger adapter.

    Live claim/lease/retry state is a disposable sidecar.  Silent outcomes do
    not append TriggerProcess events.  Semantic outcomes still commit the
    ledger process lineage so schedule projection and replay stay intact.
    """

    def __init__(
        self,
        *,
        ledger,
        owner_id: str,
        lease_seconds: int = 120,
        source: str = "world-v2:life-ecology-trigger-store",
        lease_store: LifeEcologyLeaseStore | None = None,
    ) -> None:
        if not isinstance(owner_id, str) or not owner_id:
            raise ValueError("life ecology trigger store requires owner_id")
        if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool) or lease_seconds <= 0:
            raise ValueError("life ecology trigger store requires positive lease_seconds")
        if not isinstance(source, str) or not source:
            raise ValueError("life ecology trigger store requires source")
        self._ledger = ledger
        self._owner_id = owner_id
        self._lease_seconds = lease_seconds
        self._source = source
        self._leases = lease_store if lease_store is not None else lease_store_for_ledger(ledger)

    def next_consideration_at(self) -> datetime | None:
        overlay = self._leases.overlay()
        if overlay is not None and overlay.next_consideration_at is not None:
            return overlay.next_consideration_at
        schedule = getattr(self._ledger.project(), "life_ecology_schedule", None)
        if schedule is None:
            return None
        return schedule.next_consideration_at

    async def claim_or_join(
        self, *, key: LifeEcologyRunKey, trace_id: str, correlation_id: str
    ) -> LifeEcologyRunClaim:
        validate_life_ecology_run_key(key)
        if key.world_id != self._ledger.world_id:
            raise ValueError("life ecology run key belongs to another world")
        if not isinstance(trace_id, str) or not trace_id:
            raise ValueError("life ecology claim requires trace_id")
        if not isinstance(correlation_id, str) or not correlation_id:
            raise ValueError("life ecology claim requires correlation_id")

        trigger_id = life_ecology_trigger_id(
            world_id=key.world_id,
            wake_event_ref=key.wake_event_ref,
            catalog_version=key.catalog_version,
        )
        projection = await self._project()
        await self._verified_wake(key=key, projection=projection)
        if trigger_id in projection.completed_trigger_ids:
            return LifeEcologyRunClaim(trigger_id=trigger_id, state="completed")
        schedule = getattr(projection, "life_ecology_schedule", None)
        if schedule is not None and schedule.last_trigger_id == trigger_id:
            return LifeEcologyRunClaim(trigger_id=trigger_id, state="completed")
        process = next(
            (item for item in projection.trigger_processes if item.trigger_id == trigger_id),
            None,
        )
        if process is not None:
            if process.process_kind != LIFE_ECOLOGY_PROCESS_KIND:
                raise ValueError("life ecology trigger identity is occupied by another process kind")
            if process.state == "terminal":
                return LifeEcologyRunClaim(trigger_id=trigger_id, state="completed")
        lease = self._leases.get(trigger_id)
        if lease is not None and lease.state == "completed":
            return LifeEcologyRunClaim(trigger_id=trigger_id, state="completed")
        logical_time = projection.logical_time or (
            await self._source_event_ref(key.wake_event_ref)
        ).logical_time
        if lease is None:
            wake_revision = next(
                (
                    item.world_revision
                    for item in projection.committed_world_event_refs
                    if item.event_id == key.wake_event_ref
                ),
                None,
            )
            watermark_revision = next(
                (
                    item.world_revision
                    for item in projection.committed_world_event_refs
                    if schedule is not None
                    and item.event_id == schedule.last_wake_event_ref
                ),
                None,
            )
            if (
                wake_revision is not None
                and watermark_revision is not None
                and wake_revision <= watermark_revision
            ):
                completed_outcome_ref = await self._completed_outcome_ref(
                    trigger_id=trigger_id,
                    projection=projection,
                )
                if completed_outcome_ref is not None:
                    return LifeEcologyRunClaim(
                        trigger_id=trigger_id,
                        state="completed",
                    )
            attempt_ordinal = 1
        elif lease.state == "claimed" and logical_time <= lease.expires_at:
            return LifeEcologyRunClaim(trigger_id=trigger_id, state="joined")
        else:
            attempt_ordinal = lease.attempt_ordinal + 1
        attempt_id = "attempt:life-ecology:" + _digest(
            {"trigger_id": trigger_id, "attempt": attempt_ordinal}
        )
        state, _stored = self._leases.occupy(
            record=LifeEcologyLeaseRecord(
                world_id=key.world_id,
                trigger_id=trigger_id,
                wake_event_ref=key.wake_event_ref,
                catalog_version=key.catalog_version,
                owner_id=self._owner_id,
                attempt_id=attempt_id,
                attempt_ordinal=attempt_ordinal,
                acquired_at=logical_time,
                expires_at=logical_time + timedelta(seconds=self._lease_seconds),
                state="claimed",
            ),
            logical_time=logical_time,
        )
        return LifeEcologyRunClaim(trigger_id=trigger_id, state=state)

    async def complete(
        self,
        *,
        key: LifeEcologyRunKey,
        trigger_id: str,
        outcome: str,
        character_interior_model_result=None,
    ) -> None:
        validate_life_ecology_run_key(key)
        if key.world_id != self._ledger.world_id:
            raise ValueError("life ecology run key belongs to another world")
        if not isinstance(outcome, str) or not _OUTCOME.fullmatch(outcome):
            raise ValueError("life ecology completion outcome is invalid")
        expected_trigger_id = life_ecology_trigger_id(
            world_id=key.world_id,
            wake_event_ref=key.wake_event_ref,
            catalog_version=key.catalog_version,
        )
        if trigger_id != expected_trigger_id:
            raise ValueError("life ecology completion does not bind its run key")

        lease = self._leases.get(trigger_id)
        if lease is not None and lease.state == "completed":
            if lease.outcome == outcome:
                return
            raise ValueError("life ecology terminal outcome conflicts with completion")

        for _ in range(_MAX_CAS_RETRIES):
            projection = await self._project()
            process = next(
                (item for item in projection.trigger_processes if item.trigger_id == trigger_id),
                None,
            )
            schedule = getattr(projection, "life_ecology_schedule", None)
            if process is None and (
                schedule is not None
                and schedule.last_trigger_id == trigger_id
                and schedule.last_outcome_ref == f"life-ecology:{outcome}"
            ):
                return
            if process is None and lease is None:
                wake_revision = next(
                    (
                        item.world_revision
                        for item in projection.committed_world_event_refs
                        if item.event_id == key.wake_event_ref
                    ),
                    None,
                )
                watermark_revision = next(
                    (
                        item.world_revision
                        for item in projection.committed_world_event_refs
                        if schedule is not None
                        and item.event_id == schedule.last_wake_event_ref
                    ),
                    None,
                )
                if (
                    wake_revision is not None
                    and watermark_revision is not None
                    and wake_revision <= watermark_revision
                ):
                    completed_outcome_ref = await self._completed_outcome_ref(
                        trigger_id=trigger_id,
                        projection=projection,
                    )
                    if completed_outcome_ref is None:
                        raise ValueError("life ecology trigger is unavailable")
                    if completed_outcome_ref != f"life-ecology:{outcome}":
                        raise ValueError(
                            "life ecology terminal outcome conflicts with completion"
                        )
                    return
                raise ValueError("life ecology trigger is unavailable")
            claimed = process
            source_event: WorldEvent
            if claimed is None:
                if lease is None or lease.state != "claimed":
                    raise ValueError("life ecology trigger is unavailable")
                if lease.owner_id != self._owner_id:
                    raise ValueError("life ecology completion does not own the active claim lease")
                source_event = await self._source_event_ref(lease.wake_event_ref)
                logical_time = projection.logical_time or source_event.logical_time
                completed_at = max(logical_time, lease.acquired_at)
                if completed_at > lease.expires_at:
                    raise ValueError("life ecology lease expired before completion")
                opened = TriggerProcess(
                    trigger_id=trigger_id,
                    trigger_ref=life_ecology_trigger_ref(
                        wake_event_ref=key.wake_event_ref,
                        catalog_version=key.catalog_version,
                    ),
                    process_kind=LIFE_ECOLOGY_PROCESS_KIND,
                    source_evidence_ref=key.wake_event_ref,
                    state="open",
                )
                claimed = opened.model_copy(
                    update={
                        "state": "claimed",
                        "claim_lease": ClaimLease(
                            owner_id=lease.owner_id,
                            attempt_id=lease.attempt_id,
                            acquired_at=lease.acquired_at,
                            expires_at=lease.expires_at,
                        ),
                        "attempt_ids": (lease.attempt_id,),
                    }
                )
            else:
                if claimed.process_kind != LIFE_ECOLOGY_PROCESS_KIND:
                    raise ValueError("life ecology trigger is unavailable")
                outcome_ref = f"life-ecology:{outcome}"
                if claimed.state == "terminal":
                    if claimed.runtime_outcome_ref != outcome_ref:
                        raise ValueError(
                            "life ecology terminal outcome conflicts with completion"
                        )
                    return
                if claimed.state != "claimed" or claimed.claim_lease is None:
                    raise ValueError("life ecology trigger must be claimed before completion")
                if claimed.claim_lease.owner_id != self._owner_id:
                    raise ValueError(
                        "life ecology completion does not own the active claim lease"
                    )
                source_event = await self._source_event(claimed)
                logical_time = projection.logical_time or source_event.logical_time
                completed_at = max(logical_time, claimed.claim_lease.acquired_at)
                if completed_at > claimed.claim_lease.expires_at:
                    raise ValueError("life ecology lease expired before completion")
                opened = None
            assert claimed.claim_lease is not None
            if outcome in SILENT_LIFE_ECOLOGY_OUTCOMES:
                delay_seconds = _silent_cadence_seconds(trigger_id, outcome)
                next_due = (
                    None
                    if outcome == "cooldown"
                    else completed_at + timedelta(seconds=delay_seconds)
                )
                self._leases.complete(
                    trigger_id=trigger_id,
                    outcome=outcome,
                    completed_at=completed_at,
                    next_consideration_at=next_due,
                )
                return
            cadence_draw_ref: str | None = None
            cadence_delay_seconds: int | None = None
            cadence_reused = (
                outcome in _SEMANTIC_STIMULUS_OUTCOMES
                and schedule is not None
                and schedule.last_outcome_ref.removeprefix("life-ecology:")
                in _SEMANTIC_STIMULUS_OUTCOMES
                and completed_at < schedule.next_consideration_at
            )
            cadence_candidates = (
                _AMBIENT_CADENCE_SECONDS
                if outcome in _AMBIENT_CADENCE_OUTCOMES
                else _SEMANTIC_STIMULUS_SECONDS
                if outcome in _SEMANTIC_STIMULUS_OUTCOMES
                else None
            )
            if cadence_candidates is not None and not cadence_reused:
                draw = await self._consideration_cadence_draw(
                    process=claimed,
                    source_event=source_event,
                    outcome=outcome,
                    logical_time=logical_time,
                    candidate_seconds=cadence_candidates,
                )
                cadence_draw_ref = "event:random-draw:" + draw.draw_id
                cadence_delay_seconds = int(
                    draw.selected_candidate_ref.removeprefix(
                        "life-ecology-cadence-seconds:"
                    )
                )
                projection = await self._project()
            payload = {
                "trigger_id": claimed.trigger_id,
                "owner_id": claimed.claim_lease.owner_id,
                "attempt_id": claimed.claim_lease.attempt_id,
                "completed_at": completed_at.isoformat(),
                "runtime_outcome_ref": f"life-ecology:{outcome}",
                "cadence_draw_event_ref": cadence_draw_ref,
                "cadence_delay_seconds": cadence_delay_seconds,
                "cadence_reused": cadence_reused,
                **(
                    {
                        "character_interior_model_result": (
                            character_interior_model_result.model_dump(mode="json")
                        )
                    }
                    if character_interior_model_result is not None
                    else {}
                ),
            }
            completed_event = WorldEvent.from_payload(
                schema_version="world-v2.1",
                event_id="event:life-ecology:completed:"
                + _digest([claimed.trigger_id, claimed.claim_lease.attempt_id, outcome]),
                world_id=key.world_id,
                event_type="TriggerProcessCompleted",
                logical_time=completed_at,
                created_at=source_event.created_at,
                actor=self._owner_id,
                source=self._source,
                trace_id=source_event.trace_id,
                causation_id=source_event.event_id,
                correlation_id=source_event.correlation_id,
                idempotency_key="world-v2:life-ecology-trigger:completed:"
                + _digest(
                    [key.world_id, claimed.trigger_id, claimed.claim_lease.attempt_id]
                ),
                payload=payload,
            )
            events = (completed_event,)
            if process is None and opened is not None:
                events = (
                    self._opened_event(
                        process=opened,
                        source_event=source_event,
                        logical_time=completed_at,
                        trace_id=source_event.trace_id,
                        correlation_id=source_event.correlation_id,
                    ),
                    self._claim_event(
                        process=claimed,
                        event_type="TriggerProcessClaimed",
                        source_event=source_event,
                        logical_time=completed_at,
                        trace_id=source_event.trace_id,
                        correlation_id=source_event.correlation_id,
                    ),
                    completed_event,
                )
            if await self._try_commit(events, projection=projection):
                after = await self._project()
                after_schedule = getattr(after, "life_ecology_schedule", None)
                self._leases.complete(
                    trigger_id=trigger_id,
                    outcome=outcome,
                    completed_at=completed_at,
                    next_consideration_at=(
                        after_schedule.next_consideration_at
                        if after_schedule is not None
                        else None
                    ),
                )
                return
        raise ConcurrencyConflict("life ecology trigger completion did not converge")

    async def _consideration_cadence_draw(
        self,
        *,
        process: TriggerProcess,
        source_event: WorldEvent,
        outcome: str,
        logical_time: datetime,
        candidate_seconds: tuple[int, ...],
    ):
        """Record one replay-stable model-consideration opportunity time.

        The draw changes only when another model consideration may happen.  It
        never chooses a life action or turns a model ``no_op`` into a scripted
        behavior.
        """

        assert process.claim_lease is not None
        candidate_refs = tuple(
            f"life-ecology-cadence-seconds:{seconds}"
            for seconds in candidate_seconds
        )
        kwargs = {
            "attempt_id": "attempt:life-ecology:cadence:"
            + _digest(
                [
                    process.trigger_id,
                    process.claim_lease.attempt_id,
                    outcome,
                ]
            ),
            "candidate_refs": candidate_refs,
            "catalog_version": "life-ecology-ambient-cadence.1",
            "logical_time": logical_time,
            "seed_instant": source_event.logical_time,
            "actor": self._owner_id,
            "trace_id": source_event.trace_id,
            "correlation_id": source_event.correlation_id,
        }
        authority = RandomAuthority(
            ledger=self._ledger,
            source="world-v2:life-ecology-cadence",
        )
        if getattr(self._ledger, "blocks_event_loop", False):
            return await asyncio.to_thread(authority.draw, **kwargs)
        return authority.draw(**kwargs)

    async def _verified_wake(self, *, key: LifeEcologyRunKey, projection) -> WorldEvent:
        source_event = await self._source_event_ref(key.wake_event_ref)
        if source_event.event_type not in LIFE_ECOLOGY_WAKE_EVENT_TYPES:
            raise ValueError("life ecology trigger source is not a durable wake")
        committed = next(
            (
                item
                for item in projection.committed_world_event_refs
                if item.event_id == key.wake_event_ref
            ),
            None,
        )
        if (
            committed is None
            or committed.event_type != source_event.event_type
            or committed.payload_hash != source_event.payload_hash
            or committed.logical_time != source_event.logical_time
        ):
            raise ValueError("life ecology trigger source is not exactly committed")
        return source_event

    async def _source_event(self, process: TriggerProcess) -> WorldEvent:
        if process.source_evidence_ref is None:
            raise ValueError("life ecology trigger has no source evidence")
        return await self._source_event_ref(process.source_evidence_ref)

    async def _source_event_ref(self, event_id: str) -> WorldEvent:
        located = await self._lookup(event_id)
        if located is None or located[0].world_id != self._ledger.world_id:
            raise ValueError("life ecology trigger source is unavailable")
        return located[0]

    async def _completed_outcome_ref(
        self, *, trigger_id: str, projection
    ) -> str | None:
        del projection
        if self._ledger.blocks_event_loop:
            event = await asyncio.to_thread(
                self._ledger.find_trigger_completion,
                trigger_id,
            )
        else:
            event = self._ledger.find_trigger_completion(trigger_id)
        if event is None:
            return None
        outcome_ref = event.payload().get("runtime_outcome_ref")
        if not isinstance(outcome_ref, str) or not outcome_ref:
            raise ValueError("life ecology completion outcome audit is invalid")
        return outcome_ref

    def _opened_event(
        self,
        *,
        process: TriggerProcess,
        source_event: WorldEvent,
        logical_time: datetime,
        trace_id: str,
        correlation_id: str,
    ) -> WorldEvent:
        payload = {"process": process.model_dump(mode="json")}
        identity = domain_idempotency_key(
            event_type="TriggerProcessOpened", world_id=self._ledger.world_id, payload=payload
        )
        if identity is None:
            raise ValueError("life ecology opened trigger has no domain identity")
        return WorldEvent.from_payload(
            schema_version="world-v2.1",
            event_id="event:life-ecology:opened:" + _digest(process.trigger_id),
            world_id=self._ledger.world_id,
            event_type="TriggerProcessOpened",
            logical_time=logical_time,
            created_at=source_event.created_at,
            actor=self._owner_id,
            source=self._source,
            trace_id=trace_id,
            causation_id=source_event.event_id,
            correlation_id=correlation_id,
            idempotency_key=identity,
            payload=payload,
        )

    def _claim_event(
        self,
        *,
        process: TriggerProcess,
        event_type: str,
        source_event: WorldEvent,
        logical_time: datetime,
        trace_id: str,
        correlation_id: str,
    ) -> WorldEvent:
        payload = {"process": process.model_dump(mode="json")}
        identity = domain_idempotency_key(
            event_type=event_type, world_id=self._ledger.world_id, payload=payload
        )
        if identity is None:
            raise ValueError("life ecology claimed trigger has no domain identity")
        attempt_id = process.claim_lease.attempt_id if process.claim_lease is not None else "missing"
        return WorldEvent.from_payload(
            schema_version="world-v2.1",
            event_id="event:life-ecology:"
            + event_type.removeprefix("TriggerProcess").lower()
            + ":"
            + _digest([process.trigger_id, attempt_id]),
            world_id=self._ledger.world_id,
            event_type=event_type,
            logical_time=logical_time,
            created_at=source_event.created_at,
            actor=self._owner_id,
            source=self._source,
            trace_id=trace_id,
            causation_id=source_event.event_id,
            correlation_id=correlation_id,
            idempotency_key=identity,
            payload=payload,
        )

    async def _try_commit(self, events: tuple[WorldEvent, ...], *, projection) -> bool:
        try:
            await self._commit(
                events,
                world_revision=projection.world_revision,
                deliberation_revision=projection.deliberation_revision,
            )
        except (ConcurrencyConflict, IdempotencyConflict):
            return False
        return True

    async def _project(self):
        if self._ledger.blocks_event_loop:
            return await asyncio.to_thread(self._ledger.project)
        return self._ledger.project()

    async def _lookup(self, event_id: str):
        if self._ledger.blocks_event_loop:
            return await asyncio.to_thread(self._ledger.lookup_event_commit, event_id)
        return self._ledger.lookup_event_commit(event_id)

    async def _commit(self, events, *, world_revision: int, deliberation_revision: int):
        if self._ledger.blocks_event_loop:
            return await asyncio.to_thread(
                self._ledger.commit,
                events,
                expected_world_revision=world_revision,
                expected_deliberation_revision=deliberation_revision,
            )
        return self._ledger.commit(
            events,
            expected_world_revision=world_revision,
            expected_deliberation_revision=deliberation_revision,
        )


__all__ = [
    "LIFE_ECOLOGY_PROCESS_KIND",
    "LIFE_ECOLOGY_WAKE_EVENT_TYPES",
    "LedgerLifeEcologyTriggerStore",
    "life_ecology_trigger_id",
    "life_ecology_trigger_ref",
    "parse_life_ecology_trigger_ref",
    "validate_life_ecology_run_key",
]
