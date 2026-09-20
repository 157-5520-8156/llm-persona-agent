"""Explicit prehistory setup and read-only preview for one capture-journey owner.

This composition owns no ledger, role model or scheduling policy. It reuses
the normal host's setup operation and authenticated dashboard, and exposes no
HTTP routes that can create another World or send a message.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
import socket

from .dashboard_operator_http import DashboardHomeFetchResult, DashboardHomeSourceError


class SameOwnerDashboardSource:
    def __init__(self):
        self._owner = None
        self._reads = {}

    def attach(self, owner):
        if self._owner is not None and self._owner is not owner:
            raise ValueError("dashboard already has a live owner")
        self._owner = owner

    async def detach(self, owner):
        if self._owner is owner:
            self._owner = None
        # Snapshot compilation may be in a worker thread. Request cancellation
        # must not let the owner close its database before that read finishes.
        pending = [task for task, reader_owner in self._reads.items() if reader_owner is owner]
        if not pending:
            return
        completion = asyncio.gather(*pending, return_exceptions=True)
        cancelled = False
        while not completion.done():
            try:
                await asyncio.shield(completion)
            except asyncio.CancelledError:
                cancelled = True
        completion.result()
        if cancelled:
            raise asyncio.CancelledError()

    def _read_finished(self, task):
        self._reads.pop(task, None)
        if not task.cancelled():
            task.exception()

    async def fetch(self, *, if_none_match=None):
        owner = self._owner
        if owner is None:
            raise DashboardHomeSourceError("owner_unavailable")
        task = asyncio.create_task(owner.dashboard_home_snapshot())
        self._reads[task] = owner
        task.add_done_callback(self._read_finished)
        snapshot = await asyncio.shield(task)
        if self._owner is not owner:
            raise DashboardHomeSourceError("owner_unavailable")
        etag = f'"{snapshot.snapshot_hash}"'
        unchanged = if_none_match == etag
        return DashboardHomeFetchResult(
            snapshot=None if unchanged else snapshot, etag=etag, not_modified=unchanged,
        )


def build_journey_dashboard_app(*, settings, source):
    from companion_daemon.app import create_http_asgi_app

    if not settings.world_v2_dashboard_auth_enabled or not settings.world_v2_dashboard_operator_token:
        raise ValueError("capture dashboard requires a dedicated read-only token")
    app = create_http_asgi_app(settings=settings, dashboard_home_source=source)
    allowed = {
        "/dashboard", "/world-v2/dashboard/session", "/world-v2/dashboard/logout",
        "/world-v2/dashboard/app.js", "/world-v2/dashboard/home",
    }
    app.router.routes[:] = [route for route in app.router.routes if route.path in allowed]
    if {route.path for route in app.router.routes} != allowed:
        raise ValueError("capture dashboard routes are incomplete")
    return app


class LongitudinalDemoSetup:
    """Bounded setup calls, with a single loopback preview across owner restarts."""

    def __init__(self, *, settings, initialize_steps=0, dashboard_port=None, token_file: Path | None = None):
        if type(initialize_steps) is not int or not 0 <= initialize_steps <= 64:
            raise ValueError("prehistory setup must be between zero and 64 steps")
        if (dashboard_port is None) != (token_file is None):
            raise ValueError("dashboard port and token file must be supplied together")
        if dashboard_port is not None and (type(dashboard_port) is not int or not 0 <= dashboard_port <= 65535):
            raise ValueError("dashboard port is outside its valid range")
        self.remaining_steps = initialize_steps
        self._billing_day = datetime.now(UTC).date()
        self.source = SameOwnerDashboardSource()
        self.settings = settings
        self.port = dashboard_port
        self._token = None
        if token_file is not None:
            if not 1 <= token_file.stat().st_size <= 4096:
                raise ValueError("dashboard token file has an invalid size")
            token = token_file.read_text().strip()
            if not token or any(not 32 <= ord(char) <= 126 for char in token):
                raise ValueError("dashboard token file is invalid")
            self._token = token
        self.url = None
        self._server = None
        self._task = None
        self._socket = None

    async def prepare_host(self, owner, *, is_restart):
        self.source.attach(owner)
        await self._start_preview()
        outcomes = []
        if is_restart:
            # Restore a paid choice, but never restart the explicit call budget.
            outcome = await owner.initialize_prehistory_once(allow_model_call=False)
            outcomes.append(outcome)
        else:
            while self.remaining_steps:
                if datetime.now(UTC).date() != self._billing_day:
                    outcomes.append({"status": "billing_period_changed"})
                    break
                self.remaining_steps -= 1
                outcome = await owner.initialize_prehistory_once(allow_model_call=True)
                outcomes.append(outcome)
                if outcome["status"] not in {"retained", "no_change"}:
                    break
        return {
            "prehistory_initialization": outcomes,
            "remaining_setup_steps": self.remaining_steps,
            "dashboard": {"url": self.url, "source": "same_capture_owner", "read_only": True}
            if self.url else None,
        }

    async def detach_host(self, owner):
        await self.source.detach(owner)

    async def _start_preview(self):
        if self.port is None or self._task is not None:
            return
        import uvicorn

        class EmbeddedServer(uvicorn.Server):
            @contextmanager
            def capture_signals(self):
                # The CLI remains the process/signal owner.
                yield

        app = build_journey_dashboard_app(
            settings=self.settings.model_copy(update={
                "world_v2_dashboard_operator_token": self._token,
                "world_v2_dashboard_auth_enabled": True,
            }), source=self.source,
        )
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket = sock
        sock.bind(("127.0.0.1", self.port))
        sock.listen(128)
        sock.setblocking(False)
        self._server = EmbeddedServer(uvicorn.Config(
            app, host="127.0.0.1", port=sock.getsockname()[1], access_log=False,
            log_level="warning", lifespan="on", timeout_graceful_shutdown=5,
        ))
        self._task = asyncio.create_task(self._server.serve(sockets=[sock]), name="journey-dashboard")
        async with asyncio.timeout(10):
            while not self._server.started:
                if self._task.done():
                    self._task.result()
                    raise RuntimeError("capture dashboard did not start")
                await asyncio.sleep(0.01)
        self.url = f"http://127.0.0.1:{sock.getsockname()[1]}/dashboard"

    async def aclose(self):
        try:
            if self._server is not None:
                self._server.should_exit = True
            if self._task is not None:
                try:
                    async with asyncio.timeout(10):
                        await asyncio.shield(self._task)
                finally:
                    if not self._task.done():
                        self._task.cancel()
                        await asyncio.gather(self._task, return_exceptions=True)
        finally:
            if self._socket is not None:
                self._socket.close()
            self._token = None
