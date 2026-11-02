"""Regression tests for thumb-typed volley listening and follow-up beat pacing.

Evidence: the 2026-10-26 QQ volley where the user typed one story at 2-11s
per bubble and the character answered mid-story each time, then sent four
already-authored bubbles within ~1.8s.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.beat_composition_pacing import composition_seconds
from companion_daemon.world_v2.qq_c2c_host import QQC2CHost
from companion_daemon.world_v2.qq_ingress_policy import MemoryQQIngressStore

from test_qq_ingress_policy import NOW, _SlowFirstTurnWorldHost, _WorldHost, _manual_clock, _text


@pytest.mark.asyncio
async def test_thumb_typed_volley_at_five_second_cadence_stays_one_followup_turn() -> None:
    """Once the sender has continued at ~5s, a next bubble 5s later still joins."""

    start = NOW
    clock, idle_sleep, drive = _manual_clock(start)
    world = _SlowFirstTurnWorldHost()
    host = QQC2CHost(
        host=world,  # type: ignore[arg-type]
        recipient_id="10001",
        canonical_user_id="geoff",
        ingress_store=MemoryQQIngressStore(),
        ingress_now=lambda: clock["now"],
        ingress_sleep=idle_sleep,
    )
    texts = ("前段时间院里争保研", "我就差一点点", "差两个名额就到我了", "而且分差实在是太小了")
    tasks = [asyncio.create_task(host.inbound_fragment(_text("message:s0", texts[0], observed_at=start)))]
    await asyncio.sleep(0)
    await drive(lambda: world.first_turn_started.is_set())
    for index, text in enumerate(texts[1:], start=1):
        at = start + timedelta(seconds=5 * index)
        await drive(lambda at=at: clock["now"] >= at)
        tasks.append(
            asyncio.create_task(host.inbound_fragment(_text(f"message:s{index}", text, observed_at=at)))
        )
        await asyncio.sleep(0)

    await drive(lambda: len(world.inbounds) == 2)
    assert world.inbounds[1].text == "我就差一点点\n差两个名额就到我了\n而且分差实在是太小了"
    world.release_first_turn.set()
    await drive(lambda: all(task.done() for task in tasks))
    assert len(world.inbounds) == 2
    await host.aclose()


@pytest.mark.asyncio
async def test_volley_cadence_hold_is_bounded_and_absent_for_a_first_bubble() -> None:
    host = QQC2CHost(
        host=_SlowFirstTurnWorldHost(),  # type: ignore[arg-type]
        recipient_id="10001",
        canonical_user_id="geoff",
        ingress_store=MemoryQQIngressStore(),
    )
    assert host._volley_cadence_hold_seconds() is None
    host._recent_gap_seconds.extend((2.0, 3.0))
    assert host._volley_cadence_hold_seconds() == pytest.approx(3.9)
    host._recent_gap_seconds.append(11.5)
    assert host._volley_cadence_hold_seconds() == host._VOLLEY_CADENCE_MAX_HOLD_SECONDS
    await host.aclose()


def test_composition_seconds_scales_with_length_and_is_bounded_and_stable() -> None:
    short = composition_seconds(character_count=3, jitter_key="action:a")
    medium = composition_seconds(character_count=12, jitter_key="action:a")
    huge = composition_seconds(character_count=400, jitter_key="action:a")
    assert 0.9 <= short < medium < huge == 6.0
    assert composition_seconds(character_count=12, jitter_key="action:a") == medium
    assert composition_seconds(character_count=None, jitter_key="action:s") == pytest.approx(1.2, rel=0.16)
    with pytest.raises(ValueError):
        composition_seconds(character_count=3, jitter_key="")


class _PacingProjectionHost(_WorldHost):
    """Platform host fake exposing one dispatched beat 1 and authorized beat 2."""

    def __init__(self, projection=None) -> None:
        super().__init__()
        self.beat_two_length = 15
        self._projection = projection or SimpleNamespace(actions=())

    async def action_due_projection(self):
        return self._projection

    async def delivered_text_character_count(self, action_id: str) -> int | None:
        return self.beat_two_length if action_id == "action:beat:2" else None


@pytest.mark.asyncio
async def test_followup_beat_waits_for_typing_gap_after_previous_beat_dispatch() -> None:
    clock = {"now": NOW}
    slept: list[float] = []

    async def record_sleep(delay: float) -> None:
        slept.append(delay)
        clock["now"] += timedelta(seconds=delay)

    beat_one = SimpleNamespace(
        action_id="action:beat:1",
        state="provider_accepted",
        expression_plan_id="plan:1",
        dependencies=(),
        kind="reply",
    )
    beat_two = SimpleNamespace(
        action_id="action:beat:2",
        state="authorized",
        expression_plan_id="plan:1",
        dependencies=("action:beat:1",),
        kind="reply",
    )
    host = QQC2CHost(
        host=_PacingProjectionHost(SimpleNamespace(actions=(beat_one, beat_two))),  # type: ignore[arg-type]
        recipient_id="10001",
        canonical_user_id="geoff",
        ingress_store=MemoryQQIngressStore(),
        ingress_now=lambda: clock["now"],
        ingress_sleep=record_sleep,
        followup_composition_pacing=True,
    )
    host._note_beat_dispatch(
        SimpleNamespace(
            status="settled",
            action_kind="reply",
            provider_status="provider_accepted",
            action_id="action:beat:1",
        )
    )
    clock["now"] += timedelta(seconds=1.0)
    await host._pace_followup_beat()
    expected = composition_seconds(character_count=15, jitter_key="action:beat:2") - 1.0
    assert slept == [pytest.approx(expected)]

    # Already waited long enough: no further pause.
    slept.clear()
    await host._pace_followup_beat()
    assert slept == []
    await host.aclose()


@pytest.mark.asyncio
async def test_pacing_disabled_never_sleeps() -> None:
    slept: list[float] = []

    async def record_sleep(delay: float) -> None:
        slept.append(delay)

    host = QQC2CHost(
        host=_PacingProjectionHost(),  # type: ignore[arg-type]
        recipient_id="10001",
        canonical_user_id="geoff",
        ingress_store=MemoryQQIngressStore(),
        ingress_sleep=record_sleep,
    )
    host._note_beat_dispatch(
        SimpleNamespace(status="settled", action_kind="reply", provider_status="provider_accepted", action_id="a")
    )
    await host._pace_followup_beat()
    assert slept == []
    await host.aclose()
