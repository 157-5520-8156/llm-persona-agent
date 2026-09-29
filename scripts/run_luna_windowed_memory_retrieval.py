#!/usr/bin/env python3
"""One isolated import→role retention→BGE recall→bounded context materialization run."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/private-audits/luna-memory-integration-20260929"
SOURCE_DB = ROOT / "output/private-audits/grounding-repair-20260929/source.sqlite"
DB = OUT / "windowed-world.sqlite"
PACKAGE = OUT / "long-record-operator-reviewed.json"
WORLD = "world:companion-v2:qq-c2c:geoff"
ACTOR = "agent:companion"
RECORD_ID = "prehistory-record:luna-window-recall-20260929"
QUERY_TEXT = "后来确认图片说明已经补齐之后，她把清单和校样收在了哪里？"


def write_private(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    path.chmod(0o600)


def copy_source_if_needed() -> None:
    if DB.exists():
        raise SystemExit(f"refusing to overwrite existing isolated ledger: {DB}")
    if not PACKAGE.is_file():
        raise SystemExit("operator-reviewed fixture package is missing")
    with sqlite3.connect(f"file:{SOURCE_DB.resolve()}?mode=ro", uri=True) as source:
        with sqlite3.connect(DB) as target:
            source.backup(target)
    DB.chmod(0o600)


def new_event_summaries(database: Path, after_seq: int) -> list[dict[str, object]]:
    with sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT ledger_sequence,event_json FROM world_v2_events "
            "WHERE world_id=? AND ledger_sequence>? ORDER BY ledger_sequence",
            (WORLD, after_seq),
        ).fetchall()
    result = []
    for sequence, raw in rows:
        event = json.loads(raw)
        payload = json.loads(event["payload_json"])
        result.append({
            "ledger_sequence": sequence,
            "event_id": event["event_id"],
            "event_type": event["event_type"],
            "payload_hash": event["payload_hash"],
            "source": event.get("source"),
            "purpose": payload.get("purpose"),
            "decision_kind": payload.get("decision_kind"),
            "status": payload.get("status"),
            "record_id": (payload.get("record", {}).get("record_id")
                          if isinstance(payload.get("record"), dict) else None),
            "experience_id": payload.get("experience_id"),
            "source_refs": payload.get("source_refs"),
        })
    return result


def build_target_corpus(ledger):
    from companion_daemon.world_v2.memory_retrieval import MemoryRetrievalCompiler
    from companion_daemon.world_v2.recall_corpus import (
        RecallCorpusCompiler,
        RecallCorpusSources,
        required_recall_authority_refs,
        select_recall_authority_bindings,
    )
    from companion_daemon.world_v2.recall_index import RecallSourceBinding
    from companion_daemon.world_v2.schemas import ProjectionCursor

    projection = ledger.project()
    active = next((
        item for item in projection.memory_candidates
        if item.values.status == "active"
        and any(binding.source_kind == "prehistory" and binding.source_id == RECORD_ID
                for binding in item.values.source_bindings)
    ), None)
    if active is None:
        return projection, None, (), None
    cursor = ProjectionCursor(
        world_revision=projection.world_revision,
        deliberation_revision=projection.deliberation_revision,
        ledger_sequence=projection.ledger_sequence,
    )
    retrieval = MemoryRetrievalCompiler(ledger=ledger, max_excerpt_characters=480).compile(
        cursor=cursor,
        candidates=(active,),
        viewer_privacy_ceiling="private",
        projection=projection,
        actor_ref=ACTOR,
    )
    sources = RecallCorpusSources(active_memory_candidates=retrieval.items)
    required = required_recall_authority_refs(sources)
    available = tuple(
        RecallSourceBinding(
            source_kind="committed_event",
            authority_type=item.event_type,
            ref=item.event_id,
            source_world_revision=item.world_revision,
            immutable_hash=item.payload_hash,
        )
        for item in projection.committed_world_event_refs
        if item.event_id in required
    )
    closed = select_recall_authority_bindings(sources=sources, candidates=available)
    corpus = RecallCorpusCompiler().compile(
        cursor=cursor,
        actor_ref=ACTOR,
        subject_refs=(ACTOR,),
        sources=sources.model_copy(update={"authority_bindings": closed}),
    )
    return projection, retrieval, corpus, cursor


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True, mode=0o700)
    OUT.chmod(0o700)
    copy_source_if_needed()
    from dotenv import dotenv_values
    from companion_daemon.config import Settings
    import httpx
    import companion_daemon.llm as llm

    environment = dotenv_values(ROOT / ".env")
    debug_key = environment.get("DEEPSEEK_DEBUG_API_KEY")
    if not debug_key:
        raise SystemExit("DEEPSEEK_DEBUG_API_KEY is missing")
    os.environ["DEEPSEEK_API_KEY"] = debug_key
    os.environ["DEEPSEEK_MODEL"] = "deepseek-flash"
    os.environ["WORLD_V2_PREHISTORY_PACKAGE_PATH"] = str(PACKAGE)
    settings = Settings(_env_file=ROOT / ".env")
    if not settings.world_v2_recall_semantic_enabled or settings.world_v2_recall_embedding_model != "bge-m3":
        raise SystemExit("configured BGE-M3 recall is not enabled")

    from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
    from companion_daemon.world_v2.longitudinal_model_input_capture import (
        ModelInputCaptureTransport,
        PrivateModelInputCapture,
    )

    input_capture = PrivateModelInputCapture(OUT / "windowed-model-inputs.jsonl")
    original_model = llm.DeepSeekChatModel

    class CapturedDeepSeek(original_model):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = ModelInputCaptureTransport(
                inner=httpx.AsyncHTTPTransport(trust_env=False),
                capture=input_capture,
                model_role="character_interior",
            )
            super().__init__(*args, **kwargs)

    llm.DeepSeekChatModel = CapturedDeepSeek
    from drive_production_lanes import open_session
    from companion_daemon.world_v2.longitudinal_journey import JourneyClock

    seed_ledger = SQLiteWorldLedger(path=DB, world_id=WORLD)
    try:
        before = seed_ledger.project()
        before_cursor = {
            "world_revision": before.world_revision,
            "deliberation_revision": before.deliberation_revision,
            "ledger_sequence": before.ledger_sequence,
            "logical_time": before.logical_time.isoformat(),
        }
    finally:
        seed_ledger.close()

    started = time.monotonic()
    session = await open_session(
        database=DB,
        output_dir=OUT,
        enable_media=False,
        primary_user_id="geoff",
        journey_clock=JourneyClock(datetime.fromisoformat(before_cursor["logical_time"])),
        use_configured_recall_embedding=True,
    )
    try:
        imported_projection = session.host._host._application._ledger.project()
        imported_count = sum(item.record.record_id == RECORD_ID for item in imported_projection.prehistory_records)
        if imported_count != 1:
            raise RuntimeError(f"reviewed fixture did not import exactly once: {imported_count}")
        initialization = await session.host.initialize_prehistory_once(allow_model_call=True)

        ledger = session.host._host._application._ledger
        projection, retrieval, corpus, cursor = build_target_corpus(ledger)
        if retrieval is None:
            payload = {
                "status": "character_did_not_retain_fixture_memory",
                "initialization": initialization,
                "imported_record_count": imported_count,
                "active_target_memory": False,
                "after_cursor": {
                    "world_revision": projection.world_revision,
                    "deliberation_revision": projection.deliberation_revision,
                    "ledger_sequence": projection.ledger_sequence,
                },
                "events": new_event_summaries(DB, before_cursor["ledger_sequence"]),
                "input_capture_health": input_capture.health(),
                "real_provider_calls": sum(
                    1 for row in input_capture.read_since()[1] if row.get("kind") == "request"
                ),
                "seconds": round(time.monotonic() - started, 3),
            }
            write_private(OUT / "windowed-recall-result.json", payload)
            print(json.dumps({"status": payload["status"], "initialization": initialization,
                              "provider_calls": payload["real_provider_calls"]}, ensure_ascii=False))
            return

        excerpt, = retrieval.items[0].source_excerpts
        long_statement = excerpt._recall_source_record.statement
        cursor_value = cursor
        from companion_daemon.world_v2.recall_embedding import (
            SQLiteCachedRecallEmbedding,
            configured_recall_embedding,
        )
        from companion_daemon.world_v2.recall_index import (
            InMemoryRecallIndex,
            RecallQuery,
        )
        from companion_daemon.world_v2.recall_audit import CharacterRecallRequest, RecallAuditTrace
        from companion_daemon.world_v2.recall_runtime import (
            augment_model_content_with_recall,
            recall_evidence_json,
        )

        underlying = configured_recall_embedding(settings)
        if underlying is None:
            raise RuntimeError("configured BGE-M3 embedding adapter is unavailable")
        semantic = SQLiteCachedRecallEmbedding(path=str(DB), world_id=WORLD, delegate=underlying)
        index = InMemoryRecallIndex(embedding=semantic)
        index.rebuild(cursor=cursor_value, documents=corpus)
        query = RecallQuery(
            query_text=QUERY_TEXT,
            cursor=__import__("companion_daemon.world_v2.recall_index", fromlist=["RecallCursor"]).RecallCursor(
                world_revision=cursor_value.world_revision,
                deliberation_revision=cursor_value.deliberation_revision,
                ledger_sequence=cursor_value.ledger_sequence,
            ),
            actor_ref=ACTOR,
            subject_refs=(ACTOR,),
            viewer_privacy_ceiling="private",
            at=projection.logical_time,
            accessibility_seed="luna-long-window-bge-retrieval",
        )
        recall = index.search(query)
        trace = RecallAuditTrace(
            **recall.model_dump(mode="python"),
            trigger_ref=excerpt.authority_event_ref,
            request=CharacterRecallRequest(query_text=query.query_text, limit=query.limit),
            mode="character_pull",
        )
        evidence = json.loads(recall_evidence_json(trace))
        augmented = json.loads(augment_model_content_with_recall(json.dumps({"slices": {}}), trace))
        injected = augmented["slices"]["active_memory_candidates"]["items"]
        visible_body = json.dumps(injected, ensure_ascii=False)
        target_hits = [
            hit for hit in recall.hits
            if hit.document.prehistory is not None
            and hit.document.source_window_start == 832
            and "末尾可以确认的细节" in hit.document.text
        ]
        usage_day = datetime.now(timezone.utc).date().isoformat()
        with sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True) as conn:
            bge_usage = conn.execute(
                "SELECT consumed_tokens, estimated_cost_cny, request_count, succeeded_count, failed_count, "
                "last_embedding_version, last_status FROM world_recall_embedding_usage_daily "
                "WHERE world_id=? AND usage_day=?",
                (WORLD, usage_day),
            ).fetchone()

        window_docs = tuple(sorted(corpus, key=lambda item: item.source_window_start))
        before_replay = ledger.project().semantic_hash
        cold = SQLiteWorldLedger(path=DB, world_id=WORLD)
        try:
            cold_projection, cold_retrieval, cold_corpus, cold_cursor = build_target_corpus(cold)
            cold_docs = tuple(sorted(cold_corpus, key=lambda item: item.source_window_start))
            cold_replay_hash = cold.rebuild().semantic_hash
        finally:
            cold.close()
        payload = {
            "contract": "luna-windowed-memory-retrieval.1",
            "scope": "same-World complete clone; exact operator-reviewed one-record retrieval fixture; no production state",
            "review_boundary": "operator fixture consistency review; DeepSeek review rejection preserved; not a production biography quality approval",
            "model": "deepseek-flash (V4.1)",
            "recall_embedding": {
                "version": semantic.version,
                "status": recall.embedding_status,
                "failure_code": recall.embedding_failure_code,
                "bge_usage_for_utc_day": bge_usage,
            },
            "initialization": initialization,
            "record_id": RECORD_ID,
            "record_import_authority_refs": [excerpt.authority_event_ref, excerpt.prehistory.archive_event_ref],
            "record_statement_characters": len(long_statement),
            "role_visible_excerpt_characters": len(excerpt.text),
            "role_visible_excerpt_truncated": excerpt.truncated,
            "tail_marker_in_role_excerpt": "末尾可以确认的细节" in excerpt.text,
            "recall_corpus_windows": [
                {"source_item_ref": doc.source_item_ref, "source_window_start": doc.source_window_start,
                 "text_characters": len(doc.text), "text_sha256": hashlib.sha256(doc.text.encode()).hexdigest(),
                 "record_event_ref": next(b.ref for b in doc.source_bindings if b.authority_type == "CharacterPrehistoryRecordImported"),
                 "archive_event_ref": next(b.ref for b in doc.source_bindings if b.authority_type == "CharacterPrehistoryArchiveAccepted")}
                for doc in window_docs
            ],
            "semantic_search": {
                "query": QUERY_TEXT,
                "query_hash": recall.query_hash,
                "result_hash": recall.result_hash,
                "hit_count": len(recall.hits),
                "hits": [{"source_item_ref": hit.document.source_item_ref,
                          "source_window_start": hit.document.source_window_start,
                          "match_channels": hit.match_channels,
                          "dense_score_bp": hit.dense_score_bp,
                          "source_refs": hit.document.source_refs,
                          "text_sha256": hashlib.sha256(hit.document.text.encode()).hexdigest()}
                         for hit in recall.hits],
                "target_tail_window_hit": bool(target_hits),
            },
            "model_context_materialization": {
                "source_lane": "active_memory_candidates",
                "source_excerpt_refs": [item.get("value", {}).get("source_refs") for item in injected],
                "injected_text_characters": [len(item.get("value", {}).get("text", "")) for item in injected],
                "tail_marker_injected": "末尾可以确认的细节" in visible_body,
                "window_text_injected": bool(injected),
                "max_injected_item_characters": max((len(item.get("value", {}).get("text", "")) for item in injected), default=0),
                "evidence_source_closures": [item.get("source_bindings") for item in injected],
            },
            "cold_replay": {
                "pre_close_projection_hash": before_replay,
                "replay_projection_hash": cold_replay_hash,
                "hash_match": before_replay == cold_replay_hash,
                "window_ids_preserved": [doc.document_id for doc in cold_docs] == [doc.document_id for doc in window_docs],
                "cold_window_count": len(cold_docs),
                "cold_record_statement_characters": len(cold_retrieval.items[0].source_excerpts[0]._recall_source_record.statement),
            },
            "model_input_capture_health": input_capture.health(),
            "model_input_capture_records": list(input_capture.read_since()[1]),
            "new_ledger_events": new_event_summaries(DB, before_cursor["ledger_sequence"]),
            "deepseek_review_rejection": {
                "response_sha256": hashlib.sha256((OUT / "long-record-review-response.json").read_bytes()).hexdigest(),
                "review_artifact_path": "private-only; raw response remains stored in the audit directory",
            },
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        write_private(OUT / "windowed-recall-result.json", payload)
        print(json.dumps({
            "status": "retained" if initialization.get("status") == "retained" else initialization.get("status"),
            "embedding_version": semantic.version,
            "embedding_status": recall.embedding_status,
            "target_tail_window_hit": bool(target_hits),
            "tail_marker_injected": payload["model_context_materialization"]["tail_marker_injected"],
            "cold_replay_hash_match": payload["cold_replay"]["hash_match"],
            "bge_usage_for_utc_day": bge_usage,
            "events_added": len(payload["new_ledger_events"]),
        }, ensure_ascii=False, indent=2), flush=True)
        semantic.close()
    finally:
        await session.close()


if __name__ == "__main__":
    os.chdir(ROOT)
    asyncio.run(main())
