"""World-source activity intent through the installed character and Plan chain.

Only the provider boundary is replaced. The public application commits a
sidecar-backed occurrence and processes an observed outcome before presenting
that accepted settlement to the real CharacterInterior structured role.
No accepted settlement, character proposal or Plan is written by this test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.character_interior.production import (
    compose_production_character_interior,
)
from companion_daemon.world_v2.companion_identity import CompanionIdentityFrame
from companion_daemon.world_v2.expression_draft import QQ_NAPCAT_EXPRESSION_CAPABILITIES
from companion_daemon.world_v2.occurrence_content_coordinator import (
    OccurrenceContentCommitRequest,
    OutcomeCandidateContent,
)
from companion_daemon.world_v2.production_turn_application import (
    WorldV2TurnApplicationConfig,
    build_sqlite_world_v2_turn_application,
)
from companion_daemon.world_v2.schemas import (
    ClockObservation,
    DueWindow,
    EvidenceRef,
    OutcomeObservation,
    WorldOccurrenceProjection,
)
from test_production_turn_application import _Identities, _Router


WORLD = "world:world-stimulus-life-intent"
ACTOR = "actor:companion"
NOW = datetime(2026, 9, 8, 2, 0, tzinfo=UTC)
OPENED = NOW + timedelta(minutes=1)
CONSIDERED = NOW + timedelta(minutes=2)
INTENTION = "想花两分钟整理一下我接下来要做的事情。"
CANDIDATE = "candidate:world-life-intent:rain"


class _NoExternalActions:
    provider = "platform:offline"

    async def send(self, request):
        raise AssertionError("a private life intention is not an external Action")

    async def lookup(self, **kwargs):
        return None


class _RoleHTTP:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []
        self.stimulus_requests: list[dict[str, object]] = []
        self.source_ref: str | None = None

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        material = json.loads(body["messages"][-1]["content"])
        purpose = material["inner_turn"]["purpose"]
        self.requests.append(body)
        capability = material["capability_manifest"]
        if purpose == "outcome_selection":
            authored = {
                "status": "decision",
                "summary": "我认得这个已经观察到的变化。",
                "attended_source_refs": capability["source_refs"],
                "recall_query": None,
                "proposals": [],
                "decision": {
                    "source_refs": capability["source_refs"],
                    "payload": {
                        "selected_token": CANDIDATE,
                        "adopt_proposed_life_direction": False,
                        "character_life_direction": None,
                    },
                },
            }
        else:
            assert purpose == "world_stimulus_appraisal", purpose
            assert self.source_ref is not None
            self.stimulus_requests.append(body)
            authored = {
                "status": "transition",
                "summary": "我想给接下来的一小段时间做个安排。",
                "attended_source_refs": [self.source_ref],
                "recall_query": None,
                "decision": None,
                "proposals": [
                    {
                        "proposal_type": "world_stimulus_appraisal_result",
                        "decision": "no_change",
                        "brief_rationale": "我选择安排一件自己的事。",
                        "behavior_tendency": "按自己的想法安排。",
                        "stance": "平常地看待这件事。",
                        "display_strategy": "withhold",
                        "confidence": 7200,
                        "meaning_candidates": None,
                        "attribution": None,
                        "severity": None,
                        "expiry": None,
                        "affect_transition": None,
                        "relationship_signal": None,
                        "aspiration_transition": None,
                        "experience_transition": None,
                        "life_intent": {
                            "source_event_ref": self.source_ref,
                            "execution_scope": "self_directed",
                            "intention": INTENTION,
                            "start_after_seconds": 0,
                            "duration_seconds": 120,
                            "importance_bp": 5300,
                        },
                    }
                ],
            }
        name = body["tool_choice"]["function"]["name"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "offline-role",
                                    "type": "function",
                                    "function": {
                                        "name": name,
                                        "arguments": json.dumps(authored, ensure_ascii=False),
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
            },
        )


def _build(path: Path, model: DeepSeekChatModel):
    capabilities = QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
        update={"private_turn_state_mode": "required"}
    )
    interior = compose_production_character_interior(
        flash_model=model,
        thinking_model=None,
        source_closure_model=None,
        report_relative_source_closure_model=None,
        source_closure_reselection_lane=None,
        expression_episode_observer_model=None,
        flash_model_id=model.model,
        thinking_model_id=None,
        expression_capabilities=capabilities,
        identity_frame=CompanionIdentityFrame(companion_name="小满", counterpart_name="用户"),
    )
    return build_sqlite_world_v2_turn_application(
        path=path,
        config=WorldV2TurnApplicationConfig(
            world_id=WORLD,
            companion_actor_ref=ACTOR,
            reply_target="user:user.1",
            action_pump_owner="pump:world-life-intent",
            character_memory_enabled=False,
            expression_capabilities=capabilities,
        ),
        identities=_Identities(),
        router=_Router(),
        character_interior=interior,
        transport=_NoExternalActions(),
        now=NOW,
    )


def _clock(tick: str, start: datetime, end: datetime) -> ClockObservation:
    return ClockObservation(
        schema_version="world-v2.1",
        tick_id=tick,
        world_id=WORLD,
        logical_time=start,
        created_at=end,
        trace_id="trace:" + tick,
        causation_id="clock:" + tick,
        correlation_id="world-life-intent",
        logical_time_from=start,
        logical_time_to=end,
        reason="test_clock",
    )


async def _accepted_settlement(app):
    await app.advance(_clock("seed", NOW, OPENED))
    candidate = OutcomeCandidateContent(
        candidate_result_ref=CANDIDATE,
        result_id="result:world-life-intent:rain",
        result_payload_ref="payload:world-life-intent:rain",
        result_payload_hash="sha256:" + "a" * 64,
        privacy_class="private",
        content_ref="content:world-life-intent:rain",
        text="一阵短雨已经停了。",
    )
    await app.commit_occurrence(
        OccurrenceContentCommitRequest(
            world_id=WORLD,
            occurrence=WorldOccurrenceProjection(
                occurrence_id="occurrence:world-life-intent:rain",
                entity_revision=1,
                trigger_ref="trigger:world-life-intent:rain",
                participant_refs=(ACTOR,),
                location_ref=None,
                time_window=DueWindow(
                    opens_at=OPENED, closes_at=CONSIDERED + timedelta(minutes=10)
                ),
                candidate_outcome_refs=(CANDIDATE,),
                visibility="private",
                status="committed",
            ),
            candidate_contents=(candidate,),
            change_id="change:world-life-intent:rain",
            transition_id="transition:world-life-intent:rain",
            evidence_refs=(
                EvidenceRef(
                    ref_id="clock:" + OPENED.isoformat(),
                    evidence_type="clock_observation",
                    claim_purpose="current_fact",
                ),
            ),
            logical_time=OPENED,
            created_at=OPENED,
            actor="system:offline-world",
            source="test",
            trace_id="trace:rain",
            causation_id="cause:rain",
            correlation_id="world-life-intent",
        )
    )
    await app.advance(_clock("activate", OPENED, CONSIDERED))
    await app.record_outcome_observation(
        OutcomeObservation(
            schema_version="world-v2.1",
            observation_id="observation:world-life-intent:rain",
            world_id=WORLD,
            logical_time=CONSIDERED,
            created_at=CONSIDERED,
            trace_id="trace:rain-observed",
            causation_id="sensor:rain",
            correlation_id="world-life-intent",
            occurrence_id="occurrence:world-life-intent:rain",
            source_kind="committed_world_event",
            source_refs=("event:trigger:clock:activate",),
            observed_payload_ref="sensor-payload:rain",
            observed_payload_hash="b" * 64,
            observed_at=CONSIDERED,
            confidence_bp=9500,
        )
    )
    await app.drain_background_once()
    evidence = app.export_replay_evidence()
    settlements = [
        row.event for row in evidence.events if row.event.event_type == "WorldOccurrenceSettled"
    ]
    assert len(settlements) == 1
    assert evidence.projection.plans == ()
    return settlements[0]


@pytest.mark.asyncio
async def test_world_stimulus_can_choose_one_plan_without_appraisal(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = _RoleHTTP()
    model = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(provider),
    )
    app = _build(tmp_path / "world-life-intent.sqlite", model)
    try:
        settlement = await _accepted_settlement(app)
        provider.source_ref = settlement.event_id
        await app.drain_background_once()
        assert provider.stimulus_requests
        actual = json.loads(provider.stimulus_requests[0]["messages"][-1]["content"])
        assert actual["capability_manifest"]["payload"].get("world_life_intent") == {
            "contract": "world-life-intent-capability.1",
            "source_event_refs": [settlement.event_id],
            "execution_scope": "self_directed",
        }
        evidence = app.export_replay_evidence()
        assert len(evidence.projection.plans) == 1
        assert evidence.projection.appraisals == ()
        plan = evidence.projection.plans[0]
        assert plan.owner_actor_ref == ACTOR
        assert plan.scheduled_window == DueWindow(
            opens_at=CONSIDERED,
            closes_at=CONSIDERED + timedelta(seconds=120),
        )
        assert plan.status == "planned"
        assert len(provider.stimulus_requests) == 1
    finally:
        app.close()
        await model.aclose()
