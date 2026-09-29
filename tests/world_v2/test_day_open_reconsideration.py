"""Same role chooses a later thought; no plan exists until the second choice."""
import json
from datetime import timedelta

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.day_open_opportunity import day_open_retry_due, day_open_reconsideration_due
from companion_daemon.world_v2.replay_evaluator import ReplayEvaluator
from test_day_open_self_directed_intent import _DayOpenHTTP, build_app as _build_app_fixture
from test_world_stimulus_life_intent import ACTOR, NOW, _http_result


build_app = _build_app_fixture


class ReconsiderHTTP(_DayOpenHTTP):
    def __init__(self, delay, *, again=False, malformed=False):
        super().__init__()
        self.delay, self.again, self.malformed = delay, again, malformed

    async def __call__(self, request):
        count = len(self.day_requests)
        response = await super().__call__(request)
        if len(self.day_requests) == count:
            return response
        if count == 0 or self.again:
            body = json.loads(request.content)
            args = json.loads(response.json()["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"])
            args["decision"]["payload"] = {"decision": "reconsider", "reconsider_after_seconds": self.delay}
            if self.malformed:
                args["decision"]["payload"]["life_intent"] = {"intention": "not allowed"}
            return _http_result(body, args)
        return response


async def tick(app, label, previous, at):
    return await app.tick(
        tick_id=label, logical_time_from=previous, logical_time_to=at, observed_at=at,
        trace_id="trace:" + label, causation_id="clock:" + label,
        correlation_id="reconsider-test", reason="test_clock",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("delay", [3600, 86400])
async def test_reconsideration_waits_survives_restart_and_plans_once(tmp_path, monkeypatch, build_app, delay):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = ReconsiderHTTP(delay)
    model = DeepSeekChatModel("offline", "https://fixture.invalid", "deepseek-v4-flash",
                             thinking_enabled=False, transport=httpx.MockTransport(provider))
    path = tmp_path / "reconsider.sqlite"
    app = build_app(path, model, ecology=True)
    first = NOW + timedelta(minutes=1)
    due = first + timedelta(seconds=delay)
    try:
        await tick(app, "choose-later", NOW, first)
        assert len(provider.day_requests) == 1
        assert app.export_replay_evidence().projection.plans == ()
        assert day_open_retry_due(app._ledger, actor_ref=ACTOR) is None
        assert day_open_reconsideration_due(app._ledger, actor_ref=ACTOR) == due
        await tick(app, "before-due", first, first + timedelta(seconds=30))
        assert len(provider.day_requests) == 1
        app.close()
        app = build_app(path, model, ecology=True)
        assert day_open_reconsideration_due(app._ledger, actor_ref=ACTOR) == due
        await tick(app, "at-due", first + timedelta(seconds=30), due)
        assert len(provider.day_requests) == 2
        evidence = app.export_replay_evidence()
        assert len(evidence.projection.plans) == 1
        plan = evidence.projection.plans[0]
        assert plan.status == "planned"
        assert plan.scheduled_window.opens_at == due
        assert plan.authority_origin.accepted_event_type == "ActivityPlanned"
        assert not evidence.projection.actions
        await tick(app, "at-due", first + timedelta(seconds=30), due)
        assert len(provider.day_requests) == 2
        await tick(app, "start", due, due + timedelta(seconds=1))
        assert app.export_replay_evidence().projection.plans[0].status == "active"
        evaluation = ReplayEvaluator().evaluate(evidence=app.export_replay_evidence())
        assert evaluation.replay_hash_matches and not evaluation.findings
    finally:
        app.close()
        await model.aclose()


@pytest.mark.asyncio
async def test_role_can_reconsider_again_without_creating_activity(tmp_path, monkeypatch, build_app):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = ReconsiderHTTP(3600, again=True)
    model = DeepSeekChatModel("offline", "https://fixture.invalid", "deepseek-v4-flash",
                             thinking_enabled=False, transport=httpx.MockTransport(provider))
    app = build_app(tmp_path / "again.sqlite", model, ecology=True)
    first = NOW + timedelta(minutes=1)
    try:
        await tick(app, "first", NOW, first)
        due = first + timedelta(hours=1)
        await tick(app, "again", first, due)
        assert len(provider.day_requests) == 2
        assert not app.export_replay_evidence().projection.plans
        assert day_open_reconsideration_due(app._ledger, actor_ref=ACTOR) == due + timedelta(hours=1)
    finally:
        app.close()
        await model.aclose()


@pytest.mark.asyncio
async def test_changed_due_time_cannot_replace_the_original_role_choice(tmp_path, monkeypatch, build_app):
    from companion_daemon.world_v2.day_open_opportunity import day_open_store_for_ledger

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = ReconsiderHTTP(3600)
    model = DeepSeekChatModel("offline", "https://fixture.invalid", "deepseek-v4-flash",
                             thinking_enabled=False, transport=httpx.MockTransport(provider))
    app = build_app(tmp_path / "tampered.sqlite", model, ecology=True)
    first = NOW + timedelta(minutes=1)
    try:
        await tick(app, "choose", NOW, first)
        store = day_open_store_for_ledger(app._ledger)
        row = store.records(ACTOR)[0]
        store.save(row.model_copy(update={"reconsider_at": first + timedelta(seconds=60)}), expected=row)
        await tick(app, "forged-due", first, first + timedelta(seconds=60))
        assert len(provider.day_requests) == 1
        assert not app.export_replay_evidence().projection.plans
    finally:
        app.close()
        await model.aclose()


@pytest.mark.asyncio
async def test_invalid_reconsideration_is_technical_failure_not_scheduled_choice(tmp_path, monkeypatch, build_app):
    from companion_daemon.world_v2.day_open_opportunity import day_open_store_for_ledger

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = ReconsiderHTTP(0, again=True, malformed=True)
    model = DeepSeekChatModel("offline", "https://fixture.invalid", "deepseek-v4-flash",
                             thinking_enabled=False, transport=httpx.MockTransport(provider))
    app = build_app(tmp_path / "invalid.sqlite", model, ecology=True)
    first = NOW + timedelta(minutes=1)
    try:
        await tick(app, "invalid", NOW, first)
        assert len(provider.day_requests) == 2  # One original role and its bounded correction.
        rows = day_open_store_for_ledger(app._ledger).records(ACTOR)
        assert all(row.reconsider_at is None and row.deferred_choice_hash is None for row in rows)
        assert rows[0].attempts[-1].failure_code is not None
        assert not app.export_replay_evidence().projection.plans
    finally:
        app.close()
        await model.aclose()


@pytest.mark.asyncio
async def test_intervening_plan_does_not_erase_a_requested_reconsideration(tmp_path, monkeypatch, build_app):
    from companion_daemon.world_v2.world_turn_runtime import InboundTurn
    from test_day_open_self_directed_intent import INTENT

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = ReconsiderHTTP(3600)
    model = DeepSeekChatModel("offline", "https://fixture.invalid", "deepseek-v4-flash",
                             thinking_enabled=False, transport=httpx.MockTransport(provider))
    app = build_app(tmp_path / "intervening.sqlite", model, ecology=True)
    first = NOW + timedelta(minutes=1)
    try:
        await tick(app, "choose", NOW, first)
        provider.chat_intent = {**INTENT, "intention": "先留时间整理提纲。", "duration_seconds": 7200}
        await app.respond(InboundTurn(
            platform="test", platform_user_id="user.1", platform_message_id="new-plan",
            text="你可以安排自己的事。", observed_at=first + timedelta(seconds=10),
            trace_id="trace:new-plan",
        ))
        before = len(app.export_replay_evidence().projection.plans)
        assert before == 1
        current = app._ledger.project().logical_time
        await tick(app, "start-intervening", current, current + timedelta(seconds=1))
        await tick(app, "requested-thought", app._ledger.project().logical_time, first + timedelta(hours=1))
        assert len(provider.day_requests) == 2
        assert len(app.export_replay_evidence().projection.plans) == before + 1
        assert day_open_reconsideration_due(app._ledger, actor_ref=ACTOR) is None
    finally:
        app.close()
        await model.aclose()
