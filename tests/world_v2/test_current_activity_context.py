from __future__ import annotations

import json

import pytest

from current_activity_fixture import accepted_current_activity
from companion_daemon.world_v2.character_interior.snapshot_compiler import (
    compile_inner_life_snapshot,
    source_envelopes_from_capsule,
)
from companion_daemon.world_v2.context_resolver import query_from_projection
from companion_daemon.world_v2.expression_draft import world_claim_source_refs_by_scope
from companion_daemon.world_v2.ledger_context_resolver import (
    ContextRelevanceScope,
    context_capsule_compiler_from_ledger,
)
from companion_daemon.world_v2.model_facing_context import compact_chat_model_facing_context
from companion_daemon.world_v2.life_development_runtime import LifeDevelopmentProposalReader
from companion_daemon.world_v2.life_content_store import (
    StoredLifeContent,
    life_content_payload_hash,
)
from companion_daemon.world_v2.schemas import ProjectionCursor


def current_context(ledger, store, *, actor_ref="actor:companion"):
    capsule = context_capsule_compiler_from_ledger(
        ledger=ledger,
        life_content_store=store,
        relevance_scope=ContextRelevanceScope(actor_ref=actor_ref),
    ).compile(
        query_from_projection(
            ledger.project(),
            actor_ref=actor_ref,
            trigger_ref="event:test-current-activity",
        )
    )
    context = json.loads(compact_chat_model_facing_context(capsule.model_content_json))
    snapshot = compile_inner_life_snapshot(
        context, source_envelopes=source_envelopes_from_capsule(capsule)
    )
    return capsule, context, snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize('status,scope', [('active', 'current_activities'), ('planned', 'planned_activities')])
async def test_existing_activity_remains_in_role_input_under_recent_settlement_pressure(tmp_path, status, scope):
    from datetime import timedelta
    from companion_daemon.world_v2.context_capsule import ContextCapsuleBudgetPolicy
    from companion_daemon.world_v2.world_outlook_runtime import WorldOutlookRuntime
    from companion_daemon.world_v2.world_outlook_store import SQLiteWorldOutlookStore
    from companion_daemon.world_v2.world_outlook_execution import WorldOutlookExecutor
    from companion_daemon.world_v2.occurrence_content_coordinator import OccurrenceContentCoordinator
    from companion_daemon.world_v2.character_interior.life_source_state_readings import life_source_profile
    from companion_daemon.world_v2.background_context_profile import slice_background_inner_life_snapshot
    from test_world_outlook import Author, draft_value
    from test_life_development_runtime import _seed_clock, _manifest, _projection_cursor, OWNER
    from test_world_consequence_aftermath import _aftermath, _NoCharacterCalls
    from test_world_consequence_producer import _advance

    if status == 'active':
        ledger, content, plan_id, activity_ref = await accepted_current_activity()
    else:
        from test_world_life_intent_runtime import _seed, _audit, INTENT
        from companion_daemon.world_v2.world_life_intent_runtime import WorldLifeIntentRuntime
        from companion_daemon.world_v2.life_content_store import InMemoryImmutableLifeContentStore
        ledger, _ = _seed(tmp_path/'future-intention.sqlite')
        proposal, cursor = _audit(ledger, intent={**INTENT, 'start_after_seconds': 3600, 'duration_seconds': 3600})
        WorldLifeIntentRuntime(ledger=ledger, owner_actor_ref=OWNER).accept(
            world_id=ledger.world_id, audit_cursor=cursor, proposal_id=proposal.proposal_id)
        plan = next(p for p in ledger.project().plans if p.plan_id.startswith('plan:world-life-intent:'))
        plan_id, activity_ref = plan.plan_id, plan.authority_origin.accepted_event_ref
        content = InMemoryImmutableLifeContentStore()
    now = ledger.project().logical_time
    wake = next(e.event for e in reversed(ledger.export_replay_evidence().events) if e.event.event_type == 'ClockAdvanced')
    from test_world_outlook_continuity import retime
    draft = retime(draft_value(wake), ledger.project(), wake)
    outlook = WorldOutlookRuntime(ledger=ledger,
        store=SQLiteWorldOutlookStore(path=tmp_path/'outlook.sqlite', world_id=ledger.world_id, actor_ref=OWNER),
        author=Author([{'decision': 'revise', 'draft': draft}]), actor_ref=OWNER,
        executor=WorldOutlookExecutor(ledger=ledger,
            occurrence_content=OccurrenceContentCoordinator(ledger=ledger, store=content), actor_ref=OWNER))
    try:
        assert (await outlook.refresh(projection=ledger.project(), context={},
                                       manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger))))['status'] == 'ready'
        assert outlook.execute_offer(outlook.ready()[0], wake_event_ref=wake.event_id).status == 'occurrence_committed'
        later = _seed_clock(ledger, event_id='clock:recent-noise', logical_time=now+timedelta(minutes=31),
                            logical_time_from=now)
        assert (await _advance(_aftermath(ledger, content, _NoCharacterCalls()), later)).status == 'settled'
        before = ledger.project()
        policy = ContextCapsuleBudgetPolicy()
        policy = policy.model_copy(update={'world_life': policy.world_life.model_copy(update={'max_items': 1})})
        capsule = context_capsule_compiler_from_ledger(ledger=ledger, life_content_store=content,
            policy=policy, relevance_scope=ContextRelevanceScope(actor_ref=OWNER)).compile(
                query_from_projection(before, actor_ref=OWNER, trigger_ref=later.event_id))
        snapshot = compile_inner_life_snapshot(json.loads(capsule.model_content_json),
            source_envelopes=source_envelopes_from_capsule(capsule))
        rendered = slice_background_inner_life_snapshot(snapshot.model_view(include_lifecycle_states=True),
                                                        life_source_profile('life-source-review.21'))
        shown = rendered['materials'].get(scope, [])
        assert len(shown) == 1, 'Recent completed scenery displaced the live accepted activity'
        assert shown[0]['plan_id'] == plan_id and shown[0]['source_ref'] == activity_ref
        assert activity_ref in snapshot.source_refs
        assert ledger.project() == before
    finally:
        outlook.close()
        if callable(getattr(ledger, 'close', None)):
            ledger.close()


@pytest.mark.asyncio
async def test_accepted_current_activity_is_readable_and_exactly_source_bound():
    ledger, store, plan_id, current_event_ref = await accepted_current_activity()
    capsule, context, snapshot = current_context(ledger, store)

    activities = snapshot.materials.get("current_activities", [])
    assert len(activities) == 1
    activity = activities[0]
    assert activity["plan_id"] == plan_id
    assert activity["source_ref"] == current_event_ref
    assert activity["status"] == "active"
    assert "校园征稿启事" in activity["accepted_intention"]["text"]
    assert activity["accepted_intention"]["epistemic_scope"] == (
        "accepted_intention_only_not_embedded_history_or_outcome"
    )
    assert "未结算的结果绝不能进入当前聊天" not in snapshot.materials_json
    assert current_event_ref in snapshot.source_refs
    scopes = world_claim_source_refs_by_scope(context=context)
    assert current_event_ref in scopes["current_world"]
    assert current_event_ref not in scopes["past_world"]
    assert current_event_ref not in scopes["shared_history"]
    assert "actor:companion" not in scopes["current_world"]
    source = next(
        item for item in snapshot.source_inventory if item.source_ref == current_event_ref
    )
    assert any(binding.ref == current_event_ref for binding in source.authority_bindings)
    assert capsule.world_life.items


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["planned", "paused"])
async def test_unstarted_or_paused_plan_is_not_current_activity(status):
    ledger, store, _, _ = await accepted_current_activity(status=status)
    _, _, snapshot = current_context(ledger, store)
    assert not snapshot.materials.get("current_activities")
    assert "校园征稿启事" not in snapshot.materials_json


@pytest.mark.asyncio
@pytest.mark.parametrize("privacy", ["private", "withhold"])
async def test_current_activity_preserves_actor_and_privacy_boundaries(privacy):
    ledger, store, plan_id, _ = await accepted_current_activity(privacy_class=privacy)
    _, _, other = current_context(ledger, store, actor_ref="actor:other")
    assert not other.materials.get("current_activities")
    assert "校园征稿启事" not in other.materials_json
    projection = ledger.project()
    cursor = ProjectionCursor(
        world_revision=projection.world_revision,
        deliberation_revision=projection.deliberation_revision,
        ledger_sequence=projection.ledger_sequence,
    )
    reader = LifeDevelopmentProposalReader(ledger=ledger, content_store=store)
    assert (
        reader.read_active_plan(
            plan_id=plan_id,
            expected_cursor=cursor,
            actor_ref="actor:companion",
            viewer_privacy_ceiling="shareable",
        )
        is None
    )
    if privacy == "withhold":
        _, _, own = current_context(ledger, store)
        assert not own.materials.get("current_activities")


@pytest.mark.asyncio
async def test_current_activity_reader_never_reads_outcomes_and_fails_closed_on_sidecar_change():
    ledger, store, plan_id, _ = await accepted_current_activity()
    projection = ledger.project()
    cursor = ProjectionCursor(
        world_revision=projection.world_revision,
        deliberation_revision=projection.deliberation_revision,
        ledger_sequence=projection.ledger_sequence,
    )
    read_refs = []

    class Store:
        altered = False

        def read_exact(self, *, content_ref):
            read_refs.append(content_ref)
            assert "character-intention:" in content_ref
            stored = store.read_exact(content_ref=content_ref)
            if self.altered:
                return StoredLifeContent(
                    content_ref=content_ref,
                    content_kind="outcome_candidate",
                    content_payload_hash=life_content_payload_hash("substituted private material"),
                    text="substituted private material",
                )
            return stored

    observed_store = Store()
    reader = LifeDevelopmentProposalReader(ledger=ledger, content_store=observed_store)
    kwargs = dict(
        plan_id=plan_id,
        expected_cursor=cursor,
        actor_ref="actor:companion",
        viewer_privacy_ceiling="private",
    )
    assert reader.read_active_plan(**kwargs) is not None
    assert len(read_refs) == 1
    observed_store.altered = True
    assert reader.read_active_plan(**kwargs) is None
    assert (
        reader.read_active_plan(
            **{
                **kwargs,
                "expected_cursor": cursor.model_copy(
                    update={"ledger_sequence": cursor.ledger_sequence + 1}
                ),
            }
        )
        is None
    )


@pytest.mark.asyncio
async def test_current_activity_projection_is_rebuildable_without_mutating_history():
    from companion_daemon.world_v2.replay_evaluator import ReplayEvaluator

    ledger, store, _, current_ref = await accepted_current_activity()
    evidence = ledger.export_replay_evidence()
    _, _, before = current_context(ledger, store)
    assert ReplayEvaluator().evaluate(evidence=evidence).passed

    class ReplayedLedger:
        def __getattr__(self, name):
            return getattr(ledger, name)

        def project(self):
            return evidence.replay

        def project_at(self, cursor):
            assert cursor == evidence.cursor
            return evidence.replay

    _, _, rebuilt = current_context(ReplayedLedger(), store)
    assert ledger.export_replay_evidence() == evidence
    assert rebuilt.snapshot_id == before.snapshot_id
    assert rebuilt.materials_json == before.materials_json
    assert current_ref in rebuilt.source_refs
    assert rebuilt.snapshot_compiler.value == "inner-life-snapshot-compiler.23"


@pytest.mark.asyncio
async def test_accepted_intention_stays_with_character_and_is_hidden_from_world_author():
    from companion_daemon.world_v2.background_context_profile import (
        background_context_profile_for_purpose,
        slice_background_capsule_context,
        slice_background_inner_life_snapshot,
    )

    ledger, store, _, _ = await accepted_current_activity()
    _, context, snapshot = current_context(ledger, store)
    author = background_context_profile_for_purpose("life_development_draft")
    author_context = slice_background_capsule_context(context, author)
    assert "校园征稿启事" not in json.dumps(author_context, ensure_ascii=False)
    character = background_context_profile_for_purpose("proactive_contact")
    character_view = slice_background_inner_life_snapshot(snapshot.model_view(), character)
    assert "校园征稿启事" in json.dumps(character_view, ensure_ascii=False)
    assert character_view["snapshot_id"] == snapshot.snapshot_id
