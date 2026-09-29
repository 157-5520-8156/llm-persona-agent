#!/usr/bin/env python3
"""Capture the full proactive_contact provider bytes on a cloned ledger.

Does not write data/, does not restart production, does not send QQ.
Output lives under output/proactive-final/.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import importlib.util
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

OUTPUT = (REPO / "output" / "proactive-final").resolve()
CAPTURE_DIR = OUTPUT / "captures"


def _load_drive():
    spec = importlib.util.spec_from_file_location(
        "drive_production_lanes",
        REPO / "scripts" / "drive_production_lanes.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load drive_production_lanes.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _analyze_raw(raw: str) -> dict[str, object]:
    stripped = raw.strip()
    analysis: dict[str, object] = {
        "len": len(raw),
        "utf8_bytes": len(raw.encode("utf-8")),
        "startswith_fence": stripped.startswith("```"),
        "endswith_fence": stripped.endswith("```"),
        "head_80": raw[:80],
        "tail_160": raw[-160:],
        "first_nonspace": stripped[:1],
        "last_nonspace": stripped[-1:],
    }
    try:
        value = json.loads(raw)
        analysis["json_loads"] = type(value).__name__
        analysis["root_keys"] = sorted(value) if isinstance(value, dict) else None
        return analysis
    except json.JSONDecodeError as exc:
        analysis["json_loads_error"] = {
            "msg": exc.msg,
            "pos": exc.pos,
            "lineno": exc.lineno,
            "colno": exc.colno,
            "around": raw[max(0, exc.pos - 80) : exc.pos + 80],
        }
    decoder = json.JSONDecoder()
    try:
        value, end = decoder.raw_decode(stripped)
        leftover = stripped[end:].strip()
        analysis["raw_decode"] = {
            "type": type(value).__name__,
            "end": end,
            "leftover_len": len(leftover),
            "leftover_head": leftover[:120],
            "root_keys": sorted(value) if isinstance(value, dict) else None,
        }
    except json.JSONDecodeError as exc:
        analysis["raw_decode_error"] = {
            "msg": exc.msg,
            "pos": exc.pos,
            "around": stripped[max(0, exc.pos - 80) : exc.pos + 80],
        }
    fence = stripped
    if fence.startswith("```"):
        lines = fence.splitlines()
        if len(lines) >= 3 and lines[-1].strip().startswith("```"):
            inner = "\n".join(lines[1:-1]).strip()
            try:
                value = json.loads(inner)
                analysis["fence_loads"] = type(value).__name__
                analysis["fence_root_keys"] = (
                    sorted(value) if isinstance(value, dict) else None
                )
            except json.JSONDecodeError as exc:
                analysis["fence_loads_error"] = exc.msg
    return analysis


def _install_captures() -> list[dict[str, object]]:
    from companion_daemon.llm import DeepSeekChatModel
    from companion_daemon.world_v2.character_interior.structured_role_tool_contract import (
        StructuredRoleToolContract,
    )

    dumps: list[dict[str, object]] = []
    orig_complete = DeepSeekChatModel._complete
    orig_unwrap = StructuredRoleToolContract.unwrap
    orig_post_holder: dict[str, object] = {}

    async def capturing_complete(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        orig_post = self.client.post

        async def capturing_post(*post_args, **post_kwargs):  # type: ignore[no-untyped-def]
            request_json = post_kwargs.get("json")
            response = await orig_post(*post_args, **post_kwargs)
            body = None
            try:
                body = response.json()
            except Exception as exc:
                body = {"_json_error": type(exc).__name__, "_text": response.text[:4000]}
            message = None
            choice = None
            if isinstance(body, dict):
                choices = body.get("choices")
                if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                    choice = choices[0]
                    message = choice.get("message")
            arguments = None
            if isinstance(message, dict):
                calls = message.get("tool_calls")
                if isinstance(calls, list) and calls and isinstance(calls[0], dict):
                    function = calls[0].get("function")
                    if isinstance(function, dict):
                        arguments = function.get("arguments")
            record = {
                "captured_at": datetime.now(UTC).isoformat(),
                "kind": "http",
                "url": str(post_args[0]) if post_args else None,
                "status_code": response.status_code,
                "model_max_completion_tokens": self.max_completion_tokens,
                "request_max_tokens": (
                    request_json.get("max_tokens")
                    if isinstance(request_json, dict)
                    else None
                ),
                "request_max_completion_tokens": (
                    request_json.get("max_completion_tokens")
                    if isinstance(request_json, dict)
                    else None
                ),
                "thinking": (
                    request_json.get("thinking") if isinstance(request_json, dict) else None
                ),
                "tool_choice": (
                    request_json.get("tool_choice")
                    if isinstance(request_json, dict)
                    else None
                ),
                "finish_reason": choice.get("finish_reason") if isinstance(choice, dict) else None,
                "usage": body.get("usage") if isinstance(body, dict) else None,
                "content": message.get("content") if isinstance(message, dict) else None,
                "arguments": arguments,
                "arguments_analysis": (
                    _analyze_raw(arguments) if isinstance(arguments, str) else None
                ),
            }
            dumps.append(record)
            CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
            index = len(dumps)
            (CAPTURE_DIR / f"http-{index:02d}.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            if isinstance(arguments, str):
                (CAPTURE_DIR / f"http-{index:02d}.arguments.txt").write_text(
                    arguments, encoding="utf-8"
                )
            orig_post_holder["last"] = record
            return response

        self.client.post = capturing_post  # type: ignore[method-assign]
        try:
            return await orig_complete(self, *args, **kwargs)
        finally:
            self.client.post = orig_post  # type: ignore[method-assign]

    def capturing_unwrap(self, raw):  # type: ignore[no-untyped-def]
        record = {
            "captured_at": datetime.now(UTC).isoformat(),
            "kind": "unwrap",
            "purpose": self.purpose,
            "wrapper_key": self.result_wrapper_key,
            "raw": raw if isinstance(raw, str) else repr(raw),
            "analysis": _analyze_raw(raw) if isinstance(raw, str) else {"not_str": type(raw).__name__},
        }
        dumps.append(record)
        CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
        index = sum(1 for item in dumps if item.get("kind") == "unwrap")
        (CAPTURE_DIR / f"unwrap-{index:02d}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        if isinstance(raw, str):
            (CAPTURE_DIR / f"unwrap-{index:02d}.raw.txt").write_text(raw, encoding="utf-8")
        try:
            return orig_unwrap(self, raw)
        except Exception as exc:
            record["unwrap_error"] = f"{type(exc).__name__}: {exc}"
            (CAPTURE_DIR / f"unwrap-{index:02d}.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            raise

    DeepSeekChatModel._complete = capturing_complete  # type: ignore[method-assign]
    StructuredRoleToolContract.unwrap = capturing_unwrap  # type: ignore[method-assign]
    return dumps


async def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    dumps = _install_captures()
    drive = _load_drive()
    source = drive.PRODUCTION_DB
    result = await drive.drive_waiting(source=source, output_dir=OUTPUT)
    summary = {
        "finished_at": datetime.now(UTC).isoformat(),
        "drive": {
            key: result.get(key)
            for key in ("status", "outcome", "cost", "breakpoint", "error")
        },
        "capture_count": len(dumps),
        "http_captures": [
            {
                "file": f"http-{index:02d}",
                "status_code": item.get("status_code"),
                "request_max_tokens": item.get("request_max_tokens"),
                "model_max_completion_tokens": item.get("model_max_completion_tokens"),
                "finish_reason": item.get("finish_reason"),
                "usage": item.get("usage"),
                "arguments_analysis": item.get("arguments_analysis"),
            }
            for index, item in enumerate(
                (entry for entry in dumps if entry.get("kind") == "http"), start=1
            )
        ],
        "unwrap_captures": [
            {
                "file": f"unwrap-{index:02d}",
                "purpose": item.get("purpose"),
                "unwrap_error": item.get("unwrap_error"),
                "analysis": item.get("analysis"),
            }
            for index, item in enumerate(
                (entry for entry in dumps if entry.get("kind") == "unwrap"), start=1
            )
        ],
    }
    (OUTPUT / "CAPTURE_SUMMARY.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    (OUTPUT / "DRIVE_WAITING.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
