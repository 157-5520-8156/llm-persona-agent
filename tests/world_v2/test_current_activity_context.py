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
    assert rebuilt.snapshot_compiler.value == "inner-life-snapshot-compiler.20"


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
