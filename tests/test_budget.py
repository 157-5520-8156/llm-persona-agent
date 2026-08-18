from datetime import UTC, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from companion_daemon.budget import (
    BudgetGate,
    UsageEstimate,
    admit_image_generation,
    image_render_estimate,
    occupancy_from_media_projection,
    ImageGenerationOccupancy,
)
from companion_daemon.db import CompanionStore, UsageEventsLedger


def test_budget_gate_blocks_soft_daily_for_automatic_calls(tmp_path: Path) -> None:
    store = CompanionStore(tmp_path / "test.sqlite")
    store.record_usage("vision", 0.95)
    gate = BudgetGate(
        store,
        monthly_budget_cny=80,
        daily_budget_cny=3,
        soft_daily_budget_cny=1,
        monthly_image_limit=20,
        monthly_vision_limit=120,
        monthly_audio_limit=60,
    )

    decision = gate.check(UsageEstimate("vision", 0.1), automatic=True)

    assert not decision.allowed
    assert decision.reason == "soft_daily_budget_requires_manual"


def test_usage_totals_are_windowed(tmp_path: Path) -> None:
    store = CompanionStore(tmp_path / "test.sqlite")
    store.record_usage("vision", 0.03)

    assert store.usage_total("day", datetime.now(UTC)) == 0.03
    assert store.usage_count("vision", "month", datetime.now(UTC)) == 1


def test_image_render_estimate_accounts_for_reference_images_and_retry_reserve() -> None:
    single = image_render_estimate(reference_count=2, quality="medium", attempts=1)
    retry_reserve = image_render_estimate(reference_count=2, quality="medium", attempts=2)

    assert single.cny > 1
    assert retry_reserve.cny == pytest.approx(single.cny * 2, abs=0.0001)


def test_model_usage_summary_groups_real_tokens_by_purpose(tmp_path: Path) -> None:
    store = CompanionStore(tmp_path / "model-usage.sqlite")
    store.record_model_usage(
        purpose="reply",
        model="deepseek-v4-flash",
        status="succeeded",
        latency_ms=420,
        prompt_tokens=100,
        completion_tokens=20,
        reasoning_tokens=0,
        cache_hit_tokens=70,
        cache_miss_tokens=30,
        total_tokens=120,
    )
    store.record_model_usage(
        purpose="reply_audit",
        model="deepseek-v4-flash",
        status="succeeded",
        latency_ms=180,
        prompt_tokens=60,
        completion_tokens=8,
        reasoning_tokens=0,
        cache_hit_tokens=40,
        cache_miss_tokens=20,
        total_tokens=68,
    )

    summary = store.model_usage_summary("day", datetime.now(UTC))

    assert summary["reply"]["calls"] == 1
    assert summary["reply"]["total_tokens"] == 120
    assert summary["reply_audit"]["total_tokens"] == 68
    assert summary["_total"]["calls"] == 2
    assert summary["_total"]["total_tokens"] == 188
    assert summary["_total"]["cache_hit_tokens"] == 110


def test_model_budget_remaining_uses_persisted_real_token_cost(tmp_path: Path) -> None:
    store = CompanionStore(tmp_path / "model-budget.sqlite")
    store.record_model_usage(
        purpose="reply",
        model="deepseek-v4-flash",
        status="succeeded",
        latency_ms=100,
        prompt_tokens=1_000_000,
        completion_tokens=0,
        cache_hit_tokens=0,
        cache_miss_tokens=1_000_000,
        total_tokens=1_000_000,
    )
    gate = BudgetGate(
        store,
        monthly_budget_cny=10,
        daily_budget_cny=2,
        soft_daily_budget_cny=1.01,
        monthly_image_limit=20,
        monthly_vision_limit=120,
        monthly_audio_limit=60,
    )

    # One million cache-miss input tokens cost USD 0.14, or CNY 1.008 at
    # the persisted report rate. This must reduce the automatic budget.
    assert 0 <= gate.remaining_model_budget_cny(automatic=True) < 0.01


def test_model_call_reservation_is_atomic_across_concurrent_budget_gates(
    tmp_path: Path,
) -> None:
    """Two concurrent turns cannot both spend the same remaining model budget."""
    path = tmp_path / "atomic-model-budget.sqlite"

    def reserve(reservation_id: str):
        gate = BudgetGate(
            CompanionStore(path),
            monthly_budget_cny=0.03,
            daily_budget_cny=0.02,
            soft_daily_budget_cny=0.02,
            monthly_image_limit=20,
            monthly_vision_limit=120,
            monthly_audio_limit=60,
        )
        return gate.reserve_model_call(
            reservation_id=reservation_id,
            estimated_cny=0.015,
            automatic=True,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        decisions = list(executor.map(reserve, ("turn-a", "turn-b")))

    assert sum(decision.allowed for decision in decisions) == 1
    assert {decision.reason for decision in decisions} == {
        "reserved",
        "daily_budget_exceeded",
    }


def test_model_call_reservation_settles_actual_usage_and_releases_failures(
    tmp_path: Path,
) -> None:
    store = CompanionStore(tmp_path / "model-reservation.sqlite")
    gate = BudgetGate(
        store,
        monthly_budget_cny=0.05,
        daily_budget_cny=0.05,
        soft_daily_budget_cny=0.05,
        monthly_image_limit=20,
        monthly_vision_limit=120,
        monthly_audio_limit=60,
    )

    reserved = gate.reserve_model_call(
        reservation_id="successful-call",
        estimated_cny=0.04,
        automatic=True,
    )
    assert reserved.allowed
    store.record_model_usage(
        purpose="reply",
        model="deepseek-v4-flash",
        status="succeeded",
        latency_ms=20,
        prompt_tokens=1_000,
        cache_miss_tokens=1_000,
        total_tokens=1_000,
        budget_reservation_id="successful-call",
    )

    # The real price is about CNY 0.001, not the CNY 0.04 preflight envelope.
    # Settlement must return the unused envelope before the next call reserves.
    assert gate.reserve_model_call(
        reservation_id="next-call",
        estimated_cny=0.04,
        automatic=True,
    ).allowed

    failed = gate.reserve_model_call(
        reservation_id="failed-call",
        estimated_cny=0.005,
        automatic=True,
    )
    assert failed.allowed
    store.record_model_usage(
        purpose="reply",
        model="deepseek-v4-flash",
        status="failed",
        latency_ms=20,
        budget_reservation_id="failed-call",
        # A provider-side validation rejection is evidence that no billable
        # completion was created. Generic failed calls intentionally default
        # to unknown billing and keep their envelope.
        billing_state="not_billed",
    )

    assert gate.reserve_model_call(
        reservation_id="after-failure",
        estimated_cny=0.005,
        automatic=True,
    ).allowed


def test_model_call_with_unknown_billing_keeps_its_envelope_after_usage_persistence_fails(
    tmp_path: Path,
) -> None:
    """An emitted provider request stays charged when its usage row is unavailable."""
    store = CompanionStore(tmp_path / "unknown-model-billing.sqlite")
    gate = BudgetGate(
        store,
        monthly_budget_cny=0.05,
        daily_budget_cny=0.05,
        soft_daily_budget_cny=0.05,
        monthly_image_limit=20,
        monthly_vision_limit=120,
        monthly_audio_limit=60,
    )

    reserved = gate.reserve_model_call(
        reservation_id="emitted-but-unpersisted",
        estimated_cny=0.04,
        automatic=True,
    )
    assert reserved.allowed
    assert gate.start_model_call("emitted-but-unpersisted")

    gate.finalize_model_call(
        "emitted-but-unpersisted",
        request_emitted=True,
        usage_persisted=False,
    )

    assert not gate.reserve_model_call(
        reservation_id="would-overrun-after-unknown-billing",
        estimated_cny=0.02,
        automatic=True,
    ).allowed


def test_expired_unstarted_reservations_release_but_started_calls_become_unknown(
    tmp_path: Path,
) -> None:
    store = CompanionStore(tmp_path / "model-reservation-lease.sqlite")
    gate = BudgetGate(
        store,
        monthly_budget_cny=0.10,
        daily_budget_cny=0.10,
        soft_daily_budget_cny=0.10,
        monthly_image_limit=20,
        monthly_vision_limit=120,
        monthly_audio_limit=60,
    )
    now = datetime(2032, 4, 3, 12, tzinfo=UTC)
    assert gate.reserve_model_call(
        reservation_id="never-started",
        estimated_cny=0.04,
        automatic=True,
        now=now,
        lease_seconds=60,
    ).allowed
    assert gate.reserve_model_call(
        reservation_id="started-before-crash",
        estimated_cny=0.04,
        automatic=True,
        now=now,
        lease_seconds=60,
    ).allowed
    assert gate.start_model_call("started-before-crash", now=now, lease_seconds=60)

    # Query-time recovery is enough after a crash: work never started returns
    # capacity, while a started request is held at its conservative envelope.
    recovered = now.replace(minute=2)
    assert not gate.reserve_model_call(
        reservation_id="new-call",
        estimated_cny=0.07,
        automatic=True,
        now=recovered,
    ).allowed
    assert gate.reserve_model_call(
        reservation_id="fits-after-unstarted-release",
        estimated_cny=0.06,
        automatic=True,
        now=recovered,
    ).allowed


def test_image_generation_admission_aligns_with_delivery_slots() -> None:
    now = datetime(2026, 8, 18, 12, tzinfo=UTC)
    open_slot = admit_image_generation(
        occupancy=ImageGenerationOccupancy(),
        logical_time=now,
    )
    after_send = admit_image_generation(
        occupancy=ImageGenerationOccupancy(
            paid_renders_today=1,
            deliveries_today=1,
            last_delivery_at=now - timedelta(minutes=30),
        ),
        logical_time=now,
    )
    daily_full = admit_image_generation(
        occupancy=ImageGenerationOccupancy(paid_renders_today=2),
        logical_time=now,
    )
    waiting_preview = admit_image_generation(
        occupancy=ImageGenerationOccupancy(undelivered_previews=1),
        logical_time=now,
    )

    assert open_slot.allowed
    assert after_send.reason == "delivery_min_gap"
    assert daily_full.reason == "daily_generation_limit"
    assert waiting_preview.reason == "undelivered_preview"


def test_occupancy_counts_in_flight_renders_and_undelivered_previews() -> None:
    now = datetime(2026, 8, 18, 12, tzinfo=UTC)
    occupancy = occupancy_from_media_projection(
        SimpleNamespace(
            actions=(
                SimpleNamespace(
                    action_id="action:render:1",
                    kind="media_render",
                    state="dispatch_started",
                    logical_time=now,
                ),
            ),
            media_artifacts=(),
            media_previews=(SimpleNamespace(plan_id="plan:waiting"),),
            media_deliveries=(),
            media_delivery_approvals=(),
        ),
        logical_time=now,
    )

    assert occupancy.in_flight_renders == 1
    assert occupancy.undelivered_previews == 1
    assert occupancy.paid_renders_today == 0


def test_budget_gate_blocks_a_second_same_day_image_inside_the_delivery_gap(
    tmp_path: Path,
) -> None:
    store = CompanionStore(tmp_path / "image-cap.sqlite")
    gate = BudgetGate(
        store,
        monthly_budget_cny=80,
        daily_budget_cny=30,
        soft_daily_budget_cny=20,
        monthly_image_limit=20,
        monthly_vision_limit=120,
        monthly_audio_limit=60,
    )
    estimate = image_render_estimate(reference_count=1, attempts=1)
    first = gate.check(estimate, automatic=True)
    gate.record(estimate, note="first")
    second = gate.check(estimate, automatic=True)

    assert first.allowed
    assert not second.allowed
    assert second.reason == "image_generation_min_gap"
    assert store.usage_count("image_generation", "day", datetime.now(UTC)) == 1


def test_usage_events_ledger_does_not_create_legacy_companion_tables(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "world-v2-only.sqlite"
    ledger = UsageEventsLedger(path)
    ledger.record_usage("image_generation", 1.25, note="stub")
    now = datetime.now(UTC)
    gate = BudgetGate(
        ledger,  # type: ignore[arg-type]
        monthly_budget_cny=80,
        daily_budget_cny=3,
        soft_daily_budget_cny=2,
        monthly_image_limit=20,
        monthly_vision_limit=120,
        monthly_audio_limit=60,
    )

    assert ledger.usage_total("day", now) == pytest.approx(1.25)
    decision = gate.check(image_render_estimate(reference_count=0), automatic=True)
    assert not decision.allowed
    conn = sqlite3.connect(path)
    try:
        tables = {row[0] for row in conn.execute("select name from sqlite_master where type='table'")}
    finally:
        conn.close()
    assert "usage_events" in tables
    assert "users" not in tables
    assert "mood_state" not in tables


def test_record_usage_recreates_missing_usage_events_table(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "missing-usage.sqlite"
    store = CompanionStore(path)
    conn = sqlite3.connect(path)
    try:
        conn.execute("drop table usage_events")
        conn.commit()
    finally:
        conn.close()

    store.record_usage("image_generation", 0.42, note="recreated")
    assert store.usage_count("image_generation", "day", datetime.now(UTC)) == 1
    assert store.usage_total("day", datetime.now(UTC)) == pytest.approx(0.42)


@pytest.mark.asyncio
async def test_stubbed_openai_image_records_usage_events_for_budget_gate(
    tmp_path: Path,
) -> None:
    import httpx

    from companion_daemon.image_generation import OpenAIImageGenerator

    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            json={
                "data": [{"b64_json": "cG5n"}],
                "usage": {
                    "input_tokens": 80,
                    "output_tokens": 1366,
                    "total_tokens": 1446,
                    "input_tokens_details": {"text_tokens": 20, "image_tokens": 60},
                },
            },
        )

    path = tmp_path / "epoch2-like.sqlite"
    ledger = UsageEventsLedger(path)
    generator = OpenAIImageGenerator(
        "test-key",
        transport=httpx.MockTransport(handler),
        spend_store=ledger,
    )
    generated = await generator.generate("prompt", output_path=tmp_path / "out.png")
    now = datetime.now(UTC)
    gate = BudgetGate(
        ledger,  # type: ignore[arg-type]
        monthly_budget_cny=100,
        daily_budget_cny=4,
        soft_daily_budget_cny=3,
        monthly_image_limit=20,
        monthly_vision_limit=120,
        monthly_audio_limit=60,
    )

    assert generated.path.read_bytes() == b"png"
    assert calls["n"] == 1
    assert ledger.usage_count("image_generation", "day", now) == 1
    assert ledger.usage_total("day", now) > 0
    blocked = gate.check(image_render_estimate(reference_count=0), automatic=True)
    assert not blocked.allowed
    assert blocked.reason in {
        "daily_image_limit_exceeded",
        "image_generation_min_gap",
        "soft_daily_budget_requires_manual",
        "daily_budget_exceeded",
    }
