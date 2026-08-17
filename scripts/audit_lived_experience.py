#!/usr/bin/env python
"""Read one production ledger and report whether she is actually living.

Every acceptance in this project has historically closed at "code committed",
which is how a frozen relationship, a character who is never angry, and a
one-activity week all survived a green test suite.  This script closes the
other end: it reads the append-only ledger and answers, in numbers, whether
she is living — and whether three silent failures (semantic recall falling
back, proactive contact paid but never delivered, reflection never firing)
are happening in production unnoticed.

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
REFLECTION_USAGE_PURPOSES = ("world_stimulus_appraisal",)
PROACTIVE_MODEL_PURPOSE = "proactive_contact"
PROACTIVE_ACTION_KINDS = frozenset({"proactive_message"})
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


def _day(moment: str) -> str | None:
    return moment[:10] if len(moment) >= 10 else None


def _table_columns(connection: sqlite3.Connection, table: str) -> tuple[str, ...] | None:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    if exists is None:
        return None
    return tuple(row[1] for row in connection.execute(f'PRAGMA table_info("{table}")'))


def _round_cny(value: float) -> float:
    return round(value, 6)


def _unrecorded() -> dict[str, Any]:
    return {"recorded": False}


def _usage_scope(
    connection: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    world_id: str,
) -> tuple[str, tuple[Any, ...], bool]:
    """Return WHERE clause, params, and whether rows are tagged with this world.

    ``world_v2_model_usage.world_id`` may be the empty default.  Filtering it
    to the selected world then yields zero rows and hides every yuan spent.
    Empty-id rows are used only when this world has no tagged rows.
    """

    if "world_id" not in columns:
        return "", (), False
    tagged = connection.execute(
        f'SELECT COUNT(*) FROM "{table}" WHERE world_id = ?',
        (world_id,),
    ).fetchone()
    if tagged and tagged[0]:
        return " WHERE world_id = ?", (world_id,), True
    empty = connection.execute(
        f"SELECT COUNT(*) FROM \"{table}\" WHERE world_id = ''",
    ).fetchone()
    if empty and empty[0]:
        return " WHERE world_id = ''", (), False
    return " WHERE world_id = ?", (world_id,), True


def semantic_recall_report(
    connection: sqlite3.Connection, world_id: str
) -> dict[str, Any]:
    columns = _table_columns(connection, "world_recall_embedding_usage_daily")
    if columns is None:
        return _unrecorded()
    needed = ("usage_day", "succeeded_count", "failed_count")
    if any(name not in columns for name in needed):
        return _unrecorded()
    optional = [
        name
        for name in (
            "request_count",
            "rejected_count",
            "last_status",
            "last_failure_code",
            "last_embedding_version",
        )
        if name in columns
    ]
    select = ", ".join(("usage_day", "succeeded_count", "failed_count", *optional))
    where, params, scoped = _usage_scope(
        connection, "world_recall_embedding_usage_daily", columns, world_id
    )
    rows = connection.execute(
        f'SELECT {select} FROM "world_recall_embedding_usage_daily"{where}'
        " ORDER BY usage_day",
        params,
    ).fetchall()
    labels = ("usage_day", "succeeded_count", "failed_count", *optional)
    days: list[dict[str, Any]] = []
    for row in rows:
        item = dict(zip(labels, row))
        days.append(
            {
                "usage_day": item["usage_day"],
                "succeeded_count": int(item["succeeded_count"] or 0),
                "failed_count": int(item["failed_count"] or 0),
                "request_count": int(item["request_count"] or 0)
                if "request_count" in item
                else None,
                "rejected_count": int(item["rejected_count"] or 0)
                if "rejected_count" in item
                else None,
                "last_status": item.get("last_status"),
                "last_failure_code": item.get("last_failure_code"),
                "last_embedding_version": item.get("last_embedding_version"),
            }
        )
    streak = 0
    for item in reversed(days):
        if item["failed_count"] > 0 and item["succeeded_count"] == 0:
            streak += 1
        else:
            break
    last = days[-1] if days else None
    return {
        "recorded": True,
        "world_scoped": scoped,
        "days": days,
        "succeeded_total": sum(item["succeeded_count"] for item in days),
        "failed_total": sum(item["failed_count"] for item in days),
        "last_status": last["last_status"] if last else None,
        "last_failure_code": last["last_failure_code"] if last else None,
        "consecutive_failed_days": streak,
        "all_failed_no_success": bool(days)
        and all(item["failed_count"] > 0 and item["succeeded_count"] == 0 for item in days),
    }


def _purpose_bucket() -> dict[str, Any]:
    return {"calls": 0, "succeeded": 0, "failed": 0, "cost_cny": 0.0}


def model_usage_report(
    connection: sqlite3.Connection, world_id: str
) -> dict[str, Any]:
    columns = _table_columns(connection, "world_v2_model_usage")
    if columns is None:
        return _unrecorded()
    needed = ("purpose", "status", "cost_cny")
    if any(name not in columns for name in needed):
        return _unrecorded()
    has_time = "recorded_at" in columns
    select = "purpose, status, cost_cny" + (", recorded_at" if has_time else "")
    where, params, scoped = _usage_scope(
        connection, "world_v2_model_usage", columns, world_id
    )
    rows = connection.execute(
        f'SELECT {select} FROM "world_v2_model_usage"{where}',
        params,
    ).fetchall()
    by_purpose: dict[str, dict[str, Any]] = {}
    by_day: dict[str, dict[str, Any]] = {}
    by_month: dict[str, dict[str, Any]] = {}
    proactive = _purpose_bucket()
    related: dict[str, dict[str, Any]] = {
        purpose: _purpose_bucket() for purpose in REFLECTION_USAGE_PURPOSES
    }
    for row in rows:
        purpose = row[0] if isinstance(row[0], str) and row[0] else "(empty)"
        status = row[1] if isinstance(row[1], str) else ""
        cost = float(row[2] or 0)
        bucket = by_purpose.setdefault(purpose, _purpose_bucket())
        bucket["calls"] += 1
        bucket["cost_cny"] = _round_cny(bucket["cost_cny"] + cost)
        if status == "succeeded":
            bucket["succeeded"] += 1
        elif status == "failed":
            bucket["failed"] += 1
        if purpose == PROACTIVE_MODEL_PURPOSE:
            proactive["calls"] += 1
            proactive["cost_cny"] = _round_cny(proactive["cost_cny"] + cost)
            if status == "succeeded":
                proactive["succeeded"] += 1
            elif status == "failed":
                proactive["failed"] += 1
        if purpose in related:
            related[purpose]["calls"] += 1
            related[purpose]["cost_cny"] = _round_cny(related[purpose]["cost_cny"] + cost)
            if status == "succeeded":
                related[purpose]["succeeded"] += 1
            elif status == "failed":
                related[purpose]["failed"] += 1
        if "reflection" in purpose and purpose not in related:
            extra = related.setdefault(purpose, _purpose_bucket())
            extra["calls"] += 1
            extra["cost_cny"] = _round_cny(extra["cost_cny"] + cost)
            if status == "succeeded":
                extra["succeeded"] += 1
            elif status == "failed":
                extra["failed"] += 1
        if has_time and isinstance(row[3], str):
            day = _day(row[3])
            month = row[3][:7] if len(row[3]) >= 7 else None
            if day:
                daily = by_day.setdefault(day, {"calls": 0, "cost_cny": 0.0})
                daily["calls"] += 1
                daily["cost_cny"] = _round_cny(daily["cost_cny"] + cost)
            if month:
                monthly = by_month.setdefault(month, {"calls": 0, "cost_cny": 0.0})
                monthly["calls"] += 1
                monthly["cost_cny"] = _round_cny(monthly["cost_cny"] + cost)
    rank = sorted(
        (
            {"purpose": name, **stats}
            for name, stats in by_purpose.items()
        ),
        key=lambda item: (-item["cost_cny"], -item["calls"], item["purpose"]),
    )
    return {
        "recorded": True,
        "world_scoped": scoped,
        "proactive": proactive,
        "related_purposes": {
            name: stats for name, stats in related.items() if stats["calls"]
        },
        "purpose_rank": rank,
        "by_month": [
            {"month": name, **stats} for name, stats in sorted(by_month.items())
        ],
        "by_day": [
            {"day": name, **stats} for name, stats in sorted(by_day.items())
        ],
        "calls": sum(item["calls"] for item in rank),
        "cost_cny": _round_cny(sum(item["cost_cny"] for item in rank)),
    }


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
        reflections_by_day: Counter[str] = Counter()
        authorized_kind: dict[str, str] = {}
        delivered_ids: list[str] = []
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
                day = _day(moment) if isinstance(moment, str) else None
                if day:
                    reflections_by_day[day] += 1
            if kind == "ActionAuthorized":
                action = payload.get("action")
                if isinstance(action, dict):
                    action_id = action.get("action_id")
                    action_kind = action.get("kind")
                    if isinstance(action_id, str) and isinstance(action_kind, str):
                        authorized_kind[action_id] = action_kind
            elif kind == "ActionDelivered":
                action_id = payload.get("action_id")
                if isinstance(action_id, str):
                    delivered_ids.append(action_id)
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
        recall = semantic_recall_report(connection, world)
        usage = model_usage_report(connection, world)
    finally:
        connection.close()

    authorized_by_kind = dict(Counter(authorized_kind.values()).most_common())
    delivered_by_kind = Counter(
        authorized_kind[action_id] if action_id in authorized_kind else "unmatched"
        for action_id in delivered_ids
    )
    delivered_proactive = sum(
        count
        for kind, count in delivered_by_kind.items()
        if kind in PROACTIVE_ACTION_KINDS
    )
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
            "reflections_by_day": dict(sorted(reflections_by_day.items())),
        },
        "life": {
            "activities_started": types["ActivityStarted"],
            "activities_completed": types["ActivityCompleted"],
            "occurrences_settled": types["WorldOccurrenceSettled"],
            "private_impressions_accepted": types["PrivateImpressionAccepted"],
            "expectations_assessed": types["ResponseExpectationAssessed"],
        },
        "voice": voice_report(messages),
        "semantic_recall": recall,
        "proactive": {
            "model_calls": {**usage["proactive"], "recorded": True}
            if usage.get("recorded")
            else _unrecorded(),
            "model_usage_world_scoped": usage.get("world_scoped"),
            "authorized_by_kind": authorized_by_kind,
            "authorized": sum(authorized_by_kind.values()),
            "delivered_by_kind": dict(delivered_by_kind.most_common()),
            "delivered": len(delivered_ids),
            "delivered_proactive": delivered_proactive,
        },
        "spend": {
            "recorded": usage.get("recorded", False),
            "world_scoped": usage.get("world_scoped"),
            "life_reflection_opened": reflections,
            "life_reflection_by_day": dict(sorted(reflections_by_day.items())),
            "related_purposes": usage.get("related_purposes", {}),
            "purpose_rank": usage.get("purpose_rank", []),
            "by_month": usage.get("by_month", []),
            "by_day": usage.get("by_day", []),
            "calls": usage.get("calls", 0),
            "cost_cny": usage.get("cost_cny", 0),
        }
        if usage.get("recorded")
        else {
            **_unrecorded(),
            "life_reflection_opened": reflections,
            "life_reflection_by_day": dict(sorted(reflections_by_day.items())),
        },
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
        f"  评价 {feeling['appraisals']} 次，分量 ≥{REFLECTION_THRESHOLD_BP}"
        f"（已退役的固定门槛）{feeling['she_weighed_above_reflection_threshold']} 次",
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
    lines += ["", *_render_semantic_recall(report["semantic_recall"])]
    lines += ["", *_render_proactive(report["proactive"])]
    lines += ["", *_render_spend(report["spend"])]
    return "\n".join(lines)


def _format_cny(value: float) -> str:
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _format_counts(mapping: dict[str, int]) -> str:
    if not mapping:
        return "无"
    return "、".join(f"{name} {count}" for name, count in mapping.items())


def _render_semantic_recall(recall: dict[str, Any]) -> list[str]:
    lines = ["语义召回是否在降级"]
    if not recall.get("recorded"):
        lines.append("  未记录")
        return lines
    if not recall.get("world_scoped", True):
        lines.append("  用量未按世界分账（world_id 为空）")
    days = recall.get("days") or []
    if not days:
        lines.append("  这个世界没有召回用量行")
        return lines
    for item in days:
        extra = []
        if item.get("rejected_count"):
            extra.append(f"拒绝 {item['rejected_count']}")
        suffix = f"（{'，'.join(extra)}）" if extra else ""
        lines.append(
            f"  {item['usage_day']} 成功 {item['succeeded_count']} / 失败 {item['failed_count']}{suffix}"
        )
    lines.append(
        f"  合计 成功 {recall['succeeded_total']} / 失败 {recall['failed_total']}"
    )
    code = recall.get("last_failure_code") or "无"
    status = recall.get("last_status") or "无"
    lines.append(f"  最后状态 {status}，失败码 {code}")
    streak = recall.get("consecutive_failed_days") or 0
    if recall.get("all_failed_no_success"):
        lines.append(f"  连续 {streak} 天失败且成功为 0")
    elif streak:
        lines.append(f"  最近连续 {streak} 天失败且成功为 0")
    else:
        lines.append("  没有连续失败日")
    return lines


def _render_proactive(proactive: dict[str, Any]) -> list[str]:
    lines = ["主动联系是否真的送达"]
    calls = proactive.get("model_calls") or {}
    if not calls.get("recorded"):
        lines.append("  模型调用 未记录")
    else:
        lines.append(
            f"  模型调用 {PROACTIVE_MODEL_PURPOSE} 成功 {calls.get('succeeded', 0)}"
            f" / 失败 {calls.get('failed', 0)}，花费 {_format_cny(calls.get('cost_cny', 0))}"
        )
        if proactive.get("model_usage_world_scoped") is False:
            lines.append("  用量未按世界分账（world_id 为空）")
    lines.append(
        f"  授权动作 {_format_counts(proactive.get('authorized_by_kind') or {})}"
    )
    lines.append(
        f"  真正送达 {_format_counts(proactive.get('delivered_by_kind') or {})}"
        f"，其中 proactive_message {proactive.get('delivered_proactive', 0)}"
    )
    return lines


def _render_spend(spend: dict[str, Any]) -> list[str]:
    lines = ["复燃频率与花费"]
    opened = spend.get("life_reflection_opened", 0)
    by_day = spend.get("life_reflection_by_day") or {}
    if by_day:
        days = "，".join(f"{day} {count}" for day, count in by_day.items())
        lines.append(f"  TriggerProcessOpened life_reflection {opened}（{days}）")
    else:
        lines.append(f"  TriggerProcessOpened life_reflection {opened}")
    if not spend.get("recorded"):
        lines.append("  模型用量 未记录")
        return lines
    if spend.get("world_scoped") is False:
        lines.append("  用量未按世界分账（world_id 为空）")
    related = spend.get("related_purposes") or {}
    if related:
        parts = [
            f"{name} 成功 {stats.get('succeeded', 0)} / 失败 {stats.get('failed', 0)}"
            f"，花费 {_format_cny(stats.get('cost_cny', 0))}"
            for name, stats in related.items()
        ]
        lines.append("  相关 purpose " + "；".join(parts))
    else:
        lines.append("  相关 purpose 未出现")
    rank = spend.get("purpose_rank") or []
    if rank:
        lines.append("  按 purpose 花费")
        for item in rank:
            lines.append(
                f"    {item['purpose']} {_format_cny(item['cost_cny'])}"
                f"（成功 {item['succeeded']} / 失败 {item['failed']} / 共 {item['calls']}）"
            )
    months = spend.get("by_month") or []
    if months:
        text = "，".join(
            f"{item['month']} {_format_cny(item['cost_cny'])}（{item['calls']} 次）"
            for item in months
        )
        lines.append(f"  月累计 {text}")
    days = spend.get("by_day") or []
    if days:
        text = "，".join(
            f"{item['day']} {_format_cny(item['cost_cny'])}（{item['calls']} 次）"
            for item in days
        )
        lines.append(f"  日累计 {text}")
    return lines


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
