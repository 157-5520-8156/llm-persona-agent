"""Install a reproduced corpus into an explicit isolated World copy, resumably.

Uses the same public archive admission and direct initialization primitives as
install_compiled. Small batches expose progress and preserve partial recovery.
No model/provider, dialogue turn, external delivery or production path is opened.
"""

from __future__ import annotations
import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from companion_daemon.world_v2.character_prehistory import CompiledPrehistoryArchive, digest
from companion_daemon.world_v2.character_prehistory_runtime import PrehistoryArchiveRuntime
from companion_daemon.world_v2.prehistory_memory_initialization import PrehistoryMemoryInitializer
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    path.chmod(0o600)


def install(*, packages: Path, source: Path, database: Path, report: Path):
    source, database = source.resolve(), database.resolve()
    if source == database or not database.is_relative_to((Path.cwd() / "output").resolve()):
        raise ValueError("install only into a distinct World under this worktree output/")
    report.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest = json.loads((packages / "manifest.json").read_text())
    book_hash = hashlib.sha256((packages / "manifest.json").read_bytes()).hexdigest()
    marker = report / "baseline.json"
    if not database.exists():
        if marker.exists():
            raise ValueError("baseline exists but its database is missing")
        database.parent.mkdir(parents=True, exist_ok=True)
        with (
            sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as old,
            sqlite3.connect(database) as new,
        ):
            old.backup(new)
        database.chmod(0o600)
    elif not marker.exists():
        raise ValueError("existing database has no matching installation baseline")
    first = CompiledPrehistoryArchive.model_validate_json(
        (packages / manifest["groups"][0]["group"] / "compiled-package.json").read_text()
    )
    ledger = SQLiteWorldLedger(path=database, world_id=first.document.world_id)
    started = time.monotonic()
    try:
        initial = ledger.project()
        if marker.exists():
            baseline = json.loads(marker.read_text())
            if baseline["book_hash"] != book_hash or baseline["database"] != str(database):
                raise ValueError("resume differs from frozen source or database")
        else:
            baseline = {
                "book_hash": book_hash,
                "database": str(database),
                "source": str(source),
                "records": {r.record.record_id: digest(r) for r in initial.prehistory_records},
                "candidates": {c.candidate_id: digest(c) for c in initial.memory_candidates},
                "cursor": initial.ledger_sequence,
                "world_hash": initial.semantic_hash,
            }
            write(marker, baseline)
        runtime = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=first.document.actor_ref)
        initializer = PrehistoryMemoryInitializer(
            ledger=ledger, owner_actor_ref=first.document.actor_ref
        )
        progress = []
        new_ids = set()
        for group in manifest["groups"]:
            phase = time.monotonic()
            package = CompiledPrehistoryArchive.model_validate_json(
                (packages / group["group"] / "compiled-package.json").read_text()
            )
            if digest(package.document.manifest()) != group["manifest_hash"]:
                raise ValueError("package differs from its frozen book manifest")
            rows = runtime.import_compiled(
                package, created_at=max(datetime.now(UTC), package.compilation.source_authored_at)
            )
            record_refs = tuple(r.record.record_id for r in rows)
            new_ids.update(record_refs)
            print(
                json.dumps(
                    {"group": group["group"], "status": "archive_admitted", "records": len(rows)},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            initialized = 0
            while True:
                result = initializer.advance_once(limit=32, record_refs=record_refs)
                initialized += result["initialized"]
                print(
                    json.dumps(
                        {
                            "group": group["group"],
                            "status": result["status"],
                            "newly_initialized_this_run": initialized,
                            "existing_active": result["existing_active"],
                            "existing_inactive": result["existing_inactive"],
                            "seconds": round(time.monotonic() - phase, 2),
                            "model_calls": result["model_calls"],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                if result["existing_pending"]:
                    raise ValueError(
                        "legacy unfinished choice must recover through its original workflow"
                    )
                if not result["initialized"]:
                    break
            progress.append(
                {
                    "group": group["group"],
                    "records": len(rows),
                    "initialized_this_run": initialized,
                    "existing_inactive": result["existing_inactive"],
                    "seconds": time.monotonic() - phase,
                }
            )
            write(report / "progress.json", progress)
        after = ledger.project()
        assert baseline["records"].keys() <= {r.record.record_id for r in after.prehistory_records}
        assert baseline["candidates"].keys() <= {c.candidate_id for c in after.memory_candidates}
        assert all(
            digest(r) == baseline["records"][r.record.record_id]
            for r in after.prehistory_records
            if r.record.record_id in baseline["records"]
        )
        assert all(
            digest(c) == baseline["candidates"][c.candidate_id]
            for c in after.memory_candidates
            if c.candidate_id in baseline["candidates"]
        )
        assert len(
            set(r.record.record_id for r in after.prehistory_records) - baseline["records"].keys()
        ) == len(new_ids)
        sequence = after.ledger_sequence
        # Repeat the user-facing installer, not only the lower-level admission.
        for group in manifest["groups"]:
            package = CompiledPrehistoryArchive.model_validate_json(
                (packages / group["group"] / "compiled-package.json").read_text()
            )
            repeated = runtime.install_compiled(package, created_at=datetime.now(UTC))
            assert repeated.memory_initialization["initialized"] == 0
        assert ledger.project().ledger_sequence == sequence
        source_candidates = {
            b.source_id: c
            for c in after.memory_candidates
            for b in c.values.source_bindings
            if b.source_kind == "prehistory"
        }
        active = sum(source_candidates[ref].values.status == "active" for ref in new_ids)
        result = {
            "contract": "dense-life-installation.1",
            "book_hash": book_hash,
            "database": str(database),
            "baseline_records_preserved": len(baseline["records"]),
            "baseline_candidates_preserved": len(baseline["candidates"]),
            "new_records": len(new_ids),
            "new_active": active,
            "new_inactive_preserved": len(new_ids) - active,
            "total_records": len(after.prehistory_records),
            "total_candidates": len(after.memory_candidates),
            "exact_reinstallation_inert": True,
            "before_cursor": baseline["cursor"],
            "after_cursor": sequence,
            "semantic_hash": after.semantic_hash,
            "groups": progress,
            "seconds": time.monotonic() - started,
            "model_calls": 0,
            "production_writes": 0,
            "automatic_retelling": "not_evaluated",
        }
        write(report / "installation.json", result)
        print(
            json.dumps({k: v for k, v in result.items() if k != "groups"}, ensure_ascii=False),
            flush=True,
        )
        return result
    finally:
        ledger.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("packages", "source", "database", "report"):
        parser.add_argument("--" + flag, type=Path, required=True)
    args = parser.parse_args()
    install(**vars(args))
