"""Non-factual, revisable world outlook. Never an actor's intention or memory."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from typing import Literal, get_args

from pydantic import Field, model_validator

from .schema_core import FrozenModel
from .schema_core import PrivacyClass


def digest(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


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


class OutlookExecution(FrozenModel):
    """A centrally authored external stimulus, never a protagonist action."""

    kind: Literal["environment", "npc_statement"]
    text: str = Field(min_length=1, max_length=2400)
    privacy_class: PrivacyClass = "private"
    duration_minutes: int = Field(default=30, ge=1, le=720)


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
        if (self.lane == "npc") != (self.execution.kind == "npc_statement"):
            raise ValueError("NPC execution must be an attributed statement")
        return self


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

    @model_validator(mode="after")
    def graph_is_closed(self):
        threads = {t.thread_id for t in self.threads}
        nodes = {n.opportunity_id: n for n in self.opportunities}
        if len(threads) != len(self.threads) or len(nodes) != len(self.opportunities):
            raise ValueError("outlook identities must be unique")
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


def source_catalog(projection, *, actor_ref: str, npc_refs: tuple[str, ...] = ()):
    """Only current technical entity state; no NPC/private psychological text."""
    values = {}
    for p in getattr(projection, "plans", ()):
        if p.owner_actor_ref == actor_ref or p.owner_actor_ref in npc_refs:
            values[p.plan_id] = OutlookSource(
                source_ref=p.plan_id,
                source_kind="plan",
                status=p.status,
                token=digest(p),
                allowed_statuses=tuple(get_args(type(p).model_fields["status"].annotation)),
            )
    for o in getattr(projection, "world_occurrences", ()):
        if actor_ref in o.participant_refs:
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
        if f.values.subject_ref == actor_ref:
            values[f.fact_id] = OutlookSource(
                source_ref=f.fact_id,
                source_kind="fact",
                status="current",
                token=digest(f),
                allowed_statuses=("current",),
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
    existing_events = {e.event_id for e in projection.committed_world_event_refs}
    for node in draft.opportunities:
        allowed_basis = set(basis_refs)
        if old_nodes.get(node.opportunity_id) == node:
            allowed_basis |= set(node.basis_refs) & existing_events
        if node.closes_at <= now or not set(node.basis_refs) <= allowed_basis:
            raise ValueError("outlook opportunity has expired or lacks offered basis")
        if node.npc_ref is not None and node.npc_ref not in npc_refs:
            raise ValueError("outlook NPC has no offered actor capability")
        if node.npc_ref is not None:
            npc = next(n for n in projection.npcs if f"npc:{n.npc_id}" == node.npc_ref)
            rank = ("public", "shareable", "personal", "private", "withhold")
            if rank.index(node.execution.privacy_class) < rank.index(npc.privacy_class):
                raise ValueError("outlook NPC script weakens source privacy")
        for condition in node.conditions:
            source = sources.get(condition.source_ref)
            if source is None:
                raise ValueError("outlook condition references an unavailable source")
            requested = set(condition.ready_statuses) | set(condition.invalid_statuses)
            if requested - set(source.allowed_statuses):
                raise ValueError("condition uses a state outside the offered entity schema")
            if source.status in condition.invalid_statuses:
                raise ValueError("new outlook condition is already invalid")
            if condition.unchanged_token is not None and condition.unchanged_token != source.token:
                raise ValueError("outlook source token does not match current state")


def opportunity_states(draft, *, now, sources, offered, produced, invalidated=()):
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
            for condition in node.conditions:
                source = sources.get(condition.source_ref)
                if (
                    source is None
                    or source.status in condition.invalid_statuses
                    or condition.unchanged_token is not None
                    and condition.unchanged_token != source.token
                ):
                    state = "invalidated"
                    break
                if condition.ready_statuses and source.status not in condition.ready_statuses:
                    state = "waiting"
            for parent in node.after_opportunities:
                if parent in produced:
                    continue
                prior = evaluate(parent)
                if prior in {"invalidated", "expired", "considered"}:
                    state = "invalidated"
                    break
                if state != "invalidated":
                    state = "waiting"
        states[key] = state
        return state

    for key in nodes:
        evaluate(key)
    return states
