"""Deterministic publishability check for sourced life photos.

The production media lane still generates images, but it does not ask a second
model to look at the pixels.  A photo may leave this gate only when the frozen
plan names a lived event, binds a non-legacy evidence pointer, and the artifact
file actually exists.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from companion_daemon.event_media import MediaInspection, MediaPlan, MediaRenderer, MediaRenderFailure, RenderResult

INSPECTOR_MODEL = "deterministic:sourced-life"
INSPECTION_VERSION = "media-inspection-sourced-life-v1"
_UNSOURCED_PRIMARY = "/legacy/action"


def sourced_life_closure_error(plan: Any) -> str | None:
    event_id = str(getattr(plan, "event_id", "") or "").strip()
    primary = str(getattr(plan, "primary_evidence_ref", "") or "").strip()
    values = getattr(plan, "evidence_values", {}) or {}
    if not event_id:
        return "unsourced_event"
    if not primary or primary == _UNSOURCED_PRIMARY:
        return "unsourced_evidence"
    value = values.get(primary)
    if not str(value or "").strip():
        return "unsourced_evidence"
    return None


class SourcedLifeMediaInspector:
    async def inspect(
        self,
        image_path: Path,
        *,
        plan: MediaPlan,
        prompt: str,
        reference_images: Iterable[Path] = (),
    ) -> MediaInspection:
        del prompt, reference_images
        closure = sourced_life_closure_error(plan)
        if closure is not None:
            return _inspection(passed=False, reason=closure, summary="")
        if not image_path.is_file():
            return _inspection(passed=False, reason="missing_image", summary="")
        primary = str(plan.primary_evidence_ref)
        summary = str(plan.evidence_values[primary]).strip()
        return _inspection(passed=True, reason="sourced_life", summary=summary)


class SourcedLifeMediaRenderer(MediaRenderer):
    async def render(self, plan: MediaPlan) -> RenderResult:
        error = sourced_life_closure_error(plan)
        if error is not None:
            return MediaRenderFailure(str(getattr(plan, "plan_id", "") or "plan"), error, 0)
        return await super().render(plan)


def _inspection(*, passed: bool, reason: str, summary: str) -> MediaInspection:
    return MediaInspection(
        passed=passed,
        reason=reason,
        observed_summary=summary,
        observed_facts=(),
        deviations=() if passed else (reason,),
        inspector_model=INSPECTOR_MODEL,
        rule_version=INSPECTION_VERSION,
    )


__all__ = [
    "INSPECTION_VERSION",
    "INSPECTOR_MODEL",
    "SourcedLifeMediaInspector",
    "SourcedLifeMediaRenderer",
    "sourced_life_closure_error",
]
