#!/usr/bin/env python3
"""Request one real V4.1 semantic review for the isolated long-record fixture."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/private-audits/luna-memory-integration-20260929"
SOURCE_DB = ROOT / "output/private-audits/grounding-repair-20260929/source.sqlite"
FIXTURE = ROOT / "fixtures/world_v2/synthetic_luna_long_prehistory_recall_test.json"
WORLD = "world:companion-v2:qq-c2c:geoff"
ACTOR = "agent:companion"


def write_private(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    path.chmod(0o600)


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True, mode=0o700)
    OUT.chmod(0o700)
    from companion_daemon.world_v2.character_prehistory import (
        PrehistoryArchiveDocument,
        PrehistoryRecord,
    )
    from companion_daemon.world_v2.prehistory_authoring import (
        PrehistorySemanticReview,
        bind_semantic_review,
        package_reviewed,
        semantic_review_request,
        validate_draft,
    )
    from prepare_character_prehistory import brief_from_database
    from companion_daemon.config import Settings
    from companion_daemon.llm import DeepSeekChatModel, model_call_scope

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    brief = brief_from_database(
        database=SOURCE_DB,
        profile_path=ROOT / "configs/character.yaml",
        world_id=WORLD,
        actor_ref=ACTOR,
        born_at=datetime(2005, 4, 12, tzinfo=UTC),
        author_ref="operator:luna-synthetic-fixture-author",
        source_ref=str(FIXTURE.relative_to(ROOT)),
    )
    start = datetime.fromisoformat(fixture["occurred_from"])
    end = datetime.fromisoformat(fixture["occurred_until"])
    document = PrehistoryArchiveDocument(
        contract="character-prehistory-archive.1",
        archive_id="prehistory-archive:luna-window-recall-20260929",
        world_id=WORLD,
        actor_ref=ACTOR,
        source_artifact_ref=brief.draft_source_ref,
        entities=tuple(fixture["entities"]),
        records=(PrehistoryRecord(
            record_id=fixture["record_id"],
            occurred_from=start,
            occurred_until=end,
            time_precision="interval",
            timezone_name="Asia/Shanghai",
            statement=fixture["statement"],
            participant_refs=("history:person:window-test-classmate",),
            location_ref="history:place:window-test-school-room",
            related_record_refs=(),
            privacy_class="personal",
        ),),
    )
    validate_draft(brief, document)
    request = semantic_review_request(brief, document)
    write_private(OUT / "long-record-brief.json", brief.model_dump(mode="json"))
    write_private(OUT / "long-record-draft.json", document.model_dump(mode="json"))
    write_private(OUT / "long-record-review-request.json", request)

    env = __import__("dotenv").dotenv_values(ROOT / ".env")
    key = env.get("DEEPSEEK_DEBUG_API_KEY")
    if not key:
        raise SystemExit("DEEPSEEK_DEBUG_API_KEY is missing")
    settings = Settings(_env_file=ROOT / ".env")
    bills = []
    model = DeepSeekChatModel(
        api_key=key,
        base_url=settings.deepseek_base_url,
        model="deepseek-flash",
        thinking_enabled=False,
        max_completion_tokens=4096,
        usage_observer=bills.append,
    )
    started = datetime.now(UTC)
    messages = [
        {"role": "system", "content": request["instruction"] + "\n审阅范围说明：source_artifact_ref明确标为isolated synthetic fixture；这是只在同World完整克隆中使用的检索机制夹具，不是生产自传或已核实现实往事。新故事本来就不会出现在profile/accepted_archives中，不能只因其新创或未在profile列出而判为矛盾。仍须严格拒绝与出生/配置年表/已接受历史不一致、把测试者或真实用户写成共同历史参与者、将他人动机伪装为事实，或时间/行动结果内部矛盾的材料；此说明不豁免任何具体事实冲突。"},
        {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
    ]
    with model_call_scope("prehistory-independent-creation-review"):
        response, usage = await model.complete_json_with_usage(messages, temperature=0)
    reviewed_at = datetime.now(UTC)
    raw_response = response
    write_private(OUT / "long-record-review-response.json", {
        "validation_status": "provider_response_received_unparsed",
        "response": raw_response,
        "response_sha256": hashlib.sha256(raw_response.encode()).hexdigest(),
        "request_sha256": hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "started_at": started.isoformat(),
        "finished_at": reviewed_at.isoformat(),
        "model": "deepseek-flash (V4.1)",
        "usage": usage,
        "billing": [__import__("dataclasses").asdict(item) for item in bills],
    })
    parsed = PrehistorySemanticReview.model_validate_json(response)
    bound = bind_semantic_review(
        request,
        parsed,
        reviewer_ref="model:deepseek-flash:independent-prehistory-review",
        reviewed_at=reviewed_at,
    )
    write_private(OUT / "long-record-review-response.json", {
        "validation_status": "parsed_and_bound",
        "response": raw_response,
        "response_sha256": hashlib.sha256(raw_response.encode()).hexdigest(),
        "request_sha256": hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "started_at": started.isoformat(),
        "finished_at": reviewed_at.isoformat(),
        "model": "deepseek-flash (V4.1)",
        "usage": usage,
        "billing": [__import__("dataclasses").asdict(item) for item in bills],
    })
    if bound.decision != "approved":
        write_private(OUT / "long-record-review-bound.json", bound.model_dump(mode="json"))
        print(json.dumps({"review": bound.decision, "findings": bound.cross_record_findings,
                          "usage": usage}, ensure_ascii=False))
        await model.client.aclose()
        return
    reviewed = package_reviewed(
        brief, document, bound,
        review_artifact_ref="output/private-audits/luna-memory-integration-20260929/long-record-review-response.json",
    )
    write_private(OUT / "long-record-reviewed.json", reviewed.model_dump(mode="json"))
    await model.client.aclose()
    print(json.dumps({"review": bound.decision, "records": len(bound.records),
                      "reviewed_package_sha256": hashlib.sha256((OUT / "long-record-reviewed.json").read_bytes()).hexdigest(),
                      "usage": usage}, ensure_ascii=False))


if __name__ == "__main__":
    os.chdir(ROOT)
    asyncio.run(main())
