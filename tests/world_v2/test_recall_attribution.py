"""Recall attribution is source data, including at the native author boundary."""

import json
from types import SimpleNamespace

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.character_interior import CharacterInterior
from companion_daemon.world_v2.character_interior.inbound_author import _InboundCharacterAuthor
from companion_daemon.world_v2.character_interior.ports import _PrefetchResult, _RecallResult
from companion_daemon.world_v2.character_interior.production import _CoordinatorRecallPort
from companion_daemon.world_v2.character_interior.snapshot_compiler import compile_inner_life_snapshot
from companion_daemon.world_v2.recall_corpus import RecallCorpusCompiler
from companion_daemon.world_v2.recall_audit import CharacterRecallRequest
from companion_daemon.world_v2.recall_index import InMemoryRecallIndex
from companion_daemon.world_v2.recall_model_reading import interior_recall_item
from companion_daemon.world_v2.recall_runtime import RecallCoordinator
from companion_daemon.world_v2.schemas import ProjectionCursor
from test_character_interior_inbound_author import _request
from test_recall_corpus import CURSOR, NOW, _sources
from test_recall_short_cues import LexicalOnly
from test_recalled_fact_payload import EXCERPT, VALUE, _document, _fact


def _documents():
    return tuple(document for document in RecallCorpusCompiler().compile(
        cursor=CURSOR, actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"), sources=_sources(),
    ) if document.source_item_ref in {"fact:tea-preference", "dialogue:observation:message-1"})


@pytest.mark.parametrize("version", [
    "world-v2-recall-index.hybrid.7+embedding:fixture.1", "recall-index:legacy",
])
def test_saved_legacy_reading_has_exact_original_shape(version):
    for document in _documents():
        assert interior_recall_item(document, index_version=version) == {
            "source_ref": document.source_item_ref,
            "memory_kind": document.memory_kind,
            "source_slice": document.source_slice,
            "authority": document.authority,
            "epistemic_scope": document.effective_epistemic_scope,
            "text": document.text,
            "occurred_from": document.occurred_from.isoformat(),
            "occurred_to": None,
            "privacy_class": document.privacy_class,
        }


@pytest.mark.parametrize("policy", [
    "world-v2-recall-index.hybrid.7", "world-v2-recall-index.hybrid.8",
])
def test_legacy_historical_reading_and_inventory_keep_original_compound_ref(monkeypatch, policy):
    from companion_daemon.world_v2 import recall_index

    monkeypatch.setattr(recall_index, "RECALL_INDEX_POLICY_VERSION", policy)
    document = _document(_fact(historical=True, bound=False))
    assert "accepted_fact" not in document.model_dump()
    index = InMemoryRecallIndex(embedding=LexicalOnly())
    index.rebuild(cursor=CURSOR, documents=(document,))
    coordinator = RecallCoordinator.from_built_index(
        index=index, cursor=CURSOR, actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"), logical_time=NOW,
        trigger_ref="event:observation:1",
    )
    try:
        trace = coordinator.recall(
            request=CharacterRecallRequest(query_text=VALUE, include_historical=True),
            expected_cursor=CURSOR, trigger_ref="event:observation:1", accessibility_seed="old-reading",
        )
        snapshot = compile_inner_life_snapshot({
            "world_id": "world:test", "actor_ref": "agent:companion",
            "trigger_ref": "event:observation:1", "logical_time": NOW.isoformat(),
            **CURSOR.model_dump(), "slices": {},
        })
        recalled = _RecallResult.model_validate(_CoordinatorRecallPort._trace_result(
            trace, request=SimpleNamespace(world_id=snapshot.world_id,
                actor_ref=snapshot.actor_ref, cursor=snapshot.cursor), trace_field="recall_trace_json",
        ))
        assert recalled.source_refs == (document.source_item_ref,)
        reading = recalled.content["items"][0]
        assert reading["source_ref"] == document.source_item_ref
        assert reading["text"] == EXCERPT and "accepted_fact" not in reading
        assert ("subject_refs" in reading) == policy.endswith(".8")
        merged = CharacterInterior._merge_recall(snapshot, recalled)
        assert document.source_item_ref in merged.source_refs
        assert _fact().accepted_fact_event_ref not in merged.source_refs
    finally:
        coordinator.close()


@pytest.mark.asyncio
async def test_coordinator_core_reading_keeps_fact_subject_and_dialogue_speaker_in_native_prompt(
    monkeypatch,
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    documents = _documents()
    index = InMemoryRecallIndex(embedding=LexicalOnly())
    index.rebuild(cursor=CURSOR, documents=documents)
    coordinator = RecallCoordinator.from_built_index(
        index=index, cursor=CURSOR, actor_ref="agent:companion",
        subject_refs=("agent:companion", "user:primary"), logical_time=NOW,
        trigger_ref="event:observation:1",
    )
    calls = []

    class Captured(BaseException):
        pass

    async def capture(request):
        calls.append(json.loads(request.content))
        raise Captured

    model = DeepSeekChatModel(
        "offline-fixture", "https://fixture.invalid", "deepseek-v4-flash",
        thinking_enabled=False, transport=httpx.MockTransport(capture),
    )
    try:
        trace = coordinator.prefetch(
            expected_cursor=CURSOR, query_text="盖碗泡凤凰单丛", accessibility_seed="attribution",
            trigger_ref="event:observation:1", limit=4,
        )
        context = {
            "world_id": "world:test", "actor_ref": "agent:companion",
            "trigger_ref": "event:observation:1", "logical_time": NOW.isoformat(),
            **CURSOR.model_dump(), "slices": {},
        }
        snapshot = compile_inner_life_snapshot(context)
        prefetched = _PrefetchResult.model_validate(_CoordinatorRecallPort._trace_result(
            trace, request=SimpleNamespace(
                world_id="world:test", actor_ref="agent:companion",
                cursor=ProjectionCursor(**CURSOR.model_dump()),
            ), trace_field="prefetch_trace_json",
        ))
        merged = CharacterInterior._merge_prefetch(snapshot, prefetched)
        context["inner_life_snapshot"] = merged.model_view()
        author = _InboundCharacterAuthor(flash_model=model, whole_candidate_mode=True)
        request = _request(revision=CURSOR.world_revision, call="call:attribution").model_copy(
            update={"model_content_json": json.dumps(context, ensure_ascii=False),
                    "evaluated_deliberation_revision": CURSOR.deliberation_revision,
                    "evaluated_ledger_sequence": CURSOR.ledger_sequence},
        )
        with pytest.raises(Captured):
            await author._propose_appraisal(request)
        assert len(calls) == 1
        shown = json.loads(calls[0]["messages"][1]["content"])
        items = shown["inner_life_snapshot"]["materials"]["automatic_prefetch"]["items"]
        by_ref = {item["source_ref"]: item for item in items}
        assert set(by_ref) == {document.source_item_ref for document in documents}
        for document in documents:
            item = by_ref[document.source_item_ref]
            assert item["subject_refs"] == ["user:primary"]
            assert item["text"] == document.text
            assert item["epistemic_scope"] == document.effective_epistemic_scope
            if document.speaker_ref:
                assert item["speaker_ref"] == "user:primary"
            else:
                assert "speaker_ref" not in item
        assert merged.snapshot_hash != snapshot.snapshot_hash
        assert set(prefetched.source_refs) <= set(merged.source_refs)
    finally:
        coordinator.close()
        await model.aclose()
