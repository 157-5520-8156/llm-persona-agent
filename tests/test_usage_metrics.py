from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from companion_daemon.db import CompanionStore
from companion_daemon.usage_metrics import (
    DEEPSEEK_V4_FLASH_OFFPEAK_PRICE,
    DEEPSEEK_V4_FLASH_PEAK_PRICE,
    DEEPSEEK_V4_FLASH_PRICE,
    UNPRICED_MODEL_CONSERVATIVE_PRICE,
    estimate_legacy_flat_cny,
    estimate_model_cost,
    estimate_model_cost_usd,
    estimate_routed_model_reserve_cny,
    is_deepseek_peak,
    parse_usage_datetime,
    resolve_model_price,
)


_BEIJING = ZoneInfo("Asia/Shanghai")
_PRE_HIKE = datetime(2026, 8, 16, 12, 0, tzinfo=_BEIJING)
_PEAK = datetime(2026, 8, 19, 10, 30, tzinfo=_BEIJING)  # Beijing 10:30
_OFFPEAK = datetime(2026, 8, 19, 20, 0, tzinfo=_BEIJING)  # Beijing 20:00
_Z_PEAK = "2026-08-19T02:30:00Z"  # UTC 02:30 = Beijing 10:30 peak
_OFFSET_PEAK = "2026-08-19T10:30:00+08:00"


def test_v4_pro_historical_july_row_is_unchanged() -> None:
    cost, version = estimate_model_cost_usd(
        model="deepseek-v4-pro",
        prompt_tokens=3_000,
        completion_tokens=500,
        cache_hit_tokens=1_000,
        cache_miss_tokens=2_000,
        at=_PRE_HIKE,
    )

    assert version == "deepseek-2026-07-13"
    assert cost == pytest.approx(0.001308625)


def test_v4_flash_peak_uses_official_cny_not_usd_times_7_2() -> None:
    priced = estimate_model_cost(
        model="deepseek-v4-flash",
        prompt_tokens=1_000_000,
        completion_tokens=0,
        cache_hit_tokens=0,
        cache_miss_tokens=1_000_000,
        at=_PEAK,
    )
    off = estimate_model_cost(
        model="deepseek-v4-flash",
        prompt_tokens=1_000_000,
        completion_tokens=0,
        cache_hit_tokens=0,
        cache_miss_tokens=1_000_000,
        at=_OFFPEAK,
    )

    assert priced.pricing_version == "deepseek-2026-08-17-peak"
    assert priced.window == "peak"
    assert priced.cny == pytest.approx(3.0)
    assert priced.usd == pytest.approx(0.44)
    assert off.pricing_version == "deepseek-2026-08-17-offpeak"
    assert off.cny == pytest.approx(1.5)
    assert off.usd == pytest.approx(0.22)
    # USD × 7.2 would overstate official CNY (0.44 * 7.2 = 3.168).
    assert priced.cny != pytest.approx(priced.usd * 7.2)


def test_v4_flash_cache_hit_is_priced_separately() -> None:
    priced = estimate_model_cost(
        model="deepseek-v4-flash",
        prompt_tokens=1_000_000,
        completion_tokens=1_000_000,
        cache_hit_tokens=1_000_000,
        cache_miss_tokens=0,
        at=_OFFPEAK,
    )

    assert priced.cny == pytest.approx(0.05 + 4.5)


def test_peak_window_normalizes_z_and_plus_eight() -> None:
    assert is_deepseek_peak(_Z_PEAK) is True
    assert is_deepseek_peak(_OFFSET_PEAK) is True
    naive_utc = datetime(2026, 8, 19, 2, 30)  # treated as UTC
    assert is_deepseek_peak(naive_utc) is True
    assert parse_usage_datetime("2026-08-19T02:30:00Z") == parse_usage_datetime(
        "2026-08-19T10:30:00+08:00"
    )
    noon_beijing = datetime(2026, 8, 19, 12, 0, tzinfo=_BEIJING)
    assert is_deepseek_peak(noon_beijing) is False


def test_historical_resolve_does_not_mutate_july_row() -> None:
    assert resolve_model_price("deepseek-v4-flash", at=_PRE_HIKE) is DEEPSEEK_V4_FLASH_PRICE
    assert resolve_model_price("deepseek-v4-flash", at=_PEAK) is DEEPSEEK_V4_FLASH_PEAK_PRICE
    assert (
        resolve_model_price("deepseek-v4-flash", at=_OFFPEAK)
        is DEEPSEEK_V4_FLASH_OFFPEAK_PRICE
    )


def test_reasoning_tokens_fill_in_when_completion_omits_them() -> None:
    folded = estimate_model_cost(
        model="deepseek-v4-flash",
        prompt_tokens=0,
        completion_tokens=1_000_000,
        cache_hit_tokens=0,
        cache_miss_tokens=0,
        reasoning_tokens=200_000,
        at=_OFFPEAK,
    )
    omitted = estimate_model_cost(
        model="deepseek-v4-flash",
        prompt_tokens=0,
        completion_tokens=0,
        cache_hit_tokens=0,
        cache_miss_tokens=0,
        reasoning_tokens=1_000_000,
        at=_OFFPEAK,
    )

    assert folded.cny == pytest.approx(4.5)
    assert omitted.cny == pytest.approx(4.5)


def test_legacy_flat_algorithm_matches_july_usd_times_7_2() -> None:
    cny = estimate_legacy_flat_cny(
        model="deepseek-v4-flash",
        prompt_tokens=1_000_000,
        completion_tokens=0,
        cache_hit_tokens=0,
        cache_miss_tokens=1_000_000,
    )
    assert cny == pytest.approx(0.14 * 7.2)


def test_unpriced_model_uses_a_conservative_cost_until_a_verified_price_is_added() -> None:
    cost, version = estimate_model_cost_usd(
        model="future-model",
        prompt_tokens=100,
        completion_tokens=10,
        cache_hit_tokens=0,
        cache_miss_tokens=0,
    )

    assert version == "unpriced-conservative-2026-07-13"
    assert cost > 0


_PRODUCTION_MODEL_IDS = (
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "deepseek-v4-flash->gpt-5.6-luna",
    "gpt-4.1-mini",
    "source-review-authority:gpt-4.1-mini|qwen/qwen-plus",
    "qwen/qwen-plus",
    "gpt-5.4-mini",
    "openai/gpt-5.4-nano",
    "openai/gpt-4o-mini",
    "gpt-5.6-luna",
    "qwen3-vl-flash",
    "qwen/qwen3-vl-flash",
    "gpt-image-2",
    "openai/gpt-image-2",
)


@pytest.mark.parametrize("model", _PRODUCTION_MODEL_IDS)
def test_production_model_ids_have_verified_price_rows(model: str) -> None:
    _cost, version = estimate_model_cost_usd(
        model=model,
        prompt_tokens=100,
        completion_tokens=10,
        cache_hit_tokens=0,
        cache_miss_tokens=100,
    )

    assert version != UNPRICED_MODEL_CONSERVATIVE_PRICE.version


def test_source_review_authority_composite_is_billed_as_both_lanes() -> None:
    composite, _version = estimate_model_cost_usd(
        model="source-review-authority:gpt-4.1-mini|qwen/qwen-plus",
        prompt_tokens=1_000_000,
        completion_tokens=0,
        cache_hit_tokens=0,
        cache_miss_tokens=1_000_000,
    )
    primary, _ = estimate_model_cost_usd(
        model="gpt-4.1-mini",
        prompt_tokens=1_000_000,
        completion_tokens=0,
        cache_hit_tokens=0,
        cache_miss_tokens=1_000_000,
    )
    secondary, _ = estimate_model_cost_usd(
        model="qwen/qwen-plus",
        prompt_tokens=1_000_000,
        completion_tokens=0,
        cache_hit_tokens=0,
        cache_miss_tokens=1_000_000,
    )

    assert composite == pytest.approx(primary + secondary)


def test_model_reserve_uses_selected_route_prompt_size_and_observed_output() -> None:
    flash = estimate_routed_model_reserve_cny(
        model="deepseek-v4-flash",
        prompt_characters=6_000,
        observed_output_tokens=(80, 120, 160),
    )
    pro = estimate_routed_model_reserve_cny(
        model="deepseek-v4-pro",
        prompt_characters=6_000,
        observed_output_tokens=(80, 120, 160),
    )
    longer_history = estimate_routed_model_reserve_cny(
        model="deepseek-v4-flash",
        prompt_characters=12_000,
        observed_output_tokens=(80, 120, 160),
    )
    higher_observed_output = estimate_routed_model_reserve_cny(
        model="deepseek-v4-flash",
        prompt_characters=6_000,
        observed_output_tokens=(80, 120, 800),
    )

    assert pro > flash
    assert longer_history > flash
    assert higher_observed_output > flash


def test_usage_samples_prefer_the_same_route_before_model_wide_history(tmp_path: Path) -> None:
    store = CompanionStore(tmp_path / "usage.sqlite")
    for purpose, cadence, output in (
        ("reply", "warm", 90),
        ("reply", "warm", 240),
        ("afterthought", "warm", 900),
    ):
        store.record_model_usage(
            purpose=purpose,
            model="deepseek-v4-flash",
            status="succeeded",
            latency_ms=20,
            completion_tokens=output,
            total_tokens=output,
            cadence=cadence,
        )

    same_route = store.recent_model_usage_samples(
        model="deepseek-v4-flash", purpose="reply", cadence="warm"
    )

    assert [sample["completion_tokens"] for sample in same_route] == [240, 90]


def test_usage_report_links_calls_to_turn_and_reports_percentiles_and_cost(tmp_path: Path) -> None:
    store = CompanionStore(tmp_path / "usage.sqlite")
    for latency, status, attempt in (
        (100, "succeeded", 1),
        (300, "failed", 2),
        (500, "succeeded", 3),
    ):
        store.record_model_usage(
            purpose="reply",
            model="deepseek-v4-flash",
            status=status,
            latency_ms=latency,
            prompt_tokens=3_000,
            completion_tokens=500,
            cache_hit_tokens=1_000,
            cache_miss_tokens=2_000,
            total_tokens=3_500,
            world_id="world-1",
            turn_id="turn-9",
            action_id=f"action-{attempt}",
            cadence="hot",
            attempt=attempt,
            thinking_enabled=attempt != 2,
            reasoning_effort="high" if attempt != 2 else "",
        )

    report = store.model_usage_report("day", datetime.now(UTC), cny_per_usd=7.2)

    turn = report["turns"]["turn-9"]
    assert turn["calls"] == 3
    assert turn["total_tokens"] == 10_500
    assert turn["p50_latency_ms"] == 300
    assert turn["p95_latency_ms"] == 500
    assert turn["success_rate"] == pytest.approx(2 / 3)
    one = estimate_model_cost(
        model="deepseek-v4-flash",
        prompt_tokens=3_000,
        completion_tokens=500,
        cache_hit_tokens=1_000,
        cache_miss_tokens=2_000,
        at=datetime.now(UTC),
    )
    assert turn["estimated_cost_usd"] == pytest.approx(one.usd * 3)
    assert turn["estimated_cost_cny"] == pytest.approx(one.cny * 3)
    group = report["groups"]["reply|hot|deepseek-v4-flash"]
    assert group["failed_calls"] == 1
    assert group["attempts"] == 3
    assert report["routes"]["deepseek-v4-flash|thinking=1|high"]["calls"] == 2
    assert report["routes"]["deepseek-v4-flash|thinking=0|default"]["calls"] == 1
    assert report["turn_routes"]["turn-9"]["deepseek-v4-flash|thinking=1|high"][
        "calls"
    ] == 2


def test_model_usage_schema_adds_linkage_columns_to_an_existing_database(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            create table model_usage_events (
              id integer primary key autoincrement,
              purpose text not null, model text not null, status text not null,
              latency_ms integer not null, prompt_tokens integer not null,
              completion_tokens integer not null, reasoning_tokens integer not null,
              cache_hit_tokens integer not null, cache_miss_tokens integer not null,
              total_tokens integer not null, error text not null, created_at text not null
            )
            """
        )

    store = CompanionStore(path)
    store.record_model_usage(
        purpose="reply",
        model="deepseek-v4-flash",
        status="succeeded",
        latency_ms=20,
        turn_id="migrated-turn",
        cadence="warm",
    )

    report = store.model_usage_report("day", datetime.now(UTC))
    assert report["turns"]["migrated-turn"]["calls"] == 1
    with store.connect() as conn:
        row = conn.execute(
            "select thinking_enabled, reasoning_effort from model_usage_events"
        ).fetchone()
    assert row["thinking_enabled"] == 0
    assert row["reasoning_effort"] == ""


def test_gpt_image_2_has_verified_2026_08_18_price_row() -> None:
    from companion_daemon.usage_metrics import (
        GPT_IMAGE_2_PRICE,
        estimate_gpt_image_2_cost_usd,
        parse_openai_image_usage,
    )

    cost, version = estimate_model_cost_usd(
        model="gpt-image-2",
        prompt_tokens=0,
        completion_tokens=1_366,
        cache_hit_tokens=0,
        cache_miss_tokens=6_563,
    )
    table_usd, table_version = estimate_gpt_image_2_cost_usd(
        size="1024x1536", quality="medium", reference_count=1
    )

    assert version == GPT_IMAGE_2_PRICE.version == "openai-image-2026-08-18"
    assert table_version == version
    assert table_usd == pytest.approx(0.041 + 6_563 * 8 / 1_000_000)
    assert cost == pytest.approx(
        6_563 * 8 / 1_000_000 + 1_366 * 30 / 1_000_000
    )
    parsed = parse_openai_image_usage(
        {
            "usage": {
                "input_tokens": 100,
                "output_tokens": 1366,
                "total_tokens": 1466,
                "input_tokens_details": {"text_tokens": 40, "image_tokens": 60},
            }
        }
    )
    assert parsed["text_input_tokens"] == 40
    assert parsed["image_input_tokens"] == 60
    assert parsed["output_tokens"] == 1366
