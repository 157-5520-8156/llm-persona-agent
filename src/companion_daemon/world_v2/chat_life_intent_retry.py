"""Provider-free acceptance failure journal for one audited life facet.

This records infrastructure failure, never a character decision. CAS races get
at most two delayed retries; invalid authority terminalizes this facet alone.
"""

from __future__ import annotations

from datetime import timedelta
import json

from .chat_life_intent_contract import ChatLifeIntentFailure, LifeIntentPayload
from .chat_life_intent_runtime import SOURCE, _digest, chat_life_plan_id
from .event_identity import domain_idempotency_key
from .proposal_envelope import DecisionProposal, validate_proposal_envelope
from .schemas import ProjectionCursor, WorldEvent
from .unified_inbound_decision import inspect_unified_inbound_decision

RETRY_DELAYS_SECONDS = (30, 120)


def failure_for(state, proposal_id: str):
    return next((x for x in state.chat_life_intent_failures if x.proposal_id == proposal_id), None)


def acceptance_is_due(state, proposal_id: str) -> bool:
    failure = failure_for(state, proposal_id)
    return failure is None or (
        not failure.terminal
        and state.logical_time is not None
        and state.logical_time >= failure.next_retry_at
    )


def _authority(state, proposal_id: str):
    audit = next((x for x in state.proposal_audits if x.proposal_id == proposal_id), None)
    if audit is None:
        raise ValueError("chat_life_intent.failure_proposal_missing")
    proposal = validate_proposal_envelope(json.loads(audit.proposal_json))
    if not isinstance(proposal, DecisionProposal) or proposal.proposal_hash != audit.proposal_hash:
        raise ValueError("chat_life_intent.failure_proposal_binding_invalid")
    change = inspect_unified_inbound_decision(proposal).life_intent
    if change is None:
        raise ValueError("chat_life_intent.failure_facet_missing")
    return audit, proposal, change


def reduce_acceptance_failure(state, event: WorldEvent):
    payload = ChatLifeIntentFailure.model_validate_json(event.payload_json)
    audit, proposal, change = _authority(state, payload.proposal_id)
    intent = LifeIntentPayload.model_validate_json(change.payload.canonical_json)
    prior = failure_for(state, payload.proposal_id)
    ordinal = 1 if prior is None else prior.retry_ordinal + 1
    terminal = payload.failure_code != "cursor_conflict" or ordinal > len(RETRY_DELAYS_SECONDS)
    next_retry = (
        None
        if terminal
        else event.logical_time + timedelta(seconds=RETRY_DELAYS_SECONDS[ordinal - 1])
    )
    expected_id = "event:chat-life-intent-failure:" + _digest(
        [event.world_id, audit.event_ref, change.change_id, ordinal]
    )
    if any(
        (
            event.source != SOURCE,
            event.actor != payload.actor_ref,
            event.actor != intent.actor_ref,
            event.causation_id != audit.event_ref,
            event.event_id != expected_id,
            payload.failure_event_ref != expected_id,
            payload.proposal_event_ref != audit.event_ref,
            payload.proposal_payload_hash != audit.event_payload_hash,
            payload.change_id != change.change_id,
            payload.change_hash != _digest(change.model_dump(mode="json")),
            payload.plan_id != chat_life_plan_id(event.world_id, proposal),
            payload.evaluated_world_revision != len(state.committed_world_event_refs),
            payload.retry_ordinal != ordinal,
            payload.terminal != terminal,
            (payload.reason_code == "cursor_conflict")
            != (payload.failure_code == "cursor_conflict"),
            payload.failed_at != state.logical_time,
            event.logical_time != state.logical_time,
            payload.next_retry_at != next_retry,
            prior is not None and (prior.terminal or prior.next_retry_at > event.logical_time),
            any(x.plan_id == payload.plan_id for x in state.plans)
            and payload.reason_code != "effect_identity_conflict",
        )
    ):
        raise ValueError("chat_life_intent.failure_authority_invalid")
    retained = tuple(
        x for x in state.chat_life_intent_failures if x.proposal_id != payload.proposal_id
    )
    return state.model_copy(update={"chat_life_intent_failures": (*retained, payload)})


def record_acceptance_failure(*, ledger, proposal_id: str, actor_ref: str, failure_code: str):
    """Append exactly one failed attempt at the current cursor.

    If this audit write itself loses CAS, the caller receives that technical
    error; no successful acceptance or durable failure is claimed.
    """
    projection = ledger.project()
    if not acceptance_is_due(projection, proposal_id):
        return failure_for(projection, proposal_id)
    audit, proposal, change = _authority(projection, proposal_id)
    plan_id = chat_life_plan_id(ledger.world_id, proposal)
    if (
        any(x.plan_id == plan_id for x in projection.plans)
        and failure_code != "chat_life_intent.effect_identity_conflict"
    ):
        return None  # A concurrent accept won; it already closed this effect.
    if projection.logical_time is None:
        raise ValueError("chat_life_intent.failure_clock_missing")
    previous = failure_for(projection, proposal_id)
    ordinal = 1 if previous is None else previous.retry_ordinal + 1
    allowed = set(ChatLifeIntentFailure.model_fields["reason_code"].annotation.__args__)
    reason = failure_code.removeprefix("chat_life_intent.")
    if reason not in allowed:
        reason = (
            "proposal_authority_invalid"
            if failure_code.startswith("decision_proposal_authority.")
            else "validation_failure"
        )
    code = "cursor_conflict" if reason == "cursor_conflict" else "authority_invalid"
    terminal = code != "cursor_conflict" or ordinal > len(RETRY_DELAYS_SECONDS)
    identifier = "event:chat-life-intent-failure:" + _digest(
        [ledger.world_id, audit.event_ref, change.change_id, ordinal]
    )
    payload = ChatLifeIntentFailure(
        failure_event_ref=identifier,
        proposal_id=proposal_id,
        proposal_event_ref=audit.event_ref,
        proposal_payload_hash=audit.event_payload_hash,
        change_id=change.change_id,
        change_hash=_digest(change.model_dump(mode="json")),
        plan_id=plan_id,
        actor_ref=actor_ref,
        evaluated_world_revision=projection.world_revision,
        retry_ordinal=ordinal,
        failure_code=code,
        reason_code=reason,
        failed_at=projection.logical_time,
        terminal=terminal,
        next_retry_at=None
        if terminal
        else projection.logical_time + timedelta(seconds=RETRY_DELAYS_SECONDS[ordinal - 1]),
    )
    source = ledger.lookup_event_commit(audit.event_ref)[0]
    value = payload.model_dump(mode="json")
    event = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id=identifier,
        world_id=ledger.world_id,
        event_type="ChatLifeIntentAcceptanceFailed",
        logical_time=projection.logical_time,
        created_at=projection.logical_time,
        actor=actor_ref,
        source=SOURCE,
        trace_id=source.trace_id,
        causation_id=source.event_id,
        correlation_id=source.correlation_id,
        idempotency_key=domain_idempotency_key(
            event_type="ChatLifeIntentAcceptanceFailed", world_id=ledger.world_id, payload=value
        ),
        payload=value,
    )
    ledger.commit_at_cursor(
        (event,),
        expected_cursor=ProjectionCursor(
            world_revision=projection.world_revision,
            deliberation_revision=projection.deliberation_revision,
            ledger_sequence=projection.ledger_sequence,
        ),
        commit_id="commit:" + identifier,
    )
    return payload
