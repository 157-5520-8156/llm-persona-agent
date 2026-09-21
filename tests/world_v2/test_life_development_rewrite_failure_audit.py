"""Source rewrite admission/transport failures remain durable technical results."""

import json
import sqlite3

import httpx
import pytest
from jsonschema import Draft202012Validator

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.life_development_draft import LifeDevelopmentPossibilityDraft
from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore
from companion_daemon.world_v2.proposal_audit_schemas import RecordedModelResultAudit
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_life_development_runtime import WORLD_ID, _SequenceModel, _novel_origin_review, _source_closure_review, _seed_clock
from test_world_consequence_producer import _advance, _draft, _runtime
from test_world_author_request_audit import _json


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "budget_denied", "timeout", "connection_error", "invalid_json", "invalid_shape",
])
async def test_rewrite_failure_records_paired_attempt_metadata_and_cold_recovers_without_retry(
    tmp_path, monkeypatch, failure,
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "rewrite.sqlite"
    usage_path = tmp_path / "usage.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    usage = WorldV2UsageStore(
        path=str(usage_path), monthly_budget_cny=1.0 if failure == "budget_denied" else 100.0,
    )
    wake = _seed_clock(ledger)
    draft = _draft(wake)
    fragment = "她及时收回了手账。"
    draft["outcomes"][0]["world_consequence"]["environment_text"] += fragment
    draft = LifeDevelopmentPossibilityDraft.model_validate_json(_json(draft)).model_dump(mode="json")
    for outcome in draft["outcomes"]:
        outcome["world_consequence"].setdefault("authorized_attempt_result", None)
    http_requests = []
    invalid_output = {
        "invalid_json": '{"replacement":{"decision":"no_op"}}}',
        "invalid_shape": '{"replacement":{"decision":"unavailable"}}',
    }.get(failure)

    def provider(request):
        wire = json.loads(request.content)
        http_requests.append(wire)
        assert request.url.path == "/beta/chat/completions"
        assert "response_format" not in wire
        assert wire["tools"] == http_requests[0]["tools"]
        assert wire["tool_choice"] == http_requests[0]["tool_choice"]
        if len(http_requests) > 1:
            if failure == "timeout":
                raise httpx.ReadTimeout("fixture: rewrite timed out", request=request)
            if failure == "connection_error":
                raise httpx.ConnectError("fixture: rewrite unavailable", request=request)
            if failure == "budget_denied":
                pytest.fail("budget-denied rewrite must not reach HTTP")
        # The simulated original response consumes the configured budget; the
        # real usage admission port must refuse the later rewrite before HTTP.
        tokens = 10_000_000 if failure == "budget_denied" else 100
        if len(http_requests) == 1:
            arguments = {"replacement": draft}
            Draft202012Validator(wire["tools"][0]["function"]["parameters"]).validate(arguments)
            raw_arguments = _json(arguments)
        else:
            raw_arguments = invalid_output
        return httpx.Response(200, json={
            "id": "offline-original-author", "model": "deepseek-v4-flash",
            "choices": [{"message": {"role": "assistant", "content": None,
                "tool_calls": [{"type": "function", "function": {
                    "name": wire["tool_choice"]["function"]["name"],
                    "arguments": raw_arguments,
                }}]}, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": tokens, "completion_tokens": 200,
                      "total_tokens": tokens + 200},
        })

    author = DeepSeekChatModel(
        "offline-fixture", "https://fixture.invalid", "deepseek-v4-flash",
        thinking_enabled=False, transport=httpx.MockTransport(provider), usage_observer=usage.record,
    )
    general = _SequenceModel(model="fixture:general", outputs=(_source_closure_review(decision="supported"),))
    focused = _SequenceModel(model="fixture:focused", outputs=(
        _novel_origin_review(
            decision="unsupported", unsupported_outcome_prerequisites=({
                "prose_path": "outcomes.0.world_consequence.environment_text",
                "violation_kinds": ["character_interior_authorship"],
                "exact_fragments": [fragment],
            },),
        ),
    ))
    try:
        result = await _advance(_runtime(ledger, store, wake, author, general, focused), wake)
        assert result.status == "technical_failure"
        assert result.reason_code == "life_development.world_author_source_rewrite_unavailable"
        assert len(http_requests) == (1 if failure == "budget_denied" else 2)
        assert focused.calls == 1 and general.calls == 1
        state = ledger.project()
        assert state.world_occurrences == state.plans == state.experiences == ()
        failed = [RecordedModelResultAudit.model_validate_json(item.audit_json)
                  for item in state.model_result_audits if json.loads(item.audit_json)["status"]
                  in {"main_exception", "main_timeout", "main_invalid"}]
        assert len(failed) == 1
        audit = failed[0]
        assert audit.slot == "primary"
        if invalid_output is not None:
            assert audit.status == "main_invalid"
            assert audit.outcome == "invalid" and audit.failure_code == "main_invalid_output"
            assert audit.model_id == "deepseek-v4-flash" and audit.attempted_model_id is None
            assert audit.response_storage.disposition == "stored_exact"
            raw = store.read_exact(content_ref=audit.response_storage.content_ref)
            assert raw.text == invalid_output
            assert raw.content_payload_hash == audit.response_hash
            assert not audit.response_storage.truncated
        else:
            assert audit.outcome == ("timeout" if failure == "timeout" else "exception")
            assert audit.failure_code == ("main_timeout" if failure == "timeout" else "main_exception")
            assert audit.response_hash is None and audit.model_id is None
            assert audit.attempted_model_id == "deepseek-v4-flash"
        original_audit = json.loads(state.model_result_audits[0].audit_json)
        assert audit.request_hash != original_audit["request_hash"]
        attempted_request = store.read_exact(content_ref="content:world-author-request:" + audit.request_hash)
        assert attempted_request is not None
        assert json.loads(attempted_request.text)[:-2] == http_requests[0]["messages"]
        if failure == "budget_denied":
            with sqlite3.connect(usage_path) as db:
                denied = db.execute(
                    "SELECT purpose, error, latency_ms, total_tokens FROM world_v2_model_usage "
                    "WHERE status='budget_denied'"
                ).fetchall()
            assert denied == [("life_development_source_rewrite", "monthly_budget_exceeded", 0, 0)]
        before = ledger.export_replay_evidence()
        store.close()
        ledger.close()
        ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
        store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
        cold_focused = _SequenceModel(model="fixture:focused", outputs=())
        recovered = await _advance(_runtime(ledger, store, wake, author, general, cold_focused), wake)
        assert recovered.status == result.status and recovered.reason_code == result.reason_code
        assert cold_focused.calls == 0
        assert general.calls == 1
        assert len(http_requests) == (1 if failure == "budget_denied" else 2)
        assert ledger.export_replay_evidence() == before
        if failure == "budget_denied":
            with sqlite3.connect(usage_path) as db:
                assert db.execute("SELECT COUNT(*) FROM world_v2_model_usage WHERE status='budget_denied'").fetchone()[0] == 1
    finally:
        await author.aclose()
        store.close()
        ledger.close()
