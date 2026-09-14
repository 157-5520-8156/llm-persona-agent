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
    affect_material_entries,
    appraisal_material_rows,
    cache_stable_recent_dialogue,
    combined_turn_system_lead,
    dialogue_attention_reasons,
    expand_present_world_context,
    order_user_present_payload,
    recent_dialogue_material_entries,
    slim_consider_instruction,
)
from companion_daemon.world_v2.character_interior.appraisal_model_view import appraisal_meanings


def test_combined_system_lead_does_not_fork_on_recall_availability() -> None:
    required = combined_turn_system_lead(private_turn_state_required=True)
    optional = combined_turn_system_lead(private_turn_state_required=False)
    assert required == combined_turn_system_lead(private_turn_state_required=True)
    assert optional == combined_turn_system_lead(private_turn_state_required=False)
    assert "If recall is unavailable" in required
    assert "If recall is unavailable" in optional
    assert "occasion" in required
    assert "occasion" in optional


def test_slim_consider_instruction_speaks_her_language() -> None:
    """Everything addressed to her is Chinese; only literal JSON stays English.

    She writes Chinese, and reading every option for who she is in English,
    inside an English wire contract, is what made the whole turn read as a form
    to fill in. The English anchors that remain are neutrality guarantees other
    tests grep for, plus field names and enum values that are literal JSON.
    """

    instruction = slim_consider_instruction()
    chinese = sum(1 for char in instruction if "\u4e00" <= char <= "\u9fff")
    assert chinese / len(instruction) > 0.35


def test_slim_consider_separates_knowing_from_saying() -> None:
    instruction = slim_consider_instruction()

    assert "是你已经知道和理解事情的背景" in instruction
    assert "默默读懂也已经是在使用它们" in instruction
    assert "不是台词清单" in instruction
    assert "禁止提起" not in instruction
    lead = combined_turn_system_lead(private_turn_state_required=True)
    assert "appraisal_draft 负责你私下怎么理解" in lead
    assert "expression_draft 负责你决定说什么" in lead
    for field in (
        "messages",
        "meaning_of_this",
        "my_state",
        "affect",
        "components",
        "matters_bp",
        "us_deltas",
        "we_are",
        "declared_display",
        "waiting_for",
        "come_back",
        "come_back_in",
        "later",
    ):
        assert field in instruction
    for enum_value in ("still_pending", "resentment", "close_friend", "supersede", "sexual_suggestive"):
        assert enum_value in instruction
    for translated in (
            "只有你写下的内心状态会跟着你走",
        "宿主不会替你编",
        "都不写就是这一轮什么都没留下",
    ):
        assert translated in instruction
    assert "source_ref_aliases" in instruction
    assert "基点" in instruction
    assert "不是百分制" in instruction


def test_present_relationship_stage_note_is_not_a_behavior_instruction() -> None:
    instruction = slim_consider_instruction()
    assert "evidence, not instruction" in instruction
    assert "no target stage and no preferred direction" in instruction
    assert "Feeling drawn, uncertain, bored, or pulled away are all yours" in instruction
    assert "meaning_of_this 是你对他这句话或眼前处境的暂定理解" in instruction
    assert "my_state 是你表达前自己此刻真正是什么感觉" in instruction
    assert "ticket-closing" in instruction  # kept as an English anchor
    assert "declared_display" in instruction
    assert "也不偏好你写了比不写更好" in instruction
    assert "发了更好" not in instruction
    assert "应该给他看" not in instruction
    assert "试着发一张" not in instruction
    assert "就别写 wait" not in instruction
    assert "永远不要从标点" not in instruction
    assert "什么都没搁着就两个都别写" not in instruction
    assert "应该升级" not in instruction
    assert "试着声明" not in instruction
    assert "应该说出来" not in instruction
    assert "请写 wait" not in instruction
    assert "记得写" not in instruction
    assert "写大一点" not in instruction
    assert "叫醒你" in instruction
    assert "30、60、90" in instruction
    assert "不写 wait 不会有人按秒叫你" in instruction
    assert "口头说「我等你」和写下 waiting_for 不是同一件事" in instruction
    assert "两千才是两成，八十不是百分之八" in instruction
    assert "come_back 是你心里搁着的一件事" in instruction
    assert "we_are 是你可以写的字段" in instruction
    assert "we_are=friend" in instruction
    assert "不必先经过 acquaintance" in instruction
    assert "只在字段里写 we_are，账本上的阶段不会动" in instruction
    assert "一字不差" in instruction
    assert "从不替你生成 said_as" in instruction
    assert "从不从你的措辞里推断承诺" in instruction
    assert "选了沉默" in instruction
    assert "两千" in instruction
    assert "四千五" in instruction
    assert "单次 +20" in instruction
    assert "从不建议你写大或写小" in instruction
    assert "reliability_bp 是你觉得他靠不靠得住" in instruction
    assert "repair_confidence_bp 是闹别扭之后你觉得还能不能修好" in instruction
    assert "不必先有承诺，也不必先有裂痕" in instruction
    assert "三个得一起写" in instruction
    assert "也不会替你补上缺的字段" in instruction
    assert "也从不建议你写哪几根轴" in instruction
    assert "应该写 reliability" not in instruction
    assert "不写、不说、维持现状、说出来" in instruction
    assert "photo 写 true，意思是你现在想让媒体车道考虑一个可用的候选" in instruction
    assert "只在文字里说要发图不会打开这条车道" in instruction
    assert "photo true（或写一个可用候选的 source_ref）可以搭 reply_only" in instruction
    assert "photo true 不能搭 reply_only" not in instruction
    assert "day_sheet 和传记习惯不能证明当前或刚才发生了什么" in instruction
    assert "语气、态度、记忆的不确定感和愿望由你写" in instruction
    assert "不会自动变成已发生的事实或持续状态" in instruction
    assert "photos_i_shared 是你已经成功发给他的照片这一世界事实" in instruction
    assert "candidate_only" in instruction
    assert "even-tempered" not in instruction
    assert "unfinished bubble" not in instruction
    lowered = instruction.lower()
    assert "please overthink" not in lowered
    assert "must probe" not in lowered
    assert "must flirt" not in lowered
    assert "must be cute" not in lowered
    assert "你在暧昧" not in instruction
    assert "搞抽象" not in instruction
    assert "relationship_signal" not in instruction


def test_present_moments_i_can_share_now_is_a_world_fact() -> None:
    instruction = slim_consider_instruction()
    assert "moments_i_can_share 里有两件互不替代的世界事实" in instruction
    assert "photo_in_hand 为 true 表示这张现在就可以选、就可以发" in instruction
    assert "和 now.photographable 无关" in instruction
    assert "now.photographable 为 true" in instruction
    assert "photographable 为 false 只和「现在新拍一张" in instruction
    assert "不阻止你发 photo_in_hand 为 true 的存货" in instruction
    assert "也不等于还在整理、相册是空的、或现在发不了手上的那张" in instruction
    assert "available_count 为 0 才是现在没有可发的存货" in instruction
    assert "no_active_activity" in instruction
    assert "annex_insufficient" in instruction
    assert "already_open" in instruction
    assert "what_happened 是那一刻已接受的原文" in instruction
    assert "hold_reason 是原因" in instruction
    assert "already_shared 已经发给他" in instruction
    assert "这不是建议你发存货，也不是建议你去拍" in instruction
    assert "说现在拍、现在发、马上给一张此刻的照片，会和这个事实打架" not in instruction
    assert "没有候选、现在拍不了，都如实是空的" not in instruction
    assert "条数就是还活着的条数，不是只给你看最近一条" in instruction
    assert "hold_reason=user_channel_limited" in instruction
    assert "发出去的那张会出现在 conversation 栏里" in instruction
    assert "conversation 栏可能暂时画不出" not in instruction
    assert "你可以给他看看" not in instruction
    assert "不要答应现在拍" not in instruction
    assert "应该给他看" not in instruction
    assert "试着发一张" not in instruction
    assert "你应该发了" not in instruction
    lowered = instruction.lower()
    assert "civitai" not in lowered
    assert "lora" not in lowered
    assert "charge" not in lowered
    assert "生成提示" not in instruction


def test_slim_photo_true_binds_media_request_and_rides_reply_only() -> None:
    from companion_daemon.world_v2.present_prompt import (
        compile_slim_consider_payload,
        compile_slim_interior_envelope,
    )

    slim = {
        "messages": ["想给你看一张。"],
        "meaning_of_this": "这张图适合回应眼前的话题",
        "my_state": "我现在想分享",
        "stuck_with_me": "想分享这一下",
        "wants": "试试发图",
        "photo": True,
    }
    compiled = compile_slim_consider_payload(slim)
    assert compiled is not None
    assert compiled["expression_draft"]["media_request"] == "consider_available_candidate"
    reply_only = compile_slim_interior_envelope(slim, reply_only=True)
    assert reply_only is not None
    assert reply_only["events"][0]["media_request"] == "consider_available_candidate"
    envelope = compile_slim_interior_envelope(slim, reply_only=False)
    assert envelope is not None
    assert "consider_available_candidate" in str(envelope)


def test_slim_photo_source_ref_survives_reply_only() -> None:
    from companion_daemon.world_v2.present_prompt import compile_slim_interior_envelope

    slim = {
        "messages": ["书店那张发你"],
        "meaning_of_this": "他在等那张书店照片",
        "my_state": "我现在想分享",
        "photo": "event:shareable-photo:bookstore",
    }
    envelope = compile_slim_interior_envelope(slim, reply_only=True)
    assert envelope is not None
    head = envelope["events"][0]
    assert head["media_request"] == "consider_available_candidate"
    assert head["media_source_refs"] == ["event:shareable-photo:bookstore"]


def test_slim_later_with_photo_still_visible_reject() -> None:
    from companion_daemon.world_v2.present_prompt import (
        SLIM_LATER_REQUIRES_TEXT,
        compile_slim_interior_envelope,
    )

    slim = {
        "messages": ["晚点发你"],
        "meaning_of_this": "他在等一张照片",
        "my_state": "我想分享但不是现在",
        "later": 60,
        "photo": True,
    }
    try:
        compile_slim_interior_envelope(slim, reply_only=True)
        raise AssertionError("later+photo must reject")
    except ValueError as exc:
        assert "photo" in str(exc).lower() or SLIM_LATER_REQUIRES_TEXT in str(exc)


def test_identity_instruction_allows_private_color_without_invented_history() -> None:
    frame = CompanionIdentityFrame(
        companion_name="沈知栀",
        counterpart_name="geoff",
        base_prompt="你是沈知栀，英文名 Celia Shen。",
        personality_frame="慢热，熟了以后会俏皮一点，偶尔轻轻调侃。说话软，有分寸。",
        appearance="自然黑色中长发。",
        background="父母在嘉兴经营一家小书店。",
        daily_life=("早上如果没有安排会赖床。",),
        speech_frame="句子偏短，像 QQ/微信私聊。",
        values=("真诚比漂亮话重要。",),
        first_message="你好呀，我是沈知栀。",
        not_an_assistant=True,
        boundaries=(
            "当前感受和联想由你决定；记忆模糊不提供新增经历的依据。",
        ),
    )
    wire = _ExpressionDraftWire.__new__(_ExpressionDraftWire)
    wire._identity_frame = frame
    text = wire._identity_instruction()
    assert text.startswith("你是沈知栀")
    assert "color this chat" in text
    assert "Chat prose does not silently become Fact" in text
    assert "picture already went out" in text
    assert "invent a scene" not in text
    assert "tonight's report" in text
    assert "记忆模糊不提供新增经历的依据" in text


def test_identity_instruction_leads_with_character_yaml_prose() -> None:
    frame = CompanionIdentityFrame(
        companion_name="沈知栀",
        counterpart_name="geoff",
        base_prompt="你是沈知栀，英文名 Celia Shen。",
        personality_frame="慢热，熟了以后会俏皮一点，偶尔轻轻调侃。说话软，有分寸。",
        appearance="自然黑色中长发。",
        background="父母在嘉兴经营一家小书店。",
        daily_life=("早上如果没有安排会赖床。",),
        speech_frame="句子偏短，像 QQ/微信私聊。",
        values=("真诚比漂亮话重要。",),
        first_message="你好呀，我是沈知栀。",
    )
    wire = _ExpressionDraftWire.__new__(_ExpressionDraftWire)
    wire._identity_frame = frame
    text = wire._identity_instruction()
    assert text.startswith("你是沈知栀")
    assert "俏皮一点" in text
    assert "说话软，有分寸" in text
    assert "搞一点抽象" not in text
    assert "都不是任务" not in text
    assert "真诚比漂亮话重要" in text
    assert "自然黑色中长发" in text
    assert "小书店" in text
    assert "你好呀，我是沈知栀" in text
    assert "文风样例" not in text
    assert "初次开口" in text
    assert "习惯（不是此刻正在做的事）" in text
    assert "日常：" not in text
    assert "tonight's report" in text
    assert "color this chat" in text
    assert "Chat prose does not silently become Fact" in text
    assert "invent a scene" not in text


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
    assert list(parsed)[-2] == "request"


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
    assert '"stable_self"' in prefix
    assert "昨天那杯茶" in prefix
    assert "current_trigger_message" not in prefix
    assert "event:a" not in prefix
    assert "event:b" not in prefix
    stable = json.dumps(
        {
            "expression_capabilities": first["expression_capabilities"],
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


def test_recent_dialogue_cache_split_preserves_semantics_and_stable_prefix() -> None:
    first_turn = {"dialogue_id": "d:1", "speaker": "counterpart", "text": "昨天那杯茶"}
    second_turn = {"dialogue_id": "d:2", "speaker": "companion", "text": "嗯"}
    third_turn = {"dialogue_id": "d:3", "speaker": "counterpart", "text": "在吗"}

    first_payload = order_user_present_payload(
        {
            "inner_life_snapshot": {
                "materials": {"recent_dialogue": [first_turn, second_turn]},
            }
        }
    )
    second_payload = order_user_present_payload(
        {
            "inner_life_snapshot": {
                "materials": {"recent_dialogue": [first_turn, second_turn, third_turn]},
            }
        }
    )
    first_dialogue = first_payload["inner_life_snapshot"]["materials"]["recent_dialogue"]
    second_dialogue = second_payload["inner_life_snapshot"]["materials"]["recent_dialogue"]
    assert isinstance(first_dialogue, dict)
    assert isinstance(second_dialogue, dict)
    assert recent_dialogue_material_entries(first_dialogue) == [first_turn, second_turn]
    assert recent_dialogue_material_entries(second_dialogue) == [
        first_turn,
        second_turn,
        third_turn,
    ]
    assert first_dialogue["volatile_last_turn"] == second_turn
    assert second_dialogue["volatile_last_turn"] == third_turn
    assert cache_stable_recent_dialogue([first_turn]) == [first_turn]
    assert cache_stable_recent_dialogue([first_turn]) == [first_turn]


def test_stable_dialogue_prefix_survives_a_decaying_attention_label() -> None:
    """The frozen half of recent_dialogue must not be rewritten between turns.

    ``ConversationContinuityCompiler`` re-derives ``acknowledged_context`` /
    ``current_turn`` / ``pending_interaction`` from scratch every turn, so an
    entry that was frozen in turn N loses labels in turn N+1.  Reproduced on the
    captured compact-gate runs: 11 of 11 frozen-entry mutations were exactly
    ``["acknowledged_context","recent"] -> ["recent"]``.  The labels belong to
    the volatile tail; the frozen bytes belong to the provider prefix cache.
    """

    def turn(ref: str, seq: int, speaker: str, text: str, reasons: list[str]) -> dict[str, object]:
        return {
            "dialogue_id": ref,
            "speaker": speaker,
            "speaker_ref": "agent:companion" if speaker == "companion" else "user:qq",
            "text": text,
            "occurred_at": "2026-09-07T06:00:00Z",
            "delivery_state": "delivered" if speaker == "companion" else "observed",
            "acknowledges_observation_event_refs": [],
            "continuity_reasons": reasons,
            "sequence": seq,
        }

    def presented(dialogue: list[dict[str, object]]) -> dict[str, object]:
        payload = order_user_present_payload(
            {"inner_life_snapshot": {"materials": {"recent_dialogue": dialogue}}}
        )
        view = payload["inner_life_snapshot"]["materials"]["recent_dialogue"]
        assert isinstance(view, dict)
        return view

    first = turn("d:c1", 100, "counterpart", "在忙吗", ["acknowledged_context", "recent"])
    reply = turn("d:m1", 200, "companion", "刚回来", ["recent", "recent_companion"])
    second = turn("d:c2", 300, "counterpart", "晚上吃什么", ["acknowledged_context", "recent"])
    reply2 = turn("d:m2", 400, "companion", "随便", ["recent", "recent_companion"])

    # Turn N: the newest counterpart line is the trigger.
    turn_n = [first, reply, second, reply2, turn("d:c3", 500, "counterpart", "?", ["current_turn", "recent"])]
    # Turn N+1: the same lines survive, but the attention labels decayed on the
    # entries that were already frozen, and a newer exchange arrived.
    decayed = [
        turn("d:c1", 100, "counterpart", "在忙吗", ["recent"]),
        reply,
        turn("d:c2", 300, "counterpart", "晚上吃什么", ["recent"]),
        reply2,
        turn("d:c3", 500, "counterpart", "?", ["acknowledged_context", "recent"]),
        turn("d:m3", 600, "companion", "在", ["recent", "recent_companion"]),
        turn("d:c4", 700, "counterpart", "在吗", ["current_turn", "recent"]),
    ]

    view_n = presented(turn_n)
    view_next = presented(decayed)
    stable_n = json.dumps(view_n["stable_turns"], ensure_ascii=False, separators=(",", ":"))
    stable_next = json.dumps(
        view_next["stable_turns"], ensure_ascii=False, separators=(",", ":")
    )

    # The frozen bytes are a byte prefix: the dialogue is append-only in the
    # serialized prompt, which is the only thing DeepSeek's cache can reuse.
    # Every frozen entry is identical, so the longer list continues exactly
    # where the shorter one closed.
    assert len(view_next["stable_turns"]) > len(view_n["stable_turns"])
    assert stable_next[: len(stable_n) - 1] == stable_n[:-1]
    # Without the split, the decayed label alone breaks that prefix at the head.
    naive_n = json.dumps(turn_n[:-1], ensure_ascii=False, separators=(",", ":"))
    naive_next = json.dumps(decayed[: len(turn_n) - 1], ensure_ascii=False, separators=(",", ":"))
    assert naive_next[: len(naive_n) - 1] != naive_n[:-1]
    # Nothing is lost: the withheld labels are republished once per turn, so the
    # packet signal moves with the attention, not with the frozen bytes.
    assert dialogue_attention_reasons(view_n) == {
        "d:c1": ["acknowledged_context"],
        "d:c2": ["acknowledged_context"],
    }
    assert dialogue_attention_reasons(view_next) == {"d:c3": ["acknowledged_context"]}
    # d:c1 lost its acknowledged_context label at the source in turn N+1, and
    # d:c3 picked one up; neither rewrites a frozen entry.
    assert view_n["stable_turns"][0]["continuity_reasons"] == ["recent"]
    assert view_next["stable_turns"][0] == view_n["stable_turns"][0]
    assert recent_dialogue_material_entries(view_next) == decayed
    # The newest turn keeps its labels inline: the system contract tells her
    # that current_turn + pending_interaction form the current packet.
    assert view_next["volatile_last_turn"] == decayed[-1]


def test_appraisal_cache_split_preserves_semantics_and_stable_prefix() -> None:
    first_row = ["appraisal:1", 8000, "2026-08-20T12:00:00+08:00", None, [["第一条"]]]
    second_row = ["appraisal:2", 7000, "2026-08-20T12:05:00+08:00", None, [["第二条"]]]
    third_row = ["appraisal:3", 6000, "2026-08-20T12:10:00+08:00", None, [["第三条"]]]
    table = {"columns": ["ref", "conf", "since", "until", "readings"], "rows": [first_row, second_row]}
    grown = {"columns": ["ref", "conf", "since", "until", "readings"], "rows": [first_row, second_row, third_row]}

    first_payload = order_user_present_payload(
        {"inner_life_snapshot": {"materials": {"appraisals": table}}}
    )
    second_payload = order_user_present_payload(
        {"inner_life_snapshot": {"materials": {"appraisals": grown}}}
    )
    first_view = first_payload["inner_life_snapshot"]["materials"]["appraisals"]
    second_view = second_payload["inner_life_snapshot"]["materials"]["appraisals"]
    assert isinstance(first_view, dict)
    assert isinstance(second_view, dict)
    assert appraisal_material_rows(first_view) == [first_row, second_row]
    assert appraisal_material_rows(second_view) == [first_row, second_row, third_row]
    assert first_view["stable_rows"] == [first_row]
    assert first_view["volatile_last_row"] == second_row
    assert second_view["volatile_last_row"] == third_row
    assert appraisal_meanings(first_view) == ["第一条", "第二条"]
    assert appraisal_meanings(second_view) == ["第一条", "第二条", "第三条"]
    assert second_view["stable_rows"][0] == first_view["stable_rows"][0]

    same_table_payload_a = order_user_present_payload(
        {
            "expression_hard_boundaries": {"alias_epoch": "a"},
            "inner_life_snapshot": {"materials": {"appraisals": table}},
        }
    )
    same_table_payload_b = order_user_present_payload(
        {
            "expression_hard_boundaries": {"alias_epoch": "b"},
            "inner_life_snapshot": {"materials": {"appraisals": table}},
        }
    )
    same_a = json.dumps(
        same_table_payload_a["inner_life_snapshot"]["materials"]["appraisals"],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    same_b = json.dumps(
        same_table_payload_b["inner_life_snapshot"]["materials"]["appraisals"],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert same_a == same_b


def test_affect_cache_split_preserves_semantics_and_stable_prefix() -> None:
    first = {"source_ref": "affect:1", "components": [{"dimension": "warmth", "intensity_bp": 3000}]}
    second = {"source_ref": "affect:2", "components": [{"dimension": "sadness", "intensity_bp": 2000}]}
    third = {"source_ref": "affect:3", "components": [{"dimension": "hurt", "intensity_bp": 2500}]}

    first_payload = order_user_present_payload(
        {"inner_life_snapshot": {"materials": {"affect": [first, second]}}}
    )
    second_payload = order_user_present_payload(
        {"inner_life_snapshot": {"materials": {"affect": [first, second, third]}}}
    )
    first_view = first_payload["inner_life_snapshot"]["materials"]["affect"]
    second_view = second_payload["inner_life_snapshot"]["materials"]["affect"]
    assert affect_material_entries(first_view) == [first, second]
    assert affect_material_entries(second_view) == [first, second, third]
    assert first_view["stable_entries"] == [first]
    assert first_view["volatile_last_entry"] == second
    assert second_view["volatile_last_entry"] == third


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


def test_over_budget_dialogue_folds_without_dropping_ids() -> None:
    items = [
        {
            "item_ref": f"dialogue:{index}",
            "source_ref": f"dialogue:{index}",
            "privacy_class": "private",
            "value": {
                "dialogue_id": f"dialogue:{index}",
                "speaker": "counterpart" if index % 2 == 0 else "companion",
                "text": f"bubble {index} " + ("雅思报名细节 " * 120),
                "occurred_at": f"2026-08-13T12:{index:02d}:00+08:00",
                "delivery_state": "observed",
                "sequence": (index + 1) * 100,
            },
        }
        for index in range(40)
    ]
    snapshot = compile_inner_life_snapshot(
        {
            "world_id": "world:present",
            "actor_ref": "agent:companion",
            "world_revision": 40,
            "deliberation_revision": 1,
            "ledger_sequence": 40,
            "slices": {"recent_dialogue": {"availability": "available", "items": items}},
        }
    ).model_view()
    folded = snapshot["materials"]["folded_dialogue"]
    recent = snapshot["materials"]["recent_dialogue"]
    ids = [dialogue_id for chunk in folded for dialogue_id in chunk["dialogue_ids"]]
    ids.extend(item["dialogue_id"] for item in recent)
    assert ids == [f"dialogue:{index}" for index in range(40)]
    assert folded
    first_fold = json.dumps(folded[0], ensure_ascii=False, separators=(",", ":"))
    items.append(
        {
            "item_ref": "dialogue:40",
            "source_ref": "dialogue:40",
            "privacy_class": "private",
            "value": {
                "dialogue_id": "dialogue:40",
                "speaker": "counterpart",
                "text": "bubble 40 " + ("雅思报名细节 " * 120),
                "occurred_at": "2026-08-13T13:00:00+08:00",
                "delivery_state": "observed",
                "sequence": 4100,
            },
        }
    )
    again = compile_inner_life_snapshot(
        {
            "world_id": "world:present",
            "actor_ref": "agent:companion",
            "world_revision": 41,
            "deliberation_revision": 1,
            "ledger_sequence": 41,
            "slices": {"recent_dialogue": {"availability": "available", "items": items}},
        }
    ).model_view()
    assert (
        json.dumps(again["materials"]["folded_dialogue"][0], ensure_ascii=False, separators=(",", ":"))
        == first_fold
    )


def test_week_diary_keeps_seven_local_days_and_caps_lines() -> None:
    items = []
    for day in range(8):
        for line in range(4):
            stamp = f"2026-08-{9 + day:02d}T12:0{line}:00+08:00"
            items.append(
                {
                    "item_ref": f"experience:{day}:{line}",
                    "source_ref": f"experience:{day}:{line}",
                    "privacy_class": "private",
                    "value": {
                        "experience_id": f"experience:{day}:{line}",
                        "values": {
                            "occurred_from": stamp,
                            "occurred_to": stamp,
                            "participant_refs": ["agent:companion"],
                            "privacy_class": "private",
                        },
                        "content": {"text": f"day{day}-line{line} 去了书店"},
                    },
                }
            )
    snapshot = compile_inner_life_snapshot(
        {
            "world_id": "world:present",
            "actor_ref": "agent:companion",
            "world_revision": 2,
            "deliberation_revision": 1,
            "ledger_sequence": 2,
            "logical_time": "2026-08-16T16:00:00+08:00",
            "slices": {
                "recent_experiences": {"availability": "available", "items": items}
            },
        }
    ).model_view()
    diary = snapshot["materials"]["week_diary"]
    assert [item["date"] for item in diary] == [
        "2026-08-10",
        "2026-08-11",
        "2026-08-12",
        "2026-08-13",
        "2026-08-14",
        "2026-08-15",
        "2026-08-16",
    ]
    assert "2026-08-09" not in {item["date"] for item in diary}
    assert all(len(item["lines"]) == 3 for item in diary)


def test_lived_moment_is_a_short_sourced_situation_not_a_lookup_panel() -> None:
    snapshot = compile_inner_life_snapshot(
        {
            "world_id": "world:present",
            "actor_ref": "agent:companion",
            "world_revision": 2,
            "deliberation_revision": 1,
            "ledger_sequence": 2,
            "logical_time": "2026-08-16T16:00:00+08:00",
            "slices": {
                "world_life": {
                    "availability": "available",
                    "items": [
                        {
                            "item_ref": "bio:1",
                            "source_ref": "bio:1",
                            "privacy_class": "private",
                            "value": {
                                "context_kind": "biographical_context",
                                "logical_at": "2026-08-16T16:00:00+08:00",
                                "age": 21,
                                "academic_phase": "term",
                                "academic_year": 3,
                                "season": "summer",
                            },
                        }
                    ],
                },
                "recent_experiences": {
                    "availability": "available",
                    "items": [
                        {
                            "item_ref": "experience:today",
                            "source_ref": "experience:today",
                            "privacy_class": "private",
                            "value": {
                                "experience_id": "experience:today",
                                "values": {
                                    "occurred_from": "2026-08-16T12:00:00+08:00",
                                    "occurred_to": "2026-08-16T12:00:00+08:00",
                                    "participant_refs": ["agent:companion"],
                                    "privacy_class": "private",
                                },
                                "content": {"text": "图书馆靠窗坐了一下午"},
                            },
                        }
                    ],
                },
            },
        }
    ).model_view()
    moment = snapshot["materials"]["lived_moment"]
    assert moment.startswith("今天已经过的：")
    assert "今天已经过的：图书馆靠窗坐了一下午" in moment
    assert "source_ref" not in moment
    assert "activity_kind" not in moment


def test_appraisal_keeps_original_observed_stimulus() -> None:
    snapshot = compile_inner_life_snapshot(
        {
            "world_id": "world:present",
            "actor_ref": "agent:companion",
            "world_revision": 2,
            "deliberation_revision": 1,
            "ledger_sequence": 2,
            "slices": {
                "recent_dialogue": {
                    "availability": "available",
                    "items": [
                        {
                            "item_ref": "dialogue:observation:obs-1",
                            "source_ref": "dialogue:observation:obs-1",
                            "privacy_class": "private",
                            "value": {
                                "dialogue_id": "dialogue:observation:obs-1",
                                "speaker": "counterpart",
                                "text": "今晚可能不去了",
                                "occurred_at": "2026-08-16T12:00:00+08:00",
                                "delivery_state": "observed",
                                "sequence": 100,
                                "source_claims": [
                                    {"authority_event_ref": "event:obs-1"}
                                ],
                            },
                        }
                    ],
                },
                "appraisals": {
                    "availability": "available",
                    "items": [
                        {
                            "item_ref": "appraisal:1",
                            "source_ref": "appraisal:1",
                            "privacy_class": "private",
                            "value": {
                                "subject_ref": "user:geoff",
                                "source_cluster_ref": "cluster:1",
                                "hypotheses": [
                                    {
                                        "hypothesis_id": "h1",
                                        "meaning": "他在往后推",
                                        "attribution": "user",
                                        "controllability": "uncontrollable",
                                        "severity": "moderate",
                                        "weight_bp": 10_000,
                                    }
                                ],
                                "evidence_refs": [
                                    {
                                        "ref_id": "event:obs-1",
                                        "evidence_type": "observed_message",
                                        "claim_purpose": "private_hypothesis",
                                    }
                                ],
                                "confidence_bp": 8_800,
                                "accepted_at": "2026-08-16T12:01:00+08:00",
                                "expires_at": "2026-08-17T12:01:00+08:00",
                            },
                        }
                    ],
                },
            },
        }
    ).model_view()
    appraisals = snapshot["materials"]["appraisals"]
    row = appraisals["rows"][0]
    assert row[5] == ["今晚可能不去了"]
    assert row[4][0][0] == "他在往后推"


def test_lived_moment_carries_today_and_an_unfinished_reading() -> None:
    snapshot = compile_inner_life_snapshot(
        {
            "world_id": "world:present",
            "actor_ref": "agent:companion",
            "world_revision": 2,
            "deliberation_revision": 1,
            "ledger_sequence": 2,
            "logical_time": "2026-08-16T16:00:00+08:00",
            "slices": {
                "recent_experiences": {
                    "availability": "available",
                    "items": [
                        {
                            "item_ref": "experience:today",
                            "source_ref": "experience:today",
                            "privacy_class": "private",
                            "value": {
                                "experience_id": "experience:today",
                                "values": {
                                    "occurred_from": "2026-08-16T12:00:00+08:00",
                                    "occurred_to": "2026-08-16T12:30:00+08:00",
                                    "participant_refs": ["agent:companion"],
                                    "privacy_class": "private",
                                },
                                "content": {"text": "图书馆靠窗坐了一上午，天阴得厉害"},
                            },
                        }
                    ],
                },
                "recent_dialogue": {
                    "availability": "available",
                    "items": [
                        {
                            "item_ref": "dialogue:observation:obs-1",
                            "source_ref": "dialogue:observation:obs-1",
                            "privacy_class": "private",
                            "value": {
                                "dialogue_id": "dialogue:observation:obs-1",
                                "speaker": "counterpart",
                                "text": "今晚可能不去了",
                                "occurred_at": "2026-08-16T12:00:00+08:00",
                                "delivery_state": "observed",
                                "sequence": 100,
                                "source_claims": [
                                    {"authority_event_ref": "event:obs-1"}
                                ],
                            },
                        }
                    ],
                },
                "appraisals": {
                    "availability": "available",
                    "items": [
                        {
                            "item_ref": "appraisal:1",
                            "source_ref": "appraisal:1",
                            "privacy_class": "private",
                            "value": {
                                "subject_ref": "user:geoff",
                                "source_cluster_ref": "cluster:1",
                                "hypotheses": [
                                    {
                                        "hypothesis_id": "h1",
                                        "meaning": "他在往后推",
                                        "attribution": "user",
                                        "controllability": "uncontrollable",
                                        "severity": "moderate",
                                        "weight_bp": 10_000,
                                    }
                                ],
                                "evidence_refs": [
                                    {
                                        "ref_id": "event:obs-1",
                                        "evidence_type": "observed_message",
                                        "claim_purpose": "private_hypothesis",
                                    }
                                ],
                                "confidence_bp": 8_800,
                                "accepted_at": "2026-08-16T12:01:00+08:00",
                                "expires_at": "2026-08-17T12:01:00+08:00",
                            },
                        }
                    ],
                },
            },
        }
    ).model_view()
    moment = snapshot["materials"]["lived_moment"]
    assert "今天已经过的" in moment
    assert "图书馆靠窗坐了一上午" in moment
    assert "还挂着" in moment
    assert "今晚可能不去了" in moment
    assert "他在往后推" in moment
    assert "warmth" not in moment


def test_lived_moment_carries_an_active_private_impression() -> None:
    snapshot = compile_inner_life_snapshot(
        {
            "world_id": "world:present",
            "actor_ref": "agent:companion",
            "world_revision": 2,
            "deliberation_revision": 1,
            "ledger_sequence": 2,
            "logical_time": "2026-08-16T16:00:00+08:00",
            "slices": {
                "private_impressions": {
                    "availability": "available",
                    "items": [
                        {
                            "item_ref": "impression:1",
                            "source_ref": "impression:1",
                            "privacy_class": "private",
                            "value": {
                                "subject_ref": "user:geoff",
                                "reflection_summary": "他说完我就一直在想他到底怎么看我",
                                "confidence_bp": 6_000,
                                "status": "active",
                            },
                        }
                    ],
                },
            },
        }
    ).model_view()
    moment = snapshot["materials"]["lived_moment"]
    assert "心里还搁着：他说完我就一直在想他到底怎么看我" in moment
    assert "warmth" not in moment


def _common_prefix(left: str, right: str) -> str:
    index = 0
    bound = min(len(left), len(right))
    while index < bound and left[index] == right[index]:
        index += 1
    return left[:index]


# ---------------------------------------------------------------------------
# Presentation-only prompt trims
# (evidence: output/release-readiness/APPENDIX-A-volume.md)
# ---------------------------------------------------------------------------


def _dialogue_turn(
    ref: str,
    sequence: int,
    speaker: str,
    text: str,
    *,
    ack: list[str] | None = None,
    reasons: list[str] | None = None,
) -> dict[str, object]:
    return {
        "dialogue_id": ref,
        "source_ref": ref,
        "speaker": speaker,
        "speaker_ref": "agent:companion" if speaker == "companion" else "user:qq",
        "text": text,
        "occurred_at": "2026-09-07T06:00:00Z",
        "delivery_state": "delivered" if speaker == "companion" else "observed",
        "acknowledges_observation_event_refs": list(ack or ()),
        "continuity_reasons": list(reasons or ["recent"]),
        "sequence": sequence,
    }


def _presented_dialogue(
    dialogue: list[dict[str, object]], *, aliases: dict[str, str] | None = None
) -> dict[str, object]:
    payload = order_user_present_payload(
        {
            "expression_hard_boundaries": {"source_ref_aliases": dict(aliases or {})},
            "inner_life_snapshot": {"materials": {"recent_dialogue": dialogue}},
        }
    )
    view = payload["inner_life_snapshot"]["materials"]["recent_dialogue"]
    assert isinstance(view, dict)
    return view


def test_duplicate_dialogue_source_ref_is_withheld_and_restored_exactly() -> None:
    """The material spells the same opaque ref twice; the view spells it once.

    ``snapshot_compiler._state_entry`` always copies the capsule item's
    ``source_ref`` onto the material entry, and for every non-media dialogue
    item that is byte-identical to ``dialogue_id`` (396/396 entries across the
    four captured compact-gate runs).  The duplicate is withheld from the whole
    block and declared, so expanding the view rebuilds the canonical entries
    exactly.  The host readers never used this copy: ``inbound_wire`` reads
    ``context["slices"]["recent_dialogue"]["items"]`` and
    ``conversation_continuity`` / ``recall_corpus`` read the
    ``RecentDialogueItem`` records.
    """

    ref_one = "dialogue:observation:observation:qq:qq-coalesced:" + "a" * 64
    ref_two = "dialogue:observation:observation:qq:qq-coalesced:" + "b" * 64
    canonical = [
        _dialogue_turn(ref_one, 100, "counterpart", "在忙吗"),
        _dialogue_turn(ref_two, 200, "companion", "刚回来"),
    ]

    view = _presented_dialogue(canonical)

    assert view["source_ref_is_dialogue_id"] is True
    assert all("source_ref" not in entry for entry in view["stable_turns"])
    assert "source_ref" not in view["volatile_last_turn"]
    # Nothing is lost: the canonical entries come back byte for byte.
    assert recent_dialogue_material_entries(view) == canonical
    assert expand_present_world_context(
        {"inner_life_snapshot": {"materials": {"recent_dialogue": view}}}
    )["inner_life_snapshot"]["materials"]["recent_dialogue"] == canonical


def test_dialogue_source_ref_is_kept_when_it_is_not_the_dialogue_id() -> None:
    """A media-delivery entry cites a *different* ref; the whole block is kept.

    The elision is all-or-nothing on purpose: withholding one entry's
    ``source_ref`` and keeping another's would make expansion guess which
    entries ever had the field.
    """

    plain = _dialogue_turn("dialogue:observation:x", 100, "counterpart", "在忙吗")
    delivery = _dialogue_turn("dialogue:media-delivery:photo:1", 200, "companion", "给你看")
    delivery["source_ref"] = "photo:1"

    view = _presented_dialogue([plain, delivery])

    assert "source_ref_is_dialogue_id" not in view
    assert view["stable_turns"][0]["source_ref"] == "dialogue:observation:x"
    assert view["volatile_last_turn"]["source_ref"] == "photo:1"
    assert recent_dialogue_material_entries(view) == [plain, delivery]


def test_volatile_dialogue_turn_uses_aliases_and_the_frozen_half_does_not() -> None:
    """Only the uncached tail of the dialogue may carry a short alias.

    ``source_ref_aliases`` is renumbered from scratch every turn -- of the
    dialogue ids carried from one captured turn to the next, 45/48, 61/89,
    62/76 and 66/77 got a different alias on the following turn.  Substituting
    an alias into ``stable_turns`` would therefore rewrite the frozen bytes and
    move the first differing byte from the end of that window back into its
    head, which is exactly the cache loss the frozen/volatile split exists to
    prevent.  The volatile last turn is past that boundary, so there the
    substitution is free -- and the canonical ref is still recoverable.
    """

    ref_one = "dialogue:observation:observation:qq:qq-coalesced:" + "a" * 64
    ref_two = "dialogue:observation:observation:qq:qq-coalesced:" + "b" * 64
    acknowledged = "event:trigger:observation:platform:qq:qq-coalesced:" + "c" * 64
    canonical = [
        _dialogue_turn(ref_one, 100, "counterpart", "在忙吗"),
        _dialogue_turn(ref_two, 200, "companion", "刚回来", ack=[acknowledged]),
    ]
    aliases = {"S7": ref_one, "S9": ref_two, "T1": acknowledged}

    view = _presented_dialogue(canonical, aliases=aliases)

    # The frozen entry keeps canonical refs, so turn N+1 with a renumbered
    # alias table leaves its bytes alone.
    assert view["stable_turns"][0]["dialogue_id"] == ref_one
    renumbered = _presented_dialogue(canonical, aliases={"S1": ref_one, "S2": ref_two, "T4": acknowledged})
    assert json.dumps(view["stable_turns"], ensure_ascii=False) == json.dumps(
        renumbered["stable_turns"], ensure_ascii=False
    )
    # The volatile turn cites the aliases the same message already publishes.
    assert view["volatile_last_turn"]["dialogue_id"] == "S9"
    assert view["volatile_last_turn"]["acknowledges_observation_event_refs"] == ["T1"]
    assert "source_ref" not in view["volatile_last_turn"]
    # Expanding with that view's own alias table restores the canonical entry.
    assert recent_dialogue_material_entries(view, source_ref_aliases=aliases) == canonical
    assert expand_present_world_context(
        {
            "expression_hard_boundaries": {"source_ref_aliases": aliases},
            "inner_life_snapshot": {"materials": {"recent_dialogue": view}},
        }
    )["inner_life_snapshot"]["materials"]["recent_dialogue"] == canonical
    # Without an alias table the entry is honestly left with its alias, and the
    # withheld duplicate is still restored.
    without_table = recent_dialogue_material_entries(view)
    assert without_table[-1]["dialogue_id"] == "S9"
    assert without_table[-1]["source_ref"] == "S9"
    assert without_table[0] == canonical[0]


def test_faculty_wrapper_keys_are_left_alone_because_readers_use_them() -> None:
    """The eight facets keep ``availability`` / ``material_keys`` verbatim.

    ``contracts.InnerLifeSnapshot.model_view`` renders every facet as
    ``{"availability": ..., "material_keys": [...]}`` and the audit ranked those
    repeated wrapper keys as cuttable.  They are not: the *serialized* view is
    read by name outside the presenter, so collapsing an available facet to its
    bare key list is a shape change, not a size cut.  Measured while
    implementing it: four provider-driven tests died on
    ``snapshot["faculties"]["selective_memory"]["material_keys"]`` /
    ``["availability"]``, and ``faculties`` (which sorts before ``materials``)
    sits inside the cacheable prefix anyway, so the trim was worth about
    CNY 0.37/month at 3 000 turns.
    """

    faculties = {
        "appraisal_affect": {"availability": "available", "material_keys": ["appraisals", "affect"]},
        "emotional_continuity": {"availability": "unavailable", "material_keys": []},
    }
    payload = order_user_present_payload(
        {
            "inner_life_snapshot": {
                "faculties": faculties,
                "materials": {},
                "snapshot_compiler": {
                    "availability": "available",
                    "value": "inner-life-snapshot-compiler.21",
                },
                "truncation": {"availability": "unavailable"},
            }
        }
    )
    view = payload["inner_life_snapshot"]

    assert view["faculties"] == faculties
    assert view["faculties"]["appraisal_affect"]["material_keys"] == ["appraisals", "affect"]
    # The scope/compiler bindings are only read host-side, so the constant
    # ``"availability": "available"`` wrapper there is elided and restored.
    assert view["snapshot_compiler"] == {"value": "inner-life-snapshot-compiler.21"}
    assert view["truncation"] == {"availability": "unavailable"}

    expanded = expand_present_world_context(payload)["inner_life_snapshot"]
    assert expanded["faculties"] == faculties
    assert expanded["snapshot_compiler"] == {
        "availability": "available",
        "value": "inner-life-snapshot-compiler.21",
    }
    assert expanded["truncation"] == {"availability": "unavailable"}


def test_automatic_prefetch_wrapper_is_elided_and_capability_evidence_is_removed() -> None:
    """Her remembered interpretations keep their text, citation and recency.

    The five withheld members hold one value in all 58 captured items.  What
    stays is what she judges with: ``text``, the ``source_ref`` she would cite,
    ``occurred_from``, and the ``privacy_class`` boundary.  ``capability_evidence``
    is removed outright -- ``production._bind_capability_evidence`` is its only
    writer and no module in ``src/`` reads it back -- but its refs stay visible:
    the writer appends them to ``source_refs``, which is where the visible
    source closure looks.
    """

    item = {
        "authority": "defeasible_interpretation",
        "epistemic_scope": "private_interpretation",
        "memory_kind": "reflective",
        "occurred_from": "2026-09-07T06:04:00Z",
        "occurred_to": None,
        "privacy_class": "withhold",
        "source_ref": "appraisal:compiled:" + "d" * 64,
        "source_slice": "recalled_emotional_associations",
        "text": "Remembered private interpretation — 他绕了半天没直说",
    }
    evidence_ref = "event:trigger:observation:platform:qq:qq-coalesced:" + "e" * 64
    snapshot = {
        "materials": {
            "automatic_prefetch": {"items": [item]},
            "capability_evidence": [
                {
                    "event_type": "ObservationRecorded",
                    "source_ref": evidence_ref,
                    "source_world_revision": 327,
                }
            ],
        },
        "source_refs": [evidence_ref],
    }
    frozen = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)

    payload = order_user_present_payload({"inner_life_snapshot": snapshot})
    view = payload["inner_life_snapshot"]

    assert view["materials"]["automatic_prefetch"]["items"] == [
        {
            "occurred_from": "2026-09-07T06:04:00Z",
            "privacy_class": "withhold",
            "source_ref": "appraisal:compiled:" + "d" * 64,
            "text": "Remembered private interpretation — 他绕了半天没直说",
        }
    ]
    assert "capability_evidence" not in view["materials"]
    assert evidence_ref in view["source_refs"]
    # Presentation only: the snapshot the host handed in is untouched.
    assert json.dumps(snapshot, ensure_ascii=False, sort_keys=True) == frozen

    expanded = expand_present_world_context(payload)["inner_life_snapshot"]["materials"]
    assert expanded["automatic_prefetch"] == {"items": [item]}


def test_duplicate_trigger_message_becomes_a_pointer_to_the_one_copy() -> None:
    """``request.trigger_message`` is the same dump as ``current_trigger_message``.

    Both come from ``request.trigger_message.model_dump(mode="json")``
    (``inbound_wire`` writes one at :10460 and one at :10529), so the model
    read 638 characters of trigger text twice.  Every reader of the trigger
    reads the frozen request object, not this JSON.  A trigger that is not
    identical is kept verbatim.
    """

    trigger = {
        "event_ref": "event:trigger:" + "f" * 64,
        "observation_ref": "observation:qq:" + "f" * 64,
        "source_world_revision": 327,
        "text": "我有点想你了",
    }
    payload = order_user_present_payload(
        {
            "request": {
                "call_id": "call:1",
                "trigger_message": json.loads(json.dumps(trigger)),
            },
            "current_trigger_message": json.loads(json.dumps(trigger)),
        }
    )

    assert payload["request"]["trigger_message"] == "see current_trigger_message"
    assert expand_present_world_context(payload)["request"]["trigger_message"] == trigger
    assert payload["current_trigger_message"] == trigger

    different = order_user_present_payload(
        {
            "request": {"trigger_message": {"text": "旧的"}},
            "current_trigger_message": json.loads(json.dumps(trigger)),
        }
    )
    assert different["request"]["trigger_message"] == {"text": "旧的"}


def test_presenting_twice_is_presenting_once() -> None:
    """Every trim is idempotent, so a re-presented view keeps its round trip."""

    ref = "dialogue:observation:x"
    canonical = [
        _dialogue_turn(ref, 100, "counterpart", "在忙吗"),
        _dialogue_turn("dialogue:observation:y", 200, "companion", "刚回来"),
    ]
    snapshot = {
        "faculties": {"appraisal_affect": {"availability": "available", "material_keys": ["appraisals"]}},
        "materials": {
            "recent_dialogue": canonical,
            "automatic_prefetch": {
                "items": [
                    {
                        "authority": "defeasible_interpretation",
                        "memory_kind": "reflective",
                        "occurred_from": "2026-09-07T06:04:00Z",
                        "privacy_class": "withhold",
                        "source_ref": "appraisal:1",
                        "source_slice": "recalled_emotional_associations",
                        "text": "想起他绕了半天",
                    }
                ]
            },
        },
    }
    once = order_user_present_payload({"inner_life_snapshot": snapshot})
    twice = order_user_present_payload(json.loads(json.dumps(once)))

    assert json.dumps(once, ensure_ascii=False, sort_keys=True) == json.dumps(
        twice, ensure_ascii=False, sort_keys=True
    )
    assert recent_dialogue_material_entries(
        twice["inner_life_snapshot"]["materials"]["recent_dialogue"]
    ) == canonical


def test_shipped_identity_does_not_license_unwritten_autobiography():
    from pathlib import Path
    from companion_daemon.character import load_character

    profile = load_character(str(Path(__file__).resolve().parents[2] / "configs/character.yaml"))
    wire = _ExpressionDraftWire.__new__(_ExpressionDraftWire)
    wire._identity_frame = CompanionIdentityFrame(
        companion_name=profile.name, counterpart_name="用户", base_prompt=profile.base_prompt,
        background=profile.background, boundaries=tuple(profile.boundaries),
    )
    text = wire._identity_instruction()
    assert "未写入的色彩可以出现在聊天里" not in text
    assert "fuzzy private memory" not in text
    assert "uncertainty does not supply missing events" in text
    assert "do not need external proof" in text
