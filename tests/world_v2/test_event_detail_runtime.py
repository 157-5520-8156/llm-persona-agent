"""Prepared prose crosses the same public author/review path, then clock-gated publication."""
import json
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.event_detail_runtime import EventDetailRuntime
from companion_daemon.world_v2.event_detail_contract import EventDetailCapability, validate_event_details
from companion_daemon.world_v2.life_content import LifeContentCompiler
from companion_daemon.world_v2.occurrence_content_coordinator import OccurrenceContentCoordinator
from companion_daemon.world_v2.world_outlook_execution import WorldOutlookExecutor
from companion_daemon.world_v2.world_outlook_runtime import WorldOutlookRuntime
from companion_daemon.world_v2.world_outlook_store import SQLiteWorldOutlookStore
from test_day_open_world_consequence_material import (
    _day_activity, _author_runtime, _wake, build_app as _build_app_fixture, ACTOR, WORLD,
    SQLiteWorldLedger, SQLiteImmutableLifeContentStore,
)
from test_world_consequence_agency_qualification import _AttemptAuthor
from test_life_development_runtime import _seed_clock
from test_life_development_runtime import _novel_origin_review
from test_life_development_runtime import _SequenceModel
from test_world_consequence_aftermath import _aftermath, _NoCharacterCalls
from test_world_consequence_producer import _advance as advance_aftermath

build_app = _build_app_fixture

FUTURE = "FUTURE_DETAIL_CANARY：走廊另一端响起短暂的脚步声。"


def test_new_course_detail_capability_requires_a_full_timed_program():
    from companion_daemon.world_v2.life_development_output_schema import life_possibility_output_schema
    from jsonschema import Draft202012Validator
    from test_world_consequence_producer import _draft
    now = datetime.fromisoformat("2026-10-19T08:15:00+08:00")
    cap = EventDetailCapability(source_kind="world_occurrence", source_event_ref="event:activate:class",
        source_payload_hash="a" * 64, source_world_revision=1, entity_ref="occurrence:class", entity_revision=2,
        starts_at=now, ends_at=now + timedelta(minutes=100), privacy_class="shareable",
        required_program=True, closing_offset_seconds=99 * 60)
    value = _draft(SimpleNamespace(event_id="event:clock", world_id="world:test", logical_time=now))
    value["event_details"] = []
    from companion_daemon.world_v2.life_development_draft import LifeDevelopmentPossibilityDraft
    draft = LifeDevelopmentPossibilityDraft.model_validate_json(json.dumps(value))
    with pytest.raises(ValueError, match="detail"):
        validate_event_details(draft, cap, logical_time=now)
    schema = life_possibility_output_schema(outcome_contract="world-consequence.2", event_details=True,
                                           required_event_details=True, closing_offset_seconds=99 * 60)
    assert "event_details" in schema["required"]
    assert schema["properties"]["event_details"]["minItems"] >= 2
    assert not Draft202012Validator(schema).is_valid(value)
    claim = value["claim_declarations"][0]["claim_id"]
    value["event_details"] = [{"after_seconds": 0, "text": "教室开始上课。", "claim_refs": [claim]},
                              {"after_seconds": 99 * 60, "text": "课程临近结束。", "claim_refs": [claim]}]
    validate_event_details(LifeDevelopmentPossibilityDraft.model_validate_json(json.dumps(value)), cap, logical_time=now)
    assert Draft202012Validator(schema).is_valid(value)
    value["event_details"][1]["after_seconds"] = 60
    with pytest.raises(ValueError, match="final.minute|final-minute"):
        validate_event_details(LifeDevelopmentPossibilityDraft.model_validate_json(json.dumps(value)), cap, logical_time=now)


def test_ordinary_course_profile_keeps_complete_steps_without_unrelated_effect_shapes():
    from companion_daemon.world_v2.life_development_output_schema import life_possibility_output_schema
    from companion_daemon.world_v2.life_development_draft import LifeDevelopmentPossibilityDraft
    from test_world_consequence_producer import _draft
    from jsonschema import Draft202012Validator

    now = datetime.fromisoformat("2026-10-19T08:15:00+08:00")
    cap = EventDetailCapability(contract="event-detail-capability.2", source_kind="world_occurrence",
        source_event_ref="event:activate:class", source_payload_hash="a" * 64,
        source_world_revision=1, entity_ref="occurrence:class", entity_revision=2,
        starts_at=now, ends_at=now + timedelta(minutes=100), privacy_class="private",
        required_program=True, closing_offset_seconds=99 * 60)
    value = _draft(SimpleNamespace(event_id="event:clock", world_id="world:test", logical_time=now))
    value.update(location_ref=None, location_capability_ref=None, entity_refs=[], privacy_class="private")
    for outcome in value["outcomes"]:
        outcome.update(privacy_class="private", visual_evidence=None, provisional_npcs=[],
                       provisional_places=[], objective_biographical_transition=None,
                       dynamic_life_direction=None)
    claim = value["claim_declarations"][0]["claim_id"]
    value["event_details"] = [
        {"after_seconds": 0, "text": "教室开始开放。", "claim_refs": [claim]},
        {"after_seconds": 99 * 60, "text": "这节课临近结束。", "claim_refs": [claim]},
    ]
    schema = life_possibility_output_schema(outcome_contract="world-consequence.2",
        event_details=True, required_event_details=True, closing_offset_seconds=99 * 60,
        ordinary_course_detail=True, event_detail_privacy_class="private")
    assert Draft202012Validator(schema).is_valid(value)
    validate_event_details(LifeDevelopmentPossibilityDraft.model_validate_json(json.dumps(value)), cap, logical_time=now)
    value["outcomes"][0]["visual_evidence"] = {"claim_refs": [claim], "activity_description": "多余画面"}
    assert not Draft202012Validator(schema).is_valid(value)
    with pytest.raises(ValueError, match="ordinary course"):
        validate_event_details(LifeDevelopmentPossibilityDraft.model_validate_json(json.dumps(value)), cap, logical_time=now)
    with pytest.raises(ValueError, match="role-visible"):
        EventDetailCapability.model_validate_json(json.dumps({**cap.model_dump(mode="json"), "privacy_class": "withhold"}))


def test_settled_course_keeps_accepted_detail_authority_until_original_window_ends():
    now = datetime.fromisoformat("2026-10-19T08:15:00+08:00")
    cap = EventDetailCapability(contract="event-detail-capability.2", source_kind="world_occurrence",
        source_event_ref="event:activate:class", source_payload_hash="a" * 64,
        source_world_revision=1, entity_ref="occurrence:class", entity_revision=2,
        starts_at=now, ends_at=now + timedelta(minutes=100), privacy_class="private",
        required_program=True, closing_offset_seconds=99 * 60)
    runtime = object.__new__(EventDetailRuntime)
    runtime.actor_ref = ACTOR
    occurrence = SimpleNamespace(occurrence_id=cap.entity_ref, participant_refs=(ACTOR,),
        status="settled", entity_revision=3, activated_at=now,
        settled_at=now + timedelta(minutes=30), event_detail_program=SimpleNamespace(capability=cap))
    assert runtime._source_live(cap, SimpleNamespace(world_occurrences=(occurrence,)))
    occurrence.status = "cancelled"
    assert not runtime._source_live(cap, SimpleNamespace(world_occurrences=(occurrence,)))
    occurrence.status = "settled"
    occurrence.event_detail_program = None
    assert not runtime._source_live(cap, SimpleNamespace(world_occurrences=(occurrence,)))


class DetailAuthor(_AttemptAuthor):
    privacy = "private"
    conditional = False
    async def complete(self, messages, **kwargs):
        raw = await super().complete(messages, **kwargs)
        value = json.loads(raw)
        assert json.loads(messages[-1]["content"])["capability_manifest"]["event_detail_capability"]
        value["privacy_class"] = self.privacy
        for outcome in value["outcomes"]:
            outcome["privacy_class"] = self.privacy
        claim = "event-detail-claim"
        value["claim_declarations"].append({**value["claim_declarations"][0], "claim_id": claim,
            "summary": "周围环境声音的变化。"})
        value["event_details"] = [
            {"after_seconds": 0, "text": "窗外有一阵轻微的风声。", "claim_refs": [claim]},
            {"after_seconds": 120, "text": FUTURE, "claim_refs": [claim]},
        ]
        if self.conditional:
            value["timing"]["duration_minutes"] = 5
            value["event_details"][1]["after_seconds"] = 600
            value["event_details"][0]["outcome_index"] = 0
        self.draft = value
        return json.dumps(value, ensure_ascii=False)


def detail_runtime(ledger, content, store):
    return EventDetailRuntime(ledger=ledger, store=store, content_store=content, actor_ref=ACTOR,
        executor=WorldOutlookExecutor(ledger=ledger, occurrence_content=OccurrenceContentCoordinator(ledger=ledger,store=content), actor_ref=ACTOR))


@pytest.mark.parametrize("privacy", ["private", "withhold"])
@pytest.mark.asyncio
async def test_prepared_details_are_hidden_until_due_and_replay_once(tmp_path, monkeypatch, build_app, privacy):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    monkeypatch.setattr(DetailAuthor, "privacy", privacy)
    path = tmp_path / "details.sqlite"
    plan = await _day_activity(path, build_app, monkeypatch, "started")
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    content = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
    store = SQLiteWorldOutlookStore(path=path, world_id=WORLD, actor_ref=ACTOR)
    try:
        wake = _wake(ledger)
        author = DetailAuthor(content, wake, plan.authority_origin.accepted_event_ref)
        executor = WorldOutlookExecutor(ledger=ledger, occurrence_content=OccurrenceContentCoordinator(ledger=ledger,store=content), actor_ref=ACTOR)
        outlook = WorldOutlookRuntime(ledger=ledger, store=store, author=author, actor_ref=ACTOR, executor=executor, content_store=content)
        critic = _SequenceModel(model="fixture:focused", outputs=(_novel_origin_review(decision="supported"),))
        runtime = _author_runtime(ledger, content, author, critic=critic)
        runtime._world_outlook = outlook
        result = await runtime.advance_active_attempt_once(execution_event_ref=plan.authority_origin.accepted_event_ref,
            wake_event_ref=wake.event_id, trace_id="details", correlation_id="details")
        assert result.status == "occurrence_committed", result
        parent = next(o for o in ledger.project().world_occurrences if o.occurrence_id == result.occurrence_id)
        assert parent.event_detail_program.step_count == 2
        assert FUTURE in json.dumps(critic.messages,ensure_ascii=False), "semantic review must inspect private future prose too"
        assert FUTURE not in content.read_exact(content_ref=parent.candidate_outcomes[0].content_ref).text
        details = outlook.event_details
        assert details.advance(wake_event_ref=wake.event_id) == 1
        assert details.advance(wake_event_ref=wake.event_id) == 0
        assert len(author.received) == 1
        assert len(ledger.project().world_occurrences) == 2
        assert ledger.project().world_occurrences[-1].visibility == privacy
        clock = _seed_clock(ledger, event_id="clock:details-before", logical_time_from=wake.logical_time,
                            logical_time=wake.logical_time + timedelta(seconds=60))
        await advance_aftermath(_aftermath(ledger,content,_NoCharacterCalls()), clock)
        assert details.advance(wake_event_ref=clock.event_id) == 0
        before = ledger.project()
        from companion_daemon.world_v2.schemas import ProjectionCursor
        cursor = ProjectionCursor(**{k:getattr(before,k) for k in ("world_revision","deliberation_revision","ledger_sequence")})
        reading = LifeContentCompiler(store=content).compile(cursor=cursor, actor_ref=ACTOR,
            viewer_privacy_ceiling="private", projection=before)
        assert FUTURE not in reading.model_dump_json()
        # Cold reader/worker uses the same paid program and existing phase receipts.
        details = detail_runtime(ledger, content, store)
        assert details.advance(wake_event_ref=clock.event_id) == 0
        later = _seed_clock(ledger,event_id="clock:details-after",logical_time_from=clock.logical_time,
                            logical_time=wake.logical_time+timedelta(seconds=121))
        assert details.advance(wake_event_ref=later.event_id) == 1
        assert details.advance(wake_event_ref=later.event_id) == 0
        assert len(author.received) == 1
        child = ledger.project().world_occurrences[-1]
        assert FUTURE in content.read_exact(content_ref=child.candidate_outcomes[0].content_ref).text
        assert ledger.rebuild().semantic_hash == ledger.project().semantic_hash
    finally:
        store.close()
        content.close()
        ledger.close()


@asynccontextmanager
async def prepared(tmp_path, monkeypatch, build_app, *, conditional=False):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    if conditional:
        import test_day_open_world_consequence_material as fixture
        monkeypatch.setattr(fixture.day_fixture, "INTENT", {**fixture.day_fixture.INTENT, "duration_seconds": 1800})
    path = tmp_path / "prepared.sqlite"
    plan = await _day_activity(path, build_app, monkeypatch, "started")
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    content = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
    store = SQLiteWorldOutlookStore(path=path, world_id=WORLD, actor_ref=ACTOR)
    try:
        wake = _wake(ledger)
        author = DetailAuthor(content, wake, plan.authority_origin.accepted_event_ref)
        author.conditional = conditional
        details = detail_runtime(ledger, content, store)
        outlook = WorldOutlookRuntime(ledger=ledger, store=store, author=author, actor_ref=ACTOR,
            executor=details.executor, content_store=content)
        runtime = _author_runtime(ledger, content, author,
            critic=_SequenceModel(model="fixture:focused", outputs=(_novel_origin_review(decision="supported"),)))
        runtime._world_outlook = outlook
        result = await runtime.advance_active_attempt_once(execution_event_ref=plan.authority_origin.accepted_event_ref,
            wake_event_ref=wake.event_id, trace_id="details", correlation_id="details")
        assert result.status == "occurrence_committed", result
        parent = next(o for o in ledger.project().world_occurrences if o.occurrence_id == result.occurrence_id)
        yield SimpleNamespace(**locals())
    finally:
        store.close()
        content.close()
        ledger.close()


@pytest.mark.asyncio
async def test_conditional_detail_wakes_after_real_parent_settlement(tmp_path, monkeypatch, build_app):
    from companion_daemon.world_v2.event_detail_runtime import detail_operation
    async with prepared(tmp_path, monkeypatch, build_app, conditional=True) as ctx:
        assert ctx.details.advance(wake_event_ref=ctx.wake.event_id) == 0
        op = detail_operation(ctx.parent.occurrence_id)
        clock = _seed_clock(ctx.ledger, event_id="clock:conditional", logical_time_from=ctx.wake.logical_time,
                            logical_time=ctx.wake.logical_time + timedelta(seconds=301))
        await advance_aftermath(_aftermath(ctx.ledger,ctx.content,_NoCharacterCalls()), clock)
        parent = next(o for o in ctx.ledger.project().world_occurrences if o.occurrence_id == ctx.parent.occurrence_id)
        assert parent.status == "settled"
        released = ctx.details.advance(wake_event_ref=clock.event_id)
        phase = ctx.store.operation(op + ":phase:0")
        assert phase["state"] == ("released" if parent.settled_outcome_ref == parent.candidate_outcome_refs[0] else "not_selected")
        assert released == (phase["state"] == "released")
        assert ctx.details.advance(wake_event_ref=clock.event_id) == 0
        assert len(ctx.author.received) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["recover", "expire", "pause"])
async def test_partial_publication_retries_or_withdraws_without_duplicate_or_future_leak(tmp_path, monkeypatch, build_app, finish):
    from companion_daemon.world_v2.event_detail_runtime import detail_operation
    async with prepared(tmp_path, monkeypatch, build_app) as ctx:
        original = ctx.ledger.commit_at_cursor
        def interrupted(events, **kwargs):
            events = tuple(events)
            if any(e.event_type == "WorldOccurrenceActivated" for e in events):
                raise OSError("injected interruption after exact child content commit")
            return original(events, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(ctx.ledger, "commit_at_cursor", interrupted)
            assert ctx.details.advance(wake_event_ref=ctx.wake.event_id) == 0
        child = ctx.ledger.project().world_occurrences[-1]
        assert child.status == "committed"
        op = detail_operation(ctx.parent.occurrence_id)
        assert ctx.store.operation(op)["payload"]["failure_count"] == 1
        if finish == "pause":
            from test_day_open_world_consequence_material import _LifecycleHTTP, _model, _tick, InboundTurn
            provider = _LifecycleHTTP()
            provider.lifecycle_choice = "pause"
            model = _model(provider)
            app = build_app(ctx.path, model, ecology=True)
            try:
                await app.respond(InboundTurn(platform="test", platform_user_id="user.1", platform_message_id="pause-details",
                    text="有空聊聊吗？", observed_at=ctx.wake.logical_time, trace_id="pause-details"))
                await _tick(app,"pause-details",ctx.wake.logical_time + timedelta(seconds=31))
                assert app.export_replay_evidence().projection.plans[0].status == "paused"
            finally:
                await app.aclose()
                await model.aclose()
            clock = ctx.ledger.lookup_event_commit("event:trigger:clock:pause-details")[0]
        else:
            clock = _seed_clock(ctx.ledger, event_id="clock:"+finish, logical_time_from=ctx.wake.logical_time,
                logical_time=(ctx.parent.event_detail_program.capability.ends_at if finish == "expire"
                              else ctx.wake.logical_time + timedelta(seconds=31)))
        cold = detail_runtime(ctx.ledger, ctx.content, ctx.store)
        count = cold.advance(wake_event_ref=clock.event_id)
        child = next(o for o in ctx.ledger.project().world_occurrences if o.occurrence_id == child.occurrence_id)
        assert child.status == ("active" if finish == "recover" else "cancelled")
        assert count == (finish == "recover")
        assert cold.advance(wake_event_ref=clock.event_id) == 0
        assert len(ctx.author.received) == 1
        assert ctx.store.operation(op)["payload"]["failure_count"] == 1
        assert ctx.ledger.rebuild().semantic_hash == ctx.ledger.project().semantic_hash


@pytest.mark.asyncio
async def test_scheduler_work_does_not_query_each_finished_historical_program(tmp_path, monkeypatch, build_app):
    async with prepared(tmp_path, monkeypatch, build_app) as ctx:
        ctx.details.advance(wake_event_ref=ctx.wake.event_id)
        queries = []
        ctx.store._db.set_trace_callback(queries.append)
        counts = []
        for count in (16, 64):
            for index in range(count):
                identity = f"old-program:{index}"
                ctx.store.claim(identity, kind="event_detail", payload={})
                ctx.store.finish(identity, state="complete", payload={})
            # Capture incremental release commits once, as a real worker does.
            ctx.details.advance(wake_event_ref=ctx.wake.event_id)
            queries.clear()
            ctx.details.next_due()
            counts.append(len(queries))
        assert counts[1] <= counts[0] <= 2


@pytest.mark.asyncio
async def test_forged_operation_key_cannot_duplicate_a_reviewed_program(tmp_path, monkeypatch, build_app):
    from companion_daemon.world_v2.event_detail_runtime import detail_operation
    async with prepared(tmp_path, monkeypatch, build_app) as ctx:
        assert ctx.details.advance(wake_event_ref=ctx.wake.event_id) == 1
        original = ctx.store.operation(detail_operation(ctx.parent.occurrence_id))["payload"]
        ctx.store.claim("forged-parent", kind="event_detail", payload={**original, "next_due_at":ctx.wake.logical_time.isoformat()})
        before = ctx.ledger.project().semantic_hash
        assert ctx.details.advance(wake_event_ref=ctx.wake.event_id) == 0
        assert ctx.ledger.project().semantic_hash == before
        assert ctx.store.operation("forged-parent")["payload"]["failure_count"] == 1


@pytest.mark.asyncio
async def test_overdue_backlog_still_has_a_future_scheduler_wake(tmp_path, monkeypatch, build_app):
    async with prepared(tmp_path, monkeypatch, build_app) as ctx:
        ctx.details.advance(wake_event_ref=ctx.wake.event_id)
        ctx.details.advance(wake_event_ref=ctx.wake.event_id)
        for i in range(65):
            ctx.store.claim(f"overdue:{i}", kind="event_detail", payload={"next_due_at":ctx.wake.logical_time.isoformat()})
        assert ctx.details.next_due() == ctx.wake.logical_time + timedelta(seconds=1)


@pytest.mark.asyncio
@pytest.mark.parametrize('interrupt_own_plan', [False, True])
async def test_activated_outlook_scene_uses_existing_author_then_private_timed_release(tmp_path, monkeypatch, build_app, interrupt_own_plan):
    from companion_daemon.world_v2.schemas import ProjectionCursor
    from test_world_consequence_producer import _draft
    from test_world_author_request_audit import _ReceivedAuthor
    path = tmp_path / "scene.sqlite"
    own_plan = await _day_activity(path, build_app, monkeypatch, 'started') if interrupt_own_plan else None
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    content = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
    store = SQLiteWorldOutlookStore(path=path, world_id=WORLD, actor_ref=ACTOR)
    try:
        wake = _wake(ledger) if own_plan is not None else _seed_clock(ledger)
        details = detail_runtime(ledger, content, store)
        state = ledger.project()
        node = {"opportunity_id":"unfolding-scene", "thread_refs":["story"], "external_possibility":"院内有风。",
                "lane":"world", "execution":{"kind":"environment","text":"院内有风。","duration_minutes":10,
                                               "privacy_class":"shareable","prepare_details":True},
                "opens_at":wake.logical_time.isoformat(),"closes_at":(wake.logical_time+timedelta(hours=1)).isoformat(),
                "basis_refs":[wake.event_id]}
        result = details.executor.execute(node=node, wake_event_ref=wake.event_id, origin={"world_id":WORLD,"actor_ref":ACTOR},
            expected_cursor=ProjectionCursor(**{k:getattr(state,k) for k in ("world_revision","deliberation_revision","ledger_sequence")}))
        assert result.status == "occurrence_committed"
        value = _draft(wake)
        value.update(authored_subject_ref=ACTOR, location_ref=None, location_capability_ref=None, privacy_class="private")
        for outcome in value["outcomes"]:
            outcome.update(experienced_by_ref=ACTOR, privacy_class="private", visual_evidence=None)
        value["event_details"]=[{"after_seconds":120,"text":FUTURE,"claim_refs":[value["claim_declarations"][0]["claim_id"]]}]
        author = _ReceivedAuthor(content,[json.dumps(value,ensure_ascii=False)])
        runtime = _author_runtime(ledger,content,author,
            critic=_SequenceModel(model="fixture:focused",outputs=(_novel_origin_review(decision="supported"),)))
        runtime._world_outlook = WorldOutlookRuntime(ledger=ledger, store=store, author=author, actor_ref=ACTOR,
            executor=details.executor, content_store=content)
        source = runtime.pending_world_event_detail_ref()
        assert runtime.pending_world_event_detail_ref(after_world_revision=ledger.project().world_revision) is None
        assert runtime.pending_world_event_detail_ref(after_world_revision=0) == source
        assert source == "event:activate:"+result.occurrence_id
        monkeypatch.setattr(runtime, "_resolve_occasion_draw", lambda **kwargs: pytest.fail("a bound event must not draw a fresh opportunity or disturbance"))
        prepared_result = await runtime.advance_world_event_detail_once(event_ref=source,wake_event_ref=wake.event_id,
            trace_id="scene",correlation_id="scene")
        assert prepared_result.status == "occurrence_committed",prepared_result
        assert runtime.pending_world_event_detail_ref() is None
        assert details.advance(wake_event_ref=wake.event_id) == 0
        if own_plan is not None:
            from test_world_consequence_authoring_context import _operator_transition
            _operator_transition(ledger, own_plan.plan_id, 'ActivityPaused', 'event:independent-pause')
        clock = _seed_clock(ledger,event_id="scene-due",logical_time_from=wake.logical_time,logical_time=wake.logical_time+timedelta(seconds=120))
        assert details.advance(wake_event_ref=clock.event_id) == 1
        assert ledger.project().world_occurrences[-1].visibility == "private", "shareable source must not downgrade private authored details"
    finally:
        store.close()
        content.close()
        ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing", "tampered"])
async def test_original_program_body_failure_is_retained_and_recovers(tmp_path, monkeypatch, build_app, fault):
    from companion_daemon.world_v2.event_detail_runtime import detail_operation
    from dataclasses import replace
    async with prepared(tmp_path, monkeypatch, build_app) as ctx:
        original = ctx.content.read_exact
        def broken(**kwargs):
            value = original(**kwargs)
            if value and value.content_kind == "raw_model_result" and FUTURE in value.text:
                return None if fault == "missing" else replace(value, text=value.text.replace(FUTURE, "altered future"))
            return value
        with monkeypatch.context() as patch:
            patch.setattr(ctx.content, "read_exact", broken)
            assert ctx.details.advance(wake_event_ref=ctx.wake.event_id) == 0
        op = detail_operation(ctx.parent.occurrence_id)
        assert ctx.store.operation(op)["payload"]["failure_count"] == 1
        assert len(ctx.ledger.project().world_occurrences) == 1
        clock = _seed_clock(ctx.ledger,event_id="body-recovered",logical_time_from=ctx.wake.logical_time,
                            logical_time=ctx.wake.logical_time+timedelta(seconds=31))
        assert ctx.details.advance(wake_event_ref=clock.event_id) == 1
        assert ctx.store.operation(op)["payload"]["failure_count"] == 1
        assert len(ctx.author.received) == 1


@pytest.mark.asyncio
async def test_explicit_source_token_change_invalidates_detail_even_while_activity_stays_active(tmp_path, monkeypatch, build_app):
    from companion_daemon.world_v2.event_detail_contract import EventDetailSourceToken
    from companion_daemon.world_v2.world_outlook import source_catalog
    async with prepared(tmp_path, monkeypatch, build_app) as ctx:
        state = ctx.ledger.project()
        plan = state.plans[0]
        source = source_catalog(state, actor_ref=ACTOR)[plan.plan_id]
        cap = ctx.parent.event_detail_program.capability.model_copy(update={
            "source_tokens": (EventDetailSourceToken(source_ref=plan.plan_id, token=source.token),),
        })
        assert ctx.details._source_live(cap, state)
        changed = state.model_copy(update={"plans":(plan.model_copy(update={"entity_revision":plan.entity_revision+1}),)})
        assert changed.plans[0].status == "active"
        assert not ctx.details._source_live(cap, changed)


def test_detail_native_schema_and_correction_keep_world_authority_and_original_failed_body():
    from companion_daemon.world_v2.world_consequence_author_tool import EVENT_CONTRACT, world_consequence_author_tool_contract, bind_world_consequence_author_tool
    from companion_daemon.world_v2.life_development_output_schema import life_possibility_output_schema
    from companion_daemon.world_v2.life_development_runtime import _world_consequence_structure_correction_messages
    schema = life_possibility_output_schema(outcome_contract="world-consequence.2",event_details=True)
    assert schema["properties"]["causal_authority"]["const"] == "world_contingency"
    assert schema["properties"]["outcome_resolution_authority"]["const"] == "world_contingency"
    assert schema["$defs"]["LifeDevelopmentTimingDraft"]["properties"]["mode"]["const"] == "now"
    contract = world_consequence_author_tool_contract(provider="anthropic",contract_id=EVENT_CONTRACT)
    messages = bind_world_consequence_author_tool(messages=[{"role":"system","content":"test"},{"role":"user","content":"{}"}],tool_contract=contract)
    fixed = _world_consequence_structure_correction_messages(original_messages=messages,rejected_raw='{"invalid":"exact original"}',correction={"validation_failure":{"code":"test"}})
    assert fixed[:-2] == messages
    call = fixed[-2]["tool_calls"][0]
    assert call["function"] == {"name": "author_world_consequence_v6", "arguments": '{"invalid":"exact original"}'}
    assert fixed[-2]["content"] is None
    assert fixed[-1]["role"] == "tool" and fixed[-1]["tool_call_id"] == call["id"]
    assert json.loads(fixed[-1]["content"])["correction"]["validation_failure"] == {"code": "test"}


@pytest.mark.asyncio
@pytest.mark.parametrize("review_due", [False, True])
async def test_reviewed_private_program_and_its_clock_releases_do_not_buy_another_outline(tmp_path, monkeypatch, build_app, review_due):
    from test_world_outlook import Author, draft_value
    from test_world_outlook_continuity import retime
    from test_life_development_runtime import _manifest, _projection_cursor

    monkeypatch.setenv('COMPANION_DISABLE_DEBUG_USAGE_LEDGER', '1')
    path=tmp_path/'detail-retry.sqlite'
    plan=await _day_activity(path,build_app,monkeypatch,'started')
    ledger=SQLiteWorldLedger(path=path,world_id=WORLD)
    content=SQLiteImmutableLifeContentStore(path=path,world_id=WORLD)
    store=SQLiteWorldOutlookStore(path=path,world_id=WORLD,actor_ref=ACTOR)
    wake=_wake(ledger)
    draft=retime(draft_value(wake),ledger.project(),wake)
    if review_due:
        draft['review_at']=(wake.logical_time+timedelta(seconds=1)).isoformat()
    author=Author([{'decision':'revise','draft':draft},RuntimeError('fixture outage')])
    outlook=WorldOutlookRuntime(ledger=ledger,store=store,author=author,actor_ref=ACTOR,
        executor=WorldOutlookExecutor(ledger=ledger,occurrence_content=OccurrenceContentCoordinator(ledger=ledger,store=content),actor_ref=ACTOR),content_store=content)
    try:
        assert (await outlook.refresh(projection=ledger.project(),context={},manifest=_manifest(wake,pinned_cursor=_projection_cursor(ledger))))['status']=='ready'
        later=_seed_clock(ledger,event_id='clock:review',logical_time=wake.logical_time+timedelta(seconds=2),logical_time_from=wake.logical_time)
        if review_due:
            assert (await outlook.refresh(projection=ledger.project(),context={},manifest=_manifest(later,pinned_cursor=_projection_cursor(ledger))))['status']=='technical_failure'
        expected_calls = 2 if review_due else 1
        prior=store.head()
        key=outlook._refresh_identity(ledger.project(),outlook.view(),(),prior)[2]
        detail_author=DetailAuthor(content,later,plan.authority_origin.accepted_event_ref)
        runtime=_author_runtime(ledger,content,detail_author,
            critic=_SequenceModel(model='fixture:focused',outputs=(_novel_origin_review(decision='supported'),)))
        runtime._world_outlook=outlook
        result=await runtime.advance_active_attempt_once(execution_event_ref=plan.authority_origin.accepted_event_ref,
            wake_event_ref=later.event_id,trace_id='detail-retry',correlation_id='detail-retry')
        assert result.status=='occurrence_committed',result
        program=next(o for o in ledger.project().world_occurrences if o.occurrence_id==result.occurrence_id)
        assert program.event_detail_program is not None
        from companion_daemon.world_v2.event_detail_runtime import is_prepaid_detail_progress
        check = dict(ledger=ledger, content_store=content, projection=ledger.project(), actor_ref=ACTOR)
        assert is_prepaid_detail_progress(occurrence=program, **check)
        assert not is_prepaid_detail_progress(occurrence=program, **{**check, 'actor_ref': 'actor:unrelated'})
        corrupt = program.model_copy(update={'event_detail_program': program.event_detail_program.model_copy(
            update={'program_hash': 'a'*64})})
        with pytest.raises(ValueError):
            is_prepaid_detail_progress(occurrence=corrupt, **check)
        assert outlook._refresh_identity(ledger.project(),outlook.view(),(),prior)[2]==key, 'Private preparation reset the failed outline identity'
        # An expressly authored dependency is still monitored as such.
        from copy import deepcopy
        bound_view = deepcopy(outlook.view())
        bound_view['opportunities'][0]['conditions'] = [{'source_ref': program.occurrence_id, 'ready_statuses': ['active']}]
        assert outlook._refresh_identity(ledger.project(), bound_view, (), prior)[2] != key
        assert outlook.event_details.advance(wake_event_ref=later.event_id)==1
        assert outlook._refresh_identity(ledger.project(),outlook.view(),(),prior)[2]==key, 'An already-paid detail release reset the failed outline identity'
        later=_seed_clock(ledger,event_id='clock:detail-later',logical_time=later.logical_time+timedelta(seconds=121),logical_time_from=later.logical_time)
        assert outlook.event_details.advance(wake_event_ref=later.event_id)==1
        assert outlook._refresh_identity(ledger.project(),outlook.view(),(),prior)[2]==key
        assert len(author.calls)==expected_calls
        assert outlook.view()['needs_revision'] is review_due
        closing = _seed_clock(ledger, event_id='clock:actual-result',
            logical_time=program.time_window.closes_at, logical_time_from=later.logical_time)
        aftermath = _aftermath(ledger, content, _NoCharacterCalls())
        for _ in range(4):
            if next(o for o in ledger.project().world_occurrences if o.occurrence_id==program.occurrence_id).status=='settled':
                break
            assert (await advance_aftermath(aftermath, closing)).status=='settled'
        view = outlook.view()
        assert view['needs_revision']
        assert program.occurrence_id in view['changed_source_refs']
        sources, _, changed_key = outlook._refresh_identity(ledger.project(), view, (), prior)
        assert program.occurrence_id in sources, 'Actual new terminal result was lost outside the live catalog'
        assert changed_key != key
        assert len(author.calls)==expected_calls
    finally:
        outlook.close()
        content.close()
        ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('transition', ['pause', 'resume', 'abandon', 'complete'])
@pytest.mark.parametrize('details_first', [False, True])
@pytest.mark.parametrize('cold', [False, True])
async def test_revoked_execution_cancels_parent_before_complete_outcome(
    tmp_path, monkeypatch, build_app, transition, details_first, cold,
):
    from test_world_consequence_authoring_context import _operator_transition

    async with prepared(tmp_path, monkeypatch, build_app) as ctx:
        assert ctx.details.advance(wake_event_ref=ctx.wake.event_id) == 1
        published = ctx.ledger.project().world_occurrences[-1].occurrence_id
        if transition in {'pause', 'resume'}:
            _operator_transition(ctx.ledger, ctx.plan.plan_id, 'ActivityPaused', 'event:interrupt')
            if transition == 'resume':
                _operator_transition(ctx.ledger, ctx.plan.plan_id, 'ActivityResumed', 'event:new-attempt')
        else:
            _operator_transition(ctx.ledger, ctx.plan.plan_id,
                'ActivityAbandoned' if transition == 'abandon' else 'ActivityCompleted', 'event:interrupt')
        current_plan = ctx.ledger.project().plans[0]
        clock = _seed_clock(ctx.ledger, event_id='clock:interrupted-program-end',
            logical_time_from=ctx.wake.logical_time,
            logical_time=ctx.parent.time_window.closes_at + timedelta(seconds=1))
        ledger, content, store = ctx.ledger, ctx.content, ctx.store
        if cold:
            ledger = SQLiteWorldLedger(path=ctx.path, world_id=WORLD)
            content = SQLiteImmutableLifeContentStore(path=ctx.path, world_id=WORLD)
            store = SQLiteWorldOutlookStore(path=ctx.path, world_id=WORLD, actor_ref=ACTOR)
        try:
            details = detail_runtime(ledger, content, store)
            if details_first:
                assert details.advance(wake_event_ref=clock.event_id) == 0
            aftermath = _aftermath(ledger, content, _NoCharacterCalls())
            for _ in range(len(ledger.project().world_occurrences) + 1):
                await advance_aftermath(aftermath, clock)
            if not details_first:
                assert details.advance(wake_event_ref=clock.event_id) == 0
            state = ledger.project()
            parent = next(o for o in state.world_occurrences if o.occurrence_id == ctx.parent.occurrence_id)
            assert parent.status == 'cancelled', 'Revoked attempt still published a complete outcome'
            assert parent.terminal_reason_ref == current_plan.authority_origin.accepted_event_ref
            assert parent.settlement_event_ref is None
            # An external fact already released before the choice remains true.
            assert next(o for o in state.world_occurrences if o.occurrence_id == published).status == 'settled'
            assert len(ctx.author.received) == 1
            before = ledger.export_replay_evidence()
            assert details.advance(wake_event_ref=clock.event_id) == 0
            await advance_aftermath(aftermath, clock)
            assert ledger.export_replay_evidence() == before
            assert ledger.rebuild() == state
        finally:
            if cold:
                store.close()
                content.close()
                ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['unqualified', 'live', 'wrong_source', 'wrong_reason', 'missing_evidence', 'wrong_policy', 'backdated'])
async def test_active_programme_cancellation_requires_exact_revoked_source(
    tmp_path, monkeypatch, build_app, fault,
):
    from test_world_consequence_authoring_context import _operator_transition
    from companion_daemon.world_v2.life_events import WorldOccurrenceTerminalPayload
    from companion_daemon.world_v2.event_identity import domain_idempotency_key
    from companion_daemon.world_v2.schemas import EvidenceRef, ProjectionCursor, WorldEvent

    async with prepared(tmp_path, monkeypatch, build_app) as ctx:
        if fault != 'live':
            _operator_transition(ctx.ledger, ctx.plan.plan_id, 'ActivityPaused', 'event:cancel-proof')
        state = ctx.ledger.project()
        reason = state.plans[0].authority_origin.accepted_event_ref
        refs = [r for r in state.committed_world_event_refs if r.event_id in {reason, ctx.wake.event_id}]
        if fault == 'missing_evidence':
            refs = [r for r in refs if r.event_id != reason]
        body = WorldOccurrenceTerminalPayload(
            change_id='cancel:proof', transition_id='cancel:proof',
            expected_entity_revision=ctx.parent.entity_revision, occurrence_id=ctx.parent.occurrence_id,
            effective_at=state.logical_time - (timedelta(seconds=1) if fault == 'backdated' else timedelta()),
            reason_ref=ctx.wake.event_id if fault == 'wrong_reason' else reason,
            interrupted_execution_source_ref=(None if fault == 'unqualified' else ctx.wake.event_id
                if fault == 'wrong_source' else ctx.parent.event_detail_program.capability.source_event_ref),
            evidence_refs=tuple(EvidenceRef(ref_id=r.event_id, evidence_type='committed_world_event',
                claim_purpose='life_transition', source_world_revision=r.world_revision,
                immutable_hash=r.payload_hash) for r in refs),
            policy_refs=('policy:other',) if fault == 'wrong_policy' else ('policy:event-detail-interruption.1',),
        ).model_dump(mode='json')
        event = WorldEvent.from_payload(
            schema_version='world-v2.1', event_id='event:bad-cancel', world_id=WORLD,
            event_type='WorldOccurrenceCancelled', logical_time=state.logical_time, created_at=state.logical_time,
            actor='worker:event-detail', source='event-detail-interruption.1', trace_id='trace:bad-cancel',
            causation_id=ctx.wake.event_id, correlation_id='bad-cancel',
            idempotency_key=domain_idempotency_key(event_type='WorldOccurrenceCancelled', world_id=WORLD, payload=body),
            payload=body,
        )
        before = ctx.ledger.export_replay_evidence()
        with pytest.raises(ValueError):
            ctx.ledger.commit_at_cursor((event,), expected_cursor=ProjectionCursor(**{
                k: getattr(state, k) for k in ('world_revision', 'deliberation_revision', 'ledger_sequence')}))
        assert ctx.ledger.export_replay_evidence() == before
        assert ctx.ledger.rebuild() == state


@pytest.mark.asyncio
async def test_new_resumed_attempt_survives_old_programme_retirement(tmp_path, monkeypatch, build_app):
    from test_world_consequence_authoring_context import _operator_transition

    async with prepared(tmp_path, monkeypatch, build_app) as ctx:
        _operator_transition(ctx.ledger, ctx.plan.plan_id, 'ActivityPaused', 'event:pause-old')
        source = _operator_transition(ctx.ledger, ctx.plan.plan_id, 'ActivityResumed', 'event:resume-new')
        clock = _wake(ctx.ledger, 'resume-program')
        author = DetailAuthor(ctx.content, clock, source)
        runtime = _author_runtime(ctx.ledger, ctx.content, author,
            critic=_SequenceModel(model='fixture:focused', outputs=(_novel_origin_review(decision='supported'),)))
        runtime._world_outlook = ctx.outlook
        made = await runtime.advance_active_attempt_once(execution_event_ref=source,
            wake_event_ref=clock.event_id, trace_id='resume-program', correlation_id='resume-program')
        assert made.status == 'occurrence_committed', made
        fresh = next(o for o in ctx.ledger.project().world_occurrences if o.occurrence_id == made.occurrence_id)
        assert fresh.event_detail_program.capability.source_event_ref == source
        assert ctx.details.advance(wake_event_ref=clock.event_id) == 1
        later = _seed_clock(ctx.ledger, event_id='clock:resumed-end', logical_time_from=clock.logical_time,
            logical_time=max(ctx.parent.time_window.closes_at, fresh.time_window.closes_at) + timedelta(seconds=1))
        aftermath = _aftermath(ctx.ledger, ctx.content, _NoCharacterCalls())
        for _ in range(len(ctx.ledger.project().world_occurrences) + 1):
            await advance_aftermath(aftermath, later)
        ctx.details.advance(wake_event_ref=later.event_id)
        states = {o.occurrence_id: o for o in ctx.ledger.project().world_occurrences}
        assert states[ctx.parent.occurrence_id].status == 'cancelled'
        assert states[fresh.occurrence_id].status == 'settled'
        assert states[fresh.occurrence_id].settlement_event_ref is not None
        assert len(ctx.author.received) == len(author.received) == 1
        assert ctx.ledger.rebuild() == ctx.ledger.project()


@pytest.mark.asyncio
async def test_interruption_during_draw_keeps_audit_but_not_complete_result(tmp_path, monkeypatch, build_app):
    from test_world_consequence_authoring_context import _operator_transition

    async with prepared(tmp_path, monkeypatch, build_app) as ctx:
        clock = _seed_clock(ctx.ledger, event_id='clock:draw-race', logical_time_from=ctx.wake.logical_time,
            logical_time=ctx.parent.time_window.closes_at + timedelta(seconds=1))
        aftermath = _aftermath(ctx.ledger, ctx.content, _NoCharacterCalls())
        original = aftermath._random.draw
        original_draws = sum(e.event.event_type == 'RandomDrawRecorded'
                             for e in ctx.ledger.export_replay_evidence().events)
        def interrupt_after_draw(**kwargs):
            result = original(**kwargs)
            _operator_transition(ctx.ledger, ctx.plan.plan_id, 'ActivityPaused', 'event:during-draw')
            return result
        with monkeypatch.context() as patch:
            patch.setattr(aftermath._random, 'draw', interrupt_after_draw)
            result = await advance_aftermath(aftermath, clock)
        assert result.status == 'cancelled'
        state = ctx.ledger.project()
        parent = next(o for o in state.world_occurrences if o.occurrence_id == ctx.parent.occurrence_id)
        assert parent.status == 'cancelled' and parent.settlement_event_ref is None
        # Selection and its attempted proposal remain durable; they are not a
        # completed attempt and must neither be erased nor re-drawn.
        assert any(p.occurrence_id == parent.occurrence_id for p in state.outcome_proposals)
        before = ctx.ledger.export_replay_evidence()
        assert sum(e.event.event_type == 'RandomDrawRecorded' for e in before.events) == original_draws + 1
        await advance_aftermath(aftermath, clock)
        assert ctx.ledger.export_replay_evidence() == before
        assert ctx.ledger.rebuild() == state


@pytest.mark.asyncio
async def test_fresh_settlement_guard_rejects_pause_after_proposal(tmp_path, monkeypatch, build_app):
    from test_world_consequence_authoring_context import _operator_transition
    from companion_daemon.world_v2.life_events import WorldOccurrenceSettledPayload
    from companion_daemon.world_v2.schemas import ProjectionCursor

    async with prepared(tmp_path, monkeypatch, build_app) as ctx:
        clock = _seed_clock(ctx.ledger, event_id='clock:settle-race', logical_time_from=ctx.wake.logical_time,
            logical_time=ctx.parent.time_window.closes_at + timedelta(seconds=1))
        aftermath = _aftermath(ctx.ledger, ctx.content, _NoCharacterCalls())
        original = ctx.ledger.commit_at_cursor
        captured = []
        def stop_before_commit(events, **kwargs):
            events = tuple(events)
            if any(e.event_type == 'WorldOccurrenceSettled' for e in events):
                captured.extend(events)
                raise OSError('fixture stop before settlement commit')
            return original(events, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(ctx.ledger, 'commit_at_cursor', stop_before_commit)
            with pytest.raises(OSError, match='fixture stop'):
                await advance_aftermath(aftermath, clock)
        assert captured
        settled = next(e for e in captured if e.event_type == 'WorldOccurrenceSettled')
        body = settled.payload()
        assert body['execution_source_ref'] == ctx.parent.event_detail_program.capability.source_event_ref
        without_guard = {k: v for k, v in body.items() if k != 'execution_source_ref'}
        with pytest.raises(ValueError, match='change hash'):
            WorldOccurrenceSettledPayload.model_validate_json(json.dumps(without_guard))
        _operator_transition(ctx.ledger, ctx.plan.plan_id, 'ActivityPaused', 'event:after-proposal')
        before = ctx.ledger.export_replay_evidence()
        state = ctx.ledger.project()
        with pytest.raises(ValueError, match='revision-pinned accepted AcceptanceRecorded'):
            ctx.ledger.commit_at_cursor(tuple(captured), expected_cursor=ProjectionCursor(**{
                k: getattr(state, k) for k in ('world_revision', 'deliberation_revision', 'ledger_sequence')}))
        assert ctx.ledger.export_replay_evidence() == before
        assert (await advance_aftermath(aftermath, clock)).status == 'cancelled'
        assert ctx.ledger.rebuild() == ctx.ledger.project()


@pytest.mark.asyncio
async def test_reducer_source_guard_rejects_unsafe_caller_with_current_proposal(tmp_path, monkeypatch, build_app):
    from test_world_consequence_authoring_context import _operator_transition
    from companion_daemon.world_v2 import event_detail_runtime

    async with prepared(tmp_path, monkeypatch, build_app) as ctx:
        clock = _seed_clock(ctx.ledger, event_id='clock:source-guard', logical_time_from=ctx.wake.logical_time,
            logical_time=ctx.parent.time_window.closes_at + timedelta(seconds=1))
        aftermath = _aftermath(ctx.ledger, ctx.content, _NoCharacterCalls())
        original = aftermath._random.draw
        def pause_after_draw(**kwargs):
            value = original(**kwargs)
            _operator_transition(ctx.ledger, ctx.plan.plan_id, 'ActivityPaused', 'event:source-guard-pause')
            return value
        # Fault-inject an unsafe caller while keeping the durable reducer and
        # all acceptance checks enabled. Its proposal is pinned AFTER pause,
        # so revision CAS alone cannot establish the old attempt's authority.
        with monkeypatch.context() as patch:
            patch.setattr(aftermath._random, 'draw', pause_after_draw)
            patch.setattr(event_detail_runtime, 'cancel_interrupted_execution', lambda **kwargs: False)
            with pytest.raises(ValueError, match='execution source is no longer current'):
                await advance_aftermath(aftermath, clock)
        state = ctx.ledger.project()
        parent = next(o for o in state.world_occurrences if o.occurrence_id == ctx.parent.occurrence_id)
        assert parent.status == 'active' and parent.settlement_event_ref is None
        proposal = next(p for p in state.outcome_proposals if p.occurrence_id == parent.occurrence_id)
        assert proposal.execution_source_ref == parent.event_detail_program.capability.source_event_ref
        assert proposal.evaluated_world_revision == state.world_revision
        assert (await advance_aftermath(aftermath, clock)).status == 'cancelled'
        assert ctx.ledger.rebuild() == ctx.ledger.project()


@pytest.mark.asyncio
@pytest.mark.parametrize('after_commit', [False, True])
async def test_interrupted_programme_retries_cleanup_without_rebuying_or_erasing_failure(
    tmp_path, monkeypatch, build_app, after_commit,
):
    from test_world_consequence_authoring_context import _operator_transition
    from companion_daemon.world_v2.event_detail_runtime import detail_operation

    async with prepared(tmp_path, monkeypatch, build_app) as ctx:
        ctx.details.advance(wake_event_ref=ctx.wake.event_id)
        _operator_transition(ctx.ledger, ctx.plan.plan_id, 'ActivityPaused', 'event:cleanup-pause')
        op = detail_operation(ctx.parent.occurrence_id)
        original_finish = ctx.store.finish
        original_commit = ctx.ledger.commit_at_cursor
        def stop_finish(identity, **kwargs):
            if identity == op:
                raise OSError('fixture crash after cancellation commit')
            return original_finish(identity, **kwargs)
        def stop_commit(events, **kwargs):
            events = tuple(events)
            if any(e.event_type == 'WorldOccurrenceCancelled' for e in events):
                raise OSError('fixture crash before cancellation commit')
            return original_commit(events, **kwargs)
        with monkeypatch.context() as patch:
            if after_commit:
                patch.setattr(ctx.store, 'finish', stop_finish)
            else:
                patch.setattr(ctx.ledger, 'commit_at_cursor', stop_commit)
            assert ctx.details.advance(wake_event_ref=ctx.wake.event_id) == 0
        assert ctx.store.operation(op)['payload']['failure_count'] == 1
        clock = _seed_clock(ctx.ledger, event_id='clock:cleanup-retry', logical_time_from=ctx.wake.logical_time,
            logical_time=ctx.wake.logical_time + timedelta(seconds=31))
        cold = detail_runtime(ctx.ledger, ctx.content, ctx.store)
        assert cold.advance(wake_event_ref=clock.event_id) == 0
        assert ctx.store.operation(op)['state'] == 'source_changed'
        assert ctx.store.operation(op)['payload']['failure_count'] == 1
        events = [e.event for e in ctx.ledger.export_replay_evidence().events]
        assert sum(e.event_type == 'WorldOccurrenceCancelled' and e.payload()['occurrence_id'] == ctx.parent.occurrence_id for e in events) == 1
        assert len(ctx.author.received) == 1
        assert ctx.ledger.rebuild() == ctx.ledger.project()


@pytest.mark.asyncio
async def test_cold_upgrade_retires_parent_of_existing_source_changed_receipt(tmp_path, monkeypatch, build_app):
    from test_world_consequence_authoring_context import _operator_transition
    from companion_daemon.world_v2 import event_detail_runtime
    from companion_daemon.world_v2.event_detail_runtime import detail_operation

    async with prepared(tmp_path, monkeypatch, build_app) as ctx:
        ctx.details.advance(wake_event_ref=ctx.wake.event_id)
        _operator_transition(ctx.ledger, ctx.plan.plan_id, 'ActivityPaused', 'event:legacy-pause')
        clock = _wake(ctx.ledger, 'legacy-cleanup')
        # Reproduce the predecessor's durable receipt: future phases closed,
        # but the active parent was never retired. All validators stay enabled.
        with monkeypatch.context() as patch:
            patch.setattr(event_detail_runtime, 'cancel_interrupted_execution', lambda **kwargs: False)
            ctx.details.advance(wake_event_ref=clock.event_id)
        op = detail_operation(ctx.parent.occurrence_id)
        prior = ctx.store.operation(op)
        assert prior['state'] == 'source_changed'
        assert next(o for o in ctx.ledger.project().world_occurrences if o.occurrence_id == ctx.parent.occurrence_id).status == 'active'
        cold = detail_runtime(ctx.ledger, ctx.content, ctx.store)
        assert cold.advance(wake_event_ref=clock.event_id) == 0
        assert next(o for o in ctx.ledger.project().world_occurrences if o.occurrence_id == ctx.parent.occurrence_id).status == 'cancelled'
        assert ctx.store.operation(op) == prior
        before = ctx.ledger.export_replay_evidence()
        cold.advance(wake_event_ref=clock.event_id)
        assert ctx.ledger.export_replay_evidence() == before
        assert len(ctx.author.received) == 1
        assert ctx.ledger.rebuild() == ctx.ledger.project()


@pytest.mark.asyncio
@pytest.mark.parametrize('cold', [False, True])
async def test_finished_detail_receipt_does_not_keep_revoked_parent_alive(tmp_path, monkeypatch, build_app, cold):
    from test_world_consequence_authoring_context import _operator_transition
    from companion_daemon.world_v2.event_detail_runtime import detail_operation

    async with prepared(tmp_path, monkeypatch, build_app) as ctx:
        assert ctx.details.advance(wake_event_ref=ctx.wake.event_id) == 1
        clock = _seed_clock(ctx.ledger, event_id='clock:all-fields-released', logical_time_from=ctx.wake.logical_time,
            logical_time=ctx.wake.logical_time + timedelta(seconds=121))
        assert ctx.details.advance(wake_event_ref=clock.event_id) == 1
        op = detail_operation(ctx.parent.occurrence_id)
        prior = ctx.store.operation(op)
        assert prior['state'] == 'complete'
        assert clock.logical_time < ctx.parent.time_window.closes_at
        published = [o.occurrence_id for o in ctx.ledger.project().world_occurrences if o.occurrence_id != ctx.parent.occurrence_id]
        _operator_transition(ctx.ledger, ctx.plan.plan_id, 'ActivityPaused', 'event:after-detail-completion')
        later = _wake(ctx.ledger, 'after-detail-completion')
        details = detail_runtime(ctx.ledger, ctx.content, ctx.store) if cold else ctx.details
        assert details.advance(wake_event_ref=later.event_id) == 0
        state = ctx.ledger.project()
        assert next(o for o in state.world_occurrences if o.occurrence_id == ctx.parent.occurrence_id).status == 'cancelled'
        assert ctx.store.operation(op) == prior
        assert all(next(o for o in state.world_occurrences if o.occurrence_id == ref).status == 'active' for ref in published)
        assert ctx.ledger.rebuild() == state
