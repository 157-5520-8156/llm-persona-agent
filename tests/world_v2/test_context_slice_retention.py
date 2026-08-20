"""Tests for context slice retention guard."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.context_capsule import TruncationEntry
from companion_daemon.world_v2.context_slice_retention import assert_context_slice_retention_coverage


def _fact_item(predicate: str = "profile.display_name") -> SimpleNamespace:
    return SimpleNamespace(payload_json=json.dumps({"predicate_code": predicate}))


def _capsule_stub(*, kept: int, truncation: tuple[TruncationEntry, ...]) -> SimpleNamespace:
    return SimpleNamespace(
        relevant_facts=SimpleNamespace(
            availability="available",
            items=[_fact_item() for _ in range(kept)],
        ),
        budget=SimpleNamespace(truncation_log=truncation),
    )


def test_retention_guard_flags_unexplained_stable_loss() -> None:
    capsule = _capsule_stub(
        kept=0,
        truncation=(
            TruncationEntry(
                slice_name="relevant_facts",
                reason="item_budget",
                omitted_count=17,
            ),
        ),
    )
    with pytest.raises(AssertionError, match="stable resolved=6"):
        assert_context_slice_retention_coverage(
            capsule=capsule,  # type: ignore[arg-type]
            resolved_counts={
                "relevant_facts": 23,
                "relevant_facts_stable": 6,
            },
        )


def test_retention_guard_passes_when_truncation_accounts_for_gap() -> None:
    capsule = _capsule_stub(
        kept=3,
        truncation=(
            TruncationEntry(
                slice_name="relevant_facts",
                reason="item_budget",
                omitted_count=7,
            ),
            TruncationEntry(
                slice_name="relevant_facts",
                reason="global_character_budget",
                omitted_count=13,
            ),
        ),
    )
    assert_context_slice_retention_coverage(
        capsule=capsule,  # type: ignore[arg-type]
        resolved_counts={"relevant_facts": 23, "relevant_facts_stable": 3},
    )
