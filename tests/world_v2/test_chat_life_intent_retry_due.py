from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.declared_due import (
    assert_declared_due_wake_coverage,
    collect_projection_declared_dues,
    select_clock_wake,
)
from companion_daemon.world_v2.delayed_trigger_owner_registry import DELAYED_TRIGGER_OWNERS


NOW = datetime(2026, 9, 8, 2, tzinfo=UTC)
KIND = "life.chat_intent_acceptance"


def projection(*, terminal=False, next_retry_at=NOW + timedelta(seconds=30), plans=()):
    return SimpleNamespace(
        chat_life_intent_failures=(
            SimpleNamespace(
                proposal_id="proposal:chat",
                change_id="change:life",
                plan_id="plan:chat-life",
                failure_event_ref="event:failure",
                retry_ordinal=1,
                failed_at=NOW,
                next_retry_at=next_retry_at,
                terminal=terminal,
            ),
        ),
        plans=plans,
    )


def test_chat_life_retry_wakes_exactly_before_ordinary_heartbeat():
    due = collect_projection_declared_dues(projection())
    selected = select_clock_wake(after=NOW, through=NOW + timedelta(minutes=1), dues=due)
    assert selected is not None
    assert selected.kind == KIND
    assert selected.due_at == NOW + timedelta(seconds=30)
    assert selected.field == "ChatLifeIntentFailure.next_retry_at"
    assert selected.wake_policy == "exact_future"
    assert_declared_due_wake_coverage()
    owner = next(item for item in DELAYED_TRIGGER_OWNERS if item.mechanism_id == KIND)
    assert owner.model_contract is None
    assert owner.retry_policy[1] == (30, 120)


@pytest.mark.parametrize(
    "updates",
    [
        {"terminal": True},
        {"next_retry_at": None},
        {"plans": (SimpleNamespace(plan_id="plan:chat-life", status="planned"),)},
    ],
)
def test_terminal_missing_due_and_accepted_plan_do_not_wake_again(updates):
    assert not [
        item
        for item in collect_projection_declared_dues(projection(**updates))
        if item.kind == KIND
    ]


def test_retry_due_retains_later_attempt_time_and_is_not_blocked_by_another_plan():
    at = NOW + timedelta(seconds=150)
    state = projection(
        next_retry_at=at, plans=(SimpleNamespace(plan_id="plan:other", status="planned"),)
    )
    selected = select_clock_wake(
        after=NOW + timedelta(seconds=30), through=at, dues=collect_projection_declared_dues(state)
    )
    assert selected is not None and selected.kind == KIND and selected.due_at == at
