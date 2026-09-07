from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.occasion import (
    OccasionAlreadyConsidered,
    OccasionConsiderGate,
    newly_accepted_head_refs,
    occasion_is_expired,
    quiet_gap_expires_at,
)
from companion_daemon.world_v2.private_impression_producer import (
    PrivateImpressionTriggerOpener,
    private_impression_opportunity,
)
from companion_daemon.world_v2.social_initiative import SocialInitiativePolicy
from test_private_impression_producer import (
    OWNER,
    _Model,
    _advance_clock,
    _append_second_appraisal,
    _ledger_with_active_appraisal,
    _private_runtime,
    _retain,
)


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
    """A clock head is not an AppraisalAccepted, so G7 is empty.

    The farm may still open the newest uninterpreted appraisal (production
    inbound batches bury AppraisalAccepted).  It still returns one identity,
    not a historical sweep of every old appraisal.
    """

    older = SimpleNamespace(
        status="active",
        appraisal_id="appraisal:old",
        origin=SimpleNamespace(accepted_event_ref="event:appraisal:old"),
        confidence_bp=9000,
    )
    newer = SimpleNamespace(
        status="active",
        appraisal_id="appraisal:newer",
        origin=SimpleNamespace(accepted_event_ref="event:appraisal:newer"),
        confidence_bp=8000,
    )
    head_clock = SimpleNamespace(
        event_id="event:clock:now",
        event_type="ClockAdvanced",
        world_revision=9,
    )
    projection = SimpleNamespace(
        logical_time=NOW,
        world_id="world:test",
        appraisals=(older, newer),
        private_impressions=(),
        trigger_processes=(),
        committed_world_event_refs=(
            SimpleNamespace(
                event_id="event:appraisal:old",
                event_type="AppraisalAccepted",
                world_revision=1,
            ),
            SimpleNamespace(
                event_id="event:appraisal:newer",
                event_type="AppraisalAccepted",
                world_revision=2,
            ),
            head_clock,
        ),
    )
    opened = private_impression_opportunity(projection)
    assert opened is not None
    assert opened[1] == "event:appraisal:newer"
    assert opened[1] != "event:appraisal:old"
    assert (
        newly_accepted_head_refs(
            projection.committed_world_event_refs,
            event_type="AppraisalAccepted",
        )
        == frozenset()
    )
    # Newest uninterpreted identity only — both old appraisals stay eligible
    # for later ticks, but this open_once does not return a historical list.
    assert private_impression_opportunity(
        SimpleNamespace(
            **{**projection.__dict__, "trigger_processes": (
                SimpleNamespace(
                    trigger_id=opened[0],
                    process_kind="private_impression_deliberation",
                    state="open",
                ),
            )},
        )
    ) is None


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


@pytest.mark.asyncio
@pytest.mark.parametrize("bury_appraisal_head", [False, True])
async def test_opener_keeps_latest_identity_and_does_not_reopen_retained_sources(
    bury_appraisal_head: bool,
) -> None:
    ledger = _ledger_with_active_appraisal()
    _append_second_appraisal(ledger)
    if bury_appraisal_head:
        _advance_clock(ledger, timedelta(minutes=5), event_id="occasion:clock-after-appraisals")
    opener = PrivateImpressionTriggerOpener(ledger=ledger, owner_id=OWNER)

    opened = await opener.open_once()

    assert opened is not None
    process = next(item for item in ledger.project().trigger_processes if item.trigger_id == opened)
    assert process.source_evidence_ref == "interaction-appraisal-accepted:2"
    assert await opener.open_once() is None

    # The character may retain the older reading alongside the new one.
    # Both source identities contain colons and must remain consumed exactly.
    runtime, _interior = _private_runtime(ledger, _Model([_retain([
        "appraisal:appraisal:interaction:1:meaning:disappointment",
        "appraisal:appraisal:interaction:2:meaning:disappointment",
    ])]))
    assert (await runtime.drain_one()).work_status == "accepted"
    after = ledger.project()
    assert len(after.private_impressions) == 1
    assert set(after.private_impressions[0].interpretation_refs) == {
        "appraisal:appraisal:interaction:1:meaning:disappointment",
        "appraisal:appraisal:interaction:2:meaning:disappointment",
    }
    assert await opener.open_once() is None
    assert ledger.project() == after
