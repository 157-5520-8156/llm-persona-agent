from __future__ import annotations

from copy import deepcopy
import hashlib
import json

import pytest

from companion_daemon.world_v2.longitudinal_review import (
    build_review_packet,
    render_longitudinal_report,
    validate_review_annotations,
)


def _event(sequence: int, event_type: str, payload: dict) -> dict:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "event_id": f"event:variant-secret:{sequence}",
        "event_type": event_type,
        "logical_time": "2026-09-01T10:00:00Z",
        "ledger_sequence": sequence,
        "payload_json": raw,
        "payload_hash": hashlib.sha256(raw.encode()).hexdigest(),
        "schema_version": "variant-secret.1",
    }


def _step(step_id: str, start: int, end: int, **extra) -> dict:
    return {
        "step_id": step_id,
        "kind": "inbound",
        "virtual_at": "2026-09-01T10:00:00Z",
        "deliveries": [],
        "status": "completed",
        "errors": [],
        "ledger_start_sequence": start + 1,
        "ledger_end_sequence": end,
        **extra,
    }


def test_packet_keeps_history_order_and_evidence_without_exposing_run_versions() -> None:
    timeline = [
        _step("z-first", 0, 1, user_text="我改主意了", context_evidence={"hash": "context-a"}),
        _step("a-second", 1, 2, deliveries=[{"kind": "text", "text": "这次想换去哪儿？"}]),
    ]
    evidence = [
        _event(1, "FactCommittedV2", {"value": "原计划", "policy_version": "variant-secret"}),
        _event(2, "FactCommittedV2", {"value": "新计划", "supersedes": evidence_id(1)}),
    ]
    original = deepcopy((timeline, evidence))
    packet = build_review_packet(
        timeline=timeline,
        evidence=evidence,
        run_manifest={"synthetic": True, "completed": True, "git_revision": "variant-secret"},
    )

    assert [row["user_text"] for row in packet["timeline"]] == ["我改主意了", None]
    assert packet["timeline"][0]["evidence_refs"] == ["event:000001"]
    assert packet["timeline"][1]["evidence_refs"] == ["event:000002"]
    assert packet["evidence"][1]["payload"]["supersedes"] == "event:000001"
    assert packet["evidence"][0]["payload"]["value"] == "原计划"
    assert "variant-secret" not in json.dumps(packet)
    assert (timeline, evidence) == original
    assert {row["status"] for row in packet["annotations_template"]} == {"insufficient"}
    assert len(packet["annotations_template"]) == 6


def evidence_id(sequence: int) -> str:
    return f"event:variant-secret:{sequence}"


def test_no_delivery_is_unknown_and_technical_failure_cannot_be_character_silence() -> None:
    packet = build_review_packet(
        timeline=[
            _step("unknown", 0, 0),
            _step("silent", 0, 0, terminal_outcome="character_silent"),
            _step("failed", 0, 0, status="technical_failure", errors=["provider_timeout"]),
            _step("conflict", 0, 0, terminal_outcome="character_silent", errors=["timeout"]),
        ],
        evidence=[],
        run_manifest={},
    )
    assert [row["terminal_outcome"] for row in packet["timeline"]] == [
        "unknown",
        "character_silent",
        "technical_failure",
        "conflicting_evidence",
    ]


@pytest.mark.parametrize("corruption", ["payload", "duplicate_id", "duplicate_sequence"])
def test_packet_rejects_evidence_that_cannot_be_referenced_unambiguously(corruption) -> None:
    event = _event(1, "FactCommittedV2", {"value": "tea"})
    evidence = [event]
    if corruption == "payload":
        event["payload_json"] = '{"value":"coffee"}'
    else:
        duplicate = _event(2, "FactCommittedV2", {"value": "coffee"})
        if corruption == "duplicate_id":
            duplicate["event_id"] = event["event_id"]
        else:
            duplicate["ledger_sequence"] = event["ledger_sequence"]
        evidence.append(duplicate)
    with pytest.raises(ValueError):
        build_review_packet(timeline=[_step("first", 0, 2)], evidence=evidence, run_manifest={})


def test_review_annotations_require_existing_evidence_but_allow_honest_insufficiency() -> None:
    packet = build_review_packet(timeline=[_step("one", 0, 0)], evidence=[], run_manifest={})
    annotations = deepcopy(packet["annotations_template"])
    assert len(validate_review_annotations(packet=packet, annotations=annotations)) == 6
    annotations[0].update(
        status="assessed", rationale="可见身份有矛盾", evidence_refs=["step:000001"]
    )
    assert (
        validate_review_annotations(packet=packet, annotations=annotations)[0]["status"]
        == "assessed"
    )
    annotations[0]["evidence_refs"] = ["event:does-not-exist"]
    with pytest.raises(ValueError, match="reference"):
        validate_review_annotations(packet=packet, annotations=annotations)
    annotations[0]["evidence_refs"] = []
    with pytest.raises(ValueError, match="assessed"):
        validate_review_annotations(packet=packet, annotations=annotations)


def test_context_claim_needs_actual_model_facing_bytes_and_hash_and_keeps_multiple_outcomes() -> (
    None
):
    raw = '{"facts":[{"value":"改喝茶"}]}'
    timeline = [
        _step("audit-only", 0, 0, context_evidence={"hash": "audit-context"}),
        _step(
            "actual",
            0,
            0,
            context_evidence={
                "model_facing": True,
                "model_content_json": raw,
                "content_hash": hashlib.sha256(raw.encode()).hexdigest(),
            },
            terminal_outcomes=[
                {"runtime_outcome_ref": "proactive:silent"},
                {"runtime_outcome_ref": "failed"},
            ],
        ),
    ]
    packet = build_review_packet(timeline=timeline, evidence=[], run_manifest={})
    assert [row["context_visibility"] for row in packet["timeline"]] == ["unverified", "supplied"]
    assert packet["timeline"][1]["terminal_outcomes"] == timeline[1]["terminal_outcomes"]
    timeline[1]["context_evidence"]["content_hash"] = "bad-hash"
    with pytest.raises(ValueError, match="context"):
        build_review_packet(timeline=timeline, evidence=[], run_manifest={})


def test_report_exposes_partial_virtual_run_missing_context_cost_and_concrete_events() -> None:
    report = render_longitudinal_report(
        manifest={
            "synthetic": True,
            "completed": False,
            "requested_virtual_days": 7,
            "elapsed_virtual_seconds": 86400,
            "wall_seconds": 30,
            "restarts": 1,
            "stop_reason": "budget_exhausted",
            "usage": {"settled_cny": 0.2, "pending_cny": 0.3},
        },
        timeline=[_step("one", 0, 2, user_text="明天再说", errors=["provider_timeout"])],
        evidence=[_event(1, "ExperienceCommitted", {"summary": "计划已经完成"})],
    )
    for text in (
        "synthetic",
        "budget_exhausted",
        "pending_cny",
        "0.3",
        "86400",
        "30",
        "明天再说",
        "ExperienceCommitted",
        "event:000001",
        "unverified",
        "technical_failure",
        "provider_timeout",
        "insufficient",
    ):
        assert text in report
    assert "真人评估通过" not in report
    assert "计划已经完成" not in report


def test_packet_preserves_causal_lineage_and_independent_clock_measurements() -> None:
    origin = _event(1, "WorldOccurrenceSettled", {"result": "下雨取消活动"})
    consequence = _event(2, "AppraisalAccepted", {"meaning": "有点遗憾"})
    consequence.update(causation_id=origin["event_id"], actor="agent:companion")
    packet = build_review_packet(
        timeline=[_step("one", 0, 2)],
        evidence=[origin, consequence],
        run_manifest={
            "elapsed_virtual_seconds": 90000,
            "elapsed_logical_seconds": 86400,
            "wall_seconds": 30,
        },
    )
    assert packet["evidence"][1]["causation_ref"] == "event:000001"
    assert packet["evidence"][1]["actor"] == "agent:companion"
    assert packet["run_summary"]["elapsed_logical_seconds"] == 86400
    assert packet["timeline"][0]["unprovided_event_count"] == 0


def test_report_summarizes_quiet_days_but_preserves_conversation_and_full_review_packet() -> None:
    timeline = [_step("opening", 0, 8, user_text="这周我先忙一阵子。")]
    for day in (1, 2):
        timeline.extend(
            _step(
                f"quiet-{day}-{minute}",
                8,
                8,
                kind="scheduler",
                status="scheduled",
                virtual_at=f"2026-09-0{day}T10:{minute // 2:02d}:{30 * (minute % 2):02d}Z",
            )
            for minute in range(100)
        )
        timeline.append(
            _step(
                f"delivery-{day}",
                8,
                8,
                kind="scheduler",
                deliveries=[{"kind": "text", "text": f"第{day}天的主动消息。"}],
            )
        )
    timeline.extend(
        [
            _step("restart", 8, 8, kind="restart", status="reopened"),
            _step("failure", 8, 8, kind="scheduler", errors=["provider_timeout"]),
            _step("return", 8, 8, user_text="回来了，后来怎么样？"),
        ]
    )
    evidence = [
        _event(index, "ExperienceCommitted", {"summary": f"完整经历原文-{index}"})
        for index in range(1, 9)
    ]
    packet = build_review_packet(timeline=timeline, evidence=evidence, run_manifest={})
    report = render_longitudinal_report(manifest={}, timeline=timeline, evidence=evidence)

    assert "2026-09-01: 100 个安静调度步骤" in report
    assert "2026-09-02: 100 个安静调度步骤" in report
    assert len(report.splitlines()) < 180
    ordered = [
        "这周我先忙一阵子。",
        "第1天的主动消息。",
        "第2天的主动消息。",
        " · restart",
        "provider_timeout",
        "回来了，后来怎么样？",
    ]
    assert [report.index(item) for item in ordered] == sorted(
        report.index(item) for item in ordered
    )
    assert "ExperienceCommitted: 8" in report
    assert "event:000003" in report
    assert "event:000008" not in report
    assert "完整经历原文" not in report
    assert "review.json" in report and "evidence.jsonl" in report
    assert "unverified" in report and "insufficient" in report
    assert len(packet["timeline"]) == len(timeline)
    assert len(packet["evidence"]) == 8
    assert packet["evidence"][-1]["payload"]["summary"] == "完整经历原文-8"
