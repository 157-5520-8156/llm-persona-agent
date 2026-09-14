"""Draft persistence binds inputs but cannot grant memory or source authority."""

import json
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.life_content_store import (
    InMemoryImmutableLifeContentStore,
    SQLiteImmutableLifeContentStore,
    StoredLifeContent,
)
from companion_daemon.world_v2.memory_representation_draft import (
    MemoryRepresentationDraft,
    prepare_memory_representation_input,
    persist_memory_representation_draft,
    read_memory_representation_draft,
)
from companion_daemon.world_v2.schemas import LifeContentDescriptorProjection, ProjectionCursor
from test_memory_retrieval import _message_fact_read_ledger
from test_prehistory_memory_source import (
    _install,
    _choice,
    _read,
    _corpus,
    prehistory_memory_binding,
)
from test_character_prehistory import started_ledger, ACTOR


def prepared():
    ledger, candidate, cursor = _message_fact_read_ledger()
    return prepare_memory_representation_input(
        ledger=ledger,
        candidate_id=candidate.candidate_id,
        actor_ref="agent:companion",
        cursor=cursor,
    )


def draft(value=None, **changes):
    return MemoryRepresentationDraft(
        input=value or prepared(),
        summary_text="最近喜欢乌龙茶。",
        role_result_ref="role-result:fixture-unverified",
        role_result_hash="a" * 64,
        **changes,
    )


@pytest.mark.parametrize("persistent", [False, True])
def test_draft_is_immutable_and_survives_reopen_without_authority(tmp_path, persistent):
    factory = (
        (
            lambda: SQLiteImmutableLifeContentStore(
                path=str(tmp_path / "draft.sqlite"), world_id="world:fixture"
            )
        )
        if persistent
        else InMemoryImmutableLifeContentStore
    )
    store = factory()
    value = draft()
    try:
        ref = persist_memory_representation_draft(store=store, draft=value)
        assert persist_memory_representation_draft(store=store, draft=value) == ref
        assert ref.authority == "unaccepted_draft"
        assert value.author_association == "unverified"
        if persistent:
            store.close()
            store = factory()
        assert (
            read_memory_representation_draft(store=store, reference=ref, expected_input=value.input)
            == value
        )
        other = value.model_copy(update={"summary_text": "喜欢茶。"})
        other_ref = persist_memory_representation_draft(store=store, draft=other)
        assert other_ref != ref
        assert (
            read_memory_representation_draft(store=store, reference=ref, expected_input=value.input)
            == value
        )
    finally:
        if persistent:
            store.close()


@pytest.mark.parametrize(
    "field,change",
    [
        ("actor_ref", "actor:other"),
        ("world_id", "world:other"),
        ("candidate_entity_revision", 50),
        ("candidate_semantic_fingerprint", "f" * 64),
        ("previous_summary_payload_hash", "e" * 64),
        ("previous_summary_ref", "summary:other"),
    ],
)
def test_draft_cannot_be_read_against_another_input(field, change):
    store = InMemoryImmutableLifeContentStore()
    value = draft()
    ref = persist_memory_representation_draft(store=store, draft=value)
    with pytest.raises(ValueError, match="expected input"):
        read_memory_representation_draft(
            store=store,
            reference=ref,
            expected_input=value.input.model_copy(update={field: change}),
        )


def test_changed_reading_text_or_cursor_invalidates_draft_association():
    store = InMemoryImmutableLifeContentStore()
    value = draft()
    ref = persist_memory_representation_draft(store=store, draft=value)
    source = value.input.reading.source_excerpts[0].model_copy(update={"text": "另一段内容"})
    changed = value.input.model_copy(
        update={"reading": value.input.reading.model_copy(update={"source_excerpts": (source,)})}
    )
    changed_cursor = value.input.model_copy(
        update={"cursor": value.input.cursor.model_copy(update={"ledger_sequence": 999})}
    )
    for expected in (changed, changed_cursor):
        with pytest.raises(ValueError):
            read_memory_representation_draft(store=store, reference=ref, expected_input=expected)


@pytest.mark.parametrize("fault", ["kind", "hash", "text", "ref"])
def test_audit_reader_rejects_corrupt_or_wrong_lane_sidecars(fault):
    store = InMemoryImmutableLifeContentStore()
    value = draft()
    ref = persist_memory_representation_draft(store=store, draft=value)
    record = store.read_exact(content_ref=ref.content_ref)
    raw = {
        field: getattr(record, field)
        for field in ("content_ref", "content_kind", "content_payload_hash", "text")
    }
    field, replacement = {
        "kind": ("content_kind", "experience_summary"),
        "hash": ("content_payload_hash", "f" * 64),
        "text": ("text", "{}"),
        "ref": ("content_ref", "wrong:ref"),
    }[fault]
    raw[field] = replacement
    bad_store = SimpleNamespace(read_exact=lambda **kwargs: SimpleNamespace(**raw))
    with pytest.raises(ValueError, match="exact lane and bytes"):
        read_memory_representation_draft(store=bad_store, reference=ref, expected_input=value.input)


def test_writer_does_not_treat_cross_lane_duplicate_as_a_memory_draft():
    value = draft()
    original = InMemoryImmutableLifeContentStore()
    ref = persist_memory_representation_draft(store=original, draft=value)
    record = original.read_exact(content_ref=ref.content_ref)
    store = InMemoryImmutableLifeContentStore()
    store.put_if_absent(
        StoredLifeContent(
            content_ref=record.content_ref,
            content_payload_hash=record.content_payload_hash,
            text=record.text,
            content_kind="raw_model_result",
        )
    )
    with pytest.raises(ValueError, match="exact lane and bytes"):
        persist_memory_representation_draft(store=store, draft=value)


def test_writer_fails_if_the_adapter_does_not_persist():
    store = SimpleNamespace(put_if_absent=lambda record: None, read_exact=lambda **kwargs: None)
    with pytest.raises(ValueError, match="did not retain"):
        persist_memory_representation_draft(store=store, draft=draft())


@pytest.mark.parametrize(
    "changes",
    [
        {"authority": "accepted"},
        {"author_association": "verified"},
        {"summary_text": " "},
        {"summary_text": "字" * 1801},
    ],
)
def test_unchecked_objects_cannot_promote_draft_authority_or_bypass_limits(changes):
    with pytest.raises(ValueError):
        persist_memory_representation_draft(
            store=InMemoryImmutableLifeContentStore(), draft=draft().model_copy(update=changes)
        )


def test_prehistory_draft_never_enters_retrieval_and_forgotten_memory_cannot_be_prepared(tmp_path):
    ledger = started_ledger(tmp_path / "world.sqlite")
    store = SQLiteImmutableLifeContentStore(
        path=str(tmp_path / "world.sqlite"), world_id=ledger.world_id
    )
    try:
        source = prehistory_memory_binding(_install(ledger))
        pending = _choice(ledger, source)
        active = _choice(ledger, source, before=pending, status="active")
        before = ledger.project()
        cursor = ProjectionCursor(
            world_revision=before.world_revision,
            deliberation_revision=before.deliberation_revision,
            ledger_sequence=before.ledger_sequence,
        )
        current = prepare_memory_representation_input(
            ledger=ledger,
            candidate_id=active.candidate_id,
            actor_ref=ACTOR,
            cursor=cursor,
            life_content_store=store,
        )
        value = MemoryRepresentationDraft(
            input=current,
            summary_text="高中做过校刊。",
            role_result_ref="role-result:fixture-unverified",
            role_result_hash="a" * 64,
        )
        read_before = _read(ledger)
        corpus_before = _corpus(ledger, read_before)
        ref = persist_memory_representation_draft(store=store, draft=value)
        assert (
            read_memory_representation_draft(store=store, reference=ref, expected_input=current)
            == value
        )
        assert ledger.project() == before
        assert _read(ledger) == read_before
        assert _corpus(ledger, _read(ledger)) == corpus_before
        # The draft kind has no World visibility descriptor.
        assert "memory_representation_draft" not in json.dumps(
            LifeContentDescriptorProjection.model_json_schema()
        )
        with pytest.raises(ValueError):
            prepare_memory_representation_input(
                ledger=ledger,
                candidate_id=active.candidate_id,
                actor_ref="actor:other",
                cursor=cursor,
            )
        _choice(ledger, source, before=active, status="forgotten")
        after = ledger.project()
        later = ProjectionCursor(
            world_revision=after.world_revision,
            deliberation_revision=after.deliberation_revision,
            ledger_sequence=after.ledger_sequence,
        )
        with pytest.raises(ValueError, match="complete eligible reading"):
            prepare_memory_representation_input(
                ledger=ledger, candidate_id=active.candidate_id, actor_ref=ACTOR, cursor=later
            )
        assert ledger.rebuild().semantic_hash == after.semantic_hash
    finally:
        store.close()
        ledger.close()


def test_sqlite_world_partition_does_not_expose_another_world_draft(tmp_path):
    path = str(tmp_path / "worlds.sqlite")
    value = draft()
    first = SQLiteImmutableLifeContentStore(path=path, world_id=value.input.world_id)
    second = SQLiteImmutableLifeContentStore(path=path, world_id="world:second")
    try:
        ref = persist_memory_representation_draft(store=first, draft=value)
        assert (
            read_memory_representation_draft(
                store=second, reference=ref, expected_input=value.input
            )
            is None
        )
    finally:
        first.close()
        second.close()


def test_a_compression_event_cannot_promote_an_unaccepted_draft_into_memory(tmp_path):
    import test_memory_candidate_authority as authority
    from test_prehistory_memory_source import _persist

    ledger = started_ledger(tmp_path / "compressed.sqlite")
    store = SQLiteImmutableLifeContentStore(
        path=str(tmp_path / "compressed.sqlite"), world_id=ledger.world_id
    )
    try:
        source = prehistory_memory_binding(_install(ledger))
        active = _choice(ledger, source, before=_choice(ledger, source), status="active")
        projection = ledger.project()
        cursor = ProjectionCursor(
            world_revision=projection.world_revision,
            deliberation_revision=projection.deliberation_revision,
            ledger_sequence=projection.ledger_sequence,
        )
        current = prepare_memory_representation_input(
            ledger=ledger, candidate_id=active.candidate_id, actor_ref=ACTOR, cursor=cursor
        )
        ref = persist_memory_representation_draft(store=store, draft=draft(current))
        after = authority.candidate(
            source,
            revision=active.entity_revision + 1,
            status="active",
            summary_hash=ref.content_payload_hash,
            opened_at=active.opened_at,
            updated_at=projection.logical_time,
            reviewed_at=projection.logical_time,
            accepted_event_ref="event:memory:unaccepted-draft-reference",
        )
        values = after.values.model_copy(update={"summary_ref": ref.content_ref})
        after = after.model_copy(
            update={
                "values": values,
                "semantic_fingerprint": authority.memory_candidate_semantic_fingerprint(
                    values=values, policy_refs=after.origin.policy_refs
                ),
            }
        )
        _persist(
            ledger,
            authority.mutation(
                after,
                operation="revise",
                revise_kind="compress",
                before=active,
                evaluated_world_revision=projection.world_revision,
            ),
        )
        result = _read(ledger)
        assert not result.items and result.suppressions[0].reasons == ("content_unavailable",)
        assert not _corpus(ledger, result)
        assert (
            read_memory_representation_draft(store=store, reference=ref, expected_input=current)
            is not None
        )
        assert ledger.rebuild().semantic_hash == ledger.project().semantic_hash
    finally:
        store.close()
        ledger.close()
