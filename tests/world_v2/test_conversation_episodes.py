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
    # Qixi sitting; eleven-line talk split into 8 + 3 plus one context line; his lone goodnight is
    # still an episode so it can surface in that form.
    assert sorted(len(doc.text.splitlines()) - 1 for doc in episodes) == [1, 4, 5, 8]
    mine_alone = [_line(50, "我先睡啦", speaker="companion", minutes=60 * 20)]
    assert not [doc for doc in _compile(mine_alone) if is_conversation_episode(doc)]
    assert all(len(doc.source_bindings) <= 16 for doc in episodes)


def test_unbidden_attention_offers_the_exchange_not_its_fragments() -> None:
    from companion_daemon.world_v2.recall_index import automatic_attention_eligible as _automatic_attention_eligible

    documents = _compile(QIXI)
    eligible = [doc for doc in documents if _automatic_attention_eligible(doc)]
    assert len(eligible) == 1 and is_conversation_episode(eligible[0])


def test_long_exchange_splits_before_the_final_excerpt_budget_and_keeps_tail():
    lines = [
        _line(i + 1, str(i) + "长" * 159, speaker="counterpart" if i % 2 else "companion", minutes=i)
        for i in range(7)
    ] + [_line(8, "最后约好周末去青羊宫见面", speaker="counterpart", minutes=7)]
    episodes = [doc for doc in _compile(lines) if is_conversation_episode(doc)]
    assert all(len(doc.text) <= 1024 for doc in episodes)
    rendered = [line[2:] for doc in sorted(episodes, key=lambda d: d.occurred_from) for line in doc.text.splitlines()[1:]]
    assert rendered == [line.text for line in lines[:6]] + [lines[5].text] + [line.text for line in lines[6:]]
    assert {ref for doc in episodes for ref in doc.source_refs} == {f"event:dialogue:{i}" for i in range(1, 9)}


def test_split_does_not_make_the_characters_tail_replies_disappear():
    lines = [_line(1, "说说你最近的安排", speaker="counterpart", minutes=0)] + [
        _line(i, str(i) + "长" * 159, speaker="companion", minutes=i) for i in range(2, 11)
    ]
    episodes = sorted((doc for doc in _compile(lines) if is_conversation_episode(doc)), key=lambda d: int(d.source_item_ref.rsplit(":", 1)[1]))
    assert len(episodes) > 1
    assert all("他：说说你最近的安排" in doc.text for doc in episodes)
    own_lines = [line[2:] for doc in episodes for line in doc.text.splitlines()[1:] if line.startswith("我：")]
    assert own_lines == [line.text[:159] + "…" if len(line.text) > 160 else line.text for line in lines[1:]]
    assert all(len(doc.text) <= 1024 and len(doc.source_bindings) <= 16 for doc in episodes)
    assert len({doc.source_item_ref for doc in episodes}) == len(episodes)
