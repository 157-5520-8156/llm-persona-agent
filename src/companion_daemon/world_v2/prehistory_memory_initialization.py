"""Source-backed initial memory installation, without a fictional character choice.

Every seed has the same declared, unassessed retrieval defaults. Source validity
and later forgetting still apply; initialization is not a salience judgement.
"""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Literal
from pydantic import Field

from .schema_core import FrozenModel
from .schemas import (
    MemorySourceBinding,
    MemorySalienceVector,
    MEMORY_SALIENCE_MATRIX_DIGEST,
    WorldEvent,
)
from .character_prehistory import digest
from .prehistory_memory_source import prehistory_memory_binding, resolve_prehistory_memory_source

EVENT_TYPE = "PrehistoryMemoryInitializationRecorded"
ACTOR = "system:prehistory-memory-initialization"
SOURCE = "world-v2:prehistory-memory-initialization-direct"
POLICY = "policy:prehistory-initial-memory.1"
ASSESSMENT = "initialization_defaults_not_character_judgment"


def initial_retrieval_defaults():
    from .fact_memory_draft import FactMemoryRetentionDraft

    return FactMemoryRetentionDraft(
        cue_kind="world_continuity",
        retention_rationales=("world_continuity",),
        salience=MemorySalienceVector(
            autobiographical_relevance_bp=5000,
            relationship_relevance_bp=5000,
            emotional_residue_bp=5000,
            unfinished_business_bp=5000,
            recurrence_bp=5000,
            novelty_bp=5000,
            future_utility_bp=5000,
            world_continuity_bp=5000,
            matrix_digest=MEMORY_SALIENCE_MATRIX_DIGEST,
        ),
    )


def initialization_event_id(world_id, source):
    return "event:prehistory-memory:initialization:" + digest(
        [world_id, source.model_dump(mode="json")]
    )


def candidate_event_id(source, operation):
    return f"event:memory:prehistory:{digest(source)}:initialization:{operation}"


class PrehistoryMemoryInitializationRecorded(FrozenModel):
    contract: Literal["prehistory-memory-initialization.1"] = "prehistory-memory-initialization.1"
    policy_ref: Literal["policy:prehistory-initial-memory.1"] = POLICY
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    assessment: Literal["initialization_defaults_not_character_judgment"] = ASSESSMENT
    actor_ref: str
    source_binding: MemorySourceBinding
    recorded_at: datetime


def reduce_initialization(state, event):
    payload = PrehistoryMemoryInitializationRecorded.model_validate_json(event.payload_json)
    row, _ = resolve_prehistory_memory_source(
        payload.source_binding,
        records=state.prehistory_records,
        archives=state.prehistory_archives,
        committed_events=state.committed_world_event_refs,
    )
    if (
        row.actor_ref != payload.actor_ref
        or row.record.privacy_class == "withhold"
        or event.actor != ACTOR
        or event.source != SOURCE
        or event.logical_time != state.logical_time
        or payload.recorded_at != state.logical_time
        or event.created_at < payload.recorded_at
        or payload.recorded_at < row.accepted_at
        or event.causation_id != row.accepted_event_ref
        or event.event_id != initialization_event_id(event.world_id, payload.source_binding)
        or payload.policy_hash != digest(initial_retrieval_defaults())
    ):
        raise ValueError(
            "initial memory grant differs from its actor, source or initialization policy"
        )
    return state


def is_unassessed_initial_memory(candidate, *, ledger):
    """Do not attribute unchanged initialization salience to a character choice.

    Reinforcement and decay alone do not become a subjective assessment.
    The exact source grant must exist; a suggestive event name is insufficient.
    """
    bindings = candidate.values.source_bindings
    if (
        len(bindings) != 1
        or bindings[0].source_kind != "prehistory"
        or candidate.candidate_id != "memory:prehistory:" + digest(bindings[0])
        or candidate.values.salience != initial_retrieval_defaults().salience
    ):
        return False
    grant = ledger.lookup_event_commit(initialization_event_id(ledger.world_id, bindings[0]))
    if grant is None:
        return False
    event, _ = grant
    if event.event_type != EVENT_TYPE or event.actor != ACTOR or event.source != SOURCE:
        return False
    payload = PrehistoryMemoryInitializationRecorded.model_validate_json(event.payload_json)
    return payload.source_binding == bindings[0] and payload.policy_hash == digest(
        initial_retrieval_defaults()
    )


class PrehistoryMemoryInitializer:
    def __init__(self, *, ledger, owner_actor_ref):
        from .fact_memory_candidate_lifecycle import FactMemoryCandidateLifecycle

        self.ledger, self.actor_ref = ledger, owner_actor_ref
        self.lifecycle = FactMemoryCandidateLifecycle(ledger=ledger, actor=ACTOR, source=SOURCE)

    def advance_once(self, *, limit=64, record_refs=None):
        if type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError("initial memory limit must be between 1 and 256")
        initialized, existing, inactive, pending, withheld = [], 0, 0, 0, 0
        with self.ledger.serialized_commit_sequence():
            from .character_interior.turn_store import read_completed_considerations
            from .prehistory_memory_decision import (
                decision_event_id,
                PrehistoryMemoryDecisionRecordedPayload,
            )

            completed = read_completed_considerations(
                path=self.ledger.database_path,
                world_id=self.ledger.world_id,
                actor_ref=self.actor_ref,
                purposes=("fact_memory_retention", "prehistory_memory_batch"),
            )
            state = self.ledger.project()
            rows = tuple(r for r in state.prehistory_records if r.actor_ref == self.actor_ref)
            requested = set(record_refs) if record_refs is not None else None
            if requested is not None and requested - {r.record.record_id for r in rows}:
                raise ValueError("initial memory scope contains unavailable history")
            for row in rows:
                if requested is not None and row.record.record_id not in requested:
                    continue
                if row.record.privacy_class == "withhold":
                    withheld += 1
                    continue
                source = prehistory_memory_binding(row)
                state = self.ledger.project()
                resolve_prehistory_memory_source(
                    source,
                    records=state.prehistory_records,
                    archives=state.prehistory_archives,
                    committed_events=state.committed_world_event_refs,
                )
                before = next(
                    (c for c in state.memory_candidates if source in c.values.source_bindings), None
                )
                if before is not None and before.values.status != "pending":
                    if before.values.status == "active":
                        existing += 1
                    else:
                        inactive += 1
                    continue
                recorded = self.ledger.lookup_event_commit(
                    decision_event_id(self.ledger.world_id, source)
                )
                legacy_pending = False
                if recorded is not None:
                    decision_event, _ = recorded
                    decision = PrehistoryMemoryDecisionRecordedPayload.model_validate_json(
                        decision_event.payload_json
                    )
                    if (
                        decision_event.event_type != "PrehistoryMemoryDecisionRecorded"
                        or decision.source_binding != source
                    ):
                        raise ValueError("legacy initialization source identity mismatch")
                    legacy_pending = decision.status == "retain"
                else:
                    for result in completed:
                        decision = result.decision
                        if not isinstance(
                            decision, dict
                        ) or source.authority_event_ref not in decision.get("source_refs", ()):
                            continue
                        if decision.get("purpose") == "fact_memory_retention":
                            legacy_pending = True
                        elif decision.get("purpose") == "prehistory_memory_batch":
                            legacy_pending |= any(
                                item.get("source_id") == source.source_id
                                for item in decision.get("payload", {}).get("choices", ())
                                if isinstance(item, dict)
                            )
                if legacy_pending:
                    pending += 1
                    continue
                grant_id = initialization_event_id(self.ledger.world_id, source)
                grant = self.ledger.lookup_event_commit(grant_id)
                if before is not None and (
                    grant is None
                    or before.origin.accepted_event_ref != candidate_event_id(source, "open")
                ):
                    pending += (
                        1  # A legacy unfinished choice needs its own recovery, not replacement.
                    )
                    continue
                if len(initialized) >= limit:
                    continue
                if grant is None:
                    payload = PrehistoryMemoryInitializationRecorded(
                        actor_ref=self.actor_ref,
                        source_binding=source,
                        recorded_at=state.logical_time,
                        policy_hash=digest(initial_retrieval_defaults()),
                    )
                    event = WorldEvent.from_payload(
                        schema_version="world-v2.1",
                        event_id=grant_id,
                        world_id=self.ledger.world_id,
                        event_type=EVENT_TYPE,
                        logical_time=state.logical_time,
                        created_at=max(datetime.now(UTC), state.logical_time),
                        actor=ACTOR,
                        source=SOURCE,
                        trace_id="trace:" + grant_id,
                        causation_id=row.accepted_event_ref,
                        correlation_id=source.source_id,
                        idempotency_key=grant_id,
                        payload=payload.model_dump(mode="json"),
                    )
                    self.ledger.commit(
                        (event,),
                        expected_world_revision=state.world_revision,
                        expected_deliberation_revision=state.deliberation_revision,
                    )
                else:
                    event, _ = grant
                    payload = PrehistoryMemoryInitializationRecorded.model_validate_json(
                        event.payload_json
                    )
                    if (
                        payload.actor_ref != self.actor_ref
                        or payload.source_binding != source
                        or payload.policy_hash != digest(initial_retrieval_defaults())
                    ):
                        raise ValueError("initial memory recovery lost its exact grant")
                now = self.ledger.project().logical_time
                for revision, status, operation in (
                    (1, "pending", "open"),
                    (2, "active", "accept"),
                ):
                    if revision == 1 and before is not None:
                        continue
                    after = self.lifecycle._candidate(
                        candidate_id="memory:prehistory:" + digest(source),
                        source=source,
                        draft=initial_retrieval_defaults(),
                        privacy_ceiling=row.record.privacy_class,
                        entity_revision=revision,
                        status=status,
                        opened_at=before.opened_at if before else now,
                        updated_at=now,
                        reviewed_at=now if status == "active" else None,
                        accepted_event_ref=candidate_event_id(source, operation),
                    )
                    self.lifecycle._record_and_accept(
                        after=after,
                        before=before,
                        operation=operation,
                        logical_time=now,
                        created_at=max(datetime.now(UTC), now),
                        trace_id="trace:" + grant_id,
                        correlation_id=source.source_id,
                    )
                    before = after
                initialized.append(source.source_id)
        return {
            "status": "initialized" if initialized else "pending_existing" if pending else "idle",
            "initialized": len(initialized),
            "source_ids": initialized,
            "existing_active": existing,
            "existing_inactive": inactive,
            "existing_pending": pending,
            "withheld": withheld,
            "model_calls": 0,
            "mode": "direct",
        }
