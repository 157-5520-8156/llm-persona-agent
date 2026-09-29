#!/usr/bin/env python3
"""Audit human-chat behavior coverage items against a World V2 ledger.

Read-only: clones production ``data/`` ledger, never writes ``data/``, never
talks to QQ. Targets the 12 🟡 items flagged as "mechanism exists, missing
production metric".

Usage::

    .venv/bin/python scripts/audit_human_chat_behavior.py
    .venv/bin/python scripts/audit_human_chat_behavior.py --database path/to/clone.sqlite
    .venv/bin/python scripts/audit_human_chat_behavior.py --seq-from 6200
    .venv/bin/python scripts/audit_human_chat_behavior.py --update-coverage
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

from behavior_audit.ledger import LedgerIndex  # noqa: E402
from behavior_audit.report import render_report, summarize_verdicts, write_outputs  # noqa: E402
from behavior_audit.rules import TARGET_IDS, run_audits  # noqa: E402
from behavior_audit.types import BehaviorAuditResult, BehaviorVerdict  # noqa: E402

PRODUCTION_DB = (REPO / "data" / "companion.epoch2.sqlite").resolve()
DEFAULT_OUTPUT = (REPO / "output" / "behavior-audit").resolve()
COVERAGE_DOC = (REPO / "docs" / "design" / "human-chat-behavior-coverage.md").resolve()
COVERAGE_JSON = (REPO / "output" / "behavior-coverage" / "coverage.json").resolve()
WORLD_ID = "world:companion-v2:qq-c2c:geoff"
DEFAULT_SEQ_FROM = 0
DOC_AUDIT_WINDOW_FROM = 6200


def _is_production_path(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    data = (REPO / "data").resolve()
    try:
        resolved.relative_to(data)
    except ValueError:
        return False
    return True


def assert_output_is_safe(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if _is_production_path(resolved):
        raise SystemExit(f"refusing to write under data/: {resolved}")
    return resolved


def clone_ledger(source: Path, target: Path) -> None:
    target = assert_output_is_safe(target)
    source = source.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(target) + suffix).unlink(missing_ok=True)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)


def suggested_coverage_status(verdict: BehaviorVerdict, *, doc_window_from: int = DOC_AUDIT_WINDOW_FROM) -> str:
    if verdict.behavior_id in {"B35", "B04", "B10", "B14", "B19", "B47", "B51"}:
        return "🟡"
    if verdict.state != "occurred":
        return "🟡"
    if verdict.classification == "human_only":
        return "🟡"
    if verdict.behavior_id == "B18":
        recent = [inst for inst in verdict.instances if inst.seq is not None and inst.seq >= doc_window_from]
        return "✅" if recent else "🟡"
    if verdict.occurred_count >= 1:
        return "✅"
    return "🟡"


def format_evidence(verdict: BehaviorVerdict, *, doc_window_from: int = DOC_AUDIT_WINDOW_FROM) -> str:
    inst = verdict.instances[0] if verdict.instances else None
    status = suggested_coverage_status(verdict, doc_window_from=doc_window_from)
    window_note = ""
    if verdict.behavior_id == "B18" and verdict.instances:
        recent = [i for i in verdict.instances if i.seq is not None and i.seq >= doc_window_from]
        if not recent and verdict.instances:
            window_note = f"主审计窗 seq>={doc_window_from} 仍无实例；更早 "
    if inst is None:
        return (
            f"{'✅' if status == '✅' else '🟡'} 审计：{verdict.state}；"
            f"机会 {verdict.opportunity_count}、发生 {verdict.occurred_count}；{verdict.notes}"
        )
    seq_part = f"seq {inst.seq}" if inst.seq is not None else "ledger"
    quote = inst.quote.replace("\n", " ")[:60]
    prefix = "✅ 生产" if status == "✅" else "🟡"
    return (
        f"{prefix} {window_note}{seq_part} 原话「{quote}」；"
        f"审计 {verdict.occurred_count}/{verdict.opportunity_count}；{verdict.notes}"
    )


def load_coverage_json() -> dict[str, Any]:
    if COVERAGE_JSON.is_file():
        return json.loads(COVERAGE_JSON.read_text(encoding="utf-8"))
    return {
        "version": 1,
        "source_doc": str(COVERAGE_DOC.relative_to(REPO)),
        "behaviors": {},
    }


def update_coverage_files(verdicts: list[BehaviorVerdict]) -> dict[str, str]:
    coverage = load_coverage_json()
    behaviors = coverage.setdefault("behaviors", {})
    status_map: dict[str, str] = {}
    for verdict in verdicts:
        status = suggested_coverage_status(verdict, doc_window_from=DOC_AUDIT_WINDOW_FROM)
        status_map[verdict.behavior_id] = status
        behaviors[verdict.behavior_id] = {
            "status": status,
            "audit_state": verdict.state,
            "classification": verdict.classification,
            "occurred_count": verdict.occurred_count,
            "opportunity_count": verdict.opportunity_count,
            "evidence": format_evidence(verdict, doc_window_from=DOC_AUDIT_WINDOW_FROM),
            "instances": [item.as_dict() for item in verdict.instances[:6]],
        }
    coverage["updated_at"] = datetime.now(tz=UTC).isoformat()
    coverage["audit_summary"] = summarize_verdicts(verdicts)
    COVERAGE_JSON.parent.mkdir(parents=True, exist_ok=True)
    COVERAGE_JSON.write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if not COVERAGE_DOC.is_file():
        return status_map

    text = COVERAGE_DOC.read_text(encoding="utf-8")
    for verdict in verdicts:
        marker = f"| {verdict.behavior_id} |"
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if not line.startswith(marker):
                continue
            parts = line.split("|")
            if len(parts) < 8:
                continue
            parts[7] = f" {format_evidence(verdict, doc_window_from=DOC_AUDIT_WINDOW_FROM)} "
            lines[index] = "|".join(parts)
            break
        text = "\n".join(lines)
    COVERAGE_DOC.write_text(text + "\n", encoding="utf-8")
    return status_map


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=PRODUCTION_DB)
    parser.add_argument("--world-id", default=WORLD_ID)
    parser.add_argument("--seq-from", type=int, default=DEFAULT_SEQ_FROM)
    parser.add_argument("--seq-to", type=int, default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--behavior", action="append", default=[], help="limit to one ID, repeatable")
    parser.add_argument("--update-coverage", action="store_true")
    parser.add_argument("--json", action="store_true", help="print audit.json to stdout")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output = assert_output_is_safe(args.out)
    source = args.database.expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"ledger not found: {source}")

    work = source
    if _is_production_path(source):
        work = output / "clone.sqlite"
        print(f"cloning {source} -> {work} (read-only)", file=sys.stderr)
        clone_ledger(source, work)

    conn = sqlite3.connect(f"file:{work}?mode=ro", uri=True)
    try:
        index = LedgerIndex.load(
            conn,
            world_id=args.world_id,
            seq_from=args.seq_from,
            seq_to=args.seq_to,
        )
    finally:
        conn.close()

    behavior_ids = tuple(args.behavior) if args.behavior else TARGET_IDS
    verdicts = run_audits(index, behavior_ids=behavior_ids)
    result = BehaviorAuditResult(
        world_id=args.world_id,
        database=str(work),
        max_seq=index.max_seq,
        seq_window=(args.seq_from, args.seq_to or index.max_seq),
        verdicts=tuple(verdicts),
        generated_at=datetime.now(tz=UTC).isoformat(),
    )
    write_outputs(output, result)
    if args.update_coverage:
        status_map = update_coverage_files(verdicts)
        print(f"updated {COVERAGE_DOC} and {COVERAGE_JSON}", file=sys.stderr)
    else:
        status_map = {item.behavior_id: suggested_coverage_status(item, doc_window_from=DOC_AUDIT_WINDOW_FROM) for item in verdicts}

    summary = summarize_verdicts(verdicts)
    print(json.dumps({"summary": summary, "suggested_status": status_map}, ensure_ascii=False, indent=2))
    print(f"wrote {output / 'REPORT.md'}", file=sys.stderr)
    if args.json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
