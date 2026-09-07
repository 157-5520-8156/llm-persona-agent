"""Descriptive instance cost forecasts, independent of spend admission policy."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Literal, TypedDict


class CostForecast(TypedDict):
    target_monthly_cny: float
    month_settled_cny: float
    pending_cny: float
    target_remaining_cny: float
    last24h_settled_cny: float
    observed_hours: float
    month_end_projected_cny: float | None
    projection_status: Literal["available", "insufficient_history"]
    forecast_pressure_threshold_percent: int
    projection_method: str


def forecast_monthly_cost(
    *,
    now: datetime,
    observed_since: datetime | None,
    month_settled_cny: float,
    pending_cny: float,
    last24h_settled_cny: float,
    target_monthly_cny: float = 100.0,
) -> CostForecast:
    """Extrapolate observed current-month settled spend, adding pending once.

    The caller supplies one consistent instance-wide accounting snapshot. Pending
    means all unfinalized cost exposure, including held unknown bills, not just
    actively running calls. The design target is not the configured hard spend cap.

    Observation begins at the later of UTC month start and the earliest durable
    usage/reservation timestamp. Fewer than 24 observed hours leaves the forecast
    unavailable. Otherwise its settled-spend rate continues until the next UTC
    month; pre-installation days are never treated as zero-spend observations.
    Known exposure can still raise pressure before a forecast is available.
    This neither prices calls nor authorizes changes to character behavior.
    """
    for field, value in (
        ("month_settled_cny", month_settled_cny),
        ("pending_cny", pending_cny),
        ("last24h_settled_cny", last24h_settled_cny),
        ("target_monthly_cny", target_monthly_cny),
    ):
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{field} must be finite and nonnegative")
    if target_monthly_cny == 0:
        raise ValueError("target_monthly_cny must be positive")
    if now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(timezone.utc)
    if observed_since is not None:
        if observed_since.utcoffset() is None:
            raise ValueError("observed_since must be timezone-aware")
        observed_since = observed_since.astimezone(timezone.utc)
        if observed_since > now:
            raise ValueError("observed_since must not be in the future")
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month = month_start.replace(
        year=month_start.year + (month_start.month == 12),
        month=month_start.month % 12 + 1,
    )
    observation_start = max(month_start, observed_since or now)
    observed_seconds = (now - observation_start).total_seconds()
    remaining_seconds = (next_month - now).total_seconds()
    projected = None
    if observed_seconds >= 86400:
        projected = (
            month_settled_cny
            + month_settled_cny / observed_seconds * remaining_seconds
            + pending_cny
        )
    pressure_amount = max(month_settled_cny + pending_cny, projected or 0.0)
    pressure = max(
        (
            threshold
            for threshold in (70, 80, 90, 100)
            if pressure_amount >= target_monthly_cny * threshold / 100
        ),
        default=0,
    )
    return {
        "target_monthly_cny": target_monthly_cny,
        "month_settled_cny": month_settled_cny,
        "pending_cny": pending_cny,
        "target_remaining_cny": max(0.0, target_monthly_cny - month_settled_cny - pending_cny),
        "last24h_settled_cny": last24h_settled_cny,
        "observed_hours": observed_seconds / 3600,
        "month_end_projected_cny": projected,
        "projection_status": "available" if projected is not None else "insufficient_history",
        "forecast_pressure_threshold_percent": pressure,
        "projection_method": "observed_current_month_settled_rate_plus_pending_once",
    }
