"""Only readable execution sources inside the original manifest are offered."""

from __future__ import annotations

import pytest

from current_activity_fixture import accepted_current_activity
from companion_daemon.world_v2.life_development_draft import LifeDevelopmentCapabilityManifest
from companion_daemon.world_v2.schemas import ProjectionCursor


def _manifest(ledger, anchors=(), *, actor="actor:companion", marker="world-consequence.2"):
    state = ledger.project()
    return LifeDevelopmentCapabilityManifest(
        version="life-development-capabilities.2", owner_actor_ref=actor,
        pinned_cursor=ProjectionCursor(
            world_revision=state.world_revision, deliberation_revision=state.deliberation_revision,
            ledger_sequence=state.ledger_sequence,
        ),
        anchor_refs=tuple(sorted(anchors)), outcome_contract=marker,
        max_future_days=1, max_window_minutes=60,
    )


@pytest.mark.asyncio
async def test_original_manifest_anchor_offers_exact_readable_execution(tmp_path):
    ledger, store, _, source_ref = await accepted_current_activity(sqlite_path=tmp_path / "life.sqlite")
    from companion_daemon.world_v2.world_consequence_authoring_context import (
        build_world_consequence_authoring_context,
        validate_world_consequence_authoring_context,
    )

    manifest = _manifest(ledger, (source_ref,))
    context = build_world_consequence_authoring_context(
        ledger=ledger, content_store=store, manifest=manifest, actor_ref="actor:companion",
    )
    assert len(context.execution_materials) == 1
    assert context.execution_materials[0].status == "available"
    assert context.authority.execution_bindings[0].source_event_ref == source_ref
    assert context.execution_materials[0].execution_binding == context.authority.execution_bindings[0]
    validate_world_consequence_authoring_context(
        ledger=ledger, content_store=store, manifest=manifest, actor_ref="actor:companion",
        authority=context.authority, execution_materials=context.execution_materials,
    )


@pytest.mark.asyncio
async def test_missing_anchor_does_not_scan_life_history_for_extra_permission(tmp_path):
    ledger, store, _, _ = await accepted_current_activity(sqlite_path=tmp_path / "life.sqlite")
    from companion_daemon.world_v2.world_consequence_authoring_context import (
        build_world_consequence_authoring_context,
    )

    context = build_world_consequence_authoring_context(
        ledger=ledger, content_store=store, manifest=_manifest(ledger), actor_ref="actor:companion",
    )
    assert context.authority.execution_bindings == ()
    assert context.execution_materials == ()
