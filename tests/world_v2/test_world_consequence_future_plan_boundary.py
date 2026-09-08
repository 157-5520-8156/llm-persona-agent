"""An existing attempt cannot be attached as the result of a new future Plan."""

import json

import pytest

from companion_daemon.world_v2.ledger import WorldLedger
from companion_daemon.world_v2.life_development_draft import LifeDevelopmentDraftError, parse_world_author_draft
from companion_daemon.world_v2.schemas import ProjectionCursor
from test_life_development_runtime import OWNER, WORLD_ID, _manifest, _seed_clock
from test_world_consequence_producer import _draft


@pytest.mark.parametrize("future_plan,attempt", [(True, True), (True, False), (False, True)])
def test_future_plan_candidates_cannot_borrow_execution_from_a_previous_attempt(future_plan, attempt):
    ledger = WorldLedger.in_memory(world_id=WORLD_ID)
    wake = _seed_clock(ledger)
    state = ledger.project()
    manifest = _manifest(wake, pinned_cursor=ProjectionCursor(
        world_revision=state.world_revision, deliberation_revision=state.deliberation_revision,
        ledger_sequence=state.ledger_sequence,
    )).model_copy(update={"outcome_contract": "world-consequence.2"})
    draft = _draft(wake)
    if future_plan:
        draft["causal_authority"] = "character_choice"
    if attempt:
        # This is a shape-valid binding, not claimed to be admitted execution
        # evidence. The later author-proof gate still checks real World sources.
        draft["outcomes"][0]["world_consequence"]["authorized_attempt_result"] = {
            "text": "原尝试的页面仍未完成载入。",
            "execution_binding": {
                "source_kind": "activity_execution", "source_event_type": "ActivityStarted",
                "actor_ref": OWNER, "source_event_ref": "event:previous-activity",
                "source_world_revision": 1, "source_payload_hash": "a" * 64,
                "privacy_class": "shareable", "plan_id": "plan:previous-attempt",
                "activity_id": "activity:previous-attempt", "plan_entity_revision": 2,
            },
        }
    raw = json.dumps(draft, ensure_ascii=False)
    if future_plan and attempt:
        with pytest.raises(LifeDevelopmentDraftError) as failed:
            parse_world_author_draft(raw=raw, manifest=manifest, logical_time=wake.logical_time)
        assert failed.value.code == "future_plan_execution_result"
        assert failed.value.violations[0]["path"] == "outcomes.0.world_consequence.authorized_attempt_result"
    else:
        parsed = parse_world_author_draft(raw=raw, manifest=manifest, logical_time=wake.logical_time)
        assert parsed.causal_authority == draft["causal_authority"]

