"""Completed activity truth through the public host and actual HTTP boundary."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from companion_daemon.world_v2.longitudinal_fixture_model import LongitudinalFixtureModel


INTENTION = "想花一会儿整理自己接下来要写的东西。"
ENDED = "这次整理写作思路的活动已经结束了。"


async def run_completed_journey(tmp_path, monkeypatch, *, scope="past_world", source_override=None):
    fixture = LongitudinalFixtureModel()
    bodies = []
    chats = []

    async def respond(request):
        body = json.loads(request.content)
        bodies.append(body)
        raw = await fixture.complete_json(
            body["messages"], tools=body.get("tools"), tool_choice=body.get("tool_choice")
        )
        value = json.loads(raw)
        if "payload_json" in value:
            context = json.loads(body["messages"][1]["content"])
            chats.append(context)
            authored = json.loads(value["payload_json"])
            authored["world_claims"] = []
            if len(chats) == 1:
                authored["messages"] = ["想留一点时间整理思路。"]
                authored["life_intent"] = {
                    "execution_scope": "self_directed",
                    "intention": INTENTION,
                    "start_after_seconds": 0,
                    "duration_seconds": 180,
                    "importance_bp": 6000,
                }
            else:
                ended = context["inner_life_snapshot"]["materials"]["recently_ended_activities"]
                assert len(ended) == 1
                authored.pop("life_intent", None)
                authored["messages"] = [ENDED]
                canonical = ended[0]["source_ref"]
                if source_override == "original_plan":
                    canonical = ended[0]["plan_id"].replace(
                        "plan:chat-life-intent:", "event:chat-life-intent:"
                    )
                aliases = context["expression_hard_boundaries"]["source_ref_aliases"]
                ref = next(
                    (alias for alias, actual in aliases.items() if actual == canonical), canonical
                )
                authored["world_claims"] = [
                    {"claim_text": ENDED, "scope": scope, "source_refs": [ref]}
                ]
            value["payload_json"] = json.dumps(authored, ensure_ascii=False)
            raw = json.dumps(value, ensure_ascii=False)
        else:
            for message in body["messages"]:
                try:
                    material = json.loads(message["content"])
                except (ValueError, TypeError):
                    continue
                if (
                    not isinstance(material, dict)
                    or material.get("inner_turn", {}).get("purpose") != "activity_lifecycle_choice"
                ):
                    continue
                capability = material["capability_manifest"]
                openings = capability["payload"].get("openings", [])
                if openings:
                    selected = next(
                        (
                            item
                            for item in openings
                            if item["safe_summary"].startswith(
                                "finish the current abstract activity"
                            )
                        ),
                        openings[0],
                    )
                    raw = json.dumps(
                        {
                            "status": "decision",
                            "summary": "我选择这个活动变化。",
                            "attended_source_refs": [],
                            "recall_query": None,
                            "proposals": [],
                            "decision": {
                                "source_refs": capability["source_refs"],
                                "payload": {
                                    "decision": "select",
                                    "selected_token": selected["opening_token"],
                                },
                            },
                        }
                    )
        function = body.get("tool_choice", {}).get("function", {}).get("name")
        wire = (
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "offline-tool",
                        "type": "function",
                        "function": {"name": function, "arguments": raw},
                    }
                ]
            }
            if function
            else {"content": raw}
        )
        usage = {"prompt_tokens": 1000, "completion_tokens": 100}
        if body.get("stream"):
            chunks = [{"choices": [{"delta": wire}]}, {"choices": [], "usage": usage}]
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content="".join("data: " + json.dumps(x) + "\n\n" for x in chunks)
                + "data: [DONE]\n\n",
            )
        return httpx.Response(200, json={"choices": [{"message": wire}], "usage": usage})

    monkeypatch.setenv("DEEPSEEK_DEBUG_API_KEY", "fixture-key-never-transmitted")
    monkeypatch.setenv("DEEPSEEK_CHARACTER_THINKING_ENABLED", "false")
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(respond))
    script = Path(__file__).parents[2] / "scripts/run_world_v2_longitudinal_audit.py"
    spec = importlib.util.spec_from_file_location("completed_activity_cli", script)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    scenario = tmp_path / "scenario.json"
    scenario.write_text(
        json.dumps(
            {
                "scenario_id": "completed-activity",
                "started_at": "2026-09-08T10:00:00+08:00",
                "duration_minutes": 5,
                "restart_minutes": [2],
                "turns": [
                    {"id": "plan", "at_minutes": 0, "text": "接下来想做什么？"},
                    {"id": "ended", "at_minutes": 4, "text": "刚才那件事后来怎么样了？"},
                ],
            }
        )
    )
    output = tmp_path / "journey"
    result = await cli.run(
        cli.parse_options(
            [
                "--scenario",
                str(scenario),
                "--output",
                str(output),
                "--model-mode",
                "real-provider",
                "--allow-real-provider",
                "--max-cost-cny",
                "0.5",
                "--max-wall-seconds",
                "30",
                "--heartbeat-seconds",
                "60",
            ]
        )
    )
    rows = [json.loads(line) for line in (output / "evidence.jsonl").read_text().splitlines()]
    for row in rows:
        row["payload"] = json.loads(row["payload_json"])
    return result, rows, chats, output


@pytest.mark.asyncio
async def test_completed_activity_is_readable_on_next_http_turn_and_exactly_source_bound(
    tmp_path, monkeypatch
):
    result, rows, chats, output = await run_completed_journey(tmp_path, monkeypatch)
    completed = [row for row in rows if row["event_type"] == "ActivityCompleted"]
    assert len(completed) == 1
    assert len(chats) >= 2
    material = chats[1]["inner_life_snapshot"]["materials"]
    ended = material.get("recently_ended_activities", [])
    assert len(ended) == 1
    assert ended[0]["source_ref"] == completed[0]["event_id"]
    assert ended[0]["completion_scope"] == "activity_lifecycle_ended_not_intention_fulfilled"
    assert ended[0]["accepted_intention"]["text"] == INTENTION
    assert not material.get("current_activities")
    assert result["turns_consumed"] == 2
    assert not result["model_failures"]
    assert result["replay"]["replay_hash_matches"]
    assert not result["replay"]["findings"]
    assert result["restarts"][0]["same_state"]
    assert not any(
        row["event_type"] in {"WorldOccurrenceCommitted", "ExperienceCommitted"} for row in rows
    )
    assert not any(
        key in ended[0]
        for key in ("location_ref", "participant_refs", "result_id", "photo_in_hand", "outcome")
    )
    assert material["recent_self_experiences"] == {"availability": "unavailable"}
    aliases = chats[1]["expression_hard_boundaries"]["source_ref_aliases"]
    ref = next(
        alias for alias, canonical in aliases.items() if canonical == completed[0]["event_id"]
    )
    scopes = chats[1]["expression_hard_boundaries"]["world_claim_source_refs"]
    assert ref in scopes["past_world"]
    assert all(ref not in values for scope, values in scopes.items() if scope != "past_world")
    proposals = [
        row
        for row in rows
        if row["event_type"] == "ProposalRecorded"
        and row["payload"].get("proposal_id", "").startswith("proposal:expression:")
    ]
    selected = json.loads(proposals[-1]["payload"]["proposal_json"])["evidence_refs"]
    fact = next(item for item in selected if item["ref_id"] == completed[0]["event_id"])
    assert fact["evidence_kind"] == "committed_world_event"
    assert fact["immutable_hash"].removeprefix("sha256:") == completed[0]["payload_hash"]
    ledger = open_journey_ledger(output)
    try:
        exact = next(
            ref
            for ref in ledger.project().committed_world_event_refs
            if ref.event_id == completed[0]["event_id"]
        )
        assert fact["source_world_revision"] == exact.world_revision
    finally:
        ledger.close()
    assert len(selected) == 2  # Exact ending plus this inbound observation only.
    assert next(item for item in selected if item != fact)["evidence_kind"] == "observed_message"
    actions = [row for row in rows if row["event_type"] == "ActionAuthorized"]
    assert len(actions) == 2
    action_id = actions[-1]["payload"]["action"]["action_id"]
    assert actions[-1]["payload"]["action"]["causation_id"] == proposals[-1]["event_id"]
    receipts = [
        row["payload"]["receipt"]
        for row in rows
        if row["event_type"] == "ExecutionReceiptRecorded"
        and row["payload"]["receipt"]["action_id"] == action_id
    ]
    assert receipts and receipts[0]["observed_state"] == "provider_accepted"
    # The isolated capture transport acknowledges receipt; this is not a real
    # QQ terminal receipt or a qualification of every journey due owner.
    assert receipts[0]["is_terminal"] is False
    assert result["completed"], result["stop_reason"]


def open_journey_ledger(output):
    from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger

    return SQLiteWorldLedger(
        path=output / "world.sqlite", world_id="world:companion-v2:qq-c2c:longitudinal-audit"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope,source_override", [("current_world", None), ("past_world", "original_plan")]
)
async def test_completed_activity_http_rejects_wrong_scope_or_original_intention_source(
    tmp_path, monkeypatch, scope, source_override
):
    _, rows, chats, _ = await run_completed_journey(
        tmp_path, monkeypatch, scope=scope, source_override=source_override
    )
    assert len([row for row in rows if row["event_type"] == "ActivityCompleted"]) == 1
    assert len(chats) >= 3  # Invalid claim and one constrained same-character correction.
    assert (
        chats[1]["inner_life_snapshot"]["snapshot_id"]
        == chats[2]["inner_life_snapshot"]["snapshot_id"]
    )
    assert len([row for row in rows if row["event_type"] == "ActionAuthorized"]) == 1
    assert any(
        row["event_type"] == "ModelResultRecorded"
        and json.loads(row["payload"]["audit_json"]).get("failure_code")
        == "paired_expression_reselection_invalid"
        for row in rows
    )


@pytest.mark.asyncio
async def test_completed_context_replay_privacy_and_exact_source_matrix(tmp_path, monkeypatch):
    from test_current_activity_context import current_context
    from test_character_interior_inbound_author import _request
    from companion_daemon.world_v2.chat_life_intent_runtime import ChatLifeIntentCompletedReader
    from companion_daemon.world_v2.expression_draft import (
        materialize_expression_draft,
        QQ_NAPCAT_EXPRESSION_CAPABILITIES,
        world_claim_source_refs_by_scope,
    )
    from companion_daemon.world_v2.schemas import ProjectionCursor

    _, rows, _, output = await run_completed_journey(tmp_path, monkeypatch)
    ledger = open_journey_ledger(output)
    try:
        before = ledger.export_replay_evidence()
        capsule, compact, snapshot = current_context(ledger, None, actor_ref="agent:companion")
        full = json.loads(capsule.model_content_json)
        item = next(
            item
            for item in full["slices"]["world_life"]["items"]
            if item["value"].get("context_kind") == "completed_activity"
        )
        value = item["value"]
        completed_ref = value["activity_event_ref"]
        planned_ref = next(
            binding["ref"]
            for binding in item["source_bindings"]
            if binding["authority_type"] == "ActivityPlanned"
        )
        request = _request(revision=before.projection.world_revision, call="completed-fact-matrix")
        invalid = {
            planned_ref,
            value["plan_id"],
            value["owner_actor_ref"],
            value["accepted_intention"]["content_ref"],
            value["accepted_intention"]["content_payload_hash"],
            item["source_hash"],
            item["value_hash"],
            "location:invented",
            "photo:invented",
            "actor:invented",
        }
        draft = {
            "private_turn_state": {
                "contract": "private-turn-state.1",
                "inner_state_summary": "我注意到这件活动结束了。",
                "attended_source_refs": [],
            },
            "beats": [{"modality": "text", "text": ENDED}],
            "stance": "平静",
            "brief_rationale": "回应刚才的事。",
        }
        for context in (full, compact):
            allowed = world_claim_source_refs_by_scope(context=context)
            assert completed_ref in allowed["past_world"]
            assert completed_ref not in allowed["current_world"]
            assert not invalid.intersection(allowed["past_world"])
            scoped_request = request.model_copy(update={"model_content_json": json.dumps(context)})
            for ref in invalid:
                with pytest.raises(ValueError, match="semantic source lane"):
                    materialize_expression_draft(
                        value={
                            **draft,
                            "world_claims": [
                                {"claim_text": ENDED, "scope": "past_world", "source_refs": [ref]}
                            ],
                        },
                        request=scoped_request,
                        capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
                    )
            materialized = materialize_expression_draft(
                value={
                    **draft,
                    "world_claims": [
                        {"claim_text": ENDED, "scope": "past_world", "source_refs": [completed_ref]}
                    ],
                },
                request=scoped_request,
                capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
            )
            assert any(
                ref.ref_id == completed_ref and ref.evidence_kind == "committed_world_event"
                for ref in materialized.evidence_refs
            )
        from companion_daemon.world_v2.background_context_profile import (
            background_context_profile_for_purpose,
            slice_background_capsule_context,
            slice_background_inner_life_snapshot,
        )

        world_author = background_context_profile_for_purpose("life_development_draft")
        external_capsule = slice_background_capsule_context(full, world_author)
        external_snapshot = slice_background_inner_life_snapshot(
            snapshot.model_view(), world_author
        )
        assert not any(
            item.get("value", {}).get("context_kind") == "completed_activity"
            for item in external_capsule["slices"]["world_life"]["items"]
        )
        assert "recently_ended_activities" not in external_snapshot["materials"]
        assert not snapshot.materials.get("moments_i_can_share", {}).get("items")
        assert not snapshot.materials.get("week_diary", {}).get("entries")
        _, _, other = current_context(ledger, None, actor_ref="actor:other")
        assert not other.materials.get("recently_ended_activities")
        projection = ledger.project()
        cursor = ProjectionCursor(
            world_revision=projection.world_revision,
            deliberation_revision=projection.deliberation_revision,
            ledger_sequence=projection.ledger_sequence,
        )
        reader = ChatLifeIntentCompletedReader(ledger=ledger)
        kwargs = dict(
            plan_id=value["plan_id"],
            expected_cursor=cursor,
            actor_ref="agent:companion",
            viewer_privacy_ceiling="private",
        )
        assert reader.read_completed_plan(**kwargs) is not None
        for overrides in (
            {"actor_ref": "actor:other"},
            {"viewer_privacy_ceiling": "shareable"},
            {
                "expected_cursor": cursor.model_copy(
                    update={"ledger_sequence": cursor.ledger_sequence + 1}
                )
            },
        ):
            assert reader.read_completed_plan(**{**kwargs, **overrides}) is None
        assert ledger.export_replay_evidence() == before
        assert before.projection == before.replay
    finally:
        ledger.close()
    # Cold reopen of the accepted event stream regenerates the same view.
    ledger = open_journey_ledger(output)
    try:
        _, _, reopened = current_context(ledger, None, actor_ref="agent:companion")
        assert reopened == snapshot
    finally:
        ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["planned", "paused"])
async def test_planned_or_paused_activity_does_not_become_ended_fact(status):
    from current_activity_fixture import accepted_current_activity
    from test_current_activity_context import current_context
    from companion_daemon.world_v2.expression_draft import world_claim_source_refs_by_scope

    ledger, store, _, event_ref = await accepted_current_activity(status=status)
    _, context, snapshot = current_context(ledger, store)
    assert not snapshot.materials.get("recently_ended_activities")
    assert event_ref not in world_claim_source_refs_by_scope(context=context)["past_world"]
