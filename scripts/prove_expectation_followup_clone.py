#!/usr/bin/env python3
"""Clone-only: n≥3 expectation-expiry end-to-end proofs.

Plants wait_seconds hopes, advances clock past not_before with no user reply,
captures wake, expired fact, and her decision (chase / silent / dismiss).

Output: ``output/expectation-followup/``. Never writes ``data/``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import drive_production_lanes as drive
import probe_initiative_lanes as initiative

OUTPUT = (REPO / "output" / "expectation-followup").resolve()
PRODUCTION_DB = drive.PRODUCTION_DB
SHANGHAI = ZoneInfo("Asia/Shanghai")
BUDGET_CNY = 6.0
WAIT_SECONDS = 90
_LOG = logging.getLogger("prove-expectation-followup")

SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "emoji_question",
        "inbound_text": "你猜我刚发那个表情是什么意思",
        "overlay": {
            "messages": ["那个表情你还没说是什么意思呀？"],
            "felt": "想听他把那个表情讲清楚",
            "wants": "等他解释那个表情",
            "waiting_for": "他解释那个表情是什么意思",
            "wait": WAIT_SECONDS,
            "timing": "now",
        },
    },
    {
        "id": "weekend_question",
        "inbound_text": "周末要不要一起出来走走",
        "overlay": {
            "messages": ["你周末有空吗？"],
            "felt": "想知道他周末有没有空",
            "wants": "等他回我周末有没有空",
            "waiting_for": "他回我周末有没有空",
            "wait": WAIT_SECONDS,
            "timing": "now",
        },
    },
    {
        "id": "hold_on_moment",
        "inbound_text": "等我一下我去接个电话",
        "overlay": {
            "messages": ["等我一下，我去接个电话"],
            "felt": "先让他去忙",
            "wants": "等他忙完回来接着聊",
            "waiting_for": "他接完电话回来接着聊",
            "wait": WAIT_SECONDS,
            "timing": "now",
        },
    },
]


def _cost(clone: Path, since_id: int) -> float:
    return float((drive.cost_report(clone, since_id=since_id) or {}).get("cost_cny") or 0)


def _daytime(instant: datetime) -> datetime:
    local = instant.astimezone(SHANGHAI)
    if local.hour < 7:
        local = local.replace(hour=10, minute=0, second=0, microsecond=0)
    elif local.hour >= 23:
        local = local.replace(hour=21, minute=0, second=0, microsecond=0)
    return local.astimezone(UTC)


def _extract_proactive(calls: list[dict[str, Any]]) -> dict[str, Any]:
    for row in reversed(calls):
        tool = str(row.get("tool") or "")
        fields = row.get("fields") if isinstance(row.get("fields"), dict) else {}
        if not fields:
            fields = initiative.extract_slim_fields(str(row.get("excerpt") or ""))
        if "proactive" in tool or fields.get("timing_choice") or fields.get("impulse_summary"):
            return {
                "tool": tool,
                "timing_choice": fields.get("timing_choice"),
                "impulse_summary": fields.get("impulse_summary") or fields.get("how_it_landed"),
                "fields": fields,
            }
    return {}


def _context_hits(calls: list[dict[str, Any]]) -> list[str]:
    needles = (
        "he has not spoken",
        "What she hoped for",
        "Timing evidence only",
        "expired_expectation",
        "还没回",
        "没说话",
    )
    hits: list[str] = []
    for row in calls:
        excerpt = str(row.get("excerpt") or "")
        for needle in needles:
            if needle in excerpt:
                hits.append(needle)
    return sorted(set(hits))


def _expiry_processes(projection: object) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for process in getattr(projection, "trigger_processes", ()) or ():
        ref = str(getattr(process, "trigger_ref", "") or "")
        if "expectation-expiry" not in ref:
            continue
        outcome = str(getattr(process, "runtime_outcome_ref", "") or "")
        out.append(
            {
                "trigger_ref": ref,
                "state": getattr(process, "state", None),
                "runtime_outcome_ref": outcome,
                "succeeded": outcome.startswith("proactive:authorized:"),
                "failed": "deliberation-failed" in outcome,
            }
        )
    return out


async def open_session(
    database: Path,
    output_dir: Path,
    *,
    inbound_payload: dict[str, object] | None,
) -> tuple[drive.DriveSession, initiative.RecordingCharacterModel]:
    from companion_daemon.config import Settings
    from companion_daemon.llm import DeepSeekChatModel
    from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore
    from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host

    if drive._is_production_path(database):  # noqa: SLF001
        raise SystemExit(f"refusing production ledger: {database}")
    sidecar = output_dir / f"{database.stem}.sidecars"
    sidecar.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        database_path=database,
        world_v2_external_perception_mode="off",
        world_v2_external_perception_sidecar_path=sidecar / "perception.sqlite",
        attachment_cache_path=sidecar / "attachments",
        world_v2_text_endpoint_enabled=False,
    )
    recipient_id = drive._recipient_id(settings)  # noqa: SLF001
    delivery = drive.CaptureDelivery()
    usage_store = WorldV2UsageStore(path=str(database))
    inner = DeepSeekChatModel(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        thinking_enabled=False,
        max_completion_tokens=4_096,
        usage_observer=usage_store.record,
    )
    if inbound_payload is not None:
        inner = drive.OverlayCharacterModel(inner, inbound_payload)
    recorder = initiative.RecordingCharacterModel(inner)
    host = build_qq_c2c_host(
        settings=settings,
        recipient_id=recipient_id,
        bootstrap_at=datetime.now(UTC),
        delivery=delivery,
        model=recorder,
        media_preview=None,
        media_transport=None,
        use_configured_recall_embedding=False,
    )
    session = drive.DriveSession(
        database=database,
        recipient_id=recipient_id,
        delivery=delivery,
        host=host,
        clock=datetime.now(UTC),
    )
    await session.logical_time()
    return session, recorder


async def run_scenario(spec: dict[str, Any], usage_from: int) -> dict[str, Any]:
    from companion_daemon.world_v2.response_expectation_view import (
        expired_unanswered_expectation,
        next_response_expectation_wake_at,
        pending_response_expectation,
    )

    clone = OUTPUT / f"clone_{spec['id']}.sqlite"
    drive.assert_clone_is_safe(source=PRODUCTION_DB, target=clone)
    drive.clone_ledger(PRODUCTION_DB, clone)

    overlay = drive._authored_inbound_payload(spec["overlay"], reply_only=True)  # noqa: SLF001
    session, recorder = await open_session(clone, OUTPUT, inbound_payload=overlay)
    try:
        origin = _daytime(await session.logical_time())
        await session.tick_to(origin, reason=f"daytime-{spec['id']}", run_life=False)
        before_seq = drive.current_seq(clone)
        await session.inbound(str(spec["inbound_text"]))
        await session.drain_loop(rounds=8, background=8)

        projection = session.projection()
        pending = pending_response_expectation(projection)
        wake_at = next_response_expectation_wake_at(projection)
        hope_events: list[dict[str, Any]] = []
        conn = drive.open_ro(clone)
        try:
            for seq, event in drive.events_after(conn, drive.WORLD_ID, before_seq):
                et = drive.event_type(event)
                payload = event.get("_payload") or {}
                if et == "AcceptanceRecorded" and payload.get("response_expectation"):
                    hope_events.append(
                        {
                            "seq": seq,
                            "plan_id": payload.get("plan_id"),
                            "response_expectation": payload.get("response_expectation"),
                        }
                    )
        finally:
            conn.close()

        if not hope_events:
            return {
                "scenario": spec["id"],
                "status": "no_hope_planted",
                "cost": drive.cost_report(clone, since_id=usage_from),
            }

        await session.close()
        session, recorder = await open_session(clone, OUTPUT, inbound_payload=None)

        hope = hope_events[-1]["response_expectation"]
        not_before = datetime.fromisoformat(str(hope["not_before"]).replace("Z", "+00:00"))
        target = _daytime(not_before + timedelta(seconds=8))
        before_calls = len(recorder.calls)
        before_sent = len(session.delivery.sent)
        await session.tick_to(target, reason=f"wake-{spec['id']}", run_life=True)
        await session.drain_loop(rounds=16, background=16)

        projection = session.projection()
        expired = expired_unanswered_expectation(projection)
        fields = _extract_proactive(recorder.calls[before_calls:])
        hits = _context_hits(recorder.calls[before_calls:])
        visible = [
            item.get("body")
            for item in session.delivery.sent[before_sent:]
            if item.get("kind") == "text"
        ]
        processes = _expiry_processes(projection)
        succeeded = [p for p in processes if p.get("succeeded")]
        failed = [p for p in processes if p.get("failed")]

        decision_kind = "unknown"
        decision_reason = ""
        if visible:
            decision_kind = "chase"
            decision_reason = visible[0]
        elif fields.get("timing_choice") == "silent":
            decision_kind = "silent"
            decision_reason = str(fields.get("impulse_summary") or "")
        elif fields.get("timing_choice") == "later":
            decision_kind = "later"
            decision_reason = str(fields.get("impulse_summary") or "")
        elif succeeded and not visible:
            decision_kind = "authorized_no_visible_yet"
            decision_reason = succeeded[-1].get("runtime_outcome_ref", "")
        elif failed:
            decision_kind = "deliberation_failed"
            decision_reason = failed[-1].get("runtime_outcome_ref", "")

        woke = bool(expired) or bool(succeeded) or bool(failed) or bool(visible)
        saw_fact = bool(hits) or expired is not None

        return {
            "scenario": spec["id"],
            "status": "ran",
            "clone": str(clone),
            "hoped_response": hope.get("hoped_response"),
            "not_before": hope.get("not_before"),
            "wake_target": target.isoformat(),
            "expired_unanswered": None if expired is None else expired.model_dump(mode="json"),
            "context_hits": hits,
            "proactive_fields": fields,
            "expiry_processes": processes,
            "decision": {
                "kind": decision_kind,
                "reason_or_text": decision_reason[:500],
                "timing_choice": fields.get("timing_choice"),
                "impulse_summary": fields.get("impulse_summary"),
            },
            "visible_text": visible,
            "proofs": {
                "hope_landed": bool(hope_events),
                "woke": woke,
                "expired_detected": expired is not None,
                "saw_unanswered_fact": saw_fact,
                "informed_decision": decision_kind in {
                    "chase",
                    "silent",
                    "later",
                    "authorized_no_visible_yet",
                },
                "pipeline_ok": woke and saw_fact and decision_kind != "deliberation_failed",
            },
            "cost": drive.cost_report(clone, since_id=usage_from),
        }
    except Exception as exc:
        return {
            "scenario": spec["id"],
            "status": "error",
            "error": {"type": type(exc).__name__, "message": str(exc)[:2000]},
            "cost": drive.cost_report(clone, since_id=usage_from),
        }
    finally:
        await session.close()


async def main_async() -> dict[str, Any]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    usage_from = drive.current_usage_id(PRODUCTION_DB)
    results: list[dict[str, Any]] = []
    total_cost = 0.0
    for spec in SCENARIOS:
        if total_cost >= BUDGET_CNY:
            results.append({"scenario": spec["id"], "status": "skipped_budget"})
            continue
        report = await run_scenario(spec, usage_from)
        results.append(report)
        total_cost = _cost(PRODUCTION_DB, usage_from)
        _LOG.info("scenario %s cost so far %.3f", spec["id"], total_cost)

    summary = {
        "scenarios": len(SCENARIOS),
        "pipeline_ok_count": sum(1 for r in results if (r.get("proofs") or {}).get("pipeline_ok")),
        "decisions": [
            {
                "scenario": r.get("scenario"),
                "kind": (r.get("decision") or {}).get("kind"),
                "text": (r.get("decision") or {}).get("reason_or_text", "")[:240],
                "visible": r.get("visible_text"),
            }
            for r in results
        ],
        "total_cost_cny": total_cost,
        "budget_ok": total_cost <= BUDGET_CNY,
    }
    out = {"results": results, "summary": summary}
    (OUTPUT / "clone_REPORT.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (OUTPUT / "clone_SUMMARY.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    os.environ.setdefault("WORLD_V2_SPEND_ACCOUNT", "debug")
    report = asyncio.run(main_async())
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
