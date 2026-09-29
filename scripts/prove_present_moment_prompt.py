#!/usr/bin/env python3
"""Clone-only: she can see whether this sitting is photographable.

Merges the present-prompt patch. Never writes ``data/``. Never talks to
8787 / NapCat. Image generation is stubbed. Budget cap ¥5.

Usage::

    .venv/bin/python scripts/prove_present_moment_prompt.py
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in __import__("sys").path:
    __import__("sys").path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in __import__("sys").path:
    __import__("sys").path.insert(0, str(REPO / "scripts"))

import drive_production_lanes as drive
import prove_conversation_slice as conv
import prove_media_conversation_ttl as ttl
import prove_present_moment_photo as present
import prove_stale_topics as sun

OUTPUT = (REPO / "output" / "present-moment-prompt").resolve()
SHANGHAI = ZoneInfo("Asia/Shanghai")
COST_CAP_CNY = 5.0
N_EACH = 3
N_SUN = 3
def promised_now_photo(texts: list[str]) -> bool:
    """True only if she commits to a photo of this sitting, not later."""

    blob = "\n".join(texts)
    refusals = (
        "拍不了",
        "现在拍不了",
        "没法拍",
        "没有在做",
        "手里没有",
        "现在没有",
        "在睡觉",
        "准备睡",
        "黑漆漆",
    )
    if any(token in blob for token in refusals):
        return False
    now_markers = ("现在拍", "马上拍", "这就拍", "这就发", "现在发一张")
    return any(token in blob for token in now_markers)
ACTIVE_SOURCE = present.ACTIVE_SOURCE
NO_ACTIVE_SOURCE = present.NO_ACTIVE_SOURCE
CONVERSATION_CLONE = REPO / "output" / "conversation-slice" / "chat.sqlite"
# conversation-slice / long-chat heads predate the affect-decay reducer
# bundle; live inbound uses the current production backup instead.
CONVERSATION_LIVE_SOURCE = drive.PRODUCTION_DB


def shareable_from_view(view: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(view, dict):
        return None
    materials = view.get("materials")
    if not isinstance(materials, dict):
        return None
    shareable = materials.get("moments_i_can_share")
    return shareable if isinstance(shareable, dict) else None


def conversation_from_view(view: dict[str, Any] | None) -> list[str]:
    if not isinstance(view, dict):
        return []
    materials = view.get("materials")
    if not isinstance(materials, dict):
        return []
    rows = materials.get("conversation")
    if not isinstance(rows, list):
        return []
    return [str(item) for item in rows]


def moment_visible(shareable: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(shareable, dict):
        return {
            "present": False,
            "photographable": None,
            "reason": None,
            "activity_kind": None,
            "available_count": None,
            "what_happened": [],
            "saw_what": False,
        }
    now = shareable.get("now")
    now = now if isinstance(now, dict) else {}
    items = shareable.get("items") if isinstance(shareable.get("items"), list) else []
    whats = [
        str(item.get("what_happened"))
        for item in items
        if isinstance(item, dict) and isinstance(item.get("what_happened"), str)
        and item.get("what_happened")
    ]
    kind = now.get("activity_kind")
    return {
        "present": True,
        "photographable": now.get("photographable"),
        "reason": now.get("reason"),
        "activity_kind": kind if isinstance(kind, str) else None,
        "available_count": shareable.get("available_count"),
        "what_happened": whats,
        "saw_what": bool(kind) or bool(whats),
    }


def inbound_expression(database: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    try:
        row = conn.execute(
            """
            SELECT terminal_result_json FROM world_v2_character_interior_turns
            WHERE purpose = 'inbound_turn'
            ORDER BY updated_at DESC LIMIT 1
            """
        ).fetchone()
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()
    if not row or not row[0]:
        return {}
    try:
        payload = json.loads(row[0])
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    expression = payload.get("expression")
    root = expression.get("payload") if isinstance(expression, dict) else None
    if not isinstance(root, dict):
        root = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
    media = root.get("media_request") if isinstance(root, dict) else None
    photo = root.get("photo") if isinstance(root, dict) else None
    draft = root.get("expression_draft") if isinstance(root, dict) else None
    if isinstance(draft, dict):
        media = media or draft.get("media_request")
        photo = photo if photo is not None else draft.get("photo")
    chose_send = media in {
        "consider_available_candidate",
        True,
        "true",
    } or photo is True
    return {
        "media_request": media,
        "photo": photo,
        "chose_send": bool(chose_send),
    }


async def run_ask(
    *,
    source: Path,
    dest: Path,
    index: int,
    group: str,
) -> dict[str, Any]:
    drive.clone_ledger(source, dest)
    before = present.usage_cost(dest)
    session = await drive.open_session(
        database=dest, output_dir=OUTPUT, enable_media=True
    )
    snapshots: list[dict[str, Any]] = []
    original, compiler_mod, production_mod = ttl.capture_snapshots(snapshots)
    try:
        present.stub_image_spend(present.application_of(session))
        inspect = present.inspect_now(session)
        inbound = await present.inbound_exact(session, present.ASK_NOW)
        shareable = shareable_from_view(snapshots[-1] if snapshots else None)
        visible = moment_visible(shareable)
        she = present.visible_text(list(inbound.get("visible") or []))
        choice = inbound_expression(dest)
        cost = present.usage_cost(dest)
        promised = promised_now_photo(she)
        return {
            "index": index,
            "group": group,
            "clone": str(dest),
            "inspect": inspect,
            "visible": visible,
            "she": she,
            "choice": choice,
            "promised_now_photo": promised,
            "inbound_status": inbound.get("status"),
            "cost_cny": round(cost["cost_cny"] - before["cost_cny"], 4),
        }
    finally:
        compiler_mod.compile_inner_life_snapshot = original
        production_mod.compile_inner_life_snapshot = original
        await session.close()


def inspect_stored_conversation(clone: Path) -> dict[str, Any]:
    from datetime import datetime as _dt

    from companion_daemon.world_v2.character_interior.contracts import (
        _rendered_conversation,
    )

    stored = conv._latest_inbound_authored(clone)
    conversation: list[str] = []
    view_error = (stored or {}).get("view_error")
    recent = (stored or {}).get("recent_dialogue") or []
    authored_mats: dict[str, Any] = {}
    conn = sqlite3.connect(f"file:{clone.resolve()}?mode=ro", uri=True)
    try:
        row = conn.execute(
            """
            SELECT authored_state_json FROM world_v2_character_interior_turns
            WHERE world_id = ? AND purpose = 'inbound_turn'
              AND authored_state_json IS NOT NULL
            ORDER BY updated_at DESC LIMIT 1
            """,
            (drive.WORLD_ID,),
        ).fetchone()
    finally:
        conn.close()
    clock = None
    if row and row[0]:
        authored = json.loads(row[0])
        snap = authored.get("snapshot") if isinstance(authored, dict) else {}
        snap = snap if isinstance(snap, dict) else {}
        raw = snap.get("materials_json")
        mats = json.loads(raw) if isinstance(raw, str) else snap.get("materials")
        if isinstance(mats, dict):
            authored_mats = mats
            recent = mats.get("recent_dialogue") or recent
            lt = snap.get("logical_time")
            if isinstance(lt, str):
                try:
                    clock = _dt.fromisoformat(lt.replace("Z", "+00:00"))
                except ValueError:
                    clock = None
            conversation = [
                str(line)
                for line in (_rendered_conversation(mats, clock) or [])
            ]
            view_error = None
    if not conversation:
        conversation = list((stored or {}).get("conversation") or [])
    her_lines = [str(line) for line in conversation if str(line).startswith("我")]
    return {
        "clone": str(clone),
        "conversation_count": len(conversation),
        "photo_in_conversation": conv._photo_in_conversation(conversation),
        "her_lines_n": len(her_lines),
        "last_her": her_lines[-1] if her_lines else None,
        "sample": conversation[-6:],
        "view_error": view_error,
        "photos_i_shared": bool(authored_mats.get("photos_i_shared")),
    }


async def run_conversation_inbound(*, dest: Path) -> dict[str, Any]:
    drive.clone_ledger(CONVERSATION_LIVE_SOURCE, dest)
    before = present.usage_cost(dest)
    previous = inspect_stored_conversation(dest)
    previous_her = []
    last = previous.get("last_her")
    if isinstance(last, str) and last:
        # Strip the "我（…）：" prefix for substring match.
        body = last.split("：", 1)[-1].strip()
        if body:
            previous_her.append(body)
    snapshots: list[dict[str, Any]] = []
    original, compiler_mod, production_mod = ttl.capture_snapshots(snapshots)
    session = await drive.open_session(
        database=dest, output_dir=OUTPUT, enable_media=False
    )
    try:
        inbound = await present.inbound_exact(session, "你刚才那句我看见了")
        live = snapshots[-1] if snapshots else {}
        conversation = conversation_from_view(live)
        she = present.visible_text(list(inbound.get("visible") or []))
        blob = "\n".join(she)
        cost = present.usage_cost(dest)
        return {
            "clone": str(dest),
            "previous": previous,
            "her_last_line_visible": conv._contains_her_line(conversation, previous_her),
            "photo_in_conversation": conv._photo_in_conversation(conversation),
            "conversation_count": len(conversation),
            "conversation_tail": conversation[-8:],
            "she": she,
            "recognized_sun": sun.recognized_sun(she),
            "reopened_old_account": sun.reopened_old_account(she),
            "denied": [token for token in conv.DENIALS if token in blob],
            "inbound_status": inbound.get("status"),
            "cost_cny": round(cost["cost_cny"] - before["cost_cny"], 4),
        }
    finally:
        compiler_mod.compile_inner_life_snapshot = original
        production_mod.compile_inner_life_snapshot = original
        await session.close()


async def run_sun(*, dest: Path, spent: float) -> dict[str, Any]:
    drive.clone_ledger(drive.PRODUCTION_DB, dest)
    evidence = sun.compile_sun_dialogue(clone=dest)
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
    while spoken < N_SUN and index < N_SUN + 2 and spent + local_spent < COST_CAP_CNY:
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
    return {
        "clone": str(dest),
        "spoken": spoken,
        "spent_cny": round(local_spent, 4),
        "recognized_sun_n": sum(1 for item in speak if item.get("recognized_sun")),
        "remembered_mood_n": sum(1 for item in speak if item.get("remembered_mood_explain")),
        "reopened_old_account_n": sum(1 for item in speak if item.get("reopened_old_account")),
        "trials": [
            {
                "index": item.get("index"),
                "her_texts": item.get("her_texts"),
                "recognized_sun": item.get("recognized_sun"),
                "reopened_old_account": item.get("reopened_old_account"),
                "cost_cny": item.get("cost_cny"),
            }
            for item in speak
        ],
    }


def write_report(report: dict[str, Any]) -> None:
    lines = [
        "# Present-prompt: 此刻能不能拍",
        "",
        f"Finished at {report.get('finished_at')}.",
        "",
        f"- cost_cny: `{report.get('cost_cny')}` / cap ¥{COST_CAP_CNY}",
        f"- active_pass: `{report.get('active_pass')}`",
        f"- no_active_pass: `{report.get('no_active_pass')}`",
        f"- conversation_pass: `{report.get('conversation_pass')}`",
        f"- sun_pass: `{report.get('sun_pass')}`",
        "",
        "## Active activity — she can see what is photographable now",
        "",
    ]
    for item in report.get("active") or []:
        visible = item.get("visible") or {}
        choice = item.get("choice") or {}
        lines.append(f"- `{Path(str(item.get('clone') or '')).name}`")
        lines.append(
            f"  - photographable=`{visible.get('photographable')}` "
            f"reason=`{visible.get('reason')}` "
            f"activity_kind=`{visible.get('activity_kind')}` "
            f"available_count=`{visible.get('available_count')}`"
        )
        if visible.get("what_happened"):
            lines.append(f"  - what_happened: {visible.get('what_happened')}")
        send = "发" if choice.get("chose_send") else "不发"
        lines.append(
            f"  - 决定：{send} media_request=`{choice.get('media_request')}`"
        )
        for quote in item.get("she") or []:
            lines.append(f"  - 她：{quote}")
        lines.append("")
    lines.extend(["## No active activity — she can see there is nothing to shoot", ""])
    for item in report.get("no_active") or []:
        visible = item.get("visible") or {}
        lines.append(f"- `{Path(str(item.get('clone') or '')).name}`")
        lines.append(
            f"  - photographable=`{visible.get('photographable')}` "
            f"reason=`{visible.get('reason')}` "
            f"available_count=`{visible.get('available_count')}`"
        )
        lines.append(f"  - promised_now_photo: `{item.get('promised_now_photo')}`")
        for quote in item.get("she") or []:
            lines.append(f"  - 她：{quote}")
        lines.append("")
    conversation = report.get("conversation") or {}
    stored = conversation.get("stored") or {}
    live = conversation.get("inbound") or {}
    lines.extend(
        [
            "## Conversation retest",
            "",
            f"- stored photo in conversation: `{stored.get('photo_in_conversation')}`",
            f"- stored last her: {stored.get('last_her')}",
            f"- inbound her last line visible: `{live.get('her_last_line_visible')}`",
            f"- inbound photo in conversation: `{live.get('photo_in_conversation')}`",
            f"- inbound 记岔: `{live.get('reopened_old_account')}`",
            "",
        ]
    )
    for quote in live.get("she") or []:
        lines.append(f"- 她：{quote}")
    lines.append("")
    sun_retest = report.get("sun") or {}
    lines.extend(
        [
            "## Sun retest",
            "",
            f"- recognized: **{sun_retest.get('recognized_sun_n')}/{sun_retest.get('spoken')}**",
            f"- 记岔: **{sun_retest.get('reopened_old_account_n')}/{sun_retest.get('spoken')}**",
            "",
        ]
    )
    for trial in sun_retest.get("trials") or []:
        lines.append(f"- T{trial.get('index')} recognized=`{trial.get('recognized_sun')}`")
        for quote in trial.get("her_texts") or []:
            lines.append(f"  - 她：{quote}")
    lines.append("")
    (OUTPUT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def main() -> dict[str, Any]:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase",
        choices=("active", "no-active", "conversation", "sun", "remainder", "all"),
        default="all",
    )
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    spent = 0.0
    active: list[dict[str, Any]] = []
    no_active: list[dict[str, Any]] = []
    conversation: dict[str, Any] = {}
    sun_retest: dict[str, Any] = {}

    if args.phase in {"active", "all"}:
        for index in range(1, N_EACH + 1):
            if spent >= COST_CAP_CNY:
                break
            rec = await run_ask(
                source=ACTIVE_SOURCE,
                dest=OUTPUT / f"active-{index}.sqlite",
                index=index,
                group="active",
            )
            active.append(rec)
            spent += float(rec.get("cost_cny") or 0)
            print(
                json.dumps(
                    {
                        "group": "active",
                        "index": index,
                        "visible": rec.get("visible"),
                        "choice": rec.get("choice"),
                        "she": rec.get("she"),
                        "cost_cny": rec.get("cost_cny"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    if args.phase in {"no-active", "all"}:
        for index in range(1, N_EACH + 1):
            if spent >= COST_CAP_CNY:
                break
            rec = await run_ask(
                source=NO_ACTIVE_SOURCE,
                dest=OUTPUT / f"no-active-{index}.sqlite",
                index=index,
                group="no-active",
            )
            no_active.append(rec)
            spent += float(rec.get("cost_cny") or 0)
            print(
                json.dumps(
                    {
                        "group": "no-active",
                        "index": index,
                        "visible": rec.get("visible"),
                        "promised": rec.get("promised_now_photo"),
                        "she": rec.get("she"),
                        "cost_cny": rec.get("cost_cny"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    if args.phase == "remainder":
        rec = await run_ask(
            source=ACTIVE_SOURCE,
            dest=OUTPUT / "active-4.sqlite",
            index=4,
            group="active",
        )
        active.append(rec)
        spent += float(rec.get("cost_cny") or 0)
        print(json.dumps({"group": "active", "index": 4, "visible": rec.get("visible"), "choice": rec.get("choice"), "she": rec.get("she"), "cost_cny": rec.get("cost_cny")}, ensure_ascii=False), flush=True)
        rec = await run_ask(
            source=NO_ACTIVE_SOURCE,
            dest=OUTPUT / "no-active-4.sqlite",
            index=4,
            group="no-active",
        )
        no_active.append(rec)
        spent += float(rec.get("cost_cny") or 0)
        print(json.dumps({"group": "no-active", "index": 4, "visible": rec.get("visible"), "promised": rec.get("promised_now_photo"), "she": rec.get("she"), "cost_cny": rec.get("cost_cny")}, ensure_ascii=False), flush=True)
        stored = inspect_stored_conversation(CONVERSATION_CLONE)
        inbound = await run_conversation_inbound(
            dest=OUTPUT / "conversation-retest.sqlite"
        )
        spent += float(inbound.get("cost_cny") or 0)
        conversation = {"stored": stored, "inbound": inbound}
        print(json.dumps({"group": "conversation", "her_last_line_visible": inbound.get("her_last_line_visible"), "photo_in_conversation": inbound.get("photo_in_conversation"), "she": inbound.get("she"), "cost_cny": inbound.get("cost_cny")}, ensure_ascii=False), flush=True)
        sun_retest = await run_sun(dest=OUTPUT / "sun-retest.sqlite", spent=spent)
        spent += float(sun_retest.get("spent_cny") or 0)
        print(json.dumps({"group": "sun", "recognized": f"{sun_retest.get('recognized_sun_n')}/{sun_retest.get('spoken')}", "reopened": f"{sun_retest.get('reopened_old_account_n')}/{sun_retest.get('spoken')}", "cost_cny": sun_retest.get("spent_cny")}, ensure_ascii=False), flush=True)

    if args.phase in {"conversation", "all"} and spent < COST_CAP_CNY:
        stored = inspect_stored_conversation(CONVERSATION_CLONE)
        inbound = await run_conversation_inbound(
            dest=OUTPUT / "conversation-retest.sqlite"
        )
        spent += float(inbound.get("cost_cny") or 0)
        conversation = {"stored": stored, "inbound": inbound}
        print(
            json.dumps(
                {
                    "group": "conversation",
                    "her_last_line_visible": inbound.get("her_last_line_visible"),
                    "photo_in_conversation": inbound.get("photo_in_conversation"),
                    "she": inbound.get("she"),
                    "cost_cny": inbound.get("cost_cny"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    if args.phase in {"sun", "all"} and spent < COST_CAP_CNY:
        sun_retest = await run_sun(dest=OUTPUT / "sun-retest.sqlite", spent=spent)
        spent += float(sun_retest.get("spent_cny") or 0)
        print(
            json.dumps(
                {
                    "group": "sun",
                    "recognized": f"{sun_retest.get('recognized_sun_n')}/{sun_retest.get('spoken')}",
                    "reopened": f"{sun_retest.get('reopened_old_account_n')}/{sun_retest.get('spoken')}",
                    "cost_cny": sun_retest.get("spent_cny"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    active_pass = (
        len(active) >= N_EACH
        and all(
            (item.get("visible") or {}).get("present")
            and (item.get("visible") or {}).get("photographable") is True
            and (item.get("visible") or {}).get("saw_what")
            and item.get("inbound_status") not in {None, "error", "failed"}
            for item in active
        )
    )
    no_active_pass = (
        len(no_active) >= N_EACH
        and all(
            (item.get("visible") or {}).get("present")
            and (item.get("visible") or {}).get("photographable") is False
            and item.get("promised_now_photo") is False
            and item.get("inbound_status") not in {None, "error", "failed"}
            for item in no_active
        )
    )
    inbound = (conversation.get("inbound") or {}) if conversation else {}
    stored = (conversation.get("stored") or {}) if conversation else {}
    conversation_pass = bool(
        stored.get("photo_in_conversation")
        and inbound.get("her_last_line_visible")
        and inbound.get("photo_in_conversation")
        and not inbound.get("reopened_old_account")
    )
    sun_pass = bool(
        sun_retest
        and int(sun_retest.get("spoken") or 0) >= N_SUN
        and int(sun_retest.get("recognized_sun_n") or 0)
        == int(sun_retest.get("spoken") or 0)
        and int(sun_retest.get("reopened_old_account_n") or 0) == 0
    )
    report = {
        "finished_at": datetime.now(SHANGHAI).isoformat(),
        "cost_cny": round(spent, 4),
        "active": active,
        "no_active": no_active,
        "conversation": conversation,
        "sun": sun_retest,
        "active_pass": active_pass,
        "no_active_pass": no_active_pass,
        "conversation_pass": conversation_pass,
        "sun_pass": sun_pass,
        "all_pass": active_pass and no_active_pass and conversation_pass and sun_pass,
    }
    (OUTPUT / "proof.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    write_report(report)
    print(json.dumps(
        {
            "all_pass": report["all_pass"],
            "active_pass": active_pass,
            "no_active_pass": no_active_pass,
            "conversation_pass": conversation_pass,
            "sun_pass": sun_pass,
            "cost_cny": report["cost_cny"],
            "report": str(OUTPUT / "REPORT.md"),
        },
        ensure_ascii=False,
        indent=2,
    ))
    return report


if __name__ == "__main__":
    asyncio.run(main())
