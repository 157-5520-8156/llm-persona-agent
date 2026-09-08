"""World-event role choices become future Plans through real SQLite authority."""

from __future__ import annotations

from datetime import timedelta
import json

import pytest

from companion_daemon.world_v2.accepted_ledger_batch import AcceptedLedgerBatchIssuer
from companion_daemon.world_v2.character_interior.run_result import CausalOpportunityIdentity
from companion_daemon.world_v2.proposal_audit_schemas import (
    ModelResultRecordedPayload,
    ProposalRecordedV2Payload,
    RecordedCharacterInteriorTurnLineage,
    RecordedModelResultAudit,
    RecordedModelRoute,
    canonical_json,
    model_audit_json,
    sha256,
)
from companion_daemon.world_v2.proposal_envelope import (
    CanonicalTypedPayload,
    DecisionProposal,
    ProposalEvidenceRef,
    TypedChange,
)
from companion_daemon.world_v2.schemas import ProjectionCursor
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_life_projection import WORLD_ID, commit, event, seed_through_proposal, settlement_batch

ACTOR = "actor:companion"
SOURCE = "occurrence-settled"
INTENT = {
    "execution_scope": "self_directed",
    "intention": "想花一会儿整理自己接下来要写的东西。",
    "start_after_seconds": 0,
    "duration_seconds": 120,
    "importance_bp": 6000,
}


def _cursor(ledger):
    projection = ledger.project()
    return ProjectionCursor(
        world_revision=projection.world_revision,
        deliberation_revision=projection.deliberation_revision,
        ledger_sequence=projection.ledger_sequence,
    )


def _seed(path, *, participants=(ACTOR,)):
    issuer = AcceptedLedgerBatchIssuer()
    ledger = SQLiteWorldLedger(path, world_id=WORLD_ID, accepted_batch_issuer=issuer)
    seed_through_proposal(ledger, participant_refs=participants)
    commit(ledger, settlement_batch(participant_refs=participants))
    return ledger, issuer


def _audit(ledger, *, actor=ACTOR, source_ref=SOURCE, purpose="world_stimulus_appraisal",
           identity="one", intent=None, evidence_hash=None, source_refs=None):
    """Use the public immutable ModelResult + Proposal pair, not a fake projection."""
    from companion_daemon.world_v2.world_life_intent_runtime import world_life_plan_id

    projection = ledger.project()
    source = next(x for x in projection.committed_world_event_refs if x.event_id == source_ref)
    payload = {**(intent or INTENT), "actor_ref": actor, "source_event_ref": source_ref}
    change = TypedChange(
        change_id="change:world-intent:" + identity,
        kind="world_life_intent",
        target_id=world_life_plan_id(world_id=WORLD_ID, actor_ref=actor, source_event_ref=source_ref),
        transition="plan", expected_entity_revision=0,
        evidence_refs=(source_ref,), policy_refs=("policy:world-life-intent.1",),
        payload=CanonicalTypedPayload.from_value(payload_schema="world_life_intent.v1", value=payload),
    )
    proposal = DecisionProposal(
        proposal_id="proposal:world-intent:" + identity,
        schema_registry_version="world-v2-proposals.4",
        trigger_ref=source_ref, evaluated_world_revision=projection.world_revision,
        evidence_refs=(ProposalEvidenceRef(
            ref_id=source_ref, evidence_kind="settled_world_event",
            source_world_revision=source.world_revision,
            immutable_hash="sha256:" + (evidence_hash or source.payload_hash),
        ),),
        proposed_changes=(change,), confidence=7000,
        brief_rationale="我决定给这件事留一点自己的时间。",
        behavior_tendency="安排自己的活动", stance="自行决定", display_strategy="withhold",
        timing_choice="silent",
    )
    opportunity = CausalOpportunityIdentity.from_source_refs(
        world_id=WORLD_ID, actor_ref=actor, purpose=purpose,
        source_refs=source_refs or (source_ref,), epoch=source_ref,
    )
    model_call = "model-call:world-intent:" + identity
    snapshot_hash = sha256("snapshot:" + identity)
    audit = RecordedModelResultAudit(
        model_call_id=model_call, model_result_ref="model-result:world-intent:" + identity,
        attempt_id="inner-turn:world-intent:" + identity,
        route=RecordedModelRoute(tier="flash", reason_code="character_interior_world_stimulus",
                                 router_version="character-interior-world-stimulus-appraisal-result.2"),
        model_id="offline-character", model_version="fixture.1",
        request_hash=sha256("request:" + identity), response_hash=sha256("response:" + identity),
        character_interior_lineage=RecordedCharacterInteriorTurnLineage(
            inner_turn_id="inner-turn:world-intent:" + identity, purpose=purpose,
            opportunity_ref=opportunity.opportunity_ref,
            causal_world_id=WORLD_ID, causal_actor_ref=actor,
            causal_source_refs=opportunity.source_refs, causal_epoch=opportunity.epoch,
            causal_contract_version=opportunity.contract_version,
            snapshot_id="inner-life-snapshot:sha256:" + snapshot_hash,
            snapshot_hash=snapshot_hash, capability_ref="capability:world-intent:" + identity,
            author_model_id="offline-character", author_model_version="fixture.1",
            author_model_call_id=model_call,
            author_request_hash="sha256:" + sha256("request:" + identity),
            author_response_hash="sha256:" + sha256("response:" + identity),
            author_attempt_ordinal=0, private_self_lineage_hash="sha256:" + "7" * 64,
            decision_hash="sha256:" + "8" * 64,
        ), status="proposal_validated",
    )
    audit_json = model_audit_json(audit)
    result_id = "deliberation:" + sha256(canonical_json({
        "capsule_id": snapshot_hash, "proposal_hash": proposal.proposal_hash,
        "attempt_audits": [json.loads(audit_json)],
    }))
    shared = dict(model_result_ref=audit.model_result_ref, deliberation_result_id=result_id,
                  proposal_hash=proposal.proposal_hash, model_call_id=model_call,
                  attempt_id=audit.attempt_id, capsule_id=snapshot_hash, trigger_ref=source_ref,
                  evaluated_world_revision=projection.world_revision)
    model_payload = ModelResultRecordedPayload(
        **shared, audit_contract="model-result-audit.7", attempt_index=0, attempt_count=1,
        audit_json=audit_json, audit_hash=sha256(audit_json),
    )
    proposal_payload = ProposalRecordedV2Payload(
        **shared, proposal_id=proposal.proposal_id, proposal_kind="decision",
        proposal_json=canonical_json(proposal.model_dump(mode="json")),
    )
    commit(ledger, [
        event("model:world-intent:" + identity, "ModelResultRecorded", model_payload.model_dump(mode="json")),
        event("proposal:world-intent:" + identity, "ProposalRecorded", proposal_payload.model_dump(mode="json")),
    ])
    return proposal, _cursor(ledger)


def test_world_role_intent_accepts_once_and_cold_replays_original_choice(tmp_path):
    from companion_daemon.world_v2.world_life_intent_runtime import WorldLifeIntentRuntime

    path = tmp_path / "world.sqlite"
    ledger, issuer = _seed(path)
    proposal, cursor = _audit(ledger)
    selected_at = ledger.project().logical_time
    runtime = WorldLifeIntentRuntime(ledger=ledger, owner_actor_ref=ACTOR)
    receipt = runtime.accept(world_id=WORLD_ID, audit_cursor=cursor, proposal_id=proposal.proposal_id)
    plan = next(x for x in ledger.project().plans if x.plan_id.startswith("plan:world-life-intent:"))
    assert plan.status == "planned"
    assert plan.scheduled_window.opens_at == selected_at
    assert plan.scheduled_window.closes_at == selected_at + timedelta(seconds=120)
    assert plan.location_ref is None and plan.participant_refs == ()
    assert plan.owner_actor_ref == ACTOR and plan.privacy_class == "private"
    assert runtime.read_for_plan(plan_id=plan.plan_id).character_intention == INTENT["intention"]
    ledger.close()

    restarted = SQLiteWorldLedger(path, world_id=WORLD_ID, accepted_batch_issuer=issuer)
    recovered = WorldLifeIntentRuntime(ledger=restarted, owner_actor_ref=ACTOR)
    assert recovered.accept(world_id=WORLD_ID, audit_cursor=cursor, proposal_id=proposal.proposal_id) == receipt
    assert recovered.read_for_plan(plan_id=plan.plan_id).origin.selected_at == selected_at
    assert next(x for x in restarted.project().plans if x.plan_id == plan.plan_id) == plan
    restarted.close()
