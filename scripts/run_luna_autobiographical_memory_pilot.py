#!/usr/bin/env python3
"""Run a bounded real-V4.1 prompt pilot over explicitly synthetic memories.

This is a direct CharacterInterior-style prompt probe. It does not import or
write a reviewed archive, create World events, or modify production data.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import time

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures/world_v2/synthetic_luna_autobiographical_memory_pilot.json"
PRIVATE = ROOT / "output/private-audits/luna-memory-pilot-20260929"


def flat_memories(data: dict[str, object]) -> str:
    memories = data["memories"]
    lines = []
    for row in memories:
        lines.append(
            f"{row['period']}：{row['event']} 当时：{row['then_understanding']} "
            f"后来：{row.get('later_understanding', '没有特别的后来理解。')}"
            + (f" 边界：{row['detail_boundary']}" if row.get("detail_boundary") else "")
        )
    return "\n".join(lines)


def layered_memories(data: dict[str, object]) -> str:
    by_id = {item["id"]: item["label"] for item in data["periods"]}
    chunks = []
    for row in data["memories"]:
        chunks.append(
            "\n".join(
                (
                    f"记忆 {row['id']}｜{by_id[row['period']]}｜{row['depth']}｜{row['kind']}",
                    f"发生的事：{row['event']}",
                    f"当时的理解：{row['then_understanding']}",
                    f"后来的理解：{row.get('later_understanding', '没有特别的后来理解。')}",
                    *( [f"未记录的细节：{row['detail_boundary']}"] if row.get("detail_boundary") else [] ),
                )
            )
        )
    return "\n\n".join(chunks)


SYSTEM = """你是林澄本人，正在和熟悉的朋友聊天。你可以自行决定动机、态度、情绪、措辞、节奏、是否提起往事、是否追问、沉默或采取什么行动。实验不要求你焦虑、追问、拒绝、接受或解释心理。
输入的自传材料是虚构实验素材，只在本轮合成角色中成立。发生的事、当时的理解、后来的理解是不同层次：角色的旧看法不是他人动机事实，后来的理解也不改写原事。没有写出的具体事件、人物、原话、感官、行动和结果均未知；不要补成已发生的故事。普通中文的省略、概括和近似表达可以自然使用。
请只输出一个 JSON 对象，字段为：memory_ids_used（字符串数组，若没有则空数组）、memory_was_salient（布尔值）、memory_was_adopted_in_current_interpretation（布尔值）、current_appraisal（简短字符串，可为空）、current_emotion（简短字符串，可为空）、chosen_response_or_action（简短字符串）、visible_reply（自然聊天回复）。审计字段只用于本地研究，不要把字段解释写进 visible_reply。实际决定始终由你自己作出。"""


def messages(data: dict[str, object], case: dict[str, object], condition: str) -> list[dict[str, str]]:
    context = {"none": "本轮没有可检索的过往记忆材料。未知的个人经历不要猜。",
               "flat": "以下是可用的自传记忆，按扁平叙述排列；其事实内容与另一表示条件相同。\n" + flat_memories(data),
               "layered": "以下是可用的自传记忆，按人生时期及事实/当时理解/后来理解分开排列；其事实内容与另一表示条件相同。\n" + layered_memories(data)}[condition]
    task = case.get("question", case.get("prompt"))
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"{context}\n\n当前对话：{task}"},
    ]


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-private-output", action="store_true", help="required acknowledgement that requests and responses will be stored locally with mode 600")
    args = parser.parse_args()
    if not args.confirm_private_output:
        raise SystemExit("pass --confirm-private-output to write private model requests/responses")
    from companion_daemon.config import Settings
    from companion_daemon.llm import DeepSeekChatModel

    settings = Settings(_env_file=Path(".env"))
    if not settings.deepseek_debug_api_key:
        raise SystemExit("DEEPSEEK_DEBUG_API_KEY is not configured in .env")
    PRIVATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    PRIVATE.chmod(0o700)
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    model = DeepSeekChatModel(
        api_key=settings.deepseek_debug_api_key,
        base_url=settings.deepseek_base_url,
        model="deepseek-flash",
        thinking_enabled=False,
        max_completion_tokens=1200,
    )
    cases = [
        {"id": "recall:school-wait", "question": "初中有一次放学后等同学的事吗？后来你现在怎么理解？", "conditions": ["flat", "layered"]},
        {"id": "recall:club-display", "question": "社团布展有一次不太顺的事吗？后来怎么样？当时和现在分别怎么想？", "conditions": ["flat", "layered"]},
        {"id": "choice:ambiguous-plan", "prompt": data["choice_probes"][0]["prompt"], "conditions": ["none", "flat", "layered"]},
        {"id": "choice:gentle-correction", "prompt": data["choice_probes"][1]["prompt"], "conditions": ["none", "flat", "layered"]},
    ]
    rows: list[dict[str, object]] = []
    manifest = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "model": "deepseek-flash (official V4.1 route)",
        "provider": "DeepSeek API",
        "thinking_enabled": False,
        "temperature": 0.7,
        "fixture_sha256": __import__("hashlib").sha256(FIXTURE.read_bytes()).hexdigest(),
        "scope": "direct prompt experiment; no World wiring or delivery",
        "attempted_calls": 0,
    }
    (PRIVATE / "run.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for case in cases:
        for condition in case["conditions"]:
            req = messages(data, case, condition)
            started = time.monotonic()
            manifest["attempted_calls"] += 1
            row: dict[str, object] = {"case_id": case["id"], "condition": condition,
                                      "request": req, "started_at": datetime.now(timezone.utc).isoformat()}
            try:
                response, usage = await model.complete_with_usage(req, temperature=0.7)
                row.update({"status": "succeeded", "response": response, "usage": usage,
                            "latency_seconds": round(time.monotonic() - started, 3)})
            except Exception as exc:
                # Keep technical failure separate from a character choosing silence.
                row.update({"status": "technical_failure", "error_type": type(exc).__name__,
                            "error": str(exc), "billing_state": "unknown",
                            "latency_seconds": round(time.monotonic() - started, 3)})
            rows.append(row)
            target = PRIVATE / "calls.json"
            target.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
            target.chmod(0o600)
            manifest["completed_calls"] = len(rows)
            manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
            manifest["succeeded_calls"] = sum(item["status"] == "succeeded" for item in rows)
            manifest["technical_failures"] = sum(item["status"] != "succeeded" for item in rows)
            (PRIVATE / "run.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            (PRIVATE / "run.json").chmod(0o600)
            print(json.dumps({"case_id": row["case_id"], "condition": condition, "status": row["status"],
                              "latency_seconds": row["latency_seconds"], "usage": row.get("usage"),
                              "error_type": row.get("error_type")}, ensure_ascii=False), flush=True)
    await model.client.aclose()
    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
    (PRIVATE / "run.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (PRIVATE / "run.json").chmod(0o600)
    return 0


if __name__ == "__main__":
    os.chdir(ROOT)
    raise SystemExit(asyncio.run(main()))
