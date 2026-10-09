"""Central-authored NPC actions using ordinary plan/lifecycle events, with zero models."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from .life_events import ActivityPlannedPayload, ActivityTransitionPayload, NpcStatusChangedPayload
from .schemas import DueWindow, EvidenceRef, PlanStateProjection, ProjectionCursor, WorldEvent
from .world_outlook import digest
from .event_identity import domain_idempotency_key

NPC_EXECUTION_KINDS = frozenset({"npc_activity", "npc_cancel_activity", "npc_status"})


def plan_ref(world_id, owner, node_id):
    return "plan:world-outlook:" + digest([world_id, owner, node_id])


def validate_npc_execution(
    node, *, projection, npc_refs, location_refs, check_busy, location_capabilities=()
):
    e = node.execution
    if e.kind not in NPC_EXECUTION_KINDS:
        return
    participants = {node.npc_ref, *e.participant_npc_refs}
    if not participants <= set(npc_refs):
        raise ValueError("NPC action contains an unavailable or protagonist participant")
    ranks = ("public", "shareable", "personal", "private", "withhold")
    for npc in projection.npcs:
        if f"npc:{npc.npc_id}" in participants and ranks.index(e.privacy_class) < ranks.index(
            npc.privacy_class
        ):
            raise ValueError("NPC activity weakens participant privacy")
    if e.location_ref is not None and e.location_ref not in location_refs:
        raise ValueError("NPC activity location was not offered")
    if e.location_ref is not None:
        start = (
            projection.logical_time if check_busy else max(projection.logical_time, node.opens_at)
        )
        window = DueWindow(opens_at=start, closes_at=start + timedelta(minutes=e.duration_minutes))
        if not any(
            c.location_ref == e.location_ref
            and c.availability_kind == "reviewed_schedule"
            and ranks.index(e.privacy_class) >= ranks.index(c.privacy_class)
            and c.authorizes(
                timing_mode="now" if start == projection.logical_time else "later", window=window
            )
            for c in location_capabilities
        ):
            raise ValueError(
                "NPC activity needs a reviewed schedule covering its entire time window"
            )
    if e.kind == "npc_cancel_activity":
        plan = next((p for p in projection.plans if p.plan_id == e.target_plan_ref), None)
        if (
            plan is None
            or plan.owner_actor_ref != node.npc_ref
            or plan.status not in {"planned", "active", "paused"}
        ):
            raise ValueError("NPC script cannot cancel this plan or a protagonist plan")
        if not any(
            c.source_ref == plan.plan_id and c.unchanged_token == digest(plan)
            for c in node.conditions
        ):
            raise ValueError("NPC cancellation must pin the exact offered plan token")
    if e.kind == "npc_activity" and check_busy:
        now = projection.logical_time
        end = now + timedelta(minutes=e.duration_minutes)
        for plan in projection.plans:
            if plan.status not in {"planned", "active", "paused"}:
                continue
            # A protagonist intention mentioning an NPC is not that NPC's
            # accepted commitment. Reserve only NPC-authored/central plans.
            if plan.owner_actor_ref not in npc_refs:
                continue
            if not participants & ({plan.owner_actor_ref} | set(plan.participant_refs)):
                continue
            window = plan.scheduled_window
            if window is None or (window.opens_at < end and now < window.closes_at):
                raise ValueError("NPC activity conflicts with a committed participant schedule")
        for occurrence in projection.world_occurrences:
            if occurrence.status == "active" and participants & set(occurrence.participant_refs):
                window = occurrence.time_window
                if window.opens_at < end and now < window.closes_at:
                    raise ValueError("NPC activity conflicts with an active shared occurrence")


class CentralNpcExecutor:
    def __init__(self, *, ledger, actor_ref):
        self.ledger, self.owner = ledger, actor_ref

    def _cursor(self):
        state = self.ledger.project()
        return ProjectionCursor(
            **{
                k: getattr(state, k)
                for k in ("world_revision", "deliberation_revision", "ledger_sequence")
            }
        )

    def _evidence(self, wake_event_ref):
        state = self.ledger.project()
        located = self.ledger.lookup_event_commit(wake_event_ref)
        if (
            located is None
            or located[0].event_type != "ClockAdvanced"
            or located[0].logical_time != state.logical_time
        ):
            raise ValueError("NPC script requires the current committed clock")
        event, commit = located
        return EvidenceRef(
            ref_id=event.event_id,
            evidence_type="committed_world_event",
            claim_purpose="life_transition",
            source_world_revision=commit.world_revision,
            immutable_hash=event.payload_hash,
        )

    def _event(self, event_type, ref, payload, *, wake):
        now = self.ledger.project().logical_time
        return WorldEvent.from_payload(
            schema_version="world-v2.1",
            event_id=ref,
            world_id=self.ledger.world_id,
            event_type=event_type,
            logical_time=now,
            created_at=now,
            actor="worker:world-outlook",
            source="world-outlook-npc.1",
            trace_id="trace:" + ref,
            causation_id=wake,
            correlation_id=ref,
            idempotency_key=domain_idempotency_key(
                event_type=event_type, world_id=self.ledger.world_id, payload=payload
            )
            or ref,
            payload=payload,
        )

    def _identity(self, node):
        return digest([self.ledger.world_id, self.owner, node.opportunity_id])

    def recover(self, node):
        identity = self._identity(node)
        effect_ref = "event:outlook-npc:effect:" + identity
        effect = self.ledger.lookup_event_commit(effect_ref)
        if effect is None:
            return None
        audit = self.ledger.lookup_event_commit(effect[0].causation_id)
        expected_type = {
            "npc_activity": "ActivityPlanned",
            "npc_cancel_activity": "ActivityAbandoned",
            "npc_status": "NpcStatusChanged",
        }[node.execution.kind]
        if (
            effect[0].event_type != expected_type
            or audit is None
            or audit[0].payload().get("node_hash") != digest(node)
            or audit[0].payload().get("central_authorship", {}).get("actor_ref") != self.owner
        ):
            raise ValueError("NPC recovery does not bind the original script and author")
        if node.execution.kind == "npc_activity":
            plan = next(
                (
                    p
                    for p in self.ledger.project().plans
                    if p.plan_id == plan_ref(self.ledger.world_id, self.owner, node.opportunity_id)
                ),
                None,
            )
            if plan is not None:
                return SimpleNamespace(
                    status="npc_activity_started",
                    reason_code="outlook.npc_activity_existing",
                    plan_id=plan.plan_id,
                )
        else:
            return SimpleNamespace(
                status="npc_effect_committed",
                reason_code="outlook.npc_effect_existing",
                effect_event_ref=effect_ref,
            )
        return None

    def has_effect(self, node):
        ref = "event:outlook-npc:effect:" + self._identity(node)
        return self.ledger.lookup_event_commit(ref) is not None

    def execute(self, *, node, wake_event_ref, origin):
        if node.npc_ref == self.owner or self.owner in node.execution.participant_npc_refs:
            raise ValueError("central NPC execution cannot control its protagonist")
        if origin.get("actor_ref") != self.owner:
            raise ValueError("central NPC author owner differs from executor")
        existing = self.recover(node)
        if existing is not None:
            return existing
        state = self.ledger.project()
        e = node.execution
        from .life_development_draft import LifeDevelopmentLocationCapability
        import json

        capabilities = tuple(
            LifeDevelopmentLocationCapability.model_validate_json(json.dumps(c))
            for c in origin.get("location_capabilities", ())
        )
        validate_npc_execution(
            node,
            projection=state,
            npc_refs=tuple(f"npc:{n.npc_id}" for n in state.npcs if n.status == "active"),
            location_refs=origin.get("location_refs", ()),
            location_capabilities=capabilities,
            check_busy=True,
        )
        evidence = self._evidence(wake_event_ref)
        identity = self._identity(node)
        audit_ref = "event:outlook-npc:author:" + digest(
            [identity, node.model_dump(mode="json"), origin]
        )
        if self.ledger.lookup_event_commit(audit_ref) is None:
            audit = self._event(
                "ProposalRecorded",
                audit_ref,
                {
                    "proposal_id": "proposal:" + audit_ref,
                    "proposal_kind": "central_npc_execution",
                    "trigger_id": wake_event_ref,
                    "evaluated_world_revision": state.world_revision,
                    "central_authorship": origin,
                    "node_hash": digest(node),
                    "execution": e.model_dump(mode="json"),
                    "npc_ref": node.npc_ref,
                    "no_model_call_at_execution": True,
                },
                wake=wake_event_ref,
            )
            self.ledger.commit_at_cursor((audit,), expected_cursor=self._cursor())
        common = dict(evidence_refs=(evidence,), policy_refs=("policy:central-npc.1",))
        effect_ref = "event:outlook-npc:effect:" + identity
        if e.kind == "npc_activity":
            ref = plan_ref(state.world_id, self.owner, node.opportunity_id)
            plan = PlanStateProjection(
                plan_id=ref,
                activity_id="activity:" + identity,
                entity_revision=1,
                activity_kind=e.activity_kind,
                evidence_refs=(evidence,),
                status="planned",
                importance_bp=0,
                scheduled_window=DueWindow(
                    opens_at=state.logical_time,
                    closes_at=state.logical_time + timedelta(minutes=e.duration_minutes),
                ),
                participant_refs=tuple(sorted({node.npc_ref, *e.participant_npc_refs})),
                location_ref=e.location_ref,
                privacy_class=e.privacy_class,
                owner_actor_ref=node.npc_ref,
            )
            planned = ActivityPlannedPayload(
                change_id="plan:" + identity,
                transition_id="plan:" + identity,
                expected_entity_revision=0,
                plan=plan,
                **common,
            )
            start = ActivityTransitionPayload(
                change_id="start:" + identity,
                transition_id="start:" + identity,
                expected_entity_revision=1,
                plan_id=ref,
                transitioned_at=state.logical_time,
                reason_ref=wake_event_ref,
                **common,
            )
            events = (
                self._event(
                    "ActivityPlanned", effect_ref, planned.model_dump(mode="json"), wake=audit_ref
                ),
                self._event(
                    "ActivityStarted",
                    "event:outlook-npc:start:" + identity,
                    start.model_dump(mode="json"),
                    wake=audit_ref,
                ),
            )
            self.ledger.commit_at_cursor(events, expected_cursor=self._cursor())
            return SimpleNamespace(
                status="npc_activity_started",
                reason_code="outlook.npc_activity_started",
                plan_id=ref,
            )
        if e.kind == "npc_cancel_activity":
            plan = next(p for p in state.plans if p.plan_id == e.target_plan_ref)
            payload = ActivityTransitionPayload(
                change_id="cancel:" + identity,
                transition_id="cancel:" + identity,
                expected_entity_revision=plan.entity_revision,
                plan_id=plan.plan_id,
                transitioned_at=state.logical_time,
                reason_ref=wake_event_ref,
                **common,
            )
            event_type = "ActivityAbandoned"
        else:
            npc = next(n for n in state.npcs if f"npc:{n.npc_id}" == node.npc_ref)
            payload = NpcStatusChangedPayload(
                change_id="status:" + identity,
                transition_id="status:" + identity,
                expected_entity_revision=npc.entity_revision,
                npc_before=npc,
                npc_after=npc.model_copy(
                    update={"entity_revision": npc.entity_revision + 1, "status": e.target_status}
                ),
                **common,
            )
            event_type = "NpcStatusChanged"
        self.ledger.commit_at_cursor(
            (self._event(event_type, effect_ref, payload.model_dump(mode="json"), wake=audit_ref),),
            expected_cursor=self._cursor(),
        )
        return SimpleNamespace(
            status="npc_effect_committed",
            reason_code="outlook.npc_effect_committed",
            effect_event_ref=effect_ref,
        )

    def advance(self, *, wake_event_ref):
        """Only centrally committed NPC activities; no protagonist plans or new choices."""
        with self.ledger.serialized_commit_sequence():
            state = self.ledger.project()
            evidence = self._evidence(wake_event_ref)
            for plan in state.plans:
                if not plan.plan_id.startswith("plan:world-outlook:") or plan.status != "active":
                    continue
                if not self._owns_plan(plan):
                    continue
                active = {f"npc:{n.npc_id}" for n in state.npcs if n.status == "active"}
                cancelled = not set(plan.participant_refs) <= active
                if not cancelled and state.logical_time < plan.scheduled_window.closes_at:
                    continue
                kind = "ActivityAbandoned" if cancelled else "ActivityCompleted"
                identity = digest([plan.plan_id, kind])
                payload = ActivityTransitionPayload(
                    change_id=identity,
                    transition_id=identity,
                    expected_entity_revision=plan.entity_revision,
                    plan_id=plan.plan_id,
                    transitioned_at=state.logical_time,
                    reason_ref=wake_event_ref,
                    evidence_refs=(evidence,),
                    policy_refs=("policy:central-npc.1",),
                )
                self.ledger.commit_at_cursor(
                    (
                        self._event(
                            kind,
                            "event:outlook-npc:finish:" + identity,
                            payload.model_dump(mode="json"),
                            wake=wake_event_ref,
                        ),
                    ),
                    expected_cursor=self._cursor(),
                )

    def next_due(self):
        state = self.ledger.project()
        return min(
            (
                p.scheduled_window.closes_at
                for p in state.plans
                if p.plan_id.startswith("plan:world-outlook:")
                and p.status == "active"
                and self._owns_plan(p)
                and p.scheduled_window is not None
                and p.scheduled_window.closes_at > state.logical_time
            ),
            default=None,
        )

    def _owns_plan(self, plan):
        if plan.authority_origin is None:
            return False
        origin = self.ledger.lookup_event_commit(plan.authority_origin.accepted_event_ref)
        if origin is None or origin[0].source != "world-outlook-npc.1":
            return False
        audit = self.ledger.lookup_event_commit(origin[0].causation_id)
        return (
            audit is not None
            and audit[0].event_type == "ProposalRecorded"
            and audit[0].payload().get("central_authorship", {}).get("actor_ref") == self.owner
        )
