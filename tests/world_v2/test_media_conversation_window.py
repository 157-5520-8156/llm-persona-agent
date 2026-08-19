"""Conversational send-window: expire the decision, keep the candidate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from companion_daemon.world_v2.media_conversation_window import (
    CONVERSATION_SELECTION_TTL,
    conversation_send_allowed,
    is_reask_eligible,
    latest_selection_decided_at,
)
from companion_daemon.world_v2.photographable_inventory import available_photo_source_refs


NOW = datetime(2026, 8, 18, 15, 48, tzinfo=UTC)
CANDIDATE = "candidate:bookstore"


def _projection(*, decided_at: datetime, status: str = "generated") -> SimpleNamespace:
    return SimpleNamespace(
        logical_time=NOW,
        committed_world_event_refs=(
            SimpleNamespace(
                event_id="event:proposal:1",
                logical_time=decided_at,
            ),
        ),
        proposal_revisions=(
            SimpleNamespace(
                candidate_id=CANDIDATE,
                proposal_event_ref="event:proposal:1",
            ),
        ),
        media_opportunities=(
            SimpleNamespace(opportunity_id="opp:1", candidate_id=CANDIDATE),
        ),
        media_plans=(SimpleNamespace(plan_id="plan:1", opportunity_id="opp:1"),),
        media_deliveries=(),
        photo_candidates=(
            SimpleNamespace(
                candidate_id=CANDIDATE,
                status=status,
                opened_at=decided_at,
                expires_at=decided_at + timedelta(hours=48),
                source_events=(SimpleNamespace(event_ref="event:settled:bookstore"),),
                source_event_refs=("event:settled:bookstore",),
            ),
        ),
    )


def test_thirty_minutes_is_the_documented_send_window() -> None:
    assert CONVERSATION_SELECTION_TTL == timedelta(minutes=30)


def test_same_sitting_selection_may_still_auto_send() -> None:
    projection = _projection(decided_at=NOW - timedelta(minutes=7))
    assert conversation_send_allowed(
        projection, logical_time=NOW, candidate_id=CANDIDATE
    )
    assert not is_reask_eligible(
        projection,
        candidate=projection.photo_candidates[0],
        logical_time=NOW,
    )


def test_forty_minutes_closes_the_send_and_reopens_the_ask() -> None:
    projection = _projection(decided_at=NOW - timedelta(minutes=40))
    assert not conversation_send_allowed(
        projection, logical_time=NOW, candidate_id=CANDIDATE
    )
    assert is_reask_eligible(
        projection,
        candidate=projection.photo_candidates[0],
        logical_time=NOW,
    )


def test_delivered_candidate_is_not_offered_again() -> None:
    projection = _projection(decided_at=NOW - timedelta(minutes=40))
    projection.media_deliveries = (SimpleNamespace(plan_id="plan:1"),)
    assert not is_reask_eligible(
        projection,
        candidate=projection.photo_candidates[0],
        logical_time=NOW,
    )


def test_lapsed_generated_candidate_counts_as_photo_in_hand() -> None:
    projection = _projection(decided_at=NOW - timedelta(minutes=40))
    assert available_photo_source_refs(projection, logical_time=NOW) == {
        "event:settled:bookstore"
    }


def test_reask_uses_plan_freeze_when_proposal_coords_are_missing() -> None:
    decided = NOW - timedelta(minutes=40)
    projection = SimpleNamespace(
        logical_time=NOW,
        committed_world_event_refs=(),
        proposal_revisions=(),
        media_opportunities=(
            SimpleNamespace(opportunity_id="opp:1", candidate_id=CANDIDATE),
        ),
        media_plans=(
            SimpleNamespace(
                plan_id="plan:1",
                opportunity_id="opp:1",
                frozen_at=decided,
            ),
        ),
        media_deliveries=(),
        photo_candidates=(
            SimpleNamespace(
                candidate_id=CANDIDATE,
                status="generated",
                opened_at=decided,
                expires_at=decided + timedelta(hours=48),
                source_events=(SimpleNamespace(event_ref="event:settled:bookstore"),),
                source_event_refs=("event:settled:bookstore",),
            ),
        ),
    )
    assert is_reask_eligible(
        projection,
        candidate=projection.photo_candidates[0],
        logical_time=NOW,
    )


def _production_shaped_projection(
    *, decided_at: datetime, moment_at: datetime
) -> SimpleNamespace:
    """The shape production actually writes.

    ``MediaSelectionProposalRecorded`` is DELIBERATION-class, so its event is
    absent from ``committed_world_event_refs``, and ``plan.frozen_at`` is the
    logical time of the photographed moment rather than of her decision.
    """

    projection = _projection(decided_at=decided_at)
    projection.committed_world_event_refs = ()
    projection.proposal_revisions = (
        SimpleNamespace(
            candidate_id=CANDIDATE,
            proposal_event_ref="event:media-selection:proposal:1",
            decided_at=decided_at,
        ),
    )
    projection.media_plans = (
        SimpleNamespace(plan_id="plan:1", opportunity_id="opp:1", frozen_at=moment_at),
    )
    return projection


def test_she_may_send_a_photo_of_a_moment_that_is_hours_old() -> None:
    projection = _production_shaped_projection(
        decided_at=NOW - timedelta(minutes=2),
        moment_at=NOW - timedelta(hours=11),
    )
    assert latest_selection_decided_at(
        projection, candidate_id=CANDIDATE
    ) == NOW - timedelta(minutes=2)
    assert conversation_send_allowed(
        projection, logical_time=NOW, candidate_id=CANDIDATE
    )
    assert not is_reask_eligible(
        projection,
        candidate=projection.photo_candidates[0],
        logical_time=NOW,
    )


def test_an_old_decision_still_closes_the_send_on_a_fresh_moment() -> None:
    projection = _production_shaped_projection(
        decided_at=NOW - timedelta(minutes=40),
        moment_at=NOW - timedelta(minutes=1),
    )
    assert not conversation_send_allowed(
        projection, logical_time=NOW, candidate_id=CANDIDATE
    )
    assert is_reask_eligible(
        projection,
        candidate=projection.photo_candidates[0],
        logical_time=NOW,
    )


def test_latest_selection_wins_after_a_second_decision() -> None:
    first = NOW - timedelta(minutes=50)
    second = NOW - timedelta(minutes=2)
    projection = _projection(decided_at=first)
    projection.committed_world_event_refs = (
        SimpleNamespace(event_id="event:proposal:1", logical_time=first),
        SimpleNamespace(event_id="event:proposal:2", logical_time=second),
    )
    projection.proposal_revisions = (
        SimpleNamespace(candidate_id=CANDIDATE, proposal_event_ref="event:proposal:1"),
        SimpleNamespace(candidate_id=CANDIDATE, proposal_event_ref="event:proposal:2"),
    )
    assert latest_selection_decided_at(projection, candidate_id=CANDIDATE) == second
    assert conversation_send_allowed(
        projection, logical_time=NOW, candidate_id=CANDIDATE
    )
