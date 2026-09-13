"""Reviewed pre-start history enters the same real ledger as runtime life."""
from datetime import UTC, datetime, timedelta
import json

import pytest

from companion_daemon.world_v2.character_prehistory import (
    PrehistoryArchiveDocument, PrehistoryReview, ReviewedPrehistoryArchive, digest,
)
from companion_daemon.world_v2.character_prehistory_runtime import PrehistoryArchiveRuntime
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_life_projection import commit, event, WORLD_ID

ACTOR = "actor:companion"
START = datetime(2026, 9, 1, tzinfo=UTC)


def reviewed_archive():
    document = PrehistoryArchiveDocument.model_validate_json(json.dumps({
        "contract": "character-prehistory-archive.1", "archive_id": "prehistory-archive:school",
        "world_id": WORLD_ID, "actor_ref": ACTOR,
        "source_artifact_ref": "fixture:reviewed-character-background.1",
        "entities": [
            {"entity_ref": "history:person:school-editor", "kind": "person", "label": "校刊同学"},
            {"entity_ref": "history:place:school-office", "kind": "place", "label": "校刊编辑室"},
        ],
        "records": [{
            "record_id": "prehistory-record:school-magazine", "occurred_from": "2023-04-01T00:00:00Z",
            "occurred_until": "2023-04-30T23:59:59Z", "time_precision": "month",
            "statement": "高中时参与过校刊，和校刊同学一起核对过一期稿件。",
            "participant_refs": ["history:person:school-editor"],
            "location_ref": "history:place:school-office", "related_record_refs": [],
            "privacy_class": "private",
        }],
    }))
    review = PrehistoryReview(
        manifest_hash=digest(document.manifest()), reviewer_ref="operator:offline-fixture",
        review_artifact_ref="fixture:independent-review.1", review_artifact_hash="a" * 64,
        reviewed_at=START, decision="approved",
    )
    return ReviewedPrehistoryArchive(document=document, review=review)


def started_ledger(path):
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    commit(ledger, [event("world-start", "WorldStarted", {}, at=START)])
    return ledger


def test_import_is_source_bound_atomic_and_idempotent_after_restart(tmp_path):
    path = tmp_path / "prehistory.sqlite"
    ledger = started_ledger(path)
    archive = reviewed_archive()
    try:
        runtime = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR)
        records = runtime.import_reviewed(archive, created_at=START)
        assert len(records) == 1
        assert records[0].record.occurred_until < START
        assert records[0].accepted_at == START
        assert records[0].world_started_at == START
        projection = ledger.project()
        assert projection.experiences == () and projection.npcs == () and projection.plans == ()
        assert projection.memory_candidates == (), "import must not decide what the character remembers"
        assert runtime.import_reviewed(archive, created_at=START + timedelta(days=1)) == records
        assert ledger.project().world_revision == projection.world_revision
        assert ledger.rebuild().semantic_hash == projection.semantic_hash
        ledger.close()
        ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
        assert PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(
            archive, created_at=START + timedelta(days=2),
        ) == records
        assert ledger.project().semantic_hash == projection.semantic_hash
    finally:
        ledger.close()


@pytest.mark.parametrize("mutation", ["world", "actor", "review", "runtime_date", "unknown_person", "user_person"])
def test_invalid_archive_cannot_partially_write_world(tmp_path, mutation):
    ledger = started_ledger(tmp_path / "rejected.sqlite")
    original = reviewed_archive().model_dump(mode="json")
    if mutation == "world":
        original["document"]["world_id"] = "world:other"
    elif mutation == "actor":
        original["document"]["actor_ref"] = "actor:other"
    elif mutation == "review":
        original["document"]["records"][0]["statement"] = "未经审核的新内容。"
    elif mutation == "runtime_date":
        original["document"]["records"][0].update(
            occurred_from=START.isoformat(), occurred_until=START.isoformat(), time_precision="day",
        )
    else:
        original["document"]["records"][0]["participant_refs"] = [
            "history:person:missing" if mutation == "unknown_person" else "user:real-person"
        ]
    before = ledger.project()
    try:
        with pytest.raises(ValueError):
            if mutation != "review":
                # Independently approved invalid scope/time still cannot pass import.
                parsed = PrehistoryArchiveDocument.model_validate_json(json.dumps(original["document"]))
                original["review"]["manifest_hash"] = digest(parsed.manifest())
            value = ReviewedPrehistoryArchive.model_validate_json(json.dumps(original))
            PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(value, created_at=START)
        assert ledger.project() == before
    finally:
        ledger.close()


def reapprove(document):
    old = reviewed_archive().review
    return ReviewedPrehistoryArchive(
        document=document,
        review=old.model_copy(update={"manifest_hash": digest(document.manifest())}),
    )


def test_later_import_keeps_original_start_and_cannot_fill_runtime_gap(tmp_path):
    ledger = started_ledger(tmp_path / "later.sqlite")
    later = START + timedelta(days=10)
    try:
        commit(ledger, [event("later-clock", "ClockAdvanced", {
            "logical_time_from": START.isoformat(), "logical_time_to": later.isoformat(),
        }, at=later)])
        archive = reviewed_archive()
        rows = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(archive, created_at=later)
        assert rows[0].accepted_at == later and rows[0].world_started_at == START
        value = archive.document.model_dump(mode="json")
        value["archive_id"] = "prehistory-archive:forged-gap"
        value["records"][0].update(record_id="prehistory-record:runtime-gap",
            occurred_from=(START + timedelta(days=2)).isoformat(),
            occurred_until=(START + timedelta(days=2)).isoformat(), time_precision="day")
        changed = reapprove(PrehistoryArchiveDocument.model_validate_json(json.dumps(value)))
        before = ledger.project()
        with pytest.raises(ValueError, match="runtime life"):
            PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(changed, created_at=later)
        assert ledger.project() == before
        assert ledger.rebuild().semantic_hash == before.semantic_hash
    finally:
        ledger.close()


@pytest.mark.parametrize("field", ["actor_ref", "accepted_payload_hash", "world_started_at", "record", "omit"])
def test_projection_cannot_forge_history_or_owner(tmp_path, field):
    from companion_daemon.world_v2.schemas import LedgerProjection

    ledger = started_ledger(tmp_path / "projection.sqlite")
    try:
        PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(reviewed_archive(), created_at=START)
        raw = ledger.project().model_dump(mode="json")
        record = raw["prehistory_records"][0]
        if field == "omit":
            raw["prehistory_records"] = []
        elif field == "record":
            record["record"]["statement"] = "未发生的新事件。"
        else:
            record[field] = {"actor_ref": "actor:other", "accepted_payload_hash": "f" * 64,
                             "world_started_at": (START + timedelta(days=1)).isoformat()}[field]
        with pytest.raises(ValueError, match="prehistory"):
            LedgerProjection.model_validate_json(json.dumps(raw))
    finally:
        ledger.close()


def test_replay_requires_reviewed_importer_and_exact_reviewed_record(tmp_path):
    from companion_daemon.world_v2.reducers import ReducerState, reduce_event
    from companion_daemon.world_v2.schemas import WorldEvent
    from companion_daemon.world_v2.character_prehistory import PrehistoryRecordImportedPayload, prehistory_event_id

    ledger = started_ledger(tmp_path / "replay.sqlite")
    try:
        PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(reviewed_archive(), created_at=START)
        state = ReducerState()
        for row in ledger.export_replay_evidence().events:
            original = row.event
            if original.event_type.startswith("CharacterPrehistory"):
                with pytest.raises(ValueError, match="reviewed importer"):
                    reduce_event(state, original.model_copy(update={"actor": ACTOR}))
            if original.event_type == "CharacterPrehistoryRecordImported":
                value = original.payload()
                value["record"]["statement"] = "试图通过重算哈希补写的新历史。"
                payload = PrehistoryRecordImportedPayload.model_validate_json(json.dumps(value))
                identity = prehistory_event_id(world_id=WORLD_ID, event_type=original.event_type, payload=payload)
                envelope = original.model_dump(exclude={"payload_json", "payload_hash"})
                changed = WorldEvent.from_payload(**{**envelope, "event_id": identity, "idempotency_key": identity}, payload=value)
                with pytest.raises(ValueError, match="reviewed history"):
                    reduce_event(state, changed)
            state = reduce_event(state, original)
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_application_import_and_owner_counts_do_not_expose_archive_as_memory(tmp_path):
    import httpx
    from companion_daemon.llm import DeepSeekChatModel
    import test_world_stimulus_life_intent as shared
    from companion_daemon.world_v2.dashboard_home_snapshot import DashboardHomeSnapshotModule

    data = reviewed_archive().document.model_dump(mode="json")
    data.update(world_id=shared.WORLD, actor_ref=shared.ACTOR)
    archive = reapprove(PrehistoryArchiveDocument.model_validate_json(json.dumps(data)))
    provider = shared._RoleHTTP()
    model = DeepSeekChatModel("offline-fixture", "https://fixture.invalid", "deepseek-v4-flash",
                             thinking_enabled=False, transport=httpx.MockTransport(provider))
    path = tmp_path / "app.sqlite"
    app = shared._build(path, model, reviewed_prehistory=archive)
    try:
        before = app.export_replay_evidence().projection
        assert len(before.prehistory_records) == 1 and before.memory_candidates == ()
        assert provider.requests == []
        snapshot = await DashboardHomeSnapshotModule(
            ledger=app._ledger, deployment_id="fixture", boot_id="fixture", clock=lambda: shared.NOW,
        ).capture()
        raw = json.dumps(snapshot.to_payload(), ensure_ascii=False)
        assert "启动前历史片段" in raw
        assert archive.document.records[0].statement not in raw
        assert "history:person:school-editor" not in raw
        from companion_daemon.world_v2.world_turn_runtime import InboundTurn
        await app.respond(InboundTurn(
            platform="test", platform_user_id="user.1", platform_message_id="prehistory-question",
            text="你以前有什么难忘的事？", observed_at=shared.NOW, trace_id="trace:prehistory-question",
        ))
        assert archive.document.records[0].statement not in json.dumps(provider.requests, ensure_ascii=False)
        before = app.export_replay_evidence().projection
        app.close()
        app = shared._build(path, model, reviewed_prehistory=archive)
        assert app.export_replay_evidence().projection == before
    finally:
        app.close()
        await model.aclose()


def test_local_month_precision_survives_canonical_world_bytes_and_reimport(tmp_path):
    ledger = started_ledger(tmp_path / "timezone.sqlite")
    try:
        data = reviewed_archive().document.model_dump(mode="json")
        data["records"][0].update(
            timezone_name="Asia/Shanghai", occurred_from="2023-04-01T00:00:00+08:00",
            occurred_until="2023-04-30T23:59:59+08:00",
        )
        archive = reapprove(PrehistoryArchiveDocument.model_validate_json(json.dumps(data)))
        runtime = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR)
        rows = runtime.import_reviewed(archive, created_at=START)
        assert rows[0].record.time_precision == "month"
        assert rows[0].record.timezone_name == "Asia/Shanghai"
        before = ledger.project()
        assert runtime.import_reviewed(archive, created_at=START) == rows
        assert ledger.rebuild().semantic_hash == before.semantic_hash
    finally:
        ledger.close()


def test_bulk_import_rolls_back_and_retries_without_a_partial_archive(tmp_path, monkeypatch):
    import companion_daemon.world_v2.sqlite_ledger as sqlite_module

    data = reviewed_archive().document.model_dump(mode="json")
    extra = {**data["records"][0], "record_id": "prehistory-record:school-review",
             "related_record_refs": ["prehistory-record:school-magazine"], "statement": "那次核稿后又检查了一遍标题。"}
    data["records"].append(extra)
    archive = reapprove(PrehistoryArchiveDocument.model_validate_json(json.dumps(data)))
    ledger = started_ledger(tmp_path / "atomic.sqlite")
    original = sqlite_module.reduce_event
    calls = []

    def interrupted(state, event, **kwargs):
        if event.event_type == "CharacterPrehistoryRecordImported":
            calls.append(event.event_id)
            if len(calls) == 2:
                raise ValueError("injected interrupted archive batch")
        return original(state, event, **kwargs)

    try:
        before = ledger.project()
        with monkeypatch.context() as patch:
            patch.setattr(sqlite_module, "reduce_event", interrupted)
            with pytest.raises(ValueError, match="interrupted archive batch"):
                PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(archive, created_at=START)
        assert len(calls) == 2 and ledger.project() == before
        assert ledger.rebuild().semantic_hash == before.semantic_hash
        records = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(archive, created_at=START)
        assert len(records) == 2
        after = ledger.project()
        assert ledger.rebuild().semantic_hash == after.semantic_hash
        # Subsequent runtime events keep the complete history in fast head updates.
        commit(ledger, [event("after-import-clock", "ClockAdvanced", {
            "logical_time_from": START.isoformat(), "logical_time_to": (START + timedelta(days=1)).isoformat(),
        }, at=START + timedelta(days=1))])
        assert ledger.project().prehistory_records == after.prehistory_records
        assert ledger.rebuild().semantic_hash == ledger.project().semantic_hash
    finally:
        ledger.close()


def test_archive_cannot_link_another_actors_private_history(tmp_path):
    ledger = started_ledger(tmp_path / "actors.sqlite")
    try:
        PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(reviewed_archive(), created_at=START)
        data = reviewed_archive().document.model_dump(mode="json")
        data.update(actor_ref="actor:other", archive_id="prehistory-archive:other")
        data["records"][0].update(record_id="prehistory-record:other", related_record_refs=["prehistory-record:school-magazine"])
        archive = reapprove(PrehistoryArchiveDocument.model_validate_json(json.dumps(data)))
        before = ledger.project()
        with pytest.raises(ValueError, match="unknown related records"):
            PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref="actor:other").import_reviewed(archive, created_at=START)
        assert ledger.project() == before
    finally:
        ledger.close()
