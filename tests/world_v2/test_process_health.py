from __future__ import annotations

from fastapi.testclient import TestClient

import companion_daemon.app as app_module
from companion_daemon.config import Settings
from companion_daemon.world_v2.process_health import (
    compile_process_health,
    ledger_path_is_writable,
)
from companion_daemon.world_v2.qq_c2c_host import QQC2CDrainResult
from companion_daemon.world_v2.qq_c2c_onebot_app import create_qq_c2c_onebot_app


def _ready_interior(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": "ready",
        "installed": True,
        "semantic_author_count": 1,
        "legacy_interface_invocations": 0,
        "parallel_character_author_conflicts": 0,
        "dual_write_conflicts": 0,
        "topology_issues": [],
        "topology_evidence": {
            "duplicate_purpose_owner_count": 0,
            "legacy_compatibility_route_installed": False,
            "semantic_author_ids": ["character-semantic-author:test"],
        },
    }
    payload.update(overrides)
    return payload


def _qq_app(tmp_path, monkeypatch):
    app = create_qq_c2c_onebot_app(
        adapter="napcat",
        settings=Settings(
            _env_file=None,
            database_path=tmp_path / "qq-process-health.sqlite",
            NAPCAT_ALLOWED_PRIVATE_USER_IDS="10001",
            WORLD_V2_TEXT_ENDPOINT_ENABLED=False,
        ),
        use_fake_model=True,
        scheduler_interval_seconds=3_600,
    )

    async def _healthy_without_world_work(**_kwargs: object) -> QQC2CDrainResult:
        return QQC2CDrainResult(action_statuses=(), background_statuses=())

    monkeypatch.setattr(
        app.state.qq_c2c_host,
        "scheduler_once",
        _healthy_without_world_work,
    )
    return app


def test_compile_process_health_is_running_when_all_checks_pass() -> None:
    verdict = compile_process_health(
        healthy_status="running",
        character_interior=_ready_interior(),
        budget={"monthly_exhausted": False, "daily_exhausted": False},
        scheduler_status="running",
        storage={"status": "ok", "writable": True},
        recall_semantic={"enabled": False},
        external_perception={"enabled": False, "state": "disabled"},
        ledger_writable=True,
    )
    assert verdict.status == "running"
    assert verdict.reason == "ok"
    assert verdict.reasons == ()


def test_compile_process_health_uses_ok_for_http_daemon() -> None:
    verdict = compile_process_health(
        healthy_status="ok",
        character_interior=_ready_interior(),
        capture={"status": "ready"},
        ledger_writable=True,
    )
    assert verdict.status == "ok"
    assert verdict.reason == "ok"


def test_compile_process_health_ci_not_installed_is_unhealthy() -> None:
    verdict = compile_process_health(
        character_interior={"status": "unavailable", "installed": False},
        ledger_writable=True,
    )
    assert verdict.status == "unhealthy"
    assert verdict.reasons == ("character_interior_not_installed",)
    assert "character interior is not installed" in verdict.reason


def test_compile_process_health_topology_issues_are_unhealthy() -> None:
    verdict = compile_process_health(
        character_interior=_ready_interior(
            status="not_ready",
            topology_issues=["projection_unbound", "recall_unbound"],
        ),
        ledger_writable=True,
    )
    assert verdict.status == "unhealthy"
    assert "character_interior_topology" in verdict.reasons
    assert "projection_unbound" in verdict.reason
    assert "recall_unbound" in verdict.reason


def test_compile_process_health_budget_exhausted_is_degraded_not_unhealthy() -> None:
    verdict = compile_process_health(
        character_interior=_ready_interior(),
        budget={
            "monthly_exhausted": True,
            "daily_exhausted": True,
            "soft_daily_exhausted": True,
        },
        ledger_writable=True,
    )
    assert verdict.status == "degraded"
    assert "budget_exhausted" in verdict.reasons
    assert "soft_daily_exhausted" in verdict.reasons
    assert "unhealthy" not in verdict.status
    assert "exhausted" in verdict.reason


def test_compile_process_health_soft_daily_only_is_degraded() -> None:
    verdict = compile_process_health(
        character_interior=_ready_interior(),
        budget={
            "monthly_exhausted": False,
            "daily_exhausted": False,
            "soft_daily_exhausted": True,
        },
        ledger_writable=True,
    )
    assert verdict.status == "degraded"
    assert verdict.reasons == ("soft_daily_exhausted",)


def test_compile_process_health_does_not_alarm_on_disabled_optional_lanes() -> None:
    verdict = compile_process_health(
        character_interior=_ready_interior(),
        scheduler_status="starting",
        recall_semantic={"enabled": False, "last_prefetch_status": "unknown"},
        external_perception={"enabled": False, "state": "disabled"},
        budget={"status": "disabled"},
        ledger_writable=True,
    )
    assert verdict.status == "running"
    assert verdict.reason == "ok"


def test_compile_process_health_recall_and_perception_failures_are_degraded() -> None:
    verdict = compile_process_health(
        character_interior=_ready_interior(),
        recall_semantic={
            "enabled": True,
            "last_prefetch_status": "technical_failure",
            "last_prefetch_failure_code": "semantic_embedding_budget_exhausted",
        },
        external_perception={"enabled": True, "state": "degraded"},
        ledger_writable=True,
    )
    assert verdict.status == "degraded"
    assert "semantic_recall_unavailable" in verdict.reasons
    assert "external_perception_degraded" in verdict.reasons


def test_compile_process_health_ledger_not_writable_is_unhealthy() -> None:
    verdict = compile_process_health(
        character_interior=_ready_interior(),
        ledger_writable=False,
    )
    assert verdict.status == "unhealthy"
    assert "ledger_not_writable" in verdict.reasons
    assert "ledger is not writable" in verdict.reason


def test_compile_process_health_unhealthy_outranks_degraded() -> None:
    verdict = compile_process_health(
        character_interior={"status": "unavailable", "installed": False},
        budget={"monthly_exhausted": True, "daily_exhausted": False},
        ledger_writable=True,
    )
    assert verdict.status == "unhealthy"
    assert "character_interior_not_installed" in verdict.reasons
    assert "budget_exhausted" in verdict.reasons


def test_compile_process_health_ci_technical_failures_are_degraded() -> None:
    verdict = compile_process_health(
        character_interior=_ready_interior(status="degraded"),
        ledger_writable=True,
    )
    assert verdict.status == "degraded"
    assert verdict.reasons == ("character_interior_degraded",)


def test_ledger_path_is_writable_for_existing_and_missing_files(tmp_path) -> None:
    present = tmp_path / "present.sqlite"
    present.write_bytes(b"")
    missing = tmp_path / "missing.sqlite"
    assert ledger_path_is_writable(present) is True
    assert ledger_path_is_writable(missing) is True
    present.chmod(0o444)
    try:
        assert ledger_path_is_writable(present) is False
    finally:
        present.chmod(0o644)


def test_qq_health_reports_running_with_http_200(tmp_path, monkeypatch) -> None:
    app = _qq_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        response = client.get("/health")
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "running"
    assert body["reason"] == "ok"
    assert body["reasons"] == []
    assert body["scheduler"]["character_interior"]["installed"] is True
    assert body["scheduler"]["character_interior"]["status"] == "ready"
    assert body["scheduler"]["storage"]["writable"] is True


def test_qq_health_ci_not_installed_is_unhealthy_with_named_reason(tmp_path, monkeypatch) -> None:
    app = _qq_app(tmp_path, monkeypatch)
    original = app.state.qq_c2c_host.world_health_diagnostics

    async def _without_interior() -> dict[str, object]:
        world = dict(await original())
        world["character_interior"] = {
            "status": "unavailable",
            "installed": False,
            "semantic_author_count": 0,
            "topology_issues": [],
        }
        return world

    monkeypatch.setattr(app.state.qq_c2c_host, "world_health_diagnostics", _without_interior)
    with TestClient(app) as client:
        response = client.get("/health")
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "unhealthy"
    assert "character interior is not installed" in body["reason"]
    assert "character_interior_not_installed" in body["reasons"]


def test_qq_health_topology_issues_are_unhealthy(tmp_path, monkeypatch) -> None:
    app = _qq_app(tmp_path, monkeypatch)
    original = app.state.qq_c2c_host.world_health_diagnostics

    async def _broken_topology() -> dict[str, object]:
        world = dict(await original())
        world["character_interior"] = _ready_interior(
            status="not_ready",
            topology_issues=["projection_unbound"],
        )
        return world

    monkeypatch.setattr(app.state.qq_c2c_host, "world_health_diagnostics", _broken_topology)
    with TestClient(app) as client:
        response = client.get("/health")
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "unhealthy"
    assert "projection_unbound" in body["reason"]
    assert "character_interior_topology" in body["reasons"]


def test_qq_health_budget_exhausted_is_degraded_not_unhealthy(tmp_path, monkeypatch) -> None:
    app = _qq_app(tmp_path, monkeypatch)
    monkeypatch.setattr(
        app.state.qq_c2c_host,
        "usage_budget_health",
        lambda: {
            "monthly_exhausted": True,
            "daily_exhausted": False,
            "soft_daily_exhausted": True,
            "monthly_budget_cny": 80.0,
            "daily_budget_cny": 3.0,
            "soft_daily_budget_cny": 0.5,
        },
    )
    with TestClient(app) as client:
        response = client.get("/health")
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "degraded"
    assert "budget_exhausted" in body["reasons"]
    assert "soft_daily_exhausted" in body["reasons"]
    assert "character interior is not installed" not in body["reason"]


def test_http_daemon_health_budget_exhausted_is_degraded(tmp_path) -> None:
    configured = app_module.create_http_asgi_app(
        settings=Settings(
            _env_file=None,
            database_path=tmp_path / "http-budget-health.sqlite",
        )
    )

    class _ReadyCapture:
        def character_interior_health(self) -> dict[str, object]:
            return _ready_interior()

        def usage_budget_health(self) -> dict[str, object]:
            return {
                "monthly_exhausted": False,
                "daily_exhausted": True,
                "soft_daily_exhausted": True,
            }

        def proactive_source_authority_health(self) -> dict[str, object]:
            return {"status": "ready"}

        async def aclose(self) -> None:
            return None

    configured.state.http_v2_capture = _ReadyCapture()
    with TestClient(configured) as client:
        response = client.get("/health")
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "degraded"
    assert "budget_exhausted" in body["reasons"]
    assert body["world_v2_capture"]["status"] == "ready"
