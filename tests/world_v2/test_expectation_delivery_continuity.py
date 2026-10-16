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


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal_status', ('delivered', 'failed', 'unknown'))
async def test_late_transport_receipt_does_not_reopen_silence_after_user_reply(tmp_path, terminal_status):
    from companion_daemon.world_v2.silence_appraisal_trigger import silence_appraisal_opportunity
    from companion_daemon.world_v2.silence_observation import silence_observation

    app, _ = _build_app(tmp_path, name=f'silence-late-{terminal_status}',
                        silence_idle_seconds=None, transport=_AcknowledgedTransport())
    try:
        await app.inbound(platform='http', platform_user_id='user.1', platform_message_id='invite',
                          text='我先去忙一会儿', observed_at=NOW, trace_id='trace:invite')
        sent = await app.drain_actions_once()
        before = app._ledger.project()
        action = next(a for a in before.actions if a.action_id == sent.action_id)
        anchor = next(e for e in before.committed_world_event_refs if e.event_type == 'ExecutionReceiptRecorded')
        # The reply is received before QQ's terminal verification. Its new
        # outbound response is deliberately not dispatched: it is not silence evidence.
        await app.inbound(platform='http', platform_user_id='user.1', platform_message_id='answered',
                          text='忙完啦，来继续聊', observed_at=NOW + timedelta(seconds=15), trace_id='trace:answered')
        await app.receipt(source='platform:test', source_event_id='late-verification',
                          action_id=action.action_id, idempotency_key=action.idempotency_key,
                          status=terminal_status, provider_ref='message:expectation:1:terminal',
                          observed_at=NOW + timedelta(seconds=30), trace_id='trace:terminal',
                          causation_id=anchor.event_id, correlation_id=action.correlation_id,
                          raw_payload_hash='sha256:' + 'c' * 64)
        current = app._ledger.project().logical_time
        await app.tick(tick_id='after-quiet', logical_time_from=current,
                       logical_time_to=NOW + timedelta(hours=2), observed_at=NOW + timedelta(hours=2),
                       trace_id='trace:quiet', causation_id='test', correlation_id='test',
                       reason='scheduled_tick', run_life_ecology=False)
        projection = app._ledger.project()
        assert silence_appraisal_opportunity(projection, idle_seconds_threshold=3600) is None
        if terminal_status == 'delivered':
            terminal = [e for e in projection.committed_world_event_refs if e.event_type == 'ExecutionReceiptRecorded'][-1]
            gap = silence_observation(projection, anchor_event_ref=terminal.event_id, actor_ref=action.actor)
            # A late receipt is transport evidence, not a new unanswered expression.
            assert gap['anchor_delivery_state'] == 'delivered'
            assert gap['unanswered_expression_groups'] == 0
    finally:
        app.close()


@pytest.mark.asyncio
async def test_one_unanswered_expression_keeps_two_day_gap_visible_to_proactive_role(tmp_path):
    app, _ = _build_app(tmp_path, name='long-gap', silence_idle_seconds=None)
    try:
        await app.inbound(platform='http', platform_user_id='user.1', platform_message_id='before-gap',
                          text='我先去忙一会儿', observed_at=NOW, trace_id='trace:before-gap')
        await app.drain_actions_once()
        current = app._ledger.project().logical_time
        await app.tick(tick_id='two-day-gap', logical_time_from=current,
                       logical_time_to=NOW + timedelta(days=2), observed_at=NOW + timedelta(days=2),
                       trace_id='trace:gap', causation_id='test', correlation_id='test',
                       reason='scheduled_tick', run_life_ecology=False)
        p = app._ledger.project()
        advisories = compile_proactive_self_history_advisories(p, actor_ref='actor:companion', target='user:primary')
        candidates = {c.candidate_ref:c.value for a in advisories for c in a.candidates}
        gap = candidates['self-history:reply_gap']
        assert '172800' in gap and '1 expression' in gap
        assert 'read status' in gap and 'motive' in gap
        assert len(gap) <= 256
        assert all(ref in {e.event_id for e in p.committed_world_event_refs}
                   for a in advisories for ref in a.source_refs)
        import json
        wire, hidden_wire = await _gap_provider_views(app._ledger)
        assert 'self-history:reply_gap' in json.dumps(wire) and '172800' in json.dumps(wire)
        assert 'self-history:reply_gap' not in json.dumps(hidden_wire)
        await app.inbound(platform='http', platform_user_id='user.1', platform_message_id='after-gap',
                          text='忙完啦', observed_at=NOW + timedelta(days=2, seconds=1), trace_id='trace:after-gap')
        after = compile_proactive_self_history_advisories(
            app._ledger.project(), actor_ref='actor:companion', target='user:primary')
        assert not any(c.candidate_ref == 'self-history:reply_gap' for a in after for c in a.candidates)
    finally:
        app.close()


async def _gap_provider_views(ledger):
    """Actual source-verified Interior projection and its privacy-filtered wire."""
    p = ledger.project()
    import hashlib
    import json
    from companion_daemon.world_v2.character_interior import InteriorOpportunity
    from companion_daemon.world_v2.character_interior.contracts import _InteriorCapabilityManifest
    from companion_daemon.world_v2.character_interior.production import _LedgerCapsuleInteriorProjection
    from companion_daemon.world_v2.ledger_context_resolver import context_capsule_compiler_from_ledger
    from companion_daemon.world_v2.background_context_profile import (
        background_context_profile_for_purpose, slice_background_inner_life_snapshot,
    )
    from companion_daemon.world_v2.schemas import ProjectionCursor
    trigger = next(e.event_id for e in reversed(p.committed_world_event_refs) if e.event_type=='ClockAdvanced')
    raw = json.dumps({'target_ref':'user:primary'}, separators=(',',':'))
    capability = _InteriorCapabilityManifest(capability_ref='capability:gap', capability_kind='proactive_contact',
        payload_json=raw, payload_hash='sha256:'+hashlib.sha256(raw.encode()).hexdigest(), source_refs=(trigger,))
    compiler = _LedgerCapsuleInteriorProjection(ledger=ledger,
        capsules=context_capsule_compiler_from_ledger(ledger=ledger), companion_actor_ref='actor:companion')
    snapshot = await compiler.project(subject=InteriorOpportunity(
        opportunity_ref='opportunity:gap', inner_turn_ref='inner:gap', world_id=p.world_id,
        actor_ref='actor:companion', trigger_ref=trigger, cursor=ProjectionCursor(world_revision=p.world_revision,
            deliberation_revision=p.deliberation_revision, ledger_sequence=p.ledger_sequence),
        logical_time=p.logical_time, purpose='proactive_contact', source_refs=(trigger,), capability_manifest=capability))
    wire = slice_background_inner_life_snapshot(snapshot.model_view(), background_context_profile_for_purpose('proactive_contact'))
    visible = frozenset(ref for ref in snapshot.source_refs if not ref.startswith('advisory:proactive:self-history:'))
    hidden_wire = slice_background_inner_life_snapshot(snapshot.model_view(visible_source_refs=visible),
        background_context_profile_for_purpose('proactive_contact'))
    return wire, hidden_wire


@pytest.mark.asyncio
async def test_more_than_32_unanswered_messages_keep_exact_totals_with_bounded_sources():
    import json
    from test_proactive_action_production import (
        _runtime, _seed_due_thread, _event, _commit, _DeliveredTransport as _VolumeTransport, WORLD,
    )
    from companion_daemon.world_v2.runtime import WorldRuntime
    from companion_daemon.world_v2.schemas import Observation

    ledger, model, proactive, _ = _runtime(choice='now', budget=1000)
    at = ledger.project().logical_time
    observation = Observation(schema_version='world-v2.1', observation_id='user:before-gap', world_id=WORLD,
        logical_time=at, created_at=at, trace_id='test:gap', causation_id='test', correlation_id='test',
        source='test', source_event_id='before-gap', actor='user:primary', channel='test',
        payload_ref='payload:user:before-gap', payload_hash='sha256:'+'9'*64, text='我先去忙了', received_at=at)
    event = _event('event:user:before-gap','ObservationRecorded',observation.model_dump(mode='json'),at=at)
    _commit(ledger, event.model_copy(update={name:getattr(observation,name)
        for name in ('actor','trace_id','causation_id','correlation_id')}))
    from companion_daemon.world_v2.platform_action_executor import PlatformActionExecutor
    from companion_daemon.world_v2.ledger_payload_reader import LedgerAuthorizedPayloadReader
    executor = PlatformActionExecutor(payloads=LedgerAuthorizedPayloadReader(ledger=ledger), transport=_VolumeTransport())
    runtime = WorldRuntime(world_id=WORLD,ledger=ledger,action_executor=executor,action_pump_owner='pump:volume')
    for index in range(33):
        if index:
            _seed_due_thread(ledger, thread_key=str(index+1), advance_clock=False, event_at=at)
        assert (await proactive.advance_due_once()).status == 'opened'
        assert (await proactive.advance_due_once()).status == 'authorized'
        assert (await runtime.drain_actions_once()).status == 'settled'
    later = at + timedelta(days=2)
    _commit(ledger, _event('event:clock:long-gap','ClockAdvanced',
        {'logical_time_from':at.isoformat(),'logical_time_to':later.isoformat()},at=later))
    p = ledger.project()
    delivered = [a for a in p.actions if a.actor=='actor:companion' and a.target=='user:primary' and a.state=='delivered']
    assert len(delivered)==33 and model.calls==33
    advisory, = compile_proactive_self_history_advisories(p, actor_ref='actor:companion', target='user:primary')
    values = {c.candidate_ref:c.value for c in advisory.candidates}
    assert '33 expression(s)' in values['self-history:reply_gap']
    assert '33 outgoing messages' in values['self-history:unanswered_outbound']
    assert '172800s ago' in values['self-history:reply_gap']
    original_count = len(p.committed_world_event_refs)
    wire, hidden = await _gap_provider_views(ledger)
    assert len(advisory.source_refs) <= 12
    payload_refs = [m.event_ref for m in p.stored_message_payloads]
    assert set(advisory.source_refs).intersection(payload_refs) == set(payload_refs[-4:])
    assert all(len(c.value)<=256 for c in advisory.candidates)
    assert '33 expression(s)' in json.dumps(wire)
    assert 'self-history:reply_gap' not in json.dumps(hidden)
    assert len(ledger.project().committed_world_event_refs)==original_count
