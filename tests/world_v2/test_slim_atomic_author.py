"""The slim atomic author carrier: same decision, mechanical envelope in the host."""

import json
from contextlib import asynccontextmanager
from dataclasses import replace

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.character_interior.inbound_author import _InboundCharacterAuthor
from companion_daemon.world_v2.expression_draft import QQ_NAPCAT_EXPRESSION_CAPABILITIES
from companion_daemon.world_v2.visible_independent_review_configuration import GroundedVisibleReviewer
from world_v2_application import (
    build_sqlite_world_v2_test_application,
    compose_fixture_character_interior,
)
from test_production_turn_application import NOW, _config, _DeliveredTransport, _Identities, _Router
from test_whole_candidate_author import BEATS, _inbound
from test_world_stimulus_life_intent import _http_result

MEANING = "他就是想让我自己说说想说什么。"
MY_STATE = "松下来一点，想把两句想法完整表达出来。"


def _slim(*, silent=False):
    return {
        "messages": [] if silent else list(BEATS),
        "meaning_of_this": MEANING,
        "my_state": MY_STATE,
        "world_claims": [],
    }


class _SlimHTTP:
    """Answer the slim carrier, then the grounded review of what she chose."""

    def __init__(self, *, first_payload=None):
        self.requests = []
        self.first_payload = first_payload
        self.authors = 0
        self.reviews = 0

    async def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        assert not body.get("stream"), "slim atomic author unexpectedly streamed"
        if "tools" not in body:
            return self._review(body)
        parameters = body["tools"][0]["function"]["parameters"]
        assert set(parameters["properties"]) == {"result_kind", "payload_json"}
        self.authors += 1
        payload = self.first_payload if self.authors == 1 else None
        inner = _slim() if payload is None else payload
        return _http_result(body, {
            "result_kind": "decision",
            "payload_json": json.dumps(inner, ensure_ascii=False),
        })

    def _review(self, body):
        packet = json.loads(body["messages"][1]["content"])
        assert packet["contract"] == "visible-grounded-review.1"
        self.reviews += 1
        decisions = [
            {
                "beat_index": beat["beat_index"],
                "review_complete": True,
                "facts": [],
                "non_record_expressions": [
                    {"text": beat["text"], "rationale": "本轮即时回应，没有既往经历断言"}
                ],
                "unresolved_details": [],
            }
            for beat in packet["visible_beats"]
        ]
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": json.dumps({
                "contract": "visible-grounded-review.1", "beat_decisions": decisions,
            }, ensure_ascii=False)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
        })


@asynccontextmanager
async def _application(tmp_path, monkeypatch, handler):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    models = [
        DeepSeekChatModel(
            "offline-fixture", "https://fixture.invalid", "deepseek-v4-flash",
            thinking_enabled=False, transport=httpx.MockTransport(handler),
        )
        for _ in range(2)
    ]
    capabilities = QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
        update={"private_turn_state_mode": "required"},
    )
    author = _InboundCharacterAuthor(
        flash_model=models[0],
        expression_capabilities=capabilities,
        require_explicit_authored_decision_fields=True,
        whole_candidate_mode=True,
        atomic_tool_envelope_version="slim",
        visible_source_review_model=GroundedVisibleReviewer(source_model=models[1]),
        visible_source_review_version="24",
    )
    app = build_sqlite_world_v2_test_application(
        path=tmp_path / "slim-atomic.sqlite",
        config=replace(
            _config(), expression_capabilities=capabilities, expression_episode_mode="off",
            visible_source_review_required=True,
        ),
        identities=_Identities(),
        router=_Router(),
        character_interior=compose_fixture_character_interior(inbound_author=author),
        transport=_DeliveredTransport(),
        now=NOW,
    )
    try:
        yield app
    finally:
        app.close()
        await __import__("asyncio").gather(*(model.aclose() for model in models))


def _proposals(evidence):
    return [
        json.loads(row.event.payload()["candidate_json"])
        for row in evidence.events
        if row.event.event_type == "ProposalRecorded"
    ]


@pytest.mark.asyncio
async def test_slim_carrier_authors_the_same_decision_and_delivers_every_bubble(
    tmp_path, monkeypatch
):
    handler = _SlimHTTP()
    async with _application(tmp_path, monkeypatch, handler) as app:
        outcome = await app.respond(_inbound())
        evidence = app.export_replay_evidence()
        assert (await app.drain_actions_once()).status == "settled"

    assert outcome.status == "action_authorized", outcome
    assert handler.authors == 1 and handler.reviews == 1
    body = handler.requests[0]
    assert body["tool_choice"]["function"]["name"] == "character_inbound_initial_slim"
    # The carrier itself is the whole provider schema.
    assert len(json.dumps(body["tools"], ensure_ascii=False)) < 1_500
    assert len(evidence.projection.actions) == len(BEATS)
    assert tuple(item.text for item in evidence.projection.stored_message_payloads) == BEATS


def test_slim_carrier_keeps_her_words_and_supplies_only_the_mechanical_siblings():
    """Her reading, her feeling, her bubbles and her timing are hers.

    Everything the host adds is the envelope: the contract constant, the beat
    modality, the bounded-rationale copies and the always-empty source list.
    """

    from companion_daemon.world_v2.character_interior.inbound_tool_contract import (
        expand_atomic_slim_payload,
    )

    compiled = expand_atomic_slim_payload(
        {"result_kind": "decision", "payload_json": json.dumps(_slim(), ensure_ascii=False)},
        recall_allowed=True,
    )
    appraisal, expression = compiled["appraisal_draft"], compiled["expression_draft"]
    assert [item["meaning"] for item in appraisal["meanings"]] == [MEANING]
    assert appraisal["brief_rationale"] == MEANING
    assert appraisal["behavior_tendency"] == MY_STATE[:64]
    assert expression["private_turn_state"]["inner_state_summary"] == MY_STATE
    assert expression["private_turn_state"]["contract"] == "private-turn-state.1"
    assert [beat["text"] for beat in expression["beats"]] == list(BEATS)
    assert [beat["modality"] for beat in expression["beats"]] == ["text"] * len(BEATS)
    assert expression["timing_choice"] == "now"
    silent = expand_atomic_slim_payload(
        {
            "result_kind": "decision",
            "payload_json": json.dumps(_slim(silent=True), ensure_ascii=False),
        },
        recall_allowed=True,
    )
    assert silent["expression_draft"]["timing_choice"] == "silent"
    assert silent["expression_draft"]["beats"] == []
    assert appraisal["affect"] == "no_change"


@pytest.mark.asyncio
async def test_slim_carrier_keeps_silence_a_real_choice(tmp_path, monkeypatch):
    handler = _SlimHTTP(first_payload=_slim(silent=True))
    async with _application(tmp_path, monkeypatch, handler) as app:
        outcome = await app.respond(_inbound())
        evidence = app.export_replay_evidence()
    assert outcome.status != "action_authorized"
    assert handler.reviews == 0
    assert evidence.projection.actions == ()
    assert evidence.projection.stored_message_payloads == ()


@pytest.mark.asyncio
async def test_invalid_slim_payload_uses_the_existing_bounded_same_role_correction(
    tmp_path, monkeypatch
):
    handler = _SlimHTTP(first_payload={"messages": ["只有这一句"]})
    async with _application(tmp_path, monkeypatch, handler) as app:
        outcome = await app.respond(_inbound())
        evidence = app.export_replay_evidence()
    assert handler.authors == 2, "the role must be asked to choose again exactly once"
    author_bodies = [body for body in handler.requests if "tools" in body]
    first, corrected = (body["messages"][0]["content"] for body in author_bodies)
    assert corrected != first
    assert "上一轮结果未通过校验" in corrected
    assert "meaning_of_this" in corrected, "the exact slim failure must reach her"
    assert outcome.status == "action_authorized", outcome
    assert tuple(item.text for item in evidence.projection.stored_message_payloads) == BEATS


def test_slim_carrier_rejects_the_schema_reference_options():
    from companion_daemon.world_v2.character_interior.inbound_tool_contract import InboundToolContracts

    with pytest.raises(ValueError, match="schema-reference"):
        InboundToolContracts().contract_for(
            phase="initial", transport="atomic", atomic_envelope_version="slim",
            capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES, recall_allowed=True,
            use_schema_references=True,
        )


def test_slim_carrier_is_far_smaller_than_the_strict_dual_draft_schema():
    from companion_daemon.world_v2.character_interior.inbound_tool_contract import InboundToolContracts

    contracts = InboundToolContracts()
    sizes = {}
    for version in ("3", "slim"):
        contract = contracts.contract_for(
            phase="initial", transport="atomic", atomic_envelope_version=version,
            capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES, recall_allowed=True,
            schema_dialect="deepseek-strict",
        )
        sizes[version] = len(json.dumps(contract.provider_tools, ensure_ascii=False))
    assert sizes["slim"] * 20 < sizes["3"], sizes
