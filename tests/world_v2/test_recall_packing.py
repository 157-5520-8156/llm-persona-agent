"""Budget source proof independently from the reading the character receives."""

import json
from companion_daemon.world_v2.recall_index import (
    InMemoryRecallIndex,
    RecallSourceBinding,
    RECALL_MODEL_READING_MAX_BYTES,
    RECALL_RESULT_MAX_BYTES,
)
from companion_daemon.world_v2.recall_model_reading import interior_recall_item
from companion_daemon.world_v2.recall_runtime import RecallCoordinator, verify_trusted_recall_trace
from companion_daemon.world_v2.recall_audit import (
    CharacterRecallRequest,
    RecallAuditTrace,
    MAX_RECALL_AUDIT_BYTES,
)
from test_recall_index import _documents, _query, CURSOR, NOW
from test_recall_short_cues import LexicalOnly


def byte_size(value):
    return len(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def document(identifier, *, text="一段旧记忆。", proofs=4, wide=False):
    refs = tuple(
        sorted(
            f"event:{identifier}:{i}:" + ("界" * 210 if wide else "x" * 90) for i in range(proofs)
        )
    )
    return _documents()[0].model_copy(
        update={
            "document_id": identifier,
            "source_item_ref": f"item:{identifier}",
            "text": text,
            "retrieval_text": None,
            "link_refs": (),
            "source_refs": refs,
            "source_bindings": tuple(
                RecallSourceBinding(
                    source_kind="committed_event",
                    authority_type="FixtureEvent",
                    ref=ref,
                    source_world_revision=7,
                    immutable_hash="1" * 64,
                )
                for ref in refs
            ),
        }
    )


def test_four_small_readings_keep_every_source_proof_and_replay_audit():
    docs = tuple(document(f"memory:{i}") for i in range(4))
    index = InMemoryRecallIndex(embedding=LexicalOnly())
    index.rebuild(cursor=CURSOR, documents=docs)
    co = RecallCoordinator.from_built_index(
        index=index,
        cursor=CURSOR,
        actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"),
        logical_time=NOW,
        trigger_ref="trigger:packing",
    )
    try:
        trace = verify_trusted_recall_trace(
            co.recall(
                request=CharacterRecallRequest(query_text="旧记忆", limit=4),
                expected_cursor=CURSOR,
                trigger_ref="trigger:packing",
                accessibility_seed="packing",
            )
        )
        assert len(trace.hits) == 4
        assert {h.document for h in trace.hits} == set(docs)
        encoded_hits = [h.model_dump(mode="json") for h in trace.hits]
        assert 6000 < byte_size(encoded_hits) <= RECALL_RESULT_MAX_BYTES
        assert (
            byte_size({"items": [interior_recall_item(h.document) for h in trace.hits]})
            <= RECALL_MODEL_READING_MAX_BYTES
        )
        assert byte_size(trace.model_dump(mode="json")) <= MAX_RECALL_AUDIT_BYTES
        assert RecallAuditTrace.model_validate_json(trace.model_dump_json()) == trace
    finally:
        co.close()


def test_utf8_reading_limit_still_excludes_large_text_despite_available_audit_space():
    docs = tuple(document(f"long:{i}", text="记忆" + "雨" * 1100, proofs=1) for i in range(4))
    index = InMemoryRecallIndex(embedding=LexicalOnly())
    index.rebuild(cursor=CURSOR, documents=docs)
    result = index.search(_query(query_text="记忆", limit=4))
    assert len(result.hits) == 1
    reading = {"items": [interior_recall_item(h.document) for h in result.hits]}
    assert byte_size(reading) <= RECALL_MODEL_READING_MAX_BYTES
    assert (
        byte_size({"items": [interior_recall_item(d) for d in docs[:2]]})
        > RECALL_MODEL_READING_MAX_BYTES
    )


def test_audit_cap_does_not_strip_proof_to_make_a_tiny_reading_fit():
    huge = document("huge-proof", proofs=16, wide=True)
    small = document("small-proof", proofs=1)
    assert byte_size(interior_recall_item(huge)) < RECALL_MODEL_READING_MAX_BYTES
    assert byte_size(huge.model_dump(mode="json")) > RECALL_RESULT_MAX_BYTES
    index = InMemoryRecallIndex(embedding=LexicalOnly())
    index.rebuild(cursor=CURSOR, documents=(huge, small))
    result = index.search(_query(query_text="旧记忆", limit=4))
    assert [h.document for h in result.hits] == [small]


def test_attribution_bytes_participate_in_the_reading_bound():
    subjects = tuple(f"user:{i}:" + "界" * 170 for i in range(8))
    docs = tuple(document(f"attributed:{i}", proofs=1).model_copy(update={
        "subject_refs": subjects,
    }) for i in range(2))
    assert byte_size({"items": [interior_recall_item(
        doc, index_version="world-v2-recall-index.hybrid.7",
    ) for doc in docs]}) < RECALL_MODEL_READING_MAX_BYTES
    assert byte_size({"items": [interior_recall_item(doc) for doc in docs]}) > (
        RECALL_MODEL_READING_MAX_BYTES
    )
    index = InMemoryRecallIndex(embedding=LexicalOnly())
    index.rebuild(cursor=CURSOR, documents=docs)
    result = index.search(_query(query_text="旧记忆", limit=4, subject_refs=subjects))
    assert len(result.hits) == 1
    assert byte_size({"items": [interior_recall_item(
        hit.document, index_version=result.index_version,
    ) for hit in result.hits]}) <= RECALL_MODEL_READING_MAX_BYTES
