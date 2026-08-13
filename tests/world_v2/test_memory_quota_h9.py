from __future__ import annotations

from companion_daemon.world_v2.ledger_context_resolver import memory_read_score_bp
from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore
from companion_daemon.world_v2.present_prompt import PRESENT_MEMORY_ITEM_LIMIT


def test_memory_read_score_is_recency_times_importance_times_relevance() -> None:
    assert (
        memory_read_score_bp(recency_bp=10_000, importance_bp=10_000, relevance_bp=10_000) == 10_000
    )
    assert memory_read_score_bp(recency_bp=5_000, importance_bp=8_000, relevance_bp=10_000) == 4_000
    weak_old = memory_read_score_bp(recency_bp=1_000, importance_bp=2_000)
    strong_recent = memory_read_score_bp(recency_bp=9_000, importance_bp=9_000)
    assert strong_recent > weak_old


def test_present_memory_quota_is_three_to_eight() -> None:
    assert 3 <= PRESENT_MEMORY_ITEM_LIMIT <= 8


def test_health_exposes_last_memory_write_and_fact_authority(tmp_path) -> None:
    path = tmp_path / "usage.sqlite"
    store = WorldV2UsageStore(path=str(path))
    health = store.budget_state(monthly_budget_cny=100.0, daily_budget_cny=10.0)
    assert health["chat_recall_authority"] == "FactCommittedV2"
    assert "last_memory_write_at" in health
    assert health["last_memory_write_at"] is None
