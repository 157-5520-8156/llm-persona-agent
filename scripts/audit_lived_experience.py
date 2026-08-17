#!/usr/bin/env python
"""Read one production ledger and report whether she is actually living.

Every acceptance in this project has historically closed at "code committed",
which is how a frozen relationship, a character who is never angry, and a
one-activity week all survived a green test suite.  This script closes the
other end: it reads the append-only ledger and answers, in numbers, the four
questions a person would ask after chatting with her for a week.

    python scripts/audit_lived_experience.py data/companion.epoch2.sqlite

It only reads.  Pass ``--json`` to get the same figures as one object for a
dashboard or a regression gate.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

REFLECTION_THRESHOLD_BP = 8_500
NEGATIVE_DIMENSIONS = ("hurt", "anger", "sadness", "loneliness", "anxiety", "resentment")
# Habits her own 92-message sample showed: a full stop on 68% of bubbles, one
# concessive opener in five, and no emoji at all.  Real phone chat looks
# nothing like that, so these are tracked as voice regressions.
VOICE_TICS = ("不过", "其实", "确实")
EMOJI = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")
QUESTION_TAIL = ("?", "？", "吗", "呢", "吧")


def busiest_world(connection: sqlite3.Connection) -> str | None:
    """Pick the world she actually lives in.

    A ledger file can hold more than one world: the QQ deployment writes
    ``world:companion-v2:qq-c2c:<user>`` while an older console world may keep a
    handful of events under a different id.  ``ledger_sequence`` restarts per
    world, so aggregating across them mixes two histories and silently
    misreports every figure below.
    """

    row = connection.execute(
        "SELECT world_id, COUNT(*) FROM world_v2_events"
        " GROUP BY world_id ORDER BY COUNT(*) DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def _events(connection: sqlite3.Connection, world_id: str) -> Iterator[dict[str, Any]]:
    cursor = connection.execute(
        "SELECT event_json FROM world_v2_events WHERE world_id = ?"
        " ORDER BY ledger_sequence",
        (world_id,),
    )
    for (raw,) in cursor:
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            continue


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("payload_json")
    if not isinstance(raw, str):
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _walk(value: Any, key: str) -> Iterator[Any]:
    """Yield every value stored under ``key`` anywhere inside a payload."""

    if isinstance(value, dict):
        for name, item in value.items():
            if name == key:
                yield item
            yield from _walk(item, key)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item, key)


def audit(path: Path, *, world_id: str | None = None) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        world = world_id or busiest_world(connection)
        if world is None:
            raise ValueError(f"账本里没有任何世界事件：{path}")
        types: Counter[str] = Counter()
        messages: list[str] = []
        affect: Counter[str] = Counter()
        intensities: list[int] = []
        appraisal_weights: list[int] = []
        relationship_deltas: list[dict[str, int]] = []
        stages: list[str] = []
        span: list[str] = []
        reflections = 0
        for event in _events(connection, world):
            kind = event.get("event_type")
            if not isinstance(kind, str):
                continue
            types[kind] += 1
            moment = event.get("logical_time")
            if isinstance(moment, str):
                span.append(moment)
            payload = _payload(event)
            if kind == "TriggerProcessOpened" and any(
                item == "life_reflection" for item in _walk(payload, "process_kind")
            ):
                reflections += 1
            if kind == "MessagePayloadStored":
                text = payload.get("message", {}).get("text")
                if isinstance(text, str) and text.strip():
                    messages.append(text.strip())
            elif kind in {"AffectEpisodeOpened", "AffectEpisodeUpdated"}:
                for component in _walk(payload, "components"):
                    if not isinstance(component, list):
                        continue
                    for item in component:
                        if not isinstance(item, dict):
                            continue
                        dimension = item.get("dimension")
                        intensity = item.get("intensity_bp")
                        if isinstance(dimension, str):
                            affect[dimension] += 1
                        if isinstance(intensity, int):
                            intensities.append(intensity)
            elif kind == "AppraisalAccepted":
                for weight in _walk(payload, "confidence_bp"):
                    if isinstance(weight, int):
                        appraisal_weights.append(weight)
            elif kind == "RelationshipSlowVariableAdjusted":
                for deltas in _walk(payload, "accepted_deltas"):
                    if isinstance(deltas, dict):
                        relationship_deltas.append(deltas)
                for stage in _walk(payload, "stage_after"):
                    if isinstance(stage, str):
                        stages.append(stage)
    finally:
        connection.close()

    negative = {name: affect[name] for name in NEGATIVE_DIMENSIONS if affect[name]}
    revisitable = [weight for weight in appraisal_weights if weight >= REFLECTION_THRESHOLD_BP]
    return {
        "ledger": str(path),
        "world_id": world,
        "events": sum(types.values()),
        "span": {"from": min(span, default=None), "to": max(span, default=None)},
        "relationship": {
            "signals_accepted": types["RelationshipSignalAccepted"],
            "slow_variable_adjustments": types["RelationshipSlowVariableAdjusted"],
            "commitments_accepted": types["RelationshipCommitmentAccepted"],
            "stage_after_latest": stages[-1] if stages else None,
            "moved_axes": sorted(
                {axis for deltas in relationship_deltas for axis, value in deltas.items() if value}
            ),
        },
        "feeling": {
            "episodes_opened": types["AffectEpisodeOpened"],
            "components_by_dimension": dict(affect.most_common()),
            "negative_components": negative,
            "intensity_median_bp": sorted(intensities)[len(intensities) // 2]
            if intensities
            else None,
            "appraisals": len(appraisal_weights),
            "she_weighed_above_reflection_threshold": len(revisitable),
            "reflections_opened": reflections,
        },
        "life": {
            "activities_started": types["ActivityStarted"],
            "activities_completed": types["ActivityCompleted"],
            "occurrences_settled": types["WorldOccurrenceSettled"],
            "private_impressions_accepted": types["PrivateImpressionAccepted"],
            "expectations_assessed": types["ResponseExpectationAssessed"],
        },
        "voice": voice_report(messages),
    }


def voice_report(messages: list[str]) -> dict[str, Any]:
    if not messages:
        return {"bubbles": 0}
    lengths = sorted(len(item) for item in messages)
    full_stop = sum(1 for item in messages if item.rstrip().endswith("。"))
    emoji = sum(1 for item in messages if EMOJI.search(item))
    question = sum(
        1 for item in messages if item.rstrip("。！ ").endswith(QUESTION_TAIL)
    )
    tics = {
        tic: sum(1 for item in messages if item.startswith(tic)) for tic in VOICE_TICS
    }
    return {
        "bubbles": len(messages),
        "length_median": lengths[len(lengths) // 2],
        "full_stop_rate": round(full_stop / len(messages), 3),
        "emoji_rate": round(emoji / len(messages), 3),
        "question_tail_rate": round(question / len(messages), 3),
        "opener_tics": {tic: count for tic, count in tics.items() if count},
        "opener_tic_rate": round(sum(tics.values()) / len(messages), 3),
    }


def render(report: dict[str, Any]) -> str:
    relationship = report["relationship"]
    feeling = report["feeling"]
    life = report["life"]
    voice = report["voice"]
    lines = [
        f"账本 {report['ledger']}  世界 {report['world_id']}  事件 {report['events']}",
        f"区间 {report['span']['from']} → {report['span']['to']}",
        "",
        "关系是否真的在动",
        f"  她写下的关系信号 {relationship['signals_accepted']}",
        f"  真正落账的慢变量调整 {relationship['slow_variable_adjustments']}"
        f"（动过的轴：{'、'.join(relationship['moved_axes']) or '无'}）",
        f"  当前阶段 {relationship['stage_after_latest'] or '未记录任何移动'}",
        "",
        "她有没有真的情绪",
        f"  情绪片段 {feeling['episodes_opened']}，成分 {feeling['components_by_dimension'] or '无'}",
        f"  其中负面 {feeling['negative_components'] or '无'}",
        f"  强度中位 {feeling['intensity_median_bp']}",
        f"  评价 {feeling['appraisals']} 次，她自己判定值得再想的 "
        f"{feeling['she_weighed_above_reflection_threshold']} 次"
        f"（门槛 {REFLECTION_THRESHOLD_BP}）",
        f"  真的又想起来 {feeling['reflections_opened']} 次",
        "",
        "她有没有自己的生活",
        f"  活动 开始 {life['activities_started']} / 完成 {life['activities_completed']}",
        f"  已结算世界事件 {life['occurrences_settled']}",
        f"  不在场时想起他 {life['private_impressions_accepted']}",
        f"  期待被回应的结算 {life['expectations_assessed']}",
        "",
        "她说话像不像真人",
    ]
    if voice["bubbles"]:
        lines += [
            f"  气泡 {voice['bubbles']}，长度中位 {voice['length_median']}",
            f"  句号结尾 {voice['full_stop_rate']:.0%}（真人私聊应远低于此）",
            f"  带表情 {voice['emoji_rate']:.0%}",
            f"  以问句收尾 {voice['question_tail_rate']:.0%}",
            f"  口头禅开头 {voice['opener_tic_rate']:.0%} {voice['opener_tics'] or ''}",
        ]
    else:
        lines.append("  这个账本里她还没说过话")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path, nargs="?", default=Path("data/companion.epoch2.sqlite"))
    parser.add_argument("--json", action="store_true", help="emit the raw figures")
    parser.add_argument("--world", help="audit one world id instead of the busiest one")
    args = parser.parse_args(argv)
    if not args.ledger.exists():
        print(f"账本不存在：{args.ledger}", file=sys.stderr)
        return 2
    report = audit(args.ledger, world_id=args.world)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
