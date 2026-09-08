"""Explicit complete-author transport through the public app and real Core."""

from contextlib import asynccontextmanager
from dataclasses import replace
import json

import httpx
import pytest
from world_v2_application import (
    build_sqlite_world_v2_test_application,
    compose_fixture_character_interior,
)

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.character_interior.inbound_author import _InboundCharacterAuthor
from companion_daemon.world_v2.expression_draft import QQ_NAPCAT_EXPRESSION_CAPABILITIES
from companion_daemon.world_v2.proposal_audit_schemas import RecordedModelResultAudit
from companion_daemon.world_v2.proposal_envelope import DecisionProposal, ExpressionPlanPayload
from companion_daemon.world_v2.world_turn_runtime import InboundTurn
from test_character_interior_inbound_author import _request
from test_production_turn_application import NOW, _config, _DeliveredTransport, _Identities, _Router
from test_world_stimulus_life_intent import _http_result


BEATS = ("我想先把自己的想法说完整。", "还有第二句，我也想一起说。")


def _decision(*, invalid=False):
    return {
        "result_kind": "decision",
        "appraisal_draft": {
            "appraise": False,
            "affect": "no_change",
            "brief_rationale": "无需新的持久评价。",
            "behavior_tendency": "按自己的想法回应。",
            "stance": "自然",
            "display_strategy": "直接表达",
            "confidence": 7000,
        },
        "expression_draft": {
            "private_turn_state": {
                "contract": "private-turn-state.1",
                "inner_state_summary": "" if invalid else "我想把两句想法完整表达出来。",
                "attended_source_refs": [],
            },
            "timing_choice": "now",
            "cadence": "conversational",
            "confidence": 8100,
            "beats": [{"modality": "text", "text": text} for text in BEATS],
            "stance": "present",
            "brief_rationale": "完整地表达自己的想法。",
            "world_claims": [],
        },
    }


class _AtomicHTTP:
    def __init__(self, *, invalid_first=False, invalid_always=False, recall_first=False):
        self.requests = []
        self.invalid_first = invalid_first
        self.invalid_always = invalid_always
        self.recall_first = recall_first

    async def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        assert not body.get("stream"), "whole candidate unexpectedly started an incremental stream"
        authored = _decision(
            invalid=self.invalid_always or (self.invalid_first and len(self.requests) == 1),
        )
        if self.recall_first and len(self.requests) == 1:
            authored = {
                "result_kind": "recall",
                "private_turn_state": {
                    "contract": "private-turn-state.1",
                    "inner_state_summary": "我想先确认之前的对话，再表达自己的想法。",
                    "attended_source_refs": [],
                },
                "recall_request": {
                    "query_text": "之前的对话",
                    "memory_kinds": ["episodic", "semantic"],
                    "limit": 4,
                },
            }
        # Supply only the forced transport's explicit null union siblings.
        fields = body["tools"][0]["function"]["parameters"]["properties"]
        authored = {key: authored.get(key) for key in fields}
        return _http_result(body, authored)


@asynccontextmanager
async def _application(tmp_path, monkeypatch, provider):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    model = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(provider),
    )
    capabilities = QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
        update={"private_turn_state_mode": "required"},
    )
    author = _InboundCharacterAuthor(
        flash_model=model,
        expression_capabilities=capabilities,
        require_explicit_authored_decision_fields=True,
        whole_candidate_mode=True,
    )
    app = build_sqlite_world_v2_test_application(
        path=tmp_path / "whole-candidate.sqlite",
        config=replace(
            _config(), expression_capabilities=capabilities, expression_episode_mode="off"
        ),
        identities=_Identities(),
        router=_Router(),
        character_interior=compose_fixture_character_interior(inbound_author=author),
        transport=_DeliveredTransport(),
        now=NOW,
    )
    try:
        yield app, author
    finally:
        app.close()
        await model.aclose()


def _inbound():
    return InboundTurn(
        platform="test",
        platform_user_id="user.1",
        platform_message_id="message:whole-candidate",
        text="你想怎么说？",
        observed_at=NOW,
        trace_id="trace:whole-candidate",
    )


def _model_audits(evidence):
    return tuple(
        RecordedModelResultAudit.model_validate_json(item.event.payload()["audit_json"])
        for item in evidence.events
        if item.event.event_type == "ModelResultRecorded"
    )


def _assert_winner_binding(evidence, *, ordinal, input_tokens, output_tokens):
    (winner,) = (audit for audit in _model_audits(evidence) if audit.outcome == "winner")
    assert winner.status == "proposal_validated"
    assert winner.parent_model_call_id is None
    assert winner.semantic_stream_part is None
    assert winner.usage is not None
    assert (winner.usage.input_tokens, winner.usage.output_tokens) == (input_tokens, output_tokens)
    assert (winner.input_tokens, winner.output_tokens) == (input_tokens, output_tokens)
    lineage = winner.character_interior_lineage
    assert lineage is not None
    assert lineage.author_attempt_ordinal == ordinal
    assert lineage.author_model_call_id == winner.model_call_id
    assert lineage.author_request_hash == "sha256:" + winner.request_hash
    assert any(
        item.model_result_ref == winner.model_result_ref
        for item in evidence.projection.proposal_audits
    )
    return winner


@pytest.mark.asyncio
async def test_normal_core_turn_authors_all_beats_once_and_binds_original_usage(
    tmp_path, monkeypatch
):
    provider = _AtomicHTTP()
    async with _application(tmp_path, monkeypatch, provider) as (app, _author):
        outcome = await app.respond(_inbound())
        # The same public Observation cannot create another author invocation.
        repeated = await app.respond(_inbound())
        evidence = app.export_replay_evidence()

    assert outcome.status == repeated.status == "action_authorized"
    assert len(provider.requests) == 1
    assert provider.requests[0]["tool_choice"]["function"]["name"] == "character_inbound_initial_v1"
    assert len(evidence.projection.actions) == len(BEATS)
    assert tuple(item.text for item in evidence.projection.stored_message_payloads) == BEATS
    _assert_winner_binding(evidence, ordinal=0, input_tokens=100, output_tokens=100)


@pytest.mark.asyncio
async def test_stream_capable_provider_core_correction_stays_atomic_and_keeps_all_beats(
    tmp_path,
    monkeypatch,
):
    provider = _AtomicHTTP(invalid_first=True)
    async with _application(tmp_path, monkeypatch, provider) as (app, _author):
        outcome = await app.respond(_inbound())
        evidence = app.export_replay_evidence()
    assert len(provider.requests) == 2
    assert all(not body.get("stream") for body in provider.requests), [
        (body.get("stream"), body["tool_choice"]) for body in provider.requests
    ]
    assert outcome.status == "action_authorized", outcome
    assert len(evidence.projection.actions) == len(BEATS)
    assert tuple(item.text for item in evidence.projection.stored_message_payloads) == BEATS
    original, corrected = (
        json.loads(body["messages"][-1]["content"]) for body in provider.requests
    )
    for field in (
        "capsule_id",
        "trigger_ref",
        "evaluated_world_revision",
        "evaluated_deliberation_revision",
        "evaluated_ledger_sequence",
        "attempt_id",
    ):
        assert corrected["request"][field] == original["request"][field]
    corrected_snapshot = dict(corrected["inner_life_snapshot"])
    correction = corrected_snapshot.pop("role_result_correction")
    assert correction["task"] == "return_one_fresh_complete_role_result"
    assert correction["failure_code"] == "role_result_schema_invalid"
    assert corrected_snapshot == original["inner_life_snapshot"]
    # This verifies the winning call only. The pre-existing conversion to a
    # Core contract error does not retain the first invalid call's usage.
    _assert_winner_binding(evidence, ordinal=1, input_tokens=100, output_tokens=100)


@pytest.mark.asyncio
async def test_invalid_correction_stops_after_two_atomic_calls_without_action(
    tmp_path, monkeypatch
):
    provider = _AtomicHTTP(invalid_always=True)
    async with _application(tmp_path, monkeypatch, provider) as (app, _author):
        outcome = await app.respond(_inbound())
        evidence = app.export_replay_evidence()

    assert len(provider.requests) == 2
    assert all(not body.get("stream") for body in provider.requests)
    assert outcome.status == "deferred"
    assert evidence.projection.actions == ()
    assert evidence.projection.stored_message_payloads == ()
    assert all(audit.outcome != "winner" for audit in _model_audits(evidence))


@pytest.mark.asyncio
async def test_core_recall_final_keeps_the_complete_atomic_contract(tmp_path, monkeypatch):
    provider = _AtomicHTTP(recall_first=True)
    async with _application(tmp_path, monkeypatch, provider) as (app, _author):
        outcome = await app.respond(_inbound())
        evidence = app.export_replay_evidence()

    assert outcome.status == "action_authorized", outcome
    assert [body["tool_choice"]["function"]["name"] for body in provider.requests] == [
        "character_inbound_initial_v1",
        "character_inbound_after_recall_v1",
    ]
    assert all(not body.get("stream") for body in provider.requests)
    final_request = json.loads(provider.requests[1]["messages"][-1]["content"])
    final_context = json.loads(final_request["request"]["model_content_json"])
    assert final_context["recall_control"] == {"remaining_character_pulls": 0}
    assert len(evidence.projection.actions) == len(BEATS)
    assert tuple(item.text for item in evidence.projection.stored_message_payloads) == BEATS
    winner = _assert_winner_binding(evidence, ordinal=0, input_tokens=200, output_tokens=200)
    assert winner.recall_trace is not None
    assert winner.recall_trace.mode == "character_pull"
    (recall,) = (
        audit
        for audit in _model_audits(evidence)
        if audit.route.reason_code == "author_candidate.recall_control_transfer.control_transfer"
    )
    assert recall.status == "candidate_returned"
    assert recall.outcome == "returned"
    assert recall.parent_model_call_id is None
    assert recall.model_call_id != winner.model_call_id
    assert recall.request_hash != winner.request_hash
    assert recall.usage is not None
    assert (recall.usage.input_tokens, recall.usage.output_tokens) == (100, 100)


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ("propose", "propose_stream_head"))
async def test_complete_and_compatibility_entrypoints_return_one_full_nonstream_output(
    tmp_path,
    monkeypatch,
    entrypoint,
):
    provider = _AtomicHTTP()
    request = _request(revision=3, call="call:whole-candidate-entrypoint")
    async with _application(tmp_path, monkeypatch, provider) as (_app, author):
        assert not author.stream_provider_available(request)
        output = await getattr(author, entrypoint)(request)
        # Compatibility callers cannot discover an unconsumed tail or ask a
        # second author after receiving the complete atomic candidate.
        with pytest.raises(RuntimeError, match="stream continuation is unavailable"):
            await author.propose_stream_tail(request)

    assert len(provider.requests) == 1
    assert provider.requests[0]["tool_choice"]["function"]["name"] == "character_inbound_initial_v1"
    assert output.semantic_stream_part is None
    assert output.provider_parent_model_call_id is None
    assert output.physical_provider_audits == ()
    assert output.winning_model_call_id
    assert output.winning_request_hash
    assert output.usage is not None
    assert (output.input_tokens, output.output_tokens) == (100, 100)
    assert (output.usage.input_tokens, output.usage.output_tokens) == (100, 100)
    proposal = DecisionProposal.model_validate_json(json.dumps(output.raw_proposal))
    (change,) = (
        change
        for change in proposal.proposed_changes
        if change.kind == "expression_plan_transition"
    )
    plan = ExpressionPlanPayload.model_validate_json(change.payload.canonical_json)
    assert tuple(beat.inline_text for beat in plan.beat_drafts) == BEATS
