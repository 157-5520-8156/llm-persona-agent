"""Story navigation must use the already admitted story pack's budgets."""

import json
import pytest

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
            return len(
                json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            )

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


def test_full_pack_keeps_primary_link_anchor_and_explicit_sources():
    from test_recall_index import _query as query_fixture

    template = _documents()[1]
    docs = [
        template.model_copy(
            update={
                "document_id": f"recall:direct:{i}",
                "source_item_ref": f"source:direct:{i}",
                "link_refs": ("lived-story-link:pair",) if i == 1 else (),
            }
        )
        for i in range(6)
    ]
    peer = template.model_copy(
        update={
            "document_id": "recall:peer",
            "source_item_ref": "source:peer",
            "link_refs": ("lived-story-link:pair",),
        }
    )
    hits = tuple(_hit(d) for d in docs)
    query = query_fixture(limit=6, link_refs=(docs[3].source_item_ref,))
    rows = tuple((d, ()) for d in (*docs, peer))
    old = expand_lived_story_hits(
        hits=hits, eligible_rows=rows, query=query, index_version="world-v2-recall-index.hybrid.19"
    )
    assert old == hits
    current = expand_lived_story_hits(
        hits=hits, eligible_rows=rows, query=query, index_version="world-v2-recall-index.hybrid.20"
    )
    assert len(current) == 6
    assert {docs[i].document_id for i in (0, 1, 3)} <= {h.document.document_id for h in current}
    assert peer.document_id in {h.document.document_id for h in current}
    assert (
        expand_lived_story_hits(
            hits=hits,
            eligible_rows=tuple((d, ()) for d in docs),
            query=query,
            index_version="world-v2-recall-index.hybrid.20",
        )
        == hits
    )
    sparse = hits[:3]
    assert expand_lived_story_hits(
        hits=sparse,
        eligible_rows=rows,
        query=query,
        index_version="world-v2-recall-index.hybrid.20",
    ) == expand_lived_story_hits(
        hits=sparse,
        eligible_rows=rows,
        query=query,
        index_version="world-v2-recall-index.hybrid.19",
    )
    explicit = query.model_copy(update={"link_refs": tuple(d.source_item_ref for d in docs)})
    assert (
        expand_lived_story_hits(
            hits=hits,
            eligible_rows=rows,
            query=explicit,
            index_version="world-v2-recall-index.hybrid.20",
        )
        == hits
    )


@pytest.mark.parametrize("coordinate", ["event", "record"])
def test_full_pack_preserves_every_supported_explicit_history_coordinate(tmp_path, coordinate):
    from companion_daemon.world_v2.recall_index import structured_recall_refs

    ledger = started_ledger(tmp_path / "protected.sqlite")
    try:
        _active_rows(ledger, _linked_archive())
        target = next(d for d in _corpus(ledger, _read(ledger)) if d.prehistory)
        if coordinate == "record":
            ref = target.source_item_ref
            target = target.model_copy(update={"source_item_ref": ref + ":window:0:40"})
        else:
            ref = target.source_refs[0]
        assert ref in structured_recall_refs(
            target, policy_version="world-v2-recall-index.hybrid.20"
        )
        base = _documents()[1]
        docs = [
            base.model_copy(
                update={
                    "document_id": f"recall:other:{i}",
                    "source_item_ref": f"other:{i}",
                    "link_refs": ("lived-story-link:protected",) if i == 1 else (),
                }
            )
            for i in range(5)
        ]
        docs.append(target)
        peer = base.model_copy(
            update={
                "document_id": "recall:peer",
                "source_item_ref": "peer",
                "link_refs": ("lived-story-link:protected",),
            }
        )
        result = expand_lived_story_hits(
            hits=tuple(_hit(d) for d in docs),
            eligible_rows=tuple((d, ()) for d in (*docs, peer)),
            query=_query(ledger).model_copy(update={"link_refs": (ref,)}),
            index_version="world-v2-recall-index.hybrid.20",
        )
        assert len(result) == 6
        assert target.document_id in {h.document.document_id for h in result}
        assert peer.document_id in {h.document.document_id for h in result}
    finally:
        ledger.close()
