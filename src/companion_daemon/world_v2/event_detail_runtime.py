"""Release privately reviewed event detail through the existing World occurrence path.

No model calls, character choices or Experience writes occur here. The only
prose this module publishes is an exact field of an accepted World Author draft.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json

from .event_detail_contract import event_detail_index
from .life_development_draft import LifeDevelopmentCapabilityManifest, parse_world_author_draft
from .schemas import ProjectionCursor, WorldEvent
from .world_outlook import digest


def detail_operation(occurrence_id):
    return "event-detail:" + digest(occurrence_id)


def read_accepted_detail_program(*, ledger, content_store, projection, occurrence):
    """Verify an accepted program from World authority, independently of the queue."""
    index = occurrence.event_detail_program
    if index is None:
        raise ValueError("event detail occurrence has no accepted program")
    located = ledger.lookup_event_commit(occurrence.trigger_ref)
    if located is None or located[1].ledger_sequence > projection.ledger_sequence:
        raise ValueError("event detail proposal is unavailable at this prefix")
    event = WorldEvent.model_validate_json(located[0].model_dump_json())
    raw = event.payload()
    if event.event_type != "ProposalRecorded" or raw.get("proposal_kind") != "life_development" or raw.get("effect_ref") != occurrence.occurrence_id:
        raise ValueError("event detail source is not an accepted World Author proposal")
    from .reducers import _validate_life_development_deliberation_binding
    _validate_life_development_deliberation_binding(projection, raw)
    manifest = LifeDevelopmentCapabilityManifest.model_validate_json(json.dumps(raw["world_author_deliberation"]["capability_manifest"]))
    binding = raw["world_author_deliberation"]
    original = content_store.read_exact(content_ref=binding["raw_content_refs"][-1])
    if original is None or original.content_kind != "raw_model_result":
        raise ValueError("original prepared event author body is unavailable")
    from .life_content_store import life_content_payload_hash
    if original.content_payload_hash != binding["response_hashes"][-1] or life_content_payload_hash(original.text) != original.content_payload_hash:
        raise ValueError("original event author body differs from its audit")
    original_time = next(r.logical_time for r in projection.committed_world_event_refs
                         if r.world_revision == manifest.pinned_cursor.world_revision)
    draft = parse_world_author_draft(raw=original.text, manifest=manifest, logical_time=original_time)
    if (event_detail_index(draft, manifest.event_detail_capability) != index
            or digest(raw["possibility_authority"]) != raw["possibility_authority_hash"]
            or raw.get("world_author_source_closure_review", {}).get("decision") != "supported"
            or raw.get("world_author_novel_origin_review", {}).get("decision") != "supported"):
        raise ValueError("prepared detail lacks exact reviewed authority")
    source = next((e for e in projection.committed_world_event_refs if e.event_id == index.capability.source_event_ref), None)
    if source is None or source.payload_hash != index.capability.source_payload_hash or source.world_revision != index.capability.source_world_revision:
        raise ValueError("prepared detail source has changed")
    return draft, index


class EventDetailRuntime:
    def __init__(self, *, ledger, store, executor, actor_ref, content_store):
        self.ledger, self.store, self.executor, self.actor_ref = ledger, store, executor, actor_ref
        self.content_store = content_store
        self._registered_revision = 0

    def _capture(self, projection):
        if self._registered_revision > projection.world_revision:
            self._registered_revision = 0
        # Cursor traversal, not an ever-growing in-memory set of old event IDs.
        for ref in projection.committed_world_event_refs[self._registered_revision:]:
            if ref.event_type in {"ActivityPaused", "ActivityAbandoned", "ActivityCompleted", "ActivityResumed",
                                  "WorldOccurrenceSettled", "WorldOccurrenceCancelled", "WorldOccurrenceExpired"}:
                event, _ = self.ledger.lookup_event_commit(ref.event_id)
                body = event.payload()
                entity_ref = body.get("plan_id") or body.get("occurrence_id")
                if entity_ref:
                    self.store.wake_event_details(entity_ref=entity_ref, at=projection.logical_time.isoformat())
            if ref.event_type != "WorldOccurrenceCommitted":
                continue
            event, _ = self.ledger.lookup_event_commit(ref.event_id)
            value = event.payload()["occurrence"]
            if value.get("event_detail_program") is None or self.actor_ref not in value["participant_refs"]:
                continue
            self.store.claim(detail_operation(value["occurrence_id"]), kind="event_detail", payload={
                "occurrence_ref": value["occurrence_id"], "proposal_event_ref": value["trigger_ref"],
                "program_index": value["event_detail_program"], "committed_event_ref": ref.event_id,
                "committed_payload_hash": ref.payload_hash,
                "next_due_at": (datetime.fromisoformat(value["event_detail_program"]["capability"]["starts_at"]) +
                                timedelta(seconds=value["event_detail_program"]["offsets"][0])).isoformat(),
            })
        self._registered_revision = projection.world_revision

    def _load(self, row, projection):
        pointer = row["payload"]
        occurrence = next(o for o in projection.world_occurrences if o.occurrence_id == pointer["occurrence_ref"])
        if occurrence.trigger_ref != pointer["proposal_event_ref"] or occurrence.event_detail_program is None:
            raise ValueError("event detail lost its accepted occurrence")
        draft, index = read_accepted_detail_program(ledger=self.ledger, content_store=self.content_store,
                                                     projection=projection, occurrence=occurrence)
        if index.model_dump(mode="json") != pointer["program_index"]:
            raise ValueError("prepared detail differs from its original author bytes")
        return occurrence, draft, index

    def _source_live(self, capability, projection):
        if capability.source_tokens:
            from .world_outlook import source_catalog
            refs = {item.source_ref for item in capability.source_tokens}
            sources = source_catalog(projection, actor_ref=self.actor_ref, required_refs=refs,
                npc_refs=tuple(f"npc:{n.npc_id}" for n in projection.npcs), legacy_fact_status=False)
            if any(item.source_ref not in sources or sources[item.source_ref].token != item.token
                   for item in capability.source_tokens):
                return False
        if capability.source_kind == "activity_execution":
            plan = next((p for p in projection.plans if p.plan_id == capability.entity_ref), None)
            return bool(plan and plan.owner_actor_ref == self.actor_ref and plan.status == "active"
                        and plan.authority_origin and plan.authority_origin.accepted_event_ref == capability.source_event_ref)
        occurrence = next((o for o in projection.world_occurrences if o.occurrence_id == capability.entity_ref), None)
        if occurrence is None or self.actor_ref not in occurrence.participant_refs:
            return False
        if occurrence.status == "active":
            return occurrence.entity_revision == capability.entity_revision
        # Settlement selects a previously authored branch; it does not revoke
        # the accepted programme while the original event window remains open.
        # Cancellation and expiry still close all unpublished phases.
        return bool(occurrence.status == "settled" and occurrence.entity_revision > capability.entity_revision
                    and occurrence.activated_at == capability.starts_at
                    and occurrence.settled_at is not None
                    and occurrence.settled_at < capability.ends_at
                    and occurrence.event_detail_program is not None
                    and occurrence.event_detail_program.capability == capability)

    def advance(self, *, wake_event_ref):
        with self.ledger.serialized_commit_sequence():
            state = self.ledger.project()
            wake = self.ledger.lookup_event_commit(wake_event_ref)
            if wake is None or wake[0].event_type != "ClockAdvanced" or wake[0].logical_time != state.logical_time:
                raise ValueError("event detail release requires the current committed Clock")
            self._capture(state)
            released = 0
            for row in self.store.pending_event_details(due_at=state.logical_time.isoformat()):
                try:
                    released += self._advance_one(row, wake_event_ref=wake_event_ref)
                except Exception as exc:
                    # Keep the failure and retry the same program. One damaged
                    # body must not block independent event progress.
                    current = self.store.operation(row["operation_ref"])
                    if current is None or current["state"] != "running":
                        raise
                    payload = current["payload"]
                    ordinal = payload.get("failure_count", 0) + 1
                    failure = {"code": type(exc).__name__, "detail": str(exc)[:1000],
                               "observed_at": state.logical_time.isoformat()}
                    self.store.attempt(row["operation_ref"], ordinal=ordinal, payload=failure)
                    self.store.update_running_operation(row["operation_ref"], kind="event_detail", previous=payload, payload={
                        **payload, "failure_count": ordinal, "failure_code": type(exc).__name__,
                        "next_due_at": (state.logical_time + timedelta(seconds=30)).isoformat(),
                    })
            return released

    def _advance_one(self, row, *, wake_event_ref):
        state = self.ledger.project()
        op = row["operation_ref"]
        occurrence = next(o for o in state.world_occurrences if o.occurrence_id == row["payload"]["occurrence_ref"])
        if op != detail_operation(occurrence.occurrence_id):
            raise ValueError("event detail operation identity differs from its occurrence")
        committed = next((r for r in state.committed_world_event_refs if r.event_id == row["payload"]["committed_event_ref"]), None)
        if committed is None or committed.payload_hash != row["payload"]["committed_payload_hash"]:
            raise ValueError("event detail committed pointer differs from its authority")
        index = occurrence.event_detail_program
        if index is None or index.model_dump(mode="json") != row["payload"]["program_index"]:
            raise ValueError("event detail pointer differs from committed authority")
        if occurrence.status in {"cancelled", "expired"} or not self._source_live(index.capability, state):
            self._close_phases(row, index, wake_event_ref)
            self.store.finish(op, state="source_changed", payload={**row["payload"], "reason": "unreleased_detail_no_longer_authorized"})
            return 0
        if state.logical_time >= index.capability.ends_at:
            self._close_phases(row, index, wake_event_ref)
            self.store.finish(op, state="incomplete", payload={**row["payload"], "reason": "unreleased_detail_window_elapsed"})
            return 0
        occurrence, draft, index = self._load(row, state)
        released = 0
        terminal = 0
        for position, step in enumerate(draft.event_details):
            phase_op = f"{op}:phase:{position}"
            existing = self.store.operation(phase_op)
            if existing and existing["state"] != "running":
                terminal += 1
                continue
            due = index.capability.starts_at + timedelta(seconds=step.after_seconds)
            if state.logical_time < due:
                continue
            if step.outcome_index is not None:
                if occurrence.status != "settled":
                    continue
                selected = occurrence.candidate_outcome_refs[step.outcome_index]
                if occurrence.settled_outcome_ref != selected:
                    self.store.claim(phase_op, kind="event_detail_phase", payload={"parent": op, "index": position})
                    self.store.finish(phase_op, state="not_selected", payload={"parent": op, "index": position})
                    terminal += 1
                    continue
            ranks = ("public", "shareable", "personal", "private", "withhold")
            privacy = [index.capability.privacy_class, draft.privacy_class, occurrence.visibility]
            if step.outcome_index is not None:
                privacy.append(occurrence.candidate_outcomes[step.outcome_index].privacy_class)
            identity = digest([op, position, index.program_hash])
            node = {
                "opportunity_id": "detail-" + identity, "thread_refs": ["event-detail"],
                "external_possibility": step.text[:1000], "lane": "world", "npc_ref": None,
                "execution": {"kind": "environment", "text": step.text, "privacy_class": max(privacy, key=ranks.index), "duration_minutes": 1},
                "opens_at": due.isoformat(), "closes_at": index.capability.ends_at.isoformat(),
                "basis_refs": [index.capability.source_event_ref], "conditions": [], "after_opportunities": [],
            }
            self.store.claim(phase_op, kind="event_detail_phase", payload={"parent": op, "index": position})
            state = self.ledger.project()
            result = self.executor.execute(node=node, wake_event_ref=wake_event_ref,
                expected_cursor=ProjectionCursor(**{k: getattr(state,k) for k in ("world_revision","deliberation_revision","ledger_sequence")}),
                origin={"world_id": state.world_id, "actor_ref": self.actor_ref,
                        "event_detail_origin": {"proposal_event_ref": occurrence.trigger_ref, "program_hash": index.program_hash,
                                                "source_event_ref": index.capability.source_event_ref, "phase": position,
                                                "due_at": due.isoformat()}})
            if result.status != "occurrence_committed":
                continue  # A technical failure keeps the exact phase pending.
            self.store.finish(phase_op, state="released", payload={"parent": op, "index": position, "occurrence_ref": result.occurrence_id})
            released += 1
            terminal += 1
            state = self.ledger.project()
        if terminal == len(draft.event_details):
            self.store.finish(op, state="complete", payload={**row["payload"], "released_or_unselected": terminal})
        current = self.store.operation(op)
        if current["state"] == "running":
            due = self._next_for(occurrence, index, state.logical_time)
            self.store.update_running_operation(op, kind="event_detail", previous=current["payload"], payload={
                **current["payload"], "failure_code": None,
                "next_due_at": (due or index.capability.ends_at).isoformat(),
            })
        return released

    def _close_phases(self, row, index, wake_event_ref):
        # A crash may leave a child committed but not activated. Withdraw it
        # before retiring the preparation; active facts remain in history.
        from .world_outlook_execution import occurrence_ref
        op = row["operation_ref"]
        for position in range(index.step_count):
            phase_op = f"{op}:phase:{position}"
            prior = self.store.operation(phase_op)
            if prior and prior["state"] != "running":
                continue
            node = {"opportunity_id": "detail-" + digest([op, position, index.program_hash])}
            self.executor.cancel_pending(node=node, wake_event_ref=wake_event_ref)
            state = self.ledger.project()
            ref = occurrence_ref(state.world_id, self.actor_ref, node["opportunity_id"])
            child = next((o for o in state.world_occurrences if o.occurrence_id == ref), None)
            self.store.claim(phase_op, kind="event_detail_phase", payload={"parent": op, "index": position})
            self.store.finish(phase_op, state="released" if child and child.status in {"active", "settled"} else "not_released",
                              payload={"parent": op, "index": position, "occurrence_ref": ref if child else None})

    def _next_for(self, occurrence, index, now):
        op = detail_operation(occurrence.occurrence_id)
        dates = [index.capability.ends_at]
        for position, seconds in enumerate(index.offsets):
            phase = self.store.operation(f"{op}:phase:{position}")
            if phase is not None and phase["state"] != "running":
                continue
            if index.outcome_indices[position] is not None and occurrence.status != "settled":
                continue  # Its existing aftermath queue owns the pending draw.
            dates.append(max(now + timedelta(seconds=1), index.capability.starts_at + timedelta(seconds=seconds)))
        return min(dates)

    def next_due(self):
        state = self.ledger.project()
        if state.logical_time is None:
            return None
        # Unregistered changes need a worker pass. Never query one SQL row per
        # completed historical program on every scheduler peek.
        for ref in state.committed_world_event_refs[self._registered_revision:]:
            if ref.event_type in {"WorldOccurrenceCommitted", "ActivityPaused", "ActivityAbandoned", "ActivityCompleted",
                                  "ActivityResumed", "WorldOccurrenceSettled", "WorldOccurrenceCancelled", "WorldOccurrenceExpired"}:
                return state.logical_time + timedelta(seconds=1)
        due = self.store.next_event_detail_due()
        return max(state.logical_time + timedelta(seconds=1), due) if due else None
