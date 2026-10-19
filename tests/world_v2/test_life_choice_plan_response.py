"""Reviewed Life opportunities can manage an existing Plan through the common authority."""

from datetime import timedelta
import json
import httpx
import pytest
from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from test_day_open_self_directed_intent import build_app as build_app, _DayOpenHTTP, INTENT
from test_world_stimulus_life_intent import NOW, _http_result
import test_life_development_runtime as life_fixture


class ActualLifeRole(_DayOpenHTTP):
    def __init__(self):
        super().__init__()
        self.operation = None
        self.target = None
        self.life_requests = []

    async def __call__(self, request):
        body = json.loads(request.content)
        for message in body["messages"]:
            try:
                material = json.loads(message.get("content") or "")
            except (ValueError, TypeError):
                continue
            if material.get("inner_turn", {}).get("purpose") != "life_development_choice":
                continue
            self.life_requests.append(body)
            cap = material["capability_manifest"]
            assert cap["payload"].get("plan_management"), "Life opportunity hides existing Plans"
            choice = {"operation": self.operation, "plan_id": self.target, "replacement": None}
            if self.operation == "revise":
                choice["replacement"] = {
                    **INTENT,
                    "intention": "等下午再整理。",
                    "start_after_seconds": 3600,
                }
            value = {
                "status": "decision",
                "summary": "这次机会暂不去，原来的安排另作选择。",
                "attended_source_refs": cap["source_refs"],
                "recall_query": None,
                "proposals": [],
                "decision": {
                    "source_refs": cap["source_refs"],
                    "payload": {"completion": {"decision": "no_op", "plan_response": choice}},
                },
            }
            props = body["tools"][0]["function"]["parameters"].get("properties", {})
            return _http_result(body, {"result": value} if "result" in props else value)
        return await super().__call__(request)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["continue", "revise", "end"])
@pytest.mark.parametrize("crash", [False, True])
async def test_reviewed_life_choice_reuses_accepted_plan_without_new_world_acceptance(
    tmp_path, monkeypatch, build_app, operation, crash
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    seed = ActualLifeRole()
    model = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(seed),
    )
    path = tmp_path / "life-plan.sqlite"
    app = build_app(path, model, ecology=True)
    store = None
    try:
        now = NOW + timedelta(minutes=1)
        await app.tick(
            tick_id="seed",
            logical_time_from=NOW,
            logical_time_to=now,
            observed_at=now,
            trace_id="seed",
            causation_id="seed",
            correlation_id="seed",
            reason="test_clock",
        )
        original = app._ledger.project().plans[0]
        wake = app._ledger.lookup_event_commit("event:trigger:clock:seed")[0]
        monkeypatch.setattr(life_fixture, "NOW", now)
        draft = life_fixture._novel_book_exchange_draft(wake=wake)
        seed.operation = operation
        seed.target = original.plan_id
        character = app._character_interior
        author = life_fixture._SequenceModel(model="world-fixture", outputs=(json.dumps(draft),))
        store = SQLiteImmutableLifeContentStore(path=str(path), world_id=app._ledger.world_id)
        runtime, _ = life_fixture._runtime(
            ledger=app._ledger,
            wake=wake,
            world_author=author,
            character_interior=character,
            store=store,
        )
        if crash:
            commit = app._ledger.commit_at_cursor
            failed = []

            def fail_after_role_audit(events, **kwargs):
                if (
                    events[0].event_type == "ProposalRecorded"
                    and events[0].payload().get("proposal_kind") == "life_development"
                    and not failed
                ):
                    failed.append(True)
                    raise RuntimeError(
                        "offline crash after accepted role operation before offer metadata"
                    )
                return commit(events, **kwargs)

            monkeypatch.setattr(app._ledger, "commit_at_cursor", fail_after_role_audit)
            with pytest.raises(RuntimeError, match="offline crash"):
                await runtime.advance_once(
                    wake_event_ref=wake.event_id, trace_id="life-plan", correlation_id="life-plan"
                )
            assert failed and app._ledger.project().pending_plan_responses
            before = (author.calls, len(seed.life_requests))
            app.close()
            store.close()
            app = build_app(path, model, ecology=True)
            store = SQLiteImmutableLifeContentStore(path=str(path), world_id=app._ledger.world_id)
            runtime, _ = life_fixture._runtime(
                ledger=app._ledger,
                wake=wake,
                world_author=author,
                character_interior=app._character_interior,
                store=store,
            )
        result = await runtime.advance_once(
            wake_event_ref=wake.event_id, trace_id="life-plan", correlation_id="life-plan"
        )
        if crash:
            assert (author.calls, len(seed.life_requests)) == before
        assert result.status == "no_op", result
        p = app._ledger.project()
        receipts = [
            e.event
            for e in app.export_replay_evidence().events
            if e.event.event_type == "WorldPlanResponseRecorded"
        ]
        assert len(receipts) == 1, result
        if operation == "continue":
            assert p.plans == (original,)
        else:
            assert next(x for x in p.plans if x.plan_id == original.plan_id).status == "abandoned"
            assert len(p.plans) == (2 if operation == "revise" else 1)
        assert app._ledger.rebuild() == p
        calls = (author.calls, len(seed.life_requests))
        second = await runtime.advance_once(
            wake_event_ref=wake.event_id, trace_id="retry", correlation_id="life-plan"
        )
        assert second.status == "no_op", second
        assert (author.calls, len(seed.life_requests)) == calls
        assert app._ledger.project().plans == p.plans
    finally:
        app.close()
        if store:
            store.close()
        await model.aclose()
