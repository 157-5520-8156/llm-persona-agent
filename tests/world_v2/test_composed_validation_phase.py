"""A composed review gets fixed timing, never a second untracked execution."""
import asyncio

import pytest

from companion_daemon.world_v2 import deliberation
from companion_daemon.world_v2.interactive_turn_budget import InteractiveTurnBudgetPolicy


@pytest.mark.asyncio
async def test_phase_reentry_cannot_renew_its_deadline_or_dispatch_after_expiry():
    clock = [0.0]
    marks = []
    budget = InteractiveTurnBudgetPolicy(
        clock=lambda: clock[0], validation_recovery_seconds=2,
    ).start(marker=marks.append)
    state = deliberation._ValidationAttemptState(
        budget=budget, author_deadline=budget.author_candidate_deadline, candidate_key='candidate:one',
    )
    token = deliberation._VALIDATION_ATTEMPT.set(state)
    calls = []

    async def stage():
        calls.append(clock[0])
        assert state.review_inflight
        return 'completed'

    try:
        assert await deliberation.run_validation_review_once(stage, timeout_seconds=10) == 'completed'
        deadline = state.recovery_deadline
        clock[0] = 1.0
        assert await deliberation.run_validation_review_once(stage, timeout_seconds=10) == 'completed'
        assert state.recovery_deadline == deadline == 2.0
        clock[0] = 3.0
        with pytest.raises(TimeoutError, match='validation window exhausted'):
            await deliberation.run_validation_review_once(stage, timeout_seconds=10)
        assert calls == [0.0, 1.0]
        assert marks.count('validation_recovery_started') == 1
        assert not state.review_inflight
        assert not state.truth_boundary_active
        assert 'technical_recovery_started' not in marks
    finally:
        deliberation._VALIDATION_ATTEMPT.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['timeout', 'invalid', 'cancel'])
async def test_composed_stage_never_repeats_calls_or_translates_cancellation(failure):
    calls = []
    original = ValueError('fixed candidate rejected')

    async def stage():
        calls.append(1)
        if failure == 'invalid':
            raise original
        if failure == 'cancel':
            raise asyncio.CancelledError()
        await asyncio.Future()

    expected = {'invalid': ValueError, 'cancel': asyncio.CancelledError, 'timeout': TimeoutError}[failure]
    with pytest.raises(expected) as raised:
        await deliberation.run_validation_review_once(stage, timeout_seconds=0.01)
    if failure == 'invalid':
        assert raised.value is original
    assert calls == [1]
