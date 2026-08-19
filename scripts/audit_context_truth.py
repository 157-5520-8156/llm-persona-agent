#!/usr/bin/env python3
"""Audit context truth: what she sees vs what the ledger recorded.

Clones the production ledger read-only (never writes ``data/``, never talks to
QQ, never restarts production). Path A is the production InnerLifeSnapshot
compile. Path B is the ledger projection of the same facts.

Usage::

    .venv/bin/python scripts/audit_context_truth.py
    .venv/bin/python scripts/audit_context_truth.py --database path/to/clone.sqlite
    .venv/bin/python scripts/audit_context_truth.py --ci --fixture tests/world_v2/fixtures/context_truth/ci_mismatch.json
    .venv/bin/python scripts/audit_context_truth.py --list-slots

Add a slot in ``scripts/context_truth/slots.py``.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

from context_truth.compare import run_slots
from context_truth.report import slot_inventory, summarize, write_outputs
from context_truth.slots import known_issue_coverage
from context_truth.types import LedgerTruth, SeenView

PRODUCTION_DB = (REPO / "data" / "companion.epoch2.sqlite").resolve()
DEFAULT_OUTPUT = (REPO / "output" / "context-audit").resolve()
WORLD_ID = "world:companion-v2:qq-c2c:geoff"
ACTOR_REF = "agent:companion"
COUNTERPART_REF = "user:geoff"
TIMEZONE = "Asia/Shanghai"
SEED_PATH = (REPO / "configs" / "world_seed.yaml").resolve()
SIDECAR = (REPO / "data" / "external-world-perception.sqlite").resolve()


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
    if _is_production_path(target):
        raise SystemExit(f"refusing to clone onto production path: {target}")
    source = source.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(target) + suffix).unlink(missing_ok=True)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)


def load_fixture(path: Path) -> tuple[SeenView, LedgerTruth]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit("fixture must be a JSON object")
    seen_raw = raw.get("seen") if isinstance(raw.get("seen"), dict) else raw.get("seen_materials")
    ledger_raw = raw.get("ledger")
    if not isinstance(seen_raw, dict) or not isinstance(ledger_raw, dict):
        raise SystemExit("fixture needs 'seen' and 'ledger' objects")
    return SeenView.from_mapping(seen_raw), LedgerTruth.from_mapping(ledger_raw)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=PRODUCTION_DB)
    parser.add_argument("--world-id", default=WORLD_ID)
    parser.add_argument("--actor-ref", default=ACTOR_REF)
    parser.add_argument("--counterpart-ref", default=COUNTERPART_REF)
    parser.add_argument("--timezone", default=TIMEZONE)
    parser.add_argument("--seed", type=Path, default=SEED_PATH)
    parser.add_argument("--sidecar", type=Path, default=SIDECAR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--fixture", type=Path, default=None)
    parser.add_argument("--ci", action="store_true", help="non-zero exit if any mismatch")
    parser.add_argument("--list-slots", action="store_true")
    parser.add_argument("--skip-behavior", action="store_true")
    parser.add_argument("--behavior-limit", type=int, default=48)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.list_slots:
        print(json.dumps(slot_inventory(), ensure_ascii=False, indent=2))
        coverage = known_issue_coverage()
        missing = [key for key, slots in coverage.items() if not slots]
        if missing:
            print(f"unmapped known issues: {missing}", file=sys.stderr)
            return 2
        return 0

    output = assert_output_is_safe(args.out)
    compile_meta: dict[str, Any] | None = None
    model_view = None
    truth: LedgerTruth | None = None
    behavior = None
    compiled = None

    if args.fixture is not None:
        seen, truth = load_fixture(args.fixture)
        compile_meta = {"mode": "fixture", "fixture": str(args.fixture)}
    else:
        source = args.database.expanduser().resolve()
        if not source.is_file():
            raise SystemExit(f"ledger not found: {source}")
        work = source
        if _is_production_path(source):
            work = output / "clone.sqlite"
            print(f"cloning {source} -> {work} (read-only backup)", file=sys.stderr)
            clone_ledger(source, work)
        from context_truth.compile_seen import compile_seen_at_head_sync
        from context_truth.ledger_truth import collect_ledger_truth

        print("compiling Path A via production InnerLifeSnapshot…", file=sys.stderr)
        compiled = compile_seen_at_head_sync(
            database=work,
            world_id=args.world_id,
            actor_ref=args.actor_ref,
            counterpart_actor_ref=args.counterpart_ref,
            timezone_name=args.timezone,
            seed_path=args.seed if args.seed.is_file() else args.seed,
        )
        seen = compiled.seen
        model_view = compiled.model_view
        sidecar = args.sidecar if args.sidecar.is_file() else None
        truth = collect_ledger_truth(
            ledger=compiled.ledger,
            projection=compiled.projection,
            counterpart_actor_ref=args.counterpart_ref,
            sidecar_path=sidecar,
        )
        compile_meta = {
            "mode": "production_compile",
            "database": str(work),
            "trigger_ref": compiled.trigger_ref,
            "compile_ms": round(compiled.compile_ms, 1),
            "cursor": compiled.cursor.model_dump(mode="json"),
            "snapshot_id": seen.snapshot_id,
        }
        if not args.skip_behavior:
            from context_truth.behavior import audit_behavior_file

            behavior = audit_behavior_file(
                work, world_id=args.world_id, limit=args.behavior_limit
            )

    findings = run_slots(seen, truth)
    summary = summarize(findings, truth=truth, behavior=behavior)
    write_outputs(
        output,
        summary=summary,
        findings=findings,
        truth=truth,
        behavior=behavior,
        compile_meta=compile_meta,
        model_view=model_view,
    )
    if compiled is not None:
        for store in compiled.stores_to_close:
            closer = getattr(store, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass

    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    print(f"wrote {output / 'REPORT.md'}", file=sys.stderr)
    if args.ci and findings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
