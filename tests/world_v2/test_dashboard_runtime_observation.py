from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import threading

import pytest

from companion_daemon.world_v2.dashboard_runtime_observation import (
    DashboardRuntimeObservationSampler,
)


NOW = datetime(2026, 8, 12, 9, 30, tzinfo=UTC)


def _probe(payload: dict[str, object]):
    return lambda: dict(payload)


def _ready_probes() -> dict[str, object]:
    return {
        "scheduler": _probe({"status": "running"}),
        "character_interior": _probe({"status": "ready"}),
        "local_provider_capacity": _probe({"enabled": True, "status": "idle"}),
        "text_endpoint": _probe({"enabled": True, "status": "ok"}),
        "proactive_source_authority": _probe({"status": "ready"}),
        "life_source_authority": _probe({"status": "ready"}),
        "external_perception_upstream": _probe({"enabled": True, "state": "healthy"}),
        "model_usage_budget": _probe({"monthly_exhausted": False, "daily_exhausted": False}),
        "process_latency": _probe({"status": "ok"}),
        "storage": _probe({"status": "ok"}),
        "expression_episode": _probe({"mode": "stream"}),
        "semantic_recall": _probe({"enabled": True}),
    }


@pytest.mark.asyncio
async def test_runtime_sampler_normalizes_the_fixed_twelve_process_signals() -> None:
    sampler = DashboardRuntimeObservationSampler(
        scheduler=_probe({"status": "running", "last_error": "secret"}),
        character_interior=_probe({"status": "ready", "primary_author_model": "secret"}),
        local_provider_capacity=_probe({"enabled": False, "status": "disabled"}),
        text_endpoint=_probe({"enabled": False, "status": "disabled"}),
        proactive_source_authority=_probe(
            {
                "status": "fact_effects_fail_closed",
                "warning_reasons": ["provider-specific-secret"],
            }
        ),
        life_source_authority=_probe({"status": "unavailable", "reviewer_model": "secret"}),
        external_perception_upstream=_probe(
            {"enabled": False, "state": "disabled", "reason": "mode_off"}
        ),
        model_usage_budget=_probe(
            {"monthly_exhausted": False, "daily_exhausted": False, "cost": 1.23}
        ),
        process_latency=_probe({"status": "not_measured", "sample_ms_p95": 999}),
        storage=_probe({"status": "ok", "database_path": "/secret/db.sqlite"}),
        expression_episode=_probe({"mode": "stream", "turns": 99, "candidate_ms_p95": 123.0}),
        semantic_recall=_probe({"enabled": False, "embedding_version": "provider-secret"}),
        clock=lambda: NOW,
    )

    observation = await sampler.capture()

    assert observation.model_dump(mode="json") == {
        "observed_at": NOW.isoformat().replace("+00:00", "Z"),
        "scheduler_state": "ready",
        "character_interior_state": "ready",
        "local_provider_capacity_state": "disabled",
        "text_endpoint_state": "disabled",
        "proactive_source_authority_state": "degraded",
        "life_source_authority_state": "unavailable",
        "external_perception_upstream_state": "disabled",
        "model_usage_budget_state": "ready",
        "process_latency_state": "warming",
        "storage_state": "ready",
        "expression_episode_state": "ready",
        "expression_episode_mode": "stream",
        "semantic_recall_state": "disabled",
        "semantic_embedding_enabled": False,
        "reasons": [
            {
                "signal": "local_provider_capacity",
                "reason_code": "not_configured",
            },
            {"signal": "text_endpoint", "reason_code": "not_configured"},
            {
                "signal": "proactive_source_authority",
                "reason_code": "source_unavailable",
            },
            {
                "signal": "life_source_authority",
                "reason_code": "composition_unavailable",
            },
            {
                "signal": "external_perception_upstream",
                "reason_code": "not_configured",
            },
            {"signal": "semantic_recall", "reason_code": "not_configured"},
        ],
    }


@pytest.mark.asyncio
async def test_expression_episode_off_is_reserved_and_disabled() -> None:
    probes = _ready_probes()
    probes["expression_episode"] = _probe({"mode": "off"})
    observation = await DashboardRuntimeObservationSampler(
        **probes,  # type: ignore[arg-type]
        clock=lambda: NOW,
    ).capture()

    assert observation.expression_episode_state == "disabled"
    assert observation.expression_episode_mode == "off"
    assert observation.reasons == ()


@pytest.mark.parametrize("failed_signal", tuple(_ready_probes()))
@pytest.mark.asyncio
async def test_one_probe_failure_only_marks_that_signal_unavailable(
    failed_signal: str,
) -> None:
    probes = _ready_probes()

    def _failure() -> dict[str, object]:
        raise RuntimeError("secret provider URL and filesystem path")

    probes[failed_signal] = _failure
    sampler = DashboardRuntimeObservationSampler(
        **probes,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )

    observation = await sampler.capture()
    payload = observation.model_dump(mode="json")
    states = {
        key.removesuffix("_state"): value
        for key, value in payload.items()
        if key.endswith("_state")
    }

    assert states[failed_signal] == "unavailable"
    assert {key: value for key, value in states.items() if key != failed_signal} == {
        key: "ready" for key in states if key != failed_signal
    }
    assert payload["reasons"] == [{"signal": failed_signal, "reason_code": "source_unavailable"}]
    assert "secret" not in str(payload)
    if failed_signal == "expression_episode":
        assert payload["expression_episode_mode"] == "off"
    if failed_signal == "semantic_recall":
        assert payload["semantic_embedding_enabled"] is False


@pytest.mark.asyncio
async def test_observed_at_only_advances_when_the_visible_observation_changes() -> None:
    scheduler_health: dict[str, object] = {
        "status": "running",
        "passes_completed": 1,
    }
    probes = _ready_probes()
    probes["scheduler"] = lambda: dict(scheduler_health)
    later = datetime(2026, 8, 12, 9, 31, tzinfo=UTC)
    clock_values = iter((NOW, later))
    sampler = DashboardRuntimeObservationSampler(
        **probes,  # type: ignore[arg-type]
        clock=lambda: next(clock_values),
    )

    first = await sampler.capture()
    scheduler_health["passes_completed"] = 2
    second = await sampler.capture()
    scheduler_health["status"] = "failing"
    third = await sampler.capture()

    assert second == first
    assert third.observed_at == later
    assert third.scheduler_state == "degraded"
    assert [reason.model_dump(mode="json") for reason in third.reasons] == [
        {
            "signal": "scheduler",
            "reason_code": "source_unavailable",
        }
    ]


@pytest.mark.asyncio
async def test_all_synchronous_probes_run_off_the_event_loop_thread() -> None:
    event_loop_thread = threading.get_ident()
    probe_threads: set[int] = set()
    probes = _ready_probes()

    for name, probe in tuple(probes.items()):

        def _record_thread(
            source=probe,
        ) -> dict[str, object]:
            probe_threads.add(threading.get_ident())
            return source()  # type: ignore[operator]

        probes[name] = _record_thread

    sampler = DashboardRuntimeObservationSampler(
        **probes,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )

    heartbeat = asyncio.create_task(asyncio.sleep(0))
    await sampler.capture()
    await heartbeat

    assert probe_threads
    assert event_loop_thread not in probe_threads


@pytest.mark.parametrize(
    ("signal", "payload"),
    (
        ("local_provider_capacity", {"enabled": True, "status": "active"}),
        ("local_provider_capacity", {"enabled": True, "status": "cooldown"}),
        ("local_provider_capacity", {"enabled": True, "status": "external_busy"}),
        ("text_endpoint", {"enabled": True, "status": "busy"}),
        (
            "text_endpoint",
            {"enabled": True, "status": "not_measured", "prediction_in_flight": True},
        ),
    ),
)
@pytest.mark.asyncio
async def test_capacity_and_endpoint_contention_remain_distinctly_busy(
    signal: str,
    payload: dict[str, object],
) -> None:
    probes = _ready_probes()
    probes[signal] = _probe(payload)
    sampler = DashboardRuntimeObservationSampler(
        **probes,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )

    observation = await sampler.capture()

    assert getattr(observation, f"{signal}_state") == "busy"
    assert observation.reasons == ()


@pytest.mark.parametrize(
    ("prefetch_status", "embedding_status", "expected_state"),
    (
        ("unknown", "unknown", "warming"),
        ("ready", "ready", "ready"),
        ("ready", "degraded", "warming"),
        ("degraded", "degraded", "degraded"),
        ("technical_failure", "unknown", "degraded"),
    ),
)
@pytest.mark.asyncio
async def test_semantic_recall_reports_embedding_readiness_without_raw_trace_details(
    prefetch_status: str,
    embedding_status: str,
    expected_state: str,
) -> None:
    probes = _ready_probes()
    probes["semantic_recall"] = _probe(
        {
            "enabled": True,
            "last_prefetch_status": prefetch_status,
            "last_prefetch_embedding_status": embedding_status,
            "last_prefetch_failure_code": "secret provider failure",
            "embedding_version": "secret provider identity",
            "prefetch_late_semantic_ready_count": 42,
        }
    )
    sampler = DashboardRuntimeObservationSampler(
        **probes,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )

    observation = await sampler.capture()
    payload = observation.model_dump(mode="json")

    assert payload["semantic_recall_state"] == expected_state
    assert payload["semantic_embedding_enabled"] is True
    assert "secret" not in str(payload)
    assert payload["reasons"] == (
        []
        if expected_state in {"ready", "warming"}
        else [{"signal": "semantic_recall", "reason_code": "source_unavailable"}]
    )


@pytest.mark.asyncio
async def test_unsafe_shared_runtime_is_degraded_not_unavailable() -> None:
    probes = _ready_probes()
    probes["life_source_authority"] = _probe({"status": "unsafe_shared_runtime"})
    observation = await DashboardRuntimeObservationSampler(
        **probes,  # type: ignore[arg-type]
        clock=lambda: NOW,
    ).capture()

    assert observation.life_source_authority_state == "degraded"
    assert [reason.model_dump(mode="json") for reason in observation.reasons] == [
        {"signal": "life_source_authority", "reason_code": "source_unavailable"},
    ]


@pytest.mark.asyncio
async def test_soft_daily_budget_exhaustion_is_a_visible_degraded_signal() -> None:
    probes = _ready_probes()
    probes["model_usage_budget"] = _probe(
        {
            "monthly_exhausted": False,
            "daily_exhausted": False,
            "soft_daily_exhausted": True,
        }
    )
    observation = await DashboardRuntimeObservationSampler(
        **probes,  # type: ignore[arg-type]
        clock=lambda: NOW,
    ).capture()

    assert observation.model_usage_budget_state == "degraded"
    assert [reason.model_dump(mode="json") for reason in observation.reasons] == [
        {"signal": "model_usage_budget", "reason_code": "budget_exhausted"},
    ]
