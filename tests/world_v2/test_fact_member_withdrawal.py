"""Explicit member withdrawal through the existing Fact model/acceptance seam."""
from __future__ import annotations

import hashlib
import json

import pytest

from companion_daemon.world_v2.accepted_ledger_batch import AcceptedLedgerBatchIssuer
from companion_daemon.world_v2.event_identity import domain_idempotency_key
from companion_daemon.world_v2.fact_draft_adapter import FactObservationProposalAdapter
from companion_daemon.world_v2.fact_memory_candidate_lifecycle import FactMemoryCandidateLifecycle
from companion_daemon.world_v2.fact_trigger import interaction_fact_trigger_event
from companion_daemon.world_v2.fact_v2_acceptance_runtime import FactV2AcceptanceRuntime
from companion_daemon.world_v2.interaction_fact_trigger_runtime import InteractionFactTriggerRuntime
from companion_daemon.world_v2.ledger_context_resolver import (
    fact_recall_items,
    historical_fact_recall_items,
)
from companion_daemon.world_v2.memory_retrieval import MemoryRetrievalCompiler
from companion_daemon.world_v2.schemas import ProjectionCursor, WorldEvent
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_interaction_fact_trigger_runtime import (
    WORLD_ID,
    _MemoryInterior,
    _RetainingMemoryChat,
    _observation,
)


class _FactModel:
    model = "offline-member-withdrawal"

    def __init__(self):
        self.results = []
        self.requests = []

    async def complete(self, messages, **_kwargs):
        self.requests.append(json.loads(messages[1]["content"]))
        return json.dumps(self.results.pop(0), ensure_ascii=False)


def _retain(value):
    return {"retain": True, "predicate_code": "schedule.commitment", "value": value,
            "privacy_class": "personal", "confidence": 9000, "rationale": "Explicit commitment."}


def _record(ledger, index, text, *, actor=None):
    original, _ = _observation()
    observation = original.model_copy(update={
        "observation_id": f"observation:member:{index}", "source_event_id": f"source:member:{index}",
        "payload_ref": f"payload:member:{index}", "text": text,
        "payload_hash": hashlib.sha256(text.encode()).hexdigest(),
        "actor": actor or original.actor,
    })
    payload = observation.model_dump(mode="json")
    event = WorldEvent.from_payload(
        schema_version="world-v2.1", event_id=f"event:observation:member:{index}", world_id=WORLD_ID,
        event_type="ObservationRecorded", logical_time=observation.logical_time,
        created_at=observation.created_at, actor=observation.actor, source=observation.source,
        trace_id=observation.trace_id, causation_id=observation.causation_id,
        correlation_id=observation.correlation_id,
        idempotency_key=domain_idempotency_key(event_type="ObservationRecorded", world_id=WORLD_ID, payload=payload),
        payload=payload,
    )
    before = ledger.project()
    ledger.commit((event, interaction_fact_trigger_event(observation=observation, observation_event=event)),
                  expected_world_revision=before.world_revision,
                  expected_deliberation_revision=before.deliberation_revision)
    return observation


def _runtime(ledger, issuer, model):
    return InteractionFactTriggerRuntime(
        ledger=ledger, acceptance=FactV2AcceptanceRuntime.compose(ledger=ledger, batch_issuer=issuer),
        adapter=FactObservationProposalAdapter(model=model),
        character_interior=_MemoryInterior(_RetainingMemoryChat()), memory_actor_ref="character:zhizhi",
        memory_lifecycle=FactMemoryCandidateLifecycle(ledger=ledger, actor="worker:memory", source="test:memory"),
        owner_id="worker:interaction-fact",
    )


@pytest.mark.asyncio
async def test_explicit_set_member_withdrawal_preserves_sibling_history_and_replay(tmp_path):
    issuer = AcceptedLedgerBatchIssuer()
    path = tmp_path / "member.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID, accepted_batch_issuer=issuer)
    model = _FactModel()
    runtime = _runtime(ledger, issuer, model)
    for index, text in enumerate(("周四下午三点有项目分享会", "周五上午十点有读书会"), 1):
        model.results.append(_retain(text))
        _record(ledger, index, text)
        assert (await runtime.drain_one()).work_status == "accepted"
    before = ledger.project()
    target, sibling = before.facts
    candidates_before = before.memory_candidates
    withdrawal = {"decision": "withdraw", "predicate_code": "schedule.commitment",
                  "target_fact_ref": target.fact_id, "confidence": 9500,
                  "rationale": "The user explicitly retracts this exact commitment."}
    model.results.extend((withdrawal, withdrawal))  # The old contract rejects both attempts.
    observation = _record(ledger, 3, "周四下午三点的项目分享会取消了，周五的读书会不变。")

    assert (await runtime.drain_one()).work_status == "accepted"

    final = ledger.project()
    assert len(model.requests) == 3
    assert {row["fact_id"] for row in model.requests[-1]["current_set_facts"]} == {target.fact_id, sibling.fact_id}
    withdrawn = next(fact for fact in final.facts if fact.fact_id == target.fact_id)
    assert withdrawn.values.status == "withdrawn"
    assert withdrawn.entity_revision == target.entity_revision + 1
    assert withdrawn.values.withdrawal_evidence_ref == observation.observation_id
    assert next(fact for fact in final.facts if fact.fact_id == sibling.fact_id) == sibling
    assert final.memory_candidates == candidates_before  # Withdrawal is not a character forgetting decision.
    current = fact_recall_items(ledger=ledger, projection=final,
                               facts=tuple(fact for fact in final.facts if fact.values.status == "active"))
    assert [item.fact_id for item in current] == [sibling.fact_id]
    historical = historical_fact_recall_items(ledger=ledger, projection=final,
                                              subject_refs=frozenset({observation.actor}))
    assert any(item.fact_id == target.fact_id for item in historical)
    retrieval = MemoryRetrievalCompiler(ledger=ledger).compile(
        cursor=ProjectionCursor(world_revision=final.world_revision,
                                deliberation_revision=final.deliberation_revision,
                                ledger_sequence=final.ledger_sequence),
        candidates=final.memory_candidates, viewer_privacy_ceiling="private", projection=final,
    )
    assert all(excerpt.source_id != target.fact_id for item in retrieval.items for excerpt in item.source_excerpts)
    assert (await runtime.drain_one()).status == "idle"
    ledger.close()
    reopened = SQLiteWorldLedger(path=path, world_id=WORLD_ID, accepted_batch_issuer=issuer)
    assert reopened.project() == final
    assert (await _runtime(reopened, issuer, model).drain_one()).status == "idle"
    assert len(model.requests) == 3
    reopened.close()
