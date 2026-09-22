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
