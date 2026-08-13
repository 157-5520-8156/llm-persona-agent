from __future__ import annotations

import json

from companion_daemon.world_v2.character_interior.inbound_wire import (
    _ExpressionDraftWire,
)
from companion_daemon.world_v2.character_interior.snapshot_compiler import (
    compile_inner_life_snapshot,
)
from companion_daemon.world_v2.companion_identity import (
    CompanionIdentityFrame,
    companion_identity_source_ref,
)
from companion_daemon.world_v2.present_prompt import (
    combined_turn_system_lead,
    order_user_present_payload,
)


def test_combined_system_lead_does_not_fork_on_recall_availability() -> None:
    required = combined_turn_system_lead(private_turn_state_required=True)
    optional = combined_turn_system_lead(private_turn_state_required=False)
    assert required == combined_turn_system_lead(private_turn_state_required=True)
    assert optional == combined_turn_system_lead(private_turn_state_required=False)
    assert "If recall is unavailable" in required
    assert "If recall is unavailable" in optional
    assert "occasion" in required
    assert "occasion" in optional


def test_identity_instruction_leads_with_character_yaml_prose() -> None:
    frame = CompanionIdentityFrame(
        companion_name="沈知栀",
        counterpart_name="geoff",
        base_prompt="你是沈知栀，英文名 Celia Shen。",
        appearance="自然黑色中长发。",
        background="父母在嘉兴经营一家小书店。",
        daily_life=("早上如果没有安排会赖床。",),
        speech_frame="句子偏短，像 QQ/微信私聊。",
        first_message="你好呀，我是沈知栀。",
    )
    wire = _ExpressionDraftWire.__new__(_ExpressionDraftWire)
    wire._identity_frame = frame
    text = wire._identity_instruction()
    assert text.startswith("你是沈知栀")
    assert "自然黑色中长发" in text
    assert "小书店" in text
    assert "你好呀，我是沈知栀" in text


def test_identity_source_ref_ignores_prose_fields() -> None:
    base = CompanionIdentityFrame(
        companion_name="沈知栀",
        counterpart_name="geoff",
        stable_identity_facts=("她叫沈知栀。",),
        speech_frame="句子偏短。",
    )
    with_prose = base.model_copy(
        update={
            "base_prompt": "你是沈知栀，英文名 Celia Shen。",
            "appearance": "自然黑色中长发。",
            "first_message": "你好呀。",
        }
    )
    assert companion_identity_source_ref(base) == companion_identity_source_ref(with_prose)


def test_user_payload_puts_current_trigger_message_last() -> None:
    ordered = order_user_present_payload(
        {
            "current_trigger_message": {"text": "hi"},
            "request": {"purpose": "inbound_turn", "evaluated_world_revision": 9},
            "expression_capabilities": {"modalities": ["text"]},
            "recall_available": True,
        }
    )
    encoded = json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))
    parsed = json.loads(encoded)
    assert list(parsed)[0] == "expression_capabilities"
    assert list(parsed)[-1] == "current_trigger_message"
    assert list(parsed)[-2] == "recall_available"


def test_g5_stable_user_prefix_survives_new_trigger_and_recall_flag() -> None:
    snapshot = {
        "contract": "inner-life-snapshot.1",
        "authority": "derived_from_verified_context",
        "materials": {
            "stable_self": [{"source_ref": "core:1", "slow_evolving": {}}],
            "recent_dialogue": [{"text": "昨天那杯茶", "speaker": "counterpart"}],
        },
        "snapshot_id": "snap:1",
        "snapshot_hash": "a" * 64,
        "cursor": {"world_revision": 1, "deliberation_revision": 1, "ledger_sequence": 1},
    }
    first = order_user_present_payload(
        {
            "expression_capabilities": {"modalities": ["text"]},
            "expression_hard_boundaries": {"contract": "hard-boundary.1"},
            "inner_life_snapshot": dict(snapshot),
            "request": {"evaluated_world_revision": 1, "trigger_ref": "event:a"},
            "recall_available": False,
            "current_trigger_message": {"text": "在吗"},
        }
    )
    second = order_user_present_payload(
        {
            "expression_capabilities": {"modalities": ["text"]},
            "expression_hard_boundaries": {"contract": "hard-boundary.1"},
            "inner_life_snapshot": {
                **snapshot,
                "snapshot_id": "snap:2",
                "snapshot_hash": "b" * 64,
                "cursor": {
                    "world_revision": 2,
                    "deliberation_revision": 1,
                    "ledger_sequence": 4,
                },
            },
            "request": {"evaluated_world_revision": 2, "trigger_ref": "event:b"},
            "recall_available": True,
            "current_trigger_message": {"text": "回来了"},
        }
    )
    left = json.dumps(first, ensure_ascii=False, separators=(",", ":"))
    right = json.dumps(second, ensure_ascii=False, separators=(",", ":"))
    prefix = _common_prefix(left, right)
    assert '"expression_capabilities"' in prefix
    assert '"expression_hard_boundaries"' in prefix
    assert '"stable_self"' in prefix
    assert "昨天那杯茶" in prefix
    assert "current_trigger_message" not in prefix
    assert "event:a" not in prefix
    assert "event:b" not in prefix
    stable = json.dumps(
        {
            "expression_capabilities": first["expression_capabilities"],
            "expression_hard_boundaries": first["expression_hard_boundaries"],
            "inner_life_snapshot": {
                key: value
                for key, value in first["inner_life_snapshot"].items()
                if key not in {"snapshot_id", "snapshot_hash", "cursor"}
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert len(prefix) >= len(stable) - 40


def test_snapshot_keeps_chronological_dialogue_tail_and_delivery_state() -> None:
    items = [
        {
            "item_ref": f"dialogue:{index}",
            "source_ref": f"dialogue:{index}",
            "privacy_class": "private",
            "value": {
                "dialogue_id": f"dialogue:{index}",
                "speaker": "counterpart" if index % 2 == 0 else "companion",
                "text": f"bubble {index}",
                "occurred_at": f"2026-08-13T12:{index:02d}:00+08:00",
                "delivery_state": "observed" if index % 2 == 0 else "delivered",
                "sequence": index + 1,
            },
        }
        for index in range(12)
    ]
    snapshot = compile_inner_life_snapshot(
        {
            "world_id": "world:present",
            "actor_ref": "agent:companion",
            "world_revision": 12,
            "deliberation_revision": 1,
            "ledger_sequence": 12,
            "slices": {"recent_dialogue": {"availability": "available", "items": items}},
        }
    ).model_view()
    dialogue = snapshot["materials"]["recent_dialogue"]
    assert [item["text"] for item in dialogue] == [f"bubble {index}" for index in range(12)]
    assert dialogue[-1]["delivery_state"] == "delivered"
    assert dialogue[0]["delivery_state"] == "observed"


def _common_prefix(left: str, right: str) -> str:
    index = 0
    bound = min(len(left), len(right))
    while index < bound and left[index] == right[index]:
        index += 1
    return left[:index]
