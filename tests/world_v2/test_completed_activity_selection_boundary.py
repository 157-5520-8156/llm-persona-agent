"""Accepted lifecycle context is visible to the author, not execution success.

Character and lifecycle choices reuse the offline public day-open fixture.
This characterizes the frozen selector, not a new World consequence producer
or a real model's ability to distinguish an ended attempt from a fulfilled one.
"""

from copy import deepcopy
import json

import pytest
import test_day_open_self_directed_intent as day_fixture

from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.visible_meaning_source_review import _eligible_readings
from companion_daemon.world_v2.visible_source_closure_protocol import _eligible_reference
from companion_daemon.world_v2.visible_source_composer import compile_visible_source_table
from companion_daemon.world_v2.visible_source_reading_experiment import _catalog
from companion_daemon.world_v2.visible_source_scope_selection import select_permission_context
from companion_daemon.world_v2.visible_source_witness_experiment import prepare_witness_experiment
from companion_daemon.world_v2.world_consequence_contract import derive_world_consequence_authority
from test_current_activity_context import current_context
from test_day_open_world_consequence_material import _day_activity
from test_visible_source_composer import _request
from test_world_stimulus_life_intent import ACTOR, WORLD


build_app = day_fixture.build_app


@pytest.mark.asyncio
async def test_completed_capsule_reaches_author_and_full_source_table_before_permission_selection(
    tmp_path, monkeypatch, build_app,
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "ended-selection.sqlite"
    plan = await _day_activity(path, build_app, monkeypatch, "completed")
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
    try:
        capsule, _, snapshot = current_context(ledger, store, actor_ref=ACTOR)
        (ended,) = snapshot.materials["recently_ended_activities"]
        terminal_ref = plan.authority_origin.accepted_event_ref
        terminal = ledger.lookup_event_commit(terminal_ref)[0]
        started = next(
            row.event for row in ledger.export_replay_evidence().events
            if row.event.event_type == "ActivityStarted"
            and row.event.payload()["plan_id"] == plan.plan_id
        )
        assert terminal.event_type == "ActivityCompleted"
        assert terminal.payload()["plan_id"] == started.payload()["plan_id"] == plan.plan_id
        assert ended["plan_id"] == plan.plan_id
        assert ended["source_ref"] == terminal_ref
        assert ended["completion_scope"] == "activity_lifecycle_ended_not_intention_fulfilled"
        assert not snapshot.materials.get("current_activities")
        assert "location_ref" not in ended and "outcome" not in ended

        # A later head does not relabel the original attempt's event/revision.
        authority = derive_world_consequence_authority(
            pinned_state=ledger.project(), actor_ref=ACTOR, source_events=(started,),
        )
        (binding,) = authority.execution_bindings
        assert binding.plan_id == ended["plan_id"]
        assert binding.source_event_ref == started.event_id != terminal_ref
        assert binding.source_event_type == "ActivityStarted"
        assert binding.plan_entity_revision < ended["plan_entity_revision"]
        with pytest.raises(ValueError, match="source_type"):
            derive_world_consequence_authority(
                pinned_state=ledger.project(), actor_ref=ACTOR, source_events=(terminal,),
            )

        table = compile_visible_source_table(request=_request(capsule), capsule=capsule)
        original = table.payload_json
        sources = table.source_references()
        row = next(r for r in sources if r["source_ref"] == terminal_ref and r.get("activity_support"))
        assert row["support_eligibility"] == "eligible"
        assert row["activity_support"]["status"] == "completed"
        assert row["review_material"]["item"]["value"]["plan_id"] == plan.plan_id
        assert not any(r.get("settled_life_support") for r in sources)

        # Wrong-plan/ending aliases cannot inherit the accepted item's proof.
        for field, substituted in (("plan_id", "plan:unrelated"),
                                   ("activity_event_ref", "event:unrelated-completion")):
            wrong = deepcopy(row)
            wrong["review_material"]["item"]["value"][field] = substituted
            assert not _eligible_reference(wrong)

        witness = prepare_witness_experiment(
            beats=("我刚在外头走了一圈。",), sources=sources,
            source_owner_semantics=True, prehistory_authority=True,
        )
        pin = json.loads(witness.payload_json)
        catalog = _catalog(
            pin, report_uptake=True, content_fields_only=True, prehistory_authority=True,
        )
        index = next(
            i for i, material in enumerate(pin["shown_materials"])
            if material.get("item", {}).get("value", {}).get("context_kind") == "completed_activity"
        )
        lifecycle = [r for r in catalog if r["material_index"] == index]
        assert lifecycle
        assert {tuple(p) for r in lifecycle for p in r["permissions"]} == {
            ("accepted_intention", "companion"), ("activity_lifecycle", "companion"),
        }

        # This is the actual .1 exclusion boundary: even a lifecycle wording
        # classified as actual_event_or_state has no lifecycle reading mode.
        fact = {"mode": "actual_event_or_state", "subject_role": "companion"}
        retained, selected, proof = select_permission_context(
            witness_pin=pin, catalog=catalog, facts=[fact],
        )
        assert proof["contract"] == "visible-permission-context-selection.1"
        assert index not in retained
        assert _eligible_readings(fact, lifecycle) == {}
        assert _eligible_readings(fact, selected) == _eligible_readings(fact, catalog)

        intention = {"mode": "past_intention", "subject_role": "companion"}
        retained, selected, _ = select_permission_context(
            witness_pin=pin, catalog=catalog, facts=[intention],
        )
        assert index in retained
        assert _eligible_readings(intention, selected) == _eligible_readings(intention, catalog)
        assert table.payload_json == original
    finally:
        store.close()
        ledger.close()
