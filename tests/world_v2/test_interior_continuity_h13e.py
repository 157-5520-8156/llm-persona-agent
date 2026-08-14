from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.response_expectation_view import (
    expired_expectation_consideration_id,
    expired_unanswered_expectation,
)
from companion_daemon.world_v2.schemas import WorldEvent
from test_expectation_feelings import HOPED, NOW as EXPECTATION_NOW
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


def _expired_projection(projection, *, expires_at, assessments=(), extra_obs=None):
    action = SimpleNamespace(action_id="action:invite", kind="reply", state="delivered")
    receipt_ref = SimpleNamespace(
        event_id="event:receipt:invite",
        event_type="ExecutionReceiptRecorded",
        world_revision=2,
        logical_time=EXPECTATION_NOW,
    )
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
                not_before=EXPECTATION_NOW + timedelta(minutes=1),
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
    assert opportunity.source_kind == "spontaneous_contact"
    assert opportunity.consideration_id == expired_expectation_consideration_id(
        "plan:invite"
    )
    assert opportunity.source_event_ref == "event:receipt:invite"
    assert opportunity.cadence_reason_codes == ("expectation:expired_unanswered",)
    assert committed == []


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
