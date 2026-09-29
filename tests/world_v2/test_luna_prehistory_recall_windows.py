from __future__ import annotations

from datetime import timedelta

from companion_daemon.world_v2.character_prehistory import (
    PrehistoryReview,
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
    _install,
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
