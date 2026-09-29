#!/usr/bin/env python3
"""Prove the conversation slice is a real back-and-forth on a production clone.

Never writes ``data/``, never talks to 8787 or NapCat. Artifacts live in
``output/conversation-slice/``.

Usage::

    .venv/bin/python scripts/prove_conversation_slice.py
    .venv/bin/python scripts/prove_conversation_slice.py --skip-speak
    .venv/bin/python scripts/prove_conversation_slice.py --skip-sun
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
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
import prove_stale_topics as sun

OUTPUT = (REPO / "output" / "conversation-slice").resolve()
WORLD_ID = drive.WORLD_ID
LONG_CHAT_CLONE = (REPO / "output" / "long-chat" / "clone.sqlite").resolve()
COST_CAP_CNY = 8.0
N_TURNS = 12
N_SUN = 5
MESSAGES = (
    "还没睡呀",
    "我倒了杯水回来了",
    "你刚才那句我看见了",
    "书店那张我看着挺好",
    "不是翻旧账，就是想起来了",
    "你现在在干嘛",
    "我这边也还没睡",
    "你要是累了就先睡",
    "其实我就是想看看你还在不在",
    "那我们随便聊两句也行",
    "今晚先这样也挺好",
    "困了就睡，不急着回",
)
PHOTO_MARKERS = ("自拍", "照片", "书店")
DENIALS = ("还没打开", "还没翻", "晚点再", "明天再给", "明天白天我再给", "还没发")


def _clone_source() -> Path:
    if LONG_CHAT_CLONE.is_file():
        return LONG_CHAT_CLONE
    return drive.PRODUCTION_DB


def _photo_in_conversation(conversation: list[object]) -> bool:
    blob = "\n".join(str(line) for line in conversation)
    return "[" in blob and any(marker in blob for marker in PHOTO_MARKERS)


def _her_texts(visible: list[dict[str, Any]]) -> list[str]:
    texts: list[str] = []
    for item in visible:
        if item.get("kind") != "text":
            continue
        body = item.get("body")
        if isinstance(body, str) and body.strip():
            texts.append(body.strip())
    return texts


def _latest_inbound_authored(clone: Path) -> dict[str, Any] | None:
    conn = sqlite3.connect(f"file:{clone.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT trigger_ref, snapshot_id, authored_state_json, terminal_result_json,
                   updated_at
            FROM world_v2_character_interior_turns
            WHERE world_id = ? AND purpose = 'inbound_turn'
              AND authored_state_json IS NOT NULL
            ORDER BY updated_at DESC LIMIT 1
            """,
            (WORLD_ID,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    authored = json.loads(row["authored_state_json"] or "{}")
    snapshot = authored.get("snapshot") or {}
    materials = snapshot.get("materials") if isinstance(snapshot, dict) else {}
    materials = materials if isinstance(materials, dict) else {}
    recent = materials.get("recent_dialogue") or []
    conversation = None
    try:
        from companion_daemon.world_v2.character_interior.contracts import (
            InnerLifeSnapshot,
        )

        cleaned = {
            key: value
            for key, value in snapshot.items()
            if key not in {"recall_trace_json", "prefetch_trace_json"}
        }
        view = InnerLifeSnapshot.model_validate(cleaned).model_view()
        view_materials = view.get("materials") if isinstance(view, dict) else {}
        if isinstance(view_materials, dict):
            conversation = view_materials.get("conversation")
            recent = view_materials.get("recent_dialogue") or recent
    except Exception as exc:
        authored["_view_error"] = f"{type(exc).__name__}: {exc}"[:400]
    current_turn = []
    speakers: list[str] = []
    if isinstance(recent, list):
        for entry in recent:
            if not isinstance(entry, dict):
                continue
            speaker = str(entry.get("speaker") or "")
            speakers.append(speaker)
            reasons = entry.get("continuity_reasons") or ()
            if isinstance(reasons, list) and "current_turn" in reasons:
                current_turn.append(
                    {
                        "speaker": speaker,
                        "text": entry.get("text"),
                        "occurred_at": entry.get("occurred_at"),
                    }
                )
    return {
        "trigger_ref": row["trigger_ref"],
        "snapshot_id": row["snapshot_id"],
        "updated_at": row["updated_at"],
        "conversation": conversation or [],
        "conversation_count": len(conversation or []),
        "speakers": speakers,
        "current_turn": current_turn,
        "recent_dialogue": recent,
        "view_error": authored.get("_view_error"),
    }


def _contains_her_line(conversation: list[object], her_texts: list[str]) -> bool:
    if not her_texts:
        return False
    blob = "\n".join(str(line) for line in conversation)
    return any(text in blob for text in her_texts)


def _alternates(conversation: list[object]) -> bool:
    speakers: list[str] = []
    for line in conversation:
        text = str(line)
        if text.startswith("他"):
            speakers.append("counterpart")
        elif text.startswith("我"):
            speakers.append("companion")
    return "counterpart" in speakers and "companion" in speakers


async def run_chat(*, spent: float) -> dict[str, Any]:
    clone = OUTPUT / "chat.sqlite"
    source = _clone_source()
    drive.clone_ledger(source, clone)
    usage_from = drive.current_usage_id(clone)
    snapshots: list[dict[str, Any]] = []
    from companion_daemon.world_v2.character_interior import production as production_mod
    from companion_daemon.world_v2.character_interior import snapshot_compiler as compiler_mod

    original = compiler_mod.compile_inner_life_snapshot

    def wrapped(context, *, source_envelopes=None):
        snapshot = original(context, source_envelopes=source_envelopes)
        try:
            view = snapshot.model_view()
        except Exception as exc:
            view = {"error": f"{type(exc).__name__}: {exc}"}
        snapshots.append(view)
        return snapshot

    compiler_mod.compile_inner_life_snapshot = wrapped
    production_mod.compile_inner_life_snapshot = wrapped
    session = await drive.open_session(
        database=clone,
        output_dir=OUTPUT,
        enable_media=False,
    )
    turns: list[dict[str, Any]] = []
    previous_her: list[str] = []
    sticky_current: list[dict[str, Any]] = []
    seen_current: dict[str, int] = {}
    try:
        for index, text in enumerate(MESSAGES, start=1):
            cost = drive.cost_report(clone, since_id=usage_from)
            spent_now = float(cost["cost_cny"])
            if spent + spent_now >= COST_CAP_CNY:
                break
            before_snaps = len(snapshots)
            inbound = await session.inbound(text)
            live = snapshots[before_snaps] if len(snapshots) > before_snaps else {}
            materials = live.get("materials") if isinstance(live, dict) else {}
            if not isinstance(materials, dict):
                materials = {}
            conversation = list(materials.get("conversation") or [])
            stored = _latest_inbound_authored(clone)
            if not conversation:
                conversation = list((stored or {}).get("conversation") or [])
            her_now = _her_texts(inbound.get("visible") or [])
            current = list((stored or {}).get("current_turn") or [])
            for item in current:
                key = str(item.get("text") or "")
                seen_current[key] = seen_current.get(key, 0) + 1
            her_last_visible = (
                bool(previous_her) and _contains_her_line(conversation, previous_her)
            )
            blob = "\n".join(her_now)
            record = {
                "index": index,
                "him": text,
                "her_texts": her_now,
                "status": inbound.get("status"),
                "conversation": conversation,
                "conversation_count": len(conversation),
                "speakers": (stored or {}).get("speakers") or [],
                "current_turn": current,
                "her_last_line_visible": her_last_visible,
                "both_speakers": _alternates(conversation),
                "photo_in_conversation": _photo_in_conversation(conversation),
                "denied": [token for token in DENIALS if token in blob],
                "view_error": (stored or {}).get("view_error") or live.get("error"),
                "cost_cny": spent_now,
            }
            turns.append(record)
            (OUTPUT / "turns").mkdir(parents=True, exist_ok=True)
            (OUTPUT / "turns" / f"t{index:02d}.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            previous_her = her_now or previous_her
            if spent + spent_now >= COST_CAP_CNY:
                break
    finally:
        compiler_mod.compile_inner_life_snapshot = original
        production_mod.compile_inner_life_snapshot = original
        await session.close()
    cost = drive.cost_report(clone, since_id=usage_from)
    for text, count in seen_current.items():
        if count > 1:
            sticky_current.append({"text": text, "turns": count})
    after_first = turns[1:] if len(turns) > 1 else []
    her_visible_n = sum(1 for item in after_first if item.get("her_last_line_visible"))
    both_n = sum(1 for item in turns if item.get("both_speakers"))
    counts = [int(item.get("conversation_count") or 0) for item in turns]
    her_blob = "\n".join(
        line for item in turns for line in (item.get("her_texts") or [])
    )
    photo_n = sum(1 for item in turns if item.get("photo_in_conversation"))
    denied_turns = [item["index"] for item in turns if item.get("denied")]
    return {
        "clone": str(clone),
        "source": str(source),
        "turns_ran": len(turns),
        "spent_cny": round(float(cost["cost_cny"]), 4),
        "usage": cost,
        "her_last_line_visible_n": her_visible_n,
        "her_last_line_visible_of": len(after_first),
        "both_speakers_n": both_n,
        "photo_in_conversation_n": photo_n,
        "denied_turns": denied_turns,
        "conversation_counts": counts,
        "min_conversation_count": min(counts) if counts else 0,
        "sticky_current_turn": sticky_current,
        "recognized_sun": sun.recognized_sun([her_blob]),
        "remembered_mood_explain": sun.remembered_mood_explain([her_blob]),
        "reopened_old_account": sun.reopened_old_account([her_blob]),
        "turns": turns,
    }


async def run_sun_retest(*, spent: float) -> dict[str, Any]:
    clone = OUTPUT / "sun-retest.sqlite"
    drive.clone_ledger(drive.PRODUCTION_DB, clone)
    evidence = sun.compile_sun_dialogue(clone=clone)
    snapshot, _manifest, _trigger = sun.compile_sun_snapshot(evidence)
    import httpx
    from companion_daemon.config import Settings
    from companion_daemon.llm import DeepSeekChatModel

    settings = Settings()
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
    author = sun._compose_sun_author(model=model)
    speak: list[dict[str, Any]] = []
    spoken = 0
    index = 0
    local_spent = 0.0
    while spoken < N_SUN and index < N_SUN + 3 and spent + local_spent < COST_CAP_CNY:
        index += 1
        trial = await sun.one_speak_trial(
            snapshot=snapshot,
            sun=evidence["_sun"],
            cursor=evidence["cursor"],
            index=index,
            author=author,
            usages=usages,
        )
        speak.append(trial)
        local_spent += float(trial.get("cost_cny") or 0)
        if trial.get("her_texts"):
            spoken += 1
    serializable = {key: value for key, value in evidence.items() if not key.startswith("_")}
    return {
        "clone": str(clone),
        "spoken": spoken,
        "spent_cny": round(local_spent, 4),
        "recognized_sun_n": sum(1 for item in speak if item.get("recognized_sun")),
        "remembered_mood_n": sum(1 for item in speak if item.get("remembered_mood_explain")),
        "reopened_old_account_n": sum(1 for item in speak if item.get("reopened_old_account")),
        "dialogue": serializable,
        "trials": speak,
    }


def write_report(report: dict[str, Any]) -> None:
    chat = report.get("chat") or {}
    sun_retest = report.get("sun_retest") or {}
    turns = chat.get("turns") or []
    lines = [
        "# Conversation slice proof",
        "",
        f"Finished at {report['finished_at']}.",
        "",
        "## Budget",
        "",
        f"- cap: ¥{COST_CAP_CNY}",
        f"- chat: ¥{chat.get('spent_cny', 0)} / {chat.get('turns_ran', 0)} inbound turns",
        f"- sun retest: ¥{sun_retest.get('spent_cny', 0)} / {sun_retest.get('spoken', 0)} speaks",
        f"- total: ¥{report.get('spent_cny', 0)}",
        "",
        "## Slice checks",
        "",
        f"- her previous line visible on next turn: **{chat.get('her_last_line_visible_n')}/{chat.get('her_last_line_visible_of')}**",
        f"- both speakers present: **{chat.get('both_speakers_n')}/{chat.get('turns_ran')}**",
        f"- delivered photo in conversation: **{chat.get('photo_in_conversation_n')}/{chat.get('turns_ran')}**",
        f"- denial turns (还没翻/还没发/明天再给): `{chat.get('denied_turns')}`",
        f"- conversation counts: `{chat.get('conversation_counts')}`",
        f"- sticky `current_turn`: `{chat.get('sticky_current_turn')}`",
        f"- in-chat sun mention: {chat.get('recognized_sun')}",
        f"- in-chat mood explain: {chat.get('remembered_mood_explain')}",
        f"- in-chat 记岔: {chat.get('reopened_old_account')}",
        "",
        "## Sun inbound retest (separate clone)",
        "",
        f"- recognized sun: **{sun_retest.get('recognized_sun_n')}/{sun_retest.get('spoken')}**",
        f"- remembered mood explain: **{sun_retest.get('remembered_mood_n')}/{sun_retest.get('spoken')}**",
        f"- reopened 记岔: **{sun_retest.get('reopened_old_account_n')}/{sun_retest.get('spoken')}**",
        "",
        "## Her lines",
        "",
    ]
    for item in turns:
        her = item.get("her_texts") or ["(silent)"]
        lines.append(f"- T{item['index']:02d} 他：{item['him']}")
        for text in her:
            lines.append(f"  我：{text}")
        convo = item.get("conversation") or []
        lines.append(f"  slice ({item.get('conversation_count')}):")
        for line in convo:
            lines.append(f"  - {line}")
        lines.append("")
    (OUTPUT / "REPORT.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-speak", action="store_true")
    parser.add_argument("--skip-sun", action="store_true")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "turns").mkdir(parents=True, exist_ok=True)
    spent = 0.0
    chat: dict[str, Any] = {}
    sun_retest: dict[str, Any] = {}
    if not args.skip_speak:
        chat = await run_chat(spent=spent)
        spent += float(chat.get("spent_cny") or 0)
    if not args.skip_sun:
        sun_retest = await run_sun_retest(spent=spent)
        spent += float(sun_retest.get("spent_cny") or 0)
    report = {
        "finished_at": datetime.now(UTC).isoformat(),
        "spent_cny": round(spent, 4),
        "chat": chat,
        "sun_retest": sun_retest,
    }
    (OUTPUT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    write_report(report)
    print(json.dumps(
        {
            "spent_cny": report["spent_cny"],
            "turns_ran": chat.get("turns_ran"),
            "her_last_line_visible": f"{chat.get('her_last_line_visible_n')}/{chat.get('her_last_line_visible_of')}",
            "both_speakers": f"{chat.get('both_speakers_n')}/{chat.get('turns_ran')}",
            "photo_in_conversation": f"{chat.get('photo_in_conversation_n')}/{chat.get('turns_ran')}",
            "denied_turns": chat.get("denied_turns"),
            "conversation_counts": chat.get("conversation_counts"),
            "sticky_current_turn": chat.get("sticky_current_turn"),
            "sun": {
                "recognized": f"{sun_retest.get('recognized_sun_n')}/{sun_retest.get('spoken')}",
                "mood": f"{sun_retest.get('remembered_mood_n')}/{sun_retest.get('spoken')}",
                "reopened": f"{sun_retest.get('reopened_old_account_n')}/{sun_retest.get('spoken')}",
            },
            "report": str(OUTPUT / "REPORT.md"),
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    asyncio.run(main())
