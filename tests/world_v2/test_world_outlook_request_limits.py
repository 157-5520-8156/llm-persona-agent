"""Preparation failure stays visible; repeated source text is losslessly shared."""

from datetime import timedelta
import json

import pytest
import httpx

from companion_daemon.llm import DeepSeekChatModel

from test_world_outlook import (
    setup,
    draft_value,
    production_application,
    production_tick,
    ProductionAuthor,
)
from test_life_development_runtime import _manifest, _projection_cursor


def test_prior_selectors_restore_only_complete_pinned_objects_and_not_new_prose():
    from companion_daemon.world_v2.world_outlook_request import ROLLING_REFERENCE_CONTRACT, decode_outlook_response
    from companion_daemon.world_v2.shared_string_view import pack_shared_strings
    node = {"opportunity_id": "prior", "execution": {"text": "完整的原始正文，不能重新措辞。" * 6}, "state": "ready"}
    thread = {"thread_id": "story", "situation": "原先的外部故事", "open_question": "尚未确定"}
    request = pack_shared_strings({"response_reference_contract": ROLLING_REFERENCE_CONTRACT,
        "current_outlook": {"opportunities": [node], "threads": [thread]}, "repeat": [node] * 3})
    raw_request = json.dumps(request)
    response = {"draft": {"opportunities": [{"retain_opportunity_id": "prior"}], "threads": [{"retain_thread_id": "story"}]}}
    restored = decode_outlook_response(json.dumps(response), request_raw=raw_request)
    assert restored["draft"] == {"opportunities": [{k: v for k, v in node.items() if k != "state"}], "threads": [thread]}
    for bad in ({"retain_opportunity_id": "unknown"}, {"retain_opportunity_id": "prior", "execution": {"text": "改写"}}):
        with pytest.raises(ValueError, match="complete pinned object"):
            decode_outlook_response(json.dumps({"draft": {"opportunities": [bad]}}), request_raw=raw_request)
    alias = next(iter(request["strings"]))
    for prose in (alias, "课程内容是 " + alias):
        with pytest.raises(ValueError, match="not new prose"):
            decode_outlook_response(json.dumps({"draft": {"long_term": [{"situation": prose}]}}), request_raw=raw_request)


def test_anchor_selection_copies_exact_offered_tuple_and_never_repairs_authority_fields():
    from companion_daemon.world_v2.world_outlook_request import ROLLING_REFERENCE_CONTRACT, decode_outlook_response
    anchor = {"source_kind": "occurrence", "source_ref": "occurrence:one", "authority_ref": "event:settled-one",
              "authority_revision": 37, "authority_hash": "b" * 64}
    packet = {"response_reference_contract": ROLLING_REFERENCE_CONTRACT,
              "current_outlook": {"threads": [], "opportunities": []},
              "draft_schema": {"$defs": {"StoryAnchor": {"properties": {"history_anchor_ref": {}}}}},
              "history_offer": {"items": [{"anchor": anchor, "privacy_class": "private"}]}}
    response = {"draft": {"threads": [{"thread_id": "story", "history_anchors": [{"history_anchor_ref": "occurrence:one"}]}]}}
    decoded = decode_outlook_response(json.dumps(response), request_raw=json.dumps(packet))
    assert decoded["draft"]["threads"][0]["history_anchors"] == [anchor]
    for bad in ({"history_anchor_ref": "unknown"}, {"history_anchor_ref": "occurrence:one", "authority_hash": "c" * 64}):
        response["draft"]["threads"][0]["history_anchors"] = [bad]
        with pytest.raises(ValueError, match="exact offered source"):
            decode_outlook_response(json.dumps(response), request_raw=json.dumps(packet))
    response["draft"]["threads"][0]["history_anchors"] = [{"history_anchor_ref": "occurrence:one"}]
    packet["draft_schema"] = {}  # Older paid .2 requests never offered this selector.
    with pytest.raises(ValueError, match="exact offered source"):
        decode_outlook_response(json.dumps(response), request_raw=json.dumps(packet))
    packet["response_tool_contract"] = "world-outlook-native.4"
    with pytest.raises(ValueError, match="occurrence:one.*story.*exact offered source"):
        decode_outlook_response(json.dumps(response), request_raw=json.dumps(packet))


@pytest.mark.asyncio
@pytest.mark.parametrize("original_contract", ["world-outlook-native.1", "world-outlook-native.2", "world-outlook-native.3", "world-outlook-native.4"])
async def test_rolling_native_http_and_cold_recovery_keep_exact_paid_tool_contract(tmp_path, monkeypatch, original_contract):
    from companion_daemon.subscription_usage import BackgroundGLMModel, SubscriptionTokenLedger
    from companion_daemon.world_v2.world_outlook_request import decode_outlook_request
    from companion_daemon.world_v2.world_outlook_runtime import WorldOutlookRuntime, _outlook_request_hash
    from companion_daemon.world_v2.world_outlook_store import SQLiteWorldOutlookStore
    from jsonschema import Draft202012Validator
    from test_life_development_runtime import NOW, WORLD_ID, OWNER
    requests = []
    ledger, wake, store, _, runtime = setup(tmp_path, lambda w: [])
    runtime._rolling_planning = True
    monkeypatch.setattr("companion_daemon.world_v2.world_outlook_runtime._NATIVE_RESULT", original_contract)

    def provider(request):
        body = json.loads(request.content)
        requests.append(body)
        packet = decode_outlook_request(body["messages"][1]["content"])
        assert body["tool_choice"] == "auto" and "response_format" not in body
        assert "For each response, call world_outlook_v6 once" in body["messages"][0]["content"]
        value = draft_value(wake)
        for node, identity in zip(value["opportunities"], packet["new_opportunity_ids"], strict=False):
            node["opportunity_id"] = identity
        value["horizon_end"] = (NOW + timedelta(days=30)).isoformat()
        value["long_term"] = [{"stage_id": "year", "starts_on": NOW.date().isoformat(),
                              "ends_on": (NOW + timedelta(days=365)).date().isoformat(), "situation": "仍可改变的外部生活阶段"}]
        result = {"decision": "revise", "draft": value}
        tool = body["tools"][0]
        Draft202012Validator.check_schema(tool["function"]["parameters"])
        assert Draft202012Validator(tool["function"]["parameters"]).is_valid(result)
        assert packet["response_tool_contract"] == original_contract
        if len(requests) == 1:
            result["draft"]["actor_ref"] = "actor:wrong-owner"
        elif original_contract in {"world-outlook-native.3", "world-outlook-native.4"}:
            assistant, feedback = body["messages"][-2:]
            assert assistant["role"] == "assistant" and assistant["content"] is None
            assert assistant["tool_calls"][0]["function"]["name"] == "world_outlook_v6"
            assert json.loads(assistant["tool_calls"][0]["function"]["arguments"])["draft"]["actor_ref"] == "actor:wrong-owner"
            assert feedback["role"] == "tool" and feedback["tool_call_id"] == assistant["tool_calls"][0]["id"]
            assert "World, owner or predecessor mismatch" in feedback["content"]
        else:
            assert [m["role"] for m in body["messages"][-2:]] == ["assistant", "user"]
        return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None, "tool_calls": [{"id": "result", "type": "function", "function": {
                "name": "world_outlook_v6", "arguments": json.dumps(result)}}]}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})

    model = BackgroundGLMModel("test", "https://fixture.invalid/v1", "GLM-5.3-Flash",
        token_ledger=SubscriptionTokenLedger(tmp_path / "tokens.sqlite"), transport=httpx.MockTransport(provider))
    runtime.author = model
    class Crash(BaseException):
        pass
    original_install = store.install
    def crash(*args, **kwargs):
        raise Crash()
    monkeypatch.setattr(store, "install", crash)
    args = dict(projection=ledger.project(), context={}, manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)))
    try:
        with pytest.raises(Crash):
            await runtime.refresh(**args)
        assert len(requests) == 2
        monkeypatch.setattr(store, "install", original_install)
        operation = store.running_refresh(cursor={k: getattr(ledger.project(), k) for k in ("world_revision", "deliberation_revision", "ledger_sequence")})
        attempts = store.attempts(operation["operation_ref"])
        assert attempts[0]["status"] == "invalid" and attempts[1]["status"] == "valid"
        paid = attempts[1]
        expected_hash = _outlook_request_hash(paid["messages"], paid["response_tools"])
        runtime.close()
        monkeypatch.setattr("companion_daemon.world_v2.world_outlook_runtime._NATIVE_RESULT", "world-outlook-native.4")
        reopened = SQLiteWorldOutlookStore(path=tmp_path / "outlook.sqlite", world_id=WORLD_ID, actor_ref=OWNER)
        runtime = WorldOutlookRuntime(ledger=ledger, store=reopened, author=model, actor_ref=OWNER, rolling_planning=False)
        monkeypatch.setattr("companion_daemon.world_v2.world_outlook_runtime.outlook_result_tool", lambda *_: pytest.fail("paid tools must not be regenerated"))
        assert (await runtime.refresh(**args))["status"] == "ready"
        assert len(requests) == 2
        assert reopened.head()[2]["request_hash"] == expected_hash
    finally:
        runtime.close()
        await model.aclose()


@pytest.mark.asyncio
async def test_refresh_input_stays_bounded_as_unreferenced_terminal_history_grows(tmp_path):
    from companion_daemon.world_v2.schemas import EvidenceRef, PlanStateProjection
    from companion_daemon.world_v2.world_outlook_request import decode_outlook_request

    class Captured(BaseException):
        pass

    packets = []

    class CaptureAuthor:
        model = "fixture:bounded-outlook-input"

        async def complete(self, messages, **kwargs):
            packets.append(decode_outlook_request(messages[1]["content"]))
            raise Captured()

    for count in (0, 10, 1000):
        directory = tmp_path / str(count)
        directory.mkdir()
        ledger, wake, _, _, runtime = setup(directory, lambda w: [])
        runtime.author = CaptureAuthor()
        live = PlanStateProjection(
            plan_id="plan:current", activity_id="activity:current", entity_revision=1,
            activity_kind="reading", evidence_refs=(EvidenceRef(
                ref_id=wake.event_id, evidence_type="committed_world_event",
                claim_purpose="life_transition", source_world_revision=1,
                immutable_hash=wake.payload_hash,
            ),), status="planned", importance_bp=5000, owner_actor_ref=runtime.actor_ref,
        )
        archived = tuple(live.model_copy(update={
            "plan_id": f"plan:old-{i}", "activity_id": f"activity:old-{i}",
            "status": "completed" if i % 2 else "abandoned",
        }) for i in range(count))
        # The constructor receives the same current work and increasingly long
        # terminal history. No selected evidence, authored condition or World
        # record is removed; stop before authoring/acceptance.
        projection = ledger.project().model_copy(update={"plans": (live, *archived)})
        try:
            with pytest.raises(Captured):
                await runtime.refresh(
                    projection=projection, context={},
                    manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)),
                )
            assert len(projection.plans) == count + 1
            assert [s["source_ref"] for s in packets[-1]["condition_sources"]] == [live.plan_id]
            assert packets[-1] == packets[0]
        finally:
            runtime.close()


def test_outlook_reference_aliases_are_request_bound_and_never_rewrite_prose():
    from companion_daemon.world_v2.world_outlook_request import (
        RESPONSE_REFERENCE_CONTRACT, decode_outlook_response,
    )
    from companion_daemon.world_v2.shared_string_view import pack_shared_strings
    ref = "event:authoritative-history:" + "b" * 90
    request = pack_shared_strings({"response_reference_contract": RESPONSE_REFERENCE_CONTRACT,
                                   "basis_refs": [ref] * 8})
    alias = next(iter(request["strings"]))
    reply = {"draft": {"opportunities": [{"basis_refs": [alias], "execution": {"text": alias}}]}}
    result = decode_outlook_response(json.dumps(reply), request_raw=json.dumps(request))
    node = result["draft"]["opportunities"][0]
    assert node["basis_refs"] == [ref]
    assert node["execution"]["text"] == alias
    old = pack_shared_strings({"basis_refs": [ref] * 8})
    assert decode_outlook_response(json.dumps(reply), request_raw=json.dumps(old)) == reply
    with pytest.raises(ValueError, match="unknown reference alias"):
        decode_outlook_response(json.dumps({"basis_refs": [request["prefix"] + "999"]}),
                                request_raw=json.dumps(request))
    other = pack_shared_strings({"response_reference_contract": RESPONSE_REFERENCE_CONTRACT,
                                 "basis_refs": [ref + "other"] * 8})
    rebound = decode_outlook_response(json.dumps(reply), request_raw=json.dumps(other))
    assert rebound["draft"]["opportunities"][0]["basis_refs"] == [ref + "other"]


@pytest.mark.asyncio
async def test_outlook_real_json_mode_request_includes_provider_required_instruction(tmp_path):
    ledger, wake, _, _, runtime = setup(tmp_path, lambda w: [])
    requests = []

    def provider(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["response_format"] == {"type": "json_object"}
        # DeepSeek rejects before generation when this documented requirement
        # is absent. Exercise the real request builder without any network.
        assert "json" in " ".join(m["content"] for m in body["messages"]).lower()
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps({"decision": "revise", "draft": draft_value(wake)}),
        }}]})

    model = DeepSeekChatModel(api_key="offline", base_url="https://api.deepseek.com",
                             model="deepseek-flash", transport=httpx.MockTransport(provider))
    runtime.author = model
    try:
        result = await runtime.refresh(
            projection=ledger.project(), context={},
            manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)),
        )
        assert result["status"] == "ready", result
        assert len(requests) == 1
    finally:
        runtime.close()
        await model.aclose()


@pytest.mark.asyncio
async def test_oversized_outlook_reports_persisted_failure_without_author_call(tmp_path):
    ledger, wake, store, author, runtime = setup(tmp_path, lambda w: [])
    try:
        args = dict(
            projection=ledger.project(),
            context={"source": "界" * 100_000},
            manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)),
        )
        result = await runtime.refresh(**args)
        assert result["failure_code"] == "outlook_input_too_large"
        assert runtime.health()["status"] == "technical_failure"
        assert runtime.health()["failure_code"] == result["failure_code"]
        assert author.calls == []
        repeat = await runtime.refresh(**args)
        assert repeat["failure_code"] == result["failure_code"]
        assert author.calls == []
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_ecology_does_not_label_outlook_provider_failure_as_idle(tmp_path):
    class Unavailable(ProductionAuthor):
        async def complete(self, messages, **kwargs):
            raise RuntimeError("offline central author unavailable")

    app, _, now = production_application(tmp_path, Unavailable())
    try:
        result = await production_tick(app, now, now + timedelta(minutes=10), "outlook-failure")
        assert result.technical_failure_code == "outlook.outlook_author_failed"
        assert result.life_development_followup_status == "technical_failure"
        assert app.world_outlook_health()["status"] == "technical_failure"
    finally:
        await app.aclose()


@pytest.mark.asyncio
async def test_repeated_large_context_can_be_presented_losslessly(tmp_path):
    ledger, wake, store, author, runtime = setup(
        tmp_path, lambda w: [{"decision": "revise", "draft": draft_value(w)}]
    )
    context = {
        "material": "有来源的同一段完整经历。" * 1600,
        "same_material_in_view": "有来源的同一段完整经历。" * 1600,
    }
    try:
        result = await runtime.refresh(
            projection=ledger.project(),
            context=context,
            manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)),
        )
        assert result["status"] == "ready"
        from companion_daemon.world_v2.world_outlook_request import decode_outlook_request

        packet = decode_outlook_request(author.calls[0][1]["content"])
        assert packet["world_context"] == context
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_compressed_author_response_recovers_without_another_call(tmp_path, monkeypatch):
    from companion_daemon.world_v2.world_outlook_store import SQLiteWorldOutlookStore

    class Crash(BaseException):
        pass

    ledger, wake, store, author, runtime = setup(
        tmp_path, lambda w: [{"decision": "revise", "draft": draft_value(w)}]
    )
    context = {"one": "完整来源。" * 6000, "two": "完整来源。" * 6000}
    args = dict(
        projection=ledger.project(),
        context=context,
        manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)),
    )

    def crash(*args, **kwargs):
        raise Crash()

    try:
        monkeypatch.setattr(store, "install", crash)
        with pytest.raises(Crash):
            await runtime.refresh(**args)
        assert json.loads(author.calls[0][1]["content"])["contract"] == "shared-string-view.1"
        store.close()
        runtime.store = SQLiteWorldOutlookStore(
            path=tmp_path / "outlook.sqlite", world_id=ledger.world_id, actor_ref=runtime.actor_ref
        )
        assert (await runtime.refresh(**args))["status"] == "ready"
        assert len(author.calls) == 1
    finally:
        runtime.close()


def test_source_table_and_string_dictionary_preserve_every_value():
    from companion_daemon.world_v2.world_outlook_request import (
        encode_outlook_request,
        decode_outlook_request,
    )

    rows = [
        {
            "source_ref": f"occurrence:{i}",
            "source_kind": "occurrence",
            "status": "settled",
            "allowed_statuses": ["active", "settled", "cancelled"],
            "token": f"{i:064x}",
        }
        for i in range(200)
    ]
    packet = {
        "contract": "world-outlook-request.1",
        "condition_sources": rows,
        "world_context": {"literal": "@s:0", "a": "完整来源。" * 200, "b": "完整来源。" * 200},
    }
    raw = encode_outlook_request(packet, byte_limit=1)
    assert decode_outlook_request(raw) == packet
    assert len(raw.encode()) < len(json.dumps(packet, ensure_ascii=False).encode())


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupted", [False, True])
async def test_script_retry_on_later_clock_reuses_its_execution_proposal(
    tmp_path, monkeypatch, interrupted
):
    from test_world_outlook import install_executor
    from test_life_development_runtime import _seed_clock

    class Crash(BaseException):
        pass

    ledger, wake, store, author, runtime = setup(
        tmp_path, lambda w: [{"decision": "revise", "draft": draft_value(w)}]
    )
    content = install_executor(ledger, runtime)
    try:
        await runtime.refresh(
            projection=ledger.project(),
            context={},
            manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)),
        )
        node = runtime.ready()[0]
        original = content.commit

        def fail(request):
            raise Crash() if interrupted else OSError("offline injected content failure")

        monkeypatch.setattr(content, "commit", fail)
        if interrupted:
            with pytest.raises(Crash):
                runtime.execute_offer(node, wake_event_ref=wake.event_id)
        else:
            assert (
                runtime.execute_offer(node, wake_event_ref=wake.event_id).status
                == "technical_failure"
            )
        monkeypatch.setattr(content, "commit", original)
        later = _seed_clock(
            ledger,
            event_id="clock:later-retry",
            logical_time=wake.logical_time + timedelta(hours=1),
            logical_time_from=wake.logical_time,
        )
        if interrupted:
            assert runtime.reconcile_accepted_offers(wake_event_ref=later.event_id) == 1
        else:
            ready = next(
                n for n in runtime.ready() if n["opportunity_id"] == node["opportunity_id"]
            )
            assert (
                runtime.execute_offer(ready, wake_event_ref=later.event_id).status
                == "occurrence_committed"
            )
        assert len(ledger.project().world_occurrences) == 1
        assert len(author.calls) == 1
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_previous_live_plan_terminal_state_is_presented_before_consuming_change(tmp_path):
    from test_world_outlook_continuity import npc_setup, npc_manifest, retime
    from test_life_projection import commit, event, mutation, world_evidence
    from companion_daemon.world_v2.world_outlook_request import decode_outlook_request

    ledger, wake, author, runtime, _, _ = npc_setup(tmp_path, lambda d: None)
    try:
        args = dict(context={}, manifest=npc_manifest(ledger, wake))
        assert (await runtime.refresh(projection=ledger.project(), **args))["status"] == "ready"
        assert "plan-tea" in runtime.store.head()[2]["source_tokens"]
        commit(ledger, [event("abandon-existing", "ActivityAbandoned", {
            **mutation("abandon-existing", expected_revision=1,
                       evidence_refs=[world_evidence(ledger, wake.event_id, "life_transition")]),
            "plan_id": "plan-tea", "transitioned_at": wake.logical_time.isoformat(),
            "reason_ref": wake.event_id,
        }, at=wake.logical_time)])
        assert runtime.view()["changed_source_refs"] == ["plan-tea"]
        # Repeated reads cannot consume an unseen change.
        assert runtime.view()["changed_source_refs"] == ["plan-tea"]
        draft = retime(draft_value(wake), ledger.project(), wake)
        draft["parent_revision"] = runtime.store.head()[0]
        author.outputs.append({"decision": "revise", "draft": draft})
        assert (await runtime.refresh(projection=ledger.project(), **args))["status"] == "ready"
        packet = decode_outlook_request(author.calls[-1][1]["content"])
        assert next(s for s in packet["condition_sources"] if s["source_ref"] == "plan-tea")["status"] == "abandoned"
        assert runtime.view()["changed_source_refs"] == []
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_future_offer_cannot_hide_terminal_source_at_historical_pin(tmp_path):
    from test_world_outlook_continuity import npc_setup, npc_manifest
    from test_life_projection import commit, event, mutation, world_evidence
    from test_life_development_runtime import _seed_clock
    from companion_daemon.world_v2.world_outlook_runtime import cursor_of

    ledger, wake, author, runtime, _, _ = npc_setup(tmp_path, lambda d: None)
    try:
        assert (await runtime.refresh(projection=ledger.project(), context={},
                                      manifest=npc_manifest(ledger, wake)))["status"] == "ready"
        for ordinal, kind in enumerate(("ActivityStarted", "ActivityCompleted"), 1):
            ref = "transition:existing:" + str(ordinal)
            commit(ledger, [event(ref, kind, {
                **mutation(ref, expected_revision=ordinal,
                           evidence_refs=[world_evidence(ledger, wake.event_id, "life_transition")]),
                "plan_id": "plan-tea", "transitioned_at": wake.logical_time.isoformat(),
                "reason_ref": wake.event_id,
            }, at=wake.logical_time)])
        pinned = ledger.project()
        later = _seed_clock(ledger, event_id="clock:future-offer", logical_time_from=wake.logical_time,
                            logical_time=wake.logical_time + timedelta(minutes=5))
        payload = {"node": draft_value(wake)["opportunities"][0],
                   "claim_cursor": cursor_of(ledger.project()), "result_cursor": cursor_of(ledger.project()),
                   "effect_refs": ["plan-tea"], "wake_event_ref": later.event_id}
        runtime.store.claim("offer:future", kind="offer", payload=payload)
        runtime.store.finish("offer:future", state="considered", payload=payload)
        assert runtime.view(projection=pinned)["changed_source_refs"] == ["plan-tea"]
        assert runtime.view()["changed_source_refs"] == []
    finally:
        runtime.close()


def test_indexed_source_capacity_is_scoped_to_owner_before_limit(tmp_path):
    from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
    from companion_daemon.world_v2.schemas import ProjectionCursor
    from test_life_projection import WORLD_ID, seed_through_proposal, commit, event, mutation

    ledger = SQLiteWorldLedger(path=tmp_path / "scoped.sqlite", world_id=WORLD_ID)
    try:
        seed_through_proposal(ledger)
        template = ledger.project().plans[0].model_dump(mode="json")
        template.pop("authority_origin", None)
        unrelated = []
        for i in range(2050):
            ref = f"plan:other:{i}"
            plan = {**template, "plan_id": ref, "activity_id": "activity:" + ref,
                    "owner_actor_ref": "actor:other", "participant_refs": [],
                    "location_ref": None, "scheduled_window": None}
            unrelated.append(event("planned:" + ref, "ActivityPlanned", {
                **mutation(ref, expected_revision=0, evidence_refs=plan["evidence_refs"]), "plan": plan,
            }))
        commit(ledger, unrelated)
        state = ledger.project()
        cursor = ProjectionCursor(world_revision=state.world_revision, deliberation_revision=state.deliberation_revision,
                                  ledger_sequence=state.ledger_sequence)
        result = ledger.outlook_entities_at((), cursor=cursor, actor_ref="actor:companion")
        assert [p.plan_id for p in result["plans"]] == ["plan-tea"]
        assert len(state.plans) == 2051
        assert ledger.rebuild() == state
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_closed_source_backlog_is_offered_in_batches_without_skipping_history(tmp_path, monkeypatch):
    from test_world_outlook_continuity import npc_setup, npc_manifest, retime
    from test_life_projection import commit, event, mutation, world_evidence
    from companion_daemon.world_v2.world_outlook_request import decode_outlook_request

    ledger, wake, author, runtime, _, _ = npc_setup(tmp_path, lambda d: None)
    try:
        async def refresh():
            return await runtime.refresh(projection=ledger.project(), context={}, manifest=npc_manifest(ledger, wake))

        assert (await refresh())["status"] == "ready"
        template = ledger.project().plans[0].model_dump(mode="json")
        template.pop("authority_origin", None)
        refs = [f"plan:short-lived:{i}" for i in range(65)]
        for ref in refs:
            plan = {**template, "plan_id": ref, "activity_id": "activity:" + ref,
                    "participant_refs": [], "location_ref": None, "scheduled_window": None}
            commit(ledger, [event("planned:" + ref, "ActivityPlanned", {
                **mutation("planned:" + ref, expected_revision=0, evidence_refs=plan["evidence_refs"]), "plan": plan,
            }, at=wake.logical_time)])
            commit(ledger, [event("abandoned:" + ref, "ActivityAbandoned", {
                **mutation("abandoned:" + ref, expected_revision=1,
                           evidence_refs=[world_evidence(ledger, wake.event_id, "life_transition")]),
                "plan_id": ref, "transitioned_at": wake.logical_time.isoformat(), "reason_ref": wake.event_id,
            }, at=wake.logical_time)])
        offered = []
        for expected_size in (32, 32, 1):
            change = runtime.view()["changed_source_refs"]
            assert len(change) == expected_size
            assert runtime.view()["changed_source_refs"] == change
            draft = retime(draft_value(wake), ledger.project(), wake)
            draft["parent_revision"] = runtime.store.head()[0]
            author.outputs.append({"decision": "revise", "draft": draft})
            if not offered:
                class Crash(BaseException):
                    pass

                original_finish = runtime.store.finish

                def crash_after_install(op, *, state, payload):
                    if state == "accepted":
                        raise Crash()
                    return original_finish(op, state=state, payload=payload)

                with monkeypatch.context() as m:
                    m.setattr(runtime.store, "finish", crash_after_install)
                    with pytest.raises(Crash):
                        await refresh()
                installed = runtime.store.head()
                assert runtime.store.operation(installed[2]["operation_ref"])["state"] == "running"
                assert runtime.view()["needs_revision"]
            else:
                assert (await refresh())["status"] == "ready"
                assert runtime.store.operation(installed[2]["operation_ref"])["state"] == "accepted"
            packet = decode_outlook_request(author.calls[-1][1]["content"])
            closed = [s["source_ref"] for s in packet["condition_sources"] if s["status"] == "abandoned"]
            assert set(closed) == set(change)
            offered.extend(closed)
        assert set(offered) == set(refs) and len(offered) == len(refs)
        assert runtime.view()["changed_source_refs"] == []
        assert len(ledger.project().plans) == 66
    finally:
        runtime.close()


def test_recent_thread_heads_match_full_history_after_growth_rewind_and_rewrite(tmp_path):
    from copy import deepcopy
    from companion_daemon.world_v2.world_outlook import WorldOutlookDraft

    ledger, wake, store, _, runtime = setup(tmp_path, lambda w: [])
    snapshots = []
    try:
        for i in range(100):
            value = draft_value(wake)
            value["opportunities"] = []
            value["parent_revision"] = store.head()[0] if i else None
            value["threads"] = [{**t, "thread_id": f"thread:{(i + j) % 19}", "situation": f"revision {i}, thread {j}"}
                                for j, t in enumerate(value["threads"][:2])]
            pin = {"world_revision": i + 1, "deliberation_revision": 0, "ledger_sequence": i + 1}
            store.install(WorldOutlookDraft.model_validate_json(json.dumps(value)), evidence={"cursor": pin})
            snapshots.append(pin)
        for pin in (snapshots[-1], snapshots[40], snapshots[-1]):
            expected = {}
            for row in reversed(store.revisions()):
                if row["evidence"]["cursor"]["ledger_sequence"] > pin["ledger_sequence"]:
                    continue
                for thread in row["draft"]["threads"]:
                    expected.setdefault(thread["thread_id"], thread)
            assert store.recent_threads(as_of=pin) == tuple(expected.values())[:12]
            assert len(store._thread_heads) == 12
            # Returned objects cannot mutate the bounded cache.
            result = deepcopy(store.recent_threads(as_of=pin))
            result[0]["situation"] = "external mutation"
            assert store.recent_threads(as_of=pin)[0]["situation"] != "external mutation"
        store._db.execute("UPDATE world_outlook_revisions SET payload=json_set(payload,'$.threads[0].situation','tampered') WHERE revision=?",
                          (store.head()[0],))
        with pytest.raises(ValueError, match="binding mismatch"):
            store.recent_threads(as_of=snapshots[-1])
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_idle_view_with_no_history_links_does_not_build_archive_anchors(tmp_path, monkeypatch):
    ledger, wake, _, _, runtime = setup(tmp_path, lambda w: [{"decision": "revise", "draft": draft_value(w)}])
    try:
        assert (await runtime.refresh(projection=ledger.project(), context={},
                                      manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger))))["status"] == "ready"

        def archive_scan(*args, **kwargs):
            raise AssertionError("this view needs no history anchors")

        monkeypatch.setattr("companion_daemon.world_v2.world_outlook_runtime.anchor_catalog", archive_scan)
        assert not runtime.view()["needs_revision"]
    finally:
        runtime.close()


def test_navigation_pages_preserve_global_order_and_failed_effects_among_unrelated_history(tmp_path):
    ledger, wake, store, _, runtime = setup(tmp_path, lambda w: [])
    expected = []
    try:
        for i in range(1000):
            relevant = i % 17 == 0
            node = {**draft_value(wake)["opportunities"][0], "opportunity_id": f"node:{i}",
                    "thread_refs": ["other", "shared"] if relevant else ["other"]}
            payload = {"node": node, "effect_refs": [f"effect:{i}"]}
            ref = f"offer:{i}"
            store.claim(ref, kind="offer", payload=payload)
            store.finish(ref, state="technical_failure" if i % 2 else "considered", payload=payload)
            if relevant:
                expected.append(ref)
        found, before = [], None
        while True:
            page = store.navigation_offer_page(("shared",), before=before, limit=7)
            if not page:
                break
            assert len(page) <= 7
            found.extend(offer["operation_ref"] for _, offer in page)
            before = page[-1][0]
        assert found == expected[::-1]
        assert len(store.offers()) == 1000
    finally:
        runtime.close()


@pytest.mark.parametrize("kind", ["completed_activity", "activity_lifecycle_state", "historical_activity_intention"])
def test_presented_activity_uses_entity_identity_as_well_as_event_identity(kind):
    from companion_daemon.world_v2.world_outlook_runtime import _planning_source_refs
    from companion_daemon.world_v2.world_outlook_history import OutlookHistoryOffer

    context = {"slices": {"world_life": {"availability": "available", "items": [
        {"item_ref": "event:activity-ended", "value": {"context_kind": kind, "plan_id": "plan:ended"}},
        {"item_ref": "event:untyped", "value": {"text": "plan:invented", "plan_id": "plan:not-typed"}},
    ]}}}
    refs = _planning_source_refs({"opportunities": []}, context, OutlookHistoryOffer(items=()), ())
    assert "plan:ended" in refs and "event:activity-ended" in refs
    assert "plan:invented" not in refs and "plan:not-typed" not in refs


@pytest.mark.asyncio
async def test_reconciliation_retains_failed_intermediate_effect_but_skips_settled_offer_archive(tmp_path, monkeypatch):
    from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
    from test_life_development_runtime import _seed_clock
    from test_world_outlook import install_executor

    _, _, store, author, runtime = setup(tmp_path, lambda w: [{"decision": "revise", "draft": draft_value(w)}])
    ledger = SQLiteWorldLedger(path=tmp_path / "world.sqlite", world_id=runtime.ledger.world_id)
    runtime.ledger = ledger
    wake = _seed_clock(ledger)
    install_executor(ledger, runtime)
    try:
        assert (await runtime.refresh(projection=ledger.project(), context={},
                                      manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger))))["status"] == "ready"
        node = runtime.ready()[0]
        original = ledger.commit_at_cursor

        def fail_activation(events, **kwargs):
            if any(e.event_type == "WorldOccurrenceActivated" for e in events):
                raise OSError("crash after intermediate effect")
            return original(events, **kwargs)

        with monkeypatch.context() as m:
            m.setattr(ledger, "commit_at_cursor", fail_activation)
            assert runtime.execute_offer(node, wake_event_ref=wake.event_id).status == "technical_failure"
        failed = store.offers()[0]["operation_ref"]
        assert ledger.project().world_occurrences[0].status == "committed"
        for i in range(1000):
            payload = {"node": {**node, "opportunity_id": f"archived:{i}"}, "effect_refs": []}
            store.claim(f"offer:archived:{i}", kind="offer", payload=payload)
            store.finish(f"offer:archived:{i}", state="technical_failure", payload=payload)
        with ledger.serialized_commit_sequence():
            work = runtime._offers_needing_reconciliation(ledger.project())
            assert [o["operation_ref"] for o in work] == [failed]
        original_offers = store.offers

        def scoped_only(*args, **kwargs):
            assert kwargs.get("node_ids") is not None or kwargs.get("states") is not None
            return original_offers(*args, **kwargs)

        monkeypatch.setattr(store, "offers", scoped_only)
        # Still current: retain the failed task and the intermediate effect.
        assert runtime.reconcile_accepted_offers(wake_event_ref=wake.event_id) == 0
        assert failed in runtime._unsettled_offers
        later = _seed_clock(ledger, event_id="clock:outlook-expired", logical_time_from=wake.logical_time,
                            logical_time=wake.logical_time + timedelta(days=8))
        assert runtime.reconcile_accepted_offers(wake_event_ref=later.event_id) == 0
        assert ledger.project().world_occurrences[0].status == "cancelled"
        assert runtime.reconcile_accepted_offers(wake_event_ref=later.event_id) == 0
        assert runtime._unsettled_offers == {}
        assert store.operation(failed)["state"] == "technical_failure"
        assert len(original_offers()) == 1001 and len(author.calls) == 1
    finally:
        runtime.close()
        ledger.close()


def test_new_working_fact_states_distinguish_withdrawal_while_old_request_contract_is_stable(tmp_path):
    from types import SimpleNamespace
    from test_fact_authority import fact
    from companion_daemon.world_v2.world_outlook import source_catalog

    withdrawn = fact(status="withdrawn", withdrawal_reason_code="source_retracted", withdrawal_evidence_ref="operator:fact")
    projection = SimpleNamespace(facts=(withdrawn,))
    legacy = source_catalog(projection, actor_ref="subject:user")
    working = source_catalog(projection, actor_ref="subject:user", required_refs={withdrawn.fact_id}, legacy_fact_status=False)
    assert legacy[withdrawn.fact_id].status == "current"
    assert working[withdrawn.fact_id].status == "withdrawn"
    assert working[withdrawn.fact_id].allowed_statuses == ("current", "withdrawn")
    assert legacy[withdrawn.fact_id].token == working[withdrawn.fact_id].token


def test_new_reference_contract_uses_lossless_aliases_below_the_size_limit():
    from companion_daemon.world_v2.world_outlook_request import (
        encode_outlook_request, decode_outlook_request, decode_outlook_response, RESPONSE_REFERENCE_CONTRACT,
    )
    value = 'f' * 64
    packet = {'contract':'world-outlook-request.1', 'response_reference_contract':RESPONSE_REFERENCE_CONTRACT,
              'condition_sources':[], 'history':[{'authority_hash':value,'authority_ref':'event:long-source:'+'a'*80} for _ in range(16)]}
    wire = encode_outlook_request(packet, byte_limit=100_000)
    packed = json.loads(wire)
    assert packed.get('contract') == 'shared-string-view.1'
    assert decode_outlook_request(wire) == packet
    source_alias = next(key for key,text in packed['strings'].items() if text.startswith('event:long-source:'))
    assert decode_outlook_response(json.dumps({'supersedes_occurrence_ref':source_alias}),request_raw=wire)['supersedes_occurrence_ref'] == packed['strings'][source_alias]
    alias = next(key for key,text in packed['strings'].items() if text==value)
    assert decode_outlook_response(json.dumps({'authority_hash':alias,'prose':'unchanged'}),request_raw=wire) == {
        'authority_hash':value,'prose':'unchanged'}
    with pytest.raises(ValueError, match='unknown reference alias'):
        decode_outlook_response(json.dumps({'authority_hash':packed['prefix']+'missing'}),request_raw=wire)
    legacy = {k:v for k,v in packet.items() if k!='response_reference_contract'}
    assert json.loads(encode_outlook_request(legacy,byte_limit=100_000)) == legacy
