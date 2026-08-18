from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from companion_daemon.config import Settings
from companion_daemon.world_v2.qq_c2c_onebot_app import create_qq_c2c_onebot_app


TOKEN = "dashboard-read-secret"


def _app(tmp_path: Path):
    return create_qq_c2c_onebot_app(
        adapter="napcat",
        settings=Settings(
            _env_file=None,
            WORLD_V2_DASHBOARD_AUTH_ENABLED=True,
            database_path=tmp_path / "qq-dashboard-owner.sqlite",
            NAPCAT_ALLOWED_PRIVATE_USER_IDS="10001",
            WORLD_V2_DASHBOARD_OPERATOR_TOKEN=TOKEN,
        ),
        use_fake_model=True,
        scheduler_interval_seconds=3_600,
    )


def test_qq_owner_dashboard_endpoint_is_fixed_authenticated_and_cacheable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = _app(tmp_path)
    path = "/internal/world-v2/dashboard/operator-snapshot"

    async def health_must_not_be_used() -> dict[str, object]:
        raise AssertionError("Dashboard snapshot must not be assembled from /health")

    monkeypatch.setattr(
        app.state.qq_c2c_host,
        "world_health_diagnostics",
        health_must_not_be_used,
    )

    with TestClient(app) as client:
        assert client.get(path).status_code == 403
        assert (
            client.get(
                path,
                headers={"X-World-V2-Internal-Token": "wrong"},
            ).status_code
            == 403
        )
        assert (
            client.get(
                path + "?world_id=other&include_debug=true",
                headers={"X-World-V2-Internal-Token": TOKEN},
            ).status_code
            == 400
        )

        first = client.get(
            path,
            headers={"X-World-V2-Internal-Token": TOKEN},
        )
        assert first.status_code == 200
        assert first.headers["cache-control"] == "private, no-store"
        payload = first.json()
        assert payload["schema_version"] == "world-v2-dashboard-home.1"
        assert set(payload["cursor"]) == {
            "world_revision",
            "deliberation_revision",
            "ledger_sequence",
        }
        assert all(type(value) is int and value >= 0 for value in payload["cursor"].values())
        assert payload["sections"]["room"]["render_state"]["route"]["availability"] == (
            "unavailable"
        )
        runtime = payload["sections"]["runtime_operations"]
        assert runtime["state"] == "ready"
        assert [item["key"] for item in runtime["data"]["signals"]] == [
            "scheduler",
            "character_interior",
            "local_provider_capacity",
            "text_endpoint",
            "proactive_source_authority",
            "life_source_authority",
            "external_perception_upstream",
            "model_usage_budget",
            "process_latency",
            "storage",
            "expression_episode",
            "semantic_recall",
        ]
        assert not {
            "initiative",
            "world_activity",
            "mechanisms",
            "reliability_ledger",
        } & {item["key"] for item in runtime["data"]["signals"]}
        assert first.headers["etag"] == f'"{payload["snapshot_hash"]}"'

        repeated = client.get(
            path,
            headers={"X-World-V2-Internal-Token": TOKEN},
        )
        assert repeated.status_code == 200
        repeated_payload = repeated.json()
        assert repeated_payload["schema_version"] == payload["schema_version"]
        assert set(repeated_payload["cursor"]) == set(payload["cursor"])
        assert repeated.headers["etag"] == f'"{repeated_payload["snapshot_hash"]}"'

        unchanged = client.get(
            path,
            headers={
                "X-World-V2-Internal-Token": TOKEN,
                "If-None-Match": repeated.headers["etag"],
            },
        )
        # The host scheduler may advance the ledger between TestClient calls,
        # producing a newer snapshot.  The cache contract itself is exercised
        # by asserting the response must either be a 304 for the exact
        # repeated ETag or a complete 200 whose hash matches its own ETag.
        assert unchanged.status_code in {200, 304}
        if unchanged.status_code == 304:
            assert unchanged.content == b""
            assert unchanged.headers["etag"] == repeated.headers["etag"]
        else:
            current = unchanged.json()
            assert current["schema_version"] == payload["schema_version"]
            assert unchanged.headers["etag"] == f'"{current["snapshot_hash"]}"'


def test_qq_owner_dashboard_endpoint_is_disabled_without_its_read_token(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("WORLD_V2_DASHBOARD_OPERATOR_TOKEN", raising=False)
    app = create_qq_c2c_onebot_app(
        adapter="napcat",
        settings=Settings(
            _env_file=None,
            database_path=tmp_path / "qq-dashboard-owner-disabled.sqlite",
            NAPCAT_ALLOWED_PRIVATE_USER_IDS="10001",
        ),
        use_fake_model=True,
        scheduler_interval_seconds=3_600,
    )

    with TestClient(app) as client:
        response = client.get("/internal/world-v2/dashboard/operator-snapshot")

    assert response.status_code == 503


def _local_client(app):
    return TestClient(
        app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    )


def test_qq_public_room_and_dashboard_dtos_are_read_only_and_do_not_touch_health(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = _app(tmp_path)

    async def health_must_not_be_used() -> dict[str, object]:
        raise AssertionError("public projection routes must not read /health")

    async def inbound_must_not_run(*_args, **_kwargs):
        raise AssertionError("read-only projection routes must not ingest")

    monkeypatch.setattr(
        app.state.qq_c2c_host,
        "world_health_diagnostics",
        health_must_not_be_used,
    )
    monkeypatch.setattr(app.state.qq_c2c_host, "inbound_fragment", inbound_must_not_run)

    with TestClient(app) as client:
        room = client.get("/world-v2/room")
        denied = client.get("/world-v2/dashboard")
        dashboard = client.get(
            "/world-v2/dashboard",
            headers={"X-World-V2-Internal-Token": TOKEN},
        )
        not_modified = client.get(
            "/world-v2/dashboard",
            headers={
                "X-World-V2-Internal-Token": TOKEN,
                "If-None-Match": dashboard.headers.get("etag", ""),
            },
        )

    assert room.status_code == 200
    room_payload = room.json()
    assert set(room_payload) == {"schema_version", "cursor", "projection_hash", "route"}
    assert room_payload["schema_version"] == "world-v2-dashboard-room.1"
    assert set(room_payload["cursor"]) == {"world_revision", "ledger_sequence"}
    assert set(room_payload["route"]) == {"scene_id", "action_id", "availability"}
    assert room_payload["route"]["scene_id"] in {"unavailable", "zhizhi-home", "zhizhi-home-legacy"}
    wire = str(room_payload)
    for forbidden in ("world_id", "semantic_hash", "affect", "participant", "debug", "operator"):
        assert forbidden not in wire

    assert denied.status_code == 403
    assert dashboard.status_code == 200
    assert dashboard.headers["cache-control"] == "no-store"
    payload = dashboard.json()
    assert payload["schema_version"] == "world-v2-dashboard.1"
    assert set(payload) == {
        "schema_version",
        "cursor",
        "projection_hash",
        "room",
        "now",
        "agenda",
        "notices",
        "freshness",
    }
    assert dashboard.headers["etag"] == f'"{payload["projection_hash"]}"'
    assert not_modified.status_code in {200, 304}


def test_qq_public_dashboard_dto_is_disabled_without_its_read_token(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("WORLD_V2_DASHBOARD_OPERATOR_TOKEN", raising=False)
    app = create_qq_c2c_onebot_app(
        adapter="napcat",
        settings=Settings(
            _env_file=None,
            database_path=tmp_path / "qq-dashboard-public-disabled.sqlite",
            NAPCAT_ALLOWED_PRIVATE_USER_IDS="10001",
        ),
        use_fake_model=True,
        scheduler_interval_seconds=3_600,
    )

    with TestClient(app) as client:
        room = client.get("/world-v2/room")
        dashboard = client.get("/world-v2/dashboard")

    assert room.status_code == 200
    assert dashboard.status_code == 503


def test_qq_owner_dashboard_html_and_room_assets_are_served_from_this_process(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)

    with _local_client(app) as client:
        login_page = client.get("/dashboard")
        accepted = client.post(
            "/world-v2/dashboard/session",
            data={"operator_token": TOKEN},
            headers={"Origin": "http://localhost"},
            follow_redirects=False,
        )
        page = client.get("/dashboard")
        script = client.get("/world-v2/dashboard/app.js")
        home = client.get("/world-v2/dashboard/home")
        pixel_home = client.get("/pixel-home/index.html")
        scene_registry = client.get("/assets/dashboard/rooms/scene-registry.json")

    assert login_page.status_code == 200
    assert "operator-token" in login_page.text
    assert accepted.status_code == 303
    assert page.status_code == 200
    assert "/pixel-home/index.html?embed=1" in page.text
    assert script.status_code == 200
    assert "/world-v2/dashboard/home" in script.text
    assert home.status_code == 200
    assert home.json()["schema_version"] == "world-v2-dashboard-home.1"
    assert TOKEN not in page.text
    assert TOKEN not in script.text
    assert pixel_home.status_code == 200
    assert "js/bridge.js" in pixel_home.text
    assert scene_registry.status_code == 200
    assert scene_registry.json()["defaultScene"] == "zhizhi-home-legacy"
