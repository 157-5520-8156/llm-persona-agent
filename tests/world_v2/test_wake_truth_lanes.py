"""Waiting_for-only hopes hitch existing lanes; wakeup copy stays factual."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from companion_daemon.world_v2.proactive_action import (
    _proactive_advisory_value,
    _proactive_opportunity_context,
)
from companion_daemon.world_v2.response_expectation_view import (
    OPEN_HOPE_WAKE_SECONDS,
    LivingUnansweredHope,
    expired_hope_advisory_value,
    expired_unanswered_expectation,
    living_unanswered_hope,
)
from test_expectation_feelings import NOW as EXPECTATION_NOW


WATER = "他倒完水回来"
# 20:00 Asia/Shanghai — outside the overnight mute used for cadence wakes.
EVENING = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
OVERNIGHT = datetime(2026, 8, 18, 18, 30, tzinfo=UTC)


def _hope_manifest(declared: datetime, *, plan_id: str = "plan:invite"):
    return SimpleNamespace(
        plan_id=plan_id,
        beats=(
            SimpleNamespace(
                beat_id="beat:invite",
                action=SimpleNamespace(action_id="action:invite"),
            ),
        ),
        response_expectation=SimpleNamespace(
            source_beat_id="beat:invite",
            hoped_response=WATER,
            not_before=declared + timedelta(seconds=86_400),
            expires_at=declared + timedelta(seconds=172_800),
        ),
    )


def _open_hope_projection(
    *,
    logical_offset_seconds: int,
    heat: str | None = None,
    declared: datetime | None = None,
    extra_observations: tuple[object, ...] = (),
    extra_refs: tuple[object, ...] = (),
    actions: tuple[object, ...] = (),
):
    declared = declared or EVENING
    hope_ref = SimpleNamespace(
        event_id="event:receipt:invite",
        event_type="ExecutionReceiptRecorded",
        world_revision=4 if heat == "hot" else 3 if heat == "warm" else 2,
        logical_time=declared,
    )
    refs = [hope_ref]
    receipts = [SimpleNamespace(action_id="action:invite", observed_state="delivered")]
    observations: tuple[object, ...] = ()
    if heat == "hot":
        refs = [
            SimpleNamespace(
                event_id="event:obs:prior",
                event_type="ObservationRecorded",
                world_revision=1,
                logical_time=declared - timedelta(seconds=40),
            ),
            SimpleNamespace(
                event_id="event:receipt:prior",
                event_type="ExecutionReceiptRecorded",
                world_revision=2,
                logical_time=declared - timedelta(seconds=25),
            ),
            SimpleNamespace(
                event_id="event:obs:current",
                event_type="ObservationRecorded",
                world_revision=3,
                logical_time=declared - timedelta(seconds=5),
            ),
            hope_ref,
        ]
        receipts = [
            SimpleNamespace(action_id="action:prior", observed_state="delivered"),
            SimpleNamespace(action_id="action:invite", observed_state="delivered"),
        ]
        observations = (
            SimpleNamespace(observation_id="m:prior", world_revision=1),
            SimpleNamespace(observation_id="m:current", world_revision=3),
        )
    elif heat == "warm":
        refs = [
            SimpleNamespace(
                event_id="event:receipt:prior",
                event_type="ExecutionReceiptRecorded",
                world_revision=1,
                logical_time=declared - timedelta(seconds=200),
            ),
            SimpleNamespace(
                event_id="event:obs:current",
                event_type="ObservationRecorded",
                world_revision=2,
                logical_time=declared - timedelta(seconds=5),
            ),
            hope_ref,
        ]
        receipts = [
            SimpleNamespace(action_id="action:prior", observed_state="delivered"),
            SimpleNamespace(action_id="action:invite", observed_state="delivered"),
        ]
        observations = (
            SimpleNamespace(observation_id="m:current", world_revision=2),
        )
    return SimpleNamespace(
        logical_time=declared + timedelta(seconds=logical_offset_seconds),
        message_observations=observations + extra_observations,
        committed_world_event_refs=tuple(refs) + extra_refs,
        execution_receipts=tuple(receipts),
        response_expectation_assessments=(),
        trigger_processes=(),
        expression_plan_manifests=(_hope_manifest(declared),),
        actions=actions,
    )


def test_expired_advisory_does_not_read_as_he_finished_pouring_water() -> None:
    value = expired_hope_advisory_value(
        hoped_response=WATER,
        seconds_since_he_last_spoke=120,
        spoken_since_declared=False,
    )

    assert value.startswith("He last spoke 120s ago")
    assert "her words, not a world event" in value
    assert f"Hope expired: {WATER}" not in value
    assert "Timing evidence only; she still decides." in value


def test_open_hope_without_exchange_history_does_not_mint_short_wake() -> None:
    projection = _open_hope_projection(logical_offset_seconds=90)

    assert expired_unanswered_expectation(projection) is None
    living = living_unanswered_hope(projection)
    assert living is not None
    assert living.is_open_hope is True


def test_legacy_sentinel_hope_does_not_mint_before_declared_not_before() -> None:
    early = _open_hope_projection(logical_offset_seconds=30, heat="hot")
    cadence_due = _open_hope_projection(
        logical_offset_seconds=OPEN_HOPE_WAKE_SECONDS["hot"], heat="hot"
    )
    at_wait = _open_hope_projection(logical_offset_seconds=86_400, heat="hot")

    assert expired_unanswered_expectation(early) is None
    assert expired_unanswered_expectation(cadence_due) is None
    found = expired_unanswered_expectation(at_wait)
    assert found is not None
    assert found.hoped_response == WATER
    assert found.not_before == EVENING + timedelta(seconds=86_400)


def test_warm_sentinel_hope_does_not_use_cadence_delay() -> None:
    too_soon = _open_hope_projection(logical_offset_seconds=90, heat="warm")
    cadence_due = _open_hope_projection(
        logical_offset_seconds=OPEN_HOPE_WAKE_SECONDS["warm"], heat="warm"
    )

    assert expired_unanswered_expectation(too_soon) is None
    assert expired_unanswered_expectation(cadence_due) is None


def test_overnight_sentinel_hope_does_not_mint_before_not_before() -> None:
    projection = _open_hope_projection(
        logical_offset_seconds=90, heat="hot", declared=OVERNIGHT
    )

    assert expired_unanswered_expectation(projection) is None
    assert living_unanswered_hope(projection) is not None


def test_open_hope_does_not_mint_after_he_spoke() -> None:
    declared = EVENING
    projection = _open_hope_projection(
        logical_offset_seconds=90,
        heat="hot",
        extra_observations=(
            SimpleNamespace(observation_id="m:after", world_revision=5),
        ),
        extra_refs=(
            SimpleNamespace(
                event_id="event:obs:after",
                event_type="ObservationRecorded",
                world_revision=5,
                logical_time=declared + timedelta(seconds=20),
            ),
        ),
    )

    assert expired_unanswered_expectation(projection) is None


def test_open_hope_does_not_remint_after_her_contact() -> None:
    declared = EVENING
    projection = _open_hope_projection(
        logical_offset_seconds=90,
        heat="hot",
        actions=(
            SimpleNamespace(
                action_id="action:ping",
                kind="proactive_message",
                state="delivered",
                logical_time=declared + timedelta(seconds=5),
            ),
        ),
    )

    assert expired_unanswered_expectation(projection) is None
    assert living_unanswered_hope(projection) is not None


def test_legacy_sentinel_hope_uses_declared_not_before_not_receipt_wall() -> None:
    wall = datetime(2026, 8, 18, 17, 10, 23, tzinfo=UTC)
    projection = _open_hope_projection(logical_offset_seconds=86_400, heat="hot")
    shifted = []
    for ref in projection.committed_world_event_refs:
        if ref.event_type == "ExecutionReceiptRecorded":
            shifted.append(
                SimpleNamespace(
                    event_id=ref.event_id,
                    event_type=ref.event_type,
                    world_revision=ref.world_revision,
                    logical_time=wall,
                )
            )
        else:
            shifted.append(ref)
    projection.committed_world_event_refs = tuple(shifted)
    projection.actions = (
        SimpleNamespace(
            action_id="action:prior",
            kind="reply",
            state="delivered",
            logical_time=EVENING - timedelta(seconds=25),
        ),
        SimpleNamespace(
            action_id="action:invite",
            kind="reply",
            state="delivered",
            logical_time=EVENING,
        ),
    )

    found = expired_unanswered_expectation(projection)
    assert found is not None
    assert found.declared_logical_time == EVENING
    assert found.not_before == EVENING + timedelta(seconds=86_400)


def test_multi_beat_same_timestamp_does_not_invent_a_cadence_wake() -> None:
    declared = EVENING
    projection = _open_hope_projection(
        logical_offset_seconds=OPEN_HOPE_WAKE_SECONDS["hot"], heat="hot"
    )
    extra_outs = tuple(
        SimpleNamespace(
            event_id=f"event:receipt:beat-{index}",
            event_type="ExecutionReceiptRecorded",
            world_revision=2,
            logical_time=declared - timedelta(seconds=25),
        )
        for index in range(3)
    )
    projection.committed_world_event_refs = (
        projection.committed_world_event_refs[:2]
        + extra_outs
        + projection.committed_world_event_refs[2:]
    )
    projection.execution_receipts = (
        projection.execution_receipts[0],
        SimpleNamespace(action_id="action:prior-b", observed_state="delivered"),
        SimpleNamespace(action_id="action:prior-c", observed_state="delivered"),
        SimpleNamespace(action_id="action:prior-d", observed_state="delivered"),
        projection.execution_receipts[1],
    )

    assert expired_unanswered_expectation(projection) is None


def test_timed_wait_still_mints_expiry() -> None:
    declared = EXPECTATION_NOW
    receipt_ref = SimpleNamespace(
        event_id="event:receipt:invite",
        event_type="ExecutionReceiptRecorded",
        world_revision=2,
        logical_time=declared,
    )
    projection = SimpleNamespace(
        logical_time=declared + timedelta(seconds=90),
        message_observations=(),
        committed_world_event_refs=(receipt_ref,),
        execution_receipts=(
            SimpleNamespace(action_id="action:invite", observed_state="delivered"),
        ),
        response_expectation_assessments=(),
        expression_plan_manifests=(
            SimpleNamespace(
                plan_id="plan:invite",
                beats=(
                    SimpleNamespace(
                        beat_id="beat:invite",
                        action=SimpleNamespace(action_id="action:invite"),
                    ),
                ),
                response_expectation=SimpleNamespace(
                    source_beat_id="beat:invite",
                    hoped_response=WATER,
                    not_before=declared + timedelta(seconds=60),
                    expires_at=declared + timedelta(seconds=120),
                ),
            ),
        ),
    )

    found = expired_unanswered_expectation(projection)
    assert found is not None
    assert found.hoped_response == WATER


def _hitch_context(monkeypatch, *, source_kind: str, event_text: str = "等我一下哈，我去倒杯水"):
    import companion_daemon.world_v2.proactive_action as proactive_action_module

    monkeypatch.setattr(
        proactive_action_module,
        "living_unanswered_hope",
        lambda _projection: LivingUnansweredHope(
            hoped_response=WATER,
            declared_world_revision=2,
            declared_seconds_ago=120,
            is_open_hope=True,
        ),
    )
    monkeypatch.setattr(
        proactive_action_module,
        "counterpart_last_spoke_facts",
        lambda _projection, since_world_revision=None: (120, False),
    )
    context = _proactive_opportunity_context(
        opportunity=SimpleNamespace(source_kind=source_kind, stimulus_event_refs=()),
        event=SimpleNamespace(payload=lambda: {"text": event_text}),
        head=None,
        projection=object(),
    )
    value = _proactive_advisory_value(
        opportunity_context=context,
        source_kind=source_kind,
    )
    return context, value


def test_spontaneous_contact_hitches_living_hope(monkeypatch) -> None:
    context, value = _hitch_context(monkeypatch, source_kind="spontaneous_contact")

    assert "She is waiting for (her words, not a world event): 他倒完水回来" in context
    assert "He last spoke 120s ago" in context
    assert "he has not spoken since she declared that hope" in context
    assert f"Hope expired: {WATER}" not in context
    assert WATER in value
    assert "her words, not a world event" in value


def test_post_silent_states_she_chose_not_to_reply_and_hitches(monkeypatch) -> None:
    context, value = _hitch_context(monkeypatch, source_kind="post_silent")

    assert "She chose not to reply on the prior consideration" in context
    assert "did not treat that as read-without-reply" in context
    assert "She is waiting for (her words, not a world event)" in context
    assert WATER in value


def test_ambient_presence_hitches_living_hope(monkeypatch) -> None:
    context, _value = _hitch_context(monkeypatch, source_kind="ambient_presence")

    assert "Clock is timing authority only" in context
    assert "She is waiting for (her words, not a world event)" in context


def test_wakeup_lane_copy_does_not_invent_world_events() -> None:
    kinds = (
        "spontaneous_contact",
        "ambient_presence",
        "post_silent",
        "situation_change",
        "thread",
        "commitment",
        "revisit_intention",
        "private_impression",
        "later_expression_refresh",
    )
    forbidden = (
        "Hope expired:",
        "should chase",
        "没理",
        "追问",
        "已读不回",
    )
    for kind in kinds:
        context = _proactive_opportunity_context(
            opportunity=SimpleNamespace(
                source_kind=kind,
                stimulus_event_refs=("event:stimulus",),
            ),
            event=SimpleNamespace(payload=lambda: {"text": "等我一下哈，我去倒杯水"}),
            head=None,
            projection=object(),
        )
        lowered = context.lower()
        for needle in forbidden:
            assert needle.lower() not in lowered, (kind, needle, context)
        if kind == "post_silent":
            assert "She chose not to reply" in context
            assert "did not treat that as read-without-reply" in context
