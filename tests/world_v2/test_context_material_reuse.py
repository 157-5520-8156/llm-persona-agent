"""Current qualification still runs; unchanged pure read material is reusable."""

from datetime import timedelta

from companion_daemon.world_v2.character_prehistory_runtime import PrehistoryArchiveRuntime
from companion_daemon.world_v2.memory_retrieval import MemoryRetrievalItem
from test_character_prehistory import ACTOR, START, reviewed_archive, started_ledger
from test_prehistory_memory_source import _read
from test_life_projection import commit, event


def test_new_clock_reuses_item_material_without_sharing_mutable_private_pointer(
    tmp_path, monkeypatch
):
    ledger = started_ledger(tmp_path / "material.sqlite")
    try:
        installation = PrehistoryArchiveRuntime(
            ledger=ledger, owner_actor_ref=ACTOR
        ).install_reviewed(reviewed_archive(), created_at=START)
        (first,) = _read(ledger).items
        expected = first.model_dump_json()
        first.source_excerpts[0]._recall_source_record = installation.records[0].record.model_copy(
            update={"statement": "篡改的私有指针不得污染下一次读取"}
        )
        calls = []
        original = MemoryRetrievalItem.__init__

        def counted(self, *args, **kwargs):
            calls.append(kwargs.get("candidate_id"))
            original(self, *args, **kwargs)

        now = START + timedelta(minutes=1)
        commit(
            ledger,
            [
                event(
                    "material-next-clock",
                    "ClockAdvanced",
                    {
                        "logical_time_from": START.isoformat(),
                        "logical_time_to": now.isoformat(),
                        "reason": "material-test",
                    },
                    at=now,
                )
            ],
        )
        with monkeypatch.context() as patch:
            patch.setattr(MemoryRetrievalItem, "__init__", counted)
            (second,) = _read(ledger).items
        assert second.model_dump_json() == expected
        assert second.source_excerpts[0]._recall_source_record == installation.records[0].record
        assert calls == [], "unchanged prehistory item should not be reconstructed for a new clock"
    finally:
        ledger.close()


def test_warm_commit_does_not_assemble_whole_semantic_document(tmp_path, monkeypatch):
    from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
    from test_head_state_split_storage import WORLD, commit_one

    ledger = SQLiteWorldLedger(path=tmp_path / "stream.sqlite", world_id=WORLD)
    try:
        commit_one(ledger, 0)

        def unnecessary_join(*args, **kwargs):
            raise AssertionError("full semantic byte string assembled")

        with monkeypatch.context() as patch:
            patch.setattr(ledger, "_assemble_semantic_material", unnecessary_join)
            commit_one(ledger, 1)
        assert ledger.project().semantic_hash == ledger.rebuild().semantic_hash
    finally:
        ledger.close()


def test_dense_arithmetic_preserves_float_bits_and_dimension_rejection():
    import random
    import pytest
    from companion_daemon.world_v2.recall_index import _cosine

    rng = random.Random(20261009)
    for size in (0, 1, 3, 256, 1024):
        for _ in range(20):
            a = tuple(rng.uniform(-1, 1) for _ in range(size))
            b = tuple(rng.uniform(-1, 1) for _ in range(size))
            expected = sum(x * y for x, y in zip(a, b, strict=True))
            assert float(_cosine(a, b)).hex() == float(expected).hex()
    with pytest.raises(ValueError, match="different dimensions"):
        _cosine((1.0,), ())


def test_episode_identity_is_computed_once_but_positions_follow_current_visibility(monkeypatch):
    from types import SimpleNamespace
    import companion_daemon.world_v2.character_prehistory as history
    from companion_daemon.world_v2.story_episode import StoryEpisode, readable_episode_memberships

    refs = tuple(f"prehistory-record:episode-{i}" for i in range(8))
    episode = StoryEpisode(
        episode_ref="story-episode:reuse", title="Author only", revision=1, record_refs=refs
    )
    archive = SimpleNamespace(
        manifest=SimpleNamespace(actor_ref=ACTOR, episodes=(episode,)),
        accepted_event_ref="event:episode",
        accepted_world_revision=2,
        accepted_payload_hash="a" * 64,
    )
    calls = []
    original = history.digest

    def count(value):
        calls.append(value)
        return original(value)

    with monkeypatch.context() as patch:
        patch.setattr(history, "digest", count)
        full = readable_episode_memberships(
            archives=(archive,),
            actor_ref=ACTOR,
            world_id="world:test",
            visible_record_ids=set(refs),
        )
        half = readable_episode_memberships(
            archives=(archive,),
            actor_ref=ACTOR,
            world_id="world:test",
            visible_record_ids=set(refs[::2]),
        )
    assert [full[r][0].position for r in refs] == list(range(8))
    assert [half[r][0].position for r in refs[::2]] == list(range(4))
    assert len(calls) == 2


def test_corpus_reuses_exact_current_bindings_instead_of_reconstructing_them(tmp_path, monkeypatch):
    from companion_daemon.world_v2.recall_corpus import (
        RecallCorpusCompiler,
        RecallCorpusSources,
        required_recall_authority_refs,
    )
    from companion_daemon.world_v2.recall_index import RecallCursor, RecallSourceBinding

    ledger = started_ledger(tmp_path / "bindings.sqlite")
    try:
        PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).install_reviewed(
            reviewed_archive(), created_at=START
        )
        state = ledger.project()
        read = _read(ledger)
        sources = RecallCorpusSources(active_memory_candidates=read.items)
        refs = required_recall_authority_refs(sources)
        bindings = tuple(
            RecallSourceBinding(
                source_kind="committed_event",
                authority_type=e.event_type,
                ref=e.event_id,
                source_world_revision=e.world_revision,
                immutable_hash=e.payload_hash,
            )
            for e in state.committed_world_event_refs
            if e.event_id in refs
        )
        sources = sources.model_copy(update={"authority_bindings": bindings})
        RecallCorpusCompiler().compile(
            cursor=RecallCursor(
                world_revision=state.world_revision,
                deliberation_revision=state.deliberation_revision,
                ledger_sequence=state.ledger_sequence,
            ),
            actor_ref=ACTOR,
            subject_refs=(ACTOR,),
            sources=sources,
        )
        calls = []
        original = RecallSourceBinding.__init__

        def count(self, *args, **kwargs):
            calls.append(kwargs.get("ref"))
            original(self, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(RecallSourceBinding, "__init__", count)
            docs = RecallCorpusCompiler().compile(
                cursor=RecallCursor(
                    world_revision=state.world_revision,
                    deliberation_revision=state.deliberation_revision,
                    ledger_sequence=state.ledger_sequence,
                ),
                actor_ref=ACTOR,
                subject_refs=(ACTOR,),
                sources=sources,
            )
        assert docs and docs[0].text == read.items[0].source_excerpts[0].text
        assert not calls
    finally:
        ledger.close()


def test_binding_value_cache_keeps_strict_validation():
    import pytest
    from pydantic import ValidationError
    from companion_daemon.world_v2.recall_index import recall_source_binding

    valid = dict(
        source_kind="committed_event",
        authority_type="FixtureEvent",
        ref="event:binding",
        source_world_revision=1,
        immutable_hash="a" * 64,
    )
    assert recall_source_binding(**valid).ref == "event:binding"
    for bad in ({"source_world_revision": True}, {"ref": []}, {"immutable_hash": "invalid"}):
        with pytest.raises(ValidationError):
            recall_source_binding(**{**valid, **bad})


def test_one_memory_read_checks_prehistory_once_and_next_read_checks_it_again(
    tmp_path, monkeypatch
):
    import companion_daemon.world_v2.memory_reducers as reducers
    import companion_daemon.world_v2.memory_retrieval as retrieval

    ledger = started_ledger(tmp_path / "proof-handoff.sqlite")
    try:
        PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).install_reviewed(
            reviewed_archive(), created_at=START
        )
        calls = []
        original = reducers.resolve_prehistory_memory_source

        def counted(*args, **kwargs):
            calls.append(args[0].source_id)
            return original(*args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(reducers, "resolve_prehistory_memory_source", counted)
            patch.setattr(retrieval, "resolve_prehistory_memory_source", counted)
            assert _read(ledger).items
            assert len(calls) == 1
            assert _read(ledger).items
            assert len(calls) == 2
    finally:
        ledger.close()
