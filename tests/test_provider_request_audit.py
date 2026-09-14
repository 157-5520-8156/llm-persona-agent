"""Native reservations join private captures without changing provider input."""

import asyncio
import json
import sqlite3

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel, ProviderCircuitBreaker, model_call_scope
from companion_daemon.provider_request_audit import (
    captured_usage_correlation,
    request_usage_extensions,
)
from companion_daemon.world_v2.longitudinal_model_input_capture import (
    ModelInputCaptureTransport,
    PrivateModelInputCapture,
)
from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore


def _rows(path):
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute("SELECT * FROM world_v2_model_usage ORDER BY id")]


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_concurrent_calls_keep_reservation_body_and_bill_correlated(tmp_path, streaming):
    database = tmp_path / "usage.sqlite"
    store = WorldV2UsageStore(path=str(database), monthly_budget_cny=1)
    capture = PrivateModelInputCapture(tmp_path / "model-inputs.jsonl")
    seen = []
    both_entered = asyncio.Event()
    second_returned = asyncio.Event()

    async def respond(request):
        body = json.loads(request.content)
        name = body["messages"][0]["content"]
        correlation = captured_usage_correlation(request.extensions)
        assert correlation["usage_association"] == "client_declared"
        reservation = correlation["usage_reservation_id"]
        assert reservation not in request.content.decode()
        assert reservation not in str(request.headers) and reservation not in str(request.url)
        seen.append((name, reservation))
        if len(seen) == 2:
            both_entered.set()
        await both_entered.wait()
        if name == "first":
            await second_returned.wait()
        else:
            second_returned.set()
        tokens = 101 if name == "first" else 202
        return httpx.Response(
            200,
            json={
                "id": "fixture-" + name,
                "choices": [{"message": {"content": "{}"}}],
                "usage": {
                    "prompt_tokens": tokens,
                    "completion_tokens": 2,
                    "total_tokens": tokens + 2,
                },
            },
        )

    model = DeepSeekChatModel(
        "fixture-private-key",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        max_completion_tokens=128,
        usage_observer=store.record,
        circuit_breaker=ProviderCircuitBreaker(),
        transport=ModelInputCaptureTransport(
            inner=httpx.MockTransport(respond), capture=capture, model_role="fixture"
        ),
    )

    async def call(name):
        with model_call_scope("inbound_turn"):
            messages = [{"role": "user", "content": name}]
            if streaming:
                return await model.complete_json_stream_with_usage(messages, temperature=0.7)
            return await model.complete_json_with_usage(messages, temperature=0.7)

    try:
        await asyncio.wait_for(asyncio.gather(call("first"), call("second")), timeout=3)
    finally:
        await model.aclose()
    bills = {row["reservation_id"]: row for row in _rows(database)}
    assert len(bills) == 2 and len({r for _, r in seen}) == 2
    _, records = capture.read_since()
    requests = [r for r in records if r["kind"] == "request"]
    assert len(requests) == 2
    for row in requests:
        name = json.loads(row["model_content_json"])["messages"][0]["content"]
        bill = bills[row["usage_reservation_id"]]
        assert bill["billing_state"] == "known"
        assert bill["prompt_tokens"] == (101 if name == "first" else 202)
        same_call = [r for r in records if r["capture_id"] == row["capture_id"]]
        assert all(r["usage_reservation_id"] == row["usage_reservation_id"] for r in same_call)
        assert all(r["audit_association"] == "unverified" for r in same_call)
        assert all(r["usage_association"] == "client_declared" for r in same_call)
        expected = model.request_payload(
            [{"role": "user", "content": name}], temperature=0.7, json_object=True
        )
        if streaming:
            expected.update(stream=True, stream_options={"include_usage": True})
        assert json.loads(row["model_content_json"]) == expected
    assert "fixture-private-key" not in capture.path.read_text()
    assert store.budget_state()["unknown_cost_hold_cny"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_cancellation_retains_its_unknown_hold_and_capture_identity(tmp_path, streaming):
    database = tmp_path / "usage.sqlite"
    store = WorldV2UsageStore(path=str(database), monthly_budget_cny=1)
    capture = PrivateModelInputCapture(tmp_path / "model-inputs.jsonl")
    entered = asyncio.Event()

    async def respond(request):
        entered.set()
        await asyncio.Event().wait()

    model = DeepSeekChatModel(
        "fixture-key",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        max_completion_tokens=128,
        usage_observer=store.record,
        circuit_breaker=ProviderCircuitBreaker(),
        transport=ModelInputCaptureTransport(
            inner=httpx.MockTransport(respond), capture=capture, model_role="fixture"
        ),
    )

    async def call():
        with model_call_scope("inbound_turn"):
            if streaming:
                return await model.complete_json_stream_with_usage(
                    [{"role": "user", "content": "hi"}]
                )
            return await model.complete_json_with_usage([{"role": "user", "content": "hi"}])

    task = asyncio.create_task(call())
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        task.cancel()
        await model.aclose()
    bills = _rows(database)
    assert len(bills) == 1 and bills[0]["billing_state"] == "unknown"
    _, records = capture.read_since()
    assert records[-1]["status"] == "cancelled"
    assert all(r["usage_reservation_id"] == bills[0]["reservation_id"] for r in records)
    assert store.budget_state()["unknown_cost_hold_cny"] > 0


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "fixture-private-key",
        {"Authorization": "secret"},
        "reservation:short",
        "reservation:" + "A" * 32,
        "reservation:" + "0" * 33,
    ],
)
def test_untrusted_extension_values_are_not_captured(value):
    assert request_usage_extensions(value) == {}
    assert (
        captured_usage_correlation({"companion.usage_reservation_id": value, "unknown": "secret"})
        == {}
    )
