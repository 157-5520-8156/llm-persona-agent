"""Paired, repeated reading benchmark; identical verified evidence, isolated budgets.

This tests evidence reading, not retrieval or a production CharacterInterior day.
Provider-labelled results and blind answer packets are separate artifacts.
"""

import argparse
import asyncio
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import random

from jsonschema import Draft202012Validator

from companion_daemon.config import Settings
from companion_daemon.llm import DeepSeekChatModel, model_call_scope, model_turn_scope
from companion_daemon.subscription_usage import SubscriptionAnthropicModel, SubscriptionTokenLedger
from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore
from trial_anthropic_proxy import digest, save


CASE_IDS = (
    "Q01 Q03 Q04 Q05 Q09 Q11 Q13 Q15 Q16 Q17 Q19 Q21 Q22 Q23 Q25 Q26 "
    "Q27 Q28 Q29 Q30 Q31 Q32 Q33 Q34 Q35 Q36 Q37 Q38 Q39 Q40"
).split()
SYSTEM = (
    "你在做一项固定来源阅读测试。请以资料中沈知栀的第一人称，用自然中文回答问题。"
    "只依赖给出的资料；不捏造人物、动作、原因、时间或共同经历，不把计划当完成。"
    "资料不足时明确保留不确定性，不为了顺着问题补写经历。"
    "可以合理概括、串联明确的时间变化，但推断不能冒充原文事实。"
    "回答通常控制在280个汉字以内，复杂问题以覆盖要点为优先。"
    "用指定结构返回answer和实际使用的source_ids；仅返回结果，不展示思考过程。"
)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def packet(case, records, query_at):
    rng = random.Random("reading-evidence.1:" + case["id"])
    gold = set(case["gold_source_ids"])
    distractors = rng.sample(sorted(set(records) - gold), 2 if gold else 3)
    selected = sorted(gold) + distractors
    rng.shuffle(selected)
    aliases = {f"S{i + 1}": ref for i, ref in enumerate(selected)}
    evidence = [
        {
            "source_id": alias,
            "statement": records[ref]["statement"],
            "occurred_from": records[ref]["occurred_from"],
            "occurred_until": records[ref]["occurred_until"],
            "time_precision": records[ref]["time_precision"],
        }
        for alias, ref in aliases.items()
    ]
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "answer": {"type": "string"},
            "source_ids": {
                "type": "array",
                "items": {"type": "string", "enum": list(aliases)},
            },
        },
        "required": ["answer", "source_ids"],
    }
    messages = [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": canonical(
                {"query_at": query_at, "question": case["question"], "available_sources": evidence}
            ),
        },
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "answer_from_sources",
                "description": "Answer using supplied sources",
                "strict": True,
                "parameters": schema,
            },
        }
    ]
    choice = {"type": "function", "function": {"name": "answer_from_sources"}}
    return messages, tools, choice, aliases


async def run(args):
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    cases_doc = json.loads((args.fixture / "cases.json").read_text())
    sources_doc = json.loads((args.fixture / "sources.json").read_text())
    cases = {c["id"]: c for c in cases_doc["cases"]}
    records = {r["record_id"]: r for r in sources_doc["records"]}
    assert len(CASE_IDS) == 30 and all(
        set(cases[c]["gold_source_ids"]) <= records.keys() for c in CASE_IDS
    )
    packets = {c: packet(cases[c], records, sources_doc["query_at"]) for c in CASE_IDS}
    jobs = [
        (c, repeat, provider)
        for c in CASE_IDS
        for repeat in range(3)
        for provider in ("deepseek", "proxy")
    ]
    random.Random(20261013).shuffle(jobs)
    protocol = {
        "contract": "fixed-source-reading-paired.1",
        "case_ids": CASE_IDS,
        "categories": dict(Counter(cases[c]["category"] for c in CASE_IDS)),
        "repeats": 3,
        "primary_answers": 180,
        "max_requests": 360,
        "parallelism": 3,
        "deepseek_budget_cny": 5.0,
        "proxy_token_budget": 10_000_000,
        "source_sha256": digest(args.fixture / "sources.json"),
        "cases_sha256": digest(args.fixture / "cases.json"),
        "source_fixture": str(args.fixture.resolve()),
        "blind_review_before_unblinding": True,
        "scoring": "Separate technical failure, unsupported/contradicted facts, coverage, and valid uncertainty. "
        "Citation IDs alone never prove support. Judge by actual supplied text; no keyword correctness proxy.",
        "scope": "reading with adequate evidence, not retrieval/persona/story/production qualification",
        "packets": {
            c: {
                "logical_request_sha256": hashlib.sha256(canonical(p[:3]).encode()).hexdigest(),
                "source_aliases": p[3],
            }
            for c, p in packets.items()
        },
        "order": jobs,
        "code_sha256": digest(Path(__file__)),
    }
    config = Settings(_env_file=tuple(args.env_file))
    protocol["models"] = {
        "deepseek": {"model": config.deepseek_model, "thinking": False, "max_output": 2048},
        "proxy": {
            "model": "gemini-3.8-flash-medium",
            "max_output": 64000,
            "carrier": "native_or_json_object",
        },
    }
    save(root / "protocol.json", protocol)
    if not args.live:
        print(json.dumps({"frozen": True, "cases": len(CASE_IDS), "primary_answers": len(jobs)}))
        return
    if not config.deepseek_debug_api_key:
        raise ValueError("DEEPSEEK_DEBUG_API_KEY required")
    os.environ["COMPANION_DISABLE_DEBUG_USAGE_LEDGER"] = "1"
    money = WorldV2UsageStore(
        path=str(root / "deepseek.sqlite"),
        monthly_budget_cny=5.0,
        daily_budget_cny=5.0,
        soft_daily_budget_cny=5.0,
    )
    tokens = SubscriptionTokenLedger(
        root / "proxy.sqlite", five_hour_tokens=10_000_000, weekly_tokens=10_000_000
    )
    models = {
        "deepseek": DeepSeekChatModel(
            config.deepseek_debug_api_key,
            config.deepseek_base_url,
            config.deepseek_model,
            thinking_enabled=False,
            max_completion_tokens=2048,
            usage_observer=money.record,
        ),
        "proxy": SubscriptionAnthropicModel(
            "local-proxy",
            "http://localhost:8788",
            "gemini-3.8-flash-medium",
            token_ledger=tokens,
            timeout_seconds=180,
            max_completion_tokens=64000,
            tool_result_carrier="native_or_json_object",
        ),
    }
    rows = []
    requests = []
    semaphore = asyncio.Semaphore(3)
    completed = False

    async def capture(request):
        if len(requests) >= 360:
            raise ValueError("frozen request ceiling reached")
        body = json.loads(request.content)
        requests.append(
            {
                "model": body["model"],
                "path": request.url.path,
                "bytes": len(request.content),
                "request_sha256": hashlib.sha256(request.content).hexdigest(),
            }
        )

    for model in models.values():
        model.client.event_hooks["request"].append(capture)

    async def work(case_id, repeat, provider):
        async with semaphore:
            messages, tools, choice, aliases = packets[case_id]
            row = {
                "case_id": case_id,
                "repeat": repeat,
                "provider": provider,
                "status": "running",
                "attempts": [],
            }
            rows.append(row)
            raw = ""
            failure = ""
            for attempt in range(2):
                current = (
                    messages
                    if attempt == 0
                    else [
                        *messages,
                        {"role": "assistant", "content": raw},
                        {
                            "role": "user",
                            "content": "结果未通过同一schema校验，请仅纠正结构，不添加资料外事实。错误："
                            + failure[:500],
                        },
                    ]
                )
                try:
                    with (
                        model_turn_scope(
                            world_id="fixed-reading", turn_id=f"{case_id}:{repeat}:{provider}"
                        ),
                        model_call_scope(
                            "provider_reading_benchmark", actor=case_id, attempt=attempt + 1
                        ),
                    ):
                        raw = await models[provider].complete_json(
                            current, temperature=0.8, tools=tools, tool_choice=choice
                        )
                    value = json.loads(raw)
                    Draft202012Validator(tools[0]["function"]["parameters"]).validate(value)
                    if not value["answer"].strip() or len(set(value["source_ids"])) != len(
                        value["source_ids"]
                    ):
                        raise ValueError("answer must be nonempty and source_ids unique")
                    row.update(
                        status="complete", answer=value["answer"], source_ids=value["source_ids"]
                    )
                    row["attempts"].append(
                        {
                            "status": "schema_valid",
                            "response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                        }
                    )
                    break
                except Exception as exc:
                    failure = str(exc)
                    row["attempts"].append(
                        {
                            "status": "failed",
                            "error_type": type(exc).__name__,
                            "error": failure[:300],
                        }
                    )
                    row["status"] = "failed"
                    # Only a received invalid object is offered one schema correction.
                    if not raw:
                        break
            save(
                root / "progress.json",
                {
                    "completed_answers": sum(r["status"] != "running" for r in rows),
                    "requests": len(requests),
                    "statuses": dict(Counter(r["status"] for r in rows)),
                },
            )
            save(root / "results.json", {"completed": False, "rows": rows})
            print(
                json.dumps(
                    {k: row[k] for k in ("case_id", "repeat", "provider", "status")},
                    ensure_ascii=False,
                ),
                flush=True,
            )

    try:
        await asyncio.gather(*(work(*job) for job in jobs))
        completed = True
    finally:
        await asyncio.gather(*(m.aclose() for m in models.values()))
        save(
            root / "results.json",
            {
                "completed": completed,
                "rows": rows,
                "requests": requests,
                "proxy_usage": tokens.snapshot(),
            },
        )
    if completed:
        blind = []
        mapping = {}
        for case_id in CASE_IDS:
            answers = [r for r in rows if r["case_id"] == case_id]
            random.Random("blind.1:" + case_id).shuffle(answers)
            bundle = {"case_id": case_id, "source_aliases": packets[case_id][3], "answers": []}
            for i, row in enumerate(answers):
                label = chr(65 + i)
                key = case_id + ":" + label
                bundle["answers"].append(
                    {
                        "label": label,
                        "status": row["status"],
                        "answer": row.get("answer"),
                        "source_ids": row.get("source_ids"),
                    }
                )
                mapping[key] = {"provider": row["provider"], "repeat": row["repeat"]}
            blind.append(bundle)
        save(root / "blind.json", blind)
        save(root / "unblind.json", mapping)
        print(
            json.dumps(
                {
                    "completed": True,
                    "answers": len(rows),
                    "requests": len(requests),
                    "known_proxy_tokens": tokens.snapshot()["known_reported_tokens"],
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, action="append", default=[])
    parser.add_argument("--live", action="store_true")
    asyncio.run(run(parser.parse_args()))
