import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from companion_daemon.budget import image_render_estimate
from companion_daemon.db import UsageEventsLedger
from companion_daemon.image_generation import ImageGenerationProviderError, OpenAIImageGenerator
from companion_daemon.world_v2.model_usage_budget import BackgroundSpendCapDenied, WorldV2UsageStore


@pytest.mark.asyncio
async def test_text_reservation_blocks_image_before_any_http_request(tmp_path: Path) -> None:
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"data": [{"b64_json": "cG5n"}]})

    estimate = image_render_estimate(reference_count=0).cny
    path = tmp_path / "shared.sqlite"
    usage = WorldV2UsageStore(path=str(path), monthly_budget_cny=estimate * 1.5)
    usage.admit_provider_call(
        purpose="inbound_turn",
        actor="agent:companion",
        provider="deepseek",
        model="deepseek-v4-flash",
        prompt_characters=1,
        estimated_cny=estimate * 0.75,
    )
    generator = OpenAIImageGenerator(
        "test-key",
        transport=httpx.MockTransport(handler),
        spend_store=UsageEventsLedger(path),
        usage_store=usage,
    )

    with pytest.raises(ImageGenerationProviderError) as raised:
        await generator.generate("prompt", output_path=tmp_path / "out.png")

    assert raised.value.kind == "spend_cap"
    assert "monthly_budget_exceeded" in raised.value.detail
    assert calls == 0
    assert UsageEventsLedger(path).usage_count("image_generation", "day", datetime.now(UTC)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed_payload", [False, True])
async def test_http_success_settles_one_image_cost_even_when_payload_is_invalid(
    tmp_path: Path,
    malformed_payload: bool,
) -> None:
    estimate = image_render_estimate(reference_count=0).cny
    path = tmp_path / "shared.sqlite"
    usage = WorldV2UsageStore(path=str(path), monthly_budget_cny=estimate * 1.5)

    async def handler(_request: httpx.Request) -> httpx.Response:
        assert usage.budget_state()["pending_cost_cny"] == pytest.approx(estimate)
        independent = WorldV2UsageStore(path=str(path), monthly_budget_cny=estimate * 1.5)
        with pytest.raises(BackgroundSpendCapDenied):
            independent.admit_provider_call(
                purpose="inbound_turn",
                actor="agent:companion",
                provider="deepseek",
                model="deepseek-v4-flash",
                prompt_characters=1,
                estimated_cny=estimate,
            )
        if malformed_payload:
            return httpx.Response(200, content=b"invalid json")
        return httpx.Response(200, json={"data": [{"b64_json": "cG5n"}]})

    ledger = UsageEventsLedger(path)
    generator = OpenAIImageGenerator(
        "test-key",
        transport=httpx.MockTransport(handler),
        spend_store=ledger,
        usage_store=usage,
    )
    if malformed_payload:
        with pytest.raises(ImageGenerationProviderError, match="invalid_response"):
            await generator.generate("prompt", output_path=tmp_path / "out.png")
    else:
        image = await generator.generate("prompt", output_path=tmp_path / "out.png")
        assert image.path.read_bytes() == b"png"

    state = usage.budget_state()
    amount = ledger.usage_total("day", datetime.now(UTC))
    assert amount > 0
    assert ledger.usage_count("image_generation", "day", datetime.now(UTC)) == 1
    assert state["monthly_cost_cny"] == pytest.approx(amount)
    assert state["monthly_committed_cny"] == pytest.approx(amount)
    assert state["pending_cost_cny"] == 0
    assert state["unknown_cost_hold_cny"] == 0
    # The external row is authoritative; no second model-usage image bill is emitted.
    assert state["purpose_counts"].get("image_generation", 0) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "cancelled", "http_error"])
async def test_emitted_request_without_a_confirmed_bill_retains_hold_after_restart(
    tmp_path: Path,
    failure: str,
) -> None:
    estimate = image_render_estimate(reference_count=0).cny
    path = tmp_path / "shared.sqlite"
    usage = WorldV2UsageStore(path=str(path), monthly_budget_cny=estimate * 1.5)

    async def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("no bill", request=request)
        if failure == "cancelled":
            raise asyncio.CancelledError()
        return httpx.Response(503, json={"error": {"code": "unavailable"}})

    ledger = UsageEventsLedger(path)
    generator = OpenAIImageGenerator(
        "test-key",
        transport=httpx.MockTransport(handler),
        spend_store=ledger,
        usage_store=usage,
    )
    error = asyncio.CancelledError if failure == "cancelled" else ImageGenerationProviderError
    with pytest.raises(error):
        await generator.generate("prompt", output_path=tmp_path / "out.png")

    reopened = WorldV2UsageStore(path=str(path), monthly_budget_cny=estimate * 1.5)
    state = reopened.budget_state()
    assert state["unknown_cost_hold_cny"] == pytest.approx(estimate)
    assert state["pending_cost_cny"] == 0
    assert state["unresolved_billing_count"] == 1
    assert ledger.usage_count("image_generation", "day", datetime.now(UTC)) == 0
    with pytest.raises(BackgroundSpendCapDenied):
        reopened.admit_provider_call(
            purpose="inbound_turn",
            actor="agent:companion",
            provider="deepseek",
            model="deepseek-v4-flash",
            prompt_characters=1,
            estimated_cny=estimate,
        )


@pytest.mark.asyncio
async def test_local_failure_before_emission_releases_unspent_image_reservation(
    tmp_path: Path,
) -> None:
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError("missing local reference must not reach the provider")

    path = tmp_path / "shared.sqlite"
    usage = WorldV2UsageStore(path=str(path))
    generator = OpenAIImageGenerator(
        "test-key",
        transport=httpx.MockTransport(handler),
        spend_store=UsageEventsLedger(path),
        usage_store=usage,
    )
    with pytest.raises(FileNotFoundError):
        await generator.generate(
            "prompt",
            output_path=tmp_path / "out.png",
            reference_images=[tmp_path / "missing.png"],
        )

    state = usage.budget_state()
    assert calls == 0
    assert state["pending_cost_cny"] == 0
    assert state["unknown_cost_hold_cny"] == 0
    assert state["monthly_cost_cny"] == 0


@pytest.mark.asyncio
async def test_invalid_usage_tokens_on_http_success_still_record_one_render_estimate(
    tmp_path: Path,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [{"b64_json": "cG5n"}],
                "usage": {"output_tokens": "unreadable"},
            },
        )

    path = tmp_path / "shared.sqlite"
    usage = WorldV2UsageStore(path=str(path))
    ledger = UsageEventsLedger(path)
    generator = OpenAIImageGenerator(
        "test-key",
        transport=httpx.MockTransport(handler),
        spend_store=ledger,
        usage_store=usage,
    )
    image = await generator.generate("prompt", output_path=tmp_path / "out.png")

    assert image.path.read_bytes() == b"png"
    assert ledger.usage_count("image_generation", "day", datetime.now(UTC)) == 1
    assert (
        ledger.usage_total("day", datetime.now(UTC)) == image_render_estimate(reference_count=0).cny
    )
    assert usage.budget_state()["pending_cost_cny"] == 0
