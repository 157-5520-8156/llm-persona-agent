"""Empty-life day_open through the installed application and real role compiler.

Only HTTP is replaced. No conversation, world occurrence, accepted character
proposal or activity event is seeded by this test.
"""

from __future__ import annotations

from datetime import timedelta
import json

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.world_turn_runtime import InboundTurn
from test_world_stimulus_life_intent import ACTOR, NOW, _http_result, _RoleHTTP


@pytest.fixture
def build_app(monkeypatch):
    # Match the host composition: the CharacterInterior terminal sidecar is
    # independent from the World ledger, and must survive the app restart.
    import test_world_stimulus_life_intent as shared
    from companion_daemon.world_v2.character_interior.turn_store import (
        open_sqlite_character_interior_turn_store,
    )

    original = shared.compose_production_character_interior
    stores = []

    def build(path, model, *, ecology):
        store = open_sqlite_character_interior_turn_store(path=path, world_id=shared.WORLD)
        stores.append(store)
        with monkeypatch.context() as patch:
            patch.setattr(
                shared,
                "compose_production_character_interior",
                lambda **kwargs: original(**kwargs, turn_store=store),
            )
            return shared._build(path, model, ecology=ecology)

    yield build
    for store in stores:
        store.close()


INTENT = {
    "execution_scope": "self_directed",
    "intention": "想花几分钟给自己的想法整理一个顺序。",
    "start_after_seconds": 0,
    "duration_seconds": 300,
    "importance_bp": 4200,
}


class _DayOpenHTTP(_RoleHTTP):
    def __init__(self, *, choose=True, fail=False, repair=False):
        super().__init__()
        self.choose = choose
        self.day_requests = []
        self.fail = fail
        self.repair = repair

    async def __call__(self, request):
        body = json.loads(request.content)
        material = None
        for message in body["messages"]:
            try:
                value = json.loads(message["content"])
            except (ValueError, TypeError):
                continue
            if isinstance(value, dict) and "inner_turn" in value:
                material = value
                break
        if material is None:
            return await super().__call__(request)
        if (
            not material.get("capability_manifest", {})
            .get("payload", {})
            .get("self_directed_intent")
        ):
            return await super().__call__(request)
        self.requests.append(body)
        self.day_requests.append(body)
        if self.fail:
            raise httpx.ReadError("offline day-open provider failure", request=request)
        assert material["inner_turn"]["purpose"] == "activity_lifecycle_choice"
        capability = material["capability_manifest"]
        assert capability["payload"]["offered_tokens"] == []
        assert capability["payload"]["self_directed_intent"]["execution_scope"] == "self_directed"
        return _http_result(
            body,
            {
                "status": "decision",
                "summary": "我给接下来留下一项自己的安排。",
                "attended_source_refs": capability["source_refs"],
                "recall_query": None,
                "proposals": [],
                "decision": {
                    "source_refs": capability["source_refs"],
                    "payload": {
                        "decision": "self_directed_intent",
                        "life_intent": {**INTENT, "execution_scope": "control_others"}
                        if self.repair and len(self.day_requests) == 1
                        else INTENT,
                    }
                    if self.choose
                    else {"decision": "no_op"},
                },
            },
        )


@pytest.mark.asyncio
async def test_empty_life_clock_offers_character_first_self_directed_plan(
    tmp_path, monkeypatch, build_app
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = _DayOpenHTTP()
    model = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(provider),
    )
    app = build_app(tmp_path / "day-open.sqlite", model, ecology=True)
    try:
        assert app.export_replay_evidence().projection.plans == ()
        at = NOW + timedelta(minutes=1)
        await app.tick(
            tick_id="empty-day-open",
            logical_time_from=NOW,
            logical_time_to=at,
            observed_at=at,
            trace_id="trace:empty-day-open",
            causation_id="clock:empty-day-open",
            correlation_id="day-open-self-directed",
            reason="test_clock",
        )
        evidence = app.export_replay_evidence()
        event_types = [item.event.event_type for item in evidence.events]
        assert "ClockAdvanced" in event_types
        assert "ObservationRecorded" not in event_types
        assert "WorldOccurrenceSettled" not in event_types
        assert len(provider.requests) == 1, "empty catalog skipped the existing day_open role"
        plans = evidence.projection.plans
        assert len(plans) == 1
        plan = plans[0]
        assert plan.owner_actor_ref == ACTOR
        assert plan.status == "planned"
        assert plan.location_ref is None and plan.participant_refs == ()
        assert plan.privacy_class == "private"
        assert plan.scheduled_window.opens_at == at
        assert plan.scheduled_window.closes_at == at + timedelta(seconds=300)
    finally:
        app.close()
        await model.aclose()


@pytest.mark.asyncio
async def test_day_open_invalid_intent_gets_only_original_role_correction(
    tmp_path, monkeypatch, build_app
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = _DayOpenHTTP(repair=True)
    model = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(provider),
    )
    app = build_app(tmp_path / "correction.sqlite", model, ecology=True)
    try:
        at = NOW + timedelta(minutes=1)
        await app.tick(
            tick_id="corrected",
            logical_time_from=NOW,
            logical_time_to=at,
            observed_at=at,
            trace_id="trace:corrected",
            causation_id="clock:corrected",
            correlation_id="day-open-correction",
            reason="test_clock",
        )
        assert len(provider.day_requests) == 2
        assert provider.day_requests[1]["messages"][0] == provider.day_requests[0]["messages"][0]
        first = json.loads(provider.day_requests[0]["messages"][1]["content"])
        corrected = json.loads(provider.day_requests[1]["messages"][1]["content"])
        assert corrected["capability_manifest"] == first["capability_manifest"]
        assert corrected["inner_life_snapshot"] == first["inner_life_snapshot"]
        assert corrected["correction"]["failure_code"] == "role_result_schema_invalid"
        assert "execution_scope" in corrected["correction"]["failure_detail"]
        assert len(app.export_replay_evidence().projection.plans) == 1
    finally:
        app.close()
        await model.aclose()


@pytest.mark.asyncio
async def test_day_open_primary_budget_denial_is_explicit_and_sends_zero_http(
    tmp_path, monkeypatch, build_app
):
    import sqlite3
    from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    usage = WorldV2UsageStore(path=str(tmp_path / "primary.sqlite"), monthly_budget_cny=0.000001)
    provider = _DayOpenHTTP()
    model = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(provider),
        usage_observer=usage.record,
    )
    path = tmp_path / "budget.sqlite"
    app = build_app(path, model, ecology=True)
    try:
        at = NOW + timedelta(minutes=1)
        await app.tick(
            tick_id="budget",
            logical_time_from=NOW,
            logical_time_to=at,
            observed_at=at,
            trace_id="trace:budget",
            causation_id="clock:budget",
            correlation_id="day-open-budget",
            reason="test_clock",
        )
        assert provider.requests == []
        assert app.export_replay_evidence().projection.plans == ()
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            row = json.loads(
                connection.execute("SELECT body FROM world_v2_day_open_opportunities").fetchone()[0]
            )
        assert row["attempts"][0]["failure_code"] == "monthly_budget_exceeded"
        assert row["terminal_reason"] != "role_no_op"
    finally:
        app.close()
        await model.aclose()


@pytest.mark.asyncio
async def test_day_open_technical_failure_has_bounded_retry_not_daily_no_op(
    tmp_path, monkeypatch, build_app
):
    import sqlite3

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = _DayOpenHTTP(fail=True)
    model = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(provider),
    )
    path = tmp_path / "day-open-failure.sqlite"
    app = build_app(path, model, ecology=True)
    try:
        previous = NOW
        for tick, seconds, calls in (
            ("first", 60, 1),
            ("waiting", 70, 1),
            ("retry1", 90, 2),
            ("retry2", 210, 3),
            ("exhausted", 240, 3),
        ):
            at = NOW + timedelta(seconds=seconds)
            await app.tick(
                tick_id=tick,
                logical_time_from=previous,
                logical_time_to=at,
                observed_at=at,
                trace_id="trace:" + tick,
                causation_id="clock:" + tick,
                correlation_id="day-open-failure",
                reason="test_clock",
            )
            previous = at
            assert len(provider.day_requests) == calls
            if tick == "retry1":
                app.close()
                app = build_app(path, model, ecology=True)
        assert app.export_replay_evidence().projection.plans == ()
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            row = json.loads(
                connection.execute("SELECT body FROM world_v2_day_open_opportunities").fetchone()[0]
            )
        assert row["terminal_reason"] == "technical_attempts_exhausted"
        assert len(row["attempts"]) == 3
        assert all(x["failure_code"] for x in row["attempts"])
    finally:
        app.close()
        await model.aclose()


@pytest.mark.asyncio
async def test_first_day_intent_starts_through_lifecycle_and_next_chat_reads_original_intention(
    tmp_path, monkeypatch, build_app
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = _DayOpenHTTP()
    model = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(provider),
    )
    app = build_app(tmp_path / "day-open-execution.sqlite", model, ecology=True)
    try:
        previous = NOW
        for tick, at in (
            ("plan", NOW + timedelta(minutes=1)),
            ("start", NOW + timedelta(minutes=1, seconds=1)),
        ):
            await app.tick(
                tick_id=tick,
                logical_time_from=previous,
                logical_time_to=at,
                observed_at=at,
                trace_id="trace:" + tick,
                causation_id="clock:" + tick,
                correlation_id="day-open-execution",
                reason="test_clock",
            )
            previous = at
        plan = app.export_replay_evidence().projection.plans[0]
        assert plan.status == "active"
        assert plan.authority_origin.accepted_event_type == "ActivityStarted"
        assert len(provider.day_requests) == 1
        assert len(provider.lifecycle_requests) == 1
        assert INTENT["intention"] in json.dumps(provider.lifecycle_requests[0], ensure_ascii=False)
        await app.respond(
            InboundTurn(
                platform="test",
                platform_user_id="user.1",
                platform_message_id="ask-active",
                text="现在在忙什么？",
                observed_at=previous,
                trace_id="trace:ask-active",
            )
        )
        material = json.loads(provider.chat_requests[-1]["messages"][-1]["content"])
        active = material["inner_life_snapshot"]["materials"]["current_activities"]
        assert len(active) == 1
        assert active[0]["plan_id"] == plan.plan_id
        assert active[0]["accepted_intention"]["text"] == INTENT["intention"]
        assert active[0]["source_ref"] == plan.authority_origin.accepted_event_ref
    finally:
        app.close()
        await model.aclose()


@pytest.mark.asyncio
async def test_day_open_no_op_is_terminal_and_shared_daily_spend_survives_restart(
    tmp_path, monkeypatch, build_app
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = _DayOpenHTTP(choose=False)
    model = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(provider),
    )
    path = tmp_path / "day-open-no-op.sqlite"
    app = build_app(path, model, ecology=True)
    try:
        previous = NOW
        for ordinal, advance in enumerate((timedelta(minutes=1), timedelta(minutes=2))):
            at = NOW + advance
            await app.tick(
                tick_id=f"decline:{ordinal}",
                logical_time_from=previous,
                logical_time_to=at,
                observed_at=at,
                trace_id=f"trace:decline:{ordinal}",
                causation_id=f"clock:decline:{ordinal}",
                correlation_id="day-open-no-op",
                reason="test_clock",
            )
            previous = at
            if ordinal == 0:
                assert len(provider.requests) == 1
                app.close()
                app = build_app(path, model, ecology=True)
        assert len(provider.requests) == 1
        evidence = app.export_replay_evidence()
        assert evidence.projection.plans == ()
        assert not any(item.event.event_type == "ActivityPlanned" for item in evidence.events)
    finally:
        app.close()
        await model.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_seam", ["before_audit", "before_plan"])
async def test_paid_day_open_choice_recovers_original_intention_after_new_clock(
    tmp_path, monkeypatch, failure_seam, build_app
):
    from companion_daemon.world_v2.day_open_life_worker import DayOpenLifeWorker
    from companion_daemon.world_v2.day_open_life_intent_runtime import DayOpenLifeIntentRuntime
    from companion_daemon.world_v2.life_events import ActivityPlannedPayload

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    target, method = (
        (DayOpenLifeWorker, "_record")
        if failure_seam == "before_audit"
        else (DayOpenLifeIntentRuntime, "accept")
    )
    original = getattr(target, method)
    failures = []

    def fail_once(self, *args, **kwargs):
        if not failures:
            failures.append(True)
            raise RuntimeError("injected once after durable role terminal")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(target, method, fail_once)
    provider = _DayOpenHTTP()
    model = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(provider),
    )
    path = tmp_path / (failure_seam + ".sqlite")
    app = build_app(path, model, ecology=True)
    try:
        selected_at = NOW + timedelta(minutes=1)
        await app.tick(
            tick_id="original",
            logical_time_from=NOW,
            logical_time_to=selected_at,
            observed_at=selected_at,
            trace_id="trace:original",
            causation_id="clock:original",
            correlation_id="day-open-recovery",
            reason="test_clock",
        )
        assert failures and len(provider.day_requests) == 1
        assert app.export_replay_evidence().projection.plans == ()
        app.close()
        app = build_app(path, model, ecology=True)
        recovered_at = selected_at + timedelta(seconds=30)
        await app.tick(
            tick_id="recovery",
            logical_time_from=selected_at,
            logical_time_to=recovered_at,
            observed_at=recovered_at,
            trace_id="trace:recovery",
            causation_id="clock:recovery",
            correlation_id="day-open-recovery",
            reason="test_clock",
        )
        evidence = app.export_replay_evidence()
        assert len(provider.day_requests) == 1
        planned = [
            row.event for row in evidence.events if row.event.event_type == "ActivityPlanned"
        ]
        assert len(planned) == 1
        payload = ActivityPlannedPayload.model_validate_json(planned[0].payload_json)
        assert payload.plan.scheduled_window.opens_at == selected_at
        assert payload.day_open_intent_origin.selected_at == selected_at
        assert payload.day_open_intent_origin.source_event_ref == "event:trigger:clock:original"
        assert planned[0].logical_time == recovered_at
        assert len(evidence.projection.plans) == 1
    finally:
        app.close()
        await model.aclose()
