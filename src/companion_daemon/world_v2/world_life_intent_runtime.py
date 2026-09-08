"""Accept a world-stimulus role intent, independently of other response facets.

The source is a settled occurrence and the author is its actor-bound interior
turn. This module grants only a Plan; lifecycle choices still own execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json

from .decision_proposal_authority import DecisionProposalAuthorityReader
from .event_identity import domain_idempotency_key
from .life_events import ActivityPlannedPayload
from .proposal_audit_schemas import RecordedModelResultAudit
from .proposal_envelope import DecisionProposal, validate_proposal_envelope
from .role_life_intent_reader import RoleLifeIntentActivityReader
from .schemas import DueWindow, EvidenceRef, PlanStateProjection, ProjectionCursor, WorldEvent
from .world_life_intent_contract import (
    WORLD_LIFE_INTENT_POLICY_REF,
    WORLD_LIFE_INTENT_REGISTRY_VERSION,
    WorldLifeIntentOrigin,
    WorldLifeIntentPayload,
    world_life_intent_source_authority,
)

SOURCE = "world-v2:world-life-intent"
PLAN_PREFIX = "plan:world-life-intent:"
EVENT_PREFIX = "event:world-life-intent:"


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()


def world_life_plan_id(*, world_id: str, actor_ref: str, source_event_ref: str) -> str:
    """One accepted life facet per actor and settled source, even after reauthoring."""
    return PLAN_PREFIX + _digest([world_id, actor_ref, source_event_ref])


class WorldLifeIntentError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = "world_life_intent." + code
        super().__init__(self.code)


def derive_world_life_plan(*, state, world_id: str, proposal_id: str, owner_actor_ref: str):
    """Re-prove source, actor, final model audit and exact selected change.

    Both live acceptance and the ActivityPlanned reducer call this function.
    It never treats the source outcome's text as a role-authored intention.
    """
    audit = next((x for x in state.proposal_audits if x.proposal_id == proposal_id), None)
    if audit is None:
        raise WorldLifeIntentError("proposal_missing")
    proposal = validate_proposal_envelope(json.loads(audit.proposal_json))
    if (
        not isinstance(proposal, DecisionProposal)
        or proposal.proposal_hash != audit.proposal_hash
        or proposal.schema_registry_version != WORLD_LIFE_INTENT_REGISTRY_VERSION
    ):
        raise WorldLifeIntentError("proposal_binding_invalid")
    changes = tuple(x for x in proposal.proposed_changes if x.kind == "world_life_intent")
    if len(changes) != 1:
        raise WorldLifeIntentError("explicit_intent_count_invalid")
    change = changes[0]
    intent = WorldLifeIntentPayload.model_validate_json(change.payload.canonical_json)
    if intent.actor_ref != owner_actor_ref:
        raise WorldLifeIntentError("actor_mismatch")
    plan_id = world_life_plan_id(
        world_id=world_id, actor_ref=owner_actor_ref, source_event_ref=intent.source_event_ref
    )
    if (
        change.transition != "plan"
        or change.expected_entity_revision != 0
        or change.target_id != plan_id
        or change.evidence_refs != (intent.source_event_ref,)
        or change.policy_refs != (WORLD_LIFE_INTENT_POLICY_REF,)
    ):
        raise WorldLifeIntentError("change_binding_invalid")
    model = next((x for x in state.model_result_audits
                  if x.model_result_ref == audit.model_result_ref), None)
    if model is None or any((
        model.proposal_hash != audit.proposal_hash,
        model.model_call_id != audit.model_call_id,
        model.attempt_id != audit.attempt_id,
        model.capsule_id != audit.capsule_id,
        model.deliberation_result_id != audit.deliberation_result_id,
        model.trigger_ref != audit.trigger_ref,
        model.evaluated_world_revision != audit.evaluated_world_revision,
        model.attempt_index != model.attempt_count - 1,
    )):
        raise WorldLifeIntentError("model_binding_invalid")
    recorded = RecordedModelResultAudit.model_validate_json(model.audit_json)
    lineage = recorded.character_interior_lineage
    if lineage is None or any((
        lineage.purpose != "world_stimulus_appraisal",
        lineage.author_model_call_id != model.model_call_id,
        lineage.inner_turn_id != model.attempt_id,
        lineage.snapshot_hash != model.capsule_id,
        lineage.causal_world_id != world_id,
        lineage.causal_actor_ref != owner_actor_ref,
        intent.source_event_ref not in lineage.causal_source_refs,
        audit.trigger_ref not in lineage.causal_source_refs,
    )):
        raise WorldLifeIntentError("inner_turn_authority_invalid")
    source = world_life_intent_source_authority(
        state=state, source_event_ref=intent.source_event_ref, owner_actor_ref=owner_actor_ref
    )
    if source is None or source.world_revision > audit.evaluated_world_revision:
        raise WorldLifeIntentError("settlement_authority_invalid")
    declared = next((x for x in proposal.evidence_refs if x.ref_id == source.event_id), None)
    if declared is None or any((
        declared.evidence_kind != "settled_world_event",
        declared.source_world_revision != source.world_revision,
        declared.immutable_hash != "sha256:" + source.payload_hash,
    )):
        raise WorldLifeIntentError("source_binding_invalid")
    selected_clock = next((x for x in state.committed_world_event_refs
                           if x.world_revision == audit.evaluated_world_revision), None)
    if selected_clock is None or selected_clock.logical_time < source.logical_time:
        raise WorldLifeIntentError("selection_clock_unavailable")
    origin = WorldLifeIntentOrigin(
        source_event_ref=source.event_id, source_world_revision=source.world_revision,
        source_payload_hash=source.payload_hash,
        proposal_id=proposal_id, proposal_event_ref=audit.event_ref,
        proposal_payload_hash=audit.event_payload_hash, proposal_hash=audit.proposal_hash,
        change_id=change.change_id, evaluated_world_revision=audit.evaluated_world_revision,
        selected_at=selected_clock.logical_time,
        model_result_ref=model.model_result_ref, model_result_payload_hash=model.event_payload_hash,
        model_call_id=model.model_call_id, inner_turn_id=lineage.inner_turn_id,
        opportunity_ref=lineage.opportunity_ref,
        snapshot_id=lineage.snapshot_id, snapshot_hash=lineage.snapshot_hash,
    )
    evidence = EvidenceRef(
        ref_id=source.event_id, evidence_type="settled_world_event", claim_purpose="future_plan",
        source_world_revision=source.world_revision, immutable_hash=source.payload_hash,
    )
    identity = plan_id.removeprefix(PLAN_PREFIX)
    starts = origin.selected_at + timedelta(seconds=intent.start_after_seconds)
    occurrence = next(x for x in state.world_occurrences if x.settlement_event_ref == source.event_id)
    plan = PlanStateProjection(
        plan_id=plan_id, activity_id="activity:world-life-intent:" + identity,
        entity_revision=1, activity_kind="self_directed." + _digest(intent.intention)[:24],
        evidence_refs=(evidence,), status="planned", importance_bp=intent.importance_bp,
        scheduled_window=DueWindow(
            opens_at=starts, closes_at=starts + timedelta(seconds=intent.duration_seconds)
        ), participant_refs=(), location_ref=None,
        privacy_class="withhold" if occurrence.visibility == "withhold" else "private",
        owner_actor_ref=owner_actor_ref,
    )
    return ActivityPlannedPayload(
        change_id=change.change_id, transition_id="transition:world-life-intent:" + identity,
        expected_entity_revision=0, evidence_refs=(evidence,),
        policy_refs=(WORLD_LIFE_INTENT_POLICY_REF,), plan=plan, world_intent_origin=origin,
    ), intent


def validate_world_life_plan_event(*, state, event: WorldEvent, payload: ActivityPlannedPayload):
    claims_family = (
        event.source == SOURCE or event.event_id.startswith(EVENT_PREFIX)
        or payload.plan.plan_id.startswith(PLAN_PREFIX) or payload.world_intent_origin is not None
    )
    if not claims_family:
        return
    origin = payload.world_intent_origin
    if origin is None:
        raise WorldLifeIntentError("origin_missing")
    expected, _ = derive_world_life_plan(
        state=state, world_id=event.world_id, proposal_id=origin.proposal_id,
        owner_actor_ref=event.actor,
    )
    if any((
        event.source != SOURCE,
        event.event_id != EVENT_PREFIX + expected.plan.plan_id.removeprefix(PLAN_PREFIX),
        event.causation_id != origin.proposal_event_ref,
        payload != expected,
    )):
        raise WorldLifeIntentError("plan_authority_mismatch")


@dataclass(frozen=True)
class WorldLifePlanMaterial:
    character_intention: str
    origin: WorldLifeIntentOrigin


class WorldLifeIntentRuntime:
    def __init__(self, *, ledger, owner_actor_ref: str) -> None:
        if not owner_actor_ref:
            raise ValueError("world life intent needs an owner")
        self.ledger = ledger
        self._owner = owner_actor_ref

    def accept(self, *, world_id: str, audit_cursor: ProjectionCursor, proposal_id: str):
        reader = DecisionProposalAuthorityReader(ledger=self.ledger)
        authority = reader.read(reader.pin(
            world_id=world_id, cursor=audit_cursor, proposal_id=proposal_id
        ))
        projection = self.ledger.project()
        payload, intent = derive_world_life_plan(
            state=projection, world_id=world_id, proposal_id=proposal_id, owner_actor_ref=self._owner
        )
        identity = payload.plan.plan_id.removeprefix(PLAN_PREFIX)
        event_id = EVENT_PREFIX + identity
        existing = self.ledger.lookup_event_commit(event_id)
        if existing is not None:
            original = ActivityPlannedPayload.model_validate_json(existing[0].payload_json)
            validate_world_life_plan_event(state=projection, event=existing[0], payload=original)
            _, original_intent = derive_world_life_plan(
                state=projection, world_id=world_id,
                proposal_id=original.world_intent_origin.proposal_id, owner_actor_ref=self._owner,
            )
            if original_intent != intent:
                raise WorldLifeIntentError("effect_identity_conflict")
            return existing[1]
        if projection.logical_time is None:
            raise WorldLifeIntentError("clock_unavailable")
        source_event = self.ledger.lookup_event_commit(authority.audit.event_ref)[0]
        value = payload.model_dump(mode="json")
        event = WorldEvent.from_payload(
            schema_version="world-v2.1", event_id=event_id, world_id=world_id,
            event_type="ActivityPlanned", logical_time=projection.logical_time,
            created_at=source_event.created_at, actor=self._owner, source=SOURCE,
            trace_id=source_event.trace_id, causation_id=source_event.event_id,
            correlation_id=source_event.correlation_id,
            idempotency_key=domain_idempotency_key(
                event_type="ActivityPlanned", world_id=world_id, payload=value
            ), payload=value,
        )
        validate_world_life_plan_event(state=projection, event=event, payload=payload)
        return self.ledger.commit_at_cursor(
            (event,), expected_cursor=ProjectionCursor(
                world_revision=projection.world_revision,
                deliberation_revision=projection.deliberation_revision,
                ledger_sequence=projection.ledger_sequence,
            ), commit_id="commit:world-life-intent:" + identity,
        )

    def read_for_plan(self, *, plan_id: str) -> WorldLifePlanMaterial | None:
        if not plan_id.startswith(PLAN_PREFIX):
            return None
        located = self.ledger.lookup_event_commit(EVENT_PREFIX + plan_id.removeprefix(PLAN_PREFIX))
        if located is None:
            return None
        projection = self.ledger.project()
        payload = ActivityPlannedPayload.model_validate_json(located[0].payload_json)
        validate_world_life_plan_event(state=projection, event=located[0], payload=payload)
        if payload.plan.owner_actor_ref != self._owner:
            return None
        _, intent = derive_world_life_plan(
            state=projection, world_id=self.ledger.world_id,
            proposal_id=payload.world_intent_origin.proposal_id, owner_actor_ref=self._owner,
        )
        return WorldLifePlanMaterial(
            character_intention=intent.intention, origin=payload.world_intent_origin
        )


class _WorldLifeIntentActivityReader(RoleLifeIntentActivityReader):
    def __init__(self, *, ledger) -> None:
        super().__init__(
            ledger=ledger, plan_prefix=PLAN_PREFIX, event_prefix=EVENT_PREFIX,
            origin_field="world_intent_origin", derive=derive_world_life_plan,
            validate=validate_world_life_plan_event,
        )


class WorldLifeIntentActiveReader(_WorldLifeIntentActivityReader):
    def read_active_plan(self, **kwargs):
        return self._read(**kwargs, status="active")


class WorldLifeIntentCompletedReader(_WorldLifeIntentActivityReader):
    def read_completed_plan(self, **kwargs):
        return self._read(**kwargs, status="completed")
