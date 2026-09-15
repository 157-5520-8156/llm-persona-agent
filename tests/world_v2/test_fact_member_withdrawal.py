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
        self.message_batches = []

    async def complete(self, messages, **_kwargs):
        self.message_batches.append(messages)
        self.requests.append(json.loads(messages[1]["content"]))
        return json.dumps(self.results.pop(0), ensure_ascii=False)


def _retain(value):
    return {
        "retain": True,
        "predicate_code": "schedule.commitment",
        "value": value,
        "privacy_class": "personal",
        "confidence": 9000,
        "rationale": "Explicit commitment.",
    }


def _record(ledger, index, text, *, actor=None, logical_time=None):
    original, _ = _observation()
    observation = original.model_copy(
        update={
            "observation_id": f"observation:member:{index}",
            "source_event_id": f"source:member:{index}",
            "payload_ref": f"payload:member:{index}",
            "text": text,
            "payload_hash": hashlib.sha256(text.encode()).hexdigest(),
            "actor": actor or original.actor,
            "logical_time": logical_time if logical_time is not None else original.logical_time,
        }
    )
    payload = observation.model_dump(mode="json")
    event = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id=f"event:observation:member:{index}",
        world_id=WORLD_ID,
        event_type="ObservationRecorded",
        logical_time=observation.logical_time,
        created_at=observation.created_at,
        actor=observation.actor,
        source=observation.source,
        trace_id=observation.trace_id,
        causation_id=observation.causation_id,
        correlation_id=observation.correlation_id,
        idempotency_key=domain_idempotency_key(
            event_type="ObservationRecorded", world_id=WORLD_ID, payload=payload
        ),
        payload=payload,
    )
    before = ledger.project()
    ledger.commit(
        (event, interaction_fact_trigger_event(observation=observation, observation_event=event)),
        expected_world_revision=before.world_revision,
        expected_deliberation_revision=before.deliberation_revision,
    )
    return observation


def _runtime(ledger, issuer, model):
    return InteractionFactTriggerRuntime(
        ledger=ledger,
        acceptance=FactV2AcceptanceRuntime.compose(ledger=ledger, batch_issuer=issuer),
        adapter=FactObservationProposalAdapter(model=model),
        character_interior=_MemoryInterior(_RetainingMemoryChat()),
        memory_actor_ref="character:zhizhi",
        memory_lifecycle=FactMemoryCandidateLifecycle(
            ledger=ledger, actor="worker:memory", source="test:memory"
        ),
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
    withdrawal = {
        "decision": "withdraw",
        "predicate_code": "schedule.commitment",
        "target_fact_ref": target.fact_id,
        "confidence": 9500,
        "rationale": "The user explicitly retracts this exact commitment.",
    }
    model.results.extend((withdrawal, withdrawal))  # The old contract rejects both attempts.
    observation = _record(ledger, 3, "周四下午三点的项目分享会取消了，周五的读书会不变。")

    assert (await runtime.drain_one()).work_status == "accepted"

    final = ledger.project()
    assert len(model.requests) == 3
    assert {row["fact_id"] for row in model.requests[-1]["current_set_facts"]} == {
        target.fact_id,
        sibling.fact_id,
    }
    withdrawn = next(fact for fact in final.facts if fact.fact_id == target.fact_id)
    assert withdrawn.values.status == "withdrawn"
    assert withdrawn.entity_revision == target.entity_revision + 1
    assert withdrawn.values.withdrawal_evidence_ref == observation.observation_id
    assert next(fact for fact in final.facts if fact.fact_id == sibling.fact_id) == sibling
    assert (
        final.memory_candidates == candidates_before
    )  # Withdrawal is not a character forgetting decision.
    current = fact_recall_items(
        ledger=ledger,
        projection=final,
        facts=tuple(fact for fact in final.facts if fact.values.status == "active"),
    )
    assert [item.fact_id for item in current] == [sibling.fact_id]
    historical = historical_fact_recall_items(
        ledger=ledger, projection=final, subject_refs=frozenset({observation.actor})
    )
    old_source = next(item for item in historical if item.fact_id == target.fact_id)
    assert old_source.accepted_fact_event_ref == target.origin.accepted_event_ref
    assert old_source.valid_from == target.updated_at
    assert old_source.valid_to == withdrawn.updated_at
    retrieval = MemoryRetrievalCompiler(ledger=ledger).compile(
        cursor=ProjectionCursor(
            world_revision=final.world_revision,
            deliberation_revision=final.deliberation_revision,
            ledger_sequence=final.ledger_sequence,
        ),
        candidates=final.memory_candidates,
        viewer_privacy_ceiling="private",
        projection=final,
    )
    assert all(
        excerpt.source_id != target.fact_id
        for item in retrieval.items
        for excerpt in item.source_excerpts
    )
    assert (await runtime.drain_one()).status == "idle"
    ledger.close()
    reopened = SQLiteWorldLedger(path=path, world_id=WORLD_ID, accepted_batch_issuer=issuer)
    assert reopened.project() == final
    assert (await _runtime(reopened, issuer, model).drain_one()).status == "idle"
    assert len(model.requests) == 3
    reopened.close()


def _withdraw(target):
    return {
        "decision": "withdraw",
        "predicate_code": target.values.predicate_code,
        "target_fact_ref": target.fact_id,
        "confidence": 9500,
        "rationale": "This exact assertion is retracted by its source.",
    }


async def _two_members(tmp_path):
    issuer = AcceptedLedgerBatchIssuer()
    ledger = SQLiteWorldLedger(
        path=tmp_path / "members.sqlite", world_id=WORLD_ID, accepted_batch_issuer=issuer
    )
    model = _FactModel()
    runtime = _runtime(ledger, issuer, model)
    for index in (1, 2):
        text = f"Independent commitment {index}."
        model.results.append(_retain(text))
        _record(ledger, index, text)
        assert (await runtime.drain_one()).work_status == "accepted"
    return ledger, issuer, model, runtime


def _with_payload(event, payload):
    return WorldEvent.from_payload(
        **{
            key: getattr(event, key)
            for key in (
                "schema_version",
                "event_id",
                "world_id",
                "event_type",
                "logical_time",
                "created_at",
                "actor",
                "source",
                "trace_id",
                "causation_id",
                "correlation_id",
            )
        },
        idempotency_key=domain_idempotency_key(
            event_type=event.event_type, world_id=event.world_id, payload=payload
        ),
        payload=payload,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid", ["unknown", "wrong_predicate", "missing_target", "model_supplied_binding"]
)
async def test_invalid_member_selection_uses_one_correction_and_does_not_clear_set(
    tmp_path, invalid
):
    ledger, _, model, runtime = await _two_members(tmp_path)
    before = ledger.project().facts
    draft = _withdraw(before[0])
    if invalid == "unknown":
        draft["target_fact_ref"] = "fact:unexposed"
    elif invalid == "wrong_predicate":
        draft["predicate_code"] = "preference.likes"
    elif invalid == "missing_target":
        del draft["target_fact_ref"]
    else:
        draft["target_binding"] = {"entity_revision": 1}
    model.results.extend((draft, {"retain": False}))
    _record(ledger, 3, "Source update requiring a decision.")
    assert (await runtime.drain_one()).work_status == "no_change"
    assert len(model.requests) == 4
    assert ledger.project().facts == before
    assert ledger.project().trigger_processes[-1].state == "terminal"
    ledger.close()


@pytest.mark.asyncio
async def test_other_users_member_is_not_in_the_model_source_set(tmp_path):
    ledger, _, model, runtime = await _two_members(tmp_path)
    model.results.append(_retain("Another user's commitment."))
    _record(ledger, 3, "Another user's commitment.", actor="user:other")
    assert (await runtime.drain_one()).work_status == "accepted"
    before = ledger.project().facts
    foreign = before[-1]
    model.results.extend((_withdraw(foreign), {"retain": False}))
    _record(ledger, 4, "A new assertion by the original user.")
    assert (await runtime.drain_one()).work_status == "no_change"
    assert foreign.fact_id not in {
        row["fact_id"] for row in model.requests[-2]["current_set_facts"]
    }
    assert ledger.project().facts == before
    ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    ["revision", "authority_hash", "value_hash", "sibling_ref", "source_event", "legacy_version"],
)
async def test_recorded_member_decision_rejects_changed_head_or_observation_binding(
    tmp_path, monkeypatch, invalid
):
    ledger, _, model, runtime = await _two_members(tmp_path)
    before = ledger.project().facts
    model.results.append(_withdraw(before[0]))
    _record(ledger, 3, "Source retraction.")
    original = ledger.commit_at_cursor

    def corrupt(events, **kwargs):
        changed = []
        for event in events:
            if event.event_type == "InteractionFactDecisionRecorded":
                payload = event.payload()
                decision = json.loads(payload["decision_json"])
                if invalid == "revision":
                    decision["target_binding"]["entity_revision"] += 1
                elif invalid == "authority_hash":
                    decision["target_binding"]["authority_payload_hash"] = "0" * 64
                elif invalid == "value_hash":
                    decision["target_binding"]["value_hash"] = "0" * 64
                elif invalid == "sibling_ref":
                    decision["target_fact_ref"] = before[1].fact_id
                elif invalid == "source_event":
                    payload["source_event_ref"] = "event:observation:member:1"
                else:
                    payload["adapter_version"] = "fact-observation-draft.3"
                payload["decision_json"] = json.dumps(
                    decision, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                payload["decision_hash"] = hashlib.sha256(
                    payload["decision_json"].encode()
                ).hexdigest()
                event = _with_payload(event, payload)
            changed.append(event)
        return original(tuple(changed), **kwargs)

    monkeypatch.setattr(ledger, "commit_at_cursor", corrupt)
    with pytest.raises(ValueError, match="withdrawal"):
        await runtime.drain_one()
    assert ledger.project().facts == before
    ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["old_policy_without_binding", "wrong_decision_hash"])
async def test_fresh_withdrawal_cannot_bypass_the_durable_model_decision(
    tmp_path, monkeypatch, invalid
):
    from companion_daemon.world_v2.fact_events import fact_mutation_hash

    ledger, _, model, runtime = await _two_members(tmp_path)
    before = ledger.project().facts
    model.results.append(_withdraw(before[0]))
    _record(ledger, 3, "Source retraction.")
    original = ledger.commit

    def corrupt(events, **kwargs):
        changed = []
        for event in events:
            payload = event.payload()
            if (
                event.event_type == "ProposalRecorded"
                and payload.get("transition_kind") == "withdraw"
            ):
                mutation = json.loads(payload["proposed_mutation"]["payload_json"])
                if invalid == "old_policy_without_binding":
                    mutation.pop("member_withdrawal")
                    mutation["policy_refs"] = ["policy:fact-commit.2"]
                    mutation["fact_after"]["origin"]["policy_refs"] = ["policy:fact-commit.2"]
                    mutation["fact_after"]["semantic_fingerprint"] = mutation["fact_before"][
                        "semantic_fingerprint"
                    ]
                    payload["policy_refs"] = ["policy:fact-commit.2"]
                else:
                    mutation["member_withdrawal"]["decision_hash"] = "0" * 64
                mutation["accepted_change_hash"] = fact_mutation_hash(mutation)
                payload["proposed_change_hash"] = mutation["accepted_change_hash"]
                payload["proposed_mutation"]["payload_json"] = json.dumps(
                    mutation, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                event = _with_payload(event, payload)
            changed.append(event)
        return original(tuple(changed), **kwargs)

    monkeypatch.setattr(ledger, "commit", corrupt)
    with pytest.raises(ValueError, match="withdrawal"):
        await runtime.drain_one()
    assert ledger.project().facts == before
    ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("crash_after_effect", [False, True])
async def test_member_withdrawal_cold_rejoins_the_same_decision_and_effect(
    tmp_path, monkeypatch, crash_after_effect
):
    from companion_daemon.world_v2.fact_correction_lifecycle import FactCorrectionLifecycle

    ledger, issuer, model, runtime = await _two_members(tmp_path)
    target, sibling = ledger.project().facts
    model.results.append(_withdraw(target))
    _record(ledger, 3, "Source retraction.")
    with monkeypatch.context() as patch:
        if crash_after_effect:

            async def stop(**_kwargs):
                raise RuntimeError("crash after effect")

            patch.setattr(runtime, "_complete", stop)
        else:

            def stop(*_args, **_kwargs):
                raise RuntimeError("crash after decision")

            patch.setattr(FactCorrectionLifecycle, "withdraw", stop)
        with pytest.raises(RuntimeError, match="crash after"):
            await runtime.drain_one()
    durable = ledger.project().interaction_fact_decisions[-1]
    ledger.close()
    reopened = SQLiteWorldLedger(
        path=tmp_path / "members.sqlite", world_id=WORLD_ID, accepted_batch_issuer=issuer
    )
    result = await _runtime(reopened, issuer, model).drain_one()
    assert result.work_status == ("no_change" if crash_after_effect else "accepted")
    final = reopened.project()
    assert len(model.requests) == 3
    from companion_daemon.world_v2.fact_trigger import interaction_fact_decision_event_id
    from companion_daemon.world_v2.interaction_fact_decision import (
        InteractionFactDecisionRecordedPayload,
    )

    recorded = reopened.lookup_event_commit(
        interaction_fact_decision_event_id(
            trigger_id=durable.trigger_id,
            fact_context_hash=durable.fact_context_hash,
        )
    )
    assert recorded is not None
    assert (
        InteractionFactDecisionRecordedPayload.model_validate_json(recorded[0].payload_json)
        == durable
    )
    assert final.facts[0].values.status == "withdrawn"
    assert final.facts[0].entity_revision == target.entity_revision + 1
    assert final.facts[1] == sibling
    assert (
        len([ref for ref in final.committed_world_event_refs if ref.event_type == "FactWithdrawn"])
        == 1
    )
    reopened.close()


@pytest.mark.asyncio
async def test_each_batch_decision_keeps_its_exact_member_after_prior_settlement(tmp_path):
    ledger, _, model, runtime = await _two_members(tmp_path)
    first, second = ledger.project().facts
    source1 = _record(ledger, 3, "First source retraction.")
    source2 = _record(ledger, 4, "Second independent source retraction.")
    model.results.append(
        {
            "decisions": [
                {"observation_id": source1.observation_id, "result": _withdraw(first)},
                {"observation_id": source2.observation_id, "result": _withdraw(second)},
            ]
        }
    )
    assert (await runtime.drain_one()).work_status == "accepted"
    assert ledger.project().facts[1] == second
    assert (await runtime.drain_one()).work_status == "accepted"
    assert len(model.requests) == 3  # Second source rejoins the same paid batch result.
    assert all(fact.values.status == "withdrawn" for fact in ledger.project().facts)
    assert (await runtime.drain_one()).status == "idle"
    ledger.close()


@pytest.mark.asyncio
async def test_member_outside_supplied_cap_cannot_be_selected(tmp_path):
    ledger, issuer, model, _ = await _two_members(tmp_path)
    runtime = InteractionFactTriggerRuntime(
        ledger=ledger,
        acceptance=FactV2AcceptanceRuntime.compose(ledger=ledger, batch_issuer=issuer),
        adapter=FactObservationProposalAdapter(model=model),
        owner_id="worker:interaction-fact",
    )
    for index in range(3, 18):
        model.results.append(_retain(f"Independent commitment {index}."))
        _record(ledger, index, f"Independent commitment {index}.")
        assert (await runtime.drain_one()).work_status == "accepted"
    before = ledger.project().facts
    model.results.extend((_withdraw(before[-1]), {"retain": False}))
    _record(ledger, 18, "A source update.")
    assert (await runtime.drain_one()).work_status == "no_change"
    supplied = model.requests[-2]["current_set_facts"]
    assert len(supplied) == 16
    assert before[-1].fact_id not in {row["fact_id"] for row in supplied}
    assert ledger.project().facts == before
    ledger.close()
