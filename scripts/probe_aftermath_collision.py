"""Read-only probe: which life-content ref collides in the stuck aftermath cell.

Opens the live production ledger with ``?mode=ro`` and never writes. Reports the
open ``occurrence:life-aftermath:*`` heads, each candidate's content bindings and
the exact rows already present in ``world_v2_life_content`` for those refs.
"""

from __future__ import annotations

import hashlib
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


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    conn = connect(DB)
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    ]
    print("tables with life content:", [t for t in tables if "life" in t or "content" in t])

    cols = [row[1] for row in conn.execute("PRAGMA table_info(world_v2_events)")]
    print("event cols:", cols)

    kinds = Counter(
        row[0]
        for row in conn.execute(
            "SELECT content_kind FROM world_v2_life_content WHERE world_id = ?", (WORLD,)
        )
    )
    print("content kinds:", dict(kinds))

    print("\n=== occurrence-result / life-development:result rows ===")
    for row in conn.execute(
        """
        SELECT content_ref, content_kind, content_payload_hash, length(text) AS n
        FROM world_v2_life_content
        WHERE world_id = ?
          AND (content_ref LIKE 'content:occurrence-result:%'
               OR content_ref LIKE 'content:life-development:result:%')
        ORDER BY content_ref
        """,
        (WORLD,),
    ):
        print(f"  {row['content_ref']}  kind={row['content_kind']} hash={row['content_payload_hash'][:12]} len={row['n']}")

    print("\n=== occurrence / life-content events ===")
    rows = list(
        conn.execute(
            """
            SELECT ledger_sequence, event_id, event_json
            FROM world_v2_events
            WHERE world_id = ?
              AND (event_id LIKE '%life-aftermath%'
                   OR event_id LIKE '%life-content%'
                   OR event_id LIKE '%life-development%')
            ORDER BY ledger_sequence
            """,
            (WORLD,),
        )
    )
    for row in rows:
        event = json.loads(row["event_json"])
        etype = event.get("event_type")
        if etype not in {
            "WorldOccurrenceOpened",
            "WorldOccurrenceCommitted",
            "WorldOccurrenceSettled",
            "LifeContentRecorded",
            "OccurrenceContentCommitted",
        }:
            continue
        payload = event.get("payload") or {}
        extra = ""
        if etype == "WorldOccurrenceSettled":
            extra = f" result_payload_ref={payload.get('result_payload_ref')}"
        if etype == "LifeContentRecorded":
            extra = f" ref={payload.get('content_ref')} kind={payload.get('content_kind')}"
        print(f"  {row['ledger_sequence']:>6} {etype:<28} {row['event_id']}{extra}")

    print("\n=== candidate contents committed per aftermath occurrence ===")
    for row in rows:
        event = json.loads(row["event_json"])
        if event.get("event_type") != "OccurrenceContentCommitted":
            continue
        payload = event.get("payload") or {}
        occ = payload.get("occurrence", {})
        print(f"\n  seq {row['ledger_sequence']} occurrence={occ.get('occurrence_id')}")
        for cand in payload.get("candidate_contents", []) or []:
            print(
                f"    result_payload_ref={cand.get('result_payload_ref')}\n"
                f"      result_payload_hash={cand.get('result_payload_hash')}\n"
                f"      content_ref={cand.get('content_ref')}\n"
                f"      content_payload_hash={cand.get('content_payload_hash')}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
