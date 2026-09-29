"""Read-only life-chain audit: facts, authored responses and delivery stay separate.

Exact repetition counts are diagnostics, never a topic classifier or production
behavior policy. Unsettled candidates and dialogue are not promoted to events.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3


def audit(database: Path, *, after_sequence: int = 0) -> dict:
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        worlds = [r[0] for r in conn.execute("select distinct world_id from world_v2_events")]
        if len(worlds) != 1:
            raise ValueError("audit requires an isolated single-world ledger")
        world = worlds[0]
        events = [(seq, json.loads(raw)) for seq, raw in conn.execute(
            "select ledger_sequence,event_json from world_v2_events where world_id=? order by ledger_sequence", (world,),
        )]
        content = {ref: (digest, text) for ref, digest, text in conn.execute(
            "select content_ref,content_payload_hash,text from world_v2_life_content where world_id=?", (world,),
        )}
        journals = [json.loads(r[0]) for r in conn.execute(
            "select body from world_v2_day_open_opportunities where world_id=? order by day_key", (world,),
        )]
    timeline, environment, responses = [], [], []
    pending_windows, skipped_windows = {}, []
    counts = Counter()
    for seq, event in events:
        if seq <= after_sequence:
            continue
        kind = event["event_type"]
        counts[kind] += 1
        payload = json.loads(event["payload_json"])
        row = {"sequence": seq, "event_ref": event["event_id"], "kind": kind, "at": event["logical_time"]}
        if kind == "ActivityPlanned":
            plan = payload["plan"]
            if plan.get("scheduled_window"):
                pending_windows[plan["plan_id"]] = plan["scheduled_window"]
        elif kind in {"ActivityStarted", "ActivityCancelled", "ActivityAbandoned"}:
            pending_windows.pop(payload.get("plan_id"), None)
        elif kind == "ClockAdvanced":
            start = datetime.fromisoformat(payload["logical_time_from"])
            end = datetime.fromisoformat(payload["logical_time_to"])
            for plan_id, window in tuple(pending_windows.items()):
                if start <= datetime.fromisoformat(window["opens_at"]) < datetime.fromisoformat(window["closes_at"]) <= end:
                    skipped_windows.append({"plan_id": plan_id, "clock_event_ref": event["event_id"],
                                            "window": window, "jump_from": start.isoformat(), "jump_to": end.isoformat()})
                    pending_windows.pop(plan_id)
        if kind == "WorldOccurrenceSettled":
            ref = payload.get("result_payload_ref")
            stored = content.get(ref)
            row.update(occurrence_id=payload["occurrence_id"], content_ref=ref)
            verified = stored is not None and stored[0] == payload.get("result_payload_hash") and hashlib.sha256(stored[1].encode()).hexdigest() == stored[0]
            row["content_verified"] = verified
            if verified:
                try:
                    value = json.loads(stored[1])
                except ValueError:
                    value = {"legacy_text": stored[1]}
                row["settled_content"] = value
                environment.append(stored[1])
        elif kind == "CharacterLifeResponseRecorded":
            response = payload.get("response", payload)
            row["character_response"] = response
            if isinstance(response, dict) and isinstance(response.get("response_text"), str):
                responses.append(response["response_text"])
        elif kind in {"ActivityPlanned", "ActivityStarted", "ActivityResumed", "ActivityCompleted", "ActivityPaused", "ActivityCancelled"}:
            row["plan_id"] = payload.get("plan_id") or payload.get("plan", {}).get("plan_id")
        else:
            continue
        timeline.append(row)
    openings = []
    for journal in journals:
        attempts = journal["attempts"]
        # Retain original history for continuity, but label its evaluated pin.
        first = attempts[0]["opportunity"]
        openings.append({"day_key": journal["day_key"], "at": first["logical_time"],
                         "evaluated_sequence": first["cursor"]["ledger_sequence"],
                         "terminal_reason": journal.get("terminal_reason"),
                         "reconsider_at": journal.get("reconsider_at"),
                         "reconsideration_ref": journal.get("reconsideration_ref"),
                         "completion_source": journal.get("completion_source"),
                         "attempt_count": len(attempts)})
    return {"contract": "life-richness-audit.1", "world_id": world, "after_sequence": after_sequence,
            "counts": dict(counts), "timeline": timeline, "planning_openings": openings,
            "clock_jumps_over_unstarted_plan_windows": skipped_windows,
            "exact_repeated_settlement_contents": sum(n - 1 for n in Counter(environment).values()),
            "exact_repeated_response_texts": sum(n - 1 for n in Counter(responses).values()),
            "interpretation": "No semantic diversity score. New IDs or changed wording do not prove a new life event. Delivery and private response do not prove completed character actions."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--after-sequence", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.database, after_sequence=args.after_sequence)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
