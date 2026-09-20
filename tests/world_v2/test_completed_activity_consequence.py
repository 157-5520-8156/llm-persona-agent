"""Offline role choices prove completion/attempt lineage, never successful outcomes."""

from copy import deepcopy
from datetime import timedelta

import pytest
import test_day_open_self_directed_intent as day_fixture

from companion_daemon.world_v2.completed_activity_consequence import (
    CompletedActivityConsequence,
    read_completed_activity_consequence,
    validate_completed_activity_consequence,
)
from companion_daemon.world_v2.schemas import ProjectionCursor
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.world_consequence_contract import (
    derive_world_consequence_authority,
)
from test_day_open_world_consequence_material import _day_activity, _LifecycleHTTP, _tick, _wake
from test_world_stimulus_life_intent import ACTOR, NOW, WORLD, _model


build_app = day_fixture.build_app


@pytest.mark.asyncio
@pytest.mark.parametrize("resumed", [False, True])
async def test_completed_role_activity_binds_last_attempt_and_survives_cold_pin(
    tmp_path, monkeypatch, build_app, resumed,
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "completion.sqlite"
    plan = await _day_activity(path, build_app, monkeypatch, "resumed" if resumed else "completed")
    if resumed:
        provider = _LifecycleHTTP()
        provider.lifecycle_choice = "complete"
        model = _model(provider)
        app = build_app(path, model, ecology=True)
        try:
            await _tick(app, "complete-resumed", NOW + timedelta(minutes=6))
            (plan,) = app.export_replay_evidence().projection.plans
        finally:
            await app.aclose()
            await model.aclose()
    assert plan.status == "completed"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    try:
        state = ledger.project()
        cursor = ProjectionCursor(
            world_revision=state.world_revision,
            deliberation_revision=state.deliberation_revision,
            ledger_sequence=state.ledger_sequence,
        )
        terminal_ref = plan.authority_origin.accepted_event_ref
        descriptor = read_completed_activity_consequence(
            ledger=ledger, pinned_state=state, actor_ref=ACTOR,
            completion_event_ref=terminal_ref,
        )
        assert descriptor is not None
        assert descriptor.completion.plan_id == descriptor.execution_binding.plan_id == plan.plan_id
        assert descriptor.completion.event_ref == terminal_ref
        assert descriptor.execution_binding.source_event_type == (
            "ActivityResumed" if resumed else "ActivityStarted"
        )
        assert descriptor.execution_binding.source_event_ref != terminal_ref
        assert descriptor.execution_binding.plan_entity_revision < descriptor.completion.plan_revision
        assert set(descriptor.model_dump()) == {"contract", "completion", "execution_binding"}
        validate_completed_activity_consequence(
            ledger=ledger, pinned_state=state, actor_ref=ACTOR, descriptor=descriptor,
        )
        with pytest.raises(ValueError, match="source_type"):
            derive_world_consequence_authority(
                pinned_state=state, actor_ref=ACTOR,
                source_events=(ledger.lookup_event_commit(terminal_ref)[0],),
            )
        for ref, actor in (
            (terminal_ref, "actor:other"),
            ("event:missing", ACTOR),
            (descriptor.execution_binding.source_event_ref, ACTOR),
        ):
            assert read_completed_activity_consequence(
                ledger=ledger, pinned_state=state, actor_ref=actor, completion_event_ref=ref,
            ) is None

        # Coherent-looking replacements still lack the exact original pair.
        for part, field, value in (
            ("completion", "event_ref", "event:wrong-completion"),
            ("completion", "payload_hash", "0" * 64),
            ("execution_binding", "source_event_ref", "event:wrong-start"),
            ("execution_binding", "source_payload_hash", "0" * 64),
        ):
            altered = deepcopy(descriptor.model_dump(mode="json"))
            altered[part][field] = value
            with pytest.raises(ValueError, match="original_pair_mismatch"):
                validate_completed_activity_consequence(
                    ledger=ledger, pinned_state=state, actor_ref=ACTOR,
                    descriptor=CompletedActivityConsequence.model_validate(altered),
                )
        altered = deepcopy(descriptor.model_dump(mode="json"))
        altered["completion"]["plan_id"] = altered["execution_binding"]["plan_id"] = "plan:other"
        with pytest.raises(ValueError, match="original_pair_mismatch"):
            validate_completed_activity_consequence(
                ledger=ledger, pinned_state=state, actor_ref=ACTOR,
                descriptor=CompletedActivityConsequence.model_validate(altered),
            )

        if resumed:
            started = next(
                row.event for row in ledger.export_replay_evidence().events
                if row.event.event_type == "ActivityStarted"
                and row.event.payload()["plan_id"] == plan.plan_id
            )
            (earlier_binding,) = derive_world_consequence_authority(
                pinned_state=state, actor_ref=ACTOR, source_events=(started,),
            ).execution_bindings
            with pytest.raises(ValueError, match="original_pair_mismatch"):
                validate_completed_activity_consequence(
                    ledger=ledger, pinned_state=state, actor_ref=ACTOR,
                    descriptor=descriptor.model_copy(update={"execution_binding": earlier_binding}),
                )

        original_lookup = ledger.lookup_event_commit
        event, commit = original_lookup(terminal_ref)
        with monkeypatch.context() as patch:
            patch.setattr(ledger, "lookup_event_commit", lambda ref: (
                (event.model_copy(update={"payload_hash": "0" * 64}), commit)
                if ref == terminal_ref else original_lookup(ref)
            ))
            with pytest.raises(ValueError):
                read_completed_activity_consequence(
                    ledger=ledger, pinned_state=state, actor_ref=ACTOR,
                    completion_event_ref=terminal_ref,
                )
        assert ledger.project() == state
    finally:
        ledger.close()

    reopened = SQLiteWorldLedger(path=path, world_id=WORLD)
    try:
        _wake(reopened, "later-head")
        assert reopened.project().world_revision > cursor.world_revision
        validate_completed_activity_consequence(
            ledger=reopened, pinned_state=reopened.project_at(cursor),
            actor_ref=ACTOR, descriptor=descriptor,
        )
        assert not reopened.project().experiences
    finally:
        reopened.close()
