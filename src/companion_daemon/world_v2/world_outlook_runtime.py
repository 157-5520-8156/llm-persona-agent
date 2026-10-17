"""World-author planning and current-condition delivery through existing lanes."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from threading import RLock
from .lived_story import StoryAnchor, anchor_catalog
from .world_outlook_request import (
    PRESENTATION_GUIDE, RESPONSE_REFERENCE_CONTRACT, decode_outlook_request,
    decode_outlook_response, encode_outlook_request,
    ROLLING_REFERENCE_CONTRACT, ROLLING_PRESENTATION_GUIDE,
)

from companion_daemon.llm import model_call_scope
from .structured_completion import complete_json_object
from .world_outlook_history import OutlookHistoryOffer, history_query_text, prepare_history_offer
from .world_outlook_author_schema import outlook_author_schema, outlook_result_tool
from .world_outlook import (
    WorldOutlookDraft,
    digest,
    source_catalog,
    validate_draft,
    opportunity_states,
)


_WORKING_SOURCES = "outlook-working-sources.1"
_ROLLING_PLANNING = "rolling-world-planning.1"
_NATIVE_RESULT = "world-outlook-native.4"
_NATIVE_RESULTS = frozenset({"world-outlook-native.1", "world-outlook-native.2", "world-outlook-native.3", _NATIVE_RESULT})
_TERMINAL_SOURCE_EVENTS = frozenset({
    "ActivityCompleted", "ActivityAbandoned", "WorldOccurrenceSettled",
    "WorldOccurrenceCancelled", "WorldOccurrenceExpired", "FactCommitted",
    "FactCorrected", "FactWithdrawn", "FactCorrectionCompensated", "FactCommittedV2",
})


def _planning_source_refs(view, context, history_offer, story):
    """Exact displayed references, not a relevance or character-choice heuristic."""
    refs = {c["source_ref"] for n in view["opportunities"] for c in n["conditions"]}
    refs.update(n["execution"]["target_plan_ref"] for n in view["opportunities"]
                if n["execution"].get("target_plan_ref"))
    refs.update(item.anchor.source_ref for item in history_offer.items)
    refs.update(item["item_ref"] for lane in context.get("slices", {}).values()
                if lane.get("availability") == "available"
                for item in lane.get("items", ()) if isinstance(item.get("item_ref"), str))
    life = context.get("slices", {}).get("world_life", {})
    if life.get("availability") == "available":
        refs.update(item["value"]["plan_id"] for item in life.get("items", ())
                    if isinstance(item.get("value"), dict)
                    and item["value"].get("context_kind") in {
                        "planned_activity", "active_activity", "completed_activity", "activity_lifecycle_state",
                        "historical_activity_intention"}
                    and isinstance(item["value"].get("plan_id"), str))
    refs.update(source["source_ref"] for thread in story for source in thread["realized_sources"])
    refs.update(view.get("changed_source_refs", ()))
    return frozenset(refs)


_SYSTEM = """You are the World Author maintaining a PRIVATE, NON-FACTUAL rolling outlook.
Organize a few persistent, intersecting life situations and external opportunities.
Storyfulness comes from distinct people, unresolved situations, setbacks, opportunities and
consequences that can develop over time. Real user support may change available paths. Never
prewrite gratitude, affection, dependency, rescue or a relationship reward; the protagonist
owns her response. Do not force distress or repeat a rejected pressure to manufacture closeness.
Use planning execution to publish or revise external arrangements such as a term, timetable,
work schedule or project period. These are World arrangements, never protagonist intentions
or evidence of attendance. Publish once, preserve weekly rules, represent cancellations and
rescheduling with exact original-date exceptions. Cancelling a previously moved instance uses
action=cancel and retains its prior new_start/duration_minutes, so the cancelled window remains visible.
The first publication uses supersedes_occurrence_ref=null; a revision names the current published
planning with the same planning_key. An ordinary environment notice is not a planning predecessor.
Plan periods may span months (at most two years), while the
publication opportunity remains within the rolling outlook horizon. At most four planning
publications per outlook; each bounded document must fit 5500 characters. Mon=0..Sun=6.
Use an empty slots list only for a background period without recurring external arrangements.
For an ongoing term, job or project established by sourced biography, give the ordinary external
structure the same attention as story hooks. If no current published or pending planning covers
it, author a coherent period and its recurring weekly arrangements, with publication in a near
future window. For a student, publish the institution's assigned course subjects and weekly
class windows for the term, not merely campus traffic or facility opening hours. Assignment is
external; attendance remains her choice. Choose coherent term-length dates and a weekly pattern
that fit the supplied background; do not invent earlier attendance,
completed coursework or new identity facts. Leave discretionary time unassigned: sleeping,
leisure, accepting a shift or attending a class remain protagonist choices. Retain existing
arrangements when still valid; amend their exact publication when external circumstances change.
Reuse current planning keys and published
source refs when revising; do not mint duplicate independent timetables for the same arrangement. Current
World facts are the only factual authority. This is not a completed story or anyone's plan.
Each fact belongs only to its subject_ref; user facts do not establish the protagonist's identity.
authoring_constraints, when present, constrain new fiction to the configured character background;
they grant no source references or evidence of past attendance, completed work or recent experience.
If a subject or institution is not sourced or constrained, do not invent a protagonist identity.
Slot weekdays/start/duration already encode time: use titles/details for subjects and conditions,
and ensure any repeated weekday prose agrees with Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6.
Never prescribe the PROTAGONIST's motives, emotions, choices, consent, participation or growth.
The user delegates all NPCs and external-world authoring to YOU. NPCs do not call their own
models. Write their executable statements centrally using their identities and knowledge.
For an unfolding encounter, difficulty or other substantive event, set prepare_details=true.
At activation, the existing World Author will prepare its concrete external progression using
then-current evidence; private future details are released only as their time and conditions allow.
A brief notice already complete in its text need not request another detail pass.
Each opportunity has an execution payload: environment text describes external conditions only;
npc_statement text is that NPC's exact utterance (a report, not certified truth of its claims).
Runtime executes these frozen payloads without asking an NPC or another event author to think.
Choose one execution kind from draft_schema.$defs.OutlookExecution.oneOf; omit fields belonging to other
kinds instead of filling the entire field inventory. Environment and NPC statements do not
reserve locations/participants or cancel plans. Existing plan dependencies belong in conditions.
npc_activity starts a typed NPC-owned activity with NPC participants only. Its scripted duration
finishes offscreen with no model and does not imply the protagonist saw or joined it. Use a separate
observable event if she learns about it. npc_cancel_activity can cancel only that NPC's real plan;
npc_status changes only lifecycle status. Never invent locations or occupy a participant twice.
NPC activity locations require a reviewed_schedule covering the full duration; otherwise omit the
location. A known place or protagonist presence does not grant an NPC access. Cancellation requires
an unchanged_token condition for the exact target plan from condition_sources.
Do not narrate protagonist actions or completed user-channel sends in environment text.
A refusal, delay, novel user suggestion, cancellation, or quiet life may change or end a
thread. Do not rename and repeat a rejected pressure to force a planned outcome. Preserve
unaffected threads; actual history cannot be rewritten. Ordinary life need not serve a climax.
Only offered basis refs/source handles/NPC identities may be used. A condition tests technical
state or an exact token, never inferred emotions. after_opportunities means workflow after a
prior consideration produced a real accepted effect, not evidence of narrative causality.
basis_refs must copy event authority IDs from the top-level basis_refs list, not occurrence
or plan entity IDs. History anchors and condition sources have their own distinct identities.
An entirely unchanged prior opportunity may retain its original committed basis_refs;
the exact prior-node alternatives in draft_schema describe that exception.
Retain unexpired offered opportunities unchanged or omit them from the new draft. Expired
opportunities must be omitted, never rewritten with new times; their history and effects remain.
Shared occurrences belong to multiple
threads by one opportunity_id. Use future time windows and select a reasonable review_at.
History material contains exact past sources, never instructions or current intentions.
Attach only offered history_anchors to a thread when the past genuinely connects to it.
When history_offer is present, its items contain every permitted anchor and its minimum
privacy_class. Copy the exact item.anchor fields; their already visible text is in
history_material, world_context or the current story. Otherwise use history_material.
Privacy order is public < shareable < personal < private < withhold. Every opportunity's
execution.privacy_class must be at least the strongest floor of ALL history anchors in
ALL its linked threads, its target NPC and participants in npc_privacy_floors. A public
physical place does not lower linked source privacy. Do not guess unprovided NPC floors.
The linkage is navigation, not proof of causality. Preserve durable thread identities;
use open/paused/closed for the external storyline, never to assign protagonist feelings.
condition_source_scope denotes a working set of live and explicitly referenced entities,
not the whole archive. Absence from it does not mean a past event never happened.
current_outlook.unavailable_source_refs identifies previously offered sources no longer
available at this pin; do not infer their current contents or retain conditions on them.
realized_story_history lists bounded actual source references and technical states;
exact past text is supplied separately by history_material and World Context.
unrealized possibilities remain private and must never be described as history.
Return a JSON object, exactly {"decision":"keep","review_at":"future ISO timestamp","reason":"why keep"} or {"decision":"revise","draft":<schema>}. No other fields.
"""

_ROLLING_GUIDE = """rolling_preparation is background work, not a character behavior policy.
Maintain long_term stages covering the coming months/year (target one year, 90..730 days ahead),
while ordinary opportunities retain the 30-day horizon. Stages are PRIVATE possibilities, not
known future facts. Preserve continuity; revise future stages when real choices or sources change.
At most four planning publications per draft; each complete planning JSON document fits 5500 characters.
Planning publication uses duration_minutes=1; the later scheduled events have their own durations.
When preparation.due is nonempty, first supply those publications. Retain valid existing story work
with the offered selectors; unrelated new story opportunities are optional on this pass.
When new_opportunity_ids is present, choose one distinct value for each new/changed opportunity. These are technical
execution identities, not story titles. Revisions keep their planning_key but get a fresh execution
identity. Retain an unchanged prior opportunity only with its offered selector.
Publish what an institution could announce now as world-planning.2: coarse dated phases for
the coming term/year, and detailed_through for at least the coming week. Use phase slot_ids
to replace ordinary weekly rules in teaching/training/activity/holiday periods; [] explicitly
means no recurring appointments there. Do not copy one timetable over all phases. Only assign
phases supported by the biography and coherent new external fiction; do not force a school plot.
Every concrete slot has event_text describing its external scene, never attendance or behavior.
Those scripts are expanded in bounded batches and executed by the existing clock; do not duplicate
them as separate opportunities. prepare_details requests time-gated detail authoring at activation.
review_at must be within seven days. Before detail coverage ends, extend it or revise/retire the
arrangement through its exact predecessor. continues=false ends a finite arrangement at ends_on.
You author the institution's new external arrangements within the supplied background; do not wait
for a separate school/calendar provider. Publication makes an arrangement known. Unannounced twists
and outcome details stay private; this grants no protagonist identity or past-attendance facts.
pending preparation is not completed by keep. Schedule each due planning revision to publish now,
or retain its already pending publication unchanged. Keep is legal only if no coverage work is due.
Your long_term planning_key connects each continuing external domain to its published arrangement;
if a linked domain has no current/pending publication, publish its coarse known planning now.
Leave personal leisure, wishes, attendance, reactions and subjective Plans to CharacterInterior.
"""
_ROLLING_SYSTEM = (_SYSTEM[:_SYSTEM.index("Use planning execution")] + _ROLLING_GUIDE + "\n"
                   + _SYSTEM[_SYSTEM.index("Reuse current planning keys"):])
_ROLLING_SYSTEM = _ROLLING_SYSTEM.replace("Use future time windows and select a reasonable review_at.",
    "Ordinary events use future windows; a due planning publication opens at logical_time. Select a review_at within the offered review bounds.")
_ROLLING_SYSTEM = _ROLLING_SYSTEM.replace("Copy the exact item.anchor fields; their already visible text is in",
    "Select the exact offered history_anchor_ref; its already visible text is in")


def _outlook_request_hash(messages, tools):
    return digest({"messages": messages, "tools": tools, "tool_choice": {"type": "function", "function": {"name": "world_outlook_v6"}}}) if tools else digest(messages)


def cursor_of(state):
    return {
        k: getattr(state, k) for k in ("world_revision", "deliberation_revision", "ledger_sequence")
    }


class WorldOutlookRuntime:
    def __init__(
        self,
        *,
        ledger,
        store,
        author,
        actor_ref: str,
        npc_refs_reader=None,
        executor=None,
        history_reader=None,
        content_store=None,
        authoring_constraints=(),
        rolling_planning=False,
    ):
        if (not isinstance(authoring_constraints, tuple) or len(authoring_constraints) > 16
                or any(not isinstance(value, str) or not value.strip() for value in authoring_constraints)
                or len(json.dumps(authoring_constraints, ensure_ascii=False).encode()) > 4096):
            raise ValueError("World author background constraints must be at most 16 texts and 4096 bytes")
        self._authoring_constraints = authoring_constraints
        self._rolling_planning = rolling_planning
        self._authoring_constraints_hash = digest(authoring_constraints) if authoring_constraints else None
        self.ledger, self.store, self.author, self.actor_ref = ledger, store, author, actor_ref
        self._content_store = content_store
        self._executor = executor
        from .event_detail_runtime import EventDetailRuntime
        self.event_details = EventDetailRuntime(ledger=ledger, store=store, executor=executor, actor_ref=actor_ref, content_store=content_store) if executor is not None and content_store is not None else None
        self._npc_refs_reader = npc_refs_reader or (lambda state: ())
        self._history_reader = history_reader
        self._health = {"status": "unprepared"}
        self._source_change_key = None
        self._source_change_sequence = 0
        self._source_change_lock = RLock()
        self._offer_frontier_generation = None
        self._offer_frontier = 0
        self._offer_frontier_sequence = 0
        self._unsettled_offers = {}

    def health(self):
        return dict(self._health)

    def _planning_publications(self, state):
        if self._content_store is None:
            return ()
        from .life_content import collect_user_channel_limited_content_refs
        from .world_planning import planning_publications
        values = planning_publications(projection=state, store=self._content_store, actor_ref=self.actor_ref,
            user_channel_limited_content_refs=collect_user_channel_limited_content_refs(ledger=self.ledger, projection=state),
            ends_at=state.logical_time + timedelta(days=730), include_elapsed_heads=True, limit=129)
        if len(values) > 128:
            raise ValueError("planning domains exceed the supported bound; no partial scheduling is allowed")
        return values

    def _preparation(self, state, draft, states=None):
        publications = self._planning_publications(state)
        today = state.logical_time.astimezone(ZoneInfo("Asia/Shanghai")).date()
        from .world_outlook_execution import occurrence_ref
        settled = {p.occurrence_ref for p in publications}
        pending = {n.execution.planning.planning_key for n in draft.opportunities
                   if n.execution.planning is not None and n.closes_at > state.logical_time
                   and (states or {}).get(n.opportunity_id) not in {"invalidated", "expired"}
                   and occurrence_ref(state.world_id, self.actor_ref, n.opportunity_id) not in settled} if draft else set()
        due = []
        for publication in publications:
            p = publication.planning
            if not p.continues and p.ends_on < today:
                continue
            if p.planning_key not in pending and (p.contract == "world-planning.1"
                    or p.preparation_due() is not None and p.preparation_due() <= state.logical_time):
                due.append({"planning_key": p.planning_key, "supersedes_occurrence_ref": publication.occurrence_ref,
                            "reason": "rolling_coverage_due"})
        if draft:
            known = {p.planning.planning_key for p in publications} | pending
            missing = {s.planning_key for s in draft.long_term if s.planning_key} - known
            retired = {}
            for occurrence in state.world_occurrences:
                if occurrence.status != "settled" or self.actor_ref not in occurrence.participant_refs:
                    continue
                candidate = next((c for c in occurrence.candidate_outcomes if c.candidate_result_ref == occurrence.settled_outcome_ref), None)
                index = candidate.world_planning_index if candidate is not None else None
                if index is not None and index.planning_key in missing:
                    old = retired.get(index.planning_key)
                    if old is None or old.settlement_world_revision < occurrence.settlement_world_revision:
                        retired[index.planning_key] = occurrence
            due.extend({"planning_key": key,
                        "supersedes_occurrence_ref": retired[key].occurrence_id if key in retired else None,
                        "reason": "domain_reopened" if key in retired else "unpublished_domain"}
                       for key in sorted(missing))
        batch = due[:max(0, 4 - len(pending))]
        due_keys = {p["planning_key"] for p in batch}
        selected = sorted(publications, key=lambda p: (p.planning.planning_key not in due_keys,
            p.planning.preparation_due() or state.logical_time, p.planning.planning_key))[:4]
        return {"contract": _ROLLING_PLANNING, "due": batch, "due_count": len(due),
                "published": [{"occurrence_ref": p.occurrence_ref, "planning": p.planning.model_dump(mode="json")}
                              for p in selected],
                "known_publications": [{"planning_key": p.planning.planning_key, "occurrence_ref": p.occurrence_ref}
                                       for p in publications],
                "outline_due": not draft or not draft.long_term
                    or max(s.ends_on for s in draft.long_term) < today + timedelta(days=60)}

    def _validate_preparation(self, candidate, pinned, projection):
        if pinned.get("draft_schema_contract") != "world-outlook-author-schema.6":
            if candidate.long_term or any(n.execution.planning and n.execution.planning.contract == "world-planning.2"
                                          for n in candidate.opportunities):
                raise ValueError("the pinned request did not offer rolling planning")
            return
        now = projection.logical_time
        today = now.astimezone(ZoneInfo("Asia/Shanghai")).date()
        if (not candidate.long_term or max(s.ends_on for s in candidate.long_term) < today + timedelta(days=90)
                or any(s.ends_on < today or s.ends_on > today + timedelta(days=730) for s in candidate.long_term)
                or candidate.review_at > now + timedelta(days=7)):
            raise ValueError("rolling outline must cover 90..730 future days, omit expired stages and review within seven days")
        covered = today - timedelta(days=1)
        for stage in sorted(candidate.long_term, key=lambda s: s.starts_on):
            if stage.starts_on > covered + timedelta(days=1):
                raise ValueError(f"long-range outline is missing {covered + timedelta(days=1)} through {stage.starts_on - timedelta(days=1)}; represent uncertain periods explicitly")
            covered = max(covered, stage.ends_on)
        publications = {n.execution.planning.planning_key: n for n in candidate.opportunities
                        if n.execution.planning is not None and n.closes_at > now}
        required = {item["planning_key"] for item in pinned["rolling_preparation"]["due"]}
        published = {item["planning_key"] for item in pinned["rolling_preparation"]["known_publications"]}
        new_domains = sorted({s.planning_key for s in candidate.long_term if s.planning_key} - published - required)
        required.update(new_domains[:max(0, 4 - len(required))])
        if not required <= publications.keys():
            raise ValueError("rolling preparation requires a publication for due domains: " + ", ".join(sorted(required - publications.keys())))
        old = {n["opportunity_id"]: {k: v for k, v in n.items() if k != "state"}
               for n in pinned["current_outlook"]["opportunities"]}
        if "new_opportunity_ids" in pinned:
            offered_ids = set(pinned["new_opportunity_ids"])
            for node in candidate.opportunities:
                if old.get(node.opportunity_id) != node.model_dump(mode="json") and node.opportunity_id not in offered_ids:
                    raise ValueError(f"new/changed opportunity {node.opportunity_id!r} must select a fresh new_opportunity_ids value; retain planning_key, not an old execution identity")
        for key, node in publications.items():
            if old.get(node.opportunity_id) == node.model_dump(mode="json") and key not in required:
                continue
            p = node.execution.planning
            if pinned.get("response_tool_contract") in _NATIVE_RESULTS - {"world-outlook-native.1"} and node.execution.duration_minutes != 1:
                raise ValueError(f"planning {key!r} publication requires duration_minutes=1; this is publication, not the duration of its scheduled events")
            if p.contract != "world-planning.2" or not now < p.review_at <= now + timedelta(days=7):
                raise ValueError(f"planning {key!r} requires world-planning.2 and its own review_at after {now.isoformat()} and no later than {(now + timedelta(days=7)).isoformat()}; received {p.review_at}. This is not ends_on or horizon_end.")
            required_end = min(p.ends_on, today + timedelta(days=7))
            if p.detailed_through < required_end or any(s.event_text is None for s in p.slots):
                raise ValueError(f"planning {key!r} must prepare through at least {required_end} and give every concrete slot its external event script")
            if p.continues and p.ends_on <= today + timedelta(days=7):
                raise ValueError("continuing planning must extend beyond the imminent renewal boundary")
            if key in required and node.opens_at > now:
                raise ValueError(f"due planning {key!r} must have a publication window open at {now.isoformat()}, not only from {node.opens_at.isoformat()}")

    def record_failure(self, exc):
        self._health = {"status": "technical_failure", "failure_code": type(exc).__name__}

    def advisory(self, *, projection):
        try:
            return self.view(projection=projection)
        except Exception as exc:
            self.record_failure(exc)
            return {
                "authority": "non_factual_private_draft",
                "status": "technical_failure",
                "opportunities": [],
            }

    def _catalog(self, state, npc_refs, *, evidence=None, required_refs=None, legacy_fact_status=None):
        if legacy_fact_status is None:
            legacy_fact_status = evidence is not None and evidence.get("condition_source_scope") != _WORKING_SOURCES
        if required_refs is None and evidence:
            required_refs = frozenset(evidence["source_tokens"])
        indexed = getattr(self.ledger, "outlook_entities_at", None)
        if required_refs is not None and callable(indexed) and state is self.ledger.project():
            from types import SimpleNamespace
            from .schemas import ProjectionCursor

            state = SimpleNamespace(**indexed(required_refs, cursor=ProjectionCursor(**cursor_of(state)),
                                             actor_ref=self.actor_ref, npc_refs=tuple(npc_refs)))
        return source_catalog(state, actor_ref=self.actor_ref, npc_refs=tuple(npc_refs),
                              required_refs=required_refs, legacy_fact_status=legacy_fact_status)

    def _entities(self, state, refs, *, include_live=False):
        indexed = getattr(self.ledger, "outlook_entities_at", None)
        if callable(indexed):
            from .schemas import ProjectionCursor
            return indexed(refs, cursor=ProjectionCursor(**cursor_of(state)), include_live=include_live)
        return {"plans": tuple(p for p in state.plans if p.plan_id in refs or
                               (include_live and p.status in {"planned", "active", "paused"})),
                "world_occurrences": tuple(o for o in state.world_occurrences if o.occurrence_id in refs or
                                           (include_live and o.status in {"committed", "active"})),
                "facts": tuple(f for f in state.facts if f.fact_id in refs), "npcs": state.npcs}

    def _anchors(self, state, refs):
        """Prove only requested anchors against the current pinned sources."""
        from types import SimpleNamespace
        from .schemas import ProjectionCursor

        refs = frozenset(refs)
        if not refs:
            return {}
        indexed = getattr(self.ledger, "outlook_entities_at", None)
        if callable(indexed):
            entities = indexed(refs, cursor=ProjectionCursor(**cursor_of(state)), include_live=False,
                               actor_ref=self.actor_ref, fields=("prehistory_records", "world_occurrences"))
        else:
            entities = {"prehistory_records": tuple(r for r in state.prehistory_records if r.record.record_id in refs),
                        "world_occurrences": tuple(o for o in state.world_occurrences if o.occurrence_id in refs)}
        return anchor_catalog(SimpleNamespace(**entities,
            memory_candidates=state.memory_candidates if entities["prehistory_records"] else ()), actor_ref=self.actor_ref)

    def _relevant_offers(self, node_ids, source_refs):
        selected = {o["operation_ref"]: o for o in self.store.offers(node_ids=node_ids)}
        selected.update((o["operation_ref"], o) for o in self.store.offers_for_effects(source_refs))
        return tuple(selected.values())

    def _new_terminal_source(self, state, revision, evidence, npc_refs, current):
        """Notice new closed entities without putting the entire archive in input.

        A positive source remains at the frontier until the private draft is
        refreshed or its existing offer receipt proves ordinary own progress.
        Irrelevant/settled own changes are consumed once, not once per heartbeat.
        """
        generation = getattr(self.ledger, "verified_history_generation", lambda: None)()
        key = (revision, tuple(npc_refs), generation, self.store.operation_generation())
        locked = self._source_change_lock.acquire(blocking=False)
        try:
            after = (self._source_change_sequence if locked and self._source_change_key == key
                     and self._source_change_sequence <= state.ledger_sequence else
                     evidence.get("source_change_cursor", evidence["cursor"]["ledger_sequence"]))
            refs, safe_after, offered_through = self._scan_terminal_sources(state, evidence, npc_refs, current, after)
            if locked:
                self._source_change_key, self._source_change_sequence = key, safe_after
            return refs, offered_through
        finally:
            if locked:
                self._source_change_lock.release()

    def _scan_terminal_sources(self, state, evidence, npc_refs, current, after):
        from .schemas import ProjectionCursor
        from types import SimpleNamespace

        changed, safe_after = [], after
        while after < state.ledger_sequence:
            page = self.ledger.events_after(cursor=ProjectionCursor(**cursor_of(state)),
                after_sequence=after, event_types=_TERMINAL_SOURCE_EVENTS)
            if not page:
                return tuple(changed), safe_after if changed else state.ledger_sequence, state.ledger_sequence
            for sequence, event in page:
                raw = event.payload()
                if event.event_type == "FactCommittedV2":
                    from .fact_accepted_contracts import rehydrate_fact_commit_materialized_v2_json
                    ref = rehydrate_fact_commit_materialized_v2_json(event.payload_json).fact_id
                else:
                    ref = raw.get("plan_id") or raw.get("occurrence_id") or raw.get("fact_after", {}).get("fact_id")
                if not isinstance(ref, str):
                    raise ValueError("outlook source change has no typed entity identity")
                if ref not in changed:
                    source = current.get(ref)
                    if source is None:
                        entities = self._entities(state, (ref,))
                        source = source_catalog(SimpleNamespace(**entities), actor_ref=self.actor_ref,
                                                npc_refs=tuple(npc_refs), required_refs=frozenset({ref}), legacy_fact_status=False).get(ref)
                    previous = {ref: evidence["source_tokens"][ref]} if ref in evidence["source_tokens"] else {}
                    offers = self.store.offers_for_effects((ref,), as_of=cursor_of(state))
                    if source is not None and self._sources_changed(previous, {ref: source}, offers):
                        changed.append(ref)
                after = sequence
                if not changed:
                    safe_after = after
                if len(changed) == 32:
                    return tuple(changed), safe_after, after
        return tuple(changed), safe_after if changed else after, after

    def _effect_states(self, state, refs):
        entities = self._entities(state, refs)
        statuses = {item.occurrence_id: item.status for item in entities["world_occurrences"]}
        statuses.update((item.plan_id, item.status) for item in entities["plans"])
        for ref in set(refs) - statuses.keys():
            located = self.ledger.lookup_event_commit(ref)
            if (located is not None and located[1].ledger_sequence <= state.ledger_sequence
                    and located[0].event_type in {"ActivityAbandoned", "NpcStatusChanged"}):
                statuses[ref] = located[0].event_type
        return statuses

    def view(self, *, projection=None, npc_refs=None):
        state = projection or self.ledger.project()
        npc_refs = tuple(self._npc_refs_reader(state)) if npc_refs is None else npc_refs
        head = self.store.head(as_of=cursor_of(state))
        if head is None:
            return {
                "contract": "world-outlook-view.1",
                "authority": "non_factual_private_draft",
                "revision": None,
                "opportunities": [],
                "threads": [],
                "needs_revision": True,
                **({"rolling_preparation": self._preparation(state, None)} if self._rolling_planning else {}),
            }
        revision, draft, evidence = head
        from .schemas import ProjectionCursor

        original = self.ledger.project_at(ProjectionCursor(**evidence["cursor"]))
        if original.semantic_hash != evidence["snapshot_hash"]:
            raise ValueError("outlook belongs to a different source history")
        from .world_outlook_execution import occurrence_ref

        relevant = self._relevant_offers({n.opportunity_id for n in draft.opportunities}, evidence["source_tokens"])
        offers = tuple(
            o
            for o in relevant
            if not any(
                o["payload"]
                .get("result_cursor", o["payload"].get("claim_cursor", {}))
                .get(k, v + 1)
                > v
                for k, v in cursor_of(state).items()
            )
        )
        terminal = {
            x["payload"]["node"]["opportunity_id"] for x in offers if x["state"] == "considered"
        }
        effects = {ref for offer in offers for ref in offer["payload"].get("effect_refs", ())}
        occurrence_refs = {occurrence_ref(state.world_id, self.actor_ref, n.opportunity_id) for n in draft.opportunities}
        effect_states = self._effect_states(state, effects | occurrence_refs)
        valid_effects = {ref for ref, status in effect_states.items()
                         if status in {"active", "settled", "completed", "ActivityAbandoned", "NpcStatusChanged"}}
        # Active occurrences are produced effects; active plans still await completion.
        plan_refs = {p.plan_id for p in self._entities(state, effects)["plans"]}
        valid_effects.difference_update(ref for ref in plan_refs if effect_states[ref] != "completed")
        pending_plans = {ref for ref in plan_refs if effect_states[ref] in {"active", "planned", "paused"}}
        pending_effects = {
            o["payload"]["node"]["opportunity_id"]
            for o in offers
            if set(o["payload"].get("effect_refs", ())) & pending_plans
        }
        produced = {
            x["payload"]["node"]["opportunity_id"]
            for x in offers
            if x["state"] == "considered"
            and set(x["payload"].get("effect_refs", ())) & valid_effects
        }
        sources = self._catalog(state, npc_refs, evidence=evidence)
        anchors = self._anchors(state, (a.source_ref for t in draft.threads for a in t.history_anchors))
        threads = {t.thread_id: t for t in draft.threads}
        withdrawn = {ref for ref in occurrence_refs if effect_states.get(ref) in {"cancelled", "expired"}}
        invalidated = {
            n.opportunity_id
            for n in draft.opportunities
            if n.lane == "npc"
            and (
                n.npc_ref not in npc_refs
                or evidence["source_tokens"].get(n.npc_ref)
                != getattr(sources.get(n.npc_ref), "token", None)
            )
        }
        invalidated.update(
            n.opportunity_id
            for n in draft.opportunities
            if occurrence_ref(state.world_id, self.actor_ref, n.opportunity_id) in withdrawn
        )
        invalidated.update(
            n.opportunity_id
            for n in draft.opportunities
            if any(
                threads[t].status != "open"
                or any(anchors.get(a.source_ref) != a for a in threads[t].history_anchors)
                for t in n.thread_refs
            )
        )
        states = opportunity_states(
            draft,
            now=state.logical_time,
            sources=sources,
            offered=terminal,
            produced=produced,
            invalidated=invalidated,
            pending_effects=pending_effects,
        )
        for node in draft.opportunities:
            if states[node.opportunity_id] == "ready":
                history = [
                    o
                    for o in offers
                    if o["payload"]["node"]["opportunity_id"] == node.opportunity_id
                ]
                if any(o["state"] == "running" for o in history):
                    states[node.opportunity_id] = "delivery_pending"
                elif any(
                    o["state"] == "technical_failure"
                    and o["payload"].get("retry_epoch")
                    == int(state.logical_time.timestamp()) // 3600
                    for o in history
                ):
                    states[node.opportunity_id] = "retry_wait"
        nodes = [
            {**n.model_dump(mode="json"), "state": states[n.opportunity_id]}
            for n in draft.opportunities
        ]
        changed_sources, source_change_cursor = self._new_terminal_source(state, revision, evidence, npc_refs, sources)
        preparation = self._preparation(state, draft, states) if self._rolling_planning else None
        return {
            "contract": "world-outlook-view.1",
            "authority": "non_factual_private_draft",
            "revision": revision,
            "threads": [t.model_dump(mode="json") for t in draft.threads],
            **({"long_term": [s.model_dump(mode="json") for s in draft.long_term],
                "rolling_preparation": preparation} if preparation is not None else {}),
            "opportunities": nodes,
            "review_at": draft.review_at.isoformat(),
            "horizon_end": draft.horizon_end.isoformat(),
            "changed_source_refs": list(changed_sources),
            "unavailable_source_refs": sorted(evidence["source_tokens"].keys() - sources.keys()),
            "source_change_cursor": source_change_cursor,
            "needs_revision": (
                evidence.get("authoring_constraints_hash") != self._authoring_constraints_hash
                or preparation is not None and (evidence.get("rolling_planning") != _ROLLING_PLANNING
                    or bool(preparation["due"]) or preparation["outline_due"])
                or state.logical_time >= draft.review_at
                or any(s in {"invalidated", "expired"} for s in states.values())
                or self._sources_changed(evidence["source_tokens"], sources, offers)
                or bool(changed_sources)
            ),
        }

    @staticmethod
    def _sources_changed(previous, sources, offers):
        # Executing a batch is expected progress, not a reason to buy another
        # complete planning pass. Cancellation/expiry and any other source
        # changes remain visible; explicit node conditions are checked above.
        own_effects = {
            ref
            for o in offers
            if o["state"] == "considered"
            for ref in o["payload"].get("effect_refs", ())
        }
        current = {k: v.token for k, v in sources.items()}
        for key in previous.keys() | current.keys():
            if previous.get(key) == current.get(key):
                continue
            source = sources.get(key)
            if (
                key in own_effects
                and source is not None
                and (
                    (
                        source.source_kind == "occurrence"
                        and source.status in {"committed", "active", "settled"}
                    )
                    or (
                        source.source_kind == "plan"
                        and source.status in {"planned", "active", "completed"}
                    )
                )
            ):
                continue
            return True
        return False

    def _refresh_identity(self, projection, view, npc_refs, prior_head):
        """One retry identity for authoring and read-only deadline selection."""
        sources = self._catalog(projection, npc_refs, required_refs=frozenset(prior_head[2]["source_tokens"] if prior_head else ()))
        now = projection.logical_time
        native_result = self._rolling_planning and callable(getattr(self.author, "complete_json", None))
        # Bounded retry epochs affect technical work only; a failed author is
        # never recorded as an actor's refusal or a completed opportunity.
        retry_epoch = int(now.timestamp()) // 3600
        running = self.store.running_refresh(cursor=cursor_of(projection))
        input_key = running["payload"]["input_key"] if running else digest(
            {
                "source_contract": _WORKING_SOURCES,
                "world_id": projection.world_id,
                "actor_ref": self.actor_ref,
                "parent": view["revision"],
                "sources": {k: v.token for k, v in sources.items()},
                "retry_epoch": retry_epoch,
                **({"rolling_planning": _ROLLING_PLANNING} if self._rolling_planning else {}),
                **({"response_tool_contract": _NATIVE_RESULT} if native_result else {}),
                "offers": digest(self._relevant_offers({n["opportunity_id"] for n in view["opportunities"]}, sources)),
                **({"authoring_constraints_hash": self._authoring_constraints_hash}
                   if self._authoring_constraints_hash is not None else {}),
            }
        )
        return sources, running, input_key

    def next_preparation_due(self, *, projection=None, view=None):
        """Coverage maintenance has its own deadline, never a role-life cadence."""
        if not self._rolling_planning:
            return None
        state = projection or self.ledger.project()
        npc_refs = tuple(self._npc_refs_reader(state))
        view = view or self.view(projection=state, npc_refs=npc_refs)
        now = state.logical_time
        preparation = view["rolling_preparation"]
        dates = [datetime.fromisoformat(view["review_at"])] if view.get("review_at") else []
        from .world_planning import WorldPlanningDraft
        for item in preparation["published"]:
            p = WorldPlanningDraft.model_validate_json(json.dumps(item["planning"]))
            if (deadline := p.preparation_due()) is not None and deadline > now:
                dates.append(deadline)
            # Expired deadlines are represented by preparation.due. A pending
            # replacement suppresses that work until its publication window;
            # its old deadline must not manufacture a one-second clock loop.
        if preparation["due"] or preparation["outline_due"]:
            dates.append(now)
        due = min(dates, default=None)
        if due is None or due > now:
            return due
        _, running, key = self._refresh_identity(state, view, npc_refs, self.store.head(as_of=cursor_of(state)))
        op = running["operation_ref"] if running else "refresh:" + key
        prior = self.store.operation(op)
        if prior and (prior["state"] != "running" or not self.store.attempts(op)
                      or any((call := self.store.operation(f"{op}:author:{i}")) and call["state"] == "running" for i in range(2))):
            # Failed/unknown paid work retains its exact receipt. A different
            # source identity or the existing next retry epoch may try again.
            return datetime.fromtimestamp((int(now.timestamp()) // 3600 + 1) * 3600, tz=now.tzinfo)
        return now

    def record_preparation_failure(self, *, projection, code):
        """Preflight failures use the same retry receipt, without inventing a call."""
        refs = tuple(self._npc_refs_reader(projection))
        view = self.view(projection=projection, npc_refs=refs)
        _, running, key = self._refresh_identity(projection, view, refs, self.store.head(as_of=cursor_of(projection)))
        op = running["operation_ref"] if running else "refresh:" + key
        prior = self.store.operation(op)
        if prior is None:
            self.store.claim(op, kind="refresh", payload={"input_key": key, "cursor": cursor_of(projection),
                "model": str(getattr(self.author, "model", type(self.author).__name__)), "parent_revision": view["revision"]})
        if prior is None or prior["state"] == "running" and not any(self.store.operation(f"{op}:author:{i}") for i in range(2)):
            self.store.finish(op, state="technical_failure", payload={"code": code})
        self._health = {"status": "technical_failure", "failure_code": code}

    async def refresh(self, *, projection, context, manifest, history_trigger_ref=None):
        native_result = self._rolling_planning and callable(getattr(self.author, "complete_json", None))
        npc_refs = tuple(
            c.npc_ref for c in manifest.npc_capabilities if c.lifecycle_state == "active"
        )
        view = self.view(projection=projection, npc_refs=npc_refs)
        prior_head = self.store.head(as_of=cursor_of(projection))
        # Installation is the durable acceptance proof even when another batch
        # is already due. Finish its receipt before selecting running work.
        if prior_head is not None and prior_head[0] == view["revision"]:
            operation = self.store.operation(prior_head[2]["operation_ref"])
            if operation and operation["state"] == "running":
                self.store.finish(prior_head[2]["operation_ref"], state="accepted",
                                  payload={"revision": prior_head[0]})
        if not view["needs_revision"]:
            self._health = {"status": "ready", "revision": view["revision"]}
            return {"status": "unchanged", "revision": view["revision"]}
        sources, running, input_key = self._refresh_identity(projection, view, npc_refs, prior_head)
        now = projection.logical_time
        op = running["operation_ref"] if running else "refresh:" + input_key
        configured_model = str(getattr(self.author, "model", type(self.author).__name__))
        cached_attempts = {}
        if not self.store.claim(
            op,
            kind="refresh",
            payload={
                "input_key": input_key,
                "cursor": cursor_of(projection),
                "model": configured_model,
                "parent_revision": view["revision"],
            },
        ):
            saved = self.store.operation(op)
            if saved["state"] != "running":
                result = {"status": saved["state"], "operation_ref": op}
                if saved["state"] == "technical_failure":
                    result["failure_code"] = saved["payload"].get("code", "outlook_author_failed")
                self._health = result
                return self.health()
            if (saved["payload"]["cursor"] != cursor_of(projection)
                    or saved["payload"].get("parent_revision", view["revision"]) != view["revision"]):
                self.store.finish(op, state="stale", payload={"cursor": saved["payload"]["cursor"]})
                return {"status": "stale", "operation_ref": op}
            cached_attempts = self.store.attempts(op)
            if not cached_attempts:
                # Unknown/in-flight provider outcome must not be billed twice.
                return {"status": "running", "operation_ref": op}
        original_model = self.store.operation(op)["payload"]["model"]
        if cached_attempts:
            # Recovery owns the original complete request, including its
            # history and reference offer. A changed query/compiler or an
            # unavailable lookup cannot invalidate already paid work.
            messages = cached_attempts[min(cached_attempts)]["messages"]
            pinned = decode_outlook_request(messages[1]["content"])
            if pinned.get("parent_revision") != view["revision"]:
                self.store.finish(op, state="stale", payload={"cursor": cursor_of(projection)})
                return {"status": "stale", "operation_ref": op}
            sources = self._catalog(projection, npc_refs,
                required_refs=frozenset(s["source_ref"] for s in pinned["condition_sources"]),
                legacy_fact_status=pinned.get("condition_source_scope") != _WORKING_SOURCES)
        else:
            history = self._history_material(projection, context, view, history_trigger_ref)
            npc_privacy_floors = [
                {"npc_ref": "npc:" + npc.npc_id, "privacy_class": npc.privacy_class}
                for npc in projection.npcs if "npc:" + npc.npc_id in npc_refs
            ]
            history_offer = prepare_history_offer(
                projection=projection, actor_ref=self.actor_ref, context=context,
                history_material=history, current_outlook=view,
            )
            story = self.story_history(projection=projection, planning=True)
            working_sources = self._catalog(projection, npc_refs, required_refs=_planning_source_refs(
                view, context, history_offer, story,
            ))
            new_ids = tuple(f"outlook:{input_key}:{i}" for i in range(24)) if native_result else None
            packet = {
                "contract": "world-outlook-request.1",
                "response_reference_contract": ROLLING_REFERENCE_CONTRACT if self._rolling_planning else RESPONSE_REFERENCE_CONTRACT,
                **({"response_tool_contract": _NATIVE_RESULT} if native_result else {}),
                **({"new_opportunity_ids": list(new_ids)} if new_ids is not None else {}),
                "world_id": projection.world_id,
                "actor_ref": self.actor_ref,
                "logical_time": now.isoformat(),
                "parent_revision": view["revision"],
                "current_outlook": {k: v for k, v in view.items() if k != "rolling_preparation"},
                **({"rolling_preparation": view["rolling_preparation"]} if self._rolling_planning else {}),
                "history_material": history,
                "history_offer": history_offer.model_dump(mode="json"),
                "realized_story_history": story,
                "world_context": context,
                **({"authoring_constraints": {
                    "authority": "generation_constraints_only",
                    "stable_identity_facts": list(self._authoring_constraints),
                    "digest": self._authoring_constraints_hash,
                }} if self._authoring_constraints else {}),
                "basis_refs": list(manifest.anchor_refs),
                "condition_source_scope": _WORKING_SOURCES,
                "condition_sources": [v.model_dump(mode="json") for v in working_sources.values()],
                "available_npc_refs": list(npc_refs),
                "npc_privacy_floors": npc_privacy_floors,
                "npc_capabilities": [
                    c.model_dump(mode="json")
                    for c in manifest.npc_capabilities
                    if c.npc_ref in npc_refs
                ],
                "plan_schedules": [
                    p.model_dump(
                        mode="json",
                        include={
                            "plan_id",
                            "owner_actor_ref",
                            "status",
                            "activity_kind",
                            "scheduled_window",
                            "location_ref",
                            "participant_refs",
                        },
                    )
                    for p in projection.plans
                    if p.plan_id in sources and p.status in {"planned", "active", "paused"}
                ],
                "location_refs": [
                    c.location_ref for c in getattr(manifest, "location_capabilities", ())
                ],
                "npc_activity_location_capabilities": [
                    c.model_dump(mode="json", exclude={"capability_ref"})
                    for c in getattr(manifest, "location_capabilities", ())
                    if c.availability_kind == "reviewed_schedule"
                ],
                "draft_schema_contract": "world-outlook-author-schema.6" if self._rolling_planning else "world-outlook-author-schema.5",
                "draft_schema": outlook_author_schema(
                    basis_refs=manifest.anchor_refs, npc_privacy_floors=npc_privacy_floors,
                    prior_opportunities=prior_head[1].opportunities if prior_head else (),
                    now=now,
                    rolling_planning=self._rolling_planning,
                    prior_threads=prior_head[1].threads if prior_head else (),
                    identity={"world_id": projection.world_id, "actor_ref": self.actor_ref, "parent_revision": view["revision"]} if native_result else None,
                    preparation=view.get("rolling_preparation") if native_result else None,
                    history_offer=history_offer if self._rolling_planning else None,
                    new_opportunity_ids=new_ids,
                ),
            }
            system = _ROLLING_SYSTEM if self._rolling_planning else _SYSTEM
            if native_result:
                system = system[:system.index("Return a JSON object, exactly")] + "For each response, call world_outlook_v6 once with your complete candidate. If validation rejects a candidate, your next response must be a corrected function call. Do not return an ordinary text/JSON message."
            messages = [
                {"role": "system", "content": system + "\n" + PRESENTATION_GUIDE + ("\n" + ROLLING_PRESENTATION_GUIDE if self._rolling_planning else "")},
                {"role": "user", "content": encode_outlook_request(packet, byte_limit=100_000)},
            ]
            if len(messages[1]["content"].encode()) > 100_000:
                self.store.finish(
                    op, state="technical_failure", payload={"code": "outlook_input_too_large"}
                )
                self._health = {"status": "technical_failure", "failure_code": "outlook_input_too_large"}
                return self.health()
        pinned_request = decode_outlook_request(messages[1]["content"])
        response_tools = None
        if pinned_request.get("response_tool_contract") in _NATIVE_RESULTS:
            response_tools = (cached_attempts[min(cached_attempts)]["response_tools"] if cached_attempts else
                              [outlook_result_tool(pinned_request["draft_schema"], allow_keep=not (
                                  pinned_request["rolling_preparation"]["due"] or pinned_request["rolling_preparation"]["outline_due"]))])
        draft = None
        for ordinal in range(2):
            raw = None
            try:
                if ordinal in cached_attempts:
                    cached = cached_attempts[ordinal]
                    if cached["status"] == "technical_failure":
                        break
                    messages, raw = cached["messages"], cached["raw"]
                    history = decode_outlook_request(messages[1]["content"]).get("history_material", [])
                else:
                    if configured_model != original_model:
                        raise ValueError("outlook original author changed before correction")
                    call_ref = f"{op}:author:{ordinal}"
                    if not self.store.claim(
                        call_ref,
                        kind="author_call",
                        payload={
                            "model": original_model,
                            "request_hash": _outlook_request_hash(messages, response_tools),
                        },
                    ):
                        self._health = {
                            "status": "running",
                            "operation_ref": op,
                            "failure_code": "outlook_author_outcome_unknown",
                        }
                        return self.health()
                    with model_call_scope(
                        "life_development_draft", action_id=f"world-outlook:{input_key}:{ordinal}"
                    ):
                        raw = await complete_json_object(self.author, messages, temperature=0.3,
                            **({"tools": response_tools, "tool_choice": {"type": "function", "function": {"name": "world_outlook_v6"}}} if response_tools else {}))
                obj = decode_outlook_response(raw, request_raw=messages[1]["content"])
                if (
                    isinstance(obj, dict)
                    and set(obj) == {"decision", "review_at", "reason"}
                    and obj["decision"] == "keep"
                ):
                    head = self.store.head()
                    if head is None or head[0] != view["revision"]:
                        raise ValueError("keep requires the exact prior outlook")
                    obj = {
                        "decision": "revise",
                        "draft": {
                            **head[1].model_dump(mode="json"),
                            "parent_revision": head[0],
                            "review_at": obj["review_at"],
                            "revise_reason": obj["reason"],
                        },
                    }
                if (
                    not isinstance(obj, dict)
                    or set(obj) != {"decision", "draft"}
                    or obj["decision"] != "revise"
                ):
                    raise ValueError(
                        "outlook response shape invalid: keep requires exactly decision, review_at, reason; "
                        "revise requires exactly decision, draft. Do not copy keep fields onto revise; "
                        "its schedule belongs inside draft."
                    )
                candidate = WorldOutlookDraft.model_validate_json(
                    json.dumps(obj["draft"], ensure_ascii=False)
                )
                # The exact persisted request is the authority for what was
                # offered, also on correction/restart. Legacy requests retain
                # their original history-material-only interpretation.
                pinned_packet = decode_outlook_request(messages[1]["content"])
                if pinned_packet.get("draft_schema_contract") not in {"world-outlook-author-schema.5", "world-outlook-author-schema.6"} and any(n.execution.kind == "planning" or n.execution.prepare_details for n in candidate.opportunities):
                    raise ValueError("the original outlook request did not offer planning or prepared details")
                if pinned_packet.get("condition_source_scope") == _WORKING_SOURCES:
                    offered_sources = frozenset(s["source_ref"] for s in pinned_packet["condition_sources"])
                    if any(c.source_ref not in offered_sources for n in candidate.opportunities for c in n.conditions):
                        raise ValueError("outlook condition source was not offered in the pinned working set")
                    current = self._catalog(projection, npc_refs, required_refs=offered_sources)
                    sources = {key: value for key, value in current.items() if key in offered_sources}
                history_offer = (OutlookHistoryOffer.model_validate_json(json.dumps(pinned_packet["history_offer"]))
                                 if "history_offer" in pinned_packet else None)
                self._validate_preparation(candidate, pinned_packet, projection)
                precise_privacy = pinned_packet.get("draft_schema_contract") in {"world-outlook-author-schema.4", "world-outlook-author-schema.5", "world-outlook-author-schema.6"}
                if history_offer is not None and precise_privacy:
                    history_offer.validate_privacy(candidate, precise_errors=True)
                validate_draft(
                    candidate,
                    projection=projection,
                    actor_ref=self.actor_ref,
                    parent_revision=view["revision"],
                    basis_refs=manifest.anchor_refs,
                    sources=sources,
                    npc_refs=npc_refs,
                    prior_draft=(self.store.head()[1] if view["revision"] else None),
                    offered_history=(history_offer.anchors if history_offer is not None else
                                     tuple(StoryAnchor.model_validate(h["anchor"]) for h in history)),
                    location_refs=tuple(
                        c.location_ref for c in getattr(manifest, "location_capabilities", ())
                    ),
                    location_capabilities=getattr(manifest, "location_capabilities", ()),
                    precise_errors=pinned_packet.get("draft_schema_contract") in {"world-outlook-author-schema.3", "world-outlook-author-schema.4", "world-outlook-author-schema.5", "world-outlook-author-schema.6"},
                    precise_privacy_errors=precise_privacy,
                )
                if history_offer is not None and not precise_privacy:
                    history_offer.validate_privacy(candidate)
                self._validate_offered_nodes(candidate, projection=projection)
            except (ValueError, TypeError) as exc:
                self._record_attempt(
                    op,
                    ordinal=ordinal,
                    payload={
                        "messages": messages,
                        **({"response_tools": response_tools} if response_tools else {}),
                        "raw": raw,
                        "status": "invalid" if raw is not None else "technical_failure",
                        "code": str(exc)[:1000],
                    },
                )
                if ordinal == 0 and raw is not None:
                    error = "Correct the same outlook using only the supplied sources. Exact error: " + str(exc)[:1000]
                    if pinned_request.get("response_tool_contract") in {"world-outlook-native.3", "world-outlook-native.4"}:
                        # These are the real, identity-checked native arguments.
                        # The local call id only pairs the validation feedback;
                        # no World effect or successful submission is invented.
                        call_id = f"call_world_outlook_validation_{ordinal}"
                        messages = [*messages,
                            {"role": "assistant", "content": None, "tool_calls": [{"id": call_id, "type": "function",
                                "function": {"name": "world_outlook_v6", "arguments": raw}}]},
                            {"role": "tool", "tool_call_id": call_id,
                             "content": "Validation rejected the candidate; no outlook was accepted. " + error}]
                    else:
                        messages = [*messages, {"role": "assistant", "content": raw}, {"role": "user", "content": error}]
                    continue
                break
            except Exception as exc:
                self._record_attempt(
                    op,
                    ordinal=ordinal,
                    payload={
                        "messages": messages,
                        **({"response_tools": response_tools} if response_tools else {}),
                        "raw": raw,
                        "status": "technical_failure",
                        "code": type(exc).__name__,
                    },
                )
                break
            self._record_attempt(
                op, ordinal=ordinal, payload={"messages": messages, "raw": raw, "status": "valid",
                    **({"response_tools": response_tools} if response_tools else {})}
            )
            draft = candidate
            break
        if draft is None:
            self.store.finish(
                op, state="technical_failure", payload={"code": "outlook_author_failed"}
            )
            self._health = {"status": "technical_failure", "failure_code": "outlook_author_failed"}
            return self.health()
        try:
            with self.ledger.serialized_commit_sequence():
                if cursor_of(self.ledger.project()) != cursor_of(projection):
                    self.store.finish(op, state="stale", payload={"cursor": cursor_of(projection)})
                    self._health = {"status": "stale"}
                    return self.health()
                revision = self.store.install(
                    draft,
                    evidence={
                        "operation_ref": op,
                        "model": original_model,
                        "request_hash": _outlook_request_hash(messages, response_tools),
                        "response_hash": digest(raw),
                        "cursor": cursor_of(projection),
                        "snapshot_hash": projection.semantic_hash,
                        **({"rolling_planning": _ROLLING_PLANNING} if "rolling_preparation" in pinned_packet else {}),
                        **({"authoring_constraints_hash": pinned_packet["authoring_constraints"]["digest"]}
                           if "authoring_constraints" in pinned_packet else {}),
                        "source_tokens": {k: v.token for k, v in sources.items()},
                        **({"condition_source_scope": _WORKING_SOURCES}
                           if pinned_packet.get("condition_source_scope") == _WORKING_SOURCES else {}),
                        "source_change_cursor": pinned_packet["current_outlook"].get("source_change_cursor", projection.ledger_sequence),
                        "basis_refs": list(manifest.anchor_refs),
                        "offer_hash": digest(self._relevant_offers({n.opportunity_id for n in draft.opportunities}, sources)
                                             if pinned_packet.get("condition_source_scope") == _WORKING_SOURCES else self.store.offers()),
                        "location_refs": [
                            c.location_ref for c in getattr(manifest, "location_capabilities", ())
                        ],
                        "location_capabilities": [
                            c.model_dump(mode="json", exclude={"capability_ref"})
                            for c in getattr(manifest, "location_capabilities", ())
                        ],
                    },
                )
            self.store.finish(op, state="accepted", payload={"revision": revision})
        except Exception as exc:
            self.store.finish(op, state="technical_failure", payload={"code": type(exc).__name__})
            self._health = {
                "status": "technical_failure",
                "failure_code": "outlook_admission_failed",
            }
            return self.health()
        self._health = {"status": "ready", "revision": revision}
        return self.health()

    def _record_attempt(self, operation_ref, *, ordinal, payload):
        payload = {**payload, "model": self.store.operation(operation_ref)["payload"]["model"]}
        self.store.attempt(operation_ref, ordinal=ordinal, payload=payload)
        call_ref = f"{operation_ref}:author:{ordinal}"
        if self.store.operation(call_ref) is not None:
            self.store.finish(call_ref, state="captured", payload={"attempt_hash": digest(payload)})

    def _history_material(self, projection, context, view, trigger_ref):
        from .recall_index import RecallCursor

        catalog = anchor_catalog(projection, actor_ref=self.actor_ref)
        old = self.store.head(as_of=cursor_of(projection))
        old_refs = (
            tuple(a.source_ref for t in old[1].threads for a in t.history_anchors) if old else ()
        )
        docs = (
            self._history_reader(
                expected_cursor=RecallCursor(**cursor_of(projection)),
                trigger_ref=trigger_ref,
                actor_ref=self.actor_ref,
                query_text=history_query_text(context=context, current_outlook=view),
                link_refs=old_refs[:32],
            )
            if self._history_reader and trigger_ref
            else ()
        )
        result = {}
        for doc in docs:
            if doc.actor_ref != self.actor_ref or doc.privacy_class == "withhold":
                continue
            ref = (
                doc.source_item_ref.split(":window:")[0]
                if doc.prehistory
                else (doc.settled_life.occurrence_id if doc.settled_life is not None else None)
            )
            anchor = catalog.get(ref)
            if anchor is None or not any(
                b.ref == anchor.authority_ref and b.immutable_hash == anchor.authority_hash
                for b in doc.source_bindings
            ):
                continue
            if doc.settled_life is not None and not doc.source_item_ref.endswith(":environment"):
                continue
            result.setdefault(ref, {"anchor": anchor.model_dump(mode="json"), "text": doc.text})
        # Carry an already chosen exact historical source without another
        # search/provider call; withdrawal still removes it from the catalog.
        for row in projection.prehistory_records:
            ref = row.record.record_id
            if ref in old_refs and ref in catalog and ref not in result:
                result[ref] = {
                    "anchor": catalog[ref].model_dump(mode="json"),
                    "text": row.record.statement,
                }
        return list(result.values())[:8]

    def story_history(self, *, projection=None, planning=False):
        state = projection or self.ledger.project()
        latest = {thread["thread_id"]: {"thread": thread, "realized_sources": []}
                  for thread in self.store.recent_threads(as_of=cursor_of(state))}
        if planning:
            for thread_id, value in latest.items():
                offers = self.store.offers_for_thread(thread_id, as_of=cursor_of(state))
                refs = {ref for offer in offers for ref in offer["payload"].get("effect_refs", ())}
                statuses = self._effect_states(state, refs)
                for offer in offers:
                    for ref in offer["payload"].get("effect_refs", ()):
                        if ref not in statuses:
                            raise ValueError("outlook realized source is unavailable in the pinned World")
                        for _ in range(offer["payload"]["node"]["thread_refs"].count(thread_id)):
                            if len(value["realized_sources"]) < 12:
                                value["realized_sources"].append({"source_ref": ref, "status": statuses[ref]})
            return tuple(latest.values())
        sources = {o.occurrence_id: o.status for o in state.world_occurrences}
        sources.update({p.plan_id: p.status for p in state.plans})
        sources.update(
            {
                e.event_id: e.event_type
                for e in state.committed_world_event_refs
                if e.event_type in {"NpcStatusChanged", "ActivityAbandoned"}
            }
        )
        experiences = {}
        for e in state.experiences:
            if self.actor_ref not in e.values.participant_refs:
                continue
            for b in e.values.source_bindings:
                ref = getattr(getattr(b, "settlement", b), "occurrence_id", None)
                if ref is not None:
                    experiences.setdefault(ref, []).append(e.experience_id)
        for offer in reversed(self.store.offers()):
            if offer["state"] != "considered":
                continue
            for ref in offer["payload"].get("effect_refs", ()):
                if ref not in sources:
                    continue
                exp = experiences.get(ref, [])
                memories = [
                    c.candidate_id
                    for c in state.memory_candidates
                    if c.values.status == "active"
                    and any(b.source_id in exp for b in c.values.source_bindings)
                ]
                for thread_ref in offer["payload"]["node"]["thread_refs"]:
                    if thread_ref in latest and len(latest[thread_ref]["realized_sources"]) < 12:
                        latest[thread_ref]["realized_sources"].append(
                            {
                                "source_ref": ref,
                                "status": sources[ref],
                                "experience_refs": exp,
                                "memory_refs": memories,
                            }
                        )
        return tuple(latest.values())[:12]

    def _validate_offered_nodes(self, draft, *, projection):
        previous = {
            r["payload"]["node"]["opportunity_id"]: r["payload"]["node"]
            for r in self.store.offers(node_ids={node.opportunity_id for node in draft.opportunities})
            if r["state"] in {"considered", "running"}
            or (self._executor is not None and self._executor.has_effect(r["payload"]["node"]))
        }
        for n in draft.opportunities:
            if (
                n.opportunity_id in previous
                and n.model_dump(mode="json") != previous[n.opportunity_id]
            ):
                raise ValueError(
                    f"opportunity {n.opportunity_id!r}: an already considered opportunity cannot be rewritten or reoffered; "
                    "omit it from the new draft if expired, otherwise retain its exact original value or omit it. "
                    "Its recorded history, effects and failures remain."
                )
            if n.opportunity_id not in previous and n.execution.planning is not None:
                from .world_planning import validate_planning_publication
                from .world_outlook_execution import occurrence_ref

                try:
                    validate_planning_publication(n.execution.planning, projection=projection,
                        actor_ref=self.actor_ref,
                        occurrence_ref=occurrence_ref(projection.world_id, self.actor_ref, n.opportunity_id),
                        store=self._content_store, ledger=self.ledger)
                except ValueError as exc:
                    raise ValueError(f"outlook opportunity {n.opportunity_id!r}: {exc}") from exc

    def ready(self, *, npc_refs=None):
        return tuple(
            n for n in (*self.view(npc_refs=npc_refs)["opportunities"], *self.scheduled()) if n["state"] == "ready"
        )

    def _scheduled_offers(self):
        if not self._rolling_planning:
            return {}
        from .world_planning import planning_event_offers
        state = self.ledger.project()
        return planning_event_offers(projection=state, publications=self._planning_publications(state),
                                     ledger=self.ledger, actor_ref=self.actor_ref)

    def scheduled(self):
        values = self._scheduled_offers()
        instances = {origin["planning_instance_origin"]["instance_ref"] for _, origin in values.values()}
        operations = self.store.planning_offers(instances) if instances else ()
        # A replaced publication gets a new execution generation, but an actual
        # activation consumes the stable original instance across all generations.
        consumed = set()
        for row in operations:
            recovered = self._executor.recover(node=row["payload"]["node"]) if self._executor is not None else None
            if row["state"] == "considered" or getattr(recovered, "status", None) == "occurrence_committed":
                consumed.add(row["payload"]["origin"]["planning_instance_origin"]["instance_ref"])
        running = {r["payload"]["node"]["opportunity_id"] for r in operations if r["state"] == "running"}
        now = self.ledger.project().logical_time
        retry_epoch = int(now.timestamp()) // 3600
        waiting_retry = {r["payload"]["node"]["opportunity_id"] for r in operations
                         if r["state"] == "technical_failure" and r["payload"]["retry_epoch"] == retry_epoch}
        return tuple({**node, "state": "offered" if origin["planning_instance_origin"]["instance_ref"] in consumed else "delivery_pending" if key in running
                      else "retry_wait" if key in waiting_retry
                      else "ready" if datetime.fromisoformat(node["opens_at"]) <= now else "waiting"}
                     for key, (node, origin) in values.items())

    def begin_offer(self, node, *, wake_event_ref):
        with self.ledger.serialized_commit_sequence():
            current = next(
                (n for n in self.ready() if n["opportunity_id"] == node["opportunity_id"]), None
            )
            if current is None or {k: v for k, v in current.items() if k != "state"} != {
                k: v for k, v in node.items() if k != "state"
            }:
                return None
            scheduled = self._scheduled_offers().get(node["opportunity_id"])
            return self._claim_offer(node, wake_event_ref=wake_event_ref,
                                     prepared_origin=scheduled[1] if scheduled else None)

    def _claim_offer(self, node, *, wake_event_ref, prepared_origin=None):
        clean = {k: v for k, v in node.items() if k != "state"}
        terminal = {
            r["payload"]["node"]["opportunity_id"]
            for r in self.store.offers(node_ids=(clean["opportunity_id"],))
            if r["state"] in {"running", "considered"}
        }
        if clean["opportunity_id"] in terminal:
            return None
        op = "offer:" + digest(
            {
                "node_id": clean["opportunity_id"],
                "retry_epoch": int(self.ledger.project().logical_time.timestamp()) // 3600,
            }
        )
        if prepared_origin is not None:
            origin = prepared_origin
        else:
            head = self.store.head()
            origin = {
                key: head[2][key] for key in ("model", "request_hash", "response_hash", "operation_ref")
            }
            origin["revision"] = head[0]
            origin["actor_ref"] = self.actor_ref
            origin["location_refs"] = head[2].get("location_refs", [])
            origin["location_capabilities"] = head[2].get("location_capabilities", [])
            from .lived_story import LivedStoryNavigation

            anchors = {
                a.source_ref: a
                for t in head[1].threads
                if t.thread_id in clean["thread_refs"]
                for a in t.history_anchors
            }
            before = None
            while len(anchors) < 24:
                page = self.store.navigation_offer_page(clean["thread_refs"], before=before)
                if not page:
                    break
                catalog = self._anchors(self.ledger.project(), (ref for _, offer in page for ref in offer["payload"].get("effect_refs", ())))
                for _, offer in page:
                    for ref in offer["payload"].get("effect_refs", ()):
                        if ref in catalog and len(anchors) < 24:
                            anchors[ref] = catalog[ref]
                before = page[-1][0]
            origin["story_navigation"] = LivedStoryNavigation(
                actor_ref=self.actor_ref,
                thread_refs=tuple(
                    "lived-thread:" + digest([self.ledger.world_id, self.actor_ref, t])
                    for t in clean["thread_refs"]
                ),
                anchors=tuple(anchors.values())[-24:],
            ).model_dump(mode="json")
        claimed = self.store.claim(
            op,
            kind="offer",
            payload={
                "node": clean,
                "wake_event_ref": wake_event_ref,
                "claim_cursor": cursor_of(self.ledger.project()),
                "origin": origin,
                "retry_epoch": int(self.ledger.project().logical_time.timestamp()) // 3600,
            },
        )
        return op if claimed else None

    def delivery_cursor(self, operation_ref):
        from .schemas import ProjectionCursor

        return ProjectionCursor(**self.store.operation(operation_ref)["payload"]["claim_cursor"])

    def execute_offer(self, node, *, wake_event_ref):
        with self.ledger.serialized_commit_sequence():
            return self._execute_offer(node, wake_event_ref=wake_event_ref)

    def _execute_offer(self, node, *, wake_event_ref):
        if self._executor is None:
            raise ValueError("central outlook executor is not installed")
        operation = self.begin_offer(node, wake_event_ref=wake_event_ref)
        if operation is None:
            return None
        try:
            result = self._executor.execute(
                node=node,
                wake_event_ref=wake_event_ref,
                expected_cursor=self.delivery_cursor(operation),
                origin=self.store.operation(operation)["payload"]["origin"],
            )
        except Exception as exc:
            self.record_failure(exc)
            from types import SimpleNamespace

            result = SimpleNamespace(
                status="technical_failure", reason_code="outlook_execution_failed"
            )
        self.finish_offer(operation, node=node, result=result, wake_event_ref=wake_event_ref)
        return result

    def _offers_needing_reconciliation(self, state):
        """Re-read running operations and actual intermediate effects, not settled history.

        Called inside the ledger's serialized sequence. This derived frontier
        never consumes a running row: its receipt may be updated in place.
        """
        from .sqlite_ledger import SQLiteWorldLedger
        from .world_outlook_execution import occurrence_ref

        if type(self.ledger) is not SQLiteWorldLedger or state is not self.ledger.project():
            return self.store.offers()
        generation = (self.ledger.verified_history_generation(), self.store.operation_generation())
        previous_generation = self._offer_frontier_generation
        self._offer_frontier_generation = None
        if previous_generation != generation or state.ledger_sequence < self._offer_frontier_sequence:
            self._offer_frontier = 0
            self._unsettled_offers = {}
        # Existing rows can finish without a new rowid.
        for ref in tuple(self._unsettled_offers):
            operation = self.store.operation(ref)
            if operation is None:
                raise ValueError("pending outlook operation disappeared")
            self._unsettled_offers[ref] = {**operation, "operation_ref": ref}
        while True:
            page = self.store.offer_page(after=self._offer_frontier)
            if not page:
                break
            self._unsettled_offers.update((offer["operation_ref"], offer) for _, offer in page)
            self._offer_frontier = page[-1][0]
            # Bound each cold scan page too; only actual open work stays in RAM.
            self._retain_unsettled_offers(state, occurrence_ref)
        self._retain_unsettled_offers(state, occurrence_ref)
        self._offer_frontier_sequence = state.ledger_sequence
        self._offer_frontier_generation = generation
        return tuple(self._unsettled_offers.values())

    def _retain_unsettled_offers(self, state, occurrence_ref):
        rows = tuple(self._unsettled_offers.items())
        for start in range(0, len(rows), 256):
            batch = rows[start:start + 256]
            refs = {key: occurrence_ref(state.world_id, self.actor_ref, offer["payload"]["node"]["opportunity_id"])
                    for key, offer in batch}
            statuses = self._effect_states(state, set(refs.values()))
            for key, offer in batch:
                if offer["state"] != "running" and statuses.get(refs[key]) != "committed":
                    del self._unsettled_offers[key]

    def reconcile_accepted_offers(self, *, wake_event_ref):
        """Finish interrupted delivery against current conditions, without models."""
        if self._executor is None:
            return 0
        self._executor.advance_npcs(wake_event_ref=wake_event_ref)
        recovered = 0
        with self.ledger.serialized_commit_sequence():
            current = {n["opportunity_id"]: n for n in (*self.view()["opportunities"], *self.scheduled())}
            offers = self._offers_needing_reconciliation(self.ledger.project())
            for offer in offers:
                node = offer["payload"]["node"]
                visible = current.get(node["opportunity_id"])
                if visible is None or visible["state"] in {"expired", "invalidated"}:
                    self._executor.cancel_pending(node=node, wake_event_ref=wake_event_ref)
        for offer in offers:
            if offer["state"] != "running":
                continue
            payload = offer["payload"]
            with self.ledger.serialized_commit_sequence():
                result = self._executor.recover(node=payload["node"])
                if result is None:
                    current = next(
                        (
                            n
                            for n in (*self.view()["opportunities"], *self.scheduled())
                            if n["opportunity_id"] == payload["node"]["opportunity_id"]
                        ),
                        None,
                    )
                    if (current is not None and current["state"] == "delivery_pending"
                            and {k: v for k, v in current.items() if k != "state"} == payload["node"]):
                        from .schemas import ProjectionCursor

                        result = self._executor.execute(
                            node=payload["node"],
                            wake_event_ref=wake_event_ref,
                            origin=payload["origin"],
                            expected_cursor=ProjectionCursor(**cursor_of(self.ledger.project())),
                        )
                    else:
                        from types import SimpleNamespace

                        result = SimpleNamespace(
                            status="rejected",
                            reason_code="outlook_interrupted_delivery_conditions_changed",
                        )
                self.finish_offer(
                    offer["operation_ref"],
                    node=payload["node"],
                    result=result,
                    wake_event_ref=wake_event_ref,
                )
                recovered += 1
        return recovered

    def advance_prepaid_npcs(self, *, wake_event_ref):
        if self._rolling_planning and self._executor is not None:
            self.reconcile_accepted_offers(wake_event_ref=wake_event_ref)
            # Publication is execution of an already paid external document.
            # It must not wait for unrelated character outcomes or new authoring.
            for node in [n for n in self.view()["opportunities"]
                         if n["state"] == "ready" and n["execution"]["kind"] == "planning"][:4]:
                self.execute_offer(node, wake_event_ref=wake_event_ref)
        if self.event_details is not None:
            self.event_details.advance(wake_event_ref=wake_event_ref)
        if self._executor is not None:
            self._executor.advance_npcs(wake_event_ref=wake_event_ref)
            # Authored external appointments use the existing prepaid clock
            # lane. A slow author/detail task cannot block all due appointments.
            due = sorted((n for n in self.scheduled() if n["state"] == "ready"),
                         key=lambda n: (n["opens_at"], n["opportunity_id"]))
            for node in due[:8]:
                self.execute_offer(node, wake_event_ref=wake_event_ref)

    def finish_offer(self, op, *, node, result, wake_event_ref):
        state = self.ledger.project()
        status = getattr(result, "status", "technical_failure")
        reason = getattr(result, "reason_code", "")
        effects = []
        occurrence_id = getattr(result, "occurrence_id", None)
        if occurrence_id and any(
            o.occurrence_id == occurrence_id and o.status in {"active", "settled"}
            for o in state.world_occurrences
        ):
            effects.append(occurrence_id)
        plan_id = getattr(result, "plan_id", None)
        if status == "npc_activity_started" and any(
            p.plan_id == plan_id and p.owner_actor_ref == node["npc_ref"] for p in state.plans
        ):
            effects.append(plan_id)
        event_ref = getattr(result, "effect_event_ref", None)
        if (
            status == "npc_effect_committed"
            and self.ledger.lookup_event_commit(event_ref) is not None
        ):
            effects.append(event_ref)
        terminal = status in {
            "occurrence_committed",
            "npc_activity_started",
            "npc_effect_committed",
        } and bool(effects)
        previous = self.store.operation(op)
        if previous and previous["state"] == "considered":
            if (
                previous["payload"]["node"] != {k: v for k, v in node.items() if k != "state"}
                or previous["payload"].get("effect_refs") != effects
            ):
                raise ValueError("outlook delivery recovery contradicts its accepted receipt")
            return
        self.store.finish(
            op,
            state="considered" if terminal else "technical_failure",
            payload={
                **self.store.operation(op)["payload"],
                "node": {k: v for k, v in node.items() if k != "state"},
                "wake_event_ref": wake_event_ref,
                "result_cursor": cursor_of(state),
                "result_status": status,
                "result_reason": reason,
                "effect_refs": effects,
            },
        )

    def next_due(self, *, npc_refs=None, include_preparation=True):
        from datetime import datetime

        state = self.ledger.project()
        view = self.view(npc_refs=npc_refs)
        dates = [
            datetime.fromisoformat(n["opens_at"])
            for n in view["opportunities"]
            if n["state"] == "waiting"
        ]
        scheduled = self.scheduled()
        dates.extend(datetime.fromisoformat(n["opens_at"]) for n in scheduled if n["state"] == "waiting")
        if any(n["state"] == "retry_wait" for n in scheduled):
            dates.append(datetime.fromtimestamp((int(state.logical_time.timestamp()) // 3600 + 1) * 3600,
                                                tz=state.logical_time.tzinfo))
        if view.get("review_at"):
            dates.append(datetime.fromisoformat(view["review_at"]))
        if self._rolling_planning:
            if include_preparation and (due := self.next_preparation_due(projection=state, view=view)) is not None:
                dates.append(max(due, state.logical_time + timedelta(seconds=1)))
            publications = [n for n in view["opportunities"] if n["execution"]["kind"] == "planning"]
            if any(n["state"] in {"ready", "delivery_pending"} for n in publications):
                dates.append(state.logical_time + timedelta(seconds=1))
            if any(n["state"] == "retry_wait" for n in publications):
                dates.append(datetime.fromtimestamp((int(state.logical_time.timestamp()) // 3600 + 1) * 3600,
                                                    tz=state.logical_time.tzinfo))
        if self._executor is not None and (due := self._executor.next_npc_due()) is not None:
            dates.append(due)
        if self.event_details is not None and (due := self.event_details.next_due()) is not None:
            dates.append(due)
        return min((d for d in dates if d > state.logical_time), default=None)

    def close(self):
        self.store.close()
