"""Public-seam continuity and NPC execution, without real model calls."""

from datetime import datetime, timedelta
import json
import pytest

from companion_daemon.world_v2.world_outlook_runtime import WorldOutlookRuntime
from companion_daemon.world_v2.world_outlook_store import SQLiteWorldOutlookStore
from companion_daemon.world_v2.world_outlook_execution import WorldOutlookExecutor
from companion_daemon.world_v2.occurrence_content_coordinator import OccurrenceContentCoordinator
from companion_daemon.world_v2.lived_story import anchor_catalog
from companion_daemon.world_v2.recall_runtime import RecallCoordinator
from companion_daemon.world_v2.recall_index import (
    InMemoryRecallIndex,
    FeatureHashRecallEmbedding,
    RecallCursor,
    RecallQuery,
    RecallSourceBinding,
)
from companion_daemon.world_v2.recall_corpus import (
    RecallCorpusSources,
    RecallCorpusCompiler,
    required_recall_authority_refs,
)
from test_world_outlook import Author, draft_value
from test_life_development_runtime import _seed_clock, _manifest, _projection_cursor, OWNER


def retime(value, state, wake):
    value.update(
        world_id=state.world_id,
        actor_ref=OWNER,
        review_at=(state.logical_time + timedelta(days=1)).isoformat(),
        horizon_end=(state.logical_time + timedelta(days=21)).isoformat(),
    )
    for node in value["opportunities"]:
        node.update(
            opens_at=state.logical_time.isoformat(),
            closes_at=(state.logical_time + timedelta(days=7)).isoformat(),
            basis_refs=[wake.event_id],
        )
    return value


def npc_manifest(ledger, wake):
    from companion_daemon.world_v2.life_development_draft import LifeDevelopmentNpcCapability

    return _manifest(wake, pinned_cursor=_projection_cursor(ledger)).model_copy(
        update={
            "npc_capabilities": (
                LifeDevelopmentNpcCapability(
                    npc_ref="npc:lin",
                    lifecycle_state="active",
                    identity_content_ref="identity:lin",
                    identity_summary="林，已注册的熟人。",
                    identity_payload_hash="a" * 64,
                    authority_refs=(wake.event_id,),
                ),
            )
        }
    )


def npc_setup(tmp_path, change):
    from test_npc_ecology import _runtime, _actor

    ledger, content, npc_model, world_model, _ = _runtime(_actor("no_op"), {"decision": "no_op"})
    previous = ledger.lookup_event_commit("clock-life")[0]
    wake = _seed_clock(
        ledger,
        event_id="clock:npc-pilot",
        logical_time=previous.logical_time + timedelta(hours=1),
        logical_time_from=previous.logical_time,
    )
    draft = retime(draft_value(wake), ledger.project(), wake)
    change(draft)
    author = Author([{"decision": "revise", "draft": draft}])
    store = SQLiteWorldOutlookStore(
        path=tmp_path / "outlook.sqlite", world_id=ledger.world_id, actor_ref=OWNER
    )
    runtime = WorldOutlookRuntime(
        ledger=ledger,
        store=store,
        author=author,
        actor_ref=OWNER,
        npc_refs_reader=lambda s: tuple("npc:" + n.npc_id for n in s.npcs if n.status == "active"),
        executor=WorldOutlookExecutor(
            ledger=ledger,
            occurrence_content=OccurrenceContentCoordinator(ledger=ledger, store=content),
            actor_ref=OWNER,
        ),
    )
    return ledger, wake, author, runtime, npc_model, world_model


def activity(node, *, duration=60):
    node.update(
        lane="npc",
        npc_ref="npc:lin",
        execution={
            "kind": "npc_activity",
            "text": "林整理旧书，结束后把待交换的书放进箱子。",
            "activity_kind": "sort_books",
            "duration_minutes": duration,
            "privacy_class": "private",
        },
    )


@pytest.mark.asyncio
async def test_central_npc_offscreen_activity_completes_without_thought_or_false_protagonist_memory(
    tmp_path,
):
    def change(draft):
        activity(draft["opportunities"][0])
        activity(draft["opportunities"][1], duration=20)
        draft["opportunities"][1]["after_opportunities"] = ["notice"]

    ledger, wake, author, runtime, npc, world = npc_setup(tmp_path, change)
    try:
        assert (
            await runtime.refresh(
                projection=ledger.project(), context={}, manifest=npc_manifest(ledger, wake)
            )
        )["status"] == "ready"
        node = next(n for n in runtime.ready() if n["opportunity_id"] == "notice")
        before_execution = ledger.project()
        result = runtime.execute_offer(node, wake_event_ref=wake.event_id)
        assert result.status == "npc_activity_started"
        plan = next(p for p in ledger.project().plans if p.owner_actor_ref == "npc:lin")
        assert plan.status == "active" and plan.owner_actor_ref == "npc:lin"
        assert OWNER not in plan.participant_refs
        assert ledger.project().world_occurrences == before_execution.world_occurrences
        assert ledger.project().experiences == before_execution.experiences
        assert ledger.project().memory_candidates == before_execution.memory_candidates
        assert not runtime.view()["needs_revision"]
        assert (
            next(n for n in runtime.view()["opportunities"] if n["opportunity_id"] == "calendar")[
                "state"
            ]
            == "waiting"
        )
        due = runtime.next_due()
        assert due == wake.logical_time + timedelta(minutes=60)
        runtime.store.close()
        runtime.store = SQLiteWorldOutlookStore(
            path=tmp_path / "outlook.sqlite", world_id=ledger.world_id, actor_ref=OWNER
        )
        later = _seed_clock(
            ledger, event_id="clock:complete", logical_time=due, logical_time_from=wake.logical_time
        )
        runtime.reconcile_accepted_offers(wake_event_ref=later.event_id)
        runtime.reconcile_accepted_offers(wake_event_ref=later.event_id)
        assert (
            next(p for p in ledger.project().plans if p.owner_actor_ref == "npc:lin").status
            == "completed"
        )
        assert next(n for n in runtime.ready() if n["opportunity_id"] == "calendar")
        assert not runtime.view()["needs_revision"]
        assert len(author.calls) == 1 and npc.calls == [] and world.calls == []
        assert ledger.export_replay_evidence().projection == ledger.export_replay_evidence().replay
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_busy_npc_cannot_be_double_booked_and_unrelated_line_stays_ready(tmp_path):
    def change(draft):
        activity(draft["opportunities"][0])
        activity(draft["opportunities"][1])

    ledger, wake, author, runtime, npc, world = npc_setup(tmp_path, change)
    try:
        await runtime.refresh(
            projection=ledger.project(), context={}, manifest=npc_manifest(ledger, wake)
        )
        first = next(n for n in runtime.ready() if n["opportunity_id"] == "notice")
        assert (
            runtime.execute_offer(first, wake_event_ref=wake.event_id).status
            == "npc_activity_started"
        )
        second = next(n for n in runtime.ready() if n["opportunity_id"] == "calendar")
        assert (
            runtime.execute_offer(second, wake_event_ref=wake.event_id).status
            == "technical_failure"
        )
        assert sum(p.owner_actor_ref == "npc:lin" for p in ledger.project().plans) == 1
        assert next(n for n in runtime.ready() if n["opportunity_id"] == "materials")
        assert npc.calls == [] and world.calls == []
    finally:
        runtime.close()


def sources_for(ledger, content=None):
    from test_prehistory_memory_source import _read
    from companion_daemon.world_v2.world_life_context import (
        WorldLifeContextCompiler,
        WorldLifeContextItem,
    )
    from companion_daemon.world_v2.life_content import LifeContentCompiler

    state = ledger.project()
    memories = _read(ledger)
    life = (
        WorldLifeContextCompiler(life_content=LifeContentCompiler(store=content)).compile(
            projection=state, actor_ref=OWNER, cursor=_projection_cursor(ledger)
        )
        if content
        else ()
    )
    base = RecallCorpusSources(
        active_memory_candidates=memories.items,
        world_life=tuple(x for x in life if isinstance(x, WorldLifeContextItem)),
    )
    refs = required_recall_authority_refs(base)
    return base.model_copy(
        update={
            "authority_bindings": tuple(
                RecallSourceBinding(
                    source_kind="committed_event",
                    authority_type=e.event_type,
                    ref=e.event_id,
                    source_world_revision=e.world_revision,
                    immutable_hash=e.payload_hash,
                )
                for e in state.committed_world_event_refs
                if e.event_id in refs
            )
        }
    )


def compile_docs(ledger, sources):
    return RecallCorpusCompiler().compile(
        cursor=RecallCursor(**_projection_cursor(ledger).model_dump()),
        actor_ref=OWNER,
        subject_refs=(OWNER,),
        sources=sources,
    )


@pytest.mark.asyncio
async def test_history_to_settled_life_link_enters_normal_recall_only_after_real_execution(
    tmp_path,
):
    from test_character_prehistory import started_ledger
    from test_prehistory_memory_source import _choice
    from test_story_memory_structure import story_archive
    from companion_daemon.world_v2.character_prehistory_runtime import PrehistoryArchiveRuntime
    from companion_daemon.world_v2.prehistory_memory_source import prehistory_memory_binding
    from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
    from test_world_consequence_aftermath import _aftermath, _NoCharacterCalls
    from test_world_consequence_producer import _advance

    path = tmp_path / "world.sqlite"
    ledger = started_ledger(path)
    content = SQLiteImmutableLifeContentStore(path=path, world_id=ledger.world_id)
    recall = RecallCoordinator(index=InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding()))
    row = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=OWNER).import_reviewed(
        story_archive(), created_at=ledger.project().logical_time
    )[0]
    binding = prehistory_memory_binding(row)
    pending = _choice(ledger, binding)
    active = _choice(ledger, binding, before=pending, status="active")
    now = ledger.project().logical_time
    wake = _seed_clock(ledger, logical_time=now + timedelta(minutes=1), logical_time_from=now)
    source = sources_for(ledger, content)
    recall.refresh(
        cursor=RecallCursor(**_projection_cursor(ledger).model_dump()),
        actor_ref=OWNER,
        subject_refs=(OWNER,),
        logical_time=wake.logical_time,
        sources=source,
        trigger_ref=wake.event_id,
    )
    anchor = anchor_catalog(ledger.project(), actor_ref=OWNER)[row.record.record_id]
    draft = retime(draft_value(wake), ledger.project(), wake)
    draft["threads"][0]["history_anchors"] = [anchor.model_dump(mode="json")]
    draft["opportunities"] = draft["opportunities"][:1]
    draft["opportunities"][0]["execution"]["text"] = "社区公告栏贴出旧书交换活动报名说明。"
    author = Author([{"decision": "revise", "draft": draft}])
    runtime = WorldOutlookRuntime(
        ledger=ledger,
        store=SQLiteWorldOutlookStore(path=path, world_id=ledger.world_id, actor_ref=OWNER),
        author=author,
        actor_ref=OWNER,
        history_reader=recall.world_author_history,
        executor=WorldOutlookExecutor(
            ledger=ledger,
            occurrence_content=OccurrenceContentCoordinator(ledger=ledger, store=content),
            actor_ref=OWNER,
        ),
    )
    try:
        before = ledger.project()
        result = await runtime.refresh(
            projection=before,
            context={"slices": {"world_life": {"availability": "available", "items": [
                {"privacy_class": row.record.privacy_class,
                 "value": {"content": {"text": row.record.statement}}},
            ]}}},
            manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)),
            history_trigger_ref=wake.event_id,
        )
        assert result["status"] == "ready"
        assert ledger.project() == before
        packet = json.loads(author.calls[0][-1]["content"])
        assert packet["history_material"][0]["anchor"] == anchor.model_dump(mode="json")
        assert not any(
            d.link_refs and any(r.startswith("lived-story-link:") for r in d.link_refs)
            for d in compile_docs(ledger, source)
        )
        executed = runtime.execute_offer(runtime.ready()[0], wake_event_ref=wake.event_id)
        assert executed.status == "occurrence_committed"
        current = compile_docs(ledger, sources_for(ledger, content))
        assert not any(any(r.startswith("lived-story-link:") for r in d.link_refs) for d in current)
        due = _seed_clock(
            ledger,
            event_id="clock:settle",
            logical_time=wake.logical_time + timedelta(minutes=30),
            logical_time_from=wake.logical_time,
        )
        char = _NoCharacterCalls()
        outcome = await _advance(_aftermath(ledger, content, char), due)
        assert outcome.status == "settled"
        docs = compile_docs(ledger, sources_for(ledger, content))
        old = next(d for d in docs if d.prehistory is not None)
        new = next(d for d in docs if d.settled_life is not None)
        shared = set(old.link_refs) & set(new.link_refs)
        assert any(ref.startswith("lived-story-link:") for ref in shared)
        assert "story_navigation" not in new.settled_life_json
        index = InMemoryRecallIndex(embedding=FeatureHashRecallEmbedding())
        index.rebuild(
            cursor=RecallCursor(**_projection_cursor(ledger).model_dump()), documents=docs
        )
        query = RecallQuery(
            query_text=row.record.statement,
            actor_ref=OWNER,
            subject_refs=(OWNER,),
            viewer_privacy_ceiling="private",
            at=ledger.project().logical_time,
            cursor=RecallCursor(**_projection_cursor(ledger).model_dump()),
            accessibility_seed="test:story",
            limit=6,
        )
        assert any(h.document.settled_life is not None for h in index.search(query).hits)
        # The same source graph must survive the ordinary Context resolver,
        # not only a directly constructed test corpus.
        from companion_daemon.world_v2.ledger_context_resolver import (
            context_capsule_compiler_from_ledger,
            ContextRelevanceScope,
        )
        from companion_daemon.world_v2.context_resolver import query_from_projection
        from companion_daemon.world_v2.recall_audit import CharacterRecallRequest
        from companion_daemon.world_v2.recall_runtime import (
            recall_evidence_json,
            verify_trusted_recall_trace,
        )

        trigger = "event:ordinary-story-query"
        capsule = context_capsule_compiler_from_ledger(
            ledger=ledger,
            relevance_scope=ContextRelevanceScope(actor_ref=OWNER),
            life_content_store=content,
            recall_coordinator=recall,
        ).compile(query_from_projection(ledger.project(), actor_ref=OWNER, trigger_ref=trigger))
        assert "story_navigation" not in capsule.model_content_json
        trace = recall.recall(
            request=CharacterRecallRequest(query_text=row.record.statement, limit=6),
            accessibility_seed="ordinary:story-query",
            expected_cursor=query.cursor,
            trigger_ref=trigger,
        )
        packet = recall_evidence_json(verify_trusted_recall_trace(trace))
        assert executed.occurrence_id in packet
        assert any(
            h.document.source_item_ref.split(":window:")[0] == row.record.record_id
            for h in trace.audit.hits
        )
        assert not any(
            h.document.settled_life is not None
            for h in index.search(query.model_copy(update={"occurred_to": wake.logical_time})).hits
        )
        assert not index.search(
            query.model_copy(
                update={"actor_ref": "actor:foreign", "subject_refs": ("actor:foreign",)}
            )
        ).hits
        _choice(ledger, binding, before=active, status="forgotten")
        docs = compile_docs(ledger, sources_for(ledger, content))
        assert not any(d.prehistory is not None for d in docs)
        assert not any(
            any(ref.startswith("lived-story-link:") for ref in d.link_refs) for d in docs
        )
        assert ledger.project().experiences == ()  # The World never manufactures her response.
    finally:
        runtime.close()
        recall.close()
        content.close()
        ledger.close()


@pytest.mark.asyncio
async def test_central_cancel_and_departure_preserve_protagonist_plan_and_original_story_history(
    tmp_path,
):
    from companion_daemon.world_v2.world_outlook import source_catalog

    def change(draft):
        activity(draft["opportunities"][0])
        draft["review_at"] = (
            datetime.fromisoformat(draft["opportunities"][0]["opens_at"]) + timedelta(minutes=10)
        ).isoformat()

    ledger, wake, author, runtime, npc, world = npc_setup(tmp_path, change)
    try:
        await runtime.refresh(
            projection=ledger.project(), context={}, manifest=npc_manifest(ledger, wake)
        )
        before_main = next(p for p in ledger.project().plans if p.owner_actor_ref == OWNER)
        node = next(n for n in runtime.ready() if n["opportunity_id"] == "notice")
        started = runtime.execute_offer(node, wake_event_ref=wake.event_id)
        later = _seed_clock(
            ledger,
            event_id="clock:cancel-npc",
            logical_time=wake.logical_time + timedelta(minutes=11),
            logical_time_from=wake.logical_time,
        )
        draft = retime(draft_value(later), ledger.project(), later)
        draft["parent_revision"] = runtime.store.head()[0]
        draft["opportunities"] = draft["opportunities"][:1]
        cancel = draft["opportunities"][0]
        cancel.update(
            opportunity_id="cancel-books",
            lane="npc",
            npc_ref="npc:lin",
            execution={
                "kind": "npc_cancel_activity",
                "text": "林临时停止整理，之后再安排。",
                "target_plan_ref": started.plan_id,
                "privacy_class": "private",
            },
        )
        token = source_catalog(ledger.project(), actor_ref=OWNER, npc_refs=("npc:lin",))[
            started.plan_id
        ].token
        cancel["conditions"] = [{"source_ref": started.plan_id, "unchanged_token": token}]
        author.outputs.append({"decision": "revise", "draft": draft})
        assert (
            await runtime.refresh(
                projection=ledger.project(), context={}, manifest=npc_manifest(ledger, later)
            )
        )["status"] == "ready"
        assert (
            runtime.execute_offer(runtime.ready()[0], wake_event_ref=later.event_id).status
            == "npc_effect_committed"
        )
        assert (
            next(p for p in ledger.project().plans if p.plan_id == started.plan_id).status
            == "abandoned"
        )
        draft = retime(draft_value(later), ledger.project(), later)
        draft["parent_revision"] = runtime.store.head()[0]
        draft["opportunities"] = draft["opportunities"][:1]
        draft["opportunities"][0].update(
            opportunity_id="departure",
            lane="npc",
            npc_ref="npc:lin",
            execution={
                "kind": "npc_status",
                "text": "林暂时离开这片生活圈。",
                "target_status": "departed",
                "privacy_class": "private",
            },
        )
        author.outputs.append({"decision": "revise", "draft": draft})
        assert (
            await runtime.refresh(
                projection=ledger.project(), context={}, manifest=npc_manifest(ledger, later)
            )
        )["status"] == "ready"
        assert (
            runtime.execute_offer(runtime.ready()[0], wake_event_ref=later.event_id).status
            == "npc_effect_committed"
        )
        assert ledger.project().npcs[0].status == "departed"
        assert (
            next(p for p in ledger.project().plans if p.plan_id == before_main.plan_id)
            == before_main
        )
        history = runtime.story_history()
        assert any(
            source["source_ref"] == started.plan_id and source["status"] == "abandoned"
            for thread in history
            for source in thread["realized_sources"]
        )
        assert len(author.calls) == 3 and npc.calls == [] and world.calls == []
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_central_author_cannot_cancel_protagonist_plan(tmp_path):
    ledger, wake, author, runtime, npc, world = npc_setup(tmp_path, lambda d: None)
    try:
        draft = author.outputs[0]["draft"]
        draft["opportunities"] = draft["opportunities"][:1]
        draft["opportunities"][0].update(
            lane="npc",
            npc_ref="npc:lin",
            execution={
                "kind": "npc_cancel_activity",
                "text": "停止这项安排",
                "target_plan_ref": "plan-tea",
                "privacy_class": "private",
            },
        )
        author.outputs.append({"decision": "revise", "draft": draft})
        before = ledger.project()
        result = await runtime.refresh(
            projection=before, context={}, manifest=npc_manifest(ledger, wake)
        )
        assert result["status"] == "technical_failure"
        assert ledger.project() == before and runtime.store.head() is None
        assert len(author.calls) == 2
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_prepaid_npc_completion_continues_when_planning_budget_is_paused(tmp_path):
    from companion_daemon.world_v2.life_ecology_runtime import (
        LifeEcologyRuntime,
        LifeEcologyAvailability,
    )
    from test_life_ecology_runtime import _Media, _TriggerStore

    ledger, wake, author, runtime, npc, world = npc_setup(
        tmp_path, lambda d: activity(d["opportunities"][0], duration=20)
    )
    try:
        await runtime.refresh(
            projection=ledger.project(), context={}, manifest=npc_manifest(ledger, wake)
        )
        node = next(n for n in runtime.ready() if n["opportunity_id"] == "notice")
        started = runtime.execute_offer(node, wake_event_ref=wake.event_id)
        later = _seed_clock(
            ledger,
            event_id="clock:budget-paused",
            logical_time=wake.logical_time + timedelta(minutes=20),
            logical_time_from=wake.logical_time,
        )
        ecology = LifeEcologyRuntime(
            ledger=ledger,
            trigger_store=_TriggerStore(),
            media_followup=_Media(),
            availability=LifeEcologyAvailability(state="installed_and_active"),
            world_outlook=runtime,
            background_budget_paused=lambda: True,
        )
        result = await ecology.advance_once(
            wake_event_ref=later.event_id, trace_id="budget", correlation_id="budget"
        )
        assert result.reason_code == "life_ecology.paused_by_budget"
        assert (
            next(p for p in ledger.project().plans if p.plan_id == started.plan_id).status
            == "completed"
        )
        assert len(author.calls) == 1 and npc.calls == [] and world.calls == []
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_npc_activity_recovers_after_commit_before_receipt_without_duplicate_plan(
    tmp_path, monkeypatch
):
    class Crash(BaseException):
        pass

    ledger, wake, author, runtime, npc, world = npc_setup(
        tmp_path, lambda d: activity(d["opportunities"][0])
    )
    try:
        await runtime.refresh(
            projection=ledger.project(), context={}, manifest=npc_manifest(ledger, wake)
        )
        node = next(n for n in runtime.ready() if n["opportunity_id"] == "notice")

        def crash(*args, **kwargs):
            raise Crash()

        with monkeypatch.context() as m:
            m.setattr(runtime, "finish_offer", crash)
            with pytest.raises(Crash):
                runtime.execute_offer(node, wake_event_ref=wake.event_id)
        assert runtime.reconcile_accepted_offers(wake_event_ref=wake.event_id) == 1
        assert runtime.reconcile_accepted_offers(wake_event_ref=wake.event_id) == 0
        assert runtime.execute_offer(node, wake_event_ref=wake.event_id) is None
        assert sum(p.owner_actor_ref == "npc:lin" for p in ledger.project().plans) == 1
        assert len(author.calls) == 1
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_revised_npc_script_after_precommit_failure_gets_its_own_author_evidence(
    tmp_path, monkeypatch
):
    ledger, wake, author, runtime, npc, world = npc_setup(
        tmp_path, lambda d: activity(d["opportunities"][0])
    )
    original = ledger.commit_at_cursor

    def unavailable(events, **kwargs):
        if events[0].event_type == "ActivityPlanned":
            raise RuntimeError("plan storage unavailable")
        return original(events, **kwargs)

    try:
        await runtime.refresh(
            projection=ledger.project(), context={}, manifest=npc_manifest(ledger, wake)
        )
        node = next(n for n in runtime.ready() if n["opportunity_id"] == "notice")
        with monkeypatch.context() as m:
            m.setattr(ledger, "commit_at_cursor", unavailable)
            assert (
                runtime.execute_offer(node, wake_event_ref=wake.event_id).status
                == "technical_failure"
            )
        later = _seed_clock(
            ledger,
            event_id="clock:revised-task",
            logical_time=wake.logical_time + timedelta(days=1),
            logical_time_from=wake.logical_time,
        )
        draft = retime(draft_value(later), ledger.project(), later)
        draft["parent_revision"] = runtime.store.head()[0]
        activity(draft["opportunities"][0])
        draft["opportunities"][0]["execution"]["activity_kind"] = "different_activity"
        author.outputs.append({"decision": "revise", "draft": draft})
        assert (
            await runtime.refresh(
                projection=ledger.project(), context={}, manifest=npc_manifest(ledger, later)
            )
        )["status"] == "ready"
        node = next(n for n in runtime.ready() if n["opportunity_id"] == "notice")
        result = runtime.execute_offer(node, wake_event_ref=later.event_id)
        assert result.status == "npc_activity_started"
        plan = next(p for p in ledger.project().plans if p.plan_id == result.plan_id)
        start = ledger.lookup_event_commit(plan.authority_origin.accepted_event_ref)[0]
        audit = ledger.lookup_event_commit(start.causation_id)[0].payload()
        assert plan.activity_kind == audit["execution"]["activity_kind"] == "different_activity"
        assert (
            audit["central_authorship"]["response_hash"] == runtime.store.head()[2]["response_hash"]
        )
        assert len(author.calls) == 2 and npc.calls == [] and world.calls == []
    finally:
        runtime.close()
