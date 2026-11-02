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
REVISIT_INTENTION_GRACE = timedelta(hours=2)
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


def due_thread_consideration_id(
    thread_id: str, *, schedule_event_ref: str | None = None
) -> str:
    """Bind new opportunities to an accepted schedule; preserve legacy identities."""

    material = (
        thread_id
        if schedule_event_ref is None
        else {"thread_id": thread_id, "schedule_event_ref": schedule_event_ref}
    )
    return "consideration:social-initiative:due-thread:" + _digest(material)


def thread_due_schedule_sources(projection, *, thread) -> tuple[str, ...]:
    """Accepted transitions belonging to this exact uninterrupted due window.

    A character-accepted changed window creates a new opportunity identity.
    Updating importance or other material without changing that window does
    not mint another consideration. Sources also let old entity-only process
    identities consume exactly the schedule they actually considered.
    """

    window = object()
    sources: list[str] = []
    for transition in sorted(
        (
            item
            for item in getattr(projection, "thread_transitions", ())
            if item.thread_id == thread.thread_id
            and item.entity_revision <= thread.entity_revision
        ),
        key=lambda item: item.entity_revision,
    ):
        accepted_window = transition.values_after.due_window
        if accepted_window != window:
            sources = []
            window = accepted_window
        sources.append(transition.accepted_event_ref)
    return tuple(sources) if window == thread.values.due_window else ()


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
    completed_refs = {
        getattr(proc, "trigger_ref", None)
        for proc in getattr(projection, "trigger_processes", ()) or ()
        if getattr(proc, "process_kind", None) == "proactive_action_deliberation"
        and getattr(proc, "state", None) in {"completed", "terminal"}
    }
    candidates: list[DueUnfinishedRevisit] = []
    for manifest in getattr(projection, "expression_plan_manifests", ()):
        if (
            "proactive-consideration:" + due_revisit_consideration_id(manifest.plan_id)
        ) in completed_refs:
            continue
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
        latest_delivered_revision = max(
            (ref.world_revision for ref in delivered_by_action.values()),
            default=0,
        )
        superseded_after_due = any(
            ref.world_revision > delivered_ref.world_revision
            and isinstance(getattr(ref, "logical_time", None), datetime)
            and ref.logical_time >= not_before
            for ref in delivered_by_action.values()
        )
        if superseded_after_due or (
            logical_time > expires_at
            and delivered_ref.world_revision < latest_delivered_revision
        ):
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
    extra_views: tuple[DueUnfinishedRevisit, ...] = (),
) -> InnerAdvisoryProjection:
    source_ref = view.receipt_event_id
    all_views = (view, *(item for item in extra_views if item.plan_id != view.plan_id))
    source_refs: list[str] = []
    candidate_refs: list[str] = []
    candidates: list[InnerAdvisoryCandidate] = []
    for item in all_views:
        ref = item.receipt_event_id
        if ref not in source_refs:
            source_refs.append(ref)
        cref = (
            "revisit-intention:" + _digest(ref)
            if item.plan_id == view.plan_id
            else "revisit-intention:" + _digest([ref, item.plan_id])
        )
        candidate_refs.append(cref)
        candidates.append(
            InnerAdvisoryCandidate(
                candidate_ref=cref,
                value=_revisit_summary(item, logical_time=logical_time),
                weight_bp=10_000,
                confidence_bp=10_000,
            )
        )
    return InnerAdvisoryProjection(
        advisory_id="advisory:revisit-intention:" + _digest(source_ref),
        kind="revisit_intention",
        source_refs=tuple(source_refs),
        candidate_refs=tuple(candidate_refs),
        candidates=tuple(candidates),
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
        all_open = unfinished_revisits(projection, due_only=False)
    except (TypeError, ValueError):
        return context
    if view is None:
        return context
    logical_time = getattr(projection, "logical_time", None)
    if logical_time is None:
        return context
    extra = tuple(item for item in all_open if item.plan_id != view.plan_id)
    advisory = revisit_intention_advisory(
        view, logical_time=logical_time, extra_views=extra
    )
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
