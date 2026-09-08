from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

import companion_daemon.app as app_module
from companion_daemon.config import Settings
from companion_daemon.world_v2.dashboard_home_snapshot import DashboardHomeSnapshotModule
from companion_daemon.world_v2.dashboard_operator_http import (
    DashboardHomeFetchResult,
    DashboardHomeSource,
    DashboardHomeSourceError,
)
from companion_daemon.world_v2.ledger import WorldLedger
from companion_daemon.world_v2.world_v2_dashboard_ui import DASHBOARD_SESSION_COOKIE


NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
OPERATOR_TOKEN = "dashboard-operator-secret"
DELIVERY_TOKEN = "delivery-control-secret"
LEGACY_BROWSER_REFERENCES = (
    "/debug/users",
    "/world-runtime/",
    "/dashboard-static/",
    "CompanionEngine",
    "WorldKernel",
    "localStorage",
    "sessionStorage",
)


class _FixedDashboardHomeSource:
    def __init__(self) -> None:
        self.snapshot = asyncio.run(
            DashboardHomeSnapshotModule(
                ledger=WorldLedger.in_memory(world_id="world:dashboard-ui-test"),
                deployment_id="deployment:dashboard-ui-test",
                boot_id="boot:dashboard-ui-test",
                clock=lambda: NOW,
            ).capture()
        )
        self.if_none_matches: list[str | None] = []

    async def fetch(self, *, if_none_match: str | None = None) -> DashboardHomeFetchResult:
        self.if_none_matches.append(if_none_match)
        etag = f'"{self.snapshot.snapshot_hash}"'
        if if_none_match == etag:
            return DashboardHomeFetchResult(snapshot=None, etag=etag, not_modified=True)
        return DashboardHomeFetchResult(snapshot=self.snapshot, etag=etag, not_modified=False)


class _FailingDashboardHomeSource:
    def __init__(self, code: str) -> None:
        self.code = code

    async def fetch(self, *, if_none_match: str | None = None) -> DashboardHomeFetchResult:
        del if_none_match
        try:
            raise RuntimeError(
                f"private upstream at http://127.0.0.1:8787 used {OPERATOR_TOKEN}"
            )
        except RuntimeError as exc:
            raise DashboardHomeSourceError(self.code) from exc


def _dashboard_app(
    tmp_path: Path,
    *,
    name: str = "dashboard.sqlite",
    dashboard_home_source: DashboardHomeSource | None = None,
):
    source = (
        dashboard_home_source
        if dashboard_home_source is not None
        else _FixedDashboardHomeSource()
    )
    return app_module.create_http_asgi_app(
        settings=Settings(
            _env_file=None,
            WORLD_V2_DASHBOARD_AUTH_ENABLED=True,
            database_path=tmp_path / name,
            DELIVERY_RECONCILIATION_TOKEN=DELIVERY_TOKEN,
            WORLD_V2_DASHBOARD_OPERATOR_TOKEN=OPERATOR_TOKEN,
            DEEPSEEK_API_KEY="dashboard-test-key",
            DEEPSEEK_DEBUG_API_KEY="dashboard-test-debug-key",
        ),
        dashboard_home_source=source,
    )


def _local_client(dashboard_app):
    return TestClient(
        dashboard_app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    )


def _login(client: TestClient, *, token: str = OPERATOR_TOKEN):
    return client.post(
        "/world-v2/dashboard/session",
        data={"operator_token": token},
        headers={"Origin": "http://localhost"},
        follow_redirects=False,
    )


def test_local_dashboard_requires_signed_session_and_never_leaks_operator_token(
    tmp_path: Path,
) -> None:
    dashboard_app = _dashboard_app(tmp_path)

    with _local_client(dashboard_app) as client:
        page = client.get("/dashboard")
        invalid = client.post(
            "/world-v2/dashboard/session",
            data={"operator_token": "wrong"},
            headers={"Origin": "http://localhost"},
            follow_redirects=False,
        )
        delivery_secret = _login(client, token=DELIVERY_TOKEN)
        accepted = _login(client)
        authenticated_page = client.get("/dashboard")

    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert "operator-token" in page.text
    assert "请输入本机配置的 operator token" in page.text
    assert OPERATOR_TOKEN not in page.text
    assert invalid.status_code == 401
    assert delivery_secret.status_code == 401
    assert "set-cookie" not in invalid.headers
    assert accepted.status_code == 303
    assert accepted.headers["location"] == "/dashboard"
    cookie = accepted.headers["set-cookie"]
    assert DASHBOARD_SESSION_COOKIE in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/" in cookie
    assert OPERATOR_TOKEN not in cookie
    assert authenticated_page.status_code == 200
    assert "operator-token" not in authenticated_page.text
    assert "沈知栀 · 生活现场" in authenticated_page.text
    assert "这一刻" in authenticated_page.text
    assert "Snapshot" not in authenticated_page.text
    assert "world-v2-dashboard-home.1-ui5" in authenticated_page.text
    assert 'id="recordingToggle"' in authenticated_page.text
    assert 'id="recordingFocus"' in authenticated_page.text
    assert "<iframe" not in authenticated_page.text


def test_authenticated_dashboard_uses_remote_source_without_bootstrapping_sandbox_host(
    tmp_path: Path,
) -> None:
    dashboard_app = _dashboard_app(tmp_path)

    with _local_client(dashboard_app) as client:
        accepted = _login(client)
        response = client.get("/dashboard")

        assert accepted.status_code == 303
        assert response.status_code == 200
        assert dashboard_app.state.http_v2_capture is None


def test_dashboard_home_requires_dedicated_access_and_preserves_owner_etag(
    tmp_path: Path,
) -> None:
    source = _FixedDashboardHomeSource()
    dashboard_app = _dashboard_app(tmp_path, dashboard_home_source=source)
    expected_etag = f'"{source.snapshot.snapshot_hash}"'

    with _local_client(dashboard_app) as client:
        denied = client.get("/world-v2/dashboard/home")
        wrong_secret = client.get(
            "/world-v2/dashboard/home",
            headers={"X-World-V2-Internal-Token": DELIVERY_TOKEN},
        )
        header_access = client.get(
            "/world-v2/dashboard/home",
            headers={"X-World-V2-Internal-Token": OPERATOR_TOKEN},
        )
        assert _login(client).status_code == 303
        unchanged = client.get(
            "/world-v2/dashboard/home",
            headers={"If-None-Match": expected_etag},
        )

    assert denied.status_code == 403
    assert wrong_secret.status_code == 403
    assert header_access.status_code == 200
    assert header_access.headers["cache-control"] == "private, no-store"
    assert header_access.headers["etag"] == expected_etag
    assert header_access.json() == source.snapshot.to_payload()
    assert unchanged.status_code == 304
    assert unchanged.content == b""
    assert unchanged.headers["etag"] == expected_etag
    assert source.if_none_matches == [None, expected_etag]
    assert dashboard_app.state.http_v2_capture is None


def test_dashboard_home_rejects_caller_selected_projection_parameters(tmp_path: Path) -> None:
    source = _FixedDashboardHomeSource()
    dashboard_app = _dashboard_app(tmp_path, dashboard_home_source=source)

    with _local_client(dashboard_app) as client:
        response = client.get(
            "/world-v2/dashboard/home?world_id=other&viewer=debug",
            headers={"X-World-V2-Internal-Token": OPERATOR_TOKEN},
        )

    assert response.status_code == 400
    assert source.if_none_matches == []


@pytest.mark.parametrize(
    ("source_code", "expected_status"),
    [
        ("owner_unreachable", 503),
        ("owner_unavailable", 503),
        ("owner_auth_misconfigured", 502),
        ("invalid_owner_contract", 502),
    ],
)
def test_dashboard_home_normalizes_owner_failures_without_leaking_upstream_data(
    tmp_path: Path,
    source_code: str,
    expected_status: int,
) -> None:
    dashboard_app = _dashboard_app(
        tmp_path,
        dashboard_home_source=_FailingDashboardHomeSource(source_code),
    )

    with _local_client(dashboard_app) as client:
        assert _login(client).status_code == 303
        response = client.get("/world-v2/dashboard/home")

    assert response.status_code == expected_status
    wire = response.text
    assert OPERATOR_TOKEN not in wire
    assert "127.0.0.1:8787" not in wire
    assert "private upstream" not in wire
    assert source_code not in wire


def test_dashboard_session_mutations_require_same_origin(tmp_path: Path) -> None:
    dashboard_app = _dashboard_app(tmp_path)

    with _local_client(dashboard_app) as client:
        missing_origin = client.post(
            "/world-v2/dashboard/session",
            data={"operator_token": OPERATOR_TOKEN},
            follow_redirects=False,
        )
        cross_origin = client.post(
            "/world-v2/dashboard/session",
            data={"operator_token": OPERATOR_TOKEN},
            headers={"Origin": "https://attacker.example"},
            follow_redirects=False,
        )
        accepted = _login(client)
        logout_missing_origin = client.post(
            "/world-v2/dashboard/logout",
            follow_redirects=False,
        )
        logout_cross_origin = client.post(
            "/world-v2/dashboard/logout",
            headers={"Origin": "https://attacker.example"},
            follow_redirects=False,
        )
        logout = client.post(
            "/world-v2/dashboard/logout",
            headers={"Origin": "http://localhost"},
            follow_redirects=False,
        )

    assert missing_origin.status_code == 403
    assert cross_origin.status_code == 403
    assert "set-cookie" not in missing_origin.headers
    assert "set-cookie" not in cross_origin.headers
    assert accepted.status_code == 303
    assert logout_missing_origin.status_code == 403
    assert logout_cross_origin.status_code == 403
    assert logout.status_code == 303


def test_loopback_dashboard_rejects_non_loopback_host_before_auth(tmp_path: Path) -> None:
    dashboard_app = _dashboard_app(tmp_path)

    with TestClient(
        dashboard_app,
        base_url="http://attacker.example",
        client=("127.0.0.1", 50000),
    ) as client:
        page = client.get("/dashboard")
        login = client.post(
            "/world-v2/dashboard/session",
            data={"operator_token": OPERATOR_TOKEN},
            headers={"Origin": "http://attacker.example"},
            follow_redirects=False,
        )
        snapshot = client.get(
            "/world-v2/dashboard/home",
            headers={"X-World-V2-Internal-Token": OPERATOR_TOKEN},
        )

    assert page.status_code == 403
    assert login.status_code == 403
    assert snapshot.status_code == 403


def test_archived_life_state_health_relay_is_not_deployed(tmp_path: Path) -> None:
    dashboard_app = _dashboard_app(tmp_path)

    with _local_client(dashboard_app) as client:
        response = client.get("/world-v2/life-state")

    assert response.status_code == 404


def test_factory_builds_safe_production_dashboard_source_when_not_injected(
    tmp_path: Path,
) -> None:
    dashboard_app = app_module.create_http_asgi_app(
        settings=Settings(
            _env_file=None,
            WORLD_V2_DASHBOARD_AUTH_ENABLED=True,
            database_path=tmp_path / "production-default-source.sqlite",
            QQ_C2C_ADAPTER_URL="http://127.0.0.1:8787",
            WORLD_V2_DASHBOARD_OPERATOR_TOKEN=OPERATOR_TOKEN,
        )
    )

    with _local_client(dashboard_app) as client:
        assert _login(client).status_code == 303
        page = client.get("/dashboard")

    assert page.status_code == 200
    assert dashboard_app.state.http_v2_capture is None


def test_module_level_production_app_builds_dashboard_source_from_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        WORLD_V2_DASHBOARD_AUTH_ENABLED=True,
        database_path=tmp_path / "module-production-source.sqlite",
        QQ_C2C_ADAPTER_URL="http://127.0.0.1:8787",
        WORLD_V2_DASHBOARD_OPERATOR_TOKEN=OPERATOR_TOKEN,
    )
    monkeypatch.setattr(app_module, "get_settings", lambda: settings)
    monkeypatch.setattr(app_module.app.state, "dashboard_home_source", None, raising=False)

    with TestClient(
        app_module.app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    ) as client:
        assert _login(client).status_code == 303
        page = client.get("/dashboard")

    assert page.status_code == 200


def test_module_level_production_app_fails_closed_for_unsafe_owner_origin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        WORLD_V2_DASHBOARD_AUTH_ENABLED=True,
        database_path=tmp_path / "module-unsafe-source.sqlite",
        QQ_C2C_ADAPTER_URL="http://owner.example:8787/private",
        WORLD_V2_DASHBOARD_OPERATOR_TOKEN=OPERATOR_TOKEN,
    )
    monkeypatch.setattr(app_module, "get_settings", lambda: settings)
    monkeypatch.setattr(app_module.app.state, "dashboard_home_source", None, raising=False)

    with TestClient(
        app_module.app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
        raise_server_exceptions=False,
    ) as client:
        first = client.get("/dashboard")
        repeated = client.get("/dashboard")

    assert first.status_code == 503
    assert repeated.status_code == 503
    assert "owner.example" not in first.text
    assert "/private" not in first.text


def test_factory_rejects_unsafe_production_dashboard_owner_origin(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="loopback"):
        app_module.create_http_asgi_app(
            settings=Settings(
                _env_file=None,
                WORLD_V2_DASHBOARD_AUTH_ENABLED=True,
                database_path=tmp_path / "unsafe-owner-source.sqlite",
                QQ_C2C_ADAPTER_URL="http://owner.example:8787",
                WORLD_V2_DASHBOARD_OPERATOR_TOKEN=OPERATOR_TOKEN,
            )
        )


def test_hot_dashboard_uses_only_v2_dtos_and_static_room_resources(tmp_path: Path) -> None:
    dashboard_app = _dashboard_app(tmp_path)
    app_module._http_v2_capture(asgi_app=dashboard_app, bootstrap_at=NOW)

    with _local_client(dashboard_app) as client:
        assert _login(client).status_code == 303
        page = client.get("/dashboard")
        script = client.get("/world-v2/dashboard/app.js")
        dashboard_dto = client.get("/world-v2/dashboard")
        room_dto = client.get("/world-v2/room")
        room_page = client.get("/pixel-home/index.html")
        scene_registry = client.get("/assets/dashboard/rooms/scene-registry.json")

    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert "/world-v2/dashboard/app.js" in page.text
    # The life view never starts the room. Its existing independent routes
    # remain available to explicit callers below.
    assert "<iframe" not in page.text
    assert "/pixel-home/index.html?embed=1" not in page.text
    assert "/pixel-home/index.html?edit=1" not in page.text
    assert "/world-v2/dashboard/logout" in page.text
    assert "zhizhi-room-isometric" not in page.text
    assert script.status_code == 200
    assert script.headers["cache-control"] == "no-store"
    # The browser shell reads only the QQ owner's typed snapshot.  Health is a
    # liveness surface, not a browser contract, and the archived relay is gone.
    assert "/world-v2/dashboard/home" in script.text
    assert "/world-v2/life-state" not in script.text
    assert "'/health'" not in script.text
    assert dashboard_dto.status_code == 200
    assert dashboard_dto.json()["schema_version"] == "world-v2-dashboard.1"
    assert room_dto.status_code == 200
    assert room_dto.json()["schema_version"] == "world-v2-dashboard-room.1"
    assert room_page.status_code == 200
    assert "js/bridge.js" in room_page.text
    assert scene_registry.status_code == 200
    for forbidden in LEGACY_BROWSER_REFERENCES:
        assert forbidden not in page.text
        assert forbidden not in script.text
    assert OPERATOR_TOKEN not in page.text
    assert OPERATOR_TOKEN not in script.text


def test_dashboard_session_is_instance_bound_and_dedicated_header_auth_remains_available(
    tmp_path: Path,
) -> None:
    first_app = _dashboard_app(tmp_path, name="first.sqlite")
    second_app = _dashboard_app(tmp_path, name="second.sqlite")
    app_module._http_v2_capture(asgi_app=first_app, bootstrap_at=NOW)
    app_module._http_v2_capture(asgi_app=second_app, bootstrap_at=NOW)

    with _local_client(first_app) as first_client:
        assert _login(first_client).status_code == 303
        first_cookie = first_client.cookies.get(DASHBOARD_SESSION_COOKIE)

    with _local_client(second_app) as second_client:
        second_client.cookies.set(DASHBOARD_SESSION_COOKIE, first_cookie)
        local_page = second_client.get("/dashboard")
        header_access = second_client.get(
            "/world-v2/dashboard",
            headers={"X-World-V2-Internal-Token": OPERATOR_TOKEN},
        )

    assert local_page.status_code == 200
    assert "operator-token" in local_page.text
    assert header_access.status_code == 200


def test_dashboard_without_configured_operator_token_is_unavailable(tmp_path: Path) -> None:
    dashboard_app = app_module.create_http_asgi_app(
        # This test asserts the unconfigured behavior; do not let the
        # developer's repository-level .env turn it into a configured app.
        settings=Settings(
            _env_file=None,
            WORLD_V2_DASHBOARD_AUTH_ENABLED=True,
            database_path=tmp_path / "disabled.sqlite",
        )
    )

    with _local_client(dashboard_app) as client:
        page = client.get("/dashboard")
        login = client.post(
            "/world-v2/dashboard/session",
            data={"operator_token": "anything"},
            headers={"Origin": "http://localhost"},
            follow_redirects=False,
        )
        static_resource = client.get("/assets/dashboard/rooms/scene-registry.json")

    assert page.status_code == 503
    assert login.status_code == 503
    assert static_resource.status_code == 200


def test_remote_dashboard_is_rejected_before_session_authentication(tmp_path: Path) -> None:
    dashboard_app = _dashboard_app(tmp_path)
    app_module._http_v2_capture(asgi_app=dashboard_app, bootstrap_at=NOW)

    with TestClient(dashboard_app, client=("192.0.2.10", 50000)) as client:
        page = client.get("/dashboard")

    assert page.status_code == 403
    assert "operator-token" not in page.text
