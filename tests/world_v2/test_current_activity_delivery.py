"""Current activity authority through real HTTP serialization and public delivery."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json

import httpx
import pytest

from current_activity_fixture import accepted_current_activity
from world_v2_application import (
    build_sqlite_world_v2_test_application,
    compose_fixture_character_interior,
)
from test_character_interior_inbound_author import _ReplyOnlySourceClosureReviewer
from test_life_development_runtime import NOW
from test_production_turn_application import _config, _DeliveredTransport, _Identities, _Router
from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.character_interior.inbound_author import _InboundCharacterAuthor
from companion_daemon.world_v2.expression_draft import QQ_NAPCAT_EXPRESSION_CAPABILITIES
from companion_daemon.world_v2.world_turn_runtime import InboundTurn


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,claim_scope",
    [("active", "current_world"), ("resumed", "current_world"), ("active", "past_world")],
)
async def test_current_activity_http_claim_reaches_accepted_action_and_terminal_receipt(
    tmp_path, status, claim_scope
):
    path = tmp_path / "current-life.sqlite"
    bodies = []
    claim = (
        "我正在尝试看看校园征稿启事。"
        if claim_scope == "current_world"
        else "我已经写完了那篇随笔。"
    )

    async def respond(request):
        body = json.loads(request.content)
        bodies.append(body)
        material = json.loads(body["messages"][1]["content"])
        current = material["inner_life_snapshot"]["materials"]["current_activities"]
        assert len(current) == 1
        source_ref = next(
            alias
            for alias, canonical in material["expression_hard_boundaries"][
                "source_ref_aliases"
            ].items()
            if canonical == current[0]["source_ref"]
        )
        slim = {
            "messages": [claim],
            "meaning_of_this": "分享自己当前的尝试。",
            "my_state": "平静。",
            "world_claims": [
                {"claim_text": claim, "scope": claim_scope, "source_refs": [source_ref]}
            ],
        }
        raw = json.dumps(
            {"result_kind": "reply_only", "payload_json": json.dumps(slim, ensure_ascii=False)}
        )
        function = body["tool_choice"]["function"]["name"]
        value = {
            "tool_calls": [
                {
                    "index": 0,
                    "id": "offline-tool",
                    "type": "function",
                    "function": {"name": function, "arguments": raw},
                }
            ]
        }
        usage = {"prompt_tokens": 1000, "completion_tokens": 100}
        if body.get("stream"):
            chunks = [{"choices": [{"delta": value}]}, {"choices": [], "usage": usage}]
            data = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=data + "data: [DONE]\n\n",
            )
        return httpx.Response(200, json={"choices": [{"message": value}], "usage": usage})

    model = DeepSeekChatModel(
        "offline-fixture",
        "https://fixture.invalid",
        "deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(respond),
    )
    reviewer = _ReplyOnlySourceClosureReviewer()
    config = replace(
        _config(),
        world_id="world:current-activity-fixture",
        companion_actor_ref="actor:companion",
        expression_episode_mode="stream",
    )
    observed_at = NOW + (timedelta(minutes=10) if status == "resumed" else timedelta())
    transport = _DeliveredTransport(received_at=observed_at)

    def build(now):
        return build_sqlite_world_v2_test_application(
            path=path,
            config=config,
            identities=_Identities(),
            router=_Router(),
            character_interior=compose_fixture_character_interior(
                inbound_author=_InboundCharacterAuthor(
                    flash_model=model,
                    source_closure_model=reviewer,
                    expression_capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
                    require_explicit_authored_decision_fields=True,
                )
            ),
            transport=transport,
            now=now,
        )

    build(NOW - timedelta(minutes=1)).close()
    ledger, store, plan_id, event_ref = await accepted_current_activity(
        status=status, sqlite_path=path
    )
    event, _ = ledger.lookup_event_commit(event_ref)
    accepted_event = next(
        ref for ref in ledger.project().committed_world_event_refs if ref.event_id == event_ref
    )
    store.close()
    ledger.close()
    app = build(observed_at)
    try:
        outcome = await app.respond(
            InboundTurn(
                platform="test",
                platform_user_id="user.1",
                platform_message_id="current-activity",
                text="你现在在做什么？",
                observed_at=observed_at,
                trace_id="trace:current-activity-http",
            )
        )
        delivery = await app.drain_actions_once()
        projection = app._ledger.project()
        assert projection == app._ledger.rebuild()
        evidence = app.export_replay_evidence()
    finally:
        app.close()
        await model.aclose()

    if claim_scope == "past_world":
        assert outcome.status != "action_authorized"
        assert not transport.bodies
        assert not projection.actions
        chats = [
            json.loads(body["messages"][1]["content"])
            for body in bodies
            if "inner_life_snapshot" in json.loads(body["messages"][1]["content"])
        ]
        assert len(chats) == 2  # One constrained correction for the pinned character turn.
        assert (
            chats[0]["inner_life_snapshot"]["snapshot_id"]
            == chats[1]["inner_life_snapshot"]["snapshot_id"]
        )
        return
    assert outcome.status == "action_authorized", outcome
    assert delivery is not None and delivery.status == "settled"
    assert transport.bodies == [claim]
    assert len(bodies) == 1
    material = json.loads(bodies[0]["messages"][1]["content"])
    activity = material["inner_life_snapshot"]["materials"]["current_activities"][0]
    assert activity["plan_id"] == plan_id
    assert activity["active_since"] == event.logical_time.isoformat().replace("+00:00", "Z")
    assert "校园征稿启事" in activity["accepted_intention"]["text"]
    assert "未结算的结果绝不能进入当前聊天" not in json.dumps(material, ensure_ascii=False)
    assert projection.actions[-1].state == "delivered"
    assert projection.execution_receipts[-1].is_terminal
    assert evidence.projection == evidence.replay
    bound = [
        ref
        for audit in projection.proposal_audits
        for ref in json.loads(audit.proposal_json).get("evidence_refs", [])
        if ref["ref_id"] == event_ref
    ]
    assert bound
    assert all(ref["evidence_kind"] == "committed_world_event" for ref in bound)
    assert all(ref["source_world_revision"] == accepted_event.world_revision for ref in bound)
    selected_evidence = [
        ref
        for audit in projection.proposal_audits
        if audit.proposal_id.startswith("proposal:expression:")
        for ref in json.loads(audit.proposal_json)["evidence_refs"]
    ]
    assert {ref["ref_id"] for ref in selected_evidence} == {
        "observation:test:user.1:current-activity",
        event_ref,
    }  # Unselected Plan/Context bindings are not copied into the Proposal.
    assert all(ref["immutable_hash"].removeprefix("sha256:") == event.payload_hash for ref in bound)
    source = next(
        alias
        for alias, canonical in material["expression_hard_boundaries"]["source_ref_aliases"].items()
        if canonical == activity["source_ref"]
    )
    scopes = material["expression_hard_boundaries"]["world_claim_source_refs"]
    assert source in scopes["current_world"]
    assert source not in scopes["past_world"]
    assert source not in scopes["shared_history"]
