"""Historical sources through the real memory ledger; choices are test fixtures.

These checks do not claim real CharacterInterior retention or source review.
"""
from datetime import timedelta
import hashlib
import json

import pytest

from companion_daemon.world_v2.character_prehistory import digest
from companion_daemon.world_v2.character_prehistory_runtime import PrehistoryArchiveRuntime
from companion_daemon.world_v2.schemas import MemorySourceBinding
from companion_daemon.world_v2.memory_events import MemoryDeliberativeForgetAuthority
from companion_daemon.world_v2.memory_retrieval import MemoryRetrievalCompiler, MemorySourceExcerpt
from companion_daemon.world_v2.prehistory_memory_source import prehistory_memory_binding, resolve_prehistory_memory_source
from companion_daemon.world_v2.recall_corpus import RecallCorpusCompiler, RecallCorpusSources, required_recall_authority_refs
from companion_daemon.world_v2.recall_index import (
    FeatureHashRecallEmbedding, InMemoryRecallIndex, RecallCursor, RecallDocument,
    RecallQuery, RecallSourceBinding,
)
from companion_daemon.world_v2.recall_audit import CharacterRecallRequest, RecallAuditTrace
from companion_daemon.world_v2.recall_runtime import augment_model_content_with_recall, recall_evidence_json
from companion_daemon.world_v2.schemas import ProjectionCursor
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger

from test_character_prehistory import ACTOR, START, reviewed_archive, started_ledger
from test_life_projection import WORLD_ID, commit, event
import test_memory_candidate_authority as memory


def _install(ledger):
    row, = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(
        reviewed_archive(), created_at=START,
    )
    return row


def _choice(ledger, source, *, before=None, status="pending"):
    now = ledger.project().logical_time
    revision = before.entity_revision + 1 if before else 1
    after = memory.candidate(
        source, revision=revision, status=status, opened_at=START, updated_at=now,
        reviewed_at=now if status != "pending" else None,
        forgotten_at=now if status == "forgotten" else None,
        accepted_event_ref=f"event:memory:history:{revision}",
    )
    payload = memory.mutation(
        after, operation={"pending": "open", "active": "accept", "forgotten": "forget"}[status],
        before=before, evaluated_world_revision=ledger.project().world_revision,
        forget_authority=MemoryDeliberativeForgetAuthority() if status == "forgotten" else None,
    )
    _persist(ledger, payload)
    return after


def _persist(ledger, payload):
    now = ledger.project().logical_time
    proposed = memory.memory_proposal(payload)
    commit(ledger, [event(f"event:{payload.proposal_id}", "ProposalRecorded", proposed.model_dump(mode="json"), at=now)])
    commit(ledger, [
        event(f"event:{payload.acceptance_id}", "AcceptanceRecorded", memory.acceptance(payload), at=now),
        event(payload.candidate_after.origin.accepted_event_ref, proposed.proposed_mutation.event_type, payload.model_dump(mode="json"), at=now),
    ])


def _read(ledger, *, candidates=None, actor_ref=ACTOR, privacy="private", limit=480):
    projection = ledger.project()
    cursor = ProjectionCursor(world_revision=projection.world_revision,
                              deliberation_revision=projection.deliberation_revision,
                              ledger_sequence=projection.ledger_sequence)
    return MemoryRetrievalCompiler(ledger=ledger, max_excerpt_characters=limit).compile(
        cursor=cursor, candidates=projection.memory_candidates if candidates is None else candidates,
        projection=projection, viewer_privacy_ceiling=privacy, actor_ref=actor_ref,
    )


def _corpus(ledger, retrieval, *, omitted=(), actor_ref=ACTOR):
    projection = ledger.project()
    refs = required_recall_authority_refs(RecallCorpusSources(active_memory_candidates=retrieval.items))
    sources = RecallCorpusSources(
        active_memory_candidates=retrieval.items,
        authority_bindings=tuple(RecallSourceBinding(
            source_kind="committed_event", authority_type=item.event_type, ref=item.event_id,
            source_world_revision=item.world_revision, immutable_hash=item.payload_hash,
        ) for item in projection.committed_world_event_refs if item.event_id in refs and item.event_id not in omitted),
    )
    return RecallCorpusCompiler().compile(
        cursor=RecallCursor(world_revision=projection.world_revision,
                            deliberation_revision=projection.deliberation_revision,
                            ledger_sequence=projection.ledger_sequence),
        actor_ref=actor_ref, subject_refs=(actor_ref,), sources=sources,
    )


def test_memory_accepts_a_distinct_exact_prehistory_source(tmp_path):
    ledger = started_ledger(tmp_path / "memory.sqlite")
    try:
        row, = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(
            reviewed_archive(), created_at=START,
        )
        source = MemorySourceBinding(
            source_kind="prehistory", source_id=row.record.record_id,
            source_entity_revision=1, authority_event_ref=row.accepted_event_ref,
            authority_world_revision=row.accepted_world_revision,
            authority_payload_hash=row.accepted_payload_hash,
            source_values_hash=digest(row.record),
        )
        assert source.source_kind == "prehistory"
    finally:
        ledger.close()


def test_active_history_uses_exact_source_and_occurrence_dates_after_cold_replay(tmp_path):
    path = tmp_path / "retained.sqlite"
    ledger = started_ledger(path)
    try:
        row = _install(ledger)
        source = prehistory_memory_binding(row)
        pending = _choice(ledger, source)
        assert not _read(ledger).items
        _choice(ledger, source, before=pending, status="active")
        result = _read(ledger, limit=12)
        excerpt = result.items[0].source_excerpts[0]
        assert excerpt.text == row.record.statement[:12] and excerpt.truncated
        assert excerpt.excerpt_payload_hash == hashlib.sha256(excerpt.text.encode()).hexdigest()
        with pytest.raises(ValueError, match="excerpt hash"):
            MemorySourceExcerpt.model_validate(excerpt.model_dump() | {"text": "新的无依据经历。"})
        assert excerpt.prehistory.actor_ref == ACTOR
        assert excerpt.prehistory.time_precision == "month"
        document, = _corpus(ledger, result)
        assert document.text == excerpt.text
        assert document.epistemic_scope == "character_prehistory"
        assert document.occurred_from == row.record.occurred_from < row.accepted_at
        assert document.occurred_to == row.record.occurred_until
        assert document.status == "active", "old occurrence is not an inactive memory"
        assert len(document.source_bindings) == 2
        duplicate = result.model_copy(update={"items": (*result.items,
            result.items[0].model_copy(update={"candidate_id": "memory:other-cue"}))})
        deduplicated, = _corpus(ledger, duplicate)
        assert deduplicated.text == document.text and len(deduplicated.link_refs) == 2
        before = ledger.project()
        assert ledger.rebuild().semantic_hash == before.semantic_hash
        ledger.close()
        ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
        assert _read(ledger, limit=12) == result
        assert _corpus(ledger, result) == (document,)
        assert ledger.project().semantic_hash == before.semantic_hash
    finally:
        ledger.close()


@pytest.mark.parametrize("damage", ["archive_missing", "archive_event_missing", "record_event_missing",
                                     "record_body", "record_actor", "archive_body", "record_cutoff"])
def test_memory_requires_original_manifest_body_and_event_closure(tmp_path, damage):
    ledger = started_ledger(tmp_path / "damaged.sqlite")
    try:
        row = _install(ledger)
        source = prehistory_memory_binding(row)
        projection = ledger.project()
        records, archives, events = projection.prehistory_records, projection.prehistory_archives, projection.committed_world_event_refs
        if damage == "archive_missing":
            archives = ()
        elif damage == "archive_event_missing":
            events = tuple(item for item in events if item.event_id != archives[0].accepted_event_ref)
        elif damage == "record_event_missing":
            events = tuple(item for item in events if item.event_id != row.accepted_event_ref)
        elif damage == "record_body":
            records = (row.model_copy(update={"record": row.record.model_copy(update={"statement": "擅自补写的经历。"})}),)
            source = prehistory_memory_binding(records[0])
        elif damage == "record_actor":
            records = (row.model_copy(update={"actor_ref": "actor:other"}),)
        elif damage == "record_cutoff":
            records = (row.model_copy(update={"world_started_at": START + timedelta(days=1)}),)
        else:
            archive = archives[0]
            label = archive.manifest.entities[0].model_copy(update={"label": "被篡改的人物"})
            manifest = archive.manifest.model_copy(update={"entities": (label, *archive.manifest.entities[1:])})
            archives = (archive.model_copy(update={"manifest": manifest}),)
        with pytest.raises(ValueError):
            resolve_prehistory_memory_source(source, records=records, archives=archives, committed_events=events)
    finally:
        ledger.close()


def test_forget_blocks_stale_active_image_and_leaves_archive_out_of_retrieval(tmp_path):
    ledger = started_ledger(tmp_path / "forgotten.sqlite")
    try:
        row = _install(ledger)
        source = prehistory_memory_binding(row)
        pending = _choice(ledger, source)
        active = _choice(ledger, source, before=pending, status="active")
        assert _read(ledger).items
        index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
        documents = _corpus(ledger, _read(ledger))
        def query():
            projection = ledger.project()
            cursor = RecallCursor(world_revision=projection.world_revision,
                deliberation_revision=projection.deliberation_revision, ledger_sequence=projection.ledger_sequence)
            return RecallQuery(query_text="高中校刊核对稿件", cursor=cursor, actor_ref=ACTOR,
                subject_refs=(ACTOR,), viewer_privacy_ceiling="private", at=projection.logical_time,
                accessibility_seed="fixture:history-recall")
        index.rebuild(cursor=query().cursor, documents=documents)
        search = index.search(query())
        assert search.hits, "ordinary recall must include remembered pre-start history"
        trace = RecallAuditTrace(**search.model_dump(), trigger_ref="fixture:recall",
            request=CharacterRecallRequest(query_text=query().query_text, limit=query().limit))
        visible = json.loads(recall_evidence_json(trace))["candidates"][0]
        assert visible["prehistory"] == documents[0].prehistory.model_dump(mode="json")
        augmented = json.loads(augment_model_content_with_recall(json.dumps({"slices": {}}), trace))
        injected = augmented["slices"]["active_memory_candidates"]["items"][0]["value"]
        assert injected["prehistory"] == visible["prehistory"]
        assert injected["epistemic_scope"] == "character_prehistory"
        assert not index.search(query().model_copy(update={"occurred_from": START})).hits
        later = START + timedelta(days=7)
        commit(ledger, [event("week-clock", "ClockAdvanced", {
            "logical_time_from": START.isoformat(), "logical_time_to": later.isoformat(),
        }, at=later)])
        _choice(ledger, source, before=active, status="forgotten")
        assert not _read(ledger).items
        stale = _read(ledger, candidates=(active,))
        assert not stale.items and stale.suppressions[0].reasons == ("source_proof_failed",)
        assert not _corpus(ledger, _read(ledger))
        index.rebuild(cursor=query().cursor, documents=_corpus(ledger, _read(ledger)))
        assert not index.search(query()).hits
        assert ledger.project().prehistory_records[0].record.statement == row.record.statement
        assert ledger.rebuild().semantic_hash == ledger.project().semantic_hash
    finally:
        ledger.close()


def test_compression_cannot_restore_discarded_details_from_full_archive(tmp_path):
    ledger = started_ledger(tmp_path / "compressed.sqlite")
    try:
        source = prehistory_memory_binding(_install(ledger))
        pending = _choice(ledger, source)
        active = _choice(ledger, source, before=pending, status="active")
        compressed = memory.candidate(source, revision=3, status="active", summary_hash="d" * 64,
            opened_at=START, updated_at=START, reviewed_at=START, accepted_event_ref="event:memory:compressed")
        _persist(ledger, memory.mutation(compressed, operation="revise", revise_kind="compress", before=active,
            evaluated_world_revision=ledger.project().world_revision))
        result = _read(ledger)
        assert not result.items and result.suppressions[0].reasons == ("content_unavailable",)
        assert ledger.project().prehistory_records
        assert not _corpus(ledger, result)
        assert ledger.rebuild().semantic_hash == ledger.project().semantic_hash
    finally:
        ledger.close()


@pytest.mark.parametrize("actor,privacy", [(None, "private"), ("actor:other", "private"), (ACTOR, "public")])
def test_history_read_requires_owner_and_source_privacy(tmp_path, actor, privacy):
    ledger = started_ledger(tmp_path / "private.sqlite")
    try:
        source = prehistory_memory_binding(_install(ledger))
        pending = _choice(ledger, source)
        _choice(ledger, source, before=pending, status="active")
        assert not _read(ledger, actor_ref=actor, privacy=privacy).items
    finally:
        ledger.close()


@pytest.mark.parametrize("field,value", [
    ("source_entity_revision", 2), ("source_values_hash", "f" * 64),
    ("authority_payload_hash", "f" * 64), ("authority_world_revision", 1),
    ("source_id", "prehistory-record:invented"), ("authority_event_ref", "event:invented"),
    ("source_kind", "experience"),
])
def test_prehistory_source_cannot_alias_current_experience_or_other_authority(tmp_path, field, value):
    ledger = started_ledger(tmp_path / "forgery.sqlite")
    try:
        source = prehistory_memory_binding(_install(ledger)).model_copy(update={field: value})
        projection = ledger.project()
        with pytest.raises(ValueError, match="prehistory authority"):
            resolve_prehistory_memory_source(source, records=projection.prehistory_records,
                archives=projection.prehistory_archives, committed_events=projection.committed_world_event_refs)
    finally:
        ledger.close()


def test_corpus_requires_both_proofs_and_rejects_owner_or_time_reinterpretation(tmp_path):
    ledger = started_ledger(tmp_path / "scope.sqlite")
    try:
        source = prehistory_memory_binding(_install(ledger))
        pending = _choice(ledger, source)
        _choice(ledger, source, before=pending, status="active")
        result = _read(ledger)
        document, = _corpus(ledger, result)
        for source_ref in document.source_refs:
            assert not _corpus(ledger, result, omitted=(source_ref,))
        assert not _corpus(ledger, result, actor_ref="actor:other")
        for updates in ({"actor_ref": "actor:other"}, {"occurred_from": START, "occurred_to": START},
                        {"epistemic_scope": "world_fact"}, {"prehistory": None}):
            value = document.model_dump(mode="json") | updates
            with pytest.raises(ValueError):
                RecallDocument.model_validate(value)
    finally:
        ledger.close()
