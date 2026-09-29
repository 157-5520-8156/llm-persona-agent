"""Read-only: the three activated-but-unsettled occurrences and the ref collision."""

from __future__ import annotations

import json
import sqlite3
import sys


DB = "data/companion.epoch2.sqlite"
WORLD = "world:companion-v2:qq-c2c:geoff"


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def main() -> int:
    conn = connect(DB)
    rows = []
    for row in conn.execute(
        "SELECT ledger_sequence, event_json FROM world_v2_events "
        "WHERE world_id = ? ORDER BY ledger_sequence",
        (WORLD,),
    ):
        event = json.loads(row["event_json"])
        event["_seq"] = row["ledger_sequence"]
        event["_payload"] = json.loads(event.get("payload_json") or "{}")
        rows.append(event)

    committed = {}
    activated = {}
    settled = {}
    for event in rows:
        etype = event["event_type"]
        payload = event["_payload"]
        if etype == "WorldOccurrenceCommitted":
            occ = payload.get("occurrence") or payload
            committed[occ.get("occurrence_id")] = event
        elif etype == "WorldOccurrenceActivated":
            activated[payload.get("occurrence_id")] = event
        elif etype == "WorldOccurrenceSettled":
            settled[payload.get("occurrence_id")] = event

    stuck = [oid for oid in activated if oid not in settled]
    print("activated:", len(activated), "settled:", len(settled))
    print("\n=== activated but never settled ===")
    for oid in stuck:
        act = activated[oid]
        com = committed.get(oid)
        print(f"\n  {oid}")
        print(f"    activated seq={act['_seq']} at={act.get('logical_time')}")
        if com is None:
            print("    NO WorldOccurrenceCommitted found")
            continue
        payload = com["_payload"]
        print(f"    committed seq={com['_seq']} keys={sorted(payload)}")
        occ = payload.get("occurrence") or {}
        print(f"    trigger_ref={occ.get('trigger_ref')} status={occ.get('status')}")
        for cand in payload.get("candidate_contents") or []:
            print(
                f"      result_payload_ref  = {cand.get('result_payload_ref')}\n"
                f"      result_payload_hash = {cand.get('result_payload_hash')}\n"
                f"      content_ref         = {cand.get('content_ref')}\n"
                f"      content_hash        = {cand.get('content_payload_hash')}"
            )

    print("\n=== life content rows for those refs ===")
    for oid in stuck:
        com = committed.get(oid)
        if com is None:
            continue
        for cand in com["_payload"].get("candidate_contents") or []:
            for ref in (cand.get("result_payload_ref"), cand.get("content_ref")):
                if not ref:
                    continue
                row = conn.execute(
                    "SELECT content_kind, content_payload_hash, text FROM world_v2_life_content "
                    "WHERE world_id = ? AND content_ref = ?",
                    (WORLD, ref),
                ).fetchone()
                if row is None:
                    print(f"  {ref}\n    -> ABSENT")
                else:
                    print(
                        f"  {ref}\n    -> kind={row['content_kind']} "
                        f"hash={row['content_payload_hash']}\n"
                        f"       text={row['text'][:160]!r}"
                    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
