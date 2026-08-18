#!/usr/bin/env python3
"""Idle isolation bench for inbound-lock tests.

Measures the same wall-clock windows as the two performance assertions in
``tests/world_v2/test_background_does_not_block_ingress.py`` without running
the full suite.  Import path is ``--repo`` so a clean HEAD worktree and the
dirty working tree can be compared with one harness.

Never writes ``data/``.  Artifacts go to ``output/ingress-perf/``.
"""

from __future__ import annotations

import argparse
import asyncio
import cProfile
from datetime import timedelta
import importlib
import json
import os
from pathlib import Path
import pstats
import statistics
import sys
import tempfile
import time
import traceback
from typing import Any, Callable
from unittest.mock import patch

REPO_DEFAULT = Path(__file__).resolve().parents[1]
OUTPUT_DEFAULT = REPO_DEFAULT / "output" / "ingress-perf"


def _loadavg() -> list[float]:
    try:
        return [round(value, 3) for value in os.getloadavg()]
    except OSError:
        return []


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def _dist(values: list[float]) -> dict[str, float]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "min": min(values),
        "median": statistics.median(values),
        "p90": _percentile(values, 0.90),
        "max": max(values),
        "mean": statistics.fmean(values),
    }


def _bind_repo(repo: Path) -> None:
    repo = repo.resolve()
    for key in ("src", "tests", "tests/support", "tests/world_v2"):
        path = str(repo / key)
        if path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)
    for name in list(sys.modules):
        if name == "companion_daemon" or name.startswith("companion_daemon."):
            del sys.modules[name]
        if name.startswith("test_background_does_not_block_ingress"):
            del sys.modules[name]
        if name == "compact_source_review_fixture":
            del sys.modules[name]


class _CallCounter:
    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.complete_calls = 0
        self.stream_calls = 0
        self.bytes_in = 0
        self.last_system_bytes = 0
        self.purposes: list[str] = []

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if name in {
            "complete_json_stream_with_usage",
            "complete_with_usage",
            "complete_json_with_usage",
        } and callable(attr):

            async def wrapped(messages, **kwargs):  # type: ignore[no-untyped-def]
                if name == "complete_json_stream_with_usage":
                    self.stream_calls += 1
                self._note(messages)
                return await attr(messages, **kwargs)

            return wrapped
        return attr

    def _note(self, messages: Any) -> None:
        self.complete_calls += 1
        blob = "\n".join(str(item.get("content", "")) for item in messages)
        self.bytes_in += len(blob.encode("utf-8"))
        system = str(messages[0]["content"]) if messages else ""
        self.last_system_bytes = len(system.encode("utf-8"))
        if "COMBINED OUTPUT ENVELOPE" in system or "result_kind" in system:
            self.purposes.append("character")
        elif "candidate-external-proposition-inventory" in system:
            self.purposes.append("inventory")
        elif "factual source-boundary classifier" in system or "Audit only factual" in system:
            self.purposes.append("source_closure")
        else:
            self.purposes.append("other")

    async def complete(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        self._note(messages)
        return await self._inner.complete(messages, **kwargs)


class _SpanBook:
    def __init__(self) -> None:
        self.spans: dict[str, float] = {}
        self.counts: dict[str, int] = {}

    def add(self, name: str, seconds: float) -> None:
        self.spans[name] = self.spans.get(name, 0.0) + seconds
        self.counts[name] = self.counts.get(name, 0) + 1


def _wrap_sync(book: _SpanBook, name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
    def wrapped(*args, **kwargs):  # type: ignore[no-untyped-def]
        started = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            book.add(name, time.perf_counter() - started)

    return wrapped


def _wrap_async(book: _SpanBook, name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
    async def wrapped(*args, **kwargs):  # type: ignore[no-untyped-def]
        started = time.perf_counter()
        try:
            return await fn(*args, **kwargs)
        finally:
            book.add(name, time.perf_counter() - started)

    return wrapped


def _install_spans(book: _SpanBook) -> list[Any]:
    patches: list[Any] = []

    def maybe(module_name: str, attr: str, *, is_async: bool) -> None:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            return
        target = getattr(module, attr, None)
        if target is None:
            return
        wrapper = _wrap_async if is_async else _wrap_sync
        patcher = patch.object(module, attr, wrapper(book, attr, target))
        patcher.start()
        patches.append(patcher)

    maybe(
        "companion_daemon.world_v2.character_interior.snapshot_compiler",
        "compile_inner_life_snapshot",
        is_async=False,
    )
    maybe(
        "companion_daemon.world_v2.character_interior.snapshot_compiler",
        "compile_citeable_source_catalog",
        is_async=False,
    )
    maybe(
        "companion_daemon.world_v2.character_interior.snapshot_compiler",
        "citeable_source_labels",
        is_async=False,
    )
    maybe("companion_daemon.world_v2.present_prompt", "order_user_present_payload", is_async=False)
    maybe("companion_daemon.world_v2.present_prompt", "present_inner_life", is_async=False)
    maybe(
        "companion_daemon.world_v2.present_prompt",
        "reply_only_slim_shape_specimen",
        is_async=False,
    )
    maybe(
        "companion_daemon.world_v2.present_prompt",
        "slim_consider_instruction",
        is_async=False,
    )
    maybe(
        "companion_daemon.world_v2.character_interior.inbound_author",
        "_compact_gate_system_content",
        is_async=False,
    )
    maybe(
        "companion_daemon.world_v2.character_interior.inbound_author",
        "_parse_combined",
        is_async=False,
    )
    maybe(
        "companion_daemon.world_v2.character_interior.inbound_author",
        "_validate_cognition_visible_spans",
        is_async=False,
    )
    maybe(
        "companion_daemon.world_v2.character_interior.inbound_author",
        "_validate_materialized_cognition_visible_spans",
        is_async=False,
    )
    maybe(
        "companion_daemon.world_v2.character_interior.inbound_author",
        "materialize_expression_draft",
        is_async=False,
    )
    maybe(
        "companion_daemon.world_v2.media_selection_occasion",
        "compile_candidate_occasion",
        is_async=False,
    )
    maybe(
        "companion_daemon.world_v2.character_interior.production",
        "drain_private_impression_once",
        is_async=True,
    )
    try:
        core = importlib.import_module("companion_daemon.world_v2.character_interior.core")
        interior = getattr(core, "CharacterInterior")
        patcher = patch.object(
            interior, "consider", _wrap_async(book, "consider", interior.consider)
        )
        patcher.start()
        patches.append(patcher)
        drain = getattr(interior, "_drain_private_impression_once", None)
        if drain is not None:
            patcher = patch.object(
                interior,
                "_drain_private_impression_once",
                _wrap_async(book, "_drain_private_impression_once", drain),
            )
            patcher.start()
            patches.append(patcher)
    except Exception:
        pass
    try:
        pinned = importlib.import_module("companion_daemon.world_v2.pinned_turn")
        compiler = getattr(pinned, "PinnedTurnCompiler")
        original = compiler.audit_observation
        patcher = patch.object(
            compiler, "audit_observation", _wrap_async(book, "audit_observation", original)
        )
        patcher.start()
        patches.append(patcher)
    except Exception:
        pass
    try:
        runtime = importlib.import_module("companion_daemon.world_v2.runtime")
        cls = getattr(runtime, "WorldRuntime")
        original = cls.ingest
        patcher = patch.object(cls, "ingest", _wrap_async(book, "ingest", original))
        patcher.start()
        patches.append(patcher)
        hitch = getattr(cls, "_hitch_paid_inbound_declared_display", None)
        if hitch is not None:
            patcher = patch.object(
                cls,
                "_hitch_paid_inbound_declared_display",
                _wrap_sync(book, "_hitch_paid_inbound_declared_display", hitch),
            )
            patcher.start()
            patches.append(patcher)
        drain = getattr(cls, "drain_background_once", None)
        if drain is not None:
            patcher = patch.object(
                cls, "drain_background_once", _wrap_async(book, "drain_background_once", drain)
            )
            patcher.start()
            patches.append(patcher)
    except Exception:
        pass
    return patches


def _latency_breakdown(samples: Any) -> dict[str, float]:
    grouped: dict[str, float] = {}
    for sample in samples:
        grouped[sample.segment] = grouped.get(sample.segment, 0.0) + float(sample.duration_ms)
    return grouped


def _prompt_sizes() -> dict[str, int]:
    sizes: dict[str, int] = {}
    try:
        present = importlib.import_module("companion_daemon.world_v2.present_prompt")
        specimen = getattr(present, "reply_only_slim_shape_specimen", None)
        instruction = getattr(present, "slim_consider_instruction", None)
        if callable(specimen):
            blob = json.dumps(specimen(), ensure_ascii=False, separators=(",", ":"))
            sizes["slim_specimen_bytes"] = len(blob.encode("utf-8"))
            sizes["slim_specimen_keys"] = len(specimen())
        if callable(instruction):
            sizes["slim_instruction_bytes"] = len(instruction().encode("utf-8"))
    except Exception:
        pass
    try:
        author = importlib.import_module(
            "companion_daemon.world_v2.character_interior.inbound_author"
        )
        builder = getattr(author, "_compact_gate_system_content", None)
        if callable(builder):
            text = builder(
                identity_instruction="fixture-identity",
                reply_only_specimen={},
                reply_only_rules={},
                full_turn_specimen={},
                full_turn_rules={},
            )
            sizes["compact_gate_system_bytes"] = len(text.encode("utf-8"))
    except Exception:
        pass
    return sizes


async def _run_lock_test(tmp: Path, fixtures: Any) -> dict[str, Any]:
    from companion_daemon.config import Settings
    from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host

    tmp.mkdir(parents=True, exist_ok=True)

    background = fixtures._BlockingBackgroundModel()
    infrastructure = _CallCounter(fixtures._FastInfrastructureModel())
    model = _CallCounter(fixtures._FastReplyModel())
    delivery = fixtures._Delivery()
    book = _SpanBook()
    patches = _install_spans(book)
    host = build_qq_c2c_host(
        settings=Settings(
            database_path=tmp / "background-nonblocking.sqlite",
            PRIMARY_USER_ID="geoff",
        ),
        recipient_id="10001",
        bootstrap_at=fixtures.NOW,
        model=model,
        world_support_model=background,
        source_closure_model=infrastructure,
        delivery=delivery,
        use_configured_recall_embedding=False,
    )
    background_task = None
    try:
        first = await host.inbound_text(
            message_id="message:one",
            recipient_id="10001",
            text="你好",
            observed_at=fixtures.NOW,
        )
        book.spans.clear()
        book.counts.clear()
        model.complete_calls = 0
        model.stream_calls = 0
        model.bytes_in = 0
        model.purposes.clear()
        infrastructure.complete_calls = 0
        infrastructure.bytes_in = 0
        infrastructure.purposes.clear()
        before_samples = host.latency_samples()
        background_task = asyncio.create_task(
            host.scheduler_once(
                observed_at=fixtures.NOW + timedelta(minutes=1),
                max_action_units=0,
                max_background_units=1,
            )
        )
        await asyncio.wait_for(background.started.wait(), timeout=2)
        loop = asyncio.get_running_loop()
        cpu_before = time.process_time()
        started = loop.time()
        second = await asyncio.wait_for(
            host.inbound_text(
                message_id="message:two",
                recipient_id="10001",
                text="还在吗？",
                observed_at=fixtures.NOW + timedelta(minutes=1),
            ),
            timeout=8,
        )
        elapsed = loop.time() - started
        cpu = time.process_time() - cpu_before
        after_samples = host.latency_samples()
        latency = _latency_breakdown(after_samples[len(before_samples) :])
        coalescing = float(latency.get("coalescing", 0.0))
        return {
            "elapsed_s": elapsed,
            "cpu_s": cpu,
            "work_s": max(0.0, elapsed - coalescing / 1000.0),
            "status": [first.status, second.status],
            "sent": list(delivery.sent),
            "background_done": background_task.done(),
            "model_complete_calls": model.complete_calls,
            "model_stream_calls": model.stream_calls,
            "model_bytes_in": model.bytes_in,
            "model_last_system_bytes": model.last_system_bytes,
            "model_purposes": list(model.purposes),
            "infra_complete_calls": infrastructure.complete_calls,
            "infra_bytes_in": infrastructure.bytes_in,
            "infra_purposes": list(infrastructure.purposes),
            "latency_ms": latency,
            "spans_ms": {name: round(value * 1000.0, 3) for name, value in book.spans.items()},
            "span_counts": dict(book.counts),
        }
    finally:
        background.release.set()
        if background_task is not None:
            await asyncio.wait_for(background_task, timeout=8)
        await host.aclose()
        for patcher in patches:
            patcher.stop()


async def _run_emotion_test(tmp: Path, fixtures: Any) -> dict[str, Any]:
    from companion_daemon.config import Settings
    from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host
    from companion_daemon.world_v2 import semantic_chat_composition

    tmp.mkdir(parents=True, exist_ok=True)

    infrastructure = _CallCounter(fixtures._LocalAppraisalInfrastructureModel())
    model = _CallCounter(fixtures._FastReplyModel())
    delivery = fixtures._Delivery()
    book = _SpanBook()
    patches = _install_spans(book)
    with patch.object(
        semantic_chat_composition,
        "OpenAICompatibleChatModel",
        lambda **_kwargs: infrastructure,
    ):
        host = build_qq_c2c_host(
            settings=Settings(
                database_path=tmp / "half-dead-local-gate.sqlite",
                PRIMARY_USER_ID="geoff",
                WORLD_V2_TEXT_ENDPOINT_ENABLED=True,
            ),
            recipient_id="10001",
            bootstrap_at=fixtures.NOW,
            model=model,
            source_closure_model=infrastructure,
            delivery=delivery,
            use_configured_recall_embedding=False,
        )
        try:
            loop = asyncio.get_running_loop()
            cpu_before = time.process_time()
            started = loop.time()
            result = await asyncio.wait_for(
                host.inbound_text(
                    message_id="message:half-dead-gate",
                    recipient_id="10001",
                    text="我今天有点难过。",
                    observed_at=fixtures.NOW,
                ),
                timeout=8,
            )
            elapsed = loop.time() - started
            cpu = time.process_time() - cpu_before
            latency = _latency_breakdown(host.latency_samples())
            coalescing = float(latency.get("coalescing", 0.0))
            return {
                "elapsed_s": elapsed,
                "cpu_s": cpu,
                "work_s": max(0.0, elapsed - coalescing / 1000.0),
                "status": result.status,
                "sent": list(delivery.sent),
                "appraisal_calls": infrastructure._inner.appraisal_calls,
                "model_complete_calls": model.complete_calls,
                "model_stream_calls": model.stream_calls,
                "model_bytes_in": model.bytes_in,
                "model_last_system_bytes": model.last_system_bytes,
                "model_purposes": list(model.purposes),
                "infra_complete_calls": infrastructure.complete_calls,
                "infra_bytes_in": infrastructure.bytes_in,
                "infra_purposes": list(infrastructure.purposes),
                "latency_ms": latency,
                "spans_ms": {name: round(value * 1000.0, 3) for name, value in book.spans.items()},
                "span_counts": dict(book.counts),
            }
        finally:
            await host.aclose()
            for patcher in patches:
                patcher.stop()


async def _one_round(fixtures: Any) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="ingress-lock-") as raw:
        tmp = Path(raw)
        lock = await _run_lock_test(tmp / "lock", fixtures)
        emotion = await _run_emotion_test(tmp / "emotion", fixtures)
        return {"lock": lock, "emotion": emotion}


def _summarize(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    elapsed = [float(row[key]["elapsed_s"]) for row in rows]
    cpu = [float(row[key]["cpu_s"]) for row in rows]
    work = [float(row[key]["work_s"]) for row in rows]
    fail_05 = sum(1 for value in elapsed if value >= 0.5)
    return {
        "elapsed_s": _dist(elapsed),
        "cpu_s": _dist(cpu),
        "work_s": _dist(work),
        "fail_elapsed_ge_0_5": fail_05,
        "elapsed_values": elapsed,
        "cpu_values": cpu,
        "work_values": work,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=REPO_DEFAULT)
    parser.add_argument("--label", required=True)
    parser.add_argument("--runs", type=int, default=12)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    repo = args.repo.resolve()
    _bind_repo(repo)
    fixtures = importlib.import_module("test_background_does_not_block_ingress")
    prompt_sizes = _prompt_sizes()
    if args.warmup:
        asyncio.run(_one_round(fixtures))
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for index in range(args.runs):
        try:
            rows.append(asyncio.run(_one_round(fixtures)))
        except Exception:
            errors.append(f"run {index}: {traceback.format_exc()}")
    profile_path = None
    if args.profile:
        profiler = cProfile.Profile()
        profiler.enable()
        asyncio.run(_one_round(fixtures))
        profiler.disable()
        profile_path = OUTPUT_DEFAULT / f"{args.label}.cprofile.txt"
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        with profile_path.open("w", encoding="utf-8") as handle:
            stats = pstats.Stats(profiler, stream=handle)
            stats.sort_stats("cumulative")
            stats.print_stats(80)
            handle.write("\n\n--- time ---\n")
            stats.sort_stats("tottime")
            stats.print_stats(80)
    payload = {
        "label": args.label,
        "repo": str(repo),
        "loadavg": _loadavg(),
        "pid": os.getpid(),
        "prompt_sizes": prompt_sizes,
        "warmup": args.warmup,
        "runs": rows,
        "errors": errors,
        "summary": {
            "lock": _summarize(rows, "lock") if rows else {},
            "emotion": _summarize(rows, "emotion") if rows else {},
        },
        "profile": str(profile_path) if profile_path else None,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    out = args.out or (OUTPUT_DEFAULT / f"{args.label}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
