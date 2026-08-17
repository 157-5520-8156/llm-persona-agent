from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel

from companion_daemon.world_v2.associative_recall import lexical_relevance_bp
from companion_daemon.world_v2.ledger_context_resolver import (
    _bounded_domain_items,
    memory_read_score_bp,
    memory_relevance_bp,
)
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


class _RankValues(BaseModel):
    confidence_bp: int = 8_000


class _ScoredFact(BaseModel):
    fact_id: str
    values: _RankValues
    updated_at: datetime
    source_excerpt: str = ""


def test_lexical_relevance_prefers_current_trigger_overlap() -> None:
    assert lexical_relevance_bp("雅思报名", ("他下周雅思报名",)) > lexical_relevance_bp(
        "雅思报名", ("今天天气不错",)
    )
    now = datetime(2026, 8, 16, 12, tzinfo=UTC)
    related = _ScoredFact(
        fact_id="fact:ielts",
        values=_RankValues(),
        updated_at=now,
        source_excerpt="他下周要去雅思报名",
    )
    unrelated = _ScoredFact(
        fact_id="fact:weather",
        values=_RankValues(),
        updated_at=now,
        source_excerpt="今天天气不错",
    )
    assert memory_relevance_bp("雅思报名", related) > memory_relevance_bp("雅思报名", unrelated)
    selected = _bounded_domain_items(
        "relevant_facts",
        (unrelated, related),
        now,
        query_text="雅思报名",
    )
    assert selected is not None
    assert selected[0].fact_id == "fact:ielts"
