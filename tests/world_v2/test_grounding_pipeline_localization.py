"""Short, offline controls that localize grounding failures by stage.

These controls use the production recall index/coordinator and model-context
transport. Fixture embeddings make the retrieval result deterministic; they do
not qualify BGE-M3 quality or change any production threshold.
"""

import json

from companion_daemon.world_v2.recall_audit import CharacterRecallRequest
from companion_daemon.world_v2.recall_index import InMemoryRecallIndex
from companion_daemon.world_v2.recall_runtime import (
    RecallCoordinator,
    augment_model_content_with_recall,
    recall_evidence_json,
    verify_trusted_recall_trace,
)
from companion_daemon.world_v2.model_facing_context import compact_chat_model_facing_context
from test_recall_index import CURSOR, NOW, _bindings, _documents, _query


QUERY = "你昨晚在校园做什么？"
TARGET_ID = "recall:life-result:campus"
TARGET_REF = "event:life-result:campus"
STAGE_CURSOR = CURSOR.model_copy(update={"world_revision": 20, "ledger_sequence": 100})


class _StageEmbedding:
    version = "grounding-stage-fixture.1"
    dimensions = 2
    dense_match_threshold_bp = 4_200

    def __init__(self, *, target=(0.98, 0.2)):
        self.target = target

    def embed(self, texts):
        vectors = {
            QUERY: (1.0, 0.0),
            "retrieval:target-result": self.target,
            # This distractor wins rank through exact lexical and dense match.
            QUERY + " retrieval": (1.0, 0.0),
        }
        return tuple(vectors[text] for text in texts)


def _target_document():
    return _documents()[2].model_copy(update={
        "document_id": TARGET_ID,
        "source_item_ref": "life-result:campus",
        "source_refs": (TARGET_REF,),
        "source_bindings": _bindings(TARGET_REF, revision=13),
        "source_world_revision": 13,
        "text": "她昨晚沿校园走了一段。",
        "retrieval_text": "retrieval:target-result",
        "occurred_from": NOW,
        "occurred_to": None,
        "status": "active",
        "authority": "world_fact",
        "epistemic_scope": "world_fact",
        "speaker_ref": None,
    })


def _distractor_document():
    return _documents()[2].model_copy(update={
        "document_id": "recall:distractor:campus",
        "source_item_ref": "fact:distractor:campus",
        "source_refs": ("event:fact:distractor:campus",),
        "source_bindings": _bindings("event:fact:distractor:campus", revision=12),
        "source_world_revision": 12,
        "text": "用户问：你昨晚在校园做什么？",
        "retrieval_text": QUERY + " retrieval",
        "link_refs": (),
    })


def _diagnosis(*, target_id: str, diagnostic) -> str:
    if target_id not in diagnostic.corpus_document_ids:
        return "fact_absent_from_recall_corpus"
    if target_id not in diagnostic.eligible_document_ids:
        return "excluded_by_recall_scope_or_status"
    if target_id not in diagnostic.matched_candidate_ids:
        return "retrieval_candidate_missed"
    if target_id not in diagnostic.selected_document_ids:
        return "rank_or_selection_excluded"
    return "selected_for_context_transport"


def test_absent_fact_and_retrieval_miss_are_distinct():
    target = _target_document()

    absent_index = InMemoryRecallIndex(embedding=_StageEmbedding())
    absent_index.rebuild(cursor=STAGE_CURSOR, documents=())
    _absent_result, absent = absent_index.snapshot().search_with_diagnostics(
        _query(query_text=QUERY, limit=8, cursor=STAGE_CURSOR)
    )
    assert _diagnosis(target_id=TARGET_ID, diagnostic=absent) == "fact_absent_from_recall_corpus"

    historical_index = InMemoryRecallIndex(embedding=_StageEmbedding())
    historical_index.rebuild(
        cursor=STAGE_CURSOR,
        documents=(target.model_copy(update={"status": "superseded"}),),
    )
    _historical_result, historical = historical_index.snapshot().search_with_diagnostics(
        _query(query_text=QUERY, limit=8, cursor=STAGE_CURSOR)
    )
    assert TARGET_ID in historical.corpus_document_ids
    assert _diagnosis(target_id=TARGET_ID, diagnostic=historical) == "excluded_by_recall_scope_or_status"

    missed_index = InMemoryRecallIndex(embedding=_StageEmbedding(target=(0.0, 1.0)))
    missed_index.rebuild(cursor=STAGE_CURSOR, documents=(target,))
    _missed_result, missed = missed_index.snapshot().search_with_diagnostics(
        _query(query_text=QUERY, limit=8, cursor=STAGE_CURSOR)
    )
    assert TARGET_ID in missed.corpus_document_ids
    assert TARGET_ID in missed.eligible_document_ids
    assert _diagnosis(target_id=TARGET_ID, diagnostic=missed) == "retrieval_candidate_missed"


def test_rank_selection_and_context_transport_are_separate():
    target, distractor = _target_document(), _distractor_document()
    index = InMemoryRecallIndex(embedding=_StageEmbedding())
    index.rebuild(cursor=STAGE_CURSOR, documents=(target, distractor))
    snapshot = index.snapshot()

    wide_result, wide = snapshot.search_with_diagnostics(
        _query(query_text=QUERY, limit=8, cursor=STAGE_CURSOR)
    )
    narrow_result, narrow = snapshot.search_with_diagnostics(
        _query(query_text=QUERY, limit=1, cursor=STAGE_CURSOR)
    )
    assert _diagnosis(target_id=TARGET_ID, diagnostic=wide) == "selected_for_context_transport"
    assert wide.ranked_candidate_ids[0] == distractor.document_id
    assert wide.ranked_candidate_ids[1] == TARGET_ID
    assert _diagnosis(target_id=TARGET_ID, diagnostic=narrow) == "rank_or_selection_excluded"
    assert TARGET_ID in narrow.ranked_candidate_ids
    assert TARGET_ID not in narrow.selected_document_ids
    assert [hit.document.document_id for hit in wide_result.hits] == list(wide.selected_document_ids)
    assert [hit.document.document_id for hit in narrow_result.hits] == list(narrow.selected_document_ids)

    coordinator = RecallCoordinator.from_built_index(
        index=index,
        cursor=STAGE_CURSOR,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        logical_time=NOW,
        trigger_ref="event:trigger:grounding-stage",
    )
    try:
        trace = verify_trusted_recall_trace(coordinator.recall(
            request=CharacterRecallRequest(query_text=QUERY, limit=8),
            expected_cursor=STAGE_CURSOR,
            trigger_ref="event:trigger:grounding-stage",
            accessibility_seed="grounding-stage-control",
        ))
        displayed = json.loads(recall_evidence_json(trace))
        recalled = next(item for item in displayed["candidates"] if item["source_refs"] == [TARGET_REF])
        assert recalled["source_refs"] == [TARGET_REF]
        assert recalled["status"] == "active"
        assert recalled["authority"] == "world_fact"
        assert recalled["epistemic_scope"] == "world_fact"
        assert recalled["occurred_from"] == NOW.isoformat()

        base_context = json.dumps({
            "world_id": "world:test",
            "actor_ref": "agent:companion",
            "trigger_ref": "event:trigger:grounding-stage",
            "world_revision": STAGE_CURSOR.world_revision,
            "logical_time": NOW.isoformat(),
            "slices": {},
        }, ensure_ascii=False)
        augmented = json.loads(augment_model_content_with_recall(base_context, trace))
        transported = next(
            item for item in augmented["slices"]["recent_experiences"]["items"]
            if item["item_ref"] == target.source_item_ref
        )
        assert transported["source_bindings"]
        assert transported["value"]["source_refs"] == [TARGET_REF]
        assert transported["value"]["status"] == "active"
        assert transported["value"]["authority"] == "world_fact"
        compacted = json.loads(compact_chat_model_facing_context(json.dumps(augmented, ensure_ascii=False)))
        compacted_item = next(
            item for item in compacted["slices"]["recent_experiences"]["items"]
            if item["source_ref"] == target.source_item_ref
        )
        assert compacted_item["source_ref"] == target.source_item_ref
        assert compacted_item["value"]["source_refs"] == [TARGET_REF]
        assert compacted_item["value"]["status"] == "active"
        assert compacted_item["value"]["epistemic_scope"] == "world_fact"
    finally:
        coordinator.close()
