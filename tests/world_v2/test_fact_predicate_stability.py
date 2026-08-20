"""Tests for stable vs episodic Fact predicate lifecycle."""

from __future__ import annotations

from companion_daemon.world_v2.fact_predicate_stability import (
    STABLE_FACT_RECENCY_BP,
    fact_predicate_is_stable,
    fact_predicate_stability,
)


def test_single_cardinality_predicates_are_stable() -> None:
    assert fact_predicate_stability("profile.display_name") == "stable"
    assert fact_predicate_stability("profile.education") == "stable"
    assert fact_predicate_is_stable("location.home") is True


def test_set_cardinality_predicates_are_episodic() -> None:
    assert fact_predicate_stability("situation.recent") == "episodic"
    assert fact_predicate_stability("activity.current") == "episodic"
    assert fact_predicate_is_stable("preference.likes") is False


def test_stable_recency_floor_is_high_enough_to_rank() -> None:
    assert STABLE_FACT_RECENCY_BP >= 8_000
