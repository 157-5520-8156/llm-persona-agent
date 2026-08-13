from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from companion_daemon.world_v2.occasion import (
    OccasionAlreadyConsidered,
    OccasionConsiderGate,
    newly_accepted_head_refs,
    occasion_is_expired,
    quiet_gap_expires_at,
)
from companion_daemon.world_v2.private_impression_producer import (
    private_impression_opportunity,
)
from companion_daemon.world_v2.social_initiative import SocialInitiativePolicy


NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


def test_second_consider_for_the_same_occasion_is_rejected() -> None:
    gate = OccasionConsiderGate()
    gate.admit("occasion:user_message:event:observation:1")
    gate.mark_spent("occasion:user_message:event:observation:1")
    try:
        gate.admit("occasion:user_message:event:observation:1")
    except OccasionAlreadyConsidered:
        return
    raise AssertionError("same Occasion identity was allowed to consider twice")


def test_quiet_gap_past_expiry_is_dropped_not_backfilled() -> None:
    created = NOW - timedelta(hours=14)
    expires_at = quiet_gap_expires_at(
        created_at=created,
        expiry_seconds=SocialInitiativePolicy().spontaneous_expiry_seconds,
    )
    assert occasion_is_expired(now=NOW, expires_at=expires_at)
    assert not occasion_is_expired(now=created + timedelta(hours=1), expires_at=expires_at)


def test_private_impression_does_not_scan_historical_appraisals() -> None:
    old = SimpleNamespace(
        status="active",
        appraisal_id="appraisal:old",
        origin=SimpleNamespace(accepted_event_ref="event:appraisal:old"),
        confidence_bp=9000,
    )
    head_clock = SimpleNamespace(
        event_id="event:clock:now",
        event_type="ClockAdvanced",
        world_revision=9,
    )
    projection = SimpleNamespace(
        logical_time=NOW,
        world_id="world:test",
        appraisals=(old,),
        private_impressions=(),
        trigger_processes=(),
        committed_world_event_refs=(
            SimpleNamespace(
                event_id="event:appraisal:old",
                event_type="AppraisalAccepted",
                world_revision=1,
            ),
            head_clock,
        ),
    )
    assert private_impression_opportunity(projection) is None
    assert (
        newly_accepted_head_refs(
            projection.committed_world_event_refs,
            event_type="AppraisalAccepted",
        )
        == frozenset()
    )


def test_private_impression_opens_only_the_head_appraisal() -> None:
    head_ref = "event:appraisal:new"
    projection = SimpleNamespace(
        logical_time=NOW,
        world_id="world:test",
        appraisals=(
            SimpleNamespace(
                status="active",
                appraisal_id="appraisal:old",
                origin=SimpleNamespace(accepted_event_ref="event:appraisal:old"),
            ),
            SimpleNamespace(
                status="active",
                appraisal_id="appraisal:new",
                origin=SimpleNamespace(accepted_event_ref=head_ref),
            ),
        ),
        private_impressions=(),
        trigger_processes=(),
        committed_world_event_refs=(
            SimpleNamespace(
                event_id="event:appraisal:old",
                event_type="AppraisalAccepted",
                world_revision=1,
            ),
            SimpleNamespace(
                event_id=head_ref,
                event_type="AppraisalAccepted",
                world_revision=2,
            ),
        ),
    )
    opened = private_impression_opportunity(projection)
    assert opened is not None
    assert opened[1] == head_ref
