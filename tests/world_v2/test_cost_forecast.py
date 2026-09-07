from datetime import datetime, timedelta, timezone

import pytest

from companion_daemon.world_v2.cost_forecast import forecast_monthly_cost


def test_month_end_forecast_adds_pending_exposure_once() -> None:
    forecast = forecast_monthly_cost(
        now=datetime(2026, 9, 16, tzinfo=timezone.utc),
        observed_since=datetime(2026, 9, 1, tzinfo=timezone.utc),
        month_settled_cny=45.0,
        pending_cny=7.0,
        last24h_settled_cny=8.0,
    )

    # Fifteen observed days at CNY 3/day leave fifteen days in September.
    # The CNY 7 pending amount is exposure, not another extrapolated burn rate.
    assert forecast["month_end_projected_cny"] == pytest.approx(97.0)
    assert forecast["projection_status"] == "available"
    assert forecast["observed_hours"] == 360.0
    assert forecast["target_remaining_cny"] == 48.0
    assert forecast["last24h_settled_cny"] == 8.0
    assert forecast["target_monthly_cny"] == 100.0
    assert forecast["forecast_pressure_threshold_percent"] == 90


@pytest.mark.parametrize(
    ("observed_since", "settled", "pending", "hours", "pressure"),
    [
        (None, 0.0, 0.0, 0.0, 0),
        (datetime(2026, 9, 15, 12, tzinfo=timezone.utc), 81.0, 3.0, 12.0, 80),
    ],
)
def test_insufficient_history_has_no_projection_but_retains_known_exposure(
    observed_since: datetime | None,
    settled: float,
    pending: float,
    hours: float,
    pressure: int,
) -> None:
    forecast = forecast_monthly_cost(
        now=datetime(2026, 9, 16, tzinfo=timezone.utc),
        observed_since=observed_since,
        month_settled_cny=settled,
        pending_cny=pending,
        last24h_settled_cny=settled,
    )

    assert forecast["projection_status"] == "insufficient_history"
    assert forecast["month_end_projected_cny"] is None
    assert forecast["observed_hours"] == hours
    assert forecast["month_settled_cny"] == settled
    assert forecast["pending_cny"] == pending
    assert forecast["forecast_pressure_threshold_percent"] == pressure


@pytest.mark.parametrize(
    ("year", "month", "expected"),
    [
        (2026, 9, 30.0),
        (2026, 12, 31.0),
        (2027, 2, 28.0),
        (2028, 2, 29.0),
        (2000, 2, 29.0),
        (2100, 2, 28.0),
    ],
)
def test_forecast_uses_actual_utc_calendar_month_length(
    year: int,
    month: int,
    expected: float,
) -> None:
    forecast = forecast_monthly_cost(
        now=datetime(year, month, 16, tzinfo=timezone.utc),
        observed_since=datetime(year - 1, 1, 1, tzinfo=timezone.utc),
        month_settled_cny=15.0,
        pending_cny=0.0,
        last24h_settled_cny=1.0,
    )

    # Fifteen observed days at CNY 1/day, including a December year rollover.
    assert forecast["month_end_projected_cny"] == pytest.approx(expected)
    assert forecast["observed_hours"] == 360.0


def test_new_installation_does_not_include_days_before_first_observation() -> None:
    forecast = forecast_monthly_cost(
        now=datetime(2026, 9, 16, 12, tzinfo=timezone.utc),
        observed_since=datetime(2026, 9, 15, 12, tzinfo=timezone.utc),
        month_settled_cny=3.0,
        pending_cny=1.0,
        last24h_settled_cny=3.0,
    )

    # CNY 3 over one day, then 14.5 remaining days and one pending yuan.
    assert forecast["projection_status"] == "available"
    assert forecast["observed_hours"] == 24.0
    assert forecast["month_end_projected_cny"] == pytest.approx(47.5)


def test_exact_month_boundary_needs_current_month_history() -> None:
    forecast = forecast_monthly_cost(
        now=datetime(2027, 1, 1, tzinfo=timezone.utc),
        observed_since=datetime(2026, 11, 1, tzinfo=timezone.utc),
        month_settled_cny=0.0,
        pending_cny=5.0,
        last24h_settled_cny=9.0,
    )

    assert forecast["projection_status"] == "insufficient_history"
    assert forecast["month_end_projected_cny"] is None
    assert forecast["observed_hours"] == 0.0
    # The rolling burn can include yesterday without becoming this month's spend.
    assert forecast["last24h_settled_cny"] == 9.0
    assert forecast["target_remaining_cny"] == 95.0


def test_aware_offsets_are_normalized_before_determining_the_month() -> None:
    forecast = forecast_monthly_cost(
        now=datetime(2026, 10, 1, tzinfo=timezone(timedelta(hours=8))),
        observed_since=datetime(2026, 9, 1, tzinfo=timezone.utc),
        month_settled_cny=89.0,
        pending_cny=2.0,
        last24h_settled_cny=3.0,
    )

    # It is still September 30, 16:00 UTC: eight hours at CNY 3/day remain.
    assert forecast["month_end_projected_cny"] == pytest.approx(92.0)


@pytest.mark.parametrize(
    ("exposure", "pressure", "remaining"),
    [
        (139.99, 0, 60.01),
        (140.0, 70, 60.0),
        (160.0, 80, 40.0),
        (180.0, 90, 20.0),
        (200.0, 100, 0.0),
        (250.0, 100, 0.0),
    ],
)
def test_pressure_thresholds_scale_with_design_target_even_without_history(
    exposure: float,
    pressure: int,
    remaining: float,
) -> None:
    forecast = forecast_monthly_cost(
        now=datetime(2026, 9, 16, tzinfo=timezone.utc),
        observed_since=None,
        month_settled_cny=0.0,
        pending_cny=exposure,
        last24h_settled_cny=0.0,
        target_monthly_cny=200.0,
    )

    assert forecast["forecast_pressure_threshold_percent"] == pressure
    assert forecast["target_remaining_cny"] == pytest.approx(remaining)


@pytest.mark.parametrize(
    "field",
    [
        "month_settled_cny",
        "pending_cny",
        "last24h_settled_cny",
        "target_monthly_cny",
    ],
)
@pytest.mark.parametrize("amount", [-1.0, float("nan"), float("inf")])
def test_invalid_amounts_cannot_be_reported_as_a_valid_forecast(field: str, amount: float) -> None:
    amounts = {
        "month_settled_cny": 1.0,
        "pending_cny": 1.0,
        "last24h_settled_cny": 1.0,
        "target_monthly_cny": 100.0,
    }
    amounts[field] = amount

    with pytest.raises(ValueError, match=field):
        forecast_monthly_cost(
            now=datetime(2026, 9, 16, tzinfo=timezone.utc),
            observed_since=datetime(2026, 9, 1, tzinfo=timezone.utc),
            **amounts,
        )


def test_design_target_must_be_positive() -> None:
    with pytest.raises(ValueError, match="target_monthly_cny"):
        forecast_monthly_cost(
            now=datetime(2026, 9, 16, tzinfo=timezone.utc),
            observed_since=None,
            month_settled_cny=0.0,
            pending_cny=0.0,
            last24h_settled_cny=0.0,
            target_monthly_cny=0.0,
        )


@pytest.mark.parametrize(
    ("now", "observed_since", "error_field"),
    [
        (datetime(2026, 9, 16), None, "now"),
        (datetime(2026, 9, 16, tzinfo=timezone.utc), datetime(2026, 9, 1), "observed_since"),
        (
            datetime(2026, 9, 16, tzinfo=timezone.utc),
            datetime(2026, 9, 17, tzinfo=timezone.utc),
            "observed_since",
        ),
    ],
)
def test_invalid_timestamps_do_not_borrow_the_process_timezone_or_future_history(
    now: datetime,
    observed_since: datetime | None,
    error_field: str,
) -> None:
    with pytest.raises(ValueError, match=error_field):
        forecast_monthly_cost(
            now=now,
            observed_since=observed_since,
            month_settled_cny=1.0,
            pending_cny=0.0,
            last24h_settled_cny=1.0,
        )
