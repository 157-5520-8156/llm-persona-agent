from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.declared_due import (
    COMPUTED_CLOCK_WAKE_KINDS,
    assert_declared_due_wake_coverage,
    collect_projection_declared_dues,
    collected_clock_wake_kinds,
    collected_projection_due_fields,
    computed_due,
    required_clock_wake_kinds,
    select_clock_wake,
)
from companion_daemon.world_v2.delayed_trigger_owner_registry import (
    DELAYED_TRIGGER_OWNERS,
    INSTALLED_PROJECTION_DUE_FIELDS,
)
import companion_daemon.world_v2.declared_due as declared_due_module


NOW = datetime(2026, 8, 19, 6, 0, 45, tzinfo=UTC)


def test_every_clock_due_kind_and_field_has_a_wake_collector() -> None:
    assert_declared_due_wake_coverage()
    assert COMPUTED_CLOCK_WAKE_KINDS <= collected_clock_wake_kinds()
    assert required_clock_wake_kinds() <= collected_clock_wake_kinds()
    assert INSTALLED_PROJECTION_DUE_FIELDS <= collected_projection_due_fields()


def test_gatekeeper_goes_red_when_a_new_due_kind_is_not_collected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = declared_due_module.required_clock_wake_kinds
    monkeypatch.setattr(
        declared_due_module,
        "required_clock_wake_kinds",
        lambda: original() | {"brand.new.due.kind"},
    )
    with pytest.raises(AssertionError, match="brand.new.due.kind"):
        assert_declared_due_wake_coverage()


def test_gatekeeper_goes_red_when_an_installed_field_has_no_extractor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        declared_due_module,
        "_FIELD_EXTRACTORS",
        {
            name: kind
            for name, kind in declared_due_module._FIELD_EXTRACTORS.items()
            if name != "Action.not_before"
        },
    )
    with pytest.raises(AssertionError, match="Action.not_before"):
        assert_declared_due_wake_coverage()


def test_clock_due_owners_are_not_a_handwritten_scheduler_list() -> None:
    clock_due_owners = {
        owner.mechanism_id
        for owner in DELAYED_TRIGGER_OWNERS
        if owner.trigger_mode == "clock_due" and owner.projection_due_fields
    }
    assert clock_due_owners <= collected_clock_wake_kinds()
    assert "social.initiative.cadence" in collected_clock_wake_kinds()
    assert "private_impression.interval" in collected_clock_wake_kinds()


def test_collector_wakes_an_appraisal_expiry_that_was_never_on_the_old_list() -> None:
    due = NOW + timedelta(hours=1)
    dues = collect_projection_declared_dues(
        SimpleNamespace(
            appraisals=(SimpleNamespace(status="active", expires_at=due),),
            actions=(),
        )
    )
    selected = select_clock_wake(
        after=NOW,
        through=NOW + timedelta(hours=2),
        dues=dues,
    )
    assert selected is not None
    assert selected.kind == "appraisal.expiry"
    assert selected.due_at == due


def test_select_clock_wake_does_not_wall_jump_an_already_due_initiative() -> None:
    past = NOW - timedelta(hours=1)
    future = NOW + timedelta(hours=1)
    selected = select_clock_wake(
        after=NOW,
        through=NOW + timedelta(hours=3),
        dues=(
            computed_due("social.initiative.cadence", past),
            computed_due("life.ecology", future, wake_policy="wall_catchup"),
        ),
    )
    assert selected is not None
    assert selected.kind == "life.ecology"
    assert selected.due_at == future


def test_life_ecology_may_catch_up_to_the_tick_boundary() -> None:
    past = NOW - timedelta(minutes=5)
    boundary = NOW + timedelta(minutes=1)
    selected = select_clock_wake(
        after=NOW,
        through=boundary,
        dues=(computed_due("life.ecology", past, wake_policy="wall_catchup"),),
    )
    assert selected is not None
    assert selected.due_at == boundary
    assert selected.reason == "qq_c2c_life_ecology_due_wake"
