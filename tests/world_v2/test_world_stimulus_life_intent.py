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
from companion_daemon.world_v2.life_events import ActivityPlannedPayload
from companion_daemon.world_v2.proposal_audit_schemas import RecordedModelResultAudit
from companion_daemon.world_v2.production_turn_application import (
    LifeEcologyComposition,
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
from companion_daemon.world_v2.world_turn_runtime import InboundTurn
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
    def __init__(self, *, intent="choose", appraise=False) -> None:
        self.requests: list[dict[str, object]] = []
        self.stimulus_requests: list[dict[str, object]] = []
        self.source_ref: str | None = None
        self.intent = intent
        self.appraise = appraise
        self.lifecycle_requests: list[dict[str, object]] = []
        self.chat_requests: list[dict[str, object]] = []
        self.lifecycle_choice = "start"

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        material = json.loads(body["messages"][-1]["content"])
        purpose = material.get("inner_turn", {}).get("purpose")
        self.requests.append(body)
        if purpose is None:
            assert "inner_life_snapshot" in material
            self.chat_requests.append(body)
            return _http_result(
                body,
                {
                    "result_kind": "reply_only",
                    "payload_json": json.dumps(
                        {
                            "messages": ["嗯，我在听。"],
                            "meaning_of_this": "我看见了这次询问。",
                            "my_state": "平静。",
                            "world_claims": [],
                        },
                        ensure_ascii=False,
                    ),
                },
            )
        capability = material["capability_manifest"]
        if purpose == "activity_lifecycle_choice":
            self.lifecycle_requests.append(body)
            summary = (
                "begin an abstract planned activity"
                if self.lifecycle_choice == "start"
                else "let go of an abstract activity" if self.lifecycle_choice == "abandon"
                else "pause the current abstract activity" if self.lifecycle_choice == "pause"
                else "finish the current abstract activity"
            )
            offered = capability["payload"].get("openings", [])
            selected = next((x for x in offered if x["safe_summary"].startswith(summary)), None)
            authored = {
                "status": "decision",
                "summary": "我选择这个活动变化。",
                "attended_source_refs": [],
                "recall_query": None,
                "proposals": [],
                "decision": {
                    "source_refs": capability["source_refs"],
                    "payload": (
                        {"decision": "select", "selected_token": selected["opening_token"]}
                        if selected
                        else {"decision": "no_op"}
                    ),
                },
            }
        elif purpose == "outcome_selection":
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
            if self.intent == "provider_failure":
                raise httpx.ReadError("offline injected provider read failure", request=request)
            authored = {
                "status": "no_change"
                if self.intent == "null" and not self.appraise
                else "transition",
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
            proposal = authored["proposals"][0]
            if self.intent == "null":
                proposal["life_intent"] = None
            elif self.intent == "invalid" or (
                self.intent == "invalid_then_choose" and len(self.stimulus_requests) == 1
            ):
                proposal["life_intent"]["source_event_ref"] = "event:not-presented"
            if self.appraise:
                proposal.update(
                    decision="activate",
                    meaning_candidates=[
                        {"meaning": "我觉得接下来可以慢慢安排。", "confidence": 7200}
                    ],
                    attribution="situation",
                    severity=2500,
                )
        return _http_result(body, authored)


def _http_result(body, authored):
    if body.get("tools", [{}])[0].get("function", {}).get("name") in {
        "character_role_world_stimulus_appraisal_v2", "character_role_world_stimulus_appraisal_v3",
        "character_role_world_stimulus_appraisal_v4",
        "character_role_world_stimulus_appraisal_v5",
        "character_role_world_stimulus_appraisal_v6",
    }:
        authored = {"result": authored}
    name = body["tool_choice"]["function"]["name"]
    tool = {
        "tool_calls": [
            {
                "index": 0,
                "id": "offline-role",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(authored, ensure_ascii=False)},
            }
        ]
    }
    usage = {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200}
    if body.get("stream"):
        frames = [
            {"choices": [{"delta": tool}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            {"choices": [], "usage": usage},
        ]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="".join("data: " + json.dumps(frame) + "\n\n" for frame in frames)
            + "data: [DONE]\n\n",
        )
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": tool,
                }
            ],
            "usage": usage,
        },
    )


def _build(path: Path, model: DeepSeekChatModel, *, ecology=False, background_budget_paused=None, reviewed_prehistory=None):
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
            background_budget_paused=background_budget_paused,
            reviewed_prehistory=reviewed_prehistory,
            expression_capabilities=capabilities,
            life_ecology=LifeEcologyComposition.production_v1() if ecology else None,
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
        _assert_plan_origin(evidence, settlement, provider)
    finally:
        app.close()
        await model.aclose()


def _model(provider):
    return DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(provider),
    )


def _assert_plan_origin(evidence, settlement, provider):
    planned = [row.event for row in evidence.events if row.event.event_type == "ActivityPlanned"]
    assert len(planned) == 1
    payload = ActivityPlannedPayload.model_validate_json(planned[0].payload_json)
    origin = payload.world_intent_origin
    source = next(
        x
        for x in evidence.projection.committed_world_event_refs
        if x.event_id == settlement.event_id
    )
    assert origin.source_event_ref == settlement.event_id
    assert origin.source_world_revision == source.world_revision
    assert origin.source_payload_hash == settlement.payload_hash
    assert origin.selected_at == CONSIDERED
    proposal = next(
        x for x in evidence.projection.proposal_audits if x.proposal_id == origin.proposal_id
    )
    assert (origin.proposal_event_ref, origin.proposal_payload_hash, origin.proposal_hash) == (
        proposal.event_ref,
        proposal.event_payload_hash,
        proposal.proposal_hash,
    )
    model_result = next(
        x
        for x in evidence.projection.model_result_audits
        if x.model_result_ref == origin.model_result_ref
    )
    assert origin.model_result_payload_hash == model_result.event_payload_hash
    assert origin.model_call_id == model_result.model_call_id
    audit = RecordedModelResultAudit.model_validate_json(model_result.audit_json)
    lineage = audit.character_interior_lineage
    assert lineage.purpose == "world_stimulus_appraisal"
    assert lineage.causal_actor_ref == ACTOR and lineage.causal_world_id == WORLD
    assert settlement.event_id in lineage.causal_source_refs
    assert (
        origin.inner_turn_id,
        origin.opportunity_ref,
        origin.snapshot_id,
        origin.snapshot_hash,
    ) == (
        lineage.inner_turn_id,
        lineage.opportunity_ref,
        lineage.snapshot_id,
        lineage.snapshot_hash,
    )
    actual = json.loads(provider.stimulus_requests[-1]["messages"][-1]["content"])
    assert actual["inner_turn"]["inner_turn_id"] == origin.inner_turn_id
    return payload


@pytest.mark.asyncio
@pytest.mark.parametrize("appraise", [False, True])
async def test_null_world_intent_does_not_create_a_plan(tmp_path, monkeypatch, appraise):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = _RoleHTTP(intent="null", appraise=appraise)
    model = _model(provider)
    app = _build(tmp_path / "null.sqlite", model)
    try:
        settlement = await _accepted_settlement(app)
        provider.source_ref = settlement.event_id
        await app.drain_background_once()
        evidence = app.export_replay_evidence()
        assert len(provider.stimulus_requests) == 1
        assert evidence.projection.plans == ()
        assert len(evidence.projection.appraisals) == int(appraise)
        assert not any(row.event.event_type == "ActivityPlanned" for row in evidence.events)
    finally:
        app.close()
        await model.aclose()


@pytest.mark.asyncio
async def test_invalid_world_intent_source_gets_one_same_role_correction(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = _RoleHTTP(intent="invalid_then_choose")
    model = _model(provider)
    app = _build(tmp_path / "corrected.sqlite", model)
    try:
        settlement = await _accepted_settlement(app)
        provider.source_ref = settlement.event_id
        await app.drain_background_once()
        assert len(provider.stimulus_requests) == 2
        first, corrected = [
            json.loads(body["messages"][-1]["content"]) for body in provider.stimulus_requests
        ]
        assert corrected["inner_turn"] == first["inner_turn"]
        assert corrected["capability_manifest"] == first["capability_manifest"]
        assert corrected["correction"]["ordinal"] == 1
        detail = corrected["correction"]["failure_detail"]
        assert "source" in detail.lower(), detail
        evidence = app.export_replay_evidence()
        assert len(evidence.projection.plans) == 1
        assert evidence.projection.appraisals == ()
        _assert_plan_origin(evidence, settlement, provider)
    finally:
        app.close()
        await model.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", ["invalid", "provider_failure"])
async def test_technical_failure_never_becomes_a_world_plan(tmp_path, monkeypatch, intent):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = _RoleHTTP(intent=intent)
    model = _model(provider)
    app = _build(tmp_path / "technical.sqlite", model)
    try:
        settlement = await _accepted_settlement(app)
        provider.source_ref = settlement.event_id
        outcome = await app.drain_background_once()
        assert outcome.work_status == "technical_failure"
        assert 1 <= len(provider.stimulus_requests) <= 2
        if intent == "invalid":
            assert len(provider.stimulus_requests) == 2
        evidence = app.export_replay_evidence()
        assert evidence.projection.plans == () and evidence.projection.appraisals == ()
        assert not any(row.event.event_type == "ActivityPlanned" for row in evidence.events)
        process = next(
            x
            for x in evidence.projection.trigger_processes
            if x.source_evidence_ref == settlement.event_id
        )
        assert process.state != "terminal"
    finally:
        app.close()
        await model.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("appraise", [False, True])
async def test_same_role_plan_and_optional_appraisal_cold_restart_is_effect_once(
    tmp_path,
    monkeypatch,
    appraise,
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = _RoleHTTP(appraise=appraise)
    model = _model(provider)
    path = tmp_path / "restart.sqlite"
    app = _build(path, model)
    try:
        settlement = await _accepted_settlement(app)
        provider.source_ref = settlement.event_id
        await app.drain_background_once()
        before = app.export_replay_evidence()
        _assert_plan_origin(before, settlement, provider)
        assert len(before.projection.appraisals) == int(appraise)
        assert len(provider.stimulus_requests) == 1
        app.close()
        app = _build(path, model)
        assert app.export_replay_evidence().projection == before.projection
        await app.advance(_clock("restart", CONSIDERED, CONSIDERED + timedelta(minutes=1)))
        for _ in range(3):
            await app.drain_background_once()
        after = app.export_replay_evidence()
        assert after.projection.plans == before.projection.plans
        assert after.projection.appraisals == before.projection.appraisals
        _assert_plan_origin(after, settlement, provider)
        assert len(provider.stimulus_requests) == 1
    finally:
        app.close()
        await model.aclose()


@pytest.mark.asyncio
async def test_installed_ecology_executes_world_plan_and_next_chat_reads_its_exact_state(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = _RoleHTTP()
    model = _model(provider)
    path = tmp_path / "installed-ecology.sqlite"
    app = _build(path, model, ecology=True)
    try:
        settlement = await _accepted_settlement(app)
        provider.source_ref = settlement.event_id
        await app.drain_background_once()
        plan = app.export_replay_evidence().projection.plans[0]
        started_at = CONSIDERED + timedelta(seconds=1)
        await app.tick(
            tick_id="start",
            logical_time_from=CONSIDERED,
            logical_time_to=started_at,
            observed_at=started_at,
            trace_id="trace:start",
            causation_id="clock:start",
            correlation_id="world-life-intent",
            reason="test_clock",
        )
        active = app.export_replay_evidence().projection.plans[0]
        assert active.plan_id == plan.plan_id and active.status == "active"
        assert provider.lifecycle_requests
        lifecycle = json.loads(provider.lifecycle_requests[0]["messages"][-1]["content"])
        assert INTENTION in json.dumps(lifecycle, ensure_ascii=False)
        await app.respond(
            InboundTurn(
                platform="test",
                platform_user_id="user.1",
                platform_message_id="active",
                text="接下来在做什么？",
                observed_at=started_at,
                trace_id="trace:ask-active",
            )
        )
        current_request = json.loads(provider.chat_requests[-1]["messages"][-1]["content"])
        from companion_daemon.world_v2.shared_string_view import CONTRACT as SHARED_STRINGS, unpack_shared_strings

        material = current_request["inner_life_snapshot"]["materials"]
        if material.get("contract") == SHARED_STRINGS:
            material = unpack_shared_strings(material)
        current = material["current_activities"]
        assert len(current) == 1
        assert current[0]["plan_id"] == plan.plan_id
        assert current[0]["accepted_intention"]["text"] == INTENTION
        assert current[0]["source_ref"] == active.authority_origin.accepted_event_ref
        assert active.authority_origin.accepted_event_type == "ActivityStarted"

        # A restart preserves the actual accepted state; the later character
        # lifecycle call still chooses whether to finish it.
        app.close()
        app = _build(path, model, ecology=True)
        provider.lifecycle_choice = "complete"
        ended_at = started_at + timedelta(minutes=5)
        await app.tick(
            tick_id="finish",
            logical_time_from=started_at,
            logical_time_to=ended_at,
            observed_at=ended_at,
            trace_id="trace:finish",
            causation_id="clock:finish",
            correlation_id="world-life-intent",
            reason="test_clock",
        )
        ended = app.export_replay_evidence().projection.plans[0]
        assert ended.plan_id == plan.plan_id and ended.status == "completed"
        await app.respond(
            InboundTurn(
                platform="test",
                platform_user_id="user.1",
                platform_message_id="ended",
                text="刚才那件事还在做吗？",
                observed_at=ended_at,
                trace_id="trace:ask-ended",
            )
        )
        last_request = json.loads(provider.chat_requests[-1]["messages"][-1]["content"])
        materials = last_request["inner_life_snapshot"]["materials"]
        if materials.get("contract") == SHARED_STRINGS:
            materials = unpack_shared_strings(materials)
        assert not materials.get("current_activities")
        recent = materials["recently_ended_activities"]
        assert len(recent) == 1
        assert recent[0]["plan_id"] == plan.plan_id
        assert recent[0]["accepted_intention"]["text"] == INTENTION
        assert recent[0]["source_ref"] == ended.authority_origin.accepted_event_ref
        assert ended.authority_origin.accepted_event_type == "ActivityCompleted"
        assert recent[0]["completion_scope"] == "activity_lifecycle_ended_not_intention_fulfilled"
        assert len(provider.stimulus_requests) == 1
        _assert_plan_origin(app.export_replay_evidence(), settlement, provider)
    finally:
        app.close()
        await model.aclose()
