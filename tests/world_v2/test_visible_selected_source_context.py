"""Offline selected Fact/Dialogue preparation; no semantic review or release."""

from contextlib import asynccontextmanager
from datetime import timedelta
import hashlib
import json
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.accepted_ledger_batch import AcceptedLedgerBatchIssuer
from companion_daemon.world_v2.character_interior.inbound_wire import _source_closure_evidence
from companion_daemon.world_v2.context_capsule import ContextCapsuleBudgetPolicy, SliceBudget
from companion_daemon.world_v2.context_resolver import query_from_projection
from companion_daemon.world_v2.deliberation import ModelInput, ModelRoute, TriggerMessage
from companion_daemon.world_v2.expression_draft import ExpressionDraft
from companion_daemon.world_v2.event_identity import domain_idempotency_key
from companion_daemon.world_v2.ledger_context_resolver import (
    ContextRelevanceScope,
    context_capsule_compiler_from_ledger,
)
from companion_daemon.world_v2.life_context import compile_life_review_context
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.schemas import WorldEvent
from companion_daemon.world_v2.visible_source_closure_protocol import (
    compact_source_reference_table,
    visible_source_closure_messages,
)
from companion_daemon.world_v2.visible_review_context import compile_visible_selected_source_context
from test_fact_member_withdrawal import _FactModel, _record, _retain, _runtime
from test_interaction_fact_trigger_runtime import WORLD_ID
from test_life_development_runtime import NOW, OWNER, _seed_clock


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _draft():
    return ExpressionDraft(
        timing_choice="now",
        beats=({"modality": "text", "text": "你取消了周五的报告，周四的约定还在。"},),
        stance="answer_from_world",
        brief_rationale="Offline candidate with no declared claims.",
        world_claims=(),
    )


@asynccontextmanager
async def _sources(tmp_path, *, extra=False, policy=None, privacy="personal"):
    issuer = AcceptedLedgerBatchIssuer()
    ledger = SQLiteWorldLedger(
        path=tmp_path / "selected-visible.sqlite",
        world_id=WORLD_ID,
        accepted_batch_issuer=issuer,
    )
    try:
        model = _FactModel()
        runtime = _runtime(ledger, issuer, model)
        observation = _record(ledger, 1, "用户决定取消周五的报告，仍保留周四的约定。")
        model.results.append({**_retain(observation.text), "privacy_class": privacy})
        assert (await runtime.drain_one()).work_status == "accepted"
        if extra:
            for index, actor in ((2, observation.actor), (3, "user:unrelated")):
                item = _record(ledger, index, f"额外事实 {index}", actor=actor)
                model.results.append(_retain(item.text))
                assert (await runtime.drain_one()).work_status == "accepted"
        wake = _seed_clock(
            ledger,
            logical_time=NOW + timedelta(minutes=1),
            logical_time_from=ledger.project().logical_time,
        )
        event, commit = ledger.lookup_event_commit("event:observation:member:1")
        compiler = context_capsule_compiler_from_ledger(
            ledger=ledger,
            relevance_scope=ContextRelevanceScope(
                actor_ref=OWNER, related_subject_refs=(observation.actor,),
            ),
            policy=policy,
        )
        capsule = compiler.compile_for_deliberation(
            query_from_projection(
                ledger.project(), actor_ref=OWNER, trigger_ref=event.event_id,
            ),
        ).capsule
        request = ModelInput(
            call_id="model-call:visible-selected-fixture",
            attempt_id="attempt:visible-selected-fixture",
            route=ModelRoute(tier="flash", reason_code="test", router_version="test.1"),
            capsule_id=capsule.capsule_id,
            trigger_ref=capsule.trigger_ref,
            evaluated_world_revision=capsule.world_revision,
            evaluated_deliberation_revision=capsule.deliberation_revision,
            evaluated_ledger_sequence=capsule.ledger_sequence,
            model_content_json=capsule.model_content_json,
            trigger_message=TriggerMessage(
                event_ref=event.event_id,
                event_payload_hash="sha256:" + event.payload_hash,
                source_world_revision=commit.world_revision,
                observation_ref=observation.observation_id,
                actor=observation.actor,
                channel=observation.channel,
                reply_target="fixture:local",
                text=observation.text,
            ),
        )
        yield SimpleNamespace(
            ledger=ledger, capsule=capsule, request=request, observation=observation, wake=wake,
        )
    finally:
        ledger.close()


def _selected_evidence(case):
    return compile_visible_selected_source_context(
        request=case.request, capsule=case.capsule,
    )


@pytest.mark.asyncio
async def test_empty_claims_prepare_original_selected_fact_and_dialogue_bodies(tmp_path):
    async with _sources(tmp_path) as case:
        chat_before = case.capsule.model_content_json
        life_before = _json(compile_life_review_context(case.capsule))
        evidence = _selected_evidence(case)
        binding = evidence["visible_review_projection"]
        assert binding["contract"] == "visible-review-selected-source-proof.1"
        assert binding["selected_lanes"] == ["relevant_facts", "recent_dialogue"]
        assert binding["model_content_hash"] == hashlib.sha256(chat_before.encode()).hexdigest()
        assert binding["model_input_hash"] == hashlib.sha256(
            _json(case.request.model_dump(mode="json")).encode(),
        ).hexdigest()
        for field in (
            "capsule_id", "compiler_result_hash", "world_id", "snapshot_id", "snapshot_hash",
            "actor_ref", "trigger_ref", "world_revision", "deliberation_revision", "ledger_sequence",
        ):
            assert binding[field] == getattr(case.capsule, field)
        assert _draft().world_claims == ()
        for lane in ("relevant_facts", "recent_dialogue"):
            original = getattr(case.capsule, lane).items
            assert original
            entries = [entry for entry in evidence["entries"] if entry.get("lane") == lane]
            assert [entry["item"]["item_ref"] for entry in entries] == [
                item.item_ref for item in original
            ]
            for entry, item in zip(entries, original, strict=True):
                assert entry["item"]["value"] == json.loads(item.payload_json)
                assert entry["item"]["value_hash"] == item.value_hash
                assert entry["item"]["source_hash"] == item.source_hash
                assert entry["item"]["source_bindings"] == [
                    binding.model_dump(mode="json") for binding in item.source_bindings
                ]
                assert entry["privacy_class"] == item.privacy_class
                assert entry["actor_ref"] == case.observation.actor
                assert entry["authority"] == (
                    "accepted_fact_with_observation_source" if lane == "relevant_facts"
                    else "counterpart_report_only"
                )
        rows = compact_source_reference_table(evidence)
        packet = json.loads(visible_source_closure_messages(
            visible_beats=tuple(beat.text for beat in _draft().beats),
            world_claims=(), source_references=rows,
        )[1]["content"])
        assert {item["lane"] for item in packet["source_materials"]} == {
            "relevant_facts", "recent_dialogue",
        }
        assert case.capsule.model_content_json == chat_before
        assert _json(compile_life_review_context(case.capsule)) == life_before


@pytest.mark.asyncio
async def test_selected_preparation_does_not_change_original_claim_only_or_life_wire(tmp_path):
    async with _sources(tmp_path) as case:
        def original_claim_only():
            return _source_closure_evidence(
                request=case.request, draft=_draft(),
                visible_context_json=case.request.model_content_json,
                identity_frame=None,
            )

        before = _json(original_claim_only())
        original_life = _json(compile_life_review_context(case.capsule))
        assert all(entry.get("lane") not in {"relevant_facts", "recent_dialogue"}
                   for entry in original_claim_only()["entries"])
        _selected_evidence(case)
        assert _json(original_claim_only()) == before
        assert _json(compile_life_review_context(case.capsule)) == original_life
        assert "visible_review_projection" not in original_life
        assert "life-review-selected-source-proof.1" in original_life


@pytest.mark.asyncio
@pytest.mark.parametrize("field", [
    "capsule_id", "trigger_ref", "evaluated_world_revision", "evaluated_deliberation_revision",
    "evaluated_ledger_sequence", "model_content_json",
])
async def test_wrong_original_input_pin_is_rejected(tmp_path, field):
    async with _sources(tmp_path) as case:
        old = getattr(case.request, field)
        changed = old + 1 if isinstance(old, int) else (
            "0" * 64 if field == "capsule_id" else old + " "
        )
        request = case.request.model_copy(update={field: changed})
        with pytest.raises(ValueError, match="original Capsule"):
            compile_visible_selected_source_context(request=request, capsule=case.capsule)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [
    "capsule_id", "snapshot_hash", "compiler_result_tag", "value_hash", "source_hash",
    "source_binding", "payload_json",
])
async def test_corrupt_typed_capsule_cannot_supply_selected_proof(tmp_path, fault):
    async with _sources(tmp_path) as case:
        capsule = case.capsule
        if fault in {"capsule_id", "snapshot_hash", "compiler_result_tag"}:
            capsule = capsule.model_copy(update={fault: "0" * 64})
        else:
            lane = capsule.relevant_facts
            item = lane.items[0]
            if fault == "source_binding":
                corrupt = item.source_bindings[0].model_copy(update={"immutable_hash": "0" * 64})
                item = item.model_copy(update={"source_bindings": (corrupt, *item.source_bindings[1:])})
            elif fault == "payload_json":
                value = json.loads(item.payload_json)
                value["source_excerpt"] = "替换过的来源正文。"
                item = item.model_copy(update={fault: _json(value)})
            else:
                item = item.model_copy(update={fault: "0" * 64})
            capsule = capsule.model_copy(update={
                "relevant_facts": lane.model_copy(update={"items": (item,)}),
            })
        with pytest.raises(ValueError):
            compile_visible_selected_source_context(request=case.request, capsule=capsule)


@pytest.mark.asyncio
async def test_protocol_shaped_object_cannot_claim_a_trusted_capsule(tmp_path):
    async with _sources(tmp_path) as case:
        impostor = SimpleNamespace(**case.capsule.model_dump())
        with pytest.raises(ValueError, match="trusted typed Context Capsule"):
            compile_visible_selected_source_context(request=case.request, capsule=impostor)


@pytest.mark.asyncio
async def test_only_original_selected_members_survive_later_world_changes(tmp_path):
    policy = ContextCapsuleBudgetPolicy(
        relevant_facts=SliceBudget(max_items=1, max_fields=128, max_characters=8000),
        recent_dialogue=SliceBudget(max_items=1, max_fields=128, max_characters=8000),
    )
    async with _sources(tmp_path, extra=True, policy=policy) as case:
        selected = {
            item.item_ref for lane in (case.capsule.relevant_facts, case.capsule.recent_dialogue)
            for item in lane.items
        }
        before = _selected_evidence(case)
        assert {entry["item"]["item_ref"] for entry in before["entries"]} == selected
        assert len(before["entries"]) == 2
        assert len(case.ledger.project().facts) == 3
        assert "user:unrelated" not in _json(before)
        observation = case.observation.model_copy(update={
            "observation_id": "observation:after-visible-pin",
            "source_event_id": "source:after-visible-pin",
            "payload_ref": "payload:after-visible-pin",
            "text": "新观察不得补进原 pin。",
            "payload_hash": hashlib.sha256("新观察不得补进原 pin。".encode()).hexdigest(),
            "logical_time": case.ledger.project().logical_time,
        })
        payload = observation.model_dump(mode="json")
        event = WorldEvent.from_payload(
            schema_version="world-v2.1", event_id="event:observation:after-visible-pin",
            world_id=WORLD_ID, event_type="ObservationRecorded",
            logical_time=observation.logical_time, created_at=observation.created_at,
            actor=observation.actor, source=observation.source, trace_id=observation.trace_id,
            causation_id=observation.causation_id, correlation_id=observation.correlation_id,
            idempotency_key=domain_idempotency_key(
                event_type="ObservationRecorded", world_id=WORLD_ID, payload=payload,
            ),
            payload=payload,
        )
        before_append = case.ledger.project()
        case.ledger.commit(
            (event,), expected_world_revision=before_append.world_revision,
            expected_deliberation_revision=before_append.deliberation_revision,
        )
        _seed_clock(
            case.ledger, event_id="event:clock:after-visible-pin",
            logical_time=case.wake.logical_time + timedelta(minutes=1),
            logical_time_from=case.ledger.project().logical_time,
        )
        assert _selected_evidence(case) == before
        assert "新观察不得补进原 pin" not in _json(before)


@pytest.mark.asyncio
async def test_unselected_fact_has_no_entry_or_support_alias(tmp_path):
    policy = ContextCapsuleBudgetPolicy(
        relevant_facts=SliceBudget(max_items=0, max_fields=128, max_characters=8000),
    )
    async with _sources(tmp_path, policy=policy) as case:
        evidence = _selected_evidence(case)
        assert case.ledger.project().facts
        assert case.capsule.relevant_facts.items == ()
        assert not any(entry["lane"] == "relevant_facts" for entry in evidence["entries"])
        rows = compact_source_reference_table(evidence)
        fact_ids = {fact.fact_id for fact in case.ledger.project().facts}
        assert fact_ids.isdisjoint(row["source_ref"] for row in rows)


@pytest.mark.asyncio
async def test_original_selected_withhold_fails_without_exposing_a_body(tmp_path):
    async with _sources(tmp_path, privacy="withhold") as case:
        assert any(item.privacy_class == "withhold" for item in case.capsule.relevant_facts.items)
        with pytest.raises(ValueError, match="withheld member"):
            _selected_evidence(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["actor", "text", "event_payload_hash", "source_world_revision"])
async def test_counterpart_mapping_must_match_the_selected_observation(tmp_path, field):
    async with _sources(tmp_path) as case:
        trigger = case.request.trigger_message
        old = getattr(trigger, field)
        changed = old + 1 if isinstance(old, int) else (
            "sha256:" + "0" * 64 if field == "event_payload_hash" else old + ":other"
        )
        request = case.request.model_copy(update={
            "trigger_message": trigger.model_copy(update={field: changed}),
        })
        with pytest.raises(ValueError, match="differs from its selected source"):
            compile_visible_selected_source_context(request=request, capsule=case.capsule)


@pytest.mark.asyncio
async def test_absent_counterpart_mapping_never_guesses_from_actor_prefix(tmp_path):
    async with _sources(tmp_path) as case:
        request = case.request.model_copy(update={"trigger_message": None})
        evidence = compile_visible_selected_source_context(request=request, capsule=case.capsule)
        assert evidence["subjects"] == {"companion_actor_ref": case.capsule.actor_ref}
        assert all(entry["actor_ref"] == case.observation.actor for entry in evidence["entries"])
        rows = compact_source_reference_table(evidence)
        assert all(row["support_subject_role"] is None for row in rows)
        assert all(row["support_eligibility"] == "baseline_only" for row in rows)
