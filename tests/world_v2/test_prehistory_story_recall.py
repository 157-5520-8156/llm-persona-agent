"""Accepted story links expand actor-owned Recall without bypassing Memory."""
from __future__ import annotations

from datetime import datetime, UTC
import json

import pytest

from companion_daemon.world_v2.character_prehistory import (
    ReviewedPrehistoryArchive,
    digest,
)
from companion_daemon.world_v2.character_prehistory_runtime import PrehistoryArchiveRuntime
from companion_daemon.world_v2.prehistory_memory_source import prehistory_memory_binding
from companion_daemon.world_v2.recall_index import (
    FeatureHashRecallEmbedding,
    InMemoryRecallIndex,
    RecallCursor,
    RecallDocument,
    RecallSourceBinding,
    RecallQuery,
)
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_character_prehistory import ACTOR, START, reviewed_archive, started_ledger
from test_prehistory_memory_source import _choice, _corpus, _read


def _linked_archive():
    original = reviewed_archive()
    source = original.document.records[0]
    first_id = "prehistory-record:blue-umbrella"
    second_id = "prehistory-record:quiet-video"
    third_id = "prehistory-record:storage-box"
    fourth_id = "prehistory-record:moving-boxes"
    fifth_id = "prehistory-record:photo-reuse-message"
    first = source.model_copy(update={
        "record_id": first_id,
        "occurred_from": datetime(2023, 4, 1, tzinfo=UTC),
        "occurred_until": datetime(2023, 4, 30, 23, 59, 59, tzinfo=UTC),
        "time_precision": "month",
        "statement": "放学回家路上，她把蓝雨伞忘在走廊窗台。校刊同学追上来塞给她，两个人一路笑到车站。",
        "related_record_refs": (second_id,),
    })
    second = source.model_copy(update={
        "record_id": second_id,
        "occurred_from": datetime(2023, 5, 1, tzinfo=UTC),
        "occurred_until": datetime(2023, 5, 31, 23, 59, 59, tzinfo=UTC),
        "time_precision": "month",
        "statement": "学校发言时她念错一行，回家后一直不高兴。校刊同学只发来开头顺畅的二十秒视频；她私下反复看了几遍。",
        "related_record_refs": (first_id, third_id),
    })
    third = source.model_copy(update={
        "record_id": third_id,
        "occurred_from": datetime(2024, 8, 1, tzinfo=UTC),
        "occurred_until": datetime(2024, 8, 31, 23, 59, 59, tzinfo=UTC),
        "time_precision": "month",
        "statement": "大学开学前，嘉禾陪她挑收纳用品，两个人买回一个放不进床底的箱子。",
        "related_record_refs": (second_id, fourth_id),
    })
    fourth = source.model_copy(update={
        "record_id": fourth_id,
        "occurred_from": datetime(2025, 5, 1, tzinfo=UTC),
        "occurred_until": datetime(2025, 5, 31, 23, 59, 59, tzinfo=UTC),
        "time_precision": "month",
        "statement": "嘉禾搬东西那天，她帮忙把纸箱抬上车。",
        "related_record_refs": (third_id, fifth_id),
    })
    fifth = source.model_copy(update={
        "record_id": fifth_id,
        "occurred_from": datetime(2025, 6, 1, tzinfo=UTC),
        "occurred_until": datetime(2025, 6, 30, 23, 59, 59, tzinfo=UTC),
        "time_precision": "month",
        "statement": "后来嘉禾说她用过那张照片，顾客问起时自己没告诉她。她回了句“你早说嘛”。",
        "related_record_refs": (fourth_id,),
    })
    document = original.document.model_copy(update={"records": (first, second, third, fourth, fifth)})
    return ReviewedPrehistoryArchive(
        document=document,
        review=original.review.model_copy(update={"manifest_hash": digest(document.manifest())}),
    )


def _active_rows(ledger: SQLiteWorldLedger, archive):
    rows = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(
        archive,
        created_at=START,
    )
    active = {}
    for row in rows:
        source = prehistory_memory_binding(row)
        candidate_id = f"memory:{row.record.record_id}"
        pending = _choice(ledger, source, candidate_id=candidate_id)
        active[row.record.record_id] = _choice(
            ledger,
            source,
            before=pending,
            status="active",
            candidate_id=candidate_id,
        )
    return active


def _query(ledger: SQLiteWorldLedger, *, actor_ref=ACTOR, occurred_to=None, limit=6):
    projection = ledger.project()
    cursor = RecallCursor(
        world_revision=projection.world_revision,
        deliberation_revision=projection.deliberation_revision,
        ledger_sequence=projection.ledger_sequence,
    )
    return RecallQuery(
        query_text="蓝雨伞走廊窗台",
        cursor=cursor,
        actor_ref=actor_ref,
        subject_refs=(actor_ref,),
        viewer_privacy_ceiling="private",
        at=projection.logical_time,
        occurred_to=occurred_to,
        accessibility_seed="fixture:linked-story-recall",
        limit=limit,
    )


def test_recall_expands_one_reviewed_linked_scene_into_character_context(tmp_path):
    from companion_daemon.world_v2.ledger_context_resolver import (
        _relevant_prehistory_memory_link_refs,
        memory_relevance_bp,
    )

    ledger = started_ledger(tmp_path / "linked-story.sqlite")
    try:
        active = _active_rows(ledger, _linked_archive())
        retrieval = _read(ledger)
        documents = _corpus(ledger, retrieval)
        attended_memory = next(
            item for item in retrieval.items
            if any(excerpt.source_id == "prehistory-record:photo-reuse-message"
                   for excerpt in item.source_excerpts)
        )
        story_query_text = "嘉禾又问她能不能把那张活动照拿去店里宣传，这次怎么回？"
        logical_time = ledger.project().logical_time
        assert _relevant_prehistory_memory_link_refs(
            (attended_memory,), actor_ref=ACTOR, logical_time=logical_time,
            query_text=story_query_text,
        ) == (attended_memory.candidate_id,)
        story_memories = tuple(
            item for item in retrieval.items
            if any(
                excerpt.prehistory is not None
                and excerpt.prehistory.actor_ref == ACTOR
                for excerpt in item.source_excerpts
            )
        )
        assert len(story_memories) > 1
        assert _relevant_prehistory_memory_link_refs(
            story_memories,
            actor_ref=ACTOR,
            logical_time=logical_time,
            query_text=story_query_text,
        ) == (attended_memory.candidate_id,)
        lexical_misses = tuple(
            item for item in story_memories
            if memory_relevance_bp(story_query_text, item) == 0
        )
        assert lexical_misses
        assert not _relevant_prehistory_memory_link_refs(
            lexical_misses,
            actor_ref=ACTOR,
            logical_time=logical_time,
            query_text=story_query_text,
        )
        assert not _relevant_prehistory_memory_link_refs(
            (attended_memory,), actor_ref="agent:another-character",
            logical_time=logical_time, query_text=story_query_text,
        )
        withheld = attended_memory.model_copy(update={"privacy_ceiling": "withhold"})
        assert not _relevant_prehistory_memory_link_refs(
            (withheld,), actor_ref=ACTOR, logical_time=logical_time,
            query_text=story_query_text,
        )
        # The prefetch requests four direct results. Its linked closure must
        # still carry the five-scene story through to its later outcome.
        query = _query(ledger, limit=4)
        index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
        index.rebuild(cursor=query.cursor, documents=documents)

        result = index.search(query)

        assert any("蓝雨伞" in hit.document.text for hit in result.hits)
        linked = next(hit for hit in result.hits if "二十秒视频" in hit.document.text)
        assert "structured" in linked.match_channels
        assert linked.document.prehistory is not None
        assert linked.document.prehistory.actor_ref == ACTOR
        assert any("收纳用品" in hit.document.text for hit in result.hits)
        assert any("你早说嘛" in hit.document.text for hit in result.hits), (
            "ordinary Recall should reach the final scene without widening "
            "actor, time, or forgotten-memory authority"
        )
        assert any("抬上车" in hit.document.text for hit in result.hits), (
            "ordinary Recall should preserve the complete bounded story link, "
            "including its later outcome"
        )
        assert all(len(hit.document.source_bindings) == 2 for hit in result.hits)

        # If the character has already attended to one retained scene, the
        # ordinary retrieval link selector should be able to anchor its
        # source-closed story closure even when the new wording does not
        # repeat the remembered scene's distinctive terms.
        attended = active["prehistory-record:photo-reuse-message"]
        indirect_query = query.model_copy(update={
            "query_text": story_query_text,
            "link_refs": (attended.candidate_id,),
        })
        indirect = index.search(indirect_query)
        assert {hit.document.source_item_ref for hit in indirect.hits} == {
            "prehistory-record:blue-umbrella",
            "prehistory-record:quiet-video",
            "prehistory-record:storage-box",
            "prehistory-record:moving-boxes",
            "prehistory-record:photo-reuse-message",
        }
        assert any("structured" in hit.match_channels for hit in indirect.hits)

        # The same result shape is what a normal character-selected recall
        # supplies to the next CharacterInterior author call.
        from companion_daemon.world_v2.recall_audit import CharacterRecallRequest
        from companion_daemon.world_v2.recall_runtime import (
            RecallCoordinator,
            augment_model_content_with_recall,
            verify_trusted_recall_trace,
        )

        coordinator = RecallCoordinator.from_built_index(
            index=index,
            cursor=query.cursor,
            actor_ref=ACTOR,
            subject_refs=(ACTOR,),
            logical_time=query.at,
            trigger_ref="fixture:story-recall",
        )
        trusted = coordinator.recall(
            request=CharacterRecallRequest(query_text=query.query_text, limit=query.limit),
            accessibility_seed=query.accessibility_seed,
            expected_cursor=query.cursor,
            trigger_ref="fixture:story-recall",
        )
        audit = verify_trusted_recall_trace(trusted)
        assert any("二十秒视频" in hit.document.text for hit in audit.hits)
        assert any("抬上车" in hit.document.text for hit in audit.hits)
        assert any("你早说嘛" in hit.document.text for hit in audit.hits)
        augmented = json.loads(augment_model_content_with_recall('{"slices":{}}', audit))
        coordinator.close()
        visible = augmented["slices"]["active_memory_candidates"]["items"]
        assert any("二十秒视频" in item["value"]["text"] for item in visible)
        assert any("抬上车" in item["value"]["text"] for item in visible)
        assert any("你早说嘛" in item["value"]["text"] for item in visible)

        # Explicitly narrowed time and actor scope are applied before link
        # expansion. A relation never widens the current Recall authority.
        earlier = query.model_copy(update={"occurred_to": query.at.replace(year=2023, month=4, day=30)})
        early_result = index.search(earlier)
        assert any("蓝雨伞" in hit.document.text for hit in early_result.hits)
        assert all("二十秒视频" not in hit.document.text for hit in early_result.hits)
        assert all("你早说嘛" not in hit.document.text for hit in early_result.hits)
        other_actor = query.model_copy(update={"actor_ref": "actor:other", "subject_refs": ("actor:other",)})
        assert not index.search(other_actor).hits

        # Recall expansion only sees current Memory candidates. Removing the
        # linked record from that read view cannot be bypassed by the immutable
        # archive graph (the real forgotten-state path is covered by the
        # prehistory memory source lifecycle suite).
        projection = ledger.project()
        second_active = next(
            candidate for candidate in projection.memory_candidates
            if candidate.values.status == "active"
            and any(binding.source_id == "prehistory-record:quiet-video"
                    for binding in candidate.values.source_bindings)
        )
        after_forget = _read(
            ledger,
            candidates=tuple(item for item in projection.memory_candidates
                             if item.candidate_id != second_active.candidate_id),
        )
        after_documents = _corpus(ledger, after_forget)
        query_after = _query(ledger)
        index.rebuild(cursor=query_after.cursor, documents=after_documents)
        after_result = index.search(query_after)
        assert any("蓝雨伞" in hit.document.text for hit in after_result.hits)
        assert all("二十秒视频" not in hit.document.text for hit in after_result.hits)
        assert all("你早说嘛" not in hit.document.text for hit in after_result.hits)
    finally:
        ledger.close()


def test_source_linked_story_anchor_survives_unrelated_direct_top_k(tmp_path):
    class _DistractorDominantEmbedding:
        version = "story-anchor-dominance.1"
        dimensions = 2
        dense_match_threshold_bp = 4_200

        def embed(self, texts):
            return tuple(
                (1.0, 0.0)
                if text in {"今天想喝水", "今天想喝水。"}
                else (0.0, 1.0)
                for text in texts
            )

    ledger = started_ledger(tmp_path / "story-anchor-top-k.sqlite")
    try:
        active = _active_rows(ledger, _linked_archive())
        retrieval = _read(ledger)
        story_documents = _corpus(ledger, retrieval)
        query = _query(ledger, limit=4).model_copy(update={
            "query_text": "今天想喝水",
            "lexical_text": "今天想喝水",
            "link_refs": (active["prehistory-record:photo-reuse-message"].candidate_id,),
            "subject_refs": tuple(sorted((ACTOR, "user:primary"))),
        })
        cursor = query.cursor
        distractor_kinds = (
            ("fact", "semantic", "relevant_facts", "world_fact", "world_fact"),
            ("dialogue", "episodic", "recent_dialogue", "dialogue_record", "counterpart_report_only"),
            ("impression", "reflective", "private_impressions", "defeasible_interpretation", "private_interpretation"),
            ("memory", "episodic", "active_memory_candidates", "world_fact", "world_fact"),
        )
        distractors = tuple(
            RecallDocument(
                document_id=f"recall:distractor:{kind}",
                memory_kind=memory_kind,
                source_item_ref=f"{kind}:distractor:{index}",
                source_slice=source_slice,
                source_refs=(f"event:distractor:{kind}:{index}",),
                source_bindings=(RecallSourceBinding(
                    source_kind="committed_event",
                    authority_type="FixtureEvent",
                    ref=f"event:distractor:{kind}:{index}",
                    source_world_revision=cursor.world_revision,
                    immutable_hash=f"{index + 1:x}" * 64,
                ),),
                source_world_revision=cursor.world_revision,
                text="今天想喝水。今天想喝水。",
                actor_ref=ACTOR,
                subject_refs=tuple(sorted((ACTOR, "user:primary"))),
                occurred_from=query.at,
                privacy_class="personal",
                authority=authority,
                epistemic_scope=epistemic_scope,
                speaker_ref=("user:primary" if source_slice == "recent_dialogue" else None),
            )
            for index, (kind,memory_kind,source_slice,authority,epistemic_scope) in enumerate(distractor_kinds)
        )
        index = InMemoryRecallIndex(embedding=_DistractorDominantEmbedding())
        index.rebuild(cursor=cursor, documents=(*story_documents, *distractors))

        result = index.search(query)

        story_ids = {f"prehistory-record:{name}" for name in (
            "blue-umbrella", "quiet-video", "storage-box", "moving-boxes",
            "photo-reuse-message",
        )}
        hit_ids = {hit.document.source_item_ref for hit in result.hits}
        assert story_ids <= hit_ids
        assert all(
            hit.document.prehistory is None or len(hit.document.source_bindings) == 2
            for hit in result.hits
        )
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_pinned_context_seeds_automatic_recall_from_relevant_actor_story_memory(
    tmp_path, monkeypatch,
):
    from companion_daemon.world_v2.context_resolver import query_from_projection
    from companion_daemon.world_v2.ledger_context_resolver import context_capsule_compiler_from_ledger
    from companion_daemon.world_v2.recall_runtime import RecallCoordinator, verify_trusted_recall_trace
    import test_ledger_context_resolver as context_resolver_test

    ledger = started_ledger(tmp_path / "attended-story-anchor.sqlite")
    recall = RecallCoordinator(index=InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding()))
    try:
        _active_rows(ledger, _linked_archive())
        before = ledger.project()
        monkeypatch.setattr(context_resolver_test, "NOW", before.logical_time)
        question = "嘉禾又问你能不能把那张活动照拿去店里宣传，这次你会怎么回她？"
        message = context_resolver_test._message_observation(
            ledger.world_id,
            991,
            question,
            received_at=before.logical_time,
        )
        ledger.commit(
            (message,),
            expected_world_revision=before.world_revision,
            expected_deliberation_revision=before.deliberation_revision,
        )
        projection = ledger.project()
        context_capsule_compiler_from_ledger(
            ledger=ledger,
            recall_coordinator=recall,
        ).compile(query_from_projection(
            projection,
            actor_ref=ACTOR,
            trigger_ref=message.event_id,
        ))

        active_story_candidate_ids = {
            item.candidate_id
            for item in _read(ledger).items
            if any(
                excerpt.prehistory is not None
                and excerpt.prehistory.actor_ref == ACTOR
                for excerpt in item.source_excerpts
            )
        }
        assert active_story_candidate_ids
        cursor = RecallCursor(
            world_revision=projection.world_revision,
            deliberation_revision=projection.deliberation_revision,
            ledger_sequence=projection.ledger_sequence,
        )
        trace = await recall.await_scheduled_prefetch(
            expected_cursor=cursor,
            trigger_ref=message.event_id,
            timeout_seconds=1.0,
        )
        assert trace is not None
        audit = verify_trusted_recall_trace(trace)
        story_link_refs = set(audit.request.link_refs) & active_story_candidate_ids
        assert len(story_link_refs) == 1
        assert story_link_refs <= active_story_candidate_ids
        assert {hit.document.source_item_ref for hit in audit.hits} == {
            "prehistory-record:blue-umbrella",
            "prehistory-record:quiet-video",
            "prehistory-record:storage-box",
            "prehistory-record:moving-boxes",
            "prehistory-record:photo-reuse-message",
        }
        assert all(len(hit.document.source_bindings) == 2 for hit in audit.hits)
    finally:
        recall.close()
        ledger.close()


def test_unreviewed_character_recall_can_cite_presented_previously_reviewed_scenes(tmp_path):
    from test_prehistory_chat_context import _capsule
    from test_visible_source_composer import _request
    from companion_daemon.world_v2.recall_audit import CharacterRecallRequest
    from companion_daemon.world_v2.recall_runtime import (
        RecallCoordinator,
        augment_model_content_with_recall,
        verify_trusted_recall_trace,
    )
    from companion_daemon.world_v2.prehistory_claim_authority import prehistory_claim_bindings
    from companion_daemon.world_v2 import expression_draft as expression
    from companion_daemon.world_v2.character_interior.inbound_author import (
        _with_visible_recall_traces,
    )

    ledger = started_ledger(tmp_path / "unreviewed-story-recall.sqlite")
    coordinator = None
    try:
        _active_rows(ledger, _linked_archive())
        capsule = _capsule(ledger, actor=ACTOR, trigger="fixture:unreviewed-story-recall")
        request = _request(capsule)
        retrieval = _read(ledger)
        documents = _corpus(ledger, retrieval)
        query = _query(ledger, limit=4)
        index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
        index.rebuild(cursor=query.cursor, documents=documents)
        coordinator = RecallCoordinator.from_built_index(
            index=index,
            cursor=query.cursor,
            actor_ref=ACTOR,
            subject_refs=(ACTOR,),
            logical_time=query.at,
            trigger_ref=request.trigger_ref,
        )
        trusted = coordinator.recall(
            request=CharacterRecallRequest(query_text=query.query_text, limit=query.limit),
            accessibility_seed=query.accessibility_seed,
            expected_cursor=query.cursor,
            trigger_ref=request.trigger_ref,
        )
        assert trusted is not None
        audit = verify_trusted_recall_trace(trusted)
        visible = augment_model_content_with_recall(
            request.model_content_json,
            audit,
        )
        visible_value = json.loads(visible)
        visible_value["slices"].pop("active_memory_candidates", None)
        snapshot = visible_value.setdefault("inner_life_snapshot", {})
        candidates = []
        evidence = json.loads(__import__(
            "companion_daemon.world_v2.recall_runtime", fromlist=["recall_evidence_json"],
        ).recall_evidence_json(audit))
        for candidate, hit in zip(evidence["candidates"], audit.hits, strict=True):
            if hit.document.prehistory is not None:
                candidates.append({**candidate, "source_ref": hit.document.source_item_ref})
        snapshot["materials"] = {
            "contract": "shared-string-view.1",
            "prefix": "@s:",
            "strings": {},
            "value": {"automatic_prefetch": {"items": candidates}},
        }
        visible = json.dumps(visible_value, ensure_ascii=False)
        unreviewed = _with_visible_recall_traces(request.model_copy(update={
            "model_content_json": visible,
            "visible_source_requirement_json": None,
        }), trusted)

        historical = prehistory_claim_bindings(unreviewed)
        final_record_id = "prehistory-record:blue-umbrella"
        assert final_record_id in historical
        context = json.loads(visible)
        allowed = expression.world_claim_source_refs_by_scope(context=context, request=unreviewed)
        assert final_record_id in allowed["past_world"]
        from companion_daemon.world_v2.model_facing_context import compact_chat_model_facing_context
        provider_request = unreviewed.model_copy(update={
            "model_content_json": compact_chat_model_facing_context(visible),
        })
        manifest = expression.expression_hard_boundary_manifest(
            request=provider_request,
            prehistory_source_authority_context_json=visible,
        )
        alias_table = expression.build_source_ref_alias_table(
            request=provider_request,
            model_visible_context_json=provider_request.model_content_json,
        )
        assert (alias_table.alias_for(final_record_id) or final_record_id) in set(
            manifest["world_claim_source_refs"]["past_world"]
        )
        claim = expression.WorldClaimDraft(
            claim_text="放学回家路上，她把蓝雨伞忘在走廊窗台。",
            scope="past_world",
            source_refs=(final_record_id,),
        )
        draft = expression.ExpressionDraft.model_validate_json(json.dumps({
            "timing_choice": "now",
            "stance": "fixture",
            "brief_rationale": "fixture",
            "beats": [{"modality": "text", "text": claim.claim_text}],
            "world_claims": [claim.model_dump(mode="json")],
        }))
        expression._validate_world_claims(draft=draft, request=unreviewed)
        evidence = expression._world_claim_evidence(draft=draft, request=unreviewed)
        assert {binding.ref for binding in historical[final_record_id]} <= {
            item.ref_id for item in evidence
        }
    finally:
        if coordinator is not None:
            coordinator.close()
        ledger.close()


def test_unreviewed_claim_materialization_keeps_proof_for_presented_inner_life_memory(tmp_path):
    from test_prehistory_chat_context import _capsule
    from test_visible_source_composer import _request
    from companion_daemon.world_v2 import expression_draft as expression
    from companion_daemon.world_v2.deliberation import TriggerMessage
    from companion_daemon.world_v2.prehistory_claim_authority import prehistory_claim_bindings

    ledger = started_ledger(tmp_path / "inner-life-source-proof.sqlite")
    try:
        rows = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(
            _linked_archive(), created_at=START,
        )
        row = rows[0]
        source = prehistory_memory_binding(row)
        pending = _choice(ledger, source, candidate_id="memory:inner-life-proof")
        _choice(
            ledger,
            source,
            before=pending,
            status="active",
            candidate_id="memory:inner-life-proof",
        )
        capsule = _capsule(ledger, actor=ACTOR, trigger="fixture:inner-life-source-proof")
        pinned = _request(capsule)
        memory_item, = capsule.active_memory_candidates.items
        memory_value = json.loads(memory_item.payload_json)
        pinned_context = json.loads(pinned.model_content_json)
        provider_context = {
            key: pinned_context[key]
            for key in (
                "world_id", "actor_ref", "trigger_ref", "world_revision",
                "deliberation_revision", "ledger_sequence", "logical_time",
                "consumer_scope",
            )
            if key in pinned_context
        }
        provider_context["inner_life_snapshot"] = {
            "materials": {
                "remembered_material": [{
                    "source_ref": memory_item.item_ref,
                    "privacy_class": memory_item.privacy_class,
                    "source_excerpts": memory_value["source_excerpts"],
                }],
            },
        }
        provider_context_json = json.dumps(provider_context, ensure_ascii=False)
        request = pinned.model_copy(update={
            "model_content_json": provider_context_json,
            "visible_source_requirement_json": None,
            "trigger_message": TriggerMessage(
                event_ref="event:inner-life-source-proof",
                event_payload_hash="sha256:" + "a" * 64,
                source_world_revision=pinned.evaluated_world_revision,
                observation_ref="observation:inner-life-source-proof",
                actor="user:counterpart",
                channel="qq:c2c",
                reply_target="conversation:fixture",
                text="你还记得那段吗？",
            ),
        })
        claim = expression.WorldClaimDraft(
            claim_text=row.record.statement,
            scope="past_world",
            source_refs=(row.record.record_id,),
        )
        draft = expression.ExpressionDraft.model_validate_json(json.dumps({
            "timing_choice": "now",
            "stance": "fixture",
            "brief_rationale": "fixture",
            "beats": [{"modality": "text", "text": row.record.statement}],
            "world_claims": [claim.model_dump(mode="json")],
        }))

        # A provider request may carry the exact excerpt under InnerLifeSnapshot
        # while acceptance still needs the pinned, proof-bearing Capsule. The
        # source capability exists only when both views are supplied together.
        assert row.record.record_id in prehistory_claim_bindings(
            request,
            context=provider_context,
            source_authority_context_json=pinned.model_content_json,
        )
        stale_authority = json.loads(pinned.model_content_json)
        stale_authority["ledger_sequence"] += 1
        assert row.record.record_id not in prehistory_claim_bindings(
            request,
            context=provider_context,
            source_authority_context_json=json.dumps(stale_authority),
        )
        with pytest.raises(ValueError, match="outside its semantic source lane"):
            expression._validate_world_claims(draft=draft, request=request)
        expression._validate_world_claims(
            draft=draft,
            request=request,
            prehistory_source_authority_context_json=pinned.model_content_json,
        )
        assert expression.invalid_world_claim_source_indexes(
            draft=draft,
            request=request,
            prehistory_source_authority_context_json=pinned.model_content_json,
        ) == ()
        evidence = expression._world_claim_evidence(
            draft=draft,
            request=request,
            prehistory_source_authority_context_json=pinned.model_content_json,
        )
        assert {binding.ref for binding in memory_item.source_bindings} <= {
            item.ref_id for item in evidence
        }
        proposal = expression.materialize_expression_draft(
            value={
                "timing_choice": "now",
                "stance": "fixture",
                "brief_rationale": "fixture",
                "beats": [{"modality": "text", "text": row.record.statement}],
                "world_claims": [claim.model_dump(mode="json")],
            },
            request=request,
            capabilities=expression.TEXT_ONLY_EXPRESSION_CAPABILITIES,
            private_state_context_json=provider_context_json,
            prehistory_source_authority_context_json=pinned.model_content_json,
        )
        assert {binding.ref for binding in memory_item.source_bindings} <= {
            item.ref_id for item in proposal.evidence_refs
        }
        unshown_context = json.loads(provider_context_json)
        unshown_context["inner_life_snapshot"]["materials"]["remembered_material"] = []
        unshown = request.model_copy(update={
            "model_content_json": json.dumps(unshown_context),
        })
        with pytest.raises(ValueError, match="outside its semantic source lane"):
            expression._validate_world_claims(
                draft=draft,
                request=unshown,
                prehistory_source_authority_context_json=pinned.model_content_json,
            )
    finally:
        ledger.close()
