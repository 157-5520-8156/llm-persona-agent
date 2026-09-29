from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from .types import BehaviorAuditResult, BehaviorVerdict


def summarize_verdicts(verdicts: list[BehaviorVerdict]) -> dict[str, Any]:
    by_state = {"occurred": 0, "opportunity_no_occurrence": 0, "no_opportunity": 0}
    by_class = {"mechanical": 0, "semi_automatic": 0, "human_only": 0}
    for item in verdicts:
        by_state[item.state] = by_state.get(item.state, 0) + 1
        by_class[item.classification] = by_class.get(item.classification, 0) + 1
    return {
        "behavior_count": len(verdicts),
        "by_state": by_state,
        "by_classification": by_class,
        "occurred_ids": [item.behavior_id for item in verdicts if item.state == "occurred"],
        "no_opportunity_ids": [item.behavior_id for item in verdicts if item.state == "no_opportunity"],
    }


def render_report(result: BehaviorAuditResult) -> str:
    lines = [
        "# Human Chat Behavior Audit",
        "",
        f"- world: `{result.world_id}`",
        f"- database: `{result.database}`",
        f"- seq window: `{result.seq_window[0]}–{result.seq_window[1]}` (head `{result.max_seq}`)",
        f"- generated: `{result.generated_at}`",
        "",
        "## Summary",
        "",
    ]
    summary = summarize_verdicts(list(result.verdicts))
    lines.append(f"- behaviors audited: **{summary['behavior_count']}**")
    lines.append(
        "- state: "
        + ", ".join(f"{key}={value}" for key, value in summary["by_state"].items())
    )
    lines.append(
        "- classification: "
        + ", ".join(f"{key}={value}" for key, value in summary["by_classification"].items())
    )
    lines.append(
        f"- **already occurred**: {', '.join(summary['occurred_ids']) or '(none)'}"
    )
    lines.append("")
    lines.append("## Per-behavior")
    lines.append("")
    for verdict in result.verdicts:
        lines.append(f"### {verdict.behavior_id} — {verdict.title}")
        lines.append("")
        lines.append(f"- classification: `{verdict.classification}`")
        lines.append(f"- state: **`{verdict.state}`**")
        lines.append(
            f"- counts: occurred **{verdict.occurred_count}** / opportunity **{verdict.opportunity_count}**"
        )
        if verdict.notes:
            lines.append(f"- notes: {verdict.notes}")
        lines.append("- criteria:")
        for key, value in verdict.criteria.items():
            lines.append(f"  - **{key}**: {value}")
        if verdict.instances:
            lines.append("- instances:")
            for inst in verdict.instances[:6]:
                seq = inst.seq if inst.seq is not None else "?"
                lines.append(f"  - seq `{seq}`: {inst.quote[:120]}")
                if inst.context:
                    lines.append(f"    - {inst.context}")
        lines.append("")
    lines.append("## Human-only remainder")
    lines.append("")
    lines.append(
        "- **B35 自嘲**: semantic; ledger cannot label embarrassment/self-mock reliably."
    )
    lines.append(
        "- **B04/B18/B47/B51**: semi-automatic; auto gate finds candidates, human judges naturalness."
    )
    lines.append("")
    return "\n".join(lines)


def write_outputs(output_dir: Path, result: BehaviorAuditResult) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = result.as_dict()
    payload["summary"] = summarize_verdicts(list(result.verdicts))
    (output_dir / "audit.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "REPORT.md").write_text(render_report(result), encoding="utf-8")
