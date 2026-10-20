"""Prepared external detail is private until its exact source and time permit release."""
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .schema_core import FrozenModel, PrivacyClass


class EventDetailSourceToken(FrozenModel):
    source_ref: str = Field(min_length=1, max_length=512)
    token: str = Field(pattern=r"^[0-9a-f]{64}$")


class EventDetailCapability(FrozenModel):
    contract: Literal["event-detail-capability.1", "event-detail-capability.2"] = "event-detail-capability.1"
    source_kind: Literal["activity_execution", "world_occurrence"]
    source_event_ref: str = Field(min_length=1, max_length=512)
    source_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_world_revision: int = Field(ge=1)
    entity_ref: str = Field(min_length=1, max_length=512)
    entity_revision: int = Field(ge=1)
    starts_at: datetime
    ends_at: datetime
    privacy_class: PrivacyClass
    source_tokens: tuple[EventDetailSourceToken, ...] = Field(default=(), max_length=12, exclude_if=lambda value: not value)
    required_program: bool = Field(default=False, exclude_if=lambda value: not value)
    closing_offset_seconds: int | None = Field(default=None, ge=60, le=86340,
                                                exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def bounded_window(self):
        if any(t.tzinfo is None or t.utcoffset() is None for t in (self.starts_at, self.ends_at)):
            raise ValueError("event details need aware times")
        if not 60 <= (self.ends_at - self.starts_at).total_seconds() <= 86400:
            raise ValueError("one prepared event covers one minute to one day")
        if self.required_program and self.closing_offset_seconds != int((self.ends_at - self.starts_at).total_seconds()) - 60:
            raise ValueError("required event detail needs its exact final release offset")
        if not self.required_program and self.closing_offset_seconds is not None:
            raise ValueError("optional event detail cannot claim a required closing offset")
        if self.contract == "event-detail-capability.2" and (
            not self.required_program or self.source_kind != "world_occurrence"
            or self.privacy_class == "withhold"
        ):
            raise ValueError("ordinary course detail needs a role-visible active world event")
        return self


class EventDetailStep(FrozenModel):
    after_seconds: int = Field(ge=0, le=86400, strict=True)
    text: str = Field(min_length=1, max_length=2000)
    claim_refs: tuple[str, ...] = Field(min_length=1, max_length=16)
    # Only this already drawn World outcome permits dependent detail. None
    # means the external detail is independent of all proposed alternatives.
    outcome_index: int | None = Field(default=None, ge=0, le=3,
                                      exclude_if=lambda value: value is None)


def execution_authority(occurrence, content_proof=None, *, candidate_ref=None):
    """Use an indexed capability or exact committed candidate content, never prose inference."""
    from .world_consequence_contract import WorldConsequenceV2, ActivityExecutionBinding
    import hashlib

    if content_proof is not None:
        digest = hashlib.sha256(content_proof.encode()).hexdigest()
        if not any(c.content_payload_hash == digest and c.result_contract == "world-consequence.2"
                   and (candidate_ref is None or c.candidate_result_ref == candidate_ref)
                   for c in occurrence.candidate_outcomes):
            raise ValueError("execution proof differs from the committed candidate")
        value = WorldConsequenceV2.model_validate_json(content_proof)
        result = value.authorized_attempt_result
        if result is None or not isinstance(result.execution_binding, ActivityExecutionBinding):
            raise ValueError("execution proof has no activity authorization")
        return result.execution_binding
    index = occurrence.event_detail_program
    if index is None or index.capability.source_kind != "activity_execution":
        raise ValueError("execution source guard requires its original authority")
    return index.capability


def execution_authorities(occurrence, content_store, *, candidate_ref=None):
    """Read existing execution bindings even when there are no timed detail steps."""
    from .world_consequence_contract import WorldConsequenceV2, ActivityExecutionBinding
    import hashlib

    index = occurrence.event_detail_program
    if index is not None and index.capability.source_kind == "activity_execution":
        return ((index.capability, None),)
    result = []
    for candidate in occurrence.candidate_outcomes:
        if candidate.result_contract != "world-consequence.2" or (
            candidate_ref is not None and candidate.candidate_result_ref != candidate_ref):
            continue
        if content_store is None:
            raise ValueError("execution source resolution needs its immutable content store")
        content = content_store.read_exact(content_ref=candidate.content_ref)
        if content is None or hashlib.sha256(content.text.encode()).hexdigest() != candidate.content_payload_hash:
            raise ValueError("execution candidate content is unavailable or substituted")
        value = WorldConsequenceV2.model_validate_json(content.text)
        attempt = value.authorized_attempt_result
        if attempt is not None and isinstance(attempt.execution_binding, ActivityExecutionBinding):
            result.append((execution_authority(occurrence, content.text,
                candidate_ref=candidate.candidate_result_ref), content.text))
    return tuple(result)


def activity_execution_plan(projection, occurrence, source_ref, content_proof=None, *, candidate_ref=None):
    """Prove the original programme and the owned Plan's current accepted head."""
    from .schemas import validate_plan_authority_state

    authority = execution_authority(occurrence, content_proof, candidate_ref=candidate_ref)
    indexed = isinstance(authority, EventDetailCapability)
    plan_id = authority.entity_ref if indexed else authority.plan_id
    revision = authority.entity_revision if indexed else authority.plan_entity_revision
    plan = next((p for p in projection.plans if p.plan_id == plan_id), None)
    original = next((e for e in projection.committed_world_event_refs if e.event_id == source_ref), None)
    if (source_ref != authority.source_event_ref or original is None
        or original.event_type not in {"ActivityStarted", "ActivityResumed"}
        or original.world_revision != authority.source_world_revision
        or original.payload_hash != authority.source_payload_hash
        or plan is None or plan.owner_actor_ref not in occurrence.participant_refs
        or plan.entity_revision < revision):
        raise ValueError("activity programme lacks its exact owned execution source")
    if not indexed and (authority.actor_ref != plan.owner_actor_ref or authority.activity_id != plan.activity_id
                        or authority.source_event_type != original.event_type):
        raise ValueError("execution content differs from the original actor or activity")
    validate_plan_authority_state((plan,), projection.committed_world_event_refs,
                                  logical_time=projection.logical_time)
    if plan.authority_origin.accepted_event_ref != source_ref and plan.entity_revision <= revision:
        raise ValueError("interruption must follow the original execution head")
    return plan


def activity_execution_is_current(plan, capability):
    revision = capability.entity_revision if isinstance(capability, EventDetailCapability) else capability.plan_entity_revision
    return bool(plan.status == "active" and plan.entity_revision == revision
                and plan.authority_origin.accepted_event_ref == capability.source_event_ref)


def validate_event_details(draft, capability, *, logical_time):
    steps = draft.event_details
    if capability is not None and (draft.causal_authority != "world_contingency"
            or draft.outcome_resolution_authority != "world_contingency" or draft.timing.mode != "now"):
        raise ValueError("event detail preparation authors only current external World contingencies")
    if not steps:
        if capability is not None and capability.required_program:
            raise ValueError("this event requires a complete timed detail programme")
        return
    if capability is None or draft.causal_authority != "world_contingency" or draft.timing.mode != "now":
        raise ValueError("prepared detail needs an explicit current event capability")
    if logical_time >= capability.ends_at:
        raise ValueError("cannot prepare detail for an elapsed event")
    ranks = {"public":0,"shareable":1,"personal":2,"private":3,"withhold":4}
    if ranks[draft.privacy_class] < ranks[capability.privacy_class]:
        raise ValueError("event detail cannot weaken its source privacy")
    if capability.contract == "event-detail-capability.2" and (
        draft.privacy_class != capability.privacy_class or draft.location_ref is not None
        or draft.location_capability_ref is not None or draft.entity_refs
        or any(outcome.privacy_class != capability.privacy_class
               or outcome.visual_evidence is not None or outcome.provisional_npcs
               or outcome.provisional_places or outcome.objective_biographical_transition is not None
               or outcome.dynamic_life_direction is not None for outcome in draft.outcomes)
    ):
        raise ValueError("ordinary course details require only source-private external steps and outcomes")
    offsets = [s.after_seconds for s in steps]
    if offsets != sorted(set(offsets)):
        raise ValueError("event detail offsets must be increasing and distinct")
    if capability.required_program and (len(steps) < 2 or offsets[0] != 0
                                        or offsets[-1] != capability.closing_offset_seconds):
        raise ValueError("required event detail must include opening and final-minute scenes")
    if offsets[-1] + 60 > (capability.ends_at - capability.starts_at).total_seconds():
        raise ValueError("each detail must leave its publication window inside the event")
    claims = {c.claim_id: c for c in draft.claim_declarations}
    for step in steps:
        if len(set(step.claim_refs)) != len(step.claim_refs) or not set(step.claim_refs) <= set(claims):
            raise ValueError("event details require exact declared claim refs")
        if any(claims[ref].subject_scope != "world_environment" for ref in step.claim_refs):
            raise ValueError("prepared detail can author only external environment, not future character acts or feelings")
        if step.outcome_index is not None and step.outcome_index >= len(draft.outcomes):
            raise ValueError("detail condition refers to an unavailable outcome")
    if sum(len(s.text) for s in steps) > 8000:
        raise ValueError("prepared event detail exceeds its bounded text budget")


class EventDetailProgramIndex(FrozenModel):
    capability: EventDetailCapability
    program_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    step_count: int = Field(ge=1, le=12)
    offsets: tuple[int, ...] = Field(min_length=1, max_length=12)
    outcome_indices: tuple[int | None, ...] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def index_matches_its_window(self):
        if (len(self.offsets) != self.step_count or len(self.outcome_indices) != self.step_count
                or tuple(sorted(set(self.offsets))) != self.offsets
                or self.offsets[0] < 0 or self.offsets[-1] + 60 > (self.capability.ends_at - self.capability.starts_at).total_seconds()
                or any(i is not None and not 0 <= i <= 3 for i in self.outcome_indices)):
            raise ValueError("event detail index has inconsistent temporal bounds")
        return self


def event_detail_index(draft, capability):
    if not draft.event_details:
        return None
    import hashlib
    import json
    raw = json.dumps([s.model_dump(mode="json") for s in draft.event_details], ensure_ascii=False,
                     sort_keys=True, separators=(",", ":"))
    return EventDetailProgramIndex(capability=capability, program_hash=hashlib.sha256(raw.encode()).hexdigest(),
                                   step_count=len(draft.event_details), offsets=tuple(s.after_seconds for s in draft.event_details),
                                   outcome_indices=tuple(s.outcome_index for s in draft.event_details))


def event_detail_authority_hash(index):
    if index is None:
        return None
    import hashlib
    import json
    return hashlib.sha256(json.dumps(index.model_dump(mode="json"), ensure_ascii=False,
                                     sort_keys=True, separators=(",", ":")).encode()).hexdigest()
