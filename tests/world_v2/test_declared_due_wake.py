from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.declared_due import (
    COMPUTED_CLOCK_WAKE_KINDS,
    NON_WAKING_PROJECTION_DUE_FIELDS,
    assert_declared_due_wake_coverage,
    assert_host_uses_declared_due_only,
    collect_clock_wake_dues,
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
    assert NON_WAKING_PROJECTION_DUE_FIELDS <= INSTALLED_PROJECTION_DUE_FIELDS


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
    original = declared_due_module.collected_projection_due_fields
    monkeypatch.setattr(
        declared_due_module,
        "collected_projection_due_fields",
        lambda: original() - {"Action.not_before"},
    )
    with pytest.raises(AssertionError, match="Action.not_before"):
        assert_declared_due_wake_coverage()


def test_gatekeeper_goes_red_when_host_keeps_a_handwritten_due_list() -> None:
    broken = '''
class QQC2CHost:
    async def _scheduler_once_serialized(self):
        computed_dues = [
            computed_due("action.authorized_due", None),
            computed_due("expression.technical_retry", None),
            computed_due("life.ecology", None),
        ]
        select_clock_wake(after=None, through=None, dues=computed_dues)
'''
    with pytest.raises(AssertionError, match="collect_clock_wake_dues"):
        assert_host_uses_declared_due_only(host_source=broken)


def test_host_scheduler_computed_peeks_match_registered_kinds() -> None:
    assert_host_uses_declared_due_only()


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


def test_active_occurrence_wakes_at_its_complete_outcome_boundary() -> None:
    opens = datetime(2026, 9, 7, 1, 12, tzinfo=UTC)
    now = datetime(2026, 9, 7, 1, 27, tzinfo=UTC)
    closes = datetime(2026, 9, 7, 4, 0, tzinfo=UTC)
    dues = collect_projection_declared_dues(SimpleNamespace(
        world_occurrences=(SimpleNamespace(
            status="active",
            time_window=SimpleNamespace(opens_at=opens, closes_at=closes),
        ),),
    ))

    selected = select_clock_wake(after=now, through=closes, dues=dues)

    assert selected is not None
    assert selected.kind == "life.activity_occurrence"
    assert selected.due_at == closes
    assert [item.due_at for item in dues] == [closes]


@pytest.mark.parametrize("status", ["active", "paused"])
def test_started_plan_retires_opening_and_wakes_at_its_remaining_window_boundary(status) -> None:
    opens = NOW - timedelta(minutes=3)
    closes = NOW + timedelta(minutes=27)
    dues = collect_projection_declared_dues(SimpleNamespace(
        plans=(SimpleNamespace(
            status=status,
            scheduled_window=SimpleNamespace(opens_at=opens, closes_at=closes),
        ),),
    ))

    # A short journey ending while the activity is in progress has no
    # unprocessed opening; its accepted end still wakes production later.
    assert not any(item.due_at <= NOW for item in dues)
    assert select_clock_wake(after=NOW, through=closes - timedelta(seconds=1), dues=dues) is None
    selected = select_clock_wake(after=NOW, through=closes, dues=dues)
    assert selected is not None
    assert selected.kind == "life.activity_occurrence"
    assert selected.due_at == closes


@pytest.mark.parametrize("status", ["planned", "completed", "abandoned"])
def test_plan_opening_remains_only_for_not_yet_started_plans(status) -> None:
    opens = NOW + timedelta(minutes=3)
    closes = NOW + timedelta(minutes=30)
    dues = collect_projection_declared_dues(SimpleNamespace(
        plans=(SimpleNamespace(
            status=status,
            scheduled_window=SimpleNamespace(opens_at=opens, closes_at=closes),
        ),),
    ))
    selected = select_clock_wake(after=NOW, through=closes, dues=dues)
    if status == "planned":
        assert selected is not None and selected.due_at == opens
    else:
        assert selected is None and dues == ()


def test_collect_clock_wake_dues_requires_exact_computed_keys() -> None:
    with pytest.raises(AssertionError, match="missing computed kinds"):
        collect_clock_wake_dues(None, computed={"social.initiative.cadence": None})


def test_post_silent_delay_enters_wake_set_via_computed_peek() -> None:
    """Today's accident shape: delay:25200 must wake before Life."""

    post_silent = NOW + timedelta(seconds=25200)
    life = NOW + timedelta(hours=8)
    dues = collect_clock_wake_dues(
        SimpleNamespace(
            actions=(),
            life_ecology_schedule=SimpleNamespace(next_consideration_at=life),
        ),
        computed={
            "social.initiative.cadence": post_silent,
            "private_impression.interval": None,
            "memory.candidate_consolidation": None,
            "life.ecology": life,
        },
    )
    selected = select_clock_wake(
        after=NOW,
        through=NOW + timedelta(hours=9),
        dues=dues,
    )
    assert selected is not None
    assert selected.kind == "social.initiative.cadence"
    assert selected.due_at == post_silent
    assert selected.reason == "qq_c2c_social_initiative_due_wake"


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


def test_silence_formula_is_explicitly_non_waking() -> None:
    dues = collect_projection_declared_dues(
        SimpleNamespace(
            silence_opportunity=SimpleNamespace(
                anchored_at=NOW,
                idle_seconds=3600,
            ),
            actions=(),
        )
    )
    assert all(item.kind != "relationship.silence_aftermath" for item in dues)
    silence = next(
        owner
        for owner in DELAYED_TRIGGER_OWNERS
        if owner.mechanism_id == "relationship.silence_aftermath"
    )
    assert silence.trigger_mode == "derived_formula"
    assert set(silence.projection_due_fields) == set(NON_WAKING_PROJECTION_DUE_FIELDS)


@pytest.mark.parametrize("answered", [False, True])
def test_expectation_wake_uses_receipt_owner_eligibility(answered):
    due = NOW - timedelta(minutes=1)
    p = SimpleNamespace(
        logical_time=NOW,
        expression_plan_manifests=(SimpleNamespace(
            plan_id="plan:hope",
            response_expectation=SimpleNamespace(
                source_beat_id="beat:hope", hoped_response="project news",
                not_before=due, expires_at=NOW + timedelta(hours=1),
            ),
            beats=(SimpleNamespace(beat_id="beat:hope", action=SimpleNamespace(action_id="action:hope")),),
        ),),
        execution_receipts=(SimpleNamespace(action_id="action:hope", observed_state="delivered"),),
        committed_world_event_refs=(SimpleNamespace(
            event_type="ExecutionReceiptRecorded", event_id="event:receipt",
            world_revision=1, logical_time=NOW - timedelta(hours=1),
        ),),
        message_observations=(SimpleNamespace(world_revision=2),) if answered else (),
        response_expectation_assessments=(),
    )
    from companion_daemon.world_v2.response_expectation_view import unanswered_response_expectations
    assert bool(unanswered_response_expectations(p)) is not answered
    openings = [d for d in collect_projection_declared_dues(p)
                if d.field == "ResponseExpectationAuthority.not_before"]
    assert bool(openings) is not answered


def test_due_commitment_keeps_unprocessed_close_instead_of_consumed_open():
    opens, closes = NOW - timedelta(hours=2), NOW - timedelta(hours=1)
    p = SimpleNamespace(logical_time=NOW, commitments=(SimpleNamespace(
        values=SimpleNamespace(status="due", due_window=SimpleNamespace(opens_at=opens, closes_at=closes)),
    ),))
    dues = collect_projection_declared_dues(p)
    assert len(dues) == 1 and dues[0].due_at == closes


def test_passed_thread_opening_is_context_but_pending_action_remains_due():
    past = NOW - timedelta(minutes=1)
    p = SimpleNamespace(logical_time=NOW,
        threads=(SimpleNamespace(values=SimpleNamespace(status="open", due_window=SimpleNamespace(opens_at=past))),),
        actions=(SimpleNamespace(state="authorized", not_before=past),))
    dues = collect_projection_declared_dues(p)
    assert [d.kind for d in dues] == ["action.authorized_due"]


def test_affect_anchor_is_history_until_its_actual_residue_close_is_pending():
    from test_affect_module import episode
    from companion_daemon.world_v2.affect_live import episode_at_residue_floor
    active = episode(at=NOW - timedelta(minutes=2))
    p = SimpleNamespace(logical_time=NOW, affect_episodes=(active,), affect_baselines=())
    assert not episode_at_residue_floor(active, logical_time=NOW)
    assert collect_projection_declared_dues(p) == ()
    # Reaching the anchor is not enough to waive an overdue residue close.
    p.logical_time = NOW + timedelta(days=30)
    assert episode_at_residue_floor(active, logical_time=p.logical_time)
    assert any(d.kind == "affect.decay" for d in collect_projection_declared_dues(p))
