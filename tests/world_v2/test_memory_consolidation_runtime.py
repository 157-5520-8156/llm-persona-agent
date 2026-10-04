from datetime import timedelta
import json
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.fact_memory_candidate_lifecycle import FactMemoryCandidateLifecycle
from companion_daemon.world_v2.fact_memory_draft import FactMemoryRetentionDraft
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.memory_consolidation_runtime import MemoryConsolidationRuntime, balanced_memory_batch
from companion_daemon.world_v2.memory_consolidation_contract import CONTRACT
from companion_daemon.world_v2.replay_evaluator import ReplayEvaluator
from companion_daemon.world_v2.memory_retrieval import _material_updated_at
from character_interior import canonical_inner_decision
from test_memory_candidate_authority import salience
from test_visible_selected_source_context import _sources
from test_life_development_runtime import _seed_clock


@pytest.mark.asyncio
async def test_public_application_memory_due_delegates_through_turn_runtime():
    from companion_daemon.world_v2.production_turn_application import WorldV2TurnApplication
    from companion_daemon.world_v2.world_turn_runtime import WorldTurnRuntime
    from datetime import UTC, datetime
    due = datetime(2026, 9, 28, tzinfo=UTC)

    class Runtime:
        async def memory_consolidation_next_due(self):
            return due

    turns = object.__new__(WorldTurnRuntime)
    turns._runtime = Runtime()
    app = object.__new__(WorldV2TurnApplication)
    app._turns = turns
    assert await app.memory_consolidation_next_due() == due


class Reviewer:
    def __init__(self, disposition="retain", fail=False):
        self.disposition, self.fail, self.calls = disposition, fail, 0

    async def consider(self, opportunity):
        self.calls += 1
        if self.fail:
            return SimpleNamespace(status="technical_failure", decision=None, failure_code="provider_unavailable")
        manifest = opportunity.capability_manifest
        assert manifest.payload["memories"][0]["reading"]["source_excerpts"]
        return canonical_inner_decision(opportunity, decision={
            "contract": "character-interior-purpose-decision.1", "purpose": opportunity.purpose,
            "source_refs": list(opportunity.source_refs), "capability_ref": manifest.capability_ref,
            "capability_payload_hash": manifest.payload_hash,
            "payload": {"contract": CONTRACT, "understandings": [], "choices": [
                {"candidate_token": token, "disposition": self.disposition, "next_review_hours": 72}
                for token in manifest.payload["offered_tokens"]]},
        }, identity=f"memory-periodic:{self.calls}")


def seed_memories(ledger):
    for fact in ledger.project().facts:
        p = ledger.project()
        transition = next(t for t in p.fact_transitions if t.fact_id == fact.fact_id and t.entity_revision == fact.entity_revision)
        event, commit = ledger.lookup_event_commit(fact.origin.accepted_event_ref)
        FactMemoryCandidateLifecycle(ledger=ledger, actor="worker:memory", source="test:memory").accept(
            fact=fact, transition=transition, fact_event=event, fact_world_revision=commit.world_revision,
            draft=FactMemoryRetentionDraft(cue_kind="future_utility", retention_rationales=("future_utility",), salience=salience()),
            logical_time=p.logical_time, created_at=p.logical_time, trace_id="trace:periodic", correlation_id="correlation:periodic")


def test_bounded_rebalanced_reads_match_complete_filter_for_every_availability_mask():
    from itertools import product

    ordered = [SimpleNamespace(candidate_id=str(i), values=SimpleNamespace(
        source_bindings=[SimpleNamespace(source_kind=kind)]))
        for i, kind in enumerate(("fact", "experience", "fact", "prehistory", "experience",
                                  "fact", "thread", "prehistory", "thread", "fact", "experience", "thread"))]
    for limit in (1, 4, 8):
        for flags in product((False, True), repeat=len(ordered)):
            readable = {c.candidate_id for c, keep in zip(ordered, flags) if keep}
            remaining = ordered
            selected = []
            while remaining:
                selected = balanced_memory_batch(remaining, limit=limit)
                unavailable = {c.candidate_id for c in selected} - readable
                if not unavailable:
                    break
                remaining = [c for c in remaining if c.candidate_id not in unavailable]
            else:
                selected = []
            assert balanced_memory_batch(
                [c for c in ordered if c.candidate_id in readable], limit=limit) == selected


@pytest.mark.asyncio
@pytest.mark.parametrize("unreadable_mode", ["none", "one", "all"])
async def test_open_batch_rebalances_without_exposing_unreadable_sources(tmp_path, monkeypatch, unreadable_mode):
    import companion_daemon.world_v2.memory_consolidation_runtime as module

    async with _sources(tmp_path, extra=True) as case:
        ledger = case.ledger
        seed_memories(ledger)
        projection = ledger.project()
        store = SQLiteImmutableLifeContentStore(path=tmp_path / "content.sqlite", world_id=ledger.world_id)
        worker = MemoryConsolidationRuntime(ledger=ledger, character_interior=Reviewer(),
            actor_ref="agent:companion", owner_id="worker:bounded", content_store=store)
        original_compile = module.MemoryRetrievalCompiler.compile
        calls = []
        suppressed = []

        def compile_readings(self, **kwargs):
            calls.append(tuple(c.candidate_id for c in kwargs["candidates"]))
            result = original_compile(self, **kwargs)
            if unreadable_mode == "all":
                return result.model_copy(update={"items": ()})
            if unreadable_mode == "one":
                if not suppressed:
                    assert result.items
                    suppressed.append(result.items[0].candidate_id)
                return result.model_copy(update={"items": tuple(
                    r for r in result.items if r.candidate_id not in suppressed)})
            return result

        monkeypatch.setattr(module.MemoryRetrievalCompiler, "compile", compile_readings)
        try:
            process = await worker._open(projection)
            if unreadable_mode == "all":
                assert process is None
                return
            assert process is not None
            job = json.loads(store.read_exact(content_ref=process.trigger_ref).text)
            expected = balanced_memory_batch(sorted(projection.memory_candidates,
                key=lambda c: (module.review_due(c), c.candidate_id)))
            assert calls[0] == tuple(c.candidate_id for c in expected)
            if unreadable_mode == "one":
                expected = [c for c in expected if c.candidate_id not in suppressed]
                assert all(not set(call).intersection(suppressed) for call in calls[1:])
            assert all(len(call) <= 8 for call in calls)
            due_reads = [identity for call in calls[:-1] for identity in call]
            assert len(due_reads) == len(set(due_reads))
            assert [c["candidate_id"] for c in job["candidates"]] == [c.candidate_id for c in expected]
            assert all(r["source_excerpts"] for r in job["readings"])
        finally:
            store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("disposition", ["retain", "forget"])
async def test_clock_opens_batch_character_decides_and_source_facts_survive_restart(tmp_path, disposition):
    async with _sources(tmp_path, extra=True) as case:
        ledger = case.ledger
        seed_memories(ledger)
        before = ledger.project()
        model = Reviewer(disposition)
        store = SQLiteImmutableLifeContentStore(path=tmp_path / "content.sqlite", world_id=ledger.world_id)
        worker = MemoryConsolidationRuntime(ledger=ledger, character_interior=model,
            actor_ref="agent:companion", owner_id="worker:periodic", content_store=store)
        # The shared source fixture already advances fourteen days after accepting its memories.
        assert await worker.next_due() <= before.logical_time
        _seed_clock(ledger, event_id="event:clock:periodic-day", logical_time=before.logical_time + timedelta(days=1), logical_time_from=before.logical_time)
        result = await worker.drain_one()
        assert result.status == "processed" and result.work_status != "technical_failure"
        after = ledger.project()
        assert after.facts == before.facts
        assert len(after.memory_candidates) == len(before.memory_candidates)
        assert all(c.values.status == ("active" if disposition == "retain" else "forgotten") for c in after.memory_candidates)
        assert model.calls == 1  # A batch, not one model call per memory.
        if disposition == "retain":
            assert all(_material_updated_at(c, after.memory_candidate_transitions) == old.updated_at
                       for c, old in zip(after.memory_candidates, before.memory_candidates, strict=True))
        cold = MemoryConsolidationRuntime(ledger=ledger, character_interior=model,
            actor_ref="agent:companion", owner_id="worker:periodic-new", content_store=store)
        assert (await cold.drain_one()).status == "idle"
        assert model.calls == 1
        assert ReplayEvaluator().evaluate(evidence=ledger.export_replay_evidence()).passed
        store.close()


@pytest.mark.asyncio
async def test_failure_keeps_memories_and_does_not_spend_again_until_retry_due(tmp_path):
    async with _sources(tmp_path) as case:
        ledger = case.ledger
        seed_memories(ledger)
        before = ledger.project()
        _seed_clock(ledger, event_id="event:clock:periodic-failure", logical_time=before.logical_time + timedelta(days=1), logical_time_from=before.logical_time)
        model = Reviewer(fail=True)
        store = SQLiteImmutableLifeContentStore(path=tmp_path / "content.sqlite", world_id=ledger.world_id)
        worker = MemoryConsolidationRuntime(ledger=ledger, character_interior=model,
            actor_ref="agent:companion", owner_id="worker:periodic", content_store=store)
        assert (await worker.drain_one()).work_status == "technical_failure"
        assert (await worker.drain_one()).status == "owned_elsewhere"
        assert model.calls == 1
        assert ledger.project().memory_candidates == before.memory_candidates
        assert await worker.next_due() > ledger.project().logical_time
        # The scheduler wakes exactly at due, not an arbitrary second later.
        # Reclaim must advance the attempt and its backoff at that boundary.
        due = await worker.next_due()
        _seed_clock(ledger, event_id="event:clock:periodic-exact-retry",
                    logical_time=due, logical_time_from=ledger.project().logical_time)
        assert (await worker.drain_one()).work_status == "technical_failure"
        assert model.calls == 2
        process = next(p for p in ledger.project().trigger_processes
                       if p.process_kind == worker.PROCESS_KIND and p.state != "terminal")
        assert len(process.attempt_ids) == 2
        assert process.claim_lease.expires_at > due
        restarted = MemoryConsolidationRuntime(ledger=ledger, character_interior=model,
            actor_ref="agent:companion", owner_id="worker:periodic", content_store=store)
        for _ in range(3):
            assert (await restarted.drain_one()).status == "owned_elsewhere"
        assert model.calls == 2
        store.close()


@pytest.mark.asyncio
async def test_crash_after_one_effect_resumes_saved_batch_without_reauthoring(tmp_path, monkeypatch):
    async with _sources(tmp_path, extra=True) as case:
        ledger = case.ledger
        seed_memories(ledger)
        before = ledger.project()
        model = Reviewer()
        store = SQLiteImmutableLifeContentStore(path=tmp_path / "content.sqlite", world_id=ledger.world_id)
        worker = MemoryConsolidationRuntime(ledger=ledger, character_interior=model,
            actor_ref="agent:companion", owner_id="worker:periodic", content_store=store)
        persist = worker._persist_choice

        def interrupted(*args):
            persist(*args)
            raise OSError("controlled crash after a durable effect")

        monkeypatch.setattr(worker, "_persist_choice", interrupted)
        with pytest.raises(OSError, match="controlled crash"):
            await worker.drain_one()
        recovered = MemoryConsolidationRuntime(ledger=ledger, character_interior=model,
            actor_ref="agent:companion", owner_id="worker:periodic", content_store=store)
        assert (await recovered.drain_one()).work_status == "reviewed"
        assert model.calls == 1
        assert all(c.entity_revision == old.entity_revision + 1 for c, old in zip(ledger.project().memory_candidates, before.memory_candidates, strict=True))
        assert ReplayEvaluator().evaluate(evidence=ledger.export_replay_evidence()).passed
        store.close()
