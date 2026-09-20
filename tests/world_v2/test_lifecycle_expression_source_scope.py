"""Life-only Plan state must not become generic visible World proof."""

import pytest

from companion_daemon.world_v2.expression_draft import world_claim_source_refs_by_scope
from current_activity_fixture import accepted_current_activity
from test_current_activity_context import current_context
from test_world_consequence_authoring_context import _operator_transition


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["ActivityPaused", "ActivityAbandoned"])
async def test_recorded_lifecycle_state_does_not_gain_visible_occurrence_authority(event_type):
    ledger, store, plan_id, _ = await accepted_current_activity()
    event_ref = _operator_transition(ledger, plan_id, event_type, "event:visible-lifecycle-scope")
    before = ledger.export_replay_evidence()
    _, context, snapshot = current_context(ledger, store)

    # The complete host Capsule legitimately retains this recorded state for
    # Life's qualified reader. The ordinary model view does not expose it.
    state = next(item for item in context["slices"]["world_life"]["items"]
                 if item["source_ref"] == event_ref)
    assert state["value"]["context_kind"] == "activity_lifecycle_state"
    assert "activity_lifecycle_states" not in snapshot.model_view()["materials"]
    scopes = world_claim_source_refs_by_scope(context=context)
    for scope in ("current_world", "past_world", "shared_history"):
        assert event_ref not in scopes[scope]
    assert ledger.export_replay_evidence() == before
