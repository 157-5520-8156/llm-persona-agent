"""Conversations are remembered as episodes, not as isolated lines."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from companion_daemon.world_v2.recall_corpus import (
    RecallCorpusCompiler,
    RecallCorpusSources,
    is_conversation_episode,
)
from companion_daemon.world_v2.recall_index import RecallCursor
from companion_daemon.world_v2.recent_dialogue import DialogueSourceClaim, RecentDialogueItem

ACTOR = "agent:companion"
USER = "user:geoff"
START = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


def _line(n: int, text: str, *, speaker: str, minutes: float) -> RecentDialogueItem:
    ref = f"event:dialogue:{n}"
    return RecentDialogueItem(
        dialogue_id=f"dialogue:{n}",
        speaker=speaker,
        text=text,
        occurred_at=START + timedelta(minutes=minutes),
        delivery_state="observed" if speaker == "counterpart" else "delivered",
        sequence=n,
        source_claims=(DialogueSourceClaim(
            authority_event_ref=ref,
            authority_world_revision=n,
            authority_payload_hash=hashlib.sha256(ref.encode()).hexdigest(),
        ),),
    )


def _compile(lines):
    return RecallCorpusCompiler().compile(
        cursor=RecallCursor(world_revision=100, deliberation_revision=100, ledger_sequence=100),
        actor_ref=ACTOR,
        subject_refs=tuple(sorted((ACTOR, USER))),
        sources=RecallCorpusSources(recent_dialogue=tuple(lines)),
    )


QIXI = [
    _line(1, "今天七夕，本来和朋友几个约好打游戏的", speaker="counterpart", minutes=0),
    _line(2, "结果朋友去陪女友去了，烦死了", speaker="counterpart", minutes=0.5),
    _line(3, "哈哈哈哈哈", speaker="companion", minutes=1),
    _line(4, "那你今晚打算干嘛？要不要找点别的乐子", speaker="companion", minutes=1.5),
    _line(5, "随便刷刷视频吧", speaker="counterpart", minutes=3),
]


def test_one_sitting_becomes_one_ordered_exchange_with_every_source() -> None:
    documents = _compile(QIXI)
    episodes = [doc for doc in documents if is_conversation_episode(doc)]
    singles = [doc for doc in documents if not is_conversation_episode(doc)]
    assert len(singles) == 5, "single lines stay searchable on purpose"
    assert len(episodes) == 1
    episode = episodes[0]
    assert episode.memory_kind == "episodic" and episode.authority == "dialogue_record"
    assert episode.text.splitlines() == [
        "一段聊天：",
        "他：今天七夕，本来和朋友几个约好打游戏的",
        "他：结果朋友去陪女友去了，烦死了",
        "我：哈哈哈哈哈",
        "我：那你今晚打算干嘛？要不要找点别的乐子",
        "他：随便刷刷视频吧",
    ]
    assert set(episode.source_refs) == {f"event:dialogue:{n}" for n in range(1, 6)}
    assert episode.occurred_from == QIXI[0].occurred_at
    assert episode.occurred_to == QIXI[-1].occurred_at
    assert episode.subject_refs == tuple(sorted((ACTOR, USER)))
    assert episode.epistemic_scope == "counterpart_report_only"


def test_separate_sittings_and_long_talks_are_split_and_lone_lines_are_not_episodes() -> None:
    long_talk = [
        _line(10 + i, f"第{i}句关于期末复习的话", speaker="counterpart" if i % 2 else "companion",
              minutes=60 * 5 + i)
        for i in range(11)
    ]
    lone = [_line(40, "晚安", speaker="counterpart", minutes=60 * 12)]
    episodes = [doc for doc in _compile([*QIXI, *long_talk, *lone]) if is_conversation_episode(doc)]
    # Qixi sitting; eleven-line talk split into 8 + 3; the lone goodnight is not an episode.
    assert sorted(len(doc.text.splitlines()) - 1 for doc in episodes) == [3, 5, 8]
    assert all(len(doc.source_bindings) <= 16 for doc in episodes)


def test_unbidden_attention_offers_the_exchange_not_its_fragments() -> None:
    from companion_daemon.world_v2.recall_runtime import _automatic_attention_eligible

    documents = _compile(QIXI)
    eligible = [doc for doc in documents if _automatic_attention_eligible(doc)]
    assert len(eligible) == 1 and is_conversation_episode(eligible[0])
