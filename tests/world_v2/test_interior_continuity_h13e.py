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
        lambda _projection: SimpleNamespace(
            plan_id="plan:invite",
            receipt_event_id=receipt.event_id,
            receipt_world_revision=receipt_ref.world_revision,
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


def test_expired_expectation_context_states_the_hope_as_fact(monkeypatch) -> None:
    monkeypatch.setattr(
        proactive_action_module,
        "expired_unanswered_expectation",
        lambda _projection: SimpleNamespace(hoped_response=HOPED),
    )
    context = _proactive_opportunity_context(
        opportunity=SimpleNamespace(
            source_kind="expired_expectation",
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
    assert "should" not in context.lower()
    assert "追问" not in context
    assert HOPED in value
    assert len(value) <= 256


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
