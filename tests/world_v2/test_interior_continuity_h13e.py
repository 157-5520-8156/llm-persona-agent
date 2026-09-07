from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

import companion_daemon.world_v2.proactive_action as proactive_action_module
from companion_daemon.world_v2.accepted_ledger_batch import AcceptedLedgerBatchIssuer
from companion_daemon.world_v2.ledger import WorldLedger
from companion_daemon.world_v2.proactive_action import (
    ProactiveOpportunity,
    _proactive_advisory_value,
    _proactive_opportunity_context,
)
from companion_daemon.world_v2.response_expectation_view import (
    expired_expectation_consideration_id,
    expired_unanswered_expectation,
)
from companion_daemon.world_v2.schemas import BudgetAccount, WorldEvent
from test_expectation_feelings import HOPED, NOW as EXPECTATION_NOW
from test_proactive_action_production import (
    NOW as PROACTIVE_NOW,
    WORLD,
    _DraftModel,
    _commit,
    _event,
    _make_proactive_runtime,
)
from test_social_initiative import _compiler_fixture


def _invite_receipt_event():
    return WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:receipt:invite",
        world_id="world:social-context-test",
        event_type="ExecutionReceiptRecorded",
        logical_time=EXPECTATION_NOW,
        created_at=EXPECTATION_NOW,
        actor="actor:companion",
        source="test",
        trace_id="trace:h13e",
        causation_id="cause:h13e",
        correlation_id="conversation:h13e",
        idempotency_key="receipt:invite",
        payload={"action_id": "action:invite", "observed_state": "delivered"},
    )


def _expired_projection(
    projection,
    *,
    expires_at,
    not_before=None,
    assessments=(),
    extra_obs=None,
):
    action = SimpleNamespace(action_id="action:invite", kind="reply", state="delivered")
    receipt_ref = SimpleNamespace(
        event_id="event:receipt:invite",
        event_type="ExecutionReceiptRecorded",
        world_revision=2,
        logical_time=EXPECTATION_NOW,
    )
    if not_before is None:
        not_before = min(expires_at, projection.logical_time) - timedelta(seconds=30)
    projection.actions = (action,)
    projection.execution_receipts = (
        SimpleNamespace(action_id="action:invite", observed_state="delivered"),
    )
    projection.committed_world_event_refs = (receipt_ref,)
    projection.expression_plan_manifests = (
        SimpleNamespace(
            plan_id="plan:invite",
            acceptance_event_ref="event:acceptance:invite",
            recorded_at_world_revision=1,
            response_expectation=SimpleNamespace(
                source_beat_id="beat:invite",
                hoped_response=HOPED,
                pressure_bp=5_000,
                importance_bp=5_000,
                not_before=not_before,
                expires_at=expires_at,
                delivery_requirement="provider_accepted_or_delivered",
            ),
            beats=(SimpleNamespace(beat_id="beat:invite", action=action),),
        ),
    )
    projection.response_expectation_assessments = assessments
    if extra_obs is not None:
        projection.message_observations = projection.message_observations + extra_obs
    return projection


def _install_receipt_lookup(compiler) -> None:
    orig = compiler._ledger.lookup_event_commit  # noqa: SLF001
    receipt = _invite_receipt_event()

    def lookup(event_id):
        if event_id == receipt.event_id:
            return receipt, SimpleNamespace(world_revision=2)
        return orig(event_id)

    compiler._ledger.lookup_event_commit = lookup  # noqa: SLF001


@pytest.mark.asyncio
async def test_expired_unanswered_hope_mints_one_quiet_gap_opportunity() -> None:
    compiler, projection, committed = _compiler_fixture(receptive=True)
    _install_receipt_lookup(compiler)
    _expired_projection(
        projection,
        expires_at=projection.logical_time - timedelta(minutes=5),
    )

    found = expired_unanswered_expectation(projection)
    opportunity = await compiler.next_opportunity(projection)

    assert found is not None
    assert found.plan_id == "plan:invite"
    assert opportunity is not None
    assert opportunity.source_kind == "expired_expectation"
    assert opportunity.consideration_id == expired_expectation_consideration_id(
        "plan:invite"
    )
    assert opportunity.source_event_ref == "event:receipt:invite"
    assert opportunity.cadence_reason_codes == ("expectation:expired_unanswered",)
    assert committed == []


@pytest.mark.asyncio
async def test_unanswered_hope_wakes_after_wait_before_expiry() -> None:
    compiler, projection, committed = _compiler_fixture(receptive=True)
    _install_receipt_lookup(compiler)
    wait_until = projection.logical_time - timedelta(seconds=30)
    _expired_projection(
        projection,
        not_before=wait_until,
        expires_at=projection.logical_time + timedelta(hours=1),
    )

    found = expired_unanswered_expectation(projection)
    opportunity = await compiler.next_opportunity(projection)

    assert found is not None
    assert found.not_before == wait_until
    assert opportunity is not None
    assert opportunity.source_kind == "expired_expectation"
    assert opportunity.scheduled_for == wait_until
    assert committed == []


@pytest.mark.asyncio
async def test_unanswered_hope_does_not_wake_before_wait() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    _expired_projection(
        projection,
        not_before=projection.logical_time + timedelta(minutes=10),
        expires_at=projection.logical_time + timedelta(hours=2),
    )

    assert expired_unanswered_expectation(projection) is None
    opportunity = await compiler.next_opportunity(projection)
    assert opportunity is None or opportunity.source_kind != "expired_expectation"


@pytest.mark.asyncio
async def test_expired_hope_past_grace_is_dropped() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    _expired_projection(
        projection,
        expires_at=projection.logical_time - timedelta(hours=2),
    )

    assert expired_unanswered_expectation(projection) is None
    opportunity = await compiler.next_opportunity(projection)
    assert opportunity is None or opportunity.consideration_id != (
        expired_expectation_consideration_id("plan:invite")
    )


@pytest.mark.asyncio
async def test_expired_hope_is_not_reminted_after_a_terminal_process() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    _install_receipt_lookup(compiler)
    consideration_id = expired_expectation_consideration_id("plan:invite")
    _expired_projection(
        projection,
        expires_at=projection.logical_time - timedelta(minutes=5),
    )
    projection.trigger_processes = (
        SimpleNamespace(
            process_kind="proactive_action_deliberation",
            trigger_ref="proactive-consideration:" + consideration_id,
            state="terminal",
            source_evidence_ref="event:receipt:invite",
            runtime_outcome_ref=None,
        ),
    )

    opportunity = await compiler.next_opportunity(projection)
    assert opportunity is None or opportunity.consideration_id != consideration_id


@pytest.mark.asyncio
async def test_still_pending_closes_the_expired_unanswered_chase() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    _expired_projection(
        projection,
        expires_at=projection.logical_time - timedelta(minutes=5),
        assessments=(
            SimpleNamespace(source_plan_id="plan:invite", status="still_pending"),
        ),
    )

    assert expired_unanswered_expectation(projection) is None
    opportunity = await compiler.next_opportunity(projection)
    assert opportunity is None or opportunity.source_kind != "expired_expectation"


@pytest.mark.asyncio
async def test_expired_expectation_opportunity_reaches_her_instead_of_failing_safe(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """The minted opportunity has to survive the host's source binding.

    The compiler tests above stop at ``next_opportunity``.  That is exactly the
    seam where H13e went wrong in production: the opportunity was minted, and
    then the proactive host rejected it because a receipt is not an
    ObservationRecorded, marked the process terminal, and permanently retired
    that expiry.
    """

    issuer = AcceptedLedgerBatchIssuer()
    ledger = WorldLedger.in_memory(world_id=WORLD, accepted_batch_issuer=issuer)
    _commit(ledger, _event("event:world:start", "WorldStarted", {}))
    _commit(
        ledger,
        _event(
            "event:budget:proactive",
            "BudgetAccountConfigured",
            {
                "account": BudgetAccount(
                    account_id="account:proactive",
                    category="proactive",
                    window_id="day:1",
                    limit=100,
                ).model_dump(mode="json")
            },
        ),
    )
    receipt = _event(
        "event:receipt:expired-hope",
        "ExecutionReceiptRecorded",
        {
            "receipt": {
                "receipt_id": "receipt:invite",
                "result_id": "result:invite",
                "action_id": "action:invite",
                "provider": "qq",
                "provider_ref": "provider:invite",
                "source_event_id": "event:action:invite",
                "receipt_kind": "terminal",
                "observed_state": "delivered",
                "is_terminal": True,
                "cost_actual": 0,
                "received_at": PROACTIVE_NOW.isoformat(),
                "raw_payload_hash": "0" * 64,
            }
        },
    )
    _commit(ledger, receipt)
    receipt_ref = next(
        item
        for item in ledger.project().committed_world_event_refs
        if item.event_id == receipt.event_id
    )
    opportunity = ProactiveOpportunity(
        source_kind="expired_expectation",
        source_id="plan:invite",
        source_event_ref=receipt.event_id,
        source_event_hash=receipt.payload_hash,
        source_world_revision=receipt_ref.world_revision,
        trace_id=receipt.trace_id,
        correlation_id=receipt.correlation_id,
        created_at=receipt.created_at,
        consideration_id=expired_expectation_consideration_id("plan:invite"),
        cadence_reason_codes=("expectation:expired_unanswered",),
    )

    class FixedInitiative:
        async def next_opportunity(self, _projection):  # type: ignore[no-untyped-def]
            return opportunity

    runtime, _turn = _make_proactive_runtime(
        ledger=ledger,
        issuer=issuer,
        model=_DraftModel("silent"),
        social_initiative=FixedInitiative(),
    )
    # The expiry itself is already covered above; what this test owns is the
    # host binding, so the pinned projection just reports the same verdict.
    monkeypatch.setattr(
        proactive_action_module,
        "expired_unanswered_expectation",
        lambda _projection, *, source_plan_id: SimpleNamespace(
            plan_id=source_plan_id,
            receipt_event_id=receipt.event_id,
            receipt_world_revision=receipt_ref.world_revision,
            declared_world_revision=receipt_ref.world_revision,
            hoped_response=HOPED,
            expires_at=PROACTIVE_NOW,
        ),
    )

    assert (await runtime.drain_one()).status == "opened"
    result = await runtime.drain_one()

    # What this test owns is the host contract, not the authoring that follows
    # it: the receipt-bound opportunity must reach deliberation instead of
    # being rejected as an invalid source.  That rejection was terminal, so it
    # also retired the expiry for good.
    assert result.reason_code != "proactive.source_binding_invalid"
    process = ledger.project().trigger_processes[-1]
    assert process.runtime_outcome_ref != "proactive:source-binding-invalid"


def test_expired_expectation_context_does_not_state_the_hope_as_fact(monkeypatch) -> None:
    monkeypatch.setattr(
        proactive_action_module,
        "expired_unanswered_expectation",
        lambda _projection, *, source_plan_id: SimpleNamespace(
            hoped_response=HOPED,
            declared_world_revision=2,
        ),
    )
    monkeypatch.setattr(
        proactive_action_module,
        "counterpart_last_spoke_facts",
        lambda _projection, since_world_revision=None: (90, True),
    )
    context = _proactive_opportunity_context(
        opportunity=SimpleNamespace(
            source_kind="expired_expectation",
            source_id="plan:invite",
            stimulus_event_refs=(),
        ),
        event=SimpleNamespace(event_id="event:receipt", payload_hash="0" * 64),
        head=None,
        projection=object(),
    )
    value = _proactive_advisory_value(
        opportunity_context=context,
        source_kind="expired_expectation",
    )

    assert HOPED in context
    assert "her words, not a world event" in context
    assert "Hope expired" not in context
    assert "He last spoke 90s ago" in context
    assert "he has spoken since she declared a hope" in context
    assert "Unanswered" not in context
    assert "没理" not in context
    assert "should" not in context.lower()
    assert "追问" not in context
    assert HOPED in value
    assert len(value) <= 256


def test_late_verified_receipt_does_not_revive_answered_hope() -> None:
    """He replied after provider_accepted; a later delivered ack must not chase.

    Production (2026-08-18): comparing against the latest receipt revision made
    a hope look unanswered after a delayed delivery verification.
    """

    compiler, projection, _committed = _compiler_fixture(receptive=True)
    _expired_projection(
        projection,
        expires_at=projection.logical_time - timedelta(minutes=5),
        extra_obs=(
            SimpleNamespace(observation_id="message:sun", world_revision=8),
        ),
    )
    first_visible = SimpleNamespace(
        event_id="event:receipt:invite:accepted",
        event_type="ExecutionReceiptRecorded",
        world_revision=2,
        logical_time=EXPECTATION_NOW,
    )
    late_verified = SimpleNamespace(
        event_id="event:receipt:invite",
        event_type="ExecutionReceiptRecorded",
        world_revision=10,
        logical_time=EXPECTATION_NOW + timedelta(minutes=2),
    )
    projection.execution_receipts = (
        SimpleNamespace(action_id="action:invite", observed_state="provider_accepted"),
        SimpleNamespace(action_id="action:invite", observed_state="delivered"),
    )
    projection.committed_world_event_refs = (first_visible, late_verified)

    assert expired_unanswered_expectation(projection) is None


def test_late_verified_receipt_still_mints_when_he_never_spoke() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    _expired_projection(
        projection,
        expires_at=projection.logical_time - timedelta(minutes=5),
    )
    first_visible = SimpleNamespace(
        event_id="event:receipt:invite:accepted",
        event_type="ExecutionReceiptRecorded",
        world_revision=2,
        logical_time=EXPECTATION_NOW,
    )
    late_verified = SimpleNamespace(
        event_id="event:receipt:invite",
        event_type="ExecutionReceiptRecorded",
        world_revision=10,
        logical_time=EXPECTATION_NOW + timedelta(minutes=2),
    )
    projection.execution_receipts = (
        SimpleNamespace(action_id="action:invite", observed_state="provider_accepted"),
        SimpleNamespace(action_id="action:invite", observed_state="delivered"),
    )
    projection.committed_world_event_refs = (first_visible, late_verified)

    found = expired_unanswered_expectation(projection)

    assert found is not None
    assert found.declared_world_revision == 2
    assert found.receipt_world_revision == 10


def test_expired_hope_is_not_unanswered_after_a_later_user_message() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    _expired_projection(
        projection,
        expires_at=projection.logical_time - timedelta(minutes=5),
        extra_obs=(
            SimpleNamespace(observation_id="message:later", world_revision=9),
        ),
    )

    assert expired_unanswered_expectation(projection) is None


def _self_history_projection(*, inbound_revision: int = 1):
    """Projection with her unanswered outbound beats and living hopes."""

    logical_time = EXPECTATION_NOW + timedelta(hours=3)
    receipts = []
    receipt_refs = []
    beats = []
    payloads = []
    texts = ("突然想到你，在干嘛呀？", "突然想到你，在干嘛呢？", "突然想到你，在干嘛呀？")
    for index, text in enumerate(texts, start=2):
        action_id = f"action:proactive:{index}"
        payload_ref = f"payload:proactive:{index}"
        receipts.append(
            SimpleNamespace(action_id=action_id, observed_state="delivered")
        )
        receipt_refs.append(
            SimpleNamespace(
                event_id=f"event:receipt:{index}",
                event_type="ExecutionReceiptRecorded",
                world_revision=index,
                logical_time=EXPECTATION_NOW + timedelta(minutes=5 * index),
            )
        )
        beats.append(
            SimpleNamespace(action_id=action_id, payload_ref=payload_ref)
        )
        payloads.append(
            SimpleNamespace(
                payload_ref=payload_ref,
                event_ref=f"event:payload:{index}",
                text=text,
            )
        )
    return SimpleNamespace(
        logical_time=logical_time,
        message_observations=(
            SimpleNamespace(observation_id="message:source", world_revision=inbound_revision),
        ),
        execution_receipts=tuple(receipts),
        committed_world_event_refs=tuple(receipt_refs),
        expression_beats=tuple(beats),
        stored_message_payloads=tuple(payloads),
        expression_plan_manifests=(),
        response_expectation_assessments=(),
    )


def test_self_history_advisory_lists_unanswered_outbound_facts() -> None:
    from companion_daemon.world_v2.proactive_action import (
        compile_proactive_self_history_advisories,
    )

    advisories = compile_proactive_self_history_advisories(_self_history_projection())

    assert len(advisories) == 1
    advisory = advisories[0]
    assert advisory.kind == "proactive_self_history"
    values = {item.candidate_ref: item.value for item in advisory.candidates}
    outbound = values["self-history:unanswered_outbound"]
    assert "you have sent 3 delivered messages" in outbound
    assert "1 of them exact repeats" in outbound
    assert "在干嘛呀" in outbound
    assert "Facts only; she still decides" in outbound
    for candidate in advisory.candidates:
        assert len(candidate.value) <= 256
    # Every listed text's payload event binds the advisory to committed sources.
    assert set(advisory.source_refs) == {
        "event:payload:2",
        "event:payload:3",
        "event:payload:4",
    }


def test_self_history_advisory_lists_living_hopes_with_wake_facts() -> None:
    from companion_daemon.world_v2.proactive_action import (
        compile_proactive_self_history_advisories,
    )

    projection = _self_history_projection()
    projection.expression_plan_manifests = (
        SimpleNamespace(
            plan_id="plan:hope:1",
            acceptance_event_ref="event:acceptance:hope:1",
            response_expectation=SimpleNamespace(
                hoped_response="他回我一句在干嘛",
                not_before=projection.logical_time + timedelta(minutes=47),
                expires_at=projection.logical_time + timedelta(hours=2),
            ),
        ),
        SimpleNamespace(
            plan_id="plan:hope:2",
            acceptance_event_ref="event:acceptance:hope:2",
            response_expectation=SimpleNamespace(
                hoped_response="他聊聊近况",
                not_before=projection.logical_time - timedelta(minutes=5),
                expires_at=projection.logical_time + timedelta(hours=1),
            ),
        ),
        # Dead hope: expiry already passed.
        SimpleNamespace(
            plan_id="plan:hope:dead",
            acceptance_event_ref="event:acceptance:hope:dead",
            response_expectation=SimpleNamespace(
                hoped_response="过期的盼头",
                not_before=projection.logical_time - timedelta(hours=2),
                expires_at=projection.logical_time - timedelta(hours=1),
            ),
        ),
        # Assessed hope: still_pending closed its consideration cycle.
        SimpleNamespace(
            plan_id="plan:hope:assessed",
            acceptance_event_ref="event:acceptance:hope:assessed",
            response_expectation=SimpleNamespace(
                hoped_response="已评估的盼头",
                not_before=projection.logical_time + timedelta(minutes=10),
                expires_at=projection.logical_time + timedelta(hours=1),
            ),
        ),
    )
    projection.response_expectation_assessments = (
        SimpleNamespace(
            source_plan_id="plan:hope:assessed", status="still_pending"
        ),
    )

    advisories = compile_proactive_self_history_advisories(projection)

    values = {item.candidate_ref: item.value for item in advisories[0].candidates}
    hopes = values["self-history:living_hopes"]
    assert "You are holding 2 waiting hope(s)" in hopes
    assert "他回我一句在干嘛" in hopes
    assert "wakes in ~47m" in hopes
    assert "wait already ran out" in hopes
    assert "过期的盼头" not in hopes
    assert "已评估的盼头" not in hopes
    assert "Each unmet hope wakes you once more" in hopes


def test_self_history_advisory_stays_silent_below_threshold() -> None:
    from companion_daemon.world_v2.proactive_action import (
        compile_proactive_self_history_advisories,
    )

    projection = _self_history_projection()
    projection.execution_receipts = projection.execution_receipts[:1]
    projection.committed_world_event_refs = projection.committed_world_event_refs[:1]
    projection.expression_beats = projection.expression_beats[:1]
    projection.stored_message_payloads = projection.stored_message_payloads[:1]

    assert compile_proactive_self_history_advisories(projection) == ()
