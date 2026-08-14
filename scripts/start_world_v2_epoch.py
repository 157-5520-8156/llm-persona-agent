#!/usr/bin/env python
"""Archive a World v2 sqlite file and start a new epoch from a continuity snapshot.

Usage (run from the repository root; does not mutate the archive):

    .venv/bin/python scripts/start_world_v2_epoch.py \\
        --source data/companion.sqlite \\
        --archive data/companion.epoch1.sqlite \\
        --target data/companion.epoch2.sqlite \\
        --world-id world:companion-v2:qq-c2c:geoff \\
        --epoch-id epoch:2
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from companion_daemon.world_v2.epoch_continuity import compile_continuity_snapshot
from companion_daemon.world_v2.epoch_genesis import archive_sqlite_file, write_epoch_ledger
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--world-id", required=True)
    parser.add_argument("--epoch-id", default="epoch:2")
    args = parser.parse_args()
    if args.target.exists():
        raise SystemExit(f"target already exists: {args.target}")
    if args.archive.exists():
        print(f"archive exists, skipping copy: {args.archive}")
    else:
        archive_sqlite_file(source=args.source, destination=args.archive)
    source = SQLiteWorldLedger(path=args.source, world_id=args.world_id)
    try:
        snapshot = compile_continuity_snapshot(source.project(), epoch_id=args.epoch_id)
    finally:
        source.close()
    ledger = write_epoch_ledger(
        path=args.target,
        world_id=args.world_id,
        now=datetime.now(tz=UTC),
        snapshot=snapshot,
    )
    ledger.close()
    print(f"archive={args.archive}")
    print(f"target={args.target}")
    print(f"epoch_id={snapshot.epoch_id}")
    print(f"facts={len(snapshot.facts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
