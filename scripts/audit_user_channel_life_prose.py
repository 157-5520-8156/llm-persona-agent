#!/usr/bin/env python3
"""Operator-only replay of the focused novel-origin critic over historical life prose.

This is not a production path. Production currently evaluates focused origin
deterministically as always-supported; the model-facing contract still lives in
``life_development_novel_origin_messages``. This tool wraps each historical
LifeContentRecorded sidecar as a one-branch draft that contract can eat, then
asks the same independent reviewer role to judge whether outcome 0 claims a
completed user-channel act.

Never sends QQ. Default is report-only. Writing the live ledger requires
``--apply-production``.

Usage::

    .venv/bin/python scripts/audit_user_channel_life_prose.py --enumerate-only
    .venv/bin/python scripts/audit_user_channel_life_prose.py
    .venv/bin/python scripts/audit_user_channel_life_prose.py --apply-production
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import drive_production_lanes as drive
from companion_daemon.llm import DeepSeekChatModel, model_call_scope
from companion_daemon.world_v2.event_identity import domain_idempotency_key
from companion_daemon.world_v2.life_content_events import (
    LIFE_CONTENT_USER_CHANNEL_AUTHORITY_LIMITED,
    LifeContentUserChannelAuthorityLimitedPayload,
)
from companion_daemon.world_v2.life_development_draft import (
    LifeDevelopmentCapabilityManifest,
    LifeDevelopmentClaimDeclaration,
    LifeDevelopmentOutcomeDraft,
    LifeDevelopmentPossibilityDraft,
    LifeDevelopmentTimingDraft,
)
from companion_daemon.world_v2.life_development_model_adapter import (
    RoleBoundLifeDevelopmentModelAdapter,
)
from companion_daemon.world_v2.life_development_source_closure import (
    LifeDevelopmentNovelOriginReview,
    LifeDevelopmentSourceClosureError,
    life_development_novel_origin_correction_message,
    life_development_novel_origin_messages,
    parse_life_development_novel_origin_review,
)
from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore
from companion_daemon.world_v2.schemas import ProjectionCursor, WorldEvent
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.structured_completion import complete_json_object

OUTPUT = (REPO / "output" / "prose-backfill").resolve()
WORLD_ID = drive.WORLD_ID
ACTOR = "agent:companion"
BUDGET_CNY = 8.0
HISTORICAL_OUTCOME_PATH = "outcomes.0.text"
ADAPTER_ALTERNATIVE_TEXT = "她待在原地，这段候选变化没有发生。"
AUDIT_ANCHOR = "event:operator:user-channel-prose-audit"


def _dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    if isinstance(payload, dict):
        return payload
    raw = event.get("payload_json")
    if isinstance(raw, str):
        decoded = json.loads(raw)
        if isinstance(decoded, dict):
            return decoded
    if isinstance(payload, str):
        decoded = json.loads(payload)
        if isinstance(decoded, dict):
            return decoded
    return {}


def enumerate_recorded(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """All LifeContentRecorded rows plus their sidecar bytes. No prose heuristics."""

    rows = conn.execute(
        """
        SELECT ledger_sequence, event_json
        FROM world_v2_events
        WHERE world_id = ?
          AND json_extract(event_json, '$.event_type') = 'LifeContentRecorded'
        ORDER BY ledger_sequence
        """,
        (WORLD_ID,),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        event = json.loads(row["event_json"])
        payload = _event_payload(event)
        content_ref = str(payload.get("content_ref") or "")
        sidecar = conn.execute(
            """
            SELECT content_kind, content_payload_hash, text
            FROM world_v2_life_content
            WHERE world_id = ? AND content_ref = ?
            """,
            (WORLD_ID, content_ref),
        ).fetchone()
        items.append(
            {
                "ledger_sequence": int(row["ledger_sequence"]),
                "event_id": event.get("event_id"),
                "logical_time": event.get("logical_time"),
                "content_ref": content_ref,
                "recorded_kind": payload.get("content_kind"),
                "source_kind": payload.get("source_kind"),
                "source_event_ref": payload.get("source_event_ref"),
                "privacy_class": payload.get("privacy_class") or "personal",
                "content_payload_hash": payload.get("content_payload_hash"),
                "sidecar_kind": None if sidecar is None else sidecar["content_kind"],
                "sidecar_hash": None if sidecar is None else sidecar["content_payload_hash"],
                "text": None if sidecar is None else sidecar["text"],
            }
        )
    return items


def sidecar_kind_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT content_kind, COUNT(*) AS c
        FROM world_v2_life_content
        WHERE world_id = ?
        GROUP BY content_kind
        """,
        (WORLD_ID,),
    ).fetchall()
    return {str(row["content_kind"]): int(row["c"]) for row in rows}


def wrap_historical_outcome(
    *,
    text: str,
    privacy_class: str,
) -> LifeDevelopmentPossibilityDraft:
    """Minimal adapter: one historical text as outcome 0 of a legal draft.

    Outcome 1 exists only because the draft schema requires two branches. The
    operator ignores findings on that dummy alternative.
    """

    claim_id = "local:claim:candidate-self-life"
    return LifeDevelopmentPossibilityDraft(
        decision="propose",
        authored_subject_ref=ACTOR,
        causal_authority="world_contingency",
        outcome_resolution_authority="world_contingency",
        premise_scope="external_opportunity",
        premise="一次尚未结算的生活分支出场。",
        premise_claim_refs=(claim_id,),
        claim_declarations=(
            LifeDevelopmentClaimDeclaration(
                claim_id=claim_id,
                summary="这一候选分支里她过了一段自己的生活。",
                scope="novel_world_generation",
                subject_scope="world_environment",
                source_refs=(),
            ),
        ),
        timing=LifeDevelopmentTimingDraft(mode="now", duration_minutes=30),
        anchor_refs=(AUDIT_ANCHOR,),
        privacy_class=privacy_class,  # type: ignore[arg-type]
        outcomes=(
            LifeDevelopmentOutcomeDraft(
                experienced_by_ref=ACTOR,
                text=text,
                user_channel_completion="none",
                privacy_class=privacy_class,  # type: ignore[arg-type]
                relative_plausibility_weight=1,
                claim_refs=(claim_id,),
            ),
            LifeDevelopmentOutcomeDraft(
                experienced_by_ref=ACTOR,
                text=ADAPTER_ALTERNATIVE_TEXT,
                user_channel_completion="none",
                privacy_class=privacy_class,  # type: ignore[arg-type]
                relative_plausibility_weight=1,
                claim_refs=(claim_id,),
            ),
        ),
    )


def audit_manifest() -> LifeDevelopmentCapabilityManifest:
    return LifeDevelopmentCapabilityManifest(
        version="life-development-capability.operator-audit.1",
        owner_actor_ref=ACTOR,
        pinned_cursor=ProjectionCursor(
            world_revision=1,
            deliberation_revision=1,
            ledger_sequence=1,
        ),
        anchor_refs=(AUDIT_ANCHOR,),
        grounding_refs=(AUDIT_ANCHOR,),
        location_capabilities=(),
        entity_refs=(),
        max_future_days=30,
        max_window_minutes=12 * 60,
    )


def _review_dump(review: LifeDevelopmentNovelOriginReview) -> dict[str, Any]:
    return review.model_dump(mode="json")


def interpret_review(review: LifeDevelopmentNovelOriginReview) -> dict[str, Any]:
    """Read the critic's own coordinates. Outcome 1 is adapter scaffolding."""

    historical_findings = [
        item.model_dump(mode="json")
        for item in review.unsupported_outcome_prerequisites
        if item.prose_path == HISTORICAL_OUTCOME_PATH
    ]
    return {
        "decision": review.decision,
        "reason": review.reason,
        "historical_outcome_findings": historical_findings,
        "claims_completed_user_channel_act": bool(historical_findings),
        "adapter_alternative_findings": [
            item.model_dump(mode="json")
            for item in review.unsupported_outcome_prerequisites
            if item.prose_path != HISTORICAL_OUTCOME_PATH
        ],
        "other_coordinates": {
            "unsupported_claims": [item.model_dump(mode="json") for item in review.unsupported_claims],
            "unsupported_provisional_npcs": [
                item.model_dump(mode="json") for item in review.unsupported_provisional_npcs
            ],
            "unsupported_provisional_places": [
                item.model_dump(mode="json") for item in review.unsupported_provisional_places
            ],
            "unsupported_objective_transitions": [
                item.model_dump(mode="json") for item in review.unsupported_objective_transitions
            ],
        },
    }


def _cost_cny(usage_db: Path) -> float:
    if not usage_db.exists():
        return 0.0
    return float((drive.cost_report(usage_db, since_id=0) or {}).get("cost_cny") or 0)


async def ask_focused_critic(
    *,
    model: RoleBoundLifeDevelopmentModelAdapter,
    draft: LifeDevelopmentPossibilityDraft,
    manifest: LifeDevelopmentCapabilityManifest,
) -> dict[str, Any]:
    messages = life_development_novel_origin_messages(
        context={},
        manifest=manifest,
        draft=draft,
    )
    attempts: list[dict[str, Any]] = []
    raw = ""
    with model_call_scope("operator.user_channel_prose_audit"):
        raw = await complete_json_object(model, messages, temperature=0.2)
    attempts.append({"ordinal": 0, "raw": raw})
    try:
        review = parse_life_development_novel_origin_review(raw=raw, draft=draft)
    except LifeDevelopmentSourceClosureError as exc:
        correction = life_development_novel_origin_correction_message(error=exc, draft=draft)
        with model_call_scope("operator.user_channel_prose_audit"):
            raw = await complete_json_object(
                model,
                [*messages, correction],
                temperature=0.2,
            )
        attempts.append(
            {
                "ordinal": 1,
                "raw": raw,
                "prior_error": {"code": exc.code, "detail": exc.detail},
            }
        )
        try:
            review = parse_life_development_novel_origin_review(raw=raw, draft=draft)
        except LifeDevelopmentSourceClosureError as retry_exc:
            return {
                "status": "technical_failure",
                "error": {"code": retry_exc.code, "detail": retry_exc.detail},
                "attempts": attempts,
            }
    interpreted = interpret_review(review)
    interpreted["status"] = "ok"
    interpreted["review"] = _review_dump(review)
    interpreted["attempts"] = len(attempts)
    return interpreted


def media_delivery_inspection_counts(projection) -> tuple[int, int]:
    delivery_count = len(getattr(projection, "media_deliveries", ()) or ())
    action_count = sum(
        1
        for item in getattr(projection, "actions", ())
        if getattr(item, "kind", None) == "media_delivery"
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
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
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
        causation_id="operator:focused-critic-historical-replay",
        correlation_id="correlation:user-channel-life-boundary",
        idempotency_key=identity,
        payload=payload,
    )


def apply_limit(*, database: Path, content_refs: tuple[str, ...]) -> dict[str, Any]:
    ledger = SQLiteWorldLedger(path=database, world_id=WORLD_ID)
    projection = ledger.project()
    if projection.logical_time is None:
        raise SystemExit("ledger has no logical time")
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
    }


def write_report(*, inventory: dict[str, Any], reviews: list[dict[str, Any]], cost_cny: float) -> None:
    polluted = [item for item in reviews if item.get("claims_completed_user_channel_act")]
    failures = [item for item in reviews if item.get("status") != "ok"]
    lines = [
        "# Historical user-channel prose audit",
        "",
        "Operator tool only. Production path unchanged. The focused critic contract",
        "in `life_development_novel_origin_messages` is the sole semantic judge.",
        "",
        f"- Recorded items: {inventory['recorded_count']}",
        f"- Unique exact texts reviewed: {inventory['unique_texts_reviewed']}",
        f"- Critic said completed user-channel act: {len(polluted)} / {len(reviews)}",
        f"- Technical failures: {len(failures)}",
        f"- Spend ¥{cost_cny}",
        f"- Sampling: {inventory['sampling']}",
        "",
        "## Polluted items",
        "",
    ]
    if not polluted:
        lines.append("None.")
        lines.append("")
    for index, item in enumerate(polluted, start=1):
        lines.append(f"### {index}. `{item['content_ref']}`")
        lines.append("")
        lines.append(f"- kind: `{item['recorded_kind']}` / source `{item['source_kind']}`")
        lines.append(f"- seq {item['ledger_sequence']} at {item['logical_time']}")
        lines.append(f"- critic decision: `{item.get('decision')}`")
        lines.append("")
        lines.append("原文")
        lines.append("")
        lines.append(item.get("text") or "")
        lines.append("")
        lines.append("评论家理由")
        lines.append("")
        lines.append(item.get("reason") or "")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(item.get("historical_outcome_findings") or [], ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")
    lines.extend(
        [
            "## All reviews",
            "",
            f"See `reviews.json`. Cost ¥{cost_cny}.",
            "",
        ]
    )
    (OUTPUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


async def async_main(args: argparse.Namespace) -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    snapshot = args.database
    if snapshot is None:
        snapshot = OUTPUT / "ledger-snapshot.sqlite"
        if not snapshot.exists():
            drive.clone_ledger(drive.PRODUCTION_DB, snapshot)
    snapshot = snapshot.resolve()
    if drive._is_production_path(snapshot) and not args.apply_production:
        raise SystemExit(f"refusing to treat production as a working copy: {snapshot}")

    with drive.open_ro(snapshot) as conn:
        recorded = enumerate_recorded(conn)
        kinds = sidecar_kind_counts(conn)
    inventory = {
        "snapshot": str(snapshot),
        "recorded_count": len(recorded),
        "sidecar_kind_counts": kinds,
        "by_recorded_kind": dict(Counter(item["recorded_kind"] for item in recorded)),
        "by_source_kind": dict(Counter(item["source_kind"] for item in recorded)),
        "kind_mismatch": sum(
            1
            for item in recorded
            if item["sidecar_kind"] not in {None, item["recorded_kind"]}
        ),
        "sampling": "all LifeContentRecorded before deploy snapshot",
    }
    _dump(OUTPUT / "inventory.json", {"meta": inventory, "items": recorded})
    print(json.dumps(inventory, ensure_ascii=False, indent=2), flush=True)
    if args.enumerate_only:
        return 0

    if len(recorded) > 100:
        cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        selected = [item for item in recorded if str(item.get("logical_time") or "") >= cutoff]
        inventory["sampling"] = (
            f"recorded count {len(recorded)} > 100; reviewing last 30 days "
            f"({len(selected)} items, cutoff {cutoff})"
        )
        recorded = selected
    else:
        inventory["sampling"] = f"all {len(recorded)} LifeContentRecorded rows"

    from companion_daemon.config import Settings

    settings = Settings()
    if not settings.deepseek_api_key:
        raise SystemExit("DEEPSEEK_API_KEY is required for the focused critic replay")
    usage_db = OUTPUT / "usage.sqlite"
    usage_store = WorldV2UsageStore(path=str(usage_db))
    inner = DeepSeekChatModel(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        thinking_enabled=False,
        max_completion_tokens=4_096,
        usage_observer=usage_store.record,
    )
    critic = RoleBoundLifeDevelopmentModelAdapter(
        model=inner,
        role="world_author_source_reviewer",
    )
    manifest = audit_manifest()
    cache: dict[str, dict[str, Any]] = {}
    reviews: list[dict[str, Any]] = []
    unique_texts = 0
    for index, item in enumerate(recorded, start=1):
        cost = _cost_cny(usage_db)
        if cost >= BUDGET_CNY - 0.4:
            print(f"budget stop at ¥{cost} after {index - 1} items", flush=True)
            break
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            row = {**item, "status": "skipped_empty"}
            reviews.append(row)
            continue
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest in cache:
            row = {**item, **cache[digest], "reused_exact_text": True}
            reviews.append(row)
            _dump(OUTPUT / f"review-{index:03d}.json", row)
            print(
                f"[{index}/{len(recorded)}] reuse {item['content_ref']} "
                f"polluted={row.get('claims_completed_user_channel_act')}",
                flush=True,
            )
            continue
        unique_texts += 1
        print(f"[{index}/{len(recorded)}] critic {item['content_ref']}", flush=True)
        draft = wrap_historical_outcome(
            text=text,
            privacy_class=str(item.get("privacy_class") or "personal"),
        )
        verdict = await ask_focused_critic(model=critic, draft=draft, manifest=manifest)
        cache[digest] = {
            key: value
            for key, value in verdict.items()
            if key != "attempts"
        }
        row = {**item, **verdict, "reused_exact_text": False}
        reviews.append(row)
        _dump(OUTPUT / f"review-{index:03d}.json", row)
        print(
            f"  status={verdict.get('status')} polluted="
            f"{verdict.get('claims_completed_user_channel_act')} "
            f"¥{_cost_cny(usage_db)}",
            flush=True,
        )
    inventory["unique_texts_reviewed"] = unique_texts
    cost = _cost_cny(usage_db)
    _dump(OUTPUT / "reviews.json", reviews)
    _dump(
        OUTPUT / "summary.json",
        {
            "inventory": inventory,
            "polluted_count": sum(1 for item in reviews if item.get("claims_completed_user_channel_act")),
            "failure_count": sum(1 for item in reviews if item.get("status") != "ok"),
            "cost_cny": cost,
        },
    )
    write_report(inventory=inventory, reviews=reviews, cost_cny=cost)
    print(
        json.dumps(
            {
                "polluted": sum(1 for item in reviews if item.get("claims_completed_user_channel_act")),
                "reviewed": len(reviews),
                "unique_texts": unique_texts,
                "cost_cny": cost,
                "report": str(OUTPUT / "REPORT.md"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if args.apply_production:
        refs = tuple(
            sorted(
                {
                    str(item["content_ref"])
                    for item in reviews
                    if item.get("claims_completed_user_channel_act")
                }
            )
        )
        if not refs:
            raise SystemExit("no polluted refs to apply")
        if len(refs) > 32:
            raise SystemExit("limit event accepts at most 32 refs; split batches")
        result = apply_limit(database=drive.PRODUCTION_DB, content_refs=refs)
        _dump(OUTPUT / "apply-production.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enumerate-only", action="store_true")
    parser.add_argument("--database", type=Path)
    parser.add_argument(
        "--apply-production",
        action="store_true",
        help="Write LifeContentUserChannelAuthorityLimited to the live ledger.",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(async_main(args)))


if __name__ == "__main__":
    main()
