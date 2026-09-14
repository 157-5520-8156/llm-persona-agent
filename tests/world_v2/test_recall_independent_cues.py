"""Attention evidence must not multiply overlap or below-threshold similarity."""

import math

import pytest

from companion_daemon.world_v2.recall_index import (
    InMemoryRecallIndex,
    _lexical_features,
    RECALL_INDEX_POLICY_VERSION,
)
from test_recall_index import CURSOR, _query
from test_recall_short_cues import LexicalOnly, document


@pytest.mark.parametrize(
    "first,second,phrase",
    [
        ("大学", "家里", "有没有"),
        ("海边", "木工", "看起来"),
        ("茶杯", "书店", "说起来"),
        ("围棋", "陶艺", "是不是"),
    ],
)
def test_independent_cues_compete_with_one_overlapping_phrase(first, second, phrase):
    docs = (
        document("two-cues", f"{first}，{second}。"),
        document("one-phrase", phrase),
        *(document(f"other:{i}", f"天气晴朗第{i}天") for i in range(12)),
    )
    index = InMemoryRecallIndex(embedding=LexicalOnly())
    index.rebuild(cursor=CURSOR, documents=docs)
    result = index.search(_query(query_text=f"{first}和{second}{phrase}有点联系", limit=2))
    scores = {h.document.document_id: h.lexical_score_bp for h in result.hits}
    assert scores["two-cues"] > scores["one-phrase"]
    assert result.hits[0].document.document_id == "two-cues"
    repeated = index.search(
        _query(query_text=f"{first}和{second}{phrase}有点联系 {phrase} {phrase}", limit=2)
    )
    assert {h.document.document_id: h.lexical_score_bp for h in repeated.hits} == scores


class ThresholdEmbedding:
    version = "fixture-threshold.1"
    dimensions = 2
    dense_match_threshold_bp = 5500

    def embed(self, texts):
        values = {"tea": 1, "tea alpha": 0.54, "tea beta": 0.05, "tea gamma": 0.56}
        return tuple((values[t], math.sqrt(1 - values[t] ** 2)) for t in texts)


def test_unqualified_dense_similarity_is_visible_but_cannot_raise_fused_score():
    index = InMemoryRecallIndex(embedding=ThresholdEmbedding())
    index.rebuild(
        cursor=CURSOR,
        documents=tuple(
            document(name, "tea " + suffix)
            for name, suffix in (("a", "alpha"), ("b", "beta"), ("c", "gamma"))
        ),
    )
    hits = {h.document.document_id: h for h in index.search(_query(query_text="tea")).hits}
    assert hits["a"].dense_score_bp == 5400 and hits["b"].dense_score_bp == 500
    assert "dense" not in hits["a"].match_channels and "dense" not in hits["b"].match_channels
    a, b, c = (hits[k].score_bp - hits[k].accessibility_offset_bp for k in ("a", "b", "c"))
    assert a == b
    assert "dense" in hits["c"].match_channels
    assert c - a == 1960


class RepeatedCueEmbedding:
    version = "fixture-repeated-cue.1"
    dimensions = 2
    dense_match_threshold_bp = 5500

    def embed(self, texts):
        values = {"窗台 家里 大学": 1, "窗台": 0.99, "又是窗台": 0.60, "家里": 0, "大学": 0}
        return tuple((values[t], math.sqrt(1 - values[t] ** 2)) for t in texts)


def test_repeated_cue_in_a_new_source_lane_does_not_evict_new_query_evidence():
    docs = (
        document("best", "窗台"),
        document("echo", "又是窗台", source_slice="open_threads"),
        document("family", "家里"),
        document("college", "大学"),
    )
    index = InMemoryRecallIndex(embedding=RepeatedCueEmbedding())
    index.rebuild(cursor=CURSOR, documents=docs)
    result = index.search(_query(query_text="窗台 家里 大学", limit=3))
    assert result.hits[0].document.document_id == "best"
    assert {h.document.document_id for h in result.hits} == {"best", "family", "college"}
    assert result.index_version.startswith(RECALL_INDEX_POLICY_VERSION)
    assert index.search(_query(query_text="窗台 家里 大学", limit=3)) == result
    assert tuple(h.document for h in result.hits) == tuple(
        next(d for d in docs if d.document_id == h.document.document_id) for h in result.hits
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Ｃａｆé学校", {"café", "学校"}),
        ("你好世界", {"你好", "好世", "世界", "你好世", "好世界"}),
        ("tea steaming tea", {"tea", "steaming"}),
        ("一a🙂二b", set()),
        ("カフェ", {"カフ", "フェ", "カフェ"}),
    ],
)
def test_coordinate_tokenization_keeps_existing_embedding_features(text, expected):
    assert _lexical_features(text) == frozenset(expected)
