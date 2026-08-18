from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from companion_daemon.world_v2.present_moment_candidate import (
    inspect_present_moment,
    present_moment_material,
)

NOW = datetime(2026, 8, 19, 1, 0, tzinfo=UTC)


def _plan(*, kind: str = "commute.short_walk", status: str = "active", source: str = "event:started:walk"):
    return SimpleNamespace(
        plan_id="plan:walk",
        activity_kind=kind,
        status=status,
        authority_origin=SimpleNamespace(accepted_event_ref=source),
    )


def _opening(*, domain: str = "commute_walk", annex: bool = True):
    return SimpleNamespace(
        domain=domain,
        visual_evidence=SimpleNamespace(
            activity_description="沿校园林荫道散步",
            self_capture=("character_front_camera",),
        )
        if annex
        else None,
    )


def test_no_active_plan_is_not_photographable() -> None:
    fact = inspect_present_moment(
        projection=SimpleNamespace(plans=(), photo_candidates=(), logical_time=NOW),
        logical_time=NOW,
    )
    assert fact.photographable is False
    assert fact.reason == "no_active_activity"


def test_sleep_active_plan_is_fail_closed() -> None:
    fact = inspect_present_moment(
        projection=SimpleNamespace(
            plans=(_plan(kind="sleep.prepare_for_bed"),),
            photo_candidates=(),
            logical_time=NOW,
        ),
        catalog=SimpleNamespace(opening_for_activity=lambda kind: _opening(domain="sleep_wake")),
        logical_time=NOW,
    )
    assert fact.photographable is False
    assert fact.reason == "sleep"


def test_missing_annex_is_fail_closed() -> None:
    fact = inspect_present_moment(
        projection=SimpleNamespace(
            plans=(_plan(),), photo_candidates=(), logical_time=NOW
        ),
        catalog=SimpleNamespace(opening_for_activity=lambda kind: _opening(annex=False)),
        logical_time=NOW,
    )
    assert fact.photographable is False
    assert fact.reason == "annex_insufficient"


def test_active_annex_backed_plan_is_photographable() -> None:
    fact = inspect_present_moment(
        projection=SimpleNamespace(
            plans=(_plan(),), photo_candidates=(), logical_time=NOW
        ),
        catalog=SimpleNamespace(opening_for_activity=lambda kind: _opening()),
        logical_time=NOW,
    )
    assert fact.photographable is True
    assert fact.reason == "active"
    assert fact.source_ref == "event:started:walk"
    packet = present_moment_material(fact)
    assert packet["photographable"] is True
    assert "编造" not in str(packet)


def test_already_open_candidate_stays_photographable_but_not_redeclared() -> None:
    fact = inspect_present_moment(
        projection=SimpleNamespace(
            plans=(_plan(),),
            photo_candidates=(
                SimpleNamespace(
                    status="available",
                    expires_at=NOW + timedelta(hours=2),
                    source_event_refs=("event:started:walk",),
                    source_events=(),
                ),
            ),
            logical_time=NOW,
        ),
        catalog=SimpleNamespace(opening_for_activity=lambda kind: _opening()),
        logical_time=NOW,
    )
    assert fact.photographable is True
    assert fact.reason == "already_open"
