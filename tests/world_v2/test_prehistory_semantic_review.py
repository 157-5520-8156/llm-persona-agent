"""Bind semantic judgments to submitted evidence, without model-copied hashes."""
from datetime import timedelta
import json

import pytest

from companion_daemon.world_v2.character_prehistory import digest
from companion_daemon.world_v2.prehistory_authoring import (
    PrehistorySemanticReview,
    bind_semantic_review, package_reviewed, semantic_review_request,
)
from companion_daemon.world_v2.prehistory_story_links import (
    PrehistoryStoryLinkArtifact,
)
from test_prehistory_authoring import brief
from test_character_prehistory import START, reviewed_archive


def materials():
    document = reviewed_archive().document
    second = document.records[0].model_copy(update={"record_id": "prehistory-record:second",
        "statement": "另一条单独接受审核的童年片段。"})
    document = document.model_copy(update={"records": (*document.records, second)})
    return brief(), document


def parsed(value):
    return PrehistorySemanticReview.model_validate_json(json.dumps(value))


def response():
    return parsed({"decision": "approved", "records": [
        {"record_index": 1, "verdict": "approve", "rationale": "Second record fixture rationale."},
        {"record_index": 0, "verdict": "approve", "rationale": "First record fixture rationale."},
    ]})


def response_with_link(*, verdict="approve", decision="approved"):
    value = response().model_dump(mode="json")
    value["decision"] = decision
    value["story_links"] = [{
        "relation_index": 0, "verdict": verdict,
        "rationale": "后一段明确回指前一段。" if verdict == "approve" else "引用不能证明两段相连。",
    }]
    return parsed(value)


def bind(packet, reply=None, **kwargs):
    return bind_semantic_review(packet, reply or response(), reviewer_ref=kwargs.get("reviewer_ref", "reviewer:fixture"),
                                reviewed_at=kwargs.get("reviewed_at", START))


def test_out_of_order_judgments_bind_each_exact_submitted_record():
    b, document = materials()
    packet = semantic_review_request(b, document)
    result = bind(packet)
    assert result.document_hash == digest(document)
    assert [r.record_hash for r in result.records] == [digest(r) for r in document.records]
    assert [r.rationale for r in result.records] == ["First record fixture rationale.", "Second record fixture rationale."]
    approved = package_reviewed(b, document, result, review_artifact_ref="fixture:semantic-review")
    assert approved.review.review_artifact_hash == digest(result)
    changed = document.model_copy(update={"records": tuple(reversed(document.records))})
    with pytest.raises(ValueError, match="current brief and document"):
        package_reviewed(b, changed, result, review_artifact_ref="fixture:semantic-review")


def test_story_link_evidence_is_pinned_into_semantic_review_request():
    from test_prehistory_story_links import ACTOR as LINK_ACTOR, STORY, _draft, _materials
    from companion_daemon.world_v2.prehistory_life_materials import export_actor_archive
    from companion_daemon.world_v2.prehistory_story_links import apply_story_links

    source_materials = _materials()
    links = _draft(source_materials)
    linked_materials = apply_story_links(
        source_materials, actor_ref=LINK_ACTOR, draft=links, narrative_text=STORY,
    )
    document = export_actor_archive(linked_materials, LINK_ACTOR, "prehistory-archive:story-review")
    b = brief().model_copy(update={
        "world_id": document.world_id, "actor_ref": document.actor_ref,
        "world_started_at": linked_materials.world_started_at,
        "born_at": linked_materials.actors[0].born_at,
        "draft_source_ref": document.source_artifact_ref,
    })
    link_artifact = PrehistoryStoryLinkArtifact(
        request_hash="b" * 64,
        source_artifact_ref=source_materials.source_artifact_ref,
        source_materials_hash=digest(source_materials),
        linked_materials_hash=digest(linked_materials),
        draft=links,
    )
    packet = semantic_review_request(
        b, document, story_links=link_artifact, narrative_text=STORY,
        source_materials=linked_materials,
    )
    assert packet["story_link_artifact_hash"] == digest(link_artifact)
    assert packet["narrative_text"] == STORY
    assert packet["narrative_source_artifact_ref"] == source_materials.source_artifact_ref
    assert packet["source_materials"]["recollections"] == linked_materials.model_dump(mode="json")["recollections"]
    with pytest.raises(ValueError, match="every submitted story-link index"):
        bind(packet, response())
    assert bind(packet, response_with_link()).decision == "approved"
    rejected = bind(packet, response_with_link(verdict="reject", decision="rejected"))
    assert rejected.decision == "rejected" and rejected.story_links[0].verdict == "reject"
    packet["story_link_artifact"]["draft"]["relations"][0]["rationale"] = "声称二人相识"
    with pytest.raises(ValueError, match="submitted semantic review packet"):
        bind(packet)


def test_semantic_review_rejects_narrative_or_materials_from_another_source():
    from test_prehistory_story_links import ACTOR as LINK_ACTOR, STORY, _draft, _materials
    from companion_daemon.world_v2.prehistory_life_materials import export_actor_archive
    from companion_daemon.world_v2.prehistory_story_links import apply_story_links

    source_materials = _materials()
    links = _draft(source_materials)
    linked = apply_story_links(source_materials, actor_ref=LINK_ACTOR, draft=links, narrative_text=STORY)
    document = export_actor_archive(linked, LINK_ACTOR, "prehistory-archive:story-review")
    b = brief().model_copy(update={
        "world_id": document.world_id, "actor_ref": document.actor_ref,
        "world_started_at": linked.world_started_at, "born_at": linked.actors[0].born_at,
        "draft_source_ref": document.source_artifact_ref,
    })
    artifact = PrehistoryStoryLinkArtifact(
        request_hash="b" * 64, source_artifact_ref=source_materials.source_artifact_ref,
        source_materials_hash=digest(source_materials), linked_materials_hash=digest(linked),
        draft=links,
    )
    with pytest.raises(ValueError, match="original narrative"):
        semantic_review_request(
            b, document, story_links=artifact, narrative_text=STORY + "另一篇原文。",
            source_materials=linked,
        )


def test_source_complete_review_can_audit_records_before_any_story_links_exist():
    from test_prehistory_story_links import ACTOR as LINK_ACTOR, STORY, _materials
    from companion_daemon.world_v2.prehistory_life_materials import export_actor_archive

    source_materials = _materials()
    document = export_actor_archive(source_materials, LINK_ACTOR, "prehistory-archive:unlinked-review")
    b = brief().model_copy(update={
        "world_id": document.world_id, "actor_ref": document.actor_ref,
        "world_started_at": source_materials.world_started_at,
        "born_at": source_materials.actors[0].born_at,
        "draft_source_ref": document.source_artifact_ref,
    })
    packet = semantic_review_request(
        b, document, narrative_text=STORY, source_materials=source_materials,
    )
    assert "story_link_artifact" not in packet
    assert packet["narrative_text"] == STORY
    assert bind(packet).decision == "approved"


def test_review_request_cli_can_emit_source_complete_offline_packet(tmp_path, capsys):
    from test_prehistory_story_links import ACTOR as LINK_ACTOR, STORY, _draft, _materials
    from companion_daemon.world_v2.prehistory_life_materials import export_actor_archive
    from companion_daemon.world_v2.prehistory_story_links import apply_story_links
    from prepare_character_prehistory import main

    source_materials = _materials()
    links = _draft(source_materials)
    linked = apply_story_links(source_materials, actor_ref=LINK_ACTOR, draft=links, narrative_text=STORY)
    document = export_actor_archive(linked, LINK_ACTOR, "prehistory-archive:cli-review")
    b = brief().model_copy(update={
        "world_id": document.world_id, "actor_ref": document.actor_ref,
        "world_started_at": linked.world_started_at, "born_at": linked.actors[0].born_at,
        "draft_source_ref": document.source_artifact_ref,
    })
    artifact = PrehistoryStoryLinkArtifact(
        request_hash="c" * 64, source_artifact_ref=source_materials.source_artifact_ref,
        source_materials_hash=digest(source_materials), linked_materials_hash=digest(linked),
        draft=links,
    )
    brief_path, archive_path, materials_path, links_path = [tmp_path / f"{name}.json" for name in
        ("brief", "archive", "materials", "links")]
    narrative_path, request_path = tmp_path / "novel.md", tmp_path / "review-request.json"
    brief_path.write_text(b.model_dump_json())
    archive_path.write_text(document.model_dump_json())
    materials_path.write_text(linked.model_dump_json())
    links_path.write_text(artifact.model_dump_json())
    narrative_path.write_text(STORY)
    main(["semantic-review-request", "--brief", str(brief_path), "--draft", str(archive_path),
          "--narrative", str(narrative_path), "--materials", str(materials_path),
          "--story-links", str(links_path), "--out", str(request_path)])
    summary = json.loads(capsys.readouterr().out)
    packet = json.loads(request_path.read_text())
    assert summary["provider_calls"] == summary["database_writes"] == 0
    assert packet["narrative_text"] == STORY
    assert packet["story_link_artifact_hash"] == digest(artifact)
    assert packet["source_materials"]["source_artifact_ref"] == source_materials.source_artifact_ref
    assert request_path.stat().st_mode & 0o777 == 0o600
    assert bind(packet, response_with_link()).decision == "approved"


@pytest.mark.parametrize("mutation", ["document_hash", "brief_hash", "record_hash", "document", "instruction", "contract"])
def test_modified_submitted_packet_cannot_be_rebound(mutation):
    b, document = materials()
    packet = semantic_review_request(b, document)
    if mutation in {"document_hash", "brief_hash"}:
        packet[mutation] = "e" * 64
    elif mutation == "record_hash":
        packet["record_hashes"][document.records[0].record_id] = "e" * 64
    elif mutation == "document":
        packet["document"]["records"][0]["statement"] = "后来改写的不同事件。"
    else:
        packet[mutation] = "different-contract-or-instruction"
    with pytest.raises(ValueError, match="submitted semantic review packet"):
        bind(packet)


@pytest.mark.parametrize("indexes", [[0], [0, 2], [0, 0], [-1, 1], [False, 1], [0, "1"]])
def test_missing_duplicate_out_of_range_and_coerced_indexes_never_approve(indexes):
    b, document = materials()
    with pytest.raises(ValueError):
        reply = parsed({"decision": "approved", "records": [
            {"record_index": i, "verdict": "approve", "rationale": "Fixture."} for i in indexes]})
        bind(semantic_review_request(b, document), reply)


def test_rejections_survive_binding_and_old_invalid_hash_reply_is_not_reinterpreted():
    b, document = materials()
    raw = response().model_dump(mode="json")
    raw.update(decision="rejected", cross_record_findings=["These two events contradict each other."])
    result = bind(semantic_review_request(b, document), parsed(raw))
    assert result.decision == "rejected" and result.cross_record_findings == tuple(raw["cross_record_findings"])
    with pytest.raises(ValueError, match="rejected draft"):
        package_reviewed(b, document, result, review_artifact_ref="fixture:rejected")
    with pytest.raises(ValueError):
        parsed(result.model_dump(mode="json"))
    raw["decision"] = "approved"
    with pytest.raises(ValueError, match="decision disagrees"):
        parsed(raw)


@pytest.mark.parametrize("kwargs", [{"reviewer_ref": "author:fixture"},
    {"reviewed_at": START - timedelta(seconds=1)}, {"reviewed_at": START.replace(tzinfo=None)}])
def test_binding_still_checks_operator_provenance(kwargs):
    b, document = materials()
    with pytest.raises(ValueError):
        bind(semantic_review_request(b, document), **kwargs)


def test_cli_uses_the_saved_request_and_preserves_original_judgment(tmp_path, capsys):
    from prepare_character_prehistory import main
    b, document = materials()
    bpath, dpath, request_path, response_path, review_path = [tmp_path / f"{name}.json" for name in
        ("brief", "draft", "request", "response", "review")]
    bpath.write_text(b.model_dump_json())
    dpath.write_text(document.model_dump_json())
    main(["semantic-review-request", "--brief", str(bpath), "--draft", str(dpath), "--out", str(request_path)])
    response_path.write_text(response().model_dump_json())
    main(["bind-semantic-review", "--request", str(request_path), "--response", str(response_path),
          "--reviewer-ref", "reviewer:fixture", "--reviewed-at", START.isoformat(), "--out", str(review_path)])
    assert json.loads(review_path.read_text())["document_hash"] == digest(document)
    assert json.loads(response_path.read_text()) == response().model_dump(mode="json")
    capsys.readouterr()
