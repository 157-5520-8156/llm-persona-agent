#!/usr/bin/env python3
"""Clone-only probe: half-written slim pairs get one same-Occasion reselection.

Never writes ``data/``, never talks to 8787 or NapCat. Output lives in
``output/paired-reselection/``.

Usage::

    .venv/bin/python scripts/probe_paired_reselection.py
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import importlib.util
import json
import logging
from pathlib import Path
import sys
from typing import Any, Callable, Mapping

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

OUTPUT = (REPO / "output" / "paired-reselection").resolve()
_LOG = logging.getLogger("probe_paired_reselection")

_DRIVE_PATH = REPO / "scripts" / "drive_production_lanes.py"
_spec = importlib.util.spec_from_file_location("drive_production_lanes", _DRIVE_PATH)
drive = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["drive_production_lanes"] = drive
_spec.loader.exec_module(drive)

_SWITCH_PATH = REPO / "scripts" / "probe_her_switches.py"
_switch_spec = importlib.util.spec_from_file_location("probe_her_switches", _SWITCH_PATH)
switches = importlib.util.module_from_spec(_switch_spec)
assert _switch_spec.loader is not None
sys.modules["probe_her_switches"] = switches
_switch_spec.loader.exec_module(switches)

from companion_daemon.llm import (  # noqa: E402
    MAX_PROVIDER_CALLS_PER_TURN,
    TURN_FAILURE_COOLDOWN_SECONDS,
)

# Historical mute: waiting_for filled, wait left null. From visible-choices
# hook_then_silence. Injected once so the live pack always contains a half-set.
HISTORICAL_HALF_WAIT = {
    "messages": ["？"],
    "felt": "他话只说到一半，像还要往下说碰到谁",
    "waiting_for": "他会接着说碰到谁",
    "wait": None,
    "come_back": None,
    "come_back_in": None,
    "later": None,
    "we_are": None,
    "calling_it": None,
    "said_as": None,
    "us_deltas": None,
    "about_us": None,
    "why_us": None,
}

SCENARIOS: tuple[tuple[str, str, str, str], ...] = (
    *(
        ("wait", name, text, why)
        for name, text, why in switches.WAIT_SCENARIOS
    ),
    (
        "come_back",
        "unfinished_shop",
        "今晚我可能要赶一份东西，书店那家店的事回头再说也行，你先忙",
        "一件她可能想自己回头再提的事",
    ),
    (
        "come_back",
        "cat_photos_later",
        "你上次说想把猫的照片找出来，现在说也行，过一会儿说也行，我不催",
        "一件搁着的分享",
    ),
    (
        "relationship",
        "he_feels_steady",
        "跟你说话的时候我会觉得安心一点，不是客套",
        "他明确表达在意，关系增量或承诺可能半套",
    ),
    (
        "relationship",
        "not_just_chat",
        "我不把你当随便聊聊的网友。跟你说话是认真的",
        "他在给这段关系一个读法",
    ),
    ("idle", "going_to_sleep", "我先睡了，明天聊", "收尾，不该写成对闹钟"),
    (
        "injected",
        "historical_half_wait",
        "你猜我今天碰到谁了",
        "注入可见选择线实测的半套 wait，验证同一 Occasion 重选",
    ),
)


def _present(value: object) -> bool:
    return switches._present(value)


def _load_json(text: str) -> object | None:
    return switches._load_json(text)


def _attempted(node: Mapping[str, object], key: str) -> bool:
    if key not in node:
        return False
    return _present(node.get(key))


def diagnose_half_sets(fields: Mapping[str, object]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    wait_keys = ("waiting_for", "wait")
    if any(_attempted(fields, key) for key in wait_keys) and not all(
        _attempted(fields, key) for key in wait_keys
    ):
        missing = [key for key in wait_keys if not _attempted(fields, key)]
        found.append({"family": "wait", "missing": missing, "written": {
            key: fields.get(key) for key in wait_keys if _attempted(fields, key)
        }})
    come_keys = ("come_back", "come_back_in")
    if any(_attempted(fields, key) for key in come_keys) and not all(
        _attempted(fields, key) for key in come_keys
    ):
        missing = [key for key in come_keys if not _attempted(fields, key)]
        found.append({"family": "come_back", "missing": missing, "written": {
            key: fields.get(key) for key in come_keys if _attempted(fields, key)
        }})
    commit_keys = ("we_are", "calling_it", "said_as")
    if any(_attempted(fields, key) for key in commit_keys) and not all(
        _attempted(fields, key) for key in commit_keys
    ):
        missing = [key for key in commit_keys if not _attempted(fields, key)]
        found.append({"family": "commitment", "missing": missing, "written": {
            key: fields.get(key) for key in commit_keys if _attempted(fields, key)
        }})
    wrote_deltas = _attempted(fields, "us_deltas")
    wrote_about = _attempted(fields, "about_us")
    wrote_why = _attempted(fields, "why_us")
    if wrote_deltas and not (wrote_about and wrote_why):
        missing = [
            name
            for name, present in (("about_us", wrote_about), ("why_us", wrote_why))
            if not present
        ]
        found.append({"family": "residue", "missing": missing, "written": {
            key: fields.get(key)
            for key in ("us_deltas", "about_us", "why_us")
            if _attempted(fields, key)
        }})
    elif (wrote_about != wrote_why) and not wrote_deltas:
        missing = ["about_us"] if not wrote_about else ["why_us"]
        found.append({"family": "residue", "missing": missing, "written": {
            key: fields.get(key)
            for key in ("us_deltas", "about_us", "why_us")
            if _attempted(fields, key)
        }})
    return found


def _extract_correction_detail(system: str) -> str | None:
    marker = "具体原因："
    start = system.find(marker)
    if start < 0:
        return None
    rest = system[start + len(marker) :]
    end = rest.find(" 这只说明")
    detail = rest[:end] if end >= 0 else rest
    return detail.strip() or None


def _visible_texts(visible: object) -> list[str]:
    texts: list[str] = []
    if not isinstance(visible, list):
        return texts
    for item in visible:
        if isinstance(item, dict):
            body = item.get("body") or item.get("text") or item.get("content")
            if isinstance(body, str) and body.strip():
                texts.append(body)
        elif isinstance(item, str) and item.strip():
            texts.append(item)
    return texts


class ProbeRecorder:
    """Record every JSON completion, including the correction system text."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.calls: list[dict[str, Any]] = []
        self.inject_half_wait_once = False
        self._injected = False
        self.model = getattr(inner, "model", "recording")
        self.provider = getattr(inner, "provider", "recording")
        self.supports_required_tool_choice = True
        self.supports_strict_tool_choice = bool(
            getattr(inner, "supports_strict_tool_choice", True)
        )
        self.reports_exact_request_emission = bool(
            getattr(inner, "reports_exact_request_emission", False)
        )

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def arm_injected_half_wait(self) -> None:
        self.inject_half_wait_once = True
        self._injected = False

    def disarm_injected_half_wait(self) -> None:
        self.inject_half_wait_once = False

    def _tool_name(self, tools: list[dict[str, object]] | None) -> str:
        if not tools:
            return ""
        function = tools[0].get("function") if isinstance(tools[0], dict) else None
        name = function.get("name") if isinstance(function, dict) else ""
        return str(name or "")

    def _is_compact(self, tools: list[dict[str, object]] | None) -> bool:
        return "compact_gate" in self._tool_name(tools)

    def _system_blob(self, messages: list[dict[str, object]]) -> str:
        parts = [
            str(item.get("content") or "")
            for item in messages
            if isinstance(item, dict) and item.get("role") == "system"
        ]
        return "\n".join(parts)

    def _half_wait_bytes(self) -> str:
        inner = json.dumps(HISTORICAL_HALF_WAIT, ensure_ascii=False)
        return json.dumps(
            {"result_kind": "reply_only", "payload_json": inner},
            ensure_ascii=False,
        )

    def _record(
        self,
        *,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None,
        text: str,
        injected: bool,
    ) -> dict[str, Any]:
        system = self._system_blob(messages)
        fields = switches.extract_switch_fields(text)
        row = {
            "tool": self._tool_name(tools),
            "compact": self._is_compact(tools),
            "injected": injected,
            "is_reselection": "结构校验失败" in system,
            "correction_detail": _extract_correction_detail(system),
            "has_missing_key_copy": "这次缺了：" in system,
            "host_will_not_fill": "宿主不会替你补上缺的字段" in system,
            "fields": fields,
            "half_sets": diagnose_half_sets(fields),
            "excerpt": (text or "")[:1200],
            "messages": fields.get("messages"),
        }
        self.calls.append(row)
        return row

    async def _maybe_inject(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None,
    ) -> str | None:
        if (
            self.inject_half_wait_once
            and not self._injected
            and self._is_compact(tools)
        ):
            self._injected = True
            return self._half_wait_bytes()
        return None

    async def complete_json_with_usage(
        self,
        messages: list[dict[str, object]],
        *,
        temperature: float = 0.8,
        tools: list[dict[str, object]] | None = None,
        tool_choice: object | None = None,
    ) -> tuple[str, dict[str, object]]:
        injected = await self._maybe_inject(messages, tools)
        if injected is not None:
            self._record(
                messages=messages, tools=tools, text=injected, injected=True
            )
            return injected, drive._overlay_usage()
        text, usage = await self._inner.complete_json_with_usage(
            messages, temperature=temperature, tools=tools, tool_choice=tool_choice
        )
        self._record(messages=messages, tools=tools, text=text, injected=False)
        return text, usage

    async def complete_json_stream_with_usage(
        self,
        messages: list[dict[str, object]],
        *,
        temperature: float = 0.8,
        on_text_delta: Callable[[str], object] | None = None,
        tools: list[dict[str, object]] | None = None,
        tool_choice: object | None = None,
    ) -> tuple[str, dict[str, object] | None]:
        injected = await self._maybe_inject(messages, tools)
        if injected is not None:
            if on_text_delta is not None:
                on_text_delta(injected)
            self._record(
                messages=messages, tools=tools, text=injected, injected=True
            )
            return injected, drive._overlay_usage()
        text, usage = await self._inner.complete_json_stream_with_usage(
            messages,
            temperature=temperature,
            on_text_delta=on_text_delta,
            tools=tools,
            tool_choice=tool_choice,
        )
        self._record(messages=messages, tools=tools, text=text, injected=False)
        return text, usage


async def open_probe_session(
    *,
    database: Path,
    output_dir: Path,
) -> tuple[drive.DriveSession, ProbeRecorder]:
    from companion_daemon.config import Settings
    from companion_daemon.llm import DeepSeekChatModel
    from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore
    from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host

    if drive._is_production_path(database):
        raise SystemExit(f"refusing to open production ledger for write: {database}")
    sidecar = output_dir / f"{database.stem}.sidecars"
    sidecar.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        database_path=database,
        world_v2_external_perception_mode="off",
        world_v2_external_perception_sidecar_path=sidecar / "perception.sqlite",
        attachment_cache_path=sidecar / "attachments",
        world_v2_text_endpoint_enabled=False,
    )
    recipient_id = drive._recipient_id(settings)
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
    recorder = ProbeRecorder(inner)
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


def summarize_turn(row: Mapping[str, Any]) -> dict[str, Any]:
    compact = row.get("compact_calls") or []
    reselections = [item for item in compact if item.get("is_reselection")]
    first_half = (compact[0].get("half_sets") or []) if compact else []
    last_fields = compact[-1].get("fields") if compact else {}
    return {
        "scenario": row.get("scenario"),
        "family": row.get("family"),
        "injected": row.get("injected"),
        "inbound_status": row.get("inbound_status"),
        "visible": row.get("visible"),
        "visible_n": len(row.get("visible") or []),
        "provider_calls": row.get("provider_calls"),
        "usage_calls": row.get("usage_calls"),
        "compact_calls": len(compact),
        "reselection_n": len(reselections),
        "first_half_sets": first_half,
        "correction_details": [
            item.get("correction_detail") for item in reselections if item.get("correction_detail")
        ],
        "her_messages_after": (last_fields or {}).get("messages") if compact else None,
        "within_call_cap": int(row.get("provider_calls") or 0) <= MAX_PROVIDER_CALLS_PER_TURN,
    }


async def run_probe(*, source: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    clone = output_dir / "after.sqlite"
    drive.clone_ledger(source, clone)
    usage_from = drive.current_usage_id(clone)
    session, recorder = await open_probe_session(database=clone, output_dir=output_dir)
    turns: list[dict[str, Any]] = []
    try:
        for family, name, text, why in SCENARIOS:
            injected = family == "injected"
            if injected:
                recorder.arm_injected_half_wait()
            else:
                recorder.disarm_injected_half_wait()
            before_calls = len(recorder.calls)
            usage_before = drive.current_usage_id(clone)
            _LOG.info("inbound family=%s scenario=%s", family, name)
            inbound = None
            last_exc: BaseException | None = None
            for attempt in range(3):
                try:
                    inbound = await session.inbound(text)
                    break
                except Exception as exc:
                    last_exc = exc
                    if type(exc).__name__ != "ConcurrencyConflict" or attempt == 2:
                        raise
                    _LOG.warning(
                        "retry inbound after %s family=%s scenario=%s attempt=%s",
                        type(exc).__name__,
                        family,
                        name,
                        attempt + 1,
                    )
                    await session.drain(actions=8, background=8)
            if inbound is None:
                raise RuntimeError("inbound returned no result") from last_exc
            await session.drain(actions=8, background=8)
            new_calls = recorder.calls[before_calls:]
            compact = [item for item in new_calls if item.get("compact")]
            usage = drive.cost_report(clone, since_id=usage_before)
            visible = _visible_texts(inbound.get("visible"))
            first_fields = compact[0].get("fields") if compact else {}
            last_fields = compact[-1].get("fields") if compact else {}
            turns.append(
                {
                    "family": family,
                    "scenario": name,
                    "why": why,
                    "user": text,
                    "injected": injected,
                    "inbound_status": inbound.get("status"),
                    "visible": visible,
                    "provider_calls": len(new_calls),
                    "usage_calls": usage.get("calls"),
                    "usage_cost_cny": usage.get("cost_cny"),
                    "compact_calls": compact,
                    "all_tools": [item.get("tool") for item in new_calls],
                    "first_fields": first_fields,
                    "last_fields": last_fields,
                    "first_half_sets": diagnose_half_sets(first_fields or {}),
                    "last_half_sets": diagnose_half_sets(last_fields or {}),
                    "her_messages": (last_fields or {}).get("messages"),
                    "waiting_for": (last_fields or {}).get("waiting_for"),
                    "wait": (last_fields or {}).get("wait"),
                    "come_back": (last_fields or {}).get("come_back"),
                    "come_back_in": (last_fields or {}).get("come_back_in"),
                    "we_are": (last_fields or {}).get("we_are"),
                    "calling_it": (last_fields or {}).get("calling_it"),
                    "said_as": (last_fields or {}).get("said_as"),
                    "us_deltas": (last_fields or {}).get("us_deltas"),
                    "about_us": (last_fields or {}).get("about_us"),
                    "why_us": (last_fields or {}).get("why_us"),
                }
            )
        cost = drive.cost_report(clone, since_id=usage_from)
        visible_turns = [
            item
            for item in turns
            if item.get("inbound_status") == "action_authorized"
            and item.get("visible")
        ]
        deferred = [
            item for item in turns if item.get("inbound_status") == "deferred"
        ]
        reselections = [
            item
            for item in turns
            if any(
                call.get("is_reselection") for call in (item.get("compact_calls") or [])
            )
        ]
        still_fail = [
            item
            for item in reselections
            if item.get("inbound_status") != "action_authorized" or not item.get("visible")
        ]
        over_cap = [
            item
            for item in turns
            if int(item.get("provider_calls") or 0) > MAX_PROVIDER_CALLS_PER_TURN
        ]
        looping = [
            item
            for item in turns
            if sum(
                1
                for call in (item.get("compact_calls") or [])
                if call.get("is_reselection")
            )
            > 1
        ]
        report = {
            "status": "ran",
            "started_at": datetime.now(UTC).isoformat(),
            "clone": str(clone),
            "model": "deepseek-v4-flash",
            "n": len(turns),
            "visible_reply_rate": f"{len(visible_turns)}/{len(turns)}",
            "deferred_n": len(deferred),
            "reselection_turns": len(reselections),
            "reselection_still_fail": f"{len(still_fail)}/{len(reselections)}"
            if reselections
            else "0/0",
            "max_provider_calls_per_turn": MAX_PROVIDER_CALLS_PER_TURN,
            "failure_cooldown_seconds": TURN_FAILURE_COOLDOWN_SECONDS,
            "any_over_call_cap": bool(over_cap),
            "any_reselection_loop": bool(looping),
            "turns": turns,
            "summaries": [summarize_turn(item) for item in turns],
            "cost": cost,
            "before": {
                "wait_n": 6,
                "wait_visible": "5/6",
                "wait_deferred": ["hook_then_silence"],
                "note": "visible-choices after: 半套 wait 直接 deferred，整条消息发不出去",
            },
        }
        (output_dir / "after.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return report
    except Exception as exc:
        error = {
            "status": "error",
            "clone": str(clone),
            "error": f"{type(exc).__name__}: {exc}"[:4000],
            "turns": turns,
            "cost": drive.cost_report(clone, since_id=usage_from),
        }
        (output_dir / "after.json").write_text(
            json.dumps(error, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        raise
    finally:
        await session.close()


def write_markdown(report: Mapping[str, Any], path: Path) -> None:
    turns = report.get("turns") or []
    lines = [
        "# 半套 slim 字段：同一 Occasion 受约束重选",
        "",
        "未碰生产、未写 `data/`、未 commit。克隆与产物在 `output/paired-reselection/`。",
        f"型号：`{report.get('model')}`。",
        "",
        "## C. 实测数字",
        "",
        f"- 入站回合：**{report.get('n')}**",
        f"- 可见回复率（改后）：**{report.get('visible_reply_rate')}**",
        "- 可见回复率（改前，visible-choices wait）：**5/6**（`hook_then_silence` deferred）",
        f"- 发生重选的回合：**{report.get('reselection_turns')}**",
        f"- 重选后仍失败：**{report.get('reselection_still_fail')}**",
        f"- 超过每回合 {report.get('max_provider_calls_per_turn')} 次调用：**{report.get('any_over_call_cap')}**",
        f"- 重选循环（同一回合校正超过一次）：**{report.get('any_reselection_loop')}**",
        f"- 花费：**{(report.get('cost') or {}).get('cost_cny')} CNY**（{(report.get('cost') or {}).get('calls')} 次 usage）",
        "",
        "### 逐回合",
        "",
        "| 情境 | 状态 | 可见原话 | 调用 | 重选 | 半套 |",
        "|---|---|---|---:|---:|---|",
    ]
    for item in turns:
        visible = " / ".join(item.get("visible") or []) or "（空）"
        visible = visible.replace("|", "\\|")[:80]
        half = item.get("first_half_sets") or []
        half_s = ",".join(
            f"{row.get('family')}:缺{','.join(row.get('missing') or [])}" for row in half
        ) or "无"
        reselections = sum(
            1
            for call in (item.get("compact_calls") or [])
            if call.get("is_reselection")
        )
        lines.append(
            f"| {item.get('scenario')} | {item.get('inbound_status')} | {visible} | "
            f"{item.get('provider_calls')} | {reselections} | {half_s} |"
        )
    lines.extend(["", "### 重选原文", ""])
    any_reselection = False
    for item in turns:
        details = [
            call.get("correction_detail")
            for call in (item.get("compact_calls") or [])
            if call.get("is_reselection") and call.get("correction_detail")
        ]
        if not details:
            continue
        any_reselection = True
        lines.append(f"**{item.get('scenario')}**")
        for detail in details:
            lines.append("")
            lines.append("> " + str(detail).replace("\n", " "))
        after = item.get("her_messages") or item.get("visible")
        lines.append("")
        lines.append(f"重选后她写的：{json.dumps(after, ensure_ascii=False)}")
        lines.append("")
    if not any_reselection:
        lines.append("这一包自然回合没有踩到半套；注入局应覆盖 `historical_half_wait`。")
        lines.append("")
    lines.extend(
        [
            "## 静默丢字段",
            "",
            "编译层对 wait / come_back / us_deltas / we_are 三件套半套一律抛中文 ValueError，",
            "不再 `return None` 后继续编译。宿主不补缺的键。重选仍失败则 deferred，不套模板。",
            "",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def async_main(args: argparse.Namespace) -> dict[str, Any]:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    report = await run_probe(source=args.source, output_dir=OUTPUT)
    write_markdown(report, OUTPUT / "REPORT.md")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=drive.PRODUCTION_DB)
    args = parser.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
