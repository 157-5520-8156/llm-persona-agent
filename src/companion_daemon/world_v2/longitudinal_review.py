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
    "model_failures",
    "provider_usage_evidence",
    "profile_differences",
    "life_source_review",
    "quiet_tail_seconds",
    "model_input_capture",
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


def _runtime_failures(row: dict) -> list[dict]:
    """Expose producer-defined failures even when every model attempt validated.

    The LifeEcology reducer uses these exact outcome identifiers for technical
    backoff. This also reads older captures whose generic terminal label was
    unknown; it does not infer character intent or parse authored language.
    """
    return [
        outcome
        for outcome in row.get("terminal_outcomes", [])
        if outcome.get("terminal_outcome") == "technical_failure"
        or outcome.get("outcome_ref") == "life-ecology:failed_safe"
        or str(outcome.get("outcome_ref", "")).startswith("life-ecology:technical_failure.")
    ]


def context_evidence_visibility(value: Any) -> str:
    if isinstance(value, dict) and isinstance(value.get("requests"), list):
        requests = value["requests"]
        captured = 0
        for request in requests:
            raw = request.get("model_content_json")
            if raw is None and request.get("model_facing") is False:
                continue
            if (
                request.get("kind") != "request"
                or not isinstance(raw, str)
                or hashlib.sha256(raw.encode("utf-8")).hexdigest() != request.get("content_hash")
            ):
                raise ValueError("client request body hash mismatch")
            captured += 1
        if not captured:
            return "unverified"
        return (
            "captured_client_requests"
            if captured == len(requests)
            else "partially_captured_client_requests"
        )
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
                "model_failures": _blind(row.get("model_failures", []), refs),
                "runtime_failures": _blind(_runtime_failures(row), refs),
                "terminal_outcome": _terminal_outcome(row),
                "terminal_outcomes": _blind(row.get("terminal_outcomes", []), refs),
                "context_evidence": _blind(row.get("context_evidence"), refs),
                "context_visibility": context_evidence_visibility(row.get("context_evidence")),
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


def _report_refs(refs: list[str]) -> str:
    shown = ", ".join(refs[:3]) or "无提供"
    return f"{shown}（共 {len(refs)} 条）" if len(refs) > 3 else shown


def _quiet_scheduler(row: dict) -> bool:
    return (
        row["kind"] == "scheduler"
        and row["status"] in {"scheduled", "completed", "idle"}
        and row["user_text"] is None
        and not row["deliveries"]
        and not row["errors"]
        and not row["model_failures"]
        and not row["runtime_failures"]
        and row["terminal_outcome"] not in {"technical_failure", "conflicting_evidence"}
    )


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
            "World 模型失败事件与主账用量记录分开呈现；成功恢复不会抹掉原尝试。",
            "用量状态及预约的原始记录见私有 provider-usage.json；budget_denied 不是已发出的 HTTP，"
            "记录与 World 事件的逐次关联仍未验证。旧报告没有这份材料时为 unavailable。",
            "此证据只含模型用量表及预约，不含独立 usage_events 外部调用账单或全局镜像；"
            "零模型记录不等于零媒体费用，费用 health 仍由 usage 字段单列。",
            "",
            "## 证据边界",
            "",
        ]
    )
    supplied = sum(row["context_visibility"] == "supplied" for row in packet["timeline"])
    captured = sum(
        row["context_visibility"] == "captured_client_requests" for row in packet["timeline"]
    )
    missing = sum(row["unprovided_event_count"] for row in packet["timeline"])
    lines.extend(
        [
            f"- 旧格式模型材料 supplied {supplied}/{len(timeline)} 步；全调用输入覆盖尚未核验。",
            f"- 另有 {captured} 步包含实际客户端请求字节；仅证明请求进入客户端 transport。",
            "- 捕获区间不是 pinned turn 关联证明；上游接收、模型关注和全调用覆盖仍需分别核对。",
            "- ModelResultRecorded、decision_context 或 Fact 已入库，均不单独证明内容送到了模型或被使用。",
            f"- 各步 ledger 范围内未提供的事件总数: {missing}；缺少事件材料不能当作机制没有发生。",
            "- 无消息不反推沉默；technical_failure、未知终态与角色明确选择分别记录。",
            "- 出现事件与后续表达不等于因果闭环；需检查生产、接受、实际 Context 消费和后续选择引用。",
            "- 以下统计为事件类型数量，不能替代生活主题丰富度、来源真实性或人格一致性判断。",
            "- 完整顺序、Context 材料和事件内容见 review.json；原始 WorldEvent 字节见 evidence.jsonl。",
        ]
    )
    quiet_days: dict[str, Counter] = {}
    for row in packet["timeline"]:
        if _quiet_scheduler(row):
            day = str(row["virtual_at"]).partition("T")[0]
            counts = quiet_days.setdefault(day, Counter())
            counts.update(
                steps=1, events=len(row["evidence_refs"]), outcomes=len(row["terminal_outcomes"])
            )
    lines.extend(["", "## 每日安静调度概览", ""])
    if not quiet_days:
        lines.append("没有省略安静调度步骤。")
    for day, counts in quiet_days.items():
        lines.append(
            f"- {day}: {counts['steps']} 个安静调度步骤；"
            f"{counts['events']} 条事件、{counts['outcomes']} 条进程终态记录。"
        )
    lines.extend(
        [
            "",
            "安静仅指该步没有用户输入、交付或错误；不表示内在状态没有变化，也不推断角色沉默。",
            "",
            "## 对话与关键节点（保持原始顺序）",
            "",
        ]
    )
    for row in packet["timeline"]:
        if _quiet_scheduler(row):
            continue
        lines.extend(
            [
                f"### {row['ref']} · {row['virtual_at']} · {row['kind']}",
                "",
                f"状态: {row['status']}；终态: {row['terminal_outcome']}；Context: {row['context_visibility']}。",
                f"事件引用: {_report_refs(row['evidence_refs'])}；未提供 {row['unprovided_event_count']} 条。",
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
            lines.append(
                f"另有 {len(row['terminal_outcomes'])} 条进程终态，见 review.json 的本步骤。"
            )
        if row["errors"]:
            lines.append("技术错误：")
            lines.extend(_quoted(_json(row["errors"])))
        if row["model_failures"]:
            lines.append("模型尝试的技术失败（之后成功恢复也保留）：")
            lines.extend(_quoted(_json(row["model_failures"])))
        if row["runtime_failures"]:
            lines.append("进程技术失败（模型返回合法也可能在提交时失败）：")
            lines.extend(_quoted(_json(row["runtime_failures"])))
        if row["context_evidence"]:
            lines.append("Context 采集材料及内容哈希见 review.json 的本步骤。")
        lines.append("")
    lines.extend(["## 已提供的机制事件", ""])
    counts = Counter(row["event_type"] for row in packet["evidence"])
    if not counts:
        lines.append("未提供 WorldEvent 证据；机制链为 insufficient。")
    for event_type, count in sorted(counts.items()):
        refs = [row["ref"] for row in packet["evidence"] if row["event_type"] == event_type]
        lines.append(f"- {event_type}: {count}，引用示例 {_report_refs(refs)}")
    lines.extend(["", "事件全文、来源哈希和因果引用保留在 review.json / evidence.jsonl。", ""])
    lines.extend(["## 独立评审待填", ""])
    for row in packet["annotations_template"]:
        lines.extend(
            [
                f"- {row['label']}（{row['dimension']}）: insufficient；{row['guidance']}",
                "  评审时记录 rationale 与 evidence_refs；材料不足可以保留 insufficient。",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
