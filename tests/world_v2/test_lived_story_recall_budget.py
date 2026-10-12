"""Story navigation must use the already admitted story pack's budgets."""

import json

from companion_daemon.world_v2.lived_story import expand_lived_story_hits
from companion_daemon.world_v2.recall_index import (
    RecallHit,
    InMemoryRecallIndex,
    FeatureHashRecallEmbedding,
)
from companion_daemon.world_v2.recall_model_reading import interior_recall_item
from test_prehistory_story_recall import _active_rows, _linked_archive, _query, started_ledger
from test_prehistory_memory_source import _corpus, _read
from test_recall_index import _documents


def _hit(document):
    return RecallHit(
        document=document,
        match_channels=("lexical",),
        score_bp=7000,
        lexical_score_bp=7000,
        dense_score_bp=0,
        temporal_score_bp=0,
        structured_score_bp=0,
        accessibility_offset_bp=0,
    )


def test_scene_and_lived_story_share_existing_budget_without_dropping_direct_hits(tmp_path):
    ledger = started_ledger(tmp_path / "story.sqlite")
    try:
        _active_rows(ledger, _linked_archive())
        docs = tuple(d for d in _corpus(ledger, _read(ledger)) if d.prehistory)[:5]
        assert len(docs) == 5
        key = "lived-story-link:fixture"
        docs = (
            docs[0].model_copy(update={"link_refs": tuple(sorted((*docs[0].link_refs, key)))}),
            *docs[1:],
        )
        hits = tuple(_hit(d) for d in docs)
        query = _query(ledger)
        def wire(obj):
            return len(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
        peer = None
        for size in range(100, 1600, 25):
            candidate = _documents()[1].model_copy(
                update={
                    "text": "已经发生的外部事实。" * size,
                    "actor_ref": query.actor_ref,
                    "subject_refs": query.subject_refs,
                    "link_refs": (key,),
                }
            )
            all_hits = (*hits, _hit(candidate))
            model_bytes = wire(
                {
                    "items": [
                        interior_recall_item(
                            h.document, index_version="world-v2-recall-index.hybrid.18"
                        )
                        for h in all_hits
                    ]
                }
            )
            result_bytes = wire([h.model_dump(mode="json") for h in all_hits])
            if 7000 < model_bytes <= 11000 and 14000 < result_bytes <= 22000:
                peer = candidate
                break
        assert peer is not None, "fixture must straddle the actual normal/story budget seam"
        rows = tuple((d, ()) for d in (*docs, peer))
        old = expand_lived_story_hits(
            hits=hits,
            eligible_rows=rows,
            query=query,
            index_version="world-v2-recall-index.hybrid.18",
        )
        assert old == hits
        current = expand_lived_story_hits(
            hits=hits,
            eligible_rows=rows,
            query=query,
            index_version="world-v2-recall-index.hybrid.19",
        )
        assert current[:5] == hits
        assert len(current) == 6 and current[-1].document.source_item_ref == peer.source_item_ref
        index = InMemoryRecallIndex(
            embedding=FeatureHashRecallEmbedding(), policy_version="world-v2-recall-index.hybrid.19"
        )
        index.rebuild(cursor=query.cursor, documents=(*docs, peer))
        assert not index.search(
            query.model_copy(
                update={"actor_ref": "actor:foreign", "subject_refs": ("actor:foreign",)}
            )
        ).hits
    finally:
        ledger.close()
