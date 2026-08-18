"""Conversational lifetime of a media-selection send decision.

She decides "I want him to see this" inside one sitting.  That decision may
start render and keep auto-delivery armed; it must not still fire after the
conversation has moved on.  The host does not discard the candidate.  It
refuses to auto-send, and the next conversation-adjacent occasion may ask
her again.

Twenty to thirty minutes is the band that fits the two clocks this lane
already has:

- typical render-plus-inspect in this deployment is several minutes (the
  silent bookstore send sat about seven minutes).  Twenty minutes already
  covers that plus one repair; thirty minutes is the top of the band so a
  slow same-sitting finish still ships.
- a "等我倒水" pause is minutes.  Forty minutes is a different chapter.
- the occasion gate that *asks* her is two hours.  Asking and sending are
  different acts: the ask window may stay open while this send window
  closes.

``approval_ttl`` (hours) remains the ActionPump artefact-binding expiry.
This module measures the *character* decision, from the latest recorded
selection proposal, not from inspection-complete approval.
"""

from __future__ import annotations

from datetime import datetime, timedelta

CONVERSATION_SELECTION_TTL = timedelta(minutes=30)
REASK_STATUSES = frozenset({"selected", "planned", "generated"})


def latest_selection_decided_at(
    projection: object, *, candidate_id: str
) -> datetime | None:
    """Latest ``MediaSelectionProposalRecorded`` time for this candidate."""

    if not isinstance(candidate_id, str) or not candidate_id:
        return None
    refs = {
        getattr(item, "event_id", None): item
        for item in getattr(projection, "committed_world_event_refs", ()) or ()
    }
    times: list[datetime] = []
    for rev in getattr(projection, "proposal_revisions", ()) or ():
        if getattr(rev, "candidate_id", None) != candidate_id:
            continue
        committed = refs.get(getattr(rev, "proposal_event_ref", None))
        at = getattr(committed, "logical_time", None)
        if isinstance(at, datetime):
            times.append(at)
    return max(times) if times else None


def candidate_has_delivery(projection: object, *, candidate_id: str) -> bool:
    opportunity_ids = {
        getattr(item, "opportunity_id", None)
        for item in getattr(projection, "media_opportunities", ()) or ()
        if getattr(item, "candidate_id", None) == candidate_id
    }
    plan_ids = {
        getattr(item, "plan_id", None)
        for item in getattr(projection, "media_plans", ()) or ()
        if getattr(item, "opportunity_id", None) in opportunity_ids
    }
    return any(
        getattr(item, "plan_id", None) in plan_ids
        for item in getattr(projection, "media_deliveries", ()) or ()
    )


def selection_decision_at(
    projection: object,
    *,
    candidate_id: str | None = None,
    plan: object | None = None,
) -> datetime | None:
    """Best available time her send decision was recorded.

    Prefer the latest selection proposal.  Fall back to the plan freeze so
    previews that predate proposal coordinates still have a same-sitting
    clock instead of a 24h approval clock.
    """

    resolved_candidate = candidate_id
    if resolved_candidate is None and plan is not None:
        opportunity_id = getattr(plan, "opportunity_id", None)
        for item in getattr(projection, "media_opportunities", ()) or ():
            if getattr(item, "opportunity_id", None) == opportunity_id:
                resolved = getattr(item, "candidate_id", None)
                if isinstance(resolved, str) and resolved:
                    resolved_candidate = resolved
                break
    if isinstance(resolved_candidate, str) and resolved_candidate:
        decided = latest_selection_decided_at(
            projection, candidate_id=resolved_candidate
        )
        if decided is not None:
            return decided
    frozen = getattr(plan, "frozen_at", None) if plan is not None else None
    return frozen if isinstance(frozen, datetime) else None


def conversation_window_expires_at(
    decided_at: datetime, *, ttl: timedelta = CONVERSATION_SELECTION_TTL
) -> datetime:
    return decided_at + ttl


def conversation_send_allowed(
    projection: object,
    *,
    logical_time: datetime,
    candidate_id: str | None = None,
    plan: object | None = None,
    ttl: timedelta = CONVERSATION_SELECTION_TTL,
) -> bool:
    decided = selection_decision_at(
        projection, candidate_id=candidate_id, plan=plan
    )
    if decided is None:
        return False
    return logical_time < conversation_window_expires_at(decided, ttl=ttl)


def is_reask_eligible(
    projection: object,
    *,
    candidate: object,
    logical_time: datetime,
    ttl: timedelta = CONVERSATION_SELECTION_TTL,
) -> bool:
    """True when a prior send decision lapsed and she may be asked again.

    The candidate is not discarded.  Status may stay ``generated``; this
    only answers whether the *send decision* is spent and the photo is
    still choosable.
    """

    candidate_id = getattr(candidate, "candidate_id", None)
    status = getattr(candidate, "status", None)
    if not isinstance(candidate_id, str) or status not in REASK_STATUSES:
        return False
    expires_at = getattr(candidate, "expires_at", None)
    if (
        not isinstance(expires_at, datetime)
        or expires_at <= logical_time
        or getattr(candidate, "opened_at", None) is None
        or not getattr(candidate, "source_events", ())
    ):
        return False
    if candidate_has_delivery(projection, candidate_id=candidate_id):
        return False
    plan = _plan_for_candidate(projection, candidate_id=candidate_id)
    decided = selection_decision_at(
        projection, candidate_id=candidate_id, plan=plan
    )
    if decided is None:
        return False
    return logical_time >= conversation_window_expires_at(decided, ttl=ttl)


def _plan_for_candidate(projection: object, *, candidate_id: str) -> object | None:
    opportunity_ids = {
        getattr(item, "opportunity_id", None)
        for item in getattr(projection, "media_opportunities", ()) or ()
        if getattr(item, "candidate_id", None) == candidate_id
    }
    for item in getattr(projection, "media_plans", ()) or ():
        if getattr(item, "opportunity_id", None) in opportunity_ids:
            return item
    return None


__all__ = [
    "CONVERSATION_SELECTION_TTL",
    "REASK_STATUSES",
    "candidate_has_delivery",
    "conversation_send_allowed",
    "conversation_window_expires_at",
    "is_reask_eligible",
    "latest_selection_decided_at",
    "selection_decision_at",
]
