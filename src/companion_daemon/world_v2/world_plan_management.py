"""Role-selected continuations/revisions using the existing Plan and audit ledger.

No semantic matching: targets are explicit IDs at exact accepted heads. A revision
abandons its predecessor and creates one linked version after its durable role audit.
"""

from types import SimpleNamespace
import json
from .world_life_intent_contract import (
    WorldPlanResponsePayload,
    WorldPlanResponseRecordedPayload,
    WorldLifeIntentPayload,
    WorldLifeIntentOrigin,
)
from .schemas import PlanStateProjection, WorldEvent, EvidenceRef, validate_plan_authority_state
from .world_life_intent_runtime import _digest

SOURCE = "world-v2:world-plan-response"
PREFIX = "event:world-plan-response:"
POLICY = "policy:world-plan-response.1"


def reader_for(ledger, actor_ref, content_store=None):
    from .chat_life_intent_runtime import ChatLifeIntentRuntime, CompositeActivityPlanMaterialReader
    from .day_open_life_intent_runtime import DayOpenLifeIntentRuntime
    from .world_life_intent_runtime import WorldLifeIntentRuntime

    readers = tuple(
        cls(ledger=ledger, owner_actor_ref=actor_ref)
        for cls in (WorldLifeIntentRuntime, ChatLifeIntentRuntime, DayOpenLifeIntentRuntime)
    )
    if content_store is not None:
        from .life_development_runtime import LifeDevelopmentProposalReader

        readers = (
            *readers,
            LifeDevelopmentProposalReader(ledger=ledger, content_store=content_store),
        )
    return CompositeActivityPlanMaterialReader(*readers)


def capability(*, state, actor_ref, source_refs, reader):
    plans = []
    for plan in sorted(
        state.plans,
        key=lambda p: (
            p.authority_origin.accepted_world_revision if p.authority_origin else 0,
            p.plan_id,
        ),
        reverse=True,
    ):
        if (
            any(key == plan.plan_id for key, _ in state.pending_plan_responses)
            or plan.owner_actor_ref != actor_ref
            or plan.status not in {"planned", "active", "paused"}
            or plan.privacy_class == "withhold"
            or plan.authority_origin is None
        ):
            continue
        material = reader.read_for_plan(plan_id=plan.plan_id)
        if material is None:
            continue
        plans.append(
            {
                "plan_id": plan.plan_id,
                "entity_revision": plan.entity_revision,
                "status": plan.status,
                "intention": material.character_intention[:480],
                "intention_truncated": len(material.character_intention) > 480,
                "scheduled_window": plan.scheduled_window.model_dump(mode="json")
                if plan.scheduled_window
                else None,
            }
        )
        if len(plans) == 8:
            break
    if not plans:
        return None
    return {
        "contract": "world-plan-management.1",
        "source_event_refs": list(source_refs),
        "plans": plans,
        "operations": ["continue", "revise", "end"],
    }


def validate_choice(choice, offered):
    if (
        not isinstance(offered, dict)
        or offered.get("contract") != "world-plan-management.1"
        or choice.source_event_ref not in offered["source_event_refs"]
        or choice.plan_id not in {p["plan_id"] for p in offered["plans"]}
    ):
        raise ValueError("plan_response must select one exact offered own Plan and settled source")


def receipt_id(proposal_id):
    return PREFIX + _digest(proposal_id)


def replacement_id(world_id, actor_ref, proposal_id, old_plan_id):
    from .world_life_intent_runtime import PLAN_PREFIX

    return PLAN_PREFIX + _digest([world_id, actor_ref, proposal_id, old_plan_id])


def receipt_payload(proposal_id, change, value):
    return WorldPlanResponseRecordedPayload(
        proposal_id=proposal_id,
        change_id=change.change_id,
        actor_ref=value.actor_ref,
        plan_id=value.plan_id,
        operation=value.operation,
        response_hash=_digest(value.model_dump(mode="json")),
    )


def _derive(*, state, world_id, proposal_id, owner_actor_ref, current=True):
    from .world_stimulus_choice_authority import (
        read_world_stimulus_choice_authority,
        world_stimulus_source_origin,
    )

    authority = read_world_stimulus_choice_authority(
        state=state,
        world_id=world_id,
        proposal_id=proposal_id,
        owner_actor_ref=owner_actor_ref,
        registry_versions=("world-v2-proposals.8",),
        error_type=ValueError,
    )
    changes = [x for x in authority.proposal.proposed_changes if x.kind == "world_plan_response"]
    if len(changes) != 1 or any(
        x.kind == "world_life_intent" for x in authority.proposal.proposed_changes
    ):
        raise ValueError("one explicit existing-Plan choice cannot also create an unrelated Plan")
    change = changes[0]
    value = WorldPlanResponsePayload.model_validate_json(change.payload.canonical_json)
    expected = PlanStateProjection.model_validate_json(value.expected_plan_json)
    head = WorldEvent.model_validate_json(value.head_event_json)
    validate_plan_authority_state(
        (expected,), state.committed_world_event_refs, logical_time=head.logical_time
    )
    origin = expected.authority_origin
    head_ref = next(
        (x for x in state.committed_world_event_refs if x.event_id == head.event_id), None
    )
    if (
        expected.plan_id != value.plan_id
        or expected.owner_actor_ref != owner_actor_ref
        or value.actor_ref != owner_actor_ref
        or expected.privacy_class == "withhold"
        or expected.status not in {"planned", "active", "paused"}
        or origin is None
        or head_ref is None
        or head.world_id != world_id
        or head.payload_hash != head_ref.payload_hash
        or origin.accepted_event_ref != head.event_id
        or origin.accepted_payload_hash != head.payload_hash
        or head_ref.world_revision > authority.audit.evaluated_world_revision
        or change.target_id != value.plan_id
        or change.transition != value.operation
        or change.expected_entity_revision != expected.entity_revision
        or change.policy_refs != (POLICY,)
        or change.evidence_refs != (value.source_event_ref, head.event_id)
    ):
        raise ValueError("plan_response lacks exact actor, source, head or revision authority")
    body = head.payload()
    if head.event_type == "ActivityPlanned":
        declared = PlanStateProjection.model_validate_json(json.dumps(body["plan"]))
        if (
            declared.plan_id != expected.plan_id
            or body["expected_entity_revision"] != 0
            or expected.entity_revision != 1
        ):
            raise ValueError("plan_response creation head is not this original Plan")
    elif (
        body.get("plan_id") != expected.plan_id
        or body.get("expected_entity_revision") != expected.entity_revision - 1
    ):
        raise ValueError("plan_response lifecycle head is not this exact Plan revision")
    live = next((p for p in state.plans if p.plan_id == value.plan_id), None)
    immutable = (
        "plan_id",
        "activity_id",
        "activity_kind",
        "owner_actor_ref",
        "privacy_class",
        "scheduled_window",
        "importance_bp",
        "participant_refs",
        "location_ref",
        "supersedes_plan_id",
        "evidence_refs",
        "goal_ref",
    )
    if live is None or any(getattr(live, key) != getattr(expected, key) for key in immutable):
        raise ValueError("Plan response immutable identity differs from its exact accepted Plan")
    if current and live != expected:
        raise ValueError("plan_response target changed; no stale Plan edit is allowed")
    source, origin_value = world_stimulus_source_origin(
        state=state,
        authority=authority,
        source_event_ref=value.source_event_ref,
        change_id=change.change_id,
        error_type=ValueError,
    )
    return value, change, expected, source, WorldLifeIntentOrigin(**origin_value)


def derive_replacement(*, state, world_id, proposal_id, owner_actor_ref):
    from .world_life_intent_runtime import make_world_life_plan

    value, change, expected, source, origin = _derive(
        state=state,
        world_id=world_id,
        proposal_id=proposal_id,
        owner_actor_ref=owner_actor_ref,
        current=False,
    )
    receipt = next(
        (x for x in state.committed_world_event_refs if x.event_id == receipt_id(proposal_id)), None
    )
    old = next(p for p in state.plans if p.plan_id == value.plan_id)
    if (
        value.operation != "revise"
        or receipt is None
        or receipt.payload_hash
        != _digest(receipt_payload(proposal_id, change, value).model_dump(mode="json"))
        or old.status != "abandoned"
        or old.entity_revision != expected.entity_revision + 1
        or old.terminal_reason_ref != receipt.event_id
    ):
        raise ValueError(
            "replacement requires its exact accepted response and abandoned predecessor"
        )
    intent = WorldLifeIntentPayload(
        **value.replacement.model_dump(mode="json"),
        actor_ref=owner_actor_ref,
        source_event_ref=value.source_event_ref,
    )
    occurrence = next(
        o for o in state.world_occurrences if o.settlement_event_ref == source.event_id
    )
    return make_world_life_plan(
        intent=intent,
        origin=origin,
        source=source,
        world_id=world_id,
        owner_actor_ref=owner_actor_ref,
        visibility=occurrence.visibility,
        supersedes_plan_id=old.plan_id,
        plan_id=replacement_id(world_id, owner_actor_ref, proposal_id, old.plan_id),
    )


def build_events(*, decision, state, model_event, proposal_event):
    """Prospective event values; the ordinary reducers re-prove the complete batch."""
    from .life_events import ActivityTransitionPayload
    from .world_stimulus_choice_authority import (
        WorldStimulusChoiceAuthority,
        world_stimulus_source_origin,
    )
    from .proposal_audit_schemas import RecordedModelResultAudit
    from .world_life_intent_runtime import make_world_life_plan, EVENT_PREFIX, PLAN_PREFIX

    change = next(x for x in decision.proposed_changes if x.kind == "world_plan_response")
    value = WorldPlanResponsePayload.model_validate_json(change.payload.canonical_json)
    plan = PlanStateProjection.model_validate_json(value.expected_plan_json)
    if (
        value.operation != "continue"
        and next((p for p in state.plans if p.plan_id == plan.plan_id), None) != plan
    ):
        raise ValueError("plan_response head changed before atomic audit submission")
    actor = value.actor_ref
    now = state.logical_time

    def event(identifier, kind, payload, cause, source_name=SOURCE):
        from .event_identity import domain_idempotency_key

        body = payload.model_dump(mode="json")
        return WorldEvent.from_payload(
            schema_version="world-v2.1",
            event_id=identifier,
            world_id=state.world_id,
            event_type=kind,
            logical_time=now,
            created_at=now,
            actor=actor,
            source=source_name,
            trace_id=proposal_event.trace_id,
            causation_id=cause,
            correlation_id=proposal_event.correlation_id,
            idempotency_key=domain_idempotency_key(
                event_type=kind, world_id=state.world_id, payload=body
            )
            or identifier,
            payload=body,
        )

    receipt = event(
        receipt_id(decision.proposal_id),
        "WorldPlanResponseRecorded",
        receipt_payload(decision.proposal_id, change, value),
        proposal_event.event_id,
    )
    events = [receipt]
    if value.operation == "continue":
        return tuple(events)
    from .plan_evidence import canonical_plan_evidence_hash

    evidence = (
        EvidenceRef(
            ref_id=plan.plan_id,
            evidence_type="active_plan",
            claim_purpose="life_transition",
            immutable_hash=canonical_plan_evidence_hash(plan),
        ),
    )
    abandoned = event(
        receipt.event_id + ":end",
        "ActivityAbandoned",
        ActivityTransitionPayload(
            change_id=change.change_id,
            transition_id=receipt.event_id + ":end",
            expected_entity_revision=plan.entity_revision,
            evidence_refs=evidence,
            policy_refs=(POLICY,),
            plan_id=plan.plan_id,
            transitioned_at=now,
            reason_ref=receipt.event_id,
            world_plan_response_proposal_id=decision.proposal_id,
        ),
        receipt.event_id,
    )
    events.append(abandoned)
    if value.operation == "revise":
        audit = SimpleNamespace(
            **proposal_event.payload(),
            event_ref=proposal_event.event_id,
            event_payload_hash=proposal_event.payload_hash,
        )
        model = SimpleNamespace(
            **model_event.payload(),
            event_ref=model_event.event_id,
            event_payload_hash=model_event.payload_hash,
        )
        lineage = RecordedModelResultAudit.model_validate_json(
            model.audit_json
        ).character_interior_lineage
        authority = WorldStimulusChoiceAuthority(decision, audit, model, lineage)
        source, origin = world_stimulus_source_origin(
            state=state,
            authority=authority,
            source_event_ref=value.source_event_ref,
            change_id=change.change_id,
            error_type=ValueError,
        )
        intent = WorldLifeIntentPayload(
            **value.replacement.model_dump(mode="json"),
            actor_ref=actor,
            source_event_ref=value.source_event_ref,
        )
        occurrence = next(
            o for o in state.world_occurrences if o.settlement_event_ref == source.event_id
        )
        payload, _ = make_world_life_plan(
            intent=intent,
            origin=WorldLifeIntentOrigin(**origin),
            source=source,
            world_id=state.world_id,
            owner_actor_ref=actor,
            visibility=occurrence.visibility,
            supersedes_plan_id=plan.plan_id,
            plan_id=replacement_id(state.world_id, actor, decision.proposal_id, plan.plan_id),
        )
        from .world_life_intent_runtime import SOURCE as INTENT_SOURCE

        events.append(
            event(
                EVENT_PREFIX + payload.plan.plan_id.removeprefix(PLAN_PREFIX),
                "ActivityPlanned",
                payload,
                proposal_event.event_id,
                INTENT_SOURCE,
            )
        )
    return tuple(events)


def commit_response(*, ledger, decision, audit_events, cursor):
    """Keep the existing separate audit commit, with no interleaving Plan edit.

    No lock is held during model work. Both short commits use the same ledger
    sequence lock; restart recovery reuses the exact paid audit and receipt.
    """
    from .schemas import ProjectionCursor

    with ledger.serialized_commit_sequence():
        existing = [ledger.lookup_event_commit(e.event_id) for e in audit_events]
        if any(existing):
            if not all(existing) or any(
                old[0].payload_hash != new.payload_hash
                for old, new in zip(existing, audit_events, strict=True)
            ):
                raise ValueError("Plan response author audit is incomplete or different")
        else:
            ledger.commit_at_cursor(
                audit_events,
                expected_cursor=cursor,
                commit_id="commit:world-plan-response:audit:" + _digest(decision.proposal_id),
            )
        if ledger.lookup_event_commit(receipt_id(decision.proposal_id)) is not None:
            return
        current = ledger.project()
        effects = build_events(
            decision=decision,
            state=current,
            model_event=audit_events[0],
            proposal_event=audit_events[1],
        )
        ledger.commit_at_cursor(
            effects,
            expected_cursor=ProjectionCursor(
                world_revision=current.world_revision,
                deliberation_revision=current.deliberation_revision,
                ledger_sequence=current.ledger_sequence,
            ),
            commit_id="commit:world-plan-response:effect:" + _digest(decision.proposal_id),
        )


def validate_receipt(*, state, event, payload):
    value, change, *_ = _derive(
        state=state,
        world_id=event.world_id,
        proposal_id=payload.proposal_id,
        owner_actor_ref=payload.actor_ref,
        current=payload.operation != "continue",
    )
    if (
        (
            payload.operation != "continue"
            and (payload.plan_id, payload.proposal_id) not in state.pending_plan_responses
        )
        or event.source != SOURCE
        or event.actor != value.actor_ref
        or event.event_id != receipt_id(payload.proposal_id)
        or payload != receipt_payload(payload.proposal_id, change, value)
        or event.logical_time != state.logical_time
    ):
        raise ValueError("World Plan response receipt differs from its exact role choice")


def validate_end(*, state, event, payload):
    identifier = payload.world_plan_response_proposal_id
    if identifier is None:
        if event.source == SOURCE:
            raise ValueError("World Plan response end lacks role authority")
        return
    value, change, expected, *_ = _derive(
        state=state, world_id=event.world_id, proposal_id=identifier, owner_actor_ref=event.actor
    )
    receipt = next(
        (x for x in state.committed_world_event_refs if x.event_id == receipt_id(identifier)), None
    )
    if (
        value.operation not in {"revise", "end"}
        or receipt is None
        or event.source != SOURCE
        or event.event_type != "ActivityAbandoned"
        or event.event_id != receipt.event_id + ":end"
        or payload.reason_ref != receipt.event_id
        or payload.plan_id != value.plan_id
        or payload.expected_entity_revision != expected.entity_revision
        or payload.change_id != change.change_id
        or payload.policy_refs != (POLICY,)
        or receipt.payload_hash
        != _digest(receipt_payload(identifier, change, value).model_dump(mode="json"))
    ):
        raise ValueError("Plan end is not the selected current role response")


def reserve_response(*, state, world_id, proposal_id):
    """A durable accepted choice owns its head until its exact effect recovers."""
    from .projection_lookup import lookup_first
    from .proposal_envelope import validate_proposal_envelope

    audit = lookup_first(state.proposal_audits, "proposal_id", proposal_id)
    decision = validate_proposal_envelope(json.loads(audit.proposal_json))
    changes = [c for c in decision.proposed_changes if c.kind == "world_plan_response"]
    if not changes:
        return state
    actor = json.loads(changes[0].payload.canonical_json)["actor_ref"]
    value, *_ = _derive(
        state=state, world_id=world_id, proposal_id=proposal_id, owner_actor_ref=actor
    )
    if value.operation == "continue":
        return state
    if any(key == value.plan_id for key, _ in state.pending_plan_responses):
        raise ValueError("Plan already has an accepted response awaiting recovery")
    return state.model_copy(
        update={
            "pending_plan_responses": (*state.pending_plan_responses, (value.plan_id, proposal_id))
        }
    )


def recover_response(*, ledger, audit):
    if ledger.lookup_event_commit(receipt_id(audit.proposal_id)) is not None:
        return
    from .proposal_envelope import validate_proposal_envelope
    from .projection_lookup import lookup_first
    from .schemas import ProjectionCursor

    current = ledger.project()
    model = lookup_first(current.model_result_audits, "model_result_ref", audit.model_result_ref)
    if model is None:
        raise ValueError("Plan response recovery lacks its original paid model audit")
    model_event = ledger.lookup_event_commit(model.event_ref)
    proposal_event = ledger.lookup_event_commit(audit.event_ref)
    if model_event is None or proposal_event is None:
        raise ValueError("Plan response recovery lacks original immutable events")
    commit_response(
        ledger=ledger,
        decision=validate_proposal_envelope(json.loads(audit.proposal_json)),
        audit_events=(model_event[0], proposal_event[0]),
        cursor=ProjectionCursor(
            world_revision=current.world_revision,
            deliberation_revision=current.deliberation_revision,
            ledger_sequence=current.ledger_sequence,
        ),
    )
