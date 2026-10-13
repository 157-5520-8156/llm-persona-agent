"""Serial recovery of missing proxy answers, retaining the original failed run."""

import argparse
import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path
import random

from jsonschema import Draft202012Validator

from companion_daemon.llm import model_call_scope, model_turn_scope
from companion_daemon.subscription_usage import SubscriptionAnthropicModel, SubscriptionTokenLedger
from compare_fixed_source_reading import CASE_IDS, canonical, packet
from trial_anthropic_proxy import digest, save


async def run(root):
    protocol = json.loads((root / "protocol.json").read_text())
    baseline = json.loads((root / "results.json").read_text())
    fixture = Path(protocol["source_fixture"])
    assert digest(fixture / "cases.json") == protocol["cases_sha256"]
    assert digest(fixture / "sources.json") == protocol["source_sha256"]
    cases = {c["id"]: c for c in json.loads((fixture / "cases.json").read_text())["cases"]}
    sources = json.loads((fixture / "sources.json").read_text())
    records = {r["record_id"]: r for r in sources["records"]}
    pending = [
        r for r in baseline["rows"] if r["provider"] == "proxy" and r["status"] != "complete"
    ]
    packets = {c: packet(cases[c], records, sources["query_at"]) for c in CASE_IDS}
    for c, p in packets.items():
        assert (
            hashlib.sha256(canonical(p[:3]).encode()).hexdigest()
            == protocol["packets"][c]["logical_request_sha256"]
        )
    save(
        root / "serial-protocol.json",
        {
            "original_protocol_sha256": digest(root / "protocol.json"),
            "parallelism": 1,
            "scope": "recover missing proxy answers only",
            "pending_answers": len(pending),
            "stop_after_consecutive_failures": 2,
            "same_request_and_source_hashes": True,
            "shared_token_ledger": True,
            "original_failures_retained": True,
            "no_new_deepseek_calls": True,
            "code_sha256": digest(Path(__file__)),
        },
    )
    ledger = SubscriptionTokenLedger(
        root / "proxy.sqlite", five_hour_tokens=10_000_000, weekly_tokens=10_000_000
    )
    model = SubscriptionAnthropicModel(
        "local-proxy",
        "http://localhost:8788",
        "gemini-3.8-flash-medium",
        token_ledger=ledger,
        timeout_seconds=180,
        max_completion_tokens=64000,
        tool_result_carrier="native_or_json_object",
    )
    rows = []
    requests = []
    consecutive = 0

    async def capture(request):
        if len(baseline["requests"]) + len(requests) >= 360:
            raise ValueError("original request ceiling reached")
        body = json.loads(request.content)
        requests.append(
            {
                "model": body["model"],
                "path": request.url.path,
                "bytes": len(request.content),
                "request_sha256": hashlib.sha256(request.content).hexdigest(),
            }
        )

    model.client.event_hooks["request"].append(capture)
    try:
        for original in pending:
            case_id, repeat = original["case_id"], original["repeat"]
            messages, tools, choice, _ = packets[case_id]
            row = {
                "case_id": case_id,
                "repeat": repeat,
                "provider": "proxy",
                "status": "running",
                "attempts": [],
                "prior_attempts": original["attempts"],
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
                            world_id="fixed-reading", turn_id=f"{case_id}:{repeat}:proxy:recovery"
                        ),
                        model_call_scope(
                            "provider_reading_benchmark", actor=case_id, attempt=attempt + 1
                        ),
                    ):
                        raw = await model.complete_json(
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
                    row.update(status="failed")
                    row["attempts"].append(
                        {
                            "status": "failed",
                            "error_type": type(exc).__name__,
                            "error": failure[:300],
                        }
                    )
                    if not raw:
                        break
            consecutive = 0 if row["status"] == "complete" else consecutive + 1
            save(
                root / "serial-progress.json",
                {"rows": rows, "requests": requests, "tokens": ledger.snapshot()},
            )
            print(
                json.dumps(
                    {
                        "case_id": case_id,
                        "repeat": repeat,
                        "status": row["status"],
                        "answers_recovered": sum(r["status"] == "complete" for r in rows),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if consecutive >= 2:
                break
            await asyncio.sleep(1)
    finally:
        await model.aclose()
        recovered = {(r["case_id"], r["repeat"], r["provider"]): r for r in rows}
        merged = [
            recovered.get((r["case_id"], r["repeat"], r["provider"]), r) for r in baseline["rows"]
        ]
        save(
            root / "results-recovered.json",
            {
                "completed": all(r["status"] == "complete" for r in merged),
                "rows": merged,
                "requests": baseline["requests"] + requests,
                "proxy_usage": ledger.snapshot(),
                "baseline_retained": "results.json",
                "serial_stop": "consecutive_failures" if consecutive >= 2 else "finished",
            },
        )
        blind = []
        mapping = {}
        for case_id in CASE_IDS:
            answers = [r for r in merged if r["case_id"] == case_id]
            random.Random("blind.1:" + case_id).shuffle(answers)
            bundle = {"case_id": case_id, "source_aliases": packets[case_id][3], "answers": []}
            for i, row in enumerate(answers):
                label = chr(65 + i)
                bundle["answers"].append(
                    {
                        "label": label,
                        "status": row["status"],
                        "answer": row.get("answer"),
                        "source_ids": row.get("source_ids"),
                    }
                )
                mapping[case_id + ":" + label] = {
                    "provider": row["provider"],
                    "repeat": row["repeat"],
                }
            blind.append(bundle)
        save(root / "blind-recovered.json", blind)
        save(root / "unblind-recovered.json", mapping)
        print(
            json.dumps(
                {
                    "statuses": dict(Counter(r["status"] for r in merged)),
                    "new_requests": len(requests),
                    "stop_after_consecutive_failures": consecutive >= 2,
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    if (args.root / "results-recovered.json").exists():
        parser.error("recovery result already exists")
    asyncio.run(run(args.root))
