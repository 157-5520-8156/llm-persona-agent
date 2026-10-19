"""Offline public-application outlook chain; optional full-history SQLite backup.

WORLD_OUTLOOK_HISTORY_SOURCE points to a read-only source database. Model outputs
are explicit fixtures, never production decisions or narrative-quality evidence.
"""

from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import socket
import sqlite3
import time

import pytest

from companion_daemon.world_v2.character_interior.production import (
    compose_production_character_interior,
)
from companion_daemon.world_v2.companion_identity import CompanionIdentityFrame
from companion_daemon.world_v2.expression_draft import QQ_NAPCAT_EXPRESSION_CAPABILITIES
from companion_daemon.world_v2.production_turn_application import (
    LifeEcologyComposition,
    WorldV2TurnApplicationConfig,
    build_sqlite_world_v2_turn_application,
)
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.visible_independent_review_configuration import (
    GroundedVisibleReviewer,
)
from companion_daemon.world_v2.world_outlook_request import decode_outlook_request
from test_production_turn_application import _Identities, _Router, _DeliveredTransport
from test_world_stimulus_life_intent import _model, _http_result
from test_world_stimulus_life_response import _ResponseHTTP
from test_whole_candidate_author import _decision
from companion_daemon.world_v2.world_turn_runtime import InboundTurn
from companion_daemon.world_v2.shared_string_view import unpack_shared_strings
from companion_daemon.world_v2.world_outlook_execution import occurrence_ref


FUTURE = "PRIVATE_UNREALIZED_OUTLOOK_SENTINEL"
NOTICE = "社区公告栏贴出了旧书交换的报名说明，报名截止时间是本周五傍晚。"
SECOND = "图书馆发布了下周学习资料整理区的开放通知。"
THIRD = "社区公布了周末旧书摊位的新分区图。"


class _NoReview:
    async def complete_json_with_usage(self, **kwargs):
        raise AssertionError("ordinary text must not invoke synchronous semantic review")


class _WorldAuthor:
    model = "fixture:outlook-closed-loop-author"

    def __init__(self):
        self.calls = []
        self.life_calls = []
        self.wire_sizes = []
        self.follow_plan = None

    async def complete(self, messages, **kwargs):
        packet = decode_outlook_request(messages[1]["content"])
        if packet.get("contract") != "world-outlook-request.1":
            assert "private_world_outlook" in packet
            self.life_calls.append(packet)
            return json.dumps({"decision": "no_op"})
        self.calls.append(packet)
        self.wire_sizes.append(len(messages[1]["content"].encode()))
        assert packet["contract"] == "world-outlook-request.1", packet.get("contract")
        now = datetime.fromisoformat(packet["logical_time"])
        if packet["parent_revision"]:
            current = packet["current_outlook"]
            if self.follow_plan is None:
                self.follow_plan = next((p["plan_id"] for p in packet.get("plan_schedules", ())
                                         if p["owner_actor_ref"] == packet["actor_ref"]), None)
            retired = any(n["state"] == "invalidated" for n in current["opportunities"])
            nodes = [
                {k: v for k, v in n.items() if k != "state"}
                for n in current["opportunities"]
                if n["state"] != "invalidated"
            ]
            source = next(
                (s for s in packet["condition_sources"] if s["source_ref"] == self.follow_plan),
                None,
            )
            if (
                source
                and source["status"] != "abandoned"
                and not any(n["opportunity_id"] == "dependent-choice" for n in nodes)
            ):
                node = deepcopy(nodes[0])
                node.update(
                    opportunity_id="dependent-choice",
                    basis_refs=packet["basis_refs"][:1],
                    opens_at=(now + timedelta(minutes=1)).isoformat(),
                    closes_at=(now + timedelta(days=2)).isoformat(),
                    conditions=[
                        {
                            "source_ref": self.follow_plan,
                            "ready_statuses": ["completed"],
                            "invalid_statuses": ["abandoned"],
                        }
                    ],
                )
                node["execution"]["text"] = "资料整理区新增了可领取的目录卡。"
                nodes.append(node)
                retired = True
            if retired or packet.get("world_changes"):
                long_term = deepcopy(current.get("long_term", []))
                if long_term and packet.get("draft_schema_contract") in {"world-outlook-author-schema.7", "world-outlook-author-schema.8"}:
                    long_term[-1]["ends_on"] = (now + timedelta(days=365)).date().isoformat()
                return json.dumps(
                    {
                        "decision": "revise",
                        "draft": {
                            "world_id": packet["world_id"],
                            "actor_ref": packet["actor_ref"],
                            "parent_revision": packet["parent_revision"],
                            "revise_reason": "根据真实的新计划保留条件分支。",
                            "review_at": (now + timedelta(days=1)).isoformat(),
                            "horizon_end": current["horizon_end"],
                            "threads": current["threads"],
                            **({"long_term": long_term} if long_term else {}),
                            "opportunities": nodes,
                            "change_responses": [{"source_ref": c["source_ref"], "decision": "retain",
                                "thread_refs": [], "planning_keys": [], "reason": "测试中只重排依赖该计划的机会。"}
                                for c in packet.get("world_changes", ())],
                        },
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "decision": "keep",
                    "reason": "已有外部安排继续有效。",
                    "review_at": (now + timedelta(days=1)).isoformat(),
                }
            )
        anchors = [m["anchor"] for m in packet["history_material"][:1]]
        node = {
            "opportunity_id": "closed-loop-notice",
            "thread_refs": ["books", "friends"],
            "external_possibility": NOTICE,
            "execution": {"kind": "environment", "text": NOTICE, "duration_minutes": 1},
            "lane": "world",
            "basis_refs": packet["basis_refs"][:1],
            "opens_at": (now + timedelta(minutes=1)).isoformat(),
            "closes_at": (now + timedelta(days=3)).isoformat(),
        }
        draft = {
            "world_id": packet["world_id"],
            "actor_ref": packet["actor_ref"],
            "revise_reason": "测试共同事项的执行，不预设主角选择。",
            "review_at": (now + timedelta(days=1)).isoformat(),
            "horizon_end": (now + timedelta(days=7)).isoformat(),
            "threads": [
                {
                    "thread_id": "books",
                    "situation": "旧书交换的筹备",
                    "open_question": FUTURE,
                    "history_anchors": anchors,
                },
                {
                    "thread_id": "friends",
                    "situation": "熟人的各自安排",
                    "open_question": "是否有交集？",
                },
            ],
            "opportunities": [node],
            **({"long_term": [{"stage_id": "ordinary-life", "starts_on": now.date().isoformat(),
                "ends_on": (now + timedelta(days=365)).date().isoformat(),
                "situation": FUTURE}]} if "rolling_preparation" in packet else {}),
        }
        for key, day, text in (("second-notice", 1, SECOND), ("independent-notice", 2, THIRD)):
            later = deepcopy(node)
            later.update(
                opportunity_id=key,
                opens_at=(now + timedelta(days=day)).isoformat(),
                thread_refs=["friends"],
                external_possibility=text,
            )
            later["execution"]["text"] = text
            draft["opportunities"].append(later)
        from test_story_outlook_replanning import apply_story_contract
        return json.dumps({"decision": "revise", "draft": apply_story_contract(draft, packet)}, ensure_ascii=False)


class _Role(_ResponseHTTP):
    def __init__(self):
        super().__init__(text="我看到报名说明了，但我现在还不打算报名。")
        self.purposes = Counter()
        self.materials = []
        self.pull_recall = False
        self.intent_source = None
        self.abandon = False

    async def __call__(self, request):
        body = json.loads(request.content)
        material = json.loads(next(m["content"] for m in body["messages"] if m["role"] == "user"))
        purpose = material.get("inner_turn", {}).get("purpose")
        self.purposes[purpose or "inbound"] += 1
        decoded = deepcopy(material)
        shown = decoded.get("inner_life_snapshot", {}).get("materials", {})
        if shown.get("contract") == "shared-string-view.1":
            decoded["inner_life_snapshot"]["materials"] = unpack_shared_strings(shown)
        self.materials.append(decoded)
        assert FUTURE not in json.dumps(body, ensure_ascii=False)
        name = body.get("tool_choice", {}).get("function", {}).get("name", "")
        if name.startswith("character_inbound_"):
            if self.pull_recall and "initial" in name:
                return _http_result(
                    body,
                    {
                        "result": {
                            "result_kind": "recall",
                            "private_turn_state": {
                                "contract": "private-turn-state.1",
                                "inner_state_summary": "我想回想一下旧书交换的来龙去脉。",
                                "attended_source_refs": [],
                            },
                            "recall_request": {
                                "query_text": "旧书交换 报名 公告",
                                "memory_kinds": ["episodic"],
                                "limit": 6,
                            },
                        }
                    },
                )
            value = _decision()
            value["expression_draft"]["beats"] = [
                {"modality": "text", "text": "我想先把这件事慢慢想清楚。"}
            ]
            if "after_recall" in name:
                items = decoded["inner_life_snapshot"]["materials"]["selected_recall"]["content"][
                    "items"
                ]
                offered = next(item for item in items if item.get("text") == NOTICE)
                value["expression_draft"]["beats"] = [{"modality": "text", "text": offered["text"]}]
                value["expression_draft"]["world_claims"] = [
                    {
                        "claim_text": offered["text"],
                        "scope": "past_world",
                        "source_refs": [offered["source_ref"]],
                    }
                ]
            return _http_result(body, {"result": value})
        if purpose == "proactive_contact":
            cap = material["capability_manifest"]
            return _http_result(
                body,
                {
                    "status": "decision",
                    "summary": "这次先不主动联系。",
                    "attended_source_refs": [],
                    "proposals": [],
                    "recall_query": None,
                    "decision": {
                        "source_refs": cap["source_refs"],
                        "payload": {
                            "timing_choice": "silent",
                            "cadence": "conversational",
                            "beats": [],
                            "stance": "keeping the thought private",
                            "brief_rationale": "这次不发送。",
                            "impulse_summary": "先保留自己的想法。",
                            "confidence": 7000,
                            "world_claims": [],
                        },
                    },
                },
            )
        if purpose in {"experience_memory_retention", "fact_memory_retention"}:
            cap = material["capability_manifest"]
            return _http_result(
                body,
                {
                    "status": "decision",
                    "summary": "这段经历我愿意保留。",
                    "attended_source_refs": cap["source_refs"],
                    "proposals": [],
                    "recall_query": None,
                    "decision": {
                        "source_refs": cap["source_refs"],
                        "payload": {
                            "retain": True,
                            "cue_kind": "world_continuity",
                            "retention_rationales": ["world_continuity"],
                            "salience": {
                                k: 5000
                                for k in (
                                    "autobiographical_relevance_bp",
                                    "relationship_relevance_bp",
                                    "emotional_residue_bp",
                                    "unfinished_business_bp",
                                    "recurrence_bp",
                                    "novelty_bp",
                                    "future_utility_bp",
                                    "world_continuity_bp",
                                )
                            },
                        },
                    },
                },
            )
        if purpose == "activity_lifecycle_choice":
            cap = material["capability_manifest"]
            from test_world_stimulus_life_intent import INTENTION

            selected = next(
                (
                    o
                    for o in cap["payload"].get("openings", ())
                    if self.abandon
                    and o["safe_summary"].startswith("let go of an abstract activity")
                    and INTENTION in o["safe_summary"]
                ),
                None,
            )
            return _http_result(
                body,
                {
                    "status": "decision",
                    "summary": "暂不改变安排。",
                    "attended_source_refs": cap["source_refs"],
                    "proposals": [],
                    "recall_query": None,
                    "decision": {
                        "source_refs": cap["source_refs"],
                        "payload": {
                            "decision": "select",
                            "selected_token": selected["opening_token"],
                        }
                        if selected
                        else {"decision": "no_op"},
                    },
                },
            )
        if purpose == "world_stimulus_appraisal":
            from test_world_stimulus_life_intent import INTENTION

            cap = material["capability_manifest"]
            response_cap = cap["payload"].get("world_life_response")
            refs = response_cap["source_event_refs"] if response_cap else []
            intent = (
                {
                    "source_event_ref": self.intent_source,
                    "execution_scope": "self_directed",
                    "intention": INTENTION,
                    "start_after_seconds": 3600,
                    "duration_seconds": 120,
                    "importance_bp": 5300,
                }
                if self.intent_source in refs
                else None
            )
            proposal = {
                "proposal_type": "world_stimulus_appraisal_result",
                "decision": "no_change",
                "brief_rationale": "我自己看待这件事。",
                "behavior_tendency": "由我决定安排。",
                "stance": "平常地看待。",
                "display_strategy": "withhold",
                "confidence": 7000,
                "meaning_candidates": None,
                "attribution": None,
                "severity": None,
                "expiry": None,
                "affect_transition": None,
                "relationship_signal": None,
                "aspiration_transition": None,
                "experience_transition": None,
                "life_intent": intent,
            }
            if response_cap is not None:
                proposal["life_responses"] = [
                    {"source_event_ref": ref, "response_text": self.text} for ref in refs
                ]
            return _http_result(
                body,
                {
                    "status": "transition" if refs or intent else "no_change",
                    "summary": "我按自己的理解回应。",
                    "attended_source_refs": cap["source_refs"],
                    "proposals": [proposal],
                    "recall_query": None,
                    "decision": None,
                },
            )
        return await super().__call__(request)


def _build(path, world, now, role, author, actor="actor:companion", archive=None):
    model = _model(role)
    capabilities = QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_copy(
        update={"private_turn_state_mode": "required"}
    )
    interior = compose_production_character_interior(
        flash_model=model,
        thinking_model=None,
        source_closure_model=None,
        report_relative_source_closure_model=None,
        source_closure_reselection_lane=None,
        expression_episode_observer_model=None,
        flash_model_id=model.model,
        thinking_model_id=None,
        expression_capabilities=capabilities,
        ordinary_text_review_mode="sampled",
        whole_candidate_mode=True,
        visible_source_review_version="25",
        visible_source_review_model=GroundedVisibleReviewer(source_model=_NoReview()),
        atomic_tool_envelope_version="3",
        identity_frame=CompanionIdentityFrame(companion_name="小满", counterpart_name="用户"),
    )
    app = build_sqlite_world_v2_turn_application(
        path=path,
        config=WorldV2TurnApplicationConfig(
            world_id=world,
            companion_actor_ref=actor,
            reply_target="user:user.1",
            action_pump_owner="pump:outlook-qualification",
            character_memory_enabled=True,
            world_outlook_enabled=True,
            expression_capabilities=capabilities,
            # Same v25 composition as the host; sampled mode above performs
            # source-bound receipt recording without a synchronous reviewer.
            visible_source_review_required=True,
            life_ecology=LifeEcologyComposition.production_v1(),
            recall_story_context=True,
            reviewed_prehistory=archive,
        ),
        identities=_Identities(),
        router=_Router(),
        character_interior=interior,
        transport=_DeliveredTransport(received_at=now + timedelta(days=3)),
        life_world_author_model=author,
        now=now,
    )
    return app, model


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    original = socket.socket.connect
    attempts = []

    def blocked(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            attempts.append(str(address))
            raise AssertionError("closed-loop qualification forbids all IP network")
        return original(sock, address)

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    # Inspect the actual assembled request even when its size gate refuses
    # the provider. This instrumentation preserves every serialized byte.
    import companion_daemon.world_v2.world_outlook_runtime as outlook_module

    original_encode = outlook_module.encode_outlook_request

    def capture_packet(value, **kwargs):
        raw = original_encode(value, **kwargs)
        root = os.environ.get("WORLD_OUTLOOK_EVIDENCE_DIR")
        if root and isinstance(value, dict) and value.get("contract") == "world-outlook-request.1":
            Path(root, "outlook-request.json").write_text(raw)
            Path(root, "outlook-request-sizes.json").write_text(
                json.dumps(
                    {
                        "total": len(raw.encode()),
                        "fields": {
                            k: len(json.dumps(v, ensure_ascii=False).encode())
                            for k, v in value.items()
                        },
                    },
                    indent=2,
                )
            )
        return raw

    monkeypatch.setattr(outlook_module, "encode_outlook_request", capture_packet)
    original_failure = outlook_module.WorldOutlookRuntime.record_failure

    def capture_failure(runtime, exc):
        root = os.environ.get("WORLD_OUTLOOK_EVIDENCE_DIR")
        if root:
            import traceback

            with Path(root, "execution-failures.log").open("a") as log:
                traceback.print_exception(type(exc), exc, exc.__traceback__, file=log)
        return original_failure(runtime, exc)

    monkeypatch.setattr(outlook_module.WorldOutlookRuntime, "record_failure", capture_failure)
    yield attempts
    assert not attempts


@pytest.mark.asyncio
async def test_outlook_public_closed_loop(tmp_path, offline):
    root = Path(os.environ.get("WORLD_OUTLOOK_EVIDENCE_DIR", tmp_path))
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    path = root / "world.sqlite"
    assert not path.exists(), "use a fresh isolated destination"
    source = os.environ.get("WORLD_OUTLOOK_HISTORY_SOURCE")
    world = "world:outlook-closed-loop"
    actor = "actor:companion"
    archive = None
    now = datetime(2026, 10, 8, 2, tzinfo=UTC)
    if source:
        source = Path(source).resolve()
        with (
            sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src,
            sqlite3.connect(path) as dst,
        ):
            src.backup(dst)
            world = "world:companion-v2:qq-c2c:geoff"
            assert src.execute("SELECT 1 FROM world_v2_heads WHERE world_id=?", (world,)).fetchone()
        ledger = SQLiteWorldLedger(path=path, world_id=world)
        now = ledger.project().logical_time
        actor = ledger.project().prehistory_records[0].actor_ref
        ledger.close()
    else:
        from test_story_memory_structure import story_archive
        from companion_daemon.world_v2.character_prehistory import digest

        archive = story_archive()
        doc = archive.document.model_copy(
            update={"world_id": world, "actor_ref": actor, "world_started_at": now}
        )
        archive = archive.model_copy(
            update={
                "document": doc,
                "review": archive.review.model_copy(
                    update={"manifest_hash": digest(doc.manifest()), "reviewed_at": now}
                ),
            }
        )
    role, author = _Role(), _WorldAuthor()
    app, model = _build(path, world, now, role, author, actor, archive)
    report = {"scope": "offline mechanism only", "steps": [], "paid_calls": 0}
    try:
        if archive:
            await app.initialize_prehistory_once(batch_size=256)
        before = app._ledger.project()
        report["prehistory_records"] = len(before.prehistory_records)
        if source:
            assert len(before.prehistory_records) >= 1259

        async def advance(minute):
            started = time.perf_counter()
            previous = await app.current_logical_time()
            key = f"closed-loop-{minute}"
            await app.tick(
                tick_id=key,
                logical_time_from=previous,
                logical_time_to=now + timedelta(minutes=minute),
                observed_at=now + timedelta(minutes=minute),
                trace_id=key,
                causation_id="offline-clock",
                correlation_id=key,
                reason="offline-outlook-qualification",
                run_life_ecology=False,
            )
            result = await app.advance_life_ecology_once(
                wake_event_ref="event:trigger:clock:" + key, trace_id=key, correlation_id=key
            )
            background = []
            for _ in range(3):
                work = await app.drain_background_once()
                background.append(work)
                if work is None:
                    break
            state = app._ledger.project()
            step = {
                "minute": minute,
                "ecology": repr(result),
                "background": repr(background),
                "outlook": app.world_outlook_health(),
                "author_calls": len(author.calls),
                "role_purposes": dict(role.purposes),
                "experiences": len(state.experiences),
                "occurrences": [
                    (o.occurrence_id, o.status)
                    for o in state.world_occurrences
                    if "outlook" in o.occurrence_id
                ],
                "seconds": time.perf_counter() - started,
            }
            report["steps"].append(step)
            print(
                json.dumps(
                    {
                        "minute": minute,
                        "outlook": step["outlook"]["status"],
                        "author_calls": len(author.calls),
                        "experiences": len(state.experiences),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return state

        for minute in (1, 2, 3, 4, 5, 10, 11, 12):
            state = await advance(minute)
        assert author.calls, report
        assert any(
            "outlook" in o.occurrence_id and o.status == "settled" for o in state.world_occurrences
        ), report
        assert len(state.experiences) > len(before.experiences), report
        assert state.plans == before.plans, (
            "the fixture declined; the World must not create her plan"
        )
        occurrence = next(o for o in state.world_occurrences if "outlook" in o.occurrence_id)
        experiences = [
            e
            for e in state.experiences
            if any(
                getattr(getattr(b, "settlement", b), "occurrence_id", None)
                == occurrence.occurrence_id
                for b in e.values.source_bindings
            )
        ]
        assert len(experiences) == 1
        memories = [
            m
            for m in state.memory_candidates
            if m.values.status == "active"
            and any(b.source_id == experiences[0].experience_id for b in m.values.source_bindings)
        ]
        assert len(memories) == 1
        if source:
            assert author.calls[0]["history_material"], (
                "full-history planning must see eligible history"
            )
        assert len(author.calls) == 1, "normal settlement and memory creation do not buy a rewrite"
        role.pull_recall = True
        started = time.perf_counter()
        response = await app.respond(
            InboundTurn(
                platform="test",
                platform_user_id="user.1",
                platform_message_id="closed-loop-recall",
                text="刚才旧书交换的公告说了什么？让你想到以前的什么经历吗？",
                observed_at=state.logical_time,
                trace_id="trace:closed-loop-recall",
            )
        )
        report["chat"] = {"status": response.status, "seconds": time.perf_counter() - started}
        assert response.status == "action_authorized", report
        selected = [
            m["inner_life_snapshot"]["materials"]["selected_recall"]["content"]["items"]
            for m in role.materials
            if m.get("inner_life_snapshot", {}).get("materials", {}).get("selected_recall")
        ]
        assert selected, "the normal role must receive its requested recall"
        assert NOTICE in json.dumps(selected, ensure_ascii=False)
        if author.calls[0]["history_material"]:
            anchor = author.calls[0]["history_material"][0]["anchor"]["source_ref"]
            assert anchor in json.dumps(selected, ensure_ascii=False)
        for action in response.authorized_action_ids:
            delivered = await app.drain_action(action)
            assert delivered.status == "settled"
        report.update(
            author_request_bytes=author.wire_sizes,
            role_purposes=dict(role.purposes),
            new_experience=experiences[0].experience_id,
            new_memory=memories[0].candidate_id,
            new_source_in_normal_chat=True,
            old_source_in_normal_chat=bool(author.calls[0]["history_material"]),
        )
        # A later, different event receives an independently authored choice.
        second_ref = occurrence_ref(world, actor, "second-notice")
        role.intent_source = "event:life-aftermath:settlement:" + second_ref
        for minute in (1441, 1442, 1443, 1444, 1445):
            state = await advance(minute)
        new_plans = [p for p in state.plans if p.plan_id not in {p.plan_id for p in before.plans}]
        assert len(new_plans) == 1, [(p.plan_id, p.status) for p in new_plans]
        plan = new_plans[0]
        planned = app._ledger.lookup_event_commit(plan.authority_origin.accepted_event_ref)[0]
        assert planned.payload()["world_intent_origin"]["source_event_ref"] == role.intent_source
        author.follow_plan = plan.plan_id
        state = await advance(1500)
        outlook = app._life_ecology._world_outlook
        assert any(
            n["opportunity_id"] == "dependent-choice" for n in outlook.view()["opportunities"]
        )
        role.abandon = True
        # A real incoming user observation opens a new causal occasion. Its
        # semantic reading remains the fixture role's choice, not a host rule.
        role.pull_recall = False
        await app.respond(
            InboundTurn(
                platform="test",
                platform_user_id="user.1",
                platform_message_id="closed-loop-change",
                text="今天事情有变化，之前的安排还合适吗？",
                observed_at=state.logical_time,
                trace_id="trace:closed-loop-change",
            )
        )
        for minute in (1501, 1510, 1560):
            state = await advance(minute)
        assert next(p for p in state.plans if p.plan_id == plan.plan_id).status == "abandoned"
        assert not any(
            n["opportunity_id"] == "dependent-choice" for n in outlook.view()["opportunities"]
        )
        assert any(
            n["opportunity_id"] == "dependent-choice" and n["state"] == "invalidated"
            for p in author.calls
            for n in p["current_outlook"]["opportunities"]
        )
        assert not any(
            o.occurrence_id == occurrence_ref(world, actor, "dependent-choice")
            for o in state.world_occurrences
        )
        role.intent_source = None
        role.text = None
        for minute in (2881, 2882, 2883, 2884):
            state = await advance(minute)
        independent = next(
            o
            for o in state.world_occurrences
            if o.occurrence_id == occurrence_ref(world, actor, "independent-notice")
        )
        assert independent.status == "settled"
        history = outlook.story_history()
        assert experiences[0].experience_id in json.dumps(history)
        assert memories[0].candidate_id in json.dumps(history)
        prefix = state
        original_events = app._ledger.export_replay_evidence()
        assert original_events.replay.semantic_hash == state.semantic_hash
        assert all(FUTURE not in row.event.model_dump_json() for row in original_events.events)
        await app.aclose()
        await model.aclose()
        app, model = _build(path, world, state.logical_time, role, author, actor)
        assert app._ledger.project().semantic_hash == prefix.semantic_hash
        assert all(step["outlook"]["status"] == "ready" for step in report["steps"]), report
        calls_before = (len(author.calls), sum(role.purposes.values()))
        repeat = await app.advance_life_ecology_once(
            wake_event_ref="event:trigger:clock:closed-loop-2884",
            trace_id="repeat",
            correlation_id="repeat",
        )
        assert repeat.status == "joined_existing"
        assert calls_before == (len(author.calls), sum(role.purposes.values()))
        assert app._ledger.project().semantic_hash == prefix.semantic_hash
        report.update(
            status="passed",
            source_history_unchanged=state.prehistory_records == before.prehistory_records,
            author_request_bytes=author.wire_sizes,
            role_purposes=dict(role.purposes),
            branch_cancelled=True,
            independent_line_settled=True,
            replay_matches=True,
            cold_repeat_effects=0,
        )
    except BaseException as exc:
        report.update(
            status="failed", failure_type=type(exc).__name__, failure_detail=str(exc)[:800]
        )
        raise
    finally:
        (root / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        (root / "role-materials.json").write_text(json.dumps(role.materials, ensure_ascii=False))
        await app.aclose()
        await model.aclose()
