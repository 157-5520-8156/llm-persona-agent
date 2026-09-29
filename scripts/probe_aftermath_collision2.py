"""Read-only: find the active aftermath occurrence and the exact colliding ref."""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter


DB = "data/companion.epoch2.sqlite"
WORLD = "world:companion-v2:qq-c2c:geoff"


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def main() -> int:
    conn = connect(DB)
    rows = list(
        conn.execute(
            "SELECT ledger_sequence, event_id, event_json FROM world_v2_events "
            "WHERE world_id = ? ORDER BY ledger_sequence",
            (WORLD,),
        )
    )
    print("total events:", len(rows), "head seq:", rows[-1]["ledger_sequence"])

    types = Counter(json.loads(r["event_json"]).get("event_type") for r in rows)
    occurrence_types = {k: v for k, v in types.items() if "Occurrence" in k or "LifeContent" in k}
    print("occurrence/life-content event types:", occurrence_types)

    sample = next(
        r for r in rows if json.loads(r["event_json"]).get("event_type") == "LifeContentRecorded"
    )
    print("\n=== sample LifeContentRecorded shape ===")
    print(json.dumps(json.loads(sample["event_json"]), ensure_ascii=False, indent=2)[:2500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
