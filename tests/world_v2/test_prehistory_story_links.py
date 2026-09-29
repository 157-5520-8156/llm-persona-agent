from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import hashlib
import json

import pytest

from companion_daemon.world_v2.prehistory_life_materials import (
    LifeMaterialActor,
    LifeMaterialBlock,
    LifeMaterialsDraft,
    LifeRecollection,
)
from companion_daemon.world_v2.character_prehistory import HistoricalEntity, digest
from companion_daemon.world_v2.prehistory_authoring import (
    PrehistoryCreationReview, PrehistoryLinkVerdict, PrehistoryRecordVerdict,
)
from companion_daemon.world_v2.prehistory_life_materials import export_actor_archive
from companion_daemon.world_v2.prehistory_story_links import (
    PrehistoryStoryLinkArtifact,
    PrehistoryStoryLinkDraft,
    PrehistoryStoryLinkResponse,
    StoryLinkEvidence,
    apply_story_links,
    carry_forward_approved_story_links,
    normalize_story_link_response,
    story_link_request,
    validate_story_links,
    STORY_LINK_INSTRUCTION,
)
from companion_daemon.world_v2.prehistory_life_materials_runner import run_story_linking


START = datetime(2015, 1, 1, tzinfo=UTC)
END = datetime(2016, 12, 31, tzinfo=UTC)
WORLD_START = datetime(2026, 8, 13, tzinfo=UTC)
ACTOR = "agent:companion"
STORY = "她忘带伞，他追到车站把伞还给她。\n后来她发消息说那天一直记得这件事。"


def _materials():
    digest = hashlib.sha256(STORY.encode()).hexdigest()
    return LifeMaterialsDraft(
        world_id="world:test",
        world_started_at=WORLD_START,
        source_artifact_ref=f"narrative#sha256={digest}",
        entities=(
            HistoricalEntity(entity_ref="history:person:celia", kind="person", label="知栀"),
            HistoricalEntity(entity_ref="history:person:other", kind="person", label="另一人"),
        ),
        actors=(
            LifeMaterialActor(
                actor_ref=ACTOR,
                historical_entity_ref="history:person:celia",
                born_at=datetime(2005, 4, 12, tzinfo=UTC),
            ),
            LifeMaterialActor(
                actor_ref="agent:other",
                historical_entity_ref="history:person:other",
            ),
        ),
        blocks=(
            LifeMaterialBlock(
                block_id="block:umbrella",
                text="她忘带伞，对方追到车站把伞还给她。",
                allowed_actor_refs=(ACTOR,),
                source_quote="她忘带伞，他追到车站把伞还给她。",
                source_locator="source-segment:0001",
                kind="event",
                event_ref="event:umbrella",
                occurred_from=START,
                occurred_until=END,
            ),
            LifeMaterialBlock(
                block_id="block:callback",
                text="后来她说自己一直记得这件事。",
                allowed_actor_refs=(ACTOR,),
                source_quote="后来她发消息说那天一直记得这件事。",
                source_locator="source-segment:0002",
                kind="event",
                event_ref="event:callback",
                occurred_from=datetime(2016, 1, 1, tzinfo=UTC),
                occurred_until=END,
            ),
            LifeMaterialBlock(
                block_id="block:other-view",
                text="另一人私下想法。",
                allowed_actor_refs=("agent:other",),
                source_quote="她忘带伞，他追到车站把伞还给她。",
                source_locator="source-segment:0001",
                kind="historical_interpretation",
                event_ref="event:other-view",
                subject_actor_ref="agent:other",
                occurred_from=START,
                occurred_until=END,
            ),
        ),
        recollections=(
            LifeRecollection(
                record_id="prehistory-record:umbrella",
                actor_ref=ACTOR,
                known_from=START,
                known_until=END,
                time_precision="interval",
                timezone_name="UTC",
                block_ids=("block:umbrella",),
                participant_refs=(),
                related_record_refs=(),
                privacy_class="personal",
            ),
            LifeRecollection(
                record_id="prehistory-record:later-message",
                actor_ref=ACTOR,
                known_from=datetime(2016, 1, 1, tzinfo=UTC),
                known_until=END,
                time_precision="year",
                timezone_name="UTC",
                block_ids=("block:callback",),
                participant_refs=(),
                related_record_refs=(),
                privacy_class="personal",
            ),
        ),
    )


def _draft(materials):
    return PrehistoryStoryLinkDraft(
        actor_ref=ACTOR,
        source_materials_hash=digest(materials),
        relations=({
            "left_record_id": "prehistory-record:umbrella",
            "right_record_id": "prehistory-record:later-message",
            "relation_kind": "later_disclosure",
            "evidence": (
                {"block_id": "block:umbrella", "source_quote": "他追到车站把伞还给她。"},
                {"block_id": "block:callback", "source_quote": "一直记得这件事"},
            ),
            "bridge_source_quote": "后来她发消息说那天一直记得这件事。",
            "rationale": "后来的消息明确回指此前还伞的那次经历。",
        },),
    )


def test_story_links_require_exact_actor_owned_evidence_and_apply_symmetrically():
    materials = _materials()
    request = story_link_request(materials, ACTOR, narrative_text=STORY)
    assert request["actor_ref"] == ACTOR
    assert {row["record_id"] for row in request["records"]} == {
        "prehistory-record:umbrella",
        "prehistory-record:later-message",
    }
    assert "block:other-view" not in json.dumps(request, ensure_ascii=False)
    assert request["source_narrative"] == STORY
    assert request["source_artifact_ref"] == materials.source_artifact_ref
    draft = _draft(materials)
    assert validate_story_links(materials, actor_ref=ACTOR, draft=draft, narrative_text=STORY) == draft
    linked = apply_story_links(materials, actor_ref=ACTOR, draft=draft, narrative_text=STORY)
    assert linked.recollections[0].related_record_refs == ("prehistory-record:later-message",)
    assert linked.recollections[1].related_record_refs == ("prehistory-record:umbrella",)


def test_linking_rejects_narrative_bytes_that_do_not_match_bound_materials():
    with pytest.raises(ValueError, match="source artifact"):
        story_link_request(_materials(), ACTOR, narrative_text=STORY + "edited")


def test_link_request_carries_prior_independent_rejections_as_negative_examples():
    request = story_link_request(
        _materials(), ACTOR, narrative_text=STORY,
        reviewed_link_findings=("时间相邻不足以证明二者延续。",),
    )
    assert request["prior_independent_review_findings"] == ("时间相邻不足以证明二者延续。",)
    assert "不是事实，也不是要求生成替代关系" in request["instruction"]


def test_story_link_call_is_private_captured_and_idempotently_replayed(tmp_path):
    materials = _materials()
    links = _draft(materials)
    calls = []

    async def completion(*, messages, attempt_dir, model_name):
        calls.append((messages, attempt_dir, model_name))
        return PrehistoryStoryLinkResponse(relations=links.relations).model_dump_json(), {
            "input_tokens": 200, "output_tokens": 40,
        }

    kwargs = {
        "materials": materials,
        "actor_ref": ACTOR,
        "narrative_text": STORY,
        "run_dir": tmp_path / "story-link-run",
        "api_key": "must-not-be-written",
        "completion": completion,
    }
    linked, manifest = asyncio.run(run_story_linking(**kwargs))
    assert manifest["status"] == "completed" and manifest["links"] == 1
    assert len(calls) == 1
    assert linked.recollections[0].related_record_refs == ("prehistory-record:later-message",)
    artifact = json.loads((tmp_path / "story-link-run/story-linking/story-links.json").read_text())
    assert artifact["source_artifact_ref"] == materials.source_artifact_ref
    assert "must-not-be-written" not in "".join(
        path.read_text(errors="ignore") for path in (tmp_path / "story-link-run").rglob("*.*")
    )
    repeated, repeated_manifest = asyncio.run(run_story_linking(**kwargs))
    assert len(calls) == 1
    assert repeated == linked and repeated_manifest == manifest


def test_complete_invalid_link_response_gets_one_captured_targeted_correction(tmp_path):
    materials = _materials()
    valid = _draft(materials).relations[0]
    unrelated = materials.blocks[0].model_copy(update={
        "block_id": "block:unrelated", "text": "另一段没有放进两个关系端点的经历。",
        "source_quote": "后来她发消息说那天一直记得这件事。", "event_ref": "event:unrelated",
    })
    materials = materials.model_copy(update={"blocks": (*materials.blocks, unrelated)})
    invalid = valid.model_copy(update={"evidence": (
        valid.evidence[0], StoryLinkEvidence(
            block_id="block:unrelated", source_quote="后来她发消息说",
        ),
    )})
    calls = []

    async def completion(*, messages, attempt_dir, model_name):
        calls.append(messages)
        result = PrehistoryStoryLinkResponse(relations=(invalid if len(calls) == 1 else valid,))
        return result.model_dump_json(), {"input_tokens": 200, "output_tokens": 40}

    linked, manifest = asyncio.run(run_story_linking(
        materials=materials, actor_ref=ACTOR, narrative_text=STORY,
        run_dir=tmp_path / "repair-once", api_key="private-test-key", completion=completion,
    ))
    assert len(calls) == 2
    assert calls[1][0]["role"] == "system"
    assert "上一份完整JSON" in calls[1][0]["content"]
    assert "lacks evidence" in calls[1][0]["content"]
    assert "previous_complete_response" in calls[1][1]["content"]
    assert manifest["status"] == "completed" and manifest["attempts"] == 2
    assert [row["attempt"] for row in manifest["attempt_usage_audit"]] == [1, 2]
    first_attempt = tmp_path / "repair-once/story-linking/attempt-01/attempt-result.json"
    second_attempt = tmp_path / "repair-once/story-linking/attempt-02/attempt-result.json"
    assert json.loads(first_attempt.read_text())["status"] == "validation_failed"
    assert json.loads(second_attempt.read_text())["status"] == "response_captured"
    assert linked.recollections[0].related_record_refs == ("prehistory-record:later-message",)


def test_captured_story_link_response_can_be_revalidated_after_prompt_revision(tmp_path, monkeypatch):
    materials = _materials()
    links = PrehistoryStoryLinkResponse(relations=_draft(materials).relations)
    calls = []

    async def completion(*, messages, attempt_dir, model_name):
        calls.append(messages)
        return links.model_dump_json(), {"input_tokens": 200, "output_tokens": 40}

    run_dir = tmp_path / "story-link-resume"
    linked, manifest = asyncio.run(run_story_linking(
        materials=materials, actor_ref=ACTOR, narrative_text=STORY, run_dir=run_dir,
        api_key="not-written", completion=completion,
    ))
    # Simulate a validator/prompt version change while keeping the original
    # provider packet and captured response immutable.
    current_manifest = json.loads((run_dir / "story-linking/manifest.json").read_text())
    current_manifest["status"] = "validation_rejected"
    (run_dir / "story-linking/manifest.json").write_text(json.dumps(current_manifest), encoding="utf-8")
    (run_dir / "story-linking/story-links.json").unlink()
    monkeypatch.setattr(
        "companion_daemon.world_v2.prehistory_story_links.STORY_LINK_INSTRUCTION",
        STORY_LINK_INSTRUCTION + " Updated prompt for validation only.",
    )
    # The originally captured model request/result remain bound and are
    # revalidated under the code that now understands the additive metadata.
    repeated, repeated_manifest = asyncio.run(run_story_linking(
        materials=materials, actor_ref=ACTOR, narrative_text=STORY, run_dir=run_dir,
        api_key="not-written", completion=completion,
    ))
    assert repeated == linked
    assert repeated_manifest["request_hash"] == manifest["request_hash"]
    assert len(calls) == 1


@pytest.mark.parametrize("damage", ["unknown_record", "wrong_actor", "unsupported_quote", "one_sided_evidence", "unsupported_bridge"])
def test_story_link_rejects_missing_or_out_of_scope_evidence(damage):
    materials = _materials()
    draft = _draft(materials)
    relation = draft.relations[0]
    if damage == "unknown_record":
        relation = relation.model_copy(update={"right_record_id": "prehistory-record:absent"})
    elif damage == "wrong_actor":
        # No evidence block outside this actor's records can be used to link.
        relation = relation.model_copy(update={"evidence": (
            StoryLinkEvidence(block_id="block:umbrella", source_quote="她忘带伞"),
            StoryLinkEvidence(block_id="block:other-view", source_quote="他追到车站"),
        )})
    elif damage == "unsupported_quote":
        relation = relation.model_copy(update={"evidence": (
            StoryLinkEvidence(block_id="block:umbrella", source_quote="陈曼决定关店"),
            StoryLinkEvidence(block_id="block:callback", source_quote="一直记得这件事"),
        )})
    elif damage == "unsupported_bridge":
        relation = relation.model_copy(update={"bridge_source_quote": "陈曼决定关店。"})
    else:
        relation = relation.model_copy(update={"evidence": (
            StoryLinkEvidence(block_id="block:umbrella", source_quote="伞"),
        )})
    damaged = draft.model_copy(update={"relations": (relation,)})
    with pytest.raises(ValueError):
        validate_story_links(materials, actor_ref=ACTOR, draft=damaged, narrative_text=STORY)


def test_duplicate_links_of_a_pair_are_deduplicated_even_with_different_kinds():
    materials = _materials()
    relation = _draft(materials).relations[0]
    other_kind = relation.model_copy(update={"relation_kind": "explicit_callback"})
    response = PrehistoryStoryLinkResponse(relations=(relation, relation, other_kind))
    normalized, dropped = normalize_story_link_response(response)
    assert dropped == 2
    assert len(normalized.relations) == 1
    bound = PrehistoryStoryLinkDraft(
        actor_ref=ACTOR, source_materials_hash=digest(materials),
        relations=normalized.relations,
    )
    linked = apply_story_links(materials, actor_ref=ACTOR, draft=bound, narrative_text=STORY)
    assert linked.recollections[0].related_record_refs == ("prehistory-record:later-message",)


def test_story_link_draft_rejects_multiple_relation_kinds_for_the_same_pair():
    materials = _materials()
    relation = _draft(materials).relations[0]
    duplicate = relation.model_copy(update={"relation_kind": "explicit_callback"})
    with pytest.raises(ValueError, match="record pairs must be unique"):
        PrehistoryStoryLinkDraft(
            actor_ref=ACTOR, source_materials_hash=digest(materials),
            relations=(relation, duplicate),
        )


def test_carry_forward_preserves_only_previously_reviewed_links_with_unchanged_evidence():
    source_materials = _materials()
    old_draft = _draft(source_materials)
    old_linked = apply_story_links(
        source_materials, actor_ref=ACTOR, draft=old_draft, narrative_text=STORY,
    )
    old_archive = export_actor_archive(old_linked, ACTOR, "prehistory-archive:carry-forward")
    old_artifact = PrehistoryStoryLinkArtifact(
        request_hash="d" * 64, source_artifact_ref=source_materials.source_artifact_ref,
        source_materials_hash=digest(source_materials), linked_materials_hash=digest(old_linked),
        draft=old_draft,
    )
    review = PrehistoryCreationReview(
        brief_hash="e" * 64, document_hash=digest(old_archive), reviewer_ref="reviewer:fixture",
        reviewed_at=WORLD_START, decision="approved",
        records=tuple(PrehistoryRecordVerdict(
            record_id=r.record_id, record_hash=digest(r), verdict="approve", rationale="Source-supported."
        ) for r in old_archive.records),
        story_links=(PrehistoryLinkVerdict(
            relation_index=0, left_record_id="prehistory-record:umbrella",
            right_record_id="prehistory-record:later-message", relation_kind="later_disclosure",
            verdict="approve", rationale="The later message explicitly recalls the event."
        ),),
    )

    carried, artifact, manifest = carry_forward_approved_story_links(
        previous_source_materials=source_materials, previous_linked_materials=old_linked,
        previous_archive=old_archive, previous_artifact=old_artifact, previous_review=review,
        current_materials=source_materials, current_archive=export_actor_archive(
            source_materials, ACTOR, old_archive.archive_id,
        ), actor_ref=ACTOR, narrative_text=STORY,
    )
    assert artifact.origin == "approved_review_carry_forward"
    assert artifact.carried_relation_indexes == (0,)
    assert manifest["provider_calls"] == 0
    assert carried.recollections[0].related_record_refs == ("prehistory-record:later-message",)

    changed_block = source_materials.blocks[0].model_copy(update={"text": "她在站台等了很久。"})
    changed_materials = source_materials.model_copy(update={"blocks": (changed_block, *source_materials.blocks[1:])})
    changed, empty_artifact, changed_manifest = carry_forward_approved_story_links(
        previous_source_materials=source_materials, previous_linked_materials=old_linked,
        previous_archive=old_archive, previous_artifact=old_artifact, previous_review=review,
        current_materials=changed_materials,
        current_archive=export_actor_archive(changed_materials, ACTOR, old_archive.archive_id),
        actor_ref=ACTOR, narrative_text=STORY,
    )
    assert empty_artifact.draft.relations == ()
    assert empty_artifact.carried_relation_indexes == ()
    assert changed_manifest["dropped_relations"] == ({"relation_index": 0, "reason": "endpoint_record_changed"},)
    assert all(not row.related_record_refs for row in changed.recollections)
