"""Repeated exact-source reads must not repeatedly walk the entire event prefix."""

import pytest

from companion_daemon.world_v2.character_prehistory import ReviewedPrehistoryArchive, digest
from companion_daemon.world_v2.character_prehistory_runtime import PrehistoryArchiveRuntime
from companion_daemon.world_v2.prehistory_memory_source import (
    prehistory_memory_binding,
    resolve_prehistory_memory_source,
)
from companion_daemon.world_v2.schemas import CommittedWorldEventRef
from test_character_prehistory import ACTOR, START, reviewed_archive, started_ledger


def test_source_reads_bound_lookup_work_and_still_reject_withdrawn_or_changed_authority(
    tmp_path, monkeypatch
):
    ledger = started_ledger(tmp_path / "history.sqlite")
    original = reviewed_archive()
    records = tuple(
        original.document.records[0].model_copy(
            update={"record_id": f"prehistory-record:scale-{i:03d}"}
        )
        for i in range(200)
    )
    document = original.document.model_copy(update={"records": records})
    archive = ReviewedPrehistoryArchive(
        document=document,
        review=original.review.model_copy(update={"manifest_hash": digest(document.manifest())}),
    )
    try:
        rows = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(
            archive, created_at=START
        )
        state = ledger.project()
        row = rows[-1]
        binding = prehistory_memory_binding(row)
        accesses = 0
        get = CommittedWorldEventRef.__getattribute__

        def counted(self, name):
            nonlocal accesses
            if name == "event_id":
                accesses += 1
            return get(self, name)

        with monkeypatch.context() as m:
            m.setattr(CommittedWorldEventRef, "__getattribute__", counted)
            for _ in range(64):
                resolved, _ = resolve_prehistory_memory_source(
                    binding,
                    records=state.prehistory_records,
                    archives=state.prehistory_archives,
                    committed_events=state.committed_world_event_refs,
                )
                assert resolved == row
        assert accesses < len(state.committed_world_event_refs) * 16
        missing = tuple(
            e for e in state.committed_world_event_refs if e.event_id != row.accepted_event_ref
        )
        with pytest.raises(ValueError, match="source event is unavailable"):
            resolve_prehistory_memory_source(
                binding,
                records=state.prehistory_records,
                archives=state.prehistory_archives,
                committed_events=missing,
            )
        changed = tuple(
            e.model_copy(update={"payload_hash": "f" * 64})
            if e.event_id == row.accepted_event_ref
            else e
            for e in state.committed_world_event_refs
        )
        with pytest.raises(ValueError, match="source event is unavailable"):
            resolve_prehistory_memory_source(
                binding,
                records=state.prehistory_records,
                archives=state.prehistory_archives,
                committed_events=changed,
            )
    finally:
        ledger.close()


def test_warm_source_proof_does_not_rehash_archive_as_cache_key(tmp_path, monkeypatch):
    from companion_daemon.world_v2.character_prehistory import PrehistoryArchiveManifest

    ledger = started_ledger(tmp_path / "hash-key.sqlite")
    try:
        (row,) = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(
            reviewed_archive(), created_at=START
        )
        state = ledger.project()
        binding = prehistory_memory_binding(row)
        kwargs = dict(
            records=state.prehistory_records,
            archives=state.prehistory_archives,
            committed_events=state.committed_world_event_refs,
        )
        assert resolve_prehistory_memory_source(binding, **kwargs)[0] == row

        def costly_key_forbidden(self):
            raise AssertionError("whole archive hashed for a cached lookup")

        with monkeypatch.context() as m:
            m.setattr(PrehistoryArchiveManifest, "__hash__", costly_key_forbidden)
            assert resolve_prehistory_memory_source(binding, **kwargs)[0] == row
        changed = row.model_copy(
            update={"record": row.record.model_copy(update={"statement": "不同的来源正文"})}
        )
        with pytest.raises(ValueError, match="exact prehistory authority"):
            resolve_prehistory_memory_source(
                binding,
                records=(changed,),
                archives=state.prehistory_archives,
                committed_events=state.committed_world_event_refs,
            )
    finally:
        ledger.close()


def test_sqlite_snapshot_reuses_exact_decoding_but_reads_changed_stored_bytes(
    tmp_path, monkeypatch
):
    import sqlite3
    from companion_daemon.world_v2.recall_index import (
        SQLiteRecallIndex,
        FeatureHashRecallEmbedding,
        RecallDocument,
    )
    from test_recall_index import _documents, CURSOR

    path = tmp_path / "recall.sqlite"
    index = SQLiteRecallIndex(
        path=path, world_id="world:decode-cache", embedding=FeatureHashRecallEmbedding()
    )
    docs = tuple(
        d.model_copy(update={"document_id": d.document_id + ":decode-cache-probe"})
        for d in _documents()[:2]
    )
    calls = 0
    original = RecallDocument.model_validate_json

    def counted(cls, *args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    try:
        with monkeypatch.context() as m:
            m.setattr(RecallDocument, "model_validate_json", classmethod(counted))
            index.rebuild(cursor=CURSOR, documents=docs)
            first = index.snapshot()
            index.rebuild(
                cursor=CURSOR.model_copy(update={"world_revision": 13, "ledger_sequence": 82}),
                documents=docs,
            )
            second = index.snapshot()
            assert first.documents == second.documents
            assert calls == 2
        with sqlite3.connect(path) as db:
            db.execute(
                "update world_v2_recall_documents set document_json='{}' where world_id=? and document_id=?",
                ("world:decode-cache", docs[0].document_id),
            )
        with pytest.raises(ValueError):
            index.snapshot()
    finally:
        index.close()


def test_index_set_hash_remains_exact_canonical_json_across_incremental_refresh(tmp_path):
    import hashlib
    import json
    import sqlite3
    from companion_daemon.world_v2.recall_index import SQLiteRecallIndex, FeatureHashRecallEmbedding
    from test_recall_index import _documents, CURSOR

    path = tmp_path / "canonical.sqlite"
    index = SQLiteRecallIndex(
        path=path, world_id="world:canonical", embedding=FeatureHashRecallEmbedding()
    )
    try:
        doc = _documents()[0].model_copy(update={"text": '引号"、换行\n与反斜线\\保持原样。'})
        for n, docs in enumerate(
            ((doc,), (doc,), (doc.model_copy(update={"text": "新正文"}),), ())
        ):
            cursor = CURSOR.model_copy(update={"world_revision": 12 + n, "ledger_sequence": 81 + n})
            index.rebuild(cursor=cursor, documents=docs)
            with sqlite3.connect(path) as db:
                stored = db.execute(
                    "select document_json,embedding_json from world_v2_recall_documents where world_id=? order by document_id",
                    ("world:canonical",),
                ).fetchall()
                actual = db.execute(
                    "select document_set_hash from world_v2_recall_index_heads where world_id=?",
                    ("world:canonical",),
                ).fetchone()[0]
            payload = [
                {"document": json.loads(d), "embedding": tuple(float(v) for v in json.loads(e))}
                for d, e in stored
            ]
            expected = hashlib.sha256(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            assert actual == expected
    finally:
        index.close()


def test_recovered_embedding_replaces_zero_vector_and_is_then_reused(tmp_path):
    from companion_daemon.world_v2.recall_index import SQLiteRecallIndex
    from test_recall_index import _documents, CURSOR

    class RecoveringEmbedding:
        version = "test:zero-recovery.1"
        dimensions = 2
        calls = 0

        def embed(self, texts):
            self.calls += 1
            return tuple((0.0, 0.0) if self.calls == 1 else (1.0, 0.0) for _ in texts)

    embedding = RecoveringEmbedding()
    index = SQLiteRecallIndex(
        path=tmp_path / "recover.sqlite", world_id="world:recover", embedding=embedding
    )
    try:
        docs = _documents()[:1]
        index.rebuild(cursor=CURSOR, documents=docs)
        assert index.snapshot()._rows[0][1] == (0.0, 0.0)
        for n in (1, 2):
            index.rebuild(
                cursor=CURSOR.model_copy(
                    update={"world_revision": 12 + n, "ledger_sequence": 81 + n}
                ),
                documents=docs,
            )
            assert index.snapshot()._rows[0][1] == (1.0, 0.0)
        assert embedding.calls == 2
    finally:
        index.close()
