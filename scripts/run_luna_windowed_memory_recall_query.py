#!/usr/bin/env python3
"""Search retained long prehistory with the configured BGE-M3 cache, then materialize a bounded Recall input."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/private-audits/luna-memory-integration-20260929"
DB = OUT / "windowed-world.sqlite"
WORLD = "world:companion-v2:qq-c2c:geoff"
ACTOR = "agent:companion"
RECORD_ID = "prehistory-record:luna-window-recall-20260929"
QUERY_TEXT = "后来确认图片说明已经补齐之后，她把清单和校样收在了哪里？"


def write_private(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    path.chmod(0o600)


def compile_target(ledger):
    from companion_daemon.world_v2.memory_retrieval import MemoryRetrievalCompiler
    from companion_daemon.world_v2.character_prehistory import digest as prehistory_record_digest
    from companion_daemon.world_v2.recall_corpus import (
        RecallCorpusCompiler,
        RecallCorpusSources,
        required_recall_authority_refs,
        select_recall_authority_bindings,
    )
    from companion_daemon.world_v2.recall_index import RecallCursor, RecallSourceBinding
    from companion_daemon.world_v2.schemas import ProjectionCursor

    projection = ledger.project()
    active = next((
        item for item in projection.memory_candidates
        if item.values.status == "active"
        and any(binding.source_kind == "prehistory" and binding.source_id == RECORD_ID
                for binding in item.values.source_bindings)
    ), None)
    if active is None:
        return projection, None, (), None, None
    recall_cursor = RecallCursor(
        world_revision=projection.world_revision,
        deliberation_revision=projection.deliberation_revision,
        ledger_sequence=projection.ledger_sequence,
    )
    item_result = MemoryRetrievalCompiler(ledger=ledger, max_excerpt_characters=480).compile(
        cursor=ProjectionCursor(
            world_revision=projection.world_revision,
            deliberation_revision=projection.deliberation_revision,
            ledger_sequence=projection.ledger_sequence,
        ),
        candidates=(active,),
        viewer_privacy_ceiling="private",
        projection=projection,
        actor_ref=ACTOR,
    )
    sources = RecallCorpusSources(active_memory_candidates=item_result.items)
    required = required_recall_authority_refs(sources)
    available = tuple(
        RecallSourceBinding(
            source_kind="committed_event", authority_type=item.event_type, ref=item.event_id,
            source_world_revision=item.world_revision, immutable_hash=item.payload_hash,
        )
        for item in projection.committed_world_event_refs if item.event_id in required
    )
    closed = select_recall_authority_bindings(sources=sources, candidates=available)
    corpus = RecallCorpusCompiler().compile(
        cursor=recall_cursor, actor_ref=ACTOR, subject_refs=(ACTOR,),
        sources=sources.model_copy(update={"authority_bindings": closed}),
    )
    return projection, item_result, corpus, recall_cursor, active


def bge_usage(database: Path) -> tuple[object, ...] | None:
    day = datetime.now(timezone.utc).date().isoformat()
    with sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True) as conn:
        return conn.execute(
            "SELECT consumed_tokens, estimated_cost_cny, request_count, succeeded_count, "
            "failed_count, last_embedding_version, last_status, last_requested_at "
            "FROM world_recall_embedding_usage_daily WHERE world_id=? AND usage_day=?",
            (WORLD, day),
        ).fetchone()


def main() -> None:
    OUT.chmod(0o700)
    from dotenv import dotenv_values
    from companion_daemon.config import Settings
    from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
    from companion_daemon.world_v2.character_prehistory import digest as prehistory_record_digest
    from companion_daemon.world_v2.recall_embedding import (
        SQLiteCachedRecallEmbedding,
        configured_recall_embedding,
    )
    from companion_daemon.world_v2.recall_index import InMemoryRecallIndex, RecallQuery
    from companion_daemon.world_v2.recall_audit import CharacterRecallRequest, RecallAuditTrace
    from companion_daemon.world_v2.recall_runtime import augment_model_content_with_recall, recall_evidence_json

    settings = Settings(_env_file=ROOT / ".env")
    if not settings.world_v2_recall_semantic_enabled or settings.world_v2_recall_embedding_model != "bge-m3":
        raise SystemExit("configured BGE-M3 is not enabled")
    embedding = configured_recall_embedding(settings)
    if embedding is None:
        raise SystemExit("configured BGE-M3 adapter is unavailable")
    semantic = SQLiteCachedRecallEmbedding(path=str(DB), world_id=WORLD, delegate=embedding)
    ledger = SQLiteWorldLedger(path=DB, world_id=WORLD)
    try:
        before = ledger.project()
        projection, retrieval, corpus, cursor, candidate = compile_target(ledger)
        if retrieval is None or candidate is None:
            raise SystemExit("target prehistory memory is not active; no recall query was issued")
        excerpt, = retrieval.items[0].source_excerpts
        full_record = excerpt._recall_source_record
        if (
            full_record is None
            or full_record.record_id != RECORD_ID
            or prehistory_record_digest(full_record) != excerpt.source_values_hash
        ):
            raise RuntimeError("full record is not source-bound in the private retrieval seam")

        index = InMemoryRecallIndex(embedding=semantic)
        index.rebuild(cursor=cursor, documents=corpus)
        query = RecallQuery(
            query_text=QUERY_TEXT,
            cursor=cursor,
            actor_ref=ACTOR,
            subject_refs=(ACTOR,),
            viewer_privacy_ceiling="private",
            at=projection.logical_time,
            accessibility_seed="luna-long-window-bge-retrieval",
        )
        result = index.search(query)
        trace = RecallAuditTrace(
            **result.model_dump(mode="python"),
            trigger_ref=candidate.values.source_bindings[0].authority_event_ref,
            request=CharacterRecallRequest(query_text=query.query_text, limit=query.limit),
            mode="character_pull",
        )
        evidence = json.loads(recall_evidence_json(trace))
        augmented = json.loads(augment_model_content_with_recall(json.dumps({"slices": {}}), trace))
        injected = augmented.get("slices", {}).get("active_memory_candidates", {}).get("items", [])
        visible_json = json.dumps(injected, ensure_ascii=False)
        target_hit = next((hit for hit in result.hits if (
            hit.document.source_window_start == 832
            and "末尾可以确认的细节" in hit.document.text
        )), None)
        after = ledger.project()
        replay = ledger.rebuild().semantic_hash
        payload = {
            "contract": "luna-windowed-memory-bge-recall.1",
            "scope": "real source-bound prehistory and retained Memory in an isolated complete World clone; no ChatGPT-visible or QQ delivery",
            "model": "deepseek-flash for the one retention decision; BGE-M3 for local recall embedding",
            "record_id": RECORD_ID,
            "memory_candidate_id": candidate.candidate_id,
            "memory_candidate_source_binding": [binding.model_dump(mode="json")
                for binding in candidate.values.source_bindings if binding.source_kind == "prehistory"],
            "memory_status": candidate.values.status,
            "full_record_characters": len(full_record.statement),
            "role_visible_prefix_characters": len(excerpt.text),
            "role_visible_prefix_truncated": excerpt.truncated,
            "tail_marker_in_prefix": "末尾可以确认的细节" in excerpt.text,
            "corpus_window_count": len(corpus),
            "corpus_windows": [{
                "source_item_ref": doc.source_item_ref,
                "start": doc.source_window_start,
                "characters": len(doc.text),
                "source_refs": doc.source_refs,
                "source_bindings": [binding.model_dump(mode="json") for binding in doc.source_bindings],
                "text_sha256": hashlib.sha256(doc.text.encode()).hexdigest(),
            } for doc in sorted(corpus, key=lambda doc: doc.source_window_start)],
            "bge_query": QUERY_TEXT,
            "embedding": {
                "version": result.embedding_version,
                "status": result.embedding_status,
                "failure_code": result.embedding_failure_code,
                "usage_daily": bge_usage(DB),
                "query_hash": result.query_hash,
                "result_hash": result.result_hash,
                "hit_count": len(result.hits),
                "hits": [{
                    "source_item_ref": hit.document.source_item_ref,
                    "window_start": hit.document.source_window_start,
                    "score_bp": hit.score_bp,
                    "dense_score_bp": hit.dense_score_bp,
                    "channels": hit.match_channels,
                    "source_refs": hit.document.source_refs,
                    "text_sha256": hashlib.sha256(hit.document.text.encode()).hexdigest(),
                } for hit in result.hits],
                "target_tail_window_hit": target_hit is not None,
            },
            "recall_evidence": {
                "source_ref_closure_count": len(evidence.get("candidates", [{}])[0].get("source_refs", [])) if evidence.get("candidates") else 0,
                "target_tail_marker_in_visible_evidence": "末尾可以确认的细节" in json.dumps(evidence, ensure_ascii=False),
            },
            "model_snapshot_materialization": {
                "lane": "active_memory_candidates",
                "item_count": len(injected),
                "item_window_starts": [item.get("item_ref") for item in injected],
                "target_tail_marker_injected": "末尾可以确认的细节" in visible_json,
                "injected_text_characters": [len(item.get("value", {}).get("text", "")) for item in injected],
                "source_refs": [item.get("value", {}).get("source_refs", []) for item in injected],
                "source_bindings": [item.get("source_bindings", []) for item in injected],
                "full_record_injected": full_record.statement in visible_json,
            },
            "projection_replay": {
                "pre_rebuild_hash": before.semantic_hash,
                "post_rebuild_hash": replay,
                "semantic_hash_preserved": before.semantic_hash == replay,
                "ledger_sequence_before": before.ledger_sequence,
                "ledger_sequence_after": after.ledger_sequence,
            },
            "limitations": [
                "This proves BGE retrieval and bounded model-context materialization from a retained, source-closed Memory; it does not prove a model attended to or causally adopted the hit in a new choice.",
                "The one-record package was approved by an independent operator for the isolated retrieval-fixture purpose after DeepSeek's separate creation review rejection; it is not production biography qualification.",
            ],
        }
        write_private(OUT / "windowed-recall-result.json", payload)
        print(json.dumps({
            "status": "complete",
            "memory_status": candidate.values.status,
            "embedding_version": result.embedding_version,
            "embedding_status": result.embedding_status,
            "hit_count": len(result.hits),
            "target_tail_window_hit": target_hit is not None,
            "tail_marker_injected": payload["model_snapshot_materialization"]["target_tail_marker_injected"],
            "cold_replay_hash_preserved": payload["projection_replay"]["semantic_hash_preserved"],
            "bge_usage_daily": payload["embedding"]["usage_daily"],
        }, ensure_ascii=False, indent=2))
    finally:
        semantic.close()
        ledger.close()


if __name__ == "__main__":
    os.chdir(ROOT)
    main()
