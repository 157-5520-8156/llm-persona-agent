"""Human and machine reports for the context-truth auditor."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Mapping

from .compare import SEVERITY_ORDER
from .slots import FACETS, SLOTS, facet_coverage, known_issue_coverage
from .types import Finding, KNOWN_ISSUE_TITLES, LedgerTruth


def slot_inventory() -> list[dict[str, Any]]:
    return [
        {
            "slot_id": slot.slot_id,
            "facets": list(slot.facets),
            "material_key": slot.material_key,
            "title": slot.title,
            "relation": slot.relation,
            "severity": slot.severity,
            "known_issue": slot.known_issue,
            "known_issue_title": KNOWN_ISSUE_TITLES.get(slot.known_issue or ""),
            "why_it_matters": slot.why_it_matters,
            "notes": slot.notes,
            "how_to_extend": (
                "Copy this SlotSpec in scripts/context_truth/slots.py; "
                "extract_seen reads SeenView, extract_ledger reads LedgerTruth; "
                "add a failing fixture under tests/world_v2/fixtures/context_truth/."
            ),
        }
        for slot in SLOTS
    ]


def summarize(
    findings: list[Finding],
    *,
    truth: LedgerTruth | None = None,
    behavior: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    known_fired = sorted(
        {item.known_issue for item in findings if item.known_issue}
    )
    novel = [item for item in findings if not item.known_issue]
    coverage = known_issue_coverage()
    uncovered = [key for key, slots in coverage.items() if not slots]
    fired_ids = {item.slot_id for item in findings}
    passed = [slot.slot_id for slot in SLOTS if slot.slot_id not in fired_ids]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "slot_count": len(SLOTS),
        "facet_coverage": {key: list(value) for key, value in facet_coverage().items()},
        "known_issue_slot_map": {key: list(value) for key, value in coverage.items()},
        "known_issues_unmapped": uncovered,
        "finding_count": len(findings),
        "by_severity": {
            level: sum(1 for item in findings if item.severity == level)
            for level in SEVERITY_ORDER
        },
        "known_issues_fired": known_fired,
        "novel_finding_count": len(novel),
        "novel_slot_ids": [item.slot_id for item in novel],
        "passed_slot_ids": passed,
        "by_kind": {
            key: sum(1 for item in findings if item.kind == key)
            for key in sorted({item.kind for item in findings})
        },
        "ledger_cursor_seq": None if truth is None else truth.cursor_seq,
        "behavior_mismatch_cases": (
            None if behavior is None else behavior.get("mismatch_cases")
        ),
        "spend_cny": 0,
    }


def render_report(
    *,
    summary: Mapping[str, Any],
    findings: list[Finding],
    truth: LedgerTruth | None,
    behavior: Mapping[str, Any] | None,
    compile_meta: Mapping[str, Any] | None,
) -> str:
    lines = [
        "# Context truth audit",
        "",
        "Path A = production `InnerLifeSnapshot.model_view()`. "
        "Path B = ledger projection of the same facts. "
        "Nothing here patches production.",
        "",
        f"- Slots: **{summary.get('slot_count')}**",
        f"- Findings: **{summary.get('finding_count')}**",
        f"- Novel findings (not K1–K6): **{summary.get('novel_finding_count')}**",
        f"- Known issues fired: {', '.join(summary.get('known_issues_fired') or ('none',))}",
    ]
    if truth is not None:
        lines.append(
            f"- Ledger cursor: seq {truth.cursor_seq} rev {truth.world_revision}"
        )
    if compile_meta:
        lines.append(
            f"- Path A compile: {compile_meta.get('compile_ms')} ms, "
            f"trigger `{compile_meta.get('trigger_ref')}`"
        )
    lines.append(f"- Spend: ¥{summary.get('spend_cny', 0)} (no model calls)")
    lines += ["", "## Known-issue self-check", ""]
    coverage = known_issue_coverage()
    fired = set(summary.get("known_issues_fired") or ())
    for key in ("K1", "K2", "K3", "K4", "K5", "K6"):
        slots = coverage.get(key) or ()
        status = "FIRED" if key in fired else "mapped, not fired on this head"
        lines.append(
            f"- **{key}** {KNOWN_ISSUE_TITLES[key]} — slots: "
            f"{', '.join(f'`{item}`' for item in slots) or 'UNMAPPED'} ({status})"
        )
    lines += ["", "## Coverage honesty", ""]
    lines += [
        "Every InnerLifeSnapshot facet has at least one world-fact slot. "
        "K1–K6 are mapped. Natural-language similarity is not used except for "
        "fixed photo-sent / false-return fragments.",
        "",
        "`monotonic_le` lanes (facts / memory / NPC / experiences / interaction acts) "
        "only fire if she is told **more** than the ledger. Capsule truncation on those "
        "lanes is treated as budget, not a lie. Appraisals, open threads, impressions, "
        "plans, album counts, and dialogue heads use `eq` / `head_included` and **will** "
        "fire when the capsule hides live facts.",
        "",
        "A known issue mapped but not fired means this ledger head currently agrees "
        "on that slot. Historical disagreements are in the behaviour pass.",
        "",
    ]
    if truth is not None and truth.notes.get("sidecar_table_counts"):
        counts = truth.notes["sidecar_table_counts"]
        top = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:8]
        lines.append("Sidecar table counts (K6 Path B):")
        for name, count in top:
            lines.append(f"- `{name}`: {count}")
        lines.append("")
    lines += ["", "## Facet coverage", ""]
    for facet in FACETS:
        slots = facet_coverage().get(facet) or ()
        lines.append(f"- `{facet}`: {len(slots)} slots")
        for slot_id in slots:
            lines.append(f"  - `{slot_id}`")
    lines += ["", "## Findings (severity order)", ""]
    if not findings:
        lines.append("No mechanical mismatches on the slots that ran.")
    for item in findings:
        tag = f" [{item.known_issue}]" if item.known_issue else " [novel]"
        lines += [
            f"### {item.severity.upper()}{tag} `{item.slot_id}`",
            "",
            item.title,
            "",
            f"- Kind: `{item.kind}`",
            f"- She saw: `{_brief(item.seen)}`",
            f"- Ledger: `{_brief(item.ledger)}`",
            f"- Diff: {item.detail}",
            f"- If she acts on this: {item.why_it_matters}",
            "",
        ]
    passed = summary.get("passed_slot_ids") or []
    lines += ["", "## Passed slots on this head", ""]
    for slot_id in passed:
        lines.append(f"- `{slot_id}`")
    lines += ["", "## How to run", ""]
    lines += [
        "```",
        ".venv/bin/python scripts/audit_context_truth.py",
        ".venv/bin/python scripts/audit_context_truth.py --database path/to/clone.sqlite",
        ".venv/bin/python scripts/audit_context_truth.py --ci --fixture tests/world_v2/fixtures/context_truth/ci_mismatch.json",
        ".venv/bin/python scripts/audit_context_truth.py --list-slots",
        "```",
        "",
        "Production `data/` is cloned read-only into `output/context-audit/clone.sqlite`. "
        "The host never writes the production ledger, never talks to QQ, never restarts production.",
        "",
    ]
    lines += ["", "## How to add a slot", ""]
    lines += [
        "1. Open `scripts/context_truth/slots.py` and copy a `SlotSpec`.",
        "2. `extract_seen` reads `SeenView` (already compiled; do not rebuild the snapshot).",
        "3. `extract_ledger` reads `LedgerTruth` (projection + event lookup; do not read snapshot materials).",
        "4. Prefer `eq` / `subset` / `head_included` / `implies_positive` / `approx_seconds` / `monotonic_le`.",
        "5. Add a failing JSON fixture under `tests/world_v2/fixtures/context_truth/`.",
        "6. Re-run `tests/world_v2/test_audit_context_truth.py` — K1–K6 must stay mapped.",
        "7. Never change the code under audit to make this pass.",
        "",
    ]
    if behavior:
        lines += ["## Behaviour-level (decision vs told facts)", ""]
        lines.append(f"- Status: `{behavior.get('status')}`")
        if behavior.get("blocker"):
            lines.append(f"- Blocker: {behavior['blocker']}")
        lines.append(f"- Turns scanned: {behavior.get('turns_scanned')}")
        lines.append(f"- Mismatch cases: {behavior.get('mismatch_cases')}")
        for case in (behavior.get("cases") or [])[:12]:
            lines += [
                "",
                f"### {case.get('severity', '').upper()} `{case.get('inner_turn_id')}`",
                "",
                f"- {case.get('mismatch')}",
                f"- Cursor seq {case.get('cursor_seq')}",
                f"- available_count she was told: {case.get('seen_available_count')}",
                f"- ledger deliveries at cursor: {case.get('ledger_deliveries_at_cursor')}",
                f"- ledger photo candidates opened at cursor: {case.get('ledger_photo_opened_at_cursor')}",
            ]
            texts = case.get("decision_texts") or []
            if texts:
                lines.append(f"- Decision: {texts[0][:160]}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _brief(value: Any, *, limit: int = 180) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def write_outputs(
    output: Path,
    *,
    summary: Mapping[str, Any],
    findings: list[Finding],
    truth: LedgerTruth | None,
    behavior: Mapping[str, Any] | None,
    compile_meta: Mapping[str, Any] | None,
    model_view: Mapping[str, Any] | None = None,
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "slots.json").write_text(
        json.dumps(slot_inventory(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output / "findings.json").write_text(
        json.dumps([item.as_dict() for item in findings], ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    (output / "summary.json").write_text(
        json.dumps(dict(summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if truth is not None:
        from dataclasses import asdict

        (output / "ledger_truth.json").write_text(
            json.dumps(asdict(truth), ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
    if behavior is not None:
        (output / "behavior.json").write_text(
            json.dumps(dict(behavior), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if compile_meta is not None:
        (output / "compile_meta.json").write_text(
            json.dumps(dict(compile_meta), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if model_view is not None:
        (output / "seen_model_view.json").write_text(
            json.dumps(dict(model_view), ensure_ascii=False, indent=2, default=str)
            + "\n",
            encoding="utf-8",
        )
    (output / "REPORT.md").write_text(
        render_report(
            summary=summary,
            findings=findings,
            truth=truth,
            behavior=behavior,
            compile_meta=compile_meta,
        ),
        encoding="utf-8",
    )
    (output / "SLOTS.md").write_text(_render_slots_md(), encoding="utf-8")


def _render_slots_md() -> str:
    lines = [
        "# Context-truth slot inventory",
        "",
        "Every slot is a mechanical comparison: Path A (what she was told) vs "
        "Path B (ledger projection). Natural-language similarity is not used "
        "except for fixed completion/return fragments on K2/K3.",
        "",
    ]
    for facet in FACETS:
        lines += [f"## {facet}", ""]
        for slot in SLOTS:
            if facet not in slot.facets:
                continue
            known = f" — known {slot.known_issue}" if slot.known_issue else ""
            lines += [
                f"### `{slot.slot_id}`{known}",
                "",
                f"- Material: `{slot.material_key}`",
                f"- Relation: `{slot.relation}`",
                f"- Severity if mismatch: `{slot.severity}`",
                f"- Why: {slot.why_it_matters}",
            ]
            if slot.notes:
                lines.append(f"- Notes: {slot.notes}")
            lines.append("")
    lines += [
        "## Adding a slot",
        "",
        "See `SlotSpec` in `scripts/context_truth/slots.py`.",
        "",
    ]
    return "\n".join(lines)
