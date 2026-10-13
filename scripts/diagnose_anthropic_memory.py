"""Freeze one real memory request, vary one wire factor, validate with the original host.

Diagnostic only: clone a supplied ledger, never deliver or modify production, and
never persist prompt text. The full packet lives in memory for every comparison.
"""

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import random
import sqlite3
import time

import httpx

from companion_daemon.config import Settings
from companion_daemon.llm import ModelCallUsage, _arguments_content, _model_usage_token_counts
from companion_daemon.llm_anthropic import AnthropicChatModel, _usage
from companion_daemon.subscription_usage import SubscriptionTokenLedger
from companion_daemon.world_v2.character_interior.core import CharacterInterior
from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host
from drive_production_lanes import CaptureDelivery
from trial_anthropic_proxy import WORLD, digest, save


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class CapturedPacket(Exception):
    pass


async def freeze(args, root):
    source, target = args.source.resolve(), root / "capture.sqlite"
    before = digest(source)
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)
    with sqlite3.connect(target) as connection:
        row = connection.execute(
            "SELECT event_json FROM world_v2_events WHERE world_id=? "
            "AND json_extract(event_json,'$.event_type')='ClockAdvanced' "
            "ORDER BY ledger_sequence DESC LIMIT 1",
            (WORLD,),
        ).fetchone()
        origin = datetime.fromisoformat(
            json.loads(json.loads(row[0])["payload_json"])["logical_time_to"].replace("Z", "+00:00")
        )
    config = Settings(
        _env_file=tuple(args.env_file),
        database_path=target,
        DEEPSEEK_API_KEY=None,
        DEEPSEEK_DEBUG_API_KEY="capture-only-no-network",
        WORLD_V2_BACKGROUND_PROVIDER="anthropic_subscription",
        WORLD_V2_BACKGROUND_API_KEY="local-proxy",
        WORLD_V2_BACKGROUND_MODEL=args.model,
        WORLD_V2_REFERENCE_WIRE_ENABLED=True,
        WORLD_V2_EXTERNAL_PERCEPTION_MODE="off",
        WORLD_V2_EXTERNAL_PERCEPTION_SIDECAR_PATH=root / "perception.sqlite",
        ATTACHMENT_CACHE_PATH=root / "attachments",
    )
    os.environ["COMPANION_DISABLE_DEBUG_USAGE_LEDGER"] = "1"
    host = build_qq_c2c_host(
        settings=config,
        recipient_id="world-v2-console-user",
        bootstrap_at=origin,
        delivery=CaptureDelivery(),
    )
    captured = {}
    try:
        for model in host._semantic_chat._owned_models:
            if model.provider in {"deepseek", "anthropic"}:
                await model.client.aclose()

                def forbidden(request):
                    raise AssertionError("capture must not invoke any remote provider")

                model.client = httpx.AsyncClient(transport=httpx.MockTransport(forbidden))
        role = host._semantic_chat.character_interior._registry.for_purpose("memory_consolidation")
        original_consider = role.consider

        async def consider(request):
            captured["request"] = request
            return await original_consider(request)

        async def complete(messages, **kwargs):
            captured["messages"], captured["kwargs"] = deepcopy(messages), deepcopy(kwargs)
            captured["body"] = role._model.request_payload(messages, **kwargs)
            raise CapturedPacket("outbound frozen before transport")

        role.consider = consider
        role._model.complete_json = complete
        outcome = await host._host._application._turns._runtime._memory_consolidation.drain_one()
        assert captured and outcome.work_status == "technical_failure"
        captured["role"] = role
        captured["source_unchanged"] = digest(source) == before
        captured["source_sha256"] = before
        assert captured["source_unchanged"]
        return captured
    finally:
        await host.aclose()


def variant_body(original, variant):
    body = deepcopy(original)
    body["max_tokens"] = {"cap4096": 4096, "cap16384": 16384}.get(variant, 64000)
    if variant == "flat":
        for tool in body["tools"]:
            schema = tool["input_schema"]
            if (
                set(schema) == {"type", "anyOf"}
                and schema["type"] == "object"
                and len(schema["anyOf"]) == 1
                and schema["anyOf"][0].get("type") == "object"
            ):
                tool["input_schema"] = schema["anyOf"][0]
    elif variant == "no_strict":
        for tool in body["tools"]:
            tool.pop("strict", None)
    elif variant == "any":
        body["tool_choice"] = {"type": "any", "disable_parallel_tool_use": True}
    elif variant == "temperature1":
        body["temperature"] = 1.0
    elif variant not in {"cap4096", "cap16384", "cap64000"}:
        raise ValueError("unknown diagnostic variant")
    return body


async def evaluate(captured, value, parser):
    message = parser._response_message(value)
    raw = _arguments_content(
        message["tool_calls"], expected_tool_name=captured["body"]["tools"][0]["name"]
    )
    if raw is None:
        raise ValueError("required_tool_result_absent")

    async def replay(*args, **kwargs):
        return raw

    role = captured["role"]
    role._model.complete_json = replay
    result = await role._complete(captured["request"])
    validated = CharacterInterior._validate_role_result(
        result,
        snapshot=captured["request"].snapshot,
        request=captured["request"],
        allowed_statuses={"decision", "recall_request"},
        require_author_lineage=True,
    )
    return {"status": validated.status, "attended_sources": len(validated.attended_source_refs)}


async def run(args):
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    captured = await freeze(args, root)
    variants = (
        ["cap4096", "cap16384", "cap64000"]
        if args.matrix == "caps"
        else ["cap64000", "flat", "no_strict", "any", "temperature1"]
    )
    cases = [(v, i) for i in range(args.repeats) for v in variants]
    random.Random(1013).shuffle(cases)
    protocol = {
        "contract": "frozen-anthropic-memory-differential.1",
        "matrix": args.matrix,
        "model": args.model,
        "max_requests": len(cases),
        "reserve_token_limit": 3_000_000,
        "observation_timeout_seconds": 180,
        "cases": cases,
        "source_sha256": captured["source_sha256"],
        "source_unchanged": captured["source_unchanged"],
        "packet_sha256": sha(captured["body"]),
        "semantic_input_sha256": sha(
            {k: v for k, v in captured["body"].items() if k != "max_tokens"}
        ),
        "schema_sha256": sha(captured["body"]["tools"]),
        "schema_bytes": len(canonical(captured["body"]["tools"]).encode()),
        "message_bytes": len(
            canonical([captured["body"].get("system"), captured["body"]["messages"]]).encode()
        ),
        "tool_choice": captured["body"]["tool_choice"],
        "production_mutation": False,
        "prompt_persisted": False,
        "quality_qualified": False,
    }
    save(root / "protocol.json", protocol)
    if args.frozen_protocol:
        expected = json.loads(args.frozen_protocol.read_text())
        for key in ("source_sha256", "semantic_input_sha256", "schema_sha256"):
            if protocol[key] != expected[key]:
                raise ValueError("frozen input drift: " + key)
    if not args.live:
        print(
            json.dumps(
                {
                    "captured": True,
                    **{k: protocol[k] for k in ("schema_bytes", "message_bytes", "packet_sha256")},
                }
            )
        )
        return
    ledger = SubscriptionTokenLedger(
        root / "tokens.sqlite", five_hour_tokens=3_000_000, weekly_tokens=3_000_000
    )
    rows = []
    completed = False
    parser = AnthropicChatModel("local-proxy", "http://localhost:8788", args.model)
    try:
        async with httpx.AsyncClient(timeout=180, trust_env=False) as client:
            for index, (variant, repeat) in enumerate(cases):
                body = variant_body(captured["body"], variant)
                reservation = ledger.reserve(
                    purpose="memory_diagnostic",
                    model=args.model,
                    tokens=len(canonical(body).encode()) + body["max_tokens"] + 1024,
                )
                row = {
                    "index": index,
                    "variant": variant,
                    "repeat": repeat,
                    "request_sha256": sha(body),
                    "max_tokens": body["max_tokens"],
                    "status": "running",
                }
                rows.append(row)
                save(root / "progress.json", {"rows": rows})
                started = time.monotonic()
                usage = {}
                status = "failed"
                try:
                    async with asyncio.timeout(180):
                        response = await client.post(
                            "http://localhost:8788/v1/messages",
                            json=body,
                            headers={"x-api-key": "local-proxy", "anthropic-version": "2023-06-01"},
                        )
                    row["http_status"] = response.status_code
                    response.raise_for_status()
                    value = response.json()
                    usage = _usage(value.get("usage"))
                    blocks = value.get("content", [])
                    row.update(
                        stop_reason=value.get("stop_reason"),
                        reported_usage=value.get("usage"),
                        block_types=[b.get("type") for b in blocks],
                        response_sha256=sha(value),
                        tool_names=[b.get("name") for b in blocks if b.get("type") == "tool_use"],
                    )
                    # Store only authored tool/text output, never thought blocks or prompt text.
                    save(
                        root / f"response-{index:02}.json",
                        {
                            "content": [b for b in blocks if b.get("type") in {"text", "tool_use"}],
                            "stop_reason": value.get("stop_reason"),
                        },
                    )
                    row["validation"] = await evaluate(captured, value, parser)
                    row["status"] = "pass"
                    status = "succeeded"
                except Exception as exc:
                    row.update(
                        status="fail",
                        error_type=type(exc).__name__,
                        failure_code=getattr(exc, "code", str(exc))[:240],
                    )
                finally:
                    if row["status"] == "running":
                        row["status"] = "interrupted"
                    row["seconds"] = time.monotonic() - started
                    known = all(
                        type(usage.get(k)) is int for k in ("prompt_tokens", "completion_tokens")
                    )
                    ledger.record(
                        ModelCallUsage(
                            purpose="memory_diagnostic",
                            model=args.model,
                            provider="anthropic",
                            status=status,
                            latency_ms=int(row["seconds"] * 1000),
                            budget_reservation_id=reservation,
                            billing_state="known" if known else "unknown",
                            **_model_usage_token_counts(usage),
                        )
                    )
                    save(root / "progress.json", {"rows": rows, "tokens": ledger.snapshot()})
                    print(
                        json.dumps(
                            {
                                k: row.get(k)
                                for k in (
                                    "index",
                                    "variant",
                                    "status",
                                    "seconds",
                                    "stop_reason",
                                    "failure_code",
                                )
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
        completed = True
    finally:
        save(
            root / "result.json",
            {
                "completed": completed,
                "rows": rows,
                "tokens": ledger.snapshot(),
                "source_unchanged": digest(args.source) == captured["source_sha256"],
            },
        )
        await parser.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, action="append", default=[])
    parser.add_argument("--model", default="gemini-3.8-flash-medium")
    parser.add_argument("--matrix", choices=("caps", "schema"), default="caps")
    parser.add_argument("--repeats", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--frozen-protocol", type=Path)
    asyncio.run(run(parser.parse_args()))
