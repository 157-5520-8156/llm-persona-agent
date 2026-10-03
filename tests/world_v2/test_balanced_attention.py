from companion_daemon.world_v2.recall_index import FeatureHashRecallEmbedding, InMemoryRecallIndex
from companion_daemon.world_v2.recall_runtime import (
    RecallCoordinator, _PinnedBatchRecallEmbedding, verify_trusted_recall_trace,
)
from test_recall_index import CURSOR, NOW, _documents, _SemanticFixtureEmbedding


class Embedding(_SemanticFixtureEmbedding):
    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(texts)
        special = {"当前处境与此前话题": (0.0, 1.0, 0.0), "当前问题": (1.0, 0.0, 0.0)}
        return tuple(special[text] if text in special else super(Embedding, self).embed((text,))[0]
                     for text in texts)


def test_automatic_attention_batches_both_queries_without_changing_sources():
    documents = _documents()[:2]
    local = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    local.rebuild(cursor=CURSOR, documents=documents)
    embedding = Embedding()
    coordinator = RecallCoordinator.from_built_index(
        index=local, cursor=CURSOR, actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"), logical_time=NOW,
        semantic_embedding=embedding, trigger_ref="trigger:balanced",
    )
    try:
        trusted = coordinator.prefetch(expected_cursor=CURSOR, query_text="当前处境与此前话题",
            lexical_text="当前问题", accessibility_seed="draw:balanced", trigger_ref="trigger:balanced")
        trace = verify_trusted_recall_trace(trusted)
    finally:
        coordinator.close()
    assert len(embedding.calls) == 1
    assert embedding.calls[0][-2:] == ("当前处境与此前话题", "当前问题")
    assert trace.embedding_version.endswith(":balanced-attention.1")
    assert trace.request.lexical_text == "当前问题"
    assert all(hit.document.document_id in {d.document_id for d in documents} for hit in trace.hits)


def test_query_override_never_replaces_a_document_vector_with_identical_text():
    embedding = Embedding()
    adapter = _PinnedBatchRecallEmbedding(source=embedding, texts=("same text",),
        vectors=((1.0, 0.0, 0.0),), focused_query=("same text", (0.0, 1.0, 0.0)))
    assert adapter.embed(("same text",)) == ((1.0, 0.0, 0.0),)
    assert adapter.embed_query("same text") == (0.0, 1.0, 0.0)
