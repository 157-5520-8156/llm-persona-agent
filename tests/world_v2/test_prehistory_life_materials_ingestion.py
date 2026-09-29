import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
import json

import httpx
import pytest

from companion_daemon.world_v2.character_prehistory import HistoricalEntity, digest
from companion_daemon.world_v2.character_prehistory import PrehistoryArchiveDocument, PrehistoryRecord
from companion_daemon.world_v2.prehistory_authoring import (
    PrehistoryAuthoringBrief, PrehistoryCreationReview, PrehistoryLinkVerdict,
    PrehistoryRecordVerdict,
)
from companion_daemon.world_v2.prehistory_life_materials import (
    LifeMaterialActor,
    LifeMaterialBlock,
    LifeMaterialsDraft,
    LifeRecollection,
    author_request,
    export_actor_archive,
    faithful_revision_request,
    ingestion_request,
    normalize_ingested_material_metadata,
    validate_ingested_materials,
    validate_faithful_revision,
)
from companion_daemon.world_v2.prehistory_life_materials_runner import (
    MAX_PREHISTORY_OUTPUT_TOKENS,
    PREHISTORY_PROVIDER_TIMEOUT_SECONDS,
    _AttemptCaptureTransport,
    ProviderOutputTruncated,
    provider_usage_audit,
    run_faithful_ingestion,
)


WORLD_START = datetime(2026, 8, 13, tzinfo=UTC)
BIRTH = datetime(2005, 4, 12, tzinfo=UTC)
NARRATIVE = "沈知栀和罗嘉禾在2015年开学后成了朋友。那天胶粘在知栀裤子上，她剪了个洞。"


def brief():
    return PrehistoryAuthoringBrief(
        world_id="world:test", actor_ref="agent:celia", world_started_at=WORLD_START,
        world_start_event_ref="event:world-start", world_start_event_hash="a" * 64,
        born_at=BIRTH, created_at=WORLD_START, author_ref="operator:fixture",
        draft_source_ref="input:novel.md", profile_source_ref="fixture:profile",
        profile_sha256="b" * 64, profile={"name": "沈知栀"},
    )


def materials_for(text: str = NARRATIVE):
    source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return LifeMaterialsDraft(
        world_id="world:test", world_started_at=WORLD_START,
        source_artifact_ref=f"narrative#sha256={source_hash}",
        entities=(
            HistoricalEntity(entity_ref="history:person:celia", kind="person", label="沈知栀（档案所属角色的历史身份）"),
            HistoricalEntity(entity_ref="history:person:jiahe", kind="person", label="罗嘉禾"),
        ),
        actors=(LifeMaterialActor(actor_ref="agent:celia", historical_entity_ref="history:person:celia", born_at=BIRTH),),
        blocks=(LifeMaterialBlock(
            block_id="block:meeting", text="两人在2015年开学后成了朋友。",
            allowed_actor_refs=("agent:celia",), source_quote="沈知栀和罗嘉禾在2015年开学后成了朋友。",
            source_locator="source-segment:0001", kind="event", event_ref="event:meeting",
            occurred_from=datetime(2015, 1, 1, tzinfo=UTC), occurred_until=datetime(2015, 12, 31, tzinfo=UTC),
        ),),
        recollections=(LifeRecollection(
            record_id="prehistory-record:meeting", actor_ref="agent:celia",
            known_from=datetime(2015, 1, 1, tzinfo=UTC), known_until=datetime(2015, 12, 31, tzinfo=UTC),
            time_precision="year", block_ids=("block:meeting",), participant_refs=("history:person:jiahe",),
            privacy_class="personal",
        ),),
    )


def revision_case():
    original = materials_for()
    first = original.recollections[0].model_copy(update={
        "related_record_refs": ("prehistory-record:duplicate-meeting",),
    })
    duplicate = original.recollections[0].model_copy(update={
        "record_id": "prehistory-record:duplicate-meeting",
        "related_record_refs": (original.recollections[0].record_id,),
    })
    previous = original.model_copy(update={"recollections": (first, duplicate)})
    archive = export_actor_archive(previous, "agent:celia", "prehistory-archive:revision-test")
    b = brief()
    review_brief = b.model_copy(update={"draft_source_ref": archive.source_artifact_ref})
    review = PrehistoryCreationReview(
        brief_hash=digest(review_brief), document_hash=digest(archive),
        reviewer_ref="model:gpt-6-luna:fixture-review", reviewed_at=WORLD_START + timedelta(days=1),
        decision="rejected", cross_record_findings=("The second record repeats the first event.",),
        story_links=(PrehistoryLinkVerdict(
            relation_index=0, left_record_id=archive.records[0].record_id,
            right_record_id=archive.records[1].record_id, relation_kind="shared_scene",
            verdict="reject", rationale="The two records duplicate one event.",
        ),),
        records=(
            PrehistoryRecordVerdict(record_id=archive.records[0].record_id, record_hash=digest(archive.records[0]),
                                    verdict="approve", rationale="Directly witnessed scene."),
            PrehistoryRecordVerdict(record_id=archive.records[1].record_id, record_hash=digest(archive.records[1]),
                                    verdict="reject", rationale="Duplicate of the first record."),
        ),
    )
    return b, previous, archive, review


def test_faithful_ingestion_has_a_separate_contract_and_source_bound_segments():
    creation = author_request(brief(), narrative_text=NARRATIVE)
    ingestion = ingestion_request(brief(), NARRATIVE)
    assert creation["contract"] == "prehistory-life-materials-author-request.1"
    assert ingestion["contract"] == "prehistory-life-materials-ingestion-request.1"
    assert ingestion["mode"] == "faithful_source_extraction"
    assert ingestion["source"]["sha256"] == hashlib.sha256(NARRATIVE.encode()).hexdigest()
    segment = ingestion["source"]["segments"][0]
    assert segment["segment_id"] == "source-segment:0001"
    assert segment["text"] == NARRATIVE
    assert "不得创作" in ingestion["instruction"]
    assert "occurred_from与occurred_until必须成对" in ingestion["instruction"]
    assert "不表示具体哪件事已经告诉知栀" in ingestion["instruction"]
    assert "后续告知或回扣" in ingestion["instruction"]
    assert "每条最多引用8个block" in ingestion["instruction"]


def test_faithful_extraction_rejects_era_sized_recollections():
    original = materials_for()
    base = original.blocks[0]
    blocks = tuple(base.model_copy(update={
        "block_id": f"block:meeting:{index}",
        "event_ref": f"event:meeting:{index}",
    }) for index in range(9))
    record = original.recollections[0].model_copy(update={
        "block_ids": tuple(block.block_id for block in blocks),
    })
    oversized = original.model_copy(update={"blocks": blocks, "recollections": (record,)})
    with pytest.raises(ValueError, match="a faithful episode may use at most 8"):
        validate_ingested_materials(brief(), NARRATIVE, oversized)


def test_revision_request_binds_review_and_prior_candidate():
    b, previous, archive, review = revision_case()
    request = faithful_revision_request(
        b, NARRATIVE, actor_ref="agent:celia", archive_id=archive.archive_id,
        previous_materials=previous, previous_archive=archive, review=review,
    )
    assert request["contract"] == "prehistory-life-materials-revision-request.1"
    assert request["mode"] == "faithful_source_revision"
    assert request["prior_candidate"]["review_hash"] == digest(review)
    assert request["prior_candidate"]["archive_hash"] == digest(archive)
    assert request["prior_candidate"]["review"]["records"][1]["verdict"] == "reject"
    assert "必须原样保留" in request["instruction"]
    assert request["revision_feedback"]["story_links_to_avoid"][0]["left_record_id"] == archive.records[0].record_id


def test_revision_can_remove_a_rejected_duplicate_but_cannot_change_approved_history():
    b, previous, archive, review = revision_case()
    approved = previous.recollections[0].model_copy(update={"related_record_refs": ()})
    revised = previous.model_copy(update={"recollections": (approved,)})
    result = validate_faithful_revision(
        b, NARRATIVE, actor_ref="agent:celia", archive_id=archive.archive_id,
        previous_materials=previous, previous_archive=archive, review=review,
        revised_materials=revised,
    )
    assert result.records[0].model_copy(update={"related_record_refs": ()}) == archive.records[0].model_copy(
        update={"related_record_refs": ()},
    )
    assert not result.records[0].related_record_refs

    modified_block = previous.blocks[0].model_copy(update={"text": "另一个不再相同的叙事细节。"})
    changed = previous.model_copy(update={"blocks": (modified_block,)})
    with pytest.raises(ValueError, match="changed previously approved record"):
        validate_faithful_revision(
            b, NARRATIVE, actor_ref="agent:celia", archive_id=archive.archive_id,
            previous_materials=previous, previous_archive=archive, review=review,
            revised_materials=changed,
        )


def test_revision_runner_sends_bound_review_and_captures_corrected_materials(tmp_path):
    b, previous, archive, review = revision_case()
    corrected = previous.model_copy(update={"recollections": (
        previous.recollections[0].model_copy(update={"related_record_refs": ()}),
    )})
    calls = []

    async def completion(*, messages, attempt_dir, model_name):
        calls.append(messages)
        submitted = json.loads(messages[1]["content"])
        assert submitted["mode"] == "faithful_source_revision"
        assert submitted["prior_candidate"]["review"]["reviewer_ref"] == review.reviewer_ref
        return corrected.model_dump_json(), {"input_tokens": 300, "output_tokens": 120}

    materials, revised_archive, manifest = asyncio.run(run_faithful_ingestion(
        brief=b, narrative_text=NARRATIVE, actor_ref="agent:celia", archive_id=archive.archive_id,
        run_dir=tmp_path / "revision-run", api_key="private-test-key",
        previous_materials=previous, previous_archive=archive, revision_review=review,
        completion=completion,
    ))
    assert len(calls) == 1
    assert manifest["mode"] == "faithful_source_revision"
    assert [row.record_id for row in materials.recollections] == [previous.recollections[0].record_id]
    assert not materials.recollections[0].related_record_refs
    assert revised_archive.records[0].model_copy(update={"related_record_refs": ()}) == archive.records[0].model_copy(
        update={"related_record_refs": ()},
    )
    assert (tmp_path / "revision-run/ingestion-request.json").exists()


def test_cli_dry_run_writes_private_packet_without_provider_call(tmp_path, capsys):
    from scripts.prepare_life_materials_prehistory import main

    brief_path = tmp_path / "brief.json"
    story_path = tmp_path / "narrative.md"
    packet_path = tmp_path / "request.json"
    brief_path.write_text(brief().model_dump_json(), encoding="utf-8")
    story_path.write_text(NARRATIVE, encoding="utf-8")
    main(["--ingest-request", "--brief", str(brief_path), "--narrative", str(story_path),
          "--output", str(packet_path)])
    report = json.loads(capsys.readouterr().out)
    assert report["provider_calls"] == 0
    assert report["source_sha256"] == hashlib.sha256(NARRATIVE.encode()).hexdigest()
    assert packet_path.stat().st_mode & 0o777 == 0o600
    assert json.loads(packet_path.read_text())["mode"] == "faithful_source_extraction"


def test_cli_binds_actual_review_response_but_same_model_cannot_create_import_package(
    tmp_path, capsys, monkeypatch,
):
    import scripts.prepare_life_materials_prehistory as cli
    import companion_daemon.world_v2.prehistory_life_materials_runner as runner
    from companion_daemon.world_v2.prehistory_authoring import PrehistorySemanticReview

    brief_path = tmp_path / "brief.json"
    story_path = tmp_path / "narrative.md"
    output_path = tmp_path / "archive.json"
    run_dir = tmp_path / "run"
    brief_path.write_text(brief().model_dump_json(), encoding="utf-8")
    story_path.write_text(NARRATIVE, encoding="utf-8")
    calls = []

    async def fake_completion(*, api_key, base_url, model_name, messages, attempt_dir, usage_observer=None,
                              max_completion_tokens=None):
        calls.append((model_name, str(attempt_dir)))
        if "story-linking" in str(attempt_dir):
            from companion_daemon.world_v2.prehistory_story_links import PrehistoryStoryLinkResponse
            links = PrehistoryStoryLinkResponse(relations=())
            return links.model_dump_json(), {"input_tokens": 80, "output_tokens": 5}
        if "semantic-review" in str(attempt_dir):
            review = PrehistorySemanticReview(
                decision="approved", records=({"record_index": 0, "verdict": "approve", "rationale": "格式和证据范围符合要求。"},),
            )
            return review.model_dump_json(), {"input_tokens": 100, "output_tokens": 20}
        return materials_for().model_dump_json(), {"input_tokens": 200, "output_tokens": 50}

    monkeypatch.setenv("DEEPSEEK_DEBUG_API_KEY", "private-test-key")
    monkeypatch.setattr(runner, "_captured_completion", fake_completion)
    argv = ["--ingest-narrative", "--semantic-review", "--brief", str(brief_path),
            "--narrative", str(story_path), "--run-dir", str(run_dir),
            "--actor-ref", "agent:celia", "--archive-id", "prehistory-archive:cli-review",
            "--output", str(output_path)]
    cli.main(argv)
    first = json.loads(capsys.readouterr().out)
    assert len(calls) == 3
    assert first["review"]["decision"] == "approved"
    assert first["review"]["same_model_as_author"] is True
    assert first["review"]["package_written"] is False
    assert (run_dir / "linked-materials.json").is_file()
    assert (run_dir / "linked-materials.json").stat().st_mode & 0o777 == 0o600
    assert not (run_dir / "semantic-review/reviewed-package.json").exists()
    assert (run_dir / "semantic-review/bound-review.json").exists()
    cli.main(argv)
    capsys.readouterr()
    assert len(calls) == 3
    assert output_path.stat().st_mode & 0o777 == 0o600


def test_cli_carries_forward_only_review_approved_links_after_new_link_call_fails(
    tmp_path, capsys, monkeypatch,
):
    import scripts.prepare_life_materials_prehistory as cli
    import companion_daemon.world_v2.prehistory_life_materials_runner as runner
    from companion_daemon.world_v2.prehistory_authoring import (
        PrehistoryCreationReview, PrehistoryLinkVerdict, PrehistoryRecordVerdict,
    )
    from companion_daemon.world_v2.prehistory_story_links import (
        PrehistoryStoryLinkArtifact, PrehistoryStoryLinkDraft, apply_story_links,
    )
    from test_prehistory_story_links import ACTOR as LINK_ACTOR, STORY, _draft, _materials

    previous_source = _materials()
    rejected_block = previous_source.blocks[0].model_copy(update={
        "block_id": "block:rejected-unused", "event_ref": "event:rejected-unused",
    })
    rejected_record = previous_source.recollections[0].model_copy(update={
        "record_id": "prehistory-record:rejected-unused", "block_ids": ("block:rejected-unused",),
        "related_record_refs": (),
    })
    previous_source = previous_source.model_copy(update={
        "blocks": (*previous_source.blocks, rejected_block),
        "recollections": (*previous_source.recollections, rejected_record),
    })
    source_relation = _draft(_materials()).relations[0]
    link_draft = PrehistoryStoryLinkDraft(
        actor_ref=LINK_ACTOR, source_materials_hash=digest(previous_source),
        relations=(source_relation,),
    )
    previous_linked = apply_story_links(
        previous_source, actor_ref=LINK_ACTOR, draft=link_draft, narrative_text=STORY,
    )
    archive_id = "prehistory-archive:cli-carry-forward"
    previous_archive = export_actor_archive(previous_linked, LINK_ACTOR, archive_id)
    previous_artifact = PrehistoryStoryLinkArtifact(
        request_hash="d" * 64, source_artifact_ref=previous_source.source_artifact_ref,
        source_materials_hash=digest(previous_source), linked_materials_hash=digest(previous_linked),
        draft=link_draft,
    )
    source_relation_verdict = PrehistoryLinkVerdict(
        relation_index=0, left_record_id=source_relation.left_record_id,
        right_record_id=source_relation.right_record_id, relation_kind=source_relation.relation_kind,
        verdict="approve", rationale="The later message explicitly recalls the first scene.",
    )
    review_brief = PrehistoryAuthoringBrief(
        world_id=previous_archive.world_id, actor_ref=LINK_ACTOR,
        world_started_at=previous_linked.world_started_at,
        world_start_event_ref="world-start", world_start_event_hash="a" * 64,
        born_at=previous_linked.actors[0].born_at, created_at=previous_linked.world_started_at,
        author_ref="author:fixture", draft_source_ref=previous_archive.source_artifact_ref,
        profile_source_ref="fixture:profile", profile_sha256="b" * 64,
        profile={"name": "知栀"},
    )
    previous_review = PrehistoryCreationReview(
        brief_hash=digest(review_brief), document_hash=digest(previous_archive),
        reviewer_ref="reviewer:fixture", reviewed_at=previous_linked.world_started_at + timedelta(days=1),
        decision="rejected", cross_record_findings=("The last record is a rejected duplicate.",),
        records=tuple(PrehistoryRecordVerdict(
            record_id=record.record_id, record_hash=digest(record),
            verdict="reject" if record.record_id == rejected_record.record_id else "approve",
            rationale="Duplicate." if record.record_id == rejected_record.record_id else "Direct source support.",
        ) for record in previous_archive.records),
        story_links=(source_relation_verdict,),
    )
    revised_materials = previous_source.model_copy(update={
        "blocks": previous_source.blocks[:-1],
        "recollections": previous_source.recollections[:-1],
    })
    revised_archive = export_actor_archive(revised_materials, LINK_ACTOR, archive_id)

    paths = {name: tmp_path / f"{name}.json" for name in (
        "brief", "source-materials", "previous-materials", "previous-archive", "previous-review", "previous-links",
    )}
    story_path, output_path = tmp_path / "narrative.md", tmp_path / "revised-archive.json"
    for name, value in (("brief", review_brief), ("source-materials", previous_source),
                        ("previous-materials", previous_linked), ("previous-archive", previous_archive),
                        ("previous-review", previous_review), ("previous-links", previous_artifact)):
        paths[name].write_text(value.model_dump_json())
    story_path.write_text(STORY)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(json.dumps({"mode": "faithful_source_revision"}))
    api_calls = []

    async def fake_revision(**kwargs):
        return revised_materials, revised_archive, {"mode": "faithful_source_revision", "usage_audit": []}

    async def failed_link_generation(**kwargs):
        api_calls.append("link generation attempted")
        raise ValueError("candidate graph cites the wrong endpoint")

    monkeypatch.setenv("DEEPSEEK_DEBUG_API_KEY", "private-test-key")
    monkeypatch.setattr(runner, "run_faithful_ingestion", fake_revision)
    monkeypatch.setattr(runner, "run_story_linking", failed_link_generation)
    cli.main([
        "--ingest-narrative", "--brief", str(paths["brief"]), "--narrative", str(story_path),
        "--run-dir", str(run_dir), "--actor-ref", LINK_ACTOR, "--archive-id", archive_id,
        "--revision-materials", str(paths["previous-materials"]),
        "--revision-archive", str(paths["previous-archive"]),
        "--revision-review", str(paths["previous-review"]),
        "--revision-story-links", str(paths["previous-links"]),
        "--output", str(output_path),
    ])
    result = json.loads(capsys.readouterr().out)
    linked_materials = LifeMaterialsDraft.model_validate_json((run_dir / "linked-materials.json").read_text())
    artifact = PrehistoryStoryLinkArtifact.model_validate_json(
        (run_dir / "story-link-carry-forward/story-links.json").read_text(),
    )
    assert api_calls == ["link generation attempted"]
    assert result["story_links"]["origin"] == "approved_review_carry_forward"
    assert artifact.carried_relation_indexes == (0,)
    assert len(artifact.draft.relations) == 1
    assert linked_materials.recollections[0].related_record_refs == ("prehistory-record:later-message",)
    assert json.loads((run_dir / "story-link-carry-forward/manifest.json").read_text())["provider_calls"] == 0


def test_import_validation_requires_exact_quote_locator_and_original_world():
    parsed = materials_for()
    assert validate_ingested_materials(brief(), NARRATIVE, parsed) == parsed
    wrong_quote = parsed.model_copy(update={
        "blocks": (parsed.blocks[0].model_copy(update={"source_quote": "她一直陪着知栀"}),)
    })
    with pytest.raises(ValueError, match="not an exact substring"):
        validate_ingested_materials(brief(), NARRATIVE, wrong_quote)
    wrong_locator = parsed.model_copy(update={
        "blocks": (parsed.blocks[0].model_copy(update={"source_locator": "source-segment:0099"}),)
    })
    with pytest.raises(ValueError, match="unknown source segment"):
        validate_ingested_materials(brief(), NARRATIVE, wrong_locator)


def test_time_precision_normalization_preserves_original_interval():
    original = materials_for()
    broad = original.model_copy(update={
        "recollections": (original.recollections[0].model_copy(update={
            "known_until": WORLD_START,
            "time_precision": "year",
        }),),
    })
    normalized, audit = normalize_ingested_material_metadata(broad.model_dump_json(), brief())
    assert normalized.recollections[0].known_from == broad.recollections[0].known_from
    assert normalized.recollections[0].known_until == WORLD_START
    assert normalized.recollections[0].time_precision == "interval"
    assert normalized.blocks == broad.blocks
    assert audit[0]["normalization"] == "widen_display_precision_only"
    archive = export_actor_archive(normalized, "agent:celia", "prehistory-archive:time-boundary")
    assert archive.records[0].occurred_until == WORLD_START - timedelta(microseconds=1)
    assert archive.records[0].time_precision == "interval"
    assert "不表示此时发生了新事件" in archive.records[0].statement


def test_unbounded_event_date_is_omitted_from_metadata_but_kept_in_source_prose():
    source = materials_for()
    phrase = "2023年以后，她慢慢适应了新工作。"
    changed_block = source.blocks[0].model_copy(update={
        "text": phrase,
        "source_quote": phrase,
        "occurred_from": BIRTH,
        "occurred_until": None,
    })
    value = source.model_copy(update={"blocks": (changed_block,)})
    normalized, audit = normalize_ingested_material_metadata(value.model_dump_json(), brief())
    block = normalized.blocks[0]
    assert block.text == phrase and block.source_quote == phrase
    assert block.occurred_from is None and block.occurred_until is None
    assert audit[0]["normalization"] == "omit_incomplete_occurrence_metadata"


def test_existing_single_actor_identity_is_reused_and_cannot_be_replaced():
    old_archive = PrehistoryArchiveDocument(
        contract="character-prehistory-archive.1", archive_id="prehistory-archive:old",
        world_id="world:test", actor_ref="agent:celia", source_artifact_ref="fixture:old",
        entities=(HistoricalEntity(entity_ref="history:person:celia", kind="person", label="沈知栀（档案所属角色的历史身份）"),),
        records=(PrehistoryRecord(
            record_id="prehistory-record:old", occurred_from=datetime(2020, 1, 1, tzinfo=UTC),
            occurred_until=datetime(2020, 1, 1, tzinfo=UTC), time_precision="day", timezone_name="UTC",
            statement="旧档案记录。", privacy_class="personal",
        ),),
    )
    bound_brief = brief().model_copy(update={"accepted_archives": (old_archive,)})
    request = ingestion_request(bound_brief, NARRATIVE)
    assert request["protagonist_binding"]["historical_entity_ref"] == "history:person:celia"
    assert validate_ingested_materials(bound_brief, NARRATIVE, materials_for())
    wrong_identity = materials_for().model_copy(update={
        "actors": (materials_for().actors[0].model_copy(update={
            "historical_entity_ref": "history:person:wrong",
        }),),
        "entities": materials_for().entities + (
            HistoricalEntity(entity_ref="history:person:wrong", kind="person", label="沈知栀"),
        ),
    })
    with pytest.raises(ValueError, match="bound accepted identity"):
        validate_ingested_materials(bound_brief, NARRATIVE, wrong_identity)


def test_metadata_normalizer_restores_exact_accepted_actor_entity_label():
    old_archive = PrehistoryArchiveDocument(
        contract="character-prehistory-archive.1", archive_id="prehistory-archive:identity-normalizer",
        world_id="world:test", actor_ref="agent:celia", source_artifact_ref="fixture:old",
        entities=(HistoricalEntity(
            entity_ref="history:person:celia", kind="person", label="沈知栀（稳定身份）",
        ),),
        records=(PrehistoryRecord(
            record_id="prehistory-record:identity-normalizer",
            occurred_from=datetime(2020, 1, 1, tzinfo=UTC),
            occurred_until=datetime(2020, 1, 1, tzinfo=UTC),
            time_precision="day", timezone_name="UTC", statement="旧记忆。", privacy_class="personal",
        ),),
    )
    bound_brief = brief().model_copy(update={"accepted_archives": (old_archive,)})
    output = materials_for().model_dump(mode="json")
    output["entities"][0]["label"] = "沈知栀"
    normalized, audit = normalize_ingested_material_metadata(json.dumps(output, ensure_ascii=False), bound_brief)
    entity = next(row for row in normalized.entities if row.entity_ref == "history:person:celia")
    assert entity.label == "沈知栀（稳定身份）"
    assert validate_ingested_materials(bound_brief, NARRATIVE, normalized)
    assert audit[0]["normalization"] == "preserve_accepted_identity_label"


def test_model_runner_repairs_once_then_persists_materials_and_usage_without_secrets(tmp_path):
    calls = []
    valid = materials_for().model_dump_json()

    async def complete(*, messages, attempt_dir, model_name):
        calls.append((messages, attempt_dir, model_name))
        if len(calls) == 1:
            return '{"contract":"wrong"}', {"prompt_tokens": 15, "completion_tokens": 3}
        assert "repair_errors" in messages[1]["content"]
        return valid, {"prompt_tokens": 30, "completion_tokens": 45}

    materials, archive, manifest = asyncio.run(run_faithful_ingestion(
        brief=brief(), narrative_text=NARRATIVE, actor_ref="agent:celia",
        archive_id="prehistory-archive:runner-test", run_dir=tmp_path / "capture",
        api_key="must-not-be-written", completion=complete,
    ))
    assert len(calls) == 2
    assert len(archive.records) == 1
    assert manifest["status"] == "completed"
    assert manifest["attempts"] == 2
    assert [row["attempt"] for row in manifest["usage_audit"]] == [1, 2]
    assert manifest["billing_state"] == "estimated_from_provider_tokens"
    assert (tmp_path / "capture/attempt-01/attempt-result.json").exists()
    assert json.loads((tmp_path / "capture/attempt-02/attempt-result.json").read_text())["usage"]["completion_tokens"] == 45
    assert (tmp_path / "capture/materials.json").exists()
    assert "must-not-be-written" not in "".join(p.read_text(errors="ignore") for p in (tmp_path / "capture").rglob("*.*"))
    assert materials.source_artifact_ref.startswith("narrative#sha256=")


def test_schema_validation_details_are_json_safe_for_same_model_repair(tmp_path):
    calls = []
    valid = materials_for().model_dump_json()
    invalid = materials_for().model_copy(update={
        "recollections": (materials_for().recollections[0].model_copy(update={
            "related_record_refs": ("prehistory-record:unknown",),
        }),),
    }).model_dump_json()

    async def complete(*, messages, attempt_dir, model_name):
        calls.append(messages)
        if len(calls) == 2:
            assert "repair_errors" in messages[1]["content"]
        return (invalid if len(calls) == 1 else valid), {"input_tokens": 100, "output_tokens": 80}

    _materials, _archive, manifest = asyncio.run(run_faithful_ingestion(
        brief=brief(), narrative_text=NARRATIVE, actor_ref="agent:celia",
        archive_id="prehistory-archive:json-safe-repair", run_dir=tmp_path / "json-safe",
        api_key="not-written", completion=complete,
    ))
    assert len(calls) == 2
    assert manifest["status"] == "completed"


def test_provider_failure_is_not_retried_and_is_recorded(tmp_path):
    calls = 0

    async def fail(*, messages, attempt_dir, model_name):
        nonlocal calls
        calls += 1
        raise TimeoutError("temporary network timeout")

    run_dir = tmp_path / "failed"
    with pytest.raises(TimeoutError):
        asyncio.run(run_faithful_ingestion(
            brief=brief(), narrative_text=NARRATIVE, actor_ref="agent:celia",
            archive_id="prehistory-archive:timeout", run_dir=run_dir,
            api_key="never-persist", completion=fail,
        ))
    assert calls == 1
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["status"] == "provider_or_runner_failed"
    assert manifest["billing_state"] == "no_provider_request_emitted"
    assert not (run_dir / "materials.json").exists()
    assert "never-persist" not in (run_dir / "attempt-01/failure.json").read_text()


def test_pre_request_runner_failure_can_resume_without_repeating_a_provider_call(tmp_path):
    calls = 0
    valid = materials_for().model_dump_json()

    async def completion(*, messages, attempt_dir, model_name):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TypeError("local validation serializer failed before transport")
        return valid, {"input_tokens": 100, "output_tokens": 40}

    run_dir = tmp_path / "local-runner-error"
    kwargs = dict(
        brief=brief(), narrative_text=NARRATIVE, actor_ref="agent:celia",
        archive_id="prehistory-archive:local-resume", run_dir=run_dir,
        api_key="not-written", completion=completion,
    )
    with pytest.raises(TypeError, match="before transport"):
        asyncio.run(run_faithful_ingestion(**kwargs))
    assert not (run_dir / "attempt-01/provider-request.json").exists()

    _materials, archive, manifest = asyncio.run(run_faithful_ingestion(**kwargs))
    assert calls == 2
    assert archive.archive_id == "prehistory-archive:local-resume"
    assert manifest["status"] == "completed"
    assert "failure_type" not in manifest
    assert (run_dir / "attempt-01/pre-request-failure.json").exists()


def test_length_finish_reason_records_known_usage_and_does_not_repeat_request(tmp_path):
    calls = 0
    run_dir = tmp_path / "truncated"

    async def truncated(*, messages, attempt_dir, model_name):
        nonlocal calls
        calls += 1
        response = {"choices": [{"finish_reason": "length"}], "usage": {
            "prompt_tokens": 200, "completion_tokens": MAX_PREHISTORY_OUTPUT_TOKENS,
        }}
        attempt_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        (attempt_dir / "provider-response.json").write_text(json.dumps({
            "status_code": 200, "body_utf8": json.dumps(response),
        }), encoding="utf-8")
        return '{"contract":"partial', response["usage"]

    kwargs = dict(
        brief=brief(), narrative_text=NARRATIVE, actor_ref="agent:celia",
        archive_id="prehistory-archive:truncated", run_dir=run_dir,
        api_key="not-written", completion=truncated,
    )
    with pytest.raises(ProviderOutputTruncated, match="max_tokens"):
        asyncio.run(run_faithful_ingestion(**kwargs))
    with pytest.raises(RuntimeError, match="previous ingestion output was truncated"):
        asyncio.run(run_faithful_ingestion(**kwargs))

    manifest = json.loads((run_dir / "manifest.json").read_text())
    attempt = json.loads((run_dir / "attempt-01/attempt-result.json").read_text())
    assert calls == 1
    assert manifest["status"] == "output_truncated"
    assert manifest["billing_state"] == "estimated_from_provider_tokens"
    assert [row["attempt"] for row in manifest["usage_audit"]] == [1]
    assert attempt["finish_reason"] == "length"
    assert attempt["status"] == "output_truncated"
    assert not (run_dir / "materials.json").exists()


def test_usage_without_complete_provider_tokens_is_unknown_not_zero():
    audit = provider_usage_audit("deepseek-flash", {"provider_usage_ref": "usage:partial"})
    assert audit["cost"]["billing_state"] == "unknown"
    assert audit["cost"]["estimated_cost_cny"] is None
    priced = provider_usage_audit("deepseek-flash", {"input_tokens": 100, "output_tokens": 20})
    assert priced["cost"]["billing_state"] == "estimated_from_provider_tokens"
    assert priced["cost"]["estimated_cost_cny"] > 0
    assert PREHISTORY_PROVIDER_TIMEOUT_SECONDS > 45


def test_capture_transport_redacts_auth_and_preserves_wire_payloads(tmp_path):
    async def handler(request):
        return httpx.Response(200, json={"choices": [], "usage": {"prompt_tokens": 2}}, request=request)

    async def execute():
        capture = _AttemptCaptureTransport(httpx.MockTransport(handler), tmp_path)
        async with httpx.AsyncClient(transport=capture) as client:
            response = await client.post("https://example.invalid/v1/chat/completions",
                                         headers={"Authorization": "Bearer secret"}, json={"messages": ["hi"]})
            assert response.status_code == 200

    asyncio.run(execute())
    request = json.loads((tmp_path / "provider-request.json").read_text())
    response = json.loads((tmp_path / "provider-response.json").read_text())
    assert request["body_sha256"] == hashlib.sha256(b'{"messages":["hi"]}').hexdigest()
    assert "authorization" not in request["headers"]
    assert "secret" not in json.dumps(request)
    assert response["status_code"] == 200
