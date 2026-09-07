"""Deterministic view of one declared leftover she asked to return to.

This is not a hope that he will reply.  It is a source-bound unfinished
thought she authored on an accepted expression, with a wait she chose.
The host may open one consider after that wait; it does not decide whether
she speaks.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json

from pydantic import Field

from .context_capsule import InnerAdvisoryCandidate, InnerAdvisoryProjection
from .schema_core import FrozenModel


REVISIT_INTENTION_ADVISORY_VERSION = "revisit-intention-view.1"
REVISIT_INTENTION_GRACE = timedelta(hours=1)
_ANSWERABLE_RECEIPT_STATES = frozenset({"provider_accepted", "delivered"})


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class DueUnfinishedRevisit(FrozenModel):
    plan_id: str = Field(min_length=1)
    thought: str = Field(min_length=1, max_length=160)
    not_before: datetime
    expires_at: datetime
    receipt_event_id: str = Field(min_length=1)
    receipt_world_revision: int = Field(ge=1)


def due_revisit_consideration_id(plan_id: str) -> str:
    return "consideration:social-initiative:revisit:" + _digest(plan_id)


def due_thread_consideration_id(thread_id: str) -> str:
    return "consideration:social-initiative:due-thread:" + _digest(thread_id)


def due_commitment_consideration_id(commitment_id: str) -> str:
    return "consideration:social-initiative:due-commitment:" + _digest(commitment_id)


def _unfinished_revisits(projection, *, due_only: bool) -> list[DueUnfinishedRevisit]:
    logical_time = projection.logical_time
    if logical_time is None:
        return []
    receipt_refs = tuple(
        item
        for item in projection.committed_world_event_refs
        if item.event_type == "ExecutionReceiptRecorded"
    )
    if len(receipt_refs) != len(getattr(projection, "execution_receipts", ())):
        return []
    latest_by_action: dict[str, tuple[object, object]] = {}
    for ref, receipt in zip(receipt_refs, projection.execution_receipts, strict=True):
        existing = latest_by_action.get(receipt.action_id)
        if existing is None or ref.world_revision > existing[0].world_revision:
            latest_by_action[receipt.action_id] = (ref, receipt)
    delivered_by_action = {
        action_id: ref
        for action_id, (ref, receipt) in latest_by_action.items()
        if receipt.observed_state in _ANSWERABLE_RECEIPT_STATES
    }
    candidates: list[DueUnfinishedRevisit] = []
    for manifest in getattr(projection, "expression_plan_manifests", ()):
        leftover = getattr(manifest, "revisit", None)
        not_before = getattr(leftover, "not_before", None)
        thought = getattr(leftover, "thought", None)
        expires_at = getattr(leftover, "expires_at", None)
        if (
            leftover is None
            or not_before is None
            or expires_at is None
            or not isinstance(thought, str)
            or not thought.strip()
            or (due_only and logical_time < not_before)
            or logical_time >= expires_at + REVISIT_INTENTION_GRACE
        ):
            continue
        beat = next(
            (
                item
                for item in manifest.beats
                if item.beat_id == leftover.source_beat_id
            ),
            None,
        )
        if beat is None:
            continue
        delivered_ref = delivered_by_action.get(beat.action.action_id)
        if delivered_ref is None:
            continue
        candidates.append(
            DueUnfinishedRevisit(
                plan_id=manifest.plan_id,
                thought=thought.strip()[:160],
                not_before=not_before,
                expires_at=expires_at,
                receipt_event_id=delivered_ref.event_id,
                receipt_world_revision=delivered_ref.world_revision,
            )
        )
    return candidates


def unfinished_revisits(projection, *, due_only: bool = True) -> tuple[DueUnfinishedRevisit, ...]:
    """Read every eligible declared leftover without selecting its fate."""

    try:
        return tuple(
            sorted(
                _unfinished_revisits(projection, due_only=due_only),
                key=lambda item: (item.not_before, item.receipt_world_revision, item.plan_id),
            )
        )
    except (TypeError, ValueError, AttributeError):
        return ()


def due_unfinished_revisit(
    projection, *, source_plan_id: str | None = None
) -> DueUnfinishedRevisit | None:
    """One exact due leftover, or the latest for an unanchored feeling view."""

    candidates = unfinished_revisits(projection)
    if source_plan_id is not None:
        return next((item for item in candidates if item.plan_id == source_plan_id), None)
    return max(
        candidates, key=lambda item: (item.receipt_world_revision, item.plan_id), default=None
    )


def open_unfinished_revisit(projection) -> DueUnfinishedRevisit | None:
    """Delivered leftover she can still read, including before its wait opens."""

    try:
        candidates = _unfinished_revisits(projection, due_only=False)
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item.receipt_world_revision, item.plan_id))
    except (TypeError, ValueError, AttributeError):
        return None


def revisit_source_plan_id(projection, consideration_id: str) -> str | None:
    leftover = due_unfinished_revisit(projection)
    if leftover is not None and due_revisit_consideration_id(leftover.plan_id) == consideration_id:
        return leftover.plan_id
    return next(
        (
            manifest.plan_id
            for manifest in getattr(projection, "expression_plan_manifests", ())
            if due_revisit_consideration_id(manifest.plan_id) == consideration_id
        ),
        None,
    )


def _revisit_summary(view: DueUnfinishedRevisit, *, logical_time) -> str:
    due = "its wait has opened" if logical_time >= view.not_before else "its wait has not opened yet"
    return (
        f"She asked to return to: {view.thought}; {due}. "
        "This leftover stays even if he has spoken. "
        "Evidence only; she still decides."
    )[:256]


def revisit_intention_advisory(
    view: DueUnfinishedRevisit,
    *,
    logical_time,
) -> InnerAdvisoryProjection:
    source_ref = view.receipt_event_id
    return InnerAdvisoryProjection(
        advisory_id="advisory:revisit-intention:" + _digest(source_ref),
        kind="revisit_intention",
        source_refs=(source_ref,),
        candidate_refs=("revisit-intention:" + _digest(source_ref),),
        candidates=(
            InnerAdvisoryCandidate(
                candidate_ref="revisit-intention:" + _digest(source_ref),
                value=_revisit_summary(view, logical_time=logical_time),
                weight_bp=10_000,
                confidence_bp=10_000,
            ),
        ),
        confidence_bp=10_000,
        expiry=logical_time + timedelta(days=1),
        producer_version=REVISIT_INTENTION_ADVISORY_VERSION,
    )


def attach_open_revisit_advisory(
    context: dict[str, object],
    projection,
) -> dict[str, object]:
    """Fold the leftover she asked to return to into Capsule materials, if any."""

    try:
        view = open_unfinished_revisit(projection)
    except (TypeError, ValueError):
        return context
    if view is None:
        return context
    logical_time = getattr(projection, "logical_time", None)
    if logical_time is None:
        return context
    advisory = revisit_intention_advisory(view, logical_time=logical_time)
    slices = context.get("slices")
    if isinstance(slices, dict):
        slices = dict(slices)
        context = {**context, "slices": slices}
    else:
        slices = {}
        context = {**context, "slices": slices}
    lane = slices.get("advisories")
    if isinstance(lane, dict):
        lane = dict(lane)
        items = list(lane.get("items") or [])
    else:
        lane = {}
        items = []
    dumped = advisory.model_dump(mode="json")
    items.append(
        {
            "source_ref": advisory.advisory_id,
            "item_ref": advisory.advisory_id,
            "value": dumped,
        }
    )
    lane["items"] = items
    lane["availability"] = "available"
    slices["advisories"] = lane
    return context
