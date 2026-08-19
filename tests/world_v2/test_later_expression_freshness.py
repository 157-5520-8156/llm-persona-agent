"""Later followups re-check the world before dispatch; cadence now-beats do not."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from companion_daemon.world_v2.action_pump import ActionPump
from companion_daemon.world_v2.character_interior.contracts import (
    assert_compile_time_materials_are_source_bound,
)
from companion_daemon.world_v2.character_interior.snapshot_compiler import (
    compile_inner_life_snapshot,
)
from companion_daemon.world_v2.context_capsule import (
    compile_pending_outbound_expression_item,
)
from companion_daemon.world_v2.later_expression_freshness import (
    LATER_TTL_SECONDS,
    companion_spoke_after,
    is_later_followup,
    later_dispatch_allowed,
    later_refresh_consideration_id,
    later_stale_reason,
    later_ttl_seconds,
    queued_later_facts,
)
from companion_daemon.world_v2.schemas import (
    Action,
    ExpressionBeatProjection,
    ExpressionPlanProjection,
)


NOW = datetime(2026, 8, 19, 0, 53, tzinfo=UTC)
HASH = "a" * 64
TEXT = "早～外面好像要下雨了，你那边呢"


def _later(
    *,
    action_id: str = "action:later:1",
    plan_id: str = "plan:later:1",
    beat_id: str = "beat:later:1",
    written_at: datetime = NOW,
    not_before: datetime | None = None,
    state: str = "authorized",
) -> SimpleNamespace:
    return SimpleNamespace(
        action_id=action_id,
        kind="followup",
        state=state,
        logical_time=written_at,
        not_before=not_before or written_at + timedelta(hours=1),
        expires_at=written_at + timedelta(hours=2),
        expression_plan_id=plan_id,
        expression_beat_id=beat_id,
        payload_ref="payload:later:1",
    )


def _spoken(
    *,
    action_id: str = "action:now:1",
    plan_id: str = "plan:now:1",
    at: datetime,
    kind: str = "proactive_message",
    state: str = "delivered",
) -> SimpleNamespace:
    return SimpleNamespace(
        action_id=action_id,
        kind=kind,
        state=state,
        logical_time=at,
        not_before=None,
        expression_plan_id=plan_id,
        expression_beat_id="beat:now:1",
        payload_ref="payload:now:1",
    )


def _projection(*, actions=(), beats=(), refs=(), processes=(), payloads=(), logical_time=None):
    return SimpleNamespace(
        logical_time=logical_time or NOW + timedelta(hours=1),
        actions=tuple(actions),
        expression_beats=tuple(beats),
        committed_world_event_refs=tuple(refs),
        trigger_processes=tuple(processes),
        stored_message_payloads=tuple(payloads),
    )


def test_ttl_copies_cadence_exit_bands_and_gives_cold_a_thirty_minute_due_window() -> None:
    assert LATER_TTL_SECONDS["hot"] == 120
    assert LATER_TTL_SECONDS["warm"] == 720
    assert LATER_TTL_SECONDS["cold"] == 1_800
    assert later_ttl_seconds("cold") == 1_800


def test_cadence_now_reply_is_not_a_later_followup() -> None:
    reply = SimpleNamespace(
        kind="reply",
        not_before=NOW + timedelta(seconds=2),
        expression_plan_id="plan:now:1",
        expression_beat_id="beat:now:1",
    )
    assert not is_later_followup(reply)
    assert later_dispatch_allowed(_projection(), reply, NOW + timedelta(seconds=2))


def test_due_later_is_current_when_nothing_happened() -> None:
    later = _later()
    due = later.not_before
    projection = _projection(actions=(later,), logical_time=due)
    assert later_stale_reason(projection, later, due) is None
    assert later_dispatch_allowed(projection, later, due)


def test_companion_speech_from_another_plan_stales_the_frozen_later() -> None:
    later = _later()
    spoken = _spoken(at=NOW + timedelta(minutes=16))
    due = later.not_before
    projection = _projection(actions=(later, spoken), logical_time=due)
    assert companion_spoke_after(
        projection, written_at=later.logical_time, plan_id=later.expression_plan_id
    )
    assert later_stale_reason(projection, later, due) == "companion_spoke"
    assert not later_dispatch_allowed(projection, later, due)


def test_same_plan_cadence_siblings_do_not_stale_each_other() -> None:
    later = _later(plan_id="plan:now:1", beat_id="beat:later")
    sibling = _spoken(
        action_id="action:now:sibling",
        plan_id="plan:now:1",
        at=NOW + timedelta(seconds=1),
        kind="reply",
    )
    due = later.not_before
    projection = _projection(actions=(later, sibling), logical_time=due)
    assert later_stale_reason(projection, later, due) is None


def test_queued_later_is_not_companion_speech() -> None:
    first = _later()
    second = _later(
        action_id="action:later:2",
        plan_id="plan:later:2",
        beat_id="beat:later:2",
        written_at=NOW + timedelta(minutes=10),
    )
    due = first.not_before
    projection = _projection(actions=(first, second), logical_time=due)
    assert later_stale_reason(projection, first, due) is None


def test_he_spoke_stales_the_frozen_later() -> None:
    later = _later()
    due = later.not_before
    projection = _projection(
        actions=(later,),
        refs=(
            SimpleNamespace(
                event_type="ObservationRecorded",
                event_id="event:obs:1",
                logical_time=NOW + timedelta(minutes=20),
                world_revision=4,
                payload_hash=HASH,
            ),
        ),
        logical_time=due,
    )
    assert later_stale_reason(projection, later, due) == "counterpart_spoke"


def test_ttl_is_measured_from_due_not_from_write() -> None:
    later = _later()
    due = later.not_before
    still_current = due + timedelta(seconds=1_799)
    lapsed = due + timedelta(seconds=1_800)
    projection = _projection(actions=(later,), logical_time=lapsed)
    assert later_stale_reason(projection, later, still_current) is None
    assert later_stale_reason(projection, later, lapsed) == "ttl_elapsed"


def test_queued_facts_include_not_yet_due_text_and_hide_after_terminal_refresh() -> None:
    later = _later()
    beat = SimpleNamespace(
        beat_id=later.expression_beat_id,
        action_id=later.action_id,
        payload_ref=later.payload_ref,
        event_ref="event:beat:later",
    )
    payload = SimpleNamespace(payload_ref=later.payload_ref, text=TEXT)
    authority = SimpleNamespace(
        event_id="event:beat:later",
        event_type="ExpressionBeatAuthorized",
        logical_time=NOW,
        world_revision=3,
        payload_hash=HASH,
    )
    projection = _projection(
        actions=(later,),
        beats=(beat,),
        refs=(authority,),
        payloads=(payload,),
        logical_time=NOW + timedelta(minutes=16),
    )
    facts = queued_later_facts(projection, logical_time=projection.logical_time)
    assert len(facts) == 1
    assert facts[0].text == TEXT
    assert facts[0].i_spoke_after is False

    spoken = _spoken(at=NOW + timedelta(minutes=16))
    projection.actions = (later, spoken)
    facts = queued_later_facts(projection, logical_time=projection.logical_time)
    assert facts[0].i_spoke_after is True

    projection.trigger_processes = (
        SimpleNamespace(
            process_kind="proactive_action_deliberation",
            trigger_ref="proactive-consideration:"
            + later_refresh_consideration_id(later.action_id),
            state="terminal",
        ),
    )
    assert queued_later_facts(projection, logical_time=projection.logical_time) == ()


def test_snapshot_waiting_messages_are_source_bound_facts_not_scripts() -> None:
    item = compile_pending_outbound_expression_item(
        action_id="action:later:1",
        plan_id="plan:later:1",
        beat_id="beat:later:1",
        text=TEXT,
        written_at=NOW,
        send_at=NOW + timedelta(hours=1),
        he_spoke_after=False,
        i_spoke_after=True,
        authority_event_ref="event:beat:later",
        authority_world_revision=3,
        authority_payload_hash=HASH,
    )
    typed = compile_inner_life_snapshot(
        {
            "world_id": "world:later",
            "actor_ref": "agent:companion",
            "world_revision": 12,
            "deliberation_revision": 2,
            "ledger_sequence": 12,
            "logical_time": "2026-08-19T09:09:00+08:00",
            "slices": {
                "pending_outbound": {
                    "availability": "available",
                    "items": [
                        {
                            "item_ref": item.action_id,
                            "source_ref": item.action_id,
                            "privacy_class": item.privacy_class,
                            "value": item.model_dump(mode="json"),
                        }
                    ],
                }
            },
        }
    )
    materials = __import__("json").loads(typed.materials_json)
    waiting = materials["messages_waiting_to_send"]
    assert waiting[0]["text"] == TEXT
    assert waiting[0]["already_in_chat"] is False
    assert "还没发出" in waiting[0]["line"]
    assert "另外发过话" in waiting[0]["line"]
    assert "别重复" not in waiting[0]["line"]
    assert "不要再" not in waiting[0]["line"]
    assert_compile_time_materials_are_source_bound(materials)
    assert item.action_id in typed.source_refs


def _authorized_action(*, kind: str, not_before: datetime | None) -> Action:
    return Action(
        schema_version="world-v2.1",
        action_id="action:expression:1",
        world_id="world:later",
        logical_time=NOW,
        created_at=NOW,
        trace_id="trace:later",
        causation_id="acceptance:later:1",
        correlation_id="correlation:later",
        kind=kind,
        layer="external_action",
        intent_ref="proposal:later:1:intent:1",
        actor="agent:companion",
        target="user:primary",
        payload_ref="payload:later:1",
        payload_hash="sha256:" + HASH,
        expression_plan_id="plan:later:1",
        expression_beat_id="beat:later:1",
        idempotency_key="action:later:1",
        not_before=not_before,
        budget_reservation_id="reservation:later:1",
        state="authorized",
        recovery_policy="effect_once",
    )


def _pump_projection(action: Action, *, extra=()):
    beat = ExpressionBeatProjection(
        acceptance_id="acceptance:later:1",
        proposal_id="proposal:later:1",
        expression_change_id="change:later:1",
        plan_id=action.expression_plan_id,
        beat_id=action.expression_beat_id,
        payload_ref=action.payload_ref,
        payload_hash=action.payload_hash,
        action_id=action.action_id,
        cancel_policy="none",
        reconsider_policy="none",
        merge_policy="none",
        event_ref="event:beat:later",
        event_payload_hash=HASH,
        state="authorized",
    )
    plan = ExpressionPlanProjection(
        acceptance_id="acceptance:later:1",
        proposal_id="proposal:later:1",
        expression_change_id="change:later:1",
        plan_id=action.expression_plan_id,
        event_ref="event:plan:later",
        event_payload_hash=HASH,
        state="authorized",
    )
    return SimpleNamespace(
        logical_time=action.not_before or NOW,
        actions=(action, *extra),
        expression_beats=(beat,),
        expression_plans=(plan,),
        trigger_processes=(),
        committed_world_event_refs=(),
    )


def test_action_pump_blocks_stale_later_and_still_allows_cadence_reply() -> None:
    later = _authorized_action(kind="followup", not_before=NOW + timedelta(hours=1))
    spoken = _spoken(at=NOW + timedelta(minutes=16))
    projection = _pump_projection(later, extra=(spoken,))
    projection.logical_time = later.not_before
    assert not ActionPump._expression_dispatch_allowed(later, projection)

    reply = _authorized_action(kind="reply", not_before=NOW + timedelta(seconds=2))
    sibling = _spoken(
        action_id="action:now:sibling",
        plan_id=reply.expression_plan_id,
        at=NOW,
        kind="reply",
    )
    projection = _pump_projection(reply, extra=(sibling,))
    projection.logical_time = reply.not_before
    assert ActionPump._expression_dispatch_allowed(reply, projection)
