"""Bounded periodic memory review through CharacterInterior and ledger CAS.

Time opens an opportunity. Only the character may retain or forget. Original
Facts/Experiences and source bindings are never rewritten by this worker.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
import hashlib
import json
from typing import Literal

from .character_interior.audit import recorded_character_interior_model_result
from .character_interior.life_memory import _decision_payload, _memory_opportunity
from .character_interior.purpose_context import InteriorPurposeContext
from .delayed_trigger_policies import TECHNICAL_RETRY_BACKOFF_SECONDS
from .errors import ConcurrencyConflict, IdempotencyConflict
from .proposal_audit_schemas import ModelResultRecordedPayload
from .life_content_store import StoredLifeContent
from .memory_consolidation_contract import CONTRACT, PURPOSE, HOST_SOURCE_BINDING, MemoryReviewBatch, validate_payload
from .memory_events import MemoryCandidateChangedPayload, MemoryDeliberativeForgetAuthority, memory_candidate_mutation_hash, memory_source_evidence
from .memory_reducers import MEMORY_POLICY_DIGEST, MEMORY_POLICY_REFS, MEMORY_POLICY_VERSION
from .memory_retrieval import MemoryRetrievalCompiler
from .memory_review_lease import MemoryReviewLease
from .schema_core import FrozenModel
from .schemas import MemoryCandidateOrigin, MemoryCandidateProjection, MemoryCandidateProposalProjection, MemoryCandidateProposedMutation, TriggerProcess, memory_candidate_semantic_fingerprint


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class MemoryReviewTechnicalFailure(RuntimeError):
    pass


class MemoryConsolidationRunResult(FrozenModel):
    trigger_id: str
    status: Literal["idle", "owned_elsewhere", "processed", "joined"]
    work_status: Literal["reviewed", "technical_failure"] | None = None


def review_due(candidate):
    if candidate.values.status != "active":
        return None
    return candidate.values.review_due_at or (candidate.values.reviewed_at + timedelta(days=1))


class MemoryConsolidationRuntime(MemoryReviewLease):
    PROCESS_KIND = "memory_consolidation_review"

    def __init__(self, *, content_store, **kwargs):
        super().__init__(source="world-v2:memory-consolidation", **kwargs)
        self._content = content_store

    async def next_due(self):
        projection = await self._project()
        pending = [p for p in projection.trigger_processes
                   if p.process_kind == self.PROCESS_KIND and p.state != "terminal"]
        if pending:
            return min(p.claim_lease.expires_at if p.claim_lease else projection.logical_time for p in pending)
        return min((at for c in projection.memory_candidates if (at := review_due(c)) is not None), default=None)

    async def _open(self, projection):
        now = projection.logical_time
        due = sorted((c for c in projection.memory_candidates if review_due(c) is not None and review_due(c) <= now),
                     key=lambda c: (review_due(c), c.candidate_id))
        clocks = [e for e in projection.committed_world_event_refs if e.event_type == "ClockAdvanced"]
        if not due or not clocks:
            return None
        clock, _ = await self._lookup(clocks[-1].event_id)
        view = await asyncio.to_thread(
            MemoryRetrievalCompiler(ledger=self._ledger, life_content_store=self._content,
                                    max_excerpt_characters=1200).compile,
            cursor=self._cursor(projection), candidates=tuple(due), projection=projection,
            actor_ref=self._actor_ref, viewer_privacy_ceiling="private",
        )
        readable = {item.candidate_id: item for item in view.items}
        due = [c for c in due if c.candidate_id in readable][:8]
        if not due:
            return None
        job = {"contract": "memory-review-job.1", "world_id": projection.world_id,
               "actor_ref": self._actor_ref, "clock_event_ref": clock.event_id,
               "opening_world_revision": projection.world_revision,
               "candidates": [c.model_dump(mode="json") for c in due],
               "readings": [readable[c.candidate_id].model_dump(mode="json") for c in due]}
        token = digest(job)
        ref = "memory-review-job:" + token
        await asyncio.to_thread(self._content.put_if_absent, StoredLifeContent(
            content_ref=ref, content_kind="capability_manifest_audit",
            content_payload_hash=hashlib.sha256(canonical(job).encode()).hexdigest(), text=canonical(job),
        ))
        process = TriggerProcess(schema_version="world-v2.1", trigger_id="trigger:memory-consolidation:" + token,
                                 trigger_ref=ref, process_kind=self.PROCESS_KIND,
                                 source_evidence_ref=clock.event_id, state="open")
        existing = next((p for p in projection.trigger_processes if p.trigger_id == process.trigger_id), None)
        if existing is not None:
            return existing if existing.state != "terminal" else None
        event = self._event(event_id="event:memory-consolidation:open:" + token,
                            event_type="TriggerProcessOpened", payload={"process": process.model_dump(mode="json")},
                            logical_time=now, created_at=clock.created_at, trace_id=clock.trace_id,
                            causation_id=clock.event_id, correlation_id=clock.correlation_id)
        await self._commit_at_cursor((event,), cursor=self._cursor(projection), commit_id="commit:memory-consolidation:open:" + token)
        return process

    async def drain_one(self):
        projection = await self._project()
        process = next((p for p in projection.trigger_processes if p.process_kind == self.PROCESS_KIND and p.state != "terminal"), None)
        if process is None:
            try:
                process = await self._open(projection)
            except (ConcurrencyConflict, IdempotencyConflict):
                return MemoryConsolidationRunResult(trigger_id="", status="joined")
            if process is None:
                return MemoryConsolidationRunResult(trigger_id="", status="idle")
            projection = await self._project()
        # A failed/crashed attempt owns its lease until the bounded retry point,
        # even for the same worker. Repeated drain calls cannot renew its spend.
        stored = await asyncio.to_thread(self._content.read_exact, content_ref=process.trigger_ref)
        if stored is None:
            raise MemoryReviewTechnicalFailure("memory_review_job_unavailable")
        job = json.loads(stored.text)
        if (digest(job) != process.trigger_ref.removeprefix("memory-review-job:")
                or job["world_id"] != projection.world_id or job["actor_ref"] != self._actor_ref
                or job["clock_event_ref"] != process.source_evidence_ref):
            raise MemoryReviewTechnicalFailure("memory_review_job_binding_invalid")
        source_event, _ = await self._lookup(job["clock_event_ref"])
        decision_ref = "memory-review-decision:" + digest(process.trigger_id)
        saved = await asyncio.to_thread(self._content.read_exact, content_ref=decision_ref)
        if saved is None and process.claim_lease and projection.logical_time < process.claim_lease.expires_at:
            return MemoryConsolidationRunResult(trigger_id=process.trigger_id, status="owned_elsewhere")
        self._lease_seconds = TECHNICAL_RETRY_BACKOFF_SECONDS[min(len(process.attempt_ids), len(TECHNICAL_RETRY_BACKOFF_SECONDS) - 1)]
        active = await self._claim_or_reclaim(process=process, source_event=source_event, projection=projection)
        if active is None:
            return MemoryConsolidationRunResult(trigger_id=process.trigger_id, status="owned_elsewhere")
        candidates = tuple(MemoryCandidateProjection.model_validate_json(canonical(c)) for c in job["candidates"])
        projection = await self._project()
        if saved is not None:
            decision = json.loads(saved.text)
            if decision.get("job_hash") != digest(job):
                raise MemoryReviewTechnicalFailure("memory_review_decision_binding_invalid")
            return await asyncio.to_thread(self._settle_decision, active, source_event, candidates, decision)
        current = {c.candidate_id: c for c in projection.memory_candidates}
        if any(current.get(c.candidate_id) != c for c in candidates):
            await self._complete(process=active, source_event=source_event, outcome_ref="memory-consolidation:stale")
            return MemoryConsolidationRunResult(trigger_id=process.trigger_id, status="joined")
        sources = tuple(dict.fromkeys([source_event.event_id, *(c.origin.accepted_event_ref for c in candidates)]))
        context = InteriorPurposeContext(inner_turn_ref="memory-consolidation:" + digest(process.trigger_id),
            trigger_ref=process.trigger_ref, cursor=self._cursor(projection), logical_time=projection.logical_time, source_refs=sources)
        capability = {
            "source_binding": HOST_SOURCE_BINDING,
            "offered_tokens": [f"m{i}" for i in range(len(candidates))],
            "memories": [{"token": f"m{i}", "candidate": c.model_dump(mode="json"), "reading": job["readings"][i]}
                         for i, c in enumerate(candidates)],
            "instruction": "这只是到期复核机会，时间久不代表应该忘记。结合原始来源及当下生活，逐条决定继续保留或退出日常提取。保留时选择24至720小时后的下次复核。忘记不会删除原事实和经历。旧意图、未完成标签不自动是当前义务。不要改写任何来源或捏造新的经历。",
        }
        opportunity = _memory_opportunity(world_id=projection.world_id, actor_ref=self._actor_ref,
                                         purpose=PURPOSE, context=context, capability_payload=capability)
        try:
            result = await self._character_interior.consider(opportunity)
            payload = _decision_payload(result=result, opportunity=opportunity, purpose=PURPOSE,
                                        technical_failure=MemoryReviewTechnicalFailure)
            validate_payload({"contract": CONTRACT, **payload}, frozenset(capability["offered_tokens"]))
            batch = MemoryReviewBatch.model_validate_json(canonical({"contract": CONTRACT, **payload}))
        except (MemoryReviewTechnicalFailure, ValueError):
            return MemoryConsolidationRunResult(trigger_id=process.trigger_id, status="processed", work_status="technical_failure")
        fresh = await self._project()
        if fresh.world_revision != projection.world_revision or fresh.logical_time != projection.logical_time:
            await self._complete(process=active, source_event=source_event, outcome_ref="memory-consolidation:stale")
            return MemoryConsolidationRunResult(trigger_id=process.trigger_id, status="joined")
        audit = recorded_character_interior_model_result(result, purpose=PURPOSE,
            subject_ref=result.opportunity_ref, trigger_ref=opportunity.trigger_ref,
            capability_ref=opportunity.capability_manifest.capability_ref, route_tier="flash",
            route_reason_code="memory.periodic_character_review", router_version="memory-periodic-review.1",
            proposal_hash="sha256:" + digest(batch.model_dump(mode="json")))
        decision = {"job_hash": digest(job), "world_revision": fresh.world_revision,
                    "batch": batch.model_dump(mode="json"), "audit": audit.model_dump(mode="json")}
        await asyncio.to_thread(self._content.put_if_absent, StoredLifeContent(
            content_ref=decision_ref, content_kind="capability_manifest_audit",
            content_payload_hash=hashlib.sha256(canonical(decision).encode()).hexdigest(), text=canonical(decision)))
        return await asyncio.to_thread(self._settle_decision, active, source_event, candidates, decision)

    def _settle_decision(self, process, source_event, candidates, decision):
        """One paid batch, ordinary per-entity authority commits under a writer lock.

        Only this exact batch's earlier effects may advance the pinned World.
        An unrelated Clock/Fact/other change invalidates the remaining choices.
        Saved decision + deterministic effect IDs resume a crash without a new
        model call and without granting generic stale-proposal acceptance.
        """
        batch = MemoryReviewBatch.model_validate_json(canonical(decision["batch"]))
        audit = ModelResultRecordedPayload.model_validate_json(canonical(decision["audit"]))
        keys = {c.candidate_id: digest([process.trigger_id, c.candidate_id]) for c in candidates}
        allowed = {name for key in keys.values() for name in (
            "event:memory-consolidation:effect:" + key,
            "event:acceptance:transition:memory-consolidation:" + key)}
        with self._ledger.serialized_commit_sequence():
            projection = self._ledger.project()
            if any(p.trigger_id == process.trigger_id and p.state == "terminal" for p in projection.trigger_processes):
                return MemoryConsolidationRunResult(trigger_id=process.trigger_id, status="joined")
            if process.claim_lease.expires_at < projection.logical_time:
                return MemoryConsolidationRunResult(trigger_id=process.trigger_id, status="joined")
            stale = any(e.world_revision > decision["world_revision"] and e.event_id not in allowed
                        for e in projection.committed_world_event_refs)
            if not stale:
                for choice in batch.choices:
                    original = candidates[int(choice.candidate_token[1:])]
                    projection = self._ledger.project()
                    current = next((c for c in projection.memory_candidates if c.candidate_id == original.candidate_id), None)
                    effect_id = "event:memory-consolidation:effect:" + keys[original.candidate_id]
                    if current is not None and current.origin.accepted_event_ref == effect_id:
                        continue
                    if current != original:
                        stale = True
                        break
                    mutation = self._mutation(original, choice, process, projection, audit)
                    proposal = self._proposal(mutation)
                    self._persist_choice(projection, proposal, mutation, source_event)
            projection = self._ledger.project()
            completion = self._completion_event(process=process, source_event=source_event, at=projection.logical_time,
                outcome_ref="memory-consolidation:" + ("stale:" if stale else "reviewed:") + digest(decision["batch"]),
                causation_id=source_event.event_id, model_result_audit=audit)
            self._ledger.commit_at_cursor((completion,), expected_cursor=self._cursor(projection),
                                          commit_id="commit:memory-consolidation:complete:" + digest(process.trigger_id))
        return MemoryConsolidationRunResult(trigger_id=process.trigger_id, status="processed",
                                               work_status=None if stale else "reviewed")

    @staticmethod
    def _proposal(mutation):
        return MemoryCandidateProposalProjection(proposal_id=mutation.proposal_id,
            proposal_encoding="typed-authority-v1", authority_contract_ref="proposal-contract:memory-candidate.1",
            transition_kind=mutation.operation, change_id=mutation.change_id, transition_id=mutation.transition_id,
            evaluated_world_revision=mutation.evaluated_world_revision, expected_entity_revision=mutation.expected_entity_revision,
            proposed_change_hash=mutation.accepted_change_hash, evidence_refs=mutation.evidence_refs, policy_refs=MEMORY_POLICY_REFS,
            proposed_mutation=MemoryCandidateProposedMutation(event_type="MemoryCandidateForgotten" if mutation.operation == "forget" else "MemoryCandidateReviewed",
                payload_json=canonical(mutation.model_dump(mode="json"))))

    def _persist_choice(self, projection, proposal, mutation, source):
        def event(event_id, kind, payload, cause):
            return self._event(event_id=event_id, event_type=kind, payload=payload, logical_time=projection.logical_time,
                created_at=source.created_at, trace_id=source.trace_id, causation_id=cause, correlation_id=source.correlation_id)
        proposal_id = "event:proposal:" + mutation.transition_id
        previous = next((p for p in projection.memory_candidate_proposals if p.proposal_id == proposal.proposal_id), None)
        if previous is None:
            self._ledger.commit_at_cursor((event(proposal_id, "ProposalRecorded", proposal.model_dump(mode="json"), source.event_id),),
                expected_cursor=self._cursor(projection), commit_id="commit:" + proposal_id)
        elif previous != proposal:
            raise MemoryReviewTechnicalFailure("memory_review_recovery_proposal_mismatch")
        projection = self._ledger.project()
        acceptance = event("event:acceptance:" + mutation.transition_id, "AcceptanceRecorded", {
            "acceptance_id": mutation.acceptance_id, "status": "accepted", "proposal_id": mutation.proposal_id,
            "evaluated_world_revision": mutation.evaluated_world_revision, "accepted_change_id": mutation.change_id,
            "accepted_change_hash": mutation.accepted_change_hash}, proposal_id)
        effect = event(mutation.candidate_after.origin.accepted_event_ref, proposal.proposed_mutation.event_type,
                       mutation.model_dump(mode="json"), acceptance.event_id)
        self._ledger.commit_at_cursor((acceptance, effect), expected_cursor=self._cursor(projection), commit_id="commit:" + effect.event_id)

    @staticmethod
    def _mutation(candidate, choice, process, projection, audit):
        key = digest([process.trigger_id, candidate.candidate_id])
        at = projection.logical_time
        forgotten = choice.disposition == "forget"
        values = candidate.values.model_copy(update=(
            {"status": "forgotten", "retrieval_strength_bp": 0, "reviewed_at": at, "forgotten_at": at}
            if forgotten else {"reviewed_at": at, "review_due_at": at + timedelta(hours=choice.next_review_hours)}))
        origin = MemoryCandidateOrigin(change_id="change:memory-consolidation:" + key,
            transition_id="transition:memory-consolidation:" + key, policy_refs=MEMORY_POLICY_REFS,
            accepted_event_ref="event:memory-consolidation:effect:" + key)
        after = candidate.model_copy(update={"values": values, "entity_revision": candidate.entity_revision + 1,
            "origin": origin, "updated_at": at,
            "semantic_fingerprint": memory_candidate_semantic_fingerprint(values=values, policy_refs=MEMORY_POLICY_REFS)})
        raw = {"change_id": origin.change_id, "transition_id": origin.transition_id,
            "expected_entity_revision": candidate.entity_revision, "evidence_refs": tuple(memory_source_evidence(b) for b in values.source_bindings),
            "policy_refs": MEMORY_POLICY_REFS, "acceptance_id": "acceptance:memory-consolidation:" + key,
            "proposal_id": "proposal:memory-consolidation:" + key, "evaluated_world_revision": projection.world_revision,
            "accepted_change_hash": "0" * 64, "operation": "forget" if forgotten else "review",
            "candidate_before": candidate, "candidate_after": after, "character_interior_model_result": audit}
        if forgotten:
            raw.update(forget_authority=MemoryDeliberativeForgetAuthority(), strength_before_bp=candidate.values.retrieval_strength_bp,
                strength_after_bp=0, reinforcement_count_before=candidate.values.reinforcement_count,
                reinforcement_count_after=values.reinforcement_count, policy_version=MEMORY_POLICY_VERSION, policy_digest=MEMORY_POLICY_DIGEST)
        # Defaults participate in the existing canonical mutation digest.
        for key in ("revise_kind", "reinforcement_reason", "rejection_reason", "forget_authority", "strength_before_bp",
                    "strength_after_bp", "reinforcement_count_before", "reinforcement_count_after", "policy_version", "policy_digest"):
            raw.setdefault(key, None)
        raw["accepted_change_hash"] = memory_candidate_mutation_hash(raw)
        return MemoryCandidateChangedPayload.model_validate(raw)
