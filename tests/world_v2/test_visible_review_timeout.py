"""Real v22 review/provider boundary distinguishes its deadline from cancellation."""

import asyncio
import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel, ProviderCapacityGate, ProviderCircuitBreaker
from companion_daemon.world_v2.deliberation import ValidationTechnicalFailure
from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore
from companion_daemon.world_v2.visible_independent_review_runtime import (
    IndependentVisibleReviewer, review_independent_candidate,
)
from companion_daemon.world_v2.visible_source_composer import compile_visible_source_table
from test_visible_selected_source_context import _sources
from test_visible_source_review_receipt import _candidate
from test_world_stimulus_life_intent import _http_result


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["deadline", "caller_cancelled"])
async def test_source_review_deadline_and_caller_cancellation_keep_distinct_provider_accounting(
    tmp_path, monkeypatch, terminal,
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    source_started = asyncio.Event()
    requests = []
    deadlines = []
    loop = asyncio.get_running_loop()
    call_at = loop.call_at

    def accelerated_deadline(when, callback, *args, **kwargs):
        # Accelerate only the existing 22-second provider timer, not the
        # enclosing review deadline or cancellation grace period.
        delay = when - loop.time()
        if 21.99 <= delay <= 22.0:
            deadlines.append(delay)
            if terminal == "deadline":
                when = loop.time() + 0.02
        return call_at(when, callback, *args, **kwargs)

    monkeypatch.setattr(loop, "call_at", accelerated_deadline)

    async def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        packet = json.loads(body["messages"][1]["content"])
        if "tool_choice" in body:
            assert body["tool_choice"]["function"]["name"] == "interpret_visible_candidate_complete_v16"
            return _http_result(body, {"contract": packet["contract"], "decisions": [{
                "beat_index": b["beat_index"], "reading_complete": True,
                "unresolved_details": [], "hypothetical_conditions": [], "questions": [],
                "presuppositions": [], "meanings": [{
                    "proposition": b["text"], "subject_role": "counterpart",
                    "mode": "actual_event_or_state",
                }],
            } for b in packet["visible_beats"]]})
        assert packet["output_contract"]["contract"] == "visible-contextual-source-review.6"
        assert packet["fixed_facts"]
        source_started.set()
        await asyncio.Future()

    usage_path = tmp_path / "usage.sqlite"
    usage = WorldV2UsageStore(path=str(usage_path))
    clock = [0.0]
    breaker = ProviderCircuitBreaker(failure_threshold=1, cooldown_seconds=30, clock=lambda: clock[0])
    if terminal == "caller_cancelled":
        breaker.record_failure()
        clock[0] = 31.0  # The cancelled call owns a half-open recovery probe.
    capacity = ProviderCapacityGate(cooldown_seconds=30, clock=lambda: clock[0])
    readers = tuple(DeepSeekChatModel("offline-fixture", "https://fixture.invalid", name,
        thinking_enabled=False, transport=httpx.MockTransport(respond),
        usage_observer=usage.record, circuit_breaker=ProviderCircuitBreaker())
        for name in ("deepseek-v4-flash", "deepseek-v4-pro"))
    source = DeepSeekChatModel("offline-fixture", "https://fixture.invalid", "deepseek-v4-pro",
        thinking_enabled=False, transport=httpx.MockTransport(respond),
        usage_observer=usage.record, circuit_breaker=breaker, capacity_gate=capacity)
    reviewer = IndependentVisibleReviewer(meaning_models=readers, source_model=source,
        scope_permission_context=True, source_response_mode="json_object")
    task = None
    try:
        async with _sources(tmp_path) as case:
            task = asyncio.create_task(review_independent_candidate(
                request=case.request, output=SimpleNamespace(
                    winning_model_call_id="model-call:author", winning_request_hash="a" * 64,
                    provider_subcall_audits=(), model_id="author", model_version="fixture.1", usage=None),
                proposal=_candidate(case, texts=("你取消了周五的报告。",)),
                source_table=compile_visible_source_table(request=case.request, capsule=case.capsule),
                aliases={}, author_request_json="{}", reviewer=reviewer, review_version="22",
                usage_purpose="inbound_source_review",
            ))
            await asyncio.wait_for(source_started.wait(), 2)
            if terminal == "caller_cancelled":
                task.cancel()
                with pytest.raises(asyncio.CancelledError) as caught:
                    await task
                failure = caught.value.world_v2_validation_technical_failure
            else:
                with pytest.raises(ValidationTechnicalFailure) as caught:
                    await task
                failure = caught.value
            assert failure.failure_code == "source_review_timeout"
            assert len(failure.provider_subcall_audits) == 3
            assert [a.outcome for a in failure.provider_subcall_audits] == ["winner", "winner", "timeout"]
            assert failure.provider_subcall_audits[-1].response_hash is None
        assert len(requests) == 3 and len(deadlines) == 3
        with sqlite3.connect(usage_path) as db:
            failed = db.execute("SELECT error, billing_state, reservation_id FROM world_v2_model_usage "
                                "WHERE status = 'failed'").fetchall()
            assert len(failed) == 1
            error, billing, reservation = failed[0]
            assert error == ("provider_timeout" if terminal == "deadline" else "caller_cancelled")
            assert billing == "unknown"
            status, reserve = db.execute("SELECT status, estimated_cny FROM world_v2_model_reservations "
                                        "WHERE reservation_id = ?", (reservation,)).fetchone()
            assert status == "billing_unknown" and reserve > 0
        assert capacity.snapshot().status == "cooldown"
        assert capacity.snapshot().ambiguous_cancellations == 1
        if terminal == "deadline":
            assert breaker.snapshot().status == "open"
        else:
            assert breaker.snapshot().status == "half_open"
            breaker.before_call()  # No stale probe lease after caller cancellation.
            breaker.release_probe()
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(*(model.aclose() for model in (*readers, source)))
