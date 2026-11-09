"""Verify the 3-tier SMS memory hierarchy and PGMem provenance/evolution graph compiler."""

from datetime import datetime, timedelta, timezone

import pytest

from companion_daemon.world_v2.dashboard_memory_hierarchy import (
    compile_dashboard_memory_hierarchy,
)
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.memory_understanding import MemoryUnderstandingReader
from test_memory_understanding import Learner, _sources, clock, seed_memories, worker


@pytest.mark.asyncio
async def test_compile_dashboard_memory_hierarchy_builds_three_tiers_and_provenance_evolution_edges(
    tmp_path,
) -> None:
    async with _sources(tmp_path, extra=True) as case:
        ledger = case.ledger
        seed_memories(ledger)
        store = SQLiteImmutableLifeContentStore(
            path=tmp_path / "hierarchy-content.sqlite",
            world_id=ledger.world_id,
        )
        try:
            first_model = Learner()
            assert (await worker(ledger, store, first_model).drain_one()).work_status == "reviewed"

            clock(ledger, "merge", 72)
            merge_model = Learner("merge")
            assert (await worker(ledger, store, merge_model).drain_one()).work_status == "reviewed"

            projection = ledger.project()
            understanding_snapshot = MemoryUnderstandingReader(
                ledger=ledger,
                content_store=store,
                actor_ref="agent:companion",
            ).read(projection)

            hierarchy = compile_dashboard_memory_hierarchy(
                projection=projection,
                ledger=ledger,
                life_content_store=store,
                understanding_snapshot=understanding_snapshot,
                actor_ref="agent:companion",
            )

            assert hierarchy.contract == "dashboard-memory-hierarchy.1"
            tiers = {t.tier_id: t for t in hierarchy.tiers}
            assert set(tiers) == {"working_self", "semantic_facts", "episodic_impressions"}

            assert tiers["working_self"].active_count >= 1
            assert tiers["working_self"].historical_count >= 2
            assert tiers["semantic_facts"].active_count >= 1
            assert tiers["episodic_impressions"].active_count >= 1

            understanding_nodes = [
                n for n in hierarchy.nodes if n.node_kind == "memory_understanding"
            ]
            assert len(understanding_nodes) == 3
            assert {n.status for n in understanding_nodes} == {"active", "superseded"}

            support_edges = [e for e in hierarchy.edges if e.edge_kind == "support"]
            evolution_edges = [e for e in hierarchy.edges if e.edge_kind == "evolution"]
            assert support_edges
            assert len(evolution_edges) == 2
            assert all(e.operation == "merge" for e in evolution_edges)
        finally:
            store.close()
