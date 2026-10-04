from __future__ import annotations

from datetime import timedelta
import pytest

from companion_daemon.world_v2.character_prehistory import (
    ReviewedPrehistoryArchive,
    digest,
)
from companion_daemon.world_v2.character_prehistory_runtime import PrehistoryArchiveRuntime
from companion_daemon.world_v2.prehistory_memory_source import prehistory_memory_binding
from companion_daemon.world_v2.recall_corpus import (
    RecallCorpusCompiler,
    RecallCorpusSources,
    required_recall_authority_refs,
    select_recall_authority_bindings,
)
from companion_daemon.world_v2.recall_index import (
    FeatureHashRecallEmbedding,
    InMemoryRecallIndex,
    RecallCursor,
    RecallQuery,
    RecallSourceBinding,
)
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.memory_retrieval import MemoryRetrievalResult

from test_prehistory_memory_source import (
    ACTOR,
    START,
    _choice,
    _read,
    _corpus,
    started_ledger,
    reviewed_archive,
)


def _long_reviewed_archive():
    existing = reviewed_archive()
    original = existing.document.records[0]
    pad = "这段记忆只记下普通的排版工作与当时的观察，没有补写同学的动机或未发生的结果。"
    statement = (
        "十五岁时，她参加校刊的一次排版值班。几个人把稿件分成短文、通知和图片说明，"
        "她负责对照纸质清单检查页码。开始时她觉得自己只是在重复核对，没有什么特别。"
        + pad * 10
        + "中段识别细节：蓝色封面的借书目录被她压在清单下面，后来她把它移到桌角。"
        + pad * 10
        + "最后大家发现一篇短文少了一页，按清单重新核对后补回了那页；收工时她把清单夹进本子。"
        "末尾识别细节：回家后她在本子空白页写下‘借书目录已还’，第二天把目录交还图书角。"
    )
    record = original.model_copy(update={
        "record_id": "prehistory-record:windowed-recall-test",
        "occurred_from": START - timedelta(days=720),
        "occurred_until": START - timedelta(days=720),
        "time_precision": "day",
        "statement": statement,
    })
    document = existing.document.model_copy(update={
        "archive_id": "prehistory-archive:windowed-recall-test",
        "source_artifact_ref": "fixture:synthetic-window-test",
        "records": (record,),
    })
    review = existing.review.model_copy(update={
        "manifest_hash": digest(document.manifest()),
        "review_artifact_ref": "fixture:offline-unit-test-review",
        "review_artifact_hash": "e" * 64,
    })
    return ReviewedPrehistoryArchive(document=document, review=review), statement


@pytest.mark.parametrize("boundary", ["available", "private", "conflicting", "budget"])
def test_parent_source_context_keeps_direct_rank_and_respects_boundaries(tmp_path, monkeypatch, boundary):
    class LexicalOnly:
        version = "fixture-lexical-only.1"
        dimensions = 3

        def __init__(self):
            self.calls = []

        def embed(self, texts):
            self.calls.append(texts)
            return tuple((0.0, 0.0, 0.0) for _ in texts)

    ledger = started_ledger(tmp_path / "adjacent.sqlite")
    archive, statement = _long_reviewed_archive()
    statement = statement[:650]
    record = archive.document.records[0].model_copy(update={"statement": statement})
    document = archive.document.model_copy(update={"records": (record,)})
    archive = ReviewedPrehistoryArchive(document=document, review=archive.review.model_copy(
        update={"manifest_hash": digest(document.manifest())}))
    try:
        row, = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(archive, created_at=START)
        source = prehistory_memory_binding(row)
        pending = _choice(ledger, source)
        _choice(ledger, source, before=pending, status="active")
        corpus = _corpus(ledger, _read(ledger), parent_context=True)
        if boundary == "private":
            corpus = tuple(d.model_copy(update={"privacy_class": "withhold"})
                           if d.context_only else d for d in corpus)
        if boundary == "conflicting":
            corpus = tuple(d.model_copy(update={"text": "不" * 64 + d.text[64:]})
                           if d.context_only else d for d in corpus)
        p = ledger.project()
        cursor = RecallCursor(world_revision=p.world_revision, deliberation_revision=p.deliberation_revision,
                              ledger_sequence=p.ledger_sequence)
        query = RecallQuery(query_text="十五岁", cursor=cursor, actor_ref=ACTOR,
            subject_refs=(ACTOR,), viewer_privacy_ceiling="private", at=START,
            accessibility_seed="fixture:adjacent", limit=1)
        old_embedding, new_embedding = LexicalOnly(), LexicalOnly()
        old = InMemoryRecallIndex(embedding=old_embedding)
        new = InMemoryRecallIndex(embedding=new_embedding, policy_version="world-v2-recall-index.hybrid.14")
        old.rebuild(cursor=cursor, documents=corpus)
        new.rebuild(cursor=cursor, documents=corpus)
        previous = old.search(query)
        if boundary == "budget":
            import companion_daemon.world_v2.recall_index as module
            from companion_daemon.world_v2.recall_model_reading import interior_recall_item

            exact_size = len(module._canonical_json({"items": [interior_recall_item(
                hit.document, index_version=new._index_version,
            ) for hit in previous.hits]}).encode())
            monkeypatch.setattr(module, "RECALL_MODEL_READING_MAX_BYTES", exact_size)
        result, diagnostic = new.snapshot().search_with_diagnostics(query)
        assert len(previous.hits) == 1
        parent_texts = {d.retrieval_text or d.text for d in corpus if d.context_only}
        assert all(not parent_texts.intersection(call) for call in new_embedding.calls)
        assert len(result.hits) == 1
        assert result.hits[0].model_dump(exclude={"document"}) == previous.hits[0].model_dump(exclude={"document"})
        if boundary == "available":
            neighbor = result.hits[0].document
            assert neighbor.source_window_start is None and neighbor.context_only
            assert neighbor.retrieval_text is None
            assert neighbor.text == statement
            assert neighbor.prehistory == result.hits[0].document.prehistory
            assert neighbor.source_bindings == result.hits[0].document.source_bindings
            assert neighbor.document_id in diagnostic.context_document_ids
        else:
            assert result.hits == previous.hits
        assert not new.search(query.model_copy(update={"actor_ref": "actor:other", "subject_refs": ("actor:other",)})).hits
    finally:
        ledger.close()


def test_retained_long_prehistory_is_windowed_only_in_the_local_recall_corpus(tmp_path):
    ledger = started_ledger(tmp_path / "long-prehistory.sqlite")
    archive, statement = _long_reviewed_archive()
    try:
        row, = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(
            archive, created_at=START,
        )
        source = prehistory_memory_binding(row)
        pending = _choice(ledger, source)
        active = _choice(ledger, source, before=pending, status="active")

        retrieval = _read(ledger)
        item, = retrieval.items
        excerpt, = item.source_excerpts
        assert excerpt.text == statement[:480]
        assert excerpt.truncated
        assert excerpt._recall_source_record == row.record
        # The unbounded bytes are private to local retrieval and cannot enter a
        # provider snapshot through Pydantic serialization.
        encoded = item.model_dump_json()
        assert "末尾识别细节" not in encoded
        assert "source_text" not in encoded

        projection = ledger.project()
        authorities = tuple(
            RecallSourceBinding(
                source_kind="committed_event",
                authority_type=event.event_type,
                ref=event.event_id,
                source_world_revision=event.world_revision,
                immutable_hash=event.payload_hash,
            )
            for event in projection.committed_world_event_refs
        )
        sources = RecallCorpusSources(active_memory_candidates=retrieval.items)
        closed_authorities = select_recall_authority_bindings(
            sources=sources, candidates=authorities,
        )
        corpus = RecallCorpusCompiler().compile(
            cursor=RecallCursor(
                world_revision=projection.world_revision,
                deliberation_revision=projection.deliberation_revision,
                ledger_sequence=projection.ledger_sequence,
            ),
            actor_ref=ACTOR,
            subject_refs=(ACTOR,),
            sources=sources.model_copy(update={"authority_bindings": closed_authorities}),
        )
        assert len(corpus) == 3
        windows = tuple(sorted(corpus, key=lambda doc: doc.source_window_start))
        assert [doc.source_window_start for doc in windows] == [0, 416, 832]
        reconstructed = "".join(
            doc.text if index == 0 else doc.text[64:]
            for index, doc in enumerate(windows)
        )
        assert reconstructed == statement
        assert all(len(doc.text) <= 480 for doc in windows)
        assert all(doc.prehistory is not None and len(doc.source_bindings) == 2 for doc in windows)
        assert all(active.candidate_id in doc.link_refs for doc in windows)
        assert all(required_recall_authority_refs(sources).issubset({b.ref for b in doc.source_bindings})
                   for doc in windows)

        # A mismatched private source value cannot inject tail prose. The
        # compiler falls back to the bounded, hash-bound excerpt.
        forged_excerpt = excerpt.model_copy()
        forged_excerpt._recall_source_record = row.record.model_copy(update={
            "statement": statement + "伪造的尾部细节。",
        })
        forged_item = item.model_copy(update={"source_excerpts": (forged_excerpt,)})
        forged_retrieval = MemoryRetrievalResult(items=(forged_item,), suppressions=())
        forged_sources = RecallCorpusSources(active_memory_candidates=forged_retrieval.items)
        forged_authorities = select_recall_authority_bindings(
            sources=forged_sources, candidates=authorities,
        )
        fallback = RecallCorpusCompiler().compile(
            cursor=RecallCursor(
                world_revision=projection.world_revision,
                deliberation_revision=projection.deliberation_revision,
                ledger_sequence=projection.ledger_sequence,
            ),
            actor_ref=ACTOR,
            subject_refs=(ACTOR,),
            sources=forged_sources.model_copy(update={"authority_bindings": forged_authorities}),
        )
        assert len(fallback) == 1 and fallback[0].text == statement[:480]
        assert "伪造的尾部细节" not in fallback[0].text

        index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
        cursor = RecallCursor(
            world_revision=projection.world_revision,
            deliberation_revision=projection.deliberation_revision,
            ledger_sequence=projection.ledger_sequence,
        )
        index.rebuild(cursor=cursor, documents=corpus)
        query = RecallQuery(
            query_text="末尾识别细节 借书目录已还",
            cursor=cursor,
            actor_ref=ACTOR,
            subject_refs=(ACTOR,),
            viewer_privacy_ceiling="private",
            at=START,
            accessibility_seed="fixture:long-past-detail",
        )
        hits = index.search(query).hits
        assert any(
            "末尾识别细节" in hit.document.text
            and hit.document.source_window_start == 832
            for hit in hits
        )

        assert not index.search(query.model_copy(update={"actor_ref": "actor:other", "subject_refs": ("actor:other",)})).hits
        assert not index.search(query.model_copy(update={"viewer_privacy_ceiling": "public"})).hits

        saved_windows = tuple(sorted(
            ((doc.document_id, doc.text, doc.source_window_start) for doc in corpus),
        ))
        ledger.close()
        ledger = SQLiteWorldLedger(path=tmp_path / "long-prehistory.sqlite", world_id=ledger.world_id)
        cold_retrieval = _read(ledger)
        cold_corpus = _corpus(ledger, cold_retrieval)
        assert tuple(sorted((doc.document_id, doc.text, doc.source_window_start) for doc in cold_corpus)) == saved_windows

        _choice(ledger, source, before=active, status="forgotten")
        assert not _read(ledger).items
        forgotten = _corpus(ledger, _read(ledger))
        assert forgotten == ()
        assert ledger.rebuild().semantic_hash == ledger.project().semantic_hash
    finally:
        ledger.close()
