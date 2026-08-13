"""Versioned model pricing and deterministic usage aggregation primitives."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Mapping


@dataclass(frozen=True)
class ModelPrice:
    model: str
    version: str
    cache_hit_usd_per_million: float
    cache_miss_usd_per_million: float
    output_usd_per_million: float


# DeepSeek public price table observed 2026-07-13. Historical rows persist the
# version and computed USD amount so later price changes do not rewrite history.
# Source: https://api-docs.deepseek.com/quick_start/pricing/
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

# Alibaba Model Studio international list, Qwen-Plus ≤256K, verified 2026-07-15.
QWEN_PLUS_PRICE = ModelPrice(
    model="qwen/qwen-plus",
    version="qwen-2026-07-15",
    cache_hit_usd_per_million=0.04,
    cache_miss_usd_per_million=0.40,
    output_usd_per_million=1.20,
)

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

MODEL_PRICES: Mapping[str, ModelPrice] = {
    DEEPSEEK_V4_FLASH_PRICE.model: DEEPSEEK_V4_FLASH_PRICE,
    DEEPSEEK_V4_PRO_PRICE.model: DEEPSEEK_V4_PRO_PRICE,
    GPT_4_1_MINI_PRICE.model: GPT_4_1_MINI_PRICE,
    GPT_4O_MINI_PRICE.model: GPT_4O_MINI_PRICE,
    "openai/gpt-4o-mini": GPT_4O_MINI_PRICE,
    GPT_5_4_MINI_PRICE.model: GPT_5_4_MINI_PRICE,
    "openai/gpt-5.4-mini": GPT_5_4_MINI_PRICE,
    GPT_5_4_NANO_PRICE.model: GPT_5_4_NANO_PRICE,
    "openai/gpt-5.4-nano": GPT_5_4_NANO_PRICE,
    GPT_5_6_LUNA_PRICE.model: GPT_5_6_LUNA_PRICE,
    QWEN_PLUS_PRICE.model: QWEN_PLUS_PRICE,
}


def _price_for(model: str) -> ModelPrice:
    keyed = MODEL_PRICES.get(model)
    if keyed is not None:
        return keyed
    if model.startswith("openai/"):
        return MODEL_PRICES.get(model.removeprefix("openai/"), UNPRICED_MODEL_CONSERVATIVE_PRICE)
    prefixed = MODEL_PRICES.get(f"openai/{model}")
    if prefixed is not None:
        return prefixed
    return UNPRICED_MODEL_CONSERVATIVE_PRICE


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


def estimate_routed_model_reserve_cny(
    *,
    model: str,
    prompt_characters: int,
    observed_output_tokens: Iterable[int] = (),
    cny_per_usd: float = 7.2,
) -> float:
    """Reserve one call from its actual route, prompt scale, and local history.

    Provider usage is still authoritative after a request completes.  This
    function is only the preflight envelope: it deliberately uses the current
    prompt's size and the p95 of comparable completed calls rather than a
    single project-wide CNY constant.  When there is no history yet, the
    explicit 256-token output floor prevents a new route from looking free.
    """
    # This is a conservative approximation for mixed Chinese/English prompt
    # text.  The provider's returned tokens supersede it in the usage ledger.
    prompt_tokens = max(1, math.ceil(max(0, prompt_characters) * 0.75))
    observed = tuple(
        max(0, int(tokens)) for tokens in observed_output_tokens if int(tokens) > 0
    )
    output_tokens = nearest_rank(observed, 0.95) if observed else 256
    usd, _pricing_version = estimate_model_cost_usd(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=output_tokens,
        cache_hit_tokens=0,
        cache_miss_tokens=prompt_tokens,
    )
    return round(usd * max(0.0, cny_per_usd), 6)


def estimate_model_cost_usd(
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_hit_tokens: int,
    cache_miss_tokens: int,
) -> tuple[float, str]:
    hit = max(0, cache_hit_tokens)
    miss = max(0, cache_miss_tokens)
    # Older/partial provider payloads may omit cache details. Conservatively
    # price all observed prompt tokens as cache misses.
    if hit + miss == 0:
        miss = max(0, prompt_tokens)
    total = 0.0
    versions: list[str] = []
    for component in billed_model_ids(model):
        price = _price_for(component)
        total += (
            hit * price.cache_hit_usd_per_million
            + miss * price.cache_miss_usd_per_million
            + max(0, completion_tokens) * price.output_usd_per_million
        ) / 1_000_000
        versions.append(price.version)
    return total, "+".join(versions)


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
    usd = sum(float(row["estimated_cost_usd"] or 0.0) for row in materialized)
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
        "estimated_cost_cny": usd * max(0.0, cny_per_usd),
    }
