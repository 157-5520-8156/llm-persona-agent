"""Bounded, isolated native-Anthropic trial through the actual character host.

Run with PYTHONPATH=src and ANTHROPIC_API_KEY set. No production configuration
or DeepSeek budget is changed. This is a component/chat trial, not a 14-day pass.
"""

import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlsplit

import httpx

from companion_daemon.config import Settings
from companion_daemon.llm import DeepSeekChatModel
from companion_daemon.llm_anthropic import AnthropicChatModel
from companion_daemon.world_v2.character_interior.life_source_review import LifeSourceReviewer
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.interactive_turn_budget import InteractiveTurnBudgetPolicy
from companion_daemon.world_v2.longitudinal_journey import JourneyClock
from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host
from companion_daemon.world_v2.visible_independent_review_configuration import (
    GroundedVisibleReviewer,
)
from drive_production_lanes import CaptureDelivery, DriveSession


WORLD = "world:companion-v2:qq-c2c:geoff"
QUESTIONS = (
    "刚忙完，想跟你聊会儿。你今天怎么样？",
    "你小时候那次做坏的生日蛋糕，后来怎么处理的？",
    "这两天有没有什么还没做完的事情？哪些已经做了，哪些只是打算？",
)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temp.chmod(0o600)
    temp.replace(path)


class TrialAllowance:
    def __init__(self, root, *, max_calls, token_limit, live, endpoint, model):
        self.root, self.max_calls, self.token_limit = root, max_calls, token_limit
        self.live, self.endpoint, self.model = live, urlsplit(endpoint), model
        self.requests, self.usage = [], []
        self.stage = "bootstrap"
        self.reserved_tokens = 0

    def record(self, usage):
        self.usage.append({"stage": self.stage, **asdict(usage)})
        # Keep admission envelopes reserved for this short trial even after a
        # result: this remains conservative when a request is interrupted.
        save(self.root / "usage.json", self.usage)

    def admit(self, request):
        body = json.loads(request.content)
        if (
            request.url.host != self.endpoint.hostname
            or request.url.port != self.endpoint.port
            or request.url.path != "/v1/messages"
        ):
            raise ValueError("trial refused an unapproved endpoint or protocol")
        if body.get("model") != self.model:
            raise ValueError("trial refused an unapproved model")
        reserve = len(request.content) + int(body["max_tokens"]) + 1024
        if (
            len(self.requests) >= self.max_calls
            or self.reserved_tokens + reserve > self.token_limit
        ):
            raise ValueError("trial request/token allowance exhausted before send")
        row = {
            "ordinal": len(self.requests) + 1,
            "stage": self.stage,
            "model": body["model"],
            "path": request.url.path,
            "stream": bool(body.get("stream")),
            "tools": [t["name"] for t in body.get("tools", [])],
            "tool_choice": body.get("tool_choice"),
            "request_bytes": len(request.content),
            "request_sha256": hashlib.sha256(request.content).hexdigest(),
            "reserve_tokens": reserve,
            "status": "intercepted" if not self.live else "inflight",
        }
        self.requests.append(row)
        self.reserved_tokens += reserve
        save(self.root / "requests.json", self.requests)
        if not self.live:
            raise ValueError("offline request intercepted; no model call")
        return row


class CaptureTransport(httpx.AsyncBaseTransport):
    def __init__(self, allowance):
        self.allowance = allowance
        self.inner = httpx.AsyncHTTPTransport(trust_env=False)

    async def handle_async_request(self, request):
        row = self.allowance.admit(request)
        start = time.perf_counter()
        try:
            response = await self.inner.handle_async_request(request)
            row.update(
                status="response_headers",
                http_status=response.status_code,
                headers_seconds=time.perf_counter() - start,
            )
            response.stream = MetadataStream(response.stream, row, self.allowance)
            return response
        except BaseException as exc:
            row.update(status="transport_failed", error_type=type(exc).__name__)
            raise
        finally:
            save(self.allowance.root / "requests.json", self.allowance.requests)

    async def aclose(self):
        await self.inner.aclose()


class MetadataStream(httpx.AsyncByteStream):
    """Preserve streaming bytes; retain only shape, usage and a digest on disk."""

    def __init__(self, inner, row, allowance):
        self.inner, self.row, self.allowance = inner, row, allowance

    async def __aiter__(self):
        buffer, digestor, size, completed = bytearray(), hashlib.sha256(), 0, False
        try:
            async for chunk in self.inner:
                digestor.update(chunk)
                size += len(chunk)
                if size <= 262144:
                    buffer.extend(chunk)
                yield chunk
            completed = True
        finally:
            self.row.update(
                response_bytes=size, response_sha256=digestor.hexdigest(), body_complete=completed
            )
            if completed and size <= 262144:
                try:
                    value = json.loads(buffer)
                    if isinstance(value, dict):
                        blocks = value.get("content", [])
                        self.row["native_response"] = {
                            "type": value.get("type"),
                            "model": value.get("model"),
                            "stop_reason": value.get("stop_reason"),
                            "usage": value.get("usage"),
                            "block_types": [b.get("type") for b in blocks if isinstance(b, dict)],
                            "tool_names": [
                                b.get("name")
                                for b in blocks
                                if isinstance(b, dict) and b.get("type") == "tool_use"
                            ],
                            "text_lengths": [
                                len(b["text"])
                                for b in blocks
                                if isinstance(b, dict) and isinstance(b.get("text"), str)
                            ],
                        }
                except (ValueError, TypeError):
                    pass  # SSE or malformed JSON is handled by the real adapter.
            save(self.allowance.root / "requests.json", self.allowance.requests)

    async def aclose(self):
        await self.inner.aclose()


async def run(args):
    endpoint = urlsplit(args.endpoint)
    if (
        endpoint.scheme != "http"
        or endpoint.hostname not in {"localhost", "127.0.0.1"}
        or endpoint.path not in {"", "/", "/v1"}
    ):
        raise ValueError("this trial requires the explicitly configured loopback proxy")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if args.live and not api_key:
        raise ValueError("ANTHROPIC_API_KEY is required for a live trial")
    root, source = args.output.resolve(), args.source.resolve()
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    database = root / "world.sqlite"
    original_hash = digest(source)
    with (
        sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src,
        sqlite3.connect(database) as dst,
    ):
        src.backup(dst)
    with sqlite3.connect(database) as db:
        raw = db.execute(
            "SELECT event_json FROM world_v2_events WHERE world_id=? AND json_extract(event_json,'$.event_type')='ClockAdvanced' ORDER BY ledger_sequence DESC LIMIT 1",
            (WORLD,),
        ).fetchone()[0]
        event = json.loads(raw)
        origin = datetime.fromisoformat(
            json.loads(event["payload_json"])["logical_time_to"].replace("Z", "+00:00")
        )
    envs = tuple(args.env_file)
    diagnostic_settings = (
        {"WORLD_V2_INTERACTIVE_TURN_BUDGET_SECONDS": args.diagnostic_turn_budget}
        if args.diagnostic_turn_budget is not None
        else {}
    )
    settings = Settings(
        database_path=database,
        PRIMARY_USER_ID="geoff",
        DEEPSEEK_API_KEY=None,
        DEEPSEEK_DEBUG_API_KEY=None,
        WORLD_V2_REFERENCE_WIRE_ENABLED=True,
        WORLD_V2_MODEL_USAGE_BUDGET_DISABLED=True,
        WORLD_V2_LIFE_OUTLOOK_ENABLED=True,
        WORLD_V2_EXTERNAL_PERCEPTION_MODE="off",
        WORLD_V2_EXTERNAL_PERCEPTION_SIDECAR_PATH=root / "perception.sqlite",
        ATTACHMENT_CACHE_PATH=root / "attachments",
        _env_file=envs or None,
        **diagnostic_settings,
    )
    effective = {
        "database_is_copy": settings.database_path.resolve() == database,
        "reference_wire": settings.world_v2_reference_wire_enabled,
        "subscription_trial_disables_money_limits": settings.world_v2_model_usage_budget_disabled,
        "external_perception": settings.world_v2_external_perception_mode,
        "ordinary_text_review_mode": settings.world_v2_ordinary_text_review_mode,
        "deepseek_credentials_absent": not settings.deepseek_api_key
        and not settings.deepseek_debug_api_key,
    }
    assert effective == {
        "database_is_copy": True,
        "reference_wire": True,
        "subscription_trial_disables_money_limits": True,
        "external_perception": "off",
        "ordinary_text_review_mode": "sampled",
        "deepseek_credentials_absent": True,
    }
    for url in (
        settings.world_v2_recall_embedding_base_url,
        settings.world_v2_text_endpoint_base_url,
    ):
        assert urlsplit(url or "").hostname in {"localhost", "127.0.0.1", "::1"}
    save(
        root / "protocol.json",
        {
            "source": str(source),
            "source_sha256": original_hash,
            "model": args.model,
            "endpoint": args.endpoint,
            "protocol": "anthropic-messages",
            "effective_settings": effective,
            "max_calls": args.max_calls,
            "reserve_token_limit": args.token_limit,
            "questions": QUESTIONS,
            "ordinary_inbound": True,
            "sender_pacing": "simulated",
            "money_accounting": "not_priced; user-supplied subscription",
            "whole_long_story_qualification": False,
            "phase": args.phase,
            "interactive_turn_budget_seconds": settings.world_v2_interactive_turn_budget_seconds,
            "latency_qualification": args.diagnostic_turn_budget is None,
            "world_author_cache_stable_prefix": False,
            "code_sha256": {
                str(p): digest(p)
                for p in (
                    Path(__file__),
                    Path("src/companion_daemon/llm.py"),
                    Path("src/companion_daemon/llm_anthropic.py"),
                )
            },
        },
    )
    allowance = TrialAllowance(
        root,
        max_calls=args.max_calls,
        token_limit=args.token_limit,
        live=args.live,
        endpoint=args.endpoint,
        model=args.model,
    )
    models = []
    os.environ["COMPANION_DISABLE_DEBUG_USAGE_LEDGER"] = "1"
    original_init = DeepSeekChatModel.__init__

    def guarded_init(self, *values, **kwargs):
        if type(self) is DeepSeekChatModel:
            raise ValueError("DeepSeek is disabled in this trial")
        return original_init(self, *values, **kwargs)

    DeepSeekChatModel.__init__ = guarded_init

    def model(max_tokens):
        value = AnthropicChatModel(
            api_key or "offline",
            args.endpoint,
            args.model,
            max_completion_tokens=max_tokens,
            transport=CaptureTransport(allowance),
            usage_observer=allowance.record,
        )
        models.append(value)
        return value

    host = None
    store = SQLiteImmutableLifeContentStore(path=str(database), world_id=WORLD)
    result = {"completed": False, "chats": [], "memory": [], "outlook": None, "error": None}
    started = time.perf_counter()
    try:
        clock, delivery = JourneyClock(origin), CaptureDelivery()
        author, background, reviewer = model(4096), model(8192), model(8192)
        host = build_qq_c2c_host(
            settings=settings,
            recipient_id="world-v2-console-user",
            bootstrap_at=origin,
            model=author,
            world_support_model=background,
            world_author_cache_stable_prefix=False,
            visible_source_review_model=GroundedVisibleReviewer(source_model=reviewer),
            life_source_reviewer=LifeSourceReviewer(model=reviewer, evidence_store=store),
            delivery=delivery,
            ingress_now=clock.presentation_now,
            ingress_sleep=clock.presentation_sleep,
            action_due_now=clock.now,
            action_due_sleep=clock.timer_sleep,
            interactive_turn_budget_policy=InteractiveTurnBudgetPolicy(
                total_seconds=settings.world_v2_interactive_turn_budget_seconds,
                hedge_after_seconds=settings.world_v2_interactive_hedge_after_seconds,
                wall_clock=clock.presentation_now,
            ),
        )
        result["bootstrap_seconds"] = time.perf_counter() - started
        session = DriveSession(
            database=database,
            recipient_id="world-v2-console-user",
            delivery=delivery,
            host=host,
            clock=origin,
            journey_clock=clock,
        )
        app = host._host._application
        result["initial_prehistory"] = len(app._ledger.project().prehistory_records)
        questions = QUESTIONS[: args.chat_count] if args.phase in {"all", "chat"} else ()
        for index, question in enumerate(questions):
            allowance.stage = f"ordinary-chat-{index + 1}"
            item = await session.inbound(question, background_units=0)
            result["chats"].append({"input": question, **item})
            save(root / "progress.json", result)
            print(
                json.dumps(
                    {"stage": allowance.stage, "status": item["status"], "timing": item["timing"]},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if not args.live:
                break
        if args.live and args.phase in {"all", "outlook"}:
            allowance.stage = "world-outlook-component"
            projection = app._ledger.project()
            wake = next(
                e
                for e in reversed(projection.committed_world_event_refs)
                if e.event_type == "ClockAdvanced"
            )
            result[
                "outlook"
            ] = await app._life_ecology._life_development_followup.prepare_outlook_once(
                wake_event_ref=wake.event_id
            )
            save(root / "outlook-view.json", app._life_ecology._world_outlook.view())
        if args.live and args.phase in {"all", "memory"}:
            for index in range(args.memory_batches):
                allowance.stage = f"memory-component-{index + 1}"
                item = await app._turns._runtime._memory_consolidation.drain_one()
                result["memory"].append(item.model_dump(mode="json"))
                if item.work_status == "technical_failure":
                    break
        result["completed"] = True
    except BaseException as exc:
        result["error"] = {"type": type(exc).__name__, "detail": str(exc)[:500]}
        raise
    finally:
        if host is not None:
            app = host._host._application
            result["final_prehistory"] = len(app._ledger.project().prehistory_records)
            await host.aclose()
        for value in models:
            await value.aclose()
        store.close()
        DeepSeekChatModel.__init__ = original_init
        result.update(
            elapsed_seconds=time.perf_counter() - started,
            requests=len(allowance.requests),
            usage=allowance.usage,
            source_unchanged=digest(source) == original_hash,
        )
        save(root / "result.json", result)
        print(
            json.dumps(
                {
                    "completed": result["completed"],
                    "error": result["error"],
                    "requests": result["requests"],
                }
            ),
            flush=True,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--env-file", action="append", default=[], type=Path)
    parser.add_argument("--endpoint", default="http://localhost:8788")
    parser.add_argument("--model", default="gemini-3.8-flash-medium")
    parser.add_argument("--max-calls", type=int, default=14)
    parser.add_argument("--token-limit", type=int, default=1_000_000)
    parser.add_argument("--memory-batches", type=int, default=2)
    parser.add_argument("--phase", choices=("all", "chat", "outlook", "memory"), default="all")
    parser.add_argument("--chat-count", type=int, default=3)
    parser.add_argument("--diagnostic-turn-budget", type=float)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.max_calls <= 0 or args.token_limit <= 0 or not 0 <= args.memory_batches <= 8:
        parser.error("positive bounded allowances and 0..8 memory batches required")
    if not 0 <= args.chat_count <= 3:
        parser.error("chat count must be 0..3")
    if args.diagnostic_turn_budget is not None and not (
        0 < args.diagnostic_turn_budget <= 30 and args.phase == "chat" and args.chat_count == 1
    ):
        parser.error("a changed deadline is only permitted for one explicitly diagnostic chat")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
