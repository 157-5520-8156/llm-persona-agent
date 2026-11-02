from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from companion_daemon.world_v2.action_pump import ActionPump, ActionPumpResult
from companion_daemon.world_v2.errors import ConcurrencyConflict, IdempotencyConflict


class _ScriptedActionPump(ActionPump):
    """Exercise the public retry contract without constructing an Action graph."""

    def __init__(
        self,
        outcomes: list[Exception | ActionPumpResult],
    ) -> None:
        self.outcomes = outcomes
        self.calls = 0

    async def _drain_once(
        self,
        *,
        target_action_id: str | None = None,
        excluded_action_ids: set[str] | None = None,
        provider_accepted_reconciliation_gate: object | None = None,
    ) -> ActionPumpResult:
        del target_action_id, excluded_action_ids, provider_accepted_reconciliation_gate
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _entrypoint(
    *,
    targeted: bool,
) -> Callable[[_ScriptedActionPump], Awaitable[ActionPumpResult]]:
    if targeted:
        return lambda pump: pump.drain_action("action:test")
    return lambda pump: pump.drain_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("targeted", [False, True])
@pytest.mark.asyncio
async def test_idempotency_conflict_is_permanent_and_propagated_without_retry(
    targeted: bool,
) -> None:
    conflict = IdempotencyConflict("provider receipt identity has different content")
    pump = _ScriptedActionPump([conflict, ActionPumpResult(status="idle")])

    with pytest.raises(IdempotencyConflict) as raised:
        await _entrypoint(targeted=targeted)(pump)

    assert raised.value is conflict
    assert pump.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("targeted", [False, True])
@pytest.mark.asyncio
async def test_concurrency_conflict_remains_a_bounded_retry(
    targeted: bool,
) -> None:
    expected = ActionPumpResult(action_id="action:test", status="settled")
    pump = _ScriptedActionPump(
        [ConcurrencyConflict("stale cursor"), expected],
    )

    result = await _entrypoint(targeted=targeted)(pump)

    assert result == expected
    assert pump.calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("targeted", [False, True])
@pytest.mark.asyncio
async def test_persistent_concurrency_conflict_stops_after_three_attempts(
    targeted: bool,
) -> None:
    pump = _ScriptedActionPump(
        [
            ConcurrencyConflict("stale cursor 1"),
            ConcurrencyConflict("stale cursor 2"),
            ConcurrencyConflict("stale cursor 3"),
        ],
    )

    with pytest.raises(ConcurrencyConflict, match="did not converge"):
        await _entrypoint(targeted=targeted)(pump)

    assert pump.calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("sqlite", [False, True])
async def test_exact_claim_survives_an_unrelated_world_commit_before_dispatch(
    tmp_path, monkeypatch, sqlite
):
    from datetime import timedelta
    from test_action_pump import _ready_ledger, _DeliveredExecutor, WORLD, NOW
    from companion_daemon.world_v2.runtime import WorldRuntime
    from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
    from companion_daemon.world_v2.schemas import ClockObservation

    ledger = _ready_ledger(
        ledger=SQLiteWorldLedger(path=tmp_path / "dispatch.sqlite", world_id=WORLD)
        if sqlite
        else None
    )
    executor = _DeliveredExecutor()
    runtime = WorldRuntime(
        world_id=WORLD, ledger=ledger, action_executor=executor, action_pump_owner="pump:primary"
    )
    original = ActionPump._commit_event
    injected = False

    async def conflict(self, **kwargs):
        nonlocal injected
        if kwargs["event_type"] == "ActionDispatchStarted" and not injected:
            injected = True
            await runtime.advance(
                ClockObservation(
                    schema_version="world-v2.1",
                    tick_id="tick:interleaved",
                    world_id=WORLD,
                    logical_time=NOW + timedelta(seconds=1),
                    created_at=NOW + timedelta(seconds=1),
                    trace_id="trace:interleaved",
                    causation_id="scheduler:interleaved",
                    correlation_id="conversation:1",
                    logical_time_from=NOW,
                    logical_time_to=NOW + timedelta(seconds=1),
                    reason="Unrelated commit between exact claim and dispatch CAS.",
                )
            )
        return await original(self, **kwargs)

    monkeypatch.setattr(ActionPump, "_commit_event", conflict)
    try:
        result = await runtime.drain_actions_once()
        assert injected and result.status == "settled"
        assert executor.dispatch_calls == 1 and ledger.project().actions[0].state == "delivered"
        types = [x.event.event_type for x in ledger.export_replay_evidence().events]
        assert types.count("ActionClaimed") == types.count("ActionDispatchStarted") == 1
        assert ledger.rebuild() == ledger.project()
    finally:
        if sqlite:
            ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("interleaving", ["expired", "already_started", "permission_withdrawn"])
async def test_dispatch_cas_retry_cannot_recover_lost_permission(monkeypatch, interleaving):
    from datetime import timedelta
    from test_action_pump import _ready_ledger, _DeliveredExecutor, WORLD, NOW
    from companion_daemon.world_v2.runtime import WorldRuntime
    from companion_daemon.world_v2.schemas import ClockObservation

    ledger = _ready_ledger()
    executor = _DeliveredExecutor()
    runtime = WorldRuntime(
        world_id=WORLD, ledger=ledger, action_executor=executor, action_pump_owner="pump:primary"
    )
    original = ActionPump._commit_event
    allowed = ActionPump._expression_dispatch_allowed
    injected = False

    async def conflict(self, **kwargs):
        nonlocal injected
        if kwargs["event_type"] == "ActionDispatchStarted" and not injected:
            injected = True
            if interleaving == "already_started":
                await original(self, **kwargs)
                raise ConcurrencyConflict(
                    "The same durable handoff now exists; never dispatch it again."
                )
            seconds = 121 if interleaving == "expired" else 1
            await runtime.advance(
                ClockObservation(
                    schema_version="world-v2.1",
                    tick_id="tick:lost-permission",
                    world_id=WORLD,
                    logical_time=NOW + timedelta(seconds=seconds),
                    created_at=NOW + timedelta(seconds=seconds),
                    trace_id="trace:lost-permission",
                    causation_id="scheduler:lost-permission",
                    correlation_id="conversation:1",
                    logical_time_from=NOW,
                    logical_time_to=NOW + timedelta(seconds=seconds),
                    reason="Recheck exact claim and current permission after contention.",
                )
            )
        return await original(self, **kwargs)

    monkeypatch.setattr(ActionPump, "_commit_event", conflict)
    monkeypatch.setattr(
        ActionPump,
        "_expression_dispatch_allowed",
        staticmethod(
            lambda a, p: (
                False if injected and interleaving == "permission_withdrawn" else allowed(a, p)
            )
        ),
    )
    result = await runtime.drain_actions_once()
    assert (
        injected
        and result.status in {"not_due", "owned_elsewhere"}
        and executor.dispatch_calls == 0
    )
    types = [x.event.event_type for x in ledger.export_replay_evidence().events]
    assert types.count("ActionClaimed") == 1
    assert types.count("ActionDispatchStarted") == (1 if interleaving == "already_started" else 0)
    assert ledger.rebuild() == ledger.project()
