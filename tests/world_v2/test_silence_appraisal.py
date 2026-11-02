"""Silence appraisal: one durable inner-feeling opportunity per unanswered reply."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import hashlib
import json

import pytest

from companion_daemon.world_v2.accepted_ledger_batch import AcceptedLedgerBatchIssuer
from companion_daemon.world_v2.batch_invariants import silence_appraisal_trigger_identity
from companion_daemon.world_v2.deliberation import (
    Deliberation,
    ModelInput,
    ModelOutput,
    ModelRoute,
    RouteRequest,
)
from companion_daemon.world_v2.event_identity import domain_idempotency_key
from companion_daemon.world_v2.ledger import WorldLedger
from companion_daemon.world_v2.ledger_context_resolver import context_capsule_compiler_from_ledger
from companion_daemon.world_v2.ledger_payload_reader import LedgerAuthorizedPayloadReader
from companion_daemon.world_v2.minimal_reply_acceptance import ReplyBudgetPolicy
from companion_daemon.world_v2.minimal_reply_atomic_recorder import MinimalReplyAtomicRecorder
from companion_daemon.world_v2.pinned_turn import PinnedTurnCompiler
from companion_daemon.world_v2.platform_action_executor import (
    PlatformActionExecutor,
    PlatformDispatchReceipt,
    PlatformDispatchRequest,
)
from companion_daemon.world_v2.proposal_envelope import (
    CanonicalTypedPayload,
    MinimalProposal,
    ProposalActionIntent,
    TypedChange,
)
from companion_daemon.world_v2.runtime import WorldRuntime
from companion_daemon.world_v2.schemas import (
    BudgetAccount,
    ClockObservation,
    TriggerProcess,
    WorldEvent,
)
from companion_daemon.world_v2.silence_appraisal_trigger import (
    SilenceAppraisalTriggerOpener,
    silence_appraisal_opportunity,
)
from companion_daemon.world_v2.world_turn_runtime import InboundTurn, WorldTurnRuntime


WORLD_ID = "world:silence-appraisal"
NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
IDLE_THRESHOLD = 3_600


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class _Identities:
    def resolve(self, *, platform: str, platform_user_id: str) -> tuple[str, str]:
        return f"user:{platform_user_id}", f"user:{platform_user_id}"


class _Router:
    async def route(self, _request: RouteRequest) -> ModelRoute:
        return ModelRoute(tier="flash", reason_code="test", router_version="test.1")


class _ReplyModel:
    """Deterministic minimal reply whose identities follow the trigger."""

    async def propose(self, request: ModelInput) -> ModelOutput:
        suffix = _digest(request.trigger_ref)[:12]
        text = "我在想你说的这件事，先抱一下。"
        payload_hash = "sha256:" + sha256(text.encode("utf-8")).hexdigest()
        proposal = MinimalProposal(
            proposal_id=f"proposal:silence-fixture:reply:{suffix}",
            trigger_ref=request.trigger_ref,
            evaluated_world_revision=request.evaluated_world_revision,
            evidence_refs=(),
            proposed_changes=(
                TypedChange(
                    change_id=f"change:silence-fixture:expression:{suffix}",
                    kind="expression_plan_transition",
                    target_id=f"plan:silence-fixture:reply:{suffix}",
                    transition="accept",
                    payload=CanonicalTypedPayload.from_value(
                        payload_schema="expression_plan_transition.v1",
                        value={
                            "plan_id": f"plan:silence-fixture:reply:{suffix}",
                            "overall_intent": "reply",
                            "ordering_policy": "dependencies",
                            "terminal_policy": "settle",
                            "beat_drafts": [
                                {
                                    "beat_id": f"beat:silence-fixture:reply:{suffix}",
                                    "inline_text": text,
                                    "materialized_payload_ref": f"payload:silence-fixture:{suffix}",
                                    "payload_hash": payload_hash,
                                    "content_type": "text/plain",
                                    "dependency_beat_ids": [],
                                    "delay_window": None,
                                    "cancel_policy": "cancel-before-dispatch",
                                    "reconsider_policy": "reconsider-on-new-observation",
                                    "merge_policy": "never",
                                }
                            ],
                        },
                    ),
                ),
            ),
            action_intents=(
                ProposalActionIntent(
                    intent_id=f"intent:silence-fixture:reply:{suffix}",
                    kind="reply",
                    layer="external_action",
                    target="user:user.1",
                    payload_ref=f"payload:silence-fixture:{suffix}",
                    payload_hash=payload_hash,
                    causal_change_id=f"change:silence-fixture:expression:{suffix}",
                    beat_ref=f"beat:silence-fixture:reply:{suffix}",
                ),
            ),
            confidence=7_000,
            brief_rationale="Reply warmly without making world claims.",
            source_model_result="model-result:silence-fixture",
            response_text=text,
            stance="acknowledge_briefly",
        )
        return ModelOutput(
            model_id="test-reply",
            model_version="test.1",
            raw_proposal=proposal.model_dump(mode="json"),
            input_tokens=1,
            output_tokens=1,
        )

    async def recover(self, request: ModelInput, _failure: str) -> ModelOutput:
        return await self.propose(request)


class _Transport:
    provider = "platform:test"

    def __init__(self) -> None:
        self.sent: list[PlatformDispatchRequest] = []

    async def send(self, request: PlatformDispatchRequest) -> PlatformDispatchReceipt:
        self.sent.append(request)
        return PlatformDispatchReceipt(
            provider_receipt_id=f"provider-receipt:{len(self.sent)}",
            provider_ref=f"provider-message:{len(self.sent)}",
            status="delivered",
            received_at=NOW,
            raw_payload_hash="sha256:" + "a" * 64,
            idempotency_key=request.idempotency_key,
            request_fingerprint=request.fingerprint,
        )

    async def lookup(self, **_kwargs):  # type: ignore[no-untyped-def]
        return None


def _seed_event(event_type: str, payload: dict[str, object], suffix: str) -> WorldEvent:
    return WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id=f"event:silence-fixture:{suffix}",
        world_id=WORLD_ID,
        event_type=event_type,
        logical_time=NOW,
        created_at=NOW,
        actor="system:test",
        source="test",
        trace_id="trace:silence-fixture",
        causation_id="test",
        correlation_id="test",
        idempotency_key=f"silence-fixture:{suffix}",
        payload=payload,
    )


def _delivered_reply_world(
):
    """One in-memory world where the companion's reply is already delivered."""

    issuer = AcceptedLedgerBatchIssuer()
    ledger = WorldLedger.in_memory(world_id=WORLD_ID, accepted_batch_issuer=issuer)
    account = BudgetAccount(
        account_id="account:silence:chat", category="chat", window_id="test", limit=100
    )
    ledger.commit(
        (
            _seed_event("WorldStarted", {}, "started"),
            _seed_event(
                "BudgetAccountConfigured", {"account": account.model_dump(mode="json")}, "budget"
            ),
        ),
        expected_world_revision=0,
        expected_deliberation_revision=0,
    )
    capsules = context_capsule_compiler_from_ledger(ledger=ledger)
    reply_model = _ReplyModel()
    transport = _Transport()
    runtime = WorldRuntime(
        world_id=WORLD_ID,
        ledger=ledger,
        pinned_turn=PinnedTurnCompiler(
            ledger=ledger,
            capsule_compiler=capsules,
            deliberation=Deliberation(
                router=_Router(), main_model=reply_model, quick_recovery=reply_model
            ),
            companion_actor_ref="agent:companion",
        ),
        # The .52 cutover folds every expression episode without a
        # CharacterInterior inbound attempt.  Claiming the unified inbound
        # state trigger keeps this fixture's reply on the production path.
        inbound_state_owner="worker:silence:inbound-state",
        reply_policy=ReplyBudgetPolicy(
            account_id=account.account_id,
            amount_limit=10,
            actor="agent:companion",
            target="user:user.1",
            recovery_policy="effect_once",
        ),
        reply_recorder=MinimalReplyAtomicRecorder(batch_issuer=issuer),
        action_executor=PlatformActionExecutor(
            payloads=LedgerAuthorizedPayloadReader(ledger=ledger), transport=transport
        ),
        action_pump_owner="pump:silence",
    )
    return runtime, ledger, None, None


async def _deliver_reply(runtime: WorldRuntime, *, message_id: str = "message:1") -> None:
    turn = WorldTurnRuntime(runtime=runtime, identities=_Identities())
    outcome = await turn.respond(
        InboundTurn(
            platform="test",
            platform_user_id="user.1",
            platform_message_id=message_id,
            text="今天有点低落。",
            observed_at=NOW,
            trace_id=f"trace:{message_id}",
        )
    )
    assert outcome.status == "action_authorized"
    delivery = await turn.drain_actions_once()
    assert delivery is not None and delivery.status == "settled"


def _receipt_event_id(ledger) -> str:
    refs = [
        item
        for item in ledger.project().committed_world_event_refs
        if item.event_type == "ExecutionReceiptRecorded"
    ]
    assert refs, "fixture must have a committed delivery receipt"
    return refs[-1].event_id


async def _advance(runtime: WorldRuntime, ledger, *, seconds: int, tick_id: str) -> None:
    current = ledger.project().logical_time
    await runtime.advance(
        ClockObservation(
            schema_version="world-v2.1",
            tick_id=tick_id,
            world_id=WORLD_ID,
            logical_time=current + timedelta(seconds=seconds),
            created_at=current + timedelta(seconds=seconds),
            trace_id=f"trace:{tick_id}",
            causation_id=f"scheduler:{tick_id}",
            correlation_id=f"scheduler:{tick_id}",
            logical_time_from=current,
            logical_time_to=current + timedelta(seconds=seconds),
            reason="scheduled_tick",
        )
    )


# --- projection layer -------------------------------------------------------


@pytest.mark.asyncio
async def test_opportunity_opens_only_after_idle_threshold_with_her_last_word() -> None:
    runtime, ledger, _worker, _turn = _delivered_reply_world()
    await _deliver_reply(runtime)

    assert (
        silence_appraisal_opportunity(
            ledger.project(), idle_seconds_threshold=IDLE_THRESHOLD
        )
        is None
    )

    await _advance(runtime, ledger, seconds=IDLE_THRESHOLD * 2, tick_id="tick-idle")
    opportunity = silence_appraisal_opportunity(
        ledger.project(), idle_seconds_threshold=IDLE_THRESHOLD
    )

    assert opportunity is not None
    assert opportunity.source_evidence_ref == _receipt_event_id(ledger)
    assert opportunity.action_kind == "reply"
    assert opportunity.idle_seconds == IDLE_THRESHOLD * 2
    assert opportunity.trigger_id == silence_appraisal_trigger_identity(
        WORLD_ID, opportunity.source_evidence_ref
    )
    # Pure derivation: repeated evaluation of the same projection is identical.
    assert opportunity == silence_appraisal_opportunity(
        ledger.project(), idle_seconds_threshold=IDLE_THRESHOLD
    )


@pytest.mark.asyncio
async def test_user_reply_after_anchor_closes_the_silence() -> None:
    runtime, ledger, _worker, _turn = _delivered_reply_world()
    await _deliver_reply(runtime)
    await _advance(runtime, ledger, seconds=IDLE_THRESHOLD * 2, tick_id="tick-idle")
    turn = WorldTurnRuntime(runtime=runtime, identities=_Identities())
    await turn.respond(
        InboundTurn(
            platform="test",
            platform_user_id="user.1",
            platform_message_id="message:answer",
            text="抱歉刚刚在开会。",
            observed_at=NOW + timedelta(seconds=IDLE_THRESHOLD * 2),
            trace_id="trace:answer",
        )
    )

    assert (
        silence_appraisal_opportunity(
            ledger.project(), idle_seconds_threshold=IDLE_THRESHOLD
        )
        is None
    )


@pytest.mark.asyncio
async def test_disabled_threshold_never_produces_an_opportunity() -> None:
    runtime, ledger, _worker, _turn = _delivered_reply_world()
    await _deliver_reply(runtime)
    await _advance(runtime, ledger, seconds=IDLE_THRESHOLD * 2, tick_id="tick-idle")

    assert silence_appraisal_opportunity(ledger.project(), idle_seconds_threshold=0) is None


@pytest.mark.asyncio
async def test_opener_is_idempotent_per_silence_anchor() -> None:
    runtime, ledger, _worker, _turn = _delivered_reply_world()
    await _deliver_reply(runtime)
    await _advance(runtime, ledger, seconds=IDLE_THRESHOLD * 2, tick_id="tick-idle")
    opener = SilenceAppraisalTriggerOpener(
        ledger=ledger, owner_id="worker:appraisal", idle_seconds_threshold=IDLE_THRESHOLD
    )

    trigger_id = await opener.open_once()
    duplicate = await opener.open_once()

    assert trigger_id is not None and duplicate is None
    process = next(
        item
        for item in ledger.project().trigger_processes
        if item.process_kind == "silence_appraisal"
    )
    assert process.trigger_id == trigger_id
    assert process.state == "open"
    assert process.source_evidence_ref == _receipt_event_id(ledger)
    assert process.trigger_ref == f"silence:{process.source_evidence_ref}"


# --- reducer layer ----------------------------------------------------------


def _opened_event(process: TriggerProcess) -> WorldEvent:
    payload = {"process": process.model_dump(mode="json")}
    identity = domain_idempotency_key(
        event_type="TriggerProcessOpened", world_id=WORLD_ID, payload=payload
    )
    assert identity is not None
    return WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:silence-test:opened:" + _digest(process.trigger_id),
        world_id=WORLD_ID,
        event_type="TriggerProcessOpened",
        logical_time=NOW + timedelta(seconds=IDLE_THRESHOLD * 2),
        created_at=NOW,
        actor="worker:appraisal",
        source="test",
        trace_id="trace:silence-test",
        causation_id="test",
        correlation_id="test",
        idempotency_key=identity,
        payload=payload,
    )


@pytest.mark.asyncio
async def test_reducer_rejects_a_silence_trigger_bound_to_a_non_receipt_event() -> None:
    runtime, ledger, _worker, _turn = _delivered_reply_world()
    await _deliver_reply(runtime)
    observation_ref = next(
        item.event_id
        for item in ledger.project().committed_world_event_refs
        if item.event_type == "ObservationRecorded"
    )
    projection = ledger.project()
    forged = TriggerProcess(
        trigger_id=silence_appraisal_trigger_identity(WORLD_ID, observation_ref),
        trigger_ref=f"silence:{observation_ref}",
        process_kind="silence_appraisal",
        source_evidence_ref=observation_ref,
        state="open",
    )

    with pytest.raises(ValueError, match="requires a committed execution receipt"):
        ledger.commit(
            (_opened_event(forged),),
            expected_world_revision=projection.world_revision,
            expected_deliberation_revision=projection.deliberation_revision,
        )

@pytest.mark.asyncio
async def test_reducer_rejects_a_silence_trigger_with_a_forged_identity() -> None:
    runtime, ledger, _worker, _turn = _delivered_reply_world()
    await _deliver_reply(runtime)
    receipt_ref = _receipt_event_id(ledger)
    projection = ledger.project()
    forged = TriggerProcess(
        trigger_id="trigger:silence-appraisal:" + "0" * 64,
        trigger_ref=f"silence:{receipt_ref}",
        process_kind="silence_appraisal",
        source_evidence_ref=receipt_ref,
        state="open",
    )

    with pytest.raises(ValueError, match="identity is not deterministic"):
        ledger.commit(
            (_opened_event(forged),),
            expected_world_revision=projection.world_revision,
            expected_deliberation_revision=projection.deliberation_revision,
        )


@pytest.mark.asyncio
async def test_multi_stage_silence_appraisal_opens_progressive_ordinals_with_jitter() -> None:
    runtime, ledger, _worker, _turn = _delivered_reply_world()
    await _deliver_reply(runtime)
    await _advance(runtime, ledger, seconds=IDLE_THRESHOLD * 2, tick_id="tick-stage-1")
    opener = SilenceAppraisalTriggerOpener(
        ledger=ledger, owner_id="worker:appraisal", idle_seconds_threshold=IDLE_THRESHOLD
    )

    trigger_1 = await opener.open_once()
    assert trigger_1 is not None
    receipt_ref = _receipt_event_id(ledger)
    assert trigger_1 == silence_appraisal_trigger_identity(WORLD_ID, receipt_ref, ordinal=1)

    # Terminalize stage 1 so stage 2 can open after the jittered half-day window (6x-9x)
    from companion_daemon.world_v2.schemas import ClaimLease

    proj = ledger.project()
    open_proc = next(item for item in proj.trigger_processes if item.trigger_id == trigger_1)
    claimed_proc = open_proc.model_copy(
        update={
            "state": "claimed",
            "attempt_ids": ("attempt:silence-1",),
            "claim_lease": ClaimLease(
                owner_id="worker:appraisal",
                attempt_id="attempt:silence-1",
                acquired_at=proj.logical_time,
                expires_at=proj.logical_time + timedelta(seconds=120),
            ),
        }
    )
    claim_payload = {"process": claimed_proc.model_dump(mode="json")}
    claimed_1 = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:silence-test:claimed:1",
        world_id=WORLD_ID,
        event_type="TriggerProcessClaimed",
        logical_time=proj.logical_time,
        created_at=proj.logical_time,
        actor="worker:appraisal",
        source="test",
        trace_id="trace:silence-test",
        causation_id=trigger_1,
        correlation_id="test",
        idempotency_key=domain_idempotency_key(
            event_type="TriggerProcessClaimed", world_id=WORLD_ID, payload=claim_payload
        )
        or "claim:1",
        payload=claim_payload,
    )
    complete_payload = {
        "trigger_id": trigger_1,
        "owner_id": "worker:appraisal",
        "attempt_id": "attempt:silence-1",
        "completed_at": proj.logical_time.isoformat(),
        "runtime_outcome_ref": "appraisal:accepted:1",
    }
    completed_1 = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:silence-test:completed:1",
        world_id=WORLD_ID,
        event_type="TriggerProcessCompleted",
        logical_time=proj.logical_time,
        created_at=proj.logical_time,
        actor="worker:appraisal",
        source="test",
        trace_id="trace:silence-test",
        causation_id=trigger_1,
        correlation_id="test",
        idempotency_key=domain_idempotency_key(
            event_type="TriggerProcessCompleted", world_id=WORLD_ID, payload=complete_payload
        )
        or "complete:1",
        payload=complete_payload,
    )
    ledger.commit(
        (claimed_1, completed_1),
        expected_world_revision=proj.world_revision,
        expected_deliberation_revision=proj.deliberation_revision,
    )

    # Advancing only 2 hours is still inside the stage-2 minimum gap / window
    await _advance(runtime, ledger, seconds=IDLE_THRESHOLD * 2, tick_id="tick-too-early")
    assert await opener.open_once() is None

    # Advancing past 9x idle threshold opens stage 2 (:2)
    await _advance(runtime, ledger, seconds=IDLE_THRESHOLD * 6, tick_id="tick-stage-2")
    trigger_2 = await opener.open_once()
    assert trigger_2 == silence_appraisal_trigger_identity(WORLD_ID, receipt_ref, ordinal=2)
    assert trigger_2.endswith(":2")
