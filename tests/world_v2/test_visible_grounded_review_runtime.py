"""Grounded review through Core, the native HTTP adapter, Action and replay."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import json
import sqlite3

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.character_interior.inbound_author import _InboundCharacterAuthor
from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore
from companion_daemon.world_v2.proposal_audit_schemas import RecordedModelResultAudit
from companion_daemon.world_v2.shared_string_view import unpack_shared_strings
from companion_daemon.world_v2.visible_independent_review_configuration import GroundedVisibleReviewer
from companion_daemon.world_v2.visible_review_evidence_storage import read_review_evidence
from companion_daemon.world_v2.visible_source_runtime import digest, verify_recorded_candidate
from world_v2_application import (
    build_sqlite_world_v2_test_application,
    compose_fixture_character_interior,
)
from test_launch_visible_source_gate import _audits
from test_production_turn_application import NOW, _config, _DeliveredTransport, _Identities, _Router
from test_whole_candidate_author import _decision, _inbound
from test_world_stimulus_life_intent import _http_result


USER_TEXT = "我洗了两遍，手上还有蓝色。"
REPORT_REPLY = "你洗了两遍，蓝色还没掉。"
UNSUPPORTED_REPLY = "我昨天也把手染蓝了。"


@pytest.fixture(autouse=True)
def isolated_usage(monkeypatch):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")


class GroundedHTTP:
    def __init__(self, *, reply=REPORT_REPLY, fault=None, review_protocol="visible-grounded-review.1"):
        self.reply = reply
        self.fault = fault
        self.review_protocol = review_protocol
        self.requests = []
        self.authors = 0
        self.reviews = []
        self.callback_errors = []

    async def __call__(self, request):
        try:
            return await self.respond(request)
        except AssertionError as exc:
            # HTTP adapter failures otherwise become an opaque technical hold.
            self.callback_errors.append(str(exc))
            raise

    async def respond(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        assert not body.get("stream")
        packet = json.loads(body["messages"][1]["content"])
        ordered = packet.get("wire_carrier") in {"grounded-review-ordered-wire.1", "grounded-review-ordered-wire.2", "grounded-review-ordered-wire.3", "grounded-review-ordered-wire.4"}
        if "tools" in body and not ordered:
            assert body["tool_choice"]["function"]["name"].startswith("character_inbound_")
            self.authors += 1
            result = _decision()
            text = (
                UNSUPPORTED_REPLY
                if self.fault == "reselect" and self.authors == 1
                else self.reply
            )
            result["expression_draft"]["beats"] = [{"modality": "text", "text": text}]
            assert result["expression_draft"]["world_claims"] == []
            return _http_result(body, {"result": {
                key: result[key] for key in ("result_kind", "appraisal_draft", "expression_draft")
            }})

        if not ordered:
            assert "tool_choice" not in body
            assert body["response_format"] == {"type": "json_object"}
        assert packet["contract"] == self.review_protocol
        assert USER_TEXT in json.dumps(packet["dialogue_context"], ensure_ascii=False)
        self.reviews.append(packet)
        if self.fault == "timeout":
            await asyncio.Future()

        cards = unpack_shared_strings(packet["source_materials"])
        report_readings = [
            reading
            for card in cards
            if card["material"]["kind"] == "current_counterpart_report"
            for reading in card["readings"]
            if ["report_uptake", "counterpart"] in reading["allowed_claims"]
        ]
        assert report_readings, cards
        decisions = []
        for beat in packet["visible_beats"]:
            reject = self.fault == "reselect" and self.authors == 1
            factual = self.reply == REPORT_REPLY or reject
            facts = []
            non_record = []
            if factual:
                missing = reject or self.fault == "missing_evidence"
                facts = [{
                    "text": beat["text"],
                    "proposition": beat["text"],
                    "claim_scope": "report_uptake",
                    "subject_role": "companion" if self.fault == "wrong_owner" else "counterpart",
                    "reading_ids": [] if missing else [report_readings[0]["reading_id"]],
                    "fact_value_selections": [],
                    "source_support": not reject,
                    "rationale": "缺少角色昨天经历的来源" if reject else "承接用户本轮报告",
                }]
                if self.review_protocol == "visible-grounded-review.2":
                    facts[0]["segment_index"] = 0
            else:
                non_record = [{
                    "text": beat["text"], "rationale": "本轮即时回应，无既往经历断言",
                    **({"segment_index": 0} if self.review_protocol == "visible-grounded-review.2" else {}),
                }]
            decisions.append({
                "beat_index": beat["beat_index"], "review_complete": True,
                "facts": facts, "non_record_expressions": non_record, "unresolved_details": [],
            })
        if ordered:
            for decision in decisions:
                segments = []
                for key, kind in (("facts", "fact"), ("non_record_expressions", "non_record")):
                    for part in decision.pop(key):
                        part.pop("segment_index", None)
                        segments.append({**part, "kind": kind})
                decision["segments"] = segments
            return _http_result(body, {"beat_decisions": decisions, **({"contract": packet["wire_carrier"]} if packet["wire_carrier"].endswith(".1") else {})})
        content = json.dumps({
            "contract": self.review_protocol, "beat_decisions": decisions,
        }, ensure_ascii=False)
        if self.fault == "unusable_answer" and len(self.reviews) == 1:
            # The first answer repeats one member. The response is then not one
            # instance of the declared contract, so it carries no verdict about
            # the candidate at all.
            content = content.replace(
                '"review_complete": true',
                '"review_complete": true, "review_complete": true',
                1,
            )
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
        })


@asynccontextmanager
async def application(path, handler, *, budget_policy=None, review_version="24"):
    usage = WorldV2UsageStore(path=str(path.with_name("usage.sqlite")))
    models = [DeepSeekChatModel(
        "offline-fixture", "https://fixture.invalid", "deepseek-v4-flash",
        thinking_enabled=False, transport=httpx.MockTransport(handler), usage_observer=usage.record,
    ) for _ in range(2)]
    author = _InboundCharacterAuthor(
        flash_model=models[0], whole_candidate_mode=True, atomic_tool_envelope_version="3",
        visible_source_review_model=GroundedVisibleReviewer(source_model=models[1]),
        visible_source_review_version=review_version,
    )
    transport = _DeliveredTransport()
    config = replace(_config(), visible_source_review_required=True)
    if budget_policy is not None:
        config = replace(config, interactive_turn_budget_policy=budget_policy)
    app = build_sqlite_world_v2_test_application(
        path=path, config=config,
        identities=_Identities(), router=_Router(),
        character_interior=compose_fixture_character_interior(inbound_author=author),
        transport=transport, now=NOW,
    )
    try:
        yield app, transport
    finally:
        app.close()
        await asyncio.gather(*(model.aclose() for model in models))


def bound_receipt(app, *, review_version="24"):
    evidence = app.export_replay_evidence()
    (proposal,) = [audit for audit in evidence.projection.proposal_audits
                   if audit.proposal_kind == "decision"]
    (winner,) = [audit for audit in _audits(app) if audit.visible_source_review_json]
    stored = read_review_evidence(winner.visible_source_review_json)
    receipt = stored["receipt"]
    expected_protocol = {
        "24": "visible-grounded-review.1",
        "25": "visible-grounded-review.2",
    }[review_version]
    assert json.loads(stored["requirement_json"])["review_protocol"] == expected_protocol
    assert receipt["contract"] == f"visible-source-review-receipt.{review_version}"
    assert receipt["review"]["parent_model_call_id"] == winner.model_call_id
    assert receipt["review"]["model_call_id"] != winner.model_call_id
    assert verify_recorded_candidate(
        audit=proposal, model_result_audits=evidence.projection.model_result_audits,
    ) == receipt["receipt_hash"]
    assert evidence.projection.semantic_hash == evidence.replay.semantic_hash
    return evidence, proposal, receipt


@pytest.mark.asyncio
async def test_v25_grounded_profile_round_trips_through_runtime_and_replay(tmp_path):
    handler = GroundedHTTP(review_protocol="visible-grounded-review.2")
    path = tmp_path / "grounded-v25.sqlite"
    inbound = replace(_inbound(), text=USER_TEXT)
    async with application(path, handler, review_version="25") as (app, transport):
        outcome = await app.respond(inbound)
        assert handler.callback_errors == []
        assert outcome.status == "action_authorized", (outcome, _audits(app))
        assert handler.authors == 1 and len(handler.reviews) == 1
        assert (await app.drain_actions_once()).status == "settled"
        assert transport.bodies == [REPORT_REPLY]
        receipt = bound_receipt(app, review_version="25")[2]

    cold = GroundedHTTP(review_protocol="visible-grounded-review.2")
    async with application(path, cold, review_version="25") as (app, transport):
        assert (await app.respond(inbound)).status == "action_authorized"
        assert (await app.drain_actions_once()).status == "idle"
        assert cold.requests == [] and transport.bodies == []
        assert bound_receipt(app, review_version="25")[2]["receipt_hash"] == receipt["receipt_hash"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [REPORT_REPLY, "我想看看你画的叶子。", "🙂"])
async def test_one_contextual_review_authorizes_delivers_and_cold_replays(tmp_path, reply):
    handler = GroundedHTTP(reply=reply)
    path = tmp_path / "world.sqlite"
    inbound = replace(_inbound(), text=USER_TEXT)
    async with application(path, handler) as (app, transport):
        outcome = await app.respond(inbound)
        assert handler.callback_errors == []
        assert outcome.status == "action_authorized", (outcome, _audits(app))
        assert handler.authors == 1 and len(handler.requests) == 2
        assert len(handler.reviews) == 1
        assert handler.reviews[0]["visible_beats"] == [{"beat_index": 0, "text": reply}]
        assert (await app.drain_actions_once()).status == "settled"
        assert transport.bodies == [reply]
        evidence, proposal, receipt = bound_receipt(app)
        assert receipt["beat_outcomes"] == ["closed" if reply == REPORT_REPLY else "source_free"]
        assert any(row.event.event_type == "ExecutionReceiptRecorded" for row in evidence.events)
        assert (await app.respond(inbound)).status == "action_authorized"
        assert (await app.drain_actions_once()).status == "idle"
        assert len(handler.requests) == 2 and transport.bodies == [reply]

        # Neither a missing subcall nor a rewritten response hash can borrow
        # the original passing receipt, even with a fresh outer audit hash.
        original_rows = evidence.projection.model_result_audits
        review_id = receipt["review"]["model_call_id"]
        for fault in ("missing_review", "changed_response"):
            rows = list(original_rows)
            index = next(i for i, row in enumerate(rows) if row.model_call_id == review_id)
            if fault == "missing_review":
                rows.pop(index)
            else:
                altered = RecordedModelResultAudit.model_validate_json(rows[index].audit_json)
                raw = altered.model_copy(update={"response_hash": "b" * 64}).model_dump_json()
                rows[index] = rows[index].model_copy(update={"audit_json": raw, "audit_hash": digest(raw)})
            with pytest.raises(ValueError):
                verify_recorded_candidate(audit=proposal, model_result_audits=tuple(rows))

    cold = GroundedHTTP(reply=reply)
    async with application(path, cold) as (app, transport):
        assert (await app.respond(inbound)).status == "action_authorized"
        assert (await app.drain_actions_once()).status == "idle"
        assert cold.requests == [] and transport.bodies == []
        assert bound_receipt(app)[2]["receipt_hash"] == receipt["receipt_hash"]


@pytest.mark.asyncio
async def test_grounded_rejection_returns_original_expression_to_same_author_once(tmp_path):
    handler = GroundedHTTP(fault="reselect")
    async with application(tmp_path / "world.sqlite", handler) as (app, transport):
        outcome = await app.respond(replace(_inbound(), text=USER_TEXT))
        assert handler.callback_errors == []
        assert outcome.status == "action_authorized", (outcome, _audits(app))
        assert handler.authors == 2 and len(handler.requests) == 4
        first, corrected = [body for body in handler.requests if "tools" in body]
        assert first["model"] == corrected["model"]
        packet = json.loads(corrected["messages"][1]["content"])
        correction = packet["role_result_correction"]["coordinate"]
        assert correction["failure_code"] == "role_result_source_invalid"
        assert correction["rejected_expression"]["beats"] == [{"beat_index": 0, "text": UNSUPPORTED_REPLY}]
        assert "缺少角色昨天经历的来源" in correction["failure_detail"]
        assert handler.reviews[1]["visible_beats"] == [{"beat_index": 0, "text": REPORT_REPLY}]
        # A definite verdict is never re-asked: one review per author attempt.
        assert len(handler.reviews) == 2
        assert (await app.drain_actions_once()).status == "settled"
        assert transport.bodies == [REPORT_REPLY]
        bound_receipt(app)


@pytest.mark.asyncio
async def test_unusable_reviewer_answer_is_reasked_once_without_losing_the_turn(tmp_path):
    """A reviewer answer that is not the declared contract carries no verdict.

    Real Flash emitted a repeated JSON member inside one fact object. That used
    to end the whole visible turn as an unexplained technical failure with no
    delivery and no correction. It must instead re-ask the same reviewer once
    inside the already-open validation phase, while the character is never
    re-asked and no unsupported content can pass.
    """

    handler = GroundedHTTP(fault="unusable_answer")
    async with application(tmp_path / "world.sqlite", handler) as (app, transport):
        outcome = await app.respond(replace(_inbound(), text=USER_TEXT))
        assert handler.callback_errors == []
        assert outcome.status == "action_authorized", (outcome, _audits(app))
        assert handler.authors == 1
        assert len(handler.reviews) == 2
        assert (await app.drain_actions_once()).status == "settled"
        assert transport.bodies == [REPORT_REPLY]
        bound_receipt(app)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing_evidence", "wrong_owner"])
async def test_positive_model_verdict_cannot_grant_missing_or_wrong_owner_permission(tmp_path, fault):
    handler = GroundedHTTP(fault=fault)
    async with application(tmp_path / "world.sqlite", handler) as (app, transport):
        outcome = await app.respond(replace(_inbound(), text=USER_TEXT))
        assert handler.callback_errors == []
        assert outcome.status != "action_authorized"
        assert handler.authors == 2 and len(handler.requests) == 4
        assert not app.export_replay_evidence().projection.actions
        assert (await app.drain_actions_once()).status == "idle"
        assert transport.bodies == []
        assert not any(audit.visible_source_review_json for audit in _audits(app))


@pytest.mark.asyncio
async def test_grounded_deadline_preserves_failed_subcall_and_unknown_usage(tmp_path, monkeypatch):
    from companion_daemon.world_v2 import visible_grounded_review_runtime as runtime

    complete = runtime.complete_with_timeout

    async def short_deadline(awaitable, *, timeout_seconds):
        assert timeout_seconds == 22.0
        return await complete(awaitable, timeout_seconds=0.02)

    monkeypatch.setattr(runtime, "complete_with_timeout", short_deadline)
    handler = GroundedHTTP(fault="timeout")
    async with application(tmp_path / "world.sqlite", handler) as (app, transport):
        outcome = await app.respond(replace(_inbound(), text=USER_TEXT))
        assert handler.callback_errors == []
        assert outcome.status != "action_authorized"
        assert handler.authors == 1 and len(handler.requests) == 2
        assert not app.export_replay_evidence().projection.actions
        assert transport.bodies == []
        subcalls = [audit for audit in _audits(app)
                    if audit.route.reason_code == "validation.source_review"]
        assert len(subcalls) == 1
        assert subcalls[0].outcome == "timeout"
        assert subcalls[0].failure_code == "provider_timeout"
        assert subcalls[0].parent_model_call_id
        assert subcalls[0].response_hash is None
        assert any(audit.failure_code == "source_review_timeout" for audit in _audits(app))
        assert not any(audit.visible_source_review_json for audit in _audits(app))
    with sqlite3.connect(tmp_path / "usage.sqlite") as db:
        assert db.execute(
            "SELECT billing_state,error FROM world_v2_model_usage WHERE purpose='inbound_source_review'",
        ).fetchall() == [("unknown", "provider_timeout")]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["connected", "disconnected", "expired"])
async def test_grounded_review_uses_its_fixed_phase_after_author_deadline(tmp_path, monkeypatch, phase):
    from companion_daemon.world_v2 import visible_grounded_review_runtime as runtime
    from companion_daemon.world_v2.interactive_turn_budget import InteractiveTurnBudgetPolicy

    class SlowReviewHTTP(GroundedHTTP):
        async def __call__(self, request):
            if "tools" not in json.loads(request.content):
                await asyncio.sleep(1.1)
            return await super().__call__(request)

    if phase == "disconnected":
        async def disconnected(operation, **_kwargs):
            return await operation()
        monkeypatch.setattr(runtime, "run_validation_review_once", disconnected)
    policy = InteractiveTurnBudgetPolicy(
        total_seconds=1.0, hedge_after_seconds=0.4,
        acceptance_dispatch_reserve_seconds=0.1,
        validation_recovery_seconds=0.05 if phase == "expired" else 3.0,
        validation_reselection_seconds=5.0,
    )
    handler = SlowReviewHTTP()
    async with application(tmp_path / "world.sqlite", handler, budget_policy=policy) as (app, transport):
        outcome = await app.respond(replace(_inbound(), text=USER_TEXT))
        assert handler.authors == 1
        if phase == "connected":
            assert outcome.status == "action_authorized", (outcome, _audits(app))
            assert len(handler.requests) == 2
            assert (await app.drain_actions_once()).status == "settled"
            assert transport.bodies == [REPORT_REPLY]
            bound_receipt(app)
        else:
            assert outcome.status != "action_authorized"
            assert not app.export_replay_evidence().projection.actions
            assert transport.bodies == []


@pytest.mark.asyncio
async def test_wrong_tool_identity_is_corrected_once_by_same_role(tmp_path):
    class WrongToolOnce(GroundedHTTP):
        async def respond(self, request):
            response = await super().respond(request)
            body = json.loads(request.content)
            if self.authors == 1 and not self.reviews:
                data = response.json()
                data["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = "wrong_function"
                return httpx.Response(200, json=data)
            return response

    handler = WrongToolOnce(review_protocol="visible-grounded-review.2")
    async with application(tmp_path / "wrong-name.sqlite", handler, review_version="25") as (app, transport):
        outcome = await app.respond(replace(_inbound(), text=USER_TEXT))
        assert handler.callback_errors == []
        assert outcome.status == "action_authorized", (outcome, _audits(app))
        assert handler.authors == 2 and len(handler.reviews) == 1
        assert "wrong_function" not in str(handler.requests[1]["tool_choice"])
        assert (await app.drain_actions_once()).status == "settled"
        assert transport.bodies == [REPORT_REPLY]
        evidence = app.export_replay_evidence()
        assert evidence.projection.semantic_hash == evidence.replay.semantic_hash
