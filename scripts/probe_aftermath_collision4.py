"""Read-only: full payload of the first stuck open-world occurrence + ref state."""

from __future__ import annotations

import json
import sqlite3
import sys


DB = "data/companion.epoch2.sqlite"
WORLD = "world:companion-v2:qq-c2c:geoff"
STUCK = "occurrence:open-world:8f2e8268e980e102c78fe576785a3a654e54c15c4ee61899781e6b21916b3ef3"


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def main() -> int:
    conn = connect(DB)
    print("=== every event mentioning the stuck occurrence ===")
    refs: set[str] = set()
    for row in conn.execute(
        "SELECT ledger_sequence, event_json FROM world_v2_events "
        "WHERE world_id = ? AND event_json LIKE ? ORDER BY ledger_sequence",
        (WORLD, f"%{STUCK}%"),
    ):
        event = json.loads(row["event_json"])
        payload = json.loads(event.get("payload_json") or "{}")
        print(f"\n--- seq {row['ledger_sequence']} {event['event_type']} {event['event_id']}")
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        print(text[:4000])
        for token in json.dumps(payload).split('"'):
            if token.startswith("content:"):
                refs.add(token)

    print("\n\n=== content refs seen, and their store rows ===")
    for ref in sorted(refs):
        row = conn.execute(
            "SELECT content_kind, content_payload_hash, text FROM world_v2_life_content "
            "WHERE world_id = ? AND content_ref = ?",
            (WORLD, ref),
        ).fetchone()
        if row is None:
            print(f"  {ref}\n    -> ABSENT from store")
        else:
            print(
                f"  {ref}\n    -> kind={row['content_kind']} hash={row['content_payload_hash']}\n"
                f"       text={row['text']!r}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
