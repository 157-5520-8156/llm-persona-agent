"""Changing the query should not tokenize unchanged document text again."""

import unicodedata

from companion_daemon.world_v2.associative_recall import lexical_relevance_bp
from companion_daemon.world_v2.recall_index import InMemoryRecallIndex
from test_recall_index import _documents, _query, _UniformEmbedding, CURSOR


def test_associative_queries_reuse_exact_document_features(monkeypatch):
    document = "查询变化仍复用此段中文词面特征，学校校刊和放学后的雨。"
    original = unicodedata.normalize
    reads = []

    def count(form, text):
        if text == document:
            reads.append(text)
        return original(form, text)

    with monkeypatch.context() as patch:
        patch.setattr(unicodedata, "normalize", count)
        assert lexical_relevance_bp("学校校刊", (document,)) > 0
        assert lexical_relevance_bp("放学后的雨", (document,)) > 0
    assert len(reads) <= 1


def test_index_queries_reuse_features_without_reusing_query_selection(monkeypatch):
    index = InMemoryRecallIndex(embedding=_UniformEmbedding())
    document = _documents()[0].model_copy(update={"text": "热茶和茶具是这次特征复用检查的内容。"})
    index.rebuild(cursor=CURSOR, documents=(document,))
    original = unicodedata.normalize
    reads = []

    def count(form, text):
        if text == document.text:
            reads.append(text)
        return original(form, text)

    with monkeypatch.context() as patch:
        patch.setattr(unicodedata, "normalize", count)
        first = index.search(_query(query_text="热茶"))
        second = index.search(_query(query_text="茶具"))
    assert first.hits and second.hits
    assert first.query_hash != second.query_hash
    assert len(reads) <= 1


def test_feature_values_are_byte_bounded_and_eviction_does_not_change_results():
    from companion_daemon.world_v2.lexical_feature_cache import lexical_feature_cache

    @lexical_feature_cache(max_bytes=1200, maxsize=2)
    def features(text):
        return frozenset((text, text.upper()))

    for text in ("first", "second", "third", "first"):
        assert features(text) == features.__wrapped__(text)
        info = features.cache_info()
        assert info.currsize <= 2 and info.retained_bytes <= 1200
    before = features.cache_info().currsize
    assert features("long" * 2000) == features.__wrapped__("long" * 2000)
    assert features.cache_info().currsize == before
    features.cache_clear()
    assert features.cache_info().retained_bytes == 0


def test_shared_features_equal_the_independent_span_tokenizer():
    import random
    from companion_daemon.world_v2.associative_recall import _text_recall_features
    from companion_daemon.world_v2.recall_index import _lexical_feature_spans

    rng = random.Random(20261009)
    alphabet = "高中文章茶雨あいうえ漢字ＡＢ１２abcDEF123 !?—\n\tëé\u0301ß"
    samples = ["", "a", "中", "中文", "中文学", "ＡＢＣ１２３", "Straße café", "ábc"]
    samples += ["".join(rng.choice(alphabet) for _ in range(80)) for _ in range(200)]
    for text in samples:
        assert _text_recall_features(text) == frozenset(
            term for _, _, term in _lexical_feature_spans(text)
        )


def test_index_maintenance_prepares_lexical_values_without_a_new_embedding_call():
    from companion_daemon.world_v2.associative_recall import _text_recall_features
    from companion_daemon.world_v2.recall_runtime import RecallCoordinator
    from companion_daemon.world_v2.recall_index import FeatureHashRecallEmbedding
    from test_recall_index import _CountingSemanticFixtureEmbedding, NOW

    semantic = _CountingSemanticFixtureEmbedding()
    primary = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    documents = _documents()
    primary.rebuild(cursor=CURSOR, documents=documents)
    coordinator = RecallCoordinator.from_built_index(
        index=primary,
        cursor=CURSOR,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        logical_time=NOW,
        semantic_embedding=semantic,
    )
    try:
        coordinator.prepare_semantic(expected_cursor=CURSOR)
        semantic.embedded_texts.clear()
        _text_recall_features.cache_clear()
        coordinator.prepare_semantic(expected_cursor=CURSOR)
        misses = _text_recall_features.cache_info().misses
        for document in documents:
            _text_recall_features(document.text)
            _text_recall_features(document.retrieval_text or document.text)
        assert _text_recall_features.cache_info().misses == misses
        assert semantic.embedded_texts == []
    finally:
        coordinator.close()
