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
        assert repeated.json() == payload
        assert repeated.headers["etag"] == first.headers["etag"]

        unchanged = client.get(
            path,
            headers={
                "X-World-V2-Internal-Token": TOKEN,
                "If-None-Match": first.headers["etag"],
            },
        )
        assert unchanged.status_code == 304
        assert unchanged.content == b""
        assert unchanged.headers["etag"] == first.headers["etag"]


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
