"""Post-Experience retention sees both authors, and reuses its durable decision."""

import json

import pytest

from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_experience_memory_recovery_queue import _CharacterMemoryChoice, _runtime
from test_world_stimulus_life_response import _ResponseHTTP, _settled
from test_world_stimulus_life_intent import WORLD, _build, _model


class _CapturingMemory(_CharacterMemoryChoice):
    def __init__(self):
        super().__init__(failures={})
        self.capabilities = []

    async def consider(self, opportunity):
        self.capabilities.append(json.loads(opportunity.capability_manifest.payload_json))
        return await super().consider(opportunity)


@pytest.mark.asyncio
@pytest.mark.parametrize("response_text", [None, "我猜他昨天已经换了工作，但我没有证据。"])
async def test_public_post_experience_retention_preserves_private_reading_and_recovers(
    tmp_path, monkeypatch, response_text,
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "retention.sqlite"
    provider = _ResponseHTTP(text=response_text)
    model = _model(provider)
    app = _build(path, model)
    try:
        await _settled(app)
        assert app.export_replay_evidence().projection.experiences == ()
        outcome_inputs = [json.loads(item["messages"][-1]["content"])
                          for item in provider.requests]
        outcome_inputs = [item for item in outcome_inputs
                          if item.get("inner_turn", {}).get("purpose") == "outcome_selection"]
        assert len(outcome_inputs) == 1
        offered = outcome_inputs[0]["capability_manifest"]["payload"]["candidates"][0]
        assert "summary" not in offered
        assert offered["world_consequence"]["environment"]["epistemic_scope"] == "candidate_world_environment"
        await app.drain_background_once()
        evidence = app.export_replay_evidence()
        assert len(evidence.projection.experiences) == 1
        experience = evidence.projection.experiences[0]
        clock_ref = [row.event.event_id for row in evidence.events
                     if row.event.event_type == "ClockAdvanced"][-1]
        assert len(provider.stimulus_requests) == 1
    finally:
        app.close()
        await model.aclose()
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
    try:
        memory = _CapturingMemory()
        runtime = _runtime(ledger, store, memory)
        await runtime.advance_once(wake_event_ref=clock_ref, trace_id="memory", correlation_id="memory")
        assert len(memory.capabilities) == 1
        capability = memory.capabilities[0]
        assert "verified_experience_text" not in capability
        assert capability["source_authorship_contract"] == "character-life-experience-summary.1"
        reading = capability["verified_experience_sources"]
        assert reading["world_consequence"]["environment"]["text"] == "一阵短雨已经停了。"
        assert reading["character_response"]["response_text"] == response_text
        assert reading["character_response"]["epistemic_scope"] == "private_interpretation_not_world_fact"
        assert memory.sources == [experience.origin.accepted_event_ref]
        state = ledger.project()
        decisions = [row.event for row in ledger.export_replay_evidence().events
                     if row.event.event_type == "ExperienceMemoryDecisionRecorded"]
        assert len(decisions) == 1
        assert len(state.experiences) == 1
        # This fixture's explicit no-retain decision does not fabricate a memory.
        assert not state.memory_candidates
        store.close()
        ledger.close()
        ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
        store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
        cold = _CapturingMemory()
        await _runtime(ledger, store, cold).advance_once(
            wake_event_ref=clock_ref, trace_id="memory", correlation_id="memory",
        )
        assert cold.capabilities == []
        assert ledger.project() == state
    finally:
        store.close()
        ledger.close()
