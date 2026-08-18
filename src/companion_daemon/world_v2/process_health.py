"""Compile a glanceable process-liveness verdict from nested /health probes.

This module only classifies already-collected operational evidence.  It never
writes the ledger, never calls a model, and never exits the process.  HTTP
handlers must keep returning 200; launchd KeepAlive watches the process, not
this JSON.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


ProcessHealthStatus = Literal["ok", "running", "degraded", "unhealthy"]
_FindingSeverity = Literal["unhealthy", "degraded"]
_Finding = tuple[_FindingSeverity, str, str]


@dataclass(frozen=True, slots=True)
class ProcessHealth:
    """Top-level process health: one status, one readable reason, stable codes."""

    status: ProcessHealthStatus
    reason: str
    reasons: tuple[str, ...]


def ledger_path_is_writable(path: str | Path | None) -> bool:
    """Return whether this process could open the ledger for write, without writing.

    A missing file is still writable when its parent directory is.  The check
    never creates the database or takes the writer lock.
    """

    if path is None:
        return False
    target = Path(path).expanduser()
    if target.exists():
        return os.access(target, os.W_OK)
    parent = target.parent
    return parent.exists() and os.access(parent, os.W_OK)


def compile_process_health(
    *,
    healthy_status: Literal["ok", "running"] = "running",
    character_interior: Mapping[str, object] | None = None,
    capture: Mapping[str, object] | None = None,
    budget: Mapping[str, object] | None = None,
    scheduler_status: str | None = None,
    storage: Mapping[str, object] | None = None,
    recall_semantic: Mapping[str, object] | None = None,
    external_perception: Mapping[str, object] | None = None,
    ledger_writable: bool | None = None,
    diagnostics_error: str | None = None,
) -> ProcessHealth:
    """Rank nested probes into ``unhealthy``, ``degraded``, or the healthy token.

    Unhealthy means she cannot actually think or persist a turn.  Degraded means
    a capability is limited or off-nominal while inbound chat can still proceed.
    Disabled-by-configuration subsystems are not faults.
    """

    findings: list[_Finding] = []
    if diagnostics_error:
        findings.append(
            (
                "unhealthy",
                "health_diagnostics_failed",
                f"health diagnostics failed ({diagnostics_error})",
            )
        )
    findings.extend(_capture_findings(capture))
    findings.extend(_ledger_findings(storage=storage, ledger_writable=ledger_writable))
    if diagnostics_error is None:
        findings.extend(_character_interior_findings(character_interior))
    findings.extend(_scheduler_findings(scheduler_status))
    findings.extend(_budget_findings(budget))
    findings.extend(_recall_findings(recall_semantic))
    findings.extend(_external_perception_findings(external_perception))

    unhealthy = [item for item in findings if item[0] == "unhealthy"]
    if not findings:
        return ProcessHealth(status=healthy_status, reason="ok", reasons=())
    status: ProcessHealthStatus = "unhealthy" if unhealthy else "degraded"
    return ProcessHealth(
        status=status,
        reason="; ".join(item[2] for item in findings),
        reasons=tuple(item[1] for item in findings),
    )


def _as_mapping(value: object) -> Mapping[str, object] | None:
    if isinstance(value, Mapping):
        return value
    return None


def _as_str_list(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None
    items = tuple(item for item in value if isinstance(item, str))
    if len(items) != len(tuple(value)):
        return None
    return items


def _capture_findings(capture: Mapping[str, object] | None) -> list[_Finding]:
    if capture is None:
        return []
    status = str(capture.get("status") or "")
    if status == "ready":
        return []
    if status == "warming":
        return [
            (
                "unhealthy",
                "world_v2_capture_warming",
                "world v2 capture is still warming",
            )
        ]
    failure = capture.get("failure_code")
    detail = f" ({failure})" if isinstance(failure, str) and failure else ""
    if status == "failed":
        return [
            (
                "unhealthy",
                "world_v2_capture_not_ready",
                f"world v2 capture failed{detail}",
            )
        ]
    label = status if status else "missing"
    return [
        (
            "unhealthy",
            "world_v2_capture_not_ready",
            f"world v2 capture is {label}{detail}",
        )
    ]


def _ledger_findings(
    *,
    storage: Mapping[str, object] | None,
    ledger_writable: bool | None,
) -> list[_Finding]:
    findings: list[_Finding] = []
    if ledger_writable is False:
        findings.append(
            (
                "unhealthy",
                "ledger_not_writable",
                "ledger is not writable",
            )
        )
    payload = _as_mapping(storage)
    if payload is None:
        return findings
    status = str(payload.get("status") or "")
    if status == "error":
        error = payload.get("error")
        detail = f" ({error})" if isinstance(error, str) and error else ""
        findings.append(
            (
                "unhealthy",
                "ledger_storage_error",
                f"ledger storage probe failed{detail}",
            )
        )
    elif status == "warning":
        reasons = _as_str_list(payload.get("warning_reasons")) or ()
        suffix = f" ({', '.join(reasons)})" if reasons else ""
        findings.append(
            (
                "degraded",
                "ledger_storage_warning",
                f"ledger storage growth is in warning{suffix}",
            )
        )
    return findings


def _character_interior_findings(
    health: Mapping[str, object] | None,
) -> list[_Finding]:
    if health is None or not isinstance(health, Mapping):
        return [
            (
                "unhealthy",
                "character_interior_not_installed",
                "character interior is not installed",
            )
        ]
    installed = health.get("installed")
    status = str(health.get("status") or "")
    if installed is not True or status in {"", "unavailable"}:
        return [
            (
                "unhealthy",
                "character_interior_not_installed",
                "character interior is not installed",
            )
        ]

    findings: list[_Finding] = []
    issues = _as_str_list(health.get("topology_issues"))
    if issues is None:
        findings.append(
            (
                "unhealthy",
                "character_interior_topology",
                "character interior topology_issues is missing",
            )
        )
    elif issues:
        findings.append(
            (
                "unhealthy",
                "character_interior_topology",
                "character interior topology is incomplete: " + ", ".join(issues),
            )
        )

    author_count = health.get("semantic_author_count")
    if author_count != 1:
        findings.append(
            (
                "unhealthy",
                "character_interior_semantic_author_count",
                f"character interior semantic author count is {author_count!r}, expected 1",
            )
        )
    conflicts = health.get("parallel_character_author_conflicts")
    if isinstance(conflicts, int) and conflicts != 0:
        findings.append(
            (
                "unhealthy",
                "character_interior_parallel_author_conflicts",
                "character interior has parallel author conflicts",
            )
        )
    dual_write = health.get("dual_write_conflicts")
    if isinstance(dual_write, int) and dual_write != 0:
        findings.append(
            (
                "unhealthy",
                "character_interior_dual_write_conflicts",
                "character interior has dual-write conflicts",
            )
        )
    legacy = health.get("legacy_interface_invocations")
    if isinstance(legacy, int) and legacy != 0:
        findings.append(
            (
                "unhealthy",
                "character_interior_legacy_interface",
                "character interior has legacy interface invocations",
            )
        )

    topology = _as_mapping(health.get("topology_evidence"))
    if topology is None:
        findings.append(
            (
                "unhealthy",
                "character_interior_topology",
                "character interior topology_evidence is missing",
            )
        )
    else:
        if topology.get("duplicate_purpose_owner_count") not in (0, None):
            findings.append(
                (
                    "unhealthy",
                    "character_interior_topology",
                    "character interior has duplicate purpose owners",
                )
            )
        if topology.get("legacy_compatibility_route_installed") is True:
            findings.append(
                (
                    "unhealthy",
                    "character_interior_topology",
                    "character interior still has a legacy compatibility route",
                )
            )
        author_ids = topology.get("semantic_author_ids")
        if not isinstance(author_ids, Sequence) or isinstance(author_ids, (str, bytes)):
            findings.append(
                (
                    "unhealthy",
                    "character_interior_topology",
                    "character interior semantic_author_ids is missing",
                )
            )
        elif len(tuple(author_ids)) != 1:
            findings.append(
                (
                    "unhealthy",
                    "character_interior_topology",
                    "character interior semantic_author_ids is not a single author",
                )
            )

    if findings:
        return findings
    if status == "degraded":
        return [
            (
                "degraded",
                "character_interior_degraded",
                "character interior reports technical failures",
            )
        ]
    if status != "ready":
        return [
            (
                "unhealthy",
                "character_interior_not_ready",
                f"character interior is not ready (status={status})",
            )
        ]
    return []


def _scheduler_findings(status: str | None) -> list[_Finding]:
    if status in {None, "", "running", "starting"}:
        return []
    if status == "stopped":
        return [
            (
                "degraded",
                "scheduler_stopped",
                "scheduler is stopped",
            )
        ]
    if status == "stale":
        return [
            (
                "degraded",
                "scheduler_stale",
                "scheduler is stale",
            )
        ]
    if status == "failing":
        return [
            (
                "degraded",
                "scheduler_failing",
                "scheduler is failing",
            )
        ]
    return [
        (
            "degraded",
            "scheduler_not_running",
            f"scheduler status is {status}",
        )
    ]


def _budget_findings(budget: Mapping[str, object] | None) -> list[_Finding]:
    payload = _as_mapping(budget)
    if payload is None:
        return []
    if str(payload.get("status") or "") == "disabled":
        return []
    findings: list[_Finding] = []
    hard = [name for name in ("monthly_exhausted", "daily_exhausted") if payload.get(name) is True]
    if hard:
        findings.append(
            (
                "degraded",
                "budget_exhausted",
                "model usage budget is exhausted (" + ", ".join(hard) + ")",
            )
        )
    if payload.get("soft_daily_exhausted") is True:
        findings.append(
            (
                "degraded",
                "soft_daily_exhausted",
                "soft daily budget is exhausted",
            )
        )
    return findings


def _recall_findings(recall: Mapping[str, object] | None) -> list[_Finding]:
    payload = _as_mapping(recall)
    if payload is None or payload.get("enabled") is not True:
        return []
    prefetch = str(payload.get("last_prefetch_status") or "")
    if prefetch in {"degraded", "technical_failure"}:
        failure = payload.get("last_prefetch_failure_code")
        detail = f" ({failure})" if isinstance(failure, str) and failure else ""
        return [
            (
                "degraded",
                "semantic_recall_unavailable",
                f"semantic recall is unavailable{detail}",
            )
        ]
    return []


def _external_perception_findings(
    perception: Mapping[str, object] | None,
) -> list[_Finding]:
    payload = _as_mapping(perception)
    if payload is None or payload.get("enabled") is not True:
        return []
    state = str(payload.get("state") or "")
    if state in {"degraded", "failed", "error"}:
        return [
            (
                "degraded",
                "external_perception_degraded",
                f"external world perception is {state}",
            )
        ]
    return []


__all__ = [
    "ProcessHealth",
    "compile_process_health",
    "ledger_path_is_writable",
]
