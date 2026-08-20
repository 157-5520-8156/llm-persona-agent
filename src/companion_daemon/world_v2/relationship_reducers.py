"""Pure reducers for relationship slow variables, signals, and boundaries.

Stage from slow variables is a mean-of-six ladder (close_friend enter 7000 /
exit 6200).  That ladder is the slow road.  The main road is her own
``we_are`` declaration.  Do not lower the close_friend number because two
axes were left at 0: reliability and repair have the same write path as
trust and closeness.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
import hashlib
import json

from .relationship_events import (
    BoundaryChangedPayload,
    RelationshipCommitmentAcceptedPayload,
    RelationshipSignalAcceptedPayload,
    RelationshipSlowVariableAdjustedPayload,
)
from .schema_core import FrozenModel
from .schemas import (
    BoundaryProjection,
    RelationshipAdjustmentProjection,
    RelationshipCommitmentProjection,
    RelationshipHysteresisProjection,
    RelationshipStateOrigin,
    RelationshipSignalProjection,
    RelationshipStateProjection,
    RelationshipVariableDeltas,
    RelationshipVariablesProjection,
)


_VARIABLE_NAMES = (
    "trust_bp",
    "closeness_bp",
    "respect_bp",
    "reliability_bp",
    "mutuality_bp",
    "repair_confidence_bp",
)
# Everyday chat moves trust/closeness/mutuality/respect.  reliability and
# repair_confidence stay at 0 until a rupture or repair episode is written;
# they must not drag the ordinary ladder down to stranger forever.
_PRIMARY_LADDER_AXES = (
    "trust_bp",
    "closeness_bp",
    "mutuality_bp",
    "respect_bp",
)
_STAGES = ("stranger", "acquaintance", "friend", "close_friend")
# Stages nobody can reach by accumulating closeness.  Becoming something more
# than close friends is not a higher score on the same ladder: it is a reading
# only she can declare, in words she actually sends, so these stages are
# reachable through an explicit commitment and never derived from thresholds.
COMMITMENT_ONLY_RELATIONSHIP_STAGES = frozenset({"ambiguous", "lover"})
RELATIONSHIP_COMMITMENT_STAGE_TRANSITIONS = {
    "stranger": frozenset({"acquaintance", "friend"}),
    "acquaintance": frozenset({"friend"}),
    "friend": frozenset({"close_friend"}),
    "close_friend": frozenset({"ambiguous"}),
    "ambiguous": frozenset({"lover", "close_friend"}),
    "lover": frozenset({"ambiguous"}),
}
_POLICY = {
    "policy_version": "relationship-policy.1",
    "delta_cap_bp": 500,
    "stage_order": _STAGES,
    # close_friend enter 7000 / exit 6200 is mean-of-six.  That is the slow
    # ladder, not the main road: she reaches this stage by declaring
    # we_are=close_friend.  reliability and repair have the same write path
    # as trust/closeness and do not require a prior commitment or rupture;
    # if they stay at 0 it is because she has not written them, not because
    # the ladder is unreachable.  Four-axis saturation is 6666 and must not
    # be "fixed" by lowering this number.
    # Ordinary ladder (stranger/acquaintance/friend) scores four primary axes.
    # close_friend enter/exit still uses mean-of-six at 7000/6200; that stage is
    # also reachable via we_are, which remains the main road.
    "enter_bp": {"acquaintance": 500, "friend": 1_800, "close_friend": 7_000},
    "exit_bp": {"acquaintance": 400, "friend": 1_500, "close_friend": 6_200},
    "required_confirmations": 2,
    "minimum_dwell_seconds": 86_400,
    "stage_step_limit": 1,
    "aggregation": "mean-four-primary-for-ordinary-ladder-mean-six-for-close-friend",
}


def relationship_policy_digest() -> str:
    commitment_stage_transitions = {
        stage: tuple(sorted(targets))
        for stage, targets in sorted(RELATIONSHIP_COMMITMENT_STAGE_TRANSITIONS.items())
    }
    encoded = json.dumps(
        {
            **_POLICY,
            "commitment_stage_transitions": commitment_stage_transitions,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


RELATIONSHIP_POLICY_DIGEST = relationship_policy_digest()
# Digests this codebase itself installed before the digest input was widened,
# for relationship state that is still carried in a projection.  The only entry
# is the pre-2026-08-11 stamp: ``_POLICY`` was byte-identical (same version,
# caps, thresholds, dwell and aggregation) and the digest changed solely because
# ``RELATIONSHIP_COMMITMENT_STAGE_TRANSITIONS`` joined the hashed payload.  So
# the accumulated variables were produced under numerically identical rules.
#
# Replay already tolerated these on stored events, but an epoch genesis carries
# relationship *state* rather than the original events, and every live
# adjustment re-asserted the current digest against that state.  The effect was
# a permanent fail-closed: production held closeness 180 / trust 100 from
# epoch 1 and could never adjust again.  Reading a retired stamp is therefore
# allowed; the mutation being written always carries the installed digest, so
# the first successful adjustment migrates the state forward.
RETIRED_RELATIONSHIP_POLICY_DIGESTS = frozenset(
    {
        # Pre-2026-08-11: commitment transitions were not yet hashed.
        "64d8b7ffc6f38f79d31bb8a83212c5668ff908ab3f6d7c924dd75ad71fb94e95",
        # Pre-2026-08-17: the transition graph stopped at close_friend, so
        # ambiguous and lover could not be declared.  The six numeric axes,
        # caps, thresholds and dwell are unchanged, and no stage that existed
        # under this digest changes meaning under the current one.
        "13bfa71dd9f8377b968714eb3d4f9a927e587832c92d2381c6ecc772071deede",
        # 2026-08-18 same-day close_friend 6000/5400 lowering.  Restored to
        # 7000/6200: four-axis saturation is not a reason to move the ladder.
        "374b96bb36fac6dfb607622075e40f1ae7fbb4dc30c802f6267727805f4fa912",
        # Pre-2026-08-20: mean-of-six at 2000/4500/7000.  Ordinary ladder now
        # uses four primary axes at 500/1800; close_friend threshold unchanged.
        "2ec7c0874f219a2fa2c7800d3679c393705905130a9007b2833148cb1df9b8de",
    }
)


def _policy_stamp(state: object) -> tuple[str | None, str | None]:
    """Read policy version/digest from a projection or a dumped mapping."""

    if isinstance(state, Mapping):
        version = state.get("policy_version")
        digest = state.get("policy_digest")
    else:
        version = getattr(state, "policy_version", None)
        digest = getattr(state, "policy_digest", None)
    return (
        version if isinstance(version, str) else None,
        digest if isinstance(digest, str) else None,
    )


def relationship_state_policy_is_readable(state: object) -> bool:
    """Whether existing state was written by an installed or retired policy."""

    version, digest = _policy_stamp(state)
    if version != _POLICY["policy_version"] or digest is None:
        return False
    return digest == RELATIONSHIP_POLICY_DIGEST or digest in (
        RETIRED_RELATIONSHIP_POLICY_DIGESTS
    )


class RelationshipAdjustmentPreview(FrozenModel):
    """Reducer-equivalent state material for one ordinary adjustment.

    This is intentionally an *input-to-proposal* value, not authority.  The
    adjustment compiler can use it to materialize an explicit typed mutation,
    while :func:`adjust_relationship_slow_variables` remains the sole reducer
    that accepts that mutation into relationship state.
    """

    relationship_id: str
    subject_ref: str
    expected_entity_revision: int
    variables_before: RelationshipVariablesProjection
    variables_after: RelationshipVariablesProjection
    stage_before: str
    stage_after: str
    hysteresis_before: RelationshipHysteresisProjection
    hysteresis_after: RelationshipHysteresisProjection
    commitment_refs: tuple[str, ...]
    policy_version: str
    policy_digest: str


def relationship_primary_id(*, subject_ref: str) -> str:
    """Derive the primary World v2 relationship identity for one subject."""

    if not subject_ref:
        raise ValueError("relationship subject is required")
    identity = hashlib.sha256(
        json.dumps({"subject_ref": subject_ref}, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return f"relationship:primary:{identity}"


def preview_relationship_slow_variable_adjustment(
    *,
    states: tuple[RelationshipStateProjection, ...],
    history: tuple[RelationshipAdjustmentProjection, ...],
    signals: tuple[RelationshipSignalProjection, ...],
    subject_ref: str,
    signal_refs: tuple[str, ...],
    proposed_deltas: RelationshipVariableDeltas,
    accepted_deltas: RelationshipVariableDeltas,
    logical_time: datetime,
) -> RelationshipAdjustmentPreview:
    """Preview the exact reducer material for one ordinary adjustment.

    The preview deliberately owns no aggregation policy: callers must provide
    the already-selected signals and bounded deltas.  It does own every
    ordinary-adjustment invariant which can be decided before a typed payload
    exists, so a payload built from this result reduces without a divergent
    second calculation.
    """

    _require_aware(logical_time)
    if not subject_ref:
        raise ValueError("relationship adjustment subject is required")
    if not signal_refs or len(signal_refs) != len(set(signal_refs)):
        raise ValueError("relationship adjustment requires unique signal refs")
    resolved_signals = []
    for signal_ref in signal_refs:
        matches = [item for item in signals if item.signal_id == signal_ref]
        if len(matches) != 1 or matches[0].subject_ref != subject_ref:
            raise ValueError("relationship adjustment signal does not resolve")
        resolved_signals.append(matches[0])
    consumed_refs = {
        signal_ref
        for item in history
        if item.operation == "adjust"
        for signal_ref in item.signal_refs
    }
    if any(signal_ref in consumed_refs for signal_ref in signal_refs):
        raise ValueError("relationship adjustment requires all signals to be unconsumed")
    _validate_adjustment_deltas(proposed_deltas=proposed_deltas, accepted_deltas=accepted_deltas)

    subject_matches = tuple(item for item in states if item.subject_ref == subject_ref)
    if len(subject_matches) > 1:
        raise ValueError("duplicate relationship state authority for subject")
    if subject_matches:
        current = subject_matches[0]
        if not relationship_state_policy_is_readable(current):
            raise ValueError("relationship state references an uninstalled policy")
        if current.last_adjusted_at is not None and logical_time < current.last_adjusted_at:
            raise ValueError("relationship adjustment precedes current state")
        relationship_id = current.relationship_id
        revision = current.entity_revision
        before = current.variables
        stage = current.stage
        hysteresis = current.hysteresis
        commitment_refs = current.commitment_refs
    else:
        relationship_id = relationship_primary_id(subject_ref=subject_ref)
        revision = 0
        before = RelationshipVariablesProjection()
        stage = "stranger"
        hysteresis = RelationshipHysteresisProjection()
        commitment_refs = ()
    if hysteresis.candidate_since is not None and hysteresis.candidate_since > logical_time:
        raise ValueError("relationship hysteresis candidate starts in the future")
    after = _apply_deltas(before, accepted_deltas)
    stage_after, hysteresis_after = _derive_stage(stage, after, hysteresis, logical_time)
    if after == before and stage_after == stage and hysteresis_after == hysteresis:
        raise ValueError("relationship adjustment is a semantic no-op")
    return RelationshipAdjustmentPreview(
        relationship_id=relationship_id,
        subject_ref=subject_ref,
        expected_entity_revision=revision,
        variables_before=before,
        variables_after=after,
        stage_before=stage,
        stage_after=stage_after,
        hysteresis_before=hysteresis,
        hysteresis_after=hysteresis_after,
        commitment_refs=commitment_refs,
        policy_version=_POLICY["policy_version"],
        policy_digest=RELATIONSHIP_POLICY_DIGEST,
    )


def accept_relationship_signal(
    signals: tuple[RelationshipSignalProjection, ...],
    payload: RelationshipSignalAcceptedPayload,
    *,
    logical_time: datetime,
) -> tuple[RelationshipSignalProjection, ...]:
    _require_aware(logical_time)
    signal = payload.signal
    if signal.accepted_at != logical_time:
        raise ValueError("relationship signal must use authoritative logical time")
    if any(item.signal_id == signal.signal_id for item in signals):
        raise ValueError("relationship signal already exists")
    if any(item.semantic_fingerprint == signal.semantic_fingerprint for item in signals):
        raise ValueError("relationship signal semantic evidence already exists")
    return (*signals, signal)


def accept_relationship_commitment(
    commitments: tuple[RelationshipCommitmentProjection, ...],
    states: tuple[RelationshipStateProjection, ...],
    payload: RelationshipCommitmentAcceptedPayload,
    *,
    logical_time: datetime,
    accepted_event_ref: str,
) -> tuple[
    tuple[RelationshipCommitmentProjection, ...],
    tuple[RelationshipStateProjection, ...],
]:
    """Atomically derive commitment history and the relationship head.

    The caller supplies one already-authored typed commitment.  This reducer
    validates mechanical authority only; it never derives a commitment from
    message text, signals, scores, or local social rules.
    """

    _require_aware(logical_time)
    commitment = payload.commitment
    if commitment.committed_at != logical_time:
        raise ValueError("relationship commitment must use authoritative logical time")
    if commitment.origin.accepted_event_ref != accepted_event_ref:
        raise ValueError("relationship commitment origin does not identify its mutation event")
    if payload.policy_version != _POLICY["policy_version"]:
        raise ValueError("uninstalled relationship policy")
    if payload.policy_digest != RELATIONSHIP_POLICY_DIGEST:
        raise ValueError("relationship policy digest is not installed")
    if payload.relationship_id != relationship_primary_id(subject_ref=payload.subject_ref):
        raise ValueError("relationship commitment must use the primary relationship identity")
    if any(item.commitment_id == commitment.commitment_id for item in commitments):
        raise ValueError("relationship commitment already exists")

    identity_matches = [
        (index, item)
        for index, item in enumerate(states)
        if item.relationship_id == payload.relationship_id
    ]
    subject_matches = [
        (index, item)
        for index, item in enumerate(states)
        if item.subject_ref == payload.subject_ref
    ]
    if len(identity_matches) > 1 or len(subject_matches) > 1:
        raise ValueError("duplicate relationship state authority")
    if identity_matches and identity_matches[0][1].subject_ref != payload.subject_ref:
        raise ValueError("relationship identity changed subject")
    if subject_matches and subject_matches[0][1].relationship_id != payload.relationship_id:
        raise ValueError("relationship subject changed authority identity")

    if subject_matches:
        index, current = subject_matches[0]
        revision = current.entity_revision
        stage = current.stage
        hysteresis = current.hysteresis
        commitment_refs = current.commitment_refs
        variables = current.variables
        temperature = current.temperature
        last_adjusted_at = current.last_adjusted_at
        if not relationship_state_policy_is_readable(current):
            raise ValueError("relationship state references an uninstalled policy")
    else:
        index = None
        revision = 0
        stage = "stranger"
        hysteresis = RelationshipHysteresisProjection()
        commitment_refs = ()
        variables = RelationshipVariablesProjection()
        temperature = "ordinary"
        last_adjusted_at = None

    if revision != payload.expected_entity_revision:
        raise ValueError("stale relationship commitment")
    if stage != payload.stage_before:
        raise ValueError("relationship commitment stage before is stale")
    if hysteresis != payload.hysteresis_before:
        raise ValueError("relationship commitment hysteresis is stale")
    if commitment_refs != payload.commitment_refs_before:
        raise ValueError("relationship commitment lineage is stale")
    if payload.stage_after not in RELATIONSHIP_COMMITMENT_STAGE_TRANSITIONS.get(
        stage, frozenset()
    ):
        raise ValueError("relationship commitment stage transition is not installed")

    updated = RelationshipStateProjection(
        relationship_id=payload.relationship_id,
        subject_ref=payload.subject_ref,
        entity_revision=revision + 1,
        stage=payload.stage_after,
        variables=variables,
        temperature=temperature,
        policy_version=payload.policy_version,
        policy_digest=payload.policy_digest,
        hysteresis=payload.hysteresis_after,
        commitment_refs=(*commitment_refs, commitment.commitment_id),
        last_adjusted_at=last_adjusted_at,
        origin=RelationshipStateOrigin(
            change_id=payload.change_id,
            transition_id=payload.transition_id,
            policy_refs=payload.policy_refs,
            accepted_event_ref=accepted_event_ref,
        ),
    )
    next_states = (*states, updated) if index is None else states[:index] + (updated,) + states[index + 1 :]
    return (*commitments, commitment), next_states


def adjust_relationship_slow_variables(
    states: tuple[RelationshipStateProjection, ...],
    history: tuple[RelationshipAdjustmentProjection, ...],
    signals: tuple[RelationshipSignalProjection, ...],
    payload: RelationshipSlowVariableAdjustedPayload,
    *,
    logical_time: datetime,
    accepted_event_ref: str | None = None,
    allow_legacy_relationship_policy_digest: bool = False,
) -> tuple[
    tuple[RelationshipStateProjection, ...],
    tuple[RelationshipAdjustmentProjection, ...],
]:
    _require_aware(logical_time)
    if payload.adjusted_at != logical_time:
        raise ValueError("relationship adjustment must use authoritative logical time")
    if payload.policy_version != "relationship-policy.1":
        raise ValueError("uninstalled relationship policy")
    if not allow_legacy_relationship_policy_digest and (
        payload.policy_digest != RELATIONSHIP_POLICY_DIGEST
    ):
        raise ValueError("relationship policy digest is not installed")
    if any(item.adjustment_id == payload.adjustment_id for item in history):
        raise ValueError("relationship adjustment already exists")
    resolved_signals = []
    for signal_ref in payload.signal_refs:
        matches = [item for item in signals if item.signal_id == signal_ref]
        if len(matches) != 1 or matches[0].subject_ref != payload.subject_ref:
            raise ValueError("relationship adjustment signal does not resolve")
        resolved_signals.append(matches[0])
    if payload.operation == "adjust":
        consumed_refs = {
            signal_ref
            for item in history
            if item.operation == "adjust"
            for signal_ref in item.signal_refs
        }
        if any(signal_ref in consumed_refs for signal_ref in payload.signal_refs):
            raise ValueError("relationship adjustment requires all signals to be unconsumed")
    if payload.contradiction_group_ref is not None and not any(
        item.contradiction_group_ref == payload.contradiction_group_ref
        for item in resolved_signals
    ):
        raise ValueError("relationship contradiction group has no supporting signal")
    _validate_adjustment_deltas(
        proposed_deltas=payload.proposed_deltas, accepted_deltas=payload.accepted_deltas
    )
    identity_matches = [
        (index, item)
        for index, item in enumerate(states)
        if item.relationship_id == payload.relationship_id
    ]
    subject_matches = [
        (index, item)
        for index, item in enumerate(states)
        if item.subject_ref == payload.subject_ref
    ]
    if len(identity_matches) > 1 or len(subject_matches) > 1:
        raise ValueError("duplicate relationship state authority")
    if identity_matches and identity_matches[0][1].subject_ref != payload.subject_ref:
        raise ValueError("relationship identity changed subject")
    if subject_matches and subject_matches[0][1].relationship_id != payload.relationship_id:
        raise ValueError("relationship subject changed authority identity")
    if subject_matches:
        index, current = subject_matches[0]
        revision = current.entity_revision
        if current.subject_ref != payload.subject_ref:
            raise ValueError("relationship identity changed subject")
        before = current.variables
        stage = current.stage
        hysteresis = current.hysteresis
        commitment_refs = current.commitment_refs
        temperature = current.temperature
        if not allow_legacy_relationship_policy_digest and (
            not relationship_state_policy_is_readable(current)
        ):
            raise ValueError("relationship state references an uninstalled policy")
        if current.last_adjusted_at is not None and logical_time < current.last_adjusted_at:
            raise ValueError("relationship adjustment precedes current state")
    else:
        index, revision, before, stage = None, 0, RelationshipVariablesProjection(), "stranger"
        hysteresis = RelationshipHysteresisProjection()
        commitment_refs = ()
        temperature = "ordinary"
    if revision != payload.expected_entity_revision or before != payload.variables_before:
        raise ValueError("stale relationship adjustment")
    if stage != payload.stage_before:
        raise ValueError("relationship stage before is stale")
    if hysteresis != payload.hysteresis_before:
        raise ValueError("relationship hysteresis before is stale")
    if hysteresis.candidate_since is not None and hysteresis.candidate_since > logical_time:
        raise ValueError("relationship hysteresis candidate starts in the future")
    if commitment_refs != payload.commitment_refs:
        raise ValueError("relationship commitment lineage is stale")
    calculated = _apply_deltas(before, payload.accepted_deltas)
    if calculated != payload.variables_after:
        raise ValueError("relationship variables do not match accepted deltas")
    target = None
    if payload.operation == "compensate":
        target = next(
            (item for item in history if item.adjustment_id == payload.compensates_adjustment_id),
            None,
        )
        if target is None or target.subject_ref != payload.subject_ref:
            raise ValueError("relationship compensation target does not resolve")
        subject_history = tuple(
            item for item in history if item.subject_ref == payload.subject_ref
        )
        if (
            not subject_history
            or subject_history[-1] != target
            or target.relationship_revision != revision
        ):
            raise ValueError("relationship compensation target must be the latest adjustment")
        if payload.signal_refs != target.signal_refs:
            raise ValueError("relationship compensation must preserve target signal lineage")
        if (
            stage != target.stage_after
            or hysteresis != target.hysteresis_after
            or calculated != target.variables_before
        ):
            raise ValueError("relationship compensation cannot restore target state")
        if any(item.compensates_adjustment_id == target.adjustment_id for item in history):
            raise ValueError("relationship adjustment is already compensated")
        for name in _VARIABLE_NAMES:
            effective_delta = getattr(target.variables_after, name) - getattr(
                target.variables_before, name
            )
            if getattr(payload.accepted_deltas, name) != -effective_delta:
                raise ValueError("relationship compensation must invert effective deltas")
    if target is not None:
        next_stage, next_hysteresis = target.stage_before, target.hysteresis_before
    else:
        next_stage, next_hysteresis = _derive_stage(stage, calculated, hysteresis, logical_time)
    if next_stage != payload.stage_after:
        if not allow_legacy_relationship_policy_digest:
            raise ValueError("relationship stage does not match hysteresis policy")
        # Replay of a historically accepted adjustment: the recorded stage is
        # authority.  Live commits still re-derive against the installed ladder.
        next_stage = payload.stage_after
    if next_hysteresis != payload.hysteresis_after:
        if not allow_legacy_relationship_policy_digest:
            raise ValueError("relationship hysteresis accumulator does not match policy")
        next_hysteresis = payload.hysteresis_after
    if calculated == before and next_stage == stage and next_hysteresis == hysteresis:
        raise ValueError("relationship adjustment is a semantic no-op")
    updated = RelationshipStateProjection(
        relationship_id=payload.relationship_id,
        subject_ref=payload.subject_ref,
        entity_revision=revision + 1,
        stage=next_stage,
        variables=calculated,
        temperature=temperature,
        policy_version=payload.policy_version,
        policy_digest=payload.policy_digest,
        hysteresis=next_hysteresis,
        commitment_refs=payload.commitment_refs,
        last_adjusted_at=logical_time,
        origin=(
            RelationshipStateOrigin(
                change_id=payload.change_id,
                transition_id=payload.transition_id,
                policy_refs=payload.policy_refs,
                accepted_event_ref=accepted_event_ref,
            )
            if accepted_event_ref is not None
            else None
        ),
    )
    adjustment = RelationshipAdjustmentProjection(
        adjustment_id=payload.adjustment_id,
        subject_ref=payload.subject_ref,
        relationship_revision=revision + 1,
        operation=payload.operation,
        signal_refs=payload.signal_refs,
        proposed_deltas=payload.proposed_deltas,
        accepted_deltas=payload.accepted_deltas,
        variables_before=payload.variables_before,
        variables_after=payload.variables_after,
        stage_before=payload.stage_before,
        stage_after=payload.stage_after,
        hysteresis_before=payload.hysteresis_before,
        hysteresis_after=payload.hysteresis_after,
        commitment_refs=payload.commitment_refs,
        confidence_bp=payload.confidence_bp,
        persistence=payload.persistence,
        contradiction_group_ref=payload.contradiction_group_ref,
        rationale_code=payload.rationale_code,
        policy_version=payload.policy_version,
        policy_digest=payload.policy_digest,
        adjusted_at=payload.adjusted_at,
        compensates_adjustment_id=payload.compensates_adjustment_id,
    )
    updated_states = (*states, updated) if index is None else (*states[:index], updated, *states[index + 1 :])
    return updated_states, (*history, adjustment)


def change_boundary(
    boundaries: tuple[BoundaryProjection, ...],
    payload: BoundaryChangedPayload,
    *,
    logical_time: datetime,
) -> tuple[BoundaryProjection, ...]:
    _require_aware(logical_time)
    candidate = payload.boundary
    if candidate.policy_version != "boundary-policy.1":
        raise ValueError("boundary references an uninstalled policy")
    if candidate.updated_at != logical_time:
        raise ValueError("boundary transition must use authoritative logical time")
    matches = [(index, item) for index, item in enumerate(boundaries) if item.boundary_id == candidate.boundary_id]
    if payload.operation == "open":
        if matches:
            raise ValueError("boundary already exists")
        if any(
            item.status == "active"
            and item.subject_ref == candidate.subject_ref
            and item.scope_ref == candidate.scope_ref
            for item in boundaries
        ):
            raise ValueError("active boundary authority already exists for subject scope")
        if candidate.opened_at != logical_time:
            raise ValueError("boundary open must use authoritative logical time")
        return (*boundaries, candidate)
    if len(matches) != 1:
        raise ValueError("boundary transition target does not resolve")
    index, current = matches[0]
    if current.entity_revision != payload.expected_entity_revision:
        raise ValueError("stale boundary transition")
    if current.status != "active":
        raise ValueError("closed boundary cannot transition")
    if (
        candidate.entity_revision != current.entity_revision + 1
        or candidate.subject_ref != current.subject_ref
        or candidate.scope_ref != current.scope_ref
        or candidate.opened_at != current.opened_at
    ):
        raise ValueError("boundary transition changed immutable identity")
    return (*boundaries[:index], candidate, *boundaries[index + 1 :])


def _apply_deltas(
    before: RelationshipVariablesProjection,
    deltas: RelationshipVariableDeltas,
) -> RelationshipVariablesProjection:
    return RelationshipVariablesProjection(
        **{
            name: min(10_000, max(0, getattr(before, name) + getattr(deltas, name)))
            for name in _VARIABLE_NAMES
        }
    )


def _validate_adjustment_deltas(
    *,
    proposed_deltas: RelationshipVariableDeltas,
    accepted_deltas: RelationshipVariableDeltas,
) -> None:
    for name in _VARIABLE_NAMES:
        proposed = getattr(proposed_deltas, name)
        accepted = getattr(accepted_deltas, name)
        if abs(accepted) > _POLICY["delta_cap_bp"]:
            raise ValueError("relationship delta exceeds policy cap")
        if accepted and (
            not proposed
            or (accepted > 0) != (proposed > 0)
            or abs(accepted) > abs(proposed)
        ):
            raise ValueError("accepted relationship delta does not refine proposal")


def _stage_ladder_score(
    variables: RelationshipVariablesProjection,
    *,
    use_six_axis: bool,
) -> int:
    names = _VARIABLE_NAMES if use_six_axis else _PRIMARY_LADDER_AXES
    return sum(getattr(variables, name) for name in names) // len(names)


def _derive_stage(
    current: str,
    variables: RelationshipVariablesProjection,
    hysteresis: RelationshipHysteresisProjection,
    logical_time: datetime,
) -> tuple[str, RelationshipHysteresisProjection]:
    if current in COMMITMENT_ONLY_RELATIONSHIP_STAGES:
        # Slow variables keep moving underneath her, but they cannot talk her
        # out of what she declared; only another commitment changes this stage.
        return current, RelationshipHysteresisProjection()
    if current not in _STAGES:
        raise ValueError("relationship stage requires an installed commitment protocol")
    index = _STAGES.index(current)
    candidate = None
    direction = None
    if index < len(_STAGES) - 1:
        next_stage = _STAGES[index + 1]
        promote_score = _stage_ladder_score(
            variables,
            use_six_axis=next_stage == "close_friend",
        )
        if promote_score >= _POLICY["enter_bp"][next_stage]:
            candidate, direction = next_stage, "promote"
    if candidate is None and index > 0:
        demote_score = _stage_ladder_score(
            variables,
            use_six_axis=current == "close_friend",
        )
        if demote_score < _POLICY["exit_bp"][current]:
            candidate, direction = _STAGES[index - 1], "demote"
    if candidate is None:
        return current, RelationshipHysteresisProjection()
    if hysteresis.candidate_stage == candidate and hysteresis.direction == direction:
        assert hysteresis.candidate_since is not None
        next_hysteresis = hysteresis.model_copy(
            update={"confirming_adjustment_count": hysteresis.confirming_adjustment_count + 1}
        )
    else:
        next_hysteresis = RelationshipHysteresisProjection(
            candidate_stage=candidate,
            direction=direction,
            candidate_since=logical_time,
            confirming_adjustment_count=1,
        )
    assert next_hysteresis.candidate_since is not None
    if (
        next_hysteresis.confirming_adjustment_count >= _POLICY["required_confirmations"]
        and logical_time - next_hysteresis.candidate_since
        >= timedelta(seconds=_POLICY["minimum_dwell_seconds"])
    ):
        return candidate, RelationshipHysteresisProjection()
    return current, next_hysteresis


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("relationship logical time must be timezone-aware")
