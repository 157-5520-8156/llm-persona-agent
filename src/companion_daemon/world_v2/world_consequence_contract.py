"""World-Author consequence prose and exact, pre-author execution evidence.

These checks establish provenance, not the meaning or completeness of prose.
The existing focused semantic review must still decide whether environmental
text or a purported objective result writes an unauthorized character response.
Neither an activity start nor a receipt grants any new character action.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Annotated, Literal, TYPE_CHECKING

from pydantic import Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from .schema_core import FrozenModel, PrivacyClass
from .world_planning import WorldPlanningDraft
from .lived_story import LivedStoryNavigation

if TYPE_CHECKING:
    from .life_development_draft import LifeDevelopmentCapabilityManifest, LifeDevelopmentPossibilityDraft
    from .proposal_audit_schemas import RecordedModelResultAudit
    from .schemas import LedgerProjection, WorldEvent


def publication_timing(
    *, manifest: LifeDevelopmentCapabilityManifest, logical_time: datetime,
    draft: LifeDevelopmentPossibilityDraft | None = None,
) -> dict[str, object] | None:
    """Expose existing release instants; never classify or rewrite authored prose.

    Missing/old markers preserve paid requests. Both author and critic receive
    this same boundary; only the critic has the already chosen draft window.
    """
    if manifest.semantic_source_review_version != "2":
        return None
    if logical_time.tzinfo is None or logical_time.utcoffset() is None:
        raise ValueError("publication timing requires the pinned aware World clock")
    value = {
        "contract": "world-consequence-publication-time.1",
        "pinned_logical_time": logical_time.isoformat(),
        "premise_available_at": "resolved_timing.opens_at",
        "outcomes_available_at": "resolved_timing.closes_at",
        "outcome_rule": "no_completed_fact_after_its_availability_time",
        "explicit_future_plan": "may_remain_a_plan_not_a_completed_fact",
        "private_programme": "later_completed_scenes_belong_only_in_their_timed_detail_steps",
    }
    window = draft.timing.resolve(logical_time=logical_time, manifest=manifest) if draft else None
    if window is not None:
        value.update(premise_available_at=window.opens_at.isoformat(),
                     outcomes_available_at=window.closes_at.isoformat())
    cap = manifest.event_detail_capability
    if cap is not None:
        value["detail_source_window"] = {
            "starts_at": cap.starts_at.isoformat(), "ends_at": cap.ends_at.isoformat(),
        }
    if draft is not None and draft.event_details:
        if cap is None:
            raise ValueError("timed prose has no original detail capability")
        value["detail_steps"] = []
        for position, step in enumerate(draft.event_details):
            due = cap.starts_at + timedelta(seconds=step.after_seconds)
            if step.outcome_index is not None:
                due = max(due, window.closes_at)
            value["detail_steps"].append({"prose_path": f"event_details.{position}.text",
                                          "available_at": due.isoformat(),
                                          "outcome_index": step.outcome_index})
    completion = manifest.completed_activity_consequence
    if completion is not None and completion.lifecycle_reading is not None:
        value["attempt_result_interval_ends_at"] = completion.lifecycle_reading.completion.logical_time.isoformat()
    elif manifest.active_attempt_consequence is not None:
        value["attempt_result_interval_ends_at"] = manifest.active_attempt_consequence.current_logical_time.isoformat()
    return value

WORLD_CONSEQUENCE_CONTRACT = "world-consequence.2"
WORLD_CONSEQUENCE_CONTRACTS = frozenset({"world-consequence.2", "world-consequence.3"})
WORLD_CONSEQUENCE_AUTHORITY_CONTRACT = "world-consequence-authority.1"
_HASH = r"^[0-9a-f]{64}$"


class WorldConsequenceAuthorCursor(FrozenModel):
    """The original request prefix; separate to keep schema imports acyclic."""

    world_revision: int = Field(ge=0)
    deliberation_revision: int = Field(ge=0)
    ledger_sequence: int = Field(ge=0)


class _ExecutionBinding(FrozenModel):
    actor_ref: str = Field(min_length=1, max_length=512)
    source_event_ref: str = Field(min_length=1, max_length=512)
    source_world_revision: int = Field(ge=1)
    source_payload_hash: str = Field(pattern=_HASH)
    privacy_class: PrivacyClass


class ActivityExecutionBinding(_ExecutionBinding):
    """A started attempt, never proof that its intention succeeded in full."""

    source_kind: Literal["activity_execution"] = "activity_execution"
    source_event_type: Literal["ActivityStarted", "ActivityResumed"]
    plan_id: str = Field(min_length=1, max_length=512)
    activity_id: str = Field(min_length=1, max_length=512)
    # Revision at this start/resume, not a later head's revision.
    plan_entity_revision: int = Field(ge=2)


class ReceiptExecutionBinding(_ExecutionBinding):
    """Only the receipt's observed result; cancellation is not execution success."""

    source_kind: Literal["execution_receipt"] = "execution_receipt"
    source_event_type: Literal["ExecutionReceiptRecorded"] = "ExecutionReceiptRecorded"
    action_id: str = Field(min_length=1, max_length=512)
    action_payload_hash: str = Field(min_length=1, max_length=128)
    receipt_id: str = Field(min_length=1, max_length=512)
    receipt_hash: str = Field(pattern=_HASH)
    result_id: str = Field(min_length=1, max_length=512)
    observed_state: Literal["delivered", "failed", "cancelled", "expired"]
    raw_payload_hash: str = Field(min_length=1, max_length=128)


WorldConsequenceExecutionBinding = Annotated[
    ActivityExecutionBinding | ReceiptExecutionBinding,
    Field(discriminator="source_kind"),
]


class AuthorizedAttemptResult(FrozenModel):
    text: str = Field(min_length=1, max_length=12_000)
    execution_binding: WorldConsequenceExecutionBinding


class WorldConsequenceV2(FrozenModel):
    contract: Literal["world-consequence.2", "world-consequence.3"] = WORLD_CONSEQUENCE_CONTRACT
    world_planning: WorldPlanningDraft | None = Field(default=None, exclude_if=lambda value: value is None)
    environment_text: str = Field(min_length=1, max_length=12_000)
    story_navigation: SkipJsonSchema[LivedStoryNavigation | None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    authorized_attempt_result: AuthorizedAttemptResult | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )


    @model_validator(mode="after")
    def planning_is_a_published_arrangement(self):
        if (self.contract == "world-consequence.3") != (self.world_planning is not None):
            raise ValueError("World.3 requires an explicit external planning publication")
        if self.world_planning is not None and (
            self.authorized_attempt_result is not None
            or self.environment_text != self.world_planning.public_text()
        ):
            raise ValueError("planning publication must preserve its exact fields and cannot assert attendance")
        return self


def validate_consequence_descriptor(consequence, candidate):
    from .world_planning import planning_index
    if candidate.result_contract != consequence.contract or candidate.world_planning_index != planning_index(consequence.world_planning):
        raise ValueError("consequence descriptor differs from its exact typed content")


class WorldConsequenceAuthority(FrozenModel):
    """Exact request material at user.execution_authority, not an audit itself."""

    contract: Literal["world-consequence-authority.1"] = WORLD_CONSEQUENCE_AUTHORITY_CONTRACT
    world_id: str = Field(min_length=1, max_length=512)
    actor_ref: str = Field(min_length=1, max_length=512)
    evaluated_cursor: WorldConsequenceAuthorCursor
    execution_bindings: tuple[WorldConsequenceExecutionBinding, ...] = Field(
        default=(), max_length=16,
    )

    @model_validator(mode="after")
    def bindings_are_unique_and_actor_bound(self):
        refs = tuple(binding.source_event_ref for binding in self.execution_bindings)
        if len(refs) != len(set(refs)):
            raise ValueError("world_consequence.duplicate_source")
        if any(binding.actor_ref != self.actor_ref for binding in self.execution_bindings):
            raise ValueError("world_consequence.actor_mismatch")
        return self


class WorldConsequenceAuthorityError(ValueError):
    def __init__(self, code: str):
        self.code = "world_consequence." + code
        super().__init__(self.code)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def _cursor(state) -> WorldConsequenceAuthorCursor:
    return WorldConsequenceAuthorCursor(
        world_revision=state.world_revision,
        deliberation_revision=state.deliberation_revision,
        ledger_sequence=state.ledger_sequence,
    )


def _source(pinned_state, event):
    from .schemas import WorldEvent

    event = WorldEvent.model_validate_json(event.model_dump_json())
    committed = next((ref for ref in pinned_state.committed_world_event_refs
                      if ref.event_id == event.event_id), None)
    if event.event_type not in {"ActivityStarted", "ActivityResumed", "ExecutionReceiptRecorded"}:
        raise WorldConsequenceAuthorityError("source_type")
    if committed is None or any((
        event.world_id != pinned_state.world_id,
        committed.event_type != event.event_type,
        committed.payload_hash != event.payload_hash,
        committed.payload_hash != hashlib.sha256(event.payload_json.encode()).hexdigest(),
        committed.logical_time != event.logical_time,
        committed.world_revision > pinned_state.world_revision,
        pinned_state.logical_time is None,
        pinned_state.logical_time is not None and event.logical_time > pinned_state.logical_time,
    )):
        raise WorldConsequenceAuthorityError("source_not_in_pin")
    return event, committed


def _activity_binding(pinned_state, event, source, actor_ref):
    from .life_events import ActivityTransitionPayload
    from .schemas import validate_plan_authority_state

    payload = ActivityTransitionPayload.model_validate_json(event.payload_json)
    plan = next((plan for plan in pinned_state.plans if plan.plan_id == payload.plan_id), None)
    if plan is None or plan.owner_actor_ref != actor_ref:
        raise WorldConsequenceAuthorityError("activity_actor_or_plan")
    validate_plan_authority_state(
        (plan,), pinned_state.committed_world_event_refs, logical_time=pinned_state.logical_time,
    )
    if any((
        plan.authority_origin is None,
        plan.status == "planned",
        plan.entity_revision < payload.expected_entity_revision + 1,
        payload.transitioned_at != event.logical_time,
        plan.authority_origin is not None
        and source.world_revision > plan.authority_origin.accepted_world_revision,
    )):
        raise WorldConsequenceAuthorityError("activity_execution_revision")
    return ActivityExecutionBinding(
        actor_ref=actor_ref, source_event_ref=source.event_id,
        source_event_type=event.event_type, source_world_revision=source.world_revision,
        source_payload_hash=source.payload_hash, privacy_class=plan.privacy_class,
        plan_id=plan.plan_id, activity_id=plan.activity_id,
        plan_entity_revision=payload.expected_entity_revision + 1,
    )


def _receipt_binding(pinned_state, event, source, actor_ref):
    from .schemas import ExecutionReceipt

    receipt = ExecutionReceipt.model_validate_json(json.dumps(event.payload().get("receipt")))
    recorded = next((item for item in pinned_state.execution_receipts
                     if item.receipt_id == receipt.receipt_id), None)
    action = next((item for item in pinned_state.actions if item.action_id == receipt.action_id), None)
    if any((
        recorded != receipt,
        not receipt.is_terminal,
        receipt.receipt_kind != "terminal",
        receipt.observed_state not in {"delivered", "failed", "cancelled", "expired"},
        action is None,
        any(item.result_id == receipt.result_id for item in pinned_state.reconciliations),
    )):
        raise WorldConsequenceAuthorityError("receipt_not_observed_result")
    assert action is not None
    if any((
        action.actor != actor_ref,
        action.world_id != pinned_state.world_id,
        action.state != receipt.observed_state,
        receipt.received_at < action.logical_time,
        receipt.received_at < action.created_at,
        receipt.received_at > event.logical_time,
    )):
        raise WorldConsequenceAuthorityError("receipt_action_binding")
    return ReceiptExecutionBinding(
        actor_ref=actor_ref, source_event_ref=source.event_id,
        source_world_revision=source.world_revision, source_payload_hash=source.payload_hash,
        privacy_class="private", action_id=action.action_id,
        action_payload_hash=action.payload_hash, receipt_id=receipt.receipt_id,
        receipt_hash=_digest(receipt.model_dump(mode="json")), result_id=receipt.result_id,
        observed_state=receipt.observed_state, raw_payload_hash=receipt.raw_payload_hash,
    )


def derive_world_consequence_authority(
    *, pinned_state: LedgerProjection, actor_ref: str, source_events: tuple[WorldEvent, ...],
) -> WorldConsequenceAuthority:
    """Derive only explicitly supplied sources, without selecting life behavior.

    Supply the original ledger.project_at(cursor). A start may precede a later
    completed/abandoned head at that pin; it still proves only the earlier
    attempt, not continued action or its success. No historical ownerless Plan
    or receipt with unknown delivery becomes new authority here.
    """
    bindings = []
    for event in source_events:
        event, source = _source(pinned_state, event)
        binding = (
            _receipt_binding(pinned_state, event, source, actor_ref)
            if event.event_type == "ExecutionReceiptRecorded"
            else _activity_binding(pinned_state, event, source, actor_ref)
        )
        bindings.append(binding)
    return WorldConsequenceAuthority(
        world_id=pinned_state.world_id, actor_ref=actor_ref, evaluated_cursor=_cursor(pinned_state),
        execution_bindings=tuple(bindings),
    )


def validate_world_consequence_authority(
    *, consequence: WorldConsequenceV2, authority: WorldConsequenceAuthority,
    pinned_state: LedgerProjection, source_events: tuple[WorldEvent, ...],
    author_messages: list[dict[str, str]], author_audit: RecordedModelResultAudit,
) -> None:
    """Verify original request coverage and the result's exact execution source.

    The caller must first resolve ``author_audit`` from the original committed
    World-Author ModelResult/Proposal chain, including its event and payload
    hash. A caller-created audit object is not proof of a model invocation.
    This function checks that audit's request and cursor, not its persistence.
    A changed current projection is never a substitute for the original pin.
    Natural-language entailment and authorship still need focused review.
    """
    from .proposal_audit_schemas import RecordedModelResultAudit

    consequence = WorldConsequenceV2.model_validate_json(consequence.model_dump_json())
    authority = WorldConsequenceAuthority.model_validate_json(authority.model_dump_json())
    audit = RecordedModelResultAudit.model_validate_json(author_audit.model_dump_json())
    if authority.evaluated_cursor != _cursor(pinned_state):
        raise WorldConsequenceAuthorityError("pinned_cursor")
    derived = derive_world_consequence_authority(
        pinned_state=pinned_state, actor_ref=authority.actor_ref, source_events=source_events,
    )
    if authority != derived:
        raise WorldConsequenceAuthorityError("execution_authority_mismatch")
    context = audit.decision_context
    if context is None or any((
        audit.route.reason_code != "life_development.world_author",
        audit.status not in {
            "candidate_returned", "proposal_validated", "main_timeout_recovered",
            "main_invalid_recovered", "main_exception_recovered",
        },
        audit.response_hash is None,
        context is not None and authority.evaluated_cursor.model_dump() != {
            "world_revision": context.world_revision,
            "deliberation_revision": context.deliberation_revision,
            "ledger_sequence": context.ledger_sequence,
        },
    )):
        raise WorldConsequenceAuthorityError("author_audit_binding")
    if _digest(author_messages) != audit.request_hash:
        raise WorldConsequenceAuthorityError("author_request_hash")
    declarations = []
    for message in author_messages:
        if message.get("role") != "user":
            continue
        try:
            value = json.loads(message["content"])
        except (ValueError, KeyError, TypeError):
            continue
        if isinstance(value, dict) and "execution_authority" in value:
            declarations.append(value["execution_authority"])
    if declarations != [authority.model_dump(mode="json")]:
        raise WorldConsequenceAuthorityError("author_request_coverage")
    result = consequence.authorized_attempt_result
    if result is not None and result.execution_binding not in authority.execution_bindings:
        raise WorldConsequenceAuthorityError("attempt_not_authorized")


__all__ = [
    "ActivityExecutionBinding", "AuthorizedAttemptResult", "ReceiptExecutionBinding",
    "WORLD_CONSEQUENCE_AUTHORITY_CONTRACT", "WORLD_CONSEQUENCE_CONTRACT",
    "WorldConsequenceAuthority", "WorldConsequenceAuthorityError", "WorldConsequenceAuthorCursor",
    "WorldConsequenceExecutionBinding", "WorldConsequenceV2",
    "derive_world_consequence_authority", "validate_world_consequence_authority",
]
