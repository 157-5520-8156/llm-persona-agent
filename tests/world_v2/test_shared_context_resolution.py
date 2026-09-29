"""Policy variants reuse sources without sharing authority or truncation."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest

from companion_daemon.world_v2.context_capsule import (
    ContextCapsuleBudgetPolicy, ContextCapsuleCompiler, SliceBudget,
)
from companion_daemon.world_v2.context_resolver import query_from_projection
from companion_daemon.world_v2.ledger import WorldLedger
from companion_daemon.world_v2.ledger_context_resolver import LedgerProjectionContextResolver
from companion_daemon.world_v2.situation_compiler import SituationCompiler
from test_ledger_context_resolver import NOW, _event, _message_observation


class RecallCounter:
    def __init__(self):
        self.refreshes = 0
        self.schedules = 0

    def refresh(self, **kwargs):
        self.refreshes += 1

    def schedule_prefetch(self, **kwargs):
        self.schedules += 1


def setup_context():
    ledger = WorldLedger.in_memory(world_id="world:shared-context")
    ledger.commit(
        [_event(ledger.world_id), *(
            _message_observation(ledger.world_id, i, f"message {i}",
                                 received_at=NOW + timedelta(seconds=i))
            for i in range(4)
        )], expected_world_revision=0, expected_deliberation_revision=0,
    )
    recall = RecallCounter()
    resolver = LedgerProjectionContextResolver(ledger=ledger, situation_compiler=SituationCompiler(), recall_coordinator=recall)
    compiler = ContextCapsuleCompiler(resolver=resolver)
    query = query_from_projection(ledger.project(), actor_ref="actor:companion",
                                  trigger_ref="event:message:3")
    return ledger, recall, resolver, compiler, query


def test_shared_sources_preserve_each_policy_bytes_and_prepared_issuer():
    ledger, recall, resolver, full, query = setup_context()
    policy = ContextCapsuleBudgetPolicy(recent_dialogue=SliceBudget(
        max_items=1, max_fields=500, max_characters=10000,
    ))
    slim = full.with_policy(policy)
    narrow = slim.compile(query)
    wide = full.compile(query)
    independent = ContextCapsuleCompiler(
        resolver=LedgerProjectionContextResolver(ledger=ledger, situation_compiler=SituationCompiler()), policy=policy,
    ).compile(query)
    assert narrow == independent
    assert len(narrow.recent_dialogue.items) == 1
    assert len(wide.recent_dialogue.items) == 4
    assert recall.refreshes == recall.schedules == 1
    assert resolver.performance_counters().cache_misses == 1
    with pytest.raises(ValueError, match="another compiler"):
        full.finalize_prepared(slim.prepare_for_deliberation(query))

    # Both compiler and resolver have warm entries; a head advance must still
    # reject the old query, then refresh the recall context for the new head.
    head = ledger.project()
    ledger.commit([_message_observation(ledger.world_id, 4, "new message", received_at=NOW)],
                  expected_world_revision=head.world_revision,
                  expected_deliberation_revision=head.deliberation_revision)
    for compiler in (full, slim):
        with pytest.raises(ValueError, match="indexed projection reader"):
            compiler.compile(query)
    fresh = query_from_projection(ledger.project(), actor_ref=query.actor_ref,
                                  trigger_ref="event:message:4")
    full.compile(fresh)
    slim.compile(fresh)
    assert recall.refreshes == recall.schedules == 2


def test_concurrent_policy_consumers_start_one_recall_job():
    _, recall, resolver, full, query = setup_context()
    sibling = full.with_policy(ContextCapsuleBudgetPolicy())
    barrier = Barrier(2)

    def compile_once(compiler):
        barrier.wait(timeout=5)
        return compiler.compile(query)

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(compile_once, full)
        b = pool.submit(compile_once, sibling)
        assert a.result(timeout=10) == b.result(timeout=10)
    assert recall.refreshes == recall.schedules == 1
    assert resolver.performance_counters().cache_hits == 1


@pytest.mark.parametrize("field,value", [
    ("trigger_ref", "event:message:2"), ("actor_ref", "actor:other"),
    ("logical_time", NOW + timedelta(seconds=30)),
])
def test_shared_cache_still_partitions_query(field, value):
    _, _, resolver, full, query = setup_context()
    full.compile(query)
    changed = query.model_copy(update={field: value})
    # A different query may be rejected by authority validation; it must never
    # receive a cached result belonging to the previous scope or time.
    try:
        result = full.with_policy(ContextCapsuleBudgetPolicy()).compile(changed)
    except ValueError:
        pass
    else:
        assert getattr(result, field) == value
    assert resolver.performance_counters().cache_hits == 0
