"""Accept one explicit inbound life intent and expose its original meaning.

The recorded DecisionProposal is the durable work journal. No text classifier,
World Author, location write, NPC participation or outcome generator is here.
The existing activity lifecycle remains the sole later start/complete authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json

from .chat_life_intent_contract import ChatLifeIntentOrigin, LifeIntentPayload
from .event_identity import domain_idempotency_key
from .life_events import ActivityPlannedPayload
from .proposal_audit_schemas import RecordedModelResultAudit
from .proposal_envelope import DecisionProposal, validate_proposal_envelope
from .role_life_intent_reader import RoleLifeIntentActivityReader
from .schemas import DueWindow, EvidenceRef, PlanStateProjection, ProjectionCursor, WorldEvent
from .unified_inbound_decision import inspect_unified_inbound_decision

SOURCE = "world-v2:chat-life-intent"
PLAN_PREFIX = "plan:chat-life-intent:"


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def chat_life_plan_id(world_id: str, proposal: DecisionProposal) -> str:
    change = inspect_unified_inbound_decision(proposal).life_intent
    if change is None:
        raise ChatLifeIntentError("explicit_intent_missing")
    return PLAN_PREFIX + _digest([world_id, change.payload.value()["source_observation_ref"]])


class ChatLifeIntentError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = "chat_life_intent." + code
        super().__init__(self.code)


def derive_chat_life_plan(*, state, world_id: str, proposal_id: str, owner_actor_ref: str):
    """Reverse-check the role/model/source chain before deriving any Plan bytes.

    Accepts either current reducer state or a ledger projection, so replay and
    live acceptance enforce exactly the same semantic-free authority checks.
    """
    audit = next((x for x in state.proposal_audits if x.proposal_id == proposal_id), None)
    if audit is None:
        raise ChatLifeIntentError("proposal_missing")
    proposal = validate_proposal_envelope(json.loads(audit.proposal_json))
    if not isinstance(proposal, DecisionProposal) or proposal.proposal_hash != audit.proposal_hash:
        raise ChatLifeIntentError("proposal_binding_invalid")
    change = inspect_unified_inbound_decision(proposal).life_intent
    if change is None or change.expected_entity_revision != 0:
        raise ChatLifeIntentError("explicit_intent_missing")
    intent = LifeIntentPayload.model_validate_json(change.payload.canonical_json)
    if intent.actor_ref != owner_actor_ref:
        raise ChatLifeIntentError("actor_mismatch")
    model = next(
        (x for x in state.model_result_audits if x.model_result_ref == audit.model_result_ref), None
    )
    if model is None or any(
        (
            model.proposal_hash != audit.proposal_hash,
            model.model_call_id != audit.model_call_id,
            model.attempt_id != audit.attempt_id,
            model.capsule_id != audit.capsule_id,
            model.deliberation_result_id != audit.deliberation_result_id,
            model.trigger_ref != audit.trigger_ref,
            model.evaluated_world_revision != audit.evaluated_world_revision,
            model.attempt_index != model.attempt_count - 1,
        )
    ):
        raise ChatLifeIntentError("model_binding_invalid")
    recorded = RecordedModelResultAudit.model_validate_json(model.audit_json)
    lineage = recorded.character_interior_lineage
    if (
        lineage is None
        or lineage.purpose != "inbound_turn"
        or lineage.author_model_call_id != model.model_call_id
    ):
        raise ChatLifeIntentError("inner_turn_authority_missing")
    observation = next(
        (
            x
            for x in state.message_observations
            if x.observation_id == intent.source_observation_ref
        ),
        None,
    )
    source = next(
        (x for x in state.committed_world_event_refs if x.event_id == audit.trigger_ref), None
    )
    if (
        observation is None
        or source is None
        or any(
            (
                source.event_type != "ObservationRecorded",
                source.world_revision != observation.world_revision,
                source.payload_hash != observation.event_payload_hash,
            )
        )
    ):
        raise ChatLifeIntentError("source_observation_invalid")
    declared = next(
        (x for x in proposal.evidence_refs if x.ref_id == observation.observation_id), None
    )
    if declared is None or any(
        (
            declared.evidence_kind != "observed_message",
            declared.source_world_revision != observation.world_revision,
            declared.immutable_hash != "sha256:" + observation.event_payload_hash,
            change.evidence_refs != (observation.observation_id,),
        )
    ):
        raise ChatLifeIntentError("source_binding_invalid")
    clock_source = next(
        (
            x
            for x in state.committed_world_event_refs
            if x.world_revision == audit.evaluated_world_revision
        ),
        None,
    )
    if clock_source is None or clock_source.world_revision < observation.world_revision:
        raise ChatLifeIntentError("selection_clock_unavailable")
    origin = ChatLifeIntentOrigin(
        proposal_id=audit.proposal_id,
        proposal_event_ref=audit.event_ref,
        proposal_payload_hash=audit.event_payload_hash,
        proposal_hash=audit.proposal_hash,
        change_id=change.change_id,
        evaluated_world_revision=audit.evaluated_world_revision,
        selected_at=clock_source.logical_time,
        model_result_ref=model.model_result_ref,
        model_result_payload_hash=model.event_payload_hash,
        model_call_id=model.model_call_id,
        inner_turn_id=lineage.inner_turn_id,
        snapshot_id=lineage.snapshot_id,
        snapshot_hash=lineage.snapshot_hash,
    )
    evidence = EvidenceRef(
        ref_id=observation.observation_id,
        evidence_type="observed_message",
        claim_purpose="future_plan",
        source_world_revision=observation.world_revision,
        immutable_hash=observation.event_payload_hash,
    )
    identity = _digest([world_id, intent.source_observation_ref])
    starts = origin.selected_at + timedelta(seconds=intent.start_after_seconds)
    plan = PlanStateProjection(
        plan_id=PLAN_PREFIX + identity,
        activity_id="activity:chat-life-intent:" + identity,
        entity_revision=1,
        activity_kind="self_directed." + _digest(intent.intention)[:24],
        evidence_refs=(evidence,),
        status="planned",
        importance_bp=intent.importance_bp,
        scheduled_window=DueWindow(
            opens_at=starts, closes_at=starts + timedelta(seconds=intent.duration_seconds)
        ),
        participant_refs=(),
        location_ref=None,
        privacy_class="private",
        owner_actor_ref=owner_actor_ref,
    )
    return ActivityPlannedPayload(
        change_id=change.change_id,
        transition_id="transition:chat-life-intent:" + identity,
        expected_entity_revision=0,
        evidence_refs=(evidence,),
        plan=plan,
        chat_intent_origin=origin,
    ), intent


def validate_chat_life_plan_event(
    *, state, event: WorldEvent, payload: ActivityPlannedPayload
) -> None:
    """Untrusted ledger writes cannot forge or detach this authority family."""
    claims_family = (
        event.source == SOURCE
        or event.event_id.startswith("event:chat-life-intent:")
        or payload.plan.plan_id.startswith(PLAN_PREFIX)
        or payload.chat_intent_origin is not None
    )
    if not claims_family:
        return
    origin = payload.chat_intent_origin
    if origin is None:
        raise ChatLifeIntentError("origin_missing")
    expected, _ = derive_chat_life_plan(
        state=state,
        world_id=event.world_id,
        proposal_id=origin.proposal_id,
        owner_actor_ref=event.actor,
    )
    identity = expected.plan.plan_id.removeprefix(PLAN_PREFIX)
    if any(
        (
            event.source != SOURCE,
            event.event_id != "event:chat-life-intent:" + identity,
            event.causation_id != origin.proposal_event_ref,
            payload != expected,
        )
    ):
        raise ChatLifeIntentError("accepted_effect_mismatch")


@dataclass(frozen=True, slots=True)
class ChatLifePlanMaterial:
    character_intention: str
    origin: ChatLifeIntentOrigin


class ChatLifeIntentRuntime:
    def __init__(self, *, ledger, owner_actor_ref: str) -> None:
        if not owner_actor_ref:
            raise ValueError("chat life intent requires an owner")
        self.ledger = ledger
        self._owner = owner_actor_ref

    def accept(self, *, world_id: str, audit_cursor: ProjectionCursor, proposal_id: str):
        from .decision_proposal_authority import DecisionProposalAuthorityReader

        reader = DecisionProposalAuthorityReader(ledger=self.ledger)
        authority = reader.read(
            reader.pin(world_id=world_id, cursor=audit_cursor, proposal_id=proposal_id)
        )
        projection = self.ledger.project()
        payload, intent = derive_chat_life_plan(
            state=projection,
            world_id=world_id,
            proposal_id=proposal_id,
            owner_actor_ref=self._owner,
        )
        identity = payload.plan.plan_id.removeprefix(PLAN_PREFIX)
        event_id = "event:chat-life-intent:" + identity
        existing = self.ledger.lookup_event_commit(event_id)
        if existing is not None:
            persisted = ActivityPlannedPayload.model_validate_json(existing[0].payload_json)
            validate_chat_life_plan_event(state=projection, event=existing[0], payload=persisted)
            _, original_intent = derive_chat_life_plan(
                state=projection,
                world_id=world_id,
                proposal_id=persisted.chat_intent_origin.proposal_id,
                owner_actor_ref=self._owner,
            )
            if original_intent != intent:
                raise ChatLifeIntentError("effect_identity_conflict")
            return existing[1]
        if projection.logical_time is None:
            raise ChatLifeIntentError("clock_unavailable")
        source_event = self.ledger.lookup_event_commit(authority.audit.event_ref)[0]
        value = payload.model_dump(mode="json")
        event = WorldEvent.from_payload(
            schema_version="world-v2.1",
            event_id=event_id,
            world_id=world_id,
            event_type="ActivityPlanned",
            logical_time=projection.logical_time,
            created_at=source_event.created_at,
            actor=self._owner,
            source=SOURCE,
            trace_id=source_event.trace_id,
            causation_id=source_event.event_id,
            correlation_id=source_event.correlation_id,
            idempotency_key=domain_idempotency_key(
                event_type="ActivityPlanned", world_id=world_id, payload=value
            ),
            payload=value,
        )
        validate_chat_life_plan_event(state=projection, event=event, payload=payload)
        return self.ledger.commit_at_cursor(
            (event,),
            expected_cursor=ProjectionCursor(
                world_revision=projection.world_revision,
                deliberation_revision=projection.deliberation_revision,
                ledger_sequence=projection.ledger_sequence,
            ),
            commit_id="commit:chat-life-intent:" + identity,
        )

    def record_failure(self, *, proposal_id: str, failure_code: str):
        from .chat_life_intent_retry import record_acceptance_failure

        return record_acceptance_failure(
            ledger=self.ledger,
            proposal_id=proposal_id,
            actor_ref=self._owner,
            failure_code=failure_code,
        )

    def read_for_plan(self, *, plan_id: str) -> ChatLifePlanMaterial | None:
        if not plan_id.startswith(PLAN_PREFIX):
            return None
        projection = self.ledger.project()
        located = self.ledger.lookup_event_commit(
            "event:chat-life-intent:" + plan_id.removeprefix(PLAN_PREFIX)
        )
        if located is None:
            return None
        payload = ActivityPlannedPayload.model_validate_json(located[0].payload_json)
        validate_chat_life_plan_event(state=projection, event=located[0], payload=payload)
        if payload.plan.owner_actor_ref != self._owner:
            return None
        _, intent = derive_chat_life_plan(
            state=projection,
            world_id=self.ledger.world_id,
            proposal_id=payload.chat_intent_origin.proposal_id,
            owner_actor_ref=self._owner,
        )
        return ChatLifePlanMaterial(
            character_intention=intent.intention, origin=payload.chat_intent_origin
        )


class CompositeActivityPlanMaterialReader:
    def __init__(self, *readers) -> None:
        self._readers = readers

    def read_for_plan(self, *, plan_id: str):
        for reader in self._readers:
            value = reader.read_for_plan(plan_id=plan_id)
            if value is not None:
                return value
        return None


class _ChatLifeIntentActivityReader(RoleLifeIntentActivityReader):
    def __init__(self, *, ledger) -> None:
        super().__init__(
            ledger=ledger, plan_prefix=PLAN_PREFIX, event_prefix="event:chat-life-intent:",
            origin_field="chat_intent_origin", derive=derive_chat_life_plan,
            validate=validate_chat_life_plan_event,
        )

class ChatLifeIntentActiveReader(_ChatLifeIntentActivityReader):
    def read_active_plan(self, **kwargs):
        return self._read(**kwargs, status="active")


class ChatLifeIntentCompletedReader(_ChatLifeIntentActivityReader):
    """Read a completed lifecycle; the accepted intention need not have succeeded."""

    def read_completed_plan(self, **kwargs):
        return self._read(**kwargs, status="completed")


class CompositeActiveActivityReader:
    def __init__(self, *readers) -> None:
        self._readers = tuple(x for x in readers if x is not None)

    def read_active_plan(self, **kwargs):
        for reader in self._readers:
            value = reader.read_active_plan(**kwargs)
            if value is not None:
                return value
        return None


class CompositeCompletedActivityReader:
    def __init__(self, *readers) -> None:
        self._readers = tuple(x for x in readers if x is not None)

    def read_completed_plan(self, **kwargs):
        for reader in self._readers:
            value = reader.read_completed_plan(**kwargs)
            if value is not None:
                return value
        return None
