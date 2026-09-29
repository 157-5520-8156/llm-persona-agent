#!/usr/bin/env python3
"""Prove the sun-round stale-topic fix on a production ledger clone.

Never writes ``data/``, never talks to 8787 or NapCat. Artifacts live in
``output/stale-topics/``.

Usage::

    .venv/bin/python scripts/prove_stale_topics.py
    .venv/bin/python scripts/prove_stale_topics.py --skip-speak
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
from companion_daemon.world_v2.conversation_continuity import (
    ConversationContinuityCompiler,
    pack_recent_dialogue_under_source_budget,
)
from companion_daemon.world_v2.ledger_context_resolver import _bounded_domain_items
from companion_daemon.world_v2.recent_dialogue import RecentDialogueCompiler, RecentDialogueItem
from companion_daemon.world_v2.schemas import ProjectionCursor
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger

OUTPUT = (REPO / "output" / "stale-topics").resolve()
WORLD_ID = drive.WORLD_ID
ACTOR = "agent:companion"
USER = "user:geoff"
SUN_EXPLAINED = "是个太阳的表情来着，表示我今天心情不错"
PHOTO_OK = "好！"
PHOTO_ASK = "算了先不说这个啦，你昨天不说有照片要给我看嘛"
REACTION_LABEL = "☀️ 太阳"
SUN_SEQ = 4498
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
        "logical_time": event.get("logical_time"),
    }


def _sun_inner_turn(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        "SELECT trigger_ref, cursor_json, snapshot_id, authored_state_json, terminal_result_json "
        "FROM world_v2_character_interior_turns "
        "WHERE world_id = ? AND trigger_ref LIKE ? AND purpose = 'inbound_turn'",
        (WORLD_ID, "%adc98495e9694072fb8396f3c%"),
    ).fetchone()
    if row is None:
        raise SystemExit("missing sun inbound interior turn")
    cursor = json.loads(row["cursor_json"])
    terminal = json.loads(row["terminal_result_json"] or "{}")
    authored = json.loads(row["authored_state_json"] or "{}")
    return {
        "trigger_ref": row["trigger_ref"],
        "cursor": ProjectionCursor(
            world_revision=int(cursor["world_revision"]),
            deliberation_revision=int(cursor["deliberation_revision"]),
            ledger_sequence=int(cursor["ledger_sequence"]),
        ),
        "snapshot_id": row["snapshot_id"],
        "summary": terminal.get("summary"),
        "authored_snapshot": authored.get("snapshot") or {},
    }


def _select_under_ref_budget(
    items: tuple[RecentDialogueItem, ...] | list[RecentDialogueItem],
    *,
    max_refs: int = 32,
) -> list[RecentDialogueItem]:
    return list(
        pack_recent_dialogue_under_source_budget(items, max_refs=max_refs)
    )


def compile_sun_dialogue(*, clone: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{clone}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        sun = _event_row(conn, SUN_SEQ)
        turn = _sun_inner_turn(conn)
    finally:
        conn.close()
    ledger = SQLiteWorldLedger(path=clone, world_id=WORLD_ID)
    projection = ledger.project_at(turn["cursor"])
    trigger_ref = str(sun["event_id"])
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
        if used + n > 256:
            continue
        kept.append(item)
        used += n
    counterpart = [item for item in kept if item.speaker == "counterpart"]
    current = next(
        (item for item in kept if "current_turn" in item.continuity_reasons),
        None,
    )
    meta = sun["payload"].get("coalescing_metadata")
    meta = meta if isinstance(meta, dict) else {}
    reply = sun["payload"].get("reply_context")
    reply = reply if isinstance(reply, dict) else {}
    return {
        "trigger_ref": trigger_ref,
        "cursor": {
            "world_revision": turn["cursor"].world_revision,
            "deliberation_revision": turn["cursor"].deliberation_revision,
            "ledger_sequence": turn["cursor"].ledger_sequence,
        },
        "_sun": {
            "event_id": sun["event_id"],
            "payload_hash": sun["payload"].get("payload_hash"),
            "observation_id": sun["payload"].get("observation_id"),
            "world_revision": sun["world_revision"],
            "logical_time": sun["logical_time"],
            "actor": sun["payload"].get("actor") or USER,
            "channel": sun["payload"].get("channel") or "qq",
            "reply_target": reply.get("target") or "conversation:qq:c2c:2759284998",
            "platform_message_id": reply.get("platform_message_id"),
            "reaction_refs": tuple(
                item for item in (meta.get("reaction_refs") or ()) if isinstance(item, str)
            ),
            "reaction_target_message_id": meta.get("reaction_target_message_id"),
        },
        "production_summary": turn["summary"],
        "reaction_in_dialogue": any(REACTION_LABEL in item.text for item in counterpart),
        "explained_in_dialogue": any(SUN_EXPLAINED in item.text for item in counterpart),
        "photo_ok_in_dialogue": any(PHOTO_OK == item.text for item in counterpart),
        "current_turn_text": None if current is None else current.text,
        "current_turn_is_reaction": bool(
            current is not None and REACTION_LABEL in current.text
        ),
        "current_turn_is_photo_ok": bool(current is not None and current.text == PHOTO_OK),
        "counterpart_texts": [item.text for item in counterpart],
        "kept": [
            {
                "speaker": item.speaker,
                "text": item.text,
                "occurred_at": item.occurred_at.isoformat(),
                "continuity_reasons": list(item.continuity_reasons),
            }
            for item in kept
        ],
        "_ledger": ledger,
        "_projection": projection,
        "_cursor": turn["cursor"],
        "_kept": kept,
        "_authored_snapshot": turn["authored_snapshot"],
    }


def compile_sun_snapshot(evidence: dict[str, Any]):
    from companion_daemon.world_v2.character_interior.contracts import (
        _InteriorCapabilityManifest,
        _canonical_json,
    )
    from companion_daemon.world_v2.character_interior.snapshot_compiler import (
        compile_inner_life_snapshot,
    )
    from companion_daemon.world_v2.expression_draft import QQ_NAPCAT_EXPRESSION_CAPABILITIES

    projection = evidence["_projection"]
    items = [
        {
            "source_ref": item.dialogue_id,
            "item_ref": item.dialogue_id,
            "value": item.model_dump(mode="json"),
        }
        for item in evidence["_kept"]
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
        "context_compiler_version": "context-capsule-compiler:stale-topics",
        "truncation": {},
        "slices": {
            "recent_dialogue": {
                "availability": "available",
                "items": items,
            }
        },
    }
    snapshot = compile_inner_life_snapshot(context)
    payload = {
        "contract": "character-interior-inbound-capability.1",
        "expression_capabilities": QQ_NAPCAT_EXPRESSION_CAPABILITIES.prompt_value(),
        "transport_operation": "complete",
    }
    payload_json = _canonical_json(payload)
    source_refs = tuple(snapshot.source_refs[:32]) or (evidence["trigger_ref"],)
    manifest = _InteriorCapabilityManifest(
        capability_ref="capability:inbound:stale-topics",
        capability_kind="inbound_turn",
        payload_json=payload_json,
        payload_hash="sha256:" + hashlib.sha256(payload_json.encode()).hexdigest(),
        source_refs=source_refs,
    )
    return snapshot, manifest, evidence["trigger_ref"]


def _extract_texts(value: object) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key in ("text", "inline_text"):
            text = value.get(key)
            if isinstance(text, str) and text.strip():
                found.append(text.strip())
        reaction_id = value.get("reaction_id")
        if isinstance(reaction_id, str) and reaction_id.strip():
            found.append(f"[reaction:{reaction_id.strip()}]")
        raw = value.get("canonical_json")
        if isinstance(raw, str) and raw.strip().startswith(("{", "[")):
            try:
                found.extend(_extract_texts(json.loads(raw)))
            except json.JSONDecodeError:
                pass
        for item in value.values():
            if item is raw:
                continue
            found.extend(_extract_texts(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_extract_texts(item))
    return found


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
                reaction_id = beat.get("reaction_id")
                if isinstance(reaction_id, str) and reaction_id.strip():
                    found.append(f"[reaction:{reaction_id.strip()}]")
    if found:
        return found
    messages = root.get("messages")
    if isinstance(messages, list):
        for item in messages:
            if isinstance(item, str) and item.strip():
                found.append(item.strip())
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    found.append(text.strip())
    return found or _extract_texts(decision)


def recognized_sun(texts: list[str]) -> bool:
    blob = "\n".join(texts)
    return "☀️" in blob or "太阳" in blob or "qq-face:74" in blob


def remembered_mood_explain(texts: list[str]) -> bool:
    blob = "\n".join(texts)
    return "心情" in blob or "不错" in blob


def reopened_old_account(texts: list[str]) -> bool:
    blob = "\n".join(texts)
    return any(
        marker in blob
        for marker in ("记岔", "不是猫", "路灯", "梧桐", "昨天那个", "拍猫", "不是照片")
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


def _parse_logical_time(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _proposal_dump(value: object) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dumped if isinstance(dumped, dict) else {}
    return value if isinstance(value, dict) else {}


def _sun_model_input(*, snapshot, sun: dict[str, Any], cursor: dict[str, int], index: int):
    from companion_daemon.world_v2.deliberation import ModelInput, ModelRoute, TriggerMessage
    from companion_daemon.world_v2.proposal_envelope import ProposalEvidenceRef
    from companion_daemon.world_v2.qq_face_render_catalog import compile_inbound_surfaces

    reaction_refs = tuple(sun.get("reaction_refs") or ("qq-face:74",))
    payload_hash = str(sun.get("payload_hash") or "")
    if not payload_hash.startswith("sha256:"):
        payload_hash = "sha256:" + payload_hash
    trigger_ref = f"{sun['event_id']}:trial-{index}"
    observation_ref = f"{sun['observation_id']}:trial-{index}"
    logical_time = _parse_logical_time(sun["logical_time"])
    content = {
        "world_id": WORLD_ID,
        "actor_ref": ACTOR,
        "world_revision": cursor["world_revision"],
        "deliberation_revision": cursor["deliberation_revision"],
        "ledger_sequence": cursor["ledger_sequence"],
        "logical_time": logical_time.isoformat(),
        "inner_life_snapshot": snapshot.model_view(),
    }
    return ModelInput(
        call_id=f"call:stale-topics:{index}",
        attempt_id=f"attempt:stale-topics:{index}",
        route=ModelRoute(tier="flash", reason_code="ordinary", router_version="stale-topics.1"),
        capsule_id=hashlib.sha256(f"stale-topics:{index}".encode()).hexdigest(),
        trigger_ref=trigger_ref[:256],
        evaluated_world_revision=int(cursor["world_revision"]),
        evaluated_deliberation_revision=int(cursor["deliberation_revision"]),
        evaluated_ledger_sequence=int(cursor["ledger_sequence"]),
        model_content_json=json.dumps(content, ensure_ascii=False, default=str),
        trigger_evidence=(
            ProposalEvidenceRef(
                ref_id=observation_ref[:256],
                evidence_kind="observed_message",
                source_world_revision=max(1, int(sun.get("world_revision") or 1)),
                immutable_hash=payload_hash,
            ),
        ),
        trigger_message=TriggerMessage(
            event_ref=trigger_ref[:256],
            event_payload_hash=payload_hash,
            observation_ref=observation_ref[:256],
            source_world_revision=max(1, int(sun.get("world_revision") or 1)),
            actor=str(sun.get("actor") or USER),
            channel=str(sun.get("channel") or "qq"),
            reply_target=str(sun.get("reply_target") or "conversation:qq:c2c:2759284998"),
            platform_message_id=(
                str(sun["platform_message_id"]) if sun.get("platform_message_id") else None
            ),
            text=None,
            reaction_refs=reaction_refs,
            inbound_surfaces=compile_inbound_surfaces(reaction_refs=reaction_refs),
            observed_at=logical_time,
            reaction_target_message_id=(
                str(sun["reaction_target_message_id"])
                if sun.get("reaction_target_message_id")
                else None
            ),
        ),
    )


def _compose_sun_author(*, model):
    from companion_daemon.character import load_character
    from companion_daemon.config import Settings
    from companion_daemon.world_v2.character_interior.inbound_author import (
        _InboundCharacterAuthor,
    )
    from companion_daemon.world_v2.companion_identity import CompanionIdentityFrame
    from companion_daemon.world_v2.expression_draft import qq_expression_capabilities

    settings = Settings()
    character = load_character(str(settings.character_path))
    aliases_raw = character.identity.get("nicknames", ())
    aliases = (
        tuple(str(item) for item in aliases_raw if str(item).strip())
        if isinstance(aliases_raw, list)
        else ()
    )
    return _InboundCharacterAuthor(
        flash_model=model,
        flash_model_id=str(getattr(model, "model", "deepseek")),
        expression_capabilities=qq_expression_capabilities("napcat"),
        identity_frame=CompanionIdentityFrame(
            companion_name=character.name,
            companion_aliases=aliases,
            counterpart_name=settings.primary_user_id,
            stable_identity_facts=tuple(character.canonical_facts),
            shared_history_facts=tuple(character.shared_history_facts),
            counterpart_history_facts=tuple(character.counterpart_history_facts),
            personality_frame=character.personality,
            values=tuple(character.values),
            speech_frame=character.speech,
            speech_examples=tuple(character.speech_examples),
            style_rules=tuple(character.style_rules),
            boundaries=tuple(character.boundaries),
            base_prompt=character.base_prompt,
            appearance=character.appearance,
            background=character.background,
            daily_life=tuple(character.daily_life),
            first_message=character.first_message,
        ),
        require_explicit_authored_decision_fields=True,
        review_claim_free_candidates=False,
    )


async def one_speak_trial(
    *,
    snapshot,
    sun: dict[str, Any],
    cursor: dict[str, int],
    index: int,
    author,
    usages: list[object],
) -> dict[str, Any]:
    from companion_daemon.world_v2.character_interior.inbound_author import (
        _InboundRecallRequested,
    )
    from companion_daemon.world_v2.deliberation import ValidationTechnicalFailure

    request = _sun_model_input(snapshot=snapshot, sun=sun, cursor=cursor, index=index)
    before = len(usages)
    result = None
    error = None
    try:
        result = await author.propose(request)
    except _InboundRecallRequested as exc:
        return {
            "status": "recall",
            "error": f"recall_request: {getattr(exc, 'query', exc)}"[:2000],
            "her_texts": [],
            "recognized_sun": False,
            "remembered_mood_explain": False,
            "reopened_old_account": False,
            "cost_cny": _usage_cost(usages[before:]),
        }
    except ValidationTechnicalFailure as exc:
        try:
            result = await author.correct_role_result(
                request, getattr(exc, "failure_code", None) or "role_result_schema_invalid"
            )
        except Exception as retry_exc:
            error = (
                f"{type(exc).__name__}: {getattr(exc, 'failure_code', None)}: "
                f"{getattr(exc, 'failure_detail', exc)}; "
                f"retry {type(retry_exc).__name__}: {retry_exc}"
            )[:2000]
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:2000]
    if error is not None:
        return {"status": "error", "error": error, "cost_cny": _usage_cost(usages[before:])}
    dumped = _proposal_dump(getattr(result, "raw_proposal", None))
    her_texts = _beat_texts(dumped)
    if not her_texts:
        her_texts = _extract_texts(dumped)
    summary = None
    private = dumped.get("private_turn_state")
    if isinstance(private, dict):
        summary = private.get("inner_state_summary")
    return {
        "status": "ran",
        "timing_choice": dumped.get("timing_choice"),
        "summary": summary,
        "her_texts": her_texts,
        "recognized_sun": recognized_sun(her_texts),
        "remembered_mood_explain": remembered_mood_explain(her_texts),
        "reopened_old_account": reopened_old_account(her_texts),
        "cost_cny": _usage_cost(usages[before:]),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-speak", action="store_true")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    clone = OUTPUT / "companion.epoch2.sqlite"
    drive.clone_ledger(drive.PRODUCTION_DB, clone)
    evidence = compile_sun_dialogue(clone=clone)
    serializable = {key: value for key, value in evidence.items() if not key.startswith("_")}
    (OUTPUT / "dialogue-after.json").write_text(
        json.dumps(serializable, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    snapshot, manifest, trigger_ref = compile_sun_snapshot(evidence)
    view = snapshot.model_view()
    preview = {
        "current_turn_text": evidence["current_turn_text"],
        "current_turn_is_reaction": evidence["current_turn_is_reaction"],
        "reaction_in_dialogue": evidence["reaction_in_dialogue"],
        "explained_in_dialogue": evidence["explained_in_dialogue"],
        "conversation": view.get("materials", {}).get("conversation")
        if isinstance(view.get("materials"), dict)
        else view.get("conversation"),
        "lived_moment": (
            (view.get("materials") or {}).get("lived_moment")
            if isinstance(view.get("materials"), dict)
            else None
        ),
        "since_he_last_spoke": (
            (view.get("materials") or {}).get("since_he_last_spoke")
            if isinstance(view.get("materials"), dict)
            else view.get("since_he_last_spoke")
        ),
    }
    materials = view.get("materials") if isinstance(view.get("materials"), dict) else {}
    preview["conversation"] = materials.get("conversation")
    preview["lived_moment"] = materials.get("lived_moment")
    preview["since_he_last_spoke"] = materials.get("since_he_last_spoke")
    (OUTPUT / "snapshot-preview.json").write_text(
        json.dumps(preview, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    speak: list[dict[str, Any]] = []
    spent = 0.0
    if not args.skip_speak:
        import httpx
        from companion_daemon.config import Settings
        from companion_daemon.llm import DeepSeekChatModel

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
        author = _compose_sun_author(model=model)
        spoken = 0
        index = 0
        while spoken < N_SPEAK and index < N_SPEAK + 3 and spent < COST_CAP_CNY:
            index += 1
            trial = await one_speak_trial(
                snapshot=snapshot,
                sun=evidence["_sun"],
                cursor=evidence["cursor"],
                index=index,
                author=author,
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
        "dialogue": serializable,
        "snapshot_preview": preview,
        "speak": speak,
        "spent_cny": round(spent, 4),
        "recognized_sun_n": sum(1 for item in speak if item.get("recognized_sun")),
        "remembered_mood_n": sum(1 for item in speak if item.get("remembered_mood_explain")),
        "reopened_old_account_n": sum(1 for item in speak if item.get("reopened_old_account")),
    }
    (OUTPUT / "SUMMARY.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(
        {
            "current_turn_text": evidence["current_turn_text"],
            "current_turn_is_reaction": evidence["current_turn_is_reaction"],
            "explained_in_dialogue": evidence["explained_in_dialogue"],
            "recognized_sun_n": summary["recognized_sun_n"],
            "remembered_mood_n": summary["remembered_mood_n"],
            "reopened_old_account_n": summary["reopened_old_account_n"],
            "spent_cny": summary["spent_cny"],
            "speak": [
                {
                    "status": item.get("status"),
                    "timing": item.get("timing_choice"),
                    "texts": item.get("her_texts"),
                    "error": item.get("error"),
                    "recognized_sun": item.get("recognized_sun"),
                    "remembered_mood": item.get("remembered_mood_explain"),
                    "reopened": item.get("reopened_old_account"),
                    "cost_cny": item.get("cost_cny"),
                }
                for item in speak
            ],
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    asyncio.run(main())
