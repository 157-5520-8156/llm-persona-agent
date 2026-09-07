from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.revisit_intention_view import (
    due_commitment_consideration_id,
    due_thread_consideration_id,
)
from companion_daemon.world_v2.schemas import DueWindow
from companion_daemon.world_v2.social_initiative import (
    SocialInitiativeCompiler,
    SocialInitiativePolicy,
)
from companion_daemon.world_v2.thread_events import ThreadChangedPayload, thread_mutation_hash
from test_thread_authority import NOW, event, initialized, payload, proposal, thread


def _commit(ledger, events):
    projection = ledger.project()
    ledger.commit(
        events,
        expected_world_revision=projection.world_revision,
        expected_deliberation_revision=projection.deliberation_revision,
    )


def _accept_thread(ledger, before, *, due, importance=6500, duration=timedelta(minutes=10)):
    revision = 1 if before is None else before.entity_revision + 1
    after = thread(revision=revision, updated_at=ledger.project().logical_time)
    after = after.model_copy(
        update={
            "values": after.values.model_copy(
                update={
                    "due_window": DueWindow(opens_at=due, closes_at=due + duration),
                    "importance_bp": importance,
                }
            )
        }
    )
    value = payload(
        operation="open" if before is None else "update",
        before=before,
        after=after,
        expected=revision - 1,
        proposal_id=f"proposal:revision:{revision}",
    )
    raw = value.model_dump()
    raw["evaluated_world_revision"] = ledger.project().world_revision
    raw["accepted_change_hash"] = thread_mutation_hash(raw)
    value = ThreadChangedPayload.model_validate(raw)
    now = ledger.project().logical_time
    _commit(
        ledger,
        [
            event(
                f"event:{value.proposal_id}",
                "ProposalRecorded",
                proposal(value).model_dump(mode="json"),
                at=now,
            )
        ],
    )
    _commit(
        ledger,
        [
            event(
                f"event:{value.acceptance_id}",
                "AcceptanceRecorded",
                {
                    "acceptance_id": value.acceptance_id,
                    "status": "accepted",
                    "proposal_id": value.proposal_id,
                    "evaluated_world_revision": value.evaluated_world_revision,
                    "accepted_change_id": value.change_id,
                    "accepted_change_hash": value.accepted_change_hash,
                },
                at=now,
            ),
            event(
                after.origin.accepted_event_ref,
                proposal(value).proposed_mutation.event_type,
                value.model_dump(mode="json"),
                at=now,
            ),
        ],
    )
    return after


def _tick(ledger, when):
    _commit(
        ledger,
        [
            event(
                f"clock:{when.isoformat()}",
                "ClockAdvanced",
                {
                    "logical_time_from": ledger.project().logical_time.isoformat(),
                    "logical_time_to": when.isoformat(),
                },
                at=when,
            )
        ],
    )


def _compiler(ledger):
    return SocialInitiativeCompiler(
        ledger=ledger,
        actor_ref="actor:companion",
        policy=SocialInitiativePolicy(),
    )


def _process(opportunity, *, state="terminal", legacy=False):
    identity = (
        due_thread_consideration_id(opportunity.source_id)
        if legacy
        else opportunity.consideration_id
    )
    return SimpleNamespace(
        trigger_id=f"trigger:{identity}",
        trigger_ref="proactive-consideration:" + identity,
        process_kind="proactive_action_deliberation",
        state=state,
        source_evidence_ref=opportunity.source_event_ref,
        runtime_outcome_ref="proactive:silent",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_accepted_reschedule_gets_one_new_opportunity_after_prior_silence(legacy):
    ledger = initialized()
    initial_due = NOW + timedelta(minutes=5)
    first = _accept_thread(ledger, None, due=initial_due)
    _tick(ledger, initial_due)
    compiler = _compiler(ledger)
    original = await compiler.next_opportunity(ledger.project())
    assert original is not None
    consumed = _process(original, legacy=legacy)
    prior = ledger.project().model_copy(update={"trigger_processes": (consumed,)})
    assert await compiler.peek_next_due(prior) is None

    later_due = initial_due + timedelta(minutes=20)
    changed = _accept_thread(ledger, first, due=later_due)
    reprojected = ledger.project().model_copy(update={"trigger_processes": (consumed,)})
    assert await compiler.peek_next_due(reprojected) == later_due
    assert await compiler.next_opportunity(reprojected) is None
    _tick(ledger, later_due)
    reprojected = ledger.project().model_copy(update={"trigger_processes": (consumed,)})
    reconsidered = await _compiler(ledger).next_opportunity(reprojected)
    assert reconsidered is not None
    assert reconsidered.source_event_ref == changed.origin.accepted_event_ref
    assert reconsidered.consideration_id != original.consideration_id

    twice_consumed = reprojected.model_copy(
        update={
            "trigger_processes": (consumed, _process(reconsidered)),
        }
    )
    assert await _compiler(ledger).next_opportunity(twice_consumed) is None
    assert await _compiler(ledger).peek_next_due(twice_consumed) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_update_without_a_changed_due_window_does_not_repeat_the_consideration(legacy):
    ledger = initialized()
    due = NOW + timedelta(minutes=5)
    first = _accept_thread(ledger, None, due=due)
    _tick(ledger, due)
    compiler = _compiler(ledger)
    original = await compiler.next_opportunity(ledger.project())
    assert original is not None
    consumed = _process(original, legacy=legacy)
    _accept_thread(ledger, first, due=due, importance=7000)
    projection = ledger.project().model_copy(update={"trigger_processes": (consumed,)})

    assert await _compiler(ledger).next_opportunity(projection) is None
    assert await _compiler(ledger).peek_next_due(projection) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_restart_keeps_the_open_process_bound_to_its_original_source(legacy):
    ledger = initialized()
    due = NOW + timedelta(minutes=5)
    first = _accept_thread(ledger, None, due=due)
    _tick(ledger, due)
    original = await _compiler(ledger).next_opportunity(ledger.project())
    assert original is not None
    process = _process(original, state="open", legacy=legacy)
    _accept_thread(ledger, first, due=due + timedelta(minutes=20))
    projection = ledger.project().model_copy(update={"trigger_processes": (process,)})

    resumed = await _compiler(ledger).next_opportunity(projection)
    assert resumed is not None
    assert resumed.source_event_ref == original.source_event_ref
    assert resumed.source_id == original.source_id
    assert resumed.consideration_id == process.trigger_ref.removeprefix("proactive-consideration:")


@pytest.mark.asyncio
async def test_commitment_clock_revision_does_not_create_a_second_character_intention():
    from companion_daemon.world_v2.commitment_events import CommitmentClockTransitionPayload
    from companion_daemon.world_v2.commitment_reducers import (
        COMMITMENT_DEADLINE_POLICY_DIGEST,
        COMMITMENT_DEADLINE_POLICY_VERSION,
    )
    from companion_daemon.world_v2.schemas import EvidenceRef
    from test_commitment_authority import (
        DUE,
        accept,
        changed,
        commitment,
        event as commitment_event,
        initialized as commitment_ledger,
    )

    ledger = commitment_ledger()
    current = commitment()
    accept(
        ledger,
        changed(
            operation="open",
            before=None,
            after=current,
            proposal_id="proposal:open",
            world_revision=ledger.project().world_revision,
        ),
    )
    clock = commitment_event(
        "clock:due",
        "ClockAdvanced",
        {
            "logical_time_from": ledger.project().logical_time.isoformat(),
            "logical_time_to": DUE.isoformat(),
        },
        at=DUE,
    )
    _commit(ledger, [clock])
    first = await _compiler(ledger).next_opportunity(ledger.project())
    assert first is not None
    assert first.consideration_id == due_commitment_consideration_id(current.commitment_id)
    consumed = _process(first)

    clock_ref = EvidenceRef(
        ref_id=f"clock:{DUE.isoformat()}",
        evidence_type="clock_observation",
        claim_purpose="conversation_continuity",
    )
    after = current.model_copy(
        update={
            "entity_revision": 2,
            "updated_at": DUE,
            "values": current.values.model_copy(
                update={
                    "source_evidence_refs": (*current.values.source_evidence_refs, clock_ref),
                    "status": "due",
                }
            ),
            "origin": current.origin.model_copy(
                update={
                    "authority_mode": "mechanical_clock",
                    "change_id": "change:due",
                    "transition_id": "transition:due",
                    "accepted_event_ref": "event:due",
                }
            ),
        }
    )
    due = CommitmentClockTransitionPayload(
        change_id="change:due",
        transition_id="transition:due",
        operation="due",
        expected_entity_revision=1,
        commitment_before=current,
        commitment_after=after,
        clock_evidence_ref=clock_ref,
        clock_event_ref=clock.event_id,
        clock_event_payload_hash=clock.payload_hash,
        policy_version=COMMITMENT_DEADLINE_POLICY_VERSION,
        policy_digest=COMMITMENT_DEADLINE_POLICY_DIGEST,
    )
    _commit(
        ledger,
        [
            commitment_event(
                "event:due",
                "PrivateCommitmentDue",
                due.model_dump(mode="json"),
                at=DUE,
            )
        ],
    )
    projection = ledger.project().model_copy(update={"trigger_processes": (consumed,)})
    assert projection.commitments[0].entity_revision == 2
    assert await _compiler(ledger).next_opportunity(projection) is None
    assert await _compiler(ledger).peek_next_due(projection) is None
