"""Content-address lookup recovers bytes without guessing a Fact's meaning."""
import hashlib

import pytest

from companion_daemon.world_v2.fact_observation_value import FactObservationValueBinding
from companion_daemon.world_v2.fact_observation_value_lookup import (
    _lookup_exact_value, resolve_observation_fact_value,
)


def _binding(value):
    sha = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return FactObservationValueBinding(value_ref="value:observation:" + sha, value_hash=sha)


@pytest.mark.parametrize("source,value", [
    ("用户决定取消周五的报告，仍保留周四的约定。", "保留周四的约定"),
    ("same same same", "same"),
    ("猫🐈\u200d⬛茶e\u0301。", "🐈\u200d⬛茶e\u0301"),
    (" with  spaces\n and punctuation. ", " spaces\n and punctuation. "),
    ("abc", "abc"),
    ("x" + "界" * 256 + "y", "界" * 256),
])
def test_recovers_exact_bytes_with_unicode_repeated_spans_and_value_bound(source, value):
    assert resolve_observation_fact_value(binding=_binding(value), source_excerpt=source) == value


@pytest.mark.parametrize("source,value", [
    ("取消周五的报告，仍保留周四的约定。", "保留周五的约定"),
    ("e\u0301", "é"),  # Canonically equivalent Unicode must not be normalized.
    ("keep Thursday", "keep thursday"),
    ("x" * 257, "x" * 257),
    ("ordinary text", ""),
])
def test_no_matching_bytes_or_out_of_producer_value_bound_fails_closed(source, value):
    with pytest.raises(ValueError, match="unavailable"):
        resolve_observation_fact_value(binding=_binding(value), source_excerpt=source)


@pytest.mark.parametrize("source", ["", "x" * 4097, None, ["text"]])
def test_source_is_bounded_before_any_lookup(source):
    before = _lookup_exact_value.cache_info()
    with pytest.raises(ValueError, match="source exceeds"):
        resolve_observation_fact_value(binding=_binding("x"), source_excerpt=source)
    assert _lookup_exact_value.cache_info() == before


def test_binding_is_revalidated_before_lookup():
    valid = _binding("x")
    for binding in (
        {"value_hash": valid.value_hash},
        valid.model_copy(update={"value_ref": "value:another"}),
        valid.model_copy(update={"contract": "unknown"}),
        valid.model_copy(update={"value_hash": "not-a-digest"}),
    ):
        with pytest.raises(ValueError):
            resolve_observation_fact_value(binding=binding, source_excerpt="x")


def test_cache_uses_exact_source_and_value_hash_and_is_bounded():
    _lookup_exact_value.cache_clear()
    first = _binding("one")
    assert resolve_observation_fact_value(binding=first, source_excerpt="one two") == "one"
    assert resolve_observation_fact_value(binding=first, source_excerpt="one two") == "one"
    assert _lookup_exact_value.cache_info().hits == 1
    assert resolve_observation_fact_value(binding=_binding("two"), source_excerpt="one two") == "two"
    assert resolve_observation_fact_value(binding=first, source_excerpt="one") == "one"
    assert _lookup_exact_value.cache_info().misses == 3
    # Absence is a cached identity result, not permission to select another text.
    for _ in range(2):
        with pytest.raises(ValueError, match="unavailable"):
            resolve_observation_fact_value(binding=first, source_excerpt="two")
    assert _lookup_exact_value.cache_info().misses == 4
    assert _lookup_exact_value.cache_info().hits == 2
    for index in range(130):
        source = f"entry-{index}"
        assert resolve_observation_fact_value(binding=_binding(source), source_excerpt=source) == source
    assert _lookup_exact_value.cache_info().maxsize == 128
    assert _lookup_exact_value.cache_info().currsize == 128
    _lookup_exact_value.cache_clear()


def test_cached_identity_still_runs_exact_quote_verification(monkeypatch):
    import companion_daemon.world_v2.fact_observation_value_lookup as module

    monkeypatch.setattr(module, "_lookup_exact_value", lambda source, value_hash: "wrong value")
    with pytest.raises(ValueError, match="exact accepted Fact value"):
        resolve_observation_fact_value(binding=_binding("right value"), source_excerpt="right value wrong value")
