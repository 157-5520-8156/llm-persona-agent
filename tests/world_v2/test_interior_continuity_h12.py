from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import hashlib
import json

from companion_daemon.world_v2.character_interior.inbound_wire import (
    _typed_recent_dialogue_proof,
)
from companion_daemon.world_v2.character_interior.snapshot_compiler import (
    compile_inner_life_snapshot,
)
from companion_daemon.world_v2.context_capsule import (
    ContextCapsuleBudgetPolicy,
    InnerAdvisoryCandidate,
    InnerAdvisoryProjection,
    RANK_DOMAIN_IMPORTANCE_BP,
    resolved_result_set_hash,
)
from companion_daemon.world_v2.deliberation import ModelInput, ModelRoute, TriggerMessage
from companion_daemon.world_v2.epoch_continuity import compile_continuity_snapshot
from companion_daemon.world_v2.recent_dialogue import RecentDialogueCompiler
from companion_daemon.world_v2.schemas import (
    Action,
    CommittedWorldEventRef,
    ExecutionReceipt,
    ExpressionPlanManifestBeatRef,
    ExpressionPlanManifestRef,
    ExpressionPlanProjection,
    LedgerProjection,
    StoredMessagePayloadProjection,
    WorldEvent,
)
from test_context_capsule import (
    _bound,
    _experience,
    _experience_bound,
    _large_memory_retrievals,
    _request as capsule_request,
    compile_context_capsule,
)
from test_epoch_continuity_h10 import _fact
from test_experience_authority import experience as make_experience
from test_memory_candidate_authority import binding, candidate, fact_authority
from test_recent_dialogue_continuity_watermark import _EventLookup, _event_ref, _world_event


NOW = datetime(2026, 8, 13, 15, 0, tzinfo=UTC)
WORLD = "world:h12"


def _empty_projection(**updates: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "world_id": WORLD,
        "world_revision": 1,
        "semantic_hash": "b" * 64,
        "logical_time": NOW,
        "facts": (),
        "memory_candidates": (),
        "relationship_states": (),
        "affect_episodes": (),
        "appraisals": (),
        "threads": (),
        "commitments": (),
        "experiences": (),
        "life_arcs": (),
        "npcs": (),
        "private_impressions": (),
        "character_core": None,
    }
    values.update(updates)
    return SimpleNamespace(**values)


def test_continuity_keeps_experiences_inside_the_thirty_day_window() -> None:
    inside = make_experience(
        experience_id="experience:recent-walk",
        transition_id="transition:experience:recent-walk",
        accepted_event_ref="event:experience:recent-walk",
        occurred_from=NOW - timedelta(days=4, hours=2),
        occurred_to=NOW - timedelta(days=4),
    )
    snapshot = compile_continuity_snapshot(
        _empty_projection(experiences=(inside,)),
        epoch_id="epoch:h12",
        logical_time=NOW,
    )
    assert snapshot.experiences
    assert snapshot.experiences[0]["experience_id"] == "experience:recent-walk"


def test_continuity_drops_experiences_outside_the_thirty_day_window() -> None:
    stale = make_experience(
        experience_id="experience:old-term",
        transition_id="transition:experience:old-term",
        accepted_event_ref="event:experience:old-term",
        occurred_from=NOW - timedelta(days=41),
        occurred_to=NOW - timedelta(days=40),
    )
    snapshot = compile_continuity_snapshot(
        _empty_projection(experiences=(stale,)),
        epoch_id="epoch:h12",
        logical_time=NOW,
    )
    assert snapshot.experiences == ()


def test_continuity_fact_and_memory_order_still_follows_updated_at() -> None:
    older_fact = _fact().model_copy(
        update={
            "fact_id": "fact:older",
            "committed_at": NOW - timedelta(days=8),
            "updated_at": NOW - timedelta(days=8),
        }
    )
    newer_fact = _fact().model_copy(
        update={
            "fact_id": "fact:newer",
            "committed_at": NOW - timedelta(days=1),
            "updated_at": NOW - timedelta(days=1),
        }
    )
    fact, transition, committed = fact_authority()
    source = binding(fact, transition, committed)
    older_memory = candidate(
        source,
        candidate_id="memory:older",
        status="active",
        reviewed_at=NOW - timedelta(days=9),
        opened_at=NOW - timedelta(days=10),
        updated_at=NOW - timedelta(days=9),
        accepted_event_ref="event:memory:older",
    )
    newer_memory = candidate(
        source,
        candidate_id="memory:newer",
        status="active",
        reviewed_at=NOW - timedelta(days=1),
        opened_at=NOW - timedelta(days=2),
        updated_at=NOW - timedelta(days=1),
        accepted_event_ref="event:memory:newer",
    )
    snapshot = compile_continuity_snapshot(
        _empty_projection(
            facts=(older_fact, newer_fact),
            memory_candidates=(older_memory, newer_memory),
        ),
        epoch_id="epoch:h12",
        logical_time=NOW,
    )
    assert [item["fact_id"] for item in snapshot.facts] == ["fact:newer", "fact:older"]
    assert [item["candidate_id"] for item in snapshot.memory_candidates] == [
        "memory:newer",
        "memory:older",
    ]


def _rerank(bound, *, slice_name: str, rank: int):
    metadata = tuple(
        item.model_copy(update={"rank_score_bp": rank}) for item in bound.item_metadata
    )
    proof = bound.resolver_proof.model_copy(
        update={"result_set_hash": resolved_result_set_hash(slice_name, metadata)}
    )
    return bound.model_copy(update={"item_metadata": metadata, "resolver_proof": proof})


def test_response_expectation_advisory_survives_global_character_eviction() -> None:
    trigger_ref = "event:observation:1"
    expectation = InnerAdvisoryProjection(
        advisory_id="advisory:zz-response-expectation",
        kind="response_expectation",
        source_refs=(trigger_ref,),
        candidate_refs=("response-expectation:pending",),
        candidates=(
            InnerAdvisoryCandidate(
                candidate_ref="response-expectation:pending",
                value="When she last spoke she hoped for: a reply about the internship.",
                weight_bp=10_000,
                confidence_bp=10_000,
            ),
        ),
        confidence_bp=10_000,
        expiry=NOW + timedelta(hours=6),
        producer_version="response-expectation-advisory.1",
    )
    optional = InnerAdvisoryProjection(
        advisory_id="advisory:aa-optional",
        kind="appraisal_candidate",
        source_refs=(trigger_ref,),
        candidate_refs=("candidate:optional",),
        confidence_bp=10_000,
        expiry=NOW + timedelta(minutes=1),
        producer_version="test-optional-matrix.1",
    )
    bound = _bound((optional, expectation), source_ref=trigger_ref, ranks=(100, 100))
    generous = compile_context_capsule(capsule_request(advisories=bound))
    assert len(generous.advisories.items) == 2
    capsule = compile_context_capsule(
        capsule_request(advisories=bound),
        policy=ContextCapsuleBudgetPolicy(
            hard_max_characters=generous.budget.used_characters - 400
        ),
    )
    assert [item.item_ref for item in capsule.advisories.items] == [expectation.advisory_id]


def test_recent_experiences_outrank_memory_candidates_under_global_eviction() -> None:
    assert (
        RANK_DOMAIN_IMPORTANCE_BP["recent_experiences"]
        > RANK_DOMAIN_IMPORTANCE_BP["active_memory_candidates"]
    )
    assert (
        RANK_DOMAIN_IMPORTANCE_BP["recent_experiences"] < RANK_DOMAIN_IMPORTANCE_BP["open_threads"]
    )
    experiences = tuple(_experience(index) for index in range(3))
    memories = _rerank(
        _large_memory_retrievals(count=3, text_characters=800),
        slice_name="active_memory_candidates",
        rank=RANK_DOMAIN_IMPORTANCE_BP["active_memory_candidates"],
    )
    experience_bound = _experience_bound(
        experiences,
        ranks=tuple(RANK_DOMAIN_IMPORTANCE_BP["recent_experiences"] for _ in experiences),
    )
    generous = compile_context_capsule(
        capsule_request(
            recent_experiences=experience_bound,
            active_memory_candidates=memories,
        )
    )
    capsule = compile_context_capsule(
        capsule_request(
            recent_experiences=experience_bound,
            active_memory_candidates=memories,
        ),
        policy=ContextCapsuleBudgetPolicy(
            hard_max_characters=generous.budget.used_characters - 1_200
        ),
    )
    assert len(capsule.recent_experiences.items) >= len(capsule.active_memory_candidates.items)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_unknown_companion_bubble_stays_in_dialogue_but_is_not_delivered_proof() -> None:
    at = NOW
    events: list[WorldEvent] = []
    refs: list[CommittedWorldEventRef] = []
    revision = 0

    def append_event(event: WorldEvent) -> CommittedWorldEventRef:
        nonlocal revision
        revision += 1
        events.append(event)
        ref = _event_ref(event, world_revision=revision)
        refs.append(ref)
        return ref

    acceptance_event = _world_event(
        event_id="event:acceptance:unknown",
        event_type="ExpressionPlanAccepted",
        logical_time=at,
        payload={"plan_id": "plan:unknown"},
    )
    append_event(acceptance_event)
    payload_event = _world_event(
        event_id="event:payload:unknown",
        event_type="ExpressionPayloadStored",
        logical_time=at,
        payload={"payload_ref": "payload:reply:unknown"},
    )
    append_event(payload_event)
    receipt = ExecutionReceipt(
        receipt_id="receipt:unknown",
        result_id="result:unknown",
        action_id="action:unknown",
        provider="test",
        provider_ref="provider-message:unknown",
        source_event_id="provider-event:unknown",
        receipt_kind="terminal",
        observed_state="unknown",
        is_terminal=True,
        cost_actual=0,
        received_at=at + timedelta(seconds=1),
        raw_payload_hash=_digest("receipt:unknown"),
    )
    receipt_event = _world_event(
        event_id="event:receipt:unknown",
        event_type="ExecutionReceiptRecorded",
        logical_time=at,
        payload={"receipt": receipt.model_dump(mode="json")},
    )
    append_event(receipt_event)
    action = Action.model_construct(
        action_id="action:unknown",
        expression_plan_id="plan:unknown",
        expression_beat_id="beat:unknown",
        state="unknown",
    )
    payload_hash = "sha256:" + _digest("我刚才那句发出去了吗")
    beat = ExpressionPlanManifestBeatRef.model_construct(
        beat_id="beat:unknown",
        payload_ref="payload:reply:unknown",
        payload_hash=payload_hash,
        text="我刚才那句发出去了吗",
        action=action,
    )
    manifest = ExpressionPlanManifestRef.model_construct(
        acceptance_id="acceptance:unknown",
        proposal_id="proposal:unknown",
        plan_id="plan:unknown",
        acceptance_event_ref=acceptance_event.event_id,
        beats=(beat,),
    )
    projection = LedgerProjection.model_construct(
        committed_world_event_refs=tuple(refs),
        message_observations=(),
        expression_plan_manifests=(manifest,),
        minimal_reply_manifests=(),
        proposal_audits=(),
        expression_plans=(
            ExpressionPlanProjection.model_construct(
                plan_id="plan:unknown",
                state="authorized",
                history=(),
            ),
        ),
        actions=(action,),
        execution_receipts=(receipt,),
        stored_message_payloads=(
            StoredMessagePayloadProjection(
                acceptance_id="acceptance:unknown",
                proposal_id="proposal:unknown",
                payload_ref="payload:reply:unknown",
                payload_hash=payload_hash,
                text="我刚才那句发出去了吗",
                content_type="text/plain",
                event_ref=payload_event.event_id,
                event_payload_hash=payload_event.payload_hash,
            ),
        ),
        expression_payload_descriptors=(),
    )
    compiled = RecentDialogueCompiler(
        ledger=_EventLookup(tuple(events)),  # type: ignore[arg-type]
        max_companion_items=4,
    ).compile_with_acknowledgements(
        projection=projection,
        actor_ref="agent:companion",
        subject_refs=frozenset({"user:primary"}),
    )
    bubble = next(
        item
        for item in compiled.dialogue
        if item.dialogue_id == "dialogue:expression:plan:unknown:beat:unknown"
    )
    assert bubble.delivery_state == "unknown"
    assert bubble.text == "我刚才那句发出去了吗"

    request = ModelInput(
        call_id="call:h12-unknown",
        attempt_id="attempt:h12-unknown",
        route=ModelRoute(tier="flash", reason_code="test", router_version="test.1"),
        capsule_id="a" * 64,
        trigger_ref="trigger:h12-unknown",
        evaluated_world_revision=3,
        model_content_json="{}",
        trigger_message=TriggerMessage(
            event_ref="event:observation:current",
            event_payload_hash="sha256:" + "b" * 64,
            observation_ref="observation:current",
            source_world_revision=3,
            actor="user:primary",
            channel="qq",
            reply_target="conversation:qq:c2c:owner",
            platform_message_id="qq-message-current",
            text="在吗",
        ),
    )
    visible = json.dumps(
        {
            "actor_ref": "agent:companion",
            "slices": {
                "recent_dialogue": {
                    "availability": "available",
                    "items": [
                        {
                            "source_ref": bubble.dialogue_id,
                            "value": json.loads(bubble.model_dump_json()),
                        }
                    ],
                }
            },
        },
        ensure_ascii=False,
    )
    proofs = _typed_recent_dialogue_proof(request=request, visible_context_json=visible)
    assert proofs == ()


def test_snapshot_compiler_does_not_read_recalled_emotional_associations() -> None:
    snapshot = compile_inner_life_snapshot(
        {
            "world_id": WORLD,
            "actor_ref": "actor:companion",
            "slices": {
                "recalled_emotional_associations": {
                    "availability": "available",
                    "items": [
                        {
                            "source_ref": "recall:emotion:1",
                            "value": {
                                "memory_kind": "reflective",
                                "actor_ref": "actor:companion",
                                "text": "那件事还堵在心里。",
                            },
                        }
                    ],
                }
            },
        }
    ).model_view()
    assert "recalled_emotional_associations" not in snapshot["materials"]
