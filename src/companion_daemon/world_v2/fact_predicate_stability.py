"""Predicate lifecycle for durable vs episodic user Facts.

Single-cardinality slots (profile, residence, timezone) describe stable
identity state.  Set-cardinality slots (recent circumstances, current
activity, preferences) accumulate episodic statements whose retrieval may
rank by recency without treating a three-week-old name as "forgotten".
"""

from __future__ import annotations

from typing import Literal

from .fact_reducers import INSTALLED_FACT_PREDICATE_CARDINALITY

FactPredicateStability = Literal["stable", "episodic"]

# Basis-point floor used only for ranking durable Facts.  This is a lifecycle
# signal ("still true until corrected"), not a claim that the Fact was recent.
STABLE_FACT_RECENCY_BP = 9_000


def fact_predicate_stability(predicate_code: str) -> FactPredicateStability:
    cardinality = INSTALLED_FACT_PREDICATE_CARDINALITY.get(predicate_code)
    if cardinality == "single":
        return "stable"
    return "episodic"


def fact_predicate_is_stable(predicate_code: str) -> bool:
    return fact_predicate_stability(predicate_code) == "stable"


__all__ = (
    "FactPredicateStability",
    "STABLE_FACT_RECENCY_BP",
    "fact_predicate_is_stable",
    "fact_predicate_stability",
)
