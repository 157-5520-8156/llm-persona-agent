"""Packing must examine later candidates after oversized earlier candidates."""

from datetime import timedelta
import json

import pytest

from companion_daemon.world_v2.recall_index import (
    FeatureHashRecallEmbedding,
    InMemoryRecallIndex,
    RecallQuery,
)
from companion_daemon.world_v2.recall_model_reading import interior_recall_item
from test_recall_index import CURSOR, NOW, _documents


@pytest.mark.parametrize("count", [6, 120])
def test_budget_rejections_keep_later_fitting_source_and_exact_diagnostic_order(count):
    base = _documents()[0]
    large = tuple(
        base.model_copy(
            update={
                "document_id": f"recall:packing:{i:03d}",
                "source_item_ref": f"fact:packing:{i:03d}",
                "text": '凤凰单丛茶杯"\\\n' * 100,
                "link_refs": ("fixture:linked",),
                "occurred_from": NOW - timedelta(days=1),
            }
        )
        for i in range(count)
    )
    small = base.model_copy(
        update={
            "document_id": "recall:packing:small",
            "source_item_ref": "fact:packing:small",
            "text": "凤凰单丛茶杯。",
            "link_refs": (),
        }
    )
    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    index.rebuild(cursor=CURSOR, documents=(*large, small))
    query = RecallQuery(
        query_text="凤凰单丛茶杯",
        cursor=CURSOR,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        viewer_privacy_ceiling="private",
        at=NOW,
        limit=8,
        link_refs=("fixture:linked",),
        accessibility_seed="packing-regression",
    )
    result, diagnostic = index.snapshot().search_with_diagnostics(query)
    assert any(h.document.document_id == small.document_id for h in result.hits)
    assert diagnostic.reading_budget_skips or diagnostic.result_budget_skips
    assert not (set(diagnostic.reading_budget_skips) | set(diagnostic.result_budget_skips)) & set(
        diagnostic.selected_document_ids
    )
    wire = json.dumps(
        {
            "items": [
                interior_recall_item(h.document, index_version=result.index_version)
                for h in result.hits
            ]
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert len(wire) <= 7000
    assert index.search(query) == result
    assert index.snapshot().search_with_diagnostics(query)[1] == diagnostic


@pytest.mark.parametrize("budget", ["reading", "result"])
def test_utf8_json_budget_boundary_counts_escapes_and_framing_exactly(monkeypatch, budget):
    from companion_daemon.world_v2 import recall_index

    doc = _documents()[0].model_copy(update={"text": '茶杯里的"茶"\\\n和🌧️。'})
    index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
    index.rebuild(cursor=CURSOR, documents=(doc,))
    query = RecallQuery(
        query_text="茶杯",
        cursor=CURSOR,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        viewer_privacy_ceiling="private",
        at=NOW,
        accessibility_seed="exact-byte-boundary",
        limit=1,
    )
    full = index.search(query)
    assert len(full.hits) == 1
    value = (
        {"items": [interior_recall_item(full.hits[0].document, index_version=full.index_version)]}
        if budget == "reading"
        else [full.hits[0].model_dump(mode="json")]
    )
    exact = len(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )
    name = "RECALL_MODEL_READING_MAX_BYTES" if budget == "reading" else "RECALL_RESULT_MAX_BYTES"
    monkeypatch.setattr(recall_index, name, exact)
    assert index.search(query) == full
    monkeypatch.setattr(recall_index, name, exact - 1)
    rejected, diagnostic = index.snapshot().search_with_diagnostics(query)
    assert rejected.hits == ()
    skips = (
        diagnostic.reading_budget_skips if budget == "reading" else diagnostic.result_budget_skips
    )
    assert skips == (doc.document_id,)
