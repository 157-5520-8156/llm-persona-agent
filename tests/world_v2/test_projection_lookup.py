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
    rows=tuple(Outer(entry=Row(identity=str(i),payload='old')) for i in range(12))
    rows=(*rows,Outer(entry=Row(identity='2',payload='duplicate')))
    for _ in range(10):
        assert lookup_first(rows,'entry.identity','2') is rows[2]
    assert lookup_first(rows,'entry.identity','2',predicate=lambda r:r.entry.payload=='duplicate') is rows[-1]
    changed=(*rows[:2], rows[2].model_copy(update={'entry':Row(identity='changed',payload='new')}), *rows[3:])
    assert lookup_first(changed,'entry.identity','2') is rows[-1]
    assert lookup_first(changed,'entry.identity','changed') is changed[2]
