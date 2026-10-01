"""Rebuildable standing understandings from completed role-owned memory batches.

The sidecar alone is never authority. Each version requires the immutable
successful batch receipt, its exact model audit and selected memory sources.
No new World Fact, personality assignment, or emotion transition is produced.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import json
import math

from .memory_consolidation_contract import MemoryReviewBatch, validate_payload
from .memory_consolidation_contract import source_texts
from .memory_retrieval import MemoryRetrievalCompiler
from .proposal_audit_schemas import ModelResultRecordedPayload
from .schemas import MemoryCandidateProjection, ProjectionCursor


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def support_fingerprint(candidate):
    """Retention-review clocks/strength are not changes to remembered content."""
    values = candidate.values
    return digest({"summary_ref": values.summary_ref, "summary_hash": values.summary_payload_hash,
                   "sources": [b.model_dump(mode="json") for b in values.source_bindings],
                   "status": values.status, "privacy_ceiling": values.privacy_ceiling})


def related_understandings(versions, readings, *, embedding=None, limit=8):
    """Retrieve possible matches; only the role may merge their meanings."""
    from .associative_recall import lexical_relevance_bp
    from .recall_index import RecallEmbeddingUnavailable
    if not versions:
        return (), {"embedding_status":"not_needed"}
    docs = tuple(v.facet + "\n" + v.summary for v in versions)
    queries = tuple("\n".join(source_texts(r))[:1024] for r in readings)
    scores = [max((lexical_relevance_bp(q,(d,)) for q in queries), default=0) for d in docs]
    diagnostic = {"embedding_status":"not_configured"}
    if embedding is not None:
        try:
            vectors = tuple(embedding.embed((*docs,*queries)))
            if len(vectors) != len(docs)+len(queries):
                raise ValueError("related memory embedding count differs")
            def normalize(vector):
                if not vector or any(not math.isfinite(float(x)) for x in vector):
                    raise ValueError("invalid related memory vector")
                length=math.sqrt(sum(float(x)**2 for x in vector))
                if length == 0:
                    raise ValueError("zero related memory vector")
                return tuple(float(x)/length for x in vector)
            vectors=tuple(normalize(v) for v in vectors)
            scores=[max((sum(a*b for a,b in zip(v,q,strict=True)) for q in vectors[len(docs):]),default=0)
                    for v in vectors[:len(docs)]]
            diagnostic={"embedding_status":"used","embedding_version":embedding.version}
        except (RecallEmbeddingUnavailable,ValueError,TypeError):
            diagnostic={"embedding_status":"degraded","fallback":"lexical"}
    order=sorted(range(len(versions)),key=lambda i:(scores[i],versions[i].selected_at,versions[i].version_ref),reverse=True)
    return tuple(versions[i] for i in order[:limit]), diagnostic


@dataclass(frozen=True)
class UnderstandingVersion:
    version_ref: str
    facet: str
    summary: str
    confidence_bp: int
    selected_at: datetime
    actor_ref: str
    sources: tuple[MemoryCandidateProjection, ...]
    predecessor_refs: tuple[str, ...]
    receipt_ref: str
    receipt_world_revision: int
    receipt_hash: str
    operation: str
    dependencies: tuple[MemoryCandidateProjection, ...] = ()
    read_status: str = "active"

    def model_view(self, *, pending_count=0):
        return {"source_ref": self.version_ref, "facet": self.facet, "summary": self.summary,
                "confidence_bp": self.confidence_bp, "selected_at": self.selected_at.isoformat(),
                "scope": "role_authored_revisable_understanding_not_world_fact_or_current_emotion",
                "epistemic_scope": "private_interpretation", "authority": "defeasible_interpretation",
                "status": self.read_status,
                "freshness": "new_memories_pending_review" if pending_count else "reviewed_sources",
                "source_candidate_refs": [c.candidate_id for c in self.sources],
                "predecessor_refs": list(self.predecessor_refs)}


@dataclass(frozen=True)
class UnderstandingSnapshot:
    versions: tuple[UnderstandingVersion, ...]
    active: tuple[UnderstandingVersion, ...]
    acknowledged: frozenset[str]
    suppressions: tuple[tuple[str, str], ...]
    pending_count: int = 0
    recallable: tuple[UnderstandingVersion, ...] = ()


class MemoryUnderstandingReader:
    """One source-closed reader shared by background review and later cognition."""

    def __init__(self, *, ledger, content_store, actor_ref):
        self.ledger, self.store, self.actor_ref = ledger, content_store, actor_ref

    def _content(self, ref):
        body = self.store.read_exact(content_ref=ref)
        if (body is None or body.content_kind != "capability_manifest_audit"
                or hashlib.sha256(body.text.encode()).hexdigest() != body.content_payload_hash):
            raise ValueError("memory understanding content proof unavailable")
        return json.loads(body.text)

    def read(self, projection):
        if self.store is None:
            return UnderstandingSnapshot((), (), frozenset(), ())
        processes = {p.trigger_id: p for p in projection.trigger_processes
                     if p.process_kind == "memory_consolidation_review" and p.state == "terminal"
                     and (p.runtime_outcome_ref or "").startswith("memory-consolidation:reviewed:")}
        versions, retired, acknowledged, failures = [], set(), set(), []
        committed = {e.event_id: e for e in projection.committed_world_event_refs}
        # Read only memory-batch receipts, never every completed process in a
        # months-long ledger on each chat.
        receipts = [located for p in processes.values() if p.attempt_ids
                    for ref in ("event:memory-review:completed:" + digest([p.trigger_id, p.attempt_ids[-1]]),)
                    for located in (self.ledger.lookup_event_commit(ref),) if located is not None
                    and located[1].ledger_sequence <= projection.ledger_sequence]
        # Completion is a deliberation receipt, not a committed World event.
        # Its existence proves an authored private view only, never new facts.
        for event, receipt_commit in sorted(receipts, key=lambda pair: pair[1].ledger_sequence):
            payload = event.payload()
            process = processes.get(payload.get("trigger_id"))
            if process is None:
                continue
            try:
                if event.world_id != projection.world_id or event.event_type != "TriggerProcessCompleted":
                    raise ValueError("completion receipt changed world or type")
                job = self._content(process.trigger_ref)
                if job.get("contract") != "memory-review-job.2":
                    continue  # Choice-only historical reviews authored no understanding.
                if (job["world_id"] != projection.world_id or job["actor_ref"] != self.actor_ref
                        or process.trigger_ref != "memory-review-job:" + digest(job)
                        or job["clock_event_ref"] != process.source_evidence_ref):
                    raise ValueError("understanding job identity changed")
                decision = self._content("memory-review-decision:" + digest(process.trigger_id))
                batch = MemoryReviewBatch.model_validate_json(canonical(decision["batch"]))
                audit = ModelResultRecordedPayload.model_validate_json(canonical(decision["audit"]))
                lineage = json.loads(audit.audit_json).get("character_interior_lineage") or {}
                if (decision["job_hash"] != digest(job)
                        or payload.get("character_interior_model_result") != audit.model_dump(mode="json")
                        or audit.proposal_hash != "sha256:" + digest(batch.model_dump(mode="json"))
                        or payload.get("runtime_outcome_ref") != process.runtime_outcome_ref
                        or process.runtime_outcome_ref != "memory-consolidation:reviewed:" + digest(decision["batch"])):
                    raise ValueError("standing understanding has no exact successful author receipt")
                if (lineage.get("purpose") != "memory_consolidation"
                        or lineage.get("causal_actor_ref") != self.actor_ref
                        or lineage.get("causal_world_id") != projection.world_id
                        or lineage.get("causal_epoch") != process.trigger_ref):
                    raise ValueError("standing understanding author belongs to another actor or purpose")
                candidates = {
                    f"m{i}": MemoryCandidateProjection.model_validate_json(canonical(c))
                    for i, c in enumerate(job["candidates"])
                }
                candidates.update({f"k{i}": MemoryCandidateProjection.model_validate_json(canonical(c))
                                   for i, c in enumerate(job.get("context_candidates", []))})
                previous = {f"u{i}": ref for i, ref in enumerate(job.get("understanding_refs", []))}
                known = {v.version_ref for v in versions if v.version_ref not in retired}
                validate_payload(batch.model_dump(mode="json"), {f"m{i}" for i in range(len(job["candidates"]))},
                                 source_tokens=set(candidates), predecessor_tokens=set(previous),
                                 readings={**{f"m{i}": r for i, r in enumerate(job["readings"])},
                                           **{f"k{i}": r for i, r in enumerate(job.get("context_readings", []))}})
                if batch.understandings is None:
                    raise ValueError("new memory batch lacks explicit understanding decision")
                # A saved model decision or a forged completion marker is not
                # enough. Every retention choice must have its typed effect
                # committed before this receipt under the very same author.
                for choice in batch.choices:
                    candidate = candidates[choice.candidate_token]
                    effect_ref = "event:memory-consolidation:effect:" + digest([process.trigger_id, candidate.candidate_id])
                    effect = committed.get(effect_ref)
                    located_effect = self.ledger.lookup_event_commit(effect_ref) if effect else None
                    if (effect is None or located_effect is None
                            or located_effect[1].ledger_sequence >= receipt_commit.ledger_sequence
                            or effect.payload_hash != located_effect[0].payload_hash
                            or located_effect[0].payload().get("character_interior_model_result") != audit.model_dump(mode="json")):
                        raise ValueError("understanding batch has incomplete or differently authored effects")
                additions, replacements = [], set()
                prior_by_ref = {v.version_ref: v for v in versions}
                for number, update in enumerate(batch.understandings):
                    predecessors = tuple(previous[t] for t in update.predecessor_tokens)
                    if not set(predecessors).issubset(known):
                        raise ValueError("understanding predecessor is not accepted and active")
                    replacements.update(predecessors)
                    selected = tuple(candidates[t] for t in update.selected_source_tokens)
                    inherited = {c.candidate_id: c for ref in predecessors
                                 for c in (prior_by_ref[ref].dependencies or prior_by_ref[ref].sources)}
                    inherited.update({c.candidate_id: c for c in selected})
                    additions.append(UnderstandingVersion(
                        version_ref="memory-understanding:" + digest([process.trigger_id, number]),
                        facet=update.facet, summary=update.summary, confidence_bp=update.confidence_bp,
                        selected_at=event.logical_time, actor_ref=self.actor_ref,
                        sources=selected, dependencies=tuple(inherited.values()),
                        predecessor_refs=predecessors, receipt_ref=event.event_id,
                        receipt_world_revision=receipt_commit.world_revision, receipt_hash=event.payload_hash,
                        operation=update.operation,
                    ))
                # No partial effect from an invalid batch, including no source acknowledgement.
                versions.extend(additions)
                retired.update(replacements)
                acknowledged.update(support_fingerprint(c) for t, c in candidates.items() if t.startswith("m"))
            except (ValueError, KeyError, TypeError):
                failures.append((event.event_id, "receipt_or_content_proof_failed"))
        current = {c.candidate_id: c for c in projection.memory_candidates}
        possible = []
        for version in versions:
            if any(c.candidate_id not in current or support_fingerprint(current[c.candidate_id]) != support_fingerprint(c)
                   for c in (version.dependencies or version.sources)):
                failures.append((version.version_ref, "source_changed_or_withdrawn"))
                continue
            possible.append(version)
        selected_sources = {c.candidate_id: current[c.candidate_id] for v in possible
                            for c in (v.dependencies or v.sources)}
        readable = MemoryRetrievalCompiler(ledger=self.ledger, life_content_store=self.store).compile(
            cursor=ProjectionCursor(world_revision=projection.world_revision,
                                    deliberation_revision=projection.deliberation_revision,
                                    ledger_sequence=projection.ledger_sequence),
            candidates=tuple(selected_sources.values()), projection=projection,
            actor_ref=self.actor_ref, viewer_privacy_ceiling="private",
        ) if selected_sources else None
        readable_ids = {c.candidate_id for c in readable.items} if readable else set()
        recallable = []
        for version in possible:
            if all(c.candidate_id in readable_ids for c in (version.dependencies or version.sources)):
                status = "released" if version.operation == "release" else "superseded" if version.version_ref in retired else "active"
                recallable.append(replace(version, read_status=status))
            else:
                failures.append((version.version_ref, "source_privacy_or_read_proof_failed"))
        pending = sum(c.values.status == "active" and support_fingerprint(c) not in acknowledged
                      for c in projection.memory_candidates)
        active = tuple(v for v in recallable if v.read_status == "active")
        return UnderstandingSnapshot(tuple(versions), active, frozenset(acknowledged), tuple(failures), pending, tuple(recallable))

    def context_candidates(self, projection, primary, snapshot, *, limit=4):
        """Exact reviewed story links and standing sources, not social rules."""
        wanted = {b.source_id for c in primary for b in c.values.source_bindings}
        related = set()
        for row in projection.prehistory_records:
            if row.record.record_id in wanted:
                related.update(row.record.related_record_refs)
        wanted.update(related)
        standing = {c.candidate_id for v in snapshot.active[-8:] for c in v.sources}
        primary_ids = {c.candidate_id for c in primary}
        available = [c for c in projection.memory_candidates
                     if c.values.status == "active" and c.candidate_id not in primary_ids
                     and (c.candidate_id in standing or any(b.source_id in wanted for b in c.values.source_bindings))]
        available.sort(key=lambda c: (
            not any(b.source_id in related for b in c.values.source_bindings),
            -c.values.retrieval_strength_bp, c.candidate_id,
        ))
        return tuple(available[:limit])


def install_understanding_context(context, snapshot, *, limit=6, offered_refs=None):
    """Keep standing private readings separate from World facts and live mood."""
    items = []
    available=sorted(snapshot.active, key=lambda v: (v.selected_at, v.version_ref), reverse=True)
    selected=([v for v in available if v.version_ref in offered_refs] if offered_refs is not None else available[:limit])
    for version in selected:
        items.append({"item_ref": version.version_ref, "source_ref": version.version_ref,
                      "privacy_class": "private", "attention_source_refs": [version.receipt_ref],
                      "source_bindings": [{"source_kind": "immutable_payload",
                          "authority_type": "TriggerProcessCompleted", "ref": version.receipt_ref,
                          "source_world_revision": version.receipt_world_revision,
                          "immutable_hash": version.receipt_hash}],
                      "value": version.model_view(pending_count=snapshot.pending_count)})
    slices = dict(context.get("slices", {}))
    slices["memory_understandings"] = {"availability": "available", "items": items}
    return {**context, "slices": slices}
