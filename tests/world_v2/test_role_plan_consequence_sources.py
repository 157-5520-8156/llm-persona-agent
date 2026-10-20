"""Offline public role revision/lifecycle -> production consequence source reader."""

from datetime import timedelta
from types import SimpleNamespace
import pytest

from test_world_plan_continuity import PlanRole, settlement, _model, _build, _clock, WORLD, ACTOR
from test_life_development_audit_context_recovery import _catalog
from companion_daemon.world_v2.life_ecology_contract import LifeEcologyRunKey
from companion_daemon.world_v2.life_development_capability import (
    ProjectionLifeCapabilityManifestCompiler,
)
from companion_daemon.world_v2.world_plan_management import RolePlanResponseReader
from companion_daemon.world_v2.schemas import ProjectionCursor


async def lifecycle(app, provider, choice, at, key):
    now = await app.current_logical_time()
    provider.lifecycle_choice = choice
    await app.advance(_clock(key, now, at))
    wake = "event:trigger:clock:" + key
    ecology = app._life_ecology
    claim = await ecology._trigger_store.claim_or_join(
        key=LifeEcologyRunKey(
            world_id=WORLD,
            wake_event_ref=wake,
            catalog_version=ecology.availability().catalog_version,
        ),
        trace_id=key,
        correlation_id="revised-consequence",
    )
    await ecology._advance_activity_once(
        wake_event_ref=wake,
        trigger_id=claim.trigger_id,
        logical_time=at,
        trace_id=key,
        correlation_id="revised-consequence",
        renewed_plan_catalog=True,
    )
    return app._ledger.lookup_event_commit(wake)[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["active", "completed"])
@pytest.mark.parametrize("long_intention", [False, True])
async def test_revised_intention_reaches_world_result_with_its_exact_original_role_audit(
    tmp_path, monkeypatch, status, long_intention
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = PlanRole()
    if long_intention:
        provider.replacement_intention = (
            "我想换个方向，仔细整理自己的新安排，结果再根据实际情况判断。" * 14
        )
    model = _model(provider)
    app = _build(tmp_path / "world.sqlite", model, ecology=True)
    try:
        await settlement(app, provider, "original")
        await app.drain_background_once()
        original = app._ledger.project().plans[0]
        provider.operation = "revise"
        provider.plan_id = original.plan_id
        await settlement(app, provider, "revision")
        for _ in range(6):
            await app.drain_background_once()
            if any(p.supersedes_plan_id == original.plan_id for p in app._ledger.project().plans):
                break
        revised = next(
            p for p in app._ledger.project().plans if p.supersedes_plan_id == original.plan_id
        )
        wake = await lifecycle(
            app,
            provider,
            "start",
            revised.scheduled_window.opens_at + timedelta(seconds=1),
            "revised-start",
        )
        revised = next(p for p in app._ledger.project().plans if p.plan_id == revised.plan_id)
        assert revised.status == "active"
        start = app._ledger.lookup_event_commit(revised.authority_origin.accepted_event_ref)[0]
        if status == "completed":
            wake = await lifecycle(
                app,
                provider,
                "complete",
                revised.scheduled_window.closes_at + timedelta(seconds=1),
                "revised-complete",
            )
        state = app._ledger.project()
        revised = next(p for p in state.plans if p.plan_id == revised.plan_id)
        assert revised.status == status
        cursor = ProjectionCursor(
            world_revision=state.world_revision,
            deliberation_revision=state.deliberation_revision,
            ledger_sequence=state.ledger_sequence,
        )
        role_reader = RolePlanResponseReader(ledger=app._ledger)
        read = getattr(role_reader, "read_" + status + "_plan")(
            plan_id=revised.plan_id,
            expected_cursor=cursor,
            actor_ref=ACTOR,
            viewer_privacy_ceiling="private",
        )
        assert read is not None and read.accepted_intention.text == provider.replacement_intention
        manifest = ProjectionLifeCapabilityManifestCompiler(
            owner_actor_ref=ACTOR, catalog=_catalog(tmp_path)
        ).compile(projection=state, wake=wake, capsule=SimpleNamespace(model_content_json="{}"))
        from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
        from companion_daemon.world_v2.world_consequence_execution_context import (
            build_world_consequence_execution_materials,
        )

        store = SQLiteImmutableLifeContentStore(path=tmp_path / "world.sqlite", world_id=WORLD)
        try:
            (material,) = build_world_consequence_execution_materials(
                ledger=app._ledger,
                content_store=store,
                pinned_state=state,
                actor_ref=ACTOR,
                source_events=(start,),
                intention_reader_version=manifest.execution_intention_sources_version,
            )
            assert material.status == "available", material
            (legacy,) = build_world_consequence_execution_materials(
                ledger=app._ledger,
                content_store=store,
                pinned_state=state,
                actor_ref=ACTOR,
                source_events=(start,),
                intention_reader_version="2",
            )
            assert (
                legacy.status == "unavailable"
                and legacy.unavailable_reason == "character_audit_unavailable"
            )
            assert (
                material.authorized_intention.epistemic_scope
                == "authorized_intention_not_embedded_history_or_execution_success"
            )
            assert material.authorized_intention.text == read.accepted_intention.text
            from companion_daemon.world_v2.world_consequence_authoring_context import (
                build_world_consequence_authoring_context,
            )

            ordinary = manifest.model_copy(update={
                "execution_intention_sources_version": "3",
                "anchor_refs": tuple(sorted({wake.event_id, start.event_id})),
            })
            original_ordinary = build_world_consequence_authoring_context(
                ledger=app._ledger, content_store=store, manifest=ordinary, actor_ref=ACTOR
            )
            assert original_ordinary.execution_materials[0].authorized_intention == material.authorized_intention
            independent = build_world_consequence_authoring_context(
                ledger=app._ledger, content_store=store,
                manifest=ordinary.model_copy(update={"execution_intention_sources_version": "4"}),
                actor_ref=ACTOR,
            )
            assert independent.authority.execution_bindings == independent.execution_materials == ()
            assert app._ledger.project() == state
            if status == "completed":
                from companion_daemon.world_v2.completed_activity_consequence import (
                    read_completed_activity_consequence,
                )
                from companion_daemon.world_v2.world_consequence_authoring_context import (
                    build_world_consequence_authoring_context,
                )

                completed = read_completed_activity_consequence(
                    ledger=app._ledger,
                    pinned_state=state,
                    actor_ref=ACTOR,
                    completion_event_ref=revised.authority_origin.accepted_event_ref,
                )
                assert completed is not None
                manifest = manifest.model_copy(
                    update={
                        "completed_activity_consequence": completed,
                        "anchor_refs": tuple(
                            sorted({wake.event_id, completed.completion.event_ref, start.event_id})
                        ),
                    }
                )
                context = build_world_consequence_authoring_context(
                    ledger=app._ledger, content_store=store, manifest=manifest, actor_ref=ACTOR
                )
                assert context.authority.execution_bindings == (completed.execution_binding,)
            assert app._ledger.project() == state
            from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger

            cold = SQLiteWorldLedger(path=tmp_path / "world.sqlite", world_id=WORLD)
            try:
                (again,) = build_world_consequence_execution_materials(
                    ledger=cold,
                    content_store=store,
                    pinned_state=state,
                    actor_ref=ACTOR,
                    source_events=(start,),
                    intention_reader_version=manifest.execution_intention_sources_version,
                )
                assert again == material and cold.rebuild() == state
            finally:
                cold.close()
        finally:
            store.close()
    finally:
        app.close()
        await model.aclose()
