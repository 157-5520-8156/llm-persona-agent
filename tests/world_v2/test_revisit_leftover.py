from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.present_prompt import compile_slim_consider_payload
from companion_daemon.world_v2.revisit_intention_view import (
    attach_open_revisit_advisory,
    due_revisit_consideration_id,
)
from companion_daemon.world_v2.schemas import WorldEvent
from companion_daemon.world_v2.social_initiative import SocialInitiativePolicy
from test_social_initiative import NOW, _compiler_fixture


def _slim_payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "messages": ["那家店我后来又路过一次"],
        "felt": "还想把那家店的事说完",
        "stuck_with_me": "那家店还挂着",
        "wants": "想找个时候再提",
        "photo": False,
    }
    payload.update(updates)
    return payload


def test_slim_come_back_becomes_a_declared_revisit() -> None:
    compiled = compile_slim_consider_payload(
        _slim_payload(come_back="还想把那家店的事说完", come_back_in=7200)
    )

    assert compiled is not None
    revisit = compiled["expression_draft"]["revisit"]
    assert revisit["thought"] == "还想把那家店的事说完"
    assert revisit["wait_seconds"] == 7200
    assert revisit["expires_after_seconds"] == 7260


def test_slim_does_not_invent_a_revisit_or_a_time() -> None:
    missing_time = compile_slim_consider_payload(
        _slim_payload(come_back="还想把那家店的事说完")
    )
    missing_thought = compile_slim_consider_payload(_slim_payload(come_back_in=7200))
    question = compile_slim_consider_payload(
        _slim_payload(messages=["那家店后来怎么样了？"])
    )

    assert missing_time is not None
    assert missing_thought is not None
    assert question is not None
    assert missing_time["expression_draft"].get("revisit") is None
    assert missing_thought["expression_draft"].get("revisit") is None
    assert question["expression_draft"].get("revisit") is None


def test_slim_come_back_without_a_visible_message_is_not_a_timed_wake() -> None:
    compiled = compile_slim_consider_payload(
        _slim_payload(
            messages=[],
            come_back="还想把那家店的事说完",
            come_back_in=7200,
        )
    )

    assert compiled is not None
    assert compiled["expression_draft"]["timing_choice"] == "silent"
    assert compiled["expression_draft"].get("revisit") is None


def _thread_opened_event() -> WorldEvent:
    return WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:thread:leftover",
        world_id="world:social-context-test",
        event_type="ThreadOpened",
        logical_time=NOW,
        created_at=NOW,
        actor="actor:companion",
        source="test",
        trace_id="trace:leftover",
        causation_id="cause:leftover",
        correlation_id="conversation:leftover",
        idempotency_key="thread:leftover",
        payload={},
    )


def _due_thread(*, opens_at: datetime, closes_at: datetime):
    values = SimpleNamespace(
        status="open",
        subject_ref="subject:leftover",
        due_window=SimpleNamespace(opens_at=opens_at, closes_at=closes_at),
        anchor_evidence_refs=(),
    )
    return SimpleNamespace(
        thread_id="thread:leftover",
        entity_revision=1,
        values=values,
    ), SimpleNamespace(
        thread_id="thread:leftover",
        entity_revision=1,
        values_after=values,
        accepted_event_ref="event:thread:leftover",
    )


@pytest.mark.asyncio
async def test_due_thread_wakes_on_her_declared_window_during_contact_cooldown() -> None:
    compiler, projection, committed = _compiler_fixture(receptive=False)
    source = _thread_opened_event()
    committed.append(source)
    compiler._ledger.lookup_event_commit = (  # noqa: SLF001
        lambda event_id: (source, SimpleNamespace(world_revision=1))
        if event_id == source.event_id
        else None
    )
    thread, transition = _due_thread(
        opens_at=NOW + timedelta(hours=2),
        closes_at=NOW + timedelta(hours=2, seconds=60),
    )
    projection.threads = (thread,)
    projection.thread_transitions = (transition,)
    projection.actions = (
        SimpleNamespace(
            kind="proactive_message",
            state="delivered",
            logical_time=NOW + timedelta(hours=2) - timedelta(minutes=5),
        ),
    )
    projection.logical_time = NOW + timedelta(hours=2, seconds=1)

    opportunity = await compiler.next_opportunity(projection)

    assert opportunity is not None
    assert opportunity.source_kind == "thread"
    assert opportunity.source_id == "thread:leftover"
    assert opportunity.scheduled_for == thread.values.due_window.opens_at
    assert "leftover:due_thread" in opportunity.cadence_reason_codes


@pytest.mark.asyncio
async def test_due_thread_does_not_wake_before_her_declared_opening() -> None:
    compiler, projection, committed = _compiler_fixture(receptive=False)
    source = _thread_opened_event()
    committed.append(source)
    compiler._ledger.lookup_event_commit = (  # noqa: SLF001
        lambda event_id: (source, SimpleNamespace(world_revision=1))
        if event_id == source.event_id
        else None
    )
    thread, transition = _due_thread(
        opens_at=NOW + timedelta(hours=2),
        closes_at=NOW + timedelta(hours=2, seconds=60),
    )
    projection.threads = (thread,)
    projection.thread_transitions = (transition,)
    projection.logical_time = NOW + timedelta(minutes=10)

    assert await compiler.next_opportunity(projection) is None


@pytest.mark.asyncio
async def test_due_thread_does_not_use_the_situation_delay_table() -> None:
    compiler, projection, committed = _compiler_fixture(receptive=False)
    source = _thread_opened_event()
    committed.append(source)
    compiler._ledger.lookup_event_commit = (  # noqa: SLF001
        lambda event_id: (source, SimpleNamespace(world_revision=1))
        if event_id == source.event_id
        else None
    )
    thread, transition = _due_thread(
        opens_at=NOW + timedelta(seconds=7_200),
        closes_at=NOW + timedelta(seconds=7_260),
    )
    projection.threads = (thread,)
    projection.thread_transitions = (transition,)
    projection.logical_time = NOW + timedelta(seconds=7_201)
    compiler._random = SimpleNamespace(  # noqa: SLF001
        draw=lambda **_kwargs: SimpleNamespace(
            selected_candidate_ref="delay:120",
            draw_id="draw:must-not-matter",
        )
    )

    opportunity = await compiler.next_opportunity(projection)

    assert opportunity is not None
    assert opportunity.source_kind == "thread"
    assert opportunity.scheduled_for == NOW + timedelta(seconds=7_200)


@pytest.mark.asyncio
async def test_already_scheduled_followup_is_not_a_second_leftover_wake() -> None:
    compiler, projection, committed = _compiler_fixture(receptive=False)
    source = _thread_opened_event()
    committed.append(source)
    compiler._ledger.lookup_event_commit = (  # noqa: SLF001
        lambda event_id: (source, SimpleNamespace(world_revision=1))
        if event_id == source.event_id
        else None
    )
    thread, transition = _due_thread(
        opens_at=NOW + timedelta(hours=2),
        closes_at=NOW + timedelta(hours=2, seconds=60),
    )
    projection.threads = (thread,)
    projection.thread_transitions = (transition,)
    projection.commitments = (
        SimpleNamespace(
            values=SimpleNamespace(
                status="open",
                subject_ref="subject:leftover",
                due_window=thread.values.due_window,
                anchor_evidence_refs=(),
                fulfillment_contract=SimpleNamespace(expected_action_id="action:followup"),
            )
        ),
    )
    projection.actions = (
        SimpleNamespace(
            action_id="action:followup",
            kind="followup",
            state="authorized",
            logical_time=NOW,
        ),
    )
    projection.logical_time = NOW + timedelta(hours=2, seconds=1)

    assert await compiler.next_opportunity(projection) is None


def _receipt_event() -> WorldEvent:
    return WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:receipt:revisit",
        world_id="world:social-context-test",
        event_type="ExecutionReceiptRecorded",
        logical_time=NOW,
        created_at=NOW,
        actor="actor:companion",
        source="test",
        trace_id="trace:revisit",
        causation_id="cause:revisit",
        correlation_id="conversation:revisit",
        idempotency_key="receipt:revisit",
        payload={"action_id": "action:revisit", "observed_state": "delivered"},
    )


@pytest.mark.asyncio
async def test_declared_revisit_wakes_on_her_wait_even_if_he_has_spoken() -> None:
    compiler, projection, committed = _compiler_fixture(receptive=False)
    receipt = _receipt_event()
    committed.append(receipt)
    compiler._ledger.lookup_event_commit = (  # noqa: SLF001
        lambda event_id: (receipt, SimpleNamespace(world_revision=1))
        if event_id == receipt.event_id
        else None
    )
    action = SimpleNamespace(
        action_id="action:revisit",
        kind="reply",
        state="delivered",
        logical_time=NOW,
    )
    projection.actions = (action,)
    projection.execution_receipts = (
        SimpleNamespace(action_id="action:revisit", observed_state="delivered"),
    )
    projection.committed_world_event_refs = (
        SimpleNamespace(
            event_id=receipt.event_id,
            event_type="ExecutionReceiptRecorded",
            world_revision=1,
            logical_time=NOW,
        ),
    )
    projection.expression_plan_manifests = (
        SimpleNamespace(
            plan_id="plan:revisit",
            revisit=SimpleNamespace(
                source_beat_id="beat:revisit",
                thought="还想把那家店的事说完",
                not_before=NOW + timedelta(hours=2),
                expires_at=NOW + timedelta(hours=2, seconds=60),
            ),
            beats=(SimpleNamespace(beat_id="beat:revisit", action=action),),
        ),
    )
    projection.message_observations = (
        SimpleNamespace(observation_id="message:source", world_revision=1),
        SimpleNamespace(observation_id="message:he-replied", world_revision=2),
    )
    projection.logical_time = NOW + timedelta(hours=2, seconds=1)

    opportunity = await compiler.next_opportunity(projection)

    assert opportunity is not None
    assert opportunity.source_kind == "revisit_intention"
    assert opportunity.source_id == "plan:revisit"
    assert opportunity.scheduled_for == NOW + timedelta(hours=2)
    assert "leftover:due_revisit" in opportunity.cadence_reason_codes


@pytest.mark.asyncio
async def test_open_revisit_process_recovers_even_if_he_has_spoken() -> None:
    compiler, projection, committed = _compiler_fixture(receptive=False)
    receipt = _receipt_event()
    committed.append(receipt)
    compiler._ledger.lookup_event_commit = (  # noqa: SLF001
        lambda event_id: (receipt, SimpleNamespace(world_revision=1))
        if event_id == receipt.event_id
        else None
    )
    action = SimpleNamespace(
        action_id="action:revisit",
        kind="reply",
        state="delivered",
        logical_time=NOW,
    )
    consideration_id = due_revisit_consideration_id("plan:revisit")
    projection.actions = (action,)
    projection.execution_receipts = (
        SimpleNamespace(action_id="action:revisit", observed_state="delivered"),
    )
    projection.committed_world_event_refs = (
        SimpleNamespace(
            event_id=receipt.event_id,
            event_type="ExecutionReceiptRecorded",
            world_revision=1,
            logical_time=NOW,
        ),
    )
    projection.expression_plan_manifests = (
        SimpleNamespace(
            plan_id="plan:revisit",
            revisit=SimpleNamespace(
                source_beat_id="beat:revisit",
                thought="还想把那家店的事说完",
                not_before=NOW + timedelta(hours=2),
                expires_at=NOW + timedelta(hours=2, seconds=60),
            ),
            beats=(SimpleNamespace(beat_id="beat:revisit", action=action),),
        ),
    )
    projection.message_observations = (
        SimpleNamespace(observation_id="message:source", world_revision=1),
        SimpleNamespace(observation_id="message:he-replied", world_revision=2),
    )
    projection.trigger_processes = (
        SimpleNamespace(
            process_kind="proactive_action_deliberation",
            state="open",
            source_evidence_ref=receipt.event_id,
            trigger_ref="proactive-consideration:" + consideration_id,
        ),
    )
    projection.logical_time = NOW + timedelta(hours=2, seconds=1)

    opportunity = await compiler.next_opportunity(projection)

    assert opportunity is not None
    assert opportunity.source_kind == "revisit_intention"
    assert opportunity.source_id == "plan:revisit"
    assert "recovery:persisted_process" in opportunity.cadence_reason_codes


def test_open_revisit_is_readable_before_its_wait_even_if_he_has_spoken() -> None:
    receipt_ref = SimpleNamespace(
        event_id="event:receipt:revisit",
        event_type="ExecutionReceiptRecorded",
        world_revision=1,
        logical_time=NOW,
    )
    action = SimpleNamespace(action_id="action:revisit")
    projection = SimpleNamespace(
        logical_time=NOW + timedelta(minutes=10),
        execution_receipts=(
            SimpleNamespace(action_id="action:revisit", observed_state="delivered"),
        ),
        committed_world_event_refs=(receipt_ref,),
        expression_plan_manifests=(
            SimpleNamespace(
                plan_id="plan:revisit",
                revisit=SimpleNamespace(
                    source_beat_id="beat:revisit",
                    thought="还想把那家店的事说完",
                    not_before=NOW + timedelta(hours=2),
                    expires_at=NOW + timedelta(hours=2, seconds=60),
                ),
                beats=(SimpleNamespace(beat_id="beat:revisit", action=action),),
            ),
        ),
        message_observations=(
            SimpleNamespace(observation_id="message:he-replied", world_revision=2),
        ),
    )

    context = attach_open_revisit_advisory({"slices": {}}, projection)

    items = context["slices"]["advisories"]["items"]
    assert len(items) == 1
    assert items[0]["value"]["kind"] == "revisit_intention"
    assert "还想把那家店的事说完" in items[0]["value"]["candidates"][0]["value"]
    assert "even if he has spoken" in items[0]["value"]["candidates"][0]["value"]


def test_social_initiative_policy_still_owns_only_opportunity_timing() -> None:
    policy = SocialInitiativePolicy(
        spontaneous_idle_seconds=1_800,
        spontaneous_expiry_seconds=43_200,
        contact_cooldown_seconds=900,
    )
    assert policy.contact_cooldown_seconds == 900
