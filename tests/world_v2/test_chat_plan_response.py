"""The real inbound author/grammar/acceptance path reuses the common Plan operations."""

from datetime import timedelta
import json
import httpx
import pytest
from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.world_turn_runtime import InboundTurn
from test_day_open_self_directed_intent import build_app as build_app, _DayOpenHTTP, INTENT
from test_world_stimulus_life_intent import NOW, _http_result
from test_character_interior_inbound_author import _MeteredSourceClosureReviewer, _metered_usage


class PlanReplyReviewer(_MeteredSourceClosureReviewer):
    async def complete_json_with_usage(self, messages, **kwargs):
        self.calls.append(messages)
        packet = json.loads(messages[1]["content"])
        decisions = []
        for beat in packet["visible_beats"]:
            assert beat["text"] == "我重新想过这项安排了。", (
                "This fixture only reviews its authored non-factual planning reply"
            )
            decisions.append(
                {
                    "beat_index": beat["beat_index"],
                    "verdict": "source_free",
                    "semantic_role": "private_state",
                    "subject_role": "companion",
                    "source_ref_indexes": [],
                }
            )
        return json.dumps(
            {"contract": "visible-beat-source-verdict.1", "decisions": decisions}
        ), _metered_usage(
            ref=f"usage:plan-review:{len(self.calls)}", input_tokens=4, output_tokens=2
        )


class ChatPlanRole(_DayOpenHTTP):
    def __init__(self):
        super().__init__()
        self.operation = None
        self.target = None
        self.plan_requests = []

    async def __call__(self, request):
        body = json.loads(request.content)
        if (
            self.operation
            and "inbound" in body["tool_choice"]["function"]["name"]
            and any("plan_management" in (m.get("content") or "") for m in body["messages"])
        ):
            self.plan_requests.append(body)
            response = {"operation": self.operation, "plan_id": self.target}
            if self.operation == "revise":
                response["replacement"] = {
                    **INTENT,
                    "intention": "晚一点再整理。",
                    "start_after_seconds": 600,
                }
            props = body["tools"][0]["function"]["parameters"].get("properties", {})
            if "payload_json" in props or body["tool_choice"]["function"]["name"].endswith("_core"):
                core = {
                    "messages": [] if self.operation == "end" else ["我重新想过这项安排了。"],
                    "meaning_of_this": "你问到了我的安排。",
                    "my_state": "平常。",
                    "world_claims": [],
                }
                if body["tool_choice"]["function"]["name"].endswith("_core"):
                    return _http_result(
                        body,
                        {
                            "result": {
                                "result_kind": "decision",
                                **core,
                                "extras_json": json.dumps(
                                    {"plan_response": response}, ensure_ascii=False
                                ),
                            }
                        },
                    )
                return _http_result(
                    body,
                    {
                        "result_kind": "decision",
                        "payload_json": json.dumps(
                            {**core, "plan_response": response}, ensure_ascii=False
                        ),
                    },
                )
            from test_inbound_atomic_result_v2 import _decision

            result = _decision()
            response.setdefault("replacement", None)
            result["appraisal_draft"]["plan_response"] = response
            props = body["tools"][0]["function"]["parameters"].get("properties", {})
            return _http_result(body, {"result": result} if "result" in props else result)

        return await super().__call__(request)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["continue", "revise", "end"])
@pytest.mark.parametrize("race", [False, True])
@pytest.mark.parametrize("transport", ["legacy", "slim", "native"])
async def test_inbound_can_manage_existing_day_plan_without_creating_unrelated_intention(
    tmp_path, monkeypatch, build_app, operation, race, transport
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    role = ChatPlanRole()
    reviewer = PlanReplyReviewer()
    model = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(role),
    )

    def open_app():
        return build_app(
            tmp_path / "chat-plan.sqlite",
            model,
            ecology=True,
            atomic_version=None if transport == "legacy" else "slim",
            native_slim_core=transport == "native",
            reviewer=reviewer,
        )

    app = open_app()
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
        role.operation = operation
        role.target = original.plan_id
        inbound = InboundTurn(
            platform="test",
            platform_user_id="user.1",
            platform_message_id="plan",
            text="你对刚才的安排怎么想？",
            observed_at=now,
            trace_id="chat-plan",
        )
        if race:
            from companion_daemon.world_v2.errors import ConcurrencyConflict

            commit = app._ledger.commit_at_cursor
            failures = []

            def race_after_paid_audit(events, **kwargs):
                if events[0].event_type == "WorldPlanResponseRecorded" and not failures:
                    failures.append(True)
                    raise ConcurrencyConflict("offline race after paid role audit")
                return commit(events, **kwargs)

            monkeypatch.setattr(app._ledger, "commit_at_cursor", race_after_paid_audit)
        result = await app.respond(inbound)
        if race:
            assert failures and app._ledger.project().pending_plan_responses
            calls = len(role.plan_requests)
            ids = {e.event.event_id for e in app.export_replay_evidence().events}
            app.close()
            app = open_app()
            await app.drain_background_once()
            assert len(role.plan_requests) == calls
            assert ids <= {e.event.event_id for e in app.export_replay_evidence().events}
        assert role.plan_requests, "The inbound author never received the shared Plan capability"
        p = app._ledger.project()
        receipt = [
            e
            for e in app.export_replay_evidence().events
            if e.event.event_type == "WorldPlanResponseRecorded"
        ]
        assert len(receipt) == 1, result
        if operation == "continue":
            assert p.plans == (original,)
        else:
            old = next(x for x in p.plans if x.plan_id == original.plan_id)
            assert old.status == "abandoned"
            assert len(p.plans) == (3 - 1 if operation == "revise" else 1)
            if operation == "revise":
                successor = next(x for x in p.plans if x.plan_id != original.plan_id)
                assert successor.supersedes_plan_id == original.plan_id
                from companion_daemon.world_v2.dashboard_life_intention import (
                    read_dashboard_life_intention,
                )

                assert (
                    read_dashboard_life_intention(
                        ledger=app._ledger, projection=p, plan=successor
                    ).intention
                    == "晚一点再整理。"
                )
        assert app._ledger.rebuild() == p
        calls = len(role.plan_requests)
        app.close()
        app = open_app()
        await app.respond(inbound)
        assert len(role.plan_requests) == calls
        assert app._ledger.project().plans == p.plans
    finally:
        app.close()
        await model.aclose()
