from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from companion_daemon.config import Settings
from companion_daemon.world_v2.qq_c2c_host import QQC2CIngressResult, QQC2CHost
from companion_daemon.world_v2.qq_ingress_policy import (
    MemoryQQIngressStore,
    QQIngressFragment,
    SQLiteQQIngressStore,
)


NOW = datetime(2026, 8, 20, 8, 0, tzinfo=UTC)


def _manual_clock(start: datetime):
    clock = {"now": start}

    async def idle_sleep(delay: float) -> None:
        clock["now"] += timedelta(seconds=max(delay, 0.0))
        await asyncio.sleep(0)

    async def drive(condition, *, step: float = 0.05, limit_seconds: float = 5.0) -> None:
        for _ in range(int(limit_seconds / step)):
            if condition():
                return
            await asyncio.sleep(0)
            if condition():
                return
            clock["now"] += timedelta(seconds=step)
        raise AssertionError("test clock driver exhausted its budget")

    return clock, idle_sleep, drive


def _text(source: str, text: str, *, observed_at: datetime = NOW) -> QQIngressFragment:
    return QQIngressFragment(
        source_event_id=source,
        recipient_id="10001",
        observed_at=observed_at,
        content_shape="text",
        text=text,
    )


class _WorldHost:
    def __init__(self) -> None:
        self.inbounds: list[object] = []
        self.life_calls = 0

    async def inbound(self, inbound):  # type: ignore[no-untyped-def]
        self.inbounds.append(inbound)
        return SimpleNamespace(
            status="observed_only",
            authorized_action_ids=(),
            scheduled_action_ids=(),
        )

    async def advance_life_ecology_once(self, **_kwargs: object) -> SimpleNamespace:
        self.life_calls += 1
        return SimpleNamespace(status="idle", work_status="life:idle")

    async def drain_background_once(self) -> SimpleNamespace:
        return SimpleNamespace(status="idle", work_status=None)

    async def drain_scheduled_work(self, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(action_statuses=(), background_statuses=())

    async def current_logical_time(self) -> datetime:
        return NOW

    async def tick(self, _tick: object) -> SimpleNamespace:
        return SimpleNamespace(status="observed_only", authorized_action_ids=())

    def close(self) -> None:
        return None


class _SlowInboundWorldHost(_WorldHost):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def inbound(self, inbound):  # type: ignore[no-untyped-def]
        self.started.set()
        await self.release.wait()
        return await super().inbound(inbound)


@pytest.mark.asyncio
async def test_accept_returns_before_slow_processing_finishes() -> None:
    clock, idle_sleep, drive = _manual_clock(NOW)
    world = _SlowInboundWorldHost()
    host = QQC2CHost(
        host=world,  # type: ignore[arg-type]
        recipient_id="10001",
        canonical_user_id="geoff",
        ingress_store=MemoryQQIngressStore(),
        ingress_now=lambda: clock["now"],
        ingress_sleep=idle_sleep,
    )
    try:
        started = time.perf_counter()
        result = await host.accept_inbound_fragment(_text("message:slow", "在吗"))
        ack_elapsed = time.perf_counter() - started

        assert result.status == "accepted"
        assert ack_elapsed < 0.05
        await drive(lambda: world.started.is_set())
        assert len(world.inbounds) == 0

        world.release.set()
        task = host._ingress_fragment_tasks["message:slow"]  # noqa: SLF001
        await asyncio.wait_for(task, timeout=1)
        assert len(world.inbounds) == 1
    finally:
        await host.aclose()


@pytest.mark.asyncio
async def test_accept_is_idempotent_for_committed_message_id() -> None:
    clock, idle_sleep, _drive = _manual_clock(NOW)
    world = _WorldHost()
    store = MemoryQQIngressStore()
    host = QQC2CHost(
        host=world,  # type: ignore[arg-type]
        recipient_id="10001",
        canonical_user_id="geoff",
        ingress_store=store,
        ingress_now=lambda: clock["now"],
        ingress_sleep=idle_sleep,
    )
    try:
        first = await host.inbound_fragment(_text("message:dup", "第一条"))
        assert first.status == "observed_only"
        assert len(world.inbounds) == 1

        replay = await host.accept_inbound_fragment(_text("message:dup", "第一条"))
        assert replay.status == "observed_only"
        assert replay.action_id == first.action_id
        assert len(world.inbounds) == 1
        assert "message:dup" not in host._ingress_fragment_tasks  # noqa: SLF001
    finally:
        await host.aclose()


@pytest.mark.asyncio
async def test_restart_recovers_pending_submitted_fragment(tmp_path: Path) -> None:
    path = tmp_path / "fast-ack-recovery.sqlite"
    store = SQLiteQQIngressStore(path)
    due = store.submit(_text("message:recover", "别丢"), received_at=NOW).due_at
    store.close()

    world = _WorldHost()
    host = QQC2CHost(
        host=world,  # type: ignore[arg-type]
        recipient_id="10001",
        canonical_user_id="geoff",
        ingress_store=SQLiteQQIngressStore(path),
        ingress_now=lambda: due,
        ingress_sleep=lambda _delay: asyncio.sleep(0),
    )
    try:
        result = await host.drain_ingress_once()
        assert result is not None
        assert result.status == "observed_only"
        assert len(world.inbounds) == 1
        assert world.inbounds[0].text == "别丢"  # type: ignore[attr-defined]
    finally:
        await host.aclose()


@pytest.mark.asyncio
async def test_fast_ack_burst_preserves_source_event_order() -> None:
    clock, idle_sleep, drive = _manual_clock(NOW)
    world = _WorldHost()
    host = QQC2CHost(
        host=world,  # type: ignore[arg-type]
        recipient_id="10001",
        canonical_user_id="geoff",
        ingress_store=MemoryQQIngressStore(),
        ingress_now=lambda: clock["now"],
        ingress_sleep=idle_sleep,
    )
    texts = ("第一条", "第二条", "第三条")
    try:
        for index, text in enumerate(texts):
            ack = await host.accept_inbound_fragment(
                _text(
                    f"message:order:{index}",
                    text,
                    observed_at=clock["now"],
                )
            )
            assert ack.status == "accepted"
            await drive(lambda: f"message:order:{index}" not in host._ingress_fragment_tasks)  # noqa: SLF001
            clock["now"] += timedelta(seconds=1)
        assert [item.text for item in world.inbounds] == list(texts)  # type: ignore[attr-defined]
    finally:
        await host.aclose()


@pytest.mark.asyncio
async def test_scheduler_life_ecology_yields_during_visible_turn_but_runs_after() -> None:
    world = _WorldHost()
    host = QQC2CHost(
        host=world,  # type: ignore[arg-type]
        recipient_id="10001",
        canonical_user_id="geoff",
        ingress_store=MemoryQQIngressStore(),
        ingress_now=lambda: NOW,
    )
    host._visible_turn_depth = 1  # noqa: SLF001
    try:
        await host._tick_admitted(  # noqa: SLF001
            tick_id="tick:test",
            logical_time_from=NOW - timedelta(minutes=1),
            logical_time_to=NOW,
            observed_at=NOW,
            reason="test",
            run_life_ecology=True,
        )
        assert world.life_calls == 0

        host._visible_turn_depth = 0  # noqa: SLF001
        await host._tick_admitted(  # noqa: SLF001
            tick_id="tick:test-2",
            logical_time_from=NOW - timedelta(minutes=1),
            logical_time_to=NOW,
            observed_at=NOW,
            reason="test",
            run_life_ecology=True,
        )
        assert world.life_calls == 1
    finally:
        await host.aclose()


@pytest.mark.asyncio
async def test_scheduler_background_resumes_after_visible_turn() -> None:
    world = _WorldHost()
    host = QQC2CHost(
        host=world,  # type: ignore[arg-type]
        recipient_id="10001",
        canonical_user_id="geoff",
        ingress_store=MemoryQQIngressStore(),
        ingress_now=lambda: NOW,
    )
    host._visible_turn_depth = 1  # noqa: SLF001
    try:
        result = await host.scheduler_once(
            observed_at=NOW,
            max_action_units=0,
            max_background_units=4,
        )
        assert world.life_calls == 0
        assert result.background_statuses == ()

        host._visible_turn_depth = 0  # noqa: SLF001
        await host.scheduler_once(
            observed_at=NOW + timedelta(seconds=1),
            max_action_units=0,
            max_background_units=0,
        )
        # life ecology only runs on committed clock ticks with run_life_ecology=True;
        # direct background drain is still gated by visible_turn_in_flight().
        host._visible_turn_depth = 0  # noqa: SLF001
        await host._tick_admitted(  # noqa: SLF001
            tick_id="tick:resume",
            logical_time_from=NOW,
            logical_time_to=NOW + timedelta(seconds=1),
            observed_at=NOW + timedelta(seconds=1),
            reason="resume",
            run_life_ecology=True,
        )
        assert world.life_calls == 1
    finally:
        await host.aclose()


@pytest.mark.asyncio
async def test_onebot_http_returns_immediately_while_host_still_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import companion_daemon.napcat_cli as napcat_cli
    import companion_daemon.world_v2.qq_c2c_onebot_app as onebot_v2

    release = asyncio.Event()
    calls: list[str] = []

    class _Host:
        def __init__(self) -> None:
            self._background: list[asyncio.Task[None]] = []

        async def accept_inbound_fragment(self, fragment):  # type: ignore[no-untyped-def]
            calls.append(fragment.source_event_id)

            async def _wait() -> None:
                await release.wait()

            self._background.append(asyncio.create_task(_wait()))
            return QQC2CIngressResult(
                status="accepted",
                action_id=None,
                canonical_user_id="geoff",
            )

        async def scheduler_once(self, **_kwargs: object):
            return None

        async def aclose(self) -> None:
            return None

        async def wait_for_shutdown_quiescence(self) -> None:
            return None

        def latency_samples(self) -> list[object]:
            return []

        async def world_health_diagnostics(self) -> dict[str, object]:
            return {}

        def local_provider_capacity_health(self) -> dict[str, object]:
            return {}

        def text_endpoint_health(self) -> dict[str, object]:
            return {}

        def proactive_source_authority_health(self) -> dict[str, object]:
            return {}

        def life_source_authority_health(self) -> dict[str, object]:
            return {}

        def usage_budget_health(self) -> dict[str, object]:
            return {}

        def external_world_perception_health(self) -> dict[str, object]:
            return {}

    host = _Host()
    settings = Settings(
        QQ_ADAPTER="napcat",
        NAPCAT_ALLOWED_PRIVATE_USER_IDS="10001",
        NAPCAT_ACCESS_TOKEN="test-token",
        NAPCAT_ACCEPT_UNAUTHENTICATED_LOCAL_EVENTS="false",
    )
    monkeypatch.setattr(napcat_cli, "get_settings", lambda: settings)
    monkeypatch.setattr(onebot_v2, "build_qq_c2c_host", lambda **_kwargs: host)

    app = napcat_cli.create_app(adapter="napcat", use_fake_model=True, world_v2_c2c=True)
    with TestClient(app) as client:
        started = time.perf_counter()
        with client.stream(
            "POST",
            "/onebot/event",
            headers={"Authorization": "Bearer test-token"},
            json={
                "post_type": "message",
                "message_type": "private",
                "user_id": "10001",
                "message_id": "onebot-fast-ack-1",
                "raw_message": "快 ack",
            },
        ) as response:
            body = response.read()
        elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert elapsed < 0.2
    assert '"status":"accepted"' in body.decode()
    assert calls == ["onebot-fast-ack-1"]
    assert release.is_set() is False
    release.set()
