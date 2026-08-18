from __future__ import annotations

from datetime import datetime, timedelta
import json

import pytest

from companion_daemon.world_v2 import WorldRuntime
from companion_daemon.world_v2.ledger import WorldLedger
from companion_daemon.world_v2.appraisal_expiry_runtime import build_due_appraisal_expiry_events
from companion_daemon.world_v2.reducers import ReducerState, reduce_event
from companion_daemon.world_v2.schemas import (
    AppraisalHypothesis,
    AppraisalOrigin,
    AppraisalProjection,
    EvidenceRef,
)
from test_goal_expiry_runtime import (
    NOW,
    OPEN_TIME,
    WORLD_ID,
    StaticProjectionLedger,
    bootstrap_goal_clock,
    clock,
    clock_event,
)


def _appraisal(
    *,
    appraisal_id: str,
    accepted_at: datetime,
    expires_at: datetime,
    status: str = "active",
    closed_at: datetime | None = None,
) -> AppraisalProjection:
    values = dict(
        appraisal_id=appraisal_id,
        entity_revision=1,
        subject_ref="interaction:user:1",
        source_cluster_ref="conversation:1",
        origin=AppraisalOrigin(
            change_id=f"change:{appraisal_id}",
            transition_id=f"transition:{appraisal_id}",
            policy_refs=("policy:appraisal-v1",),
            matrix_catalog_version="appraisal-matrix.1",
            clustering_policy_version="source-clustering.1",
            accepted_event_ref=f"event:{appraisal_id}",
        ),
        hypotheses=(
            AppraisalHypothesis(
                hypothesis_id=f"meaning:{appraisal_id}",
                meaning="disappointment",
                attribution="user",
                controllability="partly_controllable",
                severity="moderate",
                weight_bp=10_000,
            ),
        ),
        evidence_refs=(
            EvidenceRef(
                ref_id="message:1",
                evidence_type="observed_message",
                claim_purpose="private_hypothesis",
            ),
        ),
        confidence_bp=7_200,
        accepted_at=accepted_at,
        expires_at=expires_at,
        status=status,
        closed_at=closed_at,
    )
    return AppraisalProjection.model_validate(values)


@pytest.mark.asyncio
async def test_due_appraisal_expires_and_a_live_one_does_not() -> None:
    seed = WorldLedger.in_memory(world_id=WORLD_ID)
    seed_runtime = WorldRuntime(world_id=WORLD_ID, ledger=seed)
    await bootstrap_goal_clock(seed_runtime)
    due = _appraisal(
        appraisal_id="appraisal:due",
        accepted_at=OPEN_TIME - timedelta(days=2),
        expires_at=OPEN_TIME,
    )
    live = _appraisal(
        appraisal_id="appraisal:live",
        accepted_at=OPEN_TIME,
        expires_at=NOW + timedelta(hours=48),
    )
    ledger = StaticProjectionLedger(
        seed.project().model_copy(update={"appraisals": (due, live)})
    )
    runtime = WorldRuntime(world_id=WORLD_ID, ledger=ledger)

    await runtime.advance(clock(tick_id="appraisal-expiry"))

    types = tuple(event.event_type for event in ledger.committed_events)
    assert types == ("ClockAdvanced", "AppraisalExpired")
    payload = json.loads(ledger.committed_events[1].payload_json)
    assert payload["appraisal_id"] == "appraisal:due"

    state = ReducerState(
        logical_time=seed.project().logical_time,
        committed_world_event_refs=seed.project().committed_world_event_refs,
        clock_transition_history=seed.project().clock_transition_history,
        appraisals=(due, live),
    )
    for event in ledger.committed_events:
        state = reduce_event(state, event)
    by_id = {item.appraisal_id: item for item in state.appraisals}
    assert by_id["appraisal:due"].status == "expired"
    assert by_id["appraisal:due"].closed_at == NOW
    assert by_id["appraisal:live"].status == "active"
    assert by_id["appraisal:live"].closed_at is None


def test_builder_skips_appraisals_that_have_not_reached_their_deadline() -> None:
    due = _appraisal(
        appraisal_id="appraisal:due",
        accepted_at=OPEN_TIME - timedelta(hours=3),
        expires_at=OPEN_TIME + timedelta(minutes=30),
    )
    live = _appraisal(
        appraisal_id="appraisal:later",
        accepted_at=OPEN_TIME,
        expires_at=NOW + timedelta(hours=1),
    )
    observation = clock(tick_id="appraisal-not-due")
    events = build_due_appraisal_expiry_events(
        world_id=WORLD_ID,
        appraisals=(due, live),
        clock=observation,
        clock_event=clock_event(observation),
    )
    assert [json.loads(event.payload_json)["appraisal_id"] for event in events] == [
        "appraisal:due"
    ]


def test_builder_does_not_expire_an_already_closed_appraisal() -> None:
    closed = _appraisal(
        appraisal_id="appraisal:already-expired",
        accepted_at=OPEN_TIME - timedelta(days=3),
        expires_at=OPEN_TIME - timedelta(days=1),
        status="expired",
        closed_at=OPEN_TIME - timedelta(days=1),
    )
    observation = clock(tick_id="appraisal-already-closed")
    assert (
        build_due_appraisal_expiry_events(
            world_id=WORLD_ID,
            appraisals=(closed,),
            clock=observation,
            clock_event=clock_event(observation),
        )
        == []
    )
