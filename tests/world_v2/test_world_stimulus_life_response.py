"""A new consequence receives one explicit reading in the installed role call.

Only the HTTP provider is replaced. Occurrences, observations, selection,
settlement, role audit and response acceptance use the public SQLite app.
"""

from __future__ import annotations

from datetime import timedelta
import json

import httpx
import pytest

from companion_daemon.world_v2.occurrence_content_coordinator import (
    OccurrenceContentCommitRequest,
    OutcomeCandidateContent,
)
from companion_daemon.world_v2.schemas import (
    DueWindow,
    EvidenceRef,
    OutcomeObservation,
    WorldOccurrenceProjection,
)
from test_world_stimulus_life_intent import (
    ACTOR,
    CANDIDATE,
    WORLD,
    _RoleHTTP,
    _build,
    _clock,
    _http_result,
    _model,
)


class _ResponseHTTP(_RoleHTTP):
    def __init__(self, *, text=None, fault=None, intent="null", appraise=False):
        super().__init__(intent=intent, appraise=appraise)
        self.text = text
        self.fault = fault

    async def __call__(self, request):
        body = json.loads(request.content)
        material = json.loads(body["messages"][-1]["content"])
        if material.get("inner_turn", {}).get("purpose") != "world_stimulus_appraisal":
            return await super().__call__(request)
        capability = material["capability_manifest"]
        self.requests.append(body)
        self.stimulus_requests.append(body)
        if self.fault == "provider_failure":
            raise httpx.ReadError("offline response author failed", request=request)
        response_capability = capability["payload"].get("world_life_response")
        refs = response_capability["source_event_refs"] if response_capability else []
        chosen = [{"source_event_ref": ref, "response_text": self.text} for ref in refs]
        proposal = {
            "proposal_type": "world_stimulus_appraisal_result",
            "decision": "no_change",
            "brief_rationale": "我自己看待这个变化。",
            "behavior_tendency": "由我自己决定。",
            "stance": "平常地看待。",
            "display_strategy": "withhold",
            "confidence": 7000,
            "meaning_candidates": None,
            "attribution": None,
            "severity": None,
            "expiry": None,
            "affect_transition": None,
            "relationship_signal": None,
            "aspiration_transition": None,
            "experience_transition": None,
            "life_intent": None,
        }
        if response_capability is not None:
            proposal["life_responses"] = chosen
        if self.intent == "choose":
            from test_world_stimulus_life_intent import INTENTION

            proposal["life_intent"] = {
                "source_event_ref": capability["payload"]["world_life_intent"]["source_event_refs"][
                    0
                ],
                "execution_scope": "self_directed",
                "intention": INTENTION,
                "start_after_seconds": 0,
                "duration_seconds": 120,
                "importance_bp": 5300,
            }
        if self.appraise:
            proposal.update(
                decision="activate",
                attribution="situation",
                severity=2500,
                meaning_candidates=[{"meaning": "我愿意承认这个变化。", "confidence": 7000}],
            )
        if self.fault in {"missing", "missing_once"} and (
            self.fault == "missing" or len(self.stimulus_requests) == 1
        ):
            proposal.pop("life_responses", None)
        if self.fault in {"wrong_source", "wrong_once"} and (
            self.fault == "wrong_source" or len(self.stimulus_requests) == 1
        ):
            proposal["life_responses"] = [
                {"source_event_ref": "event:not-offered", "response_text": None}
            ]
        if self.fault == "missing_text":
            proposal["life_responses"] = [{"source_event_ref": ref} for ref in refs]
        if self.fault == "duplicate" and chosen:
            proposal["life_responses"] = chosen + chosen[:1]
        if self.fault == "empty":
            proposal["life_responses"] = []
        return _http_result(
            body,
            {
                "status": "transition"
                if refs or self.intent == "choose" or self.appraise
                else "no_change",
                "summary": "我按自己的理解回应。",
                "attended_source_refs": capability["source_refs"],
                "recall_query": None,
                "decision": None,
                "proposals": [proposal],
            },
        )


async def _settled(app, *, name="rain", current=True):
    """Author a typed candidate before public acceptance; never alter accepted bytes."""
    start = app.export_replay_evidence().projection.logical_time
    assert start is not None
    opened = start + timedelta(minutes=1)
    considered = opened + timedelta(minutes=1)
    await app.advance(_clock("seed-" + name, start, opened))
    from companion_daemon.world_v2.life_content_store import life_content_payload_hash

    text = (
        json.dumps(
            {"contract": "world-consequence.2", "environment_text": "一阵短雨已经停了。"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if current
        else "一阵短雨已经停了。"
    )
    candidate = OutcomeCandidateContent(
        candidate_result_ref=CANDIDATE,
        result_id="result:response:" + name,
        result_payload_ref="payload:response:" + name,
        result_payload_hash=life_content_payload_hash(text),
        privacy_class="private",
        content_ref="content:response:" + name,
        text=text,
        **({"result_contract": "world-consequence.2"} if current else {}),
    )
    await app.commit_occurrence(
        OccurrenceContentCommitRequest(
            world_id=WORLD,
            occurrence=WorldOccurrenceProjection(
                occurrence_id="occurrence:response:" + name,
                entity_revision=1,
                trigger_ref="trigger:response:" + name,
                participant_refs=(ACTOR,),
                location_ref=None,
                time_window=DueWindow(
                    opens_at=opened, closes_at=considered + timedelta(minutes=10)
                ),
                candidate_outcome_refs=(CANDIDATE,),
                visibility="private",
                status="committed",
            ),
            candidate_contents=(candidate,),
            change_id="change:response:" + name,
            transition_id="transition:response:" + name,
            evidence_refs=(
                EvidenceRef(
                    ref_id="clock:" + opened.isoformat(),
                    evidence_type="clock_observation",
                    claim_purpose="current_fact",
                ),
            ),
            logical_time=opened,
            created_at=opened,
            actor="system:offline-world",
            source="test",
            trace_id="trace:" + name,
            causation_id="cause:" + name,
            correlation_id="response-fixture",
        )
    )
    await app.advance(_clock("activate-" + name, opened, considered))
    await app.record_outcome_observation(
        OutcomeObservation(
            schema_version="world-v2.1",
            observation_id="observation:response:" + name,
            world_id=WORLD,
            logical_time=considered,
            created_at=considered,
            trace_id="trace:" + name,
            causation_id="sensor:" + name,
            correlation_id="response-fixture",
            occurrence_id="occurrence:response:" + name,
            source_kind="committed_world_event",
            source_refs=("event:trigger:clock:activate-" + name,),
            observed_payload_ref="sensor:" + name,
            observed_payload_hash="b" * 64,
            observed_at=considered,
            confidence_bp=9500,
        )
    )
    await app.drain_background_once()
    settlements = [
        row.event
        for row in app.export_replay_evidence().events
        if row.event.event_type == "WorldOccurrenceSettled"
        and row.event.payload()["occurrence_id"] == "occurrence:response:" + name
    ]
    assert len(settlements) == 1
    return settlements[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [None, "我有点意外，想先安静地想一想。"])
async def test_new_consequence_records_explicit_response_in_same_public_role_call(
    tmp_path, monkeypatch, text
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = _ResponseHTTP(text=text)
    model = _model(provider)
    app = _build(tmp_path / "response.sqlite", model)
    try:
        source = await _settled(app)
        before = app.export_replay_evidence().projection
        await app.drain_background_once()
        evidence = app.export_replay_evidence()
        responses = [
            row.event
            for row in evidence.events
            if row.event.event_type == "CharacterLifeResponseRecorded"
        ]
        assert len(provider.stimulus_requests) == len(responses) == 1
        material = json.loads(provider.stimulus_requests[0]["messages"][-1]["content"])
        assert material["capability_manifest"]["payload"]["world_life_response"] == {
            "contract": "world-life-response-capability.1",
            "source_event_refs": [source.event_id],
        }
        proposal_schema = provider.stimulus_requests[0]["tools"][0]["function"]["parameters"][
            "anyOf"
        ][0]["properties"]["proposals"]["items"]
        assert "life_responses" in proposal_schema["required"]
        response_schema = proposal_schema["properties"]["life_responses"]
        assert response_schema["minItems"] == response_schema["maxItems"] == 1
        assert "response_text" in response_schema["items"]["required"]
        assert response_schema["items"]["properties"]["source_event_ref"]["enum"] == [
            source.event_id
        ]
        response = responses[0].payload()
        assert response["response_text"] == text
        assert response["origin"]["source_event_ref"] == source.event_id
        assert response["origin"]["source_payload_hash"] == source.payload_hash
        assert evidence.projection.plans == before.plans
        assert evidence.projection.appraisals == before.appraisals
        assert before.experiences == ()
        assert len(evidence.projection.experiences) == 1
        experience = evidence.projection.experiences[0]
        assert experience.authority_contract_version == "experience.2"
        binding = experience.values.source_bindings[0]
        assert binding.source_kind == "world_life_response"
        assert binding.response.response_text == text
        assert binding.response_event_ref == responses[0].event_id
        assert binding.settlement.authority_event_ref == source.event_id
        await app.drain_background_once()
        assert len(provider.stimulus_requests) == 1
    finally:
        await app.aclose()
        await model.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault,accepted",
    [
        ("missing_once", True),
        ("wrong_once", True),
        ("missing", False),
        ("wrong_source", False),
        ("missing_text", False),
        ("duplicate", False),
        ("empty", False),
    ],
)
async def test_exact_same_role_correction_cannot_supply_an_implicit_null(
    tmp_path, monkeypatch, fault, accepted
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = _ResponseHTTP(fault=fault)
    model = _model(provider)
    app = _build(tmp_path / "correction.sqlite", model)
    try:
        source = await _settled(app)
        before = app.export_replay_evidence().projection
        await app.drain_background_once()
        evidence = app.export_replay_evidence()
        responses = [
            row.event
            for row in evidence.events
            if row.event.event_type == "CharacterLifeResponseRecorded"
        ]
        assert len(provider.stimulus_requests) == 2
        assert len(responses) == int(accepted)
        assert evidence.projection.plans == before.plans
        assert evidence.projection.appraisals == before.appraisals
        assert before.experiences == ()
        assert len(evidence.projection.experiences) == int(accepted)
        if accepted:
            binding = evidence.projection.experiences[0].values.source_bindings[0]
            assert binding.response_event_ref == responses[0].event_id
            assert binding.response.response_text is None
        if accepted:
            assert responses[0].payload()["response_text"] is None
            assert responses[0].payload()["origin"]["source_event_ref"] == source.event_id
        correction = json.dumps(provider.stimulus_requests[1], ensure_ascii=False)
        assert "life_responses" in correction
        assert "source_event_ref" in correction
    finally:
        await app.aclose()
        await model.aclose()


@pytest.mark.asyncio
async def test_legacy_settlement_keeps_its_tool_shape_without_response_capability(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = _ResponseHTTP(intent="choose")
    model = _model(provider)
    app = _build(tmp_path / "legacy.sqlite", model)
    try:
        await _settled(app, current=False)
        await app.drain_background_once()
        evidence = app.export_replay_evidence()
        assert len(provider.stimulus_requests) == 1
        request = provider.stimulus_requests[0]
        assert "life_responses" not in json.dumps(request, ensure_ascii=False)
        assert "world_life_response" not in json.dumps(request, ensure_ascii=False)
        assert not any(
            row.event.event_type == "CharacterLifeResponseRecorded" for row in evidence.events
        )
        audit = next(
            x
            for x in evidence.projection.proposal_audits
            if '"kind":"world_life_intent"' in x.proposal_json
        )
        assert json.loads(audit.proposal_json)["schema_registry_version"] == "world-v2-proposals.4"
    finally:
        await app.aclose()
        await model.aclose()


@pytest.mark.asyncio
async def test_response_is_recorded_before_independent_appraisal_and_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = _ResponseHTTP(text="我对此有自己的感受。", intent="choose", appraise=True)
    model = _model(provider)
    app = _build(tmp_path / "siblings.sqlite", model)
    try:
        source = await _settled(app)
        await app.drain_background_once()
        evidence = app.export_replay_evidence()
        types = [row.event.event_type for row in evidence.events]
        response = next(
            row.event
            for row in evidence.events
            if row.event.event_type == "CharacterLifeResponseRecorded"
        )
        assert types.index("CharacterLifeResponseRecorded") < types.index("ActivityPlanned")
        assert types.index("CharacterLifeResponseRecorded") < types.index("AppraisalAccepted")
        assert len(provider.stimulus_requests) == 1
        assert response.payload()["origin"]["source_event_ref"] == source.event_id
        assert len(evidence.projection.plans) == 1
        assert len(evidence.projection.appraisals) == 1
    finally:
        await app.aclose()
        await model.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_first", [False, True])
async def test_cold_restart_resumes_original_null_after_acceptance_crash(
    tmp_path, monkeypatch, terminal_first
):
    from companion_daemon.world_v2.character_life_response_runtime import (
        CharacterLifeResponseRuntime,
    )
    from companion_daemon.world_v2.errors import ConcurrencyConflict

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "restart.sqlite"
    provider = _ResponseHTTP(appraise=terminal_first)
    model = _model(provider)
    app = _build(path, model)
    try:
        source = await _settled(app)
        with monkeypatch.context() as local:

            def interrupted(*args, **kwargs):
                raise ConcurrencyConflict("offline stop before response append")

            local.setattr(CharacterLifeResponseRuntime, "accept", interrupted)
            await app.drain_background_once()
        before = app.export_replay_evidence()
        assert len(provider.stimulus_requests) == 1
        assert not any(
            row.event.event_type == "CharacterLifeResponseRecorded" for row in before.events
        )
        authored = next(
            x
            for x in before.projection.proposal_audits
            if '"kind":"world_life_response"' in x.proposal_json
        )
    finally:
        await app.aclose()
        await model.aclose()
    if terminal_first:
        from companion_daemon.world_v2.accepted_ledger_batch import AcceptedLedgerBatchIssuer
        from companion_daemon.world_v2.appraisal_proposal_worker import AppraisalProposalWorker
        from companion_daemon.world_v2.appraisal_proposal_compiler import AppraisalProposalCompiler
        from companion_daemon.world_v2.appraisal_acceptance_runtime import (
            AppraisalAcceptanceRuntime,
        )
        from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
        from companion_daemon.world_v2.schemas import ProjectionCursor
        from test_world_life_intent_runtime import _cursor

        issuer = AcceptedLedgerBatchIssuer()
        ledger = SQLiteWorldLedger(path=path, world_id=WORLD, accepted_batch_issuer=issuer)
        try:
            commit = ledger.lookup_event_commit(authored.event_ref)[1]
            worker = AppraisalProposalWorker(
                compiler=AppraisalProposalCompiler(
                    ledger=ledger, world_appraisal_subject_ref=ACTOR
                ),
                acceptance=AppraisalAcceptanceRuntime(ledger=ledger, batch_issuer=issuer),
                actor="worker:world-v2:inner-state-settlement",
            )
            assert (
                worker.process_rebased(
                    world_id=WORLD,
                    proposal_id=authored.proposal_id,
                    audit_cursor=ProjectionCursor(
                        world_revision=commit.world_revision,
                        deliberation_revision=commit.deliberation_revision,
                        ledger_sequence=commit.ledger_sequence,
                    ),
                    current_cursor=_cursor(ledger),
                ).status
                == "accepted"
            )
            assert any(
                p.state == "terminal" and p.source_evidence_ref == source.event_id
                for p in ledger.project().trigger_processes
            )
        finally:
            ledger.close()
    provider = _ResponseHTTP(fault="provider_failure")
    model = _model(provider)
    app = _build(path, model)
    try:
        await app.drain_background_once()
        after = app.export_replay_evidence()
        responses = [
            row.event
            for row in after.events
            if row.event.event_type == "CharacterLifeResponseRecorded"
        ]
        assert len(responses) == 1
        assert provider.stimulus_requests == []
        value = responses[0].payload()
        assert value["response_text"] is None
        assert value["origin"]["proposal_id"] == authored.proposal_id
        assert value["origin"]["source_event_ref"] == source.event_id
        assert after.projection.model_result_audits == before.projection.model_result_audits
        await app.drain_background_once()
        assert provider.stimulus_requests == []
        assert [
            row.event
            for row in app.export_replay_evidence().events
            if row.event.event_type == "CharacterLifeResponseRecorded"
        ] == responses
    finally:
        await app.aclose()
        await model.aclose()


@pytest.mark.asyncio
async def test_coalesced_current_and_legacy_sources_require_only_the_current_set(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = _ResponseHTTP(text="我想先把这两个变化放在心里。")
    model = _model(provider)
    app = _build(tmp_path / "mixed.sqlite", model)
    try:
        first = await _settled(app)
        second = await _settled(app, name="second")
        legacy = await _settled(app, name="legacy", current=False)
        assert provider.stimulus_requests == []
        await app.drain_background_once()
        evidence = app.export_replay_evidence()
        assert len(provider.stimulus_requests) == 1
        material = json.loads(provider.stimulus_requests[0]["messages"][-1]["content"])
        manifest = material["capability_manifest"]
        assert set(manifest["source_refs"]) == {first.event_id, second.event_id, legacy.event_id}
        assert manifest["payload"]["world_life_response"]["source_event_refs"] == sorted(
            [first.event_id, second.event_id]
        )
        responses = [
            row.event
            for row in evidence.events
            if row.event.event_type == "CharacterLifeResponseRecorded"
        ]
        assert {event.payload()["origin"]["source_event_ref"] for event in responses} == {
            first.event_id,
            second.event_id,
        }
        assert len({event.payload()["origin"]["model_call_id"] for event in responses}) == 1
    finally:
        await app.aclose()
        await model.aclose()
