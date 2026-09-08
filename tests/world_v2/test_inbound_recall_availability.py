"""Recall capability presentation at the captured author request boundary."""

import json

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.character_interior.inbound_author import _InboundCharacterAuthor
from companion_daemon.world_v2.expression_draft import qq_expression_capabilities
from test_character_interior_inbound_author import _request
from test_whole_candidate_author import (
    _application,
    _AtomicHTTP,
    _inbound,
    _model_audits,
)


@pytest.mark.asyncio
async def test_direct_author_without_recall_capability_reports_unavailable(monkeypatch):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = _AtomicHTTP()
    model = DeepSeekChatModel(
        "offline-fixture", "https://fixture.invalid", "deepseek-v4-flash",
        thinking_enabled=False, transport=httpx.MockTransport(provider),
    )
    author = _InboundCharacterAuthor(
        flash_model=model, expression_capabilities=qq_expression_capabilities("napcat"),
        require_explicit_authored_decision_fields=True, whole_candidate_mode=True,
    )
    try:
        output = await author.propose(_request(revision=3, call="call:without-recall"))
    finally:
        await model.aclose()

    assert output.winning_model_call_id
    assert len(provider.requests) == 1
    occasion = json.loads(provider.requests[0]["messages"][1]["content"])
    assert occasion["recall_available"] is False


@pytest.mark.asyncio
async def test_core_correction_after_recall_keeps_recall_consumed(tmp_path, monkeypatch):
    provider = _AtomicHTTP(recall_first=True)

    async def respond(request):
        # The provider first chooses Recall, then returns one malformed final
        # private state, then corrects that state under the same pinned turn.
        provider.invalid_always = len(provider.requests) == 1
        return await provider(request)

    async with _application(tmp_path, monkeypatch, respond) as (app, _author):
        outcome = await app.respond(_inbound())
        evidence = app.export_replay_evidence()

    assert outcome.status == "action_authorized", outcome
    assert [body["tool_choice"]["function"]["name"] for body in provider.requests] == [
        "character_inbound_initial_v1",
        "character_inbound_after_recall_v1",
        "character_inbound_after_recall_v1",
    ]
    occasions = [json.loads(body["messages"][1]["content"]) for body in provider.requests]
    assert [occasion["recall_available"] for occasion in occasions] == [True, False, False]
    for occasion in occasions[1:]:
        context = json.loads(occasion["request"]["model_content_json"])
        assert context["recall_control"] == {"remaining_character_pulls": 0}
    for field in (
        "capsule_id", "trigger_ref", "evaluated_world_revision",
        "evaluated_deliberation_revision", "evaluated_ledger_sequence", "attempt_id",
    ):
        assert len({occasion["request"][field] for occasion in occasions}) == 1
    snapshots = [dict(occasion["inner_life_snapshot"]) for occasion in occasions[1:]]
    correction = snapshots[1].pop("role_result_correction")
    assert correction["task"] == "return_one_fresh_complete_role_result"
    assert snapshots[0] == snapshots[1]
    (winner,) = (audit for audit in _model_audits(evidence) if audit.outcome == "winner")
    assert winner.recall_trace is not None
    assert winner.character_interior_lineage.author_attempt_ordinal == 1
