"""Replay a recorded day's clock inputs on a fresh capture-only ledger clone.

Keeps production model settings, records request/response bytes and every bill,
and never treats a cap, technical failure, or fewer events as successful saving.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def usage_rows(database, after):
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(
            "select * from world_v2_model_usage where id > ? order by id", (after,),
        )]


def chat_delivery_outcomes(events, chats):
    """Join final capture receipts, not just the synchronous ingress result.

    Virtual elapsed time describes this harness's wake schedule, not measured
    production latency. Capture-only receipts do not prove a real QQ delivery.
    """
    observations = {}
    delivered = {}
    for event in events:
        payload = json.loads(event["payload_json"])
        correlation = event["correlation_id"]
        if event["event_type"] == "ObservationRecorded":
            for message_id in payload.get("coalescing_metadata", {}).get("source_event_ids", ()):
                observations[message_id] = event
        elif event["event_type"] == "ActionDelivered":
            delivered.setdefault(correlation, {})[payload["action_id"]] = event
    outcomes = []
    for chat in chats:
        observation = observations.get(chat["message_id"])
        receipts = tuple(delivered.get(observation["correlation_id"], {}).values()) if observation else ()
        first = min((event["logical_time"] for event in receipts), default=None)
        elapsed = (
            datetime.fromisoformat(first) - datetime.fromisoformat(observation["logical_time"])
        ).total_seconds() if first else None
        outcomes.append({
            "message_id": chat["message_id"], "ingress_status": chat["status"],
            "immediate_visible": bool(chat["visible"]),
            "eventual_capture_delivery": bool(receipts), "delivered_actions": len(receipts),
            "first_terminal_receipt_virtual_at": first,
            "virtual_elapsed_seconds": elapsed,
            "observation_found": observation is not None,
        })
    return outcomes


def comparable_prices(output, rows):
    """Separate actual bills from tariff/cold-cache sensitivity estimates."""
    from companion_daemon.usage_metrics import estimate_model_cost_cny

    scopes = {}
    capture = output / "model-inputs.jsonl"
    if capture.exists():
        for line in capture.read_text().splitlines():
            value = json.loads(line)
            scope = value.get("scope")
            if value.get("kind") == "request" and isinstance(scope, dict) and scope.get("virtual_at"):
                scopes[value.get("usage_reservation_id")] = scope["virtual_at"]
    totals = Counter()
    missing = 0
    for row in rows:
        tokens = {key: row.get(key, 0) for key in (
            "prompt_tokens", "completion_tokens", "cache_hit_tokens", "cache_miss_tokens", "reasoning_tokens",
        )}
        scoped = scopes.get(row.get("reservation_id"))
        at = datetime.fromisoformat(scoped).astimezone(UTC) if scoped else None
        missing += int(at is None)
        weekday = datetime(2026, 9, 28, at.hour, at.minute, at.second, tzinfo=UTC) if at else None
        for label, instant, peak in (
            ("weekday_at_same_local_hours_cny", weekday, at is None),
            ("all_offpeak_cny", datetime(2026, 9, 27, tzinfo=UTC), False),
            ("all_peak_cny", datetime(2026, 9, 28, 2, tzinfo=UTC), True),
        ):
            totals[label] += estimate_model_cost_cny(model=row["model"], **tokens, at=instant, conservative_peak=peak)[0]
        totals["uncached_offpeak_sensitivity_cny"] += estimate_model_cost_cny(
            model=row["model"], **{**tokens, "cache_hit_tokens": 0, "cache_miss_tokens": tokens["prompt_tokens"]},
            at=datetime(2026, 9, 27, tzinfo=UTC),
        )[0]
    return {**{key: round(value, 6) for key, value in totals.items()},
            "unscoped_requests_priced_at_peak": missing,
            "note": "Same observed token/cache counts; weekday estimate changes tariff only, never task timing."}


async def run(args):
    from companion_daemon.config import Settings
    from companion_daemon.llm import DeepSeekChatModel
    from companion_daemon.world_v2.longitudinal_model_input_capture import (
        ModelInputCaptureTransport, PrivateModelInputCapture, model_input_capture_scope,
    )
    import httpx
    from drive_production_lanes import current_usage_id, open_session

    args.output.mkdir(parents=True, exist_ok=False)
    database = args.output / "clone.sqlite"
    with sqlite3.connect(f"file:{args.source}?mode=ro", uri=True) as src, sqlite3.connect(database) as dst:
        src.backup(dst)
    with sqlite3.connect(database) as conn:
        worlds = conn.execute("select world_id,max(ledger_sequence) from world_v2_events group by world_id").fetchall()
    expected_world = "world:companion-v2:qq-c2c:" + args.primary_user_id
    if len(worlds) != 1 or worlds[0][0] != expected_world:
        raise ValueError("source World must match the explicitly supplied isolated identity")
    first_seq = worlds[0][1]
    first_usage = current_usage_id(database)
    # These are experiment guardrails well above the acceptance target, never
    # a way to achieve it. All variants use the same limits and zero background cap.
    for key in ("DAILY_BUDGET_CNY", "SOFT_DAILY_BUDGET_CNY", "MONTHLY_BUDGET_CNY"):
        os.environ[key] = "100"
    os.environ["WORLD_V2_BACKGROUND_DAILY_BUDGET_CNY"] = "0"
    os.environ["WORLD_V2_MODEL_USAGE_BUDGET_DISABLED"] = "true"
    os.environ["WORLD_V2_REFERENCE_WIRE_ENABLED"] = str(args.reference_wire).lower()
    settings = Settings()
    if not settings.deepseek_debug_api_key:
        raise ValueError("debug key required; production-key fallback is forbidden")
    frozen_baseline_hash = None
    if args.baseline_source_clock:
        name = "companion_daemon.world_v2._cost_baseline_choice_authority"
        module = types.ModuleType(name)
        module.__package__ = "companion_daemon.world_v2"
        sys.modules[name] = module
        source = subprocess.check_output([
            "git", "show", "HEAD:src/companion_daemon/world_v2/world_stimulus_choice_authority.py",
        ], cwd=ROOT, text=True)
        frozen_baseline_hash = hashlib.sha256(source.encode()).hexdigest()
        exec(compile(source, "<frozen-baseline-clock>", "exec"), module.__dict__)
        from companion_daemon.world_v2 import world_stimulus_choice_authority as target
        target.world_stimulus_source_origin = module.world_stimulus_source_origin
        # open_session imports the host lazily, after this exact baseline hook.
        from companion_daemon.world_v2 import character_life_response_runtime as response
        from companion_daemon.world_v2 import world_life_intent_runtime as intent
        response.world_stimulus_source_origin = module.world_stimulus_source_origin
        if hasattr(intent, "world_stimulus_source_origin"):
            intent.world_stimulus_source_origin = module.world_stimulus_source_origin
    capture = PrivateModelInputCapture(args.output / "model-inputs.jsonl")
    code_hashes = {}
    for relative in (
        "reference_wire.py", "background_context_profile.py", "world_stimulus_choice_authority.py",
        "visible_grounded_review.py", "visible_grounded_review_runtime.py", "grounded_review_wire.py", "grounded_material_presentation.py",
        "character_interior/inbound_author.py", "character_interior/inbound_prompt.py",
        "character_interior/personal_action_evidence.py", "character_interior/life_runtime_readings.py",
        "proposal_envelope.py", "reducers.py",
        "character_interior/structured_role.py", "character_interior/structured_role_tool_contract.py",
        "character_interior/life_source_view.py", "character_interior/life_source_readings.py",
        "character_interior/life_source_review_request.py", "character_interior/life_source_review.py",
        "character_interior/experience_transition_tool_schema.py",
        "world_consequence_author_tool.py", "world_consequence_prompt.py",
        "activity_lifecycle_worker.py", "life_ecology_activity.py",
        "day_open_life_worker.py",
    ):
        original = ROOT / "src/companion_daemon/world_v2" / relative
        raw = original.read_bytes()
        target = args.output / "code" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        code_hashes[relative] = hashlib.sha256(raw).hexdigest()
    for filename in ("benchmark_daily_cost.py", "drive_production_lanes.py"):
        raw = (ROOT / "scripts" / filename).read_bytes()
        target = args.output / "code" / "scripts" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        code_hashes["scripts/" + filename] = hashlib.sha256(raw).hexdigest()
    original_init = DeepSeekChatModel.__init__

    def captured_init(self, *pos, **kw):
        if kw.get("client") is not None or kw.get("transport") is not None:
            raise ValueError("benchmark cannot silently replace an existing provider transport")
        kw["transport"] = ModelInputCaptureTransport(
            inner=httpx.AsyncHTTPTransport(trust_env=False), capture=capture,
            model_role=str(kw.get("model", "configured")),
        )
        original_init(self, *pos, **kw)

    DeepSeekChatModel.__init__ = captured_init
    schedule = json.loads(args.wake_schedule.read_text())
    targets = [datetime.fromisoformat(row["tick"]["logical_time"]) for row in schedule["wakes"] if "tick" in row]
    chats = json.loads(args.chat_scenario.read_text()) if args.chat_scenario else []
    origin = datetime.fromisoformat(schedule["origin"])
    commands = [(target, "wake", index, None) for index, target in enumerate(targets)]
    for index, chat in enumerate(chats):
        at = origin + timedelta(hours=float(chat["after_hours"]))
        if not origin <= at <= targets[-1] or not isinstance(chat["text"], str) or not chat["text"].strip():
            raise ValueError("chat inputs must be nonempty and inside the frozen day")
        commands.append((at, "chat", index, chat["text"]))
    commands.sort(key=lambda row: row[0])
    dump(args.output / "provenance.json", {
        "contract": "daily-cost-benchmark.1", "source": str(args.source),
        "snapshot_sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
        "source_sequence": first_seq, "usage_from": first_usage,
        "world_id": expected_world, "new_user_turns": len(chats), "chat_inputs": chats,
        "workload": "same recorded 24 clock inputs and inherited pending chat; optional fixed additional chat",
        "targets": targets, "background_units": 8, "drain_rounds": 6,
        "models": {"character": settings.deepseek_model, "thinking": settings.deepseek_character_thinking_enabled},
        "expression_profile": settings.world_v2_visible_expression_profile,
        "reference_wire": settings.world_v2_reference_wire_enabled,
        "virtual_delivery_clock": args.virtual_clock,
        "follow_runtime_dues": args.follow_runtime_dues,
        "configured_recall_embedding": args.configured_recall_embedding,
        "code_sha256": code_hashes,
        "source_clock_variant": "baseline" if args.baseline_source_clock else "fixed",
        "frozen_baseline_hash": frozen_baseline_hash,
        "excluded": ["real QQ", "media", "external feeds", "real-time cache lifetime"] +
                    ([] if args.configured_recall_embedding else ["semantic embeddings"]),
    })
    session = None
    progress = []
    error = None
    try:
        journey_clock = None
        if args.virtual_clock:
            from companion_daemon.world_v2.longitudinal_journey import JourneyClock
            from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger

            ledger = SQLiteWorldLedger(path=database, world_id=expected_world)
            try:
                journey_clock = JourneyClock(ledger.project().logical_time)
            finally:
                ledger.close()
        session = await open_session(database=database, output_dir=args.output,
                                     enable_media=False, primary_user_id=args.primary_user_id,
                                     journey_clock=journey_clock,
                                     use_configured_recall_embedding=args.configured_recall_embedding)
        for index, (target, kind, ordinal, text) in enumerate(commands):
            automatic = []
            if args.follow_runtime_dues:
                def guard(_at):
                    if sum(row["cost_cny"] for row in usage_rows(database, first_usage)) >= 15:
                        raise RuntimeError("diagnostic spending guard reached; benchmark failed")

                with model_input_capture_scope(step_id=f"before-{kind}-{ordinal:02d}", virtual_at=target):
                    automatic = await session.advance_with_runtime_dues(
                        target, reason=f"cost-benchmark-{index:02d}", before_step=guard,
                        run_life_at_target=kind == "wake",
                    )
            with model_input_capture_scope(step_id=f"{kind}-{ordinal:02d}", virtual_at=target):
                if kind == "wake":
                    tick = await session.tick_to(target, reason=f"cost-benchmark-{ordinal:02d}")
                    drains = await session.drain_loop(rounds=6, background=8)
                else:
                    tick = await session.inbound(text, observed_at=target, background_units=8)
                    drains = []
            rows = usage_rows(database, first_usage)
            row = {"index": index, "kind": kind, "tick": tick, "drains": drains,
                   "automatic_steps": automatic,
                   "cost_cny": sum(x["cost_cny"] for x in rows), "calls": len(rows)}
            progress.append(row)
            dump(args.output / "progress.json", progress)
            print(json.dumps({k: row[k] for k in ("index", "kind", "cost_cny", "calls")}), flush=True)
            if row["cost_cny"] >= 15:
                raise RuntimeError("diagnostic spending guard reached; benchmark failed")
    except asyncio.CancelledError:
        error = "CancelledError: diagnostic interrupted before completion"
        raise
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        logging.exception("daily benchmark stopped")
    finally:
        if session is not None:
            dump(args.output / "delivery.json", session.delivery.sent)
            await session.close()
        DeepSeekChatModel.__init__ = original_init
        rows = usage_rows(database, first_usage)
        dump(args.output / "usage.json", rows)
        with sqlite3.connect(database) as conn:
            events = [json.loads(raw) for (raw,) in conn.execute(
                "select event_json from world_v2_events where world_id=? and ledger_sequence>? order by ledger_sequence",
                (expected_world, first_seq),
            )]
        failures = Counter()
        for event in events:
            if event["event_type"] == "ModelResultRecorded":
                audit = json.loads(json.loads(event["payload_json"])["audit_json"])
                if audit.get("failure_code"):
                    failures[audit["failure_code"]] += 1
        dump(args.output / "result.json", {
            "completed": error is None and len(progress) == len(commands), "error": error,
            "cost_cny": sum(row["cost_cny"] for row in rows), "calls": len(rows),
            "purpose_counts": Counter(row["purpose"] for row in rows),
            "event_counts": Counter(event["event_type"] for event in events),
            "model_failures": failures, "budget_denials": sum(row["status"] == "budget_denied" for row in rows),
            "unknown_billing": sum(row.get("billing_state") == "unknown" for row in rows),
            "input_tokens": sum(row["prompt_tokens"] for row in rows),
            "output_tokens": sum(row["completion_tokens"] for row in rows),
            "cache_hit_tokens": sum(row["cache_hit_tokens"] for row in rows),
            "cache_miss_tokens": sum(row["cache_miss_tokens"] for row in rows),
            "capture": capture.health(), "wake_count": sum(row["kind"] == "wake" for row in progress),
            "chat_count": sum(row["kind"] == "chat" for row in progress),
            "chat_results": [row["tick"] for row in progress if row["kind"] == "chat"],
            "chat_delivery_outcomes": chat_delivery_outcomes(
                events, [row["tick"] for row in progress if row["kind"] == "chat"],
            ),
            "comparable_prices": comparable_prices(args.output, rows),
        })
    return 1 if error else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--wake-schedule", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--primary-user-id", required=True)
    parser.add_argument("--baseline-source-clock", action="store_true")
    parser.add_argument("--reference-wire", action="store_true")
    parser.add_argument("--virtual-clock", action="store_true")
    parser.add_argument("--follow-runtime-dues", action="store_true")
    parser.add_argument("--configured-recall-embedding", action="store_true")
    parser.add_argument("--chat-scenario", type=Path)
    parser.add_argument("--allow-real-provider", action="store_true", required=True)
    args = parser.parse_args()
    if args.follow_runtime_dues and not args.virtual_clock:
        parser.error("following runtime dues requires --virtual-clock")
    args.source = args.source.resolve()
    args.output = args.output.resolve()
    if args.output.exists() or not args.output.is_relative_to(ROOT / "output"):
        parser.error("output must be a fresh directory under output/")
    logging.basicConfig(level=logging.ERROR)
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
