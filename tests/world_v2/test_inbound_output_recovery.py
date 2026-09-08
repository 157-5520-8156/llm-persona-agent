"""Completed inbound author output survives the Core-to-Proposal crash window."""

from __future__ import annotations

import json
import sqlite3
import asyncio
import hashlib
import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

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
from companion_daemon.world_v2.deliberation import ModelInput
from companion_daemon.world_v2.deliberation import (
    ModelOutput,
    PhysicalProviderInvocationAudit,
    ProviderSubcallAudit,
    AuthoredCandidateInvocationAudit,
)
from companion_daemon.world_v2.recall_runtime import TrustedRecallTrace, verify_trusted_recall_trace
from companion_daemon.world_v2.character_interior.inbound_output_record import (
    _recorded_output,
    output_record_identity,
)
import companion_daemon.world_v2.production_turn_application as application_module
from companion_daemon.world_v2.proposal_audit import ProposalAuditRecorder
from companion_daemon.world_v2.world_turn_runtime import InboundTurn
import test_world_stimulus_life_intent as public


class _ProcessStopped(BaseException):
    """Fault injection after the durable author terminal, before World audit."""


class _AtomicHTTP:
    def __init__(self, *, recall=False):
        self.chat_requests = []
        self.recall = recall

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
        if self.recall and len(self.chat_requests) == 1:
            recall = {
                "result_kind": "recall",
                "private_turn_state": {
                    "contract": "private-turn-state.1",
                    "inner_state_summary": "我想先看看原来的谈话。",
                    "attended_source_refs": [],
                },
                "recall_request": {"query_text": "原来的谈话"},
            }
            if "payload_json" in properties:
                authored = {
                    "result_kind": "recall",
                    "payload_json": json.dumps(recall, ensure_ascii=False),
                }
            else:
                authored = {**dict.fromkeys(properties), **recall}
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


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _replace_checkpoint(path, row, *, terminal, prepared):
    terminal_json, prepared_json = _json(terminal), _json(prepared)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE world_v2_character_interior_turns SET terminal_result_json=?,"
            "terminal_result_hash=?,authored_state_json=?,authored_state_hash=? "
            "WHERE inner_turn_id=?",
            (
                terminal_json,
                _digest(terminal_json),
                prepared_json,
                _digest(prepared_json),
                row["inner_turn_id"],
            ),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "window",
    [
        "live_lease",
        "expired_lease",
        "same_pin_port",
        "recall_port",
        "recall_subprocess",
        "recall_tampered_subprocess",
        "prefetch_subprocess",
        "prefetch_missing_subprocess",
        "prefetch_changed_subprocess",
        "legacy_same_pin",
        *(
            "record_" + name
            for name in (
                "world",
                "actor",
                "cursor",
                "capability",
                "source",
                "turn",
                "proposal",
                "author",
            )
        ),
    ],
)
async def test_public_sqlite_reopens_completed_output_before_proposal(
    tmp_path, monkeypatch, window
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "world.sqlite"
    usage_path = tmp_path / "usage.sqlite"
    usage = WorldV2UsageStore(path=str(usage_path), monthly_budget_cny=1, daily_budget_cny=1)
    prefetch_case = window.startswith("prefetch_")
    requests = _AtomicHTTP(recall=window.startswith("recall_"))
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
            assert len(_terminals(path)) == 1 + len(prior_terminals)
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
    if prefetch_case:
        # Real first conversation supplies the subsequent automatic prefetch;
        # no trace, memory, or accepted World event is manually inserted.
        from dataclasses import replace

        await app.respond(
            replace(inbound, platform_message_id="message:warmup", trace_id="trace:warmup")
        )
    prior_terminals = _terminals(path)
    prior_projection = app.export_replay_evidence().projection
    monkeypatch.setattr(ProposalAuditRecorder, "record", stop_before_record)
    try:
        with pytest.raises(_ProcessStopped):
            await app.respond(inbound)
        original = _terminals(path)
        assert len(original) == 1 + len(prior_terminals)
        original_calls = len(requests.chat_requests)
        assert original_calls > 0
        original_request, original_output = captured_results[-1]
        original_usage = _usage_rows(usage_path)
        assert len(original_usage) == original_calls
        assert all(item["billing_state"] == "known" for item in original_usage)
        assert all(
            item["prompt_tokens"] == item["completion_tokens"] == 100 for item in original_usage
        )
        original_projection = app.export_replay_evidence().projection
        assert original_projection.proposal_audits == prior_projection.proposal_audits
        assert original_projection.model_result_audits == prior_projection.model_result_audits
        # The primary bill exists. The absent World audit and absent output
        # body are separate losses; this is not a claim of unmetered HTTP.
        original_row = next(row for row in original if row not in prior_terminals)
        decision = json.loads(original_row["terminal_result_json"])["decision"]
        assert decision["contract"] == "character-interior-inbound-turn-decision.2"
        assert decision["output_record"]["output"]["raw_proposal"] == original_output.raw_proposal
        assert original_output.winning_model_call_id is not None
    finally:
        await app.aclose()
        stores[-1].close()
    monkeypatch.setattr(ProposalAuditRecorder, "record", record)
    if window.endswith("subprocess"):
        if prefetch_case:
            assert original_output.prefetch_trace is None
            assert original_output.presented_prefetch_traces
            original_trace = original_output.presented_prefetch_traces[-1].trace
        else:
            assert original_output.recall_trace is not None
            original_trace = original_output.recall_trace
        evidence_path = tmp_path / "child-input.json"
        evidence_path.write_text(
            json.dumps(
                {
                    "request": original_request.model_dump(mode="json"),
                    "old_trace": original_trace.model_dump(mode="json"),
                    "trace_kind": "prefetch" if prefetch_case else "recall",
                    "expected_output": _recorded_output(original_output).model_dump(mode="json"),
                },
                ensure_ascii=False,
            )
        )
        if window == "recall_tampered_subprocess":
            prepared = json.loads(original[0]["authored_state_json"])
            trace = json.loads(prepared["snapshot"]["recall_trace_json"])
            trace["audit"]["trigger_ref"] = "event:another-observation"
            prepared["snapshot"]["recall_trace_json"] = json.dumps(
                trace, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            raw = json.dumps(prepared, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(
                json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode()
            ).hexdigest()
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "UPDATE world_v2_character_interior_turns SET authored_state_json=?,authored_state_hash=? WHERE inner_turn_id=?",
                    (raw, digest, original[0]["inner_turn_id"]),
                )
        if window in {"prefetch_missing_subprocess", "prefetch_changed_subprocess"}:
            terminal = json.loads(original_row["terminal_result_json"])
            prepared = json.loads(original_row["authored_state_json"])
            payload = terminal["decision"]
            body = payload["output_record"]
            if window == "prefetch_missing_subprocess":
                body["output"].pop("presented_prefetch_traces")
                presentations = []
            else:
                # Keep a valid trace at the same actor/cursor, but replace its
                # execution identity; recomputing outer hashes cannot make it
                # the trace actually shown in the original pinned snapshot.
                presentations = body["output"]["presented_prefetch_traces"]
                presentations[-1]["trace"]["embedding_version"] = "other-embedding.1"
                from companion_daemon.world_v2.recall_audit import PrefetchPresentationAudit

                PrefetchPresentationAudit.model_validate_json(_json(presentations[-1]))
            terminal["presented_prefetch_traces"] = presentations
            prepared["presented_prefetch_traces"] = presentations
            payload["output_ref"], payload["output_hash"] = output_record_identity(body)
            prepared["result"]["decision"] = payload
            _replace_checkpoint(path, original_row, terminal=terminal, prepared=prepared)
        root = Path(__file__).resolve().parents[2]
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import asyncio,sys; from test_inbound_output_recovery import _recover_in_child; asyncio.run(_recover_in_child(sys.argv[1], sys.argv[2]))",
            str(path),
            str(evidence_path),
            cwd=root,
            env={
                "PYTHONPATH": ":".join(
                    str(root / part) for part in ("src", "tests/support", "tests/world_v2")
                ),
                "PYTHONDONTWRITEBYTECODE": "1",
                "COMPANION_DISABLE_DEBUG_USAGE_LEDGER": "1",
            },
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(child.communicate(), 20)
        assert child.returncode == 0, stderr.decode()
        result = json.loads(stdout)
        assert result["fresh_hmac"] is True
        assert result["http_calls"] == result["recall_calls"] == 0
        if window == "recall_tampered_subprocess":
            assert result["error"] == "inbound_output_record.invalid_original_checkpoint"
        elif prefetch_case and window != "prefetch_subprocess":
            assert result["error"] == "inbound_output_record.prefetch_source_mismatch"
        else:
            assert result["error"] is None
            assert result["equal_output"] is True
            assert _terminals(path) == original
        await model.aclose()
        return
    if window == "legacy_same_pin" or window.startswith("record_"):
        terminal = json.loads(original[0]["terminal_result_json"])
        payload = terminal["decision"]
        if window == "legacy_same_pin":
            # Exact baseline .1 formula, applied only to the temporary
            # technical terminal. No accepted World event is fabricated.
            payload.pop("output_record")
            payload["contract"] = "character-interior-inbound-turn-decision.1"
            payload["output_hash"] = "sha256:" + _digest(original_output.model_dump(mode="json"))
            payload["output_ref"] = "inbound-turn-output:sha256:" + _digest(
                {
                    "inner_turn_id": terminal["inner_turn_id"],
                    "output_hash": payload["output_hash"],
                    "proposal_hash": payload["proposal_hash"],
                }
            )
        else:
            body = payload["output_record"]
            mutation = window.removeprefix("record_")
            if mutation == "world":
                body["world_id"] = "world:another"
            elif mutation == "actor":
                body["actor_ref"] = "actor:another"
            elif mutation == "cursor":
                body["cursor"]["ledger_sequence"] += 1
            elif mutation == "capability":
                body["capability_payload_hash"] = "sha256:" + "e" * 64
            elif mutation == "source":
                body["source_refs"] = ["event:another-observation"]
            elif mutation == "turn":
                body["inner_turn_id"] += ":another"
            elif mutation == "proposal":
                body["proposal_hash"] = "e" * 64
            else:
                body["author_lineage"]["response_hash"] = "sha256:" + "e" * 64
            payload["output_ref"], payload["output_hash"] = output_record_identity(body)
        prepared = json.loads(original[0]["authored_state_json"])
        prepared["result"]["decision"] = payload
        _replace_checkpoint(path, original[0], terminal=terminal, prepared=prepared)
        original = _terminals(path)
    reopened = public._build(path, model)
    try:
        if window == "legacy_same_pin" or window.startswith("record_"):
            expected = (
                "inbound CharacterInterior output cache is unavailable"
                if window == "legacy_same_pin"
                else "inbound_output_record.original_authority_mismatch"
                if window == "record_world"
                else "inbound_output_record.binding_mismatch"
            )
            for _ in range(2):
                with pytest.raises((ValueError, RuntimeError), match=expected):
                    await adapters[-1].propose(original_request)
            assert _terminals(path) == original
            assert len(requests.chat_requests) == original_calls
            assert _usage_rows(usage_path) == original_usage
            return
        if window in {"same_pin_port", "recall_port"}:
            # This is the public Deliberation adapter port used by app
            # composition, not an automatic cold ingress recovery policy.
            # The original request is captured from the real initial call.
            # Neither its cursor nor its Context is reconstructed or changed.
            recovered = await adapters[-1].propose(original_request)
            assert recovered == original_output
            if window == "recall_port":
                assert recovered.recall_trace is not None
            assert await adapters[-1].propose(original_request) == recovered
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


async def _recover_in_child(path_string, evidence_string):
    """Real subprocess entry: no inherited HMAC, Core, Faculty, or output cache."""
    from companion_daemon.world_v2.character_interior.production import _CoordinatorRecallPort

    path = Path(path_string)
    evidence = json.loads(Path(evidence_string).read_text())
    old_trace = TrustedRecallTrace.model_validate_json(json.dumps(evidence["old_trace"]))
    try:
        verify_trusted_recall_trace(old_trace)
    except ValueError:
        fresh_hmac = True
    else:
        fresh_hmac = False
    calls = {"http_calls": 0, "recall_calls": 0}

    def no_http(request):
        calls["http_calls"] += 1
        raise AssertionError("cold terminal cannot call a provider")

    async def no_recall(*args, **kwargs):
        calls["recall_calls"] += 1
        raise AssertionError("cold terminal cannot retrieve again")

    model = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(no_http),
    )
    store = open_sqlite_character_interior_turn_store(path=path, world_id=public.WORLD)
    compose = public.compose_production_character_interior
    make_adapter = application_module.compose_character_interior_inbound_deliberation
    adapters = []

    def capture_adapter(**kwargs):
        adapter = make_adapter(**kwargs)
        adapters.append(adapter)
        return adapter

    with (
        patch.object(
            public,
            "compose_production_character_interior",
            lambda **kwargs: compose(**kwargs, turn_store=store),
        ),
        patch.object(
            application_module, "compose_character_interior_inbound_deliberation", capture_adapter
        ),
    ):
        app = public._build(path, model)
    result = {**calls, "fresh_hmac": fresh_hmac, "error": None, "equal_output": False}
    try:
        request = ModelInput.model_validate_json(json.dumps(evidence["request"]))
        with (
            patch.object(_CoordinatorRecallPort, "recall", no_recall),
            patch.object(_CoordinatorRecallPort, "prefetch", no_recall),
        ):
            try:
                output = await adapters[-1].propose(request)
                assert await adapters[-1].propose(request) == output
            except ValueError as exc:
                result["error"] = str(exc)
            else:
                restored_trace = (
                    output.presented_prefetch_traces[-1].trace
                    if evidence.get("trace_kind") == "prefetch"
                    else output.recall_trace
                )
                assert verify_trusted_recall_trace(restored_trace) == old_trace.audit
                if evidence.get("trace_kind") == "prefetch":
                    assert output.prefetch_trace is None
                result["equal_output"] = (
                    _recorded_output(output).model_dump(mode="json") == evidence["expected_output"]
                )
        result.update(calls)
        print(json.dumps(result))
    finally:
        await app.aclose()
        store.close()
        await model.aclose()


def test_stable_body_retains_all_excluded_audits_and_no_live_seal():
    # This codec fixture tests field preservation, not an additional provider
    # request. The public SQLite cases above exercise the real producer.
    identity = dict(model_id="fixture", model_version="fixture.1", request_hash="a" * 64)
    output = ModelOutput(
        model_id="fixture",
        model_version="fixture.1",
        raw_proposal={},
        winning_model_call_id="call:final",
        winning_request_hash="a" * 64,
        physical_provider_audits=(
            PhysicalProviderInvocationAudit(
                **identity,
                model_call_id="call:physical",
                outcome="cancelled",
                failure_code="caller_cancelled",
                usage_status="cancelled",
                semantic_model_call_ids=("call:head",),
            ),
        ),
        provider_subcall_audits=(
            ProviderSubcallAudit(
                **identity,
                purpose="validation",
                parent_model_call_id="call:final",
                model_call_id="call:validation",
                lane="direct",
                outcome="winner",
                response_hash="b" * 64,
            ),
        ),
        authored_candidate_audits=(
            AuthoredCandidateInvocationAudit(
                **identity,
                purpose="expression",
                model_call_id="call:rejected",
                response_hash="c" * 64,
                outcome="validation_rejected",
            ),
        ),
    )
    ordinary = output.model_dump(mode="json")
    stable = _recorded_output(output).model_dump(mode="json")
    names = ("physical_provider_audits", "provider_subcall_audits", "authored_candidate_audits")
    for name in names:
        assert name not in ordinary
        assert stable[name] == [item.model_dump(mode="json") for item in getattr(output, name)]
    assert "authority_seal" not in _json(stable)
    assert ModelOutput.model_validate_json(_json(stable)) == output


@pytest.mark.parametrize("kind", ["bytes", "nodes"])
def test_oversized_record_is_rejected_before_body_validation(kind):
    from companion_daemon.world_v2.character_interior.contracts import InnerDecision
    from companion_daemon.world_v2.character_interior.inbound_output_record import _validate_record
    from companion_daemon.world_v2.deliberation import MAX_MODEL_OUTPUT_NODES

    raw = {"oversized": "界" * 171_000} if kind == "bytes" else [None] * MAX_MODEL_OUTPUT_NODES
    decision = InnerDecision.model_construct(
        decision={
            "contract": "character-interior-inbound-turn-decision.2",
            "output_record": raw,
        }
    )
    with pytest.raises(
        ValueError, match="inbound output record exceeds " + kind.removesuffix("s") + " limit"
    ):
        _validate_record(decision=decision, model_input=None)


def test_recall_restoration_does_not_accept_arbitrary_audit_dict():
    from companion_daemon.world_v2.recall_runtime import _restore_completed_inbound_traces

    with pytest.raises(TypeError, match="completed inbound trace authority is unavailable"):
        _restore_completed_inbound_traces({"audit": {"trigger_ref": "event:invented"}})
