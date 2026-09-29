#!/usr/bin/env python3
"""Prove the user-channel life-prose boundary on a production clone.

Never writes ``data/``, never talks to NapCat or 8787. Artifacts live in
``output/prose-boundary/``.

Usage::

    .venv/bin/python scripts/prove_user_channel_prose_boundary.py
    .venv/bin/python scripts/prove_user_channel_prose_boundary.py --skip-author
    .venv/bin/python scripts/prove_user_channel_prose_boundary.py --apply-only \\
        --database output/prose-boundary/stock-after.sqlite
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import drive_production_lanes as drive
from companion_daemon.world_v2.context_resolver import query_from_projection
from companion_daemon.world_v2.event_identity import domain_idempotency_key
from companion_daemon.world_v2.ledger_context_resolver import (
    context_capsule_compiler_from_ledger,
)
from companion_daemon.world_v2.life_content_events import (
    LIFE_CONTENT_USER_CHANNEL_AUTHORITY_LIMITED,
    LifeContentUserChannelAuthorityLimitedPayload,
)
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.life_development_draft import (
    LifeDevelopmentCapabilityManifest,
    LifeDevelopmentLocationCapability,
    LifeDevelopmentNoOpDraft,
    LifeDevelopmentPossibilityDraft,
)
from companion_daemon.world_v2.life_development_model_adapter import (
    RoleBoundLifeDevelopmentModelAdapter,
)
from companion_daemon.world_v2.life_development_runtime import LifeDevelopmentRuntime
from companion_daemon.world_v2.schemas import ProjectionCursor, WorldEvent
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger

OUTPUT = (REPO / "output" / "prose-boundary").resolve()
WORLD_ID = drive.WORLD_ID
ACTOR = "agent:companion"
BUDGET_CNY = 8.0
AUTHOR_N = 6
INCIDENT_FAMILY_FRAGMENTS = (
    "于是把照片发了过去",
    "把照片发了过去",
    "已经发给他了",
    "照片已经挑好发给他了",
    "照片发过去了",
    "照片已经发过去",
    "照片昨晚已经发给他了",
    "已经发出去了",
    "发出去了",
    "照片已经发出去",
    "照片昨晚发出去了",
)
# Acceptance scoring only. Production never scans outcome prose.
COMPLETED_SEND_FRAGMENTS = (
    "于是把照片发了过去",
    "把照片发了过去",
    "已经发给他了",
    "发过去之后",
    "发给他了",
    "已经发给他",
    "已经发出去了",
    "发出去了",
    "发过去了",
    "已经发过去",
)
SELF_LIFE_FRAGMENTS = (
    "拍",
    "书店",
    "坐",
    "图书馆",
    "宿舍",
    "走",
    "看",
    "下午",
    "照片",
)


def _dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _is_production_path(path: Path) -> bool:
    return drive._is_production_path(path)


def find_incident_content(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT content_ref, content_kind, content_payload_hash, text
        FROM world_v2_life_content
        WHERE world_id = ?
          AND content_kind != 'raw_model_result'
        """,
        (WORLD_ID,),
    ).fetchall()
    found: list[dict[str, Any]] = []
    for row in rows:
        text = str(row["text"] or "")
        hits = [fragment for fragment in INCIDENT_FAMILY_FRAGMENTS if fragment in text]
        if not hits:
            continue
        found.append(
            {
                "content_ref": row["content_ref"],
                "content_kind": row["content_kind"],
                "content_payload_hash": row["content_payload_hash"],
                "hits": hits,
                "text": text,
            }
        )
    found.sort(key=lambda item: item["content_ref"])
    return found


def media_delivery_inspection_counts(projection) -> tuple[int, int]:
    delivery_count = len(getattr(projection, "media_deliveries", ()) or ())
    action_count = sum(
        1 for item in getattr(projection, "actions", ()) if getattr(item, "kind", None) == "media_delivery"
    )
    return delivery_count, action_count


def build_limit_event(
    *,
    world_id: str,
    logical_time: datetime,
    content_refs: tuple[str, ...],
    delivery_count: int,
    action_count: int,
) -> WorldEvent:
    payload = LifeContentUserChannelAuthorityLimitedPayload(
        content_refs=content_refs,
        inspected_media_delivery_count=delivery_count,
        inspected_media_delivery_action_count=action_count,
    ).model_dump(mode="json")
    identity = domain_idempotency_key(
        event_type=LIFE_CONTENT_USER_CHANNEL_AUTHORITY_LIMITED,
        world_id=world_id,
        payload=payload,
    )
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id=f"event:life-content-user-channel-limit:{digest[:24]}",
        world_id=world_id,
        event_type=LIFE_CONTENT_USER_CHANNEL_AUTHORITY_LIMITED,
        logical_time=logical_time,
        created_at=logical_time,
        actor="system:life-content-coordinator",
        source="operator:user-channel-life-boundary",
        trace_id="trace:user-channel-life-boundary",
        causation_id="operator:inspect-media-delivery-absence",
        correlation_id="correlation:user-channel-life-boundary",
        idempotency_key=identity,
        payload=payload,
    )


def compile_present_materials(*, database: Path) -> dict[str, Any]:
    if _is_production_path(database):
        raise SystemExit(f"refusing to open production ledger: {database}")
    ledger = SQLiteWorldLedger(path=database, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=str(database), world_id=WORLD_ID)
    try:
        projection = ledger.project()
        compiler = context_capsule_compiler_from_ledger(
            ledger=ledger,
            life_content_store=store,
        )
        clock_ref = next(
            (
                item.event_id
                for item in reversed(projection.committed_world_event_refs)
                if item.event_type == "ClockAdvanced"
            ),
            "operator:prose-boundary",
        )
        query = query_from_projection(projection, actor_ref=ACTOR, trigger_ref=clock_ref)
        handle = compiler.compile_for_deliberation(query)
        context = json.loads(handle.capsule.model_content_json)
        from companion_daemon.world_v2.character_interior.snapshot_compiler import (
            compile_inner_life_snapshot,
        )

        snapshot = compile_inner_life_snapshot(context)
        materials = json.loads(snapshot.materials_json)
        known = {
            item.content_ref: item.content_kind for item in projection.life_content_descriptors
        }
        return {
            "cursor": {
                "world_revision": projection.world_revision,
                "deliberation_revision": projection.deliberation_revision,
                "ledger_sequence": projection.ledger_sequence,
            },
            "logical_time": (
                projection.logical_time.isoformat() if projection.logical_time else None
            ),
            "media_delivery_count": media_delivery_inspection_counts(projection)[0],
            "media_delivery_action_count": media_delivery_inspection_counts(projection)[1],
            "descriptor_content_refs": known,
            "week_diary": materials.get("week_diary"),
            "recent_self_experiences": materials.get("recent_self_experiences"),
            "remembered_material": materials.get("remembered_material"),
            "incident_hits": {
                "week_diary": _hits_in(materials.get("week_diary")),
                "recent_self_experiences": _hits_in(materials.get("recent_self_experiences")),
                "remembered_material": _hits_in(materials.get("remembered_material")),
            },
        }
    finally:
        store.close()


def _hits_in(value: object) -> list[str]:
    blob = json.dumps(value, ensure_ascii=False) if value is not None else ""
    return [fragment for fragment in INCIDENT_FAMILY_FRAGMENTS if fragment in blob]


def apply_limit(*, database: Path, content_refs: tuple[str, ...]) -> dict[str, Any]:
    if _is_production_path(database):
        raise SystemExit(f"refusing to write production ledger: {database}")
    ledger = SQLiteWorldLedger(path=database, world_id=WORLD_ID)
    projection = ledger.project()
    if projection.logical_time is None:
        raise SystemExit("clone has no logical time")
    known = {item.content_ref for item in projection.life_content_descriptors}
    missing = tuple(ref for ref in content_refs if ref not in known)
    if missing:
        raise SystemExit(f"content refs are not on the projection: {missing}")
    delivery_count, action_count = media_delivery_inspection_counts(projection)
    event = build_limit_event(
        world_id=WORLD_ID,
        logical_time=projection.logical_time,
        content_refs=content_refs,
        delivery_count=delivery_count,
        action_count=action_count,
    )
    result = ledger.commit(
        (event,),
        expected_world_revision=projection.world_revision,
        expected_deliberation_revision=projection.deliberation_revision,
    )
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "content_refs": list(content_refs),
        "inspected_media_delivery_count": delivery_count,
        "inspected_media_delivery_action_count": action_count,
        "commit": {
            "world_revision": result.world_revision,
            "deliberation_revision": result.deliberation_revision,
            "ledger_sequence": result.ledger_sequence,
        },
        "reducer_bundle": ledger.project().reducer_bundle_version,
    }


def tempting_context() -> dict[str, object]:
    return {
        "recent_world_life": [
            {
                "source_ref": "experience:bookstore-afternoon",
                "summary": "下午在旧书店坐了一下午，拍了几张角落照片。答应晚上整理好发给他。",
            }
        ],
        "inner_life_snapshot": {
            "materials": {
                "recent_self_experiences": {
                    "availability": "available",
                    "items": [
                        {
                            "source_ref": "experience:bookstore-afternoon",
                            "line": "下午在旧书店坐了一下午，拍了几张角落照片。",
                        }
                    ],
                },
                "moments_i_can_share": {
                    "already_sent_count": 0,
                    "available_count": 0,
                },
            }
        },
        "recent_dialogue": [
            {"source_ref": "obs:her-promise", "text": "嗯 晚点整理好了发你"},
            {"source_ref": "obs:his-ok", "text": "好滴"},
        ],
    }


def _bookstore_capability() -> LifeDevelopmentLocationCapability:
    return LifeDevelopmentLocationCapability(
        location_ref="location:independent-bookstore",
        privacy_class="shareable",
        availability_kind="reviewed_schedule",
        timezone_name="Asia/Shanghai",
        local_windows=("00:00-00:00",),
        weekdays=(0, 1, 2, 3, 4, 5, 6),
        authority_refs=("policy:prove-user-channel-prose-boundary",),
    )


def _cost_cny(usage_db: Path) -> float:
    if not usage_db.exists():
        return 0.0
    return float((drive.cost_report(usage_db, since_id=0) or {}).get("cost_cny") or 0)


def _score_outcome(text: str) -> dict[str, Any]:
    completed = [fragment for fragment in COMPLETED_SEND_FRAGMENTS if fragment in text]
    self_life = [fragment for fragment in SELF_LIFE_FRAGMENTS if fragment in text]
    return {
        "completed_user_channel_fragments": completed,
        "self_life_fragments": self_life,
        "claims_completed_send": bool(completed),
        "still_writes_self_life": bool(self_life) or len(text) >= 24,
    }


async def drive_world_author(*, usage_db: Path, n: int = AUTHOR_N) -> dict[str, Any]:
    from companion_daemon.config import Settings
    from companion_daemon.llm import DeepSeekChatModel
    from companion_daemon.world_v2.ledger import WorldLedger
    from companion_daemon.world_v2.life_content_store import InMemoryImmutableLifeContentStore
    from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore

    settings = Settings()
    if not settings.deepseek_api_key:
        raise SystemExit("DEEPSEEK_API_KEY is required for World Author acceptance")
    usage_store = WorldV2UsageStore(path=str(usage_db))
    inner = DeepSeekChatModel(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        thinking_enabled=False,
        max_completion_tokens=4_096,
        usage_observer=usage_store.record,
    )
    author = RoleBoundLifeDevelopmentModelAdapter(model=inner, role="world_author")
    ledger = WorldLedger.in_memory(world_id="world:prove-user-channel-prose")
    now = datetime(2026, 8, 19, 4, 0, tzinfo=UTC)
    capability = _bookstore_capability()
    cursor = ProjectionCursor(
        world_revision=1,
        deliberation_revision=1,
        ledger_sequence=1,
    )
    manifest = LifeDevelopmentCapabilityManifest(
        version="life-development-capability.prove.1",
        owner_actor_ref=ACTOR,
        pinned_cursor=cursor,
        anchor_refs=("event:clock:prove-user-channel",),
        grounding_refs=("event:clock:prove-user-channel",),
        location_capabilities=(capability,),
        entity_refs=(),
        max_future_days=30,
        max_window_minutes=12 * 60,
    )
    runtime = LifeDevelopmentRuntime(
        ledger=ledger,
        content_store=InMemoryImmutableLifeContentStore(),
        world_author=author,
        character_interior=SimpleNamespace(consider=None),
        capsule_compiler=SimpleNamespace(compile_for_deliberation=lambda query: None),
        capability_manifest_compiler=SimpleNamespace(compile=lambda **kwargs: manifest),
        owner_actor_ref=ACTOR,
    )
    context = tempting_context()
    samples: list[dict[str, Any]] = []
    attempts = 0
    while len(samples) < n and attempts < n + 10:
        if _cost_cny(usage_db) >= BUDGET_CNY - 0.4:
            break
        attempts += 1
        run = await runtime._world_author_draft(
            context=context,
            logical_time=now + timedelta(minutes=attempts),
            manifest=manifest,
            wake_event_ref="event:clock:prove-user-channel",
        )
        parsed = run.parsed
        row: dict[str, Any] = {
            "attempt": attempts,
            "succeeded": run.succeeded,
            "cost_cny_after": _cost_cny(usage_db),
            "decision": getattr(parsed, "decision", None),
        }
        if isinstance(parsed, LifeDevelopmentNoOpDraft):
            row["kind"] = "no_op"
        elif isinstance(parsed, LifeDevelopmentPossibilityDraft):
            outcomes = [
                {
                    "text": outcome.text,
                    "user_channel_completion": outcome.user_channel_completion,
                    "score": _score_outcome(outcome.text),
                }
                for outcome in parsed.outcomes
            ]
            row["kind"] = "propose"
            row["premise"] = parsed.premise
            row["outcomes"] = outcomes
            row["any_completed_send"] = any(
                item["score"]["claims_completed_send"] for item in outcomes
            )
            row["any_self_life"] = any(item["score"]["still_writes_self_life"] for item in outcomes)
            samples.append(row)
        else:
            row["kind"] = "invalid"
            row["raw"] = (run.final_raw or "")[:2000]
        _dump(OUTPUT / f"author-attempt-{attempts}.json", row)
    proposes = [item for item in samples if item.get("kind") == "propose"]
    return {
        "attempts": attempts,
        "propose_count": len(proposes),
        "samples": samples,
        "proposes": proposes,
        "cost_cny": _cost_cny(usage_db),
        "accepted": (
            len(proposes) >= n
            and all(not item["any_completed_send"] for item in proposes)
            and any(item["any_self_life"] for item in proposes)
        ),
    }


def write_report(*, stock: dict[str, Any], author: dict[str, Any] | None, cost_cny: float) -> None:
    lines = [
        "# User-channel life prose boundary",
        "",
        "Clone-only. Production `data/` unread-write, napcat untouched.",
        "",
        "## Authoring",
    ]
    if author is None:
        lines.append("Skipped (`--skip-author`).")
    else:
        lines.append(
            f"World Author propose samples: {author['propose_count']} / required {AUTHOR_N}."
        )
        lines.append(f"Accepted: {author['accepted']}. Cost so far ¥{author['cost_cny']}.")
        lines.append("")
        for index, sample in enumerate(author.get("proposes") or (), start=1):
            lines.append(f"### Sample {index} premise")
            lines.append("")
            lines.append(sample.get("premise") or "")
            lines.append("")
            for outcome in sample.get("outcomes") or ():
                lines.append(f"- `user_channel_completion={outcome['user_channel_completion']}`")
                lines.append("")
                lines.append(outcome["text"])
                lines.append("")
    lines.extend(
        [
            "## Stock compensation",
            "",
            json.dumps(stock.get("apply") or {}, ensure_ascii=False, indent=2),
            "",
            "### Before",
            "",
            json.dumps(stock.get("before_hits") or {}, ensure_ascii=False, indent=2),
            "",
            "### After",
            "",
            json.dumps(stock.get("after_hits") or {}, ensure_ascii=False, indent=2),
            "",
            "### Remaining present materials (allowed self-life / intention)",
            "",
            json.dumps(stock.get("remaining_self_life") or {}, ensure_ascii=False, indent=2),
            "",
            f"Total spend ¥{cost_cny}.",
            "",
        ]
    )
    (OUTPUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


async def async_main(args: argparse.Namespace) -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    source = drive.PRODUCTION_DB
    if args.apply_only:
        if args.database is None:
            raise SystemExit("--apply-only requires --database")
        database = Path(args.database).resolve()
        refs = tuple(args.content_ref) if args.content_ref else tuple(
            item["content_ref"]
            for item in find_incident_content(drive.open_ro(database))
        )
        result = apply_limit(database=database, content_refs=refs)
        _dump(OUTPUT / "apply-only.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    inspect_clone = OUTPUT / "inspect.sqlite"
    drive.clone_ledger(source, inspect_clone)
    with drive.open_ro(inspect_clone) as conn:
        incident = find_incident_content(conn)
    _dump(OUTPUT / "incident-content.json", incident)
    if not incident:
        raise SystemExit("did not find the forged bookstore-send prose on the clone")

    stock_clone = OUTPUT / "stock-after.sqlite"
    drive.clone_ledger(source, stock_clone)
    print("compiling present materials before compensation (ledger replay)…", flush=True)
    before = compile_present_materials(database=stock_clone)
    _dump(OUTPUT / "present-before.json", before)
    known = set(before["descriptor_content_refs"])
    refs = tuple(
        sorted({item["content_ref"] for item in incident if item["content_ref"] in known})
    )
    if not refs:
        raise SystemExit("incident content refs are not bound by LifeContentRecorded descriptors")
    apply_result = apply_limit(database=stock_clone, content_refs=refs)
    _dump(OUTPUT / "apply.json", apply_result)
    print("compiling present materials after compensation…", flush=True)
    after = compile_present_materials(database=stock_clone)
    _dump(OUTPUT / "present-after.json", after)
    remaining_self_life = {
        "week_diary": after.get("week_diary"),
        "recent_self_experiences": after.get("recent_self_experiences"),
        "remembered_material_excerpts": [
            excerpt.get("text")
            for item in (after.get("remembered_material") or ())
            for excerpt in (item.get("source_excerpts") or ())
        ],
    }
    stock = {
        "incident": incident,
        "limited_refs": list(refs),
        "apply": apply_result,
        "before_hits": before["incident_hits"],
        "after_hits": after["incident_hits"],
        "remaining_self_life": remaining_self_life,
        "accepted": (
            any(before["incident_hits"].values())
            and not any(after["incident_hits"].values())
        ),
    }
    _dump(OUTPUT / "stock-proof.json", stock)

    author = None
    usage_db = OUTPUT / "usage.sqlite"
    if not args.skip_author:
        print("driving World Author n>=6 on an in-memory ledger…", flush=True)
        author = await drive_world_author(usage_db=usage_db, n=AUTHOR_N)
        _dump(OUTPUT / "author-proof.json", author)
    cost = _cost_cny(usage_db) if usage_db.exists() else 0.0
    write_report(stock=stock, author=author, cost_cny=cost)
    summary = {
        "stock_accepted": stock["accepted"],
        "author_accepted": None if author is None else author["accepted"],
        "author_propose_count": None if author is None else author["propose_count"],
        "cost_cny": cost,
        "out": str(OUTPUT / "REPORT.md"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not stock["accepted"]:
        return 1
    if author is not None and not author["accepted"]:
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-author", action="store_true")
    parser.add_argument("--apply-only", action="store_true")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--content-ref", action="append", default=[])
    args = parser.parse_args()
    raise SystemExit(asyncio.run(async_main(args)))


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    main()
