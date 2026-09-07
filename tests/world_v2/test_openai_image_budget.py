import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from companion_daemon.budget import image_render_estimate
from companion_daemon.db import UsageEventsLedger
from companion_daemon.image_generation import ImageGenerationProviderError, OpenAIImageGenerator
from companion_daemon.world_v2.model_usage_budget import BackgroundSpendCapDenied, WorldV2UsageStore


_COMPLETE_USAGE = {
    "input_tokens": 80,
    "output_tokens": 1366,
    "total_tokens": 1446,
    "input_tokens_details": {"text_tokens": 20, "image_tokens": 60},
}


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
        return httpx.Response(
            200,
            json={
                "data": [] if malformed_payload else [{"b64_json": "cG5n"}],
                "usage": _COMPLETE_USAGE,
            },
        )

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
@pytest.mark.parametrize("failure", ["timeout", "cancelled", "http_error", "http_408", "http_429"])
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
        status = {"http_error": 503, "http_408": 408, "http_429": 429}[failure]
        return httpx.Response(status, json={"error": {"code": "unavailable"}})

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
@pytest.mark.parametrize("status", [400, 401])
async def test_definite_provider_rejection_releases_image_cost_hold(
    tmp_path: Path, status: int
) -> None:
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={"error": {"code": "invalid_api_key"}})

    path = tmp_path / "shared.sqlite"
    usage = WorldV2UsageStore(path=str(path))
    ledger = UsageEventsLedger(path)
    generator = OpenAIImageGenerator(
        "test-key",
        transport=httpx.MockTransport(handler),
        spend_store=ledger,
        usage_store=usage,
    )
    with pytest.raises(ImageGenerationProviderError) as raised:
        await generator.generate("prompt", output_path=tmp_path / "out.png")

    state = usage.budget_state()
    assert raised.value.status_code == status
    assert calls == 1
    assert state["pending_cost_cny"] == 0
    assert state["unknown_cost_hold_cny"] == 0
    assert state["unresolved_billing_count"] == 0
    assert ledger.usage_count("image_generation", "day", datetime.now(UTC)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_case",
    [
        "missing_usage",
        "malformed_json",
        "malformed_usage",
        "partial_usage",
        "missing_modality",
        "inconsistent_usage",
        "negative_usage",
        "fractional_usage",
        "boolean_usage",
    ],
)
async def test_incomplete_image_bill_stays_provisional_until_same_call_is_reconciled(
    tmp_path: Path,
    response_case: str,
) -> None:
    calls = 0
    admissions: list[str] = []

    class RecordingAdmissions(WorldV2UsageStore):
        def admit_provider_call(self, **kwargs) -> str:
            token = super().admit_provider_call(**kwargs)
            admissions.append(token)
            return token

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if response_case == "malformed_json":
            return httpx.Response(200, content=b"invalid json")
        cases = {
            "missing_usage": None,
            "malformed_usage": {"output_tokens": "unreadable"},
            "partial_usage": {"output_tokens": 1366},
            "missing_modality": {"input_tokens": 80, "output_tokens": 1366, "total_tokens": 1446},
            "inconsistent_usage": {**_COMPLETE_USAGE, "total_tokens": 100},
            "negative_usage": {
                **_COMPLETE_USAGE,
                "input_tokens_details": {"text_tokens": -1, "image_tokens": 81},
            },
            "fractional_usage": {**_COMPLETE_USAGE, "input_tokens": 80.5},
            "boolean_usage": {**_COMPLETE_USAGE, "output_tokens": True},
        }
        return httpx.Response(
            200,
            json={
                "data": [{"b64_json": "cG5n"}],
                "usage": cases[response_case],
            },
        )

    path = tmp_path / "shared.sqlite"
    usage = RecordingAdmissions(path=str(path))
    ledger = UsageEventsLedger(path)
    generator = OpenAIImageGenerator(
        "test-key",
        transport=httpx.MockTransport(handler),
        spend_store=ledger,
        usage_store=usage,
    )
    if response_case == "malformed_json":
        with pytest.raises(ImageGenerationProviderError, match="invalid_response"):
            await generator.generate("prompt", output_path=tmp_path / "out.png")
    else:
        image = await generator.generate("prompt", output_path=tmp_path / "out.png")
        assert image.path.read_bytes() == b"png"

    estimate = image_render_estimate(reference_count=0).cny
    assert ledger.usage_count("image_generation", "day", datetime.now(UTC)) == 1
    assert ledger.usage_total("day", datetime.now(UTC)) == estimate
    reopened = WorldV2UsageStore(path=str(path))
    state = reopened.budget_state()
    assert state["monthly_cost_cny"] == 0
    assert state["unknown_cost_hold_cny"] == estimate
    assert state["unresolved_billing_count"] == 1

    # The later invoice corrects the existing provisional record, not another
    # image request or another paid usage row. Redelivery of that invoice is safe.
    assert len(admissions) == 1
    for _ in range(2):
        reopened.record_external_usage(
            reservation_id=admissions[0],
            kind="image_generation",
            estimated_cny=0.8,
            billing_state="known",
            note="provider bill reconciled",
        )
    assert calls == 1
    assert ledger.usage_count("image_generation", "day", datetime.now(UTC)) == 1
    assert ledger.usage_total("day", datetime.now(UTC)) == 0.8
    assert reopened.budget_state()["monthly_cost_cny"] == 0.8
    assert reopened.budget_state()["unknown_cost_hold_cny"] == 0
