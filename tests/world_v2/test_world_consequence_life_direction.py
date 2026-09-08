"""The installed Aftermath role sees candidate facts before choosing a direction."""

from datetime import timedelta
import json

import pytest

from companion_daemon.world_v2.life_content_store import life_content_payload_hash
from companion_daemon.world_v2.occurrence_content_coordinator import (
    OccurrenceContentCommitRequest, OutcomeCandidateContent,
)
from companion_daemon.world_v2.schemas import DueWindow, EvidenceRef, WorldOccurrenceProjection
from test_world_stimulus_life_intent import (
    ACTOR, CANDIDATE, NOW, WORLD, _build, _clock, _http_result, _model,
)
from test_world_stimulus_life_response import _ResponseHTTP


ENVIRONMENT = "公开的写作练习征集现在提供每周一次的投稿机会。"
DIRECTION = "想给自己留一段持续写作的时间，先试一阵子。"


class _DirectionHTTP(_ResponseHTTP):
    def __init__(self):
        super().__init__(text=None)
        self.outcome_requests = []

    async def __call__(self, request):
        body = json.loads(request.content)
        material = json.loads(body["messages"][-1]["content"])
        if material.get("inner_turn", {}).get("purpose") != "outcome_selection":
            return await super().__call__(request)
        self.requests.append(body)
        self.outcome_requests.append(material)
        capability = material["capability_manifest"]
        return _http_result(body, {
            "status": "decision", "summary": "我选择继续尝试这件事。",
            "attended_source_refs": capability["source_refs"],
            "recall_query": None, "proposals": [],
            "decision": {
                "source_refs": capability["source_refs"],
                "payload": {
                    "selected_token": CANDIDATE, "adopt_proposed_life_direction": False,
                    "character_life_direction": {
                        "coordinate_ref": "biography:direction.writing",
                        "summary": DIRECTION,
                        "context_tags": ["direction.writing:exploring"],
                        "replaces_context_tag_prefixes": ["direction.writing:"],
                        "privacy_class": "private",
                    },
                },
            },
        })


@pytest.mark.asyncio
@pytest.mark.parametrize("privacy", ["private", "withhold"])
async def test_after_math_keeps_versioned_candidate_facts_and_character_direction_separate(
    tmp_path, monkeypatch, privacy,
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "direction.sqlite"
    provider = _DirectionHTTP()
    model = _model(provider)
    app = _build(path, model, ecology=True)
    opened, active, due = (NOW + timedelta(minutes=i) for i in (1, 2, 3))
    try:
        await app.advance(_clock("direction-seed", NOW, opened))
        text = json.dumps({"contract": "world-consequence.2", "environment_text": ENVIRONMENT},
                          ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        candidate = OutcomeCandidateContent(
            candidate_result_ref=CANDIDATE, result_id="result:direction",
            result_payload_ref="payload:direction", result_payload_hash=life_content_payload_hash(text),
            result_contract="world-consequence.2", privacy_class=privacy,
            content_ref="content:direction", text=text, causal_authority="character_choice",
        )
        await app.commit_occurrence(OccurrenceContentCommitRequest(
            world_id=WORLD,
            occurrence=WorldOccurrenceProjection(
                occurrence_id="occurrence:direction", entity_revision=1,
                trigger_ref="trigger:direction", participant_refs=(ACTOR,), location_ref=None,
                time_window=DueWindow(opens_at=opened, closes_at=due),
                candidate_outcome_refs=(CANDIDATE,), visibility=privacy, status="committed",
            ),
            candidate_contents=(candidate,), change_id="change:direction",
            transition_id="transition:direction",
            evidence_refs=(EvidenceRef(ref_id="clock:" + opened.isoformat(),
                                      evidence_type="clock_observation", claim_purpose="current_fact"),),
            logical_time=opened, created_at=opened, actor="system:offline-world", source="test",
            trace_id="direction", causation_id="direction", correlation_id="direction",
        ))
        await app.advance(_clock("direction-activate", opened, active))
        await app.advance(_clock("direction-due", active, due))
        result = await app.advance_life_ecology_once(
            wake_event_ref="event:trigger:clock:direction-due", trace_id="direction", correlation_id="direction",
        )
        state = app.export_replay_evidence().projection
        if privacy == "withhold":
            assert provider.outcome_requests == []
            assert state.world_occurrences[0].status == "active"
            assert state.biographical_coordinates == state.experiences == ()
            assert result.status == "failed_safe"
            assert result.reason_code == "life_ecology.aftermath_followup_failed"
            return
        assert len(provider.outcome_requests) == 1
        capability = provider.outcome_requests[0]["capability_manifest"]["payload"]
        offered, = capability["candidates"]
        assert "summary" not in offered
        assert offered["world_consequence"]["environment"] == {
            "text": ENVIRONMENT, "truncated": False, "epistemic_scope": "candidate_world_environment",
        }
        assert DIRECTION not in json.dumps(capability, ensure_ascii=False)
        assert state.world_occurrences[0].status == "settled"
        coordinate, = state.biographical_coordinates
        assert coordinate.coordinate_ref == "biography:direction.writing"
        assert state.experiences == ()
        await app.drain_background_once()
        state = app.export_replay_evidence().projection
        assert len(state.experiences) == 1
        assert state.experiences[0].authority_contract_version == "experience.2"
        assert state.experiences[0].values.source_bindings[0].response.response_text is None
        before = app.export_replay_evidence()
        await app.aclose()
        app = _build(path, model, ecology=True)
        assert app.export_replay_evidence() == before
        await app.advance_life_ecology_once(
            wake_event_ref="event:trigger:clock:direction-due", trace_id="direction", correlation_id="direction",
        )
        assert len(provider.outcome_requests) == 1
        assert len(app.export_replay_evidence().projection.biographical_coordinates) == 1
    finally:
        await app.aclose()
        await model.aclose()
