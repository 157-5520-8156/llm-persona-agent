from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json

from companion_daemon.world_v2.companion_identity import CompanionIdentityFrame
from companion_daemon.world_v2.private_impression_events import (
    PrivateImpressionAcceptedPayload,
    PrivateImpressionPredecessorRef,
    private_impression_mutation_hash,
)
from companion_daemon.world_v2.private_impression_producer import (
    PrivateImpressionReflectionCapsule,
    PrivateImpressionReflectionSource,
    _materialize_draft,
)
from companion_daemon.world_v2.private_impression_reducers import accept_private_impression
from companion_daemon.world_v2.schema_core import EvidenceRef
from companion_daemon.world_v2.schemas import (
    AppraisalHypothesis,
    AppraisalMeaningRef,
    AppraisalOrigin,
    AppraisalProjection,
    PrivateImpressionOrigin,
    PrivateImpressionProjection,
)

NOW = datetime(2026, 8, 18, 16, 0, tzinfo=UTC)
EARLIER = NOW - timedelta(days=3)
WORLD_ID = "world-v2-private-impression-release"


def _release_capsule() -> PrivateImpressionReflectionCapsule:
    appraisal = PrivateImpressionReflectionSource(
        source_ref="src:appraisal:1",
        source_kind="appraisal",
        authority_event_ref="evt:appraisal:1",
        value_json=json.dumps({"appraisal_id": "ap:1"}, ensure_ascii=False),
    )
    existing = PrivateImpressionReflectionSource(
        source_ref="private-impression:impression:older",
        source_kind="existing_impression",
        authority_event_ref="evt:impression:older",
        value_json=json.dumps({"reflection_summary": "旧心事"}, ensure_ascii=False),
    )
    return PrivateImpressionReflectionCapsule(
        capsule_id="a" * 64,
        world_id=WORLD_ID,
        world_revision=1,
        deliberation_revision=1,
        ledger_sequence=1,
        logical_time=NOW.isoformat(),
        subject_ref="user:primary",
        anchor_appraisal_id="ap:1",
        identity_frame=CompanionIdentityFrame(companion_name="枝枝", counterpart_name="对方"),
        sources=(appraisal, existing),
    )


def test_release_materializes_exactly_one_predecessor() -> None:
    draft = _materialize_draft(
        json.dumps(
            {
                "decision": "release",
                "predecessor_refs": ["private-impression:impression:older"],
                "source_refs": [
                    "src:appraisal:1",
                    "private-impression:impression:older",
                ],
                "reflection_summary": "这件事我已经说过了，可以搁下。",
                "confidence": 7200,
                "expiry_condition": "until_counter_evidence",
            },
            ensure_ascii=False,
        ),
        capsule=_release_capsule(),
    )
    assert draft is not None
    assert draft.decision == "release"
    assert draft.predecessor_refs == ("private-impression:impression:older",)


def test_release_without_predecessor_is_rejected() -> None:
    try:
        _materialize_draft(
            json.dumps(
                {
                    "decision": "release",
                    "predecessor_refs": [],
                    "source_refs": ["src:appraisal:1"],
                    "reflection_summary": "搁下。",
                    "confidence": 5000,
                    "expiry_condition": "until_counter_evidence",
                },
                ensure_ascii=False,
            ),
            capsule=_release_capsule(),
        )
    except ValueError as exc:
        assert "invalid" in str(exc)
    else:
        raise AssertionError("release without a predecessor must fail closed")


def _appraisal() -> AppraisalProjection:
    return AppraisalProjection(
        appraisal_id="appraisal:care",
        entity_revision=1,
        subject_ref="user:primary",
        source_cluster_ref="cluster:message-1",
        origin=AppraisalOrigin(
            change_id="change:appraisal:1",
            transition_id="transition:appraisal:1",
            policy_refs=("policy:appraisal.1",),
            matrix_catalog_version="matrix.1",
            clustering_policy_version="cluster.1",
            accepted_event_ref="event:appraisal:1",
        ),
        hypotheses=(
            AppraisalHypothesis(
                hypothesis_id="hypothesis:care",
                meaning="care",
                attribution="user",
                controllability="partly_controllable",
                severity="low",
                weight_bp=10_000,
            ),
        ),
        evidence_refs=(
            EvidenceRef(
                ref_id="event:observation:1",
                evidence_type="observed_message",
                claim_purpose="private_hypothesis",
            ),
        ),
        confidence_bp=7_200,
        accepted_at=EARLIER,
        expires_at=NOW + timedelta(days=6),
        status="active",
    )


def test_release_replaces_in_place_and_leaves_the_row_recallable() -> None:
    appraisal = _appraisal()
    active = PrivateImpressionProjection(
        impression_id="impression:care",
        entity_revision=1,
        subject_ref="user:primary",
        interpretation_refs=("appraisal:appraisal:care:hypothesis:care",),
        source_refs=("event:impression:1",),
        reflection_summary="我还搁着这件事。",
        confidence_bp=6_900,
        first_seen=EARLIER,
        last_supported=EARLIER,
        expiry_condition="until_counter_evidence",
        status="active",
        origin=PrivateImpressionOrigin(
            change_id="change:impression:1",
            transition_id="transition:impression:1",
            policy_refs=("policy:private-impression.1",),
            accepted_event_ref="event:impression:1",
        ),
    )
    released = active.model_copy(
        update={
            "entity_revision": 2,
            "status": "released",
            "reflection_summary": "这件事已经说过了，可以搁下。",
            "confidence_bp": 8_000,
            "last_supported": NOW,
            "source_refs": ("event:impression:1", "event:appraisal:1"),
            "origin": PrivateImpressionOrigin(
                change_id="change:impression:release",
                transition_id="transition:impression:release",
                policy_refs=("policy:private-impression.1",),
                accepted_event_ref="event:impression:release",
            ),
        }
    )
    payload_dict: dict[str, object] = {
        "change_id": "change:impression:release",
        "transition_id": "transition:impression:release",
        "transition_kind": "release",
        "expected_entity_revision": 1,
        "predecessor_refs": [
            PrivateImpressionPredecessorRef(
                impression_id="impression:care",
                expected_entity_revision=1,
            ).model_dump(mode="json")
        ],
        "evidence_refs": [
            EvidenceRef(
                ref_id="event:impression:1",
                evidence_type="committed_world_event",
                claim_purpose="private_hypothesis",
                source_world_revision=1,
                immutable_hash="a" * 64,
            ).model_dump(mode="json"),
            EvidenceRef(
                ref_id="event:appraisal:1",
                evidence_type="committed_world_event",
                claim_purpose="private_hypothesis",
                source_world_revision=2,
                immutable_hash="b" * 64,
            ).model_dump(mode="json"),
        ],
        "appraisal_refs": [
            AppraisalMeaningRef(
                appraisal_id=appraisal.appraisal_id,
                hypothesis_id="hypothesis:care",
                source_cluster_ref=appraisal.source_cluster_ref,
                accepted_change_id=appraisal.origin.change_id,
                accepted_transition_id=appraisal.origin.transition_id,
            ).model_dump(mode="json")
        ],
        "policy_refs": ["policy:private-impression.1"],
        "acceptance_id": "acceptance:impression:release",
        "proposal_id": "proposal:impression:release",
        "evaluated_world_revision": 3,
        "accepted_change_hash": "0" * 64,
        "reflection_contract": "character-interior-private-impression-transition.1",
        "reflection_decision": "release",
        "reflection_source_refs": [
            "private-impression:impression:care",
            f"appraisal:{appraisal.appraisal_id}:hypothesis:care",
        ],
        "source_model_result": "model-result:release",
        "source_capsule_id": "c" * 64,
        "impression": released.model_dump(mode="json"),
    }
    payload_dict["accepted_change_hash"] = private_impression_mutation_hash(payload_dict)
    payload = PrivateImpressionAcceptedPayload.model_validate_json(
        json.dumps(payload_dict, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )

    after = accept_private_impression(
        (active,),
        payload,
        logical_time=NOW,
        appraisals=(appraisal,),
    )

    assert len(after) == 1
    assert after[0].impression_id == "impression:care"
    assert after[0].status == "released"
    assert after[0].entity_revision == 2
    assert after[0].first_seen == EARLIER
    assert after[0].interpretation_refs == active.interpretation_refs
    assert after[0].reflection_summary == "这件事已经说过了，可以搁下。"
