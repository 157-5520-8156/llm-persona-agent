"""Deterministic, model-safe view of one pending declared response expectation.

When she speaks, the expression contract may freeze a model-declared
``ResponseExpectationAuthority`` ("I hope they come back and tell me how it
went").  Until now that authority only drove behaviour (the response-gap
follow-up lane).  This module gives the feeling lanes the same committed
fact: being left waiting after asking for comfort and being left waiting
after an idle remark are different experiences, and the appraisal model can
only weigh that difference if it knows what she hoped for.

Everything here is a pure read over one pinned projection.  The exported
view carries only semantic values (hoped response, coarse pressure and
importance, how long she has been waiting) and never IDs, hashes or
authority references; the advisory helper binds the committed sources in
the ordinary Inner-Advisory envelope instead.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import hashlib
import json
from typing import Literal

from pydantic import Field

from companion_daemon.conversation_cadence import derive_conversation_cadence

from .context_capsule import InnerAdvisoryCandidate, InnerAdvisoryProjection
from .media_conversation_window import media_cross_lane_timing_clause
from .schema_core import FrozenModel


RESPONSE_EXPECTATION_ADVISORY_VERSION = "response-expectation-view.4"
# Matches present_prompt open-hope sentinel: wait=86400, expires=172800.
# A real max wait of 86400 still chases by 60s and is not this marker.
OPEN_HOPE_CHASE_SECONDS = 86_400
# Leftover from the 2026-08-18 optional-wait experiment. H21 does not use
# these delays: a hope compiled without wait should not exist, and a hope
# with wait wakes at `not_before`. Kept so living-hope views can still
# recognize sentinel records already in a ledger.
OPEN_HOPE_WAKE_SECONDS = {
    "hot": 60,
    "warm": 480,
}
OPEN_HOPE_LOCAL_TIMEZONE = "Asia/Shanghai"
OPEN_HOPE_OVERNIGHT_END_HOUR = 7

# Mirrors the silence-anchor discipline: a receipt in these states means she
# visibly said it from her own point of view.  Behavioural follow-up applies
# the stricter ``delivery_requirement`` gate; the feeling side only needs
# "the invitation left her hands".
_ANSWERABLE_RECEIPT_STATES = frozenset({"provider_accepted", "delivered"})

ExpectationTier = Literal["low", "medium", "high"]
_TERMINAL_ASSESSMENT_STATES = frozenset(
    {"fulfilled", "superseded", "still_pending"}
)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _tier(basis_points: int) -> ExpectationTier:
    # Presentation binning only: the model receives a coarse strength label
    # instead of raw basis points, so prose material stays human-shaped.
    if basis_points <= 3_333:
        return "low"
    if basis_points <= 6_666:
        return "medium"
    return "high"


class PendingResponseExpectationView(FrozenModel):
    """What she hoped for, in semantic values only: no IDs, hashes or refs."""

    hoped_response: str = Field(min_length=1, max_length=128)
    pressure: ExpectationTier
    importance: ExpectationTier
    # Seconds since the inviting expression was visibly delivered.  Delivery
    # is the declaration instant a projection can testify to; the freeze-time
    # wait/expiry offsets are not separately recoverable from the authority.
    declared_seconds_ago: int = Field(ge=0)


def answerable_receipt_history(projection, *, before_world_revision=None):
    """First visible leave and latest still-answerable receipt per Action.

    A later failed/unknown receipt removes present delivery evidence, not the
    character's authored hope. Historical readers only consume the receipt
    prefix they actually had; late delivery never moves the original leave.
    """

    refs = tuple(
        item for item in projection.committed_world_event_refs
        if item.event_type == "ExecutionReceiptRecorded"
    )
    if len(refs) != len(projection.execution_receipts):
        raise ValueError("execution receipt projection does not align with committed refs")
    first_visible = {}
    latest = {}
    for ref, receipt in zip(refs, projection.execution_receipts, strict=True):
        if before_world_revision is not None and ref.world_revision >= before_world_revision:
            continue
        prior = latest.get(receipt.action_id)
        if prior is None or ref.world_revision > prior[0].world_revision:
            latest[receipt.action_id] = (ref, receipt)
        if receipt.observed_state in _ANSWERABLE_RECEIPT_STATES:
            first = first_visible.get(receipt.action_id)
            if first is None or ref.world_revision < first.world_revision:
                first_visible[receipt.action_id] = ref
    return {
        action_id: (first_visible[action_id], ref)
        for action_id, (ref, receipt) in latest.items()
        if receipt.observed_state in _ANSWERABLE_RECEIPT_STATES
    }


def pending_response_expectation(
    projection,
    *,
    anchor_event_ref: str | None = None,
    before_world_revision: int | None = None,
) -> PendingResponseExpectationView | None:
    """Resolve the pending declared expectation from committed projection state.

    With ``anchor_event_ref`` (a committed ``ExecutionReceiptRecorded`` event
    of her own visible message, e.g. the silence anchor) the chain is exact:
    receipt → action → manifest beat → that manifest's frozen expectation.

    Without an anchor, the most recently delivered, still-unexpired
    expectation wins; ``before_world_revision`` optionally restricts the
    search to expectations delivered strictly before that revision, so an
    inbound message is never explained by a hope she declared after it.
    """

    logical_time = projection.logical_time
    if logical_time is None:
        return None
    receipt_refs = tuple(
        item
        for item in projection.committed_world_event_refs
        if item.event_type == "ExecutionReceiptRecorded"
    )
    # Each ``ExecutionReceiptRecorded`` reduction appends exactly one receipt,
    # so the committed refs of that type align positionally with the receipt
    # projection (same invariant as the silence-anchor derivation).
    if len(receipt_refs) != len(projection.execution_receipts):
        raise ValueError("execution receipt projection does not align with committed refs")
    pairs = tuple(zip(receipt_refs, projection.execution_receipts, strict=True))
    answerable = answerable_receipt_history(
        projection, before_world_revision=before_world_revision
    )
    terminal_plan_ids = {
        item.source_plan_id
        for item in getattr(projection, "response_expectation_assessments", ())
        if item.status in _TERMINAL_ASSESSMENT_STATES
        and (
            before_world_revision is None
            or getattr(item, "world_revision", 0) < before_world_revision
        )
    }

    if anchor_event_ref is not None:
        anchor = next((pair for pair in pairs if pair[0].event_id == anchor_event_ref), None)
        if anchor is None:
            return None
        anchor_ref, anchor_receipt = anchor
        if before_world_revision is not None and anchor_ref.world_revision >= before_world_revision:
            return None
        if anchor_receipt.observed_state not in _ANSWERABLE_RECEIPT_STATES:
            return None
        if anchor_receipt.action_id not in answerable:
            return None
        # The anchor is her last visible message; the invitation may live on
        # an earlier beat of the same accepted plan, so the manifest is bound
        # through any beat whose action produced this receipt.
        manifest = next(
            (
                item
                for item in projection.expression_plan_manifests
                if item.response_expectation is not None
                and any(
                    beat.action.action_id == anchor_receipt.action_id for beat in item.beats
                )
            ),
            None,
        )
        if manifest is None:
            return None
        if manifest.plan_id in terminal_plan_ids:
            return None
        expectation = manifest.response_expectation
        if logical_time >= expectation.expires_at:
            return None
        inviting_beat = next(
            (beat for beat in manifest.beats if beat.beat_id == expectation.source_beat_id),
            None,
        )
        if inviting_beat is None or inviting_beat.action.action_id not in answerable:
            return None
        return _view(
            expectation,
            declared_seconds_ago=int((logical_time - anchor_ref.logical_time).total_seconds()),
        )

    # First visible leave per action — late terminal delivery must not push
    # the hope "after" an inbound that already answered it.
    first_visible_by_action = {action_id: refs[0] for action_id, refs in answerable.items()}
    candidates = []
    for manifest in projection.expression_plan_manifests:
        expectation = manifest.response_expectation
        if (
            expectation is None
            or manifest.plan_id in terminal_plan_ids
            or (
                before_world_revision is None
                and logical_time >= expectation.expires_at
            )
        ):
            continue
        beat = next(
            (item for item in manifest.beats if item.beat_id == expectation.source_beat_id),
            None,
        )
        if beat is None:
            continue
        declared_ref = first_visible_by_action.get(beat.action.action_id)
        if declared_ref is None:
            continue
        if (
            before_world_revision is not None
            and declared_ref.world_revision >= before_world_revision
        ):
            continue
        candidates.append((declared_ref, manifest))
    if not candidates and before_world_revision is not None:
        still_pending_manifest = _latest_still_pending_manifest(
            projection,
            first_visible_by_action=first_visible_by_action,
            before_world_revision=before_world_revision,
        )
        if still_pending_manifest is not None:
            candidates.append(still_pending_manifest)
    if not candidates:
        return None
    declared_ref, manifest = max(
        candidates, key=lambda item: (item[0].world_revision, item[1].acceptance_event_ref)
    )
    return _view(
        manifest.response_expectation,
        declared_seconds_ago=max(
            0, int((logical_time - declared_ref.logical_time).total_seconds())
        ),
    )


EXPIRED_EXPECTATION_GRACE = timedelta(hours=1)


class ExpiredUnansweredExpectation(FrozenModel):
    plan_id: str = Field(min_length=1)
    hoped_response: str = Field(min_length=1, max_length=128)
    not_before: datetime
    expires_at: datetime
    receipt_event_id: str = Field(min_length=1)
    receipt_world_revision: int = Field(ge=1)
    receipt_logical_time: datetime
    # First visible delivery of the inviting beat.  Distinct from the latest
    # receipt, which may arrive late and cannot be used as "has he spoken".
    declared_world_revision: int = Field(ge=1)
    declared_logical_time: datetime
    is_open_hope: bool = False
    pressure: ExpectationTier | None = None
    importance: ExpectationTier | None = None
    inviting_beat_text: str | None = Field(default=None, max_length=128)


def _inviting_beat_text(projection, beat: object) -> str | None:
    """Resolve the delivered text of the beat that carried the response expectation."""

    direct = getattr(beat, "text", None)
    if isinstance(direct, str) and direct.strip():
        return direct.strip()[:128]
    payload_ref = getattr(beat, "payload_ref", None)
    if isinstance(payload_ref, str) and payload_ref:
        for item in getattr(projection, "stored_message_payloads", ()):
            if getattr(item, "payload_ref", None) == payload_ref:
                text = getattr(item, "text", None)
                if isinstance(text, str) and text.strip():
                    return text.strip()[:128]
    action_id = getattr(getattr(beat, "action", None), "action_id", None)
    if isinstance(action_id, str) and action_id:
        payload_by_ref = {
            getattr(item, "payload_ref", None): getattr(item, "text", None)
            for item in getattr(projection, "stored_message_payloads", ())
        }
        for expr_beat in getattr(projection, "expression_beats", ()):
            if getattr(expr_beat, "action_id", None) == action_id:
                text = payload_by_ref.get(getattr(expr_beat, "payload_ref", None))
                if isinstance(text, str) and text.strip():
                    return text.strip()[:128]
    return None


def expired_expectation_consideration_id(plan_id: str) -> str:
    return "consideration:social-initiative:expectation-expiry:" + _digest(plan_id)


def unanswered_response_expectations(
    projection,
    *,
    require_unanswered: bool = True,
    due_only: bool = True,
) -> tuple[ExpiredUnansweredExpectation, ...]:
    """All source-bound hopes eligible for their declared consideration window.

    The scheduler may read future openings without minting an opportunity.
    Selection and process recovery must retain each plan's identity instead
    of letting the newest hope hide another eligible one.
    """

    try:
        logical_time = projection.logical_time
        if logical_time is None:
            return ()
        answerable = answerable_receipt_history(projection)
        latest_message_revision = (
            projection.message_observations[-1].world_revision
            if projection.message_observations
            else 0
        )
        terminal_plan_ids = {
            item.source_plan_id
            for item in getattr(projection, "response_expectation_assessments", ())
            if item.status in _TERMINAL_ASSESSMENT_STATES
        }
        first_visible_by_action = {action_id: refs[0] for action_id, refs in answerable.items()}
        delivered_by_action = {action_id: refs[1] for action_id, refs in answerable.items()}
        candidates: list[ExpiredUnansweredExpectation] = []
        for manifest in projection.expression_plan_manifests:
            expectation = manifest.response_expectation
            authority_not_before = getattr(expectation, "not_before", None)
            if (
                expectation is None
                or authority_not_before is None
                or manifest.plan_id in terminal_plan_ids
                or logical_time >= expectation.expires_at + EXPIRED_EXPECTATION_GRACE
            ):
                continue
            beat = next(
                (item for item in manifest.beats if item.beat_id == expectation.source_beat_id),
                None,
            )
            if beat is None:
                continue
            delivered_ref = delivered_by_action.get(beat.action.action_id)
            if delivered_ref is None:
                continue
            declared_ref = first_visible_by_action.get(beat.action.action_id, delivered_ref)
            # Compare against first visible leave (provider_accepted/delivered),
            # never the latest receipt: a late terminal `delivered` ack can land
            # after he already replied and must not rewrite "unanswered".
            if (
                require_unanswered
                and latest_message_revision > declared_ref.world_revision
            ):
                continue
            declared_at = _declared_world_clock(
                projection, action_id=beat.action.action_id, receipt_ref=declared_ref
            )
            # H21: wake at the declared wait (`not_before`). Cadence-derived
            # short wakes for waiting_for-without-wait conflicted with
            # "没填 wait 不编译盼头" and are not used here.
            if due_only and logical_time < authority_not_before:
                continue
            not_before = authority_not_before
            pressure_bp = getattr(expectation, "pressure_bp", None)
            importance_bp = getattr(expectation, "importance_bp", None)
            pressure = (
                _tier(pressure_bp)
                if isinstance(pressure_bp, int) and not isinstance(pressure_bp, bool)
                else None
            )
            importance = (
                _tier(importance_bp)
                if isinstance(importance_bp, int) and not isinstance(importance_bp, bool)
                else None
            )
            candidates.append(
                ExpiredUnansweredExpectation(
                    plan_id=manifest.plan_id,
                    hoped_response=expectation.hoped_response,
                    not_before=not_before,
                    expires_at=expectation.expires_at,
                    receipt_event_id=delivered_ref.event_id,
                    receipt_world_revision=delivered_ref.world_revision,
                    receipt_logical_time=delivered_ref.logical_time,
                    declared_world_revision=declared_ref.world_revision,
                    declared_logical_time=declared_at,
                    is_open_hope=_is_open_hope_expectation(expectation),
                    pressure=pressure,
                    importance=importance,
                    inviting_beat_text=_inviting_beat_text(projection, beat),
                )
            )
        if not candidates:
            return ()
        return tuple(
            sorted(
                candidates,
                key=lambda item: (item.not_before, item.receipt_world_revision, item.plan_id),
            )
        )
    except (TypeError, ValueError, AttributeError):
        return ()


def expired_unanswered_expectation(
    projection,
    *,
    require_unanswered: bool = True,
    source_plan_id: str | None = None,
) -> ExpiredUnansweredExpectation | None:
    """Read one exact due hope, or the latest for an unanchored feeling view."""

    candidates = unanswered_response_expectations(
        projection, require_unanswered=require_unanswered
    )
    if source_plan_id is not None:
        return next((item for item in candidates if item.plan_id == source_plan_id), None)
    return max(
        candidates, key=lambda item: (item.receipt_world_revision, item.plan_id), default=None
    )


def next_response_expectation_wake_at(projection) -> datetime | None:
    """Soonest future declared opening, using the same receipt evidence as minting."""

    logical_time = getattr(projection, "logical_time", None)
    if logical_time is None:
        return None
    return min(
        (
            item.not_before
            for item in unanswered_response_expectations(projection, due_only=False)
            if item.not_before > logical_time
        ),
        default=None,
    )


def _latest_still_pending_manifest(
    projection,
    *,
    first_visible_by_action: dict[str, object],
    before_world_revision: int,
):
    """Find the latest manifest whose most recent assessment before ``before_world_revision`` is ``still_pending``."""

    assessments = [
        item
        for item in getattr(projection, "response_expectation_assessments", ()) or ()
        if getattr(item, "world_revision", 0) < before_world_revision
    ]
    if not assessments:
        return None
    last_settled_revision = max(
        (
            getattr(item, "world_revision", 0)
            for item in assessments
            if getattr(item, "status", None) in {"fulfilled", "superseded"}
        ),
        default=0,
    )
    by_plan: dict[str, list[object]] = {}
    for item in assessments:
        plan_id = getattr(item, "source_plan_id", None)
        if isinstance(plan_id, str) and plan_id:
            by_plan.setdefault(plan_id, []).append(item)
    still_pending_plans = {
        plan_id
        for plan_id, items in by_plan.items()
        if getattr(
            max(items, key=lambda entry: getattr(entry, "world_revision", 0)),
            "status",
            None,
        )
        == "still_pending"
        and getattr(
            max(items, key=lambda entry: getattr(entry, "world_revision", 0)),
            "world_revision",
            0,
        )
        > last_settled_revision
    }
    if not still_pending_plans:
        return None
    candidates = []
    for manifest in getattr(projection, "expression_plan_manifests", ()) or ():
        expectation = getattr(manifest, "response_expectation", None)
        if (
            expectation is None
            or manifest.plan_id not in still_pending_plans
        ):
            continue
        beat = next(
            (item for item in manifest.beats if item.beat_id == expectation.source_beat_id),
            None,
        )
        if beat is None:
            continue
        declared_ref = first_visible_by_action.get(beat.action.action_id)
        if (
            declared_ref is not None
            and declared_ref.world_revision < before_world_revision
            and declared_ref.world_revision > last_settled_revision
        ):
            candidates.append((declared_ref, manifest))
    if not candidates:
        return None
    return max(
        candidates, key=lambda item: (item[0].world_revision, item[1].acceptance_event_ref)
    )


def pending_response_expectation_manifest(
    projection,
    *,
    before_world_revision: int,
    at_logical_time: datetime | None = None,
):
    """Return the exact accepted manifest behind the current inbound advisory."""

    logical_time = at_logical_time or projection.logical_time
    if logical_time is None:
        return None
    answerable = answerable_receipt_history(
        projection, before_world_revision=before_world_revision
    )
    delivered_by_action = {action_id: refs[0] for action_id, refs in answerable.items()}
    terminal_plan_ids = {
        item.source_plan_id
        for item in getattr(projection, "response_expectation_assessments", ())
        if item.status in _TERMINAL_ASSESSMENT_STATES
        and item.world_revision < before_world_revision
    }
    candidates = []
    for manifest in projection.expression_plan_manifests:
        expectation = manifest.response_expectation
        if (
            expectation is None
            or manifest.plan_id in terminal_plan_ids
        ):
            continue
        beat = next(
            (item for item in manifest.beats if item.beat_id == expectation.source_beat_id),
            None,
        )
        if beat is None:
            continue
        delivered_ref = delivered_by_action.get(beat.action.action_id)
        if delivered_ref is not None:
            candidates.append((delivered_ref.world_revision, manifest))
    if candidates:
        return max(candidates, key=lambda item: item[0])[1]
    still_pending_pair = _latest_still_pending_manifest(
        projection,
        first_visible_by_action=delivered_by_action,
        before_world_revision=before_world_revision,
    )
    return still_pending_pair[1] if still_pending_pair is not None else None


def _view(expectation, *, declared_seconds_ago: int) -> PendingResponseExpectationView:
    return PendingResponseExpectationView(
        hoped_response=expectation.hoped_response,
        pressure=_tier(expectation.pressure_bp),
        importance=_tier(expectation.importance_bp),
        declared_seconds_ago=max(0, declared_seconds_ago),
    )


def _expectation_summary(
    view: PendingResponseExpectationView,
    *,
    counterpart_replied: bool,
) -> str:
    """Compress the pending expectation into one bounded read-only hint.

    Every value is copied from the frozen authority; nothing is inferred.
    The bound is the advisory candidate's 256-character contract.
    """

    minutes = view.declared_seconds_ago // 60
    waited = f"declared about {minutes} minutes ago" if minutes else "declared moments ago"
    reply = "he has since spoken" if counterpart_replied else "he has not spoken"
    return (
        f"When she last spoke she hoped for: {view.hoped_response}"
        f"; {reply}; pressure {view.pressure}; importance {view.importance}; {waited}"
        ". This is evidence only; she still decides."
    )[:256]


def counterpart_last_spoke_facts(
    projection,
    *,
    since_world_revision: int | None = None,
) -> tuple[int | None, bool]:
    """Seconds since his last inbound line, and whether any line follows ``since``.

    Both values are ledger coordinates.  They do not interpret whether a hope
    was semantically fulfilled.
    """

    logical_time = getattr(projection, "logical_time", None)
    observations = tuple(getattr(projection, "message_observations", ()) or ())
    if logical_time is None or not observations:
        return None, False
    last = max(
        observations,
        key=lambda item: (item.world_revision, getattr(item, "observation_id", "")),
    )
    payload_hash = getattr(last, "event_payload_hash", None)
    refs = tuple(
        item
        for item in getattr(projection, "committed_world_event_refs", ())
        if item.event_type == "ObservationRecorded"
    )
    last_ref = next(
        (
            item
            for item in refs
            if item.world_revision == last.world_revision
            and (payload_hash is None or item.payload_hash == payload_hash)
        ),
        None,
    )
    last_at = getattr(last_ref, "logical_time", None) if last_ref is not None else None
    seconds: int | None = None
    if last_at is not None:
        elapsed = int((logical_time - last_at).total_seconds())
        seconds = elapsed if elapsed >= 0 else None
    spoken_since = (
        last.world_revision > since_world_revision
        if since_world_revision is not None
        else False
    )
    return seconds, spoken_since


def _is_open_hope_expectation(expectation) -> bool:
    """True when waiting_for landed without a timer she chose."""

    not_before = getattr(expectation, "not_before", None)
    expires_at = getattr(expectation, "expires_at", None)
    if not_before is None or expires_at is None:
        return False
    return (expires_at - not_before).total_seconds() >= OPEN_HOPE_CHASE_SECONDS




def _action_world_time(projection, action_id: str):
    return next(
        (
            getattr(item, "logical_time", None)
            for item in getattr(projection, "actions", ()) or ()
            if getattr(item, "action_id", None) == action_id
            and getattr(item, "logical_time", None) is not None
        ),
        None,
    )


def _declared_world_clock(projection, *, action_id: str, receipt_ref) -> datetime:
    world = _action_world_time(projection, action_id)
    if world is not None:
        return world
    return receipt_ref.logical_time


def is_overnight_local(at: datetime) -> bool:
    """True before the local morning floor. Contact lanes must not wake him."""

    local = at.astimezone(ZoneInfo(OPEN_HOPE_LOCAL_TIMEZONE))
    return local.hour < OPEN_HOPE_OVERNIGHT_END_HOUR


_is_overnight_local = is_overnight_local


_CONTACT_KINDS = frozenset({"proactive_message", "followup"})
_CONTACT_SKIP_STATES = frozenset({"failed", "cancelled", "expired"})








_CADENCE_WINDOW_SECONDS = 720.0
_CADENCE_TURN_LIMIT = 12


def _cadence_window(
    messages: list[dict[str, str]], *, observed_at: datetime
) -> list[dict[str, str]]:
    """Keep the exchange around this inbound, not the whole ledger.

    Full-history rows mix days of traffic and can look out-of-order to
    ``derive_conversation_cadence``, which then fail-closes as cold.
    """

    kept: list[dict[str, str]] = []
    for item in messages:
        try:
            at = datetime.fromisoformat(item["observed_at"])
        except (KeyError, TypeError, ValueError):
            continue
        gap = (observed_at - at).total_seconds()
        if 0 <= gap <= _CADENCE_WINDOW_SECONDS:
            kept.append(item)
    return kept[-_CADENCE_TURN_LIMIT:]




class LivingUnansweredHope(FrozenModel):
    hoped_response: str = Field(min_length=1, max_length=128)
    declared_world_revision: int = Field(ge=1)
    declared_seconds_ago: int = Field(ge=0)
    is_open_hope: bool


def living_unanswered_hope(projection) -> LivingUnansweredHope | None:
    """Delivered, unassessed, still-unexpired hope; he has not spoken since.

    Open hopes (legacy waiting_for-without-wait sentinel records) hitch here
    as a fact. H21 timed wake uses only a declared `not_before`.
    """

    try:
        logical_time = projection.logical_time
        if logical_time is None:
            return None
        answerable = answerable_receipt_history(projection)
        terminal_plan_ids = {
            item.source_plan_id
            for item in getattr(projection, "response_expectation_assessments", ())
            if item.status in _TERMINAL_ASSESSMENT_STATES
        }
        delivered_by_action = {action_id: refs[1] for action_id, refs in answerable.items()}
        first_visible_by_action = {action_id: refs[0] for action_id, refs in answerable.items()}
        candidates: list[LivingUnansweredHope] = []
        for manifest in projection.expression_plan_manifests:
            expectation = manifest.response_expectation
            if (
                expectation is None
                or manifest.plan_id in terminal_plan_ids
                or logical_time >= expectation.expires_at
            ):
                continue
            beat = next(
                (item for item in manifest.beats if item.beat_id == expectation.source_beat_id),
                None,
            )
            if beat is None:
                continue
            delivered_ref = delivered_by_action.get(beat.action.action_id)
            if delivered_ref is None:
                continue
            declared_ref = first_visible_by_action.get(
                beat.action.action_id, delivered_ref
            )
            _seconds, spoken_since = counterpart_last_spoke_facts(
                projection, since_world_revision=declared_ref.world_revision
            )
            if spoken_since:
                continue
            hoped = str(expectation.hoped_response or "").strip()[:128]
            if not hoped:
                continue
            declared_at = _declared_world_clock(
                projection, action_id=beat.action.action_id, receipt_ref=declared_ref
            )
            candidates.append(
                LivingUnansweredHope(
                    hoped_response=hoped,
                    declared_world_revision=declared_ref.world_revision,
                    declared_seconds_ago=max(
                        0,
                        int((logical_time - declared_at).total_seconds()),
                    ),
                    is_open_hope=_is_open_hope_expectation(expectation),
                )
            )
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda item: (item.declared_world_revision, item.hoped_response),
        )
    except (TypeError, ValueError, AttributeError):
        return None


def _append_media_cross_lane_clause(text: str, projection: object | None) -> str:
    if projection is None:
        return text
    clause = media_cross_lane_timing_clause(projection)
    if not clause:
        return text
    combined = f"{text} {clause}".strip()
    return combined[:256]


def living_hope_hitch_clause(
    *,
    hoped_response: str,
    seconds_since_he_last_spoke: int | None,
    projection: object | None = None,
) -> str:
    """Fact for existing wakeup lanes: she is waiting, he has not replied."""

    hope = hoped_response.strip()[:72]
    if seconds_since_he_last_spoke is None:
        timing = "No counterpart line is on the ledger yet."
    else:
        timing = (
            f"He last spoke {seconds_since_he_last_spoke}s ago; "
            "he has not spoken since she declared that hope."
        )
    base = f"She is waiting for (her words, not a world event): {hope}. {timing}"
    return _append_media_cross_lane_clause(base, projection)


def expired_hope_advisory_value(
    *,
    hoped_response: str,
    seconds_since_he_last_spoke: int | None,
    spoken_since_declared: bool,
    projection: object | None = None,
    pressure: str | None = None,
    importance: str | None = None,
    inviting_beat_text: str | None = None,
    chase_count: int = 0,
) -> str:
    """Neutral timing facts for an expired hope.  She still decides.

    The hoped phrase is her leftover, not a world event. Putting it after
    ``Hope expired:`` read as if the thing she was waiting for had happened.
    """

    meta_parts: list[str] = []
    if isinstance(pressure, str) and pressure and isinstance(importance, str) and importance:
        meta_parts.append(f"p={pressure}/i={importance}")
    elif isinstance(pressure, str) and pressure:
        meta_parts.append(f"p={pressure}")
    elif isinstance(importance, str) and importance:
        meta_parts.append(f"i={importance}")
    if isinstance(inviting_beat_text, str) and inviting_beat_text.strip():
        meta_parts.append(f"sent='{inviting_beat_text.strip()[:20]}'")
    if isinstance(chase_count, int) and not isinstance(chase_count, bool) and chase_count > 0:
        meta_parts.append(f"woken={chase_count}x")
    hope_limit = 36 if meta_parts else 72
    hope = hoped_response.strip()[:hope_limit]
    meta_clause = f" [{', '.join(meta_parts)}]" if meta_parts else ""
    if seconds_since_he_last_spoke is None:
        timing = "No counterpart line is on the ledger yet."
    else:
        spoken = (
            "he has spoken since she declared a hope"
            if spoken_since_declared
            else "he has not spoken since she declared a hope"
        )
        timing = f"He last spoke {seconds_since_he_last_spoke}s ago; {spoken}."
    base = (
        f"{timing} What she hoped for (her words, not a world event): {hope}{meta_clause}. "
        "Timing evidence only; she still decides."
    )
    return _append_media_cross_lane_clause(base, projection)


def expired_expectation_advisory(
    expired: ExpiredUnansweredExpectation,
    *,
    logical_time: datetime,
    seconds_since_he_last_spoke: int | None,
    spoken_since_declared: bool,
    projection: object | None = None,
) -> InnerAdvisoryProjection:
    """Wrap expired-hope timing facts in the ordinary advisory envelope."""

    source_ref = expired.receipt_event_id
    value = expired_hope_advisory_value(
        hoped_response=expired.hoped_response,
        seconds_since_he_last_spoke=seconds_since_he_last_spoke,
        spoken_since_declared=spoken_since_declared,
        projection=projection,
        pressure=getattr(expired, "pressure", None),
        importance=getattr(expired, "importance", None),
        inviting_beat_text=getattr(expired, "inviting_beat_text", None),
    )
    return InnerAdvisoryProjection(
        advisory_id="advisory:expired-expectation:" + _digest(source_ref),
        kind="expired_expectation",
        source_refs=(source_ref,),
        candidate_refs=("expired-expectation:" + _digest(source_ref),),
        candidates=(
            InnerAdvisoryCandidate(
                candidate_ref="expired-expectation:" + _digest(source_ref),
                value=value,
                weight_bp=10_000,
                confidence_bp=10_000,
            ),
        ),
        confidence_bp=10_000,
        expiry=logical_time + timedelta(days=1),
        producer_version=RESPONSE_EXPECTATION_ADVISORY_VERSION,
    )


def response_expectation_advisory(
    view: PendingResponseExpectationView,
    *,
    source_ref: str,
    logical_time: datetime,
    counterpart_replied: bool = False,
) -> InnerAdvisoryProjection:
    """Wrap the view in the ordinary non-authoritative advisory envelope."""

    return InnerAdvisoryProjection(
        advisory_id="advisory:response-expectation:" + _digest(source_ref),
        kind="response_expectation",
        source_refs=(source_ref,),
        candidate_refs=("response-expectation:" + _digest(source_ref),),
        candidates=(
            InnerAdvisoryCandidate(
                candidate_ref="response-expectation:" + _digest(source_ref),
                value=_expectation_summary(
                    view, counterpart_replied=counterpart_replied
                ),
                weight_bp=10_000,
                confidence_bp=10_000,
            ),
        ),
        confidence_bp=10_000,
        # A short-lived deliberation aid anchored to the pinned durable head,
        # not wall-clock process time.
        expiry=logical_time + timedelta(days=1),
        producer_version=RESPONSE_EXPECTATION_ADVISORY_VERSION,
    )


def unfulfilled_expectation_backlog_advisory(
    projection,
    *,
    before_world_revision: int | None = None,
) -> InnerAdvisoryProjection | None:
    """Compile her accumulated unfulfilled expectations (still_pending + overdue unanswered) as source-bound facts."""

    logical_time = getattr(projection, "logical_time", None)
    if not isinstance(logical_time, datetime):
        return None
    try:
        answerable = answerable_receipt_history(
            projection, before_world_revision=before_world_revision
        )
    except (TypeError, ValueError):
        return None
    first_visible_by_action = {action_id: refs[0] for action_id, refs in answerable.items()}
    assessments = [
        item
        for item in getattr(projection, "response_expectation_assessments", ()) or ()
        if before_world_revision is None
        or getattr(item, "world_revision", 0) < before_world_revision
    ]
    last_fulfilled_revision = max(
        (
            getattr(item, "world_revision", 0)
            for item in assessments
            if getattr(item, "status", None) == "fulfilled"
        ),
        default=0,
    )
    by_plan: dict[str, list[object]] = {}
    for item in assessments:
        plan_id = getattr(item, "source_plan_id", None)
        if isinstance(plan_id, str) and plan_id:
            by_plan.setdefault(plan_id, []).append(item)

    observations = tuple(getattr(projection, "message_observations", ()) or ())
    delivered_manifests = []
    for manifest in getattr(projection, "expression_plan_manifests", ()) or ():
        expectation = getattr(manifest, "response_expectation", None)
        if expectation is None:
            continue
        beat = next(
            (item for item in getattr(manifest, "beats", ()) if item.beat_id == expectation.source_beat_id),
            None,
        )
        if beat is None:
            continue
        action_id = getattr(getattr(beat, "action", None), "action_id", None)
        declared_ref = first_visible_by_action.get(action_id) if isinstance(action_id, str) else None
        if declared_ref is None:
            continue
        if (
            before_world_revision is not None
            and declared_ref.world_revision >= before_world_revision
        ):
            continue
        if declared_ref.world_revision <= last_fulfilled_revision:
            continue
        delivered_manifests.append((declared_ref, manifest, beat, expectation))

    delivered_manifests.sort(
        key=lambda item: (
            item[0].world_revision,
            getattr(item[1], "acceptance_event_ref", "") or "",
        )
    )
    backlog: list[dict[str, object]] = []
    for declared_ref, manifest, beat, expectation in delivered_manifests:
        plan_id = getattr(manifest, "plan_id", "")
        plan_assessments = by_plan.get(plan_id, [])
        latest = max(
            plan_assessments,
            key=lambda entry: getattr(entry, "world_revision", 0),
            default=None,
        )
        minutes_ago = max(
            0, int((logical_time - declared_ref.logical_time).total_seconds()) // 60
        )
        beat_text = _inviting_beat_text(projection, beat)
        acceptance_ref = (
            getattr(manifest, "acceptance_event_ref", None) or declared_ref.event_id
        )
        if latest is not None:
            status = getattr(latest, "status", None)
            if status in {"fulfilled", "superseded"}:
                continue
            if status == "still_pending":
                if getattr(latest, "world_revision", 0) <= last_fulfilled_revision:
                    continue
                ordered_pending = sorted(
                    (
                        entry
                        for entry in plan_assessments
                        if getattr(entry, "status", None) == "still_pending"
                        and getattr(entry, "world_revision", 0) > last_fulfilled_revision
                    ),
                    key=lambda entry: getattr(entry, "world_revision", 0),
                )
                prior_reason = (
                    str(getattr(ordered_pending[-2], "reason", None) or "").strip()
                    if len(ordered_pending) >= 2
                    else ""
                )
                source_ref = getattr(latest, "event_ref", None) or acceptance_ref
                backlog.append(
                    {
                        "kind": "still_pending",
                        "source_ref": str(source_ref),
                        "hoped_response": expectation.hoped_response,
                        "importance": _tier(expectation.importance_bp),
                        "pressure": _tier(expectation.pressure_bp),
                        "minutes_ago": minutes_ago,
                        "reason": str(getattr(latest, "reason", None) or "").strip(),
                        "prior_reason": prior_reason,
                        "step_count": max(1, len(ordered_pending)),
                        "beat_text": beat_text,
                        "is_long_ignored": False,
                    }
                )
                continue
        replied_between = any(
            getattr(obs, "world_revision", 0) > declared_ref.world_revision
            and (
                before_world_revision is None
                or getattr(obs, "world_revision", 0) < before_world_revision
            )
            for obs in observations
        )
        not_before = getattr(expectation, "not_before", None)
        expires_at = getattr(expectation, "expires_at", None)
        if (
            not replied_between
            and isinstance(not_before, datetime)
            and isinstance(expires_at, datetime)
            and logical_time >= not_before
        ):
            backlog.append(
                {
                    "kind": "unanswered",
                    "source_ref": str(acceptance_ref),
                    "hoped_response": expectation.hoped_response,
                    "importance": _tier(expectation.importance_bp),
                    "pressure": _tier(expectation.pressure_bp),
                    "minutes_ago": minutes_ago,
                    "reason": "",
                    "beat_text": beat_text,
                    "is_long_ignored": logical_time >= expires_at + EXPIRED_EXPECTATION_GRACE,
                }
            )

    still_pending_items = [item for item in backlog if item["kind"] == "still_pending"]
    unanswered_items = [item for item in backlog if item["kind"] == "unanswered"]
    has_long_ignored = any(bool(item.get("is_long_ignored")) for item in unanswered_items)
    if not still_pending_items and len(backlog) < 2 and not has_long_ignored:
        return None

    selected = backlog[-5:]
    source_refs: list[str] = []
    for item in selected:
        ref = str(item["source_ref"])
        if ref not in source_refs:
            source_refs.append(ref)
    if not source_refs:
        return None

    oldest_minutes = max(int(item["minutes_ago"]) for item in backlog)
    summary_ref = "unfulfilled-expectation-backlog:summary:" + _digest(source_refs)
    candidates: list[InnerAdvisoryCandidate] = [
        InnerAdvisoryCandidate(
            candidate_ref=summary_ref,
            value=(
                f"Unfulfilled expectation backlog ({len(backlog)} unresolved since last fulfillment: "
                f"{len(still_pending_items)} still_pending after his replies, "
                f"{len(unanswered_items)} left unanswered; oldest {oldest_minutes}m ago). "
                "Facts only; she decides how or whether to bring this up."
            )[:256],
            weight_bp=10_000,
            confidence_bp=10_000,
        )
    ]
    candidate_refs: list[str] = [summary_ref]
    for idx, item in enumerate(selected):
        cref = f"unfulfilled-expectation-backlog:{idx}:" + _digest(
            [item["source_ref"], item["kind"], item["minutes_ago"]]
        )
        said_clause = f" (when saying {item['beat_text']!r})" if item["beat_text"] else ""
        if item["kind"] == "still_pending":
            step_count = int(item.get("step_count") or 1)
            step_label = f"still_pending x{step_count}" if step_count > 1 else "still_pending"
            prior_clause = (
                f" (prev: {item['prior_reason'][:48]})"
                if step_count > 1 and item.get("prior_reason")
                else ""
            )
            reason_clause = (
                f"; her assessment: {item['reason']}{prior_clause}"
                if item["reason"]
                else prior_clause
            )
            val = (
                f"[{step_label} {item['minutes_ago']}m ago, importance {item['importance']}]"
                f"{said_clause} hoped for: {item['hoped_response']}{reason_clause}."
            )[:256]
        else:
            label = "long_unanswered" if item["is_long_ignored"] else "unanswered"
            val = (
                f"[{label} {item['minutes_ago']}m ago, importance {item['importance']}]"
                f"{said_clause} hoped for: {item['hoped_response']}; he did not answer it."
            )[:256]
        candidate_refs.append(cref)
        candidates.append(
            InnerAdvisoryCandidate(
                candidate_ref=cref,
                value=val,
                weight_bp=9_500,
                confidence_bp=10_000,
            )
        )

    return InnerAdvisoryProjection(
        advisory_id="advisory:unfulfilled-expectation-backlog:" + _digest(source_refs),
        kind="unfulfilled_expectation_backlog",
        source_refs=tuple(source_refs),
        candidate_refs=tuple(candidate_refs),
        candidates=tuple(candidates),
        confidence_bp=10_000,
        expiry=logical_time + timedelta(days=1),
        producer_version=RESPONSE_EXPECTATION_ADVISORY_VERSION,
    )


def attach_pending_expectation_advisory(
    context: dict[str, object],
    projection,
    *,
    anchor_event_ref: str,
) -> dict[str, object]:
    """Fold the silence-anchored or inbound-answered pending hope into Capsule materials.

    An expired hope whose inviting receipt is this turn's trigger still gets
    timing facts: how long since he last spoke, and whether he has spoken
    since she declared the hope.  When the trigger is an inbound counterpart
    message observation, the open expectation she declared before his reply
    is attached with ``counterpart_replied=True`` so she can see what she was
    hoping for and assess ``how_it_landed``.  Accumulated unfulfilled
    expectations (``still_pending`` or overdue/repeated unanswered hopes) are
    also surfaced via ``unfulfilled_expectation_backlog``.
    """

    logical_time = getattr(projection, "logical_time", None)
    if logical_time is None:
        return context
    try:
        view = pending_response_expectation(
            projection, anchor_event_ref=anchor_event_ref
        )
    except (TypeError, ValueError):
        view = None
    if view is not None:
        advisory = response_expectation_advisory(
            view, source_ref=anchor_event_ref, logical_time=logical_time
        )
        context = _append_advisory(context, advisory)
        backlog = unfulfilled_expectation_backlog_advisory(projection)
        if backlog is not None:
            context = _append_advisory(context, backlog)
        return context

    message_obs = next(
        (
            item
            for item in getattr(projection, "message_observations", ()) or ()
            if getattr(item, "observation_id", None) == anchor_event_ref
        ),
        None,
    )
    obs_revision = getattr(message_obs, "world_revision", None)
    if isinstance(obs_revision, int) and not isinstance(obs_revision, bool):
        try:
            inbound_view = pending_response_expectation(
                projection, before_world_revision=obs_revision
            )
        except (TypeError, ValueError):
            inbound_view = None
        if inbound_view is not None:
            advisory = response_expectation_advisory(
                inbound_view,
                source_ref=anchor_event_ref,
                logical_time=logical_time,
                counterpart_replied=True,
            )
            context = _append_advisory(context, advisory)
        backlog = unfulfilled_expectation_backlog_advisory(
            projection, before_world_revision=obs_revision
        )
        if backlog is not None:
            context = _append_advisory(context, backlog)
        return context

    try:
        expired = expired_unanswered_expectation(
            projection, require_unanswered=False
        )
    except (TypeError, ValueError):
        expired = None
    if expired is not None and expired.receipt_event_id == anchor_event_ref:
        seconds, spoken_since = counterpart_last_spoke_facts(
            projection, since_world_revision=expired.declared_world_revision
        )
        context = _append_advisory(
            context,
            expired_expectation_advisory(
                expired,
                logical_time=logical_time,
                seconds_since_he_last_spoke=seconds,
                spoken_since_declared=spoken_since,
                projection=projection,
            ),
        )
    backlog = unfulfilled_expectation_backlog_advisory(projection)
    if backlog is not None:
        context = _append_advisory(context, backlog)
    return context


def _append_advisory(
    context: dict[str, object], advisory: InnerAdvisoryProjection
) -> dict[str, object]:
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
    if any(
        isinstance(item, dict) and item.get("source_ref") == advisory.advisory_id
        for item in items
    ):
        return context
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


__all__ = [
    "EXPIRED_EXPECTATION_GRACE",
    "OPEN_HOPE_CHASE_SECONDS",
    "OPEN_HOPE_LOCAL_TIMEZONE",
    "OPEN_HOPE_OVERNIGHT_END_HOUR",
    "OPEN_HOPE_WAKE_SECONDS",
    "is_overnight_local",
    "RESPONSE_EXPECTATION_ADVISORY_VERSION",
    "ExpiredUnansweredExpectation",
    "LivingUnansweredHope",
    "PendingResponseExpectationView",
    "answerable_receipt_history",
    "attach_pending_expectation_advisory",
    "counterpart_last_spoke_facts",
    "expired_expectation_advisory",
    "expired_hope_advisory_value",
    "expired_unanswered_expectation",
    "living_hope_hitch_clause",
    "living_unanswered_hope",
    "next_response_expectation_wake_at",
    "pending_response_expectation",
    "pending_response_expectation_manifest",
    "response_expectation_advisory",
    "unanswered_response_expectations",
    "unfulfilled_expectation_backlog_advisory",
]
