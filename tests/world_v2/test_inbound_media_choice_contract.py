"""Explicit role-owned media choice on a versioned inbound wire."""

import json

import pytest

from companion_daemon.world_v2.character_interior.inbound_tool_contract import InboundToolContracts
from companion_daemon.world_v2.expression_draft import qq_expression_capabilities


def _contract(version="slim3"):
    return InboundToolContracts().contract_for(
        phase="initial", capabilities=qq_expression_capabilities("napcat", media_request_available=True),
        recall_allowed=False, atomic_envelope_version=version,
    )


def _wire(**extra):
    payload = {
        "meaning_of_this": "他问照片在哪里。",
        "my_state": "想给他看一张，先调一下光。",
        "messages": ["行吧 给你看一张", "拍得丑不许笑我啊"],
        "world_claims": [],
        **extra,
    }
    return json.dumps({"result_kind": "decision", "payload_json": json.dumps(payload, ensure_ascii=False)})


def test_media_choice_omission_is_not_silently_treated_as_no_photo():
    with pytest.raises(ValueError, match="photo"):
        _contract().unwrap(_wire())


@pytest.mark.parametrize("choice, expected", [(True, "consider_available_candidate"), (False, "none")])
def test_role_choice_reaches_expression_draft(choice, expected):
    value = json.loads(_contract().unwrap(_wire(photo=choice)))
    assert value["expression_draft"]["media_request"] == expected
    assert [beat["text"] for beat in value["expression_draft"]["beats"]] == [
        "行吧 给你看一张", "拍得丑不许笑我啊"
    ]


def test_legacy_contract_keeps_omission_replay():
    value = json.loads(_contract("slim2").unwrap(_wire()))
    assert value["expression_draft"]["media_request"] == "none"


def test_role_selected_reference_survives_request_local_alias_expansion():
    from companion_daemon.world_v2.reference_wire import expand_inbound_arguments, prepare_reference_view

    source = "event:life:" + "a" * 64
    view, bindings = prepare_reference_view({"source_refs": [source]})
    alias = view["source_refs"][0]
    value = json.loads(_contract().unwrap(expand_inbound_arguments(_wire(photo=alias), bindings)))
    assert value["expression_draft"]["media_request"] == "consider_available_candidate"
    assert value["expression_draft"]["media_source_refs"] == [source]


def test_media_unavailable_does_not_require_a_choice():
    contract = InboundToolContracts().contract_for(
        phase="initial", capabilities=qq_expression_capabilities("napcat", media_request_available=False),
        recall_allowed=False, atomic_envelope_version="slim3",
    )
    assert json.loads(contract.unwrap(_wire()))["expression_draft"]["media_request"] == "none"


@pytest.mark.asyncio
async def test_missing_choice_returns_to_same_author_and_accepts_her_media_choice(tmp_path, monkeypatch):
    import httpx

    from test_slim_atomic_author import _CurrentSlimHTTP, _application, _slim
    from test_whole_candidate_author import _inbound
    from test_world_stimulus_life_intent import _http_result
    from companion_daemon.world_v2.media_preview_conductor import MediaPreviewConductorResult
    from companion_daemon.world_v2.media_request_runtime import MediaRequestRuntime

    class NoCandidateConductor:
        def __init__(self):
            self.calls = 0

        async def advance_once(self, **_kwargs):
            self.calls += 1
            return MediaPreviewConductorResult(status="idle", reason_code="no_candidate")

    class Author(_CurrentSlimHTTP):
        async def __call__(self, request: httpx.Request):
            body = json.loads(request.content)
            name = body.get("tool_choice", {}).get("function", {}).get("name", "")
            if name.startswith("character_inbound_"):
                self.requests.append(body)
                self.authors += 1
                payload = {**_slim(), "messages": ["行吧 给你看一张"]}
                if self.authors > 1:
                    payload["photo"] = True
                return _http_result(body, {"result_kind": "decision", "payload_json": json.dumps(payload, ensure_ascii=False)})
            return await super().__call__(request)

    handler = Author()
    capabilities = qq_expression_capabilities("napcat", media_request_available=True)
    async with _application(tmp_path, monkeypatch, handler, tool_version="slim3", review_version="25", capabilities=capabilities) as app:
        result = await app.respond(_inbound())
        evidence = app.export_replay_evidence()
        assert result.status == "action_authorized"
        assert handler.authors == 2
        calls = [body for body in handler.requests if body.get("tool_choice", {}).get("function", {}).get("name", "").startswith("character_inbound_")]
        assert "photo 必填" in calls[0]["messages"][0]["content"]
        assert "photo" in json.dumps(calls[1]["messages"], ensure_ascii=False)
        accepted = [row.event.payload() for row in evidence.events if row.event.event_type == "ExpressionPlanAccepted"]
        assert len(accepted) == 1
        assert accepted[0]["media_request"] == "consider_available_candidate"
        assert evidence.projection.semantic_hash == evidence.replay.semantic_hash
        assert (await app.drain_actions_once()).status == "settled"
        conductor = NoCandidateConductor()
        media = MediaRequestRuntime(ledger=app._ledger, conductor=conductor)
        assert await media.request_for_actions(result.authorized_action_ids)
        media_result = await media.advance_once(logical_time=app._ledger.project().logical_time,
                                               trace_id="trace:test-media", correlation_id="test-media")
        assert media_result.status == "completed"
        assert media_result.reason_code == "media-request:no_candidate"
        assert conductor.calls == 1
        reopened = MediaRequestRuntime(ledger=app._ledger, conductor=conductor)
        repeated = await reopened.advance_once(logical_time=app._ledger.project().logical_time,
                                              trace_id="trace:restart", correlation_id="restart")
        assert repeated.status == "idle"
        assert conductor.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("old_version,new_version", [("slim2", "slim3"), ("slim2", "slim4"), ("slim3", "slim4")])
async def test_new_wire_reopens_accepted_legacy_decision_without_new_authorship(tmp_path, monkeypatch, old_version, new_version):
    from test_slim_atomic_author import _CurrentSlimHTTP, _application, _slim
    from test_whole_candidate_author import _inbound
    from test_production_turn_application import _DeliveredTransport

    handler = _CurrentSlimHTTP(first_payload={**_slim(), "messages": ["之前选好的话"], **({"photo": False} if old_version == "slim3" else {})})
    capabilities = qq_expression_capabilities("napcat", media_request_available=True)
    async with _application(tmp_path, monkeypatch, handler, tool_version=old_version, review_version="25", capabilities=capabilities) as app:
        old = await app.respond(_inbound())
        assert old.status == "action_authorized"
    transport = _DeliveredTransport()
    async with _application(tmp_path, monkeypatch, handler, tool_version=new_version, review_version="25", capabilities=capabilities, transport=transport) as app:
        restored = await app.respond(_inbound())
        assert restored.authorized_action_ids == old.authorized_action_ids
        assert handler.authors == 1
        assert (await app.drain_actions_once()).status == "settled"
        assert transport.bodies == ["之前选好的话"]


@pytest.mark.asyncio
async def test_repeated_missing_choice_is_technical_failure_not_silent_no_media(tmp_path, monkeypatch):
    from test_slim_atomic_author import _CurrentSlimHTTP, _application, _slim
    from test_whole_candidate_author import _inbound

    handler = _CurrentSlimHTTP(first_payload={**_slim(), "messages": ["行吧 给你看一张"]})
    capabilities = qq_expression_capabilities("napcat", media_request_available=True)
    async with _application(tmp_path, monkeypatch, handler, tool_version="slim3", review_version="25", capabilities=capabilities) as app:
        result = await app.respond(_inbound())
        assert handler.authors == 2
        assert result.status != "action_authorized"
        assert app.export_replay_evidence().projection.actions == ()
