"""The production captioner shares text spend without billing image base64 as text."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sqlite3

import httpx
import pytest

from companion_daemon.world_v2.model_usage_budget import (
    BackgroundSpendCapDenied,
    WorldV2UsageStore,
)
from companion_daemon.world_v2.perception_vision_transport import (
    SQLiteDurableVisionPerceptionTransport,
)


BODY = "data:image/png;base64,cG5n"
WORLD = "world:vision-budget"


def _transport(path: Path, handler, *, budget: float = 1.0):
    return SQLiteDurableVisionPerceptionTransport(
        path,
        api_key="fixture-secret-key",
        base_url="https://fixture.invalid/v1",
        model="qwen3-vl-flash",
        transport=httpx.MockTransport(handler),
        usage_store=WorldV2UsageStore(path=str(path), monthly_budget_cny=budget),
        world_id=WORLD,
    )


async def _analyze(transport, *, body=BODY, key="action-key:vision-1"):
    return await transport.analyze(
        analysis_kind="vision",
        input_ref="attachment:fixture-1",
        input_hash="sha256:" + "a" * 64,
        body=body,
        idempotency_key=key,
    )


def _rows(path: Path, table: str):
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]


@pytest.mark.asyncio
async def test_caption_reserves_then_records_usage_and_restart_lookup_is_free(tmp_path):
    path = tmp_path / "world.sqlite"
    calls = []

    def handler(request):
        calls.append(request)
        (reservation,) = _rows(path, "world_v2_model_reservations")
        assert reservation["status"] == "pending"
        assert reservation["purpose"] == "qq_attachment_perception"
        assert reservation["actor"] == "agent:companion"
        assert reservation["world_id"] == WORLD
        assert reservation["turn_id"] == "action-key:vision-1"
        assert reservation["estimated_cny"] > 0
        return httpx.Response(
            200,
            json={
                "id": "caption:fixture",
                "choices": [{"message": {"content": "窗台有一只猫。"}}],
                "usage": {
                    "prompt_tokens": 9000,
                    "completion_tokens": 30,
                    "total_tokens": 9030,
                    "prompt_tokens_details": {"cached_tokens": 1000},
                },
            },
        )

    transport = _transport(path, handler)
    try:
        result = await _analyze(transport)
        assert await _analyze(transport) == result
        assert result[3] == 0  # Existing Action units are not CNY.
    finally:
        transport.close()
    reopened = _transport(path, handler)
    try:
        assert await reopened.lookup(idempotency_key="action-key:vision-1") == result
        assert await _analyze(reopened) == result
    finally:
        reopened.close()
    assert len(calls) == 1
    (usage,) = _rows(path, "world_v2_model_usage")
    assert usage["billing_state"] == "known"
    assert usage["status"] == "succeeded"
    assert usage["provider"] == "dashscope:vision"
    assert usage["prompt_tokens"] == 9000
    assert usage["completion_tokens"] == 30
    assert usage["cache_hit_tokens"] == 1000
    assert usage["cache_miss_tokens"] == 8000
    assert usage["cost_cny"] > 0
    assert "fixture-secret-key" not in str(usage)
    assert BODY not in str(usage)


@pytest.mark.asyncio
async def test_caption_budget_rejection_happens_before_http(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        raise AssertionError("budget denied caption must not emit a request")

    path = tmp_path / "world.sqlite"
    transport = _transport(path, handler, budget=0.0000001)
    try:
        with pytest.raises(BackgroundSpendCapDenied):
            await _analyze(transport)
    finally:
        transport.close()
    assert calls == []
    assert _rows(path, "world_v2_model_reservations") == []
    (denied,) = _rows(path, "world_v2_model_usage")
    assert denied["status"] == "budget_denied"


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [httpx.ReadTimeout, asyncio.CancelledError])
async def test_caption_post_emission_failure_retains_unknown_cost(tmp_path, error):
    calls = []

    def handler(request):
        calls.append(request)
        raise error("fixture failure")

    path = tmp_path / "world.sqlite"
    transport = _transport(path, handler)
    try:
        with pytest.raises(error):
            await _analyze(transport)
    finally:
        transport.close()
    assert len(calls) == 1
    (usage,) = _rows(path, "world_v2_model_usage")
    assert usage["status"] == "failed"
    assert usage["billing_state"] == "unknown"
    (reservation,) = _rows(path, "world_v2_model_reservations")
    assert reservation["status"] == "billing_unknown"
    assert reservation["estimated_cny"] > 0


@pytest.mark.asyncio
async def test_caption_local_client_failure_is_not_billed(tmp_path, monkeypatch):
    import companion_daemon.world_v2.perception_vision_transport as module

    path = tmp_path / "world.sqlite"
    transport = _transport(path, lambda _: None)

    def reject_client(**kwargs):
        raise ValueError("fixture invalid client settings")

    monkeypatch.setattr(module.httpx, "AsyncClient", reject_client)
    try:
        with pytest.raises(ValueError, match="fixture invalid"):
            await _analyze(transport)
    finally:
        transport.close()
    (usage,) = _rows(path, "world_v2_model_usage")
    assert usage["billing_state"] == "not_billed"
    (reservation,) = _rows(path, "world_v2_model_reservations")
    assert reservation["status"] == "settled"


@pytest.mark.asyncio
async def test_missing_usage_is_unknown_and_base64_length_does_not_drive_reserve(tmp_path):
    path = tmp_path / "world.sqlite"

    def handler(_):
        return httpx.Response(200, json={"choices": [{"message": {"content": "一张图。"}}]})

    transport = _transport(path, handler)
    try:
        await _analyze(transport)
        await _analyze(transport, body=BODY + "A" * 100000, key="action-key:vision-2")
    finally:
        transport.close()
    reservations = _rows(path, "world_v2_model_reservations")
    assert len(reservations) == 2
    assert reservations[0]["estimated_cny"] == reservations[1]["estimated_cny"]
    assert all(row["status"] == "billing_unknown" for row in reservations)
    assert all(row["billing_state"] == "unknown" for row in _rows(path, "world_v2_model_usage"))


@pytest.mark.asyncio
async def test_caption_admission_sees_existing_text_reservation(tmp_path):
    path = tmp_path / "world.sqlite"
    store = WorldV2UsageStore(path=str(path), monthly_budget_cny=1.0)
    store.admit_provider_call(
        purpose="inbound_turn",
        actor="agent:companion",
        provider="fixture",
        model="deepseek-chat",
        prompt_characters=1,
        estimated_cny=1.0,
        world_id=WORLD,
        turn_id="turn:text",
    )
    calls = []

    def handler(request):
        calls.append(request)
        raise AssertionError("text reservation already holds all shared capacity")

    transport = _transport(path, handler)
    try:
        with pytest.raises(BackgroundSpendCapDenied):
            await _analyze(transport)
    finally:
        transport.close()
    assert calls == []
    reservations = _rows(path, "world_v2_model_reservations")
    assert len(reservations) == 1
    assert reservations[0]["turn_id"] == "turn:text"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 400])
async def test_caption_failure_distinguishes_known_bill_from_provider_rejection(tmp_path, status):
    path = tmp_path / "world.sqlite"

    def handler(_):
        return httpx.Response(
            status,
            json={
                "choices": [],
                "usage": {"prompt_tokens": 9000, "completion_tokens": 30},
            },
        )

    transport = _transport(path, handler)
    try:
        with pytest.raises(ValueError if status == 200 else httpx.HTTPStatusError):
            await _analyze(transport)
    finally:
        transport.close()
    (usage,) = _rows(path, "world_v2_model_usage")
    assert usage["status"] == "failed"
    assert usage["billing_state"] == ("known" if status == 200 else "not_billed")
    assert usage["prompt_tokens"] == (9000 if status == 200 else 0)
