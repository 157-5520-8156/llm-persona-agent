"""One pinned read snapshot may feed multiple views; later reads see new state."""

from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from companion_daemon.world_v2.context_read_scope import context_read_scope
from companion_daemon.world_v2.life_content import LifeContentCompiler, LifeContentBudget
from companion_daemon.world_v2.life_content_store import (
    InMemoryImmutableLifeContentStore,
    SQLiteImmutableLifeContentStore,
    StoredLifeContent,
    life_content_payload_hash,
)
from test_life_content import _projection_with_bound_content, _cursor


def record(ref, text):
    return StoredLifeContent(
        content_ref=ref,
        content_kind="occurrence_result",
        content_payload_hash=life_content_payload_hash(text),
        text=text,
    )


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    value = (
        InMemoryImmutableLifeContentStore()
        if request.param == "memory"
        else SQLiteImmutableLifeContentStore(
            path=str(tmp_path / "snapshot.sqlite"), world_id="world:snapshot"
        )
    )
    yield value
    if hasattr(value, "close"):
        value.close()


def test_missing_and_existing_bytes_are_snapshot_bound_and_next_scope_refreshes(store):
    store.put_if_absent(record("content:old", "原有正文"))
    projection, _, _ = _projection_with_bound_content()
    with context_read_scope(store, projection):
        assert store.read_exact(content_ref="content:new") is None
        store.put_if_absent(record("content:new", "后续补写"))
        assert store.read_exact(content_ref="content:new") is None
        assert store.read_exact(content_ref="content:old").text == "原有正文"
        with context_read_scope(store, projection):
            assert store.read_exact(content_ref="content:new").text == "后续补写"
        assert store.read_exact(content_ref="content:new") is None
    assert store.read_exact(content_ref="content:new").text == "后续补写"


def test_copied_context_cannot_share_a_snapshot_with_another_thread(store):
    projection, _, _ = _projection_with_bound_content()
    with context_read_scope(store, projection):
        assert store.read_exact(content_ref="content:thread") is None
        store.put_if_absent(record("content:thread", "另一个线程能读到新增"))
        copied = copy_context()
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(
                copied.run, lambda: store.read_exact(content_ref="content:thread")
            ).result()
        assert result.text == "另一个线程能读到新增"
        assert store.read_exact(content_ref="content:thread") is None


def test_scope_reuses_equal_view_but_keeps_actor_privacy_budget_and_projection_distinct(
    monkeypatch,
):
    projection, descriptor, text = _projection_with_bound_content(text="共同经历" * 300)
    store = InMemoryImmutableLifeContentStore()
    store.put_if_absent(record(descriptor.content_ref, text))
    compiler = LifeContentCompiler(store=store)
    reads = []
    original = store.read_exact

    def counted(**kwargs):
        reads.append(kwargs["content_ref"])
        return original(**kwargs)

    monkeypatch.setattr(store, "read_exact", counted)
    kwargs = dict(
        cursor=_cursor(projection),
        actor_ref="actor:companion",
        viewer_privacy_ceiling="private",
        projection=projection,
    )
    expected = compiler.compile(**kwargs)
    reads.clear()
    with context_read_scope(store, projection):
        assert compiler.compile(**kwargs) == expected
        assert compiler.compile(**kwargs) == expected
        assert len(reads) == 1
        full = compiler.compile(**kwargs, budget=None)
        assert full.settled_items[0].text == text
        assert not compiler.compile(**{**kwargs, "actor_ref": "actor:other"}).settled_items
        assert not compiler.compile(**{**kwargs, "viewer_privacy_ceiling": "public"}).settled_items
        assert not compiler.compile(
            **kwargs, user_channel_limited_content_refs=frozenset({descriptor.content_ref})
        ).settled_items
        changed = projection.model_copy(update={"life_content_descriptors": ()})
        assert not compiler.compile(**{**kwargs, "projection": changed}).settled_items
        with pytest.raises(ValueError, match="pinned cursor"):
            compiler.compile(
                **{
                    **kwargs,
                    "cursor": _cursor(projection).model_copy(update={"ledger_sequence": 0}),
                }
            )
    reads.clear()
    compiler.compile(**kwargs)
    assert reads


def test_unknown_store_adapter_does_not_acquire_implicit_snapshot_or_cache():
    projection, descriptor, text = _projection_with_bound_content()

    class MutableStore:
        body = record(descriptor.content_ref, text)
        calls = 0

        def read_exact(self, **kwargs):
            self.calls += 1
            return self.body

    store = MutableStore()
    compiler = LifeContentCompiler(store=store)
    kwargs = dict(
        cursor=_cursor(projection),
        actor_ref="actor:companion",
        viewer_privacy_ceiling="private",
        projection=projection,
    )
    with context_read_scope(store, projection):
        assert compiler.compile(**kwargs).settled_items
        store.body = None
        assert not compiler.compile(**kwargs).settled_items
    assert store.calls == 2


def test_sqlite_snapshot_releases_on_error_and_next_scope_rejects_corruption(tmp_path):
    path = tmp_path / "corrupt.sqlite"
    store = SQLiteImmutableLifeContentStore(path=str(path), world_id="world:snapshot")
    projection, _, _ = _projection_with_bound_content()
    store.put_if_absent(record("content:bound", "原文"))
    try:
        with pytest.raises(RuntimeError, match="scope exit"):
            with context_read_scope(store, projection):
                assert store.read_exact(content_ref="content:bound").text == "原文"
                with sqlite3.connect(path) as other:
                    other.execute(
                        "update world_v2_life_content set text='篡改' where content_ref='content:bound'"
                    )
                assert store.read_exact(content_ref="content:bound").text == "原文"
                raise RuntimeError("scope exit")
        with context_read_scope(store, projection), pytest.raises(ValueError, match="hash"):
            store.read_exact(content_ref="content:bound")
        with sqlite3.connect(path) as db:
            assert db.execute("pragma wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
    finally:
        store.close()


def test_prehistory_proof_shared_only_within_same_projection_read_scope(tmp_path, monkeypatch):
    from companion_daemon.world_v2.character_prehistory_runtime import PrehistoryArchiveRuntime
    from test_character_prehistory import ACTOR, START, reviewed_archive, started_ledger
    from test_prehistory_memory_source import _read
    import companion_daemon.world_v2.prehistory_memory_source as source

    ledger = started_ledger(tmp_path / "history.sqlite")
    try:
        PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).install_reviewed(
            reviewed_archive(), created_at=START
        )
        projection = ledger.project()
        store = InMemoryImmutableLifeContentStore()
        calls = []
        original = source.prehistory_memory_binding

        def counted(row):
            calls.append(row.record.record_id)
            return original(row)

        with monkeypatch.context() as patch:
            patch.setattr(source, "prehistory_memory_binding", counted)
            with context_read_scope(store, projection):
                assert _read(ledger).items
                after_first = len(calls)
                assert _read(ledger).items
                assert len(calls) == after_first
            assert _read(ledger).items
            assert len(calls) > after_first
    finally:
        ledger.close()


def test_mutable_results_do_not_poison_the_scope_immutability_proof():
    from companion_daemon.world_v2.context_read_scope import memoized_context_read
    from companion_daemon.world_v2.schema_core import FrozenModel

    class Mutable(FrozenModel):
        values: list[str]

    projection, _, _ = _projection_with_bound_content()
    store = InMemoryImmutableLifeContentStore()
    value = Mutable(values=["first"])
    calls = []

    def read():
        calls.append(1)
        return (value,)

    with context_read_scope(store, projection):
        for n in range(3):
            result = memoized_context_read(
                store=store, projection=projection, kind="mutable", compute=read
            )
            value.values.append(str(n))
            assert result[0].values[-1] == str(n)
    assert len(calls) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("response_text", [None, "这段经历让我愿意慢慢说清楚。" * 25])
async def test_structured_material_shared_across_budgets_without_changing_suppressions(
    tmp_path, monkeypatch, response_text
):
    from test_character_life_experience_runtime import (
        _accepted_response,
        _cursor as cursor_of,
        WORLD,
        ACTOR,
    )
    from companion_daemon.world_v2.character_life_experience_runtime import (
        CharacterLifeExperienceRuntime,
    )
    import companion_daemon.world_v2.character_life_experience_runtime as runtime_module
    from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "structured.sqlite"
    _, response_ref = await _accepted_response(path, response_text)
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD)
    try:
        runtime = CharacterLifeExperienceRuntime(
            ledger=ledger, content_store=store, owner_actor_ref=ACTOR
        )
        runtime.accept(
            world_id=WORLD, audit_cursor=cursor_of(ledger), response_event_ref=response_ref
        )
        projection = ledger.project()
        compiler = LifeContentCompiler(store=store)
        kwargs = dict(
            cursor=cursor_of(ledger),
            actor_ref=ACTOR,
            viewer_privacy_ceiling="private",
            projection=projection,
        )
        budgets = (
            None,
            LifeContentBudget(max_item_characters=1, max_total_characters=1),
            LifeContentBudget(max_item_characters=37, max_total_characters=50),
            LifeContentBudget(),
        )
        expected = tuple(compiler.compile(**kwargs, budget=b).model_dump_json() for b in budgets)
        calls = []
        original = runtime_module.validate_character_life_experience_binding

        def counted(**kwargs):
            calls.append(kwargs["binding"].response_event_ref)
            return original(**kwargs)

        with monkeypatch.context() as patch, context_read_scope(store, projection):
            patch.setattr(runtime_module, "validate_character_life_experience_binding", counted)
            actual = tuple(compiler.compile(**kwargs, budget=b).model_dump_json() for b in budgets)
        assert actual == expected
        assert len(calls) == 1, "current paired source should be proved once per read snapshot"
        with context_read_scope(store, projection):
            assert compiler.compile(**kwargs, budget=None).model_dump_json() == expected[0]
    finally:
        store.close()
        ledger.close()


def test_read_snapshot_lease_expires_and_closed_sqlite_store_cannot_reopen(tmp_path):
    store = SQLiteImmutableLifeContentStore(
        path=str(tmp_path / "closed.sqlite"), world_id="world:closed"
    )
    store.put_if_absent(record("content:closed", "正文"))
    with store.read_snapshot() as snapshot:
        assert snapshot.read_exact(content_ref="content:closed").text == "正文"
    with pytest.raises(RuntimeError, match="closed"):
        snapshot.read_exact(content_ref="content:closed")
    store.close()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        with store.read_snapshot():
            pass


def test_life_views_construct_one_complete_material_then_apply_independent_budgets(monkeypatch):
    from test_life_content import _projection_with_bound_experience_content
    from companion_daemon.world_v2.life_content import RecentExperienceContextItem

    projection, descriptor, text = _projection_with_bound_experience_content(
        text="长经历材料。" * 120
    )
    store = InMemoryImmutableLifeContentStore()
    store.put_if_absent(
        StoredLifeContent(
            content_ref=descriptor.content_ref,
            content_kind="experience_summary",
            content_payload_hash=descriptor.content_payload_hash,
            text=text,
        )
    )
    compiler = LifeContentCompiler(store=store)
    kwargs = dict(
        cursor=_cursor(projection),
        actor_ref="actor:companion",
        viewer_privacy_ceiling="private",
        projection=projection,
    )
    budgets = (
        LifeContentBudget(),
        None,
        LifeContentBudget(max_item_characters=10, max_total_characters=5),
    )
    expected = [compiler.compile(**kwargs, budget=b).model_dump_json() for b in budgets]
    calls = []
    original = RecentExperienceContextItem.model_validate

    def counted(cls, *args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    with monkeypatch.context() as patch, context_read_scope(store, projection):
        patch.setattr(RecentExperienceContextItem, "model_validate", classmethod(counted))
        actual = [compiler.compile(**kwargs, budget=b).model_dump_json() for b in budgets]
    assert actual == expected
    assert len(calls) == 1


def test_sqlite_snapshot_keeps_original_database_when_working_directory_changes(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    store = SQLiteImmutableLifeContentStore(path="relative.sqlite", world_id="world:path")
    store.put_if_absent(record("content:path", "同一个数据库"))
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    try:
        with store.read_snapshot() as snapshot:
            assert snapshot.read_exact(content_ref="content:path").text == "同一个数据库"
    finally:
        store.close()


def test_sqlite_memory_adapter_can_take_an_independent_snapshot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = SQLiteImmutableLifeContentStore(path=":memory:", world_id="world:memory")
    try:
        with store.read_snapshot() as snapshot:
            store.put_if_absent(record("content:new", "新正文"))
            assert snapshot.read_exact(content_ref="content:new") is None
        assert store.read_exact(content_ref="content:new").text == "新正文"
    finally:
        store.close()


def test_candidate_qualification_shared_in_scope_but_privacy_and_new_scope_rechecked(
    tmp_path, monkeypatch
):
    from companion_daemon.world_v2.character_prehistory_runtime import PrehistoryArchiveRuntime
    from companion_daemon.world_v2.memory_retrieval import MemoryRetrievalCompiler
    from companion_daemon.world_v2.schemas import MemoryRetrievalDecision
    from test_character_prehistory import ACTOR, START, reviewed_archive, started_ledger
    from test_prehistory_memory_source import _read

    ledger = started_ledger(tmp_path / "qualification.sqlite")
    try:
        PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).install_reviewed(
            reviewed_archive(), created_at=START
        )
        projection = ledger.project()
        store = InMemoryImmutableLifeContentStore()
        calls = []
        original = MemoryRetrievalDecision.__init__

        def counted(self, *args, **kwargs):
            calls.append(1)
            original(self, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(MemoryRetrievalDecision, "__init__", counted)
            with context_read_scope(store, projection):
                assert _read(ledger).items and _read(ledger).items
                assert len(calls) == 1
                private = MemoryRetrievalCompiler(ledger=ledger).compile(
                    cursor=_cursor(projection),
                    candidates=projection.memory_candidates,
                    projection=projection,
                    actor_ref=ACTOR,
                    viewer_privacy_ceiling="public",
                )
                assert not private.items
                assert len(calls) == 2
            assert _read(ledger).items
            assert len(calls) == 3
    finally:
        ledger.close()
