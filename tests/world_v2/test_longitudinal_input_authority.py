"""Exercise production prompt compilation at the actual HTTP body boundary."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from companion_daemon.world_v2.longitudinal_fixture_model import LongitudinalFixtureModel


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_fact_source", [False, True], ids=["routine_input", "fact_source"])
async def test_real_host_http_input_and_authored_current_life_sources(
    tmp_path, monkeypatch, invalid_fact_source
):
    # Real host, compiler and DeepSeek adapter; only the HTTP peer is synthetic.
    # Invalid source declarations must never authorize character speech.
    fixture = LongitudinalFixtureModel()
    bodies = []

    async def respond(request):
        body = json.loads(request.content)
        bodies.append(body)
        raw = await fixture.complete_json(
            body["messages"], tools=body.get("tools"), tool_choice=body.get("tool_choice")
        )
        carrier = json.loads(raw)
        if invalid_fact_source and "payload_json" in carrier:
            authored = json.loads(carrier["payload_json"])
            authored["messages"] = ["我在图书馆看书"]
            authored["world_claims"] = [
                {
                    "claim_text": "我在图书馆看书",
                    "scope": "current_world",
                    "source_refs": ["event:never-accepted-library-activity"],
                }
            ]
            carrier["payload_json"] = json.dumps(authored, ensure_ascii=False)
            raw = json.dumps(carrier, ensure_ascii=False)
        function = body.get("tool_choice", {}).get("function", {}).get("name")
        value = (
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "fixture-tool",
                        "type": "function",
                        "function": {"name": function, "arguments": raw},
                    }
                ]
            }
            if function
            else {"content": raw}
        )
        usage = {"prompt_tokens": 1000, "completion_tokens": 100}
        if body.get("stream"):
            chunks = [
                {"choices": [{"delta": value}]},
                {"choices": [], "usage": usage},
            ]
            data = "".join("data: " + json.dumps(x) + "\n\n" for x in chunks)
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=data + "data: [DONE]\n\n",
            )
        return httpx.Response(200, json={"choices": [{"message": value}], "usage": usage})

    monkeypatch.setenv("DEEPSEEK_DEBUG_API_KEY", "fixture-key-never-transmitted")
    monkeypatch.setenv("DEEPSEEK_CHARACTER_THINKING_ENABLED", "false")
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(respond))
    script = Path(__file__).parents[2] / "scripts/run_world_v2_longitudinal_audit.py"
    spec = importlib.util.spec_from_file_location("authority_journey_cli", script)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    scenario = tmp_path / "scenario.json"
    scenario.write_text(
        json.dumps(
            {
                "scenario_id": "routine-current-authority",
                "started_at": "2026-09-08T10:00:00+08:00",
                "duration_minutes": 3,
                "restart_minutes": [],
                "turns": [{"id": "current", "at_minutes": 0, "text": "你今天怎么样？"}],
            }
        )
    )
    output = tmp_path / "journey"
    result = await cli.run(
        cli.parse_options(
            [
                "--scenario",
                str(scenario),
                "--output",
                str(output),
                "--model-mode",
                "real-provider",
                "--allow-real-provider",
                "--max-cost-cny",
                "0.5",
                "--max-wall-seconds",
                "30",
            ]
        )
    )
    if invalid_fact_source:
        timeline = [json.loads(line) for line in (output / "timeline.jsonl").read_text().splitlines()]
        assert not [
            record
            for row in timeline
            for record in row["deliveries"]
            if record["text"] == "我在图书馆看书"
        ]
        events = [json.loads(line) for line in (output / "evidence.jsonl").read_text().splitlines()]
        assert not [event for event in events if event["event_type"] == "ActionAuthorized"]
        # An explicitly labeled service notice is separate from character speech.
        assert result["model_failures"]
    else:
        assert result["completed"], result["stop_reason"]
    requests = [
        json.loads(line)
        for line in (output / "model-inputs.jsonl").read_text().splitlines()
        if json.loads(line)["kind"] == "request"
    ]
    assert len(requests) == len(bodies) > 0
    assert all(
        json.loads(record["model_content_json"]) == body
        for record, body in zip(requests, bodies, strict=True)
    )
    inputs = []
    for body in bodies:
        for message in body["messages"]:
            if message["role"] != "user":
                continue
            payload = json.loads(message["content"])
            if "inner_life_snapshot" in payload and "current_trigger_message" in payload:
                inputs.append(payload)
    assert inputs
    if not invalid_fact_source:
        assert len(inputs) == 1
    materials = inputs[0]["inner_life_snapshot"]["materials"]
    assert "图书馆看书" in materials["day_sheet"]  # Habit remains available to the character.
    assert "（现在）" not in materials["day_sheet"]
    assert "此刻窗口是" not in materials["day_sheet"]
    assert "天气：" not in materials["day_sheet"]
    assert "lived_moment" not in materials
    assert materials["recent_self_experiences"] == {"availability": "unavailable"}
    assert inputs[0]["expression_hard_boundaries"]["world_claim_source_refs"]["past_world"] == []
