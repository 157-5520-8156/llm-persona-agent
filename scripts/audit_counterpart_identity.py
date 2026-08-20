#!/usr/bin/env python3
"""Audit epoch-migrated counterpart facts and compile seen before/after fixes."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from companion_daemon.world_v2.ledger_context_resolver import fact_recall_items
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger

from scripts.context_truth.compile_seen import compile_seen_at_head_sync


WORLD = "world:companion-v2:qq-c2c:geoff"
USER = "user:geoff"
OUTPUT = ROOT / "output" / "counterpart-identity"


@dataclass(frozen=True)
class ArchiveLedgerReader:
    _database_path: Path
    _world_id: str


def _audit_gaps(ledger: SQLiteWorldLedger, archive: ArchiveLedgerReader | None, user: str) -> dict:
    projection = ledger.project()
    user_facts = [
        item
        for item in projection.facts
        if item.values.subject_ref == user and item.values.status == "active"
    ]
    recalls = fact_recall_items(
        ledger=ledger,
        projection=projection,
        facts=tuple(user_facts),
        archive_ledger=archive,
    )
    recalled_ids = {item.fact_id for item in recalls}
    gaps = []
    for fact in user_facts:
        if fact.fact_id in recalled_ids:
            continue
        gaps.append(
            {
                "fact_id": fact.fact_id,
                "predicate_code": fact.values.predicate_code,
                "transition_id": fact.origin.transition_id,
            }
        )
    by_predicate = Counter(item["predicate_code"] for item in gaps)
    display_name = next(
        (item for item in recalls if item.predicate_code == "profile.display_name"),
        None,
    )
    return {
        "active_user_facts": len(user_facts),
        "recalled_user_facts": len(recalls),
        "missing_recall": len(gaps),
        "missing_by_predicate": dict(sorted(by_predicate.items())),
        "display_name_recall": None
        if display_name is None
        else {
            "source_excerpt": display_name.source_excerpt,
            "accepted_fact_event_ref": display_name.accepted_fact_event_ref,
        },
        "recalled_predicates": sorted({item.predicate_code for item in recalls}),
    }


def _relevant_fact_excerpts(compiled) -> list[dict]:
    facts = compiled.materials.get("relevant_facts") or []
    output = []
    for item in facts:
        if not isinstance(item, dict):
            continue
        output.append(
            {
                "predicate_code": item.get("predicate_code"),
                "source_excerpt": item.get("source_excerpt"),
                "subject_ref": item.get("subject_ref"),
            }
        )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=ROOT / "data" / "companion.epoch2.sqlite")
    parser.add_argument("--archive", type=Path, default=ROOT / "data" / "companion.epoch1.sqlite")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    clone = args.output / "clone-readonly.sqlite"
    if clone.exists():
        clone.unlink()
    shutil.copy2(args.database, clone)

    live = SQLiteWorldLedger(path=args.database, world_id=WORLD)
    archive = (
        ArchiveLedgerReader(_database_path=args.archive.resolve(), _world_id=WORLD)
        if args.archive.is_file()
        else None
    )
    try:
        without_archive = _audit_gaps(live, None, USER)
        with_archive = _audit_gaps(live, archive, USER)
    finally:
        live.close()

    compiled = compile_seen_at_head_sync(
        database=clone,
        world_id=WORLD,
        actor_ref="agent:companion",
        counterpart_actor_ref=USER,
        timezone_name="Asia/Shanghai",
        seed_path=ROOT / "configs" / "world_seed.yaml",
        archive_database=args.archive if args.archive.is_file() else None,
    )
    for store in compiled.stores_to_close:
        close = getattr(store, "close", None)
        if callable(close):
            close()

    payload = {
        "without_archive": without_archive,
        "with_archive": with_archive,
        "compiled_relevant_facts": _relevant_fact_excerpts(compiled),
        "compiled_head_seq": compiled.cursor.ledger_sequence,
    }
    (args.output / "audit.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (args.output / "seen-at-head.json").write_text(
        json.dumps(compiled.model_view, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
