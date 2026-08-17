from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.character_interior.production import (
    _CharacterInteriorBackgroundDriver,
)
from companion_daemon.world_v2.character_interior.snapshot_compiler import (
    compile_inner_life_snapshot,
)
from companion_daemon.world_v2.context_resolver import query_from_projection
from companion_daemon.world_v2.ledger_context_resolver import (
    ContextRelevanceScope,
    context_capsule_compiler_from_ledger,
)
from companion_daemon.world_v2.present_prompt import compile_slim_consider_payload
from companion_daemon.world_v2.private_impression_producer import (
    compile_paid_private_impression_draft,
)
from test_private_impression_producer import (
    _Model,
    _ledger_with_active_appraisal,
    _private_runtime,
)


def test_slim_stuck_with_me_compiles_into_a_private_impression_draft() -> None:
    compiled = compile_slim_consider_payload(
        {
            "messages": ["嗯"],
            "felt": "心里还搁着刚才那句",
            "stuck_with_me": "他说完我就一直在想他到底怎么看我",
            "wants": "想把这件事慢慢看清楚",
            "photo": False,
            "noticed": "回家路上看见一只猫在便利店门口停了一会儿",
            "keep_impression": True,
            "relationship_signal": {
                "signal_code": "should_not_enter_slim",
                "rationale_code": "slim_must_not_write_relationship",
                "confidence_bp": 8000,
            },
        }
    )

    assert compiled is not None
    private_state = compiled["expression_draft"]["private_turn_state"]
    assert private_state["noticed"] == "回家路上看见一只猫在便利店门口停了一会儿"
    assert private_state["keep_impression"] is True
    assert "relationship_signal" not in compiled["appraisal_draft"]

    assert compiled is not None
    assert compiled["expression_draft"]["impulse_summary"] == "想把这件事慢慢看清楚"
    draft = compile_paid_private_impression_draft(
        reflection_summary=str(
            compiled["expression_draft"]["private_turn_state"]["inner_state_summary"]
        ),
        offered_source_refs=("event:observation:1", "event:appraisal:1"),
        keep_impression=True,
    )
    assert draft is not None
    assert draft.decision == "retain"
    assert draft.reflection_summary == "他说完我就一直在想他到底怎么看我"
    assert draft.source_refs == ("event:observation:1", "event:appraisal:1")
    assert draft.predecessor_refs == ()


def test_empty_stuck_with_me_does_not_open_an_impression() -> None:
    assert (
        compile_paid_private_impression_draft(
            reflection_summary="   ",
            offered_source_refs=("event:observation:1",),
            keep_impression=True,
        )
        is None
    )
    assert (
        compile_paid_private_impression_draft(
            reflection_summary="还搁着",
            offered_source_refs=(),
            keep_impression=True,
        )
        is None
    )


def test_stuck_with_me_is_dropped_unless_she_keeps_the_impression() -> None:
    assert (
        compile_paid_private_impression_draft(
            reflection_summary="他说完我就一直在想他到底怎么看我",
            offered_source_refs=("event:observation:1", "event:appraisal:1"),
        )
        is None
    )
    assert (
        compile_paid_private_impression_draft(
            reflection_summary="他说完我就一直在想他到底怎么看我",
            offered_source_refs=("event:observation:1", "event:appraisal:1"),
            keep_impression=False,
        )
        is None
    )


@pytest.mark.asyncio
async def test_production_private_impression_drain_does_not_call_the_model() -> None:
    called: list[str] = []
    driver = object.__new__(_CharacterInteriorBackgroundDriver)
    driver._private_impression = SimpleNamespace(
        advance_due_once=lambda: called.append("advance") or SimpleNamespace(status="accepted")
    )
    driver._private_impression_opener = SimpleNamespace(
        open_once=lambda: called.append("open")
    )

    result = await driver.drain_private_impression_once()

    assert result is None
    assert called == []


@pytest.mark.asyncio
async def test_paid_inbound_impression_is_dropped_unless_she_keeps_it() -> None:
    ledger = _ledger_with_active_appraisal()
    runtime, _interior = _private_runtime(ledger, _Model([]))
    located = ledger.lookup_event_commit("message-event:1")
    assert located is not None
    source_event = located[0]
    before = len(ledger.project().private_impressions)

    dropped = await runtime.record_paid_inbound(
        keep_impression=False,
        reflection_summary="他说完我就一直在想他到底怎么看我",
        model_result_ref="model-result:paid-inbound:test",
        source_event=source_event,
    )
    omitted = await runtime.record_paid_inbound(
        keep_impression=None,
        reflection_summary="他说完我就一直在想他到底怎么看我",
        model_result_ref="model-result:paid-inbound:test",
        source_event=source_event,
    )

    assert dropped is None
    assert omitted is None
    assert ledger.project().private_impressions == ()
    assert len(ledger.project().private_impressions) == before


@pytest.mark.asyncio
async def test_paid_inbound_impression_lands_only_when_she_keeps_it() -> None:
    ledger = _ledger_with_active_appraisal()
    model = _Model([])
    runtime, _interior = _private_runtime(ledger, model)
    located = ledger.lookup_event_commit("message-event:1")
    assert located is not None
    source_event = located[0]

    accepted = await runtime.record_paid_inbound(
        keep_impression=True,
        reflection_summary="他说完我就一直在想他到底怎么看我",
        model_result_ref="model-result:paid-inbound:test",
        source_event=source_event,
    )

    assert accepted is not None
    impressions = ledger.project().private_impressions
    assert len(impressions) == 1
    assert impressions[0].status == "active"
    assert "一直在想" in impressions[0].reflection_summary
    assert model.calls == []


@pytest.mark.asyncio
async def test_kept_stuck_with_me_reaches_the_next_inner_life_snapshot() -> None:
    ledger = _ledger_with_active_appraisal()
    runtime, _interior = _private_runtime(ledger, _Model([]))
    located = ledger.lookup_event_commit("message-event:1")
    assert located is not None
    source_event = located[0]
    kept_text = "他说完我就一直在想他到底怎么看我"

    accepted = await runtime.record_paid_inbound(
        keep_impression=True,
        reflection_summary=kept_text,
        model_result_ref="model-result:paid-inbound:test",
        source_event=source_event,
    )
    assert accepted is not None
    impression = ledger.project().private_impressions[0]
    assert impression.status == "active"
    assert impression.origin is not None
    assert impression.reflection_summary == kept_text

    capsules = context_capsule_compiler_from_ledger(
        ledger=ledger,
        relevance_scope=ContextRelevanceScope(
            actor_ref="actor:companion",
            related_subject_refs=(impression.subject_ref,),
        ),
    )
    projection = ledger.project()
    capsule = capsules.compile(
        query_from_projection(
            projection,
            actor_ref="actor:companion",
            trigger_ref="message-event:1",
        )
    )
    snapshot = compile_inner_life_snapshot(json.loads(capsule.model_content_json)).model_view()
    materials = snapshot["materials"]["private_impressions"]
    assert any(item.get("reflection_summary") == kept_text for item in materials)


@pytest.mark.asyncio
async def test_dropped_stuck_with_me_does_not_reach_the_next_inner_life_snapshot() -> None:
    ledger = _ledger_with_active_appraisal()
    runtime, _interior = _private_runtime(ledger, _Model([]))
    located = ledger.lookup_event_commit("message-event:1")
    assert located is not None
    source_event = located[0]

    dropped = await runtime.record_paid_inbound(
        keep_impression=False,
        reflection_summary="他说完我就一直在想他到底怎么看我",
        model_result_ref="model-result:paid-inbound:test",
        source_event=source_event,
    )
    assert dropped is None
    assert ledger.project().private_impressions == ()

    capsules = context_capsule_compiler_from_ledger(
        ledger=ledger,
        relevance_scope=ContextRelevanceScope(
            actor_ref="actor:companion",
            related_subject_refs=("interaction:user:1",),
        ),
    )
    projection = ledger.project()
    capsule = capsules.compile(
        query_from_projection(
            projection,
            actor_ref="actor:companion",
            trigger_ref="message-event:1",
        )
    )
    snapshot = compile_inner_life_snapshot(json.loads(capsule.model_content_json)).model_view()
    assert "private_impressions" not in snapshot["materials"]
