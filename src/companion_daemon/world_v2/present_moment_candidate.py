"""Present-moment photographability from an already-active activity.

This is supply, not a share decision.  A candidate may open only from a
ledger-active plan whose reviewed annex already names visible facts.  No
day-sheet, hashed weather, or spoken promise invents a scene.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from .schema_core import FrozenModel


PresentMomentReason = Literal[
    "active",
    "no_active_activity",
    "sleep",
    "annex_insufficient",
    "already_open",
]


class PresentMomentFact(FrozenModel):
    """Whether this sitting can become a photo, bound to one active plan."""

    photographable: bool
    reason: PresentMomentReason
    source_ref: str | None = None
    plan_id: str | None = None
    activity_kind: str | None = None


def activity_kind_is_sleep(activity_kind: object) -> bool:
    if not isinstance(activity_kind, str) or not activity_kind:
        return False
    return (
        activity_kind.startswith("sleep.")
        or ".sleep." in activity_kind
        or activity_kind.endswith(".sleep")
    )


def annex_is_sufficient(annex: object | None) -> bool:
    if annex is None:
        return False
    description = getattr(annex, "activity_description", None)
    capture = getattr(annex, "self_capture", ()) or ()
    return isinstance(description, str) and bool(description.strip()) and bool(capture)


def _started_event_ref(plan: object) -> str | None:
    origin = getattr(plan, "authority_origin", None)
    ref = getattr(origin, "accepted_event_ref", None) if origin is not None else None
    return ref if isinstance(ref, str) and ref else None


def _candidate_binds_source(projection: object, *, source_ref: str, logical_time: datetime) -> bool:
    for item in getattr(projection, "photo_candidates", ()) or ():
        status = getattr(item, "status", None)
        if status in {"skipped", "unrenderable", "expired", "failed"}:
            continue
        expires_at = getattr(item, "expires_at", None)
        if (
            isinstance(expires_at, datetime)
            and expires_at <= logical_time
        ):
            continue
        refs = set(getattr(item, "source_event_refs", ()) or ())
        refs.update(
            getattr(source, "event_ref", None)
            for source in getattr(item, "source_events", ()) or ()
        )
        if source_ref in refs:
            return True
    return False


def inspect_present_moment(
    *,
    projection: object,
    catalog: object | None = None,
    logical_time: datetime | None = None,
    declared_sources: frozenset[str] = frozenset(),
) -> PresentMomentFact:
    """Read-only: can the current active plan become a present-moment candidate?"""

    clock = logical_time or getattr(projection, "logical_time", None)
    if not isinstance(clock, datetime):
        return PresentMomentFact(photographable=False, reason="no_active_activity")
    active = [
        item
        for item in getattr(projection, "plans", ()) or ()
        if getattr(item, "status", None) == "active"
        and isinstance(getattr(item, "activity_kind", None), str)
    ]
    active.sort(key=lambda item: getattr(item, "plan_id", "") or "")
    if not active:
        return PresentMomentFact(photographable=False, reason="no_active_activity")
    plan = active[0]
    kind = getattr(plan, "activity_kind")
    source_ref = _started_event_ref(plan)
    opening = None
    if catalog is not None:
        lookup = getattr(catalog, "opening_for_activity", None)
        if callable(lookup):
            opening = lookup(kind)
    if activity_kind_is_sleep(kind) or getattr(opening, "domain", None) == "sleep_wake":
        return PresentMomentFact(
            photographable=False,
            reason="sleep",
            source_ref=source_ref,
            plan_id=getattr(plan, "plan_id", None),
            activity_kind=kind,
        )
    annex = getattr(opening, "visual_evidence", None) if opening is not None else None
    if catalog is not None and opening is None:
        if isinstance(kind, str) and kind.startswith("open_life."):
            return PresentMomentFact(
                photographable=True,
                reason="active",
                source_ref=source_ref,
                plan_id=getattr(plan, "plan_id", None),
                activity_kind=kind,
            )
        return PresentMomentFact(
            photographable=False,
            reason="annex_insufficient",
            source_ref=source_ref,
            plan_id=getattr(plan, "plan_id", None),
            activity_kind=kind,
        )
    if catalog is not None and not annex_is_sufficient(annex):
        return PresentMomentFact(
            photographable=False,
            reason="annex_insufficient",
            source_ref=source_ref,
            plan_id=getattr(plan, "plan_id", None),
            activity_kind=kind,
        )
    if (
        isinstance(source_ref, str)
        and (
            source_ref in declared_sources
            or _candidate_binds_source(projection, source_ref=source_ref, logical_time=clock)
        )
    ):
        return PresentMomentFact(
            photographable=True,
            reason="already_open",
            source_ref=source_ref,
            plan_id=getattr(plan, "plan_id", None),
            activity_kind=kind,
        )
    return PresentMomentFact(
        photographable=True,
        reason="active",
        source_ref=source_ref,
        plan_id=getattr(plan, "plan_id", None),
        activity_kind=kind,
    )


def present_moment_material(fact: PresentMomentFact) -> dict[str, object]:
    """Model-facing packet.  False is a fact, not a prompt to invent a scene."""

    payload: dict[str, object] = {
        "photographable": fact.photographable,
        "reason": fact.reason,
    }
    if fact.source_ref:
        payload["source_ref"] = fact.source_ref
    if fact.plan_id:
        payload["plan_id"] = fact.plan_id
    if fact.activity_kind:
        payload["activity_kind"] = fact.activity_kind
    return payload


__all__ = [
    "PresentMomentFact",
    "activity_kind_is_sleep",
    "annex_is_sufficient",
    "inspect_present_moment",
    "present_moment_material",
]
