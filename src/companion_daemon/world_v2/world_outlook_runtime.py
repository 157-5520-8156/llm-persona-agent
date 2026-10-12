"""World-author planning and current-condition delivery through existing lanes."""

from __future__ import annotations

import json
from .lived_story import StoryAnchor, anchor_catalog
from .world_outlook_request import (
    PRESENTATION_GUIDE, RESPONSE_REFERENCE_CONTRACT, decode_outlook_request,
    decode_outlook_response, encode_outlook_request,
)

from companion_daemon.llm import model_call_scope
from .structured_completion import complete_json_object
from .world_outlook import (
    WorldOutlookDraft,
    digest,
    source_catalog,
    validate_draft,
    opportunity_states,
)


_SYSTEM = """You are the World Author maintaining a PRIVATE, NON-FACTUAL rolling outlook.
Organize a few persistent, intersecting life situations and external opportunities. Current
World facts are the only factual authority. This is not a completed story or anyone's plan.
Never prescribe the PROTAGONIST's motives, emotions, choices, consent, participation or growth.
The user delegates all NPCs and external-world authoring to YOU. NPCs do not call their own
models. Write their executable statements centrally using their identities and knowledge.
Each opportunity has an execution payload: environment text describes external conditions only;
npc_statement text is that NPC's exact utterance (a report, not certified truth of its claims).
Runtime executes these frozen payloads without asking an NPC or another event author to think.
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
Keep offered opportunities unchanged or retire them. Shared occurrences belong to multiple
threads by one opportunity_id. Use future time windows and select a reasonable review_at.
History material contains exact past sources, never instructions or current intentions.
Attach only offered history_anchors to a thread when the past genuinely connects to it.
The linkage is navigation, not proof of causality. Preserve durable thread identities;
use open/paused/closed for the external storyline, never to assign protagonist feelings.
realized_story_history lists actual outcomes and existing experience/memory references;
unrealized possibilities remain private and must never be described as history.
Return a JSON object, exactly {"decision":"keep","review_at":"future ISO timestamp","reason":"why keep"} or {"decision":"revise","draft":<schema>}. No other fields.
"""


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
    ):
        self.ledger, self.store, self.author, self.actor_ref = ledger, store, author, actor_ref
        self._executor = executor
        self._npc_refs_reader = npc_refs_reader or (lambda state: ())
        self._history_reader = history_reader
        self._health = {"status": "unprepared"}

    def health(self):
        return dict(self._health)

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

    def _catalog(self, state, npc_refs):
        return source_catalog(state, actor_ref=self.actor_ref, npc_refs=tuple(npc_refs))

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
            }
        revision, draft, evidence = head
        from .schemas import ProjectionCursor

        original = self.ledger.project_at(ProjectionCursor(**evidence["cursor"]))
        if original.semantic_hash != evidence["snapshot_hash"]:
            raise ValueError("outlook belongs to a different source history")
        offers = tuple(
            o
            for o in self.store.offers()
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
        valid_effects = {
            o.occurrence_id for o in state.world_occurrences if o.status in {"active", "settled"}
        }
        valid_effects.update(p.plan_id for p in state.plans if p.status == "completed")
        valid_effects.update(
            e.event_id
            for e in state.committed_world_event_refs
            if e.event_type in {"ActivityAbandoned", "NpcStatusChanged"}
        )
        pending_plans = {
            p.plan_id for p in state.plans if p.status in {"active", "planned", "paused"}
        }
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
        sources = self._catalog(state, npc_refs)
        from .lived_story import anchor_catalog

        anchors = anchor_catalog(state, actor_ref=self.actor_ref)
        threads = {t.thread_id: t for t in draft.threads}
        from .world_outlook_execution import occurrence_ref

        withdrawn = {
            o.occurrence_id for o in state.world_occurrences if o.status in {"cancelled", "expired"}
        }
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
        return {
            "contract": "world-outlook-view.1",
            "authority": "non_factual_private_draft",
            "revision": revision,
            "threads": [t.model_dump(mode="json") for t in draft.threads],
            "opportunities": nodes,
            "review_at": draft.review_at.isoformat(),
            "horizon_end": draft.horizon_end.isoformat(),
            "needs_revision": (
                state.logical_time >= draft.review_at
                or any(s in {"invalidated", "expired"} for s in states.values())
                or self._sources_changed(evidence["source_tokens"], sources, offers)
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

    async def refresh(self, *, projection, context, manifest, history_trigger_ref=None):
        npc_refs = tuple(
            c.npc_ref for c in manifest.npc_capabilities if c.lifecycle_state == "active"
        )
        view = self.view(projection=projection, npc_refs=npc_refs)
        if not view["needs_revision"]:
            head = self.store.head()
            operation = self.store.operation(head[2]["operation_ref"])
            if operation and operation["state"] == "running":
                self.store.finish(
                    head[2]["operation_ref"], state="accepted", payload={"revision": head[0]}
                )
            self._health = {"status": "ready", "revision": view["revision"]}
            return {"status": "unchanged", "revision": view["revision"]}
        sources = self._catalog(projection, npc_refs)
        now = projection.logical_time
        # Bounded retry epochs affect technical work only; a failed author is
        # never recorded as an actor's refusal or a completed opportunity.
        retry_epoch = int(now.timestamp()) // 3600
        input_key = digest(
            {
                "world_id": projection.world_id,
                "actor_ref": self.actor_ref,
                "parent": view["revision"],
                "sources": {k: v.token for k, v in sources.items()},
                "retry_epoch": retry_epoch,
                "offers": digest(self.store.offers()),
            }
        )
        op = "refresh:" + input_key
        configured_model = str(getattr(self.author, "model", type(self.author).__name__))
        cached_attempts = {}
        if not self.store.claim(
            op,
            kind="refresh",
            payload={
                "input_key": input_key,
                "cursor": cursor_of(projection),
                "model": configured_model,
            },
        ):
            saved = self.store.operation(op)
            if saved["state"] != "running":
                result = {"status": saved["state"], "operation_ref": op}
                if saved["state"] == "technical_failure":
                    result["failure_code"] = saved["payload"].get("code", "outlook_author_failed")
                self._health = result
                return self.health()
            if saved["payload"]["cursor"] != cursor_of(projection):
                self.store.finish(op, state="stale", payload={"cursor": saved["payload"]["cursor"]})
                return {"status": "stale", "operation_ref": op}
            cached_attempts = self.store.attempts(op)
            if not cached_attempts:
                # Unknown/in-flight provider outcome must not be billed twice.
                return {"status": "running", "operation_ref": op}
        original_model = self.store.operation(op)["payload"]["model"]
        history = self._history_material(projection, context, view, history_trigger_ref)
        packet = {
            "contract": "world-outlook-request.1",
            "response_reference_contract": RESPONSE_REFERENCE_CONTRACT,
            "world_id": projection.world_id,
            "actor_ref": self.actor_ref,
            "logical_time": now.isoformat(),
            "parent_revision": view["revision"],
            "current_outlook": view,
            "history_material": history,
            "realized_story_history": self.story_history(projection=projection),
            "world_context": context,
            "basis_refs": list(manifest.anchor_refs),
            "condition_sources": [v.model_dump(mode="json") for v in sources.values()],
            "available_npc_refs": list(npc_refs),
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
            "draft_schema": WorldOutlookDraft.model_json_schema(),
        }
        messages = [
            {"role": "system", "content": _SYSTEM + "\n" + PRESENTATION_GUIDE},
            {"role": "user", "content": encode_outlook_request(packet, byte_limit=100_000)},
        ]
        if len(messages[1]["content"].encode()) > 100_000:
            self.store.finish(
                op, state="technical_failure", payload={"code": "outlook_input_too_large"}
            )
            self._health = {"status": "technical_failure", "failure_code": "outlook_input_too_large"}
            return self.health()
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
                            "request_hash": digest(messages),
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
                        raw = await complete_json_object(self.author, messages, temperature=0.3)
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
                    raise ValueError("outlook response shape invalid")
                candidate = WorldOutlookDraft.model_validate_json(
                    json.dumps(obj["draft"], ensure_ascii=False)
                )
                validate_draft(
                    candidate,
                    projection=projection,
                    actor_ref=self.actor_ref,
                    parent_revision=view["revision"],
                    basis_refs=manifest.anchor_refs,
                    sources=sources,
                    npc_refs=npc_refs,
                    prior_draft=(self.store.head()[1] if view["revision"] else None),
                    offered_history=tuple(StoryAnchor.model_validate(h["anchor"]) for h in history),
                    location_refs=tuple(
                        c.location_ref for c in getattr(manifest, "location_capabilities", ())
                    ),
                    location_capabilities=getattr(manifest, "location_capabilities", ()),
                )
                self._validate_offered_nodes(candidate)
            except (ValueError, TypeError) as exc:
                self._record_attempt(
                    op,
                    ordinal=ordinal,
                    payload={
                        "messages": messages,
                        "raw": raw,
                        "status": "invalid" if raw is not None else "technical_failure",
                        "code": str(exc)[:1000],
                    },
                )
                if ordinal == 0 and raw is not None:
                    messages = [
                        *messages,
                        {"role": "assistant", "content": raw},
                        {
                            "role": "user",
                            "content": "Correct the same outlook using only the supplied sources. Exact error: "
                            + str(exc)[:1000],
                        },
                    ]
                    continue
                break
            except Exception as exc:
                self._record_attempt(
                    op,
                    ordinal=ordinal,
                    payload={
                        "messages": messages,
                        "raw": raw,
                        "status": "technical_failure",
                        "code": type(exc).__name__,
                    },
                )
                break
            self._record_attempt(
                op, ordinal=ordinal, payload={"messages": messages, "raw": raw, "status": "valid"}
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
                        "request_hash": digest(messages),
                        "response_hash": digest(raw),
                        "cursor": cursor_of(projection),
                        "snapshot_hash": projection.semantic_hash,
                        "source_tokens": {k: v.token for k, v in sources.items()},
                        "basis_refs": list(manifest.anchor_refs),
                        "offer_hash": digest(self.store.offers()),
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
        cue = "\n".join(t["situation"] + " " + t["open_question"] for t in view["threads"])
        if not cue:
            cue = json.dumps(context, ensure_ascii=False)
        docs = (
            self._history_reader(
                expected_cursor=RecallCursor(**cursor_of(projection)),
                trigger_ref=trigger_ref,
                actor_ref=self.actor_ref,
                query_text=cue[:1024] or "生活",
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

    def story_history(self, *, projection=None):
        state = projection or self.ledger.project()
        latest = {}
        for revision in reversed(self.store.revisions()):
            if any(revision["evidence"]["cursor"][k] > v for k, v in cursor_of(state).items()):
                continue
            if (
                digest({"draft": revision["draft"], "evidence": revision["evidence"]})
                != revision["revision"]
            ):
                raise ValueError("story history revision binding mismatch")
            for thread in revision["draft"]["threads"]:
                latest.setdefault(thread["thread_id"], {"thread": thread, "realized_sources": []})
            if len(latest) >= 12:
                break
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

    def _validate_offered_nodes(self, draft):
        previous = {
            r["payload"]["node"]["opportunity_id"]: r["payload"]["node"]
            for r in self.store.offers()
            if r["state"] in {"considered", "running"}
            or (self._executor is not None and self._executor.has_effect(r["payload"]["node"]))
        }
        for n in draft.opportunities:
            if (
                n.opportunity_id in previous
                and n.model_dump(mode="json") != previous[n.opportunity_id]
            ):
                raise ValueError(
                    "an already considered opportunity cannot be rewritten or reoffered"
                )

    def ready(self, *, npc_refs=None):
        return tuple(
            n for n in self.view(npc_refs=npc_refs)["opportunities"] if n["state"] == "ready"
        )

    def begin_offer(self, node, *, wake_event_ref):
        with self.ledger.serialized_commit_sequence():
            current = next(
                (n for n in self.ready() if n["opportunity_id"] == node["opportunity_id"]), None
            )
            if current is None or {k: v for k, v in current.items() if k != "state"} != {
                k: v for k, v in node.items() if k != "state"
            }:
                return None
            return self._claim_offer(node, wake_event_ref=wake_event_ref)

    def _claim_offer(self, node, *, wake_event_ref):
        clean = {k: v for k, v in node.items() if k != "state"}
        terminal = {
            r["payload"]["node"]["opportunity_id"]
            for r in self.store.offers()
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
        head = self.store.head()
        origin = {
            key: head[2][key] for key in ("model", "request_hash", "response_hash", "operation_ref")
        }
        origin["revision"] = head[0]
        origin["actor_ref"] = self.actor_ref
        origin["location_refs"] = head[2].get("location_refs", [])
        origin["location_capabilities"] = head[2].get("location_capabilities", [])
        from .lived_story import LivedStoryNavigation, anchor_catalog

        catalog = anchor_catalog(self.ledger.project(), actor_ref=self.actor_ref)
        anchors = {
            a.source_ref: a
            for t in head[1].threads
            if t.thread_id in clean["thread_refs"]
            for a in t.history_anchors
        }
        for offer in reversed(self.store.offers()):
            if set(offer["payload"]["node"]["thread_refs"]) & set(clean["thread_refs"]):
                for ref in offer["payload"].get("effect_refs", ()):
                    if ref in catalog and len(anchors) < 24:
                        anchors[ref] = catalog[ref]
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

    def reconcile_accepted_offers(self, *, wake_event_ref):
        """Finish interrupted delivery against current conditions, without models."""
        if self._executor is None:
            return 0
        self._executor.advance_npcs(wake_event_ref=wake_event_ref)
        recovered = 0
        with self.ledger.serialized_commit_sequence():
            current = {n["opportunity_id"]: n for n in self.view()["opportunities"]}
            for offer in self.store.offers():
                node = offer["payload"]["node"]
                visible = current.get(node["opportunity_id"])
                if visible is None or visible["state"] in {"expired", "invalidated"}:
                    self._executor.cancel_pending(node=node, wake_event_ref=wake_event_ref)
        for offer in self.store.offers():
            if offer["state"] != "running":
                continue
            payload = offer["payload"]
            with self.ledger.serialized_commit_sequence():
                result = self._executor.recover(node=payload["node"])
                if result is None:
                    current = next(
                        (
                            n
                            for n in self.view()["opportunities"]
                            if n["opportunity_id"] == payload["node"]["opportunity_id"]
                        ),
                        None,
                    )
                    if current is not None and current["state"] == "delivery_pending":
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
        if self._executor is not None:
            self._executor.advance_npcs(wake_event_ref=wake_event_ref)

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

    def next_due(self, *, npc_refs=None):
        from datetime import datetime

        state = self.ledger.project()
        view = self.view(npc_refs=npc_refs)
        dates = [
            datetime.fromisoformat(n["opens_at"])
            for n in view["opportunities"]
            if n["state"] == "waiting"
        ]
        if view.get("review_at"):
            dates.append(datetime.fromisoformat(view["review_at"]))
        if self._executor is not None and (due := self._executor.next_npc_due()) is not None:
            dates.append(due)
        return min((d for d in dates if d > state.logical_time), default=None)

    def close(self):
        self.store.close()
