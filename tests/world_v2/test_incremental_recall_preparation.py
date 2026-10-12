"""Preparation reuse must preserve public corpus/search results and fresh state."""

from datetime import timedelta
import json
import sqlite3

from companion_daemon.world_v2 import recall_index as module
from companion_daemon.world_v2.recall_corpus import RecallCorpusCompiler
from companion_daemon.world_v2.recall_index import (
    FeatureHashRecallEmbedding,
    InMemoryRecallIndex,
    IncrementalSemanticRecallIndex,
    SQLiteRecallIndex,
)
from test_recall_corpus import _sources, CURSOR as CORPUS_CURSOR
from test_recall_index import _documents, _query, CURSOR, _SemanticFixtureEmbedding


def compile_sources(compiler, sources, cursor=CORPUS_CURSOR):
    return compiler.compile(
        cursor=cursor,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        sources=sources,
    )


def test_corpus_materials_survive_new_cursor_but_rebuild_changed_input():
    compiler = RecallCorpusCompiler()
    first = compile_sources(compiler, _sources())
    cursor = CORPUS_CURSOR.model_copy(update={"ledger_sequence": CORPUS_CURSOR.ledger_sequence + 1})
    sources = _sources()  # Equal current source values, not the same model instances.
    second = compile_sources(compiler, sources, cursor)
    assert second == compile_sources(RecallCorpusCompiler(), sources, cursor)
    assert all(a is b for a, b in zip(first, second, strict=True))
    revised = sources.recent_dialogue[0].model_copy(update={"text": "今天改用玻璃杯。"})
    sources = sources.model_copy(update={"recent_dialogue": (revised,)})
    third = compile_sources(compiler, sources, cursor)
    assert third == compile_sources(RecallCorpusCompiler(), sources, cursor)
    assert third != second


def test_local_and_semantic_search_share_basis_but_keep_their_original_results(monkeypatch):
    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    index.rebuild(cursor=CURSOR, documents=_documents())
    local = index.snapshot()
    query = _query()
    semantic = IncrementalSemanticRecallIndex(_SemanticFixtureEmbedding())
    prepared, _ = semantic.prepare(local)
    # Freeze expected complete results before counting actual preparation work.
    expected_local = index.snapshot().search(query)
    separate, _ = IncrementalSemanticRecallIndex(_SemanticFixtureEmbedding()).prepare(
        index.snapshot()
    )
    expected_dense = separate.search(query)
    original = module._corpus_lexical_scores
    calls = []

    def count(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "_corpus_lexical_scores", count)
    assert local.search(query) == expected_local
    assert prepared.search(query) == expected_dense
    assert len(calls) == 1
    changed = query.model_copy(update={"at": query.at + timedelta(days=1)})
    prepared.search(changed)
    assert len(calls) == 2
    restricted = query.model_copy(update={"viewer_privacy_ceiling": "public"})
    assert prepared.search(restricted) == separate.search(restricted)


def test_index_cursor_refresh_skips_full_document_reads_and_rechecks_external_changes(tmp_path):
    path = tmp_path / "recall.sqlite"
    index = SQLiteRecallIndex(
        path=path, world_id="world:test", embedding=FeatureHashRecallEmbedding()
    )
    external = sqlite3.connect(path)
    external.execute("CREATE TABLE unrelated_world_events (value TEXT)")
    external.commit()
    try:
        docs = _documents()
        index.rebuild(cursor=CURSOR, documents=docs)
        old = index.snapshot()
        external.execute("INSERT INTO unrelated_world_events VALUES ('new observation')")
        external.commit()
        statements = []
        index._connection.set_trace_callback(statements.append)
        new_cursor = CURSOR.model_copy(update={"ledger_sequence": CURSOR.ledger_sequence + 1})
        index.rebuild(cursor=new_cursor, documents=docs)
        current = index.snapshot()
        scans = [
            s
            for s in statements
            if s.lstrip().upper().startswith("SELECT") and "FROM world_v2_recall_documents" in s
        ]
        assert scans == []
        assert current.cursor == new_cursor and old.cursor == CURSOR
        assert current.documents == old.documents
        # A second connection changing only a row, not the head, must invalidate reuse.
        changed = docs[0].model_copy(update={"privacy_class": "withhold"})
        external.execute(
            "UPDATE world_v2_recall_documents SET document_json=? WHERE world_id=? AND document_id=?",
            (json.dumps(changed.model_dump(mode="json")), "world:test", changed.document_id),
        )
        external.commit()
        fresh = index.snapshot()
        assert (
            next(d for d in fresh.documents if d.document_id == changed.document_id).privacy_class
            == "withhold"
        )
        assert old.documents == tuple(sorted(docs, key=lambda d: d.document_id))
    finally:
        external.close()
        index.close()


def test_warm_material_checks_authority_and_timezone_representation():
    import pytest
    from datetime import timezone

    compiler = RecallCorpusCompiler()
    sources = _sources()
    fact = sources.relevant_facts[0]
    from companion_daemon.world_v2.recall_index import RecallSourceBinding

    binding = RecallSourceBinding(
        source_kind="committed_event",
        authority_type="FactCommitted",
        ref=fact.accepted_fact_event_ref,
        source_world_revision=fact.accepted_fact_world_revision,
        immutable_hash=fact.accepted_fact_payload_hash,
    )
    sources = sources.model_copy(
        update={"authority_bindings": (*sources.authority_bindings, binding)}
    )
    before = compile_sources(compiler, sources)
    dialogue = sources.recent_dialogue[0]
    shifted = dialogue.model_copy(
        update={"occurred_at": dialogue.occurred_at.astimezone(timezone(timedelta(hours=8)))}
    )
    changed = sources.model_copy(update={"recent_dialogue": (shifted,)})
    after = compile_sources(compiler, changed)
    assert [d.model_dump_json() for d in after] == [
        d.model_dump_json() for d in compile_sources(RecallCorpusCompiler(), changed)
    ]
    assert [d.model_dump_json() for d in before] != [d.model_dump_json() for d in after]
    fact = sources.relevant_facts[0]
    broken = sources.model_copy(
        update={
            "relevant_facts": (fact.model_copy(update={"accepted_fact_payload_hash": "a" * 64}),)
        }
    )
    with pytest.raises(ValueError, match="disagrees"):
        compile_sources(compiler, broken)


def test_index_serializes_only_changed_material_and_retains_removals(tmp_path, monkeypatch):
    index = SQLiteRecallIndex(
        path=tmp_path / "changes.sqlite",
        world_id="world:test",
        embedding=FeatureHashRecallEmbedding(),
    )
    try:
        docs = _documents()
        index.rebuild(cursor=CURSOR, documents=docs)
        before = index.snapshot()
        original = module._canonical_json
        serialized = []

        def record(value):
            if isinstance(value, dict) and "document_id" in value:
                serialized.append(value["document_id"])
            return original(value)

        monkeypatch.setattr(module, "_canonical_json", record)
        revised = docs[0].model_copy(update={"privacy_class": "withhold"})
        current = (revised, *docs[1:-1])
        next_cursor = CURSOR.model_copy(update={"ledger_sequence": 82})
        index.rebuild(cursor=next_cursor, documents=current)
        assert serialized == [revised.document_id]
        assert index.snapshot().documents == tuple(sorted(current, key=lambda d: d.document_id))
        assert before.documents == tuple(sorted(docs, key=lambda d: d.document_id))
    finally:
        index.close()


def test_index_rechecks_generation_after_work_outside_write_transaction(tmp_path, monkeypatch):
    path = tmp_path / "race.sqlite"
    index = SQLiteRecallIndex(
        path=path, world_id="world:test", embedding=FeatureHashRecallEmbedding()
    )
    external = sqlite3.connect(path)
    try:
        docs = _documents()
        index.rebuild(cursor=CURSOR, documents=docs)
        original = index._materialize
        changed = False

        def concurrent_change(**kwargs):
            nonlocal changed
            result = original(**kwargs)
            if not changed:
                changed = True
                foreign = docs[0].model_copy(update={"text": "A concurrent derived-index change."})
                external.execute(
                    "UPDATE world_v2_recall_documents SET document_json=? WHERE document_id=?",
                    (json.dumps(foreign.model_dump(mode="json")), foreign.document_id),
                )
                external.commit()
            return result

        monkeypatch.setattr(index, "_materialize", concurrent_change)
        index.rebuild(cursor=CURSOR.model_copy(update={"ledger_sequence": 82}), documents=docs)
        assert changed
        assert index.snapshot().documents == tuple(sorted(docs, key=lambda d: d.document_id))
        raw = external.execute(
            "SELECT document_json FROM world_v2_recall_documents WHERE document_id=?",
            (docs[0].document_id,),
        ).fetchone()[0]
        assert json.loads(raw) == docs[0].model_dump(mode="json")
    finally:
        external.close()
        index.close()


def test_shared_basis_is_built_once_for_concurrent_readers(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import time

    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    index.rebuild(cursor=CURSOR, documents=_documents())
    snapshot = index.snapshot()
    original = module._corpus_lexical_scores
    calls = []

    def slow(*args, **kwargs):
        calls.append(1)
        time.sleep(0.02)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "_corpus_lexical_scores", slow)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: snapshot.search(_query()), range(4)))
    assert all(r == results[0] for r in results)
    assert len(calls) == 1


def test_same_cursor_with_new_privacy_cannot_use_previous_shared_basis():
    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    doc = _documents()[0]
    index.rebuild(cursor=CURSOR, documents=(doc,))
    old = index.snapshot()
    original = old.search(_query(viewer_privacy_ceiling="private"))
    index.rebuild(cursor=CURSOR, documents=(doc.model_copy(update={"privacy_class": "withhold"}),))
    new = index.snapshot()
    assert new.search(_query(viewer_privacy_ceiling="private")).hits == ()
    assert old.search(_query(viewer_privacy_ceiling="private")) == original


def test_json_restored_timezone_and_frozen_fact_models_are_prepared_once(monkeypatch):
    from companion_daemon.world_v2.recall_index import RecallDocument
    from companion_daemon.world_v2.recall_corpus import RecallCorpusSources
    from companion_daemon.world_v2.recall_materialization import immutable_material

    sources = RecallCorpusSources.model_validate_json(_sources().model_dump_json())
    compiler = RecallCorpusCompiler()
    before = compile_sources(compiler, sources)
    after = compile_sources(
        compiler, RecallCorpusSources.model_validate_json(sources.model_dump_json())
    )
    assert all(a is b for a, b in zip(before, after, strict=True))
    docs = tuple(RecallDocument.model_validate_json(d.model_dump_json()) for d in _documents())
    assert all(immutable_material(d) for d in docs)
    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    index.rebuild(cursor=CURSOR, documents=docs)
    snapshot = index.snapshot()
    query = module.RecallQuery.model_validate_json(_query().model_dump_json())
    calls = []
    original = module._corpus_lexical_scores

    def count(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "_corpus_lexical_scores", count)
    assert snapshot.search(query) == snapshot.search(query)
    assert len(calls) == 1


def test_same_pinned_context_reads_equal_memory_candidate_set_once(tmp_path, monkeypatch):
    from companion_daemon.world_v2.character_prehistory_runtime import PrehistoryArchiveRuntime
    from companion_daemon.world_v2.life_content_store import InMemoryImmutableLifeContentStore
    from companion_daemon.world_v2.ledger_context_resolver import (
        context_capsule_compiler_from_ledger,
        ContextRelevanceScope,
    )
    from companion_daemon.world_v2.context_resolver import query_from_projection
    from companion_daemon.world_v2.memory_retrieval import MemoryRetrievalCompiler
    from companion_daemon.world_v2.recall_runtime import RecallCoordinator
    from test_character_prehistory import ACTOR, START, reviewed_archive, started_ledger

    ledger = started_ledger(tmp_path / "context.sqlite")
    recall = RecallCoordinator(index=InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding()))
    try:
        PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).install_reviewed(
            reviewed_archive(), created_at=START
        )
        compiler = context_capsule_compiler_from_ledger(
            ledger=ledger,
            relevance_scope=ContextRelevanceScope(actor_ref=ACTOR, related_subject_refs=()),
            life_content_store=InMemoryImmutableLifeContentStore(),
            recall_coordinator=recall,
        )
        original = MemoryRetrievalCompiler.compile
        calls = []

        def count(self, **kwargs):
            calls.append(tuple(c.candidate_id for c in kwargs["candidates"]))
            return original(self, **kwargs)

        monkeypatch.setattr(MemoryRetrievalCompiler, "compile", count)
        state = ledger.project()
        compiler.compile(query_from_projection(state, actor_ref=ACTOR, trigger_ref="test:context"))
        assert calls and calls[0]
        assert len(calls) == 1
        assert recall._index.snapshot().documents
    finally:
        recall.close()
        ledger.close()
