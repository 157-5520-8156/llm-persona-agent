"""A future role-authored plan must remain readable before it starts."""

from datetime import timedelta
import json

import httpx
import pytest

from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.world_v2.world_turn_runtime import InboundTurn
from test_day_open_self_directed_intent import (
    INTENT,
    NOW,
    _DayOpenHTTP,
    build_app as build_app,
)
from test_whole_candidate_author import _decision
from test_world_stimulus_life_intent import _http_result


class _FuturePlanHTTP(_DayOpenHTTP):
    async def __call__(self, request):
        body = json.loads(request.content)
        material = json.loads(body["messages"][-1]["content"])
        if "inner_life_snapshot" in material and "inner_turn" not in material:
            self.requests.append(body)
            self.chat_requests.append(body)
            authored = _decision()
            fields = body["tools"][0]["function"]["parameters"]["properties"]
            return _http_result(body, {key: authored.get(key) for key in fields})
        return await super().__call__(request)


@pytest.mark.asyncio
@pytest.mark.parametrize("restart", [False, True])
async def test_future_plan_original_intention_reaches_next_actual_chat_request(
    tmp_path, monkeypatch, build_app, restart
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    monkeypatch.setitem(INTENT, "start_after_seconds", 3600)
    provider = _FuturePlanHTTP()
    model = DeepSeekChatModel(
        "offline-fixture", "https://fixture.invalid", "deepseek-v4-flash",
        thinking_enabled=False, transport=httpx.MockTransport(provider),
    )
    path = tmp_path / "planned.sqlite"
    app = build_app(path, model, ecology=True)
    try:
        at = NOW + timedelta(minutes=1)
        await app.tick(
            tick_id="future-plan", logical_time_from=NOW, logical_time_to=at,
            observed_at=at, trace_id="trace:future-plan", causation_id="clock:future-plan",
            correlation_id="future-plan", reason="test_clock",
        )
        before = app.export_replay_evidence()
        plan = before.projection.plans[0]
        assert plan.status == "planned"
        assert plan.scheduled_window.opens_at == at + timedelta(hours=1)
        if restart:
            app.close()
            app = build_app(path, model, ecology=True)
            assert app.export_replay_evidence().projection == before.projection
        await app.respond(InboundTurn(
            platform="test", platform_user_id="user.1", platform_message_id="ask-future",
            text="一会儿有什么打算？", observed_at=at, trace_id="trace:ask-future",
        ))
        assert len(provider.chat_requests) == 1
        material = json.loads(provider.chat_requests[0]["messages"][-1]["content"])
        snapshot = material["inner_life_snapshot"]["materials"]
        planned = snapshot.get("planned_activities", [])
        assert len(planned) == 1, "accepted future intention was reduced to an opaque plan ID"
        assert planned[0]["accepted_intention"]["text"] == INTENT["intention"]
        assert planned[0]["plan_id"] == plan.plan_id
        assert planned[0]["status"] == "planned"
        assert planned[0]["source_ref"] == plan.authority_origin.accepted_event_ref
        assert planned[0]["planning_scope"] == "accepted_plan_not_started_or_completed"
        assert not snapshot.get("current_activities")
        assert not snapshot.get("recently_ended_activities")
        assert snapshot["recent_self_experiences"] == {"availability": "unavailable"}
        assert not any(
            item.event.event_type in {"ActivityStarted", "ExperienceCommitted"}
            for item in app.export_replay_evidence().events
        )
    finally:
        app.close()
        await model.aclose()
    _assert_planned_sources(path, plan)


def _assert_planned_sources(path, plan):
    from types import SimpleNamespace

    from companion_daemon.world_v2.background_context_profile import (
        background_context_profile_for_purpose,
        slice_background_capsule_context,
        slice_background_inner_life_snapshot,
    )
    from companion_daemon.world_v2.day_open_life_intent_runtime import DayOpenLifeIntentPlannedReader
    from companion_daemon.world_v2.context_resolver import query_from_projection
    from companion_daemon.world_v2.deliberation import TriggerMessage
    from companion_daemon.world_v2.expression_draft import world_claim_source_refs_by_scope
    from companion_daemon.world_v2.ledger_context_resolver import (
        ContextRelevanceScope, context_capsule_compiler_from_ledger,
    )
    from companion_daemon.world_v2.schemas import Observation, ProjectionCursor
    from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
    from companion_daemon.world_v2.visible_source_closure_protocol import visible_source_closure_messages
    from companion_daemon.world_v2.visible_source_composer import compile_visible_source_table
    from companion_daemon.world_v2.visible_source_review_receipt import prepare_visible_source_review
    from companion_daemon.world_v2.world_life_context import WorldLifeContextCompiler
    from test_current_activity_context import current_context
    from test_visible_source_composer import _request
    from test_visible_source_review_receipt import _candidate
    from test_world_stimulus_life_intent import ACTOR, WORLD

    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    try:
        before = ledger.export_replay_evidence()
        capsule, compact, snapshot = current_context(ledger, None)
        full = json.loads(capsule.model_content_json)
        event_ref = plan.authority_origin.accepted_event_ref
        item = next(item for item in capsule.world_life.items if item.item_ref == event_ref)
        assert item.privacy_class == "private"
        assert len(item.source_bindings) == 1
        assert item.source_bindings[0].authority_type == "ActivityPlanned"
        assert event_ref in snapshot.source_refs
        for context in (full, compact):
            scopes = world_claim_source_refs_by_scope(context=context)
            assert event_ref in scopes["current_world"]
            assert all(event_ref not in refs for scope, refs in scopes.items() if scope != "current_world")
            assert not {plan.plan_id, item.source_hash, item.value_hash}.intersection(
                set().union(*scopes.values())
            )
        observed = next(x.event for x in before.events if x.event.event_type == "ObservationRecorded")
        event, commit = ledger.lookup_event_commit(observed.event_id)
        observation = Observation.model_validate_json(event.payload_json, strict=True)
        review_capsule = context_capsule_compiler_from_ledger(
            ledger=ledger, relevance_scope=ContextRelevanceScope(actor_ref=ACTOR),
        ).compile(query_from_projection(ledger.project(), actor_ref=ACTOR, trigger_ref=event.event_id))
        request = _request(review_capsule).model_copy(update={"trigger_message": TriggerMessage(
            event_ref=event.event_id, event_payload_hash="sha256:" + event.payload_hash,
            source_world_revision=commit.world_revision, observation_ref=observation.observation_id,
            actor=observation.actor, channel=observation.channel, reply_target="fixture:local",
            text=observation.text,
        )})
        table = compile_visible_source_table(request=request, capsule=review_capsule)
        assert table.as_dict()["contract"] == "visible-source-row-table.2"
        rows = table.source_references()
        typed = next(row for row in rows if row.get("activity_support"))
        assert typed["source_ref"] == event_ref
        assert typed["support_subject_ref"] == ACTOR
        assert typed["support_eligibility"] == "eligible"
        assert typed["activity_support"]["scope"] == "accepted_plan_not_started_or_completed"
        assert all(
            row["support_eligibility"] == "baseline_only"
            for row in rows if row["source_ref"] == event_ref and row != typed
        )
        packet = json.loads(visible_source_closure_messages(
            visible_beats=("我给自己留了一项稍后的安排。",),
            world_claims=(), source_references=rows,
        )[1]["content"])
        assert "planned_activity_support_contract" in packet
        assert INTENT["intention"] in json.dumps(packet, ensure_ascii=False)
        prepared = prepare_visible_source_review(
            candidate=_candidate(SimpleNamespace(request=request), texts=("我想整理一下思路。",)),
            source_table=table, source_ref_aliases={},
        )
        assert prepared.as_dict()["source_table_json"] == table.payload_json
        external = background_context_profile_for_purpose("life_development_draft")
        assert not any(
            item["value"].get("context_kind") == "planned_activity"
            for item in slice_background_capsule_context(full, external)["slices"]["world_life"]["items"]
        )
        assert "planned_activities" not in slice_background_inner_life_snapshot(
            snapshot.model_view(), external,
        )["materials"]
        _, _, other = current_context(ledger, None, actor_ref="actor:other")
        assert "planned_activities" not in other.materials
        projection = ledger.project()
        cursor = ProjectionCursor(
            world_revision=projection.world_revision,
            deliberation_revision=projection.deliberation_revision,
            ledger_sequence=projection.ledger_sequence,
        )
        reader = DayOpenLifeIntentPlannedReader(ledger=ledger)
        kwargs = dict(plan_id=plan.plan_id, expected_cursor=cursor, actor_ref=ACTOR,
                      viewer_privacy_ceiling="private")
        result = reader.read_planned_plan(**kwargs)
        assert result is not None
        class ChangedProposalLedger:
            def __init__(self, mode):
                self.mode = mode

            def __getattr__(self, name):
                return getattr(ledger, name)

            def lookup_event_commit(self, ref):
                located = ledger.lookup_event_commit(ref)
                if located and ref == result.proposal_source.authority_event_ref:
                    # The advertised old hash alone cannot prove different bytes.
                    if self.mode == "body":
                        return located[0].model_copy(update={"payload_json": "{}"}), located[1]
                    return located[0], located[1].model_copy(update={"event_ids": ("unrelated",)})
                return located

        for mode in ("body", "membership"):
            assert DayOpenLifeIntentPlannedReader(ledger=ChangedProposalLedger(mode)).read_planned_plan(
                **kwargs,
            ) is None
        for changes in (
            {"actor_ref": "actor:other"}, {"viewer_privacy_ceiling": "shareable"},
            {"expected_cursor": cursor.model_copy(update={"ledger_sequence": cursor.ledger_sequence + 1})},
        ):
            assert reader.read_planned_plan(**(kwargs | changes)) is None
        assert WorldLifeContextCompiler(planned_activity_reader=reader).compile(
            projection=projection, actor_ref=ACTOR, cursor=cursor,
            user_channel_limited_content_refs=frozenset({result.accepted_intention.content_ref}),
        ) == ()
        assert ledger.export_replay_evidence() == before
    finally:
        ledger.close()
