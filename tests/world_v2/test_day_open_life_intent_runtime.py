"""Clock and original role audit authority, through public SQLite recording."""

from datetime import timedelta
import hashlib
import json
from types import SimpleNamespace

import pytest

from character_interior import canonical_inner_decision
from companion_daemon.world_v2.character_interior.audit import recorded_character_interior_model_result
from companion_daemon.world_v2.character_interior.run_result import CausalOpportunityRuntime
from companion_daemon.world_v2.day_open_life_intent_contract import canonical_json, day_open_opportunity_ref, digest
from companion_daemon.world_v2.day_open_life_intent_runtime import (
    DayOpenLifeIntentRuntime, derive_day_open_life_plan, materialize_day_open_proposal,
    validate_day_open_life_plan_event,
)
from companion_daemon.world_v2.life_ecology_activity import ActivityOpeningCatalog
from companion_daemon.world_v2.proposal_audit_schemas import ProposalRecordedV2Payload
from companion_daemon.world_v2.schemas import ProjectionCursor
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_life_development_runtime import _seed_clock
from test_life_projection import WORLD_ID, commit, event

ACTOR = "actor:companion"
INTENT = {
    "execution_scope": "self_directed", "intention": "想留一点时间整理我自己的想法。",
    "start_after_seconds": 15, "duration_seconds": 120, "importance_bp": 6000,
}


def _cursor(ledger):
    state = ledger.project()
    return ProjectionCursor(world_revision=state.world_revision,
                            deliberation_revision=state.deliberation_revision,
                            ledger_sequence=state.ledger_sequence)


def _seed(path):
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    return ledger, _seed_clock(ledger, event_id="clock:z-first")


def _choice(ledger, first, *, selected=None, ordinal=1, identity="first", choose=True,
            actor=ACTOR, mutate_cap=None):
    selected = selected or first
    state = ledger.project()
    clock_refs = {x.event_id: x for x in state.committed_world_event_refs}
    first_ref, selected_ref = clock_refs[first.event_id], clock_refs[selected.event_id]
    day = first.logical_time.date().isoformat()
    day_ref = day_open_opportunity_ref(world_id=WORLD_ID, actor_ref=actor, day_key=day,
                                       first_clock_ref=first.event_id)
    catalog = ActivityOpeningCatalog(owner_actor_ref=actor).openings_for(
        projection=state, wake_event_ref=selected.event_id,
    )
    assert catalog.status == "no_openings"
    cap = {
        "contract": "character-interior-activity-lifecycle-capability.3",
        "catalog_version": catalog.catalog_version, "catalog_hash": catalog.catalog_hash,
        "offered_tokens": [], "openings": [], "self_directed_intent": {
            "contract": "day-open-life-intent-capability.1", "execution_scope": "self_directed",
            "opportunity_ref": day_ref, "day_key": day, "timezone_name": "UTC",
            "first_clock_ref": first.event_id, "first_clock_world_revision": first_ref.world_revision,
            "first_clock_payload_hash": first_ref.payload_hash,
            "selected_clock_ref": selected.event_id,
            "selected_clock_world_revision": selected_ref.world_revision,
            "selected_clock_payload_hash": selected_ref.payload_hash, "attempt_ordinal": ordinal,
        },
    }
    if mutate_cap:
        mutate_cap(cap)
    refs = tuple(sorted({first.event_id, selected.event_id}))
    causal = CausalOpportunityRuntime(world_id=WORLD_ID, actor_ref=actor,
                                     purpose="activity_lifecycle_choice").identity_for_refs(
        refs, epoch=f"{day_ref}:attempt:{ordinal}",
    )
    opportunity = SimpleNamespace(opportunity_ref=causal.opportunity_ref, actor_ref=actor,
                                  cursor=_cursor(ledger), source_refs=refs)
    payload = {"contract": "character-interior-activity-lifecycle-choice.2",
               "decision": "self_directed_intent" if choose else "no_op"}
    if choose:
        payload["life_intent"] = INTENT
    result = canonical_inner_decision(opportunity, identity=identity, decision={
        "contract": "character-interior-purpose-decision.1", "purpose": "activity_lifecycle_choice",
        "capability_ref": "capability:day-open:" + digest(cap),
        "capability_payload_hash": "sha256:" + digest(cap), "source_refs": list(refs),
        "payload": payload,
    })
    return result, cap, causal


def _record(ledger, result, cap, causal):
    proposal = materialize_day_open_proposal(result, cap, world_id=WORLD_ID)
    model = recorded_character_interior_model_result(
        result, purpose="activity_lifecycle_choice", subject_ref=result.opportunity_ref,
        trigger_ref=proposal.trigger_ref, capability_ref=result.decision["capability_ref"],
        route_tier="flash", route_reason_code="activity_lifecycle.day_open_intent",
        router_version="character-interior-activity-lifecycle-capability.3",
        proposal_hash=proposal.proposal_hash, causal_opportunity=causal,
    )
    shared = {key: getattr(model, key) for key in (
        "model_result_ref", "deliberation_result_id", "proposal_hash", "model_call_id", "attempt_id",
        "capsule_id", "trigger_ref", "evaluated_world_revision",
    )}
    recorded = ProposalRecordedV2Payload(
        **shared, proposal_id=proposal.proposal_id, proposal_kind="decision",
        proposal_json=canonical_json(proposal.model_dump(mode="json")),
    )
    now = ledger.project().logical_time
    commit(ledger, [
        event("model:" + result.inner_turn_id, "ModelResultRecorded", model.model_dump(mode="json"), at=now),
        event("proposal:" + result.inner_turn_id, "ProposalRecorded", recorded.model_dump(mode="json"), at=now),
    ])
    return proposal, _cursor(ledger)


def _accept(ledger, proposal, cursor):
    return DayOpenLifeIntentRuntime(ledger=ledger, owner_actor_ref=ACTOR).accept(
        world_id=WORLD_ID, audit_cursor=cursor, proposal_id=proposal.proposal_id,
    )


@pytest.mark.parametrize("delayed", [False, True])
def test_original_paid_role_creates_one_private_plan_and_cold_recovers(tmp_path, delayed):
    path = tmp_path / "day.sqlite"
    ledger, first = _seed(path)
    result, cap, causal = _choice(ledger, first)
    if delayed:
        _seed_clock(ledger, event_id="clock:after-paid", logical_time=first.logical_time + timedelta(seconds=60),
                    logical_time_from=first.logical_time)
    proposal, cursor = _record(ledger, result, cap, causal)
    receipt = _accept(ledger, proposal, cursor)
    plan, = ledger.project().plans
    assert plan.privacy_class == "private" and plan.owner_actor_ref == ACTOR
    assert plan.location_ref is None and plan.participant_refs == () and plan.status == "planned"
    assert plan.scheduled_window.opens_at == first.logical_time + timedelta(seconds=15)
    runtime = DayOpenLifeIntentRuntime(ledger=ledger, owner_actor_ref=ACTOR)
    material = runtime.read_for_plan(plan_id=plan.plan_id)
    assert material.character_intention == INTENT["intention"]
    assert material.origin.evaluated_cursor.model_dump() == result.cursor.model_dump()
    assert material.origin.selected_at == first.logical_time
    assert ledger.project().experiences == ()
    ledger.close()
    reopened = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    assert _accept(reopened, proposal, cursor) == receipt
    assert reopened.project().plans == (plan,)
    assert reopened.rebuild().plans == (plan,)
    reopened.close()


def test_retry_binds_both_clocks_without_moving_original_day(tmp_path):
    ledger, first = _seed(tmp_path / "retry.sqlite")
    selected = _seed_clock(ledger, event_id="clock:a-retry", logical_time=first.logical_time + timedelta(seconds=30),
                           logical_time_from=first.logical_time)
    result, cap, causal = _choice(ledger, first, selected=selected, ordinal=2)
    proposal, cursor = _record(ledger, result, cap, causal)
    _accept(ledger, proposal, cursor)
    plan, = ledger.project().plans
    origin = DayOpenLifeIntentRuntime(ledger=ledger, owner_actor_ref=ACTOR).read_for_plan(plan_id=plan.plan_id).origin
    assert origin.first_clock_ref == first.event_id and origin.source_event_ref == selected.event_id
    assert origin.attempt_ordinal == 2 and origin.selected_at == selected.logical_time
    assert {x.ref_id for x in plan.evidence_refs} == {first.event_id, selected.event_id}
    ledger.close()


def test_same_local_day_cannot_gain_second_plan_by_replacing_first_clock(tmp_path):
    ledger, first = _seed(tmp_path / "once.sqlite")
    one = _choice(ledger, first)
    second = _seed_clock(ledger, event_id="clock:second", logical_time=first.logical_time + timedelta(seconds=1),
                         logical_time_from=first.logical_time)
    two = _choice(ledger, second, identity="second")
    proposal1, cursor1 = _record(ledger, *one)
    proposal2, cursor2 = _record(ledger, *two)
    _accept(ledger, proposal1, cursor1)
    with pytest.raises(ValueError, match="effect_identity_conflict"):
        _accept(ledger, proposal2, cursor2)
    assert len(ledger.project().plans) == 1
    ledger.close()


@pytest.mark.parametrize("mutate", [
    lambda c: c.update(catalog_hash="0" * 64),
    lambda c: c["self_directed_intent"].update(first_clock_payload_hash="0" * 64,
                                                selected_clock_payload_hash="0" * 64),
])
def test_recorded_role_cannot_upgrade_false_catalog_or_clock_proof(tmp_path, mutate):
    ledger, first = _seed(tmp_path / "bad.sqlite")
    result, cap, causal = _choice(ledger, first, mutate_cap=mutate)
    proposal, cursor = _record(ledger, result, cap, causal)
    with pytest.raises(ValueError, match="(catalog|clock_source)_invalid"):
        _accept(ledger, proposal, cursor)
    assert ledger.project().plans == ()
    ledger.close()


def test_no_op_and_wrong_scope_cannot_materialize_a_plan(tmp_path):
    ledger, first = _seed(tmp_path / "noop.sqlite")
    result, cap, _ = _choice(ledger, first, choose=False)
    assert materialize_day_open_proposal(result, cap, world_id=WORLD_ID) is None
    cap["offered_tokens"] = ["not-empty"]
    with pytest.raises(ValueError):
        materialize_day_open_proposal(result, cap, world_id=WORLD_ID)
    ledger.close()


@pytest.mark.parametrize("old", [".1", ".2", ".3", ".4", ".5"])
def test_old_registries_do_not_gain_day_open_plan_permission(tmp_path, old):
    from companion_daemon.world_v2.proposal_envelope import validate_proposal_envelope

    ledger, first = _seed(tmp_path / "registry.sqlite")
    result, cap, _ = _choice(ledger, first)
    value = materialize_day_open_proposal(result, cap, world_id=WORLD_ID).model_dump(mode="json")
    value["schema_registry_version"] = "world-v2-proposals" + old
    with pytest.raises(ValueError):
        validate_proposal_envelope(value)
    ledger.close()


def test_reducer_rejects_proposal_rewrite_and_plan_location_upgrade(tmp_path):
    ledger, first = _seed(tmp_path / "tamper.sqlite")
    result, cap, causal = _choice(ledger, first)
    proposal, cursor = _record(ledger, result, cap, causal)
    state = ledger.project()
    payload, _ = derive_day_open_life_plan(state=state, world_id=WORLD_ID,
                                          proposal_id=proposal.proposal_id, owner_actor_ref=ACTOR)
    altered = payload.model_copy(update={"plan": payload.plan.model_copy(update={"location_ref": "place:library"})})
    forged = event("forged", "ActivityPlanned", altered.model_dump(mode="json"), at=first.logical_time).model_copy(
        update={"actor": ACTOR, "source": "world-v2:day-open-life-intent"},
    )
    with pytest.raises(ValueError, match="plan_authority_mismatch"):
        validate_day_open_life_plan_event(state=state, event=forged, payload=altered)
    audit = state.proposal_audits[0]
    value = json.loads(audit.proposal_json)
    typed = json.loads(value["proposed_changes"][0]["payload"]["canonical_json"])
    typed["intention"] = "host invented replacement"
    value["proposed_changes"][0]["payload"]["canonical_json"] = canonical_json(typed)
    changed = state.model_copy(update={"proposal_audits": (audit.model_copy(update={"proposal_json": canonical_json(value)}),)})
    with pytest.raises(ValueError):
        derive_day_open_life_plan(state=changed, world_id=WORLD_ID,
                                  proposal_id=proposal.proposal_id, owner_actor_ref=ACTOR)
    assert ledger.project().plans == ()
    ledger.close()


@pytest.mark.parametrize("field", ["decision_hash", "snapshot_hash", "private_self_lineage_hash",
                                  "causal_actor_ref", "decision_context"])
def test_complete_original_model_proof_is_required_even_if_outer_hash_is_recomputed(tmp_path, field):
    ledger, first = _seed(tmp_path / "model-proof.sqlite")
    result, cap, causal = _choice(ledger, first)
    proposal, _ = _record(ledger, result, cap, causal)
    state = ledger.project()
    model = state.model_result_audits[0]
    audit = json.loads(model.audit_json)
    if field == "decision_context":
        audit["decision_context"]["deliberation_revision"] += 1
    elif field == "snapshot_hash":
        audit["character_interior_lineage"][field] = "1" * 64
        audit["character_interior_lineage"]["snapshot_id"] = "inner-life-snapshot:sha256:" + "1" * 64
    else:
        audit["character_interior_lineage"][field] = (
            "actor:other" if field == "causal_actor_ref" else "sha256:" + "1" * 64
        )
    audit_json = canonical_json(audit)
    changed = model.model_copy(update={"audit_json": audit_json,
                                       "audit_hash": hashlib.sha256(audit_json.encode()).hexdigest()})
    changed = changed.model_copy(update={"event_payload_hash": digest(
        changed.model_dump(mode="json", exclude={"event_ref", "event_payload_hash"}),
    )})
    state = state.model_copy(update={"model_result_audits": (changed,)})
    with pytest.raises(ValueError):
        derive_day_open_life_plan(state=state, world_id=WORLD_ID,
                                  proposal_id=proposal.proposal_id, owner_actor_ref=ACTOR)
    assert ledger.project().plans == ()
    ledger.close()


def test_other_actor_and_old_activity_purpose_do_not_inherit_clock_authority(tmp_path):
    ledger, first = _seed(tmp_path / "purpose.sqlite")
    result, cap, causal = _choice(ledger, first)
    proposal, cursor = _record(ledger, result, cap, causal)
    with pytest.raises(ValueError, match="actor_mismatch"):
        DayOpenLifeIntentRuntime(ledger=ledger, owner_actor_ref="actor:other").accept(
            world_id=WORLD_ID, audit_cursor=cursor, proposal_id=proposal.proposal_id,
        )
    decision = dict(result.decision)
    decision["payload"] = {"contract": "character-interior-activity-lifecycle-choice.1",
                            "decision": "self_directed_intent", "life_intent": INTENT}
    with pytest.raises(ValueError, match="role_contract_invalid"):
        materialize_day_open_proposal(result.model_copy(update={"decision": decision}), cap, world_id=WORLD_ID)
    decision["payload"]["contract"] = "character-interior-activity-lifecycle-choice.2"
    decision["purpose"] = "world_stimulus_appraisal"
    with pytest.raises(ValueError, match="role_capability_binding_invalid"):
        materialize_day_open_proposal(result.model_copy(update={"decision": decision}), cap, world_id=WORLD_ID)
    assert ledger.project().plans == ()
    ledger.close()
