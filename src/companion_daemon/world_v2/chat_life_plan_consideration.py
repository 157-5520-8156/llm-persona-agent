"""Initial plan opportunities use real clocks and never imply life outcomes."""

from __future__ import annotations

from datetime import timedelta
import hashlib
import json

from .chat_life_intent_runtime import (
    PLAN_PREFIX,
    derive_chat_life_plan,
    validate_chat_life_plan_event,
)
from .chat_life_plan_consideration_contract import (
    ChatLifePlanConsideration,
    ChatLifePlanOpportunity,
)
from .event_identity import domain_idempotency_key
from .life_events import ActivityPlannedPayload
from .schemas import ProjectionCursor, WorldEvent

SOURCE = "world-v2:chat-life-plan-consideration"


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def consideration_id(plan_event_ref: str, attempt_ordinal: int) -> str:
    return "event:chat-life-plan-consideration:" + _digest([plan_event_ref, attempt_ordinal])


def _next_opportunity(state, *, plan_id: str, initial_due):
    previous = tuple(
        x for x in state.chat_life_plan_considerations if x.opportunity.plan_id == plan_id
    )
    if not previous:
        return 1, initial_due
    if previous[-1].terminal:
        return None
    return previous[-1].opportunity.attempt_ordinal + 1, previous[-1].next_retry_at


def _completion_fields(*, state, opportunity, status, recorded_at):
    if status != "technical_failure":
        return dict(terminal=True, terminal_reason="role_decision", next_retry_at=None)
    if opportunity.attempt_ordinal >= 3:
        return dict(terminal=True, terminal_reason="attempts_exhausted", next_retry_at=None)
    plan = next(x for x in state.plans if x.plan_id == opportunity.plan_id)
    retry_at = recorded_at + timedelta(seconds=(30, 120)[opportunity.attempt_ordinal - 1])
    if retry_at >= plan.scheduled_window.closes_at:
        return dict(terminal=True, terminal_reason="window_expired", next_retry_at=None)
    return dict(terminal=False, terminal_reason=None, next_retry_at=retry_at)


def opportunity_from_state(
    state, *, world_id: str, opportunity: ChatLifePlanOpportunity
) -> ChatLifePlanOpportunity:
    plan = next((x for x in state.plans if x.plan_id == opportunity.plan_id), None)
    source = next(
        (x for x in state.committed_world_event_refs if x.event_id == opportunity.plan_event_ref),
        None,
    )
    if plan is None or source is None:
        raise ValueError("chat_life_plan.original_plan_authority_missing")
    payload, _ = derive_chat_life_plan(
        state=state,
        world_id=world_id,
        proposal_id=opportunity.origin.proposal_id,
        owner_actor_ref=plan.owner_actor_ref,
    )
    if (
        source.event_id != "event:chat-life-intent:" + plan.plan_id.removeprefix(PLAN_PREFIX)
        or source.event_type != "ActivityPlanned"
        or payload.plan.plan_id != plan.plan_id
        or _digest(payload.model_dump(mode="json")) != source.payload_hash
    ):
        raise ValueError("chat_life_plan.original_plan_authority_missing")
    next_opportunity = _next_opportunity(
        state,
        plan_id=plan.plan_id,
        initial_due=max(
            source.logical_time + timedelta(seconds=1), payload.plan.scheduled_window.opens_at
        ),
    )
    if next_opportunity is None:
        raise ValueError("chat_life_plan.already_terminal")
    ordinal, due_at = next_opportunity
    return ChatLifePlanOpportunity(
        plan_id=plan.plan_id,
        plan_event_ref=source.event_id,
        plan_payload_hash=source.payload_hash,
        owner_actor_ref=plan.owner_actor_ref,
        origin=payload.chat_intent_origin,
        due_at=due_at,
        attempt_ordinal=ordinal,
    )


def pending_opportunities(
    ledger, *, owner_actor_ref: str, projection=None
) -> tuple[ChatLifePlanOpportunity, ...]:
    projection = projection if projection is not None else ledger.project()
    resolved = {
        x.opportunity.plan_id for x in projection.chat_life_plan_considerations if x.terminal
    }
    pending = []
    for plan in projection.plans:
        if (
            plan.plan_id in resolved
            or not plan.plan_id.startswith(PLAN_PREFIX)
            or plan.owner_actor_ref != owner_actor_ref
            or plan.status != "planned"
        ):
            continue
        # A closed window belongs to the existing abandon/close opportunity.
        if (
            projection.logical_time is None
            or plan.scheduled_window.closes_at <= projection.logical_time
        ):
            continue
        located = ledger.lookup_event_commit(
            "event:chat-life-intent:" + plan.plan_id.removeprefix(PLAN_PREFIX)
        )
        if located is None:
            continue
        payload = ActivityPlannedPayload.model_validate_json(located[0].payload_json)
        validate_chat_life_plan_event(state=projection, event=located[0], payload=payload)
        ordinal, due_at = _next_opportunity(
            projection,
            plan_id=plan.plan_id,
            initial_due=max(
                located[0].logical_time + timedelta(seconds=1), plan.scheduled_window.opens_at
            ),
        )
        pending.append(
            ChatLifePlanOpportunity(
                plan_id=plan.plan_id,
                plan_event_ref=located[0].event_id,
                plan_payload_hash=located[0].payload_hash,
                owner_actor_ref=owner_actor_ref,
                origin=payload.chat_intent_origin,
                due_at=due_at,
                attempt_ordinal=ordinal,
            )
        )
    return tuple(sorted(pending, key=lambda x: (x.due_at, x.plan_event_ref)))


def reduce_consideration(state, event: WorldEvent):
    value = ChatLifePlanConsideration.model_validate_json(event.payload_json)
    expected = opportunity_from_state(state, world_id=event.world_id, opportunity=value.opportunity)
    clock = next(
        (x for x in state.committed_world_event_refs if x.event_id == value.clock_event_ref), None
    )
    recording_clock = next(
        (
            x
            for x in state.committed_world_event_refs
            if x.event_id == value.recording_clock_event_ref
        ),
        None,
    )
    if any(
        (
            value.opportunity != expected,
            event.source != SOURCE,
            event.actor != expected.owner_actor_ref,
            value.consideration_ref
            != consideration_id(expected.plan_event_ref, expected.attempt_ordinal),
            event.event_id != value.consideration_ref,
            event.causation_id != expected.plan_event_ref,
            event.logical_time != state.logical_time,
            value.recorded_at != state.logical_time,
            value.considered_at > value.recorded_at,
            value.considered_at < expected.due_at,
            recording_clock is None
            or recording_clock.event_type != "ClockAdvanced"
            or recording_clock.logical_time != state.logical_time
            or recording_clock.payload_hash != value.recording_clock_payload_hash,
            clock is None
            or clock.event_type != "ClockAdvanced"
            or clock.payload_hash != value.clock_payload_hash
            or clock.logical_time != value.considered_at,
            any(
                x.opportunity.plan_id == expected.plan_id
                and x.opportunity.attempt_ordinal == expected.attempt_ordinal
                for x in state.chat_life_plan_considerations
            ),
        )
    ):
        raise ValueError("chat_life_plan.consideration_binding_invalid")
    completion = _completion_fields(
        state=state, opportunity=expected, status=value.status, recorded_at=value.recorded_at
    )
    if any(getattr(value, key) != item for key, item in completion.items()):
        raise ValueError("chat_life_plan.retry_binding_invalid")
    if value.status == "technical_failure":
        if (
            not value.failure_code
            or value.character_interior_model_result is not None
            or value.character_decision_json is not None
            or value.lifecycle_proposal_ref is not None
            or value.lifecycle_proposal_json is not None
        ):
            raise ValueError("chat_life_plan.technical_result_invalid")
    else:
        if (
            value.failure_code is not None
            or value.character_interior_model_result is None
            or value.character_decision_json is None
        ):
            raise ValueError("chat_life_plan.role_decision_missing")
        from .character_interior.audit import recorded_character_interior_model_result
        from .character_interior.contracts import InnerDecision
        from .character_interior.run_result import CausalOpportunityRuntime

        result = InnerDecision.model_validate_json(value.character_decision_json)
        decision = result.decision
        source_refs = tuple(sorted((clock.event_id, expected.plan_event_ref)))
        source_revision = max(
            index + 1
            for index, binding in enumerate(state.committed_world_event_refs)
            if binding.event_id in source_refs
        )
        if (
            not source_revision
            <= result.cursor.world_revision
            <= len(state.committed_world_event_refs)
            or result.status != "decided"
            or result.actor_ref != expected.owner_actor_ref
            or not isinstance(decision, dict)
            or decision.get("contract") != "character-interior-purpose-decision.1"
            or decision.get("purpose") != "activity_lifecycle_choice"
            or not isinstance(decision.get("capability_ref"), str)
            or tuple(decision.get("source_refs", ())) != source_refs
        ):
            raise ValueError("chat_life_plan.role_decision_binding_invalid")
        identity = CausalOpportunityRuntime(
            world_id=event.world_id,
            actor_ref=expected.owner_actor_ref,
            purpose="activity_lifecycle_choice",
        ).identity_for_refs(source_refs, epoch=clock.event_id)
        expected_model = recorded_character_interior_model_result(
            result,
            purpose="activity_lifecycle_choice",
            subject_ref=result.opportunity_ref,
            trigger_ref=clock.event_id,
            capability_ref=decision["capability_ref"],
            route_tier="flash",
            route_reason_code="activity_lifecycle.character_choice",
            router_version="character-interior-activity-lifecycle-capability.2",
            causal_opportunity=identity,
        )
        if value.character_interior_model_result != expected_model:
            raise ValueError("chat_life_plan.role_decision_binding_invalid")
        role_payload = decision.get("payload")
        if (
            not isinstance(role_payload, dict)
            or role_payload.get("contract") != "character-interior-activity-lifecycle-choice.1"
        ):
            raise ValueError("chat_life_plan.role_decision_binding_invalid")
        selected = role_payload.get("decision")
        if (
            value.status == "declined"
            and (
                selected != "no_op"
                or value.lifecycle_proposal_ref is not None
                or value.lifecycle_proposal_json is not None
            )
        ) or (
            value.status == "selected"
            and (selected != "select" or value.lifecycle_proposal_ref is None)
        ):
            raise ValueError("chat_life_plan.role_result_mismatch")
        if value.status == "selected":
            from .activity_lifecycle_contract import ActivityLifecycleProposalRecordedPayload

            proposal = ActivityLifecycleProposalRecordedPayload.model_validate_json(
                value.lifecycle_proposal_json or "{}"
            )
            binding = next(
                (
                    x
                    for x in state.proposal_revisions
                    if x.proposal_event_ref == value.lifecycle_proposal_ref
                ),
                None,
            )
            if (
                binding is None
                or binding.proposal_event_payload_hash != _digest(proposal.model_dump(mode="json"))
                or proposal.character_interior_model_result != value.character_interior_model_result
                or proposal.opening_token != decision["payload"]["selected_token"]
            ):
                raise ValueError("chat_life_plan.selected_proposal_missing")
    return state.model_copy(
        update={"chat_life_plan_considerations": (*state.chat_life_plan_considerations, value)}
    )


def record_consideration(
    *,
    ledger,
    opportunity,
    clock_event_ref,
    status,
    model_result=None,
    decision_json=None,
    lifecycle_proposal_ref=None,
    failure_code=None,
):
    projection = ledger.project()
    identifier = consideration_id(opportunity.plan_event_ref, opportunity.attempt_ordinal)
    existing = ledger.lookup_event_commit(identifier)
    if existing is not None:
        return existing[1]
    clock = next(x for x in projection.committed_world_event_refs if x.event_id == clock_event_ref)
    recording_clock = next(
        x
        for x in reversed(projection.committed_world_event_refs)
        if x.event_type == "ClockAdvanced" and x.logical_time == projection.logical_time
    )
    value = ChatLifePlanConsideration(
        consideration_ref=identifier,
        opportunity=opportunity,
        clock_event_ref=clock.event_id,
        clock_payload_hash=clock.payload_hash,
        considered_at=clock.logical_time,
        recorded_at=projection.logical_time,
        recording_clock_event_ref=recording_clock.event_id,
        recording_clock_payload_hash=recording_clock.payload_hash,
        status=status,
        **_completion_fields(
            state=projection,
            opportunity=opportunity,
            status=status,
            recorded_at=projection.logical_time,
        ),
        character_interior_model_result=model_result,
        character_decision_json=decision_json,
        lifecycle_proposal_ref=lifecycle_proposal_ref,
        lifecycle_proposal_json=None
        if lifecycle_proposal_ref is None
        else ledger.lookup_event_commit(lifecycle_proposal_ref)[0].payload_json,
        failure_code=failure_code,
    )
    payload = value.model_dump(mode="json")
    original = ledger.lookup_event_commit(opportunity.plan_event_ref)[0]
    event = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id=identifier,
        world_id=ledger.world_id,
        event_type="ChatLifePlanConsiderationRecorded",
        logical_time=projection.logical_time,
        created_at=projection.logical_time,
        actor=opportunity.owner_actor_ref,
        source=SOURCE,
        trace_id=original.trace_id,
        causation_id=original.event_id,
        correlation_id=original.correlation_id,
        idempotency_key=domain_idempotency_key(
            event_type="ChatLifePlanConsiderationRecorded",
            world_id=ledger.world_id,
            payload=payload,
        ),
        payload=payload,
    )
    return ledger.commit_at_cursor(
        (event,),
        expected_cursor=ProjectionCursor(
            world_revision=projection.world_revision,
            deliberation_revision=projection.deliberation_revision,
            ledger_sequence=projection.ledger_sequence,
        ),
        commit_id="commit:" + identifier,
    )
