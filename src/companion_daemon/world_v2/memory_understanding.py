"""Rebuildable standing understandings from completed role-owned memory batches.

The sidecar alone is never authority. Each version requires the immutable
successful batch receipt, its exact model audit and selected memory sources.
No new World Fact, personality assignment, or emotion transition is produced.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import hashlib
import json
import math
import sqlite3
from threading import RLock
from weakref import finalize

from .memory_consolidation_contract import MemoryReviewBatch, validate_payload
from .memory_consolidation_contract import source_texts
from .memory_retrieval import MemoryRetrievalCompiler
from .immutable_value_cache import immutable_value_cache
from .projection_lookup import lookup_last
from .proposal_audit_schemas import ModelResultRecordedPayload
from .schemas import MemoryCandidateProjection, ProjectionCursor


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@immutable_value_cache(maxsize=1024)
def _parse_audit(raw):
    return ModelResultRecordedPayload.model_validate_json(raw)


@immutable_value_cache(maxsize=4096)
def _parse_candidate(raw):
    return MemoryCandidateProjection.model_validate_json(raw)


@immutable_value_cache(maxsize=2048)
def _verified_content_body(content_kind: str, content_payload_hash: str, text: str):
    if (content_kind != "capability_manifest_audit"
            or hashlib.sha256(text.encode()).hexdigest() != content_payload_hash):
        raise ValueError("memory understanding content proof unavailable")
    return json.loads(text)


@immutable_value_cache(maxsize=4096)
def _event_model_result_raw(payload_json: str) -> str:
    return canonical(json.loads(payload_json).get("character_interior_model_result"))


@immutable_value_cache(maxsize=1024)
def _validated_batch_material(
    world_id: str,
    actor_ref: str,
    trigger_id: str,
    trigger_ref: str,
    source_evidence_ref: str | None,
    runtime_outcome_ref: str | None,
    event_world_id: str,
    event_type: str,
    event_payload_json: str,
    job_kind: str,
    job_hash: str,
    job_text: str,
    decision_kind: str | None,
    decision_hash: str | None,
    decision_text: str | None,
):
    payload = json.loads(event_payload_json)
    if (event_world_id != world_id or event_type != "TriggerProcessCompleted"
            or payload.get("trigger_id") != trigger_id):
        raise ValueError("completion receipt changed world or type")
    job = _verified_content_body(job_kind, job_hash, job_text)
    if job.get("contract") != "memory-review-job.2":
        return None
    if (job["world_id"] != world_id or job["actor_ref"] != actor_ref
            or trigger_ref != "memory-review-job:" + digest(job)
            or job["clock_event_ref"] != source_evidence_ref):
        raise ValueError("understanding job identity changed")
    if decision_kind is None or decision_hash is None or decision_text is None:
        raise ValueError("memory understanding content proof unavailable")
    decision = _verified_content_body(decision_kind, decision_hash, decision_text)
    batch = MemoryReviewBatch.model_validate_json(canonical(decision["batch"]))
    audit = _parse_audit(canonical(decision["audit"]))
    audit_dump = audit.model_dump(mode="json")
    audit_raw = canonical(audit_dump)
    batch_dump = batch.model_dump(mode="json")
    lineage = json.loads(audit.audit_json).get("character_interior_lineage") or {}
    if (decision["job_hash"] != digest(job)
            or payload.get("character_interior_model_result") != audit_dump
            or audit.proposal_hash != "sha256:" + digest(batch_dump)
            or payload.get("runtime_outcome_ref") != runtime_outcome_ref
            or runtime_outcome_ref != "memory-consolidation:reviewed:" + digest(decision["batch"])):
        raise ValueError("standing understanding has no exact successful author receipt")
    if (lineage.get("purpose") != "memory_consolidation"
            or lineage.get("causal_actor_ref") != actor_ref
            or lineage.get("causal_world_id") != world_id
            or lineage.get("causal_epoch") != trigger_ref):
        raise ValueError("standing understanding author belongs to another actor or purpose")
    candidates = {
        f"m{i}": _parse_candidate(canonical(c))
        for i, c in enumerate(job["candidates"])
    }
    candidates.update({f"k{i}": _parse_candidate(canonical(c))
                       for i, c in enumerate(job.get("context_candidates", []))})
    previous = {f"u{i}": ref for i, ref in enumerate(job.get("understanding_refs", []))}
    validate_payload(batch_dump, {f"m{i}" for i in range(len(job["candidates"]))},
                     source_tokens=set(candidates), predecessor_tokens=set(previous),
                     readings={**{f"m{i}": r for i, r in enumerate(job["readings"])},
                               **{f"k{i}": r for i, r in enumerate(job.get("context_readings", []))}})
    if batch.understandings is None:
        raise ValueError("new memory batch lacks explicit understanding decision")
    effect_refs = tuple(
        "event:memory-consolidation:effect:" + digest([trigger_id, candidates[choice.candidate_token].candidate_id])
        for choice in batch.choices
    )
    return batch, candidates, previous, audit_raw, effect_refs


@immutable_value_cache(maxsize=4096)
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
        self._schedule_lock = RLock()
        self._schedule_index = None
        self._schedule_sequence = 0
        self._schedule_generation = None
        self._read_lock = RLock()
        self._read_cache: OrderedDict[tuple[object, ...], UnderstandingSnapshot] = OrderedDict()
        self._batch_cache: OrderedDict[tuple[object, ...], object] = OrderedDict()
        self._retrieval_compiler = (
            MemoryRetrievalCompiler(ledger=self.ledger, life_content_store=self.store)
            if self.store is not None
            else None
        )

    def schedule(self, projection):
        """Incremental review acknowledgements, not a semantic/permission read.

        Only the concrete verified append-only adapters may reuse old batch
        proofs. Historical projections and substituted readers retain the full
        independent reader. A private temporary SQLite index keeps historical
        fingerprints off the Python heap; its page cache has a fixed budget.
        """
        from .sqlite_ledger import SQLiteWorldLedger
        from .life_content_store import SQLiteImmutableLifeContentStore
        from .context_read_scope import current_content_snapshot

        if (type(self.ledger) is not SQLiteWorldLedger
                or type(self.store) is not SQLiteImmutableLifeContentStore
                or "lookup_event_commit" in vars(self.ledger)
                or "read_exact" in vars(self.store)
                or current_content_snapshot(self.store) is not None
                or projection is not self.ledger.project()):
            return self.read(projection)
        if not self._schedule_lock.acquire(blocking=False):
            return self.read(projection)
        try:
            try:
                return self._schedule(projection)
            except BaseException:
                self._schedule_generation = None
                raise
        finally:
            self._schedule_lock.release()

    def _schedule(self, projection):
        generation = (self.ledger.verified_history_generation(), *self.store.read_generations())
        cursor = ProjectionCursor(world_revision=projection.world_revision,
                                  deliberation_revision=projection.deliberation_revision,
                                  ledger_sequence=projection.ledger_sequence)
        if self._schedule_index is None:
            self._schedule_index = sqlite3.connect("", isolation_level=None, check_same_thread=False)
            finalize(self, self._schedule_index.close)
            self._schedule_index.executescript("""
                PRAGMA cache_size=-1024;
                CREATE TABLE acknowledged(fingerprint TEXT PRIMARY KEY);
                CREATE TABLE versions(ref TEXT PRIMARY KEY, active INTEGER NOT NULL);
                CREATE TABLE pending(trigger_id TEXT PRIMARY KEY, blocked INTEGER NOT NULL DEFAULT 0,
                                     receipt_ref TEXT);
            """)
        db = self._schedule_index
        old = self._schedule_generation
        if (old is None or generation[0] != old[0] or generation[2] != old[2]
                or projection.ledger_sequence < self._schedule_sequence):
            db.executescript("DELETE FROM acknowledged; DELETE FROM versions; DELETE FROM pending;")
            self._schedule_sequence = 0
        elif generation[1] != old[1]:
            # New bytes may repair a missing immutable job. Check only those
            # failed batches first; unrelated appends never re-fold good history.
            for identity, in db.execute("SELECT trigger_id FROM pending WHERE blocked=1").fetchall():
                process = self.ledger.trigger_processes_at((identity,), cursor=cursor).get(identity)
                if process is None or not process.attempt_ids:
                    continue
                ref = "event:memory-review:completed:" + digest([identity, process.attempt_ids[-1]])
                located = self.ledger.lookup_event_commit(ref)
                if located is None or located[1].ledger_sequence > projection.ledger_sequence:
                    continue
                try:
                    restored = self._validated_batch(projection, process, *located)
                except (ValueError, KeyError, TypeError):
                    continue
                if restored is not None:
                    # A repaired earlier batch can change later predecessor
                    # validity. Rebuild in receipt order only on actual repair.
                    self._schedule_generation = None
                    return self._schedule(projection)
        after = self._schedule_sequence
        while after < projection.ledger_sequence:
            page = self.ledger.events_after(
                cursor=cursor, after_sequence=after,
                event_types=frozenset({"TriggerProcessOpened", "TriggerProcessCompleted"}),
            )
            if not page:
                break
            identities = {event.payload().get("trigger_id") or event.payload().get("process", {}).get("trigger_id")
                          for _, event in page}
            identities.discard(None)
            processes = self.ledger.trigger_processes_at(tuple(identities), cursor=cursor)
            db.executemany("INSERT OR IGNORE INTO pending(trigger_id) VALUES(?)", (
                (p.trigger_id,) for p in processes.values() if p.process_kind == "memory_consolidation_review"))
            after = page[-1][0]
        identities = [row[0] for row in db.execute("SELECT trigger_id FROM pending WHERE blocked=0")]
        receipts = []
        for offset in range(0, len(identities), 256):
            processes = self.ledger.trigger_processes_at(identities[offset:offset + 256], cursor=cursor)
            for identity, process in processes.items():
                if process.state != "terminal":
                    continue
                if not (process.runtime_outcome_ref or "").startswith("memory-consolidation:reviewed:"):
                    db.execute("DELETE FROM pending WHERE trigger_id=?", (identity,))
                    continue
                ref = ("event:memory-review:completed:" + digest([identity, process.attempt_ids[-1]])
                       if process.attempt_ids else None)
                located = self.ledger.lookup_event_commit(ref) if ref else None
                if located is not None and located[1].ledger_sequence <= projection.ledger_sequence:
                    receipts.append((process, *located))
        for process, event, commit in sorted(receipts, key=lambda row: row[2].ledger_sequence):
            blocked = 1
            try:
                verified = self._validated_batch(projection, process, event, commit)
                if verified is None:
                    db.execute("DELETE FROM pending WHERE trigger_id=?", (process.trigger_id,))
                    continue
                batch, sources, previous = verified
                replacements = {previous[token] for update in batch.understandings for token in update.predecessor_tokens}
                blocked = 2  # Immutable predecessor order cannot heal from unrelated new bytes.
                if any(not db.execute("SELECT 1 FROM versions WHERE ref=? AND active=1", (ref,)).fetchone()
                       for ref in replacements):
                    raise ValueError("understanding predecessor is not accepted and active")
                db.execute("BEGIN")
                try:
                    db.executemany("UPDATE versions SET active=0 WHERE ref=?", ((ref,) for ref in replacements))
                    db.executemany("INSERT INTO versions VALUES(?,1)", (
                        ("memory-understanding:" + digest([process.trigger_id, number]),)
                        for number, _ in enumerate(batch.understandings)))
                    db.executemany("INSERT OR IGNORE INTO acknowledged VALUES(?)", (
                        (support_fingerprint(candidate),) for token, candidate in sources.items() if token.startswith("m")))
                    db.execute("DELETE FROM pending WHERE trigger_id=?", (process.trigger_id,))
                    db.execute("COMMIT")
                except BaseException:
                    db.execute("ROLLBACK")
                    raise
            except (ValueError, KeyError, TypeError):
                db.execute("INSERT OR REPLACE INTO pending VALUES(?,?,?)", (process.trigger_id, blocked, event.event_id))
        if (self.ledger.verified_history_generation(), *self.store.read_generations()) != generation:
            self._schedule_generation = None
            raise ValueError("memory schedule proof changed during its read")
        self._schedule_generation = generation
        self._schedule_sequence = projection.ledger_sequence
        fingerprints = {support_fingerprint(c) for c in projection.memory_candidates if c.values.status == "active"}
        acknowledged = frozenset(fp for fp in fingerprints
                                 if db.execute("SELECT 1 FROM acknowledged WHERE fingerprint=?", (fp,)).fetchone())
        failures = tuple((ref, "receipt_or_content_proof_failed")
                         for ref, in db.execute("SELECT receipt_ref FROM pending WHERE blocked>0"))
        pending = sum(c.values.status == "active" and support_fingerprint(c) not in acknowledged
                      for c in projection.memory_candidates)
        return UnderstandingSnapshot((), (), acknowledged, failures, pending)

    def _content(self, ref):
        body = self.store.read_exact(content_ref=ref)
        if body is None:
            raise ValueError("memory understanding content proof unavailable")
        return _verified_content_body(body.content_kind, body.content_payload_hash, body.text)

    def _validated_batch(self, projection, process, event, receipt_commit, *, committed=None):
        if event.world_id != projection.world_id or event.event_type != "TriggerProcessCompleted":
            raise ValueError("completion receipt changed world or type")
        job_body = self.store.read_exact(content_ref=process.trigger_ref)
        if job_body is None:
            raise ValueError("memory understanding content proof unavailable")
        decision_body = self.store.read_exact(
            content_ref="memory-review-decision:" + digest(process.trigger_id)
        )
        material = _validated_batch_material(
            projection.world_id,
            self.actor_ref,
            process.trigger_id,
            process.trigger_ref,
            process.source_evidence_ref,
            process.runtime_outcome_ref,
            event.world_id,
            event.event_type,
            event.payload_json,
            job_body.content_kind,
            job_body.content_payload_hash,
            job_body.text,
            decision_body.content_kind if decision_body is not None else None,
            decision_body.content_payload_hash if decision_body is not None else None,
            decision_body.text if decision_body is not None else None,
        )
        if material is None:
            return None  # Choice-only historical reviews authored no understanding.
        batch, candidates, previous, audit_raw, effect_refs = material
        # A saved model decision or a forged completion marker is not
        # enough. Every retention choice must have its typed effect
        # committed before this receipt under the very same author.
        for effect_ref in effect_refs:
            located_effect = self.ledger.lookup_event_commit(effect_ref)
            effect = (
                committed.get(effect_ref)
                if isinstance(committed, dict)
                else lookup_last(committed, "event_id", effect_ref)
                if committed is not None
                else self.ledger.resolve_committed_event_refs(
                    (effect_ref,),
                    at_world_revision=projection.world_revision,
                ).get(effect_ref)
            )
            if (effect is None or located_effect is None
                    or located_effect[1].ledger_sequence >= receipt_commit.ledger_sequence
                    or located_effect[0].world_id != projection.world_id
                    or effect.payload_hash != located_effect[0].payload_hash
                    or _event_model_result_raw(located_effect[0].payload_json) != audit_raw):
                raise ValueError("understanding batch has incomplete or differently authored effects")
        return batch, candidates, previous

    def read(self, projection):
        if self.store is None:
            return UnderstandingSnapshot((), (), frozenset(), ())
        from .sqlite_ledger import SQLiteWorldLedger
        from .life_content_store import SQLiteImmutableLifeContentStore
        from .context_read_scope import current_content_snapshot

        active_snapshot = current_content_snapshot(self.store)
        snapshot_generations = (
            getattr(active_snapshot, "generations", None)
            if active_snapshot is not None
            else None
        )
        can_cache = (
            type(self.ledger) is SQLiteWorldLedger
            and type(self.store) is SQLiteImmutableLifeContentStore
            and "lookup_event_commit" not in vars(self.ledger)
            and "read_exact" not in vars(self.store)
            and (active_snapshot is None or snapshot_generations is not None)
        )
        store_generations = (
            snapshot_generations
            if snapshot_generations is not None
            else (self.store.read_generations() if can_cache else None)
        )
        generation_before = (
            (self.ledger.verified_history_generation(), *store_generations)
            if can_cache and store_generations is not None
            else None
        )
        cache_key = (
            (
                projection.world_id,
                projection.world_revision,
                projection.deliberation_revision,
                projection.ledger_sequence,
                projection.semantic_hash,
                *generation_before,
            )
            if generation_before is not None
            else None
        )
        if cache_key is not None:
            with self._read_lock:
                cached = self._read_cache.get(cache_key)
                if cached is not None:
                    self._read_cache.move_to_end(cache_key)
                    return cached
        processes = {p.trigger_id: p for p in projection.trigger_processes
                     if p.process_kind == "memory_consolidation_review" and p.state == "terminal"
                     and (p.runtime_outcome_ref or "").startswith("memory-consolidation:reviewed:")}
        versions, retired, acknowledged, failures = [], set(), set(), []
        committed = projection.committed_world_event_refs
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
                batch_key = (
                    (
                        projection.world_id,
                        self.actor_ref,
                        process.trigger_id,
                        process.trigger_ref,
                        process.source_evidence_ref,
                        process.runtime_outcome_ref,
                        event.event_id,
                        event.payload_hash,
                        receipt_commit.ledger_sequence,
                        generation_before[0],
                        generation_before[2],
                    )
                    if generation_before is not None
                    else None
                )
                verified = None
                if batch_key is not None:
                    with self._read_lock:
                        cached_batch = self._batch_cache.get(batch_key)
                        if cached_batch is not None:
                            self._batch_cache.move_to_end(batch_key)
                            cached_verified, effect_hashes = cached_batch
                            if all(
                                (eff := lookup_last(committed, "event_id", ref)) is not None
                                and eff.payload_hash == expected_hash
                                for ref, expected_hash in effect_hashes
                            ):
                                verified = cached_verified
                if verified is None:
                    verified = self._validated_batch(projection, process, event, receipt_commit, committed=committed)
                    if (
                        batch_key is not None
                        and generation_before is not None
                        and (self.ledger.verified_history_generation(), *self.store.read_generations()) == generation_before
                    ):
                        if verified is None:
                            effect_hashes = ()
                        else:
                            batch_obj, cand_map, _ = verified
                            effect_hashes = tuple(
                                (
                                    ref,
                                    lookup_last(committed, "event_id", ref).payload_hash,
                                )
                                for choice in batch_obj.choices
                                for ref in (
                                    "event:memory-consolidation:effect:"
                                    + digest([process.trigger_id, cand_map[choice.candidate_token].candidate_id]),
                                )
                            )
                        with self._read_lock:
                            self._batch_cache[batch_key] = (verified, effect_hashes)
                            self._batch_cache.move_to_end(batch_key)
                            while len(self._batch_cache) > 128:
                                self._batch_cache.popitem(last=False)
                if verified is None:
                    continue
                batch, candidates, previous = verified
                known = {v.version_ref for v in versions if v.version_ref not in retired}
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
        compiler = (
            self._retrieval_compiler
            if can_cache and self._retrieval_compiler is not None
            else MemoryRetrievalCompiler(ledger=self.ledger, life_content_store=self.store)
        )
        readable = compiler.compile(
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
        snapshot = UnderstandingSnapshot(tuple(versions), active, frozenset(acknowledged), tuple(failures), pending, tuple(recallable))
        if (
            cache_key is not None
            and generation_before is not None
            and (self.ledger.verified_history_generation(), *self.store.read_generations()) == generation_before
        ):
            with self._read_lock:
                self._read_cache[cache_key] = snapshot
                self._read_cache.move_to_end(cache_key)
                while len(self._read_cache) > 16:
                    self._read_cache.popitem(last=False)
        return snapshot

    def context_candidates(self, projection, primary, snapshot, *, limit=8, exclude_ids=frozenset()):
        """Exact reviewed story links, standing sources, and recent personal life experiences."""
        wanted = {b.source_id for c in primary for b in c.values.source_bindings}
        related = set()
        for row in projection.prehistory_records:
            if row.record.record_id in wanted:
                related.update(row.record.related_record_refs)
        wanted.update(related)
        standing = {c.candidate_id for v in snapshot.active[-8:] for c in v.sources}
        excluded = {c.candidate_id for c in primary} | set(exclude_ids)
        direct = [c for c in projection.memory_candidates
                  if c.values.status == "active" and c.candidate_id not in excluded
                  and (c.candidate_id in standing or any(b.source_id in wanted for b in c.values.source_bindings))]
        direct.sort(key=lambda c: (
            not any(b.source_id in related for b in c.values.source_bindings),
            -c.values.retrieval_strength_bp, c.candidate_id,
        ))
        selected = list(direct[:limit])
        if len(selected) < limit and projection.logical_time is not None:
            selected_ids = excluded | {c.candidate_id for c in selected}
            cutoff = projection.logical_time - timedelta(days=7)
            recent_exp = [
                c for c in projection.memory_candidates
                if c.values.status == "active"
                and c.candidate_id not in selected_ids
                and c.opened_at >= cutoff
                and c.values.salience.autobiographical_relevance_bp >= 5500
                and any(
                    b.source_kind == "experience"
                    and b.source_id.startswith("experience:character-life-response:")
                    for b in c.values.source_bindings
                )
            ]
            recent_exp.sort(
                key=lambda c: (
                    c.opened_at.date(),
                    c.values.retrieval_strength_bp
                    + c.values.salience.autobiographical_relevance_bp
                    + c.values.salience.future_utility_bp
                    + c.values.salience.emotional_residue_bp
                    + c.values.salience.relationship_relevance_bp,
                    c.candidate_id,
                ),
                reverse=True,
            )
            seen_days = set()
            for c in recent_exp:
                day = c.opened_at.date()
                if day not in seen_days:
                    seen_days.add(day)
                    selected.append(c)
                    if len(selected) >= limit:
                        break
        return tuple(selected[:limit])


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
