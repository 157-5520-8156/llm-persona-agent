"""World-author planning and current-condition delivery through existing lanes."""

from __future__ import annotations

import json

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
Do not narrate protagonist actions or completed user-channel sends in environment text.
A refusal, delay, novel user suggestion, cancellation, or quiet life may change or end a
thread. Do not rename and repeat a rejected pressure to force a planned outcome. Preserve
unaffected threads; actual history cannot be rewritten. Ordinary life need not serve a climax.
Only offered basis refs/source handles/NPC identities may be used. A condition tests technical
state or an exact token, never inferred emotions. after_opportunities means workflow after a
prior consideration produced a real accepted effect, not evidence of narrative causality.
Keep offered opportunities unchanged or retire them. Shared occurrences belong to multiple
threads by one opportunity_id. Use future time windows and select a reasonable review_at.
Return exactly {"decision":"keep","review_at":"future ISO timestamp","reason":"why keep"} or {"decision":"revise","draft":<schema>}. No other fields.
"""


def cursor_of(state):
    return {
        k: getattr(state, k) for k in ("world_revision", "deliberation_revision", "ledger_sequence")
    }


class WorldOutlookRuntime:
    def __init__(
        self, *, ledger, store, author, actor_ref: str, npc_refs_reader=None, executor=None
    ):
        self.ledger, self.store, self.author, self.actor_ref = ledger, store, author, actor_ref
        self._executor = executor
        self._npc_refs_reader = npc_refs_reader or (lambda state: ())
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
        produced = {
            x["payload"]["node"]["opportunity_id"]
            for x in offers
            if x["state"] == "considered"
            and set(x["payload"].get("effect_refs", ())) & valid_effects
        }
        sources = self._catalog(state, npc_refs)
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
        states = opportunity_states(
            draft,
            now=state.logical_time,
            sources=sources,
            offered=terminal,
            produced=produced,
            invalidated=invalidated,
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
                and source.source_kind == "occurrence"
                and source.status in {"committed", "active", "settled"}
            ):
                continue
            return True
        return False

    async def refresh(self, *, projection, context, manifest):
        npc_refs = tuple(
            c.npc_ref for c in manifest.npc_capabilities if c.lifecycle_state == "active"
        )
        view = self.view(projection=projection, npc_refs=npc_refs)
        if not view["needs_revision"]:
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
        if not self.store.claim(
            op, kind="refresh", payload={"input_key": input_key, "cursor": cursor_of(projection)}
        ):
            saved = self.store.operation(op)
            return {"status": saved["state"], "operation_ref": op}
        packet = {
            "contract": "world-outlook-request.1",
            "world_id": projection.world_id,
            "actor_ref": self.actor_ref,
            "logical_time": now.isoformat(),
            "parent_revision": view["revision"],
            "current_outlook": view,
            "world_context": context,
            "basis_refs": list(manifest.anchor_refs),
            "condition_sources": [v.model_dump(mode="json") for v in sources.values()],
            "available_npc_refs": list(npc_refs),
            "draft_schema": WorldOutlookDraft.model_json_schema(),
        }
        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": json.dumps(packet, ensure_ascii=False)},
        ]
        if len(messages[1]["content"].encode()) > 100_000:
            self.store.finish(
                op, state="technical_failure", payload={"code": "outlook_input_too_large"}
            )
            return {"status": "technical_failure", "failure_code": "outlook_input_too_large"}
        draft = None
        for ordinal in range(2):
            raw = None
            try:
                with model_call_scope(
                    "life_development_draft", action_id=f"world-outlook:{input_key}:{ordinal}"
                ):
                    raw = await complete_json_object(self.author, messages, temperature=0.3)
                obj = json.loads(raw)
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
                )
                self._validate_offered_nodes(candidate)
            except (ValueError, TypeError) as exc:
                self.store.attempt(
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
                self.store.attempt(
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
            self.store.attempt(
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
                        "model": str(getattr(self.author, "model", type(self.author).__name__)),
                        "request_hash": digest(messages),
                        "response_hash": digest(raw),
                        "cursor": cursor_of(projection),
                        "snapshot_hash": projection.semantic_hash,
                        "source_tokens": {k: v.token for k, v in sources.items()},
                        "basis_refs": list(manifest.anchor_refs),
                        "offer_hash": digest(self.store.offers()),
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

    def _validate_offered_nodes(self, draft):
        previous = {
            r["payload"]["node"]["opportunity_id"]: r["payload"]["node"]
            for r in self.store.offers()
            if r["state"] in {"considered", "running"}
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
        recovered = 0
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
        terminal = status == "occurrence_committed" and bool(effects)
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
        return min((d for d in dates if d > state.logical_time), default=None)

    def close(self):
        self.store.close()
