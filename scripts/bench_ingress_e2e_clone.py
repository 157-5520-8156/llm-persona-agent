#!/usr/bin/env python3
"""One real inbound turn on a production-ledger clone.

Clones ``data/companion.epoch2.sqlite`` read-only into ``output/ingress-perf/``,
then times Observation-commit → first authorized beat.  Never writes ``data/``,
never talks to 8787 / NapCat.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sys
import time

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import drive_production_lanes as drive

OUTPUT = (REPO / "output" / "ingress-perf").resolve()
CLONE = OUTPUT / "e2e-inbound.sqlite"
TEXT = "还在吗？"


def _samples_payload(host: object) -> list[dict[str, object]]:
    samples = host.latency_samples()  # type: ignore[attr-defined]
    rows = [
        {
            "trace_id": sample.trace_id,
            "startup": sample.startup,
            "segment": sample.segment,
            "duration_ms": sample.duration_ms,
            "environment": sample.environment,
        }
        for sample in samples
    ]
    by_trace: dict[str, dict[str, float]] = {}
    for row in rows:
        trace = str(row["trace_id"])
        by_trace.setdefault(trace, {})[str(row["segment"])] = float(row["duration_ms"])
    return rows, by_trace


def _observation_to_authorized(segments: dict[str, float]) -> dict[str, float]:
    coalescing = segments.get("coalescing", 0.0)
    ingress_to_visible = segments.get("ingress_to_visible")
    # Authorization is acceptance of the first beat, before dispatch/receipt.
    after_observation = {
        "snapshot": segments.get("snapshot", 0.0),
        "context": segments.get("context", 0.0),
        "ledger_commit": segments.get("ledger_commit", 0.0),
        "model_ttft": segments.get("model_ttft", 0.0),
        "model_completion": segments.get("model_completion", 0.0),
        "acceptance": segments.get("acceptance", 0.0),
        "queue": segments.get("queue", 0.0),
    }
    local_ms = sum(after_observation.values())
    provider_ms = after_observation["model_ttft"] + after_observation["model_completion"]
    authorized_ms = local_ms
    if ingress_to_visible is not None:
        authorized_ms = max(
            0.0,
            ingress_to_visible
            - coalescing
            - segments.get("dispatch", 0.0)
            - segments.get("receipt", 0.0),
        )
    return {
        "coalescing_ms": coalescing,
        "observation_to_authorized_ms": authorized_ms,
        "provider_ms": provider_ms,
        "non_provider_after_observation_ms": local_ms - provider_ms,
        "ingress_to_first_role_provider_ms": segments.get("ingress_to_first_role_provider", 0.0),
        "ingress_to_visible_ms": ingress_to_visible or 0.0,
        **{f"{key}_ms": value for key, value in after_observation.items()},
        "dispatch_ms": segments.get("dispatch", 0.0),
        "receipt_ms": segments.get("receipt", 0.0),
    }


async def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    drive.clone_ledger(drive.PRODUCTION_DB, CLONE)
    usage_from = drive.current_usage_id(CLONE)
    started_seq = drive.current_seq(CLONE)
    session = await drive.open_session(
        database=CLONE,
        output_dir=OUTPUT,
        enable_media=False,
    )
    payload: dict[str, object] = {
        "clone": str(CLONE),
        "started_seq": started_seq,
        "text": TEXT,
    }
    try:
        when = datetime.now(UTC) + timedelta(seconds=2)
        wall_started = time.perf_counter()
        result = await session.host.inbound_text(
            message_id=f"ingress-perf-{time.time_ns()}",
            recipient_id=session.recipient_id,
            text=TEXT,
            observed_at=when,
        )
        wall_s = time.perf_counter() - wall_started
        samples, by_trace = _samples_payload(session.host)
        traces = list(by_trace.values())
        latest = traces[-1] if traces else {}
        breakdown = _observation_to_authorized(latest)
        total = breakdown["observation_to_authorized_ms"] or 1.0
        shares = {
            key: round(100.0 * float(breakdown[key]) / total, 2)
            for key in (
                "queue_ms",
                "snapshot_ms",
                "context_ms",
                "ledger_commit_ms",
                "model_ttft_ms",
                "model_completion_ms",
                "acceptance_ms",
            )
            if total
        }
        payload.update(
            {
                "status": getattr(result, "status", None),
                "action_id": getattr(result, "action_id", None),
                "visible": list(session.delivery.sent),
                "inbound_wall_s": wall_s,
                "samples": samples,
                "latest_trace": latest,
                "breakdown": breakdown,
                "share_of_observation_to_authorized_pct": shares,
                "new_events": drive.new_events(CLONE, started_seq),
                "cost": drive.cost_report(CLONE, since_id=usage_from),
            }
        )
    finally:
        await session.close()
    path = OUTPUT / "e2e-inbound.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: payload[k] for k in payload if k != "samples"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
