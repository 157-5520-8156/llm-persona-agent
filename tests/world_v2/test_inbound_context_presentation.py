from copy import deepcopy
import json
import os

import pytest

from companion_daemon.world_v2.character_interior.inbound_context_presentation import (
    cache_ordered_inbound_material,
)
from companion_daemon.world_v2.reference_wire import expand_reference_view, prepare_reference_view


def _dialogue_tail(packet):
    from companion_daemon.world_v2.character_interior import inbound_context_presentation

    presenter = getattr(inbound_context_presentation, "dialogue_tail_inbound_material", None)
    return presenter(packet) if presenter is not None else packet


def test_dialogue_tail_keeps_every_value_and_places_protocol_before_conversation():
    packet = _packet("后面还有一件事", 12)
    packet["request"]["plan_response_input"] = {"proof": "source-bound plan details"}
    snapshot = packet["inner_life_snapshot"]
    snapshot["materials"]["conversation"] = {"entries": [{"source_ref": "dialogue:1"}]}
    snapshot["materials"]["recent_dialogue"] = {
        "stable_turns": [
            {"dialogue_id": "dialogue:1", "speaker": "counterpart", "text": "第一句"},
            {"dialogue_id": "dialogue:2", "speaker": "companion", "text": "角色的短反馈"},
        ],
        "volatile_last_turn": {"speaker": "counterpart", "text": "后面还有一件事"},
        "record_scope": "recorded_utterance_only",
    }
    packet["role_result_correction"] = {"failure": "unchanged precise feedback"}
    original = deepcopy(packet)
    encoded, bindings = prepare_reference_view(packet)
    result = _dialogue_tail(encoded)
    assert expand_reference_view(result, bindings) == original
    assert packet == original
    assert list(result)[-3:] == ["inner_life_snapshot", "current_trigger_message", "role_result_correction"]
    assert list(result["inner_life_snapshot"])[-1] == "materials"
    materials = result["inner_life_snapshot"]["materials"]
    assert list(materials)[-2:] == ["conversation", "recent_dialogue"]
    assert list(materials["recent_dialogue"])[-2:] == ["stable_turns", "volatile_last_turn"]
    assert len(json.dumps(result, ensure_ascii=False)) == len(json.dumps(encoded, ensure_ascii=False))


def test_dialogue_tail_does_not_decode_an_opaque_material_packet_or_invent_dialogue():
    packet = {
        "current_trigger_message": {"text": "保留原话"},
        "inner_life_snapshot": {"materials": {"contract": "shared-string-view.1", "opaque": []}},
        "request": {"call_id": "existing-call"},
    }
    before = deepcopy(packet)
    result = _dialogue_tail(packet)
    assert result == packet == before
    assert result["inner_life_snapshot"]["materials"] == before["inner_life_snapshot"]["materials"]
    assert list(result)[-1] == "current_trigger_message"


def _packet(text, cursor):
    ref = 'event:character:' + 'a' * 64
    return {
        'request': {'call_id': 'call:' + 'b' * 64 + str(cursor)},
        'inner_life_snapshot': {
            'cursor': {'ledger_sequence': cursor},
            'materials': {
                'recent_dialogue': [{'text': text, 'source_ref': 'event:dialogue:' + 'c' * 64 + str(cursor)}],
                'stable_self': [{'text': '她的性格与身世', 'source_ref': ref}],
                'routine_background': {'text': '不强制执行的生活习惯', 'null': None},
                'moments_i_can_share': {'items': [
                    {'candidate_id': 'photo:' + 'd' * 64, 'photo_in_hand': False,
                     'hold_reason': 'expired', 'source_ref': ref},
                ]},
            },
            'source_refs': [ref],
        },
        'expression_hard_boundaries': {'source_refs': [ref]},
        'current_trigger_message': {'text': text},
        'recall_available': True,
    }


def test_ordering_keeps_values_permissions_and_source_decoding_exact():
    packet = _packet('回忆可以有感受，但来源不可改', 12)
    original = deepcopy(packet)
    ordered = cache_ordered_inbound_material(packet)
    assert ordered == packet == original
    view, bindings = prepare_reference_view(ordered)
    assert expand_reference_view(view, bindings) == original
    candidate = view['inner_life_snapshot']['materials']['moments_i_can_share']['items'][0]
    assert candidate['photo_in_hand'] is False and candidate['hold_reason'] == 'expired'


def test_changing_dialogue_does_not_renumber_stable_source_or_break_its_prefix():
    views = [prepare_reference_view(cache_ordered_inbound_material(_packet(text, cursor)))[0]
             for text, cursor in [('昨天怎么样', 12), ('以前的生日呢', 13)]]
    stable = [v['inner_life_snapshot']['materials']['stable_self'] for v in views]
    assert stable[0] == stable[1]
    wire = [json.dumps(v, ensure_ascii=False, separators=(',', ':')) for v in views]
    prefix = os.path.commonprefix(wire)
    assert '她的性格与身世' in prefix
    assert 'expired' in prefix
    assert '昨天怎么样' not in prefix
    assert '以前的生日呢' not in prefix


def test_media_rows_roundtrip_preserves_missing_null_and_literal_selectors():
    import pytest
    from companion_daemon.world_v2.character_interior.inbound_context_presentation import (
        pack_literal_rows, unpack_literal_rows,
    )

    items = [{
        'candidate_id': 'candidate:' + str(index), 'source_ref': 'source:' + str(index),
        'family': 'character_media', 'taxonomy': 'character_media:selfie',
        'privacy_class': 'private', 'photo_in_hand': False, 'hold_reason': 'expired',
        **({'location_ref': None} if index % 2 else {}),
    } for index in range(20)]
    items[0]['hold_reason'] = '__absent_cell__'
    packed = pack_literal_rows(items)
    assert isinstance(packed, dict)
    assert unpack_literal_rows(packed) == items
    assert len(json.dumps(packed)) < len(json.dumps(items))
    corrupted = deepcopy(packed)
    corrupted['stable_rows'][0].pop()
    with pytest.raises(ValueError):
        unpack_literal_rows(corrupted)


def test_reference_codec_understands_media_row_source_columns():
    from companion_daemon.world_v2.character_interior.inbound_context_presentation import (
        compact_inbound_media_rows, unpack_literal_rows,
    )
    original = _packet('试试照片', 12)
    items = original['inner_life_snapshot']['materials']['moments_i_can_share']['items']
    items *= 20
    packed = compact_inbound_media_rows(original)
    view, bindings = prepare_reference_view(cache_ordered_inbound_material(packed))
    expanded = expand_reference_view(view, bindings)
    album = expanded['inner_life_snapshot']['materials']['moments_i_can_share']
    album['items'] = unpack_literal_rows(album['items'])
    assert expanded == original


def test_flat_memory_rows_preserve_nested_keys_absent_null_arrays_and_references():
    from companion_daemon.world_v2.character_interior.inbound_context_presentation import (
        pack_flat_literal_rows, unpack_flat_literal_rows,
    )

    source = "source:" + "e" * 64
    items = [{"token": f"m{i}", "candidate": {"source_ref": source, "values": {
        "salience": {"emotional_residue_bp": 5000}, "nullable": None, "empty": {},
        "a/b~c": {"": "__absent_cell__"}, **({"optional": False} if i % 2 else {}),
    }}, "reading": {"source_excerpts": [{"source_ref": source, "text": "原文不能省略"}]}}
        for i in range(12)]
    packed = pack_flat_literal_rows(items)
    assert isinstance(packed, dict)
    assert unpack_flat_literal_rows(packed) == items
    view, bindings = prepare_reference_view({"memories": packed})
    restored = expand_reference_view(view, bindings)
    assert unpack_flat_literal_rows(restored["memories"]) == items


def test_flat_memory_rows_reject_colliding_or_noncanonical_paths():
    import pytest
    from companion_daemon.world_v2.character_interior.inbound_context_presentation import unpack_flat_literal_rows

    for columns, row in [(["/a", "/a/b"], [1, 2]), (["/bad~2"], [1])]:
        packet = {"contract": "literal-flat-row-table.1", "columns": columns,
                  "stable_rows": [row], "absent_cell": "__absent_cell__"}
        with pytest.raises(ValueError):
            unpack_flat_literal_rows(packet)


@pytest.mark.asyncio
async def test_short_authored_feedback_keeps_wait_and_next_input_complete(tmp_path, monkeypatch):
    """Use the existing author/reviewer/SQLite seam; no scripted host reply."""
    from dataclasses import replace
    from test_slim_atomic_author import _CurrentSlimHTTP, _application, _slim
    from test_production_turn_application import _DeliveredTransport
    from test_whole_candidate_author import _inbound

    parts = ("事情还没讲完", "第一件事是会议改期", "第二件事我还在想怎么说")
    feedback = "嗯 你接着说"
    handler = _CurrentSlimHTTP(first_payload={
        **_slim(), "messages": [feedback], "waiting_for": "后续内容",
        "wait": {"after_seconds": 60, "pressure_bp": 2000, "importance_bp": 4000},
    })
    delivered = _DeliveredTransport()
    async with _application(tmp_path, monkeypatch, handler, tool_version="slim4",
                            review_version="25", transport=delivered) as app:
        first = replace(_inbound(), text="\n".join(parts),
                        coalescing_metadata={"content_shapes": ["text", "text", "text"]})
        outcome = await app.respond(first)
        assert outcome.status == "action_authorized"
        assert (await app.drain_actions_once()).status == "settled"
        evidence = app.export_replay_evidence()
        assert delivered.bodies == [feedback]
        assert any(m.response_expectation is not None
                   and m.response_expectation.hoped_response == "后续内容"
                   for m in evidence.projection.expression_plan_manifests)
        # Delivering an acknowledgement is not a counterpart response.
        assert not evidence.projection.response_expectation_assessments

        next_text = "第二件事：我明天可能不参加了，还没决定"
        second = replace(first, platform_message_id="message:continued-story",
                         trace_id="trace:continued-story", text=next_text,
                         coalescing_metadata={"content_shapes": ["text"]})
        assert (await app.respond(second)).status == "action_authorized"
        author_requests = [r for r in handler.requests
                           if r.get("tool_choice", {}).get("function", {}).get("name", "").startswith("character_")]
        assert len(author_requests) == 2
        first_view, second_view = (json.loads(r["messages"][1]["content"]) for r in author_requests)
        assert first_view["current_trigger_message"]["text"] == "\n".join(parts)
        assert second_view["current_trigger_message"]["text"] == next_text
        dialogue = second_view["inner_life_snapshot"]["materials"]["recent_dialogue"]
        rows = dialogue if isinstance(dialogue, list) else [
            *dialogue.get("stable_turns", []), dialogue.get("volatile_last_turn", {}),
        ]
        retained = [row.get("text") for row in rows if isinstance(row, dict)]
        assert "\n".join(parts) in retained and feedback in retained and next_text in retained
        assert list(first_view)[-2:] == ["inner_life_snapshot", "current_trigger_message"]
        assert list(second_view)[-2:] == ["inner_life_snapshot", "current_trigger_message"]
        replay = app.export_replay_evidence()
        assert replay.projection.semantic_hash == replay.replay.semantic_hash


@pytest.mark.asyncio
async def test_saved_previous_layout_recovers_original_request_and_sends_once(tmp_path, monkeypatch):
    from companion_daemon.world_v2.character_interior import inbound_context_presentation
    from companion_daemon.world_v2.visible_review_evidence_storage import read_review_evidence
    from test_launch_visible_source_gate import _audits
    from test_slim_atomic_author import _CurrentSlimHTTP, _application, _slim
    from test_production_turn_application import _DeliveredTransport
    from test_whole_candidate_author import _inbound

    original_text = "角色原来决定发出的内容"
    first_http = _CurrentSlimHTTP(first_payload={**_slim(), "messages": [original_text]})
    with monkeypatch.context() as previous_layout:
        previous_layout.setattr(inbound_context_presentation, "dialogue_tail_inbound_material", lambda packet: packet)
        async with _application(tmp_path, previous_layout, first_http,
                                tool_version="slim4", review_version="25") as app:
            accepted = await app.respond(_inbound())
            assert accepted.status == "action_authorized"
            stored = next(row for row in _audits(app) if row.visible_source_review_json)
            original_request = read_review_evidence(stored.visible_source_review_json)["author_request_json"]
            original_packet = json.loads(json.loads(original_request)["messages"][1]["content"])
            assert list(original_packet)[-2:] != ["inner_life_snapshot", "current_trigger_message"]

    cold_http = _CurrentSlimHTTP()
    delivery = _DeliveredTransport()
    async with _application(tmp_path, monkeypatch, cold_http, tool_version="slim4",
                            review_version="25", transport=delivery) as app:
        recovered = await app.respond(_inbound())
        assert recovered.authorized_action_ids == accepted.authorized_action_ids
        assert not cold_http.requests
        assert (await app.drain_actions_once()).status == "settled"
        assert delivery.bodies == [original_text]
        await app.respond(_inbound())
        await app.drain_actions_once()
        assert delivery.bodies == [original_text] and not cold_http.requests
        stored = next(row for row in _audits(app) if row.visible_source_review_json)
        assert read_review_evidence(stored.visible_source_review_json)["author_request_json"] == original_request
        evidence = app.export_replay_evidence()
        assert evidence.projection.semantic_hash == evidence.replay.semantic_hash
