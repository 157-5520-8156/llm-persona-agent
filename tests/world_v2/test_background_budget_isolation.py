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
