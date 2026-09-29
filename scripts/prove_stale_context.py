#!/usr/bin/env python3
"""Prove stale expired-expectation context on a production ledger clone.

Never writes ``data/``, never talks to 8787 or NapCat. Artifacts live in
``output/stale-context/``.

Usage::

    .venv/bin/python scripts/prove_stale_context.py
    .venv/bin/python scripts/prove_stale_context.py --skip-speak
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
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
from companion_daemon.world_v2.conversation_continuity import ConversationContinuityCompiler
from companion_daemon.world_v2.ledger_context_resolver import _bounded_domain_items
from companion_daemon.world_v2.proactive_action import (
    _proactive_advisory_value,
    _proactive_opportunity_context,
)
from companion_daemon.world_v2.recent_dialogue import RecentDialogueCompiler, RecentDialogueItem
from companion_daemon.world_v2.response_expectation_view import (
    attach_pending_expectation_advisory,
    counterpart_last_spoke_facts,
    expired_unanswered_expectation,
)
from companion_daemon.world_v2.schemas import ProjectionCursor
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger

OUTPUT = (REPO / "output" / "stale-context").resolve()
WORLD_ID = drive.WORLD_ID
ACTOR = "agent:companion"
USER = "user:geoff"
SUN = "是个太阳的表情来着，表示我今天心情不错"
BUSY = "没啥，刚刚忙完，想着来骚扰你一下"
STRANGE = "你想要我发什么奇怪的东西嘛"
OLD_HIM = "对的，你那边看不到嘛"
HOPED = "他解释一下这个表情是什么意思"
TRIGGER_SEQ = 4290
COST_CAP_CNY = 3.0
N_SPEAK = 5


def _event_row(conn: sqlite3.Connection, seq: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT ledger_sequence, world_revision, deliberation_revision, event_json "
        "FROM world_v2_events WHERE world_id = ? AND ledger_sequence = ?",
        (WORLD_ID, seq),
    ).fetchone()
    if row is None:
        raise SystemExit(f"missing ledger seq {seq}")
    event = drive.parse_event(row["event_json"])
    return {
        "seq": int(row["ledger_sequence"]),
        "world_revision": int(row["world_revision"]),
        "deliberation_revision": int(row["deliberation_revision"]),
        "event_type": event.get("event_type"),
        "event_id": event.get("event_id"),
        "payload": event.get("_payload") or {},
    }


def _inner_turn_cursor(conn: sqlite3.Connection) -> ProjectionCursor:
    """Cursor of the last event before the chase payload was stored."""

    row = conn.execute(
        "SELECT ledger_sequence, world_revision, deliberation_revision, event_json "
        "FROM world_v2_events WHERE world_id = ? AND ledger_sequence BETWEEN ? AND ? "
        "ORDER BY ledger_sequence",
        (WORLD_ID, TRIGGER_SEQ, 4313),
    ).fetchall()
    chosen = None
    for item in row:
        event = drive.parse_event(item["event_json"])
        kind = event.get("event_type")
        if kind in {"ModelResultRecorded", "ProposalRecorded"}:
            chosen = item
            break
        if chosen is None:
            chosen = item
    if chosen is None:
        raise SystemExit("could not locate chase inner-turn cursor")
    return ProjectionCursor(
        world_revision=int(chosen["world_revision"]),
        deliberation_revision=int(chosen["deliberation_revision"]),
        ledger_sequence=int(chosen["ledger_sequence"]),
    )


def _strip_marks(items: tuple[RecentDialogueItem, ...]) -> tuple[RecentDialogueItem, ...]:
    return tuple(item.model_copy(update={"continuity_reasons": ()}) for item in items)


def _select_under_ref_budget(
    items: tuple[RecentDialogueItem, ...], *, max_refs: int = 32
) -> list[RecentDialogueItem]:
    selected: list[RecentDialogueItem] = []
    refs: set[str] = set()
    for item in items:
        candidate = {claim.authority_event_ref for claim in item.source_claims}
        if len(refs | candidate) > max_refs:
            continue
        selected.append(item)
        refs |= candidate
    return selected


def _texts(items: list[RecentDialogueItem] | tuple[RecentDialogueItem, ...]) -> list[str]:
    return [item.text for item in items if item.speaker == "counterpart"]


def compile_slice(
    *,
    ledger: SQLiteWorldLedger,
    projection,
    trigger_ref: str,
    strip: bool,
    max_items: int | None = None,
    max_fields: int | None = None,
) -> dict[str, Any]:
    compiled = RecentDialogueCompiler(ledger=ledger).compile_with_acknowledgements(
        projection=projection,
        actor_ref=ACTOR,
        subject_refs=frozenset({ACTOR, USER}),
    )
    dialogue = compiled.dialogue
    if strip:
        dialogue = _strip_marks(dialogue)
    continuity = ConversationContinuityCompiler().compile(
        dialogue=dialogue, trigger_ref=trigger_ref
    )
    ranked = _bounded_domain_items("recent_dialogue", continuity.dialogue, projection.logical_time)
    assert ranked is not None
    selected = _select_under_ref_budget(ranked)
    if max_fields is not None:
        kept: list[RecentDialogueItem] = []
        used = 0
        for item in selected:
            n = len(item.model_dump(mode="json"))
            if used + n > max_fields:
                continue
            kept.append(item)
            used += n
            if max_items is not None and len(kept) >= max_items:
                break
        selected = kept
    elif max_items is not None:
        selected = selected[:max_items]
    counterpart = [
        {"text": item.text, "dialogue_id": item.dialogue_id, "sequence": item.sequence}
        for item in selected
        if item.speaker == "counterpart"
    ]
    return {
        "counterpart_texts": [item["text"] for item in counterpart],
        "has_sun": any(SUN in item["text"] for item in counterpart),
        "has_busy": any(BUSY in item["text"] for item in counterpart),
        "has_strange": any(STRANGE in item["text"] for item in counterpart),
        "has_old_unseen": any(OLD_HIM in item["text"] for item in counterpart),
        "item_count": len(selected),
        "counterpart": counterpart,
    }


def compile_evidence(*, clone: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{clone}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        trigger = _event_row(conn, TRIGGER_SEQ)
        cursor = _inner_turn_cursor(conn)
    finally:
        conn.close()
    payload = trigger.get("payload") or {}
    process = payload.get("process") if isinstance(payload.get("process"), dict) else {}
    trigger_ref = str(
        process.get("source_evidence_ref")
        or payload.get("source_evidence_ref")
        or trigger.get("event_id")
    )
    ledger = SQLiteWorldLedger(path=clone, world_id=WORLD_ID)
    projection = ledger.project_at(cursor)
    before = compile_slice(
        ledger=ledger,
        projection=projection,
        trigger_ref=trigger_ref,
        strip=True,
        max_items=8,
        max_fields=96,
    )
    after = compile_slice(
        ledger=ledger,
        projection=projection,
        trigger_ref=trigger_ref,
        strip=False,
        max_fields=96,
    )
    replay = compile_slice(
        ledger=ledger,
        projection=projection,
        trigger_ref=trigger_ref,
        strip=False,
        max_fields=96,
    )
    expired = expired_unanswered_expectation(projection)
    seconds, spoken_since = counterpart_last_spoke_facts(
        projection,
        since_world_revision=None if expired is None else expired.declared_world_revision,
    )
    attached = attach_pending_expectation_advisory(
        {
            "world_id": WORLD_ID,
            "actor_ref": ACTOR,
            "world_revision": cursor.world_revision,
            "deliberation_revision": cursor.deliberation_revision,
            "ledger_sequence": cursor.ledger_sequence,
            "logical_time": projection.logical_time.isoformat()
            if projection.logical_time
            else None,
            "consumer_scope": "deliberation_internal",
            "viewer_privacy_ceiling": "private",
            "context_compiler_version": "context-capsule-compiler:test",
            "truncation": {},
            "slices": {},
        },
        projection,
        anchor_event_ref=trigger_ref,
    )
    advisories = (
        ((attached.get("slices") or {}).get("advisories") or {}).get("items") or []
    )
    advisory_values = []
    for item in advisories:
        value = item.get("value") if isinstance(item, dict) else None
        if isinstance(value, dict):
            candidates = value.get("candidates") or []
            if candidates:
                advisory_values.append(
                    {
                        "kind": value.get("kind"),
                        "value": candidates[0].get("value"),
                    }
                )
    opportunity_context = None
    opportunity_advisory = None
    if expired is not None:
        from types import SimpleNamespace

        opportunity_context = _proactive_opportunity_context(
            opportunity=SimpleNamespace(source_kind="expired_expectation", stimulus_event_refs=()),
            event=SimpleNamespace(event_id=trigger_ref, payload_hash="0" * 64),
            head=None,
            projection=projection,
        )
        opportunity_advisory = _proactive_advisory_value(
            opportunity_context=opportunity_context,
            source_kind="expired_expectation",
        )
    other_lanes = {}
    compiled = RecentDialogueCompiler(ledger=ledger).compile_with_acknowledgements(
        projection=projection,
        actor_ref=ACTOR,
        subject_refs=frozenset({ACTOR, USER}),
    )
    for name, lane_trigger in (
        ("expired_expectation", trigger_ref),
        ("spontaneous_contact", "event:observation:synthetic-head"),
        ("post_silent", "event:clock:synthetic"),
        ("private_impression", "event:private-impression:synthetic"),
    ):
        if name == "spontaneous_contact":
            newest = next(
                (
                    item
                    for item in sorted(
                        compiled.dialogue, key=lambda item: item.sequence, reverse=True
                    )
                    if item.speaker == "counterpart"
                ),
                None,
            )
            lane_trigger = (
                newest.source_claims[0].authority_event_ref if newest is not None else lane_trigger
            )
        slice_ = compile_slice(
            ledger=ledger,
            projection=projection,
            trigger_ref=lane_trigger,
            strip=False,
            max_fields=96,
        )
        other_lanes[name] = {
            "has_sun": slice_["has_sun"],
            "counterpart_texts": slice_["counterpart_texts"],
        }
    return {
        "cursor": {
            "world_revision": cursor.world_revision,
            "deliberation_revision": cursor.deliberation_revision,
            "ledger_sequence": cursor.ledger_sequence,
        },
        "trigger_ref": trigger_ref,
        "logical_time": projection.logical_time.isoformat() if projection.logical_time else None,
        "before_strip_marks": before,
        "after_live_head": after,
        "replay_matches": replay == after,
        "expired": None
        if expired is None
        else {
            "hoped_response": expired.hoped_response,
            "receipt_event_id": expired.receipt_event_id,
            "receipt_world_revision": expired.receipt_world_revision,
            "declared_world_revision": expired.declared_world_revision,
        },
        "seconds_since_he_last_spoke": seconds,
        "spoken_since_declared": spoken_since,
        "attached_advisories": advisory_values,
        "opportunity_context": opportunity_context,
        "opportunity_advisory": opportunity_advisory,
        "other_lanes": other_lanes,
        "_projection": projection,
        "_ledger": ledger,
        "_cursor": cursor,
    }


def _extract_texts(value: object) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str) and text.strip():
            found.append(text.strip())
        for item in value.values():
            found.extend(_extract_texts(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_extract_texts(item))
    return found


def compile_wakeup_snapshot(evidence: dict[str, Any]):
    from companion_daemon.world_v2.character_interior.contracts import (
        _InteriorCapabilityManifest,
        _canonical_json,
    )
    from companion_daemon.world_v2.character_interior.snapshot_compiler import (
        compile_inner_life_snapshot,
    )
    from companion_daemon.world_v2.expression_draft import QQ_NAPCAT_EXPRESSION_CAPABILITIES

    ledger = evidence["_ledger"]
    projection = evidence["_projection"]
    trigger_ref = evidence["trigger_ref"]
    compiled = RecentDialogueCompiler(ledger=ledger).compile_with_acknowledgements(
        projection=projection,
        actor_ref=ACTOR,
        subject_refs=frozenset({ACTOR, USER}),
    )
    continuity = ConversationContinuityCompiler().compile(
        dialogue=compiled.dialogue, trigger_ref=trigger_ref
    )
    ranked = _bounded_domain_items(
        "recent_dialogue", continuity.dialogue, projection.logical_time
    )
    assert ranked is not None
    selected = _select_under_ref_budget(ranked)
    kept: list[RecentDialogueItem] = []
    used = 0
    for item in selected:
        n = len(item.model_dump(mode="json"))
        if used + n > 96:
            continue
        kept.append(item)
        used += n
    items = [
        {
            "source_ref": item.dialogue_id,
            "item_ref": item.dialogue_id,
            "value": item.model_dump(mode="json"),
        }
        for item in kept
    ]
    context: dict[str, object] = {
        "world_id": WORLD_ID,
        "actor_ref": ACTOR,
        "world_revision": evidence["_cursor"].world_revision,
        "deliberation_revision": evidence["_cursor"].deliberation_revision,
        "ledger_sequence": evidence["_cursor"].ledger_sequence,
        "logical_time": projection.logical_time.isoformat() if projection.logical_time else None,
        "consumer_scope": "deliberation_internal",
        "viewer_privacy_ceiling": "private",
        "context_compiler_version": "context-capsule-compiler:stale-context",
        "truncation": {},
        "slices": {
            "recent_dialogue": {
                "availability": "available",
                "items": items,
            }
        },
    }
    context = attach_pending_expectation_advisory(
        context, projection, anchor_event_ref=trigger_ref
    )
    snapshot = compile_inner_life_snapshot(context)
    payload = {
        "contract": "character-interior-proactive-capability.1",
        "expression_capabilities": QQ_NAPCAT_EXPRESSION_CAPABILITIES.prompt_value(),
        "source_opportunity": {"source_kind": "expired_expectation"},
        "target_ref": USER,
    }
    payload_json = _canonical_json(payload)
    source_refs = tuple(snapshot.source_refs[:32]) or (trigger_ref,)
    manifest = _InteriorCapabilityManifest(
        capability_ref="capability:proactive:stale-context",
        capability_kind="proactive_contact",
        payload_json=payload_json,
        payload_hash="sha256:" + hashlib.sha256(payload_json.encode()).hexdigest(),
        source_refs=source_refs,
    )
    return snapshot, manifest, trigger_ref


def _beat_texts(decision: object) -> list[str]:
    found: list[str] = []
    if not isinstance(decision, dict):
        return found
    payload = decision.get("payload")
    root = payload if isinstance(payload, dict) else decision
    beats = root.get("beats")
    if isinstance(beats, list):
        for beat in beats:
            if isinstance(beat, dict):
                text = beat.get("text")
                if isinstance(text, str) and text.strip():
                    found.append(text.strip())
    if found:
        return found
    return _extract_texts(decision)


def asked_about_unanswered_emoji(texts: list[str]) -> bool:
    return any(
        ("表情" in text and ("什么" in text or "啥" in text)) or "藏着掖着" in text
        for text in texts
    )


async def one_speak_trial(
    *,
    snapshot,
    manifest,
    trigger_ref: str,
    index: int,
    model,
    usages: list[object],
) -> dict[str, Any]:
    from companion_daemon.world_v2.character_interior.ports import _InteriorRoleRequest
    from companion_daemon.world_v2.character_interior.structured_role import (
        StructuredCharacterRoleFaculty,
        StructuredRoleResultError,
    )

    role = StructuredCharacterRoleFaculty(
        model=model, model_id=str(getattr(model, "model", "deepseek"))
    )
    request = _InteriorRoleRequest(
        inner_turn_id=f"character-inner-turn:stale-context:{index}",
        phase="consider",
        subject_ref=f"opportunity:stale-context:{index}",
        trigger_ref=trigger_ref,
        purpose="proactive_contact",
        context_note=evidence_note(),
        subject_source_refs=manifest.source_refs,
        capability_manifest=manifest,
        snapshot=snapshot,
    )
    before = len(usages)
    result = None
    error = None
    try:
        result = await role.consider(request)
    except StructuredRoleResultError as exc:
        try:
            corrected = request.model_copy(
                update={
                    "correction_ordinal": 1,
                    "correction_failure_code": (exc.code or "role_result_schema_invalid")[:128],
                    "correction_failure_detail": (exc.detail or str(exc))[:4096],
                }
            )
            result = await role.consider(corrected)
        except Exception as retry_exc:
            error = (
                f"{type(exc).__name__}: {exc.code}: {exc.detail}; "
                f"retry {type(retry_exc).__name__}: {retry_exc}"
            )[:2000]
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:2000]
    if error is not None:
        return {"status": "error", "error": error, "cost_cny": _usage_cost(usages[before:])}
    decision = result.get("decision") if isinstance(result, dict) else None
    her_texts = _beat_texts(decision)
    status = result.get("status") if isinstance(result, dict) else None
    timing = None
    if isinstance(decision, dict):
        payload = decision.get("payload")
        root = payload if isinstance(payload, dict) else decision
        timing = root.get("timing_choice")
    return {
        "status": "ran",
        "role_status": status,
        "timing_choice": timing,
        "summary": result.get("summary") if isinstance(result, dict) else None,
        "her_texts": her_texts,
        "asked_again": asked_about_unanswered_emoji(her_texts),
        "saw_sun_in_snapshot": SUN
        in json.dumps(snapshot.materials.get("recent_dialogue"), ensure_ascii=False),
        "cost_cny": _usage_cost(usages[before:]),
    }


def evidence_note() -> str:
    return (
        "Hope expired: timing evidence only; she still decides. "
        "The recent dialogue is the live head through this instant."
    )


def _usage_cost(records: list[object]) -> float:
    from companion_daemon.usage_metrics import estimate_model_cost_usd

    total = 0.0
    for item in records:
        usd, _version = estimate_model_cost_usd(
            model=str(getattr(item, "model", "") or "__unpriced__"),
            prompt_tokens=int(getattr(item, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(item, "completion_tokens", 0) or 0),
            cache_hit_tokens=int(getattr(item, "cache_hit_tokens", 0) or 0),
            cache_miss_tokens=int(getattr(item, "cache_miss_tokens", 0) or 0),
        )
        total += usd * 7.2
    return round(total, 4)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-speak", action="store_true")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    clone = OUTPUT / "companion.epoch2.sqlite"
    drive.clone_ledger(drive.PRODUCTION_DB, clone)
    evidence = compile_evidence(clone=clone)
    serializable = {key: value for key, value in evidence.items() if not key.startswith("_")}
    (OUTPUT / "context-evidence.json").write_text(
        json.dumps(serializable, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    speak: list[dict[str, Any]] = []
    spent = 0.0
    snapshot_preview: dict[str, Any] | None = None
    if not args.skip_speak:
        import httpx
        from companion_daemon.config import Settings
        from companion_daemon.llm import DeepSeekChatModel

        snapshot, manifest, trigger_ref = compile_wakeup_snapshot(evidence)
        view = snapshot.model_view()
        snapshot_preview = {
            "availability": snapshot.availability,
            "source_ref_count": len(snapshot.source_refs),
            "since_he_last_spoke": view.get("since_he_last_spoke"),
            "advisories": snapshot.materials.get("advisories"),
            "recent_dialogue_texts": [
                {"speaker": item.get("speaker"), "text": item.get("text")}
                for item in (snapshot.materials.get("recent_dialogue") or [])
                if isinstance(item, dict)
            ],
            "has_sun": SUN
            in json.dumps(snapshot.materials.get("recent_dialogue"), ensure_ascii=False),
            "has_busy": BUSY
            in json.dumps(snapshot.materials.get("recent_dialogue"), ensure_ascii=False),
            "has_strange": STRANGE
            in json.dumps(snapshot.materials.get("recent_dialogue"), ensure_ascii=False),
        }
        (OUTPUT / "snapshot-preview.json").write_text(
            json.dumps(snapshot_preview, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        settings = Settings()
        if not settings.deepseek_api_key:
            raise SystemExit("DEEPSEEK_API_KEY is missing")
        usages: list[object] = []
        model = DeepSeekChatModel(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            thinking_enabled=False,
            max_completion_tokens=2_048,
            usage_observer=usages.append,
            client=httpx.AsyncClient(timeout=180, trust_env=False),
        )
        spoken = 0
        index = 0
        while spoken < N_SPEAK and index < N_SPEAK + 3 and spent < COST_CAP_CNY:
            index += 1
            trial = await one_speak_trial(
                snapshot=snapshot,
                manifest=manifest,
                trigger_ref=trigger_ref,
                index=index,
                model=model,
                usages=usages,
            )
            speak.append(trial)
            spent += float(trial.get("cost_cny") or 0)
            if trial.get("her_texts"):
                spoken += 1
            (OUTPUT / "speak-trials.json").write_text(
                json.dumps(speak, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
    summary = {
        "finished_at": datetime.now(UTC).isoformat(),
        "context": {
            "before_has_sun": evidence["before_strip_marks"]["has_sun"],
            "after_has_sun": evidence["after_live_head"]["has_sun"],
            "before_texts": evidence["before_strip_marks"]["counterpart_texts"],
            "after_texts": evidence["after_live_head"]["counterpart_texts"],
            "advisory": evidence["opportunity_advisory"],
            "attached": evidence["attached_advisories"],
            "replay_matches": evidence["replay_matches"],
            "snapshot_preview": snapshot_preview,
            "other_lanes": {
                name: item["has_sun"] for name, item in evidence["other_lanes"].items()
            },
        },
        "speak": [
            {
                "status": item.get("status"),
                "role_status": item.get("role_status"),
                "her_texts": item.get("her_texts"),
                "asked_again": item.get("asked_again"),
                "timing_choice": item.get("timing_choice"),
                "summary": item.get("summary"),
                "cost_cny": item.get("cost_cny"),
                "error": item.get("error"),
            }
            for item in speak
        ],
        "spend_cny": round(spent, 4),
    }
    (OUTPUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
