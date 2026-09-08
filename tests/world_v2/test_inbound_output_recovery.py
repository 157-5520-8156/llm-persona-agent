"""Completed inbound author output survives the Core-to-Proposal crash window."""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.character_interior.turn_store import (
    open_sqlite_character_interior_turn_store,
)
from companion_daemon.world_v2.character_interior.inbound_turn import (
    CharacterInteriorInboundDeliberationAdapter,
)
from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore
import companion_daemon.world_v2.production_turn_application as application_module
from companion_daemon.world_v2.proposal_audit import ProposalAuditRecorder
from companion_daemon.world_v2.world_turn_runtime import InboundTurn
import test_world_stimulus_life_intent as public


class _ProcessStopped(BaseException):
    """Fault injection after the durable author terminal, before World audit."""


class _AtomicHTTP:
    def __init__(self):
        self.chat_requests = []

    def __call__(self, request):
        body = json.loads(request.content)
        self.chat_requests.append(body)
        properties = body["tools"][0]["function"]["parameters"]["properties"]
        authored = dict.fromkeys(properties)
        authored.update(
            result_kind="decision",
            appraisal_draft={"appraise": False, "affect": "no_change"},
            expression_draft={
                "private_turn_state": {
                    "contract": "private-turn-state.1",
                    "inner_state_summary": "我想听一听。",
                    "attended_source_refs": [],
                },
                "timing_choice": "now",
                "beats": [{"modality": "text", "text": "嗯，我在听。"}],
                "stance": "平静",
                "brief_rationale": "回应这次询问。",
                "confidence": 8000,
                "world_claims": [],
            },
        )
        if "payload_json" in properties:
            authored = {
                "result_kind": "reply_only",
                "payload_json": json.dumps(
                    {
                        "messages": ["嗯，我在听。"],
                        "meaning_of_this": "我想听一听。",
                        "my_state": "平静。",
                        "world_claims": [],
                    },
                    ensure_ascii=False,
                ),
            }
        return public._http_result(body, authored)


def _terminals(path):
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM world_v2_character_interior_turns "
                "WHERE purpose='inbound_turn' AND state='terminal'"
            )
        ]


def _usage_rows(path):
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in connection.execute(
                "SELECT id,billing_state,prompt_tokens,completion_tokens "
                "FROM world_v2_model_usage ORDER BY id"
            )
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["live_lease", "expired_lease", "same_pin_port"])
async def test_public_sqlite_reopens_completed_output_before_proposal(
    tmp_path, monkeypatch, window
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "world.sqlite"
    usage_path = tmp_path / "usage.sqlite"
    usage = WorldV2UsageStore(path=str(usage_path), monthly_budget_cny=1, daily_budget_cny=1)
    requests = _AtomicHTTP()
    model = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(requests),
        usage_observer=usage.record,
    )
    compose = public.compose_production_character_interior
    stores = []

    def durable_compose(**kwargs):
        store = open_sqlite_character_interior_turn_store(path=path, world_id=public.WORLD)
        stores.append(store)
        return compose(**kwargs, turn_store=store)

    monkeypatch.setattr(public, "compose_production_character_interior", durable_compose)
    adapters = []
    captured_results = []
    make_adapter = application_module.compose_character_interior_inbound_deliberation
    propose = CharacterInteriorInboundDeliberationAdapter.propose

    def capture_adapter(**kwargs):
        adapter = make_adapter(**kwargs)
        adapters.append(adapter)
        return adapter

    async def capture_propose(self, request):
        output = await propose(self, request)
        captured_results.append((request, output))
        return output

    monkeypatch.setattr(
        application_module, "compose_character_interior_inbound_deliberation", capture_adapter
    )
    monkeypatch.setattr(CharacterInteriorInboundDeliberationAdapter, "propose", capture_propose)
    record = ProposalAuditRecorder.record

    def stop_before_record(self, result, context):
        if result.proposal is not None:
            assert len(_terminals(path)) == 1
            raise _ProcessStopped()
        return record(self, result, context)

    inbound = InboundTurn(
        platform="test",
        platform_user_id="user.1",
        platform_message_id="message:durable-output",
        text="我想和你聊一下。",
        observed_at=public.NOW,
        trace_id="trace:durable-output",
    )
    app = public._build(path, model)
    monkeypatch.setattr(ProposalAuditRecorder, "record", stop_before_record)
    try:
        with pytest.raises(_ProcessStopped):
            await app.respond(inbound)
        original = _terminals(path)
        assert len(original) == 1
        original_calls = len(requests.chat_requests)
        assert original_calls > 0
        original_request, original_output = captured_results[0]
        original_usage = _usage_rows(usage_path)
        assert len(original_usage) == original_calls
        assert all(item["billing_state"] == "known" for item in original_usage)
        assert all(
            item["prompt_tokens"] == item["completion_tokens"] == 100 for item in original_usage
        )
        original_projection = app.export_replay_evidence().projection
        assert not original_projection.proposal_audits
        assert not original_projection.model_result_audits
        # The primary bill exists. The absent World audit and absent output
        # body are separate losses; this is not a claim of unmetered HTTP.
        decision = json.loads(original[0]["terminal_result_json"])["decision"]
        assert decision["contract"] == "character-interior-inbound-turn-decision.1"
        assert "output_record" not in decision
        assert original_output.winning_model_call_id is not None
    finally:
        await app.aclose()
        stores[-1].close()
    monkeypatch.setattr(ProposalAuditRecorder, "record", record)
    reopened = public._build(path, model)
    try:
        if window == "same_pin_port":
            # This is the public Deliberation adapter port used by app
            # composition, not an automatic cold ingress recovery policy.
            # The original request is captured from the real initial call.
            # Neither its cursor nor its Context is reconstructed or changed.
            recovered = await adapters[-1].propose(original_request)
            assert recovered == original_output
            assert len(requests.chat_requests) == original_calls
            assert _terminals(path) == original
            return
        if window == "expired_lease":
            await reopened.tick(
                tick_id="expire-original-owner",
                logical_time_from=public.NOW,
                logical_time_to=public.NOW + timedelta(seconds=121),
                observed_at=public.NOW + timedelta(seconds=121),
                trace_id="trace:expire-original-owner",
                causation_id="scheduler:test",
                correlation_id="durable-output",
                reason="test_owner_expired",
                run_life_ecology=False,
            )
        outcome = await reopened.respond(inbound)
        if window == "live_lease":
            assert len(requests.chat_requests) == original_calls
            assert _terminals(path) == original
            assert not outcome.authorized_action_ids, "a live foreign lease is still a join"
            return
        # Diagnostic of a different pin, not a requirement to suppress a
        # legitimate fresh role choice after World has changed.
        assert len(requests.chat_requests) > original_calls
        terminals = _terminals(path)
        assert len(terminals) == 2
        assert original[0] in terminals
        fresh = next(
            item for item in terminals if item["inner_turn_id"] != original[0]["inner_turn_id"]
        )
        assert fresh["cursor_json"] != original[0]["cursor_json"]
        fresh_request, _ = captured_results[-1]
        assert fresh_request.attempt_id != original_request.attempt_id
        assert fresh_request.evaluated_world_revision > original_request.evaluated_world_revision
        projection = reopened.export_replay_evidence().projection
        assert outcome.authorized_action_ids
        assert projection.proposal_audits
        assert all(
            item.model_call_id != original_output.winning_model_call_id
            for item in projection.model_result_audits
        )
        assert _usage_rows(usage_path)[:original_calls] == original_usage
    finally:
        await reopened.aclose()
        stores[-1].close()
        await model.aclose()
