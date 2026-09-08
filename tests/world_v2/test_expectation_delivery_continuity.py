"""A transport failure must not become evidence that the user ignored a message."""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.proactive_action import compile_proactive_self_history_advisories
from companion_daemon.world_v2.response_expectation_view import (
    living_unanswered_hope,
    pending_response_expectation,
    pending_response_expectation_manifest,
    unanswered_response_expectations,
)
from test_expectation_feelings import (
    HOPED, NOW, _build_app, _DeliveredTransport, _expectation, _fake_projection,
)


class _AcknowledgedTransport(_DeliveredTransport):
    async def send(self, request):
        receipt = await super().send(request)
        return receipt.model_copy(update={"status": "provider_accepted"})


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ("failed", "unknown"))
async def test_terminal_receipt_reaches_hope_views_and_next_inbound(tmp_path, terminal_status):
    app, cognition = _build_app(
        tmp_path,
        name=f"expectation-{terminal_status}",
        silence_idle_seconds=None,
        transport=_AcknowledgedTransport(),
    )
    try:
        await app.inbound(
            platform="http", platform_user_id="user.1", platform_message_id="first",
            text="我先去忙一会儿", observed_at=NOW, trace_id="trace:invite",
        )
        sent = await app.drain_actions_once()
        assert sent.provider_status == "provider_accepted"
        before = app._ledger.project()  # noqa: SLF001
        action = next(item for item in before.actions if item.action_id == sent.action_id)
        inviting_receipt = next(
            item for item in before.committed_world_event_refs
            if item.event_type == "ExecutionReceiptRecorded"
        )
        assert living_unanswered_hope(before).hoped_response == HOPED

        await app.receipt(
            source="platform:test", source_event_id="terminal:invite",
            action_id=action.action_id, idempotency_key=action.idempotency_key,
            status=terminal_status, provider_ref="message:expectation:1:terminal",
            observed_at=NOW + timedelta(seconds=30), trace_id="trace:terminal",
            causation_id=inviting_receipt.event_id, correlation_id=action.correlation_id,
            raw_payload_hash="sha256:" + "c" * 64,
        )
        after = app._ledger.project()  # noqa: SLF001
        assert next(item for item in after.actions if item.action_id == action.action_id).state == terminal_status
        terminal_ref = next(
            item for item in reversed(after.committed_world_event_refs)
            if item.event_type == "ExecutionReceiptRecorded"
        )
        assert living_unanswered_hope(after) is None
        assert pending_response_expectation(after) is None
        assert pending_response_expectation(after, anchor_event_ref=inviting_receipt.event_id) is None
        assert unanswered_response_expectations(after, due_only=False) == ()
        assert compile_proactive_self_history_advisories(after) == ()

        # The later callback changes present evidence, never the historical
        # invitation supplied to an inbound that preceded the callback.
        assert pending_response_expectation_manifest(
            after, before_world_revision=terminal_ref.world_revision,
            at_logical_time=NOW + timedelta(seconds=15),
        ) is not None
        assert pending_response_expectation(
            after, before_world_revision=terminal_ref.world_revision,
        ) is not None
        assert pending_response_expectation_manifest(
            after, before_world_revision=after.world_revision + 1,
        ) is None

        app.close()
        app, cognition = _build_app(
            tmp_path,
            name=f"expectation-{terminal_status}",
            silence_idle_seconds=None,
            transport=_AcknowledgedTransport(),
        )
        restored = app._ledger.project()  # noqa: SLF001
        assert restored.semantic_hash == after.semantic_hash
        assert living_unanswered_hope(restored) is None

        await app.inbound(
            platform="http", platform_user_id="user.1", platform_message_id="return",
            text="忙完了，刚才有什么事吗？", observed_at=NOW + timedelta(seconds=120),
            trace_id="trace:return",
        )
        last_observation = next(
            item for item in reversed(app._ledger.project().committed_world_event_refs)  # noqa: SLF001
            if item.event_type == "ObservationRecorded"
        )
        request = cognition.request_for(last_observation.event_id)
        assert '"response_expectation"' not in request.model_content_json
        assert HOPED not in request.model_content_json
    finally:
        app.close()


def test_failed_acknowledgements_do_not_count_as_unanswered_outreach():
    from test_interior_continuity_h13e import _self_history_projection

    projection = _self_history_projection()
    # Two accepted sends later fail/become unknown. The third really arrived.
    projection.execution_receipts = tuple(
        SimpleNamespace(action_id=item.action_id, observed_state="provider_accepted")
        if index < 2 else item
        for index, item in enumerate(projection.execution_receipts)
    )
    for index, state in enumerate(("failed", "unknown"), start=2):
        projection.execution_receipts += (
            SimpleNamespace(action_id=f"action:proactive:{index}", observed_state=state),
        )
        projection.committed_world_event_refs += (
            SimpleNamespace(
                event_id=f"receipt:terminal:{index}", event_type="ExecutionReceiptRecorded",
                world_revision=10 + index, logical_time=projection.logical_time,
            ),
        )
    assert compile_proactive_self_history_advisories(projection) == ()


def _append_receipt(projection, *, action_id, state, revision):
    ref = SimpleNamespace(
        event_id=f"receipt:{action_id}:{revision}", event_type="ExecutionReceiptRecorded",
        world_revision=revision, logical_time=NOW + timedelta(seconds=revision),
    )
    projection.committed_world_event_refs += (ref,)
    projection.execution_receipts += (
        SimpleNamespace(action_id=action_id, observed_state=state),
    )
    return ref


def _acknowledged_projection():
    projection = _fake_projection(
        logical_time=NOW + timedelta(minutes=10), receipt_state="provider_accepted",
        expectation=_expectation(expires_at=NOW + timedelta(hours=1)),
    )
    projection.message_observations = ()
    return projection


def test_reconciliation_restores_delivery_without_moving_original_invitation():
    projection = _acknowledged_projection()
    _append_receipt(projection, action_id="action:invite", state="unknown", revision=8)
    _append_receipt(projection, action_id="action:invite", state="delivered", revision=10)
    assert pending_response_expectation(projection, before_world_revision=10) is None
    assert pending_response_expectation_manifest(projection, before_world_revision=10) is None
    assert pending_response_expectation_manifest(projection, before_world_revision=11) is not None
    assert living_unanswered_hope(projection).declared_world_revision == 5
    assert unanswered_response_expectations(projection)[0].declared_world_revision == 5
    projection.message_observations = (
        SimpleNamespace(observation_id="message:answer", world_revision=7),
    )
    assert living_unanswered_hope(projection) is None
    assert unanswered_response_expectations(projection) == ()


def test_another_beat_cannot_restore_failed_invitation_or_supply_future_anchor():
    projection = _acknowledged_projection()
    projection.expression_plan_manifests[0].beats += (
        SimpleNamespace(beat_id="beat:closing", action=SimpleNamespace(action_id="action:closing")),
    )
    _append_receipt(projection, action_id="action:closing", state="provider_accepted", revision=6)
    _append_receipt(projection, action_id="action:invite", state="failed", revision=8)
    anchor = _append_receipt(projection, action_id="action:closing", state="delivered", revision=9)
    assert pending_response_expectation(projection, anchor_event_ref=anchor.event_id) is None
    assert pending_response_expectation(
        projection, anchor_event_ref=anchor.event_id, before_world_revision=8,
    ) is None
