from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.declared_due import (
    collect_clock_wake_dues,
    collect_projection_declared_dues,
    select_clock_wake,
)


NOW = datetime(2026, 9, 8, 2, tzinfo=UTC)


@pytest.mark.parametrize("effective_due", [None, NOW + timedelta(hours=1)])
def test_owner_ecology_peek_supersedes_an_older_projected_cadence(effective_due):
    projection = SimpleNamespace(
        life_ecology_schedule=SimpleNamespace(
            next_consideration_at=NOW + timedelta(minutes=2)
        )
    )
    # The semantic schedule is still replayable; a quiet completion may have
    # subsequently moved the effective schedule in the existing Life sidecar.
    assert any(
        item.kind == "life.ecology"
        for item in collect_projection_declared_dues(projection)
    )
    dues = collect_clock_wake_dues(projection, computed={
        "life.ecology": effective_due,
        "social.initiative.cadence": None,
        "private_impression.interval": None,
    })
    assert [item.due_at for item in dues if item.kind == "life.ecology"] == (
        [] if effective_due is None else [effective_due]
    )
    assert select_clock_wake(
        after=NOW, through=NOW + timedelta(minutes=5), dues=dues,
    ) is None


def test_ecology_peek_does_not_suppress_another_owners_exact_due():
    action_due = NOW + timedelta(minutes=3)
    projection = SimpleNamespace(
        life_ecology_schedule=SimpleNamespace(next_consideration_at=NOW),
        actions=(SimpleNamespace(state="scheduled", not_before=action_due),),
    )
    dues = collect_clock_wake_dues(projection, computed={
        "life.ecology": NOW + timedelta(hours=1),
        "social.initiative.cadence": None,
        "private_impression.interval": None,
    })
    selected = select_clock_wake(
        after=NOW, through=NOW + timedelta(minutes=5), dues=dues,
    )
    assert selected is not None
    assert (selected.kind, selected.due_at) == ("action.authorized_due", action_due)
