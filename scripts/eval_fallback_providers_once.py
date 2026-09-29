#!/usr/bin/env python3
"""One-shot fallback-provider bakeoff (NOT production).

Compares tool-capable Chinese candidates against DeepSeek on the same real
schemas used in production. Hermes numbers are loaded from
output/hermes-eval/results.json when present (no re-spend).

Constraints:
- Do not edit src/, restart production, write production ledgers, or send QQ.
- Budget ~¥8; prefer Beijing off-peak for DeepSeek control.
- Writes under output/fallback-provider/ (+ debug spend ledger).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "output" / "fallback-provider"
HERMES_RESULTS = REPO / "output" / "hermes-eval" / "results.json"
CNY_PER_USD = 7.2
BEIJING = ZoneInfo("Asia/Shanghai")

DEEPSEEK_OFFPEAK = {"cache_hit": 0.05, "cache_miss": 1.5, "completion": 4.5}

# Production epoch2 token totals (spend-audit).
PROD_PROMPT = 6_155_852
PROD_COMPLETION = 102_015
PROD_CACHE_HIT = 1_203_968


@dataclass(frozen=True)
class Candidate:
    id: str
    display: str
    channel: str  # dashscope | deepseek | openrouter
    model: str
    base_url: str
    api_key_env: str
    # Pricing for projection (CNY per million tokens when currency=cny).
    price_prompt_per_mtok: float
    price_completion_per_mtok: float
    price_currency: str  # cny | usd
    price_source: str
    schema_dialect: str  # deepseek-strict | standard
    enable_thinking: bool | None = None
    disable_deepseek_thinking: bool = False
    openrouter_disable_reasoning: bool = False
    notes: str = ""


CANDIDATES: list[Candidate] = [
    Candidate(
        id="deepseek-flash",
        display="DeepSeek V4 Flash (control)",
        channel="deepseek",
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        price_prompt_per_mtok=1.5,
        price_completion_per_mtok=4.5,
        price_currency="cny",
        price_source="https://api-docs.deepseek.com/zh-cn/quick_start/pricing",
        schema_dialect="deepseek-strict",
        disable_deepseek_thinking=True,
        notes="off-peak miss/output; peak 2x",
    ),
    Candidate(
        id="qwen-flash",
        display="Qwen-Flash (DashScope)",
        channel="dashscope",
        model="qwen-flash",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_env="QWEN_API_KEY",
        price_prompt_per_mtok=0.15,
        price_completion_per_mtok=1.5,
        price_currency="cny",
        price_source="https://help.aliyun.com/zh/model-studio/qwen-flash",
        schema_dialect="deepseek-strict",
        notes="0-128K tier; cache hit cheaper",
    ),
    Candidate(
        id="qwen-plus",
        display="Qwen-Plus (DashScope)",
        channel="dashscope",
        model="qwen-plus",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_env="QWEN_API_KEY",
        price_prompt_per_mtok=0.8,
        price_completion_per_mtok=2.0,
        price_currency="cny",
        price_source="https://help.aliyun.com/zh/model-studio/qwen-plus",
        schema_dialect="deepseek-strict",
        notes="non-thinking tier quoted on model page",
    ),
    Candidate(
        id="glm-4.7-flash",
        display="GLM-4.7-Flash (OpenRouter)",
        channel="openrouter",
        model="z-ai/glm-4.7-flash",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        price_prompt_per_mtok=0.06,
        price_completion_per_mtok=0.40,
        price_currency="usd",
        price_source="https://openrouter.ai/z-ai/glm-4.7-flash",
        schema_dialect="standard",
        openrouter_disable_reasoning=True,
        notes="OpenRouter; reasoning disabled for tool reliability/cost",
    ),
    Candidate(
        id="kimi-k2.5",
        display="Kimi K2.5 (OpenRouter)",
        channel="openrouter",
        model="moonshotai/kimi-k2.5",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        price_prompt_per_mtok=0.45,
        price_completion_per_mtok=2.25,
        price_currency="usd",
        price_source="https://openrouter.ai/moonshotai/kimi-k2.5",
        schema_dialect="standard",
        openrouter_disable_reasoning=True,
    ),
    Candidate(
        id="minimax-m2.5",
        display="MiniMax M2.5 (OpenRouter)",
        channel="openrouter",
        model="minimax/minimax-m2.5",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        price_prompt_per_mtok=0.22,
        price_completion_per_mtok=0.90,
        price_currency="usd",
        price_source="https://openrouter.ai/minimax/minimax-m2.5",
        schema_dialect="standard",
        openrouter_disable_reasoning=True,
    ),
]


@dataclass
class CallRecord:
    candidate_id: str
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
    cost_cny: float = 0.0
    cost_usd: float = 0.0
    error: str = ""
    raw_excerpt: str = ""
    her_text: str = ""
    notes: list[str] = field(default_factory=list)


def _load_dotenv() -> None:
    path = REPO / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def _beijing_hour() -> int:
    return datetime.now(tz=BEIJING).hour


def _is_deepseek_peak(hour: int | None = None) -> bool:
    h = _beijing_hour() if hour is None else hour
    return (9 <= h < 12) or (14 <= h < 18)


def _excerpt(text: str, n: int = 700) -> str:
    text = text.replace("\n", "\\n")
    return text if len(text) <= n else text[: n - 3] + "..."


def _apply_provider_flags(cand: Candidate, payload: dict[str, Any]) -> None:
    if cand.enable_thinking is False:
        payload["enable_thinking"] = False
    if cand.disable_deepseek_thinking:
        payload["thinking"] = {"type": "disabled"}
    if cand.openrouter_disable_reasoning:
        payload["reasoning"] = {"enabled": False}


def _estimate_cost_cny(cand: Candidate, prompt: int, completion: int, cache_hit: int = 0) -> tuple[float, float]:
    """Return (cost_cny, cost_usd)."""

    if cand.channel == "deepseek":
        peak = _is_deepseek_peak()
        mult = 2.0 if peak else 1.0
        miss = max(0, prompt - cache_hit)
        hit = min(cache_hit, prompt)
        cny = (
            hit * DEEPSEEK_OFFPEAK["cache_hit"] * mult
            + miss * DEEPSEEK_OFFPEAK["cache_miss"] * mult
            + completion * DEEPSEEK_OFFPEAK["completion"] * mult
        ) / 1_000_000.0
        return cny, cny / CNY_PER_USD
    prompt_cost = prompt * cand.price_prompt_per_mtok / 1_000_000.0
    completion_cost = completion * cand.price_completion_per_mtok / 1_000_000.0
    total = prompt_cost + completion_cost
    if cand.price_currency == "cny":
        return total, total / CNY_PER_USD
    return total * CNY_PER_USD, total


def _project_prod_cny(cand: Candidate, first_pass_rate: float) -> dict[str, Any]:
    cny, usd = _estimate_cost_cny(cand, PROD_PROMPT, PROD_COMPLETION, PROD_CACHE_HIT)
    # DeepSeek projection uses cache; others ignore cache discount (conservative).
    if cand.channel != "deepseek" and cand.price_currency == "cny":
        cny, usd = _estimate_cost_cny(cand, PROD_PROMPT, PROD_COMPLETION, 0)
    rate = first_pass_rate if first_pass_rate > 0 else 0.0
    adjusted = (cny / rate) if rate > 0 else None
    return {
        "base_cny": round(cny, 2),
        "base_usd": round(usd, 4),
        "first_pass_rate": rate,
        "retry_adjusted_cny": round(adjusted, 2) if adjusted is not None else None,
        "price_source": cand.price_source,
    }


def _gold_reply_only_payload() -> dict[str, Any]:
    return {
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


def _gate_system_prompt(contract: Any, *, curly_quote_stress: bool = False) -> str:
    fn = contract.provider_tools[0]["function"]
    gold_inner = _gold_reply_only_payload()
    if curly_quote_stress:
        # Intentionally ask model to include Chinese curly quotes inside beat.text
        # while keeping payload_json a valid JSON string (must escape ASCII ").
        gold_inner = json.loads(json.dumps(gold_inner))
        gold_inner["events"][0]["beat"]["text"] = "真的吗？那张「书店」照片挺普通的啊。"
    carrier = {
        "result_kind": "reply_only",
        "payload_json": json.dumps(gold_inner, ensure_ascii=False, separators=(",", ":")),
    }
    stress = (
        "\n压力测试：beat.text 里必须出现中文弯引号「」，同时整段 payload_json 仍是合法 JSON 字符串。\n"
        if curly_quote_stress
        else ""
    )
    return (
        "你是沈知栀（Celia Shen），说中文。做结构化决定，不是助手。\n"
        "外层只有 result_kind 与 payload_json；payload_json 必须是合法 JSON 字符串"
        "（内部 ASCII 双引号全部转义）。枚举与示例一致：timing_choice=now|later|silent；"
        "confidence 为整数；media_request=none；appraise 为布尔；affect=no_change。\n"
        "优先 reply_only。不要免责声明。\n"
        f"{stress}"
        f"合法示例：\n{json.dumps(carrier, ensure_ascii=False)}\n\n"
        f"工具名: {fn['name']}\n工具说明: {fn['description']}\n"
        f"参数 schema: {json.dumps(fn['parameters'], ensure_ascii=False)}"
    )


def _gate_user_prompt(situation: dict[str, Any], *, curly_quote_stress: bool = False) -> str:
    history = "\n".join(situation["conversation"])
    extra = "（回复里用「」提到书店照片）" if curly_quote_stress else ""
    return (
        f"当前对话：\n{history}\n\n他最新一句：{situation['him']}{extra}\n"
        "提交一次 compact gate（优先 reply_only）。"
    )


def _voice_system_prompt() -> str:
    return (
        "你是沈知栀，二十出头中国女生，QQ 和熟人聊天。"
        "短句、口语、懒散但不冷淡；不要助手腔、免责声明、列举、过度热情。"
        "只输出气泡，每条一行；不要 JSON，不要旁白。"
    )


def _voice_user_prompt(situation: dict[str, Any]) -> str:
    history = "\n".join(situation["conversation"])
    return f"对话：\n{history}\n\n他刚说：{situation['him']}\n你回："


def _load_situations() -> list[dict[str, Any]]:
    turns_dir = REPO / "output" / "conversation-slice" / "turns"
    out: list[dict[str, Any]] = []
    for path in sorted(turns_dir.glob("t*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        out.append(
            {
                "id": path.stem,
                "him": data.get("him") or "",
                "conversation": list(data.get("conversation") or [])[-12:],
                "her_reference": list(data.get("her_texts") or []),
            }
        )
    if len(out) < 8:
        raise RuntimeError(f"need ≥8 slice turns, found {len(out)}")
    return out


def _build_gate(dialect: str) -> Any:
    from companion_daemon.world_v2.character_interior.inbound_tool_contract import (
        InboundToolContracts,
    )
    from companion_daemon.world_v2.expression_draft import QQ_NAPCAT_EXPRESSION_CAPABILITIES

    return InboundToolContracts().compact_gate_for(
        capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
        recall_allowed=True,
        schema_dialect=dialect,  # type: ignore[arg-type]
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    from companion_daemon.world_v2.json_wire_repair import loads_one_json_object

    value = loads_one_json_object(text)
    if not isinstance(value, dict):
        raise ValueError("json root is not object")
    return value


def _validate_gate(contract: Any, carrier: dict[str, Any]) -> tuple[bool, str, list[str]]:
    notes: list[str] = []
    her = ""
    inner = carrier.get("payload_json")
    if isinstance(inner, str):
        try:
            from companion_daemon.world_v2.json_wire_repair import loads_one_json_object

            obj = loads_one_json_object(inner)
            notes.append("inner_parse=ok" if isinstance(obj, dict) else "inner_parse=non_object")
            if isinstance(obj, dict):
                for event in obj.get("events") or []:
                    if isinstance(event, dict):
                        beat = event.get("beat")
                        if isinstance(beat, dict) and isinstance(beat.get("text"), str):
                            her = beat["text"]
                            break
        except Exception as exc:
            notes.append(f"inner_parse:{type(exc).__name__}:{exc}")
    try:
        decoded = contract.decode(json.dumps(carrier, ensure_ascii=False))
        notes.append(f"kind={decoded.get('result_kind')}")
        return True, her, notes
    except Exception as exc:
        notes.append(f"decode:{type(exc).__name__}:{exc}")
        return False, her, notes


def _tool_arguments_from_body(body: dict[str, Any]) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        return ""
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        fn = tool_calls[0].get("function") if isinstance(tool_calls[0], dict) else None
        if isinstance(fn, dict) and isinstance(fn.get("arguments"), str):
            return fn["arguments"]
    content = message.get("content")
    return content if isinstance(content, str) else ""


def _usage_tokens(body: dict[str, Any]) -> tuple[int, int, int]:
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    cached = 0
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        cached = int(details.get("cached_tokens") or 0)
    cached = cached or int(usage.get("prompt_cache_hit_tokens") or 0)
    return prompt, completion, cached


async def _post_chat(
    client: httpx.AsyncClient,
    *,
    cand: Candidate,
    api_key: str,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any], str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if cand.channel == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/local/girl-agent-fallback-eval"
        headers["X-Title"] = "girl-agent-fallback-eval"
    url = cand.base_url.rstrip("/") + "/chat/completions"
    response = await client.post(url, headers=headers, json=payload)
    text = response.text
    try:
        body = response.json()
    except Exception:
        body = {"raw": text}
    return response.status_code, body, text


async def tools_probe(
    client: httpx.AsyncClient,
    *,
    cand: Candidate,
    api_key: str,
    gate: Any,
) -> dict[str, Any]:
    tools = list(gate.provider_tools)
    payload: dict[str, Any] = {
        "model": cand.model,
        "messages": [
            {"role": "system", "content": "Call the forced tool immediately. No prose."},
            {"role": "user", "content": "ping"},
        ],
        "tools": tools,
        "tool_choice": {
            "type": "function",
            "function": {"name": tools[0]["function"]["name"]},
        },
        # Reasoning providers (GLM) may spend hundreds of tokens before arguments.
        "max_tokens": 900,
        "temperature": 0.0,
    }
    _apply_provider_flags(cand, payload)
    status, body, raw = await _post_chat(client, cand=cand, api_key=api_key, payload=payload)
    args = _tool_arguments_from_body(body) if status == 200 else ""
    has_calls = False
    if status == 200:
        choices = body.get("choices") if isinstance(body.get("choices"), list) else []
        if choices and isinstance(choices[0], dict):
            msg = choices[0].get("message") if isinstance(choices[0].get("message"), dict) else {}
            calls = msg.get("tool_calls") if isinstance(msg, dict) else None
            has_calls = isinstance(calls, list) and bool(calls) and bool(args)
    return {
        "http_status": status,
        "has_tool_calls": has_calls,
        "error": "" if status == 200 else _excerpt(str(body.get("error") or raw), 400),
        "excerpt": _excerpt(args or raw, 300),
    }


async def run_gate_trials(
    client: httpx.AsyncClient,
    *,
    cand: Candidate,
    api_key: str,
    gate: Any,
    situations: list[dict[str, Any]],
    n: int,
    curly_n: int,
    records: list[CallRecord],
    budget: dict[str, float],
) -> None:
    tool_name = gate.provider_tools[0]["function"]["name"]
    total = n + curly_n
    for i in range(total):
        if budget["spent_cny"] >= budget["cap_cny"]:
            break
        stress = i >= n
        situation = situations[i % len(situations)]
        messages = [
            {"role": "system", "content": _gate_system_prompt(gate, curly_quote_stress=stress)},
            {"role": "user", "content": _gate_user_prompt(situation, curly_quote_stress=stress)},
        ]
        payload: dict[str, Any] = {
            "model": cand.model,
            "messages": messages,
            "tools": list(gate.provider_tools),
            "tool_choice": {"type": "function", "function": {"name": tool_name}},
            "max_tokens": 1600,
            "temperature": 0.7,
        }
        _apply_provider_flags(cand, payload)
        t0 = time.perf_counter()
        status, body, raw = await _post_chat(client, cand=cand, api_key=api_key, payload=payload)
        latency = time.perf_counter() - t0
        prompt, completion, cached = _usage_tokens(body) if status < 500 else (0, 0, 0)
        cost_cny, cost_usd = _estimate_cost_cny(cand, prompt, completion, cached)
        # OpenRouter may report cost in usage.
        if cand.channel == "openrouter" and isinstance(body.get("usage"), dict):
            reported = body["usage"].get("cost")
            if isinstance(reported, (int, float)):
                cost_usd = float(reported)
                cost_cny = cost_usd * CNY_PER_USD
        budget["spent_cny"] += cost_cny
        phase = "curly_quote_stress" if stress else "strict_tools_gate"
        record = CallRecord(
            candidate_id=cand.id,
            phase=phase,
            trial=i if not stress else i - n,
            ok_http=status == 200,
            ok_parse=False,
            ok_contract=False,
            repaired=False,
            latency_s=latency,
            prompt_tokens=prompt,
            completion_tokens=completion,
            cache_hit_tokens=cached,
            cost_cny=cost_cny,
            cost_usd=cost_usd,
            notes=[f"situation={situation['id']}", f"http={status}", f"dialect={cand.schema_dialect}"],
        )
        if status != 200:
            record.error = _excerpt(str(body.get("error") or raw), 500)
            record.raw_excerpt = _excerpt(raw)
            records.append(record)
            continue
        content = _tool_arguments_from_body(body)
        record.raw_excerpt = _excerpt(content)
        repaired = False
        try:
            carrier = _extract_json_object(content)
            record.ok_parse = True
        except Exception as exc:
            try:
                from companion_daemon.world_v2.json_wire_repair import loads_one_json_object

                carrier = loads_one_json_object(content)
                if not isinstance(carrier, dict):
                    raise ValueError("not object")
                repaired = True
                record.ok_parse = True
            except Exception as exc2:
                record.error = f"parse:{type(exc).__name__}/{exc2}"
                records.append(record)
                continue
        ok, her, notes = _validate_gate(gate, carrier)
        record.ok_contract = ok
        record.repaired = repaired
        record.her_text = her
        record.notes.extend(notes)
        if stress:
            record.notes.append(
                "curly_in_text=" + str(("「" in her) or ("」" in her) or ("“" in her) or ("”" in her))
            )
        if not ok:
            record.error = ";".join(notes)[:500]
        records.append(record)


async def run_voice_trials(
    client: httpx.AsyncClient,
    *,
    cand: Candidate,
    api_key: str,
    situations: list[dict[str, Any]],
    n: int,
    records: list[CallRecord],
    budget: dict[str, float],
    voice_rows: dict[str, dict[str, Any]],
) -> None:
    for i in range(n):
        if budget["spent_cny"] >= budget["cap_cny"]:
            break
        situation = situations[i % len(situations)]
        voice_rows.setdefault(
            situation["id"],
            {
                "situation_id": situation["id"],
                "him": situation["him"],
                "conversation": situation["conversation"],
                "her_reference": situation["her_reference"],
                "by_candidate": {},
            },
        )
        payload: dict[str, Any] = {
            "model": cand.model,
            "messages": [
                {"role": "system", "content": _voice_system_prompt()},
                {"role": "user", "content": _voice_user_prompt(situation)},
            ],
            "max_tokens": 220,
            "temperature": 0.85,
        }
        _apply_provider_flags(cand, payload)
        t0 = time.perf_counter()
        status, body, raw = await _post_chat(client, cand=cand, api_key=api_key, payload=payload)
        latency = time.perf_counter() - t0
        prompt, completion, cached = _usage_tokens(body) if status == 200 else (0, 0, 0)
        cost_cny, cost_usd = _estimate_cost_cny(cand, prompt, completion, cached)
        if cand.channel == "openrouter" and isinstance(body.get("usage"), dict):
            reported = body["usage"].get("cost")
            if isinstance(reported, (int, float)):
                cost_usd = float(reported)
                cost_cny = cost_usd * CNY_PER_USD
        budget["spent_cny"] += cost_cny
        text = ""
        if status == 200:
            choices = body.get("choices") or []
            if choices and isinstance(choices[0], dict):
                msg = choices[0].get("message") or {}
                if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                    text = msg["content"]
        voice_rows[situation["id"]]["by_candidate"][cand.id] = text
        records.append(
            CallRecord(
                candidate_id=cand.id,
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
                cost_cny=cost_cny,
                cost_usd=cost_usd,
                her_text=text,
                error="" if status == 200 else _excerpt(str(body.get("error") or raw), 400),
                notes=[f"situation={situation['id']}"],
            )
        )


def _phase_stats(records: list[CallRecord], candidate_id: str, phase: str) -> dict[str, Any]:
    rows = [r for r in records if r.candidate_id == candidate_id and r.phase == phase]
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
        "errors": [r.error for r in rows if r.error][:6],
    }


def _load_hermes_reference() -> dict[str, Any]:
    if not HERMES_RESULTS.exists():
        return {"available": False}
    data = json.loads(HERMES_RESULTS.read_text(encoding="utf-8"))
    stats = data.get("stats") or {}
    voice = []
    for row in data.get("voice_pairs") or []:
        voice.append(
            {
                "situation_id": row.get("situation_id"),
                "him": row.get("him"),
                "hermes": row.get("hermes"),
                "deepseek_from_hermes_eval": row.get("deepseek"),
                "her_reference": row.get("her_reference"),
            }
        )
    return {
        "available": True,
        "tools_probe": data.get("hermes_tools_probe"),
        "json_gate": stats.get("hermes_json_gate"),
        "voice": voice,
        "source": str(HERMES_RESULTS),
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate-n", type=int, default=15)
    parser.add_argument("--curly-n", type=int, default=3)
    parser.add_argument("--voice-n", type=int, default=8)
    parser.add_argument("--budget-cny", type=float, default=8.0)
    parser.add_argument(
        "--only",
        default="",
        help="comma-separated candidate ids; empty = all with keys",
    )
    args = parser.parse_args()
    _load_dotenv()
    OUT.mkdir(parents=True, exist_ok=True)

    only = {x.strip() for x in args.only.split(",") if x.strip()}
    situations = _load_situations()
    records: list[CallRecord] = []
    voice_rows: dict[str, dict[str, Any]] = {}
    budget = {"cap_cny": args.budget_cny, "spent_cny": 0.0}
    probes: dict[str, Any] = {}
    missing_keys: list[str] = []
    skipped: list[dict[str, str]] = []

    # Credential gaps for candidates the user named but we cannot reach natively.
    for label, envs in [
        ("智谱 GLM 原生", ["ZHIPU_API_KEY", "GLM_API_KEY"]),
        ("Moonshot/Kimi 原生", ["MOONSHOT_API_KEY", "KIMI_API_KEY"]),
        ("MiniMax 原生", ["MINIMAX_API_KEY"]),
        ("豆包/火山方舟对话", ["ARK_API_KEY", "VOLCENGINE_ARK_API_KEY"]),
    ]:
        if not any(os.environ.get(e) for e in envs):
            missing_keys.append(f"{label}: need one of {envs} (OpenRouter may still cover some)")

    proxy = os.environ.get("OPENAI_PROXY_URL") or None
    report: dict[str, Any] = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "beijing_hour": _beijing_hour(),
        "deepseek_peak_now": _is_deepseek_peak(),
        "budget_cap_cny": args.budget_cny,
        "missing_native_keys": missing_keys,
        "hermes_reference": _load_hermes_reference(),
    }

    async with httpx.AsyncClient(timeout=120, proxy=proxy, trust_env=False) as client:
        for cand in CANDIDATES:
            if only and cand.id not in only:
                continue
            api_key = os.environ.get(cand.api_key_env)
            if not api_key:
                skipped.append({"id": cand.id, "reason": f"missing {cand.api_key_env}"})
                continue
            if budget["spent_cny"] >= budget["cap_cny"]:
                skipped.append({"id": cand.id, "reason": "budget_cap"})
                continue
            gate = _build_gate(cand.schema_dialect)
            probe = await tools_probe(client, cand=cand, api_key=api_key, gate=gate)
            probes[cand.id] = probe
            if probe["http_status"] != 200 or not probe["has_tool_calls"]:
                skipped.append(
                    {
                        "id": cand.id,
                        "reason": f"tools_probe_failed:{probe.get('error') or probe.get('http_status')}",
                    }
                )
                continue
            await run_gate_trials(
                client,
                cand=cand,
                api_key=api_key,
                gate=gate,
                situations=situations,
                n=args.gate_n,
                curly_n=args.curly_n,
                records=records,
                budget=budget,
            )
            await run_voice_trials(
                client,
                cand=cand,
                api_key=api_key,
                situations=situations,
                n=args.voice_n,
                records=records,
                budget=budget,
                voice_rows=voice_rows,
            )

    stats: dict[str, Any] = {}
    projections: dict[str, Any] = {}
    for cand in CANDIDATES:
        gate_stats = _phase_stats(records, cand.id, "strict_tools_gate")
        curly_stats = _phase_stats(records, cand.id, "curly_quote_stress")
        voice_stats = _phase_stats(records, cand.id, "voice")
        stats[cand.id] = {
            "display": cand.display,
            "channel": cand.channel,
            "model": cand.model,
            "probe": probes.get(cand.id),
            "strict_tools_gate": gate_stats,
            "curly_quote_stress": curly_stats,
            "voice": voice_stats,
            "price_source": cand.price_source,
            "notes": cand.notes,
        }
        rate = float(gate_stats.get("contract_rate_incl_repair") or 0.0)
        if gate_stats.get("n", 0) > 0:
            projections[cand.id] = _project_prod_cny(cand, rate)

    report["spent_cny_est"] = round(budget["spent_cny"], 4)
    report["probes"] = probes
    report["skipped"] = skipped
    report["stats"] = stats
    report["cost_projections"] = projections
    report["records"] = [asdict(r) for r in records]
    report["voice_pairs"] = list(voice_rows.values())
    report["candidates"] = [asdict(c) for c in CANDIDATES]

    (OUT / "results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "voice_pairs.json").write_text(
        json.dumps(list(voice_rows.values()), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "spent_cny_est": report["spent_cny_est"],
                "skipped": skipped,
                "stats": {
                    k: {
                        "gate": v["strict_tools_gate"],
                        "curly": v["curly_quote_stress"],
                        "voice_n": v["voice"].get("n"),
                    }
                    for k, v in stats.items()
                    if v["strict_tools_gate"].get("n") or v.get("probe")
                },
                "out": str(OUT / "results.json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
