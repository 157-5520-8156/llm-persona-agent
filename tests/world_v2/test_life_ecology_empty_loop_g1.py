from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.ledger import WorldLedger
from companion_daemon.world_v2.life_ecology_runtime import LifeEcologyRunKey
from companion_daemon.world_v2.life_ecology_trigger_store import (
    LedgerLifeEcologyTriggerStore,
)
from companion_daemon.world_v2.qq_c2c_host import QQC2CHost
from companion_daemon.world_v2.qq_ingress_policy import SQLiteQQIngressStore
from companion_daemon.world_v2.schemas import WorldEvent
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger


WORLD_ID = "world:life-ecology-g1"
START = datetime(2026, 8, 13, 11, 59, tzinfo=UTC)
NOW = START + timedelta(minutes=1)


def _clock_wake() -> WorldEvent:
    return WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:life-ecology:wake:clock",
        world_id=WORLD_ID,
        event_type="ClockAdvanced",
        logical_time=NOW,
        created_at=NOW,
        actor="worker:clock",
        source="test:life-ecology-g1",
        trace_id="trace:wake",
        causation_id="event:world-started",
        correlation_id="correlation:wake",
        idempotency_key="test:life-ecology-g1:wake:clock",
        payload={
            "logical_time_from": START.isoformat(),
            "logical_time_to": NOW.isoformat(),
        },
    )


def _key(wake_event_ref: str = "event:life-ecology:wake:clock") -> LifeEcologyRunKey:
    return LifeEcologyRunKey(
        world_id=WORLD_ID,
        wake_event_ref=wake_event_ref,
        catalog_version="life-ecology.1",
    )


def _process_event_types(ledger: WorldLedger) -> tuple[str, ...]:
    return tuple(
        item.event_type
        for item in ledger.project().committed_world_event_refs
        if item.event_type.startswith("TriggerProcess") or item.event_type == "RandomDrawRecorded"
    )


@pytest.mark.asyncio
async def test_cooldown_empty_loop_does_not_write_trigger_process_quartet() -> None:
    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    ledger.commit(
        (_clock_wake(),),
        expected_world_revision=0,
        expected_deliberation_revision=0,
    )
    store = LedgerLifeEcologyTriggerStore(ledger=ledger, owner_id="worker:g1")
    key = _key()
    claim = await store.claim_or_join(
        key=key,
        trace_id="trace:cooldown",
        correlation_id="correlation:cooldown",
    )
    assert claim.state == "owned"
    assert _process_event_types(ledger) == ()

    await store.complete(key=key, trigger_id=claim.trigger_id, outcome="cooldown")

    assert _process_event_types(ledger) == ()
    assert ledger.project().life_ecology_schedule is None
    assert ledger.project().trigger_processes == ()
    assert ledger.project().world_revision == 1
    completed = await store.claim_or_join(
        key=key,
        trace_id="trace:cooldown:again",
        correlation_id="correlation:cooldown",
    )
    assert completed.state == "completed"


@pytest.mark.asyncio
async def test_idle_empty_loop_keeps_due_time_on_sidecar_not_ledger() -> None:
    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    ledger.commit(
        (_clock_wake(),),
        expected_world_revision=0,
        expected_deliberation_revision=0,
    )
    store = LedgerLifeEcologyTriggerStore(ledger=ledger, owner_id="worker:g1-idle")
    key = _key()
    claim = await store.claim_or_join(
        key=key,
        trace_id="trace:idle",
        correlation_id="correlation:idle",
    )
    await store.complete(key=key, trigger_id=claim.trigger_id, outcome="idle")

    assert _process_event_types(ledger) == ()
    assert ledger.project().life_ecology_schedule is None
    due = store.next_consideration_at()
    assert due is not None
    delay = due - NOW
    assert timedelta(minutes=45) <= delay <= timedelta(hours=8)


@pytest.mark.asyncio
async def test_sidecar_lease_survives_sqlite_restart_without_ledger_claim(
    tmp_path,
) -> None:
    path = tmp_path / "life-ecology-g1.sqlite3"
    key = _key()
    first = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    first.commit(
        (_clock_wake(),),
        expected_world_revision=0,
        expected_deliberation_revision=0,
    )
    owned = await LedgerLifeEcologyTriggerStore(
        ledger=first, owner_id="worker:durable"
    ).claim_or_join(key=key, trace_id="trace:durable", correlation_id="correlation:durable")
    assert owned.state == "owned"
    assert _process_event_types(first) == ()
    first.close()

    restarted = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    joined = await LedgerLifeEcologyTriggerStore(
        ledger=restarted, owner_id="worker:durable"
    ).claim_or_join(key=key, trace_id="trace:restart", correlation_id="correlation:restart")
    assert joined.state == "joined"
    assert _process_event_types(restarted) == ()
    restarted.close()


@pytest.mark.asyncio
async def test_qq_scheduler_does_not_heartbeat_when_life_is_not_due(tmp_path) -> None:
    class _IdleHost:
        def __init__(self) -> None:
            self.logical_time = NOW
            self.ticks = []
            self.life_wakes = []

        async def current_logical_time(self):  # type: ignore[no-untyped-def]
            return self.logical_time

        async def life_ecology_next_due(self):  # type: ignore[no-untyped-def]
            return NOW + timedelta(hours=8)

        async def tick(self, tick):  # type: ignore[no-untyped-def]
            self.ticks.append(tick)
            self.logical_time = tick.logical_time_to
            return SimpleNamespace(status="observed_only")

        async def advance_life_ecology_once(self, **_kwargs):  # type: ignore[no-untyped-def]
            self.life_wakes.append(_kwargs)
            return SimpleNamespace(status="idle")

        async def drain_background_once(self):  # type: ignore[no-untyped-def]
            return None

        async def drain_scheduled_work(self, **_kwargs):  # type: ignore[no-untyped-def]
            return SimpleNamespace(action_statuses=(), background_statuses=())

        def close(self) -> None:
            return None

    platform = _IdleHost()
    host = QQC2CHost(
        host=platform,  # type: ignore[arg-type]
        recipient_id="10001",
        canonical_user_id="geoff",
        ingress_store=SQLiteQQIngressStore(tmp_path / "g1-idle-heartbeat.sqlite"),
        ingress_now=lambda: NOW,
        idle_heartbeat_seconds=600,
    )
    try:
        for seconds in (30, 600, 3_600):
            await host.scheduler_once(
                observed_at=NOW + timedelta(seconds=seconds),
                max_action_units=0,
                max_background_units=0,
            )
    finally:
        await host.aclose()

    assert platform.ticks == []
    assert platform.life_wakes == []


@pytest.mark.asyncio
async def test_qq_scheduler_ticks_exactly_when_life_is_due(tmp_path) -> None:
    due_at = NOW + timedelta(minutes=10)

    class _DueHost:
        def __init__(self) -> None:
            self.logical_time = NOW
            self.ticks = []
            self.life_wakes = []

        async def current_logical_time(self):  # type: ignore[no-untyped-def]
            return self.logical_time

        async def life_ecology_next_due(self):  # type: ignore[no-untyped-def]
            return due_at

        async def tick(self, tick):  # type: ignore[no-untyped-def]
            self.ticks.append(tick)
            self.logical_time = tick.logical_time_to
            return SimpleNamespace(status="observed_only")

        async def advance_life_ecology_once(self, **kwargs):  # type: ignore[no-untyped-def]
            self.life_wakes.append(kwargs)
            return SimpleNamespace(status="idle")

        async def drain_background_once(self):  # type: ignore[no-untyped-def]
            return None

        async def drain_scheduled_work(self, **_kwargs):  # type: ignore[no-untyped-def]
            return SimpleNamespace(action_statuses=(), background_statuses=())

        def close(self) -> None:
            return None

    platform = _DueHost()
    host = QQC2CHost(
        host=platform,  # type: ignore[arg-type]
        recipient_id="10001",
        canonical_user_id="geoff",
        ingress_store=SQLiteQQIngressStore(tmp_path / "g1-life-due.sqlite"),
        ingress_now=lambda: NOW,
        idle_heartbeat_seconds=600,
    )
    try:
        await host.scheduler_once(
            observed_at=due_at,
            max_action_units=0,
            max_background_units=0,
        )
    finally:
        await host.aclose()

    assert len(platform.ticks) == 1
    assert platform.ticks[0].logical_time_to == due_at
    assert platform.ticks[0].reason == "qq_c2c_life_ecology_due_wake"
    assert platform.ticks[0].run_life_ecology is False
    assert len(platform.life_wakes) == 1
