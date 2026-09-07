"""Rejected streamed carriers retain their original HTTP billing tail."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel, complete_with_timeout
from companion_daemon.world_v2.character_interior.inbound_author import (
    _InboundCharacterAuthor as InboundCharacterAuthor,
)
from companion_daemon.world_v2.expression_draft import QQ_NAPCAT_EXPRESSION_CAPABILITIES
from companion_daemon.world_v2.longitudinal_model_input_capture import (
    ModelInputCaptureTransport,
    PrivateModelInputCapture,
)
from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore
from companion_daemon.world_v2.world_turn_runtime import InboundTurn
from test_character_interior_inbound_author import _request
from test_production_turn_application import NOW, _config, _DeliveredTransport, _Identities, _Router
from world_v2_application import (
    build_sqlite_world_v2_test_application,
    compose_fixture_character_interior,
)


def _carrier(*, malformed: bool) -> str:
    inner = json.dumps(
        {
            "messages": ["不能交付的原稿" if malformed else "这一条是角色的纠正"],
            "meaning_of_this": "对方在和我说话",
            "my_state": "想回应",
            "world_claims": [],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if malformed:
        # The real failure: valid outer tool JSON, missing inner messages ']'.
        inner = inner.replace('"],"meaning_of_this"', '","meaning_of_this"', 1)
    return json.dumps({"result_kind": "reply_only", "payload_json": inner}, ensure_ascii=False)


def _frame(value: object) -> bytes:
    return ("data: " + json.dumps(value, ensure_ascii=False) + "\n\n").encode()


class _DelayedUsageStream(httpx.AsyncByteStream):
    def __init__(self, name: str, *, malformed: bool):
        self.name = name
        self.raw = _carrier(malformed=malformed)
        self.prefix_read = asyncio.Event()
        self.allow_tail = asyncio.Event()
        self.closed = asyncio.Event()
        self.eof = False

    async def __aiter__(self):
        yield _frame(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "original-tool",
                                    "type": "function",
                                    "function": {"name": self.name, "arguments": self.raw},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            }
        )
        self.prefix_read.set()
        await self.allow_tail.wait()
        yield _frame({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]})
        yield _frame(
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 30,
                    "prompt_cache_hit_tokens": 60,
                    "prompt_cache_miss_tokens": 40,
                },
            }
        )
        yield b"data: [DONE]\n\n"
        self.eof = True

    async def aclose(self):
        self.closed.set()


class _HTTPScenario:
    def __init__(self, *, malformed: bool = True):
        self.malformed = malformed
        self.requests: list[dict] = []
        self.started = asyncio.Event()
        self.stream: _DelayedUsageStream | None = None
        self.correction_before_eof = False

    def respond(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        name = body["tool_choice"]["function"]["name"]
        if len(self.requests) == 1:
            self.stream = _DelayedUsageStream(name, malformed=self.malformed)
            self.started.set()
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=self.stream,
            )
        assert self.stream is not None
        self.correction_before_eof = not self.stream.eof
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "corrected-tool",
                                    "type": "function",
                                    "function": {
                                        "name": name,
                                        "arguments": _carrier(malformed=False),
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 35,
                    "prompt_cache_hit_tokens": 70,
                    "prompt_cache_miss_tokens": 50,
                },
            },
        )


def _setup(tmp_path, monkeypatch, *, malformed=True):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    scenario = _HTTPScenario(malformed=malformed)
    capture = PrivateModelInputCapture(tmp_path / "capture.jsonl")
    ledger = tmp_path / "usage.sqlite"
    usage = WorldV2UsageStore(path=str(ledger), monthly_budget_cny=1, daily_budget_cny=1)
    model = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        usage_observer=usage.record,
        transport=ModelInputCaptureTransport(
            inner=httpx.MockTransport(scenario.respond),
            capture=capture,
            model_role="flash",
        ),
    )
    author = InboundCharacterAuthor(
        flash_model=model,
        expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
        require_explicit_authored_decision_fields=True,
    )
    return scenario, capture, ledger, usage, model, author


def _bills(path: Path):
    # Read the public durable billing contract, including its exact reservation.
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in connection.execute(
                "SELECT u.billing_state,u.error,u.prompt_tokens,u.completion_tokens,"
                "r.status AS reservation_status FROM world_v2_model_usage u "
                "JOIN world_v2_model_reservations r ON u.reservation_id=r.reservation_id "
                "ORDER BY u.id"
            )
        ]


@pytest.mark.asyncio
async def test_malformed_head_drains_billing_before_one_role_correction(tmp_path, monkeypatch):
    scenario, capture, ledger, usage, model, author = _setup(tmp_path, monkeypatch)
    transport = _DeliveredTransport()
    app = build_sqlite_world_v2_test_application(
        path=tmp_path / "world.sqlite",
        config=replace(_config(), expression_episode_mode="stream"),
        identities=_Identities(),
        router=_Router(),
        character_interior=compose_fixture_character_interior(inbound_author=author),
        transport=transport,
        now=NOW,
    )
    task = asyncio.create_task(
        app.respond(
            InboundTurn(
                platform="test",
                platform_user_id="user.1",
                platform_message_id="message:billing-tail",
                text="你想说什么？",
                observed_at=NOW,
                trace_id="trace:billing-tail",
            )
        )
    )
    try:
        await asyncio.wait_for(scenario.started.wait(), timeout=1)
        stream = scenario.stream
        assert stream is not None
        await asyncio.wait_for(stream.prefix_read.wait(), timeout=1)
        await asyncio.sleep(0.02)
        assert len(scenario.requests) == 1, "correction cancelled the unread original usage tail"
        assert not task.done(), "a malformed carrier is not an authorized expression head"
        assert transport.bodies == []
        stream.allow_tail.set()
        outcome = await asyncio.wait_for(task, timeout=2)
        await app.drain_actions_once()
        assert "这一条是角色的纠正" in json.dumps(transport.bodies, ensure_ascii=False), outcome
        assert "不能交付的原稿" not in json.dumps(transport.bodies, ensure_ascii=False)
        assert len(scenario.requests) == 2
        assert scenario.correction_before_eof is False
        assert stream.eof
        snapshots = [
            value["inner_life_snapshot"]
            for body in scenario.requests
            for message in body["messages"]
            if message["role"] == "user"
            for value in [json.loads(message["content"])]
            if "inner_life_snapshot" in value
        ]
        assert len(snapshots) == 2
        assert snapshots[0]["snapshot_id"] == snapshots[1]["snapshot_id"]
        assert "role_result_correction" not in snapshots[0]
        assert (
            "payload_json 不是一层合法 JSON"
            in snapshots[1]["role_result_correction"]["failure_detail"]
        )
        assert {body["model"] for body in scenario.requests} == {"deepseek-v4-flash"}
        bills = _bills(ledger)
        assert [(b["billing_state"], b["reservation_status"]) for b in bills] == [
            ("known", "settled"),
            ("known", "settled"),
        ]
        assert [(b["prompt_tokens"], b["completion_tokens"]) for b in bills] == [
            (100, 30),
            (120, 35),
        ]
        assert usage.budget_state()["unknown_cost_hold_cny"] == 0
        bodies = [
            r
            for r in capture.read_since()[1]
            if r["kind"] == "response_body" and r["stream_status"] != "pending"
        ]
        assert len(bodies) == 2
        assert all(r["body_complete"] for r in bodies)
    finally:
        if scenario.stream is not None:
            scenario.stream.allow_tail.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        app.close()
        await model.client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["deadline", "caller_cancel"])
async def test_malformed_head_drain_respects_cancellation_and_retains_unknown(
    tmp_path,
    monkeypatch,
    stop,
):
    scenario, capture, ledger, usage, model, author = _setup(tmp_path, monkeypatch)
    request = _request(revision=3, call="call:cancelled-malformed-http")
    operation = author.propose_stream_head(request)
    if stop == "deadline":
        # The existing public deadline owner bounds the original operation.
        operation = complete_with_timeout(operation, timeout_seconds=0.15)
    task = asyncio.create_task(operation)
    try:
        await asyncio.wait_for(scenario.started.wait(), timeout=1)
        stream = scenario.stream
        assert stream is not None
        await asyncio.wait_for(stream.prefix_read.wait(), timeout=1)
        if stop == "caller_cancel":
            task.cancel("external-caller-cancelled")
        expected_stop = (
            pytest.raises(TimeoutError, match="model call exceeded 0.15s")
            if stop == "deadline"
            else pytest.raises(asyncio.CancelledError)
        )
        with expected_stop:
            await asyncio.wait_for(task, timeout=0.5)
        await asyncio.wait_for(stream.closed.wait(), timeout=0.5)
        assert not stream.eof
        assert len(scenario.requests) == 1
        assert _bills(ledger) == [
            {
                "billing_state": "unknown",
                "error": "caller_cancelled",
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "reservation_status": "billing_unknown",
            }
        ]
        assert usage.budget_state()["unknown_cost_hold_cny"] > 0
        reopened = WorldV2UsageStore(path=str(ledger), monthly_budget_cny=1, daily_budget_cny=1)
        assert (
            reopened.budget_state()["unknown_cost_hold_cny"]
            == usage.budget_state()["unknown_cost_hold_cny"]
        )
        bodies = [
            r
            for r in capture.read_since()[1]
            if r["kind"] == "response_body" and r["stream_status"] != "pending"
        ]
        assert len(bodies) == 1
        assert bodies[0]["stream_status"] == "cancelled"
        assert bodies[0]["body_complete"] is False
        # A late peer release must not resume a cancelled role or settle its bill.
        stream.allow_tail.set()
        await asyncio.sleep(0)
        assert not stream.eof
        assert len(scenario.requests) == 1
        assert reopened.budget_state()["unknown_cost_hold_cny"] > 0
    finally:
        if scenario.stream is not None:
            scenario.stream.allow_tail.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await model.client.aclose()


@pytest.mark.asyncio
async def test_valid_http_head_remains_available_before_its_billing_tail(tmp_path, monkeypatch):
    scenario, capture, ledger, usage, model, author = _setup(tmp_path, monkeypatch, malformed=False)
    request = _request(revision=3, call="call:valid-http")
    task = asyncio.create_task(author.propose_stream_head(request))
    try:
        await asyncio.wait_for(scenario.started.wait(), timeout=1)
        stream = scenario.stream
        assert stream is not None
        await asyncio.wait_for(stream.prefix_read.wait(), timeout=1)
        head = await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
        assert "这一条是角色的纠正" in json.dumps(head.raw_proposal, ensure_ascii=False)
        assert not stream.eof
        assert not stream.closed.is_set()
        assert _bills(ledger) == []
        assert usage.budget_state()["pending_cost_cny"] > 0
        stream.allow_tail.set()
        tail = await asyncio.wait_for(author.propose_stream_tail(request), timeout=1)
        assert len(tail.physical_provider_audits) == 1
        assert tail.physical_provider_audits[0].usage_status == "provider_reported"
        assert len(scenario.requests) == 1
        assert stream.eof
        assert _bills(ledger)[0]["billing_state"] == "known"
        assert capture.health()["response_bodies_complete"] == 1
    finally:
        if scenario.stream is not None:
            scenario.stream.allow_tail.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await model.client.aclose()
