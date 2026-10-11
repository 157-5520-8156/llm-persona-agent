import gc
import weakref
from companion_daemon.world_v2.immutable_value_cache import immutable_value_cache
from companion_daemon.world_v2.schema_core import FrozenModel


class Row(FrozenModel):
    name: str


def test_identity_cache_recomputes_copies_is_bounded_and_does_not_keep_input_alive():
    calls = []

    @immutable_value_cache(maxsize=2)
    def read(row):
        calls.append(row.name)
        return row.name

    row = Row(name="first")
    assert read(row) == read(row) == "first"
    assert calls == ["first"]
    assert read(row.model_copy(update={"name": "second"})) == "second"
    assert read(row.model_copy()) == "first"
    assert calls == ["first", "second", "first"]
    assert read.cache_info().currsize <= 2
    pointer = weakref.ref(row)
    del row
    gc.collect()
    assert pointer() is None


def test_mutable_nested_fields_are_not_memoized():
    class Nested(FrozenModel):
        values: list[str]

    @immutable_value_cache(maxsize=2)
    def read(row):
        return tuple(row.values)

    row = Nested(values=["old"])
    assert read(row) == ("old",)
    row.values[0] = "changed"
    assert read(row) == ("changed",)
    assert read.cache_info().currsize == 0


def test_cache_binds_every_model_and_scalar_option():
    @immutable_value_cache(maxsize=4)
    def combine(first, second, *, limit=5):
        return (first.name + second.name)[:limit]

    a, b = Row(name="one"), Row(name="two")
    assert combine(a, b, limit=2) == "on"
    assert combine(a, b, limit=6) == "onetwo"
    assert combine(a, b.model_copy(update={"name": "new"}), limit=6) == "onenew"
    assert combine(a, b, limit=6) == "onetwo"
    assert combine.cache_info().hits == 1


def test_tuple_inputs_bind_every_element_without_owning_the_collection():
    calls = []

    @immutable_value_cache(maxsize=2)
    def read(rows):
        calls.append(1)
        return tuple(row.name for row in rows)

    a, b = Row(name='a'), Row(name='b')
    assert read((a, b)) == read(tuple([a, b])) == ('a', 'b')
    assert len(calls) == 1
    assert read((b, a)) == ('b', 'a')
    assert read((a.model_copy(update={'name': 'new'}), b)) == ('new', 'b')
    assert len(calls) == 3
    pointer = weakref.ref(b)
    del b
    gc.collect()
    assert pointer() is None
