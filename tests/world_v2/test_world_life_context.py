from __future__ import annotations

import json
from datetime import timedelta

import pytest

from companion_daemon.world_v2.context_resolver import query_from_projection
from companion_daemon.world_v2.ledger import WorldLedger
from companion_daemon.world_v2.ledger_context_resolver import (
    ContextRelevanceScope,
    context_capsule_compiler_from_ledger,
)
from companion_daemon.world_v2.world_life_context import WorldLifeContextCompiler
from test_life_projection import WORLD_ID, commit, seed_through_proposal, settlement_batch


def _settled_life_ledger() -> WorldLedger:
    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    seed_through_proposal(ledger)
    commit(ledger, settlement_batch())
    return ledger


def test_settled_npc_occurrence_is_not_companion_life_when_only_bound_by_plan() -> None:
    ledger = _settled_life_ledger()

    assert WorldLifeContextCompiler().compile(
        projection=ledger.project(), actor_ref="actor:companion"
    ) == ()


def test_plan_owned_npc_occurrence_does_not_enter_companion_context() -> None:
    ledger = _settled_life_ledger()
    projection = ledger.project()
    capsule = context_capsule_compiler_from_ledger(
        ledger=ledger,
        relevance_scope=ContextRelevanceScope(actor_ref="actor:companion"),
    ).compile(
        query_from_projection(
            projection, actor_ref="actor:companion", trigger_ref="event:next-turn"
        )
    )

    assert capsule.world_life.availability == "available"
    assert capsule.world_life.source_refs == ()
    model_slice = json.loads(capsule.model_content_json)["slices"]["world_life"]
    assert model_slice["items"] == []


def test_npc_occurrence_does_not_leak_to_an_unrelated_actor() -> None:
    ledger = _settled_life_ledger()

    assert WorldLifeContextCompiler().compile(
        projection=ledger.project(), actor_ref="actor:unrelated"
    ) == ()


@pytest.mark.asyncio
async def test_exact_new_settlement_precedes_unrelated_preproof_context(tmp_path, monkeypatch):
    from companion_daemon.world_v2.life_content import LifeContentCompiler
    from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
    from companion_daemon.world_v2.ledger_context_resolver import _bounded_domain_items
    from test_world_life_response_readers import _cursor
    from test_world_stimulus_life_response import _ResponseHTTP, _settled
    from test_world_stimulus_life_intent import ACTOR, WORLD, _build, _model

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "focus.sqlite"
    model = _model(_ResponseHTTP())
    app = _build(path, model)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
    try:
        event = await _settled(app)
        state = app.export_replay_evidence().projection
        (target,) = WorldLifeContextCompiler(life_content=LifeContentCompiler(store=store)).compile(
            projection=state, actor_ref=ACTOR, cursor=_cursor(state))
        # Sorting does not validate these decoys or grant them source authority.
        decoys = tuple(target.model_copy(update={
            "occurrence_id": f"aaa:unrelated:{i}",
            "settled_at": target.settled_at + timedelta(seconds=1),
            "source": target.source.model_copy(update={"authority_event_ref": f"event:unrelated:{i}"}),
        }) for i in range(8))
        import companion_daemon.world_v2.ledger_context_resolver as module
        monkeypatch.setattr(module, "MAX_INPUT_ITEMS_PER_SLICE", 3)
        assert target not in _bounded_domain_items("world_life", (*decoys, target), state.logical_time)
        selected = _bounded_domain_items("world_life", (*decoys, target), state.logical_time,
            trigger_ref=event.event_id)
        assert selected[0] == target and len(selected) == 3
    finally:
        store.close()
        app.close()
        await model.aclose()
