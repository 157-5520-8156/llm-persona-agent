"""Collect every accepted declared due into one clock-wake set.

The QQ scheduler used to wake on a handwritten list.  A recorded social-
initiative cadence was not on that list, so the world clock sat still while
her decision waited.  Adding one more item would only delay the next miss.

This module is the construction: projection due fields registered in
``delayed_trigger_owner_registry`` plus a small set of compiler-computed
dues are collected together.  The scheduler takes the next wake from that
set.  A gatekeeper test fails when a new clock-due owner or installed
projection field is added without a collector.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

WakePolicy = Literal["exact_future", "wall_catchup"]

# Compiler-computed dues that are accepted decisions but do not live as a
# stored projection field until a process opens.  The gatekeeper requires
# these kinds to have a collector even though they are not INSTALLED fields.
COMPUTED_CLOCK_WAKE_KINDS = frozenset(
    {
        "social.initiative.cadence",
        "private_impression.interval",
        "expression.technical_retry",
    }
)

# Existing tick-reason strings that tests and ops already depend on.
_KIND_WAKE_REASONS: Mapping[str, str] = {
    "action.authorized_due": "qq_c2c_action_due_wake",
    "expression.technical_retry": "qq_c2c_expression_retry_wake",
    "proactive.technical_retry": "qq_c2c_proactive_retry_wake",
    "life.ecology": "qq_c2c_life_ecology_due_wake",
    "social.initiative.cadence": "qq_c2c_social_initiative_due_wake",
}

_WALL_CATCHUP_KINDS = frozenset({"life.ecology"})

_ACTION_DUE_STATES = frozenset({"authorized", "scheduled"})
_ACTION_LEASE_STATES = frozenset({"claimed", "dispatch_started", "provider_accepted"})
_LIVE_COMMITMENT_STATUSES = frozenset({"open", "due"})
_LIVE_THREAD_STATUSES = frozenset({"open"})
_LIVE_PLAN_STATUSES = frozenset({"planned", "active", "paused"})
_LIVE_OCCURRENCE_STATUSES = frozenset({"committed", "active"})
_LIVE_GOAL_STATUSES = frozenset({"active", "paused", "blocked"})
_LIVE_APPRAISAL_STATUSES = frozenset({"active"})
_LIVE_AFFECT_STATUSES = frozenset({"active"})


@dataclass(frozen=True, slots=True)
class DeclaredDueTarget:
    """One accepted due instant that can wake the world clock."""

    kind: str
    due_at: datetime
    reason: str
    wake_policy: WakePolicy = "exact_future"
    field: str | None = None


def clock_wake_reason(kind: str) -> str:
    """Stable scheduler reason for a collected due kind."""

    known = _KIND_WAKE_REASONS.get(kind)
    if known is not None:
        return known
    return f"qq_c2c_declared_due_wake:{kind}"


def required_clock_wake_kinds() -> frozenset[str]:
    """Every clock-due owner plus computed dues the scheduler must collect."""

    from .delayed_trigger_owner_registry import DELAYED_TRIGGER_OWNERS

    owners = frozenset(
        owner.mechanism_id
        for owner in DELAYED_TRIGGER_OWNERS
        if owner.trigger_mode == "clock_due" and owner.projection_due_fields
    )
    return owners | COMPUTED_CLOCK_WAKE_KINDS


def collected_projection_due_fields() -> frozenset[str]:
    """Projection field names this collector knows how to read."""

    return frozenset(_FIELD_EXTRACTORS)


def collected_clock_wake_kinds() -> frozenset[str]:
    """Kinds this collector can emit, including computed extras."""

    return frozenset(_KIND_EXTRACTORS) | COMPUTED_CLOCK_WAKE_KINDS


def _as_datetime(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _window_open(value: object) -> datetime | None:
    if value is None:
        return None
    opens = getattr(value, "opens_at", None)
    if isinstance(opens, datetime):
        return opens
    starts = getattr(value, "starts_at", None)
    if isinstance(starts, datetime):
        return starts
    if isinstance(value, tuple) and value and isinstance(value[0], datetime):
        return value[0]
    return None


def _iter(value: object) -> tuple[object, ...]:
    if value is None or isinstance(value, (str, bytes)):
        return ()
    if isinstance(value, Sequence):
        return tuple(value)
    return ()


def _extract_action_authorized_due(projection: object) -> tuple[DeclaredDueTarget, ...]:
    found: list[DeclaredDueTarget] = []
    for action in _iter(getattr(projection, "actions", ())):
        state = getattr(action, "state", None)
        if state in _ACTION_DUE_STATES:
            due = _as_datetime(getattr(action, "not_before", None))
            field = "Action.not_before"
        elif state in _ACTION_LEASE_STATES:
            lease = getattr(action, "claim_lease", None)
            due = _as_datetime(getattr(lease, "expires_at", None)) if lease is not None else None
            field = "ClaimLease.expires_at"
        else:
            due = None
            field = None
        if due is None:
            expires = _as_datetime(getattr(action, "expires_at", None))
            if expires is not None and state in _ACTION_DUE_STATES:
                due = expires
                field = "Action.expires_at"
        if due is not None and field is not None:
            found.append(
                DeclaredDueTarget(
                    kind="action.authorized_due",
                    due_at=due,
                    reason=clock_wake_reason("action.authorized_due"),
                    field=field,
                )
            )
    return tuple(found)


def _extract_life_ecology(projection: object) -> tuple[DeclaredDueTarget, ...]:
    schedule = getattr(projection, "life_ecology_schedule", None)
    due = _as_datetime(getattr(schedule, "next_consideration_at", None)) if schedule is not None else None
    if due is None:
        return ()
    return (
        DeclaredDueTarget(
            kind="life.ecology",
            due_at=due,
            reason=clock_wake_reason("life.ecology"),
            wake_policy="wall_catchup",
            field="LifeEcologyScheduleProjection.next_consideration_at",
        ),
    )


def _extract_expression_beats(projection: object) -> tuple[DeclaredDueTarget, ...]:
    found: list[DeclaredDueTarget] = []
    for manifest in _iter(getattr(projection, "expression_plan_manifests", ())):
        expectation = getattr(manifest, "response_expectation", None)
        if expectation is not None:
            for field_name, attr in (
                ("ResponseExpectationAuthority.not_before", "not_before"),
                ("ResponseExpectationAuthority.expires_at", "expires_at"),
            ):
                due = _as_datetime(getattr(expectation, attr, None))
                if due is not None:
                    found.append(
                        DeclaredDueTarget(
                            kind="conversation.expectation_expiry",
                            due_at=due,
                            reason=clock_wake_reason("conversation.expectation_expiry"),
                            field=field_name,
                        )
                    )
        for beat in _iter(getattr(manifest, "beats", ())):
            for field_name, attr in (
                ("ExpressionPlanManifestBeatRef.not_before", "not_before"),
                ("ExpressionPlanManifestBeatRef.expires_at", "expires_at"),
            ):
                due = _as_datetime(getattr(beat, attr, None))
                if due is not None:
                    found.append(
                        DeclaredDueTarget(
                            kind="expression.multibeat",
                            due_at=due,
                            reason=clock_wake_reason("expression.multibeat"),
                            field=field_name,
                        )
                    )
    return tuple(found)


def _extract_commitments(projection: object) -> tuple[DeclaredDueTarget, ...]:
    found: list[DeclaredDueTarget] = []
    for commitment in _iter(getattr(projection, "commitments", ())):
        values = getattr(commitment, "values", None)
        if values is None or getattr(values, "status", None) not in _LIVE_COMMITMENT_STATUSES:
            continue
        due = _window_open(getattr(values, "due_window", None))
        if due is not None:
            found.append(
                DeclaredDueTarget(
                    kind="conversation.commitment_due",
                    due_at=due,
                    reason=clock_wake_reason("conversation.commitment_due"),
                    field="CommitmentValues.due_window",
                )
            )
    return tuple(found)


def _extract_threads(projection: object) -> tuple[DeclaredDueTarget, ...]:
    found: list[DeclaredDueTarget] = []
    for thread in _iter(getattr(projection, "threads", ())):
        values = getattr(thread, "values", None)
        status = getattr(values, "status", None) if values is not None else getattr(thread, "status", None)
        if status not in _LIVE_THREAD_STATUSES:
            continue
        due_window = getattr(values, "due_window", None) if values is not None else getattr(thread, "due_window", None)
        due = _window_open(due_window)
        if due is not None:
            found.append(
                DeclaredDueTarget(
                    kind="conversation.commitment_due",
                    due_at=due,
                    reason=clock_wake_reason("conversation.commitment_due"),
                    field="CommitmentValues.due_window",
                )
            )
    return tuple(found)


def _extract_plans(projection: object) -> tuple[DeclaredDueTarget, ...]:
    found: list[DeclaredDueTarget] = []
    for plan in _iter(getattr(projection, "plans", ())):
        if getattr(plan, "status", None) not in _LIVE_PLAN_STATUSES:
            continue
        due = _window_open(getattr(plan, "scheduled_window", None))
        if due is not None:
            found.append(
                DeclaredDueTarget(
                    kind="life.activity_occurrence",
                    due_at=due,
                    reason=clock_wake_reason("life.activity_occurrence"),
                    field="PlanStateProjection.scheduled_window",
                )
            )
    return tuple(found)


def _extract_occurrences(projection: object) -> tuple[DeclaredDueTarget, ...]:
    found: list[DeclaredDueTarget] = []
    for occurrence in _iter(getattr(projection, "world_occurrences", ())):
        if getattr(occurrence, "status", None) not in _LIVE_OCCURRENCE_STATUSES:
            continue
        due = _window_open(getattr(occurrence, "time_window", None))
        if due is not None:
            found.append(
                DeclaredDueTarget(
                    kind="life.activity_occurrence",
                    due_at=due,
                    reason=clock_wake_reason("life.activity_occurrence"),
                    field="WorldOccurrenceProjection.time_window",
                )
            )
    return tuple(found)


def _extract_appraisals(projection: object) -> tuple[DeclaredDueTarget, ...]:
    found: list[DeclaredDueTarget] = []
    for appraisal in _iter(getattr(projection, "appraisals", ())):
        if getattr(appraisal, "status", None) not in _LIVE_APPRAISAL_STATUSES:
            continue
        due = _as_datetime(getattr(appraisal, "expires_at", None))
        if due is not None:
            found.append(
                DeclaredDueTarget(
                    kind="appraisal.expiry",
                    due_at=due,
                    reason=clock_wake_reason("appraisal.expiry"),
                    field="AppraisalProjection.expires_at",
                )
            )
    return tuple(found)


def _extract_affect(projection: object) -> tuple[DeclaredDueTarget, ...]:
    found: list[DeclaredDueTarget] = []
    for episode in _iter(getattr(projection, "affect_episodes", ())):
        if getattr(episode, "status", None) not in _LIVE_AFFECT_STATUSES:
            continue
        for component in _iter(getattr(episode, "components", ())):
            due = _as_datetime(getattr(component, "decay_not_before", None))
            if due is not None:
                found.append(
                    DeclaredDueTarget(
                        kind="affect.decay",
                        due_at=due,
                        reason=clock_wake_reason("affect.decay"),
                        field="AffectComponentProjection.decay_not_before",
                    )
                )
    return tuple(found)


def _extract_goals(projection: object) -> tuple[DeclaredDueTarget, ...]:
    found: list[DeclaredDueTarget] = []
    for goal in _iter(getattr(projection, "goals", ())):
        values = getattr(goal, "values", goal)
        if getattr(values, "status", None) not in _LIVE_GOAL_STATUSES:
            continue
        due = _window_open(getattr(values, "due_window", None))
        if due is not None:
            found.append(
                DeclaredDueTarget(
                    kind="goal.expiry",
                    due_at=due,
                    reason=clock_wake_reason("goal.expiry"),
                    field="V2GoalValues.due_window",
                )
            )
    return tuple(found)


def _extract_media_opportunities(projection: object) -> tuple[DeclaredDueTarget, ...]:
    found: list[DeclaredDueTarget] = []
    for opportunity in _iter(getattr(projection, "media_opportunities", ())):
        due = _as_datetime(getattr(opportunity, "expires_at", None))
        if due is not None:
            found.append(
                DeclaredDueTarget(
                    kind="media.planning",
                    due_at=due,
                    reason=clock_wake_reason("media.planning"),
                    field="MediaOpportunity.expires_at",
                )
            )
    for candidate in _iter(getattr(projection, "photo_candidates", ())):
        due = _as_datetime(getattr(candidate, "expires_at", None))
        if due is not None and getattr(candidate, "status", None) in {None, "available", "open"}:
            found.append(
                DeclaredDueTarget(
                    kind="media.planning",
                    due_at=due,
                    reason=clock_wake_reason("media.planning"),
                    field="MediaOpportunity.expires_at",
                )
            )
    return tuple(found)


def _extract_trigger_leases(projection: object) -> tuple[DeclaredDueTarget, ...]:
    found: list[DeclaredDueTarget] = []
    for process in _iter(getattr(projection, "trigger_processes", ())):
        if getattr(process, "state", None) not in {"claimed", "open"}:
            continue
        lease = getattr(process, "claim_lease", None)
        due = _as_datetime(getattr(lease, "expires_at", None)) if lease is not None else None
        if due is None:
            continue
        kind = (
            "proactive.technical_retry"
            if getattr(process, "process_kind", None) == "proactive_action_deliberation"
            else "action.authorized_due"
        )
        found.append(
            DeclaredDueTarget(
                kind=kind,
                due_at=due,
                reason=clock_wake_reason(kind),
                field="ClaimLease.expires_at",
            )
        )
    return tuple(found)


def _extract_silence_formula(projection: object) -> tuple[DeclaredDueTarget, ...]:
    """Silence aftermath is a derived formula, not a stored due.

    The registry marks it ``derived_formula``.  Collecting last-message +
    idle would invent a policy constant here.  Inbound already advances the
    clock; leftover silence rides the next collected wake.
    """

    return ()


def _extract_perception_refresh(projection: object) -> tuple[DeclaredDueTarget, ...]:
    """Perception refresh lives on the hub sidecar, not the world projection.

    A host may pass it as a computed due.  The projection has no
    ``SourceHealthSnapshot.next_refresh_at``.
    """

    snapshots = _iter(getattr(projection, "external_signal_snapshots", ()))
    found: list[DeclaredDueTarget] = []
    for snapshot in snapshots:
        due = _as_datetime(getattr(snapshot, "next_refresh_at", None))
        if due is not None:
            found.append(
                DeclaredDueTarget(
                    kind="perception.refresh_attention",
                    due_at=due,
                    reason=clock_wake_reason("perception.refresh_attention"),
                    field="SourceHealthSnapshot.next_refresh_at",
                )
            )
    return tuple(found)


_FIELD_EXTRACTORS: Mapping[str, str] = {
    "Action.not_before": "action.authorized_due",
    "Action.expires_at": "action.authorized_due",
    "ClaimLease.expires_at": "action.authorized_due",
    "ProactiveOpportunity.scheduled_for": "social.initiative.cadence",
    "ProactiveTechnicalRetryState.next_retry_at": "proactive.technical_retry",
    "LifeEcologyScheduleProjection.next_consideration_at": "life.ecology",
    "ExpressionPlanManifestBeatRef.not_before": "expression.multibeat",
    "ExpressionPlanManifestBeatRef.expires_at": "expression.multibeat",
    "CommitmentValues.due_window": "conversation.commitment_due",
    "ResponseExpectationAuthority.not_before": "conversation.expectation_expiry",
    "ResponseExpectationAuthority.expires_at": "conversation.expectation_expiry",
    "PlanStateProjection.scheduled_window": "life.activity_occurrence",
    "WorldOccurrenceProjection.time_window": "life.activity_occurrence",
    "AffectComponentProjection.decay_not_before": "affect.decay",
    "AppraisalProjection.expires_at": "appraisal.expiry",
    "SilenceOpportunity.anchored_at": "relationship.silence_aftermath",
    "SilenceOpportunity.idle_seconds": "relationship.silence_aftermath",
    "V2GoalValues.due_window": "goal.expiry",
    "SourceHealthSnapshot.next_refresh_at": "perception.refresh_attention",
    "MediaOpportunity.expires_at": "media.planning",
}

_KIND_EXTRACTORS: frozenset[str] = frozenset(_FIELD_EXTRACTORS.values()) | frozenset(
    {
        "expression.deferred_reply",
        "media.execution",
        "media.delivery",
        "proactive.event_driven",
        "proactive.ambient",
        "proactive.post_silent",
    }
)


def collect_projection_declared_dues(projection: object | None) -> tuple[DeclaredDueTarget, ...]:
    """Read every installed projection due that is still live."""

    if projection is None:
        return ()
    collected: list[DeclaredDueTarget] = []
    collected.extend(_extract_action_authorized_due(projection))
    collected.extend(_extract_trigger_leases(projection))
    collected.extend(_extract_life_ecology(projection))
    collected.extend(_extract_expression_beats(projection))
    collected.extend(_extract_commitments(projection))
    collected.extend(_extract_threads(projection))
    collected.extend(_extract_plans(projection))
    collected.extend(_extract_occurrences(projection))
    collected.extend(_extract_appraisals(projection))
    collected.extend(_extract_affect(projection))
    collected.extend(_extract_goals(projection))
    collected.extend(_extract_media_opportunities(projection))
    collected.extend(_extract_perception_refresh(projection))
    collected.extend(_extract_silence_formula(projection))
    return tuple(collected)


def computed_due(
    kind: str,
    due_at: datetime | None,
    *,
    wake_policy: WakePolicy | None = None,
    field: str | None = None,
) -> DeclaredDueTarget | None:
    """Wrap a host/compiler due so it enters the same wake set."""

    if not isinstance(due_at, datetime):
        return None
    return DeclaredDueTarget(
        kind=kind,
        due_at=due_at,
        reason=clock_wake_reason(kind),
        wake_policy=wake_policy or ("wall_catchup" if kind in _WALL_CATCHUP_KINDS else "exact_future"),
        field=field,
    )


def select_clock_wake(
    *,
    after: datetime,
    through: datetime,
    dues: Iterable[DeclaredDueTarget],
) -> DeclaredDueTarget | None:
    """Return the next wake inside ``(after, through]``, honouring wake policy.

    ``exact_future`` only ticks toward a still-future instant.  An already-due
    consider is the current cursor's job; jumping to wall would skip siblings.
    ``wall_catchup`` (Life) may jump to ``through`` when the due is already at
    or behind the cursor, because Life is triggered *by* the tick.
    """

    candidates: list[DeclaredDueTarget] = []
    for due in dues:
        if due.wake_policy == "wall_catchup":
            if after < due.due_at <= through:
                candidates.append(due)
            elif due.due_at <= after < through:
                candidates.append(
                    DeclaredDueTarget(
                        kind=due.kind,
                        due_at=through,
                        reason=due.reason,
                        wake_policy=due.wake_policy,
                        field=due.field,
                    )
                )
            continue
        if after < due.due_at <= through:
            candidates.append(due)
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item.due_at, item.kind, item.reason))


def assert_declared_due_wake_coverage() -> None:
    """Fail closed when a registered due kind has no collector.

    Imported by the gatekeeper test.  Production startup does not call this;
    the test is the tripwire so a new due cannot ship without a wake.
    """

    from .delayed_trigger_owner_registry import INSTALLED_PROJECTION_DUE_FIELDS

    required = required_clock_wake_kinds()
    collected = collected_clock_wake_kinds()
    missing_kinds = sorted(required - collected)
    if missing_kinds:
        raise AssertionError(
            "declared dues without a clock-wake collector: " + ", ".join(missing_kinds)
        )
    missing_fields = sorted(INSTALLED_PROJECTION_DUE_FIELDS - collected_projection_due_fields())
    if missing_fields:
        raise AssertionError(
            "installed projection due fields without a collector: " + ", ".join(missing_fields)
        )


__all__ = [
    "COMPUTED_CLOCK_WAKE_KINDS",
    "DeclaredDueTarget",
    "assert_declared_due_wake_coverage",
    "clock_wake_reason",
    "collect_projection_declared_dues",
    "collected_clock_wake_kinds",
    "collected_projection_due_fields",
    "computed_due",
    "required_clock_wake_kinds",
    "select_clock_wake",
]
