"""Sanitize process-local health probes into the owner Dashboard contract.

The sampler is the only translation seam between broad operational health
payloads and :class:`DashboardRuntimeObservation`.  It runs synchronous probes
off the event loop and retains only fixed states, modes, flags, and reason
codes; diagnostic payloads themselves never cross the boundary.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock

from .dashboard_home_snapshot import (
    DashboardRuntimeObservation,
    DashboardRuntimeReason,
    DashboardRuntimeSignalKey,
    DashboardRuntimeState,
)


RuntimeProbe = Callable[[], Mapping[str, object]]


@dataclass(frozen=True, slots=True)
class _NormalizedSignal:
    state: DashboardRuntimeState
    reason_code: str | None = None


def _state(
    value: str,
    *,
    ready: frozenset[str] = frozenset(),
    warming: frozenset[str] = frozenset(),
    degraded: frozenset[str] = frozenset(),
    disabled: frozenset[str] = frozenset(),
    degraded_reason: str = "source_unavailable",
) -> _NormalizedSignal:
    if value in ready:
        return _NormalizedSignal("ready")
    if value in warming:
        return _NormalizedSignal("warming")
    if value in degraded:
        return _NormalizedSignal("degraded", degraded_reason)
    if value in disabled:
        return _NormalizedSignal("disabled", "not_configured")
    return _NormalizedSignal("unavailable", "source_unavailable")


def _scheduler(payload: Mapping[str, object]) -> _NormalizedSignal:
    return _state(
        str(payload.get("status") or ""),
        ready=frozenset({"running"}),
        warming=frozenset({"starting"}),
        degraded=frozenset({"stale", "failing"}),
    )


def _character_interior(payload: Mapping[str, object]) -> _NormalizedSignal:
    status = str(payload.get("status") or "")
    if status == "unavailable" or payload.get("installed") is False:
        return _NormalizedSignal("unavailable", "composition_unavailable")
    return _state(
        status,
        ready=frozenset({"ready"}),
        warming=frozenset({"starting"}),
        degraded=frozenset({"degraded", "not_ready"}),
    )


def _local_provider_capacity(payload: Mapping[str, object]) -> _NormalizedSignal:
    status = str(payload.get("status") or "")
    if status in {"active", "busy", "cooldown", "external_busy"}:
        return _NormalizedSignal("busy")
    return _state(
        status,
        ready=frozenset({"idle"}),
        degraded=frozenset({"degraded", "marker_unavailable", "marker_unreadable"}),
        disabled=frozenset({"disabled"}),
    )


def _text_endpoint(payload: Mapping[str, object]) -> _NormalizedSignal:
    status = str(payload.get("status") or "")
    if status in {"busy", "in_flight"} or payload.get("prediction_in_flight") is True:
        return _NormalizedSignal("busy")
    return _state(
        status,
        ready=frozenset({"ok"}),
        warming=frozenset({"not_measured"}),
        degraded=frozenset({"degraded"}),
        disabled=frozenset({"disabled"}),
    )


def _proactive_source_authority(payload: Mapping[str, object]) -> _NormalizedSignal:
    status = str(payload.get("status") or "")
    if status == "unavailable":
        return _NormalizedSignal("unavailable", "composition_unavailable")
    return _state(
        status,
        ready=frozenset({"ready"}),
        degraded=frozenset({"correlated_guard", "fact_effects_fail_closed"}),
    )


def _life_source_authority(payload: Mapping[str, object]) -> _NormalizedSignal:
    status = str(payload.get("status") or "")
    if status == "unavailable":
        return _NormalizedSignal("unavailable", "composition_unavailable")
    return _state(
        status,
        ready=frozenset({"ready"}),
        degraded=frozenset(
            {
                "operational_isolation_unverified",
                "operational_unqualified",
            }
        ),
    )


def _external_perception_upstream(payload: Mapping[str, object]) -> _NormalizedSignal:
    if payload.get("enabled") is False:
        return _NormalizedSignal("disabled", "not_configured")
    return _state(
        str(payload.get("state") or ""),
        ready=frozenset({"healthy", "ready"}),
        warming=frozenset({"starting", "warming"}),
        degraded=frozenset({"degraded"}),
        disabled=frozenset({"disabled"}),
    )


def _model_usage_budget(payload: Mapping[str, object]) -> _NormalizedSignal:
    if str(payload.get("status") or "") == "disabled":
        return _NormalizedSignal("disabled", "not_configured")
    if payload.get("monthly_exhausted") is True or payload.get("daily_exhausted") is True:
        return _NormalizedSignal("degraded", "budget_exhausted")
    if "monthly_exhausted" in payload and "daily_exhausted" in payload:
        return _NormalizedSignal("ready")
    return _NormalizedSignal("unavailable", "source_unavailable")


def _process_latency(payload: Mapping[str, object]) -> _NormalizedSignal:
    return _state(
        str(payload.get("status") or ""),
        ready=frozenset({"ok"}),
        warming=frozenset({"not_measured"}),
        degraded=frozenset({"warning"}),
        degraded_reason="latency_gate_exceeded",
    )


def _storage(payload: Mapping[str, object]) -> _NormalizedSignal:
    return _state(
        str(payload.get("status") or ""),
        ready=frozenset({"ok"}),
        degraded=frozenset({"warning"}),
    )


def _semantic_recall(payload: Mapping[str, object]) -> _NormalizedSignal:
    if payload.get("enabled") is not True:
        return _NormalizedSignal("disabled", "not_configured")
    prefetch_status = str(payload.get("last_prefetch_status") or "")
    embedding_status = str(payload.get("last_prefetch_embedding_status") or "")
    if prefetch_status in {"degraded", "technical_failure"} or embedding_status == ("degraded"):
        return _NormalizedSignal("degraded", "source_unavailable")
    if prefetch_status == "unknown" or embedding_status == "unknown":
        return _NormalizedSignal("warming")
    return _NormalizedSignal("ready")


class DashboardRuntimeObservationSampler:
    """Capture the fixed twelve runtime signals without retaining raw health."""

    def __init__(
        self,
        *,
        scheduler: RuntimeProbe,
        character_interior: RuntimeProbe,
        local_provider_capacity: RuntimeProbe,
        text_endpoint: RuntimeProbe,
        proactive_source_authority: RuntimeProbe,
        life_source_authority: RuntimeProbe,
        external_perception_upstream: RuntimeProbe,
        model_usage_budget: RuntimeProbe,
        process_latency: RuntimeProbe,
        storage: RuntimeProbe,
        expression_episode: RuntimeProbe,
        semantic_recall: RuntimeProbe,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._probes = {
            "scheduler": scheduler,
            "character_interior": character_interior,
            "local_provider_capacity": local_provider_capacity,
            "text_endpoint": text_endpoint,
            "proactive_source_authority": proactive_source_authority,
            "life_source_authority": life_source_authority,
            "external_perception_upstream": external_perception_upstream,
            "model_usage_budget": model_usage_budget,
            "process_latency": process_latency,
            "storage": storage,
            "expression_episode": expression_episode,
            "semantic_recall": semantic_recall,
        }
        self._clock = clock or (lambda: datetime.now(UTC))
        self._observation_lock = Lock()
        self._last_visible: DashboardRuntimeObservation | None = None
        self._last_observation: DashboardRuntimeObservation | None = None

    async def capture(self) -> DashboardRuntimeObservation:
        """Run every synchronous probe on a worker thread and sanitize it."""

        return await asyncio.to_thread(self._capture_sync)

    def _capture_sync(self) -> DashboardRuntimeObservation:
        payloads: dict[str, Mapping[str, object] | None] = {}
        for name, probe in self._probes.items():
            try:
                payload = probe()
            except Exception:
                payload = None
            payloads[name] = payload if isinstance(payload, Mapping) else None

        def normalize(
            name: str,
            translator: Callable[[Mapping[str, object]], _NormalizedSignal],
        ) -> _NormalizedSignal:
            payload = payloads[name]
            if payload is None:
                return _NormalizedSignal("unavailable", "source_unavailable")
            return translator(payload)

        semantic_payload = payloads["semantic_recall"]
        normalized: dict[DashboardRuntimeSignalKey, _NormalizedSignal] = {
            "scheduler": normalize("scheduler", _scheduler),
            "character_interior": normalize("character_interior", _character_interior),
            "local_provider_capacity": normalize(
                "local_provider_capacity", _local_provider_capacity
            ),
            "text_endpoint": normalize("text_endpoint", _text_endpoint),
            "proactive_source_authority": normalize(
                "proactive_source_authority", _proactive_source_authority
            ),
            "life_source_authority": normalize("life_source_authority", _life_source_authority),
            "external_perception_upstream": normalize(
                "external_perception_upstream", _external_perception_upstream
            ),
            "model_usage_budget": normalize("model_usage_budget", _model_usage_budget),
            "process_latency": normalize("process_latency", _process_latency),
            "storage": normalize("storage", _storage),
            "expression_episode": _NormalizedSignal("unavailable", "source_unavailable"),
            "semantic_recall": normalize("semantic_recall", _semantic_recall),
        }
        expression_payload = payloads["expression_episode"]
        mode = str(expression_payload.get("mode") or "") if expression_payload else ""
        if mode == "off":
            normalized["expression_episode"] = _NormalizedSignal("disabled")
        elif mode in {"shadow", "stream"}:
            normalized["expression_episode"] = _NormalizedSignal("ready")
        else:
            normalized["expression_episode"] = _NormalizedSignal(
                "unavailable", "source_unavailable"
            )
            mode = "off"
        reasons = tuple(
            DashboardRuntimeReason(signal=signal, reason_code=value.reason_code)
            for signal, value in normalized.items()
            if value.reason_code is not None
        )
        visible = DashboardRuntimeObservation(
            observed_at=None,
            scheduler_state=normalized["scheduler"].state,
            character_interior_state=normalized["character_interior"].state,
            local_provider_capacity_state=normalized["local_provider_capacity"].state,
            text_endpoint_state=normalized["text_endpoint"].state,
            proactive_source_authority_state=normalized["proactive_source_authority"].state,
            life_source_authority_state=normalized["life_source_authority"].state,
            external_perception_upstream_state=normalized["external_perception_upstream"].state,
            model_usage_budget_state=normalized["model_usage_budget"].state,
            process_latency_state=normalized["process_latency"].state,
            storage_state=normalized["storage"].state,
            expression_episode_state=normalized["expression_episode"].state,
            expression_episode_mode=mode,
            semantic_recall_state=normalized["semantic_recall"].state,
            semantic_embedding_enabled=(
                semantic_payload is not None and semantic_payload.get("enabled") is True
            ),
            reasons=reasons,
        )
        with self._observation_lock:
            if visible == self._last_visible and self._last_observation is not None:
                return self._last_observation
            observation = DashboardRuntimeObservation.model_validate(
                {
                    **visible.model_dump(mode="python"),
                    "observed_at": self._clock(),
                }
            )
            self._last_visible = visible
            self._last_observation = observation
            return observation


__all__ = ["DashboardRuntimeObservationSampler", "RuntimeProbe"]
