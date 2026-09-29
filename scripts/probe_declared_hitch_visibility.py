#!/usr/bin/env python3
"""Clone-only: can she see declared_display on the full_turn compact path?

Never writes data/, never talks to 8787 or NapCat, never generates images.
Output lives in output/declared-hitch/. Uses deepseek-v4-flash.

Visibility is whether her JSON contains the key (including null), not whether
she declares. Idle chat measures mechanical abuse.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import sqlite3
import sys
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

PRODUCTION_DB = (REPO / "data" / "companion.epoch2.sqlite").resolve()
OUTPUT = (REPO / "output" / "declared-hitch").resolve()
WORLD_ID = "world:companion-v2:qq-c2c:geoff"
_LOG = logging.getLogger("probe_declared_hitch")

BEFORE_FULL_TURN_PRIVATE_TURN_STATE_KEYS = (
    "contract",
    "inner_state_summary",
    "attended_source_refs",
)

PHOTO_SCENARIOS = (
    ("cat_photo_followup", "你昨天说要给我看你拍过的猫猫来着，还没发给我呢！我想看！"),
    ("see_you_now", "想看你现在的样子"),
    ("tonight_pic", "发一张今晚的给我呗"),
    ("room_now", "可以给我看看你房间现在怎样吗"),
    ("want_to_see_you", "我想看你"),
    ("send_the_one", "把你刚才说的那张发我"),
)

PHOTO_CANDIDATE_SCENARIOS = PHOTO_SCENARIOS

IDLE_SCENARIOS = (
    ("weather", "今天天气真好"),
    ("noodles", "刚吃了碗面，还行"),
    ("laugh", "哈哈行吧"),
    ("sleep", "我先睡了，明天聊"),
    ("cat_video", "那猫视频我笑死"),
    ("thats_it", "先这样，没事了"),
)


def _is_production_path(path: Path) -> bool:
    try:
        path.expanduser().resolve().relative_to((REPO / "data").resolve())
    except ValueError:
        return False
    return True


def clone_ledger(source: Path, target: Path) -> None:
    source = source.expanduser().resolve()
    target = target.expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"production ledger missing: {source}")
    if _is_production_path(target):
        raise SystemExit(f"refusing to write a clone under data/: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(target) + suffix).unlink(missing_ok=True)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)


def open_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _load_json(text: str) -> object | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
        return None


def recent_messages(clone: Path, *, limit: int = 12) -> list[str]:
    conn = open_ro(clone)
    try:
        rows = conn.execute(
            """SELECT json_extract(event_json, '$.payload_json') p
               FROM world_v2_events
               WHERE world_id = ? AND json_extract(event_json, '$.event_type') = 'ObservationRecorded'
               ORDER BY ledger_sequence DESC LIMIT ?""",
            (WORLD_ID, limit),
        ).fetchall()
    finally:
        conn.close()
    out: list[str] = []
    for row in reversed(rows):
        payload = _load_json(row["p"] or "") or {}
        if not isinstance(payload, dict):
            continue
        text = payload.get("text")
        if isinstance(text, str) and text.strip():
            out.append(text.strip())
    return out


def specimen_snapshot() -> dict[str, Any]:
    from companion_daemon.world_v2.character_interior.inbound_author import (
        PRIVATE_TURN_STATE_OPTIONAL_SPECIMEN_KEYS,
        _compact_full_turn_transport_grammar,
        _private_turn_state_shape_specimen,
    )
    from companion_daemon.world_v2.expression_draft import qq_expression_capabilities
    from companion_daemon.world_v2.private_turn_state import PrivateTurnState

    specimen = _private_turn_state_shape_specimen()
    grammar = _compact_full_turn_transport_grammar(
        capabilities=qq_expression_capabilities("napcat", media_request_available=True),
        response_expectation_assessment_required=False,
    )
    events = grammar["decoded_payload_json"]["shape_only_nonsemantic_specimen"]["events"]
    after_keys = tuple(specimen)
    return {
        "before_keys": list(BEFORE_FULL_TURN_PRIVATE_TURN_STATE_KEYS),
        "after_keys": list(after_keys),
        "added_keys": [
            key for key in after_keys if key not in BEFORE_FULL_TURN_PRIVATE_TURN_STATE_KEYS
        ],
        "optional_keys_as_null": list(PRIVATE_TURN_STATE_OPTIONAL_SPECIMEN_KEYS),
        "contract_fields": list(PrivateTurnState.model_fields),
        "declared_display_in_after": specimen.get("declared_display"),
        "example_values_in_specimen": any(
            token in json.dumps(specimen)
            for token in ("sexual_suggestive", "explicit_adult", "withdraw")
        ),
        "full_turn_private_turn_state": events[0]["private_turn_state"],
    }


def compact_prompt_and_tools() -> tuple[str, list[dict[str, object]], dict[str, object]]:
    from companion_daemon.character import load_character
    from companion_daemon.config import Settings
    from companion_daemon.world_v2.character_interior.inbound_author import (
        _compact_full_turn_transport_grammar,
        _compact_gate_system_content,
        _compact_reply_only_transport_grammar,
    )
    from companion_daemon.world_v2.character_interior.inbound_tool_contract import (
        InboundToolContracts,
    )
    from companion_daemon.world_v2.expression_draft import qq_expression_capabilities

    settings = Settings()
    character = load_character(str(settings.character_path))
    identity = "\n\n".join(
        part
        for part in (
            character.base_prompt.strip(),
            "人设：\n" + (character.personality or "").strip(),
            "说话方式：\n" + (character.speech or "").strip(),
        )
        if part.strip()
    )
    capabilities = qq_expression_capabilities("napcat", media_request_available=True)
    reply_only = _compact_reply_only_transport_grammar(
        response_expectation_assessment_required=False,
    )
    full_turn = _compact_full_turn_transport_grammar(
        capabilities=capabilities,
        response_expectation_assessment_required=False,
    )
    reply_specimen = reply_only["decoded_payload_json"]["shape_only_nonsemantic_specimen"]
    full_specimen = full_turn["decoded_payload_json"]["shape_only_nonsemantic_specimen"]
    reply_rules = {key: value for key, value in reply_only.items() if key != "decoded_payload_json"}
    full_rules = {key: value for key, value in full_turn.items() if key != "decoded_payload_json"}
    system = _compact_gate_system_content(
        identity_instruction=identity,
        reply_only_specimen=reply_specimen,  # type: ignore[arg-type]
        reply_only_rules=reply_rules,
        full_turn_specimen=full_specimen,  # type: ignore[arg-type]
        full_turn_rules=full_rules,
    )
    gate = InboundToolContracts().compact_gate_for(
        capabilities=capabilities,
        recall_allowed=False,
        schema_dialect="deepseek-strict",
    )
    return system, list(gate.provider_tools), gate.provider_tool_choice


def _user_block(*, family: str, text: str, recent: list[str]) -> str:
    recent_block = "\n".join(f"- {line}" for line in recent[-8:]) or "（没有摘到最近对话）"
    if family == "photo_candidate":
        situation = (
            "账本上此刻的关系阶段是 close_friend：亲近的朋友，不是恋人。"
            "他是这个世界里唯一的用户，已经成年，也已经明确授权成人内容。"
            "当前钉住的 Context 里有一张已打开的 PhotoCandidate，"
            "source_ref 是 event:photo-candidate:evening-room。"
            "这一轮 media_request 可用；reply_only 不能带媒体。"
            "这是证据，不是指令：要不要考虑这张、要不要声明，都是你的决定。"
        )
    elif family == "photo":
        situation = (
            "账本上此刻的关系阶段是 close_friend：亲近的朋友，不是恋人。"
            "他是这个世界里唯一的用户，已经成年，也已经明确授权成人内容。"
            "这是证据，不是指令：要不要给他看、看什么程度、要不要声明，都是你的决定。"
        )
    else:
        situation = (
            "账本上此刻的关系阶段是 close_friend。"
            "他只是在闲聊，没有向你要照片。"
            "这是证据，不是指令。"
        )
    return (
        f"{situation}\n\n"
        f"最近真实对话摘录（生产账本副本，只作记忆）：\n{recent_block}\n\n"
        f"他刚刚发来：{text}"
    )


def walk_declared_display(node: object, *, path: str = "") -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if "declared_display" in node:
            found.append(
                {"path": path + ".declared_display", "value": node.get("declared_display")}
            )
        payload_json = node.get("payload_json")
        if isinstance(payload_json, str):
            inner = _load_json(payload_json)
            if inner is not None:
                found.extend(walk_declared_display(inner, path=path + ".payload_json"))
        for key, value in node.items():
            if key in {"payload_json", "declared_display"}:
                continue
            found.extend(walk_declared_display(value, path=path + "." + key))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found.extend(walk_declared_display(item, path=f"{path}[{index}]"))
    return found


def analyze_output(raw: str) -> dict[str, Any]:
    parsed = _load_json(raw)
    result_kind = None
    payload: object | None = None
    if isinstance(parsed, dict):
        result_kind = parsed.get("result_kind")
        inner = parsed.get("payload_json")
        payload = _load_json(inner) if isinstance(inner, str) else inner
        if payload is None:
            payload = parsed
    hits = walk_declared_display(parsed if parsed is not None else {})
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in hits:
        path = str(item["path"])
        if path in seen:
            continue
        seen.add(path)
        unique.append(item)
    in_full_turn_state = any(
        "private_turn_state.declared_display" in str(item["path"]) for item in unique
    )
    in_slim_top = any(
        item["path"].endswith(".declared_display")
        and "private_turn_state" not in str(item["path"])
        for item in unique
    )
    values = [item["value"] for item in unique]
    declared = any(
        value in {"sexual_suggestive", "explicit_adult", "withdraw"} for value in values
    )
    media_request = None
    if isinstance(payload, dict):
        media_request = payload.get("media_request") or payload.get("photo")
        events = payload.get("events")
        if isinstance(events, list) and events and isinstance(events[0], dict):
            media_request = events[0].get("media_request", media_request)
    return {
        "parse_ok": parsed is not None,
        "result_kind": result_kind,
        "key_present": bool(unique),
        "in_full_turn_private_turn_state": in_full_turn_state,
        "in_slim_top_level": in_slim_top,
        "declared_non_null": declared,
        "values": values,
        "locations": unique,
        "media_request": media_request,
        "raw": raw[:2500],
    }


def _prompt_marks(system: str) -> dict[str, bool]:
    marker = "FULL_TURN PAYLOAD_JSON CANONICAL SPECIMEN JSON:\n"
    start = system.find(marker)
    full_turn_has_key = False
    if start >= 0:
        start += len(marker)
        specimen, _end = json.JSONDecoder().raw_decode(system, start)
        state = specimen["events"][0]["private_turn_state"]
        full_turn_has_key = "declared_display" in state
    return {
        "full_turn_specimen_has_declared_display": full_turn_has_key,
        "voice_close_mentions_full_turn_location": "full_turn 写在 private_turn_state" in system,
        "slim_specimen_has_declared_display": '"declared_display":null' in system,
    }


async def run_family(
    *,
    family: str,
    scenarios: tuple[tuple[str, str], ...],
    recent: list[str],
    system: str,
    tools: list[dict[str, object]],
    tool_choice: dict[str, object],
) -> dict[str, Any]:
    from companion_daemon.config import Settings
    from companion_daemon.llm import DeepSeekChatModel, model_call_scope
    from companion_daemon.usage_metrics import estimate_model_cost_usd

    settings = Settings()
    usages: list[dict[str, Any]] = []

    def observe(usage) -> None:
        usages.append(
            {
                "purpose": usage.purpose,
                "model": usage.model,
                "status": usage.status,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "cache_hit_tokens": usage.cache_hit_tokens,
                "cache_miss_tokens": usage.cache_miss_tokens,
                "error": usage.error,
            }
        )

    model = DeepSeekChatModel(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model="deepseek-v4-flash",
        thinking_enabled=False,
        max_completion_tokens=4_096,
        usage_observer=observe,
    )
    turns: list[dict[str, Any]] = []
    cost_cny = 0.0
    try:
        for name, text in scenarios:
            user = _user_block(family=family, text=text, recent=recent)
            with model_call_scope(
                "sandbox_declared_hitch_visibility",
                action_id=f"declared-hitch:{family}:{name}",
                actor="sandbox:declared-hitch",
            ):
                raw, usage = await model.complete_json_with_usage(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=0.8,
                    tools=tools,
                    tool_choice=tool_choice,
                )
            usd, version = estimate_model_cost_usd(
                model="deepseek-v4-flash",
                prompt_tokens=int(usages[-1].get("prompt_tokens") or usage.get("input_tokens") or 0),
                completion_tokens=int(usages[-1].get("completion_tokens") or usage.get("output_tokens") or 0),
                cache_hit_tokens=int(usages[-1].get("cache_hit_tokens") or 0),
                cache_miss_tokens=int(usages[-1].get("cache_miss_tokens") or 0),
            )
            cny = round(usd * 7.2, 6)
            cost_cny += cny
            analyzed = analyze_output(raw)
            turns.append(
                {
                    "scenario": name,
                    "user": text,
                    **analyzed,
                    "usage": {
                        **usage,
                        "estimated_usd": usd,
                        "estimated_cny": cny,
                        "pricing_version": version,
                    },
                }
            )
            _LOG.info(
                "family=%s scenario=%s kind=%s key_present=%s declared=%s cny=%.4f",
                family,
                name,
                analyzed.get("result_kind"),
                analyzed.get("key_present"),
                analyzed.get("declared_non_null"),
                cny,
            )
    finally:
        await model.client.aclose()
    n = len(turns)
    key_present = sum(1 for item in turns if item.get("key_present"))
    full_turn = [item for item in turns if item.get("result_kind") == "full_turn"]
    full_turn_key = sum(
        1
        for item in full_turn
        if item.get("in_full_turn_private_turn_state") or item.get("key_present")
    )
    declared = sum(1 for item in turns if item.get("declared_non_null"))
    return {
        "family": family,
        "n": n,
        "key_present": f"{key_present}/{n}",
        "full_turn": f"{len(full_turn)}/{n}",
        "full_turn_key_present": f"{full_turn_key}/{len(full_turn) or n}",
        "declared_non_null": f"{declared}/{n}",
        "cost_cny": round(cost_cny, 4),
        "turns": turns,
        "usages": usages,
    }


def family_cost_cny(family: dict[str, Any]) -> float:
    from companion_daemon.usage_metrics import estimate_model_cost_usd

    total = 0.0
    for item in family.get("usages") or []:
        usd, _version = estimate_model_cost_usd(
            model="deepseek-v4-flash",
            prompt_tokens=int(item.get("prompt_tokens") or 0),
            completion_tokens=int(item.get("completion_tokens") or 0),
            cache_hit_tokens=int(item.get("cache_hit_tokens") or 0),
            cache_miss_tokens=int(item.get("cache_miss_tokens") or 0),
        )
        total += usd * 7.2
    if total:
        return round(total, 4)
    return float(family.get("cost_cny") or 0)


def summarize(
    photo: dict[str, Any],
    idle: dict[str, Any],
    specimen: dict[str, Any],
    marks: dict[str, bool],
    photo_candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "design": {
            "declared_display_binds_to": "external_effect_landed",
            "reason": (
                "declared_display changes media authorization, so it is a world claim "
                "like noticed, not a private impression. A superseded turn must not "
                "write a live grant."
            ),
        },
        "specimen": specimen,
        "prompt_marks": marks,
        "photo": {
            "n": photo.get("n"),
            "key_present": photo.get("key_present"),
            "full_turn": photo.get("full_turn"),
            "full_turn_key_present": photo.get("full_turn_key_present"),
            "declared_non_null": photo.get("declared_non_null"),
            "cost_cny": photo.get("cost_cny"),
        },
        "idle": {
            "n": idle.get("n"),
            "key_present": idle.get("key_present"),
            "full_turn": idle.get("full_turn"),
            "declared_non_null": idle.get("declared_non_null"),
            "cost_cny": idle.get("cost_cny"),
        },
        "mechanical_abuse": {
            "idle_declared_non_null": idle.get("declared_non_null"),
            "note": "non-null declared_display on idle chat would be mechanical filling",
        },
        "before_vs_after_keys": {
            "before": specimen.get("before_keys"),
            "after": specimen.get("after_keys"),
            "added": specimen.get("added_keys"),
        },
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=PRODUCTION_DB)
    parser.add_argument(
        "--family",
        choices=("all", "photo", "idle", "photo_candidate"),
        default="all",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    clone = OUTPUT / "production-clone.sqlite"
    if not clone.is_file() or args.family == "all":
        clone_ledger(args.source, clone)
    recent = recent_messages(clone)
    specimen = specimen_snapshot()
    system, tools, tool_choice = compact_prompt_and_tools()
    marks = _prompt_marks(system)
    (OUTPUT / "compact-system-excerpt.txt").write_text(system[-2500:], encoding="utf-8")
    existing: dict[str, Any] = {}
    report_path = OUTPUT / "report.json"
    if report_path.is_file() and args.family != "all":
        existing = json.loads(report_path.read_text(encoding="utf-8"))
    photo = existing.get("photo") or {}
    idle = existing.get("idle") or {}
    photo_candidate = existing.get("photo_candidate") or {}
    if args.family in {"all", "photo"}:
        photo = await run_family(
            family="photo",
            scenarios=PHOTO_SCENARIOS,
            recent=recent,
            system=system,
            tools=tools,
            tool_choice=tool_choice,
        )
    if args.family in {"all", "idle"}:
        idle = await run_family(
            family="idle",
            scenarios=IDLE_SCENARIOS,
            recent=recent,
            system=system,
            tools=tools,
            tool_choice=tool_choice,
        )
    if args.family in {"all", "photo_candidate"}:
        photo_candidate = await run_family(
            family="photo_candidate",
            scenarios=PHOTO_CANDIDATE_SCENARIOS,
            recent=recent,
            system=system,
            tools=tools,
            tool_choice=tool_choice,
        )
    for family in (photo, idle, photo_candidate):
        if family:
            family["cost_cny"] = family_cost_cny(family)
    summary = summarize(photo, idle, specimen, marks)
    summary["photo_candidate"] = {
        "n": photo_candidate.get("n"),
        "key_present": photo_candidate.get("key_present"),
        "full_turn": photo_candidate.get("full_turn"),
        "full_turn_key_present": photo_candidate.get("full_turn_key_present"),
        "declared_non_null": photo_candidate.get("declared_non_null"),
        "cost_cny": photo_candidate.get("cost_cny"),
    }
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "clone": str(clone),
        "model": "deepseek-v4-flash",
        "recent_messages": recent,
        "specimen": specimen,
        "prompt_marks": marks,
        "photo": photo,
        "idle": idle,
        "photo_candidate": photo_candidate,
        "summary": summary,
        "cost_cny": round(
            float(photo.get("cost_cny") or 0)
            + float(idle.get("cost_cny") or 0)
            + float(photo_candidate.get("cost_cny") or 0),
            4,
        ),
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    _LOG.info("wrote %s cost_cny=%s", report_path, report["cost_cny"])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
