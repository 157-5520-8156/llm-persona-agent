from __future__ import annotations

import pytest

from companion_daemon.world_v2.model_usage_budget import BackgroundSpendCapDenied
from companion_daemon.world_v2.runtime import WorldRuntime, _BackgroundWorkerIsolated


def _bare_runtime() -> WorldRuntime:
    """One runtime without a ledger: this test only exercises the drain guard."""

    runtime = object.__new__(WorldRuntime)
    runtime._background_worker_failures = {}
    return runtime


@pytest.mark.asyncio
async def test_spent_background_envelope_is_capacity_not_a_technical_failure() -> None:
    """A capped background day must not look like a broken worker.

    Once WORLD_V2_BACKGROUND_DAILY_BUDGET_CNY is spent this denial is routine,
    so it may neither log a traceback, poison the technical backoff, nor be
    re-raised as the drain's failure when nothing else did work.
    """

    runtime = _bare_runtime()

    async def drain():
        raise BackgroundSpendCapDenied("background_daily_budget_exceeded")

    result = await runtime._isolated_background_worker("appraisal", drain)

    assert isinstance(result, _BackgroundWorkerIsolated)
    assert result.exc is None
    assert runtime._background_worker_failures == {}


@pytest.mark.asyncio
async def test_other_background_failures_still_isolate_and_report() -> None:
    runtime = _bare_runtime()

    async def drain():
        raise RuntimeError("provider broke")

    result = await runtime._isolated_background_worker("appraisal", drain)

    assert isinstance(result, _BackgroundWorkerIsolated)
    assert isinstance(result.exc, RuntimeError)


def test_one_switch_lifts_every_model_spend_ceiling(monkeypatch, tmp_path) -> None:
    """One explicit switch must be able to turn the whole budget mechanism off.

    Three of the four ceilings are plain floats with no documented off value:
    setting them to zero denies every call instead of lifting the cap, so there
    was no honest way to disable the mechanism. On the production ledger the
    gates fired like this: 1510 soft_daily_budget_exceeded, 1538
    daily_budget_exceeded, 28 background_daily_budget_exceeded, and 60.1 percent
    of every model call was refused before it left the machine.
    """

    from companion_daemon.config import Settings
    from companion_daemon.world_v2.model_usage_budget import usage_store_for_settings

    monkeypatch.setenv('MONTHLY_BUDGET_CNY', '100')
    monkeypatch.setenv('DAILY_BUDGET_CNY', '8')
    monkeypatch.setenv('SOFT_DAILY_BUDGET_CNY', '6')
    monkeypatch.setenv('WORLD_V2_BACKGROUND_DAILY_BUDGET_CNY', '1.5')

    def admit(flag: str) -> str:
        monkeypatch.setenv('WORLD_V2_MODEL_USAGE_BUDGET_DISABLED', flag)
        settings = Settings(
            database_path=tmp_path / f'budget-switch-{flag}.sqlite', PRIMARY_USER_ID='geoff'
        )
        store = usage_store_for_settings(settings)
        return store.admit_provider_call(
            purpose='world_stimulus_appraisal',
            actor='agent:companion',
            provider='deepseek',
            model='deepseek-v4-flash',
            prompt_characters=1,
            estimated_cny=500,
        )

    # With the mechanism on, a call this large is refused by the monthly ceiling.
    with pytest.raises(BackgroundSpendCapDenied):
        admit('false')

    # With the single switch on, every ceiling is lifted together.
    assert admit('true').startswith('reservation:')


def _store(tmp_path, *, soft: float):
    from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore

    return WorldV2UsageStore(
        path=str(tmp_path / 'tiers.sqlite'),
        monthly_cost_target_cny=100.0,
        monthly_budget_cny=None,
        daily_budget_cny=None,
        soft_daily_budget_cny=soft,
        background_daily_budget_cny=None,
    )


def _deny_reason(store, purpose: str, estimated: float) -> str | None:
    from companion_daemon.world_v2.model_usage_budget import BackgroundSpendCapDenied

    try:
        store.admit_provider_call(
            purpose=purpose, actor='agent:companion', provider='deepseek',
            model='deepseek-v4-flash', prompt_characters=1, estimated_cny=estimated,
        )
    except BackgroundSpendCapDenied as denied:
        return str(denied)
    return None


def test_a_low_priority_lane_is_cut_before_a_high_priority_one(tmp_path) -> None:
    """The last of the envelope goes to the lanes that carry her.

    On the production ledger 1510 denials were soft_daily_budget_exceeded and
    60.1 percent of every model call was refused, spread by call volume:
    activity_lifecycle_choice lost 734 calls and world_stimulus_appraisal 184,
    with no relation to what the work is worth.
    """

    store = _store(tmp_path, soft=10.0)
    # Fill to 60 percent: tier 4 stops at 50, tier 2 still has room to 90.
    _deny_reason(store, 'world_stimulus_appraisal', 6.0)

    assert 'reserved_for_higher_priority_lanes' in (
        _deny_reason(store, 'media_selection', 0.5) or ''
    )
    assert _deny_reason(store, 'activity_lifecycle_choice', 0.5) is None
    assert _deny_reason(store, 'world_stimulus_appraisal', 0.5) is None


def test_a_high_priority_lane_may_use_the_whole_envelope(tmp_path) -> None:
    """A reservation no higher tier ever claims must not waste capacity."""

    store = _store(tmp_path, soft=10.0)
    assert _deny_reason(store, 'world_stimulus_appraisal', 9.5) is None
    assert 'soft_daily_budget_exceeded' in (
        _deny_reason(store, 'world_stimulus_appraisal', 1.0) or ''
    )


def test_the_ceiling_itself_still_applies_to_every_lane(tmp_path) -> None:
    """Priority reorders the last of the envelope; it does not lift the ceiling."""

    store = _store(tmp_path, soft=2.0)
    _deny_reason(store, 'world_stimulus_appraisal', 1.9)

    assert 'soft_daily_budget_exceeded' in (
        _deny_reason(store, 'world_stimulus_appraisal', 1.0) or ''
    )


def test_the_priority_table_is_consistent() -> None:
    from companion_daemon.world_v2.model_usage_budget import (
        BACKGROUND_LANE_PRIORITY,
        BACKGROUND_LANE_TIER_CEILING,
        VISIBLE_INBOUND_PURPOSES,
    )

    assert set(BACKGROUND_LANE_PRIORITY.values()) <= ({1} | set(BACKGROUND_LANE_TIER_CEILING))
    # A later tier must never be allowed to run later into the day than an
    # earlier one, or the ordering means nothing.
    ceilings = [BACKGROUND_LANE_TIER_CEILING[tier] for tier in sorted(BACKGROUND_LANE_TIER_CEILING)]
    assert ceilings == sorted(ceilings, reverse=True)
    assert all(0 < value < 1 for value in ceilings)
    # Tier 1 is the one with no extra ceiling.
    assert 1 not in BACKGROUND_LANE_TIER_CEILING
    # A visible lane is exempt before any tier is consulted.
    assert not (set(BACKGROUND_LANE_PRIORITY) & VISIBLE_INBOUND_PURPOSES)
