from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from companion_daemon.world_v2 import relationship_reducers
from companion_daemon.world_v2.relationship_events import (
    BoundaryChangedPayload,
    RelationshipSignalAcceptedPayload,
    RelationshipSlowVariableAdjustedPayload,
    relationship_mutation_hash,
)
from companion_daemon.world_v2.relationship_reducers import (
    COMMITMENT_ONLY_RELATIONSHIP_STAGES,
    RELATIONSHIP_COMMITMENT_STAGE_TRANSITIONS,
    RELATIONSHIP_POLICY_DIGEST,
    RETIRED_RELATIONSHIP_POLICY_DIGESTS,
    accept_relationship_signal,
    adjust_relationship_slow_variables,
    change_boundary,
    preview_relationship_slow_variable_adjustment,
    relationship_state_policy_is_readable,
)
from companion_daemon.world_v2.schemas import (
    BoundaryProjection,
    EvidenceRef,
    RelationshipAdjustmentProjection,
    RelationshipBoundaryOrigin,
    RelationshipSignalOrigin,
    RelationshipSignalProjection,
    RelationshipHysteresisProjection,
    RelationshipStateProjection,
    RelationshipVariableDeltas,
    RelationshipVariablesProjection,
    relationship_signal_fingerprint,
)


NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
NAIVE_NOW = datetime(2026, 7, 14, 12, 0)
# Exact stamps carried on production epoch2.  Genesis brought 64d8b7ff from
# epoch 1; the 2026-08-17 adjustment restamped to 13bfa71d; H23 restored
# close_friend enter/exit to 7000/6200 and is the installed digest again.
# Live writes must still accept every retired stamp as readable state.
PRODUCTION_EPOCH2_GENESIS_DIGEST = (
    "64d8b7ffc6f38f79d31bb8a83212c5668ff908ab3f6d7c924dd75ad71fb94e95"
)
PRODUCTION_EPOCH2_POST_H22_DIGEST = (
    "13bfa71dd9f8377b968714eb3d4f9a927e587832c92d2381c6ecc772071deede"
)
PRODUCTION_H23_DIGEST = (
    "2ec7c0874f219a2fa2c7800d3679c393705905130a9007b2833148cb1df9b8de"
)
# Pre-2026-08-20 mean-of-six ordinary ladder at 2000/4500; close_friend 7000 unchanged.
PRODUCTION_H23_MEAN_SIX_DIGEST = PRODUCTION_H23_DIGEST
# Same-day 2026-08-18 close_friend 6000/5400 lowering; restored to 7000/6200.
LOWERED_CLOSE_FRIEND_6000_DIGEST = (
    "374b96bb36fac6dfb607622075e40f1ae7fbb4dc30c802f6267727805f4fa912"
)


def evidence(ref_id: str = "operator:relationship:1") -> EvidenceRef:
    return EvidenceRef(
        ref_id=ref_id,
        evidence_type="operator_observation",
        claim_purpose="private_hypothesis",
        immutable_hash="1" * 64,
    )


def authorized(model_type, **values):
    raw = {
        "change_id": values.pop("change_id"),
        "transition_id": values.pop("transition_id"),
        "expected_entity_revision": values.pop("expected_entity_revision"),
        "evidence_refs": tuple(values.pop("evidence_refs", (evidence(),))),
        "policy_refs": tuple(values.pop("policy_refs")),
        "acceptance_id": values.pop("acceptance_id", "acceptance:relationship:1"),
        "proposal_id": values.pop("proposal_id", "proposal:relationship:1"),
        "evaluated_world_revision": values.pop("evaluated_world_revision", 3),
        "accepted_change_hash": "0" * 64,
        **values,
    }
    if model_type is RelationshipSlowVariableAdjustedPayload:
        raw.setdefault("compensates_adjustment_id", None)
        raw.setdefault("commitment_refs", ())
    raw["accepted_change_hash"] = relationship_mutation_hash(raw)
    return model_type.model_validate(raw)


def signal(signal_id: str, *, code: str, contradiction_group_ref: str) -> RelationshipSignalProjection:
    refs = (evidence(f"operator:{signal_id}"),)
    policy_refs = ("policy:relationship-signal-v1",)
    return RelationshipSignalProjection(
        signal_id=signal_id,
        semantic_fingerprint=relationship_signal_fingerprint(
            subject_ref="user:geoff",
            signal_code=code,
            evidence_refs=refs,
            policy_refs=policy_refs,
        ),
        entity_revision=1,
        subject_ref="user:geoff",
        signal_code=code,
        confidence_bp=8_000,
        persistence="durable",
        contradiction_group_ref=contradiction_group_ref,
        rationale_code="settled_interaction_signal",
        evidence_refs=refs,
        origin=RelationshipSignalOrigin(
            change_id=f"change:{signal_id}",
            transition_id=f"transition:{signal_id}",
            policy_refs=policy_refs,
            accepted_event_ref=f"event:{signal_id}",
        ),
        accepted_at=NOW,
    )


def adjustment_payload(
    source: RelationshipSignalProjection,
    *,
    adjustment_id: str,
    expected_revision: int,
    before: RelationshipVariablesProjection,
    after: RelationshipVariablesProjection,
    accepted: RelationshipVariableDeltas,
    stage_before: str = "stranger",
    stage_after: str = "stranger",
    hysteresis_before: RelationshipHysteresisProjection | None = None,
    hysteresis_after: RelationshipHysteresisProjection | None = None,
    adjusted_at: datetime = NOW,
):
    return authorized(
        RelationshipSlowVariableAdjustedPayload,
        change_id=f"change:{adjustment_id}",
        transition_id=f"transition:{adjustment_id}",
        expected_entity_revision=expected_revision,
        policy_refs=("policy:relationship-v1",),
        acceptance_id=f"acceptance:{adjustment_id}",
        proposal_id=f"proposal:{adjustment_id}",
        relationship_id="relationship:user:geoff",
        subject_ref="user:geoff",
        adjustment_id=adjustment_id,
        operation="adjust",
        signal_refs=(source.signal_id,),
        proposed_deltas=accepted,
        accepted_deltas=accepted,
        variables_before=before,
        variables_after=after,
        stage_before=stage_before,
        stage_after=stage_after,
        hysteresis_before=hysteresis_before or RelationshipHysteresisProjection(),
        hysteresis_after=hysteresis_after or RelationshipHysteresisProjection(),
        confidence_bp=8_000,
        persistence="durable",
        contradiction_group_ref=source.contradiction_group_ref,
        rationale_code=source.rationale_code,
        policy_version="relationship-policy.1",
        policy_digest=RELATIONSHIP_POLICY_DIGEST,
        adjusted_at=adjusted_at,
    )


def test_state_carried_across_an_epoch_can_still_be_adjusted() -> None:
    """An epoch genesis carries state, not events, so its stamp can be retired.

    Production held closeness 180 / trust 100 from epoch 1 under a numerically
    identical policy, and every live adjustment fail-closed on the old digest.
    """

    retired = next(iter(RETIRED_RELATIONSHIP_POLICY_DIGESTS))
    before = RelationshipVariablesProjection(trust_bp=100, closeness_bp=180)
    after = before.model_copy(update={"trust_bp": 350})
    source = signal(
        "signal:carried-across-epoch",
        code="carried",
        contradiction_group_ref="group:carried",
    )
    carried = RelationshipStateProjection(
        relationship_id="relationship:user:geoff",
        subject_ref="user:geoff",
        entity_revision=1,
        variables=before,
        policy_digest=retired,
    )

    preview = preview_relationship_slow_variable_adjustment(
        states=(carried,),
        history=(),
        signals=(source,),
        subject_ref="user:geoff",
        signal_refs=(source.signal_id,),
        proposed_deltas=RelationshipVariableDeltas(trust_bp=250),
        accepted_deltas=RelationshipVariableDeltas(trust_bp=250),
        logical_time=NOW,
    )
    assert preview.variables_after.trust_bp == 350
    # The mutation being written re-stamps the state onto the installed policy.
    assert preview.policy_digest == RELATIONSHIP_POLICY_DIGEST

    states, _history = adjust_relationship_slow_variables(
        (carried,),
        (),
        (source,),
        adjustment_payload(
            source,
            adjustment_id="adjustment:carried-across-epoch",
            expected_revision=1,
            before=before,
            after=after,
            accepted=RelationshipVariableDeltas(trust_bp=250),
        ),
        logical_time=NOW,
    )
    assert states[0].variables.trust_bp == 350
    assert states[0].policy_digest == RELATIONSHIP_POLICY_DIGEST


def test_a_foreign_policy_stamp_is_still_refused() -> None:
    carried = RelationshipStateProjection(
        relationship_id="relationship:user:geoff",
        subject_ref="user:geoff",
        entity_revision=1,
        variables=RelationshipVariablesProjection(trust_bp=100),
        policy_digest="0" * 64,
    )
    source = signal(
        "signal:foreign-policy",
        code="foreign",
        contradiction_group_ref="group:foreign",
    )
    with pytest.raises(ValueError, match="uninstalled policy"):
        preview_relationship_slow_variable_adjustment(
            states=(carried,),
            history=(),
            signals=(source,),
            subject_ref="user:geoff",
            signal_refs=(source.signal_id,),
            proposed_deltas=RelationshipVariableDeltas(trust_bp=250),
            accepted_deltas=RelationshipVariableDeltas(trust_bp=250),
            logical_time=NOW,
        )


def test_production_epoch2_policy_stamps_are_readable_as_models_and_mappings() -> None:
    assert PRODUCTION_EPOCH2_GENESIS_DIGEST in RETIRED_RELATIONSHIP_POLICY_DIGESTS
    assert PRODUCTION_EPOCH2_POST_H22_DIGEST in RETIRED_RELATIONSHIP_POLICY_DIGESTS
    assert LOWERED_CLOSE_FRIEND_6000_DIGEST in RETIRED_RELATIONSHIP_POLICY_DIGESTS
    assert PRODUCTION_H23_MEAN_SIX_DIGEST in RETIRED_RELATIONSHIP_POLICY_DIGESTS
    assert PRODUCTION_H23_MEAN_SIX_DIGEST != RELATIONSHIP_POLICY_DIGEST
    for digest in (
        PRODUCTION_EPOCH2_GENESIS_DIGEST,
        PRODUCTION_EPOCH2_POST_H22_DIGEST,
        LOWERED_CLOSE_FRIEND_6000_DIGEST,
        PRODUCTION_H23_MEAN_SIX_DIGEST,
        RELATIONSHIP_POLICY_DIGEST,
    ):
        state = RelationshipStateProjection(
            relationship_id="relationship:user:geoff",
            subject_ref="user:geoff",
            entity_revision=3,
            variables=RelationshipVariablesProjection(
                trust_bp=120, closeness_bp=200, respect_bp=80, mutuality_bp=110
            ),
            policy_digest=digest,
        )
        dumped = state.model_dump(mode="json")
        assert relationship_state_policy_is_readable(state)
        assert relationship_state_policy_is_readable(dumped)
    assert not relationship_state_policy_is_readable({"policy_version": "relationship-policy.1"})
    assert not relationship_state_policy_is_readable(
        {"policy_version": "relationship-policy.1", "policy_digest": "0" * 64}
    )


def test_production_shaped_state_can_be_previewed_and_restamped() -> None:
    """The 2026-08-17 production head must not fail-closed on its retired stamp."""

    before = RelationshipVariablesProjection(
        trust_bp=120,
        closeness_bp=200,
        respect_bp=80,
        reliability_bp=0,
        mutuality_bp=110,
        repair_confidence_bp=0,
    )
    source = signal(
        "signal:production-shaped",
        code="production_shaped",
        contradiction_group_ref="group:production-shaped",
    )
    carried = RelationshipStateProjection(
        relationship_id="relationship:primary:45c9ebfa1a5c474219e868668aff3a6700e2f18fb49e7792ea67dc669f819181",
        subject_ref="user:geoff",
        entity_revision=3,
        variables=before,
        policy_digest=PRODUCTION_EPOCH2_POST_H22_DIGEST,
    )
    preview = preview_relationship_slow_variable_adjustment(
        states=(carried,),
        history=(),
        signals=(source,),
        subject_ref="user:geoff",
        signal_refs=(source.signal_id,),
        proposed_deltas=RelationshipVariableDeltas(trust_bp=20, closeness_bp=20),
        accepted_deltas=RelationshipVariableDeltas(trust_bp=20, closeness_bp=20),
        logical_time=NOW,
    )
    assert preview.stage_before == "stranger"
    assert preview.stage_after == "stranger"
    assert preview.policy_digest == RELATIONSHIP_POLICY_DIGEST
    assert preview.expected_entity_revision == 3


def test_four_everyday_axes_cannot_open_close_friend_hysteresis() -> None:
    """Four-axis saturation is 6666; close_friend enter is mean-of-six 7000.

    reliability and repair have the same write path as trust/closeness.  If
    they stay at 0, the slow ladder honestly does not open.  The main road
    is her ``we_are`` declaration, not a lowered threshold.
    """

    variables = RelationshipVariablesProjection(
        trust_bp=10_000,
        closeness_bp=10_000,
        respect_bp=10_000,
        reliability_bp=0,
        mutuality_bp=10_000,
        repair_confidence_bp=0,
    )
    stage, hysteresis = relationship_reducers._derive_stage(
        "friend",
        variables,
        RelationshipHysteresisProjection(),
        NOW,
    )
    assert stage == "friend"
    assert hysteresis.candidate_stage is None


def test_relationship_policy_digest_binds_commitment_transition_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_digest_without_commitment_graph = (
        "64d8b7ffc6f38f79d31bb8a83212c5668ff908ab3f6d7c924dd75ad71fb94e95"
    )
    changed_transitions = dict(RELATIONSHIP_COMMITMENT_STAGE_TRANSITIONS)
    changed_transitions["friend"] = frozenset({"acquaintance", "close_friend"})
    monkeypatch.setattr(
        relationship_reducers,
        "RELATIONSHIP_COMMITMENT_STAGE_TRANSITIONS",
        changed_transitions,
    )

    assert relationship_reducers.relationship_policy_digest() != (
        RELATIONSHIP_POLICY_DIGEST
    )
    assert RELATIONSHIP_POLICY_DIGEST != legacy_digest_without_commitment_graph

    source = signal(
        "signal:legacy-policy",
        code="legacy_policy",
        contradiction_group_ref="group:legacy-policy",
    )
    before = RelationshipVariablesProjection()
    after = RelationshipVariablesProjection(trust_bp=1)
    legacy_payload = adjustment_payload(
        source,
        adjustment_id="adjustment:legacy-policy",
        expected_revision=0,
        before=before,
        after=after,
        accepted=RelationshipVariableDeltas(trust_bp=1),
    ).model_copy(update={"policy_digest": legacy_digest_without_commitment_graph})

    with pytest.raises(ValueError, match="policy digest is not installed"):
        adjust_relationship_slow_variables(
            (),
            (),
            (source,),
            legacy_payload,
            logical_time=NOW,
        )


def test_replay_keeps_recorded_hysteresis_when_ordinary_ladder_changed() -> None:
    """Pre-2026-08-20 mean-of-six 2000 would not open acquaintance hysteresis here.

    Four-axis 500 now would.  Historical project_at must keep the recorded
    accumulator; a live commit with the installed digest must still refuse.
    """
    source = signal(
        "signal:legacy-hysteresis",
        code="care_observed",
        contradiction_group_ref="group:legacy-hysteresis",
    )
    before = RelationshipVariablesProjection()
    after = RelationshipVariablesProjection(
        trust_bp=500,
        closeness_bp=500,
        mutuality_bp=500,
        respect_bp=500,
    )
    accepted = RelationshipVariableDeltas(
        trust_bp=500,
        closeness_bp=500,
        mutuality_bp=500,
        respect_bp=500,
    )
    recorded = adjustment_payload(
        source,
        adjustment_id="adjustment:legacy-hysteresis",
        expected_revision=0,
        before=before,
        after=after,
        accepted=accepted,
    ).model_copy(update={"policy_digest": PRODUCTION_H23_MEAN_SIX_DIGEST})
    live_mismatch = recorded.model_copy(update={"policy_digest": RELATIONSHIP_POLICY_DIGEST})

    with pytest.raises(ValueError, match="hysteresis accumulator does not match policy"):
        adjust_relationship_slow_variables(
            (),
            (),
            (source,),
            live_mismatch,
            logical_time=NOW,
        )

    states, history = adjust_relationship_slow_variables(
        (),
        (),
        (source,),
        recorded,
        logical_time=NOW,
        allow_legacy_relationship_policy_digest=True,
    )
    assert states[0].stage == "stranger"
    assert states[0].hysteresis == RelationshipHysteresisProjection()
    assert history[0].hysteresis_after == RelationshipHysteresisProjection()


def test_relationship_signals_accumulate_inside_a_contradiction_group() -> None:
    first = signal("signal:care", code="care_observed", contradiction_group_ref="group:care")
    second = signal(
        "signal:withdrawal",
        code="withdrawal_observed",
        contradiction_group_ref="group:care",
    )
    first_payload = authorized(
        RelationshipSignalAcceptedPayload,
        change_id=first.origin.change_id,
        transition_id=first.origin.transition_id,
        expected_entity_revision=0,
        evidence_refs=first.evidence_refs,
        policy_refs=first.origin.policy_refs,
        signal=first,
    )
    second_payload = authorized(
        RelationshipSignalAcceptedPayload,
        change_id=second.origin.change_id,
        transition_id=second.origin.transition_id,
        expected_entity_revision=0,
        evidence_refs=second.evidence_refs,
        policy_refs=second.origin.policy_refs,
        signal=second,
    )

    accepted = accept_relationship_signal((), first_payload, logical_time=NOW)
    accepted = accept_relationship_signal(accepted, second_payload, logical_time=NOW)

    assert accepted == (first, second)
    assert {item.contradiction_group_ref for item in accepted} == {"group:care"}


def test_adjustment_clips_only_at_acceptance_and_stage_moves_one_hysteresis_step() -> None:
    source = signal("signal:reliable", code="reliability_observed", contradiction_group_ref="group:1")
    proposed = RelationshipVariableDeltas(
        trust_bp=900,
        closeness_bp=900,
        respect_bp=900,
        reliability_bp=900,
        mutuality_bp=900,
        repair_confidence_bp=900,
    )
    accepted = RelationshipVariableDeltas(
        trust_bp=500,
        closeness_bp=500,
        respect_bp=500,
        reliability_bp=500,
        mutuality_bp=500,
        repair_confidence_bp=500,
    )
    before = RelationshipVariablesProjection(
        trust_bp=1_900,
        closeness_bp=1_900,
        respect_bp=1_900,
        reliability_bp=1_900,
        mutuality_bp=1_900,
        repair_confidence_bp=1_900,
    )
    after = RelationshipVariablesProjection(
        trust_bp=2_400,
        closeness_bp=2_400,
        respect_bp=2_400,
        reliability_bp=2_400,
        mutuality_bp=2_400,
        repair_confidence_bp=2_400,
    )
    payload = authorized(
        RelationshipSlowVariableAdjustedPayload,
        change_id="change:relationship:1",
        transition_id="transition:relationship:1",
        expected_entity_revision=1,
        policy_refs=("policy:relationship-v1",),
        relationship_id="relationship:user:geoff",
        subject_ref="user:geoff",
        adjustment_id="relationship-adjustment:1",
        operation="adjust",
        signal_refs=(source.signal_id,),
        proposed_deltas=proposed,
        accepted_deltas=accepted,
        variables_before=before,
        variables_after=after,
        stage_before="stranger",
        stage_after="stranger",
        hysteresis_before=RelationshipHysteresisProjection(),
        hysteresis_after=RelationshipHysteresisProjection(
            candidate_stage="acquaintance",
            direction="promote",
            candidate_since=NOW,
            confirming_adjustment_count=1,
        ),
        confidence_bp=8_000,
        persistence="durable",
        contradiction_group_ref="group:1",
        rationale_code="reliability_observed",
        policy_version="relationship-policy.1",
        policy_digest=RELATIONSHIP_POLICY_DIGEST,
        adjusted_at=NOW,
    )

    existing = RelationshipStateProjection(
        relationship_id="relationship:user:geoff",
        subject_ref="user:geoff",
        entity_revision=1,
        variables=before,
        policy_digest=RELATIONSHIP_POLICY_DIGEST,
    )
    states, history = adjust_relationship_slow_variables(
        (existing,), (), (source,), payload, logical_time=NOW
    )

    assert states[0].entity_revision == 2
    assert states[0].variables == after
    assert states[0].stage == "stranger"
    assert states[0].hysteresis.candidate_stage == "acquaintance"
    assert history[0].proposed_deltas == proposed
    assert history[0].accepted_deltas == accepted

    confirming_source = signal(
        "signal:reliable-again",
        code="reliability_observed_again",
        contradiction_group_ref="group:1",
    )
    confirming_payload = authorized(
        RelationshipSlowVariableAdjustedPayload,
        change_id="change:relationship:2",
        transition_id="transition:relationship:2",
        expected_entity_revision=2,
        policy_refs=("policy:relationship-v1",),
        acceptance_id="acceptance:relationship:2",
        proposal_id="proposal:relationship:2",
        relationship_id="relationship:user:geoff",
        subject_ref="user:geoff",
        adjustment_id="relationship-adjustment:2",
        operation="adjust",
        signal_refs=(confirming_source.signal_id,),
        proposed_deltas=RelationshipVariableDeltas(
            trust_bp=1,
            closeness_bp=1,
            respect_bp=1,
            reliability_bp=1,
            mutuality_bp=1,
            repair_confidence_bp=1,
        ),
        accepted_deltas=RelationshipVariableDeltas(
            trust_bp=1,
            closeness_bp=1,
            respect_bp=1,
            reliability_bp=1,
            mutuality_bp=1,
            repair_confidence_bp=1,
        ),
        variables_before=after,
        variables_after=RelationshipVariablesProjection(
            trust_bp=2_401,
            closeness_bp=2_401,
            respect_bp=2_401,
            reliability_bp=2_401,
            mutuality_bp=2_401,
            repair_confidence_bp=2_401,
        ),
        stage_before="stranger",
        stage_after="acquaintance",
        hysteresis_before=states[0].hysteresis,
        hysteresis_after=RelationshipHysteresisProjection(),
        confidence_bp=8_000,
        persistence="durable",
        contradiction_group_ref="group:1",
        rationale_code="reliability_observed_again",
        policy_version="relationship-policy.1",
        policy_digest=RELATIONSHIP_POLICY_DIGEST,
        adjusted_at=NOW + timedelta(days=1),
    )
    promoted_states, promoted_history = adjust_relationship_slow_variables(
        states,
        history,
        (source, confirming_source),
        confirming_payload,
        logical_time=NOW + timedelta(days=1),
    )
    assert promoted_states[0].stage == "acquaintance"
    assert promoted_states[0].hysteresis == RelationshipHysteresisProjection()
    assert len(promoted_history) == 2

    with pytest.raises(ValueError, match="already exists"):
        adjust_relationship_slow_variables(states, history, (source,), payload, logical_time=NOW)


def test_compensation_is_an_inverse_event_and_preserves_signal_history() -> None:
    source = signal("signal:repair", code="repair_observed", contradiction_group_ref="group:repair")
    before = RelationshipVariablesProjection()
    after = RelationshipVariablesProjection(trust_bp=300)
    original_payload = authorized(
        RelationshipSlowVariableAdjustedPayload,
        change_id="change:relationship:original",
        transition_id="transition:relationship:original",
        expected_entity_revision=0,
        policy_refs=("policy:relationship-v1",),
        relationship_id="relationship:user:geoff",
        subject_ref="user:geoff",
        adjustment_id="relationship-adjustment:original",
        operation="adjust",
        signal_refs=(source.signal_id,),
        proposed_deltas=RelationshipVariableDeltas(trust_bp=400),
        accepted_deltas=RelationshipVariableDeltas(trust_bp=300),
        variables_before=before,
        variables_after=after,
        stage_before="stranger",
        stage_after="stranger",
        hysteresis_before=RelationshipHysteresisProjection(),
        hysteresis_after=RelationshipHysteresisProjection(),
        confidence_bp=7_000,
        persistence="durable",
        contradiction_group_ref="group:repair",
        rationale_code="repair_observed",
        policy_version="relationship-policy.1",
        policy_digest=RELATIONSHIP_POLICY_DIGEST,
        adjusted_at=NOW,
    )
    states, history = adjust_relationship_slow_variables(
        (), (), (source,), original_payload, logical_time=NOW
    )
    compensation = authorized(
        RelationshipSlowVariableAdjustedPayload,
        change_id="change:relationship:compensation",
        transition_id="transition:relationship:compensation",
        expected_entity_revision=1,
        policy_refs=("policy:relationship-v1",),
        relationship_id="relationship:user:geoff",
        subject_ref="user:geoff",
        adjustment_id="relationship-adjustment:compensation",
        operation="compensate",
        signal_refs=(source.signal_id,),
        proposed_deltas=RelationshipVariableDeltas(trust_bp=-300),
        accepted_deltas=RelationshipVariableDeltas(trust_bp=-300),
        variables_before=after,
        variables_after=before,
        stage_before="stranger",
        stage_after="stranger",
        hysteresis_before=RelationshipHysteresisProjection(),
        hysteresis_after=RelationshipHysteresisProjection(),
        confidence_bp=10_000,
        persistence="durable",
        contradiction_group_ref="group:repair",
        rationale_code="correction",
        policy_version="relationship-policy.1",
        policy_digest=RELATIONSHIP_POLICY_DIGEST,
        adjusted_at=NOW,
        compensates_adjustment_id="relationship-adjustment:original",
    )

    compensated_states, compensated_history = adjust_relationship_slow_variables(
        states, history, (source,), compensation, logical_time=NOW
    )

    assert compensated_states[0].variables.trust_bp == 0
    assert [item.adjustment_id for item in compensated_history] == [
        "relationship-adjustment:original",
        "relationship-adjustment:compensation",
    ]
    assert source.contradiction_group_ref == "group:repair"


def test_compensation_inverts_effective_clamped_delta() -> None:
    source = signal("signal:clamped-repair", code="repair", contradiction_group_ref="group:clamp")
    before = RelationshipVariablesProjection(trust_bp=9_900)
    after = RelationshipVariablesProjection(trust_bp=10_000)
    stage_after, hysteresis_after = relationship_reducers._derive_stage(
        "stranger",
        after,
        RelationshipHysteresisProjection(),
        NOW,
    )
    original = adjustment_payload(
        source,
        adjustment_id="adjustment:clamped-original",
        expected_revision=1,
        before=before,
        after=after,
        accepted=RelationshipVariableDeltas(trust_bp=300),
        stage_after=stage_after,
        hysteresis_after=hysteresis_after,
    )
    existing = RelationshipStateProjection(
        relationship_id="relationship:user:geoff",
        subject_ref="user:geoff",
        entity_revision=1,
        variables=before,
        policy_digest=RELATIONSHIP_POLICY_DIGEST,
    )
    states, history = adjust_relationship_slow_variables(
        (existing,), (), (source,), original, logical_time=NOW
    )
    compensation = authorized(
        RelationshipSlowVariableAdjustedPayload,
        change_id="change:clamped-compensation",
        transition_id="transition:clamped-compensation",
        expected_entity_revision=2,
        policy_refs=("policy:relationship-v1",),
        relationship_id="relationship:user:geoff",
        subject_ref="user:geoff",
        adjustment_id="adjustment:clamped-compensation",
        operation="compensate",
        signal_refs=(source.signal_id,),
        proposed_deltas=RelationshipVariableDeltas(trust_bp=-100),
        accepted_deltas=RelationshipVariableDeltas(trust_bp=-100),
        variables_before=after,
        variables_after=before,
        stage_before=stage_after,
        stage_after="stranger",
        hysteresis_before=hysteresis_after,
        hysteresis_after=RelationshipHysteresisProjection(),
        confidence_bp=10_000,
        persistence="durable",
        contradiction_group_ref="group:clamp",
        rationale_code="correction",
        policy_version="relationship-policy.1",
        policy_digest=RELATIONSHIP_POLICY_DIGEST,
        adjusted_at=NOW,
        compensates_adjustment_id="adjustment:clamped-original",
    )
    compensated, _ = adjust_relationship_slow_variables(
        states, history, (source,), compensation, logical_time=NOW
    )
    assert compensated[0].variables == before


def test_adjustment_rejects_stale_revision_reused_signal_and_clamp_noop() -> None:
    source = signal("signal:once", code="once", contradiction_group_ref="group:once")
    zero = RelationshipVariablesProjection()
    one_hundred = RelationshipVariablesProjection(trust_bp=100)
    first = adjustment_payload(
        source,
        adjustment_id="adjustment:once",
        expected_revision=0,
        before=zero,
        after=one_hundred,
        accepted=RelationshipVariableDeltas(trust_bp=100),
    )
    states, history = adjust_relationship_slow_variables(
        (), (), (source,), first, logical_time=NOW
    )

    stale_source = signal("signal:stale", code="stale", contradiction_group_ref="group:once")
    stale = adjustment_payload(
        stale_source,
        adjustment_id="adjustment:stale",
        expected_revision=0,
        before=one_hundred,
        after=RelationshipVariablesProjection(trust_bp=200),
        accepted=RelationshipVariableDeltas(trust_bp=100),
    )
    with pytest.raises(ValueError, match="stale"):
        adjust_relationship_slow_variables(
            states, history, (source, stale_source), stale, logical_time=NOW
        )

    reused = adjustment_payload(
        source,
        adjustment_id="adjustment:reused",
        expected_revision=1,
        before=one_hundred,
        after=RelationshipVariablesProjection(trust_bp=200),
        accepted=RelationshipVariableDeltas(trust_bp=100),
    )
    with pytest.raises(ValueError, match="signals.*unconsumed"):
        adjust_relationship_slow_variables(states, history, (source,), reused, logical_time=NOW)

    fresh = signal("signal:fresh", code="fresh", contradiction_group_ref="group:once")
    mixed = adjustment_payload(
        fresh,
        adjustment_id="adjustment:mixed-lineage",
        expected_revision=1,
        before=one_hundred,
        after=RelationshipVariablesProjection(trust_bp=200),
        accepted=RelationshipVariableDeltas(trust_bp=100),
    ).model_copy(update={"signal_refs": (source.signal_id, fresh.signal_id)})
    with pytest.raises(ValueError, match="all signals.*unconsumed"):
        adjust_relationship_slow_variables(
            states, history, (source, fresh), mixed, logical_time=NOW
        )

    capped_source = signal("signal:capped", code="capped", contradiction_group_ref="group:capped")
    capped = RelationshipVariablesProjection(trust_bp=10_000)
    capped_state = RelationshipStateProjection(
        relationship_id="relationship:user:geoff",
        subject_ref="user:geoff",
        entity_revision=1,
        variables=capped,
        policy_digest=RELATIONSHIP_POLICY_DIGEST,
    )
    noop = adjustment_payload(
        capped_source,
        adjustment_id="adjustment:clamp-noop",
        expected_revision=1,
        before=capped,
        after=capped,
        accepted=RelationshipVariableDeltas(trust_bp=300),
    )
    with pytest.raises(ValueError, match="semantic no-op|hysteresis accumulator"):
        adjust_relationship_slow_variables(
            (capped_state,), (), (capped_source,), noop, logical_time=NOW
        )


def test_hysteresis_requires_distinct_confirmations_and_dwell_and_never_skips_stage() -> None:
    first_signal = signal("signal:h1", code="h1", contradiction_group_ref="group:h")
    second_signal = signal("signal:h2", code="h2", contradiction_group_ref="group:h")
    third_signal = signal("signal:h3", code="h3", contradiction_group_ref="group:h")
    start = RelationshipVariablesProjection(
        trust_bp=1_900,
        closeness_bp=1_900,
        respect_bp=1_900,
        reliability_bp=1_900,
        mutuality_bp=1_900,
        repair_confidence_bp=1_900,
    )
    high = RelationshipVariablesProjection(
        trust_bp=8_000,
        closeness_bp=8_000,
        respect_bp=8_000,
        reliability_bp=8_000,
        mutuality_bp=8_000,
        repair_confidence_bp=8_000,
    )
    candidate = RelationshipHysteresisProjection(
        candidate_stage="acquaintance",
        direction="promote",
        candidate_since=NOW,
        confirming_adjustment_count=1,
    )
    first = adjustment_payload(
        first_signal,
        adjustment_id="adjustment:h1",
        expected_revision=1,
        before=start,
        after=high,
        accepted=RelationshipVariableDeltas(
            trust_bp=500,
            closeness_bp=500,
            respect_bp=500,
            reliability_bp=500,
            mutuality_bp=500,
            repair_confidence_bp=500,
        ),
        hysteresis_after=candidate,
    )
    # The accepted deltas are capped, so use a high-valued state whose before matches
    # the effective update while preserving a score above every later-stage threshold.
    high_before = RelationshipVariablesProjection(
        trust_bp=7_500,
        closeness_bp=7_500,
        respect_bp=7_500,
        reliability_bp=7_500,
        mutuality_bp=7_500,
        repair_confidence_bp=7_500,
    )
    initial_state = RelationshipStateProjection(
        relationship_id="relationship:user:geoff",
        subject_ref="user:geoff",
        entity_revision=1,
        variables=high_before,
        policy_digest=RELATIONSHIP_POLICY_DIGEST,
    )
    first = first.model_copy(
        update={"variables_before": high_before, "variables_after": high}
    )
    states, history = adjust_relationship_slow_variables(
        (initial_state,), (), (first_signal,), first, logical_time=NOW
    )
    assert states[0].stage == "stranger"
    assert states[0].hysteresis == candidate

    same_day_after = high.model_copy(update={"trust_bp": 8_001})
    same_day_hysteresis = candidate.model_copy(update={"confirming_adjustment_count": 2})
    same_day = adjustment_payload(
        second_signal,
        adjustment_id="adjustment:h2",
        expected_revision=2,
        before=high,
        after=same_day_after,
        accepted=RelationshipVariableDeltas(trust_bp=1),
        hysteresis_before=candidate,
        hysteresis_after=same_day_hysteresis,
        adjusted_at=NOW + timedelta(hours=1),
    )
    states, history = adjust_relationship_slow_variables(
        states,
        history,
        (first_signal, second_signal),
        same_day,
        logical_time=NOW + timedelta(hours=1),
    )
    assert states[0].stage == "stranger"

    next_day_after = same_day_after.model_copy(update={"trust_bp": 8_002})
    next_day = adjustment_payload(
        third_signal,
        adjustment_id="adjustment:h3",
        expected_revision=3,
        before=same_day_after,
        after=next_day_after,
        accepted=RelationshipVariableDeltas(trust_bp=1),
        stage_after="acquaintance",
        hysteresis_before=same_day_hysteresis,
        adjusted_at=NOW + timedelta(days=1),
    )
    states, _ = adjust_relationship_slow_variables(
        states,
        history,
        (first_signal, second_signal, third_signal),
        next_day,
        logical_time=NOW + timedelta(days=1),
    )
    assert states[0].stage == "acquaintance"


def test_compensation_restores_hysteresis_without_counting_as_confirmation() -> None:
    source = signal("signal:h-comp", code="h-comp", contradiction_group_ref="group:h-comp")
    before = RelationshipVariablesProjection(
        trust_bp=1_900,
        closeness_bp=1_900,
        respect_bp=1_900,
        reliability_bp=1_900,
        mutuality_bp=1_900,
        repair_confidence_bp=1_900,
    )
    after = RelationshipVariablesProjection(
        trust_bp=2_400,
        closeness_bp=2_400,
        respect_bp=2_400,
        reliability_bp=2_400,
        mutuality_bp=2_400,
        repair_confidence_bp=2_400,
    )
    candidate = RelationshipHysteresisProjection(
        candidate_stage="acquaintance",
        direction="promote",
        candidate_since=NOW,
        confirming_adjustment_count=1,
    )
    existing = RelationshipStateProjection(
        relationship_id="relationship:user:geoff",
        subject_ref="user:geoff",
        entity_revision=1,
        variables=before,
        policy_digest=RELATIONSHIP_POLICY_DIGEST,
    )
    original = adjustment_payload(
        source,
        adjustment_id="adjustment:h-comp-original",
        expected_revision=1,
        before=before,
        after=after,
        accepted=RelationshipVariableDeltas(
            trust_bp=500,
            closeness_bp=500,
            respect_bp=500,
            reliability_bp=500,
            mutuality_bp=500,
            repair_confidence_bp=500,
        ),
        hysteresis_after=candidate,
    )
    states, history = adjust_relationship_slow_variables(
        (existing,), (), (source,), original, logical_time=NOW
    )
    compensation = authorized(
        RelationshipSlowVariableAdjustedPayload,
        change_id="change:h-compensation",
        transition_id="transition:h-compensation",
        expected_entity_revision=2,
        policy_refs=("policy:relationship-v1",),
        relationship_id="relationship:user:geoff",
        subject_ref="user:geoff",
        adjustment_id="adjustment:h-compensation",
        operation="compensate",
        signal_refs=(source.signal_id,),
        proposed_deltas=RelationshipVariableDeltas(
            trust_bp=-500,
            closeness_bp=-500,
            respect_bp=-500,
            reliability_bp=-500,
            mutuality_bp=-500,
            repair_confidence_bp=-500,
        ),
        accepted_deltas=RelationshipVariableDeltas(
            trust_bp=-500,
            closeness_bp=-500,
            respect_bp=-500,
            reliability_bp=-500,
            mutuality_bp=-500,
            repair_confidence_bp=-500,
        ),
        variables_before=after,
        variables_after=before,
        stage_before="stranger",
        stage_after="stranger",
        hysteresis_before=candidate,
        hysteresis_after=RelationshipHysteresisProjection(),
        confidence_bp=10_000,
        persistence="durable",
        contradiction_group_ref="group:h-comp",
        rationale_code="correction",
        policy_version="relationship-policy.1",
        policy_digest=RELATIONSHIP_POLICY_DIGEST,
        adjusted_at=NOW + timedelta(days=1),
        compensates_adjustment_id="adjustment:h-comp-original",
    )
    restored, _ = adjust_relationship_slow_variables(
        states, history, (source,), compensation, logical_time=NOW + timedelta(days=1)
    )
    assert restored[0].stage == "stranger"
    assert restored[0].hysteresis == RelationshipHysteresisProjection()


def test_stage_gap_is_stable_and_declared_stages_ignore_thresholds() -> None:
    source = signal("signal:gap", code="gap", contradiction_group_ref="group:gap")
    gap = RelationshipVariablesProjection(
        trust_bp=1_200,
        closeness_bp=1_200,
        respect_bp=1_200,
        reliability_bp=1_200,
        mutuality_bp=1_200,
        repair_confidence_bp=1_200,
    )
    gap_after = gap.model_copy(update={"trust_bp": 1_201})
    state = RelationshipStateProjection(
        relationship_id="relationship:user:geoff",
        subject_ref="user:geoff",
        entity_revision=1,
        stage="acquaintance",
        variables=gap,
        policy_digest=RELATIONSHIP_POLICY_DIGEST,
    )
    payload = adjustment_payload(
        source,
        adjustment_id="adjustment:gap",
        expected_revision=1,
        before=gap,
        after=gap_after,
        accepted=RelationshipVariableDeltas(trust_bp=1),
        stage_before="acquaintance",
        stage_after="acquaintance",
    )
    states, _ = adjust_relationship_slow_variables(
        (state,), (), (source,), payload, logical_time=NOW
    )
    assert states[0].stage == "acquaintance"
    assert states[0].hysteresis == RelationshipHysteresisProjection()

    # A stage she declared keeps holding while the numbers move underneath it:
    # the thresholds cannot quietly demote her out of what she said.
    for declared_stage in COMMITMENT_ONLY_RELATIONSHIP_STAGES:
        declared = state.model_copy(update={"stage": declared_stage})
        declared_payload = payload.model_copy(
            update={"stage_before": declared_stage, "stage_after": declared_stage}
        )
        held, _ = adjust_relationship_slow_variables(
            (declared,), (), (source,), declared_payload, logical_time=NOW
        )
        assert held[0].stage == declared_stage
        assert held[0].variables.trust_bp == 1_201


def test_boundary_lifecycle_is_independent_of_relationship_stage() -> None:
    boundary = BoundaryProjection(
        boundary_id="boundary:privacy",
        entity_revision=1,
        subject_ref="user:geoff",
        scope_ref="scope:private-media",
        strength_bp=8_000,
        status="active",
        expires_at=None,
        evidence_refs=(evidence(),),
        origin=RelationshipBoundaryOrigin(
            change_id="change:boundary:open",
            transition_id="transition:boundary:open",
            policy_refs=("policy:boundary-v1",),
            accepted_event_ref="event:boundary:open",
        ),
        policy_version="boundary-policy.1",
        opened_at=NOW,
        updated_at=NOW,
    )
    opened = authorized(
        BoundaryChangedPayload,
        change_id=boundary.origin.change_id,
        transition_id=boundary.origin.transition_id,
        expected_entity_revision=0,
        evidence_refs=boundary.evidence_refs,
        policy_refs=boundary.origin.policy_refs,
        operation="open",
        boundary=boundary,
    )

    boundaries = change_boundary((), opened, logical_time=NOW)

    assert boundaries == (boundary,)
    assert boundaries[0].strength_bp == 8_000
    assert not hasattr(opened, "relationship_stage")

    duplicate_scope = boundary.model_copy(
        update={
            "boundary_id": "boundary:privacy:duplicate",
            "origin": RelationshipBoundaryOrigin(
                change_id="change:boundary:duplicate",
                transition_id="transition:boundary:duplicate",
                policy_refs=("policy:boundary-v1",),
                accepted_event_ref="event:boundary:duplicate",
            ),
        }
    )
    duplicate_open = authorized(
        BoundaryChangedPayload,
        change_id="change:boundary:duplicate",
        transition_id="transition:boundary:duplicate",
        expected_entity_revision=0,
        evidence_refs=duplicate_scope.evidence_refs,
        policy_refs=("policy:boundary-v1",),
        acceptance_id="acceptance:boundary:duplicate",
        proposal_id="proposal:boundary:duplicate",
        operation="open",
        boundary=duplicate_scope,
    )
    with pytest.raises(ValueError, match="subject scope"):
        change_boundary(boundaries, duplicate_open, logical_time=NOW)

    revised_boundary = boundary.model_copy(
        update={
            "entity_revision": 2,
            "strength_bp": 9_000,
            "origin": RelationshipBoundaryOrigin(
                change_id="change:boundary:revise",
                transition_id="transition:boundary:revise",
                policy_refs=("policy:boundary-v1",),
                accepted_event_ref="event:boundary:revise",
            ),
        }
    )
    revised = authorized(
        BoundaryChangedPayload,
        change_id="change:boundary:revise",
        transition_id="transition:boundary:revise",
        expected_entity_revision=1,
        evidence_refs=revised_boundary.evidence_refs,
        policy_refs=("policy:boundary-v1",),
        acceptance_id="acceptance:boundary:revise",
        proposal_id="proposal:boundary:revise",
        operation="revise",
        boundary=revised_boundary,
    )
    boundaries = change_boundary(boundaries, revised, logical_time=NOW)
    assert boundaries[0].strength_bp == 9_000

    with pytest.raises(ValueError, match="stale"):
        change_boundary(boundaries, revised, logical_time=NOW)

    closed_boundary = revised_boundary.model_copy(
        update={
            "entity_revision": 3,
            "status": "closed",
            "origin": RelationshipBoundaryOrigin(
                change_id="change:boundary:close",
                transition_id="transition:boundary:close",
                policy_refs=("policy:boundary-v1",),
                accepted_event_ref="event:boundary:close",
            ),
        }
    )
    closed = authorized(
        BoundaryChangedPayload,
        change_id="change:boundary:close",
        transition_id="transition:boundary:close",
        expected_entity_revision=2,
        evidence_refs=closed_boundary.evidence_refs,
        policy_refs=("policy:boundary-v1",),
        acceptance_id="acceptance:boundary:close",
        proposal_id="proposal:boundary:close",
        operation="close",
        boundary=closed_boundary,
    )
    boundaries = change_boundary(boundaries, closed, logical_time=NOW)
    assert boundaries[0].status == "closed"

    invalid_revise_boundary = closed_boundary.model_copy(
        update={
            "origin": RelationshipBoundaryOrigin(
                change_id="change:boundary:bad-revise",
                transition_id="transition:boundary:bad-revise",
                policy_refs=("policy:boundary-v1",),
                accepted_event_ref="event:boundary:bad-revise",
            )
        }
    )
    with pytest.raises(ValueError, match="revision must remain active"):
        authorized(
            BoundaryChangedPayload,
            change_id="change:boundary:bad-revise",
            transition_id="transition:boundary:bad-revise",
            expected_entity_revision=2,
            evidence_refs=invalid_revise_boundary.evidence_refs,
            policy_refs=("policy:boundary-v1",),
            operation="revise",
            boundary=invalid_revise_boundary,
        )


def test_close_friend_7000_is_mean_of_six_not_four_axis_saturation() -> None:
    """close_friend enter 7000 is honest mean-of-six; four-axis max is 6666.

    The slow ladder must not be lowered because reliability/repair were left
    at 0.  Those axes share the trust/closeness write path.  Declaration
    (we_are) is the main road.
    """

    saturated = RelationshipVariablesProjection(
        trust_bp=10_000,
        closeness_bp=10_000,
        respect_bp=10_000,
        reliability_bp=0,
        mutuality_bp=10_000,
        repair_confidence_bp=0,
    )
    just_under = RelationshipVariablesProjection(
        trust_bp=6_999,
        closeness_bp=6_999,
        respect_bp=6_999,
        reliability_bp=6_999,
        mutuality_bp=6_999,
        repair_confidence_bp=6_999,
    )
    just_on = RelationshipVariablesProjection(
        trust_bp=7_000,
        closeness_bp=7_000,
        respect_bp=7_000,
        reliability_bp=7_000,
        mutuality_bp=7_000,
        repair_confidence_bp=7_000,
    )

    def derive(variables: RelationshipVariablesProjection):
        return relationship_reducers._derive_stage(
            "friend",
            variables,
            RelationshipHysteresisProjection(),
            NOW,
        )

    sat_stage, sat_hysteresis = derive(saturated)
    just_under_stage, just_under_hysteresis = derive(just_under)
    just_on_stage, just_on_hysteresis = derive(just_on)

    assert sat_stage == "friend"
    assert sat_hysteresis.candidate_stage is None
    assert just_under_stage == "friend"
    assert just_under_hysteresis.candidate_stage is None
    assert just_on_stage == "friend"
    assert just_on_hysteresis.candidate_stage == "close_friend"


def test_us_deltas_without_about_us_why_us_pair_are_a_visible_failure() -> None:
    """A lone us_deltas must not compile and vanish; she gets a precise miss.

    ``present_prompt`` owns this gate. inbound_wire.py only forwards it.
    """

    from companion_daemon.world_v2.present_prompt import (
        SLIM_RELATIONSHIP_RESIDUE_INCOMPLETE,
        compile_slim_interior_envelope,
    )

    base = {
        "messages": ["我记下了"],
        "felt": "心里动了一下",
        "stuck_with_me": "他认真听了",
        "wants": "把靠近留下来",
        "photo": False,
        "us_deltas": {"trust_bp": 120, "closeness_bp": 80},
    }
    for partial in (base, {**base, "about_us": "被认真听的感觉"}):
        try:
            compile_slim_interior_envelope(partial, reply_only=True)
        except ValueError as exc:
            assert SLIM_RELATIONSHIP_RESIDUE_INCOMPLETE in str(exc)
            continue
        raise AssertionError("incomplete residue must not compile")
    kept = compile_slim_interior_envelope(
        {
            **base,
            "about_us": "被认真听的感觉",
            "why_us": "因为这一句是真的",
        },
        reply_only=True,
    )
    assert kept is not None
    assert kept["appraisal_draft"]["relationship_signal"]["suggested_deltas"]["trust_bp"] == 120


def test_relationship_schema_rejects_naive_authority_times() -> None:
    source = signal("signal:time", code="time", contradiction_group_ref="group:time")
    with pytest.raises(ValueError, match="timezone-aware"):
        RelationshipSignalProjection.model_validate(
            {**source.model_dump(), "accepted_at": NAIVE_NOW}
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        RelationshipHysteresisProjection(
            candidate_stage="acquaintance",
            direction="promote",
            candidate_since=NAIVE_NOW,
            confirming_adjustment_count=1,
        )
    adjustment = RelationshipAdjustmentProjection(
        adjustment_id="adjustment:time",
        subject_ref="user:geoff",
        relationship_revision=1,
        operation="adjust",
        signal_refs=(source.signal_id,),
        proposed_deltas=RelationshipVariableDeltas(trust_bp=1),
        accepted_deltas=RelationshipVariableDeltas(trust_bp=1),
        variables_before=RelationshipVariablesProjection(),
        variables_after=RelationshipVariablesProjection(trust_bp=1),
        stage_before="stranger",
        stage_after="stranger",
        hysteresis_before=RelationshipHysteresisProjection(),
        hysteresis_after=RelationshipHysteresisProjection(),
        confidence_bp=8_000,
        persistence="durable",
        rationale_code="time",
        policy_version="relationship-policy.1",
        policy_digest=RELATIONSHIP_POLICY_DIGEST,
        adjusted_at=NOW,
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        RelationshipAdjustmentProjection.model_validate(
            {**adjustment.model_dump(), "adjusted_at": NAIVE_NOW}
        )
    boundary = BoundaryProjection(
        boundary_id="boundary:time",
        entity_revision=1,
        subject_ref="user:geoff",
        scope_ref="scope:time",
        strength_bp=1,
        status="active",
        evidence_refs=(evidence(),),
        origin=RelationshipBoundaryOrigin(
            change_id="change:time",
            transition_id="transition:time",
            policy_refs=("policy:boundary-v1",),
            accepted_event_ref="event:time",
        ),
        policy_version="boundary-policy.1",
        opened_at=NOW,
        updated_at=NOW,
    )
    for field in ("opened_at", "updated_at", "expires_at"):
        with pytest.raises(ValueError, match="timezone-aware"):
            BoundaryProjection.model_validate({**boundary.model_dump(), field: NAIVE_NOW})
    state = RelationshipStateProjection(
        relationship_id="relationship:user:geoff",
        subject_ref="user:geoff",
        policy_digest=RELATIONSHIP_POLICY_DIGEST,
        last_adjusted_at=NOW,
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        RelationshipStateProjection.model_validate(
            {**state.model_dump(), "last_adjusted_at": NAIVE_NOW}
        )
