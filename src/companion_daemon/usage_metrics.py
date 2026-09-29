"""Versioned model pricing and deterministic usage aggregation primitives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
import json
import math
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo


# Used only when a price row has no native CNY (OpenAI, historical DeepSeek).
# DeepSeek from 2026-08-17 bills CNY directly; do not convert those rows.
CNY_PER_USD = 7.2
BEIJING_TZ = ZoneInfo("Asia/Shanghai")

# Official peak window: Beijing 09:00-12:00 and 14:00-18:00, half-open.
# Equivalent UTC 01:00-04:00 and 06:00-10:00.
# Sources:
#   https://api-docs.deepseek.com/zh-cn/quick_start/pricing
#   https://api-docs.deepseek.com/quick_start/pricing/
#   https://api-docs.deepseek.com/zh-cn/updates  (effective 2026-08-17 00:00 Beijing)
DEEPSEEK_PEAK_OFFPEAK_EFFECTIVE_FROM = datetime(2026, 8, 17, 0, 0, tzinfo=BEIJING_TZ)
DEEPSEEK_V41_EFFECTIVE_FROM = datetime(2026, 9, 10, 4, 0, tzinfo=timezone.utc)
DEEPSEEK_PEAK_WINDOWS_BEIJING: tuple[tuple[time, time], ...] = (
    (time(9, 0), time(12, 0)),
    (time(14, 0), time(18, 0)),
)


@dataclass(frozen=True)
class ModelPrice:
    model: str
    version: str
    cache_hit_usd_per_million: float
    cache_miss_usd_per_million: float
    output_usd_per_million: float
    cache_hit_cny_per_million: float | None = None
    cache_miss_cny_per_million: float | None = None
    output_cny_per_million: float | None = None
    window: str = "flat"

    @property
    def has_native_cny(self) -> bool:
        return (
            self.cache_hit_cny_per_million is not None
            and self.cache_miss_cny_per_million is not None
            and self.output_cny_per_million is not None
        )


@dataclass(frozen=True)
class ModelCostEstimate:
    usd: float
    cny: float
    pricing_version: str
    window: str


# DeepSeek public price table observed 2026-07-13. Historical rows persist the
# version and computed USD amount so later price changes do not rewrite history.
# Source: https://api-docs.deepseek.com/quick_start/pricing/ (pre-peak/off-peak)
DEEPSEEK_V4_FLASH_PRICE = ModelPrice(
    model="deepseek-v4-flash",
    version="deepseek-2026-07-13",
    cache_hit_usd_per_million=0.0028,
    cache_miss_usd_per_million=0.14,
    output_usd_per_million=0.28,
)

DEEPSEEK_V4_PRO_PRICE = ModelPrice(
    model="deepseek-v4-pro",
    version="deepseek-2026-07-13",
    cache_hit_usd_per_million=0.003625,
    cache_miss_usd_per_million=0.435,
    output_usd_per_million=0.87,
)

# DeepSeek peak/off-peak, effective Beijing 2026-08-17 00:00.
# CNY is the billed currency for mainland console balance (do not FX).
# USD is the official English table, stored for the estimated_cost_usd column.
# Sources fetched 2026-08-19:
#   https://api-docs.deepseek.com/zh-cn/quick_start/pricing
#   https://api-docs.deepseek.com/quick_start/pricing/
DEEPSEEK_V4_FLASH_OFFPEAK_PRICE = ModelPrice(
    model="deepseek-v4-flash",
    version="deepseek-2026-08-17-offpeak",
    cache_hit_usd_per_million=0.007,
    cache_miss_usd_per_million=0.22,
    output_usd_per_million=0.66,
    cache_hit_cny_per_million=0.05,
    cache_miss_cny_per_million=1.5,
    output_cny_per_million=4.5,
    window="off-peak",
)

DEEPSEEK_V4_FLASH_PEAK_PRICE = ModelPrice(
    model="deepseek-v4-flash",
    version="deepseek-2026-08-17-peak",
    cache_hit_usd_per_million=0.014,
    cache_miss_usd_per_million=0.44,
    output_usd_per_million=1.32,
    cache_hit_cny_per_million=0.10,
    cache_miss_cny_per_million=3.0,
    output_cny_per_million=9.0,
    window="peak",
)

# Official Chinese/English pricing pages verified 2026-09-16; effective date:
# https://api-docs.deepseek.com/news/news260910/
# Legacy Flash names route to V4.1-Flash. The current pricing-page footnote
# supersedes the initial retirement announcement: V4 Pro remains available at
# its own unchanged rates. Preserve old rows and select by actual call time.
DEEPSEEK_V41_FLASH_OFFPEAK_PRICE = ModelPrice(
    model="deepseek-flash", version="deepseek-2026-09-10-offpeak",
    cache_hit_usd_per_million=0.003, cache_miss_usd_per_million=0.15,
    output_usd_per_million=0.6, cache_hit_cny_per_million=0.02,
    cache_miss_cny_per_million=1.0, output_cny_per_million=4.0, window="off-peak",
)
DEEPSEEK_V41_FLASH_PEAK_PRICE = ModelPrice(
    model="deepseek-flash", version="deepseek-2026-09-10-peak",
    cache_hit_usd_per_million=0.006, cache_miss_usd_per_million=0.3,
    output_usd_per_million=1.2, cache_hit_cny_per_million=0.04,
    cache_miss_cny_per_million=2.0, output_cny_per_million=8.0, window="peak",
)

DEEPSEEK_V4_PRO_OFFPEAK_PRICE = ModelPrice(
    model="deepseek-v4-pro",
    version="deepseek-2026-08-17-offpeak",
    cache_hit_usd_per_million=0.022,
    cache_miss_usd_per_million=0.66,
    output_usd_per_million=1.98,
    cache_hit_cny_per_million=0.15,
    cache_miss_cny_per_million=4.5,
    output_cny_per_million=13.5,
    window="off-peak",
)

DEEPSEEK_V4_PRO_PEAK_PRICE = ModelPrice(
    model="deepseek-v4-pro",
    version="deepseek-2026-08-17-peak",
    cache_hit_usd_per_million=0.044,
    cache_miss_usd_per_million=1.32,
    output_usd_per_million=3.96,
    cache_hit_cny_per_million=0.30,
    cache_miss_cny_per_million=9.0,
    output_cny_per_million=27.0,
    window="peak",
)

# OpenAI public list prices observed 2026-08-13.
# Sources: https://developers.openai.com/api/docs/models/gpt-4.1-mini
#          https://developers.openai.com/api/docs/models/gpt-5.4-mini
#          https://www.aipricing.guru/openai-pricing/ (GPT-5.6 Luna, 2026-08-12)
GPT_4_1_MINI_PRICE = ModelPrice(
    model="gpt-4.1-mini",
    version="openai-2026-08-13",
    cache_hit_usd_per_million=0.10,
    cache_miss_usd_per_million=0.40,
    output_usd_per_million=1.60,
)

GPT_4O_MINI_PRICE = ModelPrice(
    model="gpt-4o-mini",
    version="openai-2026-08-13",
    cache_hit_usd_per_million=0.075,
    cache_miss_usd_per_million=0.15,
    output_usd_per_million=0.60,
)

GPT_5_4_MINI_PRICE = ModelPrice(
    model="gpt-5.4-mini",
    version="openai-2026-08-13",
    cache_hit_usd_per_million=0.075,
    cache_miss_usd_per_million=0.75,
    output_usd_per_million=4.50,
)

GPT_5_4_NANO_PRICE = ModelPrice(
    model="gpt-5.4-nano",
    version="openai-2026-08-13",
    cache_hit_usd_per_million=0.02,
    cache_miss_usd_per_million=0.20,
    output_usd_per_million=1.25,
)

GPT_5_6_LUNA_PRICE = ModelPrice(
    model="gpt-5.6-luna",
    version="openai-2026-08-13",
    cache_hit_usd_per_million=0.02,
    cache_miss_usd_per_million=0.20,
    output_usd_per_million=1.20,
)

# Public unauthenticated GET observed 2026-09-07:
# https://openrouter.ai/api/v1/models/nousresearch/hermes-4-70b/endpoints
# Exactly one endpoint, Nebius (nebius/fp8), quoted $0.13/M prompt and
# $0.40/M completion; no separate request/image or discounted-cache price.
# Its status was -5: this price snapshot does not qualify availability.
# The ceiling covers every endpoint in that snapshot, not a cheapest-route
# estimate. OpenRouter request max_price pins these rates against later route
# changes; usage settlement conservatively assumes no cache discount.
HERMES_4_70B_PRICE = ModelPrice(
    model="nousresearch/hermes-4-70b",
    version="openrouter-hermes-2026-09-07",
    cache_hit_usd_per_million=0.13,
    cache_miss_usd_per_million=0.13,
    output_usd_per_million=0.40,
)

# Alibaba Model Studio international list, Qwen-Plus ≤256K, verified 2026-07-15.
QWEN_PLUS_PRICE = ModelPrice(
    model="qwen/qwen-plus",
    version="qwen-2026-07-15",
    cache_hit_usd_per_million=0.04,
    cache_miss_usd_per_million=0.40,
    output_usd_per_million=1.20,
)

# Mainland DashScope list for Qwen3-VL-Flash ≤32K, thinking off, 2026-08-15.
# Official CNY: ¥0.03 / ¥0.15 / ¥1.5 per million; converted at 7.2 CNY/USD.
QWEN3_VL_FLASH_PRICE = ModelPrice(
    model="qwen3-vl-flash",
    version="dashscope-2026-08-15",
    cache_hit_usd_per_million=0.004167,
    cache_miss_usd_per_million=0.020833,
    output_usd_per_million=0.208333,
)

# OpenAI GPT Image 2 token rates, official pricing page verified 2026-08-18.
# Source: https://developers.openai.com/api/docs/pricing  (Image generation models)
#   Image input $8.00 / cached $2.00 / output $30.00 per 1M tokens
#   Text input $5.00 / cached $1.25 per 1M tokens
# Per-image calculator (same page + image-generation guide, 2026-08-18):
#   1024x1024  low $0.006 / medium $0.053 / high $0.211
#   1024x1536  low $0.005 / medium $0.041 / high $0.165
#   1536x1024  low $0.005 / medium $0.041 / high $0.165
# ModelPrice below prices image tokens (the dominant cost). Text prompt tokens
# are added by estimate_gpt_image_2_cost_usd when a provider usage block exists.
GPT_IMAGE_2_PRICE = ModelPrice(
    model="gpt-image-2",
    version="openai-image-2026-08-18",
    cache_hit_usd_per_million=2.00,
    cache_miss_usd_per_million=8.00,
    output_usd_per_million=30.00,
)

GPT_IMAGE_2_TEXT_INPUT_USD_PER_MILLION = 5.00
GPT_IMAGE_2_IMAGE_INPUT_USD_PER_MILLION = 8.00
GPT_IMAGE_2_IMAGE_OUTPUT_USD_PER_MILLION = 30.00

# Official calculator USD for one output image. Verified 2026-08-18.
GPT_IMAGE_2_OUTPUT_USD: Mapping[tuple[str, str], float] = {
    ("1024x1024", "low"): 0.006,
    ("1024x1024", "medium"): 0.053,
    ("1024x1024", "high"): 0.211,
    ("1024x1536", "low"): 0.005,
    ("1024x1536", "medium"): 0.041,
    ("1024x1536", "high"): 0.165,
    ("1536x1024", "low"): 0.005,
    ("1536x1024", "medium"): 0.041,
    ("1536x1024", "high"): 0.165,
}

# High-fidelity identity reference (~6,563 image input tokens at $8/M).
GPT_IMAGE_2_PORTRAIT_REFERENCE_INPUT_USD = 6_563 * 8 / 1_000_000

# A new provider model must never silently become free just because its price
# table has not reached this release yet.  This is intentionally above the
# currently supported Pro rate, so routing remains bounded until an exact row
# is added and verified.
UNPRICED_MODEL_CONSERVATIVE_PRICE = ModelPrice(
    model="__unpriced__",
    version="unpriced-conservative-2026-07-13",
    cache_hit_usd_per_million=0.01,
    cache_miss_usd_per_million=1.20,
    output_usd_per_million=2.40,
)

# Flat (non-time-varying) current rows. DeepSeek live rows are resolved by
# ``resolve_model_price`` from the versioned peak/off-peak schedule.
MODEL_PRICES: Mapping[str, ModelPrice] = {
    GPT_4_1_MINI_PRICE.model: GPT_4_1_MINI_PRICE,
    GPT_4O_MINI_PRICE.model: GPT_4O_MINI_PRICE,
    "openai/gpt-4o-mini": GPT_4O_MINI_PRICE,
    GPT_5_4_MINI_PRICE.model: GPT_5_4_MINI_PRICE,
    "openai/gpt-5.4-mini": GPT_5_4_MINI_PRICE,
    GPT_5_4_NANO_PRICE.model: GPT_5_4_NANO_PRICE,
    "openai/gpt-5.4-nano": GPT_5_4_NANO_PRICE,
    GPT_5_6_LUNA_PRICE.model: GPT_5_6_LUNA_PRICE,
    HERMES_4_70B_PRICE.model: HERMES_4_70B_PRICE,
    QWEN_PLUS_PRICE.model: QWEN_PLUS_PRICE,
    QWEN3_VL_FLASH_PRICE.model: QWEN3_VL_FLASH_PRICE,
    "qwen/qwen3-vl-flash": QWEN3_VL_FLASH_PRICE,
    GPT_IMAGE_2_PRICE.model: GPT_IMAGE_2_PRICE,
    "openai/gpt-image-2": GPT_IMAGE_2_PRICE,
}


def parse_usage_datetime(value: datetime | str | None) -> datetime:
    """Normalize ledger timestamps (``Z``, ``+00:00``, ``+08:00``, naive) to aware UTC.

    Naive values are treated as UTC: World V2 usage writers emit
    ``datetime.now(timezone.utc).isoformat()``. Peak/off-peak is applied after
    converting this instant to Beijing.
    """

    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return datetime.now(timezone.utc)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def beijing_datetime(value: datetime | str | None) -> datetime:
    return parse_usage_datetime(value).astimezone(BEIJING_TZ)


def is_deepseek_peak(value: datetime | str | None = None) -> bool:
    local = beijing_datetime(value)
    if local >= DEEPSEEK_V41_EFFECTIVE_FROM and local.weekday() >= 5:
        return False
    clock = local.time()
    return any(start <= clock < end for start, end in DEEPSEEK_PEAK_WINDOWS_BEIJING)


def seconds_until_deepseek_offpeak_start(value: datetime | str | None = None) -> int:
    """Seconds until the current Beijing peak window ends (0 if already off-peak)."""

    instant = beijing_datetime(value)
    clock = instant.time()
    for start, end in DEEPSEEK_PEAK_WINDOWS_BEIJING:
        if start <= clock < end:
            end_dt = instant.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
            if end_dt <= instant:
                end_dt = end_dt + timedelta(days=1)
            return max(0, int((end_dt - instant).total_seconds()))
    return 0


def interaction_fact_retry_delay_seconds(
    *,
    failure_code: str,
    retry_ordinal: int,
    default_delays: tuple[int, ...],
) -> int:
    """Budget denials should not masquerade as provider outages or retry in peak."""

    normalized = str(failure_code or "").strip()
    if normalized in {
        "soft_daily_budget_exceeded",
        "daily_budget_exceeded",
        "monthly_budget_exceeded",
        "deferred_offpeak",
    }:
        offpeak_wait = seconds_until_deepseek_offpeak_start()
        if offpeak_wait > 0:
            return max(60, offpeak_wait)
        return max(600, default_delays[min(max(retry_ordinal, 1), len(default_delays)) - 1])
    index = min(max(retry_ordinal, 1), len(default_delays)) - 1
    return default_delays[index]


def billed_output_tokens(completion_tokens: int, reasoning_tokens: int = 0) -> int:
    """Bill output tokens without double-counting thinking.

    DeepSeek reports ``reasoning_tokens`` inside ``completion_tokens_details``.
    When thinking is already folded into ``completion_tokens``, completion wins.
    If a payload omits thinking from completion, reasoning is the floor.
    """

    completion = max(0, int(completion_tokens or 0))
    reasoning = max(0, int(reasoning_tokens or 0))
    return max(completion, reasoning)


def _deepseek_family(model: str) -> str | None:
    lowered = (model or "").strip().casefold()
    if "deepseek" not in lowered:
        return None
    if "pro" in lowered and "flash" not in lowered:
        return "pro"
    return "flash"


def resolve_model_price(
    model: str,
    *,
    at: datetime | str | None = None,
    conservative_peak: bool = False,
) -> ModelPrice:
    """Select the price row that applied at ``at`` (call time)."""

    family = _deepseek_family(model)
    if family is not None:
        instant = parse_usage_datetime(at)
        if beijing_datetime(instant) < DEEPSEEK_PEAK_OFFPEAK_EFFECTIVE_FROM:
            return DEEPSEEK_V4_PRO_PRICE if family == "pro" else DEEPSEEK_V4_FLASH_PRICE
        peak = True if conservative_peak else is_deepseek_peak(instant)
        if family == "pro":
            return DEEPSEEK_V4_PRO_PEAK_PRICE if peak else DEEPSEEK_V4_PRO_OFFPEAK_PRICE
        if instant >= DEEPSEEK_V41_EFFECTIVE_FROM:
            return DEEPSEEK_V41_FLASH_PEAK_PRICE if peak else DEEPSEEK_V41_FLASH_OFFPEAK_PRICE
        return DEEPSEEK_V4_FLASH_PEAK_PRICE if peak else DEEPSEEK_V4_FLASH_OFFPEAK_PRICE
    keyed = MODEL_PRICES.get(model)
    if keyed is not None:
        return keyed
    if model.startswith("openai/"):
        return MODEL_PRICES.get(model.removeprefix("openai/"), UNPRICED_MODEL_CONSERVATIVE_PRICE)
    prefixed = MODEL_PRICES.get(f"openai/{model}")
    if prefixed is not None:
        return prefixed
    return UNPRICED_MODEL_CONSERVATIVE_PRICE


def _price_for(model: str) -> ModelPrice:
    """Current conservative row (DeepSeek peak) for route comparison."""

    return resolve_model_price(model, conservative_peak=True)


def billed_model_ids(model: str) -> tuple[str, ...]:
    """Split composite provider ids into the models that actually ran."""

    value = (model or "").strip()
    if not value:
        return (value,)
    if value.startswith("source-review-authority:"):
        parts = tuple(
            part.strip()
            for part in value.removeprefix("source-review-authority:").split("|")
            if part.strip()
        )
        if len(parts) >= 2:
            return parts
    if "->" in value:
        parts = tuple(part.strip() for part in value.split("->") if part.strip())
        if parts:
            return (
                max(parts, key=lambda item: _price_for(item).cache_miss_usd_per_million),
            )
    return (value,)


def _component_cost(
    price: ModelPrice,
    *,
    hit: int,
    miss: int,
    output: int,
    cny_per_usd: float,
) -> tuple[float, float]:
    usd = (
        hit * price.cache_hit_usd_per_million
        + miss * price.cache_miss_usd_per_million
        + output * price.output_usd_per_million
    ) / 1_000_000
    if price.has_native_cny:
        cny = (
            hit * float(price.cache_hit_cny_per_million)
            + miss * float(price.cache_miss_cny_per_million)
            + output * float(price.output_cny_per_million)
        ) / 1_000_000
    else:
        cny = usd * max(0.0, cny_per_usd)
    return usd, cny


def estimate_model_cost(
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_hit_tokens: int,
    cache_miss_tokens: int,
    reasoning_tokens: int = 0,
    at: datetime | str | None = None,
    cny_per_usd: float = CNY_PER_USD,
    conservative_peak: bool = False,
) -> ModelCostEstimate:
    hit = max(0, int(cache_hit_tokens or 0))
    miss = max(0, int(cache_miss_tokens or 0))
    # Older/partial provider payloads may omit cache details. Conservatively
    # price all observed prompt tokens as cache misses.
    if hit + miss == 0:
        miss = max(0, int(prompt_tokens or 0))
    output = billed_output_tokens(completion_tokens, reasoning_tokens)
    usd = 0.0
    cny = 0.0
    versions: list[str] = []
    windows: list[str] = []
    for component in billed_model_ids(model):
        price = resolve_model_price(
            component, at=at, conservative_peak=conservative_peak
        )
        part_usd, part_cny = _component_cost(
            price, hit=hit, miss=miss, output=output, cny_per_usd=cny_per_usd
        )
        usd += part_usd
        cny += part_cny
        versions.append(price.version)
        windows.append(price.window)
    window = windows[0] if len(set(windows)) == 1 else "+".join(windows)
    return ModelCostEstimate(
        usd=usd,
        cny=cny,
        pricing_version="+".join(versions),
        window=window,
    )


def estimate_model_cost_usd(
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_hit_tokens: int,
    cache_miss_tokens: int,
    reasoning_tokens: int = 0,
    at: datetime | str | None = None,
) -> tuple[float, str]:
    priced = estimate_model_cost(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cache_hit_tokens=cache_hit_tokens,
        cache_miss_tokens=cache_miss_tokens,
        reasoning_tokens=reasoning_tokens,
        at=at,
    )
    return priced.usd, priced.pricing_version


def estimate_model_cost_cny(
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_hit_tokens: int,
    cache_miss_tokens: int,
    reasoning_tokens: int = 0,
    at: datetime | str | None = None,
    cny_per_usd: float = CNY_PER_USD,
    conservative_peak: bool = False,
) -> tuple[float, str]:
    priced = estimate_model_cost(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cache_hit_tokens=cache_hit_tokens,
        cache_miss_tokens=cache_miss_tokens,
        reasoning_tokens=reasoning_tokens,
        at=at,
        cny_per_usd=cny_per_usd,
        conservative_peak=conservative_peak,
    )
    return priced.cny, priced.pricing_version


def estimate_legacy_flat_cny(
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_hit_tokens: int,
    cache_miss_tokens: int,
    reasoning_tokens: int = 0,
    cny_per_usd: float = CNY_PER_USD,
) -> float:
    """Recompute the pre-2026-08-17 ledger algorithm (USD × 7.2, no peak)."""

    family = _deepseek_family(model)
    if family == "pro":
        price = DEEPSEEK_V4_PRO_PRICE
    elif family == "flash":
        price = DEEPSEEK_V4_FLASH_PRICE
    else:
        return estimate_model_cost(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cache_hit_tokens=cache_hit_tokens,
            cache_miss_tokens=cache_miss_tokens,
            reasoning_tokens=reasoning_tokens,
            at=datetime(2026, 7, 15, tzinfo=timezone.utc),
            cny_per_usd=cny_per_usd,
        ).cny
    hit = max(0, int(cache_hit_tokens or 0))
    miss = max(0, int(cache_miss_tokens or 0))
    if hit + miss == 0:
        miss = max(0, int(prompt_tokens or 0))
    output = billed_output_tokens(completion_tokens, reasoning_tokens)
    _usd, cny = _component_cost(
        price, hit=hit, miss=miss, output=output, cny_per_usd=cny_per_usd
    )
    return cny


def estimate_routed_model_reserve_cny(
    *,
    model: str,
    prompt_characters: int,
    observed_output_tokens: Iterable[int] = (),
    cny_per_usd: float = CNY_PER_USD,
    at: datetime | str | None = None,
) -> float:
    """Reserve one call from its actual route, prompt scale, and local history.

    Provider usage is still authoritative after a request completes.  This
    function is only the preflight envelope: it deliberately uses the current
    prompt's size and the p95 of comparable completed calls rather than a
    single project-wide CNY constant.  When there is no history yet, the
    explicit 256-token output floor prevents a new route from looking free.

    DeepSeek reservations after the 2026-08-17 hike use peak CNY so a call
    that crosses into Beijing 09:00 cannot look cheaper than it will be.
    """
    # This is a conservative approximation for mixed Chinese/English prompt
    # text.  The provider's returned tokens supersede it in the usage ledger.
    prompt_tokens = max(1, math.ceil(max(0, prompt_characters) * 0.75))
    observed = tuple(
        max(0, int(tokens)) for tokens in observed_output_tokens if int(tokens) > 0
    )
    output_tokens = nearest_rank(observed, 0.95) if observed else 256
    conservative = _deepseek_family(model) is not None
    priced = estimate_model_cost(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=output_tokens,
        cache_hit_tokens=0,
        cache_miss_tokens=prompt_tokens,
        at=at,
        cny_per_usd=cny_per_usd,
        conservative_peak=conservative,
    )
    return round(priced.cny, 6)


class ProviderRequestPricingUnavailable(ValueError):
    """An outbound request has no supported pre-emission cost envelope."""


def estimate_provider_request_reserve_cny(
    *,
    request_payload: Mapping[str, object],
    cny_per_usd: float = CNY_PER_USD,
) -> float:
    """Reserve the final text request, including tools and its output ceiling.

    This admission envelope deliberately does not reuse a historical p95 or
    the character/token heuristic used for forecasting. UTF-8 wire bytes bound
    text tokenization conservatively; an additional framing allowance covers
    provider message/tool separators. Returned provider usage still settles the
    bill. Unknown pricing and non-text inputs require their own priced route.
    """
    model = str(request_payload.get("model") or "")
    price = resolve_model_price(model, conservative_peak=True)
    if price is UNPRICED_MODEL_CONSERVATIVE_PRICE:
        raise ProviderRequestPricingUnavailable("provider request model has no installed price")
    if _deepseek_family(model) is not None and model not in {
        "deepseek-chat",
        "deepseek-reasoner",
        "deepseek-flash",
        "deepseek-v4-flash",
        "deepseek-v4-pro",
    }:
        raise ProviderRequestPricingUnavailable("provider request model has no installed price")
    output_limit = request_payload.get("max_completion_tokens", request_payload.get("max_tokens"))
    if type(output_limit) is not int or output_limit <= 0:
        raise ProviderRequestPricingUnavailable("provider request requires a positive output ceiling")
    count = request_payload.get("n", 1)
    if type(count) is not int or count <= 0:
        raise ProviderRequestPricingUnavailable("provider request completion count is invalid")
    messages = request_payload.get("messages", ())
    if not isinstance(messages, (list, tuple)):
        raise ProviderRequestPricingUnavailable("provider request messages are not text-priced")
    for message in messages:
        if not isinstance(message, Mapping):
            raise ProviderRequestPricingUnavailable("provider request messages are not text-priced")
        content = message.get("content")
        if isinstance(content, (list, tuple)) and any(
            not isinstance(part, Mapping) or part.get("type") != "text" for part in content
        ):
            raise ProviderRequestPricingUnavailable("provider request contains non-text input")
    wire = json.dumps(request_payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    prompt_tokens = len(wire.encode("utf-8")) + 1024
    priced = estimate_model_cost(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=output_limit * count,
        cache_hit_tokens=0,
        cache_miss_tokens=prompt_tokens,
        cny_per_usd=cny_per_usd,
        conservative_peak=True,
    )
    return math.ceil(priced.cny * 1_000_000) / 1_000_000


def estimate_gpt_image_2_cost_usd(
    *,
    size: str = "1024x1536",
    quality: str = "medium",
    reference_count: int = 0,
    text_input_tokens: int = 0,
    image_input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> tuple[float, str]:
    """Price one GPT Image 2 render from usage tokens, else the official table.

    Provider usage is authoritative.  The calculator row is only the preflight
    envelope for a missing usage block, so a 200 response cannot look free.
    """

    if output_tokens is not None and output_tokens > 0:
        image_in = max(0, int(image_input_tokens or 0))
        text_in = max(0, int(text_input_tokens or 0))
        usd = (
            text_in * GPT_IMAGE_2_TEXT_INPUT_USD_PER_MILLION
            + image_in * GPT_IMAGE_2_IMAGE_INPUT_USD_PER_MILLION
            + max(0, int(output_tokens)) * GPT_IMAGE_2_IMAGE_OUTPUT_USD_PER_MILLION
        ) / 1_000_000
        return usd, GPT_IMAGE_2_PRICE.version
    output_usd = GPT_IMAGE_2_OUTPUT_USD.get((size, quality))
    if output_usd is None:
        output_usd = GPT_IMAGE_2_OUTPUT_USD[("1024x1536", "medium")]
    image_in = image_input_tokens
    if image_in is None:
        input_usd = max(0, int(reference_count)) * GPT_IMAGE_2_PORTRAIT_REFERENCE_INPUT_USD
    else:
        input_usd = max(0, int(image_in)) * GPT_IMAGE_2_IMAGE_INPUT_USD_PER_MILLION / 1_000_000
    text_usd = max(0, int(text_input_tokens)) * GPT_IMAGE_2_TEXT_INPUT_USD_PER_MILLION / 1_000_000
    return output_usd + input_usd + text_usd, GPT_IMAGE_2_PRICE.version


def parse_openai_image_usage(payload: Mapping[str, object] | None) -> dict[str, int]:
    """Read Image API usage without assuming every provider includes the block."""

    empty = {
        "text_input_tokens": 0,
        "image_input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }
    if not isinstance(payload, Mapping):
        return empty
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        return empty
    details = usage.get("input_tokens_details")
    text_tokens = 0
    image_tokens = 0
    if isinstance(details, Mapping):
        text_tokens = max(0, int(details.get("text_tokens") or 0))
        image_tokens = max(0, int(details.get("image_tokens") or 0))
    input_tokens = max(0, int(usage.get("input_tokens") or 0))
    if text_tokens + image_tokens == 0 and input_tokens:
        text_tokens = input_tokens
    output_tokens = max(0, int(usage.get("output_tokens") or 0))
    total_tokens = max(0, int(usage.get("total_tokens") or 0))
    if total_tokens == 0:
        total_tokens = text_tokens + image_tokens + output_tokens
    return {
        "text_input_tokens": text_tokens,
        "image_input_tokens": image_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def _as_usage_mapping(row: Mapping[str, object] | object) -> Mapping[str, object]:
    if isinstance(row, Mapping):
        return row
    keys = getattr(row, "keys", None)
    if callable(keys):
        return {str(key): row[key] for key in keys()}
    return {}


def _row_int(row: Mapping[str, object], *names: str) -> int:
    for name in names:
        if name in row and row[name] is not None:
            try:
                return max(0, int(row[name] or 0))
            except (TypeError, ValueError):
                return 0
    return 0


def _row_str(row: Mapping[str, object], *names: str) -> str:
    for name in names:
        if name in row and row[name] is not None:
            return str(row[name] or "")
    return ""


def _row_float(row: Mapping[str, object], *names: str) -> float:
    for name in names:
        if name in row and row[name] is not None:
            try:
                return float(row[name] or 0.0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def price_usage_row(
    row: Mapping[str, object],
    *,
    cny_per_usd: float = CNY_PER_USD,
) -> ModelCostEstimate:
    """Reprice one persisted usage mapping from tokens and call time.

    Budget gates use this so already-written ``cost_cny`` / ``estimated_cost_usd``
    rows from the old half-price table do not understate remaining spend.
    """

    row = _as_usage_mapping(row)
    model = _row_str(row, "model")
    at = _row_str(row, "recorded_at", "created_at") or None
    priced = estimate_model_cost(
        model=model or "__unpriced__",
        prompt_tokens=_row_int(row, "prompt_tokens"),
        completion_tokens=_row_int(row, "completion_tokens"),
        cache_hit_tokens=_row_int(row, "cache_hit_tokens"),
        cache_miss_tokens=_row_int(row, "cache_miss_tokens"),
        reasoning_tokens=_row_int(row, "reasoning_tokens"),
        at=at,
        cny_per_usd=cny_per_usd,
    )
    if priced.cny == 0.0 and priced.usd == 0.0:
        stored_cny = _row_float(row, "estimated_cost_cny", "cost_cny")
        stored_usd = _row_float(row, "estimated_cost_usd")
        if stored_cny:
            return ModelCostEstimate(
                usd=stored_usd,
                cny=stored_cny,
                pricing_version=_row_str(row, "pricing_version") or priced.pricing_version,
                window=priced.window,
            )
        if stored_usd:
            return ModelCostEstimate(
                usd=stored_usd,
                cny=stored_usd * max(0.0, cny_per_usd),
                pricing_version=_row_str(row, "pricing_version") or priced.pricing_version,
                window=priced.window,
            )
    return priced


def nearest_rank(values: Iterable[int], percentile: float) -> int:
    ordered = sorted(max(0, int(value)) for value in values)
    if not ordered:
        return 0
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def aggregate_usage_rows(
    rows: Iterable[Mapping[str, object]], *, cny_per_usd: float
) -> dict[str, object]:
    materialized = list(rows)
    calls = len(materialized)
    succeeded = sum(1 for row in materialized if row["status"] == "succeeded")
    latencies = [int(row["latency_ms"] or 0) for row in materialized]
    priced_rows = [price_usage_row(row, cny_per_usd=cny_per_usd) for row in materialized]
    usd = sum(item.usd for item in priced_rows)
    cny = sum(item.cny for item in priced_rows)
    return {
        "calls": calls,
        "succeeded_calls": succeeded,
        "failed_calls": calls - succeeded,
        "success_rate": succeeded / calls if calls else 0.0,
        "prompt_tokens": sum(int(row["prompt_tokens"] or 0) for row in materialized),
        "completion_tokens": sum(int(row["completion_tokens"] or 0) for row in materialized),
        "reasoning_tokens": sum(int(row["reasoning_tokens"] or 0) for row in materialized),
        "cache_hit_tokens": sum(int(row["cache_hit_tokens"] or 0) for row in materialized),
        "cache_miss_tokens": sum(int(row["cache_miss_tokens"] or 0) for row in materialized),
        "total_tokens": sum(int(row["total_tokens"] or 0) for row in materialized),
        "latency_ms": sum(latencies),
        "p50_latency_ms": nearest_rank(latencies, 0.50),
        "p95_latency_ms": nearest_rank(latencies, 0.95),
        "attempts": calls,
        "max_attempt": max((max(1, int(row["attempt"] or 1)) for row in materialized), default=0),
        "estimated_cost_usd": usd,
        "estimated_cost_cny": cny,
    }
