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
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
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
async def test_inbound_keeps_only_the_authored_residue_not_the_momentary_state(
    tmp_path,
) -> None:
    stuck_with_me = "他说只是忙，不等于我对他不重要"
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
        [stuck_with_me]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fresh_appraisal", [False, True])
async def test_paid_retention_is_bound_to_this_authored_appraisal(
    tmp_path, fresh_appraisal: bool,
) -> None:
    class RoleModel:
        model = "test-paid-appraisal-binding"
        retain = False

        async def complete(self, messages, *, temperature=0.8):
            if not self.retain or fresh_appraisal:
                return json.dumps({
                    "messages": ["嗯，我听到了。"],
                    "meaning_of_this": "他在解释今天为什么忙" if self.retain else "他想讲昨天的事",
                    "my_state": "我想听他说完",
                    "keep_impression": self.retain,
                    "stuck_with_me": "他今天认真解释过，我想记住" if self.retain else None,
                }, ensure_ascii=False)
            return json.dumps({
                "appraisal_draft": {
                    "appraise": False, "brief_rationale": "今天我没有新的读法",
                    "behavior_tendency": "继续听", "stance": "平静",
                    "display_strategy": "直接表达", "confidence": 5000,
                },
                "expression_draft": {
                    "timing_choice": "now",
                    "beats": [{"modality": "text", "text": "嗯，我听到了。"}],
                    "stance": "平静", "brief_rationale": "我想听他说完", "confidence": 5000,
                    "private_turn_state": {
                        "inner_state_summary": "我想听他说完", "attended_source_refs": [],
                        "keep_impression": True, "stuck_with_me": "他今天认真解释过，我想记住",
                    },
                    "world_claims": [],
                },
            }, ensure_ascii=False)

    model = RoleModel()
    app = build_sqlite_world_v2_test_application(
        path=tmp_path / "paid-appraisal-binding.sqlite", config=_config(),
        identities=_Identities(), router=_Router(),
        character_interior=compose_fixture_character_interior(
            inbound_author=_InboundCharacterAuthor(flash_model=model),
        ),
        transport=_DeliveredTransport(), now=NOW,
    )
    try:
        for index in range(2):
            model.retain = index == 1
            outcome = await app.respond(InboundTurn(
                platform="test", platform_user_id="user.1",
                platform_message_id=f"message:paid-binding:{index}",
                text="我想讲讲这两天的事。", observed_at=NOW,
                trace_id=f"trace:paid-binding:{index}",
            ))
            assert outcome.status == (
                "action_authorized" if index == 0 or fresh_appraisal else "deferred"
            )
        evidence = app.export_replay_evidence()
    finally:
        app.close()
    projection = evidence.projection
    if fresh_appraisal:
        assert len(projection.appraisals) == 2
        assert len(projection.private_impressions) == 1
        current = projection.appraisals[-1]
        impression = projection.private_impressions[0]
        assert impression.subject_ref == current.subject_ref
        assert impression.interpretation_refs == tuple(
            f"appraisal:{current.appraisal_id}:{item.hypothesis_id}" for item in current.hypotheses
        )
        assert impression.source_refs == (current.origin.accepted_event_ref,)
    else:
        assert len(projection.appraisals) == 1
        assert projection.private_impressions == ()
        assert "retained_appraisal_required" in json.dumps(
            [item.audit_json for item in projection.model_result_audits], ensure_ascii=False,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("wire_shape, missing_material", [
    ("slim", "text"), ("full", "text"), ("full", "appraisal"),
])
@pytest.mark.parametrize("correction_choice", ["keep", "discard", "invalid"])
async def test_new_inbound_retention_without_text_gets_one_precise_reselection(
    tmp_path, wire_shape, missing_material, correction_choice,
) -> None:
    requests: list[list[dict[str, str]]] = []

    class RoleModel:
        model = "test-retention-reselection"

        async def complete(self, messages, *, temperature=0.8):
            requests.append(messages)
            state = {
                "inner_state_summary": "我此刻有些疲倦",
                "attended_source_refs": [],
                "keep_impression": True,
            }
            if missing_material == "appraisal":
                state["stuck_with_me"] = "我仍想记着他这次认真解释过"
            if len(requests) > 1 and correction_choice == "keep":
                state["stuck_with_me"] = "我仍想记着他这次认真解释过"
            elif len(requests) > 1 and correction_choice == "discard":
                state["keep_impression"] = False
            if wire_shape == "slim":
                return json.dumps(
                    {
                        "messages": ["嗯，我听到了。"],
                        "meaning_of_this": "他在解释忙碌",
                        "my_state": state["inner_state_summary"],
                        "keep_impression": state["keep_impression"],
                        "stuck_with_me": state.get("stuck_with_me"),
                    }, ensure_ascii=False,
                )
            return json.dumps(
                {
                    "appraisal_draft": {
                        "appraise": len(requests) > 1 and correction_choice == "keep",
                        "affect": "no_change",
                        "meanings": [{"meaning": "他在解释忙碌", "confidence": 5000}],
                        "attribution": "user",
                        "severity": 2000,
                        "brief_rationale": "我想先听他解释。",
                        "behavior_tendency": "继续听",
                        "stance": "平静",
                        "display_strategy": "直接表达",
                        "confidence": 5000,
                    },
                    "expression_draft": {
                        "timing_choice": "now",
                        "beats": [{"modality": "text", "text": "嗯，我听到了。"}],
                        "stance": "平静",
                        "brief_rationale": "我想先听他解释。",
                        "confidence": 5000,
                        "private_turn_state": state,
                        "world_claims": [],
                    },
                }, ensure_ascii=False,
            )

    app = build_sqlite_world_v2_test_application(
        path=tmp_path / "retention-reselection.sqlite",
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
                platform="test", platform_user_id="user.1",
                platform_message_id=f"message:retention:{wire_shape}",
                text="这两天有点忙。", observed_at=NOW,
                trace_id="trace:retention-reselection",
            )
        )
        evidence = app.export_replay_evidence()
    finally:
        app.close()
    if correction_choice == "invalid":
        assert outcome.status == "deferred"
        assert evidence.projection.actions == ()
        assert f"retained_{missing_material}_required" in json.dumps(
            [item.audit_json for item in evidence.projection.model_result_audits],
            ensure_ascii=False,
        )
    else:
        assert outcome.status == "action_authorized"
    assert len(requests) == 2
    assert f"retained_{missing_material}_required" in json.dumps(requests[1], ensure_ascii=False)
    if correction_choice == "keep":
        assert len(evidence.projection.private_impressions) == 1


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
@pytest.mark.parametrize("needs_correction", [None, "text", "appraisal"])
async def test_proactive_paid_residue_reaches_the_next_projection(
    tmp_path, needs_correction: str | None,
) -> None:
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
            if needs_correction != "text" or len(self.messages) > 1:
                payload["stuck_with_me"] = kept_text
            if needs_correction == "appraisal" and len(self.messages) == 1:
                payload["appraisal_draft"]["appraise"] = False
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
    assert len(model.messages) == (2 if needs_correction else 1)
    if needs_correction:
        assert f"retained_{needs_correction}_required" in json.dumps(model.messages[1], ensure_ascii=False)


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
async def test_paid_retention_rejects_an_unbound_model_result() -> None:
    ledger = _ledger_with_active_appraisal()
    model = _Model([])
    runtime, _interior = _private_runtime(ledger, model)
    located = ledger.lookup_event_commit("message-event:1")
    assert located is not None
    source_event = located[0]

    with pytest.raises(ValueError, match="paid_retention_appraisal_authority_missing"):
        await runtime.record_paid_inbound(
            keep_impression=True,
            reflection_summary="他说完我就一直在想他到底怎么看我",
            model_result_ref="model-result:paid-inbound:test",
            source_event=source_event,
        )
    assert ledger.project().private_impressions == ()
    assert "paid_retention_appraisal_authority_missing" in json.dumps(
        [item.audit_json for item in ledger.project().model_result_audits], ensure_ascii=False,
    )
    assert model.calls == []


async def _ledger_after_authored_paid_retention(tmp_path):
    class RoleModel:
        model = "test-authored-paid-retention"

        async def complete(self, messages, *, temperature=0.8):
            return json.dumps({
                "messages": ["嗯，我听到了。"], "meaning_of_this": "他在解释这两天的忙碌",
                "my_state": "我想听他解释", "keep_impression": True,
                "stuck_with_me": "他说完我就一直在想他到底怎么看我",
            }, ensure_ascii=False)

    path = tmp_path / "authored-paid-retention.sqlite"
    app = build_sqlite_world_v2_test_application(
        path=path, config=_config(), identities=_Identities(), router=_Router(),
        character_interior=compose_fixture_character_interior(
            inbound_author=_InboundCharacterAuthor(flash_model=RoleModel()),
        ), transport=_DeliveredTransport(), now=NOW,
    )
    try:
        result = await app.respond(InboundTurn(
            platform="test", platform_user_id="user.1", platform_message_id="paid-retention",
            text="这两天很忙。", observed_at=NOW, trace_id="trace:paid-retention",
        ))
        assert result.status == "action_authorized"
        world_id = app.export_replay_evidence().projection.world_id
    finally:
        app.close()
    return SQLiteWorldLedger(path=path, world_id=world_id)


@pytest.mark.asyncio
async def test_paid_retention_does_not_reopen_the_same_appraisal_for_reflection(tmp_path) -> None:
    ledger = await _ledger_after_authored_paid_retention(tmp_path)
    try:
        opener = PrivateImpressionTriggerOpener(ledger=ledger, owner_id="worker:test")
        assert await opener.open_once() is None
    finally:
        ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("expire_appraisal", [False, True])
async def test_exact_paid_retention_recovery_does_not_write_the_decision_again(
    tmp_path, expire_appraisal: bool,
) -> None:
    ledger = await _ledger_after_authored_paid_retention(tmp_path)
    try:
        if expire_appraisal:
            from companion_daemon.world_v2 import WorldRuntime
            from companion_daemon.world_v2.schemas import ClockObservation

            current = ledger.project()
            when = current.appraisals[0].expires_at
            await WorldRuntime(world_id=ledger.world_id, ledger=ledger).advance(ClockObservation(
                schema_version="world-v2.1", tick_id="retention:expiry", world_id=ledger.world_id,
                logical_time=when, created_at=when, trace_id="retention:expiry",
                causation_id="retention:expiry", correlation_id="retention:expiry",
                logical_time_from=current.logical_time, logical_time_to=when,
                reason="advance to the accepted appraisal expiry",
            ))
            assert ledger.project().appraisals[0].status == "expired"
        before = ledger.project()
        paid = before.proposal_audits[0]
        impression = before.private_impressions[0]
        runtime, _interior = _private_runtime(ledger, _Model([]))
        recovered = await runtime.record_paid_inbound(
            keep_impression=True, reflection_summary=impression.reflection_summary,
            model_result_ref=paid.model_result_ref,
            source_event=ledger.lookup_event_commit(paid.trigger_ref)[0],
        )
        assert recovered == impression.origin.accepted_event_ref
        assert ledger.project() == before
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_paid_retention_resumes_its_persisted_proposal_after_storage_failure(
    tmp_path, monkeypatch,
) -> None:
    original_commit = SQLiteWorldLedger.commit_at_cursor
    failed = False

    def fail_acceptance_once(ledger, events, **kwargs):
        nonlocal failed
        if not failed and any(item.event_type == "PrivateImpressionAccepted" for item in events):
            failed = True
            raise OSError("storage failed before accepting the retained impression")
        return original_commit(ledger, events, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(SQLiteWorldLedger, "commit_at_cursor", fail_acceptance_once)
        ledger = await _ledger_after_authored_paid_retention(tmp_path)
    try:
        before = ledger.project()
        assert failed
        assert before.private_impressions == ()
        assert len(before.private_impression_proposals) == 1
        proposed = before.private_impression_proposals[0]
        paid = before.proposal_audits[0]
        runtime, _interior = _private_runtime(ledger, _Model([]))
        accepted = await runtime.record_paid_inbound(
            keep_impression=True, reflection_summary="他说完我就一直在想他到底怎么看我",
            model_result_ref=paid.model_result_ref,
            source_event=ledger.lookup_event_commit(paid.trigger_ref)[0],
        )
        after = ledger.project()
        assert accepted == after.private_impressions[0].origin.accepted_event_ref
        assert ledger.lookup_event_commit(accepted)[0].payload()["proposal_id"] == proposed.proposal_id
        assert after.private_impression_proposals == ()
        assert after.model_result_audits == before.model_result_audits
        assert ledger.export_replay_evidence().replay == after
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_kept_stuck_with_me_reaches_the_next_inner_life_snapshot(tmp_path) -> None:
    ledger = await _ledger_after_authored_paid_retention(tmp_path)
    kept_text = "他说完我就一直在想他到底怎么看我"
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
            trigger_ref=projection.proposal_audits[0].trigger_ref,
        )
    )
    snapshot = compile_inner_life_snapshot(json.loads(capsule.model_content_json)).model_view()
    materials = snapshot["materials"]["private_impressions"]
    assert any(item.get("reflection_summary") == kept_text for item in materials)
    ledger.close()


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
