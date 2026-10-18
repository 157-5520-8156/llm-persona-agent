"""Non-factual, revisable world outlook. Never an actor's intention or memory."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import json
from typing import Annotated, Literal, get_args

from pydantic import Field, model_validator

from .world_planning import WorldPlanningDraft
from .schema_core import FrozenModel
from .schema_core import PrivacyClass
from .lived_story import StoryAnchor, LivedStoryNavigation, validate_navigation


def digest(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def outlook_clock_evidence(projection, event):
    """Bind the clock event itself, never the final revision of its batch."""
    from .schemas import EvidenceRef

    source = next((ref for ref in projection.committed_world_event_refs if ref.event_id == event.event_id), None)
    if (source is None or source.event_type != "ClockAdvanced" or event.event_type != "ClockAdvanced" or event.world_id != projection.world_id
            or source.payload_hash != event.payload_hash or source.logical_time != event.logical_time):
        raise ValueError("outlook clock lacks exact committed authority")
    return EvidenceRef(ref_id=source.event_id, evidence_type="committed_world_event",
                       claim_purpose="life_transition", source_world_revision=source.world_revision,
                       immutable_hash=source.payload_hash)


class OutlookDependency(FrozenModel):
    source_ref: str = Field(min_length=1, max_length=512)
    # State predicates are executable domain facts, not motives or feelings.
    ready_statuses: tuple[str, ...] = Field(default=(), max_length=8)
    invalid_statuses: tuple[str, ...] = Field(default=(), max_length=8)
    unchanged_token: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def exclusive_conditions(self):
        if set(self.ready_statuses) & set(self.invalid_statuses):
            raise ValueError("ready and invalid states overlap")
        if not self.ready_statuses and self.unchanged_token is None:
            raise ValueError("condition needs a state predicate or an exact source token")
        return self


class OutlookThread(FrozenModel):
    thread_id: str = Field(min_length=1, max_length=96)
    situation: str = Field(min_length=1, max_length=600)
    open_question: str = Field(min_length=1, max_length=600)
    status: Literal["open", "paused", "closed"] = Field(
        default="open", exclude_if=lambda v: v == "open"
    )
    history_anchors: tuple[StoryAnchor, ...] = Field(
        default=(), max_length=8, exclude_if=lambda v: not v
    )
    developments: tuple[Annotated[str, Field(min_length=1, max_length=600)], ...] = Field(
        default=(), max_length=3, exclude_if=lambda v: not v,
    )


class OutlookStage(FrozenModel):
    stage_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    starts_on: date
    ends_on: date
    situation: str = Field(min_length=1, max_length=600)
    planning_key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,95}$")
    thread_refs: tuple[str, ...] = Field(default=(), max_length=6, exclude_if=lambda v: not v)
    conditions: tuple[OutlookDependency, ...] = Field(default=(), max_length=12, exclude_if=lambda v: not v)

    @model_validator(mode="after")
    def bounded_window(self):
        if not 0 <= (self.ends_on - self.starts_on).days <= 730:
            raise ValueError("long-range stage must be a bounded forward period")
        return self


class OutlookExecution(FrozenModel):
    """A centrally authored external stimulus, never a protagonist action."""

    kind: Literal[
        "environment", "planning", "npc_statement", "npc_activity", "npc_cancel_activity", "npc_status"
    ]
    prepare_details: bool = Field(default=False, exclude_if=lambda value: not value)
    text: str = Field(min_length=1, max_length=2400)
    planning: WorldPlanningDraft | None = Field(default=None, exclude_if=lambda value: value is None)
    privacy_class: PrivacyClass = "private"
    duration_minutes: int = Field(default=30, ge=1, le=720)
    activity_kind: str | None = Field(
        default=None, min_length=1, max_length=128, exclude_if=lambda v: v is None
    )
    location_ref: str | None = Field(
        default=None, min_length=1, max_length=512, exclude_if=lambda v: v is None
    )
    participant_npc_refs: tuple[str, ...] = Field(
        default=(), max_length=6, exclude_if=lambda v: not v
    )
    target_plan_ref: str | None = Field(
        default=None, min_length=1, max_length=512, exclude_if=lambda v: v is None
    )
    target_status: Literal["dormant", "departed", "retired"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )

    @model_validator(mode="after")
    def action_fields_match_kind(self):
        if self.prepare_details and self.kind not in {"environment", "npc_statement"}:
            raise ValueError("only an observable event can request prepared details")
        if (self.kind == "planning") != (self.planning is not None):
            raise ValueError("planning execution requires its complete external arrangement")
        if (self.kind == "npc_activity") != (self.activity_kind is not None):
            raise ValueError("only NPC activities require an activity kind")
        if self.kind != "npc_activity" and (self.location_ref or self.participant_npc_refs):
            raise ValueError("only NPC activities reserve participants and places")
        if (self.kind == "npc_cancel_activity") != (self.target_plan_ref is not None):
            raise ValueError("only NPC cancellation requires a target plan")
        if (self.kind == "npc_status") != (self.target_status is not None):
            raise ValueError("only NPC lifecycle changes require target status")
        if len(set(self.participant_npc_refs)) != len(self.participant_npc_refs):
            raise ValueError("NPC participants must be distinct")
        return self


class OutlookOpportunity(FrozenModel):
    opportunity_id: str = Field(min_length=1, max_length=96)
    thread_refs: tuple[str, ...] = Field(min_length=1, max_length=6)
    external_possibility: str = Field(min_length=1, max_length=1000)
    lane: Literal["world", "npc"]
    npc_ref: str | None = Field(default=None, min_length=1, max_length=256)
    execution: OutlookExecution
    opens_at: datetime
    closes_at: datetime
    basis_refs: tuple[str, ...] = Field(min_length=1, max_length=12)
    conditions: tuple[OutlookDependency, ...] = Field(default=(), max_length=12)
    after_opportunities: tuple[str, ...] = Field(default=(), max_length=6)

    @model_validator(mode="after")
    def closed_shape(self):
        if self.opens_at >= self.closes_at:
            raise ValueError("outlook window must be nonempty")
        if (self.lane == "npc") != (self.npc_ref is not None):
            raise ValueError("only NPC scripts identify a target NPC")
        if (self.lane == "npc") != self.execution.kind.startswith("npc_"):
            raise ValueError("NPC execution must use a typed NPC action")
        return self


class OutlookChangeResponse(FrozenModel):
    """Author's revision decision about one accepted source, never a new fact."""

    source_ref: str = Field(min_length=1, max_length=512)
    decision: Literal["revise", "retain"]
    thread_refs: tuple[str, ...] = Field(max_length=6)
    planning_keys: tuple[str, ...] = Field(default=(), max_length=4)
    reason: str = Field(min_length=1, max_length=600)


class WorldOutlookDraft(FrozenModel):
    contract: Literal["world-outlook-draft.1"] = "world-outlook-draft.1"
    world_id: str = Field(min_length=1, max_length=256)
    actor_ref: str = Field(min_length=1, max_length=256)
    parent_revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    revise_reason: str = Field(min_length=1, max_length=600)
    review_at: datetime
    horizon_end: datetime
    threads: tuple[OutlookThread, ...] = Field(min_length=1, max_length=6)
    opportunities: tuple[OutlookOpportunity, ...] = Field(default=(), max_length=24)
    long_term: tuple[OutlookStage, ...] = Field(default=(), max_length=16, exclude_if=lambda v: not v)
    change_responses: tuple[OutlookChangeResponse, ...] = Field(default=(), max_length=32, exclude_if=lambda v: not v)

    @model_validator(mode="after")
    def graph_is_closed(self):
        if sum(n.execution.kind == "planning" for n in self.opportunities) > 4:
            raise ValueError("one outlook may contain at most four planning publications")
        threads = {t.thread_id for t in self.threads}
        nodes = {n.opportunity_id: n for n in self.opportunities}
        if len(threads) != len(self.threads) or len(nodes) != len(self.opportunities):
            raise ValueError("outlook identities must be unique")
        if len({s.stage_id for s in self.long_term}) != len(self.long_term):
            raise ValueError("long-range stage identities must be unique")
        if any(not set(s.thread_refs) <= threads for s in self.long_term):
            raise ValueError("long-range stage references an unknown thread")
        if len({r.source_ref for r in self.change_responses}) != len(self.change_responses):
            raise ValueError("one accepted change needs one response")
        if any(not set(r.thread_refs) <= threads or r.decision == "revise" and not r.thread_refs
               for r in self.change_responses):
            raise ValueError("change response must name its affected threads")
        if self.review_at > self.horizon_end:
            raise ValueError("review lies beyond the horizon")
        visited, active = set(), set()

        def visit(key):
            if key in active:
                raise ValueError("outlook opportunity dependencies contain a cycle")
            if key in visited:
                return
            active.add(key)
            for parent in nodes[key].after_opportunities:
                if parent not in nodes:
                    raise ValueError("unknown prerequisite opportunity")
                visit(parent)
            active.remove(key)
            visited.add(key)

        for node in self.opportunities:
            if not set(node.thread_refs) <= threads or node.closes_at > self.horizon_end:
                raise ValueError("opportunity threads or horizon are invalid")
            visit(node.opportunity_id)
        return self


class OutlookSource(FrozenModel):
    source_ref: str
    source_kind: Literal["plan", "occurrence", "fact", "npc"]
    status: str
    allowed_statuses: tuple[str, ...] = ()
    token: str = Field(pattern=r"^[a-f0-9]{64}$")


def source_catalog(projection, *, actor_ref: str, npc_refs: tuple[str, ...] = (), required_refs=None,
                   legacy_fact_status=True):
    """Resolve technical state; a working catalog retains live and referenced history.

    None retains the legacy full catalog for old pinned requests. A concrete
    reference set scopes new planning reads, never deletes or retires entities.
    """
    values = {}
    for p in getattr(projection, "plans", ()):
        if (p.owner_actor_ref == actor_ref or p.owner_actor_ref in npc_refs) and (
            required_refs is None or p.status in {"planned", "active", "paused"}
            or p.plan_id in required_refs
        ):
            values[p.plan_id] = OutlookSource(
                source_ref=p.plan_id,
                source_kind="plan",
                status=p.status,
                token=digest(p),
                allowed_statuses=tuple(get_args(type(p).model_fields["status"].annotation)),
            )
    for o in getattr(projection, "world_occurrences", ()):
        if actor_ref in o.participant_refs and (
            required_refs is None or o.status in {"committed", "active"}
            or o.occurrence_id in required_refs
        ):
            values[o.occurrence_id] = OutlookSource(
                source_ref=o.occurrence_id,
                source_kind="occurrence",
                status=o.status,
                allowed_statuses=tuple(get_args(type(o).model_fields["status"].annotation)),
                token=digest(o),
            )
    # Fact content stays in the already actor-scoped World Context. An exact
    # token can invalidate a premise without inferring meaning from its prose.
    for f in getattr(projection, "facts", ()):
        if f.values.subject_ref == actor_ref and (required_refs is None or f.fact_id in required_refs):
            values[f.fact_id] = OutlookSource(
                source_ref=f.fact_id,
                source_kind="fact",
                status="current" if legacy_fact_status or f.values.status == "active" else "withdrawn",
                token=digest(f),
                allowed_statuses=("current",) if legacy_fact_status else ("current", "withdrawn"),
            )
    for n in getattr(projection, "npcs", ()):
        ref = f"npc:{n.npc_id}"
        if ref in npc_refs:
            values[ref] = OutlookSource(
                source_ref=ref,
                source_kind="npc",
                status=n.status,
                allowed_statuses=tuple(get_args(type(n).model_fields["status"].annotation)),
                token=digest(n.model_dump(exclude={"subjective_state"})),
            )
    return values


def validate_draft(
    draft,
    *,
    projection,
    actor_ref,
    parent_revision,
    basis_refs,
    sources,
    npc_refs,
    prior_draft=None,
    offered_history=(),
    location_refs=(),
    location_capabilities=(),
    precise_errors=False,
    precise_privacy_errors=False,
):
    if (
        draft.world_id != projection.world_id
        or draft.actor_ref != actor_ref
        or draft.parent_revision != parent_revision
    ):
        raise ValueError("outlook World, owner or predecessor mismatch")
    now = projection.logical_time
    if now is None or not now < draft.review_at <= draft.horizon_end <= now + timedelta(days=30):
        raise ValueError("outlook horizon must be within the next 30 days")
    old_nodes = {n.opportunity_id: n for n in prior_draft.opportunities} if prior_draft else {}
    offered = set(offered_history)
    if prior_draft is not None:
        offered.update(a for t in prior_draft.threads for a in t.history_anchors)
    for thread in draft.threads:
        if not set(thread.history_anchors) <= offered:
            raise ValueError("story anchors were not offered to this author")
        validate_navigation(
            LivedStoryNavigation(
                actor_ref=actor_ref, thread_refs=(), anchors=thread.history_anchors
            ),
            projection=projection,
            privacy_class="withhold",
        )
    threads = {t.thread_id: t for t in draft.threads}
    existing_events = {e.event_id for e in projection.committed_world_event_refs}
    for node in draft.opportunities:
        if any(threads[t].status != "open" for t in node.thread_refs):
            raise ValueError("new opportunity belongs to a paused or closed thread")
        validate_navigation(
            LivedStoryNavigation(
                actor_ref=actor_ref,
                thread_refs=(),
                anchors=tuple(
                    dict.fromkeys(a for t in node.thread_refs for a in threads[t].history_anchors)
                ),
            ),
            projection=projection,
            privacy_class=node.execution.privacy_class,
        )
        allowed_basis = set(basis_refs)
        if old_nodes.get(node.opportunity_id) == node:
            allowed_basis |= set(node.basis_refs) & existing_events
        if not precise_errors and (node.closes_at <= now or not set(node.basis_refs) <= allowed_basis):
            raise ValueError("outlook opportunity has expired or lacks offered basis")
        if node.closes_at <= now:
            raise ValueError(f"outlook opportunity {node.opportunity_id!r}: closes_at must be after {now.isoformat()}")
        invalid_basis = sorted(set(node.basis_refs) - allowed_basis)
        if invalid_basis:
            raise ValueError(
                f"outlook opportunity {node.opportunity_id!r}: basis_refs contains unoffered references "
                f"{invalid_basis!r}; choose exact event authority IDs from the request basis_refs list, "
                "not occurrence/plan entity IDs"
            )
        if node.npc_ref is not None and node.npc_ref not in npc_refs:
            raise ValueError("outlook NPC has no offered actor capability")
        if node.npc_ref is not None:
            npc = next(n for n in projection.npcs if f"npc:{n.npc_id}" == node.npc_ref)
            rank = ("public", "shareable", "personal", "private", "withhold")
            if rank.index(node.execution.privacy_class) < rank.index(npc.privacy_class):
                if precise_privacy_errors:
                    raise ValueError(f"outlook opportunity {node.opportunity_id!r}: NPC {node.npc_ref!r} requires privacy_class at least {npc.privacy_class}")
                raise ValueError("outlook NPC script weakens source privacy")
        from .world_outlook_npc import validate_npc_execution

        validate_npc_execution(
            node,
            projection=projection,
            npc_refs=npc_refs,
            location_refs=location_refs,
            location_capabilities=location_capabilities,
            check_busy=False,
        )
        validate_conditions(node.conditions, sources)
    for stage in draft.long_term:
        validate_conditions(stage.conditions, sources)


def validate_conditions(conditions, sources):
    for condition in conditions:
        source = sources.get(condition.source_ref)
        if source is None:
            raise ValueError("outlook condition references an unavailable source")
        if (set(condition.ready_statuses) | set(condition.invalid_statuses)) - set(source.allowed_statuses):
            raise ValueError("condition uses a state outside the offered entity schema")
        if source.status in condition.invalid_statuses:
            raise ValueError("new outlook condition is already invalid")
        if condition.unchanged_token is not None and condition.unchanged_token != source.token:
            raise ValueError("outlook source token does not match current state")


def dependency_state(conditions, sources):
    state = "ready"
    for condition in conditions:
        source = sources.get(condition.source_ref)
        if (source is None or source.status in condition.invalid_statuses
                or condition.unchanged_token is not None and condition.unchanged_token != source.token):
            return "invalidated"
        if condition.ready_statuses and source.status not in condition.ready_statuses:
            state = "waiting"
    return state


def opportunity_states(
    draft, *, now, sources, offered, produced, invalidated=(), pending_effects=()
):
    """Evaluate authored conditions only; never decide a character response."""
    nodes = {n.opportunity_id: n for n in draft.opportunities}
    states = {}

    def evaluate(key):
        if key in states:
            return states[key]
        node = nodes[key]
        if key in offered:
            state = "considered"
        elif now >= node.closes_at:
            state = "expired"
        elif key in invalidated:
            state = "invalidated"
        else:
            state = "ready" if now >= node.opens_at else "waiting"
            dependency = dependency_state(node.conditions, sources)
            if dependency == "invalidated" or dependency == "waiting" and state != "invalidated":
                state = dependency
            for parent in node.after_opportunities:
                if parent in produced:
                    continue
                prior = evaluate(parent)
                if prior in {"invalidated", "expired"} or (
                    prior == "considered" and parent not in pending_effects
                ):
                    state = "invalidated"
                    break
                if state != "invalidated":
                    state = "waiting"
        states[key] = state
        return state

    for key in nodes:
        evaluate(key)
    return states
