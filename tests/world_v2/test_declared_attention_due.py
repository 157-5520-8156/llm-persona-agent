from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.proactive_action import (
    ProactiveOpportunity,
    _proactive_opportunity_context,
)
from companion_daemon.world_v2.response_expectation_view import (
    expired_expectation_consideration_id,
)
from companion_daemon.world_v2.revisit_intention_view import (
    due_commitment_consideration_id,
    due_revisit_consideration_id,
    due_thread_consideration_id,
    unfinished_revisits,
)
from companion_daemon.world_v2.schemas import WorldEvent
from test_social_initiative import NOW, _compiler_fixture


def _declared(compiler, projection, *, kind, name, due, revision=2):
    source_kind = {
        "expectation": "ExecutionReceiptRecorded",
        "revisit": "ExecutionReceiptRecorded",
        "thread": "ThreadOpened",
        "commitment": "PrivateCommitmentOpened",
    }[kind]
    source = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id=f"event:{name}",
        world_id=projection.world_id,
        event_type=source_kind,
        logical_time=NOW,
        created_at=NOW,
        actor="actor:companion",
        source="test",
        trace_id=f"trace:{name}",
        causation_id=f"cause:{name}",
        correlation_id="conversation:attention",
        idempotency_key=name,
        payload={},
    )
    prior_lookup = compiler._ledger.lookup_event_commit  # noqa: SLF001
    compiler._ledger.lookup_event_commit = (  # noqa: SLF001
        lambda event_id: (
            (source, SimpleNamespace(world_revision=revision))
            if event_id == source.event_id
            else prior_lookup(event_id)
        )
    )
    projection.committed_world_event_refs += (
        SimpleNamespace(
            event_id=source.event_id,
            event_type=source_kind,
            world_revision=revision,
            logical_time=NOW,
        ),
    )
    closes = due + timedelta(minutes=5)
    if kind in {"expectation", "revisit"}:
        action = SimpleNamespace(
            action_id=f"action:{name}", kind="reply", state="delivered", logical_time=NOW
        )
        projection.actions += (action,)
        projection.execution_receipts = getattr(projection, "execution_receipts", ()) + (
            SimpleNamespace(action_id=action.action_id, observed_state="delivered"),
        )
        authority = SimpleNamespace(
            source_beat_id=f"beat:{name}",
            hoped_response=f"hope {name}",
            thought=f"thought {name}",
            pressure_bp=5000,
            importance_bp=5000,
            not_before=due,
            expires_at=closes,
        )
        projection.expression_plan_manifests += (
            SimpleNamespace(
                plan_id=f"plan:{name}",
                acceptance_event_ref=f"acceptance:{name}",
                response_expectation=authority if kind == "expectation" else None,
                revisit=authority if kind == "revisit" else None,
                beats=(SimpleNamespace(beat_id=f"beat:{name}", action=action),),
            ),
        )
        consideration_id = (
            expired_expectation_consideration_id(f"plan:{name}")
            if kind == "expectation"
            else due_revisit_consideration_id(f"plan:{name}")
        )
        source_id = f"plan:{name}"
    else:
        source_id = f"{kind}:{name}"
        values = SimpleNamespace(
            status="open",
            subject_ref=f"subject:{name}",
            anchor_evidence_refs=(),
            due_window=SimpleNamespace(opens_at=due, closes_at=closes),
            fulfillment_contract=SimpleNamespace(expected_action_id=f"future:{name}"),
        )
        entity = SimpleNamespace(**{f"{kind}_id": source_id}, entity_revision=1, values=values)
        transition = SimpleNamespace(
            **{f"{kind}_id": source_id},
            entity_revision=1,
            values_after=values,
            accepted_event_ref=source.event_id,
        )
        plural = "threads" if kind == "thread" else "commitments"
        setattr(projection, plural, getattr(projection, plural) + (entity,))
        transitions = f"{kind}_transitions"
        setattr(projection, transitions, getattr(projection, transitions) + (transition,))
        consideration_id = (
            due_thread_consideration_id(source_id)
            if kind == "thread"
            else due_commitment_consideration_id(source_id)
        )
    return source, source_id, consideration_id


def _process(source, consideration_id, *, state):
    return SimpleNamespace(
        trigger_id=f"trigger:{consideration_id}",
        trigger_ref="proactive-consideration:" + consideration_id,
        process_kind="proactive_action_deliberation",
        state=state,
        source_evidence_ref=source.event_id,
        runtime_outcome_ref="proactive:silent",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["expectation", "revisit", "thread", "commitment"])
async def test_scheduler_keeps_declared_due_inside_ordinary_contact_cooldown(kind):
    compiler, projection, committed = _compiler_fixture(receptive=False)
    projection.logical_time = NOW + timedelta(minutes=10)
    due = projection.logical_time + timedelta(seconds=30)
    _declared(compiler, projection, kind=kind, name="one", due=due)
    projection.actions += (
        SimpleNamespace(
            action_id="action:recent",
            kind="proactive_message",
            state="delivered",
            logical_time=projection.logical_time,
        ),
    )

    assert await compiler.peek_next_due(projection) == due
    assert committed == []
    projection.logical_time = due
    opportunity = await compiler.next_opportunity(projection)
    assert opportunity is not None
    assert opportunity.scheduled_for == due


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["expectation", "revisit", "thread", "commitment"])
async def test_scheduler_exposes_already_due_declaration_before_a_process_exists(kind):
    compiler, projection, committed = _compiler_fixture(receptive=False)
    due = projection.logical_time - timedelta(seconds=1)
    _declared(compiler, projection, kind=kind, name="one", due=due)

    assert await compiler.peek_next_due(projection) <= projection.logical_time
    assert committed == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["expectation", "revisit"])
@pytest.mark.parametrize("newest_state", ["terminal", "excluded"])
async def test_one_considered_or_backing_off_matter_does_not_hide_another(kind, newest_state):
    compiler, projection, _ = _compiler_fixture(receptive=False)
    due = projection.logical_time - timedelta(seconds=1)
    _, old_id, _ = _declared(compiler, projection, kind=kind, name="old", due=due)
    newest_source, _, newest_id = _declared(
        compiler,
        projection,
        kind=kind,
        name="new",
        due=due,
        revision=3,
    )
    excluded = frozenset()
    if newest_state == "terminal":
        projection.trigger_processes = (_process(newest_source, newest_id, state="terminal"),)
    else:
        excluded = frozenset({newest_id})

    opportunity = await compiler.next_opportunity(
        projection,
        excluded_consideration_ids=excluded,
    )
    assert opportunity is not None
    assert opportunity.source_id == old_id


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["expectation", "revisit"])
async def test_pending_process_recovers_its_own_matter_when_a_newer_one_exists(kind):
    compiler, projection, _ = _compiler_fixture(receptive=False)
    due = projection.logical_time - timedelta(seconds=1)
    source, old_id, consideration_id = _declared(
        compiler,
        projection,
        kind=kind,
        name="old",
        due=due,
    )
    _declared(compiler, projection, kind=kind, name="new", due=due, revision=3)
    projection.trigger_processes = (_process(source, consideration_id, state="open"),)

    opportunity = await compiler.next_opportunity(projection)
    assert opportunity is not None
    assert opportunity.source_id == old_id
    assert opportunity.source_event_ref == source.event_id
    assert opportunity.consideration_id == consideration_id

    runtime_opportunity = ProactiveOpportunity(
        **opportunity.model_dump(
            exclude={"scheduled_for", "cadence_reason_codes", "policy_version", "cadence_profile"}
        ),
    )
    context = _proactive_opportunity_context(
        opportunity=runtime_opportunity,
        event=source,
        head=None,
        projection=projection,
    )
    assert f"{'hope' if kind == 'expectation' else 'thought'} old" in context
    assert f"{'hope' if kind == 'expectation' else 'thought'} new" not in context


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["expectation", "revisit"])
async def test_failed_process_retry_keeps_the_original_declared_source(kind):
    compiler, projection, _ = _compiler_fixture(receptive=False)
    due = projection.logical_time - timedelta(seconds=1)
    source, source_id, consideration_id = _declared(
        compiler,
        projection,
        kind=kind,
        name="old",
        due=due,
    )
    _declared(compiler, projection, kind=kind, name="new", due=due, revision=3)
    process = _process(source, consideration_id, state="terminal")
    process.runtime_outcome_ref = "proactive:deliberation-failed:result:failed"
    projection.trigger_processes = (process,)
    projection.model_result_audits = (
        SimpleNamespace(
            model_result_ref="result:failed",
            proposal_hash=None,
            evaluated_world_revision=4,
        ),
    )

    opportunity = await compiler.next_opportunity(projection)
    assert opportunity is not None
    assert opportunity.source_id == source_id
    assert opportunity.source_event_ref == source.event_id
    assert opportunity.consideration_id == consideration_id


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["expectation", "revisit", "thread", "commitment"])
@pytest.mark.parametrize("closed_by", ["terminal", "expiry"])
async def test_scheduler_does_not_resurrect_consumed_or_expired_declarations(kind, closed_by):
    compiler, projection, committed = _compiler_fixture(receptive=False)
    due = projection.logical_time - timedelta(seconds=1)
    source, _, consideration_id = _declared(
        compiler,
        projection,
        kind=kind,
        name="one",
        due=due,
    )
    if closed_by == "terminal":
        projection.trigger_processes = (_process(source, consideration_id, state="terminal"),)
    else:
        projection.logical_time += timedelta(hours=2)

    assert await compiler.peek_next_due(projection) is None
    assert committed == []


@pytest.mark.asyncio
async def test_internal_life_settlement_thread_and_silent_reschedule_do_not_wake_user_contact():
    compiler, projection, _ = _compiler_fixture(receptive=False)
    due = projection.logical_time - timedelta(seconds=1)
    source, thread_id, _ = _declared(compiler, projection, kind="thread", name="npc-essay", due=due)
    projection.threads[0].values.subject_ref = "event:life-aftermath:settlement:essay-contest"
    assert await compiler.next_opportunity(projection) is None

    # Restore user subject_ref, mark first schedule as having authorized a proactive message
    # after latest user message, then add a background reschedule while user is still silent.
    projection.threads[0].values.subject_ref = "subject:user-topic"
    first_cid = due_thread_consideration_id(thread_id, schedule_event_ref=source.event_id)
    first_proc = _process(source, first_cid, state="terminal")
    first_proc.runtime_outcome_ref = "proactive:authorized:plan:thread-outreach"
    projection.trigger_processes = (first_proc,)

    second_due = projection.logical_time - timedelta(seconds=1)
    second_closes = second_due + timedelta(minutes=5)
    reschedule_event = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:thread-reschedule",
        world_id=projection.world_id,
        event_type="ThreadMaterialUpdated",
        logical_time=NOW,
        created_at=NOW,
        actor="actor:companion",
        source="test",
        trace_id="trace:reschedule",
        causation_id="cause:reschedule",
        correlation_id="conversation:attention",
        idempotency_key="reschedule",
        payload={},
    )
    prior_lookup = compiler._ledger.lookup_event_commit  # noqa: SLF001
    compiler._ledger.lookup_event_commit = (  # noqa: SLF001
        lambda event_id: (
            (reschedule_event, SimpleNamespace(world_revision=4))
            if event_id == reschedule_event.event_id
            else prior_lookup(event_id)
        )
    )
    new_values = SimpleNamespace(
        status="open",
        subject_ref="subject:user-topic",
        anchor_evidence_refs=(),
        due_window=SimpleNamespace(opens_at=second_due, closes_at=second_closes),
        fulfillment_contract=SimpleNamespace(expected_action_id="future:user-topic"),
    )
    projection.threads = (
        SimpleNamespace(thread_id=thread_id, entity_revision=2, values=new_values),
    )
    projection.thread_transitions += (
        SimpleNamespace(
            thread_id=thread_id,
            entity_revision=2,
            values_after=new_values,
            accepted_event_ref=reschedule_event.event_id,
        ),
    )
    assert await compiler.next_opportunity(projection) is None


@pytest.mark.asyncio
async def test_proactive_expired_expectation_does_not_self_chain_while_user_is_silent():
    compiler, projection, _ = _compiler_fixture(receptive=False)
    due = projection.logical_time - timedelta(seconds=1)
    first_source, _, first_cid = _declared(
        compiler, projection, kind="expectation", name="first-reply", due=due, revision=2
    )
    _, _, _ = _declared(
        compiler, projection, kind="expectation", name="chained-proactive", due=due, revision=5
    )
    projection.expression_plan_manifests[-1].beats[0].action.kind = "proactive_message"
    proc = _process(first_source, first_cid, state="terminal")
    proc.runtime_outcome_ref = "proactive:authorized:plan:chained-proactive"
    projection.trigger_processes = (proc,)

    assert await compiler.next_opportunity(projection) is None


def test_late_verified_receipt_does_not_resurrect_revisit_superseded_by_newer_plan():
    compiler, projection, _ = _compiler_fixture(receptive=False)
    due = projection.logical_time - timedelta(seconds=10)
    _declared(compiler, projection, kind="revisit", name="old", due=due, revision=2)
    projection.execution_receipts[-1].observed_state = "provider_accepted"

    # A newer turn is accepted and sent at projection.logical_time (>= due)
    _declared(compiler, projection, kind="revisit", name="new", due=projection.logical_time + timedelta(minutes=15), revision=3)
    projection.committed_world_event_refs[-1].logical_time = projection.logical_time
    projection.execution_receipts[-1].observed_state = "provider_accepted"

    # Then the 90s post-send verification receipt for `action:old` arrives at revision=4 (> 3)
    projection.committed_world_event_refs += (
        SimpleNamespace(
            event_id="event:old:verified",
            event_type="ExecutionReceiptRecorded",
            world_revision=4,
            logical_time=projection.logical_time + timedelta(seconds=5),
        ),
    )
    projection.execution_receipts += (
        SimpleNamespace(action_id="action:old", observed_state="delivered"),
    )

    due_now = unfinished_revisits(projection, due_only=True)
    assert [item.plan_id for item in due_now] == []


def test_revisit_superseded_by_newer_plan_accepted_before_due_window():
    compiler, projection, _ = _compiler_fixture(receptive=False)
    due = projection.logical_time + timedelta(hours=3)
    _declared(compiler, projection, kind="revisit", name="early", due=due, revision=2)
    projection.execution_receipts[-1].observed_state = "provider_accepted"

    # A newer proactive/expression plan is accepted and delivered 30 minutes later (still before `due`)
    later_turn_time = NOW + timedelta(minutes=30)
    _declared(
        compiler,
        projection,
        kind="expectation",
        name="newer-turn",
        due=due + timedelta(hours=1),
        revision=3,
    )
    projection.committed_world_event_refs[-1].logical_time = later_turn_time
    projection.execution_receipts[-1].observed_state = "provider_accepted"

    projection.logical_time = due + timedelta(minutes=1)
    due_now = unfinished_revisits(projection, due_only=True)
    assert [item.plan_id for item in due_now] == []


@pytest.mark.asyncio
async def test_revisit_proactive_outreach_does_not_chain_into_expired_expectation_while_user_is_silent():
    compiler, projection, _ = _compiler_fixture(receptive=False)
    due = projection.logical_time - timedelta(seconds=1)
    revisit_source, _, revisit_cid = _declared(
        compiler, projection, kind="revisit", name="first-revisit", due=due, revision=2
    )
    _, _, _ = _declared(
        compiler, projection, kind="expectation", name="revisit-proactive-hope", due=due, revision=5
    )
    projection.expression_plan_manifests[-1].beats[0].action.kind = "proactive_message"
    proc = _process(revisit_source, revisit_cid, state="terminal")
    proc.runtime_outcome_ref = "proactive:authorized:plan:revisit-proactive-hope"
    projection.trigger_processes = (proc,)

    assert await compiler.next_opportunity(projection) is None


@pytest.mark.asyncio
async def test_revisit_silent_completion_does_not_spawn_post_silent_cadence():
    compiler, projection, _ = _compiler_fixture(receptive=False)
    due = projection.logical_time - timedelta(seconds=1)
    revisit_source, _, revisit_cid = _declared(
        compiler, projection, kind="revisit", name="goodnight-revisit", due=due, revision=2
    )
    proc = _process(revisit_source, revisit_cid, state="terminal")
    proc.runtime_outcome_ref = "proactive:silent"
    projection.trigger_processes = (proc,)

    assert await compiler._post_silent_chain_active(projection) is False  # noqa: SLF001
    assert await compiler._post_silent_consideration(projection, projection.logical_time) is None  # noqa: SLF001


