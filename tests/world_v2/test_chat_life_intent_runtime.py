"""Explicit same-turn life intentions, never actions inferred from dialogue."""

from __future__ import annotations

import json

import pytest

from companion_daemon.world_v2.character_interior.inbound_tool_contract import InboundToolContracts
from companion_daemon.world_v2.expression_draft import QQ_NAPCAT_EXPRESSION_CAPABILITIES

INTENT = {
    "execution_scope": "self_directed",
    "intention": "想花一会儿整理自己接下来要写的东西。",
    "start_after_seconds": 0,
    "duration_seconds": 1800,
    "importance_bp": 6000,
}


@pytest.mark.parametrize("result_kind", ["reply_only", "full_turn"])
@pytest.mark.parametrize("silent", [False, True])
def test_chat_carrier_preserves_explicit_life_intent(result_kind, silent):
    contract = InboundToolContracts().compact_gate_for(
        capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
        recall_allowed=False,
    )
    value = contract.decode(
        json.dumps(
            {
                "result_kind": result_kind,
                "payload_json": json.dumps(
                    {
                        "messages": [] if silent else ["等会儿想整理一下写作思路。"],
                        "meaning_of_this": "他在问我接下来想做什么。",
                        "my_state": "想给自己留一点时间。",
                        "world_claims": [],
                        "life_intent": INTENT,
                    }
                ),
            }
        )
    )
    if result_kind == "full_turn":
        value = json.loads(value["full_turn_json"])
    assert value["appraisal_draft"]["life_intent"] == INTENT


async def _run_http_journey(
    tmp_path,
    monkeypatch,
    *,
    result_kind="reply_only",
    intent=INTENT,
    silent=False,
    repair=False,
    no_appraisal=False,
    followup=False,
    repair_after_calls=1,
    broken_appraisal=False,
    duration_minutes=None,
    vary_reply=False,
    prefer_complete=False,
):
    import importlib.util
    from pathlib import Path
    import httpx
    from companion_daemon.world_v2.longitudinal_fixture_model import LongitudinalFixtureModel

    fixture = LongitudinalFixtureModel()
    bodies = []
    chat_calls = 0

    async def respond(request):
        nonlocal chat_calls
        body = json.loads(request.content)
        bodies.append(body)
        raw = await fixture.complete_json(
            body["messages"], tools=body.get("tools"), tool_choice=body.get("tool_choice")
        )
        value = json.loads(raw)
        if "payload_json" in value:
            authored = json.loads(value["payload_json"])
            chat_calls += 1
            authored["messages"] = (
                []
                if silent
                else [
                    "我想给自己留一点时间。"
                    if not vary_reply or chat_calls == 1
                    else "想腾点空整理思路。"
                ]
            )
            authored["world_claims"] = []
            authored["wants"] = INTENT["intention"]
            chosen_intent = INTENT if repair and chat_calls > repair_after_calls else intent
            if chosen_intent is not None:
                authored["life_intent"] = chosen_intent
            if followup and chat_calls > 1:
                authored.pop("life_intent", None)
                context = next(
                    json.loads(m["content"])
                    for m in body["messages"]
                    if m["content"].startswith("{")
                    and "inner_life_snapshot" in json.loads(m["content"])
                )
                active = context["inner_life_snapshot"]["materials"]["current_activities"][0]
                authored["messages"] = ["这会儿在整理写作思路。"]
                authored["world_claims"] = [
                    {
                        "claim_text": authored["messages"][0],
                        "scope": "current_world",
                        "source_refs": [active["source_ref"]],
                    }
                ]
            value["result_kind"] = result_kind
            if no_appraisal or broken_appraisal:
                from companion_daemon.world_v2.present_prompt import compile_slim_interior_envelope

                authored = compile_slim_interior_envelope(
                    authored, reply_only=result_kind == "reply_only"
                )
                if no_appraisal:
                    authored["appraisal_draft"].update(
                        appraise=False, meanings=None, attribution=None, severity=None
                    )
                if broken_appraisal:
                    authored["appraisal_draft"]["confidence"] = "not a number"
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
                    selected = (
                        next(
                            (
                                x
                                for x in openings
                                if x["safe_summary"].startswith(
                                    "finish the current abstract activity"
                                )
                            ),
                            openings[0],
                        )
                        if prefer_complete
                        else openings[0]
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
                        "id": "fixture-tool",
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
    spec = importlib.util.spec_from_file_location("life_intent_journey_cli", script)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    scenario = tmp_path / "scenario.json"
    scenario.write_text(
        json.dumps(
            {
                "scenario_id": "chat-life-intent",
                "started_at": "2026-09-08T10:00:00+08:00",
                "duration_minutes": duration_minutes or (5 if followup else 3),
                "restart_minutes": [2],
                "turns": [{"id": "plan", "at_minutes": 0, "text": "接下来想做什么？"}]
                + (
                    [{"id": "current", "at_minutes": 3, "text": "现在在做什么？"}]
                    if followup
                    else []
                ),
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
    events = [json.loads(line) for line in (output / "evidence.jsonl").read_text().splitlines()]
    for event in events:
        event["payload"] = json.loads(event["payload_json"])
    return result, events, bodies, output, chat_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("result_kind", ["reply_only", "full_turn"])
@pytest.mark.parametrize("silent", [False, True])
async def test_http_chat_intent_reaches_plan_and_existing_lifecycle(
    tmp_path, monkeypatch, result_kind, silent
):
    result, events, bodies, output, chat_calls = await _run_http_journey(
        tmp_path,
        monkeypatch,
        result_kind=result_kind,
        silent=silent,
        no_appraisal=silent,
    )
    assert result["completed"], result["stop_reason"]
    plans = [x for x in events if x["event_type"] == "ActivityPlanned"]
    assert len(plans) == 1
    assert plans[0]["payload"]["plan"]["status"] == "planned"
    origin = plans[0]["payload"]["chat_intent_origin"]
    assert origin["inner_turn_id"]
    assert origin["proposal_event_ref"] in {
        x["event_id"] for x in events if x["event_type"] == "ProposalRecorded"
    }
    started = [x for x in events if x["event_type"] == "ActivityStarted"]
    assert len(started) == 1
    assert started[0]["payload"]["plan_id"] == plans[0]["payload"]["plan"]["plan_id"]
    assert not [
        x
        for x in events
        if x["event_type"]
        in {"ExperienceCommitted", "WorldOccurrenceCommitted", "ActivityCompleted"}
    ]
    assert chat_calls == 1
    assert any(INTENT["intention"][:30] in json.dumps(x, ensure_ascii=False) for x in bodies[1:])
    assert bool([x for x in events if x["event_type"] == "ActionAuthorized"]) is not silent


@pytest.mark.asyncio
@pytest.mark.parametrize("result_kind", ["reply_only", "full_turn"])
@pytest.mark.parametrize("repair", [False, True])
async def test_http_invalid_intent_gets_one_same_role_correction(
    tmp_path, monkeypatch, result_kind, repair
):
    invalid = {**INTENT, "execution_scope": "generate_world_event"}
    result, events, bodies, output, chat_calls = await _run_http_journey(
        tmp_path,
        monkeypatch,
        result_kind=result_kind,
        intent=invalid,
        repair=repair,
    )
    primary = []
    for body in bodies:
        if (
            body.get("tool_choice", {}).get("function", {}).get("name")
            != "character_inbound_compact_gate_v2"
        ):
            continue
        for message in body["messages"]:
            try:
                material = json.loads(message["content"])
            except (ValueError, TypeError):
                continue
            if isinstance(material, dict) and "inner_life_snapshot" in material:
                primary.append(material["inner_life_snapshot"])
    corrections = [x for x in primary if "role_result_correction" in x]
    if repair:
        assert chat_calls == 2
        assert len(primary) == 2 and len(corrections) == 1
    else:
        # Existing backup and later technical retries remain separate from
        # the one bounded correction within each original InnerTurn.
        assert len(primary) == 4 and len(corrections) == 2
        assert chat_calls == 6
    assert all(
        "execution_scope" in x["role_result_correction"]["failure_detail"] for x in corrections
    )
    plans = [x for x in events if x["event_type"] == "ActivityPlanned"]
    assert len(plans) == (1 if repair else 0)
    if not repair:
        assert result["model_failures"]
        assert not [x for x in events if x["event_type"] == "ActionAuthorized"]


@pytest.mark.asyncio
async def test_http_wishes_and_messages_alone_create_no_life_plan(tmp_path, monkeypatch):
    result, events, _, _, calls = await _run_http_journey(tmp_path, monkeypatch, intent=None)
    assert result["completed"]
    assert calls == 1
    assert not [x for x in events if x["event_type"] in {"ActivityPlanned", "ActivityStarted"}]


@pytest.mark.parametrize("result_kind", ["reply_only", "full_turn"])
@pytest.mark.parametrize(
    "bad",
    [
        {**INTENT, "execution_scope": "generate_world_event"},
        {**INTENT, "location_ref": "location:invented-campus"},
        {**INTENT, "participant_refs": ["npc:anyone"]},
        {**INTENT, "duration_seconds": True},
        {**INTENT, "intention": " "},
        {**INTENT, "outcome": "已经拍到了落叶"},
    ],
)
def test_gate_rejects_invalid_life_intent_instead_of_silently_omitting_it(result_kind, bad):
    contract = InboundToolContracts().compact_gate_for(
        capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
        recall_allowed=False,
    )
    with pytest.raises(ValueError):
        contract.decode(
            json.dumps(
                {
                    "result_kind": result_kind,
                    "payload_json": json.dumps(
                        {
                            "messages": ["我想给自己留点时间。"],
                            "meaning_of_this": "他问我的安排。",
                            "my_state": "想动一动。",
                            "world_claims": [],
                            "life_intent": bad,
                        }
                    ),
                }
            )
        )


@pytest.mark.asyncio
async def test_started_chat_intent_is_readable_and_citable_after_restart(tmp_path, monkeypatch):
    result, events, bodies, output, chat_calls = await _run_http_journey(
        tmp_path, monkeypatch, followup=True
    )
    assert result["completed"], result["stop_reason"]
    assert chat_calls == 2
    started = next(x for x in events if x["event_type"] == "ActivityStarted")
    delivered = [json.loads(line) for line in (output / "timeline.jsonl").read_text().splitlines()]
    assert any(
        item["text"] == "这会儿在整理写作思路。" for row in delivered for item in row["deliveries"]
    )
    assert any(started["event_id"] in json.dumps(x, ensure_ascii=False) for x in bodies)
    assert len([x for x in events if x["event_type"] == "ActivityPlanned"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("reject_appraisal", [False, True])
async def test_life_facet_recovers_after_cas_without_reauthoring_chat(
    tmp_path, monkeypatch, reject_appraisal
):
    from companion_daemon.world_v2.chat_life_intent_runtime import ChatLifeIntentRuntime
    from companion_daemon.world_v2.errors import ConcurrencyConflict
    from companion_daemon.world_v2.immediate_emotion_proposal_worker import (
        ImmediateEmotionProposalWorker,
    )

    accept = ChatLifeIntentRuntime.accept
    calls = 0

    def race_once(self, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConcurrencyConflict("fixture intervening commit")
        return accept(self, **kwargs)

    monkeypatch.setattr(ChatLifeIntentRuntime, "accept", race_once)
    appraisal_calls = 0
    if reject_appraisal:

        def reject(*args, **kwargs):
            nonlocal appraisal_calls
            appraisal_calls += 1
            raise ValueError("fixture appraisal cannot be accepted")

        monkeypatch.setattr(ImmediateEmotionProposalWorker, "process", reject)
    result, events, _, _, chat_calls = await _run_http_journey(
        tmp_path,
        monkeypatch,
        no_appraisal=not reject_appraisal,
    )
    assert result["completed"], result["stop_reason"]
    assert chat_calls == 1
    assert calls == 2
    failures = [x for x in events if x["event_type"] == "ChatLifeIntentAcceptanceFailed"]
    assert len(failures) == 1
    assert failures[0]["payload"]["failure_code"] == "cursor_conflict"
    assert failures[0]["payload"]["terminal"] is False
    assert len([x for x in events if x["event_type"] == "ActivityPlanned"]) == 1
    assert len([x for x in events if x["event_type"] == "ActivityStarted"]) == 1
    if reject_appraisal:
        assert appraisal_calls == 1


@pytest.mark.asyncio
async def test_chat_plan_replay_rejects_detached_origin_and_owner_and_is_effect_once(
    tmp_path, monkeypatch
):
    from companion_daemon.world_v2.chat_life_intent_runtime import ChatLifeIntentRuntime
    from companion_daemon.world_v2.reducers import ReducerState, reduce_event
    from companion_daemon.world_v2.schemas import ProjectionCursor, WorldEvent
    from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger

    result, rows, _, output, _ = await _run_http_journey(tmp_path, monkeypatch)
    assert result["completed"]
    state = ReducerState()
    plan_event = None
    for row in rows:
        value = {k: v for k, v in row.items() if k not in {"ledger_sequence", "payload"}}
        event = WorldEvent.model_validate_json(json.dumps(value))
        if event.event_type == "ActivityPlanned":
            plan_event = event
            break
        state = reduce_event(state, event)
    assert plan_event is not None
    assert len(reduce_event(state, plan_event).plans) == 1
    cases = []
    missing = plan_event.payload()
    missing.pop("chat_intent_origin")
    cases.append((missing, plan_event.actor))
    cases.append((plan_event.payload(), "agent:another"))
    detached = plan_event.payload()
    detached["chat_intent_origin"]["model_call_id"] = "model-call:another"
    cases.append((detached, plan_event.actor))
    invented = plan_event.payload()
    invented["plan"]["importance_bp"] = 1
    cases.append((invented, plan_event.actor))
    for payload, actor in cases:
        envelope = plan_event.model_dump(exclude={"payload_hash", "payload_json"})
        forged = WorldEvent.from_payload(**{**envelope, "actor": actor}, payload=payload)
        with pytest.raises(ValueError, match="chat_life_intent"):
            reduce_event(state, forged)
    ledger = SQLiteWorldLedger(path=output / "world.sqlite", world_id=plan_event.world_id)
    try:
        origin = plan_event.payload()["chat_intent_origin"]
        proposal_commit = ledger.lookup_event_commit(origin["proposal_event_ref"])[1]
        cursor = ProjectionCursor(
            world_revision=proposal_commit.world_revision,
            deliberation_revision=proposal_commit.deliberation_revision,
            ledger_sequence=proposal_commit.ledger_sequence,
        )
        before = ledger.project()
        runtime = ChatLifeIntentRuntime(ledger=ledger, owner_actor_ref=plan_event.actor)
        committed = runtime.accept(
            world_id=ledger.world_id, audit_cursor=cursor, proposal_id=origin["proposal_id"]
        )
        assert committed.event_ids == (plan_event.event_id,)
        assert ledger.project().semantic_hash == before.semantic_hash
        assert ledger.rebuild().semantic_hash == before.semantic_hash
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_reply_only_appraisal_repair_keeps_the_valid_life_intent(tmp_path, monkeypatch):
    result, events, _, _, calls = await _run_http_journey(
        tmp_path, monkeypatch, broken_appraisal=True
    )
    assert result["completed"], result["stop_reason"]
    assert calls == 1
    assert len([x for x in events if x["event_type"] == "ActivityPlanned"]) == 1


@pytest.mark.asyncio
async def test_first_accepted_life_choice_after_retry_uses_its_new_snapshot_clock(
    tmp_path, monkeypatch
):
    result, events, _, _, calls = await _run_http_journey(
        tmp_path,
        monkeypatch,
        intent={**INTENT, "execution_scope": "generate_world_event"},
        repair=True,
        repair_after_calls=3,
    )
    assert result["completed"], result["stop_reason"]
    assert calls == 4
    plan = next(x["payload"] for x in events if x["event_type"] == "ActivityPlanned")
    assert plan["chat_intent_origin"]["selected_at"] == "2026-09-08T02:00:30Z"
    assert plan["plan"]["scheduled_window"]["opens_at"] == "2026-09-08T02:00:30Z"
    assert plan["plan"]["scheduled_window"]["closes_at"] == "2026-09-08T02:30:30Z"


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_forever", [False, True])
async def test_acceptance_failure_journal_survives_restart_and_stops_busy_retry(
    tmp_path, monkeypatch, fail_forever
):
    from companion_daemon.world_v2.chat_life_intent_runtime import ChatLifeIntentRuntime
    from companion_daemon.world_v2.ledger import ConcurrencyConflict
    from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger

    original = ChatLifeIntentRuntime.accept
    attempts = []

    def raced(self, **kwargs):
        attempts.append(self.ledger.project().logical_time.isoformat())
        if fail_forever or len(attempts) <= 2:
            raise ConcurrencyConflict("fixture race")
        return original(self, **kwargs)

    monkeypatch.setattr(ChatLifeIntentRuntime, "accept", raced)
    result, rows, _, output, chat_calls = await _run_http_journey(
        tmp_path, monkeypatch, no_appraisal=True, duration_minutes=5
    )
    assert result["completed"], result["stop_reason"]
    assert chat_calls == 1
    assert attempts == [
        "2026-09-08T02:00:00+00:00",
        "2026-09-08T02:00:30+00:00",
        "2026-09-08T02:02:30+00:00",
    ]
    failures = [x for x in rows if x["event_type"] == "ChatLifeIntentAcceptanceFailed"]
    assert len(failures) == (3 if fail_forever else 2)
    assert failures[-1]["payload"]["terminal"] is fail_forever
    plans = [x for x in rows if x["event_type"] == "ActivityPlanned"]
    assert len(plans) == (0 if fail_forever else 1)
    if plans:
        assert plans[0]["payload"]["chat_intent_origin"]["selected_at"] == "2026-09-08T02:00:00Z"
    ledger = SQLiteWorldLedger(path=output / "world.sqlite", world_id=rows[0]["world_id"])
    try:
        before = ledger.project()
        assert before.chat_life_intent_failures[-1].retry_ordinal == len(failures)
        rebuilt = ledger.rebuild()
        assert rebuilt.semantic_hash == before.semantic_hash
        assert rebuilt.chat_life_intent_failures == before.chat_life_intent_failures
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_expression_reauthor_keeps_one_observation_life_effect_and_original_origin(
    tmp_path, monkeypatch
):
    import companion_daemon.world_v2.runtime as runtime_module
    from companion_daemon.world_v2.expression_plan_acceptance import ExpressionPlanAcceptanceError

    derive = runtime_module.derive_expression_plan_material
    attempts = 0

    def fail_once(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ExpressionPlanAcceptanceError("fixture_temporary_unavailable")
        return derive(**kwargs)

    monkeypatch.setattr(runtime_module, "derive_expression_plan_material", fail_once)
    result, rows, _, _, calls = await _run_http_journey(
        tmp_path, monkeypatch, no_appraisal=True, vary_reply=True
    )
    assert result["completed"], result["stop_reason"]
    assert calls == 2
    audits = [
        x
        for x in rows
        if x["event_type"] == "ProposalRecorded" and "life_intent.v1" in x["payload_json"]
    ]
    assert len(audits) == 2
    assert audits[0]["payload"]["proposal_hash"] != audits[1]["payload"]["proposal_hash"]
    plans = [x for x in rows if x["event_type"] == "ActivityPlanned"]
    assert len(plans) == 1
    assert plans[0]["payload"]["chat_intent_origin"]["proposal_event_ref"] == audits[0]["event_id"]
    assert len([x for x in rows if x["event_type"] == "ActivityStarted"]) == 1


@pytest.mark.asyncio
async def test_autonomous_activity_closes_without_inventing_an_experience(tmp_path, monkeypatch):
    result, rows, _, _, calls = await _run_http_journey(
        tmp_path,
        monkeypatch,
        intent={**INTENT, "duration_seconds": 180},
        duration_minutes=5,
        prefer_complete=True,
    )
    assert result["completed"], result["stop_reason"]
    assert calls == 1
    for kind in ("ActivityPlanned", "ActivityStarted", "ActivityCompleted"):
        assert len([x for x in rows if x["event_type"] == kind]) == 1
    assert not any(
        x["event_type"]
        in {"WorldOccurrenceCommitted", "WorldOccurrenceSettled", "ExperienceCommitted"}
        for x in rows
    )


@pytest.mark.asyncio
async def test_failure_journal_replay_rejects_actor_source_and_change_tampering(
    tmp_path, monkeypatch
):
    from companion_daemon.world_v2.chat_life_intent_runtime import ChatLifeIntentRuntime
    from companion_daemon.world_v2.errors import ConcurrencyConflict
    from companion_daemon.world_v2.reducers import ReducerState, reduce_event
    from companion_daemon.world_v2.schemas import WorldEvent

    def fail(self, **kwargs):
        raise ConcurrencyConflict("fixture race")

    monkeypatch.setattr(ChatLifeIntentRuntime, "accept", fail)
    result, rows, _, _, _ = await _run_http_journey(
        tmp_path, monkeypatch, no_appraisal=True, duration_minutes=5
    )
    assert result["completed"]
    state = ReducerState()
    for row in rows:
        event = WorldEvent.model_validate_json(
            json.dumps({k: v for k, v in row.items() if k not in {"ledger_sequence", "payload"}})
        )
        if event.event_type == "ChatLifeIntentAcceptanceFailed":
            break
        state = reduce_event(state, event)
    assert event.event_type == "ChatLifeIntentAcceptanceFailed"
    assert len(reduce_event(state, event).chat_life_intent_failures) == 1
    for edits, actor, source in [
        ({}, "agent:other", event.source),
        ({}, event.actor, "world-v2:other"),
        ({"change_hash": "0" * 64}, event.actor, event.source),
        ({"proposal_payload_hash": "0" * 64}, event.actor, event.source),
        ({"retry_ordinal": 2}, event.actor, event.source),
        (
            {
                "actor_ref": "agent:other",
                "reason_code": "actor_mismatch",
                "failure_code": "authority_invalid",
                "terminal": True,
                "next_retry_at": None,
            },
            "agent:other",
            event.source,
        ),
    ]:
        envelope = event.model_dump(exclude={"payload_json", "payload_hash"})
        forged = WorldEvent.from_payload(
            **{**envelope, "actor": actor, "source": source}, payload={**event.payload(), **edits}
        )
        with pytest.raises(ValueError, match="chat_life_intent"):
            reduce_event(state, forged)
