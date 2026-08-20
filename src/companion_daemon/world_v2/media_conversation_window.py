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
    """Latest ``MediaSelectionProposalRecorded`` time for this candidate.

    The revision carries ``decided_at`` because the proposal event is
    DELIBERATION-class and is therefore absent from
    ``committed_world_event_refs``.  Revisions written before that field
    existed still resolve through the world-ref lookup when their proposal
    happens to be mirrored there.
    """

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
        at = getattr(rev, "decided_at", None)
        if not isinstance(at, datetime):
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


_CROSS_LANE_CLAUSE_MAX = 220


def _delivery_count(projection: object) -> int:
    deliveries = getattr(projection, "media_deliveries", ()) or ()
    actions = getattr(projection, "actions", ()) or ()
    action_deliveries = sum(
        1
        for item in actions
        if getattr(item, "kind", None) == "media_delivery"
        and getattr(item, "state", None) in {"provider_accepted", "delivered"}
    )
    return max(len(deliveries), action_deliveries)


def media_cross_lane_timing_clause(
    projection: object,
    *,
    logical_time: datetime | None = None,
    max_chars: int = _CROSS_LANE_CLAUSE_MAX,
) -> str:
    """Neutral media send-window facts for text/expiry lanes. She still decides."""

    at = logical_time or getattr(projection, "logical_time", None)
    if at is None:
        return ""
    clauses: list[str] = []
    delivered = _delivery_count(projection)
    if delivered:
        noun = "photo" if delivered == 1 else "photos"
        clauses.append(
            f"{delivered} {noun} already reached him on the ledger (MediaDeliveryShared)."
        )
    for candidate in getattr(projection, "photo_candidates", ()) or ():
        candidate_id = getattr(candidate, "candidate_id", None)
        if not isinstance(candidate_id, str) or not candidate_id:
            continue
        if candidate_has_delivery(projection, candidate_id=candidate_id):
            continue
        expires_at = getattr(candidate, "expires_at", None)
        if isinstance(expires_at, datetime) and expires_at <= at:
            clauses.append(
                "A photo candidate she opened has expired and is no longer choosable."
            )
            continue
        decided = selection_decision_at(
            projection, candidate_id=candidate_id, plan=_plan_for_candidate(
                projection, candidate_id=candidate_id
            ),
        )
        if decided is None:
            continue
        if conversation_send_allowed(
            projection, logical_time=at, candidate_id=candidate_id
        ):
            minutes = max(0, int((at - decided).total_seconds()) // 60)
            clauses.append(
                "A photo send decision is still inside the same-sitting auto-send window"
                + (f" (about {minutes} minutes since she chose it)." if minutes else ".")
            )
        elif is_reask_eligible(projection, candidate=candidate, logical_time=at):
            minutes = max(0, int((at - decided).total_seconds()) // 60)
            clauses.append(
                "A photo send decision is no longer auto-sending"
                + (
                    f" (about {minutes} minutes since she chose it); "
                    "the candidate may still be choosable."
                    if minutes
                    else "; the candidate may still be choosable."
                )
            )
    if not clauses:
        return ""
    body = " ".join(dict.fromkeys(clauses))
    suffix = " Timing evidence only; she still decides."
    room = max(0, max_chars - len(suffix))
    return (body[:room].rstrip() + suffix) if room else suffix[:max_chars]


__all__ = [
    "CONVERSATION_SELECTION_TTL",
    "REASK_STATUSES",
    "candidate_has_delivery",
    "conversation_send_allowed",
    "conversation_window_expires_at",
    "is_reask_eligible",
    "latest_selection_decided_at",
    "media_cross_lane_timing_clause",
    "selection_decision_at",
]
