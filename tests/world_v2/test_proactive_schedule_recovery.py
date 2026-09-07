from __future__ import annotations

from datetime import timedelta
import json

import pytest

from companion_daemon.world_v2.accepted_ledger_batch import AcceptedLedgerBatchIssuer
from companion_daemon.world_v2.deferred_reply_runtime import DeferredReplyRuntime
from companion_daemon.world_v2.event_identity import domain_idempotency_key
from companion_daemon.world_v2.proactive_action import next_proactive_retry_due
from test_commitment_authority import (
    DUE,
    accept,
    changed,
    commitment,
    event as commitment_event,
    initialized as commitment_ledger,
)
from companion_daemon.world_v2.schemas import Observation, WorldEvent
from test_proactive_action_production import (
    _DraftModel,
    _TimeoutProactiveModel,
    _make_proactive_runtime,
)
from test_thread_authority import NOW, initialized
from test_thread_reschedule_attention import _accept_thread, _commit, _compiler, _tick


def _runtime(ledger, model):
    return _make_proactive_runtime(
        ledger=ledger,
        issuer=AcceptedLedgerBatchIssuer(),
        model=model,
        social_initiative=_compiler(ledger),
    )[0]


def _advance_clock(ledger, at):
    clock = WorldEvent.from_payload(
        schema_version="world-v2.1", world_id=ledger.world_id,
        event_id=f"clock:{at.isoformat()}", event_type="ClockAdvanced",
        logical_time=at, created_at=at, actor="system:test", source="test",
        trace_id="trace:recovery", causation_id="cause:recovery",
        correlation_id="correlation:recovery", idempotency_key=f"clock:{at.isoformat()}",
        payload={"logical_time_from": ledger.project().logical_time.isoformat(),
                 "logical_time_to": at.isoformat()},
    )
    _commit(ledger, [clock])
    return clock


def _declared_domain(source_type):
    if source_type.startswith("thread"):
        ledger = initialized()
        due = NOW + timedelta(minutes=5)
        current = _accept_thread(ledger, None, due=due, duration=timedelta(hours=1))
        _advance_clock(ledger, due)
        if source_type == "thread_updated":
            current = _accept_thread(
                ledger, current, due=due, duration=timedelta(hours=1), importance=7000,
            )
    else:
        ledger = commitment_ledger()
        current = commitment()
        accept(ledger, changed(
            operation="open", before=None, after=current,
            proposal_id="proposal:open", world_revision=ledger.project().world_revision,
        ))
        clock = _advance_clock(ledger, DUE)
        if source_type == "commitment_due":
            _commit(ledger, DeferredReplyRuntime(ledger=ledger).clock_events(
                projection=ledger.project(), clock_event=clock,
            ))
            current = ledger.project().commitments[-1]
    return ledger, current


def _record_user_message(ledger):
    now = ledger.project().logical_time
    observation = Observation(
        schema_version="world-v2.1", world_id=ledger.world_id,
        observation_id="new-user-context", logical_time=now, created_at=now,
        trace_id="trace:recovery", causation_id="cause:recovery",
        correlation_id="conversation:recovery", source="test",
        source_event_id="message:new-user-context", actor="system:test", channel="test",
        payload_ref="payload:new-user-context", payload_hash="sha256:" + "9" * 64,
        text="刚才我在吃饭，现在回来了。", received_at=now,
        reply_context={"target": "actor:companion", "platform_message_id": "new-user-context"},
    )
    _commit(ledger, [WorldEvent.from_payload(
        schema_version="world-v2.1", world_id=ledger.world_id,
        event_id="event:observation:new-user-context", event_type="ObservationRecorded",
        logical_time=now, created_at=now, actor="system:test", source="test",
        trace_id=observation.trace_id, causation_id=observation.causation_id,
        correlation_id=observation.correlation_id,
        idempotency_key=domain_idempotency_key(
            event_type="ObservationRecorded", world_id=ledger.world_id,
            payload=observation.model_dump(mode="json"),
        ),
        payload=observation.model_dump(mode="json"),
    )])


@pytest.mark.asyncio
async def test_open_thread_consideration_survives_a_material_update_in_the_same_schedule():
    ledger = initialized()
    due = NOW + timedelta(minutes=5)
    first = _accept_thread(ledger, None, due=due)
    _tick(ledger, due)
    model = _DraftModel("silent")
    runtime = _runtime(ledger, model)
    assert (await runtime.drain_one()).status == "opened"
    original = ledger.project().trigger_processes[-1]

    _accept_thread(ledger, first, due=due, importance=7000)
    restarted = _runtime(ledger, model)
    result = await restarted.drain_one()

    assert result.status == "silent", result
    assert model.calls == 1
    process = ledger.project().trigger_processes[-1]
    assert process.trigger_id == original.trigger_id
    assert process.source_evidence_ref == original.source_evidence_ref
    assert process.runtime_outcome_ref == "proactive:silent"
    assert (await restarted.drain_one()).status == "idle"
    assert model.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("return_to_original_window", [False, True])
async def test_accepted_reschedule_retires_the_old_process_and_considers_the_new_schedule_once(
    return_to_original_window,
):
    ledger = initialized()
    original_due = NOW + timedelta(minutes=5)
    first = _accept_thread(ledger, None, due=original_due)
    _tick(ledger, original_due)
    model = _DraftModel("silent")
    runtime = _runtime(ledger, model)
    assert (await runtime.drain_one()).status == "opened"
    original = ledger.project().trigger_processes[-1]

    new_due = original_due + timedelta(minutes=20)
    rescheduled = _accept_thread(ledger, first, due=new_due)
    if return_to_original_window:
        new_due = original_due
        rescheduled = _accept_thread(ledger, rescheduled, due=new_due)
    restarted = _runtime(ledger, model)
    rejected = await restarted.drain_one()
    assert rejected.reason_code == "proactive.source_binding_invalid"
    assert model.calls == 0
    assert ledger.project().trigger_processes[-1].runtime_outcome_ref == (
        "proactive:source-binding-invalid"
    )

    if new_due != original_due:
        assert (await restarted.drain_one()).status == "idle"
        _tick(ledger, new_due)
    assert (await restarted.drain_one()).status == "opened"
    renewed = ledger.project().trigger_processes[-1]
    assert renewed.trigger_id != original.trigger_id
    assert renewed.source_evidence_ref == rescheduled.origin.accepted_event_ref
    assert (await restarted.drain_one()).status == "silent"
    assert model.calls == 1
    assert (await restarted.drain_one()).status == "idle"


@pytest.mark.asyncio
async def test_open_commitment_consideration_survives_its_mechanical_due_transition():
    ledger = commitment_ledger()
    current = commitment()
    accept(ledger, changed(
        operation="open", before=None, after=current,
        proposal_id="proposal:open", world_revision=ledger.project().world_revision,
    ))
    clock = commitment_event(
        "clock:due", "ClockAdvanced",
        {"logical_time_from": ledger.project().logical_time.isoformat(),
         "logical_time_to": DUE.isoformat()},
        at=DUE,
    )
    _commit(ledger, [clock])
    model = _DraftModel("silent")
    runtime = _runtime(ledger, model)
    assert (await runtime.drain_one()).status == "opened"
    original = ledger.project().trigger_processes[-1]

    _commit(ledger, DeferredReplyRuntime(ledger=ledger).clock_events(
        projection=ledger.project(), clock_event=clock,
    ))
    assert ledger.project().commitments[-1].values.status == "due"
    restarted = _runtime(ledger, model)
    result = await restarted.drain_one()
    assert result.status == "silent", result
    assert model.calls == 1
    process = ledger.project().trigger_processes[-1]
    assert process.trigger_id == original.trigger_id
    assert process.source_evidence_ref == current.origin.accepted_event_ref
    assert (await restarted.drain_one()).status == "idle"


@pytest.mark.asyncio
@pytest.mark.parametrize("new_user_message", [False, True])
async def test_installing_social_initiative_recovers_an_open_legacy_thread_process_once(new_user_message):
    ledger = initialized()
    due = NOW + timedelta(minutes=5)
    first = _accept_thread(ledger, None, due=due)
    _tick(ledger, due)
    model = _DraftModel("silent")
    legacy, _ = _make_proactive_runtime(
        ledger=ledger, issuer=AcceptedLedgerBatchIssuer(), model=model,
    )
    assert (await legacy.drain_one()).status == "opened"
    original = ledger.project().trigger_processes[-1]
    _accept_thread(ledger, first, due=due, importance=7000)
    if new_user_message:
        _record_user_message(ledger)

    installed = _runtime(ledger, model)
    result = await installed.drain_one()
    assert result.status == "silent", result
    assert model.calls == 1
    assert ledger.project().trigger_processes[-1].trigger_id == original.trigger_id
    assert (await installed.drain_one()).status == "idle"


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", ["commitment_open", "commitment_due"])
async def test_installing_social_initiative_recovers_a_legacy_commitment_once(source_type):
    ledger, _current = _declared_domain(source_type)
    model = _DraftModel("silent")
    legacy, _ = _make_proactive_runtime(
        ledger=ledger, issuer=AcceptedLedgerBatchIssuer(), model=model,
    )
    assert (await legacy.drain_one()).status == "opened"
    original = ledger.project().trigger_processes[-1]

    installed = _runtime(ledger, model)
    assert (await installed.drain_one()).status == "silent"
    assert model.calls == 1
    assert ledger.project().trigger_processes[-1].trigger_id == original.trigger_id
    assert (await installed.drain_one()).status == "idle"


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", [
    "thread_open", "thread_updated", "commitment_open", "commitment_due",
])
async def test_domain_consideration_retries_the_same_source_after_provider_failure(source_type):
    ledger, current = _declared_domain(source_type)
    failing_model = _TimeoutProactiveModel()
    runtime = _runtime(ledger, failing_model)
    assert (await runtime.drain_one()).status == "opened"
    original = ledger.project().trigger_processes[-1]
    assert (await runtime.drain_one()).status == "failed_safe"
    waiting = await runtime.drain_one()
    assert waiting.status == "retry_wait", waiting
    assert waiting.source_ref == current.origin.accepted_event_ref
    _advance_clock(ledger, waiting.next_retry_at)

    model = _DraftModel("silent")
    restarted = _runtime(ledger, model)
    assert (await restarted.drain_one()).status == "opened"
    retry = ledger.project().trigger_processes[-1]
    assert retry.trigger_ref == original.trigger_ref
    assert retry.source_evidence_ref == original.source_evidence_ref
    assert (await restarted.drain_one()).status == "silent"
    assert model.calls == 1
    proposal = json.loads(ledger.project().proposal_audits[-1].proposal_json)
    assert proposal["proactive_opportunity_decision"]["source_kind"] == (
        "thread" if source_type.startswith("thread") else "commitment"
    )
    assert (await restarted.drain_one()).status == "idle"


@pytest.mark.asyncio
async def test_rescheduling_after_provider_failure_does_not_retry_the_old_schedule():
    ledger, current = _declared_domain("thread_updated")
    runtime = _runtime(ledger, _TimeoutProactiveModel())
    assert (await runtime.drain_one()).status == "opened"
    original = ledger.project().trigger_processes[-1]
    assert (await runtime.drain_one()).status == "failed_safe"

    new_due = ledger.project().logical_time + timedelta(minutes=20)
    rescheduled = _accept_thread(
        ledger, current, due=new_due, duration=timedelta(hours=1),
    )
    model = _DraftModel("silent")
    restarted = _runtime(ledger, model)
    assert (await restarted.drain_one()).status == "idle"
    _advance_clock(ledger, new_due)
    assert (await restarted.drain_one()).status == "opened"
    renewed = ledger.project().trigger_processes[-1]
    assert renewed.trigger_ref != original.trigger_ref
    assert renewed.source_evidence_ref == rescheduled.origin.accepted_event_ref
    assert (await restarted.drain_one()).status == "silent"
    assert model.calls == 1
    assert (await restarted.drain_one()).status == "idle"


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", ["thread_open", "commitment_open"])
@pytest.mark.parametrize("failed", [False, True])
async def test_new_user_context_does_not_cancel_an_unfinished_domain_consideration(source_type, failed):
    ledger, current = _declared_domain(source_type)
    runtime = _runtime(ledger, _TimeoutProactiveModel())
    assert (await runtime.drain_one()).status == "opened"
    original = ledger.project().trigger_processes[-1]
    if failed:
        assert (await runtime.drain_one()).status == "failed_safe"
        waiting = await runtime.drain_one()
        assert waiting.status == "retry_wait"
    _record_user_message(ledger)
    if failed:
        assert (await runtime.drain_one()).status == "retry_wait"
        _advance_clock(ledger, waiting.next_retry_at)

    model = _DraftModel("silent")
    restarted = _runtime(ledger, model)
    if failed:
        assert (await restarted.drain_one()).status == "opened"
    result = await restarted.drain_one()
    assert result.status == "silent", result
    process = ledger.project().trigger_processes[-1]
    assert process.trigger_ref == original.trigger_ref
    assert process.source_evidence_ref == current.origin.accepted_event_ref
    assert model.calls == 1


@pytest.mark.asyncio
async def test_superseded_newer_failure_does_not_hide_an_older_valid_domain_retry():
    ledger, first = _declared_domain("thread_open")
    runtime = _runtime(ledger, _TimeoutProactiveModel())
    assert (await runtime.drain_one()).status == "opened"
    assert (await runtime.drain_one()).status == "failed_safe"
    second = _accept_thread(
        ledger, None, due=ledger.project().logical_time,
        duration=timedelta(hours=1), thread_id="thread:second",
    )
    assert (await runtime.drain_one()).status == "opened"
    assert (await runtime.drain_one()).status == "failed_safe"
    _accept_thread(
        ledger, second, due=ledger.project().logical_time + timedelta(minutes=20),
        duration=timedelta(hours=1),
    )
    waiting = await runtime.drain_one()
    assert waiting.status == "retry_wait", waiting
    assert waiting.source_ref == first.origin.accepted_event_ref
    _advance_clock(ledger, waiting.next_retry_at)
    model = _DraftModel("silent")
    restarted = _runtime(ledger, model)
    assert (await restarted.drain_one()).status == "opened"
    assert (await restarted.drain_one()).status == "silent"
    assert ledger.project().trigger_processes[-1].source_evidence_ref == first.origin.accepted_event_ref
    assert model.calls == 1


@pytest.mark.asyncio
async def test_newer_domain_backoff_does_not_delay_an_older_ready_retry():
    ledger, first = _declared_domain("thread_open")
    runtime = _runtime(ledger, _TimeoutProactiveModel())
    assert (await runtime.drain_one()).status == "opened"
    assert (await runtime.drain_one()).status == "failed_safe"
    first_retry_at = (await runtime.drain_one()).next_retry_at
    _advance_clock(ledger, ledger.project().logical_time + timedelta(minutes=1))
    _accept_thread(
        ledger, None, due=ledger.project().logical_time,
        duration=timedelta(hours=1), thread_id="thread:second",
    )
    assert (await runtime.drain_one()).status == "opened"
    assert (await runtime.drain_one()).status == "failed_safe"
    assert next_proactive_retry_due(ledger.project()) == first_retry_at
    _advance_clock(ledger, first_retry_at)
    model = _DraftModel("silent")
    restarted = _runtime(ledger, model)
    opened = await restarted.drain_one()
    assert opened.status == "opened", opened
    assert opened.source_ref == first.origin.accepted_event_ref
    assert (await restarted.drain_one()).status == "silent"
    assert model.calls == 1
    waiting = await restarted.drain_one()
    assert waiting.status == "retry_wait", waiting
    _advance_clock(ledger, waiting.next_retry_at)
    assert (await restarted.drain_one()).status == "opened"
    assert (await restarted.drain_one()).status == "silent"
    assert model.calls == 2
