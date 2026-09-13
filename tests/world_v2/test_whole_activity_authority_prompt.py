"""Lifecycle prompt compilation preserves evidence and historical message bytes."""

import json

import pytest

from companion_daemon.world_v2.character_interior.inbound_wire import _ExpressionDraftWire
from current_activity_fixture import accepted_current_activity
from test_character_interior_inbound_author import _request
from test_current_activity_context import current_context


@pytest.mark.asyncio
async def test_activity_prompt_preserves_evidence_without_verbatim_fact_permission():
    ledger, store, _, source_ref = await accepted_current_activity()
    _, _, snapshot = current_context(ledger, store)
    request = _request(revision=3, call="call:activity-authority").model_copy(update={
        "model_content_json": json.dumps({"inner_life_snapshot": snapshot.model_view()}),
    })
    adapter = _ExpressionDraftWire(model=object())
    args = dict(request=request, quick_recovery=False, failure_code=None)
    legacy = adapter._messages(**args)
    revised = adapter._messages(**args, activity_status_authority=True)
    assert legacy == adapter._messages(**args, activity_status_authority=False)
    old_user, new_user = (json.loads(messages[1]["content"]) for messages in (legacy, revised))
    # Every source, private state, capability and request coordinate stays intact.
    old_rule, new_rule = (u.pop("activity_source_rule") for u in (old_user, new_user))
    assert old_user == new_user
    current = new_user["inner_life_snapshot"]["materials"]["current_activities"]
    assert current == snapshot.model_view()["materials"]["current_activities"]
    assert current[0]["source_ref"] == source_ref
    assert current[0]["accepted_intention"]["epistemic_scope"] == (
        "accepted_intention_only_not_embedded_history_or_outcome"
    )
    assert "The only current-activity content you may state is exactly" in old_rule
    assert "The only current-activity content you may state is exactly" not in new_rule
    assert "not that every clause in its intention text happened" in new_rule
    assert "accepted_intention.text remains an intention" in revised[0]["content"]
    assert "use only the exact accepted_intention.text" not in revised[0]["content"]
