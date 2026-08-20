"""Media send-window facts reach text lanes; text queue facts reach media selection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from companion_daemon.world_v2.later_expression_freshness import (
    later_refresh_opportunity_context,
)
from companion_daemon.world_v2.media_conversation_window import (
    media_cross_lane_timing_clause,
)
from companion_daemon.world_v2.media_selection_occasion import (
    compile_text_cross_lane_facts,
)
from companion_daemon.world_v2.response_expectation_view import (
    expired_hope_advisory_value,
    living_hope_hitch_clause,
)

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
CANDIDATE = "candidate:bookstore"
HASH = "a" * 64


def _projection(*, decided_at: datetime, delivered: bool = False) -> SimpleNamespace:
    deliveries = (SimpleNamespace(plan_id="plan:1"),) if delivered else ()
    return SimpleNamespace(
        logical_time=NOW,
        committed_world_event_refs=(
            SimpleNamespace(
                event_id="event:proposal:1",
                logical_time=decided_at,
                event_type="MediaSelectionProposalRecorded",
                world_revision=10,
                payload_hash=HASH,
            ),
        ),
        proposal_revisions=(
            SimpleNamespace(
                candidate_id=CANDIDATE,
                proposal_event_ref="event:proposal:1",
                decided_at=decided_at,
            ),
        ),
        media_opportunities=(
            SimpleNamespace(opportunity_id="opp:1", candidate_id=CANDIDATE),
        ),
        media_plans=(SimpleNamespace(plan_id="plan:1", opportunity_id="opp:1"),),
        media_deliveries=deliveries,
        actions=(),
        photo_candidates=(
            SimpleNamespace(
                candidate_id=CANDIDATE,
                status="generated",
                opened_at=decided_at,
                expires_at=decided_at + timedelta(hours=48),
                source_events=(SimpleNamespace(event_ref="event:settled:bookstore"),),
                source_event_refs=("event:settled:bookstore",),
            ),
        ),
        message_observations=(),
        execution_receipts=(),
        expression_plan_manifests=(),
        response_expectation_assessments=(),
    )


def test_lapsed_send_window_surfaces_on_expired_hope_advisory() -> None:
    projection = _projection(decided_at=NOW - timedelta(minutes=41))
    value = expired_hope_advisory_value(
        hoped_response="他会不会喜欢这张",
        seconds_since_he_last_spoke=900,
        spoken_since_declared=False,
        projection=projection,
    )
    assert "no longer auto-sending" in value
    assert "Timing evidence only" in value


def test_delivery_surfaces_on_living_hope_hitch() -> None:
    projection = _projection(decided_at=NOW - timedelta(minutes=5), delivered=True)
    clause = living_hope_hitch_clause(
        hoped_response="想听他说好不好看",
        seconds_since_he_last_spoke=120,
        projection=projection,
    )
    assert "already reached him" in clause


def test_later_refresh_context_includes_media_clause() -> None:
    projection = _projection(decided_at=NOW - timedelta(minutes=41))
    context = later_refresh_opportunity_context(projection)
    assert "delayed message" in context
    assert "no longer auto-sending" in context


def test_media_selection_sees_queued_later_text() -> None:
    written = NOW - timedelta(minutes=20)
    send_at = NOW + timedelta(minutes=10)
    projection = SimpleNamespace(
        logical_time=NOW,
        committed_world_event_refs=(
            SimpleNamespace(
                event_id="event:beat:1",
                world_revision=5,
                payload_hash=HASH,
            ),
        ),
        actions=(
            SimpleNamespace(
                action_id="action:later:1",
                kind="followup",
                state="authorized",
                logical_time=written,
                not_before=send_at,
                expression_plan_id="plan:later:1",
                expression_beat_id="beat:later:1",
                payload_ref="payload:later:1",
            ),
        ),
        expression_beats=(
            SimpleNamespace(
                beat_id="beat:later:1",
                event_ref="event:beat:1",
                payload_ref="payload:later:1",
            ),
        ),
        stored_message_payloads=(
            SimpleNamespace(payload_ref="payload:later:1", text="照片我晚点发你"),
        ),
        trigger_processes=(),
        message_observations=(),
        execution_receipts=(),
        expression_plan_manifests=(),
        response_expectation_assessments=(),
    )
    facts = compile_text_cross_lane_facts(projection, logical_time=NOW)
    assert any(item.get("kind") == "text_lane_queued_message" for item in facts)
    queued = next(item for item in facts if item.get("kind") == "text_lane_queued_message")
    assert "照片我晚点发你" in str(queued.get("text"))


def test_media_cross_lane_clause_mentions_expired_candidate() -> None:
    decided = NOW - timedelta(hours=50)
    projection = _projection(decided_at=decided)
    projection.photo_candidates = (
        SimpleNamespace(
            candidate_id=CANDIDATE,
            status="expired",
            opened_at=decided,
            expires_at=NOW - timedelta(minutes=1),
            source_events=(SimpleNamespace(event_ref="event:settled:bookstore"),),
            source_event_refs=("event:settled:bookstore",),
        ),
    )
    clause = media_cross_lane_timing_clause(projection, logical_time=NOW)
    assert "expired" in clause
