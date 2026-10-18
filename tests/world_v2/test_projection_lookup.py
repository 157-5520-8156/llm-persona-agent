"""Indexing selects the same current rows and never proves permission."""

from types import SimpleNamespace

from companion_daemon.world_v2.projection_lookup import lookup_first
from companion_daemon.world_v2.schema_core import FrozenModel


class Row(FrozenModel):
    identity: str
    payload: str


def test_index_keeps_first_row_and_never_reuses_a_different_current_tuple():
    rows = tuple(Row(identity=str(i), payload="old") for i in range(12))
    rows = (*rows, Row(identity="2", payload="duplicate"))
    for _ in range(10):
        assert lookup_first(rows, "identity", "2") is rows[2]
    assert lookup_first(rows, "identity", "missing") is None
    changed = (*rows[:2], rows[2].model_copy(update={"payload": "changed"}), *rows[3:])
    assert lookup_first(changed, "identity", "2").payload == "changed"
    withdrawn = tuple(row for row in rows if row.identity != "2")
    assert lookup_first(withdrawn, "identity", "2") is None
    assert lookup_first(rows, "identity", "2").payload == "old"


def test_mutable_rows_and_collections_are_scanned_again_after_mutation():
    rows = tuple(SimpleNamespace(identity=str(i)) for i in range(12))
    assert lookup_first(rows, "identity", "2") is rows[2]
    rows[2].identity = "changed"
    assert lookup_first(rows, "identity", "2") is None
    mutable = [Row(identity=str(i), payload="old") for i in range(12)]
    assert lookup_first(mutable, "identity", "2") is mutable[2]
    mutable[2] = Row(identity="2", payload="new")
    assert lookup_first(mutable, "identity", "2").payload == "new"


def test_warm_index_rechecks_predicate_and_keeps_matching_duplicate_fallback():
    rows = tuple(Row(identity=str(i), payload="old") for i in range(12))
    rows = (*rows, Row(identity="2", payload="duplicate"))
    for _ in range(10):
        assert lookup_first(rows, "identity", "2") is rows[2]
    allowed = {"old"}

    def check(row):
        return row.payload in allowed

    assert lookup_first(rows, "identity", "2", predicate=check) is rows[2]
    allowed.clear()
    assert lookup_first(rows, "identity", "2", predicate=check) is None
    allowed.add("duplicate")
    assert lookup_first(rows, "identity", "2", predicate=check) is rows[-1]


def test_nested_address_keeps_current_frozen_path_and_duplicate_predicate():
    class Outer(FrozenModel):
        entry: Row

    rows = tuple(Outer(entry=Row(identity=str(i), payload="old")) for i in range(12))
    rows = (*rows, Outer(entry=Row(identity="2", payload="duplicate")))
    for _ in range(10):
        assert lookup_first(rows, "entry.identity", "2") is rows[2]
    assert (
        lookup_first(
            rows, "entry.identity", "2", predicate=lambda r: r.entry.payload == "duplicate"
        )
        is rows[-1]
    )
    changed = (
        *rows[:2],
        rows[2].model_copy(update={"entry": Row(identity="changed", payload="new")}),
        *rows[3:],
    )
    assert lookup_first(changed, "entry.identity", "2") is rows[-1]
    assert lookup_first(changed, "entry.identity", "changed") is changed[2]


def test_interleaved_current_collections_keep_their_address_indexes(monkeypatch):
    import companion_daemon.world_v2.projection_lookup as module
    from collections import OrderedDict

    monkeypatch.setattr(module, "_INDEXES", OrderedDict())
    monkeypatch.setattr(module, "_RETAINED_ROWS", 0)
    collections = [
        tuple(Row(identity=str(i), payload=str(group)) for i in range(40)) for group in range(20)
    ]
    for rows in collections:
        for _ in range(8):
            assert lookup_first(rows, "identity", "39") is rows[-1]
    accesses = []
    original = Row.__getattribute__

    def count(self, name):
        if name == "identity":
            accesses.append(name)
        return original(self, name)

    monkeypatch.setattr(Row, "__getattribute__", count)
    for _ in range(3):
        for rows in collections:
            assert lookup_first(rows, "identity", "39") is rows[-1]
    assert accesses == [], "interleaving the Context collections must not evict every hot index"


def test_address_cache_also_bounds_total_retained_rows(monkeypatch):
    from collections import OrderedDict
    import companion_daemon.world_v2.projection_lookup as module

    monkeypatch.setattr(module, "_INDEXES", OrderedDict())
    monkeypatch.setattr(module, "_RETAINED_ROWS", 0)
    monkeypatch.setattr(module, "_MAX_RETAINED_ROWS", 64)
    for group in range(20):
        rows = tuple(Row(identity=str(i), payload=str(group)) for i in range(12))
        assert lookup_first(rows, "identity", "11") is rows[-1]
        assert module._RETAINED_ROWS == sum(len(entry[0]) for entry in module._INDEXES.values())
        assert module._RETAINED_ROWS <= 64


def test_replay_append_index_keeps_old_and_changed_snapshots_separate(monkeypatch):
    import companion_daemon.world_v2.projection_lookup as module
    builds = []
    original = module._build_index
    def counted(rows, attribute):
        builds.append(len(rows))
        return original(rows, attribute)
    monkeypatch.setattr(module, '_build_index', counted)
    with module.replay_lookup_indexes():
        rows = ()
        for i in range(128):
            rows = module.append_indexed(rows, Row(identity=str(i), payload='old'), 'identity')
            assert module.lookup_first(rows, 'identity', '0') is rows[0]
        old = rows
        rows = module.append_indexed(rows, Row(identity='0', payload='duplicate'), 'identity')
        assert module.lookup_first(rows, 'identity', '0') is old[0]
        assert module.lookup_first(rows, 'identity', '0', predicate=lambda r:r.payload=='duplicate') is rows[-1]
        changed = (*rows[:1], rows[1].model_copy(update={'payload':'changed'}), *rows[2:])
        assert module.lookup_first(changed, 'identity', '1') is changed[1]
        assert module.lookup_first(old, 'identity', '1') is old[1]
        with module.replay_lookup_indexes():
            other = module.append_indexed((), Row(identity='0', payload='other'), 'identity')
            assert module.lookup_first(other, 'identity', '0') is other[0]
        assert module.lookup_first(rows, 'identity', '1') is rows[1]
    assert not module._REPLAY_INDEXES.get()
    assert max(builds) <= 1  # No growing prefix is rescanned or re-indexed.


def test_replay_authority_retains_last_match_and_never_leaks_new_prefix():
    from companion_daemon.world_v2.projection_lookup import append_indexed, lookup_last, replay_lookup_indexes
    with replay_lookup_indexes():
        rows = append_indexed((), Row(identity='key', payload='first'), 'identity')
        old = rows
        rows = append_indexed(rows, Row(identity='key', payload='last'), 'identity')
        assert lookup_first(rows, 'identity', 'key') is rows[0]
        assert lookup_last(rows, 'identity', 'key') is rows[-1]
        assert lookup_last(old, 'identity', 'key') is old[0]
        rows = append_indexed(rows, Row(identity='new', payload='new'), 'identity')
        assert lookup_last(old, 'identity', 'new') is None


def test_replay_cannot_index_mutable_rows_or_retain_its_stream_after_exit():
    import companion_daemon.world_v2.projection_lookup as module
    with module.replay_lookup_indexes():
        row = SimpleNamespace(identity='key')
        rows = module.append_indexed((), row, 'identity')
        assert module.lookup_first(rows, 'identity', 'key') is row
        row.identity = 'changed'
        assert module.lookup_first(rows, 'identity', 'key') is None
        assert module.lookup_last(rows, 'identity', 'changed') is row
    assert module._REPLAY_INDEXES.get() is None


def test_replay_probe_then_same_event_does_not_rebuild_the_growing_prefix(monkeypatch):
    import companion_daemon.world_v2.projection_lookup as module
    builds = []
    original = module._build_index
    def counted(rows,attribute):
        builds.append(len(rows))
        return original(rows, attribute)
    monkeypatch.setattr(module,'_build_index',counted)
    with module.replay_lookup_indexes():
        rows=()
        for i in range(64):
            row=Row(identity=str(i),payload='accepted')
            # Ledger legacy Acceptance verification trial-reduces this exact
            # event, discards that result, then performs the actual fold.
            trial=module.append_indexed(rows,row,'identity')
            assert module.lookup_last(rows,'identity',str(i)) is None
            rows=module.append_indexed(rows,row,'identity')
            assert module.lookup_first(rows,'identity',str(i)) is row
            assert module.lookup_last(trial,'identity',str(i)) is row
    assert max(builds)<=1
