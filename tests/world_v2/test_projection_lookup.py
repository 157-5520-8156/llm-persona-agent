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
