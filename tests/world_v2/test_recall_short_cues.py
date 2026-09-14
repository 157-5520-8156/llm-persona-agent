"""Distinctive short cues compete for bounded attention without new model calls."""
import json
import pytest
from companion_daemon.world_v2.recall_index import InMemoryRecallIndex, SQLiteRecallIndex, RECALL_RESULT_MAX_BYTES
from test_recall_index import _documents, _query, CURSOR


class LexicalOnly:
    version = "fixture-no-dense.1"
    dimensions = 2
    def embed(self, texts):
        return tuple((0.0, 0.0) for _ in texts)


def document(identifier, text, **updates):
    return _documents()[0].model_copy(update={"document_id": identifier, "text": text,
        "retrieval_text": None, "link_refs": (), **updates})


def corpus(topic):
    return (document("remembered", f"高中参加过{topic}，留下了几本旧册子。"),
            *(document(f"recent:{i}", f"今天处理第{i}份任务，然后谈谈今天的安排。") for i in range(12)))


@pytest.mark.parametrize("topic", ["校刊", "合唱", "木工", "潜水", "陶艺", "围棋"])
@pytest.mark.parametrize("wording", ["{topic}", "所以你今天也忙着{topic}的事吗？", "突然想到你以前提过的{topic}，愿意再聊聊吗？"])
def test_short_cue_survives_generic_recent_material(topic, wording):
    index = InMemoryRecallIndex(embedding=LexicalOnly())
    index.rebuild(cursor=CURSOR, documents=corpus(topic))
    result = index.search(_query(query_text=wording.format(topic=topic), limit=2))
    assert result.hits[0].document.document_id == "remembered"
    assert "lexical" in result.hits[0].match_channels
    assert len(json.dumps([h.model_dump(mode="json") for h in result.hits],
                          ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()) <= RECALL_RESULT_MAX_BYTES
    unrelated = index.search(_query(query_text="火星登陆", limit=2))
    assert not unrelated.hits


@pytest.mark.parametrize("excluded", ["privacy", "actor", "status", "time"])
def test_unavailable_corpus_cannot_change_visible_term_frequency(excluded):
    docs = corpus("陶艺")
    query = _query(query_text="今天也想聊聊陶艺", viewer_privacy_ceiling="personal")
    index = InMemoryRecallIndex(embedding=LexicalOnly())
    index.rebuild(cursor=CURSOR, documents=docs)
    before = index.search(query)
    updates = {"privacy": {"privacy_class": "withhold"}, "actor": {"actor_ref": "agent:other"},
               "status": {"status": "superseded"}, "time": {"valid_from": query.at.replace(year=2027)}}[excluded]
    hidden = tuple(document(f"hidden:{i}", "陶艺陶艺陶艺", **updates) for i in range(20))
    index.rebuild(cursor=CURSOR, documents=(*docs, *hidden))
    assert index.search(query) == before


def test_sqlite_rebuild_migrates_disposable_index_and_preserves_cold_results(tmp_path):
    path = tmp_path / "index.sqlite"
    index = SQLiteRecallIndex(path=path, world_id="world:fixture", embedding=LexicalOnly())
    docs = corpus("围棋")
    query = _query(query_text="今天也想聊聊围棋")
    index.rebuild(cursor=CURSOR, documents=docs)
    result = index.search(query)
    index._connection.execute("UPDATE world_v2_recall_index_heads SET index_version = ?",
                              ("world-v2-recall-index.hybrid.3+embedding:fixture-no-dense.1",))
    index.rebuild(cursor=CURSOR, documents=docs)
    assert index.search(query) == result
    index.close()
    reopened = SQLiteRecallIndex(path=path, world_id="world:fixture", embedding=LexicalOnly())
    try:
        assert reopened.search(query) == result
    finally:
        reopened.close()
