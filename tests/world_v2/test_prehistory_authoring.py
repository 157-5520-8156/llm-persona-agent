"""Offline creation packages through the real reviewed import boundary."""
from datetime import timedelta
import json
from pathlib import Path

import pytest

from companion_daemon.world_v2.character_prehistory import PrehistoryArchiveDocument, digest
from companion_daemon.world_v2.character_prehistory_runtime import PrehistoryArchiveRuntime
from companion_daemon.world_v2.prehistory_authoring import (
    PrehistoryAuthoringBrief, PrehistoryCreationReview, author_request, review_request,
    validate_draft, package_reviewed,
)
from test_character_prehistory import ACTOR, START, WORLD_ID, reviewed_archive, started_ledger


def brief():
    return PrehistoryAuthoringBrief(world_id=WORLD_ID, actor_ref=ACTOR,
        world_started_at=START, world_start_event_ref="world-start", world_start_event_hash="a" * 64,
        born_at=START.replace(year=2005), created_at=START, author_ref="author:fixture",
        draft_source_ref=reviewed_archive().document.source_artifact_ref,
        profile_source_ref="fixture:profile", profile_sha256="b" * 64,
        profile={"name": "角色", "background": "高中做过校刊"})


def review(b, document):
    return PrehistoryCreationReview.model_validate_json(json.dumps({
        "brief_hash": digest(b), "document_hash": digest(document),
        "reviewer_ref": "reviewer:fixture", "reviewed_at": START.isoformat(), "decision": "approved",
        "records": [{"record_id": r.record_id, "record_hash": digest(r), "verdict": "approve",
                     "rationale": "Offline fixture approves this exact source."} for r in document.records],
    }))


def test_reviewed_package_imports_without_choosing_what_character_remembers(tmp_path):
    b = brief()
    document = reviewed_archive().document
    assert author_request(b)["brief_hash"] == digest(b)
    packet = review_request(b, document)
    verdict = review(b, document)
    assert packet["record_hashes"] == {v.record_id: v.record_hash for v in verdict.records}
    packaged = package_reviewed(b, document, verdict, review_artifact_ref="fixture:review")
    assert packaged.review.review_artifact_hash == digest(verdict)
    ledger = started_ledger(tmp_path / "import.sqlite")
    try:
        runtime = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR)
        rows = runtime.import_reviewed(packaged, created_at=START)
        assert len(rows) == 1 and rows[0].record.statement == document.records[0].statement
        state = ledger.project()
        assert not state.memory_candidates and not state.experiences and not state.npcs
        assert runtime.import_reviewed(packaged, created_at=START) == rows
        assert ledger.rebuild().semantic_hash == state.semantic_hash
    finally:
        ledger.close()


@pytest.mark.parametrize("mutation", ["brief", "document", "record_hash", "record_id", "same_author", "early", "rejected"])
def test_review_cannot_approve_other_content_or_skip_independent_record_coverage(mutation):
    b = brief()
    document = reviewed_archive().document
    raw = review(b, document).model_dump(mode="json")
    if mutation in {"brief", "document"}:
        raw[mutation + "_hash"] = "c" * 64
    elif mutation == "record_hash":
        raw["records"][0]["record_hash"] = "c" * 64
    elif mutation == "record_id":
        raw["records"][0]["record_id"] = "prehistory-record:other"
    elif mutation == "same_author":
        raw["reviewer_ref"] = b.author_ref
    elif mutation == "early":
        raw["reviewed_at"] = (START - timedelta(days=1)).isoformat()
    else:
        raw.update(decision="rejected", cross_record_findings=["Inconsistent with accepted background."])
    with pytest.raises(ValueError):
        package_reviewed(b, document, PrehistoryCreationReview.model_validate_json(json.dumps(raw)),
                         review_artifact_ref="fixture:review")


def test_review_requires_all_records_and_cannot_cover_duplicates():
    b = brief()
    document = reviewed_archive().document
    second = document.records[0].model_copy(update={"record_id": "prehistory-record:second"})
    two = document.model_copy(update={"records": (*document.records, second)})
    verdict = review(b, two)
    with pytest.raises(ValueError, match="every exact record"):
        package_reviewed(b, two, verdict.model_copy(update={"records": verdict.records[:1]}),
                         review_artifact_ref="fixture:review")
    with pytest.raises(ValueError, match="duplicates"):
        PrehistoryCreationReview.model_validate_json(verdict.model_copy(update={
            "records": (verdict.records[0], verdict.records[0]),
        }).model_dump_json())


@pytest.mark.parametrize("mutation", ["world", "actor", "source", "runtime", "before_birth", "unknown_link", "accepted_record"])
def test_draft_keeps_original_world_lifetime_and_accepted_history(mutation):
    b = brief()
    document = reviewed_archive().document
    raw = document.model_dump(mode="json")
    if mutation in {"world", "actor", "source"}:
        raw[{"world": "world_id", "actor": "actor_ref", "source": "source_artifact_ref"}[mutation]] = "other"
    elif mutation in {"runtime", "before_birth"}:
        instant = START if mutation == "runtime" else b.born_at - timedelta(days=1)
        raw["records"][0].update(occurred_from=instant.isoformat(), occurred_until=instant.isoformat())
    elif mutation == "unknown_link":
        raw["records"][0]["related_record_refs"] = ["prehistory-record:missing"]
    else:
        b = b.model_copy(update={"accepted_archives": (document,)})
        raw["archive_id"] = "prehistory-archive:new"
    with pytest.raises(ValueError):
        validate_draft(b, PrehistoryArchiveDocument.model_validate_json(json.dumps(raw)))


def test_changed_draft_requires_fresh_review():
    b = brief()
    document = reviewed_archive().document
    verdict = review(b, document)
    edited = document.model_copy(update={"records": (document.records[0].model_copy(
        update={"statement": "另外一个尚未审核的片段。"}),)})
    with pytest.raises(ValueError, match="current brief and document"):
        package_reviewed(b, edited, verdict, review_artifact_ref="fixture:old-review")


def test_database_brief_uses_original_start_and_does_not_write_or_include_live_user(tmp_path):
    from prepare_character_prehistory import brief_from_database
    from test_life_projection import commit, event
    path = tmp_path / "source.sqlite"
    ledger = started_ledger(path)
    try:
        PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(reviewed_archive(), created_at=START)
        commit(ledger, [event("advance", "ClockAdvanced", {
            "logical_time_from": START.isoformat(), "logical_time_to": (START + timedelta(days=10)).isoformat(),
        }, at=START + timedelta(days=10))])
        before = ledger.project()
        profile = tmp_path / "profile.yaml"
        profile.write_text('name: 角色\nbase_prompt: private-user-prompt\nshared_history_facts: [private-user-history]\nbackground: 高中做过校刊\n')
        b = brief_from_database(database=path, profile_path=profile, world_id=WORLD_ID,
            actor_ref=ACTOR, born_at=START.replace(year=2005), author_ref="author:fixture", source_ref="draft:new")
        assert b.world_started_at == START and len(b.accepted_archives) == 1
        assert "private-user" not in json.dumps(author_request(b), ensure_ascii=False)
        assert ledger.project() == before
        assert ledger.rebuild().semantic_hash == before.semantic_hash
    finally:
        ledger.close()


def test_shipped_rich_draft_is_linked_but_not_approved():
    path = Path(__file__).resolve().parents[2] / "configs/prehistory/celia-life-draft-20260914.json"
    document = PrehistoryArchiveDocument.model_validate_json(path.read_text())
    base = reviewed_archive().document
    seeds = tuple(base.records[0].model_copy(update={"record_id": ref}) for ref in (
        "prehistory-record:celia-school-journal", "prehistory-record:celia-college-societies"))
    accepted = base.model_copy(update={"world_id": document.world_id, "actor_ref": document.actor_ref, "records": seeds})
    b = brief().model_copy(update={"world_id": document.world_id, "actor_ref": document.actor_ref,
        "draft_source_ref": document.source_artifact_ref, "accepted_archives": (accepted,)})
    validate_draft(b, document)
    assert len(document.records) == 24
    assert len({r.occurred_from.year for r in document.records}) >= 10
    assert len({r.location_ref for r in document.records}) >= 6
    assert sum(bool(r.related_record_refs) for r in document.records) >= 12
    assert "review" not in json.loads(path.read_text())


def test_cli_review_roundtrip_refuses_changed_draft_and_existing_output(tmp_path, capsys):
    from prepare_character_prehistory import main
    b = brief()
    document = reviewed_archive().document
    brief_path = tmp_path / "brief.json"
    draft_path = tmp_path / "draft.json"
    review_path = tmp_path / "review.json"
    output = tmp_path / "reviewed.json"
    brief_path.write_text(b.model_dump_json())
    draft_path.write_text(document.model_dump_json())
    review_path.write_text(review(b, document).model_dump_json())
    packet = tmp_path / "packet.json"
    main(["review-request", "--brief", str(brief_path), "--draft", str(draft_path), "--out", str(packet)])
    assert json.loads(packet.read_text())["document_hash"] == digest(document)
    args = ["package-reviewed", "--brief", str(brief_path), "--draft", str(draft_path),
            "--review", str(review_path), "--review-artifact-ref", "fixture:review", "--out", str(output)]
    main(args)
    assert json.loads(output.read_text())["review"]["manifest_hash"] == digest(document.manifest())
    original = output.read_bytes()
    with pytest.raises(FileExistsError):
        main(args)
    assert output.read_bytes() == original
    changed = document.model_copy(update={"records": (document.records[0].model_copy(
        update={"statement": "没有被这个审核批准的另一件事。"}),)})
    draft_path.write_text(changed.model_dump_json())
    refused = tmp_path / "refused.json"
    with pytest.raises(ValueError, match="current brief and document"):
        main([*args[:-1], str(refused)])
    assert not refused.exists()
    capsys.readouterr()



def test_database_brief_rejects_changed_source_event_hash(tmp_path):
    import sqlite3
    from prepare_character_prehistory import brief_from_database
    path = tmp_path / "damaged.sqlite"
    ledger = started_ledger(path)
    ledger.close()
    with sqlite3.connect(path) as db:
        db.execute("UPDATE world_v2_events SET event_hash=?", ("f" * 64,))
    profile = tmp_path / "profile.yaml"
    profile.write_text("base_prompt: fixture\n")
    with pytest.raises(ValueError, match="immutable hash"):
        brief_from_database(database=path, profile_path=profile, world_id=WORLD_ID,
            actor_ref=ACTOR, born_at=START.replace(year=2005), author_ref="author:fixture", source_ref="draft:new")



@pytest.mark.parametrize("operation", ["author", "review"])
def test_creation_packets_have_a_finite_byte_budget_without_evidence_truncation(operation):
    b = brief().model_copy(update={"profile": {"background": "很多既有人生材料" * 10000}})
    with pytest.raises(ValueError, match="byte budget"):
        if operation == "author":
            author_request(b)
        else:
            review_request(b, reviewed_archive().document)
