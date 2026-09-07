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
    PrivateImpressionTriggerOpener,
    compile_paid_private_impression_draft,
)
from companion_daemon.world_v2.private_turn_state import PrivateTurnState
from companion_daemon.world_v2.character_interior.inbound_author import (
    _InboundCharacterAuthor,
)
from companion_daemon.world_v2.world_turn_runtime import InboundTurn
from test_production_turn_application import (
    NOW,
    _config,
    _DeliveredTransport,
    _Identities,
    _Router,
)
from world_v2_application import (
    build_sqlite_world_v2_test_application,
    compose_fixture_character_interior,
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
            "meaning_of_this": "他那句话还没有说透",
            "my_state": "我心里还搁着刚才那句",
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
    state = PrivateTurnState.model_validate_json(json.dumps(private_state))
    assert state.stuck_with_me == "他说完我就一直在想他到底怎么看我"
    assert state.inner_state_summary == "我心里还搁着刚才那句"
    assert "relationship_signal" not in compiled["appraisal_draft"]

    assert compiled is not None
    assert compiled["expression_draft"]["impulse_summary"] == "想把这件事慢慢看清楚"
    draft = compile_paid_private_impression_draft(
        reflection_summary=str(
            compiled["expression_draft"]["private_turn_state"]["stuck_with_me"]
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


def test_legacy_private_state_round_trips_without_inventing_a_retained_residue() -> None:
    legacy = {
        "contract": "private-turn-state.1",
        "inner_state_summary": "这一刻有点疲倦",
        "attended_source_refs": [],
        "keep_impression": True,
    }
    state = PrivateTurnState.model_validate_json(json.dumps(legacy))
    assert state.stuck_with_me is None
    assert state.model_dump(mode="json") == legacy


@pytest.mark.asyncio
@pytest.mark.parametrize("stuck_with_me", ["他说只是忙，不等于我对他不重要", None])
async def test_inbound_keeps_only_the_authored_residue_not_the_momentary_state(
    tmp_path, stuck_with_me: str | None,
) -> None:
    class RoleModel:
        model = "test-paid-residue"

        async def complete(self, messages, *, temperature=0.8):
            return json.dumps(
                {
                    "messages": ["嗯，我听到了。"],
                    "meaning_of_this": "他在解释这两天的忙碌",
                    "my_state": "我松了口气，现在想去睡一觉",
                    "stuck_with_me": stuck_with_me,
                    "keep_impression": True,
                },
                ensure_ascii=False,
            )

    app = build_sqlite_world_v2_test_application(
        path=tmp_path / "paid-residue.sqlite",
        config=_config(),
        identities=_Identities(),
        router=_Router(),
        character_interior=compose_fixture_character_interior(
            inbound_author=_InboundCharacterAuthor(flash_model=RoleModel()),
        ),
        transport=_DeliveredTransport(),
        now=NOW,
    )
    try:
        outcome = await app.respond(
            InboundTurn(
                platform="test",
                platform_user_id="user.1",
                platform_message_id="message:paid-residue",
                text="这两天忙，没及时回你。",
                observed_at=NOW,
                trace_id="trace:paid-residue",
            )
        )
        evidence = app.export_replay_evidence()
    finally:
        app.close()

    assert outcome.status == "action_authorized"
    assert [item.reflection_summary for item in evidence.projection.private_impressions] == (
        [stuck_with_me] if stuck_with_me is not None else []
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
async def test_proactive_paid_residue_reaches_the_next_projection(tmp_path) -> None:
    from test_proactive_action_production import (
        NOW as PROACTIVE_NOW,
        WORLD,
        _application_config,
        _fixture_character_interior,
        _InvalidMain,
        _NoDispatchTransport,
        _seed_due_thread,
        _WarmthDraftModel,
    )
    from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger

    kept_text = "这件事我还没有想通，想留着以后再看"

    class RoleModel(_WarmthDraftModel):
        async def complete(self, messages, *, temperature=0.8):
            payload = json.loads(await super().complete(messages, temperature=temperature))
            payload["keep_impression"] = True
            payload["stuck_with_me"] = kept_text
            return json.dumps(payload, ensure_ascii=False)

    model = RoleModel(reading="这件没说完的事仍然让我在意")
    path = tmp_path / "proactive-residue.sqlite"

    def build():
        return build_sqlite_world_v2_test_application(
            path=path,
            config=_application_config(
                world_id=WORLD,
                companion_actor_ref="actor:companion",
                reply_target="user:primary",
                action_pump_owner="worker:actions",
                private_impression_daily_model_call_limit=0,
            ),
            identities=_Identities(),
            router=_Router(),
            character_interior=_fixture_character_interior(
                inbound_author=_InvalidMain(), proactive_provider=model,
            ),
            transport=_NoDispatchTransport(),
            now=PROACTIVE_NOW,
        )

    build().close()
    seed = SQLiteWorldLedger(path=path, world_id=WORLD)
    try:
        _seed_due_thread(seed)
    finally:
        seed.close()
    app = build()
    try:
        for _ in range(12):
            await app.drain_background_once()
            if app.export_replay_evidence().projection.private_impressions:
                break
        evidence = app.export_replay_evidence()
    finally:
        app.close()

    assert [item.reflection_summary for item in evidence.projection.private_impressions] == [
        kept_text
    ]


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
async def test_paid_retention_does_not_reopen_the_same_appraisal_for_reflection() -> None:
    ledger = _ledger_with_active_appraisal()
    runtime, _interior = _private_runtime(ledger, _Model([]))
    source_event = ledger.lookup_event_commit("message-event:1")[0]
    assert await runtime.record_paid_inbound(
        keep_impression=True,
        reflection_summary="他说完我就一直在想他到底怎么看我",
        model_result_ref="model-result:paid-inbound:test",
        source_event=source_event,
    ) is not None

    opener = PrivateImpressionTriggerOpener(ledger=ledger, owner_id="worker:test")
    assert await opener.open_once() is None


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
