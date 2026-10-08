"""Execute a frozen central script through ordinary occurrence authority.

No model call and no protagonist Action, intention or inner state is written.
NPC speech stays an attributed statement, not evidence that its claims are true.
"""

from __future__ import annotations

from datetime import timedelta
import json
from types import SimpleNamespace

from .event_identity import domain_idempotency_key
from .life_content_store import life_content_payload_hash
from .life_events import WorldOccurrenceActivatedPayload
from .occurrence_content_coordinator import OccurrenceContentCommitRequest, OutcomeCandidateContent
from .schemas import DueWindow, EvidenceRef, ProjectionCursor, WorldEvent, WorldOccurrenceProjection
from .world_consequence_contract import WorldConsequenceV2
from .world_outlook import OutlookOpportunity, digest


def occurrence_ref(world_id, actor_ref, node_id):
    return "occurrence:world-outlook:" + digest([world_id, actor_ref, node_id])


class WorldOutlookExecutor:
    def __init__(self, *, ledger, occurrence_content, actor_ref):
        if occurrence_content.ledger is not ledger:
            raise ValueError("outlook executor must use the same World")
        self._ledger, self._content, self._owner = ledger, occurrence_content, actor_ref

    def recover(self, *, node):
        ref = occurrence_ref(self._ledger.world_id, self._owner, node["opportunity_id"])
        occurrence = next(
            (o for o in self._ledger.project().world_occurrences if o.occurrence_id == ref), None
        )
        if occurrence is None or occurrence.status == "committed":
            return None
        if occurrence.status not in {"active", "settled"}:
            return SimpleNamespace(status="rejected", reason_code="outlook_effect_withdrawn")
        return SimpleNamespace(
            status="occurrence_committed",
            reason_code="world_outlook.executed_recovered",
            occurrence_id=ref,
        )

    def execute(self, *, node, wake_event_ref, origin, expected_cursor):
        node = OutlookOpportunity.model_validate_json(
            json.dumps({k: v for k, v in node.items() if k != "state"})
        )
        with self._ledger.serialized_commit_sequence():
            state = self._ledger.project()
            if (
                ProjectionCursor(
                    **{
                        k: getattr(state, k)
                        for k in ("world_revision", "deliberation_revision", "ledger_sequence")
                    }
                )
                != expected_cursor
            ):
                return SimpleNamespace(
                    status="stale_prefix", reason_code="outlook_execution_prefix_changed"
                )
            located = self._ledger.lookup_event_commit(wake_event_ref)
            if (
                located is None
                or located[0].event_type != "ClockAdvanced"
                or located[0].logical_time != state.logical_time
            ):
                raise ValueError("outlook execution needs its current committed clock")
            wake = located[0]
            if not node.opens_at <= state.logical_time < node.closes_at:
                return SimpleNamespace(status="rejected", reason_code="outlook_window_not_open")
            ref = occurrence_ref(state.world_id, self._owner, node.opportunity_id)
            existing = next((o for o in state.world_occurrences if o.occurrence_id == ref), None)
            if existing is None:
                # Only the currently selected script enters World, never the
                # full private outlook or unchosen future branches.
                body = node.execution.text
                if node.lane == "npc":
                    npc = next(
                        (
                            n
                            for n in state.npcs
                            if f"npc:{n.npc_id}" == node.npc_ref and n.status == "active"
                        ),
                        None,
                    )
                    if npc is None:
                        raise ValueError("scripted NPC is unavailable")
                    rank = ("public", "shareable", "personal", "private", "withhold")
                    if rank.index(node.execution.privacy_class) < rank.index(npc.privacy_class):
                        raise ValueError("scripted NPC statement weakens privacy")
                    body = json.dumps(
                        {
                            "kind": "npc_statement",
                            "speaker_ref": node.npc_ref,
                            "utterance": node.execution.text,
                            "claim_authority": "speaker_report_only",
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                consequence = WorldConsequenceV2(environment_text=body)
                text = json.dumps(
                    consequence.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                identity = digest([state.world_id, self._owner, node.model_dump(mode="json")])
                content = OutcomeCandidateContent(
                    candidate_result_ref="candidate:outlook:" + identity,
                    result_id="result:outlook:" + identity,
                    result_payload_ref="payload:outlook:" + identity,
                    result_payload_hash=life_content_payload_hash(text),
                    result_contract="world-consequence.2",
                    privacy_class=node.execution.privacy_class,
                    content_ref="content:outlook:" + identity,
                    text=text,
                    causal_authority="world_contingency",
                )
                # The immutable execution receipt records original authoring,
                # not a fabricated provider call at execution time.
                proposal_id = "event:world-outlook:execution:" + digest([identity, wake_event_ref])
                payload = {
                    "proposal_id": "proposal:world-outlook:" + identity,
                    "proposal_kind": "world_outlook_execution",
                    "trigger_id": wake_event_ref,
                    "evaluated_world_revision": state.world_revision,
                    "central_authorship": origin,
                    "script": node.execution.model_dump(mode="json"),
                    "npc_ref": node.npc_ref,
                    "occurrence_id": ref,
                    "no_model_call_at_execution": True,
                }
                event = WorldEvent.from_payload(
                    schema_version="world-v2.1",
                    event_id=proposal_id,
                    world_id=state.world_id,
                    event_type="ProposalRecorded",
                    logical_time=state.logical_time,
                    created_at=state.logical_time,
                    actor="worker:world-outlook",
                    source="world-outlook-execution.1",
                    trace_id="trace:" + identity,
                    causation_id=wake.event_id,
                    correlation_id="outlook:" + identity,
                    idempotency_key=proposal_id,
                    payload=payload,
                )
                if self._ledger.lookup_event_commit(proposal_id) is None:
                    self._ledger.commit_at_cursor(
                        (event,), expected_cursor=expected_cursor, commit_id="commit:" + proposal_id
                    )
                evidence = EvidenceRef(
                    ref_id=wake.event_id,
                    evidence_type="committed_world_event",
                    claim_purpose="life_transition",
                    source_world_revision=located[1].world_revision,
                    immutable_hash=wake.payload_hash,
                )
                participants = tuple(
                    sorted({self._owner, *((node.npc_ref,) if node.npc_ref else ())})
                )
                self._content.commit(
                    OccurrenceContentCommitRequest(
                        world_id=state.world_id,
                        occurrence=WorldOccurrenceProjection(
                            occurrence_id=ref,
                            entity_revision=1,
                            trigger_ref=proposal_id,
                            participant_refs=participants,
                            time_window=DueWindow(
                                opens_at=state.logical_time,
                                closes_at=state.logical_time
                                + timedelta(minutes=node.execution.duration_minutes),
                            ),
                            candidate_outcome_refs=(content.candidate_result_ref,),
                            visibility=node.execution.privacy_class,
                            status="committed",
                        ),
                        candidate_contents=(content,),
                        change_id="change:outlook:" + identity,
                        transition_id="transition:outlook:" + identity,
                        evidence_refs=(evidence,),
                        policy_refs=("policy:world-outlook.1",),
                        logical_time=state.logical_time,
                        created_at=state.logical_time,
                        actor="worker:world-outlook",
                        source="world-outlook-execution.1",
                        trace_id="trace:" + identity,
                        causation_id=proposal_id,
                        correlation_id="outlook:" + identity,
                    )
                )
            state = self._ledger.project()
            occurrence = next(o for o in state.world_occurrences if o.occurrence_id == ref)
            if occurrence.status == "committed":
                evidence = EvidenceRef(
                    ref_id=wake.event_id,
                    evidence_type="committed_world_event",
                    claim_purpose="life_transition",
                    source_world_revision=located[1].world_revision,
                    immutable_hash=wake.payload_hash,
                )
                payload = WorldOccurrenceActivatedPayload(
                    change_id="activate:" + ref,
                    transition_id="activate:" + ref,
                    expected_entity_revision=occurrence.entity_revision,
                    evidence_refs=(evidence,),
                    policy_refs=("policy:world-outlook.1",),
                    occurrence_id=ref,
                    activated_at=state.logical_time,
                ).model_dump(mode="json")
                event = WorldEvent.from_payload(
                    schema_version="world-v2.1",
                    event_id="event:activate:" + ref,
                    world_id=state.world_id,
                    event_type="WorldOccurrenceActivated",
                    logical_time=state.logical_time,
                    created_at=state.logical_time,
                    actor="worker:world-outlook",
                    source="world-outlook-execution.1",
                    trace_id="trace:" + ref,
                    causation_id=wake.event_id,
                    correlation_id=ref,
                    idempotency_key=domain_idempotency_key(
                        event_type="WorldOccurrenceActivated",
                        world_id=state.world_id,
                        payload=payload,
                    ),
                    payload=payload,
                )
                self._ledger.commit_at_cursor(
                    (event,),
                    expected_cursor=ProjectionCursor(
                        **{
                            k: getattr(state, k)
                            for k in ("world_revision", "deliberation_revision", "ledger_sequence")
                        }
                    ),
                )
            return SimpleNamespace(
                status="occurrence_committed",
                reason_code="world_outlook.script_executed",
                occurrence_id=ref,
            )
