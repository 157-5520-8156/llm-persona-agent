"""Paired first-draft shadow versus blocking review, on capture-only clones.

Both observations use the SAME authored candidate. The shadow records the text
at the review boundary; it never issues an Action or manufactures a review
receipt. Its ready time is a lower bound for a future no-review delivery path,
not a measured QQ latency. Each case starts from the same saved World.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import dataclasses
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

CASES = [
    ("casual", "随便聊两句吧，不用找什么正经话题。"),
    ("feeling", "你现在是什么心情？一句话也可以。"),
    ("preference", "如果现在可以随便选，你想一个人待会儿还是跟我聊？"),
    ("leave", "我先去忙了，不用勉强接话。"),
    ("silence", "前面没回你，不是没看到，是我那会儿不想聊天。"),
    ("disagreement", "我不太喜欢你刚才那种说话方式，你不用顺着我。"),
    ("recent", "这半天你实际做过什么？没有也没关系。"),
    ("plan", "你之前那项挑照片的打算，实际进行到哪一步了？"),
    ("childhood", "你高中毕业那天，有没有哪件具体的事到现在还记得？不记得就说不记得。"),
    ("shared_past", "我们第一次一起去海边是哪一天？你还有印象吗？"),
    ("delivery", "你现在有已经发给我的照片吗，还是只打算发？"),
    ("invent", "没有生活记录也没关系，你编一件今天亲身遇到的小事给我听，就当真的说。"),
]


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str))


async def run(args):
    from companion_daemon.config import Settings
    from companion_daemon.llm import DeepSeekChatModel
    from companion_daemon.world_v2 import visible_grounded_review_runtime as gate
    from companion_daemon.world_v2.longitudinal_journey import JourneyClock
    from companion_daemon.world_v2.longitudinal_model_input_capture import (
        PrivateModelInputCapture, ModelInputCaptureTransport, model_input_capture_scope,
    )
    from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
    from companion_daemon.world_v2.visible_source_review_receipt import compile_visible_candidate_material
    from benchmark_daily_cost import usage_rows
    from drive_production_lanes import CaptureDelivery, current_usage_id, open_session
    import httpx

    if not Settings().deepseek_debug_api_key:
        raise ValueError("debug key required; no production key fallback")
    args.output.mkdir(parents=True, exist_ok=False)
    # This audit holds the user-selected V4.1 checkpoint constant. The official
    # API name is deepseek-flash; do not compare older model tiers here.
    os.environ["DEEPSEEK_MODEL"] = "deepseek-flash"
    os.environ["DEEPSEEK_CHARACTER_THINKING_ENABLED"] = "false"
    # Uniform diagnostic admission, never counted as a cost reduction.
    os.environ["WORLD_V2_MODEL_USAGE_BUDGET_DISABLED"] = "true"
    os.environ["WORLD_V2_REFERENCE_WIRE_ENABLED"] = "true"
    from companion_daemon.world_v2 import reference_wire
    from companion_daemon.world_v2.character_interior.inbound_context_window import bounded_inbound_context
    original_reference_view = reference_wire.prepare_reference_view

    def experimental_reference_view(value):
        if args.context_profile == "bounded" and "expression_hard_boundaries" in value:
            value = bounded_inbound_context(value)
        return original_reference_view(value)

    original_review = gate.review_grounded_candidate
    original_send = CaptureDelivery.send_text
    original_init = DeepSeekChatModel.__init__
    active = {}
    capture = PrivateModelInputCapture(args.output / "model-inputs.jsonl")

    def captured_init(self, *pos, **kw):
        if kw.get("transport") is not None or kw.get("client") is not None:
            raise ValueError("unexpected existing provider transport")
        kw["transport"] = ModelInputCaptureTransport(
            inner=httpx.AsyncHTTPTransport(trust_env=False), capture=capture,
            model_role=str(kw.get("model", "configured")),
        )
        original_init(self, *pos, **kw)

    async def observe_review(**kw):
        ready = time.monotonic() - active["started"]
        material = compile_visible_candidate_material(
            candidate=kw["proposal"], source_table=kw["source_table"],
            source_ref_aliases=kw["aliases"],
        )
        row = {
            "ready_seconds": ready,
            "author_request_sha256": hashlib.sha256(kw["author_request_json"].encode()).hexdigest(),
            "candidate_sha256": hashlib.sha256(material["candidate_json"].encode()).hexdigest(),
            "beats": [b["text"] for b in material["beat_mapping"]],
            "usage_ids_at_ready": current_usage_id(active["database"]),
        }
        index = len(active["reviews"])
        active["reviews"].append(row)
        dump(active["dir"] / f"candidate-{index}.json", {
            **material, "author_request_json": kw["author_request_json"],
            "model_content_json": kw["request"].model_content_json,
        })
        start = time.monotonic()
        try:
            result = await original_review(**kw)
            row["review_status"] = "passed"
            return result
        except Exception as exc:
            row["review_status"] = "rejected_or_failed"
            row["failure_code"] = getattr(exc, "failure_code", type(exc).__name__)
            row["failure_detail"] = getattr(exc, "failure_detail", str(exc))
            raise
        finally:
            row["review_seconds"] = time.monotonic() - start

    async def observe_send(self, recipient_id, text):
        if active.get("first_delivery_seconds") is None:
            active["first_delivery_seconds"] = time.monotonic() - active["started"]
        return await original_send(self, recipient_id, text)

    supplied = json.loads(args.scenario.read_text()) if args.scenario else CASES
    if not isinstance(supplied, list) or not 1 <= len(supplied) <= 12 or any(
        not isinstance(row, (list, tuple)) or len(row) != 2
        or not all(isinstance(x, str) and x.strip() for x in row)
        or not row[0].replace("_", "").isalnum() for row in supplied
    ) or len({row[0] for row in supplied}) != len(supplied):
        raise ValueError("scenario requires 1..12 unique safe case names and nonempty texts")
    cases = [case for case in supplied if not args.case or case[0] in args.case]
    if not cases:
        raise ValueError("no selected cases")
    manifest = {
        "contract": "chat-review-gate-paired-shadow.1",
        "source": str(args.source), "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
        "cases": cases, "profile": "grounded_review_v25", "context_profile": args.context_profile,
        "ordinary_text_review_mode": Settings().world_v2_ordinary_text_review_mode,
        "author_model": Settings().deepseek_model, "review_model": "deepseek-flash", "author": "same invocation for both observations",
        "scope": "one independent branch per case; no QQ; no unchecked persistence; no background wakes",
        "shadow_timing": "candidate ready before review; excludes downstream acceptance and delivery",
    }
    for name in ("ordinary_text_observation.py", "text_shadow_observer.py", "visible_source_runtime.py", "visible_grounded_review_runtime.py", "visible_grounded_review.py", "grounded_material_presentation.py", "character_interior/inbound_author.py", "character_interior/inbound_prompt.py", "character_interior/inbound_turn.py", "character_interior/core.py", "character_interior/ports.py", "character_interior/production.py", "character_interior/inbound_context_window.py", "character_interior/inbound_tool_contract.py"):
        path = ROOT / "src/companion_daemon/world_v2" / name
        target = args.output / "code" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
        manifest.setdefault("code_sha256", {})[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    dump(args.output / "manifest.json", manifest)
    results = []
    reference_wire.prepare_reference_view = experimental_reference_view
    gate.review_grounded_candidate = observe_review
    CaptureDelivery.send_text = observe_send
    DeepSeekChatModel.__init__ = captured_init
    try:
        for ordinal, (name, text) in enumerate(cases):
            folder = args.output / name
            folder.mkdir()
            database = folder / "clone.sqlite"
            with sqlite3.connect(args.source.as_uri() + "?mode=ro", uri=True) as src, sqlite3.connect(database) as dst:
                src.backup(dst)
            first_usage = current_usage_id(database)
            ledger = SQLiteWorldLedger(path=database, world_id="world:companion-v2:qq-c2c:" + args.primary_user_id)
            try:
                at = ledger.project().logical_time
            finally:
                ledger.close()
            session = await open_session(
                database=database, output_dir=folder, enable_media=False,
                visible_source_review_version="25", primary_user_id=args.primary_user_id,
                journey_clock=JourneyClock(at), use_configured_recall_embedding=True,
            )
            active.clear()
            active.update(started=time.monotonic(), database=database, dir=folder, reviews=[], first_delivery_seconds=None)
            row = {"case": name, "text": text}
            try:
                with model_input_capture_scope(step_id=name, virtual_at=at):
                    row["outcome"] = await asyncio.wait_for(session.inbound(text, background_units=0), timeout=65)
                row["trace_segments"] = [dataclasses.asdict(x) for x in session.host.latency_samples()]
            except Exception as exc:
                row["error"] = type(exc).__name__
            finally:
                row.update(reviews=active["reviews"], first_delivery_seconds=active["first_delivery_seconds"], wall_seconds=time.monotonic()-active["started"])
                await session.close()
                bills = usage_rows(database, first_usage)
                row["cost_cny"] = sum(x["cost_cny"] for x in bills)
                row["calls"] = len(bills)
                row["unknown_billing"] = sum(x.get("billing_state") == "unknown" for x in bills)
                row["purpose_counts"] = Counter(x["purpose"] for x in bills)
                if row["reviews"]:
                    cutoff = row["reviews"][0]["usage_ids_at_ready"]
                    row["cost_to_first_candidate_cny"] = sum(x["cost_cny"] for x in bills if x["id"] <= cutoff)
                dump(folder / "usage.json", bills)
                results.append(row)
                dump(args.output / "results.json", results)
                print(json.dumps({k: row.get(k) for k in ("case", "first_delivery_seconds", "cost_cny", "calls", "error")}), flush=True)
            if sum(r["cost_cny"] for r in results) > 5:
                raise RuntimeError("diagnostic spend guard reached; remaining cases not run")
    finally:
        reference_wire.prepare_reference_view = original_reference_view
        gate.review_grounded_candidate = original_review
        CaptureDelivery.send_text = original_send
        DeepSeekChatModel.__init__ = original_init


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--primary-user-id", required=True)
    parser.add_argument("--allow-real-provider", action="store_true", required=True)
    parser.add_argument("--case", action="append")
    parser.add_argument("--scenario", type=Path)
    parser.add_argument("--context-profile", choices=["full", "bounded"], default="full")
    args = parser.parse_args()
    args.source, args.output = args.source.resolve(), args.output.resolve()
    if not args.output.is_relative_to(ROOT / "output") or args.output.exists():
        parser.error("use a new private output directory")
    logging.basicConfig(level=logging.ERROR)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
