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
from test_world_stimulus_life_intent import ACTOR, NOW, _build, _http_result


INTENT = {
    "execution_scope": "self_directed",
    "intention": "想花几分钟给自己的想法整理一个顺序。",
    "start_after_seconds": 0,
    "duration_seconds": 300,
    "importance_bp": 4200,
}


class _DayOpenHTTP:
    def __init__(self):
        self.requests = []

    async def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        material = json.loads(body["messages"][-1]["content"])
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
                        "life_intent": INTENT,
                    },
                },
            },
        )


@pytest.mark.asyncio
async def test_empty_life_clock_offers_character_first_self_directed_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = _DayOpenHTTP()
    model = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(provider),
    )
    app = _build(tmp_path / "day-open.sqlite", model, ecology=True)
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
