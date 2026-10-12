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
