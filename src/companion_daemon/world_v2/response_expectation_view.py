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
from .schema_core import FrozenModel


RESPONSE_EXPECTATION_ADVISORY_VERSION = "response-expectation-view.3"
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
    terminal_plan_ids = {
        item.source_plan_id
        for item in getattr(projection, "response_expectation_assessments", ())
        if item.status in _TERMINAL_ASSESSMENT_STATES
    }

    if anchor_event_ref is not None:
        anchor = next((pair for pair in pairs if pair[0].event_id == anchor_event_ref), None)
        if anchor is None:
            return None
        anchor_ref, anchor_receipt = anchor
        if anchor_receipt.observed_state not in _ANSWERABLE_RECEIPT_STATES:
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
        return _view(
            expectation,
            declared_seconds_ago=int((logical_time - anchor_ref.logical_time).total_seconds()),
        )

    delivered_by_action: dict[str, object] = {}
    for ref, receipt in pairs:
        if receipt.observed_state not in _ANSWERABLE_RECEIPT_STATES:
            continue
        existing = delivered_by_action.get(receipt.action_id)
        if existing is None or ref.world_revision > existing.world_revision:
            delivered_by_action[receipt.action_id] = ref
    candidates = []
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
        if (
            before_world_revision is not None
            and delivered_ref.world_revision >= before_world_revision
        ):
            continue
        candidates.append((delivered_ref, manifest))
    if not candidates:
        return None
    delivered_ref, manifest = max(
        candidates, key=lambda item: (item[0].world_revision, item[1].acceptance_event_ref)
    )
    return _view(
        manifest.response_expectation,
        declared_seconds_ago=max(
            0, int((logical_time - delivered_ref.logical_time).total_seconds())
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


def expired_expectation_consideration_id(plan_id: str) -> str:
    return "consideration:social-initiative:expectation-expiry:" + _digest(plan_id)


def expired_unanswered_expectation(projection) -> ExpiredUnansweredExpectation | None:
    """One declared hope whose wait ran out.

    Whether he has spoken since she declared it is a fact for the advisory,
    not a host decision to suppress the opportunity.
    """

    try:
        logical_time = projection.logical_time
        if logical_time is None:
            return None
        receipt_refs = tuple(
            item
            for item in projection.committed_world_event_refs
            if item.event_type == "ExecutionReceiptRecorded"
        )
        if len(receipt_refs) != len(projection.execution_receipts):
            return None
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
        latest_by_action: dict[str, tuple[object, object]] = {}
        first_visible_by_action: dict[str, object] = {}
        for ref, receipt in zip(receipt_refs, projection.execution_receipts, strict=True):
            existing = latest_by_action.get(receipt.action_id)
            if existing is None or ref.world_revision > existing[0].world_revision:
                latest_by_action[receipt.action_id] = (ref, receipt)
            if receipt.observed_state in _ANSWERABLE_RECEIPT_STATES:
                visible = first_visible_by_action.get(receipt.action_id)
                if visible is None or ref.world_revision < visible.world_revision:
                    first_visible_by_action[receipt.action_id] = ref
        delivered_by_action = {
            action_id: ref
            for action_id, (ref, receipt) in latest_by_action.items()
            if receipt.observed_state in _ANSWERABLE_RECEIPT_STATES
        }
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
            if latest_message_revision > delivered_ref.world_revision:
                continue
            declared_at = _declared_world_clock(
                projection, action_id=beat.action.action_id, receipt_ref=declared_ref
            )
            # H21: wake at the declared wait (`not_before`). Cadence-derived
            # short wakes for waiting_for-without-wait conflicted with
            # "没填 wait 不编译盼头" and are not used here.
            if logical_time < authority_not_before:
                continue
            not_before = authority_not_before
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
                )
            )
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item.receipt_world_revision, item.plan_id))
    except (TypeError, ValueError, AttributeError):
        return None


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
    receipt_refs = tuple(
        item
        for item in projection.committed_world_event_refs
        if item.event_type == "ExecutionReceiptRecorded"
    )
    if len(receipt_refs) != len(projection.execution_receipts):
        raise ValueError("execution receipt projection does not align with committed refs")
    delivered_by_action: dict[str, object] = {}
    for ref, receipt in zip(receipt_refs, projection.execution_receipts, strict=True):
        if (
            receipt.observed_state in _ANSWERABLE_RECEIPT_STATES
            and ref.world_revision < before_world_revision
        ):
            prior = delivered_by_action.get(receipt.action_id)
            if prior is None or ref.world_revision > prior.world_revision:
                delivered_by_action[receipt.action_id] = ref
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
        if delivered_ref is not None:
            candidates.append((delivered_ref.world_revision, manifest))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


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


def _open_hope_cadence_due_at(
    projection, *, declared_ref, declared_at: datetime | None = None
) -> datetime | None:
    """When an open hope may reuse the expiry lane. None means do not mint.

    Host timing only: she still chooses now / later / silent on that consider.
    ``declared_at`` must be world logical time (her Action), not a provider
    wall-clock receipt. Receipt timestamps can sit in the future relative to
    the world clock and would mute a hot pause as overnight.
    """

    if declared_at is None:
        declared_at = getattr(declared_ref, "logical_time", None)
    declared_revision = getattr(declared_ref, "world_revision", None)
    if declared_at is None or declared_revision is None:
        return None
    heat = _exchange_heat_before_declared(projection, declared_revision, declared_at)
    delay = OPEN_HOPE_WAKE_SECONDS.get(heat)
    if delay is None:
        return None
    due = declared_at + timedelta(seconds=delay)
    if _is_overnight_local(due):
        return None
    return due


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


def _contact_since_hope_declared(projection, *, declared_at: datetime) -> bool:
    """True when she already used a contact at or after this hope.

    Cadence wakeup is one offer per hope. A later proactive/followup means
    she already had a turn; the host must not mint another short wake.
    Inbound replies are not this set, so a mid-chat ``waiting_for`` still
    gets its first cadence offer. Explicit ``wait`` does not use this gate.
    """

    return any(
        getattr(item, "kind", None) in _CONTACT_KINDS
        and getattr(item, "state", None) not in _CONTACT_SKIP_STATES
        and getattr(item, "logical_time", None) is not None
        and item.logical_time >= declared_at
        for item in getattr(projection, "actions", ()) or ()
    )


def _exchange_heat_before_declared(
    projection, declared_revision: int, declared_at: datetime
) -> str:
    """Heat of the exchange she answered, not the silence after she hoped.

    ``conversation_cadence`` measures gap from *now* to the last line. At
    hope-declaration that gap is ~0, so every reply would look hot. Classify
    at his inbound instead, using only earlier in/out lines.
    """

    inbound = None
    for ref in getattr(projection, "committed_world_event_refs", ()):
        if (
            getattr(ref, "event_type", None) != "ObservationRecorded"
            or getattr(ref, "world_revision", 0) >= declared_revision
        ):
            continue
        if inbound is None or ref.world_revision > inbound.world_revision:
            inbound = ref
    if inbound is None or getattr(inbound, "logical_time", None) is None:
        return "cold"
    recent = _cadence_messages_before(
        projection, before_world_revision=inbound.world_revision
    )
    recent = _collapse_cadence_turns(
        _cadence_window(recent, observed_at=inbound.logical_time)
    )
    previous_heat = None
    if len(recent) >= 1:
        try:
            prior_at = datetime.fromisoformat(recent[-1]["observed_at"])
        except (TypeError, ValueError):
            prior_at = None
        if prior_at is not None:
            previous_heat = derive_conversation_cadence(
                {"recent_messages": recent[:-1]},
                user_id="hope-cadence",
                observed_at=prior_at,
            ).heat
    cadence = derive_conversation_cadence(
        {"recent_messages": recent},
        user_id="hope-cadence",
        observed_at=inbound.logical_time,
        previous_heat=previous_heat,
    )
    del declared_at
    return cadence.heat


def _cadence_messages_before(projection, *, before_world_revision: int) -> list[dict[str, str]]:
    rows: list[tuple[int, dict[str, str]]] = []
    receipt_refs = tuple(
        item
        for item in getattr(projection, "committed_world_event_refs", ())
        if getattr(item, "event_type", None) == "ExecutionReceiptRecorded"
    )
    receipts = tuple(getattr(projection, "execution_receipts", ()) or ())
    if len(receipt_refs) == len(receipts):
        for ref, receipt in zip(receipt_refs, receipts, strict=True):
            if (
                getattr(ref, "world_revision", 0) >= before_world_revision
                or getattr(receipt, "observed_state", None) not in _ANSWERABLE_RECEIPT_STATES
                or getattr(ref, "logical_time", None) is None
            ):
                continue
            observed = _action_world_time(projection, receipt.action_id) or ref.logical_time
            rows.append(
                (
                    ref.world_revision,
                    {
                        "user_id": "",
                        "direction": "out",
                        "observed_at": observed.isoformat(),
                    },
                )
            )
    for ref in getattr(projection, "committed_world_event_refs", ()):
        if (
            getattr(ref, "event_type", None) != "ObservationRecorded"
            or getattr(ref, "world_revision", 0) >= before_world_revision
            or getattr(ref, "logical_time", None) is None
        ):
            continue
        rows.append(
            (
                ref.world_revision,
                {
                    "user_id": "",
                    "direction": "in",
                    "observed_at": ref.logical_time.isoformat(),
                },
            )
        )
    rows.sort(key=lambda item: item[0])
    return [item[1] for item in rows]


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


def _collapse_cadence_turns(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """One row per spoken turn. Multi-beat replies share one timestamp."""

    collapsed: list[dict[str, str]] = []
    for item in messages:
        if (
            collapsed
            and collapsed[-1].get("direction") == item.get("direction")
            and collapsed[-1].get("observed_at") == item.get("observed_at")
        ):
            continue
        collapsed.append(item)
    return collapsed


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
        receipt_refs = tuple(
            item
            for item in projection.committed_world_event_refs
            if item.event_type == "ExecutionReceiptRecorded"
        )
        if len(receipt_refs) != len(projection.execution_receipts):
            return None
        terminal_plan_ids = {
            item.source_plan_id
            for item in getattr(projection, "response_expectation_assessments", ())
            if item.status in _TERMINAL_ASSESSMENT_STATES
        }
        delivered_by_action: dict[str, object] = {}
        first_visible_by_action: dict[str, object] = {}
        for ref, receipt in zip(receipt_refs, projection.execution_receipts, strict=True):
            if receipt.observed_state not in _ANSWERABLE_RECEIPT_STATES:
                continue
            existing = delivered_by_action.get(receipt.action_id)
            if existing is None or ref.world_revision > existing.world_revision:
                delivered_by_action[receipt.action_id] = ref
            visible = first_visible_by_action.get(receipt.action_id)
            if visible is None or ref.world_revision < visible.world_revision:
                first_visible_by_action[receipt.action_id] = ref
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


def living_hope_hitch_clause(
    *,
    hoped_response: str,
    seconds_since_he_last_spoke: int | None,
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
    return f"She is waiting for (her words, not a world event): {hope}. {timing}"


def expired_hope_advisory_value(
    *,
    hoped_response: str,
    seconds_since_he_last_spoke: int | None,
    spoken_since_declared: bool,
) -> str:
    """Neutral timing facts for an expired hope.  She still decides.

    The hoped phrase is her leftover, not a world event. Putting it after
    ``Hope expired:`` read as if the thing she was waiting for had happened.
    """

    hope = hoped_response.strip()[:72]
    if seconds_since_he_last_spoke is None:
        timing = "No counterpart line is on the ledger yet."
    else:
        spoken = (
            "he has spoken since she declared a hope"
            if spoken_since_declared
            else "he has not spoken since she declared a hope"
        )
        timing = f"He last spoke {seconds_since_he_last_spoke}s ago; {spoken}."
    return (
        f"{timing} What she hoped for (her words, not a world event): {hope}. "
        "Timing evidence only; she still decides."
    )[:256]


def expired_expectation_advisory(
    expired: ExpiredUnansweredExpectation,
    *,
    logical_time: datetime,
    seconds_since_he_last_spoke: int | None,
    spoken_since_declared: bool,
) -> InnerAdvisoryProjection:
    """Wrap expired-hope timing facts in the ordinary advisory envelope."""

    source_ref = expired.receipt_event_id
    value = expired_hope_advisory_value(
        hoped_response=expired.hoped_response,
        seconds_since_he_last_spoke=seconds_since_he_last_spoke,
        spoken_since_declared=spoken_since_declared,
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


def attach_pending_expectation_advisory(
    context: dict[str, object],
    projection,
    *,
    anchor_event_ref: str,
) -> dict[str, object]:
    """Fold the silence-anchored pending hope into Capsule materials, if any.

    An expired hope whose inviting receipt is this turn's trigger still gets
    timing facts: how long since he last spoke, and whether he has spoken
    since she declared the hope.  That is not a suggestion to chase or stay
    silent.
    """

    try:
        view = pending_response_expectation(
            projection, anchor_event_ref=anchor_event_ref
        )
    except (TypeError, ValueError):
        view = None
    logical_time = getattr(projection, "logical_time", None)
    if view is not None and logical_time is not None:
        advisory = response_expectation_advisory(
            view, source_ref=anchor_event_ref, logical_time=logical_time
        )
        return _append_advisory(context, advisory)
    if logical_time is None:
        return context
    try:
        expired = expired_unanswered_expectation(projection)
    except (TypeError, ValueError):
        return context
    if expired is None or expired.receipt_event_id != anchor_event_ref:
        return context
    seconds, spoken_since = counterpart_last_spoke_facts(
        projection, since_world_revision=expired.declared_world_revision
    )
    return _append_advisory(
        context,
        expired_expectation_advisory(
            expired,
            logical_time=logical_time,
            seconds_since_he_last_spoke=seconds,
            spoken_since_declared=spoken_since,
        ),
    )


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
    "OPEN_HOPE_CHASE_SECONDS",
    "OPEN_HOPE_LOCAL_TIMEZONE",
    "OPEN_HOPE_OVERNIGHT_END_HOUR",
    "OPEN_HOPE_WAKE_SECONDS",
    "is_overnight_local",
    "RESPONSE_EXPECTATION_ADVISORY_VERSION",
    "ExpiredUnansweredExpectation",
    "LivingUnansweredHope",
    "PendingResponseExpectationView",
    "attach_pending_expectation_advisory",
    "counterpart_last_spoke_facts",
    "expired_expectation_advisory",
    "expired_hope_advisory_value",
    "expired_unanswered_expectation",
    "living_hope_hitch_clause",
    "living_unanswered_hope",
    "pending_response_expectation",
    "pending_response_expectation_manifest",
    "response_expectation_advisory",
]
