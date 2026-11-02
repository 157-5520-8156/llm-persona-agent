"""Presentation of observed cross-channel timing, not a role behavior rule."""

from copy import deepcopy
import inspect
import json
import pytest

from companion_daemon.world_v2.character_interior.inbound_context_window import bounded_inbound_context


def _present(packet):
    # The pre-fix path does not support the versioned option. Its actual
    # presentation still runs here so the test goes red on the missing timing
    # context rather than on a missing function or unsupported parameter.
    if "include_delivery_overlap" not in inspect.signature(bounded_inbound_context).parameters:
        return bounded_inbound_context(packet)
    return bounded_inbound_context(packet, include_delivery_overlap=True)


def _recorded_overlap_packet():
    # Frozen timestamps from the observed case; the private dialogue text and
    # production IDs are deliberately replaced with inert fixture values.
    companions = [
        {"dialogue_id": f"role:{i}", "speaker": "companion", "text": f"beat {i}",
         "delivery_state": "delivered", "occurred_at": time}
        for i, time in enumerate((
            "2026-10-26T16:18:22.705691Z", "2026-10-26T16:18:23.274456Z",
            "2026-10-26T16:18:23.930629Z", "2026-10-26T16:18:24.513981Z",
        ))
    ]
    current = {"dialogue_id": "user:current", "speaker": "counterpart",
               "text": "current message", "delivery_state": "observed",
               "continuity_reasons": ["current_turn"],
               "occurred_at": "2026-10-26T16:18:22Z"}
    return {"inner_life_snapshot": {"materials": {"recent_dialogue": {
        "stable_turns": companions, "volatile_last_turn": current,
    }}}}


def test_frozen_overlap_has_honest_causality_without_removing_delivered_words():
    packet = _recorded_overlap_packet()
    before = deepcopy(packet)
    result = _present(packet)
    assert packet == before
    assert result["inner_life_snapshot"]["materials"]["recent_dialogue"] == before["inner_life_snapshot"]["materials"]["recent_dialogue"]
    timing = result["dialogue_observation_timing"]
    assert timing["current_ref"] == "user:current"
    assert timing["relation_scope"] == "observation_and_provider_receipt_only"
    assert timing["comparisons"] == [
        {"dialogue_ref": "role:0", "relation": "same_second_uncertain"},
        {"dialogue_ref": "role:1", "relation": "role_receipt_recorded_after_observation_second"},
        {"dialogue_ref": "role:2", "relation": "role_receipt_recorded_after_observation_second"},
        {"dialogue_ref": "role:3", "relation": "role_receipt_recorded_after_observation_second"},
    ]
    assert "不能证明" in timing["instruction"]


def test_legacy_presentation_remains_unchanged():
    result = bounded_inbound_context(_recorded_overlap_packet())
    assert "dialogue_observation_timing" not in result
    assert result["context_window"]["contract"] == "inbound-narrative-window.1"


def test_missing_or_naive_times_are_unknown_not_fabricated_order():
    packet = _recorded_overlap_packet()
    dialogue = packet["inner_life_snapshot"]["materials"]["recent_dialogue"]
    dialogue["volatile_last_turn"]["occurred_at"] = "2026-10-26T16:18:22"
    result = _present(packet)["dialogue_observation_timing"]
    assert result["observation_recorded_at"] is None
    assert result["comparison_precision"] == "unknown"
    assert {item["relation"] for item in result["comparisons"]} == {"timestamp_unknown"}

    dialogue["volatile_last_turn"]["occurred_at"] = "2026-10-26T16:18:22Z"
    dialogue["stable_turns"][1].pop("occurred_at")
    dialogue["stable_turns"][2]["delivery_state"] = "authorized"
    result = _present(packet)["dialogue_observation_timing"]
    assert result["comparisons"][1:3] == [
        {"dialogue_ref": "role:1", "relation": "timestamp_unknown"},
        {"dialogue_ref": "role:2", "relation": "timestamp_unknown"},
    ]


def test_provider_accepted_is_not_upgraded_to_delivered():
    packet = _recorded_overlap_packet()
    dialogue = packet["inner_life_snapshot"]["materials"]["recent_dialogue"]
    dialogue["stable_turns"][0]["delivery_state"] = "provider_accepted"
    result = _present(packet)
    role = result["inner_life_snapshot"]["materials"]["recent_dialogue"]["stable_turns"][0]
    assert role["delivery_state"] == "provider_accepted"
    assert result["dialogue_observation_timing"]["comparisons"][0]["relation"] == "same_second_uncertain"
    assert "及其记录的投递状态均保留" in result["dialogue_observation_timing"]["instruction"]


def test_timing_can_be_presented_without_enabling_a_narrative_window():
    from companion_daemon.world_v2.character_interior.inbound_context_window import present_dialogue_observation_timing

    packet = _recorded_overlap_packet()
    turns = packet["inner_life_snapshot"]["materials"]["recent_dialogue"]["stable_turns"]
    turns[:] = [deepcopy(item) for item in turns * 3]
    for i, item in enumerate(turns):
        item["dialogue_id"] = f"role:{i}"
    packet["role_result_correction"] = {"failure": "recorded"}
    before = deepcopy(packet)
    result = present_dialogue_observation_timing(packet)
    assert packet == before
    assert result["inner_life_snapshot"] == before["inner_life_snapshot"]
    assert len(result["inner_life_snapshot"]["materials"]["recent_dialogue"]["stable_turns"]) == 12
    assert result["dialogue_observation_timing"]["current_ref"] == "user:current"
    assert list(result)[-1] == "role_result_correction"


def test_missing_dialogue_context_is_explicitly_unknown():
    from companion_daemon.world_v2.character_interior.inbound_context_window import present_dialogue_observation_timing

    timing = present_dialogue_observation_timing({"inner_life_snapshot": {"materials": {}}})["dialogue_observation_timing"]
    assert timing["current_ref"] is None
    assert timing["comparison_precision"] == "unknown"
    assert timing["comparisons"] == []


def test_ambiguous_current_turn_and_old_receipts_do_not_become_claimed_order():
    packet = _recorded_overlap_packet()
    dialogue = packet["inner_life_snapshot"]["materials"]["recent_dialogue"]
    dialogue["stable_turns"][0]["occurred_at"] = "2026-10-26T16:18:21.999999Z"
    assert _present(packet)["dialogue_observation_timing"]["comparisons"][0]["dialogue_ref"] == "role:1"
    dialogue["volatile_last_turn"].pop("continuity_reasons")
    timing = _present(packet)["dialogue_observation_timing"]
    assert timing["current_ref"] is None
    assert {item["relation"] for item in timing["comparisons"]} == {"timestamp_unknown"}


def test_comparisons_reference_only_retained_companion_items():
    packet = _recorded_overlap_packet()
    dialogue = packet["inner_life_snapshot"]["materials"]["recent_dialogue"]
    dialogue["stable_turns"] = [deepcopy(item) for item in dialogue["stable_turns"] * 3]
    for i, item in enumerate(dialogue["stable_turns"]):
        item["dialogue_id"] = f"role:{i}"
    result = _present(packet)
    retained = result["inner_life_snapshot"]["materials"]["recent_dialogue"]["stable_turns"]
    refs = {item["dialogue_id"] for item in retained}
    assert len(refs) == 8
    assert {item["dialogue_ref"] for item in result["dialogue_observation_timing"]["comparisons"]} <= refs


def test_timing_refs_follow_the_same_request_local_aliases_as_dialogue():
    from companion_daemon.world_v2.character_interior.inbound_context_presentation import cache_ordered_inbound_material
    from companion_daemon.world_v2.model_context_reading import present_context_evidence
    from companion_daemon.world_v2.reference_wire import prepare_reference_view, expand_reference_view

    packet = _recorded_overlap_packet()
    dialogue = packet["inner_life_snapshot"]["materials"]["recent_dialogue"]
    dialogue["stable_turns"] = [deepcopy(item) for item in dialogue["stable_turns"] * 2]
    for index, item in enumerate(dialogue["stable_turns"]):
        item["dialogue_id"] = f"dialogue:role:{index}:" + "a" * 200
    dialogue["volatile_last_turn"]["dialogue_id"] = "dialogue:user:" + "b" * 200
    presented = cache_ordered_inbound_material(present_context_evidence(_present(packet)))
    encoded, bindings = prepare_reference_view(presented)
    assert bindings is not None
    items = encoded["inner_life_snapshot"]["materials"]["recent_dialogue"]
    timing = encoded["dialogue_observation_timing"]
    assert timing["current_ref"] == items["volatile_last_turn"]["dialogue_id"]
    assert timing["comparisons"][0]["dialogue_ref"] == items["stable_turns"][0]["dialogue_id"]
    assert expand_reference_view(encoded, bindings) == presented


async def _captured_author_request(tmp_path, monkeypatch, version):
    from test_production_turn_application import _DeliveredTransport
    from test_slim_atomic_author import _CurrentSlimHTTP, _application, _slim
    from test_whole_candidate_author import _inbound
    from test_launch_visible_source_gate import _audits
    from companion_daemon.world_v2.visible_source_runtime import verify_recorded_candidate
    from companion_daemon.world_v2.visible_review_evidence_storage import read_review_evidence

    tmp_path.mkdir(parents=True, exist_ok=True)
    handler = _CurrentSlimHTTP(first_payload={**_slim(), "photo": False, "messages": ["角色自己选择的话"]})
    async with _application(tmp_path, monkeypatch, handler, tool_version=version, review_version="25", transport=_DeliveredTransport()) as app:
        result = await app.respond(_inbound())
        assert result.status == "action_authorized"
        assert (await app.drain_actions_once()).status == "settled"
        evidence = app.export_replay_evidence()
        assert evidence.projection.semantic_hash == evidence.replay.semantic_hash
        winner = next(item for item in _audits(app) if item.visible_source_review_json)
        stored = read_review_evidence(winner.visible_source_review_json)
        assert json.loads(stored["author_request_json"])["messages"] == handler.requests[0]["messages"]
        proposal = next(item for item in evidence.projection.proposal_audits if item.proposal_kind == "decision")
        assert verify_recorded_candidate(audit=proposal, model_result_audits=evidence.projection.model_result_audits)
    author = next(request for request in handler.requests if request.get("tool_choice", {}).get("function", {}).get("name", "").startswith("character_inbound_"))
    return author


def test_versioned_author_contract_has_distinct_identity_and_keeps_media_choice():
    from companion_daemon.world_v2.character_interior.inbound_tool_contract import InboundToolContracts
    from companion_daemon.world_v2.expression_draft import qq_expression_capabilities
    import json
    caps = qq_expression_capabilities("napcat", media_request_available=True)
    contracts = InboundToolContracts()
    old = contracts.contract_for(phase="initial", capabilities=caps, recall_allowed=False, atomic_envelope_version="slim3")
    new = contracts.contract_for(phase="initial", capabilities=caps, recall_allowed=False, atomic_envelope_version="slim4")
    assert old.identity.tool_name != new.identity.tool_name
    payload = json.dumps({"result_kind": "decision", "payload_json": json.dumps({"meaning_of_this": "先听", "my_state": "我在想", "world_claims": [], "messages": ["嗯"]}, ensure_ascii=False)})
    with pytest.raises(ValueError, match="photo"):
        new.unwrap(payload)


@pytest.mark.asyncio
async def test_new_author_receives_timing_context_without_changing_legacy_wire(tmp_path, monkeypatch):
    import json

    old = await _captured_author_request(tmp_path / "old", monkeypatch, "slim3")
    new = await _captured_author_request(tmp_path / "new", monkeypatch, "slim4")
    assert "dialogue_observation_timing" not in json.loads(old["messages"][1]["content"])
    assert "dialogue_observation_timing" in json.loads(new["messages"][1]["content"])
    assert old["tool_choice"]["function"]["name"] == "character_inbound_initial_slim3"
    assert new["tool_choice"]["function"]["name"] == "character_inbound_initial_slim4"


@pytest.mark.asyncio
async def test_new_author_receives_timing_after_bounded_context_and_before_reference_view(tmp_path, monkeypatch):
    import test_slim_atomic_author as fixture

    original = fixture._InboundCharacterAuthor

    def bounded_author(**kwargs):
        kwargs["inbound_context_window"] = True
        return original(**kwargs)

    monkeypatch.setattr(fixture, "_InboundCharacterAuthor", bounded_author)
    request = await _captured_author_request(tmp_path, monkeypatch, "slim4")
    packet = json.loads(request["messages"][1]["content"])
    assert packet["context_window"]["contract"] == "inbound-narrative-window.1"
    assert "dialogue_observation_timing" in packet


def test_counterpart_temporal_rhythm_and_activity_presence_mode_are_derived_factually():
    from types import SimpleNamespace
    from companion_daemon.world_v2.character_interior.inbound_context_window import (
        present_dialogue_observation_timing,
    )
    from companion_daemon.world_v2.qq_c2c_host import QQC2CHost

    packet = {
        "inner_life_snapshot": {
            "materials": {
                "recent_dialogue": {
                    "stable_turns": [
                        {
                            "dialogue_id": "user:prev",
                            "speaker": "counterpart",
                            "text": "昨晚那题做完了",
                            "delivery_state": "observed",
                            "occurred_at": "2026-10-26T23:10:00+08:00",
                        },
                        {
                            "dialogue_id": "role:prev",
                            "speaker": "companion",
                            "text": "这么晚还在写呀",
                            "delivery_state": "delivered",
                            "occurred_at": "2026-10-26T23:12:00+08:00",
                        },
                    ],
                    "volatile_last_turn": {
                        "dialogue_id": "user:now",
                        "speaker": "counterpart",
                        "text": "刚刚又看了一眼",
                        "delivery_state": "observed",
                        "continuity_reasons": ["current_turn"],
                        "occurred_at": "2026-10-27T01:12:00+08:00",
                    },
                }
            }
        }
    }
    timing = present_dialogue_observation_timing(packet)["dialogue_observation_timing"]
    assert timing["counterpart_turn_local_hour"] == 1
    assert timing["seconds_since_previous_counterpart_turn"] == 7320
    assert timing["seconds_since_latest_companion_turn"] == 7200
    assert timing["observed_counterpart_local_hours"] == [1, 23]

    class_proj = SimpleNamespace(
        v2_attention={},
        plans=(SimpleNamespace(status="active", activity_kind="class", actor_ref="agent:companion"),),
    )
    study_proj = SimpleNamespace(
        v2_attention={},
        plans=(SimpleNamespace(status="active", activity_kind="study", actor_ref="agent:companion"),),
    )
    leisure_proj = SimpleNamespace(
        v2_attention={},
        plans=(SimpleNamespace(status="active", activity_kind="leisure", actor_ref="agent:companion"),),
    )
    assert QQC2CHost._derive_companion_attention_mode(class_proj) == "do_not_disturb"
    assert QQC2CHost._derive_companion_attention_mode(study_proj) == "occupied"
    assert QQC2CHost._derive_companion_attention_mode(leisure_proj) == "available"


def test_utc_timestamps_report_shanghai_local_hour_in_timing_view():
    from companion_daemon.world_v2.character_interior.inbound_context_window import (
        present_dialogue_observation_timing,
    )

    packet = {
        "inner_life_snapshot": {
            "materials": {
                "recent_dialogue": {
                    "stable_turns": [
                        {
                            "dialogue_id": "user:prev",
                            "speaker": "counterpart",
                            "text": "上午好",
                            "delivery_state": "observed",
                            "occurred_at": "2026-10-27T03:08:52Z",
                        }
                    ],
                    "volatile_last_turn": {
                        "dialogue_id": "user:now",
                        "speaker": "counterpart",
                        "text": "Hello hello",
                        "delivery_state": "observed",
                        "continuity_reasons": ["current_turn"],
                        "occurred_at": "2026-10-28T17:29:12Z",
                    },
                }
            }
        }
    }
    timing = present_dialogue_observation_timing(packet)["dialogue_observation_timing"]
    assert timing["counterpart_turn_local_hour"] == 1
    assert timing["observed_counterpart_local_hours"] == [1, 11]


