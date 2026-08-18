from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib

from companion_daemon.world_v2.conversation_continuity import (
    ContinuityRetrievalCandidate,
    ConversationContinuityCompiler,
    pack_recent_dialogue_under_source_budget,
)
from companion_daemon.world_v2.recent_dialogue import DialogueSourceClaim, RecentDialogueItem


NOW = datetime(2026, 7, 25, 17, 8, tzinfo=UTC)


def _dialogue(
    suffix: str,
    text: str,
    *,
    speaker: str,
    at: datetime,
    sequence: int,
    acknowledges: tuple[str, ...] = (),
) -> RecentDialogueItem:
    ref = f"event:dialogue:{suffix}"
    return RecentDialogueItem(
        dialogue_id=f"dialogue:{suffix}",
        speaker=speaker,
        text=text,
        occurred_at=at,
        delivery_state="observed" if speaker == "counterpart" else "delivered",
        sequence=sequence,
        source_claims=(
            DialogueSourceClaim(
                authority_event_ref=ref,
                authority_world_revision=sequence,
                authority_payload_hash=hashlib.sha256(ref.encode()).hexdigest(),
            ),
        ),
        acknowledges_observation_event_refs=acknowledges,
    )


def test_compile_separates_pending_interaction_from_replied_history_and_memory() -> None:
    first = _dialogue(
        "first",
        "从深圳回来啦",
        speaker="counterpart",
        at=NOW - timedelta(minutes=4),
        sequence=1,
    )
    reply = _dialogue(
        "reply",
        "回来啦！深圳怎么样？",
        speaker="companion",
        at=NOW - timedelta(minutes=3),
        sequence=2,
        acknowledges=("event:dialogue:first",),
    )
    pending = _dialogue(
        "pending",
        "深圳说实话不是很好玩哈哈哈哈",
        speaker="counterpart",
        at=NOW - timedelta(minutes=1),
        sequence=3,
    )
    current = _dialogue(
        "current",
        "👀",
        speaker="counterpart",
        at=NOW,
        sequence=4,
    )

    result = ConversationContinuityCompiler().compile(
        dialogue=(first, reply, pending, current),
        trigger_ref="event:dialogue:current",
        retrieval_candidates=(
            ContinuityRetrievalCandidate(
                slice_name="active_memory_candidates",
                item_ref="memory:shenzhen-trip",
                texts=("上午说过深圳旅行，回来以后觉得不太好玩。",),
            ),
            ContinuityRetrievalCandidate(
                slice_name="active_memory_candidates",
                item_ref="memory:coffee",
                texts=("用户平时喜欢喝手冲咖啡。",),
            ),
        ),
    )

    by_id = {item.dialogue_id: item for item in result.dialogue}
    assert "pending_interaction" in by_id[pending.dialogue_id].continuity_reasons
    assert "pending_interaction" not in by_id[first.dialogue_id].continuity_reasons
    # Retrieval candidates are already source-bound Context.  Lexical overlap
    # must not secretly promote one of them into the model's working set.
    assert result.rank_overrides == frozenset()


def test_acknowledged_context_reaches_beyond_the_last_two_companion_beats() -> None:
    explained = _dialogue(
        "explained",
        "是个太阳的表情来着，表示我今天心情不错",
        speaker="counterpart",
        at=NOW - timedelta(minutes=10),
        sequence=1,
    )
    reply_explained = _dialogue(
        "reply-explained",
        "太阳啊 那挺好",
        speaker="companion",
        at=NOW - timedelta(minutes=9),
        sequence=2,
        acknowledges=("event:dialogue:explained",),
    )
    photo = _dialogue(
        "photo",
        "好！",
        speaker="counterpart",
        at=NOW - timedelta(minutes=5),
        sequence=5,
    )
    reply_photo = _dialogue(
        "reply-photo",
        "嗯 那我发你",
        speaker="companion",
        at=NOW - timedelta(minutes=4),
        sequence=6,
        acknowledges=("event:dialogue:photo",),
    )
    later = tuple(
        _dialogue(
            f"later-{index}",
            f"later {index}",
            speaker="companion",
            at=NOW - timedelta(minutes=3, seconds=index),
            sequence=7 + index,
        )
        for index in range(5)
    )
    current = _dialogue(
        "current",
        "☀️ 太阳",
        speaker="counterpart",
        at=NOW,
        sequence=20,
    )
    result = ConversationContinuityCompiler().compile(
        dialogue=(explained, reply_explained, photo, reply_photo, *later, current),
        trigger_ref="event:dialogue:current",
    )
    by_id = {item.dialogue_id: item for item in result.dialogue}
    assert "current_turn" in by_id[current.dialogue_id].continuity_reasons
    assert "acknowledged_context" in by_id[explained.dialogue_id].continuity_reasons
    assert "acknowledged_context" in by_id[photo.dialogue_id].continuity_reasons
    packed = pack_recent_dialogue_under_source_budget(result.dialogue)
    packed_ids = {item.dialogue_id for item in packed}
    assert current.dialogue_id in packed_ids
    assert explained.dialogue_id in packed_ids
    assert photo.dialogue_id in packed_ids
    assert reply_explained.dialogue_id in packed_ids


def test_selected_dialogue_is_returned_in_causal_sequence_when_timestamps_tie() -> None:
    """Fast/burst turns must not be regrouped by speaker.

    QQ observations are second-granularity while delivery receipts can carry
    sub-second timestamps.  Ledger order is therefore the authoritative
    conversational chronology when several turns share one wall-clock second.
    """

    first = _dialogue(
        "first",
        "第一句用户消息",
        speaker="counterpart",
        at=NOW,
        sequence=100,
    )
    first_reply = _dialogue(
        "first-reply",
        "第一句角色回复",
        speaker="companion",
        at=NOW,
        sequence=201,
        acknowledges=("event:dialogue:first",),
    )
    second = _dialogue(
        "second",
        "第二句用户消息",
        speaker="counterpart",
        at=NOW,
        sequence=300,
    )
    second_reply = _dialogue(
        "second-reply",
        "第二句角色回复",
        speaker="companion",
        at=NOW,
        sequence=401,
        acknowledges=("event:dialogue:second",),
    )
    current = _dialogue(
        "current",
        "第三句用户消息",
        speaker="counterpart",
        at=NOW,
        sequence=500,
    )

    result = ConversationContinuityCompiler().compile(
        dialogue=(second_reply, first, current, first_reply, second),
        trigger_ref="event:dialogue:current",
    )

    assert [item.dialogue_id for item in result.dialogue] == [
        first.dialogue_id,
        first_reply.dialogue_id,
        second.dialogue_id,
        second_reply.dialogue_id,
        current.dialogue_id,
    ]


def test_current_cue_prefetches_only_the_source_bound_associative_memory() -> None:
    earlier = _dialogue(
        "earlier",
        "我最近开始很喜欢喝乌龙茶。",
        speaker="counterpart",
        at=NOW - timedelta(hours=5),
        sequence=1,
    )
    current = _dialogue(
        "current",
        "你还记得我之前说过喜欢乌龙茶吗？",
        speaker="counterpart",
        at=NOW,
        sequence=2,
    )
    candidates = (
        ContinuityRetrievalCandidate(
            slice_name="active_memory_candidates",
            item_ref="memory:oolong",
            texts=("用户最近开始喜欢喝乌龙茶。",),
        ),
        ContinuityRetrievalCandidate(
            slice_name="active_memory_candidates",
            item_ref="memory:coffee",
            texts=("用户平时喜欢喝手冲咖啡。",),
        ),
    )
    compiler = ConversationContinuityCompiler()

    first = compiler.compile(
        dialogue=(earlier, current),
        trigger_ref="event:dialogue:current",
        retrieval_candidates=candidates,
    )
    replayed = compiler.compile(
        dialogue=(earlier, current),
        trigger_ref="event:dialogue:current",
        retrieval_candidates=tuple(reversed(candidates)),
    )

    expected = frozenset({("active_memory_candidates", "memory:oolong")})
    assert first.rank_overrides == expected
    assert replayed.rank_overrides == expected


def test_one_generic_two_character_overlap_does_not_prefetch_a_memory() -> None:
    current = _dialogue(
        "current",
        "今天有点忙。",
        speaker="counterpart",
        at=NOW,
        sequence=1,
    )

    result = ConversationContinuityCompiler().compile(
        dialogue=(current,),
        trigger_ref="event:dialogue:current",
        retrieval_candidates=(
            ContinuityRetrievalCandidate(
                slice_name="active_memory_candidates",
                item_ref="memory:coffee",
                texts=("今天去喝咖啡。",),
            ),
        ),
    )

    assert result.rank_overrides == frozenset()


def test_delayed_reply_does_not_acknowledge_a_newer_stuck_message() -> None:
    replied = _dialogue(
        "replied",
        "上午那件事我处理好了。",
        speaker="counterpart",
        at=NOW - timedelta(minutes=4),
        sequence=1,
    )
    stuck = _dialogue(
        "stuck",
        "第三条你是不是没看到？",
        speaker="counterpart",
        at=NOW - timedelta(minutes=2),
        sequence=2,
    )
    delayed_reply = _dialogue(
        "delayed-reply",
        "看到了，上午那件事辛苦了。",
        speaker="companion",
        at=NOW - timedelta(minutes=1),
        sequence=3,
        acknowledges=("event:dialogue:replied",),
    )
    emoji = _dialogue(
        "emoji",
        "👀",
        speaker="counterpart",
        at=NOW,
        sequence=4,
    )

    result = ConversationContinuityCompiler().compile(
        dialogue=(replied, stuck, delayed_reply, emoji),
        trigger_ref="event:dialogue:emoji",
    )

    by_id = {item.dialogue_id: item for item in result.dialogue}
    assert "pending_interaction" in by_id[stuck.dialogue_id].continuity_reasons
    assert "pending_interaction" not in by_id[replied.dialogue_id].continuity_reasons


def test_common_time_word_alone_does_not_reactivate_an_unrelated_topic() -> None:
    coffee = _dialogue(
        "coffee",
        "今天去喝咖啡。",
        speaker="counterpart",
        at=NOW - timedelta(hours=3),
        sequence=1,
    )
    current = _dialogue(
        "busy",
        "今天有点忙。",
        speaker="counterpart",
        at=NOW,
        sequence=2,
    )

    result = ConversationContinuityCompiler().compile(
        dialogue=(coffee, current),
        trigger_ref="event:dialogue:busy",
    )

    by_id = {item.dialogue_id: item for item in result.dialogue}
    assert "topic_reactivation" not in by_id[coffee.dialogue_id].continuity_reasons


def test_clarification_keeps_user_context_behind_recent_companion_questions() -> None:
    dashboard = _dialogue(
        "dashboard",
        "都是些工作上的事情，需要一些看板工具。",
        speaker="counterpart",
        at=NOW - timedelta(minutes=4),
        sequence=1,
    )
    tool_question = _dialogue(
        "tool-question",
        "你平时用什么？Trello 还是 Notion 那种？",
        speaker="companion",
        at=NOW - timedelta(minutes=3),
        sequence=2,
        acknowledges=("event:dialogue:dashboard",),
    )
    assignment = _dialogue(
        "assignment",
        "然后就问我能不能做。",
        speaker="counterpart",
        at=NOW - timedelta(minutes=2),
        sequence=3,
    )
    accept_question = _dialogue(
        "accept-question",
        "你妈又给你派活了？那你打算接吗？",
        speaker="companion",
        at=NOW - timedelta(minutes=1),
        sequence=4,
        acknowledges=("event:dialogue:assignment",),
    )
    current = _dialogue(
        "current-tool",
        "是飞书那种啦，用的 OpenClaw 接入的。",
        speaker="counterpart",
        at=NOW,
        sequence=5,
    )

    result = ConversationContinuityCompiler(
        max_items=4,
        max_companion_items=2,
    ).compile(
        dialogue=(dashboard, tool_question, assignment, accept_question, current),
        trigger_ref="event:dialogue:current-tool",
    )

    retained = {item.dialogue_id for item in result.dialogue}
    assert dashboard.dialogue_id in retained


def _beat(
    suffix: str,
    text: str,
    *,
    speaker: str,
    at: datetime,
    sequence: int,
    claim_count: int = 1,
    acknowledges: tuple[str, ...] = (),
    reasons: tuple[str, ...] = (),
) -> RecentDialogueItem:
    claims = tuple(
        DialogueSourceClaim(
            authority_event_ref=f"event:dialogue:{suffix}:{index}",
            authority_world_revision=max(1, sequence),
            authority_payload_hash=hashlib.sha256(
                f"event:dialogue:{suffix}:{index}".encode()
            ).hexdigest(),
        )
        for index in range(claim_count)
    )
    return RecentDialogueItem(
        dialogue_id=f"dialogue:{suffix}",
        speaker=speaker,
        text=text,
        occurred_at=at,
        delivery_state="observed" if speaker == "counterpart" else "delivered",
        sequence=sequence,
        source_claims=claims,
        acknowledges_observation_event_refs=acknowledges,
        continuity_reasons=reasons,
    )


def test_twelve_turn_slice_keeps_her_last_line_and_does_not_stick_current() -> None:
    turns: list[RecentDialogueItem] = []
    sequence = 1
    for index in range(12):
        him_ref = f"event:dialogue:him-{index}:0"
        turns.append(
            _beat(
                f"him-{index}",
                f"他第 {index} 句",
                speaker="counterpart",
                at=NOW - timedelta(minutes=30 - index),
                sequence=sequence,
            )
        )
        sequence += 1
        turns.append(
            _beat(
                f"her-{index}",
                f"我第 {index} 句",
                speaker="companion",
                at=NOW - timedelta(minutes=30 - index, seconds=-20),
                sequence=sequence,
                claim_count=4,
                acknowledges=(him_ref,),
            )
        )
        sequence += 1
    current = _beat(
        "him-now",
        "还没睡呀",
        speaker="counterpart",
        at=NOW,
        sequence=sequence,
    )
    result = ConversationContinuityCompiler().compile(
        dialogue=(*turns, current),
        trigger_ref="event:dialogue:him-now:0",
    )
    packed = pack_recent_dialogue_under_source_budget(result.dialogue)
    texts = [item.text for item in packed]
    speakers = {item.speaker for item in packed}
    current_marks = [
        item.text for item in packed if "current_turn" in item.continuity_reasons
    ]
    ordered = sorted(packed, key=lambda item: (item.sequence, item.occurred_at))

    assert speakers == {"counterpart", "companion"}
    assert "我第 11 句" in texts
    assert "还没睡呀" in texts
    assert current_marks == ["还没睡呀"]
    assert [item.sequence for item in ordered] == sorted(item.sequence for item in packed)
    assert len(packed) > 7


def test_four_hour_old_reaction_is_not_current_after_newer_turns() -> None:
    sun = _beat(
        "old-sun",
        "☀️ 太阳",
        speaker="counterpart",
        at=NOW - timedelta(hours=4),
        sequence=1,
        reasons=("recent",),
    )
    later: list[RecentDialogueItem] = []
    sequence = 10
    for index in range(10):
        later.append(
            _beat(
                f"later-him-{index}",
                f"后来他 {index}",
                speaker="counterpart",
                at=NOW - timedelta(minutes=20 - index),
                sequence=sequence,
            )
        )
        sequence += 1
        later.append(
            _beat(
                f"later-her-{index}",
                f"后来我 {index}",
                speaker="companion",
                at=NOW - timedelta(minutes=20 - index, seconds=-15),
                sequence=sequence,
                claim_count=4,
            )
        )
        sequence += 1
    current = later[-2]
    result = ConversationContinuityCompiler().compile(
        dialogue=(sun, *later),
        trigger_ref=current.source_claims[0].authority_event_ref,
    )
    packed = pack_recent_dialogue_under_source_budget(result.dialogue)
    by_text = {item.text: item for item in packed}

    assert "☀️ 太阳" not in by_text
    current_marks = [
        item.text for item in packed if "current_turn" in item.continuity_reasons
    ]
    assert current_marks == [current.text]


def test_unacked_old_reaction_is_not_pending_after_a_live_counterpart_window() -> None:
    sun = _beat(
        "old-sun",
        "☀️ 太阳",
        speaker="counterpart",
        at=NOW - timedelta(hours=10),
        sequence=1,
    )
    later: list[RecentDialogueItem] = []
    sequence = 10
    for index in range(8):
        him_ref = f"event:dialogue:later-him-{index}:0"
        later.append(
            _beat(
                f"later-him-{index}",
                f"后来他 {index}",
                speaker="counterpart",
                at=NOW - timedelta(minutes=16 - index),
                sequence=sequence,
            )
        )
        sequence += 1
        later.append(
            _beat(
                f"later-her-{index}",
                f"后来我 {index}",
                speaker="companion",
                at=NOW - timedelta(minutes=16 - index, seconds=-10),
                sequence=sequence,
                claim_count=4,
                acknowledges=(him_ref,),
            )
        )
        sequence += 1
    current = _beat(
        "him-now",
        "困了就睡",
        speaker="counterpart",
        at=NOW,
        sequence=sequence,
    )
    result = ConversationContinuityCompiler().compile(
        dialogue=(sun, *later, current),
        trigger_ref="event:dialogue:him-now:0",
    )
    packed = pack_recent_dialogue_under_source_budget(result.dialogue)
    by_text = {item.text: item for item in packed}
    pending = [
        item.text
        for item in result.dialogue
        if "pending_interaction" in item.continuity_reasons
    ]

    assert "☀️ 太阳" not in by_text
    assert "☀️ 太阳" not in pending
    assert "困了就睡" in by_text


def test_conversation_view_follows_causal_order_not_rank_order() -> None:
    from companion_daemon.world_v2.character_interior.snapshot_compiler import (
        compile_inner_life_snapshot,
    )

    snapshot = compile_inner_life_snapshot(
        {
            "world_id": "world:conversation-order",
            "actor_ref": "agent:companion",
            "world_revision": 4,
            "deliberation_revision": 1,
            "ledger_sequence": 4,
            "logical_time": NOW.isoformat(),
            "slices": {
                "recent_dialogue": {
                    "availability": "available",
                    "items": [
                        {
                            "item_ref": "dialogue:her",
                            "source_ref": "event:dialogue:her",
                            "value": {
                                "dialogue_id": "dialogue:her",
                                "speaker": "companion",
                                "text": "晚点整理好了发你",
                                "occurred_at": (NOW - timedelta(minutes=1)).isoformat(),
                                "delivery_state": "delivered",
                                "sequence": 2,
                            },
                        },
                        {
                            "item_ref": "dialogue:him-old",
                            "source_ref": "event:dialogue:him-old",
                            "value": {
                                "dialogue_id": "dialogue:him-old",
                                "speaker": "counterpart",
                                "text": "☀️ 太阳",
                                "occurred_at": (NOW - timedelta(hours=4)).isoformat(),
                                "delivery_state": "observed",
                                "sequence": 1,
                            },
                        },
                        {
                            "item_ref": "dialogue:him-now",
                            "source_ref": "event:dialogue:him-now",
                            "value": {
                                "dialogue_id": "dialogue:him-now",
                                "speaker": "counterpart",
                                "text": "还没睡呀",
                                "occurred_at": NOW.isoformat(),
                                "delivery_state": "observed",
                                "sequence": 3,
                            },
                        },
                    ],
                }
            },
        }
    )
    conversation = snapshot.model_view()["materials"]["conversation"]
    assert isinstance(conversation, list)
    assert conversation[0].endswith("☀️ 太阳")
    assert conversation[1].endswith("晚点整理好了发你")
    assert conversation[2].endswith("还没睡呀")
