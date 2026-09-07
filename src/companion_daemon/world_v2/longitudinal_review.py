"""Read-only, ordered evidence for a human longitudinal review.

No model calls, semantic verdicts, runtime policy, or formal qualification live
here. Administrative provenance is hidden from reviewers; authored text is not
rewritten. Packets may contain private fixture text and are not public exports.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter
from copy import deepcopy
import hashlib
import json
from typing import Any


REVIEW_DIMENSIONS = {
    "personality_continuity": (
        "人格连续性",
        "跨情境检查身份、价值与边界；区分有来源的成长和无解释的矛盾，不要求语气恒定。",
    ),
    "memory": (
        "记忆",
        "分别核对入库、召回、实际模型可见、准确使用与更正；未主动提起不等于遗忘。",
    ),
    "topic_repetition": (
        "话题重复",
        "顺序查看同题的新进展、主动回忆和未获回应的催问；字面重复只能提供待审候选。",
    ),
    "event_change": (
        "事件带来的变化",
        "追踪事件到记忆、Appraisal、生活状态、后续上下文和角色选择；仅先后发生不证明因果。",
    ),
    "event_richness": (
        "事件丰富度",
        "结合实际开放的机会核对已结算经历、参与者和后果；提案数不等于经历丰富度。",
    ),
    "choice_diversity": (
        "选择多样性",
        "在可用机会下观察角色如何表达、等待、沉默和使用能力；不规定比例或要求每次有动作。",
    ),
}

_MANIFEST_FIELDS = (
    "synthetic",
    "completed",
    "requested_virtual_days",
    "elapsed_virtual_seconds",
    "elapsed_logical_seconds",
    "final_logical_time",
    "wall_seconds",
    "turns_consumed",
    "turns_requested",
    "restarts",
    "replay",
    "stop_reason",
    "usage",
    "safety",
    "exclusions",
)
_PROVENANCE_FIELDS = {
    "version",
    "variant",
    "variant_id",
    "git_revision",
    "git_commit",
    "commit",
    "branch",
    "model",
    "model_id",
    "model_name",
    "provider",
    "provider_id",
    "provider_name",
    "world_id",
    "trace_id",
    "correlation_id",
    "idempotency_key",
}


def _hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _blind(value: Any, refs: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _blind(item, refs)
            for key, item in value.items()
            if key not in _PROVENANCE_FIELDS and not key.endswith("_version")
        }
    if isinstance(value, list):
        return [_blind(item, refs) for item in value]
    if isinstance(value, str):
        return refs.get(value, value)
    return deepcopy(value)


def _terminal_outcome(row: dict) -> str:
    declared = row.get("terminal_outcome", "unknown")
    failed = bool(row.get("errors")) or row.get("status") in {
        "technical_failure",
        "failed",
        "error",
    }
    if failed and declared == "character_silent":
        return "conflicting_evidence"
    if failed and declared == "unknown":
        return "technical_failure"
    return declared


def _context_visibility(value: Any) -> str:
    if not isinstance(value, dict) or value.get("model_facing") is not True:
        return "unverified"
    raw = value.get("model_content_json")
    if not isinstance(raw, str) or not raw:
        return "unverified"
    if hashlib.sha256(raw.encode("utf-8")).hexdigest() != value.get("content_hash"):
        raise ValueError("model-facing context hash mismatch")
    return "supplied"


def build_review_packet(
    *,
    timeline: list[dict],
    evidence: list[dict],
    run_manifest: dict,
) -> dict:
    """Bind immutable event bytes and preserve the caller's chronological order.

    Ledger ranges mean ``start <= ledger_sequence <= end``; an empty range has
    ``start == end + 1``. The packet contains
    projected evidence, not original event bytes; ``event_hash`` binds the full
    supplied event and ``payload_hash`` binds the original payload JSON. Known
    event refs are pseudonymized consistently, including references in payloads.
    """
    ids = [row["event_id"] for row in evidence] + [row["step_id"] for row in timeline]
    if len(set(ids)) != len(ids) or any(not isinstance(item, str) or not item for item in ids):
        raise ValueError("event and step IDs must be nonempty and unique")
    sequences = [row["ledger_sequence"] for row in evidence]
    if any(type(item) is not int or item <= 0 for item in sequences):
        raise ValueError("event ledger sequences must be positive integers")
    if any(left >= right for left, right in zip(sequences, sequences[1:])):
        raise ValueError("event ledger sequences must be unique and ordered")
    refs = {row["event_id"]: f"event:{index:06d}" for index, row in enumerate(evidence, 1)}
    refs.update({row["step_id"]: f"step:{index:06d}" for index, row in enumerate(timeline, 1)})
    events = []
    for row in evidence:
        raw = row["payload_json"]
        if hashlib.sha256(raw.encode("utf-8")).hexdigest() != row["payload_hash"]:
            raise ValueError("event payload hash mismatch")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("event payload must be an object")
        events.append(
            {
                "ref": refs[row["event_id"]],
                "event_hash": _hash(row),
                "payload_hash": row["payload_hash"],
                "ledger_sequence": row["ledger_sequence"],
                "event_type": row["event_type"],
                "logical_time": row["logical_time"],
                "actor": row.get("actor"),
                "causation_ref": refs.get(row.get("causation_id")),
                "payload": _blind(payload, refs),
            }
        )
    steps = []
    for row in timeline:
        start, end = row["ledger_start_sequence"], row["ledger_end_sequence"]
        if type(start) is not int or type(end) is not int or start < 1 or end < start - 1:
            raise ValueError("invalid step ledger range")
        event_refs = [
            item["ref"]
            for item in events[bisect_left(sequences, start) : bisect_right(sequences, end)]
        ]
        steps.append(
            {
                "ref": refs[row["step_id"]],
                "step_hash": _hash(row),
                "kind": row["kind"],
                "virtual_at": row["virtual_at"],
                "user_text": row.get("user_text"),
                "deliveries": _blind(row.get("deliveries", []), refs),
                "status": row.get("status", "unknown"),
                "errors": _blind(row.get("errors", []), refs),
                "terminal_outcome": _terminal_outcome(row),
                "terminal_outcomes": _blind(row.get("terminal_outcomes", []), refs),
                "context_evidence": _blind(row.get("context_evidence"), refs),
                "context_visibility": _context_visibility(row.get("context_evidence")),
                "ledger_start_sequence": start,
                "ledger_end_sequence": end,
                "evidence_refs": event_refs,
                "unprovided_event_count": end - start + 1 - len(event_refs),
            }
        )
    return {
        "schema_version": "longitudinal-review.1",
        "instructions": [
            "按完整时间顺序评审；不自动打真人分，不把沉默、拒绝或没有选中某种能力判为失败。",
            "assessed 表示已依据证据完成判断，并不表示通过；证据不足使用 insufficient。",
            "事件存在不证明进入后续 Context；来源引用合法也不证明可见自然语言真实。",
            "虚拟时间跨度不等于真实跨日连续运行；synthetic 只能验证评估链路。",
            "管理版本和供应商字段已隐藏；角色原文保留，原文可能自行泄露身份，不能保证完全盲评。",
        ],
        "run_summary": _blind(
            {key: run_manifest[key] for key in _MANIFEST_FIELDS if key in run_manifest}, refs
        ),
        "timeline": steps,
        "evidence": events,
        "annotations_template": [
            {
                "dimension": key,
                "label": value[0],
                "guidance": value[1],
                "status": "insufficient",
                "evidence_refs": [],
                "rationale": "尚未独立评审",
            }
            for key, value in REVIEW_DIMENSIONS.items()
        ],
    }


def validate_review_annotations(*, packet: dict, annotations: list[dict]) -> list[dict]:
    """Check review completeness and reference integrity, never its semantics.

    ``assessed`` means a reviewer supplied an evidence-backed judgement, not
    that the character passed. ``insufficient`` needs an explanation and may
    have zero refs. Rationale prose is not machine-checked for truthfulness.
    """
    dimensions = [row.get("dimension") for row in annotations]
    if len(dimensions) != len(REVIEW_DIMENSIONS) or set(dimensions) != set(REVIEW_DIMENSIONS):
        raise ValueError("annotations must cover each review dimension exactly once")
    available = {row["ref"] for row in packet["timeline"] + packet["evidence"]}
    for row in annotations:
        if row.get("status") not in {"assessed", "insufficient"}:
            raise ValueError("review status must be assessed or insufficient")
        if not isinstance(row.get("rationale"), str) or not row["rationale"].strip():
            raise ValueError("review rationale is required, including insufficient evidence")
        refs = row.get("evidence_refs")
        if not isinstance(refs, list) or any(
            not isinstance(ref, str) or ref not in available for ref in refs
        ):
            raise ValueError("unknown review evidence reference")
        if row["status"] == "assessed" and not refs:
            raise ValueError("assessed review requires evidence references")
    return deepcopy(annotations)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _quoted(value: Any) -> list[str]:
    # An indented code block preserves fixture text without interpreting HTML,
    # Markdown images/links or embedded fences as report markup.
    return ["", *("    " + line for line in str(value).splitlines()), ""]


def render_longitudinal_report(
    *, manifest: dict, timeline: list[dict], evidence: list[dict]
) -> str:
    """Render a diagnostic report, retaining unknowns and partial-run limits."""
    packet = build_review_packet(timeline=timeline, evidence=evidence, run_manifest=manifest)
    lines = [
        "# 纵向旅程证据报告",
        "",
        "本报告整理运行证据，六维结论仍需独立评审。assessed 不表示通过；当前均为 insufficient。",
        "虚拟时间推进不能证明真实跨日连续运行；synthetic 仅证明评估链路可执行。",
        "",
        "## 运行范围与费用",
        "",
    ]
    for key in _MANIFEST_FIELDS:
        lines.append(f"- {key}: {_json(packet['run_summary'].get(key, 'unavailable'))}")
    lines.extend(
        [
            "",
            "费用中的 settled、pending、unknown 按采集值原样列出；缺项不视作零，预测不等于实付。",
            "",
            "## 证据边界",
            "",
        ]
    )
    supplied = sum(row["context_visibility"] == "supplied" for row in packet["timeline"])
    missing = sum(row["unprovided_event_count"] for row in packet["timeline"])
    lines.extend(
        [
            f"- 实际模型可见 Context: supplied {supplied}/{len(timeline)} 步；其余 unverified。",
            "- ModelResultRecorded、decision_context 或 Fact 已入库，均不单独证明内容送到了模型或被使用。",
            f"- 各步 ledger 范围内未提供的事件总数: {missing}；缺少事件材料不能当作机制没有发生。",
            "- 无消息不反推沉默；technical_failure、未知终态与角色明确选择分别记录。",
            "- 出现事件与后续表达不等于因果闭环；需检查生产、接受、实际 Context 消费和后续选择引用。",
            "- 以下统计为事件类型数量，不能替代生活主题丰富度、来源真实性或人格一致性判断。",
            "",
            "## 已提供的机制事件",
            "",
        ]
    )
    counts = Counter(row["event_type"] for row in packet["evidence"])
    if not counts:
        lines.append("未提供 WorldEvent 证据；机制链为 insufficient。")
    for event_type, count in sorted(counts.items()):
        refs = [row["ref"] for row in packet["evidence"] if row["event_type"] == event_type]
        lines.append(f"- {event_type}: {count}，引用 {', '.join(refs)}")
    lines.extend(["", "## 完整顺序时间线", ""])
    for row in packet["timeline"]:
        lines.extend(
            [
                f"### {row['ref']} · {row['virtual_at']} · {row['kind']}",
                "",
                f"状态: {row['status']}；终态: {row['terminal_outcome']}；Context: {row['context_visibility']}。",
                f"ledger: [{row['ledger_start_sequence']}, {row['ledger_end_sequence']}]；step_hash: {row['step_hash']}",
                f"事件引用: {', '.join(row['evidence_refs']) or '无提供'}；未提供 {row['unprovided_event_count']} 条。",
            ]
        )
        if row["user_text"] is not None:
            lines.append("用户：")
            lines.extend(_quoted(row["user_text"]))
        for delivery in row["deliveries"]:
            lines.append(f"交付记录（{delivery.get('kind', 'unknown')}）：")
            lines.extend(_quoted(delivery.get("text", _json(delivery))))
        if not row["deliveries"]:
            lines.append("无交付记录；原因见终态证据，不能据此判定角色沉默。")
        if row["terminal_outcomes"]:
            lines.append("逐条进程终态：")
            lines.extend(_quoted(_json(row["terminal_outcomes"])))
        if row["errors"]:
            lines.append("技术错误：")
            lines.extend(_quoted(_json(row["errors"])))
        if row["context_evidence"]:
            lines.append("Context 采集材料（内容哈希仅绑定所采集字节）：")
            lines.extend(_quoted(_json(row["context_evidence"])))
        lines.append("")
    lines.extend(["## 事件内容与来源哈希", ""])
    for row in packet["evidence"]:
        lines.extend(
            [
                f"### {row['ref']} · {row['event_type']} · ledger {row['ledger_sequence']}",
                "",
                f"logical_time: {row['logical_time']}；actor: {row['actor']}；causation_ref: {row['causation_ref']}",
                f"event_hash: {row['event_hash']}；payload_hash: {row['payload_hash']}",
            ]
        )
        lines.extend(_quoted(_json(row["payload"])))
    lines.extend(["## 独立评审待填", ""])
    for row in packet["annotations_template"]:
        lines.extend(
            [
                f"- {row['label']}（{row['dimension']}）: insufficient；{row['guidance']}",
                "  评审时记录 rationale 与 evidence_refs；材料不足可以保留 insufficient。",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
