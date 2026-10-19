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


@pytest.mark.parametrize("contract", ["world-outlook-native.7", "world-outlook-native.8", "world-outlook-native.9", "world-outlook-native.10"])
def test_schema_once_history_selector_requires_exact_separately_pinned_schema(contract):
    from companion_daemon.world_v2.world_outlook_request import (
        ROLLING_REFERENCE_CONTRACT, decode_outlook_response,
    )

    anchor = {"source_ref": "occurrence:one", "authority_hash": "a" * 64}
    packet = {"response_tool_contract": contract,
              "response_reference_contract": ROLLING_REFERENCE_CONTRACT,
              "draft_schema_carrier": "response_tools.world_outlook_v6",
              "current_outlook": {"threads": [], "opportunities": []},
              "history_offer": {"items": [{"anchor": anchor}]}}
    tools = [{"function": {"name": "world_outlook_v6", "parameters": {
        "$defs": {"StoryAnchor": {"properties": {"history_anchor_ref": {}}}}}}}]
    response = {"draft": {"threads": [{"history_anchors": [{"history_anchor_ref": "occurrence:one"}]}]}}
    args = {"request_raw": json.dumps(packet)}
    with pytest.raises(ValueError, match="exact pinned"):
        decode_outlook_response(json.dumps(response), **args)
    decoded = decode_outlook_response(json.dumps(response), response_tools=tools, **args)
    assert decoded["draft"]["threads"][0]["history_anchors"] == [anchor]
    for bad in ({"history_anchor_ref": "unknown"},
                {"history_anchor_ref": "occurrence:one", "authority_hash": "b" * 64}):
        response["draft"]["threads"][0]["history_anchors"] = [bad]
        with pytest.raises(ValueError, match="exact offered source"):
            decode_outlook_response(json.dumps(response), response_tools=tools, **args)


@pytest.mark.asyncio
@pytest.mark.parametrize(("original_contract", "original_working_scope"), [
    *((contract, "outlook-working-sources.3") for contract in [
        "world-outlook-native.1", "world-outlook-native.2", "world-outlook-native.3", "world-outlook-native.4",
        "world-outlook-native.7", "world-outlook-native.8", "world-outlook-native.9", "world-outlook-native.10"]),
    ("world-outlook-native.10", "outlook-working-sources.2"),
])
async def test_rolling_native_http_and_cold_recovery_keep_exact_paid_tool_contract(tmp_path, monkeypatch, original_contract, original_working_scope):
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
    monkeypatch.setattr("companion_daemon.world_v2.world_outlook_runtime._WORKING_SOURCES", original_working_scope)
    monkeypatch.setattr("companion_daemon.world_v2.world_outlook_runtime._ROLLING_PLANNING", "rolling-world-planning.4")
    if original_contract == "world-outlook-native.3":
        monkeypatch.setattr("companion_daemon.world_v2.world_outlook_runtime._ROLLING_PLANNING", "rolling-world-planning.3")
    context = {}
    json_carrier = original_contract in {"world-outlook-native.8", "world-outlook-native.9", "world-outlook-native.10"}
    if original_contract in {"world-outlook-native.7", "world-outlook-native.8", "world-outlook-native.9", "world-outlook-native.10"}:
        import companion_daemon.world_v2.world_outlook_runtime as module
        original_schema = module.outlook_author_schema

        def large_schema(**kwargs):
            return {**original_schema(**kwargs), "description": "schema-contract-" * 2500}

        monkeypatch.setattr(module, "outlook_author_schema", large_schema)
        context = {"qualification_fixture": "evidence-" * 7500}

    def provider(request):
        body = json.loads(request.content)
        requests.append(body)
        packet = decode_outlook_request(body["messages"][1]["content"])
        if json_carrier:
            assert body["response_format"] == {"type": "json_object"} and "tools" not in body
            assert "Do not return an ordinary text/JSON message" not in body["messages"][0]["content"]
            assert 'This request requires exactly {"decision":"revise","draft":<schema>}' in body["messages"][0]["content"]
            envelope = json.loads(body["messages"][-1]["content"])
            tool = {"function": {"parameters": envelope["response_schema"]}}
        else:
            assert body["tool_choice"] == "auto" and "response_format" not in body
            assert "For each response, call world_outlook_v6 once" in body["messages"][0]["content"]
            tool = body["tools"][0]
        assert "Maintain at least two distinct ongoing external story threads" in body["messages"][0]["content"]
        assert "Respond once per world_changes source" in body["messages"][0]["content"]
        value = draft_value(wake)
        for node, identity in zip(value["opportunities"], packet["new_opportunity_ids"], strict=False):
            node["opportunity_id"] = identity
        value["horizon_end"] = (NOW + timedelta(days=30)).isoformat()
        value["long_term"] = [{"stage_id": "year", "starts_on": NOW.date().isoformat(),
                              "ends_on": (NOW + timedelta(days=365)).date().isoformat(), "situation": "仍可改变的外部生活阶段"}]
        from test_story_outlook_replanning import apply_story_contract
        value = apply_story_contract(value, packet)
        result = {"decision": "revise", "draft": value}
        Draft202012Validator.check_schema(tool["function"]["parameters"])
        assert Draft202012Validator(tool["function"]["parameters"]).is_valid(result)
        assert packet["response_tool_contract"] == original_contract
        if original_contract in {"world-outlook-native.7", "world-outlook-native.8", "world-outlook-native.9", "world-outlook-native.10"}:
            assert "draft_schema" not in packet
            assert packet["draft_schema_carrier"] == "response_tools.world_outlook_v6"
            assert packet["world_context"] == {**context, "slices": {}}
            assert tool["function"]["parameters"]["properties"]["draft"]["description"] == "schema-contract-" * 2500
            assert len(body["messages"][1]["content"].encode()) < 100_000
            assert len(json.dumps(tool).encode()) + len(body["messages"][1]["content"].encode()) > 100_000
        if len(requests) == 1:
            if original_contract in {"world-outlook-native.9", "world-outlook-native.10"}:
                result["draft_schema_carrier"] = "copied-request-metadata"
                result["draft"]["opportunities"][0]["basis_refs"] = ["event:not-offered"]
            else:
                result["draft"]["actor_ref"] = "actor:wrong-owner"
        elif json_carrier:
            assistant, feedback = body["messages"][-3:-1]  # Last is the same schema clause.
            assert assistant["role"] == "assistant" and "tool_calls" not in assistant
            if original_contract in {"world-outlook-native.9", "world-outlook-native.10"}:
                assert json.loads(assistant["content"])["draft_schema_carrier"] == "copied-request-metadata"
                assert "outlook response shape invalid" in feedback["content"]
                assert '"unavailable_ref": "event:not-offered"' in feedback["content"]
                assert '"path": "draft.opportunities[0].basis_refs[0]"' in feedback["content"]
            else:
                assert json.loads(assistant["content"])["draft"]["actor_ref"] == "actor:wrong-owner"
                assert "World, owner or predecessor mismatch" in feedback["content"]
            assert feedback["role"] == "user"
        elif original_contract in {"world-outlook-native.3", "world-outlook-native.4", "world-outlook-native.7"}:
            assistant, feedback = body["messages"][-2:]
            assert assistant["role"] == "assistant" and assistant["content"] is None
            assert assistant["tool_calls"][0]["function"]["name"] == "world_outlook_v6"
            assert json.loads(assistant["tool_calls"][0]["function"]["arguments"])["draft"]["actor_ref"] == "actor:wrong-owner"
            assert feedback["role"] == "tool" and feedback["tool_call_id"] == assistant["tool_calls"][0]["id"]
            assert "World, owner or predecessor mismatch" in feedback["content"]
        else:
            assert [m["role"] for m in body["messages"][-2:]] == ["assistant", "user"]
        if json_carrier:
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps(result)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})
        return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None, "tool_calls": [{"id": "result", "type": "function", "function": {
                "name": "world_outlook_v6", "arguments": json.dumps(result)}}]}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})

    model = BackgroundGLMModel("test", "https://fixture.invalid/v1", "GLM-5.3-Flash",
        tool_result_carrier="json_object" if json_carrier else "native",
        token_ledger=SubscriptionTokenLedger(tmp_path / "tokens.sqlite"), transport=httpx.MockTransport(provider))
    runtime.author = model
    class Crash(BaseException):
        pass
    original_install = store.install
    def crash(*args, **kwargs):
        raise Crash()
    monkeypatch.setattr(store, "install", crash)
    args = dict(projection=ledger.project(), context=context, manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)))
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
        monkeypatch.setattr("companion_daemon.world_v2.world_outlook_runtime._WORKING_SOURCES", "outlook-working-sources.3")
        monkeypatch.setattr("companion_daemon.world_v2.world_outlook_runtime._NATIVE_RESULT", "world-outlook-native.4")
        monkeypatch.setattr("companion_daemon.world_v2.world_outlook_runtime._ROLLING_PLANNING", "rolling-world-planning.4")
        reopened = SQLiteWorldOutlookStore(path=tmp_path / "outlook.sqlite", world_id=WORLD_ID, actor_ref=OWNER)
        runtime = WorldOutlookRuntime(ledger=ledger, store=reopened, author=model, actor_ref=OWNER, rolling_planning=False)
        monkeypatch.setattr("companion_daemon.world_v2.world_outlook_runtime.outlook_result_tool", lambda *_: pytest.fail("paid tools must not be regenerated"))
        monkeypatch.setattr(runtime, "_prepaid_detail_refs", lambda *_: pytest.fail("paid key must not use a fresh trigger classifier"))
        assert (await runtime.refresh(**args))["status"] == "ready"
        assert len(requests) == 2
        assert reopened.head()[2]["request_hash"] == expected_hash
        assert reopened.head()[2]["condition_source_scope"] == original_working_scope
        assert reopened.head()[2]["rolling_planning"] == (
            "rolling-world-planning.3" if original_contract == "world-outlook-native.3" else "rolling-world-planning.4")
    finally:
        runtime.close()
        await model.aclose()


@pytest.mark.asyncio
async def test_new_role_plan_change_bypasses_only_the_old_failed_refresh_receipt(tmp_path):
    from test_world_outlook_continuity import npc_setup, npc_manifest
    from test_life_development_runtime import _seed_clock
    from test_life_projection import commit, event, mutation, world_evidence

    def review_soon(draft):
        from datetime import datetime
        draft['review_at'] = (datetime.fromisoformat(draft['opportunities'][0]['opens_at'])
                              + timedelta(seconds=1)).isoformat()

    ledger, wake, author, runtime, _, _ = npc_setup(tmp_path, review_soon)
    try:
        assert (await runtime.refresh(projection=ledger.project(), context={},
                                       manifest=npc_manifest(ledger, wake)))['status'] == 'ready'
        later = _seed_clock(ledger, event_id='clock:role-plan-review',
                            logical_time=wake.logical_time + timedelta(seconds=2),
                            logical_time_from=wake.logical_time)
        author.outputs.extend([RuntimeError('first failure'), RuntimeError('new-source failure')])
        args = {'context': {}, 'manifest': npc_manifest(ledger, later)}
        assert (await runtime.refresh(projection=ledger.project(), **args))['status'] == 'technical_failure'
        key = runtime._refresh_identity(ledger.project(), runtime.view(), ('npc:lin',), runtime.store.head())[2]
        commit(ledger, [event('role-abandoned-plan', 'ActivityAbandoned', {
            **mutation('role-abandoned-plan', expected_revision=1,
                       evidence_refs=[world_evidence(ledger, later.event_id, 'life_transition')]),
            'plan_id': 'plan-tea', 'transitioned_at': later.logical_time.isoformat(),
            'reason_ref': later.event_id,
        }, at=later.logical_time)])
        assert runtime.view()['changed_source_refs'] == ['plan-tea']
        new_key = runtime._refresh_identity(ledger.project(), runtime.view(), ('npc:lin',), runtime.store.head())[2]
        assert new_key != key
        args['manifest'] = npc_manifest(ledger, later)
        assert (await runtime.refresh(projection=ledger.project(), **args))['status'] == 'technical_failure'
        assert len(author.calls) == 3
        assert len(runtime.store.attempts('refresh:' + key)) == 1
        assert len(runtime.store.attempts('refresh:' + new_key)) == 1
    finally:
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("live_history", [False, True])
async def test_refresh_input_stays_bounded_as_unreferenced_plan_history_grows(tmp_path, live_history):
    from companion_daemon.world_v2.schemas import EvidenceRef, PlanStateProjection, PlanAuthorityOrigin
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
            "status": "planned" if live_history else "completed" if i % 2 else "abandoned",
            # Input-compiler fixture: realistic proof shape, no model result or
            # accepted World effect is synthesized by this constructor test.
            "authority_origin": PlanAuthorityOrigin(
                transition_id=f"fixture:{i}", accepted_event_type="ActivityPlanned",
                accepted_event_ref=f"event:archive:{i}", accepted_world_revision=1,
                accepted_payload_hash="a" * 64, accepted_at=wake.logical_time,
                authority_projection_hash="b" * 64, binding_hash="c" * 64),
        }) for i in range(count))
        # The constructor receives the same current work and increasingly long
        # unfinished as well as terminal history with broad manifest proofs. No selected evidence, authored condition or World
        # record is removed; stop before authoring/acceptance.
        projection = ledger.project().model_copy(update={"plans": (live, *archived)})
        try:
            with pytest.raises(Captured):
                await runtime.refresh(
                    projection=projection, context={"slices": {"world_life": {
                        "availability": "available", "items": [{"item_ref": live.plan_id,
                            "value": {"context_kind": "planned_activity", "plan_id": live.plan_id}}]}}},
                    manifest=_manifest(wake, pinned_cursor=_projection_cursor(ledger)).model_copy(update={
                        "anchor_refs": tuple(sorted((wake.event_id, *(p.authority_origin.accepted_event_ref for p in archived))))}),
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


def test_live_plan_and_location_tables_round_trip_without_dropping_authority():
    from companion_daemon.world_v2.world_outlook_request import encode_outlook_request, decode_outlook_request

    packet = {
        "response_reference_contract": "outlook-source-references.3",
        "condition_sources": [],
        "plan_schedules": [{"plan_id": f"plan:{i}", "owner_actor_ref": "actor:companion",
            "status": "planned", "activity_kind": "school", "scheduled_window": {"opens_at": "2026-10-20T10:00:00+08:00"},
            "location_ref": None, "participant_refs": ["actor:companion"]} for i in range(17)],
        "npc_activity_location_capabilities": [{"location_ref": f"location:{i}",
            "availability_kind": "reviewed_schedule", "privacy_class": "private",
            "timezone_name": "Asia/Shanghai", "weekdays": [0, 1, 2, 3, 4],
            "local_windows": ["09:00-17:00"], "authority_refs": [f"event:{i}"]} for i in range(10)],
    }
    wire = encode_outlook_request(packet, byte_limit=1)
    assert decode_outlook_request(wire) == packet
    assert len(wire.encode()) < len(json.dumps(packet, ensure_ascii=False).encode())
    malformed = {"contract": "outlook-records-table.1", "columns": ["plan_id", "plan_id"], "rows": [["a", "b"]]}
    with pytest.raises(ValueError, match="records table"):
        decode_outlook_request(json.dumps({"plan_schedules": malformed}))


def test_outlook_compacts_only_proofs_after_admitting_both_life_sources():
    from types import SimpleNamespace
    from companion_daemon.world_v2.world_outlook_runtime import (
        _outlook_presentation_context, _planning_source_refs,
    )

    context = {"slices": {"world_life": {"availability": "available", "items": [
        {"item_ref": "occurrence:first", "source_hash": "a" * 64,
         "source_bindings": [{"ref": "event:first", "immutable_hash": "b" * 64}],
         "value": {"text": "first", "source": {"authority_event_ref": "event:first"}}},
        {"item_ref": "occurrence:second", "source_hash": "c" * 64,
         "source_bindings": [{"ref": "event:second", "immutable_hash": "d" * 64}],
         "value": {"text": "second", "source_bindings": [{"ref": "event:second"}]}},
    ]}}}
    offered = _planning_source_refs({"opportunities": []}, context, SimpleNamespace(items=()), ())
    shown = _outlook_presentation_context(context)
    assert offered == {"occurrence:first", "occurrence:second"}
    assert [item["item_ref"] for item in shown["slices"]["world_life"]["items"]] == ["occurrence:first", "occurrence:second"]
    assert [item["value"]["text"] for item in shown["slices"]["world_life"]["items"]] == ["first", "second"]
    assert all("source_bindings" not in item and "source_hash" not in item
               for item in shown["slices"]["world_life"]["items"])
    assert all("source" not in item["value"] and "source_bindings" not in item["value"]
               for item in shown["slices"]["world_life"]["items"])
    assert len(context["slices"]["world_life"]["items"]) == 2


def test_outlook_proof_compaction_keeps_privacy_completeness_refs_and_semantics():
    from copy import deepcopy
    from companion_daemon.world_v2.world_outlook_runtime import _outlook_presentation_context

    lane = {"availability": "available", "source_refs": ["event:one", "event:two"],
            "source_hash": "a" * 64, "resolver_proof": {
                "explicit_authority_refs": ["event:one", "event:two"],
                "completeness": "bounded", "privacy_floor": "private",
                "snapshot_hash": "b" * 64}, "items": [{"item_ref": "fact:one", "value": {"text": "原事实"}}]}
    context = {"slices": {"relevant_facts": lane,
                          "empty": {k: v for k, v in lane.items() if k != "items"}}}
    original = deepcopy(context)
    shown = _outlook_presentation_context(context)
    assert context == original
    for name, presented in shown["slices"].items():
        assert presented["source_refs"] == lane["source_refs"]
        assert presented["availability"] == "available"
        assert presented["resolver_proof"] == {"completeness": "bounded", "privacy_floor": "private"}
        assert "source_hash" not in presented
    assert shown["slices"]["relevant_facts"]["items"] == lane["items"]


def test_outlook_reference_feedback_finds_hash_confusion_despite_wrong_root_shape():
    from copy import deepcopy
    from companion_daemon.world_v2.world_outlook_runtime import _outlook_reference_errors

    candidate = {"decision": "revise", "draft_schema_carrier": "request-metadata",
                 "draft": {"opportunities": [{"conditions": [{"source_ref": "occurrence:one",
                                                              "unchanged_token": "a" * 64}]}]}}
    pinned = {"condition_sources": [{"source_ref": "occurrence:one", "token": "b" * 64}],
              "history_offer": {"items": [{"anchor": {"authority_hash": "a" * 64}}]}}
    original = deepcopy(candidate)
    errors = _outlook_reference_errors(candidate, pinned)
    assert candidate == original  # Feedback is not local repair or admission.
    assert errors == [{"path": "draft.opportunities[0].conditions[0].unchanged_token",
                       "source_ref": "occurrence:one", "received": "a" * 64,
                       "required_token": "b" * 64, "received_is_history_authority_hash": True}]
    candidate["draft"]["opportunities"][0]["conditions"][0]["unchanged_token"] = "b" * 64
    assert _outlook_reference_errors(candidate, pinned) == []


def test_reference_feedback_leaves_prior_default_compatibility_to_canonical_validation():
    from companion_daemon.world_v2.world_outlook_runtime import _outlook_reference_errors

    prior = {"opportunity_id": "old", "basis_refs": ["event:old-authority"],
             "conditions": [], "after_opportunities": [], "state": "waiting"}
    pinned = {"basis_refs": ["event:current-authority"], "current_outlook": {"opportunities": [prior]}}
    assert _outlook_reference_errors({"draft": {"opportunities": [
        {"opportunity_id": "old", "basis_refs": ["event:old-authority"]},
    ]}}, pinned) == []
    # Malformed identity must not cause an exception in the error-report path.
    assert _outlook_reference_errors({"draft": {"opportunities": [{"opportunity_id": []}]}}, pinned) == []


@pytest.mark.parametrize("count", [0, 10, 1000])
def test_explicit_source_working_set_does_not_expand_with_unpresented_live_plans(count):
    from types import SimpleNamespace
    from test_situation_plan_presence import _plan, ACTOR
    from companion_daemon.world_v2.world_outlook import source_catalog

    shown = _plan("shown", status="active").head
    others = tuple(_plan(f"other-{i}", status="planned").head for i in range(count))
    projection = SimpleNamespace(plans=(shown, *others), world_occurrences=(), facts=(), npcs=())
    catalog = source_catalog(projection, actor_ref=ACTOR, required_refs={shown.plan_id}, only_referenced=True)
    assert set(catalog) == {shown.plan_id}
    assert len(projection.plans) == count + 1
    # Old pinned requests still resolve their original complete live catalog.
    assert len(source_catalog(projection, actor_ref=ACTOR, required_refs={shown.plan_id})) == count + 1


def test_request_thread_references_deduplicate_only_exact_objects_losslessly():
    from copy import deepcopy
    from companion_daemon.world_v2.world_outlook_request import encode_outlook_request, decode_outlook_request, ROLLING_REFERENCE_CONTRACT
    from companion_daemon.world_v2.shared_string_view import unpack_shared_strings, CONTRACT

    thread = {"thread_id": "thread:one", "situation": "完整的过去与当前依据。" * 50,
              "history_anchors": [{"source_ref": "occurrence:one", "authority_hash": "a" * 64}]}
    different = {**thread, "situation": "不同的历史版本不得被当前措辞覆盖。"}
    packet = {"response_reference_contract": ROLLING_REFERENCE_CONTRACT,
              "condition_source_scope": "outlook-working-sources.2", "condition_sources": [],
              "current_outlook": {"threads": [thread]},
              "realized_story_history": [{"thread": deepcopy(thread)}, {"thread": different}]}
    original = deepcopy(packet)
    wire = encode_outlook_request(packet, byte_limit=100000)
    assert packet == original
    encoded = json.loads(wire)
    encoded = unpack_shared_strings(encoded) if encoded.get("contract") == CONTRACT else encoded
    assert encoded["realized_story_history"][0]["thread"] == {"retain_thread_id": "thread:one"}
    assert encoded["realized_story_history"][1]["thread"] == different
    decoded = decode_outlook_request(wire)
    assert decoded == packet
    decoded["realized_story_history"][0]["thread"]["situation"] = "改动副本"
    assert decoded["current_outlook"]["threads"][0] == thread
    for bad in ({"retain_thread_id": "unknown"}, {"retain_thread_id": "thread:one", "situation": "覆盖"}):
        broken = deepcopy(encoded)
        broken["realized_story_history"][0]["thread"] = bad
        with pytest.raises(ValueError, match="without overrides"):
            decode_outlook_request(json.dumps(broken))
    old = {**packet, "condition_source_scope": "outlook-working-sources.1"}
    assert decode_outlook_request(json.dumps(old)) == old


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
@pytest.mark.parametrize("source_scope", ["outlook-working-sources.2", "outlook-working-sources.3"])
async def test_previous_live_plan_terminal_state_is_presented_before_consuming_change(tmp_path, monkeypatch, source_scope):
    from test_world_outlook_continuity import npc_setup, npc_manifest, retime
    from test_life_projection import commit, event, mutation, world_evidence
    from companion_daemon.world_v2.world_outlook_request import decode_outlook_request

    monkeypatch.setattr("companion_daemon.world_v2.world_outlook_runtime._WORKING_SOURCES", source_scope)
    ledger, wake, author, runtime, _, _ = npc_setup(tmp_path, lambda d: None)
    try:
        args = dict(context={}, manifest=npc_manifest(ledger, wake))
        assert (await runtime.refresh(projection=ledger.project(), **args))["status"] == "ready"
        # A read set and notification acknowledgment are separate in .3.
        # The older paid contract retains its original broad tracking.
        assert ("plan-tea" in runtime.store.head()[2]["source_tokens"]) == (source_scope == "outlook-working-sources.2")
        assert runtime.view(projection=ledger.project())["needs_revision"] is False
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


@pytest.mark.asyncio
@pytest.mark.parametrize('settle', [False, True])
async def test_failed_refresh_survives_ordinary_execution_of_its_paid_batch(tmp_path, settle):
    from test_world_outlook import install_executor
    from test_life_development_runtime import _seed_clock

    def outputs(wake):
        value = draft_value(wake)
        value['review_at'] = (wake.logical_time + timedelta(seconds=1)).isoformat()
        return [{'decision': 'revise', 'draft': value}, RuntimeError('provider unavailable'), RuntimeError('unexpected paid retry')]

    ledger, wake, store, author, runtime = setup(tmp_path, outputs)
    coordinator = install_executor(ledger, runtime)
    try:
        args = {'context': {}, 'manifest': _manifest(wake, pinned_cursor=_projection_cursor(ledger))}
        assert (await runtime.refresh(projection=ledger.project(), **args))['status'] == 'ready'
        later = _seed_clock(ledger, event_id='clock:review', logical_time=wake.logical_time + timedelta(seconds=2), logical_time_from=wake.logical_time)
        args['manifest'] = _manifest(later, pinned_cursor=_projection_cursor(ledger))
        failed = await runtime.refresh(projection=ledger.project(), **args)
        assert failed['status'] == 'technical_failure'
        assert len(author.calls) == 2
        key_before = runtime._refresh_identity(ledger.project(), runtime.view(), (), store.head())[2]
        node = runtime.ready()[0]
        assert runtime.execute_offer(node, wake_event_ref=later.event_id).status == 'occurrence_committed'
        if settle:
            from test_world_consequence_aftermath import _aftermath, _NoCharacterCalls
            from test_world_consequence_producer import _advance
            later = _seed_clock(ledger, event_id='clock:ordinary-settlement',
                                logical_time=wake.logical_time + timedelta(minutes=31),
                                logical_time_from=later.logical_time)
            assert (await _advance(_aftermath(ledger, coordinator._store, _NoCharacterCalls()), later)).status == 'settled'
            assert all(o.status == 'settled' for o in ledger.project().world_occurrences)
        key_after = runtime._refresh_identity(ledger.project(), runtime.view(), (), store.head())[2]
        assert key_after == key_before, 'Ordinary paid batch execution reset failed refresh identity'
        args['manifest'] = _manifest(later, pinned_cursor=_projection_cursor(ledger))
        assert (await runtime.refresh(projection=ledger.project(), **args))['status'] == 'technical_failure'
        assert len(author.calls) == 2, 'Unchanged revision work bought a fresh provider call before retry epoch'
        # The original hourly retry remains live, with every old failure saved.
        later = _seed_clock(ledger, event_id='clock:retry-hour',
                            logical_time=wake.logical_time + timedelta(hours=1),
                            logical_time_from=later.logical_time)
        args['manifest'] = _manifest(later, pinned_cursor=_projection_cursor(ledger))
        assert (await runtime.refresh(projection=ledger.project(), **args))['status'] == 'technical_failure'
        assert len(author.calls) == 3
        assert len(store.attempts('refresh:' + key_before)) == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("byte_limit", [100_000, 4_000])
def test_repeated_short_schedule_values_fit_without_changing_accepted_wire(byte_limit):
    import hashlib
    from companion_daemon.world_v2.world_outlook_request import (
        ROLLING_REFERENCE_CONTRACT, decode_outlook_request, encode_outlook_request,
    )

    packet = {
        "contract": "world-outlook-request.1",
        "response_reference_contract": ROLLING_REFERENCE_CONTRACT,
        "condition_sources": [],
        "plan_schedules": [
            {
                "plan_id": f"plan:choice:{index:03}",
                "owner_actor_ref": "agent:companion",
                "status": "planned",
                "scheduled_window": {
                    "opens_at": "2026-10-19T08:15:00+08:00",
                    "closes_at": "2026-10-19T09:45:00+08:00",
                },
                "participant_refs": ["agent:companion"],
            }
            for index in range(30)
        ],
    }
    raw = encode_outlook_request(packet, byte_limit=byte_limit)
    assert len(raw.encode()) <= byte_limit
    assert decode_outlook_request(raw) == packet
    if byte_limit == 100_000:
        # Already-fitting requests retain their exact pre-fallback wire.
        assert hashlib.sha256(raw.encode()).hexdigest() == (
            "cae68f13a6fdbf8416811ea164d5cbaa2f7dd4c6b75cfee6a594c131cc25193f"
        )


@pytest.mark.parametrize('archive_count', [0, 100, 10000])
def test_unpresented_plan_proofs_do_not_accumulate_in_fate_input(archive_count):
    from types import SimpleNamespace
    from companion_daemon.world_v2.world_outlook_runtime import _planning_source_refs

    plans = tuple(SimpleNamespace(plan_id=f'plan:archive:{n}', owner_actor_ref='agent:companion',
        status='paused', authority_origin=SimpleNamespace(accepted_event_ref=f'event:archive:{n}'))
        for n in range(archive_count))
    context = {'slices': {'world_life': {'availability': 'available', 'items': [
        {'item_ref': 'event:shown', 'value': {'context_kind': 'historical_activity_intention', 'plan_id': 'plan:shown'}}]}}}
    view = {'opportunities': [{'conditions': [{'source_ref': 'plan:past-required'}], 'execution': {}}],
        'changed_source_refs': ['plan:changed']}
    history = SimpleNamespace(items=(SimpleNamespace(anchor=SimpleNamespace(source_ref='occurrence:history')),))
    story = [{'realized_sources': [{'source_ref': 'plan:realized'}]}]
    manifest = SimpleNamespace(anchor_refs=tuple(f'event:archive:{n}'for n in range(archive_count)), npc_capabilities=())
    actual = _planning_source_refs(view, context, history, story,
        projection=SimpleNamespace(plans=plans), manifest=manifest)
    assert actual == {'plan:past-required', 'plan:changed', 'event:shown', 'plan:shown', 'occurrence:history', 'plan:realized'}


@pytest.mark.parametrize("archive_count", [0, 100, 10000])
def test_planning_basis_is_task_scoped_and_keeps_exact_old_required_heads(archive_count):
    from types import SimpleNamespace as S
    from test_life_development_runtime import NOW
    from companion_daemon.world_v2.world_outlook_runtime import _planning_basis_refs

    plans = tuple(S(plan_id=f"plan:archive:{i}",
        authority_origin=S(accepted_event_ref=f"event:archive:{i}"))
        for i in range(archive_count))
    plans += (S(plan_id="plan:required-old", authority_origin=S(accepted_event_ref="event:required-head")),)
    projection = S(plans=plans, logical_time=NOW, committed_world_event_refs=(
        S(event_id="clock:now", event_type="ClockAdvanced", logical_time=NOW),))
    original = ("clock:now", "event:visible", "event:required-head", "event:history", "event:story")
    manifest = S(anchor_refs=(*original, *(f"event:archive:{i}" for i in range(archive_count))))
    context = {"slices": {"facts": {"availability": "available",
        "source_refs": ["event:visible", "event:not-authorized"], "items": []}}}
    history = S(items=(S(anchor=S(authority_ref="event:history")),))
    story = ({"thread": {"history_anchors": [{"authority_ref": "event:story"}]}},)
    args = (projection, context, manifest, {"plan:required-old"}, history, story)
    assert _planning_basis_refs(*args, trigger_ref="clock:now") == original
    assert _planning_basis_refs(*args) == original
    assert len(projection.plans) == archive_count + 1
