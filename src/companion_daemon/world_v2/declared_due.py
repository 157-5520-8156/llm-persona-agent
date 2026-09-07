"""Collect every accepted declared due into one clock-wake set.

The QQ scheduler used to wake on a handwritten list.  A recorded social-
initiative cadence was not on that list, so the world clock sat still while
her decision waited.  Adding one more item would only delay the next miss.

This module is the construction: projection due fields registered in
``delayed_trigger_owner_registry`` plus a small set of compiler-computed
dues are collected together.  The scheduler takes the next wake from that
set only — never from a second handwritten kind list in the host.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

WakePolicy = Literal["exact_future", "wall_catchup"]

# Compiler/host peeks that are accepted decisions but do not live as a stable
# projection field the collector can read alone.  The host must pass exactly
# these keys into ``collect_clock_wake_dues``; the AST gatekeeper checks that.
COMPUTED_CLOCK_WAKE_KINDS = frozenset(
    {
        "social.initiative.cadence",
        "private_impression.interval",
        "life.ecology",
    }
)

# Installed fields that are derived formulas and must never wake the clock.
# Empty extractors are allowed only for these; they stay registered so a later
# author cannot silently invent a wake by adding a field name alone.
NON_WAKING_PROJECTION_DUE_FIELDS = frozenset(
    {
        "SilenceOpportunity.anchored_at",
        "SilenceOpportunity.idle_seconds",
    }
)

# Fields whose wake path is a registered computed peek, not a projection read.
_FIELD_COMPUTED_COVER: Mapping[str, str] = {
    "ProactiveOpportunity.scheduled_for": "social.initiative.cadence",
}

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


@dataclass(frozen=True, slots=True)
class SchedulerWakeSnapshot:
    """Read-only clock and pending wakes, without claiming an atomic cursor.

    Due owners are peeked independently, as in the production scheduler. A
    caller must re-read after advancing or draining; only normal tick/claim
    CAS authorizes work. Overdue targets retain their original wake policy.
    """

    logical_time: datetime | None
    dues: tuple[DeclaredDueTarget, ...]


@dataclass(frozen=True, slots=True)
class _ProjectionExtractor:
    """One real read path from a projection into wake candidates."""

    name: str
    kinds: frozenset[str]
    fields: frozenset[str]
    extract: Callable[[object], tuple[DeclaredDueTarget, ...]]
    wake_collects: bool = True


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
    """Projection field names this collector knows how to read or explicitly skip."""

    covered = frozenset(field for item in _PROJECTION_EXTRACTORS for field in item.fields)
    return covered | frozenset(_FIELD_COMPUTED_COVER) | NON_WAKING_PROJECTION_DUE_FIELDS


def collected_clock_wake_kinds() -> frozenset[str]:
    """Kinds this collector can emit, including computed extras."""

    kinds = frozenset(kind for item in _PROJECTION_EXTRACTORS for kind in item.kinds)
    return kinds | COMPUTED_CLOCK_WAKE_KINDS | frozenset(_FIELD_COMPUTED_COVER.values())


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
        status = getattr(plan, "status", None)
        if status not in _LIVE_PLAN_STATUSES:
            continue
        window = getattr(plan, "scheduled_window", None)
        # Starting consumes the opening. Active/paused activities retain the
        # accepted end boundary; replaying their past opening would both miss
        # completion wakes and falsely report already-started work as overdue.
        # The lifecycle owner still decides the legal transition at that wake.
        due = (
            _window_open(window)
            if status == "planned"
            else _as_datetime(getattr(window, "closes_at", None))
        )
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
        status = getattr(occurrence, "status", None)
        if status not in _LIVE_OCCURRENCE_STATUSES:
            continue
        window = getattr(occurrence, "time_window", None)
        # Admission still wakes committed occurrences at the window opening.
        # An active occurrence cannot realize its complete frozen outcome
        # before the same accepted window ends (LifeAftermathRuntime).
        due = (
            _as_datetime(getattr(window, "closes_at", None))
            if status == "active"
            else _window_open(window)
        )
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


def _extract_chat_life_intent_retry(projection: object) -> tuple[DeclaredDueTarget, ...]:
    """Wake a recorded technical retry without selecting a character action."""

    accepted = {getattr(plan, "plan_id", None) for plan in _iter(getattr(projection, "plans", ()))}
    found: list[DeclaredDueTarget] = []
    kind = "life.chat_intent_acceptance"
    for failure in _iter(getattr(projection, "chat_life_intent_failures", ())):
        if getattr(failure, "terminal", True) or getattr(failure, "plan_id", None) in accepted:
            continue
        due = _as_datetime(getattr(failure, "next_retry_at", None))
        if due is not None:
            found.append(DeclaredDueTarget(
                kind=kind, due_at=due, reason=clock_wake_reason(kind),
                field="ChatLifeIntentFailure.next_retry_at",
            ))
    return tuple(found)


def _extract_proactive_technical_retry(projection: object) -> tuple[DeclaredDueTarget, ...]:
    from .proactive_action import next_proactive_retry_due

    try:
        due = next_proactive_retry_due(projection)
    except AttributeError:
        return ()
    if due is None:
        return ()
    return (
        DeclaredDueTarget(
            kind="proactive.technical_retry",
            due_at=due,
            reason=clock_wake_reason("proactive.technical_retry"),
            field="ProactiveTechnicalRetryState.next_retry_at",
        ),
    )


def _extract_expression_technical_retry(projection: object) -> tuple[DeclaredDueTarget, ...]:
    from .expression_episode_lifecycle import next_expression_retry_due

    try:
        due = next_expression_retry_due(projection)
    except AttributeError:
        return ()
    if due is None:
        return ()
    return (
        DeclaredDueTarget(
            kind="expression.technical_retry",
            due_at=due,
            reason=clock_wake_reason("expression.technical_retry"),
            field="ClaimLease.expires_at",
        ),
    )


def _extract_proactive_scheduled(projection: object) -> tuple[DeclaredDueTarget, ...]:
    """Read stored opportunity schedules when present on the projection.

    Post-silent / spontaneous cadence usually lives in RandomAuthority draws and
    enters the wake set via the ``social.initiative.cadence`` computed peek.
    This extractor covers the same field when a concrete opportunity object is
    already projected.
    """

    found: list[DeclaredDueTarget] = []
    for opportunity in _iter(getattr(projection, "proactive_opportunities", ())):
        due = _as_datetime(getattr(opportunity, "scheduled_for", None))
        if due is None:
            continue
        found.append(
            DeclaredDueTarget(
                kind="social.initiative.cadence",
                due_at=due,
                reason=clock_wake_reason("social.initiative.cadence"),
                field="ProactiveOpportunity.scheduled_for",
            )
        )
    return tuple(found)


def _extract_silence_formula(projection: object) -> tuple[DeclaredDueTarget, ...]:
    """Silence aftermath is a derived formula, not a stored due.

    Marked ``wake_collects=False``.  Inbound already advances the clock;
    leftover silence rides the next collected wake.
    """

    return ()


def _extract_perception_refresh(projection: object) -> tuple[DeclaredDueTarget, ...]:
    """Perception refresh may live on projection snapshots; hub also uses wall."""

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


_PROJECTION_EXTRACTORS: tuple[_ProjectionExtractor, ...] = (
    _ProjectionExtractor(
        name="_extract_action_authorized_due",
        kinds=frozenset(
            {
                "action.authorized_due",
                "media.execution",
                "media.delivery",
                "media.planning",
            }
        ),
        fields=frozenset({"Action.not_before", "Action.expires_at", "ClaimLease.expires_at"}),
        extract=_extract_action_authorized_due,
    ),
    _ProjectionExtractor(
        name="_extract_trigger_leases",
        kinds=frozenset({"action.authorized_due", "proactive.technical_retry"}),
        fields=frozenset({"ClaimLease.expires_at"}),
        extract=_extract_trigger_leases,
    ),
    _ProjectionExtractor(
        name="_extract_life_ecology",
        kinds=frozenset({"life.ecology"}),
        fields=frozenset({"LifeEcologyScheduleProjection.next_consideration_at"}),
        extract=_extract_life_ecology,
    ),
    _ProjectionExtractor(
        name="_extract_expression_beats",
        kinds=frozenset(
            {
                "expression.multibeat",
                "expression.deferred_reply",
                "conversation.expectation_expiry",
            }
        ),
        fields=frozenset(
            {
                "ExpressionPlanManifestBeatRef.not_before",
                "ExpressionPlanManifestBeatRef.expires_at",
                "ResponseExpectationAuthority.not_before",
                "ResponseExpectationAuthority.expires_at",
            }
        ),
        extract=_extract_expression_beats,
    ),
    _ProjectionExtractor(
        name="_extract_expression_technical_retry",
        kinds=frozenset({"expression.technical_retry"}),
        fields=frozenset({"ClaimLease.expires_at"}),
        extract=_extract_expression_technical_retry,
    ),
    _ProjectionExtractor(
        name="_extract_commitments",
        kinds=frozenset({"conversation.commitment_due"}),
        fields=frozenset({"CommitmentValues.due_window"}),
        extract=_extract_commitments,
    ),
    _ProjectionExtractor(
        name="_extract_threads",
        kinds=frozenset({"conversation.commitment_due"}),
        fields=frozenset({"CommitmentValues.due_window"}),
        extract=_extract_threads,
    ),
    _ProjectionExtractor(
        name="_extract_plans",
        kinds=frozenset({"life.activity_occurrence"}),
        fields=frozenset({"PlanStateProjection.scheduled_window"}),
        extract=_extract_plans,
    ),
    _ProjectionExtractor(
        name="_extract_occurrences",
        kinds=frozenset({"life.activity_occurrence"}),
        fields=frozenset({"WorldOccurrenceProjection.time_window"}),
        extract=_extract_occurrences,
    ),
    _ProjectionExtractor(
        name="_extract_appraisals",
        kinds=frozenset({"appraisal.expiry"}),
        fields=frozenset({"AppraisalProjection.expires_at"}),
        extract=_extract_appraisals,
    ),
    _ProjectionExtractor(
        name="_extract_affect",
        kinds=frozenset({"affect.decay"}),
        fields=frozenset({"AffectComponentProjection.decay_not_before"}),
        extract=_extract_affect,
    ),
    _ProjectionExtractor(
        name="_extract_goals",
        kinds=frozenset({"goal.expiry"}),
        fields=frozenset({"V2GoalValues.due_window"}),
        extract=_extract_goals,
    ),
    _ProjectionExtractor(
        name="_extract_media_opportunities",
        kinds=frozenset({"media.planning"}),
        fields=frozenset({"MediaOpportunity.expires_at"}),
        extract=_extract_media_opportunities,
    ),
    _ProjectionExtractor(
        name="_extract_chat_life_intent_retry",
        kinds=frozenset({"life.chat_intent_acceptance"}),
        fields=frozenset({"ChatLifeIntentFailure.next_retry_at"}),
        extract=_extract_chat_life_intent_retry,
    ),
    _ProjectionExtractor(
        name="_extract_proactive_technical_retry",
        kinds=frozenset({"proactive.technical_retry"}),
        fields=frozenset({"ProactiveTechnicalRetryState.next_retry_at"}),
        extract=_extract_proactive_technical_retry,
    ),
    _ProjectionExtractor(
        name="_extract_proactive_scheduled",
        kinds=frozenset(
            {
                "social.initiative.cadence",
                "proactive.event_driven",
                "proactive.ambient",
                "proactive.post_silent",
            }
        ),
        fields=frozenset({"ProactiveOpportunity.scheduled_for"}),
        extract=_extract_proactive_scheduled,
    ),
    _ProjectionExtractor(
        name="_extract_perception_refresh",
        kinds=frozenset({"perception.refresh_attention"}),
        fields=frozenset({"SourceHealthSnapshot.next_refresh_at"}),
        extract=_extract_perception_refresh,
    ),
    _ProjectionExtractor(
        name="_extract_silence_formula",
        kinds=frozenset({"relationship.silence_aftermath"}),
        fields=NON_WAKING_PROJECTION_DUE_FIELDS,
        extract=_extract_silence_formula,
        wake_collects=False,
    ),
)


def collect_projection_declared_dues(projection: object | None) -> tuple[DeclaredDueTarget, ...]:
    """Read every installed projection due that is still live and waking."""

    if projection is None:
        return ()
    collected: list[DeclaredDueTarget] = []
    for item in _PROJECTION_EXTRACTORS:
        if not item.wake_collects:
            continue
        collected.extend(item.extract(projection))
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


def collect_clock_wake_dues(
    projection: object | None,
    *,
    computed: Mapping[str, datetime | None],
) -> tuple[DeclaredDueTarget, ...]:
    """Sole selection input: projection dues plus effective owner peeks.

    ``computed`` must contain exactly ``COMPUTED_CLOCK_WAKE_KINDS`` as keys.
    Each peek supersedes its kind's projection value, including ``None`` for
    no pending due. Life's quiet completions can move the effective cadence
    in its sidecar while leaving the last semantic schedule unchanged.
    """

    missing = COMPUTED_CLOCK_WAKE_KINDS - frozenset(computed)
    extra = frozenset(computed) - COMPUTED_CLOCK_WAKE_KINDS
    if missing or extra:
        problems: list[str] = []
        if missing:
            problems.append("missing computed kinds: " + ", ".join(sorted(missing)))
        if extra:
            problems.append("unknown computed kinds: " + ", ".join(sorted(extra)))
        raise AssertionError("; ".join(problems))
    collected: list[DeclaredDueTarget] = [
        due for due in collect_projection_declared_dues(projection)
        if due.kind not in computed
    ]
    for kind in sorted(COMPUTED_CLOCK_WAKE_KINDS):
        wrapped = computed_due(kind, computed.get(kind))
        if wrapped is not None:
            collected.append(wrapped)
    return tuple(collected)


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
    """Fail closed when a registered due kind has no real collector path.

    Imported by the gatekeeper test and composition checks.  Production
    startup may call this; the test is the tripwire so a new due cannot ship
    without a wake.
    """

    from .delayed_trigger_owner_registry import (
        DELAYED_TRIGGER_OWNERS,
        INSTALLED_PROJECTION_DUE_FIELDS,
    )

    required = required_clock_wake_kinds()
    collected = collected_clock_wake_kinds()
    missing_kinds = sorted(required - collected)
    if missing_kinds:
        raise AssertionError(
            "declared dues without a clock-wake collector: " + ", ".join(missing_kinds)
        )

    covered_fields = collected_projection_due_fields()
    missing_fields = sorted(INSTALLED_PROJECTION_DUE_FIELDS - covered_fields)
    if missing_fields:
        raise AssertionError(
            "installed projection due fields without a collector: " + ", ".join(missing_fields)
        )

    # Every waking extractor must be reachable from collect_projection_declared_dues
    # (registered in _PROJECTION_EXTRACTORS with wake_collects=True).
    waking = tuple(item for item in _PROJECTION_EXTRACTORS if item.wake_collects)
    if not waking:
        raise AssertionError("no waking projection extractors registered")

    for field_name, computed_kind in _FIELD_COMPUTED_COVER.items():
        if computed_kind not in COMPUTED_CLOCK_WAKE_KINDS:
            raise AssertionError(
                f"field {field_name} claims computed cover {computed_kind} "
                "which is not in COMPUTED_CLOCK_WAKE_KINDS"
            )

    if not NON_WAKING_PROJECTION_DUE_FIELDS <= INSTALLED_PROJECTION_DUE_FIELDS:
        raise AssertionError(
            "NON_WAKING_PROJECTION_DUE_FIELDS must stay inside INSTALLED_PROJECTION_DUE_FIELDS"
        )
    silence_owners = tuple(
        owner
        for owner in DELAYED_TRIGGER_OWNERS
        if owner.mechanism_id == "relationship.silence_aftermath"
    )
    if len(silence_owners) != 1 or silence_owners[0].trigger_mode != "derived_formula":
        raise AssertionError(
            "relationship.silence_aftermath must remain a derived_formula non-wake owner"
        )


def assert_host_uses_declared_due_only(*, host_source: str | None = None) -> None:
    """AST gate: QQ scheduler must not keep a second handwritten due list.

    Checks that the public snapshot and scheduler use the same reader, whose
    sole ``collect_clock_wake_dues`` call supplies the registered computed
    keys. Neither caller may maintain an independent handwritten due list.
    """

    source = host_source
    if source is None:
        path = Path(__file__).resolve().parent / "qq_c2c_host.py"
        source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    methods: dict[str, ast.AsyncFunctionDef | ast.FunctionDef] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "QQC2CHost":
            for item in node.body:
                if isinstance(item, (ast.AsyncFunctionDef, ast.FunctionDef)):
                    methods[item.name] = item
    scheduler_fn = methods.get("_scheduler_once_serialized")
    if scheduler_fn is None:
        raise AssertionError("QQC2CHost._scheduler_once_serialized not found")

    collect_calls: list[ast.Call] = []
    computed_due_calls = 0
    forbidden_kind_lists: list[str] = []

    class _Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            nonlocal computed_due_calls
            name = _call_name(node)
            if name == "collect_clock_wake_dues":
                collect_calls.append(node)
            elif name == "computed_due":
                computed_due_calls += 1
            self.generic_visit(node)

        def visit_List(self, node: ast.List) -> None:  # noqa: N802
            kinds = _string_elements(node.elts)
            if kinds and kinds <= (
                COMPUTED_CLOCK_WAKE_KINDS
                | frozenset(
                    {
                        "action.authorized_due",
                        "expression.technical_retry",
                        "proactive.technical_retry",
                    }
                )
            ):
                forbidden_kind_lists.append(", ".join(sorted(kinds)))
            self.generic_visit(node)

        def visit_Tuple(self, node: ast.Tuple) -> None:  # noqa: N802
            kinds = _string_elements(node.elts)
            if len(kinds) >= 3 and kinds <= (
                COMPUTED_CLOCK_WAKE_KINDS
                | frozenset(
                    {
                        "action.authorized_due",
                        "expression.technical_retry",
                        "proactive.technical_retry",
                        "life.ecology",
                    }
                )
            ):
                forbidden_kind_lists.append(", ".join(sorted(kinds)))
            self.generic_visit(node)

    _Visitor().visit(scheduler_fn)
    snapshot_fn = methods.get("scheduler_wake_snapshot")
    reader_fn = methods.get("_read_scheduler_wake_snapshot")
    if snapshot_fn is None or reader_fn is None:
        raise AssertionError("scheduler and snapshot must share a collect_clock_wake_dues reader")
    for caller in (scheduler_fn, snapshot_fn):
        reader_calls = sum(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and node.func.attr == "_read_scheduler_wake_snapshot"
            for node in ast.walk(caller)
        )
        if reader_calls != 1:
            raise AssertionError(f"{caller.name} must call the shared wake reader exactly once")
    _Visitor().visit(snapshot_fn)
    if collect_calls:
        raise AssertionError("collect_clock_wake_dues must live only in the shared wake reader")
    _Visitor().visit(reader_fn)

    if len(collect_calls) != 1:
        raise AssertionError(
            "QQC2CHost._read_scheduler_wake_snapshot must call collect_clock_wake_dues exactly once "
            f"(found {len(collect_calls)})"
        )
    if computed_due_calls:
        raise AssertionError(
            "QQC2CHost wake callers and reader must not call computed_due; "
            "pass peeks through collect_clock_wake_dues"
        )
    if forbidden_kind_lists:
        raise AssertionError(
            "handwritten due-kind list in scheduler: " + "; ".join(forbidden_kind_lists)
        )

    call = collect_calls[0]
    computed_keys: set[str] = set()
    for keyword in call.keywords:
        if keyword.arg != "computed":
            continue
        if isinstance(keyword.value, ast.Dict):
            for key in keyword.value.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    computed_keys.add(key.value)
        elif isinstance(keyword.value, ast.Call) and _call_name(keyword.value) == "dict":
            for kw in keyword.value.keywords:
                if isinstance(kw.arg, str):
                    computed_keys.add(kw.arg)
            for arg in keyword.value.args:
                if isinstance(arg, ast.Dict):
                    for key in arg.keys:
                        if isinstance(key, ast.Constant) and isinstance(key.value, str):
                            computed_keys.add(key.value)
    if frozenset(computed_keys) != COMPUTED_CLOCK_WAKE_KINDS:
        raise AssertionError(
            "scheduler computed peeks must equal COMPUTED_CLOCK_WAKE_KINDS; "
            f"got {sorted(computed_keys)}, want {sorted(COMPUTED_CLOCK_WAKE_KINDS)}"
        )


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _string_elements(elts: list[ast.expr]) -> frozenset[str]:
    values: set[str] = set()
    for item in elts:
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            values.add(item.value)
        else:
            return frozenset()
    return frozenset(values)


__all__ = [
    "COMPUTED_CLOCK_WAKE_KINDS",
    "NON_WAKING_PROJECTION_DUE_FIELDS",
    "DeclaredDueTarget",
    "SchedulerWakeSnapshot",
    "assert_declared_due_wake_coverage",
    "assert_host_uses_declared_due_only",
    "clock_wake_reason",
    "collect_clock_wake_dues",
    "collect_projection_declared_dues",
    "collected_clock_wake_kinds",
    "collected_projection_due_fields",
    "computed_due",
    "required_clock_wake_kinds",
    "select_clock_wake",
]
