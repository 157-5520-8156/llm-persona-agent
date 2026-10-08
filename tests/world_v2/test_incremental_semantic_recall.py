"""Contract tests through the existing public recall seam."""

from companion_daemon.world_v2.recall_index import IncrementalSemanticRecallIndex, RecallQuery
from companion_daemon.world_v2.recall_embedding import SQLiteCachedRecallEmbedding
import pytest

from companion_daemon.world_v2.recall_runtime import RecallCoordinator, CharacterRecallRequest
from companion_daemon.world_v2.recall_index import InMemoryRecallIndex, FeatureHashRecallEmbedding
from test_recall_index import CURSOR, NOW, _documents, _CountingSemanticFixtureEmbedding


def test_prepared_recall_embeds_only_queries_and_keeps_exact_results():
    semantic = _CountingSemanticFixtureEmbedding()
    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    index.rebuild(cursor=CURSOR, documents=_documents())
    recall = RecallCoordinator.from_built_index(
        index=index,
        cursor=CURSOR,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        logical_time=NOW,
        semantic_embedding=semantic,
    )
    try:
        report = recall.prepare_semantic(expected_cursor=CURSOR)
        assert report["status"] == "ready"
        semantic.embedded_texts.clear()
        result = recall.recall(
            request=CharacterRecallRequest(query_text="泡茶工具"),
            accessibility_seed="draw:prepared",
            expected_cursor=CURSOR,
            trigger_ref="test:prepared",
        )
        assert semantic.embedded_texts == ["泡茶工具"]
        assert any(h.document.document_id == "recall:experience:gaiwan" for h in result.audit.hits)
        semantic.embedded_texts.clear()
        recall.prepare_semantic(expected_cursor=CURSOR)
        assert semantic.embedded_texts == []
    finally:
        recall.close()


class CountingEmbedding:
    version = "counting-unified.1"
    dimensions = 3

    def __init__(self):
        self.calls = []
        self.fail = False

    def embed(self, texts):
        from companion_daemon.world_v2.recall_index import RecallEmbeddingUnavailable

        self.calls.append(texts)
        if self.fail:
            raise RecallEmbeddingUnavailable("fixture_unavailable")
        return tuple((1.0, 0.0, 0.0) for _ in texts)


def local_snapshot(documents, cursor=CURSOR):
    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    index.rebuild(cursor=cursor, documents=documents)
    return index.snapshot()


def tea_query(cursor=CURSOR, privacy="private"):
    return RecallQuery(
        query_text="泡茶工具",
        cursor=cursor,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        viewer_privacy_ceiling=privacy,
        at=NOW,
        accessibility_seed="draw:incremental",
    )


def test_incremental_text_and_current_metadata_never_reuse_old_membership(tmp_path):
    provider = CountingEmbedding()
    cache = SQLiteCachedRecallEmbedding(
        path=tmp_path / "cache.sqlite", world_id="world:test", delegate=provider
    )
    index = IncrementalSemanticRecallIndex(cache)
    doc = next(d for d in _documents() if d.document_id == "recall:experience:gaiwan")
    try:
        old, _ = index.prepare(local_snapshot((doc,)))
        assert len(provider.calls) == 1
        provider.calls.clear()
        # Same body, different privacy and revision: reuse vector, current gates.
        cursor = CURSOR.model_copy(update={"ledger_sequence": 82})
        restricted = doc.model_copy(update={"privacy_class": "withhold"})
        new, _ = index.prepare(local_snapshot((restricted,), cursor))
        assert provider.calls == []
        assert new.search(tea_query(cursor)).hits == ()
        assert old.search(tea_query()).hits[0].document == doc
        provider.calls.clear()
        revised = doc.model_copy(update={"text": "她后来把盖碗送给了朋友。"})
        revision, _ = index.prepare(local_snapshot((revised,), cursor))
        assert provider.calls == [("她后来把盖碗送给了朋友。",)]
        assert (
            revision.search(tea_query(cursor)).hits[0].document.text == "她后来把盖碗送给了朋友。"
        )
        empty, _ = index.prepare(local_snapshot((), cursor))
        assert empty.search(tea_query(cursor)).hits == ()
        # Original pinned turn stays immutable despite update and removal.
        assert old.search(tea_query()).hits[0].document.text == doc.text
    finally:
        cache.close()


def test_preparation_progress_survives_restart_without_provider_rebilling(tmp_path):
    path = tmp_path / "cache.sqlite"
    provider = CountingEmbedding()
    doc = _documents()[0]
    snapshot = local_snapshot((doc,))
    cache = SQLiteCachedRecallEmbedding(path=path, world_id="world:test", delegate=provider)
    IncrementalSemanticRecallIndex(cache).prepare(snapshot)
    cache.close()
    second_provider = CountingEmbedding()
    reopened = SQLiteCachedRecallEmbedding(
        path=path, world_id="world:test", delegate=second_provider
    )
    try:
        rebuilt, _ = IncrementalSemanticRecallIndex(reopened).prepare(snapshot)
        assert second_provider.calls == []
        assert rebuilt.documents == snapshot.documents
    finally:
        reopened.close()


def test_failed_preparation_is_visible_retryable_and_never_poisoned_as_ready():
    provider = CountingEmbedding()
    provider.fail = True
    index = IncrementalSemanticRecallIndex(provider)
    snapshot = local_snapshot((_documents()[0],))
    with pytest.raises(RuntimeError, match="fixture_unavailable"):
        index.prepare(snapshot)
    assert index.health()["status"] == "degraded"
    provider.fail = False
    prepared, _ = index.prepare(snapshot)
    assert index.health()["status"] == "ready"
    assert prepared.documents == snapshot.documents


@pytest.mark.asyncio
async def test_application_prepares_at_startup_and_after_background_with_no_role_call(tmp_path):
    from test_production_turn_application import (
        _build_application,
        _config,
        _Identities,
        _Router,
        _InvalidModel,
        _Transport,
        NOW as APP_NOW,
    )

    provider = CountingEmbedding()
    app = _build_application(
        path=tmp_path / "world.sqlite",
        config=_config(),
        identities=_Identities(),
        router=_Router(),
        inbound_author=_InvalidModel(),
        transport=_Transport(),
        now=APP_NOW,
        semantic_recall_embedding=provider,
    )
    try:
        assert app.dashboard_semantic_recall_health()["maintenance"]["status"] == "ready"
        before = app.export_replay_evidence()
        provider.calls.clear()
        assert (await app.prepare_memory_index())["unchanged"] is True
        assert provider.calls == []
        assert await app.drain_background_once() is None
        after = app.export_replay_evidence()
        assert before == after
    finally:
        await app.aclose()


def test_new_runtime_life_uses_same_incremental_index_and_exact_source_bindings():
    from test_contextual_life_recall import life, documents, query, CURSOR as LIFE_CURSOR

    provider = CountingEmbedding()
    index = IncrementalSemanticRecallIndex(provider)
    old = documents([life("old", "昨天和朋友一起修台灯，接线完成了。", plan="lamp", days=2)])
    index.prepare(local_snapshot(old, LIFE_CURSOR))
    provider.calls.clear()
    new = documents([life("new", "今天朋友带来灯泡，更换后通电测试通过。", plan="lamp", days=1)])
    both, _ = index.prepare(local_snapshot((*old, *new), LIFE_CURSOR))
    old_texts = {d.retrieval_text or d.text for d in old if not d.context_only}
    assert all(t not in old_texts for batch in provider.calls for t in batch)
    hits = both.search(query("朋友带来灯泡更换通电测试")).hits
    hit = next(h for h in hits if h.document.source_item_ref.startswith("occurrence:new:"))
    expected = next(d for d in new if d.document_id == hit.document.document_id)
    assert hit.document == expected
    assert hit.document.settled_life is not None
