#!/usr/bin/env python3
"""Prepare an existing World's unified index, without a role model or delivery.

Use --clone for qualification. The only provider is the configured embedding
adapter; its existing durable token/currency budget is unchanged.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
import time

from companion_daemon.config import Settings
from companion_daemon.world_v2.ledger_context_resolver import ContextRelevanceScope
from companion_daemon.world_v2.ledger_context_resolver import context_capsule_compiler_from_ledger
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.expression_payload_store import SQLiteImmutableExpressionPayloadStore
from companion_daemon.world_v2.memory_index_maintenance import MemoryIndexMaintenance
from companion_daemon.world_v2.recall_embedding import (
    configured_recall_embedding,
    SQLiteCachedRecallEmbedding,
)
from companion_daemon.world_v2.recall_index import SQLiteRecallIndex, FeatureHashRecallEmbedding
from companion_daemon.world_v2.recall_runtime import RecallCoordinator, CharacterRecallRequest
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.situation_compiler import SituationCompiler
from companion_daemon.world_v2.local_chronology import LocalChronology


def run(args):
    database = args.database.resolve()
    if args.clone:
        if database.exists():
            raise ValueError("clone target already exists")
        database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(args.clone.resolve().as_uri() + "?mode=ro", uri=True) as source:
            with sqlite3.connect(database) as target:
                source.backup(target)
    if not database.exists():
        raise ValueError("prepare requires an existing World")
    settings = Settings(
        database_path=database, **({"_env_file": tuple(args.env_file)} if args.env_file else {})
    )
    embedding = configured_recall_embedding(settings)
    if embedding is None:
        raise ValueError("semantic embedding is not configured")
    ledger = SQLiteWorldLedger(path=database, world_id=args.world)
    life = SQLiteImmutableLifeContentStore(path=str(database), world_id=args.world)
    expressions = SQLiteImmutableExpressionPayloadStore(path=str(database), world_id=args.world)
    index = SQLiteRecallIndex(
        path=database,
        world_id=args.world,
        embedding=FeatureHashRecallEmbedding(),
        policy_version=(
            "world-v2-recall-index.hybrid.17"
            if settings.world_v2_recall_story_context
            else "world-v2-recall-index.hybrid.14"
            if settings.world_v2_recall_parent_context
            else None
        ),
    )
    cached = SQLiteCachedRecallEmbedding(path=database, world_id=args.world, delegate=embedding)
    coordinator = RecallCoordinator(index=index, semantic_embedding=cached)
    compiler = context_capsule_compiler_from_ledger(
        ledger=ledger,
        situation_compiler=SituationCompiler(
            local_chronology=LocalChronology(settings.local_timezone)
        ),
        relevance_scope=ContextRelevanceScope(
            actor_ref=args.actor, related_subject_refs=tuple(args.subject)
        ),
        life_content_store=life,
        expression_payload_store=expressions,
        recall_coordinator=coordinator,
    )
    maintenance = MemoryIndexMaintenance(
        ledger=ledger, prepare=lambda: compiler.prepare_memory_index(actor_ref=args.actor)
    )
    try:
        before = ledger.project()
        usage_before = cached.health_snapshot()
        report = maintenance.advance_once()
        queries = []
        if report["status"] == "ready":
            for query in args.query:
                started = time.perf_counter()
                trace = coordinator.recall(
                    request=CharacterRecallRequest(query_text=query),
                    expected_cursor=index.snapshot().cursor,
                    trigger_ref="recall-index:maintenance",
                    accessibility_seed="memory-index-qualification",
                )
                queries.append(
                    {
                        "query": query,
                        "seconds": time.perf_counter() - started,
                        "embedding_status": trace.audit.embedding_status,
                        "hits": [
                            {"source": h.document.source_item_ref, "text": h.document.text}
                            for h in trace.audit.hits
                        ],
                        "result_hash": trace.audit.result_hash,
                    }
                )
        docs = index.snapshot().documents if coordinator.cursor is not None else ()
        after = ledger.project()
        assert (before.ledger_sequence, before.semantic_hash) == (
            after.ledger_sequence,
            after.semantic_hash,
        )
        return {
            "contract": "memory-index-preparation.1",
            "preparation": report,
            "repeat": maintenance.advance_once(),
            "queries": queries,
            "corpus_by_source_slice": dict(Counter(d.source_slice for d in docs)),
            "dense20_records": len(
                {
                    d.source_item_ref.split(":window:")[0]
                    for d in docs
                    if "prehistory-record:celia-dense20-" in d.source_item_ref
                }
            ),
            "usage_before": usage_before,
            "usage_after": cached.health_snapshot(),
            "ledger_unchanged": True,
            "ledger_sequence": after.ledger_sequence,
            "semantic_hash": after.semantic_hash,
            "role_model_calls": 0,
            "deliveries": 0,
            "scope": "index_preparation_and_source_retrieval_not_natural_dialogue",
        }
    finally:
        coordinator.close()
        index.close()
        expressions.close()
        life.close()
        ledger.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--clone", type=Path)
    parser.add_argument("--world", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--subject", action="append", default=[])
    parser.add_argument("--env-file", type=Path, action="append")
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": report["preparation"]["status"], "output": str(args.output)}))
    if report["preparation"]["status"] != "ready":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
