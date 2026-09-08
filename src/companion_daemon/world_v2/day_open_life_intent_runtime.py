"""Derive one private Plan from the exact audited day-open role decision.

Neither the bridge nor the accepting runtime authors an intention. The bridge
is inert; acceptance and replay re-prove its complete original role/model,
capability, Clock and local-day bindings before permitting ActivityPlanned.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import json
from zoneinfo import ZoneInfo

from .day_open_life_intent_contract import (
    DAY_OPEN_CHOICE_CONTRACT,
    DAY_OPEN_LIFE_INTENT_POLICY_REF,
    DAY_OPEN_LIFE_INTENT_REGISTRY_VERSION,
    DayOpenActivityCapability,
    DayOpenLifeIntentOrigin,
    DayOpenLifeIntentPayload,
    canonical_json,
    day_open_opportunity_ref,
    digest,
)
from .chat_life_intent_contract import LifeIntentDraft
from .event_identity import domain_idempotency_key
from .life_events import ActivityPlannedPayload
from .role_life_intent_reader import RoleLifeIntentActivityReader
from .schemas import DueWindow, EvidenceRef, PlanStateProjection, ProjectionCursor, WorldEvent

SOURCE = "world-v2:day-open-life-intent"
PLAN_PREFIX = "plan:day-open-life-intent:"
EVENT_PREFIX = "event:day-open-life-intent:"
PURPOSE = "activity_lifecycle_choice"


class DayOpenLifeIntentError(ValueError):
    def __init__(self, code: str):
        self.code = "day_open_life_intent." + code
        super().__init__(self.code)


def day_open_life_plan_id(*, world_id: str, actor_ref: str, day_key: str) -> str:
    """One effect per world/actor/day, even if a caller substitutes a first Clock."""
    return PLAN_PREFIX + digest([world_id, actor_ref, day_key])


def _role_material(result, capability_payload, *, world_id):
    from .character_interior.contracts import InnerDecision

    result = InnerDecision.model_validate_json(result.model_dump_json())
    capability = DayOpenActivityCapability.model_validate_json(canonical_json(capability_payload))
    supplied = capability.self_directed_intent
    sources = tuple(sorted({supplied.first_clock_ref, supplied.selected_clock_ref}))
    decision = result.decision
    if (
        result.status != "decided"
        or not isinstance(decision, dict)
        or decision.get("contract") != "character-interior-purpose-decision.1"
        or decision.get("purpose") != PURPOSE
        or not isinstance(decision.get("capability_ref"), str)
        or not decision["capability_ref"]
        or decision.get("capability_payload_hash") != "sha256:" + digest(capability_payload)
        or tuple(decision.get("source_refs", ())) != sources
        or supplied.opportunity_ref != day_open_opportunity_ref(
            world_id=world_id, actor_ref=result.actor_ref, day_key=supplied.day_key,
            first_clock_ref=supplied.first_clock_ref,
        )
    ):
        raise DayOpenLifeIntentError("role_capability_binding_invalid")
    payload = decision.get("payload")
    if not isinstance(payload, dict) or payload.get("contract") != DAY_OPEN_CHOICE_CONTRACT:
        raise DayOpenLifeIntentError("role_contract_invalid")
    if payload.get("decision") == "no_op" and (
        set(payload) <= {"contract", "decision", "life_intent"}
        and payload.get("life_intent") is None
    ):
        return result, capability, None
    if set(payload) != {"contract", "decision", "life_intent"} or (
        payload.get("decision") != "self_directed_intent"
    ):
        raise DayOpenLifeIntentError("explicit_intent_missing")
    intent = LifeIntentDraft.model_validate_json(canonical_json(payload["life_intent"]))
    return result, capability, intent


def materialize_day_open_proposal(result, capability_payload, *, world_id: str):
    """Return only the exact derived proposal; no_op has no domain proposal."""
    from .proposal_envelope import (
        CanonicalTypedPayload, DecisionProposal, ProposalEvidenceRef, TypedChange,
    )

    result, capability, intent = _role_material(result, capability_payload, world_id=world_id)
    if intent is None:
        return None
    supplied = capability.self_directed_intent
    source_material = {
        supplied.first_clock_ref: (supplied.first_clock_world_revision, supplied.first_clock_payload_hash),
        supplied.selected_clock_ref: (
            supplied.selected_clock_world_revision, supplied.selected_clock_payload_hash
        ),
    }
    evidence = tuple(
        ProposalEvidenceRef(
            ref_id=ref, evidence_kind="committed_world_event", source_world_revision=revision,
            immutable_hash="sha256:" + hash_,
        )
        for ref, (revision, hash_) in sorted(source_material.items())
    )
    identity = digest(result.model_dump(mode="json"))
    payload = DayOpenLifeIntentPayload(
        **intent.model_dump(mode="json"), actor_ref=result.actor_ref,
        role_decision_json=canonical_json(result.model_dump(mode="json")),
        capability_payload_json=canonical_json(capability_payload),
    )
    return DecisionProposal(
        proposal_id="proposal:day-open-life-intent:" + identity,
        schema_registry_version=DAY_OPEN_LIFE_INTENT_REGISTRY_VERSION,
        trigger_ref=supplied.selected_clock_ref,
        evaluated_world_revision=result.cursor.world_revision,
        evidence_refs=evidence,
        proposed_changes=(TypedChange(
            change_id="change:day-open-life-intent:" + identity,
            target_id=day_open_life_plan_id(
                world_id=world_id, actor_ref=result.actor_ref, day_key=supplied.day_key,
            ),
            kind="day_open_life_intent", transition="plan", expected_entity_revision=0,
            evidence_refs=tuple(item.ref_id for item in evidence),
            policy_refs=(DAY_OPEN_LIFE_INTENT_POLICY_REF,),
            payload=CanonicalTypedPayload.from_value(
                payload_schema="day_open_life_intent.v1", value=payload.model_dump(mode="json"),
            ),
        ),),
        confidence=10_000, brief_rationale=result.summary[:240],
        behavior_tendency="self_directed_intent", stance="role_authored",
        display_strategy="withhold", timing_choice="silent",
    )


def derive_day_open_life_plan(*, state, world_id: str, proposal_id: str, owner_actor_ref: str):
    """The same original proof is checked by live acceptance and reducer replay."""
    from .character_interior.audit import recorded_character_interior_lineage
    from .character_interior.contracts import InnerDecision
    from .character_interior.run_result import CausalOpportunityRuntime
    from .proposal_audit_schemas import RecordedModelResultAudit
    from .proposal_envelope import DecisionProposal, validate_proposal_envelope

    audit = next((x for x in state.proposal_audits if x.proposal_id == proposal_id), None)
    if audit is None:
        raise DayOpenLifeIntentError("proposal_missing")
    proposal = validate_proposal_envelope(json.loads(audit.proposal_json))
    if not isinstance(proposal, DecisionProposal) or any((
        proposal.schema_registry_version != DAY_OPEN_LIFE_INTENT_REGISTRY_VERSION,
        proposal.proposal_hash != audit.proposal_hash,
        len(proposal.proposed_changes) != 1,
        proposal.proposed_changes[0].kind != "day_open_life_intent",
    )):
        raise DayOpenLifeIntentError("proposal_binding_invalid")
    change = proposal.proposed_changes[0]
    payload = DayOpenLifeIntentPayload.model_validate_json(change.payload.canonical_json)
    result = InnerDecision.model_validate_json(payload.role_decision_json)
    capability_payload = json.loads(payload.capability_payload_json)
    result, capability, intent = _role_material(result, capability_payload, world_id=world_id)
    if payload.actor_ref != owner_actor_ref or result.actor_ref != owner_actor_ref:
        raise DayOpenLifeIntentError("actor_mismatch")
    if proposal != materialize_day_open_proposal(result, capability_payload, world_id=world_id):
        raise DayOpenLifeIntentError("derived_proposal_mismatch")
    supplied = capability.self_directed_intent
    model = next((x for x in state.model_result_audits
                  if x.model_result_ref == audit.model_result_ref), None)
    if model is None or any(
        getattr(model, field) != getattr(audit, field)
        for field in (
            "proposal_hash", "model_call_id", "attempt_id", "capsule_id", "trigger_ref",
            "deliberation_result_id", "evaluated_world_revision",
        )
    ) or model.audit_contract != "model-result-audit.7" or (
        model.attempt_index != 0 or model.attempt_count != 1
    ):
        raise DayOpenLifeIntentError("model_binding_invalid")
    recorded = RecordedModelResultAudit.model_validate_json(model.audit_json)
    identity = CausalOpportunityRuntime(
        world_id=world_id, actor_ref=owner_actor_ref, purpose=PURPOSE,
    ).identity_for_refs(
        tuple(result.decision["source_refs"]),
        epoch=supplied.opportunity_ref + f":attempt:{supplied.attempt_ordinal}",
    )
    expected_lineage = recorded_character_interior_lineage(
        result, purpose=PURPOSE, subject_ref=result.opportunity_ref,
        capability_ref=result.decision["capability_ref"], causal_opportunity=identity,
    )
    context = recorded.decision_context
    if any((
        recorded.character_interior_lineage != expected_lineage,
        recorded.status != "proposal_validated",
        model.capsule_id != result.snapshot_hash,
        model.model_call_id != result.author_lineage.model_call_id,
        model.attempt_id != result.inner_turn_id,
        model.evaluated_world_revision != result.cursor.world_revision,
        context is None,
    )) or context.model_dump(mode="json") != {
        "context_contract": "model-decision-context.1",
        "decision_subject_hash": digest(result.model_dump(mode="json")),
        **result.cursor.model_dump(mode="json"),
    }:
        raise DayOpenLifeIntentError("original_role_audit_invalid")
    for item in (audit, model):
        if item.event_payload_hash != digest(
            item.model_dump(mode="json", exclude={"event_ref", "event_payload_hash"})
        ):
            raise DayOpenLifeIntentError("audit_event_hash_invalid")
    sources = {x.event_id: x for x in state.committed_world_event_refs}
    for declared in proposal.evidence_refs:
        source = sources.get(declared.ref_id)
        if source is None or any((
            source.event_type != "ClockAdvanced",
            source.world_revision != declared.source_world_revision,
            "sha256:" + source.payload_hash != declared.immutable_hash,
            source.world_revision > result.cursor.world_revision,
            source.logical_time.astimezone(ZoneInfo(supplied.timezone_name)).date().isoformat()
            != supplied.day_key,
        )):
            raise DayOpenLifeIntentError("clock_source_invalid")
    selected = sources[supplied.selected_clock_ref]
    selected_head = next((x for x in sources.values()
                          if x.world_revision == result.cursor.world_revision), None)
    if selected_head is None or selected_head.logical_time != selected.logical_time:
        raise DayOpenLifeIntentError("selection_clock_invalid")
    origin = DayOpenLifeIntentOrigin(
        world_id=world_id, actor_ref=owner_actor_ref, opportunity_ref=supplied.opportunity_ref,
        day_key=supplied.day_key, timezone_name=supplied.timezone_name,
        first_clock_ref=supplied.first_clock_ref,
        first_clock_world_revision=supplied.first_clock_world_revision,
        first_clock_payload_hash=supplied.first_clock_payload_hash,
        source_event_ref=selected.event_id, source_world_revision=selected.world_revision,
        source_payload_hash=selected.payload_hash, attempt_ordinal=supplied.attempt_ordinal,
        selected_at=selected.logical_time, evaluated_cursor=result.cursor.model_dump(),
        evaluated_world_revision=result.cursor.world_revision,
        capability_ref=result.decision["capability_ref"],
        capability_payload_hash=result.decision["capability_payload_hash"],
        proposal_id=audit.proposal_id, proposal_event_ref=audit.event_ref,
        proposal_payload_hash=audit.event_payload_hash, proposal_hash=audit.proposal_hash,
        change_id=change.change_id, model_result_ref=model.model_result_ref,
        model_result_payload_hash=model.event_payload_hash, model_call_id=model.model_call_id,
        inner_turn_id=result.inner_turn_id, role_opportunity_ref=result.opportunity_ref,
        snapshot_id=result.snapshot_id, snapshot_hash=result.snapshot_hash,
    )
    evidence = tuple(EvidenceRef(
        ref_id=item.ref_id, evidence_type="committed_world_event", claim_purpose="future_plan",
        source_world_revision=item.source_world_revision,
        immutable_hash=item.immutable_hash.removeprefix("sha256:"),
    ) for item in proposal.evidence_refs)
    suffix = change.target_id.removeprefix(PLAN_PREFIX)
    starts = origin.selected_at + timedelta(seconds=intent.start_after_seconds)
    return ActivityPlannedPayload(
        change_id=change.change_id, transition_id="transition:day-open-life-intent:" + suffix,
        expected_entity_revision=0, evidence_refs=evidence,
        policy_refs=(DAY_OPEN_LIFE_INTENT_POLICY_REF,), day_open_intent_origin=origin,
        plan=PlanStateProjection(
            plan_id=change.target_id, activity_id="activity:day-open-life-intent:" + suffix,
            entity_revision=1, activity_kind="self_directed." + digest(intent.intention)[:24],
            evidence_refs=evidence, status="planned", importance_bp=intent.importance_bp,
            scheduled_window=DueWindow(
                opens_at=starts, closes_at=starts + timedelta(seconds=intent.duration_seconds),
            ),
            participant_refs=(), location_ref=None, privacy_class="private",
            owner_actor_ref=owner_actor_ref,
        ),
    ), intent


def validate_day_open_life_plan_event(*, state, event: WorldEvent, payload: ActivityPlannedPayload):
    if not (
        event.source == SOURCE or event.event_id.startswith(EVENT_PREFIX)
        or payload.plan.plan_id.startswith(PLAN_PREFIX) or payload.day_open_intent_origin is not None
    ):
        return
    origin = payload.day_open_intent_origin
    if origin is None:
        raise DayOpenLifeIntentError("origin_missing")
    expected, _ = derive_day_open_life_plan(
        state=state, world_id=event.world_id, proposal_id=origin.proposal_id,
        owner_actor_ref=event.actor,
    )
    if any((
        payload != expected, event.source != SOURCE,
        event.event_id != EVENT_PREFIX + expected.plan.plan_id.removeprefix(PLAN_PREFIX),
        event.causation_id != origin.proposal_event_ref,
    )):
        raise DayOpenLifeIntentError("plan_authority_mismatch")


@dataclass(frozen=True)
class DayOpenLifePlanMaterial:
    character_intention: str
    origin: DayOpenLifeIntentOrigin


class DayOpenLifeIntentRuntime:
    def __init__(self, *, ledger, owner_actor_ref: str):
        if not owner_actor_ref:
            raise ValueError("day-open life intent needs an owner")
        self.ledger = ledger
        self._owner = owner_actor_ref

    def accept(self, *, world_id: str, audit_cursor: ProjectionCursor, proposal_id: str):
        if world_id != self.ledger.world_id:
            raise DayOpenLifeIntentError("world_mismatch")
        # The original paid audit may have been durably recorded at a later
        # Clock. Read its actual commit prefix, without changing its evaluated pin.
        pinned = self.ledger.project_at(audit_cursor)
        payload, _ = derive_day_open_life_plan(
            state=pinned, world_id=world_id, proposal_id=proposal_id, owner_actor_ref=self._owner,
        )
        origin = payload.day_open_intent_origin
        self._validate_original_catalog(pinned, origin)
        for event_ref, hash_ in (
            (origin.proposal_event_ref, origin.proposal_payload_hash),
            (next(x.event_ref for x in pinned.model_result_audits
                  if x.model_result_ref == origin.model_result_ref), origin.model_result_payload_hash),
        ):
            located = self.ledger.lookup_event_commit(event_ref)
            if located is None or any((
                located[0].world_id != world_id, located[0].payload_hash != hash_,
                located[1].ledger_sequence > audit_cursor.ledger_sequence,
            )):
                raise DayOpenLifeIntentError("audit_commit_unavailable")
        projection = self.ledger.project()
        suffix = payload.plan.plan_id.removeprefix(PLAN_PREFIX)
        event_id = EVENT_PREFIX + suffix
        existing = self.ledger.lookup_event_commit(event_id)
        if existing is not None:
            original = ActivityPlannedPayload.model_validate_json(existing[0].payload_json)
            validate_day_open_life_plan_event(state=projection, event=existing[0], payload=original)
            if original != payload:
                raise DayOpenLifeIntentError("effect_identity_conflict")
            return existing[1]
        if projection.logical_time is None:
            raise DayOpenLifeIntentError("clock_unavailable")
        source_event = self.ledger.lookup_event_commit(origin.proposal_event_ref)[0]
        value = payload.model_dump(mode="json")
        event = WorldEvent.from_payload(
            schema_version="world-v2.1", event_id=event_id, world_id=world_id,
            event_type="ActivityPlanned", logical_time=projection.logical_time,
            created_at=source_event.created_at, actor=self._owner, source=SOURCE,
            trace_id=source_event.trace_id, causation_id=origin.proposal_event_ref,
            correlation_id=source_event.correlation_id,
            idempotency_key=domain_idempotency_key(
                event_type="ActivityPlanned", world_id=world_id, payload=value,
            ), payload=value,
        )
        validate_day_open_life_plan_event(state=projection, event=event, payload=payload)
        return self.ledger.commit_at_cursor(
            (event,), expected_cursor=ProjectionCursor(
                world_revision=projection.world_revision,
                deliberation_revision=projection.deliberation_revision,
                ledger_sequence=projection.ledger_sequence,
            ), commit_id="commit:day-open-life-intent:" + suffix,
        )

    def _validate_original_catalog(self, state, origin):
        from .life_ecology_activity import ActivityOpeningCatalog

        audit = next(x for x in state.proposal_audits if x.proposal_id == origin.proposal_id)
        change = json.loads(audit.proposal_json)["proposed_changes"][0]
        declared = json.loads(json.loads(change["payload"]["canonical_json"])["capability_payload_json"])
        original = self.ledger.project_at(ProjectionCursor(**origin.evaluated_cursor.model_dump()))
        catalog = ActivityOpeningCatalog(
            owner_actor_ref=self._owner, catalog_version=declared["catalog_version"],
        ).openings_for(projection=original, wake_event_ref=origin.source_event_ref)
        if catalog.status != "no_openings" or catalog.catalog_hash != declared["catalog_hash"]:
            raise DayOpenLifeIntentError("original_catalog_invalid")

    def read_for_plan(self, *, plan_id: str):
        if not plan_id.startswith(PLAN_PREFIX):
            return None
        located = self.ledger.lookup_event_commit(EVENT_PREFIX + plan_id.removeprefix(PLAN_PREFIX))
        if located is None:
            return None
        payload = ActivityPlannedPayload.model_validate_json(located[0].payload_json)
        state = self.ledger.project()
        validate_day_open_life_plan_event(state=state, event=located[0], payload=payload)
        if payload.plan.owner_actor_ref != self._owner:
            return None
        _, intent = derive_day_open_life_plan(
            state=state, world_id=self.ledger.world_id,
            proposal_id=payload.day_open_intent_origin.proposal_id, owner_actor_ref=self._owner,
        )
        return DayOpenLifePlanMaterial(intent.intention, payload.day_open_intent_origin)


class _DayOpenLifeIntentReader(RoleLifeIntentActivityReader):
    def __init__(self, *, ledger):
        super().__init__(
            ledger=ledger, plan_prefix=PLAN_PREFIX, event_prefix=EVENT_PREFIX,
            origin_field="day_open_intent_origin", derive=derive_day_open_life_plan,
            validate=validate_day_open_life_plan_event,
        )


class DayOpenLifeIntentPlannedReader(_DayOpenLifeIntentReader):
    def read_planned_plan(self, **kwargs):
        return self._read(**kwargs, status="planned")


class DayOpenLifeIntentActiveReader(_DayOpenLifeIntentReader):
    def read_active_plan(self, **kwargs):
        return self._read(**kwargs, status="active")


class DayOpenLifeIntentCompletedReader(_DayOpenLifeIntentReader):
    def read_completed_plan(self, **kwargs):
        return self._read(**kwargs, status="completed")
