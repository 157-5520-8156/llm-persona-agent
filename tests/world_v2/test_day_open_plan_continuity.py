"""Retaining an existing agenda after one completion must not require another Plan."""

from datetime import timedelta
import json
import httpx
import pytest
from companion_daemon.llm import DeepSeekChatModel
from test_day_open_self_directed_intent import build_app as build_app, _DayOpenHTTP, INTENT
from test_world_stimulus_life_intent import NOW, _http_result


class RetainingRole(_DayOpenHTTP):
    def __init__(self, *, foreign=False, independent=False, operation="continue"):
        super().__init__()
        self.agenda = [
            INTENT,
            {**INTENT, "intention": "午后留一点时间整理东西。", "start_after_seconds": 3600},
        ]
        self.retained = []
        self.foreign = foreign
        self.independent = independent
        self.operation = operation

    async def __call__(self, request):
        body = json.loads(request.content)
        for message in body["messages"]:
            try:
                material = json.loads(message.get("content") or "")
            except (ValueError, TypeError):
                continue
            cap = material.get("capability_manifest", {})
            if not cap.get("payload", {}).get("self_directed_intent") or not self.day_requests:
                continue
            offered = cap["payload"].get("existing_plans") or cap["payload"].get(
                "plan_management", {}
            ).get("plans")
            assert offered, (
                "Completion planning hides existing future intentions and offers only another new agenda"
            )
            assert "manage_plan" in json.dumps(body), (
                "The role has no explicit existing-agenda choice"
            )
            self.day_requests.append(body)
            self.retained.append(offered[0]["plan_id"])
            return _http_result(
                body,
                {
                    "status": "decision",
                    "summary": "原来的安排依然是我的打算。",
                    "attended_source_refs": cap["source_refs"],
                    "recall_query": None,
                    "proposals": [],
                    "decision": {
                        "source_refs": cap["source_refs"],
                        "payload": {"decision": "self_directed_agenda", "life_intents": self.agenda}
                        if self.independent
                        else {
                            "decision": "manage_plan",
                            "plan_response": {
                                "operation": self.operation,
                                "plan_id": "plan:foreign" if self.foreign else self.retained[-1],
                                **(
                                    {
                                        "replacement": {
                                            **INTENT,
                                            "intention": "改到下午再整理。",
                                            "start_after_seconds": 3600,
                                        }
                                    }
                                    if self.operation == "revise"
                                    else {}
                                ),
                            },
                        },
                    },
                },
            )
        return await super().__call__(request)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "foreign,independent,crash,operation",
    [
        (False, False, False, "continue"),
        (True, False, False, "continue"),
        (False, True, False, "continue"),
        (False, False, True, "continue"),
        (False, False, False, "revise"),
        (False, False, False, "end"),
        (False, False, True, "revise"),
    ],
)
async def test_completion_can_retain_exact_existing_future_plan_without_new_agenda(
    tmp_path, monkeypatch, build_app, foreign, independent, crash, operation
):
    from companion_daemon.world_v2.day_open_opportunity import (
        DayOpenOpportunityStore,
        day_open_store_for_ledger,
    )

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    role = RetainingRole(foreign=foreign, independent=independent, operation=operation)
    model = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(role),
    )
    app = build_app(tmp_path / "retaining.sqlite", model, ecology=True)
    save = DayOpenOpportunityStore.save
    interruptions = []

    def interrupted_save(self, row, **kwargs):
        if crash and row.terminal_reason == "role_plan_response" and not interruptions:
            interruptions.append(True)
            raise RuntimeError("offline crash after paid continuation before journal receipt")
        return save(self, row, **kwargs)

    monkeypatch.setattr(DayOpenOpportunityStore, "save", interrupted_save)
    previous = NOW
    try:
        for minute in (1, 2, 6, 7):
            role.lifecycle_choice = (
                "complete" if minute == 6 else "no_op" if minute == 7 else "start"
            )
            now = NOW + timedelta(minutes=minute)
            await app.tick(
                tick_id=f"retain-{minute}",
                logical_time_from=previous,
                logical_time_to=now,
                observed_at=now,
                trace_id=f"retain-{minute}",
                causation_id=f"retain-{minute}",
                correlation_id="retain",
                reason="test_clock",
            )
            previous = now
        assert role.retained, "The original completion-to-role path did not offer continuity"
        projection = app.export_replay_evidence().projection
        assert len(projection.plans) == (4 if independent else 3 if operation == "revise" else 2)
        assert sum(p.status == "completed" for p in projection.plans) == 1
        remaining = [p for p in projection.plans if p.status in {"planned", "active", "paused"}]
        assert len(remaining) == (0 if operation == "end" else 3 if independent else 1)
        if operation == "revise":
            assert remaining[0].supersedes_plan_id == role.retained[-1]
            from companion_daemon.world_v2.chat_life_plan_consideration import pending_opportunities

            assert any(
                o.plan_id == remaining[0].plan_id
                for o in pending_opportunities(
                    app._ledger, owner_actor_ref=remaining[0].owner_actor_ref
                )
            )
            from companion_daemon.world_v2.activity_continuation_source import (
                latest_completion_source,
            )

            physical_ending = latest_completion_source(
                projection,
                actor_ref=remaining[0].owner_actor_ref,
                include_abandoned=True,
                allow_existing_plans=True,
            )
            assert physical_ending.plan_id != role.retained[-1], (
                "Administrative revision caused another completion thought"
            )
        for current in remaining:
            assert current.entity_revision == 1
        assert app._ledger.rebuild() == app._ledger.project()
        rows = day_open_store_for_ledger(app._ledger).records("actor:companion")
        endings = [r for r in rows if r.completion_source is not None]
        if foreign:
            assert endings[-1].attempts[-1].failure_code
        elif independent:
            assert endings[-1].terminal_reason == "plan_accepted"
        elif not crash:
            assert endings[-1].terminal_reason == "role_plan_response"
        if not foreign and not independent:
            if crash:
                assert interruptions and not endings[-1].terminal
            before = projection.plans
            calls = len(role.day_requests)
            app.close()
            app = build_app(tmp_path / "retaining.sqlite", model, ecology=True)
            now = NOW + timedelta(minutes=8)
            await app.tick(
                tick_id="retaining-restart",
                logical_time_from=previous,
                logical_time_to=now,
                observed_at=now,
                trace_id="restart",
                causation_id="restart",
                correlation_id="retain",
                reason="test_clock",
            )
            assert len(role.day_requests) == calls
            assert app._ledger.project().plans == before
            ending = next(
                r
                for r in day_open_store_for_ledger(app._ledger).records("actor:companion")
                if r.completion_source is not None
            )
            assert ending.terminal_reason == "role_plan_response"
            assert app._ledger.rebuild() == app._ledger.project()
    finally:
        app.close()
        await model.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('characters', [90, 480])
async def test_completion_with_existing_plans_keeps_prior_intention_in_bounded_note(
    tmp_path, monkeypatch, build_app, characters
):
    original = RetainingRole.__init__
    roles = []
    text = '想' * characters

    def with_prior(self, **kwargs):
        original(self, **kwargs)
        self.agenda[0] = {**self.agenda[0], 'intention': text}
        roles.append(self)

    monkeypatch.setattr(RetainingRole, '__init__', with_prior)
    await test_completion_can_retain_exact_existing_future_plan_without_new_agenda(
        tmp_path, monkeypatch, build_app, foreign=False, independent=False,
        crash=False, operation='continue',
    )
    completed = json.loads(roles[0].day_requests[1]['messages'][1]['content'])
    note = completed['inner_turn']['context_note']
    assert text in note and len(note) <= 1024
    assert completed['capability_manifest']['payload']['plan_management']['plans']
