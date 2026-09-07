from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.declared_due import (
    assert_host_uses_declared_due_only,
    select_clock_wake,
)
from companion_daemon.world_v2.qq_c2c_host import QQC2CHost
from companion_daemon.world_v2.qq_ingress_policy import SQLiteQQIngressStore


NOW = datetime(2026, 9, 7, 4, tzinfo=UTC)


class _WakePlatform:
    def __init__(self) -> None:
        self.logical_time: datetime | None = NOW
        self.tick_targets: list[datetime] = []

    async def current_logical_time(self) -> datetime | None:
        return self.logical_time

    async def action_due_projection(self) -> SimpleNamespace:
        return SimpleNamespace(
            actions=(),
            appraisals=(
                SimpleNamespace(status="active", expires_at=NOW + timedelta(minutes=2)),
            ),
        )

    async def social_initiative_next_due(self) -> datetime:
        return NOW + timedelta(minutes=3)

    async def private_impression_next_due(self) -> datetime:
        return NOW - timedelta(minutes=1)

    async def life_ecology_next_due(self) -> datetime:
        return NOW + timedelta(minutes=4)

    async def tick(self, tick: object) -> SimpleNamespace:
        self.logical_time = getattr(tick, "logical_time_to")
        self.tick_targets.append(self.logical_time)
        return SimpleNamespace(status="observed_only")

    async def drain_scheduled_work(self, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(action_statuses=(), background_statuses=())

    def close(self) -> None:
        pass


def _host(platform: object, tmp_path: Path) -> QQC2CHost:
    return QQC2CHost(
        host=platform,  # type: ignore[arg-type]
        recipient_id="10001",
        canonical_user_id="geoff",
        ingress_store=SQLiteQQIngressStore(tmp_path / "wake.sqlite"),
        ingress_now=lambda: NOW,
        action_due_now=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_wake_snapshot_is_immutable_and_reads_all_dues_without_advancing(
    tmp_path: Path,
) -> None:
    platform = _WakePlatform()
    host = _host(platform, tmp_path)
    try:
        snapshot = await host.scheduler_wake_snapshot()
        assert snapshot.logical_time == NOW
        assert isinstance(snapshot.dues, tuple)
        assert {(due.kind, due.due_at, due.wake_policy) for due in snapshot.dues} == {
            ("appraisal.expiry", NOW + timedelta(minutes=2), "exact_future"),
            ("social.initiative.cadence", NOW + timedelta(minutes=3), "exact_future"),
            ("private_impression.interval", NOW - timedelta(minutes=1), "exact_future"),
            ("life.ecology", NOW + timedelta(minutes=4), "wall_catchup"),
        }
        assert await host.scheduler_wake_snapshot() == snapshot
        assert platform.logical_time == NOW
        assert platform.tick_targets == []
        with pytest.raises(FrozenInstanceError):
            snapshot.logical_time = NOW + timedelta(days=7)  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            snapshot.dues[0].due_at = NOW  # type: ignore[misc]
    finally:
        await host.aclose()


@pytest.mark.asyncio
async def test_snapshot_wakes_match_production_scheduler_and_refresh_after_tick(
    tmp_path: Path,
) -> None:
    platform = _WakePlatform()
    host = _host(platform, tmp_path)
    try:
        before = await host.scheduler_wake_snapshot()
        selected = select_clock_wake(
            after=before.logical_time,
            through=NOW + timedelta(minutes=2),
            dues=before.dues,
        )
        assert selected is not None
        assert selected.due_at == NOW + timedelta(minutes=2)
        await host.scheduler_once(
            observed_at=selected.due_at, max_action_units=0, max_background_units=0
        )
        after = await host.scheduler_wake_snapshot()
        assert after.logical_time == NOW + timedelta(minutes=2)
        assert platform.tick_targets == [NOW + timedelta(minutes=2)]
        # The original frozen observation is unchanged; overdue exact-future
        # candidates remain visible but cannot rewind or jump the clock.
        assert before.logical_time == NOW
        next_wake = select_clock_wake(
            after=after.logical_time,
            through=NOW + timedelta(minutes=3),
            dues=after.dues,
        )
        assert next_wake is not None
        assert next_wake.kind == "social.initiative.cadence"
        assert next_wake.due_at == NOW + timedelta(minutes=3)
    finally:
        await host.aclose()


@pytest.mark.asyncio
async def test_unbootstrapped_host_without_optional_due_owners_has_empty_snapshot(
    tmp_path: Path,
) -> None:
    class _EmptyPlatform:
        async def current_logical_time(self) -> None:
            return None

        def close(self) -> None:
            pass

    host = _host(_EmptyPlatform(), tmp_path)
    try:
        snapshot = await host.scheduler_wake_snapshot()
        assert snapshot.logical_time is None
        assert snapshot.dues == ()
    finally:
        await host.aclose()
    with pytest.raises(RuntimeError, match="closing"):
        await host.scheduler_wake_snapshot()


def test_gate_rejects_public_snapshot_bypassing_the_shared_reader() -> None:
    import companion_daemon.world_v2.qq_c2c_host as host_module

    source = Path(host_module.__file__).read_text(encoding="utf-8")
    broken = source.replace(
        "return await self._read_scheduler_wake_snapshot(logical_time=logical_time)",
        "return SchedulerWakeSnapshot(logical_time=logical_time, dues=())",
    )
    assert broken != source
    with pytest.raises(AssertionError, match="scheduler_wake_snapshot must call the shared"):
        assert_host_uses_declared_due_only(host_source=broken)
