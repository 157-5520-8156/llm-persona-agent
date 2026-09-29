#!/usr/bin/env python3
"""Two back-to-back DeepSeek calls with production-stable prefix ordering.

Never writes ``data/``. Artifacts: ``output/cost-control/cache-live-probe.json``.

Usage::

    .venv/bin/python scripts/probe_prefix_cache_live.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

from companion_daemon.config import get_settings
from companion_daemon.llm import DeepSeekChatModel, ModelCallUsage, model_call_scope
from companion_daemon.world_v2.present_prompt import (
    order_user_present_payload,
    ordered_json_dumps,
    present_inner_life,
)
from context_truth.compile_seen import compile_seen_at_head

OUTPUT = REPO / "output" / "cost-control"
PRODUCTION_DB = REPO / "data" / "companion.epoch2.sqlite"
WORLD_ID = "world:companion-v2:qq-c2c:geoff"

# Representative static inbound system tail (mode schema now trails stable contract).
STABLE_SYSTEM = (
    "You are Celia Shen. Reply with exactly one word: ok. "
    + ("X" * 4000)
)


def _build_user_payload(*, materials: dict[str, object], trigger: str) -> str:
    material = order_user_present_payload(
        {
            "expression_capabilities": {"reply_only": True, "full_turn": True},
            "inner_life_snapshot": present_inner_life({"materials": materials}),
            "expression_hard_boundaries": {"alias_epoch": trigger, "aliases": {"S1": "src:1"}},
            "request": {"kind": "inbound_turn", "turn_id": f"probe-{trigger}"},
            "current_trigger_message": {"text": trigger, "message_id": f"msg-{trigger}"},
        }
    )
    return ordered_json_dumps(material)


async def _probe() -> dict[str, object]:
    cfg = get_settings()
    api_key = os.environ.get("DEEPSEEK_API_KEY") or cfg.deepseek_api_key
    if not api_key:
        raise SystemExit("DEEPSEEK_API_KEY missing")

    compiled = await compile_seen_at_head(
        database=PRODUCTION_DB,
        world_id=WORLD_ID,
        actor_ref="agent:companion",
        counterpart_actor_ref="user:geoff",
        timezone_name="Asia/Shanghai",
        seed_path=REPO / "configs" / "world_seed.yaml",
    )
    try:
        materials = dict(compiled.materials)
        usages: list[ModelCallUsage] = []

        def _observe(usage: ModelCallUsage) -> None:
            usages.append(usage)

        model = DeepSeekChatModel(
            api_key=api_key,
            base_url=cfg.deepseek_base_url,
            model=cfg.deepseek_model,
            thinking_enabled=False,
            max_completion_tokens=32,
            usage_observer=_observe,
        )
        results: list[dict[str, object]] = []
        for idx, trigger in enumerate(("cache-probe-a", "cache-probe-b"), start=1):
            messages = [
                {"role": "system", "content": STABLE_SYSTEM},
                {
                    "role": "user",
                    "content": _build_user_payload(materials=materials, trigger=trigger),
                },
            ]
            with model_call_scope("cache_probe"):
                await model.complete_with_usage(messages, temperature=0.2)
            usage = usages[-1]
            hit = int(usage.cache_hit_tokens or 0)
            miss = int(usage.cache_miss_tokens or 0)
            prompt = int(usage.prompt_tokens or 0)
            results.append(
                {
                    "call": idx,
                    "trigger": trigger,
                    "prompt_tokens": prompt,
                    "cache_hit_tokens": hit,
                    "cache_miss_tokens": miss,
                    "cache_hit_rate": round(hit / (hit + miss), 4) if (hit + miss) else 0.0,
                }
            )
        return {
            "stable_system_chars": len(STABLE_SYSTEM),
            "user_payload_chars": len(
                _build_user_payload(materials=materials, trigger="cache-probe-a")
            ),
            "calls": results,
            "second_call_hit_rate": results[1]["cache_hit_rate"] if len(results) > 1 else 0.0,
        }
    finally:
        for store in compiled.stores_to_close:
            close = getattr(store, "close", None)
            if callable(close):
                close()


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(_probe())
    path = OUTPUT / "cache-live-probe.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    second = report["calls"][1] if len(report["calls"]) > 1 else {}
    print(
        f"call2 hit_rate={second.get('cache_hit_rate', 0):.1%} "
        f"hit={second.get('cache_hit_tokens', 0)} miss={second.get('cache_miss_tokens', 0)} "
        f"report={path}"
    )


if __name__ == "__main__":
    main()
