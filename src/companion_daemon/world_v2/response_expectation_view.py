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
import hashlib
import json
from typing import Literal

from pydantic import Field

from .context_capsule import InnerAdvisoryCandidate, InnerAdvisoryProjection
from .schema_core import FrozenModel


RESPONSE_EXPECTATION_ADVISORY_VERSION = "response-expectation-view.2"

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
            not_before = getattr(expectation, "not_before", None)
            if (
                expectation is None
                or not_before is None
                or manifest.plan_id in terminal_plan_ids
                or logical_time < not_before
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
                    declared_logical_time=declared_ref.logical_time,
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


def expired_hope_advisory_value(
    *,
    hoped_response: str,
    seconds_since_he_last_spoke: int | None,
    spoken_since_declared: bool,
) -> str:
    """Neutral timing facts for an expired hope.  She still decides."""

    hope = hoped_response.strip()[:72]
    if seconds_since_he_last_spoke is None:
        timing = "No counterpart line is on the ledger yet."
    else:
        spoken = (
            "he has spoken since this hope was declared"
            if spoken_since_declared
            else "he has not spoken since this hope was declared"
        )
        timing = f"He last spoke {seconds_since_he_last_spoke}s ago; {spoken}."
    return f"Hope expired: {hope} {timing} Timing evidence only; she still decides."[:256]


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
    "RESPONSE_EXPECTATION_ADVISORY_VERSION",
    "ExpiredUnansweredExpectation",
    "PendingResponseExpectationView",
    "attach_pending_expectation_advisory",
    "counterpart_last_spoke_facts",
    "expired_expectation_advisory",
    "expired_hope_advisory_value",
    "expired_unanswered_expectation",
    "pending_response_expectation",
    "pending_response_expectation_manifest",
    "response_expectation_advisory",
]
