"""World-event role choices become future Plans through real SQLite authority."""

from __future__ import annotations

from datetime import datetime, timedelta
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
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID, accepted_batch_issuer=issuer)
    seed_through_proposal(ledger, participant_refs=participants)
    commit(ledger, settlement_batch(participant_refs=participants))
    return ledger, issuer


def _audit(ledger, *, actor=ACTOR, source_ref=SOURCE, purpose="world_stimulus_appraisal",
           identity="one", intent=None, evidence_hash=None, source_refs=None, lineage_actor=None):
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
        world_id=WORLD_ID, actor_ref=lineage_actor or actor, purpose=purpose,
        source_refs=source_refs or (source_ref,), epoch=source_ref,
    )
    model_call = "model-call:world-intent:" + identity
    snapshot_hash = sha256("snapshot:" + identity)
    audit = RecordedModelResultAudit(
        model_call_id=model_call, model_result_ref="model-result:" + sha256(canonical_json({
            "model_call_id": model_call, "response_hash": sha256("response:" + identity),
        })),
        attempt_id="inner-turn:world-intent:" + identity,
        route=RecordedModelRoute(tier="flash", reason_code="character_interior_world_stimulus",
                                 router_version="character-interior-world-stimulus-appraisal-result.2"),
        model_id="offline-character", model_version="fixture.1",
        request_hash=sha256("request:" + identity), response_hash=sha256("response:" + identity),
        character_interior_lineage=RecordedCharacterInteriorTurnLineage(
            inner_turn_id="inner-turn:world-intent:" + identity, purpose=purpose,
            opportunity_ref=opportunity.opportunity_ref,
            causal_world_id=WORLD_ID, causal_actor_ref=lineage_actor or actor,
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

    restarted = SQLiteWorldLedger(path=path, world_id=WORLD_ID, accepted_batch_issuer=issuer)
    recovered = WorldLifeIntentRuntime(ledger=restarted, owner_actor_ref=ACTOR)
    assert recovered.accept(world_id=WORLD_ID, audit_cursor=cursor, proposal_id=proposal.proposal_id) == receipt
    assert recovered.read_for_plan(plan_id=plan.plan_id).origin.selected_at == selected_at
    assert next(x for x in restarted.project().plans if x.plan_id == plan.plan_id) == plan
    restarted.close()


def _advance_clock(ledger, seconds, label):
    now = ledger.project().logical_time
    commit(ledger, [event("clock:" + label, "ClockAdvanced", {
        "logical_time_from": now.isoformat(),
        "logical_time_to": (now + timedelta(seconds=seconds)).isoformat(),
    }, at=now + timedelta(seconds=seconds))])
    return "clock:" + label


@pytest.mark.asyncio
async def test_world_plan_uses_existing_lifecycle_and_exact_active_completed_readers(tmp_path):
    from companion_daemon.world_v2.activity_lifecycle_runtime import (
        ActivityLifecycleAcceptanceRuntime, ActivityLifecycleProposalRecorder,
    )
    from companion_daemon.world_v2.activity_lifecycle_worker import ActivityLifecycleWorker
    from companion_daemon.world_v2.chat_life_intent_runtime import CompositeActivityPlanMaterialReader
    from companion_daemon.world_v2.life_ecology_activity import ActivityOpeningCatalog
    from companion_daemon.world_v2.life_ecology_contract import LifeEcologyRunKey
    from companion_daemon.world_v2.life_ecology_trigger_store import LedgerLifeEcologyTriggerStore
    from companion_daemon.world_v2.world_life_intent_runtime import (
        WorldLifeIntentActiveReader, WorldLifeIntentCompletedReader, WorldLifeIntentRuntime,
    )
    from test_activity_lifecycle_runtime import _Interior

    class ChoosingInterior(_Interior):
        async def consider(self, opportunity):
            result = await super().consider(opportunity)
            openings = opportunity.capability_manifest.payload["openings"]
            selected = next((x for x in openings if x["safe_summary"].startswith(
                "finish the current abstract activity"
            )), openings[0])
            decision = dict(result.decision)
            decision["payload"] = {**decision["payload"], "selected_token": selected["opening_token"]}
            return result.model_copy(update={"decision": decision})

    path = tmp_path / "world.sqlite"
    ledger, issuer = _seed(path)
    proposal, cursor = _audit(ledger)
    runtime = WorldLifeIntentRuntime(ledger=ledger, owner_actor_ref=ACTOR)
    runtime.accept(world_id=WORLD_ID, audit_cursor=cursor, proposal_id=proposal.proposal_id)
    plan_id = next(x.plan_id for x in ledger.project().plans if x.plan_id.startswith("plan:world-life-intent:"))
    experiences_before = ledger.project().experiences
    interior = ChoosingInterior()

    async def step(wake_ref):
        claim = await LedgerLifeEcologyTriggerStore(ledger=ledger, owner_id="worker:life").claim_or_join(
            key=LifeEcologyRunKey(world_id=WORLD_ID, wake_event_ref=wake_ref, catalog_version="life-ecology.1"),
            trace_id="trace:life", correlation_id="correlation:life",
        )
        worker = ActivityLifecycleWorker(
            ledger=ledger, catalog=ActivityOpeningCatalog(owner_actor_ref=ACTOR),
            character_interior=interior, owner_actor_ref=ACTOR,
            proposal_recorder=ActivityLifecycleProposalRecorder(ledger=ledger),
            acceptance_runtime=ActivityLifecycleAcceptanceRuntime(ledger=ledger, batch_issuer=issuer),
            ecology_catalog_version="life-ecology.1",
            plan_material_reader=CompositeActivityPlanMaterialReader(runtime),
        )
        return await worker.advance_once(
            wake_event_ref=wake_ref, trigger_id=claim.trigger_id,
            logical_time=ledger.project().logical_time, actor="worker:life",
            trace_id="trace:life", correlation_id="correlation:life",
        )

    assert (await step(_advance_clock(ledger, 1, "start"))).status == "transitioned"
    active = WorldLifeIntentActiveReader(ledger=ledger).read_active_plan(
        plan_id=plan_id, expected_cursor=_cursor(ledger), actor_ref=ACTOR, viewer_privacy_ceiling="private",
    )
    assert active is not None and active.accepted_intention.text == INTENT["intention"]
    assert active.location_ref is None and active.participant_refs == ()
    assert active.source_bindings[-1].authority_event_ref == active.activity_event_ref
    assert ledger.lookup_event_commit(active.activity_event_ref)[0].event_type == "ActivityStarted"
    assert WorldLifeIntentActiveReader(ledger=ledger).read_active_plan(
        plan_id=plan_id, expected_cursor=_cursor(ledger), actor_ref="actor:other", viewer_privacy_ceiling="private",
    ) is None
    assert WorldLifeIntentActiveReader(ledger=ledger).read_active_plan(
        plan_id=plan_id, expected_cursor=_cursor(ledger), actor_ref=ACTOR, viewer_privacy_ceiling="public",
    ) is None
    ledger.close()
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID, accepted_batch_issuer=issuer)
    runtime = WorldLifeIntentRuntime(ledger=ledger, owner_actor_ref=ACTOR)
    assert (await step(_advance_clock(ledger, 120, "complete"))).status == "transitioned"
    completed = WorldLifeIntentCompletedReader(ledger=ledger).read_completed_plan(
        plan_id=plan_id, expected_cursor=_cursor(ledger), actor_ref=ACTOR, viewer_privacy_ceiling="private",
    )
    assert completed is not None and completed.accepted_intention.text == INTENT["intention"]
    assert ledger.lookup_event_commit(completed.activity_event_ref)[0].event_type == "ActivityCompleted"
    assert ledger.project().experiences == experiences_before  # Completion does not prove an outcome.
    calls = len(interior.opportunities)
    receipt = runtime.accept(world_id=WORLD_ID, audit_cursor=cursor, proposal_id=proposal.proposal_id)
    assert len(interior.opportunities) == calls == 2
    ledger.close()
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID, accepted_batch_issuer=issuer)
    assert WorldLifeIntentRuntime(ledger=ledger, owner_actor_ref=ACTOR).accept(
        world_id=WORLD_ID, audit_cursor=cursor, proposal_id=proposal.proposal_id,
    ) == receipt
    assert WorldLifeIntentCompletedReader(ledger=ledger).read_completed_plan(
        plan_id=plan_id, expected_cursor=_cursor(ledger), actor_ref=ACTOR, viewer_privacy_ceiling="private",
    ) == completed
    ledger.close()


@pytest.mark.parametrize("options,error", [
    ({"actor": "actor:other"}, "actor_mismatch"),
    ({"purpose": "inbound_turn"}, "inner_turn_authority_invalid"),
    ({"source_ref": "clock-life"}, "settlement_authority_invalid"),
    ({"source_refs": ("clock-life",)}, "inner_turn_authority_invalid"),
    ({"lineage_actor": "actor:other"}, "inner_turn_authority_invalid"),
    ({"evidence_hash": "0" * 64}, "source_binding_invalid"),
])
def test_unrelated_source_or_role_audit_cannot_authorize_world_intent(tmp_path, options, error):
    from companion_daemon.world_v2.world_life_intent_runtime import WorldLifeIntentRuntime

    ledger, _ = _seed(tmp_path / "world.sqlite")
    proposal, cursor = _audit(ledger, **options)
    before = ledger.project()
    with pytest.raises(ValueError, match=error):
        WorldLifeIntentRuntime(ledger=ledger, owner_actor_ref=ACTOR).accept(
            world_id=WORLD_ID, audit_cursor=cursor, proposal_id=proposal.proposal_id,
        )
    assert ledger.project() == before
    ledger.close()


def test_nonparticipant_has_no_self_response_plan_authority(tmp_path):
    from companion_daemon.world_v2.world_life_intent_runtime import WorldLifeIntentRuntime

    ledger, _ = _seed(tmp_path / "world.sqlite", participants=("npc:lin",))
    proposal, cursor = _audit(ledger)
    with pytest.raises(ValueError, match="settlement_authority_invalid"):
        WorldLifeIntentRuntime(ledger=ledger, owner_actor_ref=ACTOR).accept(
            world_id=WORLD_ID, audit_cursor=cursor, proposal_id=proposal.proposal_id,
        )
    ledger.close()


def test_same_source_reauthoring_cannot_duplicate_or_rebind_accepted_plan(tmp_path):
    from companion_daemon.world_v2.world_life_intent_runtime import WorldLifeIntentRuntime

    ledger, _ = _seed(tmp_path / "world.sqlite")
    proposal, cursor = _audit(ledger)
    runtime = WorldLifeIntentRuntime(ledger=ledger, owner_actor_ref=ACTOR)
    first = runtime.accept(world_id=WORLD_ID, audit_cursor=cursor, proposal_id=proposal.proposal_id)
    _advance_clock(ledger, 30, "later")
    # New model choice exists, but identical life facet joins its original accepted effect.
    revised, revised_cursor = _audit(ledger, identity="two")
    assert runtime.accept(world_id=WORLD_ID, audit_cursor=revised_cursor, proposal_id=revised.proposal_id) == first
    material = runtime.read_for_plan(plan_id=ledger.lookup_event_commit(first.event_ids[0])[0].payload()["plan"]["plan_id"])
    assert material.origin.proposal_id == proposal.proposal_id
    changed, changed_cursor = _audit(ledger, identity="three", intent={**INTENT, "duration_seconds": 180})
    with pytest.raises(ValueError, match="effect_identity_conflict"):
        runtime.accept(world_id=WORLD_ID, audit_cursor=changed_cursor, proposal_id=changed.proposal_id)
    assert len([x for x in ledger.project().plans if x.plan_id.startswith("plan:world-life-intent:")]) == 1
    ledger.close()


def test_delayed_consumption_keeps_selected_window_and_source(tmp_path):
    from companion_daemon.world_v2.world_life_intent_runtime import WorldLifeIntentRuntime

    ledger, _ = _seed(tmp_path / "world.sqlite")
    proposal, cursor = _audit(ledger)
    selected_at = ledger.project().logical_time
    _advance_clock(ledger, 30, "late-consumption")
    result = WorldLifeIntentRuntime(ledger=ledger, owner_actor_ref=ACTOR).accept(
        world_id=WORLD_ID, audit_cursor=cursor, proposal_id=proposal.proposal_id,
    )
    accepted = ledger.lookup_event_commit(result.event_ids[0])[0].payload()
    assert datetime.fromisoformat(accepted["world_intent_origin"]["selected_at"]) == selected_at
    assert datetime.fromisoformat(accepted["plan"]["scheduled_window"]["opens_at"]) == selected_at
    ledger.close()


@pytest.mark.parametrize("tamper", ["origin", "actor", "intention", "policy", "source"])
def test_direct_ledger_plan_write_cannot_bypass_original_authority(tmp_path, tamper):
    from companion_daemon.world_v2.world_life_intent_runtime import derive_world_life_plan, EVENT_PREFIX, PLAN_PREFIX, SOURCE as PLAN_SOURCE

    ledger, _ = _seed(tmp_path / "world.sqlite")
    proposal, _ = _audit(ledger)
    payload, _ = derive_world_life_plan(state=ledger.project(), world_id=WORLD_ID,
                                      proposal_id=proposal.proposal_id, owner_actor_ref=ACTOR)
    value = payload.model_dump(mode="json")
    actor = ACTOR
    source = PLAN_SOURCE
    if tamper == "origin":
        value.pop("world_intent_origin")
    elif tamper == "actor":
        actor = "actor:other"
    elif tamper == "intention":
        value["plan"]["activity_kind"] = "forged_performed_activity"
    elif tamper == "policy":
        value["policy_refs"] = []
    else:
        source = "world-v2:chat-life-intent"
    raw = event(EVENT_PREFIX + payload.plan.plan_id.removeprefix(PLAN_PREFIX), "ActivityPlanned", value)
    raw = raw.model_copy(update={"actor": actor, "source": source,
                                 "causation_id": payload.world_intent_origin.proposal_event_ref})
    before = ledger.project()
    with pytest.raises(ValueError):
        commit(ledger, [raw])
    assert ledger.project() == before
    ledger.close()


@pytest.mark.parametrize("version", ["world-v2-proposals.1", "world-v2-proposals.3"])
def test_old_registry_cannot_carry_new_world_intent(tmp_path, version):
    ledger, _ = _seed(tmp_path / "world.sqlite")
    proposal, _ = _audit(ledger)
    raw = proposal.model_dump(mode="json")
    raw["schema_registry_version"] = version
    with pytest.raises(ValueError, match="world_life_intent requires proposal registry .4"):
        DecisionProposal.model_validate_json(json.dumps(raw))
    ledger.close()


def test_capability_exposes_only_exact_participating_settlement_sources(tmp_path):
    from companion_daemon.world_v2.world_life_intent_contract import world_life_intent_capability

    ledger, _ = _seed(tmp_path / "world.sqlite")
    source = ledger.lookup_event_commit(SOURCE)[0]
    clock = ledger.lookup_event_commit("clock-life")[0]
    value = world_life_intent_capability(state=ledger.project(), source_events=(clock, source, source), owner_actor_ref=ACTOR)
    assert value == {"contract": "world-life-intent-capability.1", "source_event_refs": [SOURCE], "execution_scope": "self_directed"}
    assert world_life_intent_capability(state=ledger.project(), source_events=(source,), owner_actor_ref="actor:other") is None
    assert world_life_intent_capability(state=ledger.project(), source_events=(clock,), owner_actor_ref=ACTOR) is None
    assert world_life_intent_capability(state=ledger.project(), source_events=(), owner_actor_ref=ACTOR) is None
    ledger.close()


def test_no_durable_role_decision_cannot_create_a_plan(tmp_path):
    from companion_daemon.world_v2.world_life_intent_runtime import WorldLifeIntentRuntime

    ledger, _ = _seed(tmp_path / "world.sqlite")
    before = ledger.project()
    with pytest.raises(ValueError):
        WorldLifeIntentRuntime(ledger=ledger, owner_actor_ref=ACTOR).accept(
            world_id=WORLD_ID, audit_cursor=_cursor(ledger), proposal_id="proposal:absent-role",
        )
    assert ledger.project() == before
    ledger.close()


def test_cas_conflict_leaves_original_audit_retryable_without_partial_plan(tmp_path, monkeypatch):
    from companion_daemon.world_v2.errors import ConcurrencyConflict
    from companion_daemon.world_v2.world_life_intent_runtime import WorldLifeIntentRuntime

    path = tmp_path / "world.sqlite"
    ledger, issuer = _seed(path)
    proposal, cursor = _audit(ledger)
    before = ledger.project()
    with monkeypatch.context() as local:
        def conflict(*args, **kwargs):
            raise ConcurrencyConflict("fixture concurrent commit")
        local.setattr(ledger, "commit_at_cursor", conflict)
        with pytest.raises(ConcurrencyConflict):
            WorldLifeIntentRuntime(ledger=ledger, owner_actor_ref=ACTOR).accept(
                world_id=WORLD_ID, audit_cursor=cursor, proposal_id=proposal.proposal_id,
            )
    assert ledger.project() == before
    ledger.close()
    restarted = SQLiteWorldLedger(path=path, world_id=WORLD_ID, accepted_batch_issuer=issuer)
    result = WorldLifeIntentRuntime(ledger=restarted, owner_actor_ref=ACTOR).accept(
        world_id=WORLD_ID, audit_cursor=cursor, proposal_id=proposal.proposal_id,
    )
    assert len(result.event_ids) == 1
    restarted.close()


@pytest.mark.parametrize("field,value", [
    ("location_ref", "library"), ("participant_refs", ["npc:other"]),
    ("execution_scope", "external_action"), ("completed_result", "I wrote a poem"),
])
def test_future_intent_contract_does_not_grant_other_world_effects(field, value):
    from companion_daemon.world_v2.world_life_intent_contract import WorldLifeIntentPayload

    with pytest.raises(ValueError):
        WorldLifeIntentPayload.model_validate_json(json.dumps({
            **INTENT, "actor_ref": ACTOR, "source_event_ref": SOURCE, field: value,
        }))


def test_legacy_planned_payload_omits_new_origin_and_replays_unchanged(tmp_path):
    from companion_daemon.world_v2.life_events import ActivityPlannedPayload

    path = tmp_path / "world.sqlite"
    ledger, issuer = _seed(path)
    before = ledger.export_replay_evidence()
    planned = next(x for x in ledger.project().committed_world_event_refs if x.event_type == "ActivityPlanned")
    raw = ledger.lookup_event_commit(planned.event_id)[0].payload()
    parsed = ActivityPlannedPayload.model_validate_json(json.dumps(raw))
    assert parsed.world_intent_origin is None
    assert "world_intent_origin" not in parsed.model_dump(mode="json")
    ledger.close()
    restarted = SQLiteWorldLedger(path=path, world_id=WORLD_ID, accepted_batch_issuer=issuer)
    assert restarted.export_replay_evidence() == before
    assert restarted.lookup_event_commit(planned.event_id)[0].payload() == raw
    restarted.close()


def test_world_intent_keeps_withheld_source_privacy_floor(tmp_path):
    from companion_daemon.world_v2.world_life_intent_runtime import WorldLifeIntentRuntime

    ledger = SQLiteWorldLedger(path=tmp_path / "world.sqlite", world_id=WORLD_ID,
                              accepted_batch_issuer=AcceptedLedgerBatchIssuer())
    seed_through_proposal(ledger, participant_refs=(ACTOR,), event_visibility="withhold")
    settlement = settlement_batch(participant_refs=(ACTOR,))
    # Only settle the source and open its appraisal. The old fixture's
    # unrelated private Experience must not downgrade this withheld source.
    commit(ledger, [settlement[0], settlement[1], settlement[4]])
    proposal, cursor = _audit(ledger)
    receipt = WorldLifeIntentRuntime(ledger=ledger, owner_actor_ref=ACTOR).accept(
        world_id=WORLD_ID, audit_cursor=cursor, proposal_id=proposal.proposal_id,
    )
    assert ledger.lookup_event_commit(receipt.event_ids[0])[0].payload()["plan"]["privacy_class"] == "withhold"
    ledger.close()
