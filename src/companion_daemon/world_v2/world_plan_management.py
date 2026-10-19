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
from .role_life_intent_reader import RoleLifeIntentActivityReader

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
    return CompositeActivityPlanMaterialReader(*readers, RolePlanResponseReader(ledger=ledger))


def capability(*, state, actor_ref, source_refs, reader):
    plans = []
    pending = {key for key, _ in state.pending_plan_responses}
    candidates = (
        p
        for p in state.plans
        if p.plan_id not in pending
        and p.owner_actor_ref == actor_ref
        and p.status in {"planned", "active", "paused"}
        and p.privacy_class != "withhold"
        and p.authority_origin is not None
    )
    for plan in sorted(
        candidates,
        key=lambda p: (p.authority_origin.accepted_world_revision, p.plan_id),
        reverse=True,
    ):
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
    from collections.abc import Mapping

    validate_capability(offered)
    if (
        not isinstance(offered, Mapping)
        or offered.get("contract") != "world-plan-management.1"
        or (
            hasattr(choice, "source_event_ref")
            and choice.source_event_ref not in offered["source_event_refs"]
        )
        or choice.plan_id not in {p["plan_id"] for p in offered["plans"]}
    ):
        raise ValueError("plan_response must select one exact offered own Plan and settled source")


def validate_capability(offered):
    from collections.abc import Mapping
    from .day_open_life_intent_contract import ExistingDayOpenPlan

    if (
        not isinstance(offered, Mapping)
        or set(offered) != {"contract", "source_event_refs", "plans", "operations"}
        or offered["contract"] != "world-plan-management.1"
        or list(offered["operations"]) != ["continue", "revise", "end"]
        or not 1 <= len(offered["plans"]) <= 8
        or len({p["plan_id"] for p in offered["plans"]}) != len(offered["plans"])
    ):
        raise ValueError("Plan management capability is invalid")
    for plan in offered["plans"]:
        ExistingDayOpenPlan.model_validate_json(json.dumps(dict(plan)))


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


_TARGET_MUTABLE = (
    "entity_revision",
    "status",
    "last_transitioned_at",
    "terminal_reason_ref",
    "authority_origin",
)


def _target_snapshot(plan):
    body = plan.model_dump(mode="json")
    return {
        "contract": "plan-target-revision.1",
        "plan_id": plan.plan_id,
        "state_hash": _digest(body),
        **{key: body[key] for key in _TARGET_MUTABLE},
    }


def _expected_target(state, value):
    if value.author_purpose is None:
        return PlanStateProjection.model_validate_json(value.expected_plan_json)
    target = json.loads(value.expected_plan_json)
    if (
        set(target) != {"contract", "plan_id", "state_hash", *_TARGET_MUTABLE}
        or target["contract"] != "plan-target-revision.1"
    ):
        raise ValueError("Role Plan operation requires its exact bounded target pin")
    current = next((p for p in state.plans if p.plan_id == target["plan_id"]), None)
    if current is None:
        raise ValueError("Role Plan target is unavailable")
    body = current.model_dump(mode="json")
    body.update({key: target[key] for key in _TARGET_MUTABLE})
    expected = PlanStateProjection.model_validate_json(json.dumps(body))
    if _digest(expected.model_dump(mode="json")) != target["state_hash"]:
        raise ValueError("Role Plan target hash differs from its exact accepted version")
    return expected


def _derive(*, state, world_id, proposal_id, owner_actor_ref, current=True):
    from .world_stimulus_choice_authority import (
        read_world_stimulus_choice_authority,
        world_stimulus_source_origin,
    )

    audit = next((x for x in state.proposal_audits if x.proposal_id == proposal_id), None)
    if audit is None:
        raise ValueError("proposal_missing")
    generic = (
        json.loads(audit.proposal_json).get("schema_registry_version") == "world-v2-proposals.9"
    )
    authority = (
        _read_role_authority(state, world_id, proposal_id, owner_actor_ref)
        if generic
        else read_world_stimulus_choice_authority(
            state=state,
            world_id=world_id,
            proposal_id=proposal_id,
            owner_actor_ref=owner_actor_ref,
            registry_versions=("world-v2-proposals.8",),
            error_type=ValueError,
        )
    )
    changes = [x for x in authority.proposal.proposed_changes if x.kind == "world_plan_response"]
    if len(changes) != 1 or any(
        x.kind == "world_life_intent" for x in authority.proposal.proposed_changes
    ):
        raise ValueError("one explicit existing-Plan choice cannot also create an unrelated Plan")
    change = changes[0]
    value = WorldPlanResponsePayload.model_validate_json(change.payload.canonical_json)
    expected = _expected_target(state, value)
    origin = expected.authority_origin
    head_ref = next(
        (
            x
            for x in state.committed_world_event_refs
            if origin and x.event_id == origin.accepted_event_ref
        ),
        None,
    )
    if head_ref is None:
        raise ValueError("Plan response lacks its committed accepted head")
    head = head_ref if generic else WorldEvent.model_validate_json(value.head_event_json)
    validate_plan_authority_state(
        (expected,), state.committed_world_event_refs, logical_time=head.logical_time
    )
    if (
        expected.plan_id != value.plan_id
        or expected.owner_actor_ref != owner_actor_ref
        or value.actor_ref != owner_actor_ref
        or expected.privacy_class == "withhold"
        or expected.status not in {"planned", "active", "paused"}
        or origin is None
        or head_ref is None
        or (not generic and head.world_id != world_id)
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
    if not generic:
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
    if generic:
        source, origin_value = _role_origin(state, authority, value, change)
        return value, change, expected, source, SimpleNamespace(**origin_value)
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
    if value.author_purpose is not None:
        return _role_replacement(value, change, old, origin, receipt, owner_actor_ref, world_id)
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
    plan = _expected_target(state, value)
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
        if value.author_purpose is not None:
            authority = _read_role_authority(state, state.world_id, decision.proposal_id, actor)
            _, origin_value = _role_origin(state, authority, value, change)
            payload, _ = _role_replacement(
                value,
                change,
                plan,
                SimpleNamespace(**origin_value),
                SimpleNamespace(
                    event_id=receipt.event_id,
                    payload_hash=receipt.payload_hash,
                    world_revision=state.world_revision + 1,
                ),
                actor,
                state.world_id,
            )
            events.append(
                event(
                    ROLE_EVENT_PREFIX + payload.plan.plan_id.removeprefix(ROLE_PLAN_PREFIX),
                    "ActivityPlanned",
                    payload,
                    proposal_event.event_id,
                )
            )
            return tuple(events)
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
    if value.author_purpose == "life_development_choice":
        metadata = json.loads(value.author_context_json or "{}")
        prior = accepted_life_response(state, value.source_event_ref, metadata["life_proposal_id"])
        if prior is not None and prior.proposal_id != proposal_id:
            raise ValueError(
                "Life source already has an accepted Plan choice awaiting its exact effect"
            )
    if value.operation == "continue" and value.author_purpose is None:
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


def _read_role_authority(state, world_id, proposal_id, actor_ref):
    from .proposal_envelope import DecisionProposal, validate_proposal_envelope
    from .proposal_audit_schemas import RecordedModelResultAudit

    audit = next(x for x in state.proposal_audits if x.proposal_id == proposal_id)
    proposal = validate_proposal_envelope(json.loads(audit.proposal_json))
    model = next(
        (x for x in state.model_result_audits if x.model_result_ref == audit.model_result_ref), None
    )
    if (
        not isinstance(proposal, DecisionProposal)
        or proposal.proposal_hash != audit.proposal_hash
        or model is None
    ):
        raise ValueError("Role Plan operation lacks its exact decision/model audit")
    if (
        any(
            getattr(model, key) != getattr(audit, key)
            for key in (
                "proposal_hash",
                "model_call_id",
                "attempt_id",
                "capsule_id",
                "deliberation_result_id",
                "trigger_ref",
                "evaluated_world_revision",
            )
        )
        or model.attempt_index != model.attempt_count - 1
    ):
        raise ValueError("Role Plan operation changed its accepted author binding")
    recorded = RecordedModelResultAudit.model_validate_json(model.audit_json)
    if recorded.status not in {
        "proposal_validated",
        "main_invalid_recovered",
        "main_timeout_recovered",
        "main_exception_recovered",
    }:
        raise ValueError("A failed model attempt is not an accepted Plan choice")
    lineage = recorded.character_interior_lineage
    if (
        lineage is None
        or lineage.author_model_call_id != model.model_call_id
        or (
            lineage.purpose != "inbound_turn"
            and (
                lineage.inner_turn_id != model.attempt_id
                or lineage.snapshot_hash != model.capsule_id
            )
        )
        or lineage.causal_world_id not in {None, world_id}
        or lineage.causal_actor_ref not in {None, actor_ref}
    ):
        raise ValueError("Role Plan operation lacks original protagonist authorship")
    return SimpleNamespace(proposal=proposal, audit=audit, model=model, lineage=lineage)


def _role_origin(state, authority, value, change):
    if value.author_purpose == "world_stimulus_appraisal":
        from .world_stimulus_choice_authority import (
            read_world_stimulus_choice_authority,
            world_stimulus_source_origin,
        )

        proven = read_world_stimulus_choice_authority(
            state=state,
            world_id=authority.lineage.causal_world_id,
            proposal_id=authority.audit.proposal_id,
            owner_actor_ref=value.actor_ref,
            registry_versions=("world-v2-proposals.9",),
            error_type=ValueError,
        )
        return world_stimulus_source_origin(
            state=state,
            authority=proven,
            source_event_ref=value.source_event_ref,
            change_id=change.change_id,
            error_type=ValueError,
        )
    source = next(
        (x for x in state.committed_world_event_refs if x.event_id == value.source_event_ref), None
    )
    expected_type = (
        "ObservationRecorded" if value.author_purpose == "inbound_turn" else "ClockAdvanced"
    )
    declared = next(
        (x for x in authority.proposal.evidence_refs if x.ref_id == value.source_event_ref), None
    )
    if (
        authority.lineage.purpose != value.author_purpose
        or source is None
        or source.event_id != authority.audit.trigger_ref
        or source.event_type != expected_type
        or source.world_revision > authority.audit.evaluated_world_revision
        or declared is None
        or declared.evidence_kind != "committed_world_event"
        or declared.source_world_revision != source.world_revision
        or declared.immutable_hash != "sha256:" + source.payload_hash
    ):
        raise ValueError("Role Plan operation changed its pinned source/purpose authority")
    clock = max(
        (
            r
            for r in state.committed_world_event_refs
            if r.world_revision <= authority.audit.evaluated_world_revision
            and r.event_type in {"WorldStarted", "ClockAdvanced"}
        ),
        key=lambda x: x.world_revision,
        default=None,
    )
    if value.author_purpose == "inbound_turn":
        clock = next(
            (
                r
                for r in state.committed_world_event_refs
                if r.world_revision == authority.audit.evaluated_world_revision
            ),
            None,
        )
    if clock is None or clock.logical_time < source.logical_time:
        raise ValueError("Role Plan operation has no original selection clock")
    a, m, lineage = authority.audit, authority.model, authority.lineage
    return source, dict(
        proposal_id=a.proposal_id,
        proposal_event_ref=a.event_ref,
        proposal_payload_hash=a.event_payload_hash,
        proposal_hash=a.proposal_hash,
        change_id=change.change_id,
        evaluated_world_revision=a.evaluated_world_revision,
        selected_at=clock.logical_time,
        model_result_ref=m.model_result_ref,
        model_result_payload_hash=m.event_payload_hash,
        model_call_id=m.model_call_id,
        inner_turn_id=lineage.inner_turn_id,
        snapshot_id=lineage.snapshot_id,
        snapshot_hash=lineage.snapshot_hash,
    )


ROLE_PLAN_PREFIX = "plan:role-life-intent:"
ROLE_EVENT_PREFIX = "event:role-life-intent:"


def _role_replacement(value, change, old, origin, receipt, actor_ref, world_id):
    from datetime import timedelta
    from .life_events import ActivityPlannedPayload
    from .schemas import DueWindow

    identity = _digest([world_id, actor_ref, origin.proposal_id, old.plan_id])
    intent = value.replacement
    starts = origin.selected_at + timedelta(seconds=intent.start_after_seconds)
    evidence = EvidenceRef(
        ref_id=receipt.event_id,
        evidence_type="committed_world_event",
        claim_purpose="future_plan",
        source_world_revision=receipt.world_revision,
        immutable_hash=receipt.payload_hash,
    )
    plan = PlanStateProjection(
        plan_id=ROLE_PLAN_PREFIX + identity,
        activity_id="activity:role-life-intent:" + identity,
        entity_revision=1,
        activity_kind="self_directed." + _digest(intent.intention)[:24],
        evidence_refs=(evidence,),
        status="planned",
        importance_bp=intent.importance_bp,
        scheduled_window=DueWindow(
            opens_at=starts, closes_at=starts + timedelta(seconds=intent.duration_seconds)
        ),
        owner_actor_ref=actor_ref,
        privacy_class=old.privacy_class,
        supersedes_plan_id=old.plan_id,
    )
    return ActivityPlannedPayload(
        change_id=change.change_id,
        transition_id=ROLE_EVENT_PREFIX + identity,
        expected_entity_revision=0,
        evidence_refs=(evidence,),
        policy_refs=(POLICY,),
        plan=plan,
        role_response_proposal_id=origin.proposal_id,
    ), intent


def validate_role_replacement(*, state, event, payload):
    if payload.role_response_proposal_id is None:
        if event.event_type == "ActivityPlanned" and (
            event.source == SOURCE or payload.plan.plan_id.startswith(ROLE_PLAN_PREFIX)
        ):
            raise ValueError("Role replacement lacks its original decision")
        return
    expected, _ = derive_replacement(
        state=state,
        world_id=event.world_id,
        proposal_id=payload.role_response_proposal_id,
        owner_actor_ref=event.actor,
    )
    authority = _read_role_authority(
        state, event.world_id, payload.role_response_proposal_id, event.actor
    )
    if (
        event.source != SOURCE
        or payload != expected
        or event.event_id != ROLE_EVENT_PREFIX + payload.plan.plan_id.removeprefix(ROLE_PLAN_PREFIX)
        or event.causation_id != authority.audit.event_ref
    ):
        raise ValueError("Role replacement differs from exact accepted role operation")


def response_input(*, ledger, projection, actor_ref, reader, source_refs):
    from .chat_life_intent_contract import PlanResponseInput

    public = capability(
        state=projection, actor_ref=actor_ref, source_refs=source_refs, reader=reader
    )
    if public is None:
        return None
    selected = {p["plan_id"] for p in public["plans"]}
    return PlanResponseInput(
        actor_ref=actor_ref,
        world_revision=projection.world_revision,
        deliberation_revision=projection.deliberation_revision,
        ledger_sequence=projection.ledger_sequence,
        capability_json=json.dumps(
            public, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ),
        target_jsons=tuple(
            json.dumps(
                _target_snapshot(p), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            for p in projection.plans
            if p.plan_id in selected
        ),
    )


def prepare_role_change(*, bounds, choice, source_ref, purpose, context_json=None):
    from .proposal_envelope import TypedChange, CanonicalTypedPayload
    from .chat_life_intent_contract import PlanResponseDraft

    choice = PlanResponseDraft.model_validate_json(choice.model_dump_json())
    public = json.loads(bounds.capability_json)
    if (
        choice.plan_id not in {p["plan_id"] for p in public["plans"]}
        or source_ref not in public["source_event_refs"]
    ):
        raise ValueError("Role Plan operation selected an unoffered target/source")
    target = next(
        json.loads(x) for x in bounds.target_jsons if json.loads(x)["plan_id"] == choice.plan_id
    )
    identity = _digest(
        [bounds.model_dump(mode="json"), choice.model_dump(mode="json"), source_ref, purpose]
    )
    value = WorldPlanResponsePayload(
        **choice.model_dump(mode="json"),
        actor_ref=bounds.actor_ref,
        source_event_ref=source_ref,
        expected_plan_json=json.dumps(
            target, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ),
        head_event_json="{}",
        author_purpose=purpose,
        author_context_json=context_json,
    )
    return TypedChange(
        change_id="change:role-plan-response:" + identity,
        kind="world_plan_response",
        target_id=target["plan_id"],
        transition=choice.operation,
        expected_entity_revision=target["entity_revision"],
        evidence_refs=(source_ref, target["authority_origin"]["accepted_event_ref"]),
        policy_refs=(POLICY,),
        payload=CanonicalTypedPayload.from_value(
            payload_schema="world_plan_response.v1", value=value.model_dump(mode="json")
        ),
    )


def role_proposal(*, ledger, bounds, choice, source_ref, purpose, proposal_id, context_json=None):
    from .proposal_envelope import InteriorDecisionProposal, ProposalEvidenceRef

    change = prepare_role_change(
        bounds=bounds,
        choice=choice,
        source_ref=source_ref,
        purpose=purpose,
        context_json=context_json,
    )
    source = ledger.lookup_event_commit(source_ref)[0]
    head = ledger.lookup_event_commit(change.evidence_refs[1])[0]
    projection = ledger.project()
    refs = {r.event_id: r for r in projection.committed_world_event_refs}
    return InteriorDecisionProposal(
        proposal_id=proposal_id,
        trigger_ref=source_ref,
        schema_registry_version="world-v2-proposals.9",
        evaluated_world_revision=bounds.world_revision,
        evidence_refs=tuple(
            ProposalEvidenceRef(
                ref_id=e.event_id,
                evidence_kind="committed_world_event",
                source_world_revision=refs[e.event_id].world_revision,
                immutable_hash="sha256:" + e.payload_hash,
            )
            for e in dict.fromkeys((source, head))
        ),
        proposed_changes=(change,),
        confidence=10000,
        brief_rationale="An explicit operation on an existing own Plan.",
        behavior_tendency=None,
        stance=None,
        display_strategy=None,
        interior_reflection_depth="brief",
        timing_choice="silent",
    )


def _reader_origin(payload, projection, actor_ref, *, world_id=None):
    from .chat_life_intent_contract import RolePlanResponseOrigin

    origin = _derive(
        state=projection,
        world_id=world_id or projection.world_id,
        proposal_id=payload.role_response_proposal_id,
        owner_actor_ref=actor_ref,
        current=False,
    )[-1]
    return RolePlanResponseOrigin(
        **{
            k: v
            for k, v in vars(origin).items()
            if k in RolePlanResponseOrigin.model_fields and k != "contract"
        }
    )


class RolePlanResponseReader(RoleLifeIntentActivityReader):
    """The existing intention reader, with the shared response source verifier."""

    def __init__(self, *, ledger):
        super().__init__(
            ledger=ledger,
            plan_prefix=ROLE_PLAN_PREFIX,
            event_prefix=ROLE_EVENT_PREFIX,
            origin_field=_reader_origin,
            derive=derive_replacement,
            validate=validate_role_replacement,
        )

    def read_planned_plan(self, **kwargs):
        return self._read(status="planned", **kwargs)

    def read_active_plan(self, **kwargs):
        return self._read(status="active", **kwargs)

    def read_completed_plan(self, **kwargs):
        return self._read(status="completed", **kwargs)

    def read_for_plan(self, *, plan_id):
        if not plan_id.startswith(ROLE_PLAN_PREFIX):
            return None
        located = self._ledger.lookup_event_commit(
            ROLE_EVENT_PREFIX + plan_id.removeprefix(ROLE_PLAN_PREFIX)
        )
        if located is None:
            return None
        from .life_events import ActivityPlannedPayload

        projection = self._ledger.project()
        payload = ActivityPlannedPayload.model_validate_json(located[0].payload_json)
        validate_role_replacement(state=projection, event=located[0], payload=payload)
        origin = _reader_origin(payload, projection, payload.plan.owner_actor_ref)
        _, intent = derive_replacement(
            state=projection,
            world_id=projection.world_id,
            proposal_id=origin.proposal_id,
            owner_actor_ref=payload.plan.owner_actor_ref,
        )
        return SimpleNamespace(character_intention=intent.intention, origin=origin)


def role_audit_events(
    *,
    result,
    proposal,
    source,
    logical_time,
    route_reason_code=None,
    router_version="role-plan-response.1",
):
    from .character_interior.audit import recorded_character_interior_model_result
    from .proposal_audit_schemas import ProposalRecordedV2Payload
    from .event_identity import domain_idempotency_key

    from .chat_life_intent_contract import PlanResponseDraft

    body = result.decision["payload"]
    authored = (body.get("completion", body)).get("plan_response")
    if authored is None:
        raise ValueError("Paid role did not author a Plan operation")
    chosen = PlanResponseDraft.model_validate_json(json.dumps(authored))
    declared = WorldPlanResponsePayload.model_validate_json(
        next(
            c.payload.canonical_json
            for c in proposal.proposed_changes
            if c.kind == "world_plan_response"
        )
    )
    if (
        chosen.model_dump(mode="json")
        != {
            k: v
            for k, v in declared.model_dump(mode="json").items()
            if k in {"operation", "plan_id", "replacement"}
        }
        or declared.actor_ref != result.actor_ref
    ):
        raise ValueError("Plan adapter changed the original paid role's choice")
    purpose = result.decision["purpose"]
    model = recorded_character_interior_model_result(
        result,
        purpose=purpose,
        subject_ref=result.opportunity_ref,
        trigger_ref=source.event_id,
        capability_ref=result.decision["capability_ref"],
        route_tier="flash",
        route_reason_code=route_reason_code or "role_plan_response." + purpose,
        router_version=router_version,
        proposal_hash=proposal.proposal_hash,
    )
    audit = ProposalRecordedV2Payload(
        proposal_id=proposal.proposal_id,
        proposal_kind="decision",
        model_result_ref=model.model_result_ref,
        deliberation_result_id=model.deliberation_result_id,
        model_call_id=model.model_call_id,
        attempt_id=model.attempt_id,
        capsule_id=model.capsule_id,
        trigger_ref=source.event_id,
        evaluated_world_revision=result.cursor.world_revision,
        proposal_json=json.dumps(
            proposal.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        proposal_hash=proposal.proposal_hash,
    )
    events = []
    for kind, name, body in [
        ("ModelResultRecorded", "model", model),
        ("ProposalRecorded", "proposal", audit),
    ]:
        body = body.model_dump(mode="json")
        events.append(
            WorldEvent.from_payload(
                schema_version="world-v2.1",
                event_id="event:role-plan-response:" + name + ":" + _digest(proposal.proposal_id),
                world_id=source.world_id,
                event_type=kind,
                logical_time=logical_time,
                created_at=source.created_at,
                actor=result.actor_ref,
                source="world-v2:role-plan-audit",
                trace_id=source.trace_id,
                causation_id=source.event_id,
                correlation_id=source.correlation_id,
                idempotency_key=domain_idempotency_key(
                    event_type=kind, world_id=source.world_id, payload=body
                ),
                payload=body,
            )
        )
    return tuple(events)


def submit_role_choice(*, ledger, result, choice, reader, offered, source_ref):
    """Audit/recover one paid own-Plan choice; no model or source re-authoring."""
    identifier = "proposal:role-plan-response:" + _digest(result.model_dump(mode="json"))
    current = ledger.project()
    audit = next((a for a in current.proposal_audits if a.proposal_id == identifier), None)
    if audit is not None:
        recover_response(ledger=ledger, audit=audit)
        return identifier
    pinned = ledger.project_at(result.cursor)
    bounds = response_input(
        ledger=ledger,
        projection=pinned,
        actor_ref=result.actor_ref,
        reader=reader,
        source_refs=offered["source_event_refs"],
    )
    if bounds is None or json.loads(bounds.capability_json) != offered:
        raise ValueError("Role Plan operation differs from its original offered targets")
    proposal = role_proposal(
        ledger=ledger,
        bounds=bounds,
        choice=choice,
        source_ref=source_ref,
        purpose=result.decision["purpose"],
        proposal_id=identifier,
    )
    source = ledger.lookup_event_commit(source_ref)[0]
    events = role_audit_events(
        result=result, proposal=proposal, source=source, logical_time=current.logical_time
    )
    commit_response(ledger=ledger, decision=proposal, audit_events=events, cursor=result.cursor)
    return identifier


def audit_context_metadata(envelope):
    if envelope.get("schema_registry_version") == "world-v2-proposals.9":
        changes = [c for c in envelope["proposed_changes"] if c["kind"] == "world_plan_response"]
        if len(changes) != 1:
            raise ValueError("Shared Plan audit needs one original role operation")
        return json.loads(
            json.loads(changes[0]["payload"]["canonical_json"])["author_context_json"]
        )
    return json.loads(envelope["response_text"])


def recover_pending(*, ledger):
    # Accepted typed work only; no scan of raw failures or new author requests.
    for _, proposal_id in tuple(ledger.project().pending_plan_responses):
        audit = next(a for a in ledger.project().proposal_audits if a.proposal_id == proposal_id)
        if json.loads(audit.proposal_json).get("schema_registry_version") == "world-v2-proposals.9":
            recover_response(ledger=ledger, audit=audit)


def accepted_life_response(projection, wake_event_ref, life_proposal_id):
    from .projection_lookup import lookup_first

    def matches(audit):
        value = json.loads(audit.proposal_json)
        if value.get("schema_registry_version") != "world-v2-proposals.9":
            return False
        change = next(
            (c for c in value["proposed_changes"] if c["kind"] == "world_plan_response"), None
        )
        if change is None:
            return False
        payload = json.loads(change["payload"]["canonical_json"])
        return (
            payload.get("author_purpose") == "life_development_choice"
            and json.loads(payload.get("author_context_json") or "{}").get("life_proposal_id")
            == life_proposal_id
        )

    return lookup_first(
        projection.proposal_audits, "trigger_ref", wake_event_ref, predicate=matches
    )
