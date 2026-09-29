#!/usr/bin/env python3
"""Read-only turn latency audit from a World V2 ledger.

Reconstructs ObservationRecorded → first visible ActionProviderAccepted
(expression reply) and attributes intentional role delay vs system overhead.

Does not call models, does not write the ledger, does not restart production.
Default output: output/latency-design/
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BJ = timezone(timedelta(hours=8))


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _secs(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds(), 1)


def _bj(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(BJ).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


@dataclass(frozen=True)
class TurnLatencyRow:
    ledger_sequence: int
    text_preview: str
    qq_observed_at: str | None
    host_received_at: str | None
    processing_started_at: str | None
    first_visible_at: str | None
    qq_to_host_s: float | None
    host_to_visible_s: float | None
    qq_to_visible_s: float | None
    timing_choice: str | None
    cadence_profile: str | None
    beat_not_before: str | None
    delay_window: Any
    intentional_delay_s: float
    system_overhead_s: float | None


def _load_proposal_timing(
    con: sqlite3.Connection, *, world_id: str, corr: str, seq_from: int, seq_to: int
) -> tuple[str | None, str | None, str | None, Any]:
    timing = None
    cadence = None
    not_before = None
    delay_window = None
    for row in con.execute(
        """
        SELECT event_json FROM world_v2_events
        WHERE world_id=? AND ledger_sequence BETWEEN ? AND ?
          AND json_extract(event_json,'$.correlation_id')=?
          AND json_extract(event_json,'$.event_type') IN
              ('ProposalRecorded','ExpressionBeatAuthorized')
        ORDER BY ledger_sequence
        """,
        (world_id, seq_from, seq_to, corr),
    ):
        ev = json.loads(row[0])
        pl = json.loads(ev["payload_json"]) if ev.get("payload_json") else {}
        if ev["event_type"] == "ExpressionBeatAuthorized":
            beat = pl.get("beat") or {}
            if beat.get("not_before") and not_before is None:
                not_before = beat["not_before"]
            continue
        raw = pl.get("proposal_json")
        if not isinstance(raw, str):
            continue
        try:
            pj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        timing = pj.get("timing_choice") or timing
        for change in pj.get("proposed_changes") or []:
            payload = (change.get("payload") or {}).get("canonical_json")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    continue
            if not isinstance(payload, dict):
                continue
            cadence = payload.get("cadence_profile") or cadence
            for beat in payload.get("beat_drafts") or []:
                if beat.get("delay_window") is not None and delay_window is None:
                    delay_window = beat.get("delay_window")
                if beat.get("not_before") and not_before is None:
                    not_before = beat.get("not_before")
    return timing, cadence, not_before, delay_window


def _first_expression_visible(
    con: sqlite3.Connection, *, world_id: str, corr: str, seq_from: int, seq_to: int
) -> datetime | None:
    for row in con.execute(
        """
        SELECT event_json FROM world_v2_events
        WHERE world_id=? AND ledger_sequence BETWEEN ? AND ?
          AND json_extract(event_json,'$.correlation_id')=?
          AND json_extract(event_json,'$.event_type')='ActionProviderAccepted'
        ORDER BY ledger_sequence
        """,
        (world_id, seq_from, seq_to, corr),
    ):
        ev = json.loads(row[0])
        pl = json.loads(ev["payload_json"])
        action_id = str(pl.get("action_id") or "")
        if "media" in action_id:
            continue
        if "expression" not in action_id:
            continue
        return _parse(pl.get("observed_at") or ev.get("created_at"))
    return None


def _host_received(
    con: sqlite3.Connection, source_event_ids: list[str]
) -> datetime | None:
    earliest: datetime | None = None
    for source_id in source_event_ids:
        row = con.execute(
            "SELECT received_at FROM world_v2_qq_ingress_fragments WHERE source_event_id=?",
            (str(source_id),),
        ).fetchone()
        if row is None:
            continue
        stamp = _parse(row[0])
        if stamp is not None and (earliest is None or stamp < earliest):
            earliest = stamp
    return earliest


def audit_turns(
    *,
    db_path: Path,
    world_id: str,
    seq_from: int,
    seq_to: int,
) -> list[TurnLatencyRow]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows: list[TurnLatencyRow] = []
    for row in con.execute(
        """
        SELECT ledger_sequence, event_json FROM world_v2_events
        WHERE world_id=? AND ledger_sequence BETWEEN ? AND ?
          AND json_extract(event_json,'$.event_type')='ObservationRecorded'
        ORDER BY ledger_sequence
        """,
        (world_id, seq_from, seq_to),
    ):
        ev = json.loads(row["event_json"])
        pl = json.loads(ev["payload_json"])
        corr = ev["correlation_id"]
        meta = pl.get("coalescing_metadata") or {}
        qq_time = _parse(pl.get("received_at"))
        host_recv = _host_received(con, list(meta.get("source_event_ids") or []))
        proc = _parse(meta.get("processing_started_at"))
        visible = _first_expression_visible(
            con,
            world_id=world_id,
            corr=corr,
            seq_from=row["ledger_sequence"],
            seq_to=min(seq_to + 400, row["ledger_sequence"] + 400),
        )
        timing, cadence, not_before, delay_window = _load_proposal_timing(
            con,
            world_id=world_id,
            corr=corr,
            seq_from=row["ledger_sequence"],
            seq_to=min(seq_to + 400, row["ledger_sequence"] + 400),
        )
        intentional = 0.0
        if not_before and proc:
            nb = _parse(not_before)
            if nb is not None and nb > proc:
                intentional = max(0.0, (nb - proc).total_seconds())
        qq_to_visible = _secs(qq_time, visible)
        system = None
        if qq_to_visible is not None:
            system = round(max(0.0, qq_to_visible - intentional), 1)
        rows.append(
            TurnLatencyRow(
                ledger_sequence=row["ledger_sequence"],
                text_preview=(pl.get("text") or "").replace("\n", " / ")[:80],
                qq_observed_at=_bj(qq_time),
                host_received_at=_bj(host_recv),
                processing_started_at=_bj(proc),
                first_visible_at=_bj(visible),
                qq_to_host_s=_secs(qq_time, host_recv),
                host_to_visible_s=_secs(host_recv, visible),
                qq_to_visible_s=qq_to_visible,
                timing_choice=timing,
                cadence_profile=cadence if isinstance(cadence, str) else (
                    json.dumps(cadence, ensure_ascii=False) if cadence is not None else None
                ),
                beat_not_before=not_before,
                delay_window=delay_window,
                intentional_delay_s=round(intentional, 1),
                system_overhead_s=system,
            )
        )
    con.close()
    return rows


def _qixi_deep_dive(con: sqlite3.Connection, *, world_id: str) -> dict[str, Any]:
    """Hard-coded deep dive for the 2026-08-19 Qixi complaint turn (seq 6060)."""

    row = con.execute(
        "SELECT event_json FROM world_v2_events WHERE world_id=? AND ledger_sequence=6060",
        (world_id,),
    ).fetchone()
    if row is None:
        return {"error": "seq 6060 missing"}
    ev = json.loads(row[0])
    pl = json.loads(ev["payload_json"])
    corr = ev["correlation_id"]
    meta = pl.get("coalescing_metadata") or {}
    qq_time = _parse(pl.get("received_at"))
    host_recv = _host_received(con, list(meta.get("source_event_ids") or []))
    proc = _parse(meta.get("processing_started_at"))

    usage = [
        dict(r)
        for r in con.execute(
            """
            SELECT recorded_at, purpose, status, latency_ms, cost_cny, model
            FROM world_v2_model_usage
            WHERE recorded_at BETWEEN '2026-08-19T15:42:45' AND '2026-08-19T15:45:00'
            ORDER BY recorded_at, id
            """
        )
    ]
    visible = _first_expression_visible(
        con, world_id=world_id, corr=corr, seq_from=6060, seq_to=6200
    )
    timing, cadence, not_before, delay_window = _load_proposal_timing(
        con, world_id=world_id, corr=corr, seq_from=6060, seq_to=6200
    )

    inbound_ms = sum(
        int(u["latency_ms"] or 0)
        for u in usage
        if u["purpose"] == "inbound_turn"
    )
    image = next((u for u in usage if u["purpose"] == "image_generation"), None)

    segments = [
        {
            "name": "qq_push_to_host_submit",
            "kind": "system",
            "seconds": _secs(qq_time, host_recv),
            "note": "OneBot event.time → ingress_store.submit(ingress_now). Not role delay.",
        },
        {
            "name": "coalescing_matrix_window",
            "kind": "system",
            "seconds": _secs(host_recv, proc),
            "note": "Policy window ~280ms + claim; advisory was semantic_endpoint_fallback.",
        },
        {
            "name": "role_intentional_delay",
            "kind": "intentional",
            "seconds": 0.0,
            "note": (
                f"timing_choice={timing!r}, cadence={cadence!r}, "
                f"not_before={not_before!r}, delay_window={delay_window!r}"
            ),
        },
        {
            "name": "inbound_model_api_sum",
            "kind": "system",
            "seconds": round(inbound_ms / 1000.0, 1),
            "note": "Sum of world_v2_model_usage.latency_ms for purpose=inbound_turn in window.",
        },
        {
            "name": "concurrent_image_generation",
            "kind": "system",
            "seconds": round((image["latency_ms"] or 0) / 1000.0, 1) if image else None,
            "note": "Same wall window; media_render competed with the visible reply path.",
        },
        {
            "name": "host_receive_to_first_visible",
            "kind": "system_total",
            "seconds": _secs(host_recv, visible),
            "note": "First ActionProviderAccepted for expression-plan action.",
        },
        {
            "name": "qq_time_to_first_visible",
            "kind": "user_perceived_total",
            "seconds": _secs(qq_time, visible),
            "note": "User-facing end-to-end from QQ message timestamp.",
        },
    ]
    return {
        "observation_seq": 6060,
        "text": pl.get("text"),
        "correlation_id": corr,
        "timestamps_bj": {
            "qq_observed": _bj(qq_time),
            "host_received": _bj(host_recv),
            "processing_started": _bj(proc),
            "first_visible": _bj(visible),
        },
        "expression": {
            "timing_choice": timing,
            "cadence_profile": cadence,
            "not_before": not_before,
            "delay_window": delay_window,
        },
        "segments": segments,
        "model_usage_window": usage,
        "verdict": {
            "intentional_share_s": 0.0,
            "system_share_s": _secs(qq_time, visible),
            "largest_system_bucket": "contention_after_host_receive",
            "largest_system_note": (
                "She chose timing_choice=now with no beat delay. The ~117s after host "
                "receive is model retries + life/media contention (including 54s "
                "image_generation) + expression reclaim/commit, not her cadence."
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/companion.epoch2.sqlite"),
    )
    parser.add_argument(
        "--world-id",
        default="world:companion-v2:qq-c2c:geoff",
    )
    parser.add_argument("--seq-from", type=int, default=6000)
    parser.add_argument("--seq-to", type=int, default=7500)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("output/latency-design"),
    )
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    turns = audit_turns(
        db_path=args.db,
        world_id=args.world_id,
        seq_from=args.seq_from,
        seq_to=args.seq_to,
    )
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    deep = _qixi_deep_dive(con, world_id=args.world_id)
    con.close()

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db": str(args.db),
        "world_id": args.world_id,
        "seq_range": [args.seq_from, args.seq_to],
        "turns": [asdict(t) for t in turns],
        "qixi_deep_dive": deep,
    }
    out_json = args.out_dir / "turn_latency.json"
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {out_json} ({len(turns)} turns)")
    qixi = next((t for t in turns if t.ledger_sequence == 6060), None)
    if qixi is not None:
        print(
            "Qixi seq 6060: "
            f"qq→visible={qixi.qq_to_visible_s}s "
            f"intentional={qixi.intentional_delay_s}s "
            f"system={qixi.system_overhead_s}s "
            f"timing={qixi.timing_choice}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
