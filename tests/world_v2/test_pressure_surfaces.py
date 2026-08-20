"""Pressure surfaces must expose ledger-backed forces, not empty context stubs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from companion_daemon.world_v2.life_development_draft import (
    LifeDevelopmentCapabilityManifest,
    LifeDevelopmentNpcCapability,
)
from companion_daemon.world_v2.life_development_runtime import compile_pressure_surfaces
from companion_daemon.world_v2.schema_core import EvidenceRef
from companion_daemon.world_v2.schemas import DueWindow, PlanStateProjection, ProjectionCursor


def _manifest(*, npc_plans: tuple[str, ...]) -> LifeDevelopmentCapabilityManifest:
    return LifeDevelopmentCapabilityManifest(
        version="test-pressure-surfaces.1",
        owner_actor_ref="agent:companion",
        pinned_cursor=ProjectionCursor(
            world_revision=1,
            deliberation_revision=1,
            ledger_sequence=1,
        ),
        npc_capabilities=(
            LifeDevelopmentNpcCapability(
                npc_ref="npc:hometown-xu",
                lifecycle_state="active",
                identity_content_ref="content:npc:xu",
                identity_summary="徐青禾，嘉兴老朋友。",
                identity_payload_hash="a" * 64,
                authority_refs=("event:npc:xu",),
                active_plan_refs=npc_plans,
                current_location_ref="location:jiaxing-family-bookstore",
                protagonist_closeness_bp=3450,
            ),
        ),
        entity_refs=("npc:hometown-xu",),
        biographical_context_tags=("calendar:summer_break",),
        max_future_days=30,
        max_window_minutes=720,
    )


def test_disturbance_consequence_usage_specimen_shows_filled_shape() -> None:
    from companion_daemon.world_v2.life_development_runtime import (
        disturbance_consequence_usage_specimen,
        outcome_has_durable_world_consequence,
    )
    from companion_daemon.world_v2.life_development_draft import LifeDevelopmentOutcomeDraft

    specimen = disturbance_consequence_usage_specimen()
    assert specimen["dynamic_life_direction"]["context_tags"]
    draft = LifeDevelopmentOutcomeDraft(
        experienced_by_ref="agent:companion",
        text=str(specimen["outcome_text"]),
        privacy_class="personal",
        relative_plausibility_weight=6000,
        claim_refs=["local:claim:specimen"],
        dynamic_life_direction=specimen["dynamic_life_direction"],
    )
    assert outcome_has_durable_world_consequence(draft)


def test_compile_pressure_surfaces_reads_projection_plans_not_context() -> None:
    now = datetime(2026, 8, 20, 16, 0, tzinfo=UTC)
    window = DueWindow(
        opens_at=now - timedelta(hours=2),
        closes_at=now + timedelta(hours=6),
    )
    plan = PlanStateProjection(
        plan_id="plan:life-development:test",
        activity_id="activity:test",
        entity_revision=1,
        activity_kind="visit_bookstore",
        evidence_refs=(
            EvidenceRef(
                ref_id="event:plan:test",
                evidence_type="committed_world_event",
                claim_purpose="life_transition",
                source_world_revision=1,
                immutable_hash="a" * 64,
            ),
        ),
        status="planned",
        importance_bp=5800,
        scheduled_window=window,
        location_ref="location:jiaxing-family-bookstore",
        owner_actor_ref="agent:companion",
    )
    npc_plan = PlanStateProjection(
        plan_id="plan:life-development:npc",
        activity_id="activity:npc",
        entity_revision=1,
        activity_kind="help_sort_books",
        evidence_refs=(
            EvidenceRef(
                ref_id="event:plan:npc",
                evidence_type="committed_world_event",
                claim_purpose="life_transition",
                source_world_revision=1,
                immutable_hash="b" * 64,
            ),
        ),
        status="active",
        importance_bp=4500,
        scheduled_window=window,
        location_ref="location:jiaxing-family-bookstore",
        owner_actor_ref="npc:hometown-xu",
        participant_refs=("npc:hometown-xu",),
    )

    class _Projection:
        plans = (plan, npc_plan)
        aspirations = ()
        npcs = ()
        biographical_coordinates = ()
        world_occurrences = ()

    surfaces = compile_pressure_surfaces(
        manifest=_manifest(npc_plans=("plan:life-development:npc",)),
        context={},
        projection=_Projection(),
        logical_time=now,
        owner_actor_ref="agent:companion",
    )

    assert surfaces["contract"] == "life-development-pressure-surfaces.2"
    assert len(surfaces["protagonist_plans"]) == 1
    assert surfaces["protagonist_plans"][0]["plan_id"] == plan.plan_id
    assert len(surfaces["npc_owned_plans"]) == 1
    assert surfaces["npc_capabilities"][0]["active_plans"][0]["plan_id"] == npc_plan.plan_id
    assert surfaces["active_plans"]
