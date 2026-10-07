import json

import pytest
from jsonschema import Draft202012Validator, ValidationError

from companion_daemon.world_v2.character_interior.inbound_tool_contract import InboundToolContracts
from companion_daemon.world_v2.expression_draft import QQ_NAPCAT_EXPRESSION_CAPABILITIES


def contract(native, *, phase="initial"):
    return InboundToolContracts(native_slim_core=native).contract_for(
        phase=phase, capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
        recall_allowed=True, atomic_envelope_version="slim", schema_dialect="deepseek-strict",
    )


def result(messages):
    return {"result": {"result_kind": "decision", "messages": messages,
        "meaning_of_this": "他想继续聊这件事。", "my_state": "我想说，但有点犹豫。",
        "world_claims": [], "extras_json": json.dumps({"wants": "先听他怎么想"}, ensure_ascii=False)}}


@pytest.mark.parametrize("messages", [[], ["嗯", "我再想想"]])
def test_native_core_preserves_silence_dialogue_feelings_and_optional_choice(messages):
    native, old = contract(True), contract(False)
    value = result(messages)
    schema = native.provider_tools[0]["function"]["parameters"]
    Draft202012Validator(schema).validate(value)
    decision = {k: v for k, v in value["result"].items() if k not in {"result_kind", "extras_json"}}
    decision.update(json.loads(value["result"]["extras_json"]))
    legacy = {"result_kind": "decision", "payload_json": json.dumps(decision, ensure_ascii=False)}
    assert json.loads(native.unwrap(json.dumps(value, ensure_ascii=False))) == json.loads(old.unwrap(json.dumps(legacy, ensure_ascii=False)))
    assert len(json.dumps(schema)) < 3000
    assert native.identity.contract_sha256 != old.identity.contract_sha256


def test_provider_schema_requires_the_semantic_fields_before_generation():
    native = contract(True)
    schema = native.provider_tools[0]["function"]["parameters"]
    for field in ("meaning_of_this", "my_state", "messages", "world_claims"):
        value = result(["嗯"])
        value["result"].pop(field)
        with pytest.raises(ValidationError):
            Draft202012Validator(schema).validate(value)
        with pytest.raises(ValueError):
            native.unwrap(json.dumps(value))


def test_explicit_provider_siblings_are_only_rewrapped_without_semantic_changes():
    value = result(["我再想想"])
    split = {"result": {"result_kind": "decision", "messages": value["result"]["messages"]},
             **{k: v for k, v in value["result"].items() if k not in {"result_kind", "messages"}}}
    assert contract(True).unwrap(json.dumps(split)) == contract(True).unwrap(json.dumps(value))
    split["result"]["my_state"] = "冲突的另一份感受"
    with pytest.raises(ValueError):
        contract(True).unwrap(json.dumps(split))


def test_complete_core_without_optional_selection_keeps_the_original_omission_semantics():
    value = result([])
    value["result"].pop("extras_json")
    assert json.loads(contract(True).unwrap(json.dumps(value)))["expression_draft"]["beats"] == []


def test_optional_string_cannot_overwrite_the_authored_semantic_core():
    value = result(["我想再说两句"])
    value["result"]["extras_json"] = '{"messages":[]}'
    with pytest.raises(ValueError):
        contract(True).unwrap(json.dumps(value))


def test_native_optional_payload_reference_aliases_reach_the_exact_pinned_ids():
    from companion_daemon.world_v2.reference_wire import expand_inbound_arguments, prepare_reference_view

    ref = "affect:episode:" + "f" * 64
    _, bindings = prepare_reference_view({"episodes": [{"episode_id": ref} for _ in range(20)]})
    alias = next(key for key, original in bindings["entries"].items() if original == ref)
    value = result(["还想说一句"])
    value["result"]["extras_json"] = json.dumps({"episode_id": alias})
    restored = json.loads(expand_inbound_arguments(json.dumps(value), bindings))
    assert json.loads(restored["result"]["extras_json"])["episode_id"] == ref


def test_recall_remains_role_selected_and_is_unavailable_in_final_transport():
    value = {"result": {"result_kind": "recall", "payload_json": json.dumps({
        "private_turn_state": {"contract": "private-turn-state.1", "inner_state_summary": "想再找找。", "attended_source_refs": []},
        "recall_request": {"query_text": "以前那次生日"},
    }, ensure_ascii=False)}}
    decoded = json.loads(contract(True).unwrap(json.dumps(value, ensure_ascii=False)))
    assert decoded["recall_request"]["query_text"] == "以前那次生日"
    with pytest.raises(ValueError):
        contract(True, phase="final").unwrap(json.dumps(value))


@pytest.mark.asyncio
async def test_native_core_reaches_v25_http_acceptance_delivery_and_cold_replay(tmp_path, monkeypatch):
    import test_slim_atomic_author as fixture
    from test_whole_candidate_author import _inbound
    from test_world_stimulus_life_intent import _http_result

    original = fixture._InboundCharacterAuthor

    def author(**kwargs):
        return original(**{**kwargs, "visible_source_review_version": "25", "native_slim_core": True})

    monkeypatch.setattr(fixture, "_InboundCharacterAuthor", author)

    class HTTP(fixture._SlimHTTP):
        async def __call__(self, request):
            body = json.loads(request.content)
            name = body.get("tool_choice", {}).get("function", {}).get("name", "")
            if name.endswith("_slim_core"):
                self.requests.append(body)
                self.authors += 1
                value = result(list(fixture.BEATS))
                Draft202012Validator(body["tools"][0]["function"]["parameters"]).validate(value)
                return _http_result(body, value)
            if name.startswith("review_grounded"):
                assert name == "review_grounded_segments_v5"
                self.requests.append(body)
                self.reviews += 1
                packet = json.loads(body["messages"][1]["content"])
                value = {"beat_decisions": [{
                    "beat_index": b["beat_index"],
                    "segments": [{"text": b["text"], "rationale": "当前回应",
                                  "assessment": {"kind": "non_record"}}],
                    "review_complete": True,
                } for b in packet["visible_beats"]]}
                Draft202012Validator(body["tools"][0]["function"]["parameters"]).validate(value)
                return _http_result(body, value)
            raise AssertionError(f"unexpected fixture tool {name}")

    http = HTTP()
    async with fixture._application(tmp_path, monkeypatch, http) as app:
        outcome = await app.respond(_inbound())
        assert outcome.status == "action_authorized"
        assert (await app.drain_actions_once()).status == "settled"
        evidence = app.export_replay_evidence()
    assert http.authors == 1
    assert evidence.projection.semantic_hash == evidence.replay.semantic_hash
    assert tuple(item.text for item in evidence.projection.stored_message_payloads) == fixture.BEATS
    assert http.requests[0]["messages"][0]["content"].startswith("character-inbound-prompt.8-slim-core\n")
