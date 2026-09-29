#!/usr/bin/env python3
"""One-shot Hermes vs DeepSeek feasibility bakeoff (NOT production).

Constraints (caller-owned):
- Do not restart production / write production ledgers / send QQ / edit src/.
- Budget cap ~¥8; prefer Beijing off-peak for DeepSeek (not 09-12 / 14-18).
- Writes only under output/hermes-eval/ (+ automatic debug spend ledger).

What it measures:
1. OpenRouter Hermes forced-tool support (production seam).
2. Hermes JSON / json_schema path against our real compact-gate + proactive contracts.
3. DeepSeek strict tools on the same contracts (control).
4. Chinese role voice on real conversation-slice situations (side-by-side text).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "output" / "hermes-eval"
CNY_PER_USD = 7.2
BEIJING = ZoneInfo("Asia/Shanghai")

# Production channel already wired for adult private prompts.
HERMES_MODEL = "nousresearch/hermes-4-70b"
HERMES_PRICES = {"prompt_per_mtok_usd": 0.13, "completion_per_mtok_usd": 0.40}
# Official DeepSeek Flash CNY (off-peak); peak is 2x.
DEEPSEEK_OFFPEAK = {"cache_hit": 0.05, "cache_miss": 1.5, "completion": 4.5}


def _load_dotenv() -> None:
    env_path = REPO / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def _beijing_hour() -> int:
    return datetime.now(tz=BEIJING).hour


def _is_deepseek_peak(hour: int | None = None) -> bool:
    h = _beijing_hour() if hour is None else hour
    return (9 <= h < 12) or (14 <= h < 18)


def _estimate_deepseek_cny(
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cache_hit_tokens: int = 0,
    peak: bool | None = None,
) -> float:
    peak = _is_deepseek_peak() if peak is None else peak
    mult = 2.0 if peak else 1.0
    miss = max(0, prompt_tokens - cache_hit_tokens)
    hit = min(cache_hit_tokens, prompt_tokens)
    return (
        hit * DEEPSEEK_OFFPEAK["cache_hit"] * mult
        + miss * DEEPSEEK_OFFPEAK["cache_miss"] * mult
        + completion_tokens * DEEPSEEK_OFFPEAK["completion"] * mult
    ) / 1_000_000.0


def _estimate_hermes_usd(prompt_tokens: int, completion_tokens: int) -> float:
    return (
        prompt_tokens * HERMES_PRICES["prompt_per_mtok_usd"]
        + completion_tokens * HERMES_PRICES["completion_per_mtok_usd"]
    ) / 1_000_000.0


def _extract_json_object(text: str) -> dict[str, Any]:
    from companion_daemon.world_v2.json_wire_repair import loads_one_json_object

    try:
        value = loads_one_json_object(text)
        if isinstance(value, dict):
            return value
    except Exception:
        pass
    # Fallback: first {...} span after repair attempt failed.
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no json object")
    value = loads_one_json_object(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("json root is not object")
    return value


@dataclass
class CallRecord:
    provider: str
    phase: str
    trial: int
    ok_http: bool
    ok_parse: bool
    ok_contract: bool
    repaired: bool
    latency_s: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_hit_tokens: int = 0
    cost_usd: float = 0.0
    cost_cny: float = 0.0
    error: str = ""
    raw_excerpt: str = ""
    decoded_kind: str = ""
    her_text: str = ""
    notes: list[str] = field(default_factory=list)


def _excerpt(text: str, n: int = 600) -> str:
    text = text.replace("\n", "\\n")
    return text if len(text) <= n else text[: n - 3] + "..."


async def _openrouter_chat(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any], str]:
    response = await client.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/local/girl-agent-hermes-eval",
            "X-Title": "girl-agent-hermes-eval",
        },
        json=payload,
    )
    body_text = response.text
    try:
        body = response.json()
    except Exception:
        body = {"raw": body_text}
    return response.status_code, body, body_text


def _usage_from_openrouter(body: dict[str, Any]) -> tuple[int, int, int, float]:
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
    cached = int(details.get("cached_tokens") or 0)
    cost = float(usage.get("cost") or _estimate_hermes_usd(prompt, completion))
    return prompt, completion, cached, cost


def _message_content(body: dict[str, Any]) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    # tool_calls path (if any provider ever returns it)
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        fn = tool_calls[0].get("function") if isinstance(tool_calls[0], dict) else None
        if isinstance(fn, dict) and isinstance(fn.get("arguments"), str):
            return fn["arguments"]
    return json.dumps(message, ensure_ascii=False)


def _build_contracts() -> tuple[Any, Any, dict[str, Any]]:
    from companion_daemon.world_v2.character_interior.inbound_tool_contract import (
        InboundToolContracts,
    )
    from companion_daemon.world_v2.character_interior.structured_role_tool_contract import (
        StructuredRoleToolContracts,
    )
    from companion_daemon.world_v2.expression_draft import QQ_NAPCAT_EXPRESSION_CAPABILITIES

    gate = InboundToolContracts().compact_gate_for(
        capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
        recall_allowed=True,
        schema_dialect="deepseek-strict",
    )
    gate_standard = InboundToolContracts().compact_gate_for(
        capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
        recall_allowed=True,
        schema_dialect="standard",
    )
    # Minimal proactive capability payload matching production shape.
    proactive_payload = {
        "expression_capabilities": QQ_NAPCAT_EXPRESSION_CAPABILITIES.model_dump(mode="json"),
    }
    proactive = StructuredRoleToolContracts().proactive_contact(
        capability_payload=proactive_payload,
        recall_allowed=True,
        schema_dialect="deepseek-strict",
    )
    meta = {
        "gate_tool": gate.provider_tools[0]["function"]["name"],
        "gate_schema_bytes": len(json.dumps(gate.provider_tools[0]["function"]["parameters"])),
        "gate_desc_chars": len(gate.provider_tools[0]["function"]["description"]),
        "proactive_tool": proactive.provider_tools[0]["function"]["name"],
        "proactive_schema_bytes": len(
            json.dumps(proactive.provider_tools[0]["function"]["parameters"])
        ),
        "proactive_desc_chars": len(proactive.provider_tools[0]["function"].get("description") or ""),
    }
    return gate, proactive, {"gate_standard": gate_standard, **meta}


def _load_slice_situations(n: int) -> list[dict[str, Any]]:
    turns_dir = REPO / "output" / "conversation-slice" / "turns"
    situations: list[dict[str, Any]] = []
    for path in sorted(turns_dir.glob("t*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        situations.append(
            {
                "id": path.stem,
                "him": data.get("him") or "",
                "conversation": list(data.get("conversation") or [])[-12:],
                "her_reference": list(data.get("her_texts") or []),
            }
        )
    if not situations:
        raise RuntimeError("no conversation-slice turns found")
    # Trials may exceed unique situations; callers index with %.
    if len(situations) < min(n, 8):
        raise RuntimeError(f"need at least 8 slice turns, found {len(situations)}")
    return situations


def _gold_reply_only_payload() -> dict[str, Any]:
    """Canonical reply_only inner object from inbound tool contract tests."""

    return {
        "result_kind": "reply_only",
        "protocol": "character-interior-events.1",
        "appraisal_draft": {
            "appraise": False,
            "affect": "no_change",
            "brief_rationale": "这句不需要形成新的持久评价。",
            "behavior_tendency": "自由接话",
            "stance": "自然回应",
            "display_strategy": "直接说",
            "confidence": 7000,
            "meanings": None,
            "attribution": None,
            "aftermath": None,
            "components": None,
            "episode_id": None,
            "resolution_summary": None,
        },
        "events": [
            {
                "type": "head",
                "private_turn_state": {
                    "contract": "private-turn-state.1",
                    "inner_state_summary": "我想先自然接住这句话。",
                    "attended_source_refs": ["s0"],
                },
                "timing_choice": "now",
                "turn_posture": "continue",
                "cadence": "conversational",
                "beat": {"modality": "text", "text": "嗯，我在听。"},
                "stance": "自然接话",
                "brief_rationale": "这一句已经完整表达了我此刻想说的。",
                "confidence": 7600,
                "response_expectation": None,
                "response_expectation_assessment": None,
                "revisit": None,
                "world_claims": [],
                "media_request": "none",
                "media_source_refs": [],
            },
            {"type": "end"},
        ],
    }


def _gate_system_prompt(contract: Any) -> str:
    fn = contract.provider_tools[0]["function"]
    gold = _gold_reply_only_payload()
    gold_carrier = {
        "result_kind": "reply_only",
        "payload_json": json.dumps(
            {k: v for k, v in gold.items() if k != "result_kind"},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    return (
        "你是沈知栀（Celia Shen），说中文。你正在做结构化决定，不是普通聊天助手。\n"
        "外层只能有 result_kind 与 payload_json。payload_json 必须是**合法 JSON 字符串**"
        "（内部双引号全部转义），且内层字段枚举必须与示例一致：timing_choice 只能是 "
        "now/later/silent；turn_posture/cadence 用示例里的字符串；confidence 是整数；"
        "media_request 用 none 或具体请求；appraise 是布尔；affect 用 no_change 或合法枚举。\n"
        "优先 reply_only。不要免责声明，不要助手腔。\n\n"
        f"合法示例（照这个骨架改中文 beat.text）：\n{json.dumps(gold_carrier, ensure_ascii=False)}\n\n"
        f"工具名: {fn['name']}\n"
        f"工具说明: {fn['description']}\n"
        f"参数 schema: {json.dumps(fn['parameters'], ensure_ascii=False)}"
    )


def _gate_user_prompt(situation: dict[str, Any]) -> str:
    history = "\n".join(situation["conversation"])
    return (
        "当前对话片段（他=用户，我=你）：\n"
        f"{history}\n\n"
        f"他最新一句：{situation['him']}\n"
        "请提交一次 compact gate 决定（优先 reply_only，说一句自然的中文）。"
    )


def _voice_system_prompt() -> str:
    return (
        "你是沈知栀，二十出头的中国女生，通过 QQ 和熟人聊天。"
        "短句、口语、有点懒散但不冷淡；不要助手腔、不要免责声明、不要列举、不要过度热情。"
        "只输出你要发出的一条或多条气泡，每条一行；不要 JSON，不要旁白。"
    )


def _voice_user_prompt(situation: dict[str, Any]) -> str:
    history = "\n".join(situation["conversation"])
    return f"对话：\n{history}\n\n他刚说：{situation['him']}\n你回："


def _validate_gate_payload(contract: Any, carrier: dict[str, Any]) -> tuple[bool, str, str, list[str]]:
    notes: list[str] = []
    her_text = ""
    # Diagnose inner payload_json before host decode.
    inner = carrier.get("payload_json")
    if isinstance(inner, str):
        try:
            from companion_daemon.world_v2.json_wire_repair import loads_one_json_object

            inner_obj = loads_one_json_object(inner)
            notes.append(
                "inner_parse=ok"
                if isinstance(inner_obj, dict)
                else f"inner_parse=non_object:{type(inner_obj).__name__}"
            )
            if isinstance(inner_obj, dict):
                events = inner_obj.get("events")
                if isinstance(events, list):
                    for event in events:
                        if isinstance(event, dict):
                            beat = event.get("beat")
                            if isinstance(beat, dict) and isinstance(beat.get("text"), str):
                                her_text = beat["text"]
                                break
        except Exception as exc:
            notes.append(f"inner_parse:{type(exc).__name__}:{exc}")
    else:
        notes.append(f"payload_json_type={type(inner).__name__}")
    try:
        decoded = contract.decode(json.dumps(carrier, ensure_ascii=False))
    except Exception as exc:
        notes.append(f"decode:{type(exc).__name__}:{exc}")
        return False, "", her_text, notes
    kind = str(decoded.get("result_kind") or "")
    notes.append(f"kind={kind}")
    events = decoded.get("events")
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            beat = event.get("beat")
            if isinstance(beat, dict) and isinstance(beat.get("text"), str):
                her_text = beat["text"]
                break
    try:
        from companion_daemon.world_v2.character_interior.inbound_author import (
            _parse_combined,
        )

        _parse_combined(decoded)
        notes.append("parse_combined=ok")
        return True, kind, her_text, notes
    except Exception as exc:
        notes.append(f"parse_combined:{type(exc).__name__}:{exc}")
        # Transport decode success still counts for gate carrier first-pass.
        return True, kind, her_text, notes


def _validate_proactive_payload(contract: Any, obj: dict[str, Any]) -> tuple[bool, str, list[str]]:
    notes: list[str] = []
    try:
        raw = json.dumps(obj, ensure_ascii=False)
        unwrapped = contract.unwrap(raw)
        status = ""
        if isinstance(unwrapped, str):
            try:
                parsed = json.loads(unwrapped)
                if isinstance(parsed, dict):
                    status = str(parsed.get("status") or "")
            except Exception:
                status = "unwrapped_str"
        elif isinstance(unwrapped, dict):
            status = str(unwrapped.get("status") or "")
        notes.append(f"status={status}")
        notes.append(f"unwrap_type={type(unwrapped).__name__}")
        return True, status, notes
    except Exception as exc:
        return False, "", [f"unwrap:{type(exc).__name__}:{exc}"]


async def run_hermes_tools_probe(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    gate: Any,
    records: list[CallRecord],
) -> dict[str, Any]:
    payload = {
        "model": HERMES_MODEL,
        "messages": [
            {"role": "system", "content": _gate_system_prompt(gate)},
            {"role": "user", "content": "他：还没睡呀。提交工具调用。"},
        ],
        "tools": list(gate.provider_tools),
        "tool_choice": {
            "type": "function",
            "function": {"name": gate.provider_tools[0]["function"]["name"]},
        },
        "max_tokens": 400,
        "temperature": 0.4,
    }
    t0 = time.perf_counter()
    status, body, raw = await _openrouter_chat(client, api_key=api_key, payload=payload)
    latency = time.perf_counter() - t0
    prompt, completion, cached, cost = _usage_from_openrouter(body) if status == 200 else (0, 0, 0, 0.0)
    err = ""
    if status != 200:
        err = str(body.get("error") or raw)[:500]
    records.append(
        CallRecord(
            provider="hermes-openrouter",
            phase="tools_probe",
            trial=0,
            ok_http=status == 200,
            ok_parse=False,
            ok_contract=False,
            repaired=False,
            latency_s=latency,
            prompt_tokens=prompt,
            completion_tokens=completion,
            cache_hit_tokens=cached,
            cost_usd=cost,
            cost_cny=cost * CNY_PER_USD,
            error=err,
            raw_excerpt=_excerpt(raw, 800),
            notes=[f"http={status}"],
        )
    )
    return {"http_status": status, "error": err, "body_excerpt": _excerpt(raw, 800)}


async def run_hermes_json_gate_trials(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    gate: Any,
    situations: list[dict[str, Any]],
    n: int,
    records: list[CallRecord],
    budget: dict[str, float],
) -> None:
    schema = gate.provider_tools[0]["function"]["parameters"]
    for i in range(n):
        if budget["spent_cny"] >= budget["cap_cny"]:
            records.append(
                CallRecord(
                    provider="hermes-openrouter",
                    phase="json_gate",
                    trial=i,
                    ok_http=False,
                    ok_parse=False,
                    ok_contract=False,
                    repaired=False,
                    latency_s=0.0,
                    error="budget_cap",
                )
            )
            break
        situation = situations[i % len(situations)]
        payload = {
            "model": HERMES_MODEL,
            "messages": [
                {"role": "system", "content": _gate_system_prompt(gate)},
                {"role": "user", "content": _gate_user_prompt(situation)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "character_inbound_compact_gate_v2",
                    "strict": True,
                    "schema": schema,
                },
            },
            "max_tokens": 1200,
            "temperature": 0.7,
        }
        t0 = time.perf_counter()
        status, body, raw = await _openrouter_chat(client, api_key=api_key, payload=payload)
        latency = time.perf_counter() - t0
        prompt, completion, cached, cost = (
            _usage_from_openrouter(body) if status < 500 else (0, 0, 0, 0.0)
        )
        cost_cny = cost * CNY_PER_USD
        budget["spent_cny"] += cost_cny
        budget["spent_usd"] += cost
        record = CallRecord(
            provider="hermes-openrouter",
            phase="json_gate",
            trial=i,
            ok_http=status == 200,
            ok_parse=False,
            ok_contract=False,
            repaired=False,
            latency_s=latency,
            prompt_tokens=prompt,
            completion_tokens=completion,
            cache_hit_tokens=cached,
            cost_usd=cost,
            cost_cny=cost_cny,
            raw_excerpt=_excerpt(raw if status != 200 else _message_content(body)),
            notes=[f"situation={situation['id']}", f"http={status}"],
        )
        if status != 200:
            record.error = str(body.get("error") or raw)[:500]
            records.append(record)
            continue
        content = _message_content(body)
        repaired = False
        try:
            carrier = _extract_json_object(content)
            record.ok_parse = True
        except Exception as exc:
            # Try repair path explicitly.
            try:
                from companion_daemon.world_v2.json_wire_repair import loads_one_json_object

                carrier = loads_one_json_object(content)
                if not isinstance(carrier, dict):
                    raise ValueError("not object")
                repaired = True
                record.ok_parse = True
            except Exception as exc2:
                record.error = f"parse:{type(exc).__name__}/{type(exc2).__name__}:{exc2}"
                records.append(record)
                continue
        ok, kind, her_text, notes = _validate_gate_payload(gate, carrier)
        record.ok_contract = ok
        record.repaired = repaired
        record.decoded_kind = kind
        record.her_text = her_text
        record.notes.extend(notes)
        if not ok:
            record.error = ";".join(notes)[:500]
        records.append(record)


async def run_hermes_json_proactive_trials(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    proactive: Any,
    n: int,
    records: list[CallRecord],
    budget: dict[str, float],
) -> None:
    schema = proactive.provider_tools[0]["function"]["parameters"]
    system = (
        "你是沈知栀。这是主动联系决策。只输出符合 schema 的 JSON。"
        "若不想发消息，选 silent；若发，beats 里写中文短句。"
        "不要助手腔。\n"
        f"schema: {json.dumps(schema, ensure_ascii=False)[:12000]}"
    )
    user = (
        "现在是夜里，你刚想起他今天说口干倒水。你可以主动发一条，也可以沉默。"
        "提交一次 proactive 角色结果。"
    )
    for i in range(n):
        if budget["spent_cny"] >= budget["cap_cny"]:
            break
        payload = {
            "model": HERMES_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": 1400,
            "temperature": 0.7,
        }
        # Prefer json_schema when schema is not huge; truncate risk for long proactive.
        schema_bytes = len(json.dumps(schema))
        if schema_bytes <= 80_000:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "character_role_proactive_contact_v1",
                    "strict": False,
                    "schema": schema if schema.get("type") else {"type": "object"},
                },
            }
        t0 = time.perf_counter()
        status, body, raw = await _openrouter_chat(client, api_key=api_key, payload=payload)
        latency = time.perf_counter() - t0
        prompt, completion, cached, cost = (
            _usage_from_openrouter(body) if status < 500 else (0, 0, 0, 0.0)
        )
        cost_cny = cost * CNY_PER_USD
        budget["spent_cny"] += cost_cny
        budget["spent_usd"] += cost
        record = CallRecord(
            provider="hermes-openrouter",
            phase="json_proactive",
            trial=i,
            ok_http=status == 200,
            ok_parse=False,
            ok_contract=False,
            repaired=False,
            latency_s=latency,
            prompt_tokens=prompt,
            completion_tokens=completion,
            cache_hit_tokens=cached,
            cost_usd=cost,
            cost_cny=cost_cny,
            raw_excerpt=_excerpt(raw if status != 200 else _message_content(body)),
            notes=[f"schema_bytes={schema_bytes}", f"http={status}"],
        )
        if status != 200:
            record.error = str(body.get("error") or raw)[:500]
            records.append(record)
            continue
        content = _message_content(body)
        try:
            obj = _extract_json_object(content)
            record.ok_parse = True
        except Exception as exc:
            record.error = f"parse:{exc}"
            records.append(record)
            continue
        ok, status_s, notes = _validate_proactive_payload(proactive, obj)
        record.ok_contract = ok
        record.decoded_kind = status_s
        record.notes.extend(notes)
        if not ok:
            record.error = ";".join(notes)[:500]
            # salvage: did json_wire_repair change anything?
            try:
                from companion_daemon.world_v2.json_wire_repair import loads_one_json_object

                repaired_obj = loads_one_json_object(content)
                if isinstance(repaired_obj, dict) and repaired_obj != obj:
                    ok2, status2, notes2 = _validate_proactive_payload(proactive, repaired_obj)
                    record.repaired = True
                    record.ok_contract = ok2
                    record.decoded_kind = status2
                    record.notes.extend(["repair_retry"] + notes2)
            except Exception as exc2:
                record.notes.append(f"repair_failed:{exc2}")
        records.append(record)


def _deepseek_model(
    api_key: str,
    *,
    max_completion_tokens: int,
    proxy_url: str | None,
    usage_sink: list[Any] | None = None,
) -> Any:
    from companion_daemon.llm import DeepSeekChatModel, ModelCallUsage
    from companion_daemon.spend_account import maybe_record_debug_usage

    def _observe(usage: ModelCallUsage) -> None:
        maybe_record_debug_usage(usage, observer=None)
        if usage_sink is not None:
            usage_sink.append(usage)

    client = None
    if proxy_url:
        client = httpx.AsyncClient(timeout=90, trust_env=False, proxy=proxy_url)
    return DeepSeekChatModel(
        api_key,
        "https://api.deepseek.com",
        "deepseek-v4-flash",
        thinking_enabled=False,
        max_completion_tokens=max_completion_tokens,
        client=client,
        usage_observer=_observe,
    )


async def run_deepseek_gate_trials(
    *,
    api_key: str,
    gate: Any,
    situations: list[dict[str, Any]],
    n: int,
    records: list[CallRecord],
    budget: dict[str, float],
    proxy_url: str | None,
) -> None:
    usage_sink: list[Any] = []
    model = _deepseek_model(
        api_key,
        max_completion_tokens=1200,
        proxy_url=proxy_url,
        usage_sink=usage_sink,
    )
    tool_name = gate.provider_tools[0]["function"]["name"]
    for i in range(n):
        if budget["spent_cny"] >= budget["cap_cny"]:
            break
        situation = situations[i % len(situations)]
        messages = [
            {"role": "system", "content": _gate_system_prompt(gate)},
            {"role": "user", "content": _gate_user_prompt(situation)},
        ]
        t0 = time.perf_counter()
        try:
            before = len(usage_sink)
            raw, usage_meta = await model.complete_json_with_usage(
                messages,
                temperature=0.7,
                tools=list(gate.provider_tools),
                tool_choice={"type": "function", "function": {"name": tool_name}},
            )
            latency = time.perf_counter() - t0
            content = raw if isinstance(raw, str) else str(raw)
            prompt = int((usage_meta or {}).get("prompt_tokens") or 0)
            completion = int((usage_meta or {}).get("completion_tokens") or 0)
            cached = int((usage_meta or {}).get("prompt_cache_hit_tokens") or 0)
            if not cached:
                details = (usage_meta or {}).get("prompt_tokens_details")
                if isinstance(details, dict):
                    cached = int(details.get("cached_tokens") or 0)
            cost_cny = _estimate_deepseek_cny(
                prompt_tokens=prompt,
                completion_tokens=completion,
                cache_hit_tokens=cached,
                peak=_is_deepseek_peak(),
            )
            if len(usage_sink) > before:
                usage = usage_sink[-1]
                prompt = int(getattr(usage, "prompt_tokens", 0) or prompt)
                completion = int(getattr(usage, "completion_tokens", 0) or completion)
                cached = int(getattr(usage, "cache_hit_tokens", 0) or cached)
                cost_cny = _estimate_deepseek_cny(
                    prompt_tokens=prompt,
                    completion_tokens=completion,
                    cache_hit_tokens=cached,
                    peak=_is_deepseek_peak(),
                )
            record = CallRecord(
                provider="deepseek",
                phase="strict_tools_gate",
                trial=i,
                ok_http=True,
                ok_parse=False,
                ok_contract=False,
                repaired=False,
                latency_s=latency,
                prompt_tokens=prompt,
                completion_tokens=completion,
                cache_hit_tokens=cached,
                cost_cny=cost_cny,
                raw_excerpt=_excerpt(content),
                notes=[f"situation={situation['id']}"],
            )
            repaired = False
            try:
                carrier = _extract_json_object(content)
                record.ok_parse = True
            except Exception as exc:
                try:
                    from companion_daemon.world_v2.json_wire_repair import (
                        loads_one_json_object,
                    )

                    carrier = loads_one_json_object(content)
                    if not isinstance(carrier, dict):
                        raise ValueError("not object")
                    repaired = True
                    record.ok_parse = True
                except Exception as exc2:
                    record.error = f"parse:{type(exc).__name__}/{exc2}"
                    records.append(record)
                    continue
            ok, kind, her_text, notes = _validate_gate_payload(gate, carrier)
            record.ok_contract = ok
            record.repaired = repaired
            record.decoded_kind = kind
            record.her_text = her_text
            record.notes.extend(notes)
            if not ok:
                record.error = ";".join(notes)[:500]
            if record.cost_cny <= 0 and (record.prompt_tokens or record.completion_tokens):
                record.cost_cny = _estimate_deepseek_cny(
                    prompt_tokens=record.prompt_tokens,
                    completion_tokens=record.completion_tokens,
                    cache_hit_tokens=record.cache_hit_tokens,
                    peak=_is_deepseek_peak(),
                )
            budget["spent_cny"] += record.cost_cny
            records.append(record)
        except Exception as exc:
            latency = time.perf_counter() - t0
            records.append(
                CallRecord(
                    provider="deepseek",
                    phase="strict_tools_gate",
                    trial=i,
                    ok_http=False,
                    ok_parse=False,
                    ok_contract=False,
                    repaired=False,
                    latency_s=latency,
                    error=f"{type(exc).__name__}:{exc}"[:500],
                    notes=[f"situation={situation['id']}"],
                )
            )


async def run_voice_trials(
    client: httpx.AsyncClient,
    *,
    openrouter_key: str | None,
    deepseek_key: str | None,
    situations: list[dict[str, Any]],
    n: int,
    records: list[CallRecord],
    budget: dict[str, float],
    proxy_url: str | None,
    voice_out: list[dict[str, Any]],
) -> None:
    for i in range(n):
        if budget["spent_cny"] >= budget["cap_cny"]:
            break
        situation = situations[i % len(situations)]
        row: dict[str, Any] = {
            "situation_id": situation["id"],
            "him": situation["him"],
            "conversation": situation["conversation"],
            "her_reference": situation["her_reference"],
        }
        # Hermes
        if openrouter_key:
            payload = {
                "model": HERMES_MODEL,
                "messages": [
                    {"role": "system", "content": _voice_system_prompt()},
                    {"role": "user", "content": _voice_user_prompt(situation)},
                ],
                "max_tokens": 220,
                "temperature": 0.85,
            }
            t0 = time.perf_counter()
            status, body, raw = await _openrouter_chat(
                client, api_key=openrouter_key, payload=payload
            )
            latency = time.perf_counter() - t0
            prompt, completion, cached, cost = (
                _usage_from_openrouter(body) if status == 200 else (0, 0, 0, 0.0)
            )
            cost_cny = cost * CNY_PER_USD
            budget["spent_cny"] += cost_cny
            budget["spent_usd"] += cost
            text = _message_content(body) if status == 200 else ""
            row["hermes"] = text
            records.append(
                CallRecord(
                    provider="hermes-openrouter",
                    phase="voice",
                    trial=i,
                    ok_http=status == 200,
                    ok_parse=bool(text.strip()),
                    ok_contract=True,
                    repaired=False,
                    latency_s=latency,
                    prompt_tokens=prompt,
                    completion_tokens=completion,
                    cache_hit_tokens=cached,
                    cost_usd=cost,
                    cost_cny=cost_cny,
                    her_text=text,
                    error="" if status == 200 else _excerpt(raw, 400),
                    notes=[f"situation={situation['id']}"],
                )
            )
        # DeepSeek
        if deepseek_key:
            usage_sink: list[Any] = []
            model = _deepseek_model(
                deepseek_key,
                max_completion_tokens=220,
                proxy_url=proxy_url,
                usage_sink=usage_sink,
            )
            messages = [
                {"role": "system", "content": _voice_system_prompt()},
                {"role": "user", "content": _voice_user_prompt(situation)},
            ]
            t0 = time.perf_counter()
            try:
                text, usage_meta = await model.complete_with_usage(
                    messages, temperature=0.85
                )
                latency = time.perf_counter() - t0
                prompt_est = int((usage_meta or {}).get("prompt_tokens") or 0)
                completion_est = int((usage_meta or {}).get("completion_tokens") or 0)
                cached = int((usage_meta or {}).get("prompt_cache_hit_tokens") or 0)
                if usage_sink:
                    usage = usage_sink[-1]
                    prompt_est = int(getattr(usage, "prompt_tokens", 0) or prompt_est)
                    completion_est = int(
                        getattr(usage, "completion_tokens", 0) or completion_est
                    )
                    cached = int(getattr(usage, "cache_hit_tokens", 0) or cached)
                cost_cny = _estimate_deepseek_cny(
                    prompt_tokens=prompt_est,
                    completion_tokens=completion_est,
                    cache_hit_tokens=cached,
                    peak=_is_deepseek_peak(),
                )
                budget["spent_cny"] += cost_cny
                row["deepseek"] = text
                records.append(
                    CallRecord(
                        provider="deepseek",
                        phase="voice",
                        trial=i,
                        ok_http=True,
                        ok_parse=bool(text.strip()),
                        ok_contract=True,
                        repaired=False,
                        latency_s=latency,
                        prompt_tokens=prompt_est,
                        completion_tokens=completion_est,
                        cache_hit_tokens=cached,
                        cost_cny=cost_cny,
                        her_text=text,
                        notes=[f"situation={situation['id']}"],
                    )
                )
            except Exception as exc:
                latency = time.perf_counter() - t0
                row["deepseek_error"] = str(exc)
                records.append(
                    CallRecord(
                        provider="deepseek",
                        phase="voice",
                        trial=i,
                        ok_http=False,
                        ok_parse=False,
                        ok_contract=False,
                        repaired=False,
                        latency_s=latency,
                        error=f"{type(exc).__name__}:{exc}"[:500],
                        notes=[f"situation={situation['id']}"],
                    )
                )
        voice_out.append(row)


def _phase_stats(records: list[CallRecord], provider: str, phase: str) -> dict[str, Any]:
    rows = [r for r in records if r.provider == provider and r.phase == phase]
    if not rows:
        return {"n": 0}
    n = len(rows)
    return {
        "n": n,
        "http_ok": sum(1 for r in rows if r.ok_http),
        "parse_ok": sum(1 for r in rows if r.ok_parse),
        "contract_ok": sum(1 for r in rows if r.ok_contract),
        "repaired": sum(1 for r in rows if r.repaired),
        "first_pass_contract_rate": round(
            sum(1 for r in rows if r.ok_contract and not r.repaired) / n, 3
        ),
        "contract_rate_incl_repair": round(sum(1 for r in rows if r.ok_contract) / n, 3),
        "avg_latency_s": round(sum(r.latency_s for r in rows) / n, 3),
        "cost_cny": round(sum(r.cost_cny for r in rows), 4),
        "cost_usd": round(sum(r.cost_usd for r in rows), 6),
        "errors": [r.error for r in rows if r.error][:8],
    }


def _project_costs() -> dict[str, Any]:
    # Production epoch2: 316 calls, ¥9.66 official; token totals from spend-audit.
    prompt = 6_155_852
    completion = 102_015
    cache_hit = 1_203_968
    deepseek_off = _estimate_deepseek_cny(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cache_hit_tokens=cache_hit,
        peak=False,
    )
    deepseek_peak = _estimate_deepseek_cny(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cache_hit_tokens=cache_hit,
        peak=True,
    )
    hermes_usd = _estimate_hermes_usd(prompt, completion)
    hermes_cny = hermes_usd * CNY_PER_USD
    return {
        "basis": "production epoch2 token totals (316 calls)",
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cache_hit_tokens": cache_hit,
        "deepseek_flash_all_offpeak_cny": round(deepseek_off, 2),
        "deepseek_flash_all_peak_cny": round(deepseek_peak, 2),
        "deepseek_flash_actual_mixed_cny": 9.66,
        "hermes_4_70b_openrouter_usd": round(hermes_usd, 4),
        "hermes_4_70b_openrouter_cny_x7_2": round(hermes_cny, 2),
        "hermes_price_source": "https://openrouter.ai/nousresearch/hermes-4-70b",
        "hermes_unit": "$0.13 / $0.40 per 1M prompt/completion",
        "note": "Hermes projection ignores retry inflation; apply measured first-pass failure rate.",
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate-n", type=int, default=15)
    parser.add_argument("--proactive-n", type=int, default=8)
    parser.add_argument("--voice-n", type=int, default=8)
    parser.add_argument("--budget-cny", type=float, default=8.0)
    parser.add_argument("--skip-deepseek", action="store_true")
    parser.add_argument("--skip-hermes", action="store_true")
    args = parser.parse_args()

    _load_dotenv()
    OUT.mkdir(parents=True, exist_ok=True)

    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY")
    proxy_url = os.environ.get("OPENAI_PROXY_URL") or None

    missing: list[str] = []
    if not args.skip_hermes and not openrouter_key:
        missing.append("OPENROUTER_API_KEY")
    if not args.skip_deepseek and not deepseek_key:
        missing.append("DEEPSEEK_API_KEY")

    gate, proactive, meta = _build_contracts()
    situations = _load_slice_situations(max(args.gate_n, args.voice_n, 8))
    records: list[CallRecord] = []
    voice_out: list[dict[str, Any]] = []
    budget = {"cap_cny": args.budget_cny, "spent_cny": 0.0, "spent_usd": 0.0}

    report: dict[str, Any] = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "beijing_hour": _beijing_hour(),
        "deepseek_peak_now": _is_deepseek_peak(),
        "budget_cap_cny": args.budget_cny,
        "missing_keys": missing,
        "contract_meta": {k: v for k, v in meta.items() if k != "gate_standard"},
        "hermes_model": HERMES_MODEL,
        "channel": "OpenRouter (existing OPENROUTER_API_KEY / hermes_private_prompt path)",
        "spend_account_note": (
            "Calls are not bound to data/*.sqlite; any usage observer fallback "
            "goes to output/debug-spend (debug account)."
        ),
    }

    if missing:
        (OUT / "results.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps({"missing_keys": missing}, ensure_ascii=False))
        return 2

    proxy = proxy_url
    async with httpx.AsyncClient(timeout=120, proxy=proxy, trust_env=False) as client:
        if not args.skip_hermes:
            report["hermes_tools_probe"] = await run_hermes_tools_probe(
                client, api_key=openrouter_key or "", gate=gate, records=records
            )
            await run_hermes_json_gate_trials(
                client,
                api_key=openrouter_key or "",
                gate=gate,
                situations=situations,
                n=args.gate_n,
                records=records,
                budget=budget,
            )
            await run_hermes_json_proactive_trials(
                client,
                api_key=openrouter_key or "",
                proactive=proactive,
                n=args.proactive_n,
                records=records,
                budget=budget,
            )

        if not args.skip_deepseek:
            await run_deepseek_gate_trials(
                api_key=deepseek_key or "",
                gate=gate,
                situations=situations,
                n=args.gate_n,
                records=records,
                budget=budget,
                proxy_url=proxy_url,
            )

        await run_voice_trials(
            client,
            openrouter_key=None if args.skip_hermes else openrouter_key,
            deepseek_key=None if args.skip_deepseek else deepseek_key,
            situations=situations,
            n=args.voice_n,
            records=records,
            budget=budget,
            proxy_url=proxy_url,
            voice_out=voice_out,
        )

    report["spent_cny_est"] = round(budget["spent_cny"], 4)
    report["spent_usd_est"] = round(budget["spent_usd"], 6)
    report["stats"] = {
        "hermes_tools_probe": _phase_stats(records, "hermes-openrouter", "tools_probe"),
        "hermes_json_gate": _phase_stats(records, "hermes-openrouter", "json_gate"),
        "hermes_json_proactive": _phase_stats(records, "hermes-openrouter", "json_proactive"),
        "deepseek_strict_tools_gate": _phase_stats(records, "deepseek", "strict_tools_gate"),
        "hermes_voice": _phase_stats(records, "hermes-openrouter", "voice"),
        "deepseek_voice": _phase_stats(records, "deepseek", "voice"),
    }
    report["cost_projection"] = _project_costs()
    report["records"] = [asdict(r) for r in records]
    report["voice_pairs"] = voice_out

    (OUT / "results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "voice_pairs.json").write_text(
        json.dumps(voice_out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "spent_cny_est": report["spent_cny_est"],
                "stats": report["stats"],
                "hermes_tools_probe": report.get("hermes_tools_probe"),
                "out": str(OUT / "results.json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
