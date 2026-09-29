from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from companion_daemon.world_v2.character_prehistory import PrehistoryArchiveDocument, digest
from companion_daemon.world_v2.prehistory_life_materials import (
    LifeMaterialActor, LifeMaterialBlock, LifeMaterialsDraft, LifeRecollection, author_request, export_actor_archive,
)


START = datetime(2026, 1, 1, tzinfo=UTC)
BIRTH = datetime(2000, 1, 1, tzinfo=UTC)


def fixture_materials():
    from companion_daemon.world_v2.character_prehistory import HistoricalEntity
    entities = (
        HistoricalEntity(entity_ref="history:person:alice", kind="person", label="Alice"),
        HistoricalEntity(entity_ref="history:person:bob", kind="person", label="Bob"),
    )
    actors = (
        LifeMaterialActor(actor_ref="actor:alice", historical_entity_ref="history:person:alice", born_at=BIRTH),
        LifeMaterialActor(actor_ref="actor:bob", historical_entity_ref="history:person:bob", born_at=BIRTH),
    )
    blocks = (
        LifeMaterialBlock(block_id="shared", text="两人参加同一场活动。", allowed_actor_refs=("actor:alice", "actor:bob"), source_quote="原文片段", source_locator="draft:line-1", kind="event", event_ref="event:meet"),
        LifeMaterialBlock(block_id="alice-view", text="她当时觉得有些失落。", allowed_actor_refs=("actor:alice",), source_quote="作者新创", source_locator="draft:line-2", kind="historical_interpretation", event_ref="event:meet", subject_actor_ref="actor:alice"),
        LifeMaterialBlock(block_id="bob-view", text="他当时很期待再见面。", allowed_actor_refs=("actor:bob",), source_quote="作者新创", source_locator="draft:line-3", kind="historical_interpretation", event_ref="event:meet", subject_actor_ref="actor:bob"),
        LifeMaterialBlock(block_id="later", text="后来Alice才知道活动的结果。", allowed_actor_refs=("actor:alice",), source_quote="原文片段", source_locator="draft:line-4", kind="event", event_ref="event:meet"),
    )
    recollections = (
        LifeRecollection(record_id="prehistory-record:alice-meet", actor_ref="actor:alice", known_from=datetime(2020, 3, 1, tzinfo=UTC), known_until=datetime(2020, 3, 1, tzinfo=UTC), time_precision="day", block_ids=("shared", "alice-view"), participant_refs=("history:person:bob",), privacy_class="personal"),
        LifeRecollection(record_id="prehistory-record:bob-meet", actor_ref="actor:bob", known_from=datetime(2020, 3, 1, tzinfo=UTC), known_until=datetime(2020, 3, 1, tzinfo=UTC), time_precision="day", block_ids=("shared", "bob-view"), participant_refs=("history:person:alice",), privacy_class="personal"),
        LifeRecollection(record_id="prehistory-record:alice-later", actor_ref="actor:alice", known_from=datetime(2020, 3, 5, tzinfo=UTC), known_until=datetime(2020, 3, 5, tzinfo=UTC), time_precision="day", block_ids=("later",), participant_refs=("history:person:bob",), privacy_class="personal"),
    )
    return LifeMaterialsDraft(world_id="world:test", world_started_at=START, source_artifact_ref="draft:life-v1", entities=entities, actors=actors, blocks=blocks, recollections=recollections)


def test_exports_actor_specific_views_and_respects_knowledge_time():
    draft = fixture_materials()
    alice = export_actor_archive(draft, "actor:alice", "prehistory-archive:alice")
    bob = export_actor_archive(draft, "actor:bob", "prehistory-archive:bob")
    assert len(alice.records) == 2 and len(bob.records) == 1
    assert "[Alice的历史感受/解释（当时）]" in alice.records[0].statement
    assert "[Bob的历史感受/解释（当时）]" in bob.records[0].statement
    assert alice.records[0].record_id != bob.records[0].record_id
    assert alice.source_artifact_ref == "life-materials:sha256:" + digest(draft)
    visible = export_actor_archive(draft, "actor:alice", "prehistory-archive:alice-early", as_of=datetime(2020, 3, 2, tzinfo=UTC))
    assert [r.record_id for r in visible.records] == ["prehistory-record:alice-meet"]
    assert "history:person:bob" in {e.entity_ref for e in visible.entities}
    assert "history:person:alice" in {e.entity_ref for e in visible.entities}
    cold = PrehistoryArchiveDocument.model_validate_json(visible.model_dump_json())
    assert cold.manifest() == visible.manifest()


def test_unknown_npc_birth_can_be_stored_but_not_exported():
    draft = fixture_materials()
    actors = tuple(a.model_copy(update={"born_at": None}) if a.actor_ref == "actor:bob" else a for a in draft.actors)
    draft = draft.model_copy(update={"actors": actors})
    with pytest.raises(ValueError, match="birth date is unknown"):
        export_actor_archive(draft, "actor:bob", "prehistory-archive:bob")


def test_rejects_cross_actor_related_record_and_private_block_leak():
    draft = fixture_materials()
    recollections = list(draft.recollections)
    recollections[0] = recollections[0].model_copy(update={"related_record_refs": ("prehistory-record:bob-meet",)})
    with pytest.raises((ValidationError, ValueError), match="same actor"):
        LifeMaterialsDraft.model_validate({**draft.model_dump(), "recollections": tuple(r.model_dump() for r in recollections)})
    recollections = list(draft.recollections)
    recollections[0] = recollections[0].model_copy(update={"block_ids": ("bob-view",)})
    with pytest.raises((ValidationError, ValueError), match="not allowed"):
        LifeMaterialsDraft.model_validate({**draft.model_dump(), "recollections": tuple(r.model_dump() for r in recollections)})


def test_world_start_is_an_exclusive_knowledge_boundary_and_identity_collisions_are_rejected():
    draft = fixture_materials()
    rows = list(draft.recollections)
    rows[0] = rows[0].model_copy(update={"known_until": START, "time_precision": "interval"})
    boundary = LifeMaterialsDraft.model_validate({**draft.model_dump(), "recollections": tuple(r.model_dump() for r in rows)})
    archive = export_actor_archive(boundary, "actor:alice", "prehistory-archive:world-boundary")
    assert archive.records[0].occurred_until == START - timedelta(microseconds=1)
    duplicated = {**draft.model_dump(), "actors": tuple(a.model_dump() for a in draft.actors) + (draft.actors[0].model_dump(),)}
    with pytest.raises(ValidationError, match="duplicate actor"):
        LifeMaterialsDraft.model_validate(duplicated)


def test_long_material_is_rejected_without_truncation_and_hash_is_stable():
    draft = fixture_materials()
    blocks = list(draft.blocks)
    blocks[1] = blocks[1].model_copy(update={"text": "x" * 1595})
    # Export metadata and labels count toward the archive's actual statement limit.
    too_long = draft.model_copy(update={"blocks": tuple(blocks)})
    with pytest.raises(ValueError, match="exceeds archive statement limit"):
        export_actor_archive(too_long, "actor:alice", "prehistory-archive:alice")
    assert digest(draft) == digest(LifeMaterialsDraft.model_validate_json(draft.model_dump_json()))


def test_celia_pilot_preserves_sources_private_views_and_existing_history():
    import hashlib
    from pathlib import Path
    from companion_daemon.world_v2.prehistory_authoring import PrehistoryAuthoringBrief, validate_draft

    root = Path(__file__).resolve().parents[2]
    story_path = root / "docs/design/celia-life-arc-v2-2026-09-29.md"
    story = story_path.read_text()
    materials = LifeMaterialsDraft.model_validate_json(
        (root / "fixtures/world_v2/celia_life_materials_pilot.json").read_text()
    )
    assert materials.source_artifact_ref == (
        "docs/design/celia-life-arc-v2-2026-09-29.md#sha256="
        + hashlib.sha256(story_path.read_bytes()).hexdigest()
    )
    assert all(block.source_quote in story for block in materials.blocks)
    archive = export_actor_archive(materials, "agent:companion", "prehistory-archive:celia-lifeline-materials-20260929")
    saved = PrehistoryArchiveDocument.model_validate_json(
        (root / "fixtures/world_v2/celia_lifeline_prehistory_unreviewed.json").read_text()
    )
    assert archive == saved
    assert len(archive.records) == 17
    body = "\n".join(record.statement for record in archive.records)
    for block in materials.blocks:
        if "agent:companion" not in block.allowed_actor_refs:
            assert block.text not in body
    early = export_actor_archive(
        materials, "agent:companion", "prehistory-archive:celia-early",
        as_of=datetime(2025, 6, 1, tzinfo=UTC),
    )
    assert "prehistory-record:celia-lifeline-12" in {r.record_id for r in early.records}
    assert "prehistory-record:celia-lifeline-16" not in {r.record_id for r in early.records}
    assert all("我用了，客人问过" not in r.statement for r in early.records)
    existing = PrehistoryArchiveDocument.model_validate_json(
        (root / "configs/prehistory/celia-everyday-20260929.json").read_text()
    )
    before = digest(existing)
    brief = PrehistoryAuthoringBrief(
        world_id=materials.world_id, actor_ref=archive.actor_ref,
        world_started_at=materials.world_started_at,
        world_start_event_ref="fixture:original-world-start", world_start_event_hash="a" * 64,
        born_at=materials.actors[0].born_at, created_at=materials.world_started_at,
        author_ref="author:offline-test", draft_source_ref=archive.source_artifact_ref,
        profile_source_ref="fixture:profile", profile_sha256="b" * 64,
        profile={}, accepted_archives=(existing,),
    )
    validate_draft(brief, archive)
    assert digest(existing) == before


def test_rejects_shared_actor_identity_future_birth_and_impossible_foresight():
    draft = fixture_materials()
    actors = tuple(a.model_copy(update={"historical_entity_ref": "history:person:alice"}) for a in draft.actors)
    with pytest.raises(ValidationError, match="duplicate actor historical identity"):
        LifeMaterialsDraft.model_validate({**draft.model_dump(), "actors": actors})
    actors = tuple(a.model_copy(update={"born_at": START}) if a.actor_ref == "actor:alice" else a for a in draft.actors)
    with pytest.raises(ValidationError, match="birth must precede"):
        LifeMaterialsDraft.model_validate({**draft.model_dump(), "actors": actors})
    blocks = list(draft.blocks)
    blocks[0] = blocks[0].model_copy(update={"occurred_from": datetime(2020, 3, 2, tzinfo=UTC), "occurred_until": datetime(2020, 3, 2, tzinfo=UTC)})
    with pytest.raises(ValidationError, match="predates the event"):
        LifeMaterialsDraft.model_validate({**draft.model_dump(), "blocks": tuple(blocks)})


def test_equal_labels_keep_distinct_identities_and_cli_never_overwrites(tmp_path):
    from scripts.prepare_life_materials_prehistory import main
    draft = fixture_materials()
    entities = tuple(e.model_copy(update={"label": "同名"}) for e in draft.entities)
    draft = draft.model_copy(update={"entities": entities})
    output = export_actor_archive(draft, "actor:alice", "prehistory-archive:same-name")
    assert {e.entity_ref for e in output.entities} == {"history:person:alice", "history:person:bob"}
    materials_path = tmp_path / "materials.json"
    output_path = tmp_path / "archive.json"
    materials_path.write_text(draft.model_dump_json(), encoding="utf-8")
    argv = ["--materials", str(materials_path), "--actor-ref", "actor:alice",
            "--archive-id", "prehistory-archive:cli", "--output", str(output_path)]
    main(argv)
    before = output_path.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        main(argv)
    assert output_path.read_text(encoding="utf-8") == before


def test_author_request_binds_optional_narrative_and_reuses_packet_limit():
    import hashlib
    from companion_daemon.world_v2.prehistory_authoring import PrehistoryAuthoringBrief
    brief = PrehistoryAuthoringBrief(
        world_id="world:test", actor_ref="actor:alice", world_started_at=START,
        world_start_event_ref="event:start", world_start_event_hash="a" * 64,
        born_at=BIRTH, created_at=START, author_ref="author:test", draft_source_ref="life-materials:sha256:" + "b" * 64,
        profile_source_ref="fixture:profile", profile_sha256="c" * 64, profile={"name": "Alice"},
    )
    text = "小说正文片段"
    packet = author_request(brief, narrative_text=text)
    assert packet["brief_hash"] == digest(brief)
    assert packet["narrative"] == {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}
    assert "archive_schema" not in packet
