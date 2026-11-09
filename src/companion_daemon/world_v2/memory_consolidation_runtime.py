"""Bounded periodic memory review through CharacterInterior and ledger CAS.

Time opens an opportunity. Only the character may retain or forget. Original
Facts/Experiences and source bindings are never rewritten by this worker.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
import hashlib
import json
import logging
import os
from typing import Literal

from .character_interior.audit import recorded_character_interior_model_result
from .character_interior.life_memory import _decision_payload, _memory_opportunity
from .character_interior.purpose_context import InteriorPurposeContext
from .character_interior.run_result import CausalOpportunityRuntime
from .delayed_trigger_policies import TECHNICAL_RETRY_BACKOFF_SECONDS
from .errors import ConcurrencyConflict, IdempotencyConflict
from .proposal_audit_schemas import ModelResultRecordedPayload
from .life_content_store import StoredLifeContent
from .memory_consolidation_contract import CONTRACT, PURPOSE, HOST_SOURCE_BINDING, UNDERSTANDING_CONTRACT, MemoryReviewBatch, validate_payload
from .memory_understanding import MemoryUnderstandingReader, support_fingerprint, related_understandings
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


def balanced_memory_batch(ordered, *, limit=8):
    """Bound attention without letting one storage lane crowd out all others.

    Preserve due order within each lane. This selects evidence opportunities,
    never a character's feeling, grouping decision or response.
    """
    lanes = {}
    for candidate in ordered:
        key = tuple(sorted({b.source_kind for b in candidate.values.source_bindings}))
        lanes.setdefault(key, []).append(candidate)
    selected = []
    while len(selected) < limit and any(lanes.values()):
        for lane in lanes.values():
            if lane and len(selected) < limit:
                selected.append(lane.pop(0))
    return selected


class MemoryConsolidationRuntime(MemoryReviewLease):
    PROCESS_KIND = "memory_consolidation_review"

    def __init__(self, *, content_store, understanding_embedding=None, **kwargs):
        super().__init__(source="world-v2:memory-consolidation", **kwargs)
        self._content = content_store
        self._understanding_embedding = understanding_embedding
        self._understandings = MemoryUnderstandingReader(
            ledger=self._ledger, content_store=content_store, actor_ref=self._actor_ref)

    def _incremental_due(self, candidate, snapshot):
        if (candidate.values.status == "active"
                and support_fingerprint(candidate) not in snapshot.acknowledged):
            return candidate.updated_at + timedelta(hours=4)
        return None

    def _cooldown_until(self, projection):
        raw = os.environ.get("WORLD_V2_MEMORY_CONSOLIDATION_MIN_INTERVAL_SECONDS", "").strip()
        if not raw:
            return None
        try:
            min_interval = int(raw)
        except ValueError:
            return None
        if min_interval <= 0:
            return None
        candidates_ts = [
            ref.logical_time
            for ref in projection.committed_world_event_refs
            if ref.event_id.startswith((
                "event:memory-consolidation:open:",
                "event:memory-consolidation:effect:",
            ))
            and ref.logical_time is not None
        ]
        for proc in getattr(projection, "trigger_processes", ()):
            if getattr(proc, "process_kind", None) == self.PROCESS_KIND:
                lease = getattr(proc, "claim_lease", None)
                if lease is not None and getattr(lease, "acquired_at", None) is not None:
                    candidates_ts.append(lease.acquired_at)
        last_opened = max(candidates_ts, default=None)
        if last_opened is None:
            return None
        return last_opened + timedelta(seconds=min_interval)

    async def next_due(self, *, future_only=False):
        projection = await self._project()
        pending = [p for p in projection.trigger_processes
                   if p.process_kind == self.PROCESS_KIND and p.state != "terminal"]
        if pending:
            return min(p.claim_lease.expires_at if p.claim_lease else projection.logical_time for p in pending)
        snapshot = await asyncio.to_thread(self._understandings.schedule, projection)
        due = min((at for c in projection.memory_candidates
                   for at in (review_due(c), self._incremental_due(c, snapshot))
                   if at is not None and (not future_only or projection.logical_time is None
                                          or at > projection.logical_time)), default=None)
        if due is None:
            return None
        cooldown_until = self._cooldown_until(projection)
        if cooldown_until is not None and cooldown_until > due:
            return cooldown_until
        return due

    async def _open(self, projection):
        now = projection.logical_time
        cooldown_until = self._cooldown_until(projection)
        if cooldown_until is not None and now is not None and now < cooldown_until:
            return None
        snapshot = await asyncio.to_thread(self._understandings.schedule, projection)
        def due_at(c):
            return min((at for at in (review_due(c), self._incremental_due(c, snapshot)) if at is not None), default=None)
        if not any((d := due_at(c)) is not None and d <= now for c in projection.memory_candidates):
            return None
        coalesce_until = now + timedelta(hours=4)
        due = sorted((c for c in projection.memory_candidates if (d := due_at(c)) is not None and d <= coalesce_until),
                     key=lambda c: (due_at(c) > now, support_fingerprint(c) in snapshot.acknowledged, due_at(c), c.candidate_id))
        clocks = [e for e in projection.committed_world_event_refs if e.event_type == "ClockAdvanced"]
        if not due or not clocks:
            return None
        clock, _ = await self._lookup(clocks[-1].event_id)
        compiler = MemoryRetrievalCompiler(ledger=self._ledger, life_content_store=self._content,
                                           max_excerpt_characters=1200)
        # Remove only candidates the unchanged source reader cannot expose.
        # Rebalance until all eight selected members are readable. At that
        # point filtering later unreadable members cannot change the selection.
        # This preserves the original full-filter result without reading the
        # entire backlog for every job; no failure is turned into model text.
        readable = {}
        unreadable_seen = set()
        while due:
            first_batch = tuple(balanced_memory_batch(due))
            unread = tuple(c for c in first_batch if c.candidate_id not in readable)
            if not unread:
                break
            view = await asyncio.to_thread(
                compiler.compile, cursor=self._cursor(projection), candidates=unread,
                projection=projection, actor_ref=self._actor_ref, viewer_privacy_ceiling="private",
            )
            # Reuse a reading only inside this one pinned job construction,
            # just as the original complete compile read each candidate once.
            readable.update((item.candidate_id, item) for item in view.items)
            unreadable_ids = {c.candidate_id for c in unread} - readable.keys()
            if not unreadable_ids:
                break
            unreadable_seen.update(unreadable_ids)
            due = [c for c in due if c.candidate_id not in unreadable_ids]
        else:
            return None
        due = balanced_memory_batch([c for c in due if c.candidate_id in readable])
        if not due or not any((d := due_at(c)) is not None and d <= now for c in due):
            return None
        # Actual role cognition still resolves full receipt, source and privacy
        # authority. Idle polling needs only the incremental review schedule.
        snapshot = await asyncio.to_thread(self._understandings.read, projection)
        related = self._understandings.context_candidates(projection, due, snapshot, exclude_ids=unreadable_seen)
        context_view = await asyncio.to_thread(
            MemoryRetrievalCompiler(ledger=self._ledger, life_content_store=self._content,
                                    max_excerpt_characters=1200).compile,
            cursor=self._cursor(projection), candidates=related, projection=projection,
            actor_ref=self._actor_ref, viewer_privacy_ceiling="private",
        )
        context_readings = {r.candidate_id: r for r in context_view.items}
        related = tuple(c for c in related if c.candidate_id in context_readings)
        standing, selection = await asyncio.to_thread(related_understandings,
            snapshot.active, [readable[c.candidate_id].model_dump(mode="json") for c in due],
            embedding=self._understanding_embedding)
        job = {"contract": "memory-review-job.2", "world_id": projection.world_id,
               "actor_ref": self._actor_ref, "clock_event_ref": clock.event_id,
               "opening_world_revision": projection.world_revision,
               "candidates": [c.model_dump(mode="json") for c in due],
               "readings": [readable[c.candidate_id].model_dump(mode="json") for c in due],
               "context_candidates": [c.model_dump(mode="json") for c in related],
               "context_readings": [context_readings[c.candidate_id].model_dump(mode="json") for c in related],
               "understanding_refs": [u.version_ref for u in standing],
               "standing_selection": selection,
               "understanding_readings": [u.model_view(pending_count=snapshot.pending_count) for u in standing]}
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
        context_candidates = tuple(MemoryCandidateProjection.model_validate_json(canonical(c))
                                   for c in job.get("context_candidates", []))
        standing_view = await asyncio.to_thread(self._understandings.read, projection)
        active_refs = {v.version_ref for v in standing_view.active}
        if (any(current.get(c.candidate_id) != c for c in (*candidates, *context_candidates))
                or not set(job.get("understanding_refs", [])).issubset(active_refs)):
            await self._complete(process=active, source_event=source_event, outcome_ref="memory-consolidation:stale")
            return MemoryConsolidationRunResult(trigger_id=process.trigger_id, status="joined")
        sources = tuple(dict.fromkeys([source_event.event_id,
                                      *(c.origin.accepted_event_ref for c in (*candidates, *context_candidates)),
                                      *job.get("understanding_refs", [])]))
        context = InteriorPurposeContext(inner_turn_ref="memory-consolidation:" + digest(process.trigger_id),
            trigger_ref=process.trigger_ref, cursor=self._cursor(projection), logical_time=projection.logical_time, source_refs=sources)
        capability = {
            "source_binding": HOST_SOURCE_BINDING,
            "offered_tokens": [f"m{i}" for i in range(len(candidates))],
            "memories": [{"token": f"m{i}", "candidate": c.model_dump(mode="json"), "reading": job["readings"][i]}
                         for i, c in enumerate(candidates)],
            "instruction": "这只是到期复核机会，时间久不代表应该忘记。结合原始来源及当下生活，逐条决定继续保留或退出日常提取。保留时选择24至720小时后的下次复核。忘记不会删除原事实和经历。旧意图、未完成标签不自动是当前义务。不要改写任何来源或捏造新的经历。",
        }
        if job["contract"] == "memory-review-job.2":
            capability.update(
                understanding_contract=UNDERSTANDING_CONTRACT,
                understanding_tokens=[f"u{i}" for i in range(len(job["understanding_refs"]))],
                understanding_source_tokens=[*(f"m{i}" for i in range(len(candidates))),
                                             *(f"k{i}" for i in range(len(context_candidates)))],
                attention_token_map={**{f"m{i}": c.origin.accepted_event_ref for i,c in enumerate(candidates)},
                                     **{f"k{i}": c.origin.accepted_event_ref for i,c in enumerate(context_candidates)},
                                     **{f"u{i}": ref for i,ref in enumerate(job["understanding_refs"])}},
                attention_instruction="attended_source_refs也可使用本轮已给出的m/k/u令牌；宿主只还原这些精确身份，不替你选择关注什么。",
                standing_understandings=[{"token": f"u{i}", **value}
                                        for i, value in enumerate(job["understanding_readings"])],
                related_memories=[{"token": f"k{i}", "reading": r}
                                  for i, r in enumerate(job["context_readings"])],
                understanding_instruction=(
                    "understandings=[]表示没有新的长期理解，完全正常。只有值得留下的才记录，最多3条。"
                    "理解用于从你自己的真实生活处境（家里近况与经济压力、学业与勤工助学、社团与身边人关系、与对方的相处经历）提炼出这一阶段的行为习惯、自我要求与处事心气："
                    "写清因为自己所处的具体生活处境，所以这一阶段做事养成了什么习惯、对自己有什么要求（例如因为家里变故或经济吃紧，所以最近做事都要注意什么、要自立不给家里添麻烦）。"
                    "单次事件的具体金额、具体日期或编号已保存在底层记忆并由evidence_quotes绑定；修订旧的standing_understandings时直接整句重写为当前阶段的完整理解，不在旧句末尾机械续写，也不必在长期理解里背诵单次事件数字。"
                    "已有方面若已准确表达了生活处境与阶段习惯且无实质变化就不更新；同一方面的重复记录选merge；retain仅用于此前没有的新方面；已翻篇的具体事务选release放下。"
                    "summary用自然平实的纯中文日常第一人称大白话写你自己的生活处境与阶段要求，不夹杂英文单词，不写成空泛格言或工作台账。"
                    "不要通过另起名字重复旧观点，只有有意义的差异才分别留下。"
                    "可以把新经历与旧记忆连起来、加强或修正已有理解；选择revise更新同一方面，"
                    "merge只合并你认为同一方面的重复理解，不要把不同人和不同事情压成一条总心得。"
                    "retain建立独立理解，release搁下旧理解。facet由你命名，summary可以很短。"
                    "predecessor_tokens只选择要更新的u令牌。用evidence_quotes选实际支撑这次理解的m/k记忆，"
                    "并摘原文；无需重复source_tokens列表。引文必须逐字出现在对应reading正文中，可从同一来源摘多段。"
                    "理解与信心是你的，可犹豫、负面或改变；不代表新增外部经历或强制当前情绪。"
                    "不需要重复已有原文，更不能新增来源里没有的人物、行动、对话和结果。"
                    "事实摘录的subject_ref和speaker_ref表示主体与说话者，不要把对方的‘我’当成你自己的经历。"
                    "不要把与身边人的经历混淆成与聊天对象的事。"
                ),
            )
        opportunity = _memory_opportunity(world_id=projection.world_id, actor_ref=self._actor_ref,
                                         purpose=PURPOSE, context=context, capability_payload=capability)
        try:
            result = await self._character_interior.consider(opportunity)
            payload = _decision_payload(result=result, opportunity=opportunity, purpose=PURPOSE,
                                        technical_failure=MemoryReviewTechnicalFailure)
            validate_payload({"contract": CONTRACT, **payload}, frozenset(capability["offered_tokens"]),
                             source_tokens=set(capability.get("understanding_source_tokens", [])),
                             predecessor_tokens=set(capability.get("understanding_tokens", [])),
                             readings={**{f"m{i}": r for i, r in enumerate(job["readings"])},
                                       **{f"k{i}": r for i, r in enumerate(job.get("context_readings", []))}})
            batch = MemoryReviewBatch.model_validate_json(canonical({"contract": CONTRACT, **payload}))
            if job["contract"] == "memory-review-job.2" and batch.understandings is None:
                raise ValueError("explicit understanding updates or [] required")
        except (MemoryReviewTechnicalFailure, ValueError) as exc:
            logging.getLogger(__name__).warning(
                "memory consolidation failed trigger=%s cause=%s detail=%s",
                process.trigger_id, type(exc).__name__, str(exc)[:512],
            )
            return MemoryConsolidationRunResult(trigger_id=process.trigger_id, status="processed", work_status="technical_failure")
        fresh = await self._project()
        if fresh.world_revision != projection.world_revision or fresh.logical_time != projection.logical_time:
            await self._complete(process=active, source_event=source_event, outcome_ref="memory-consolidation:stale")
            return MemoryConsolidationRunResult(trigger_id=process.trigger_id, status="joined")
        audit = recorded_character_interior_model_result(result, purpose=PURPOSE,
            subject_ref=result.opportunity_ref, trigger_ref=opportunity.trigger_ref,
            capability_ref=opportunity.capability_manifest.capability_ref, route_tier="flash",
            route_reason_code="memory.periodic_character_review", router_version="memory-periodic-review.1",
            proposal_hash="sha256:" + digest(batch.model_dump(mode="json")),
            causal_opportunity=CausalOpportunityRuntime(world_id=projection.world_id, actor_ref=self._actor_ref,
                purpose=PURPOSE).identity_for_refs(opportunity.source_refs, epoch=opportunity.trigger_ref))
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
        if audit.proposal_hash != "sha256:" + digest(batch.model_dump(mode="json")):
            raise MemoryReviewTechnicalFailure("memory_review_saved_author_binding_invalid")
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
