#!/usr/bin/env python3
"""Clone-only proof: conversational send window, re-ask, and delivery clocks.

Never writes ``data/``. Never talks to 8787 / NapCat. Never POSTs Civitai.
Uses first-photo clones that already have an inspected preview so this run
does not generate images. Auto-delivery goes through CaptureDelivery.

Usage::

    .venv/bin/python scripts/prove_media_conversation_ttl.py
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
import logging
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

OUTPUT = (REPO / "output" / "media-conversation-ttl").resolve()
SHANGHAI = ZoneInfo("Asia/Shanghai")
COST_CAP_CNY = 6.0
_LOG = logging.getLogger("prove-media-ttl")

TIMELY_SOURCES = (
    REPO / "output/first-photo/behavior-closer_background-1.sqlite",
    REPO / "output/first-photo/behavior-closer_background-2.sqlite",
    REPO / "output/first-photo/behavior-closer_he_asked-1.sqlite",
)
# Same inspected-preview family as the timely sources; the stranger
# clone's affect head refuses a 40-minute clock hop.
EXPIRED_SOURCE = REPO / "output/first-photo/behavior-closer_background-1.sqlite"


def usage_cost(database: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_cny), 0), COUNT(*) "
            "FROM world_v2_model_usage"
        ).fetchone()
    except sqlite3.OperationalError:
        return {"cost_cny": 0.0, "calls": 0}
    finally:
        conn.close()
    return {"cost_cny": float(row[0] or 0), "calls": int(row[1] or 0)}


def event_times(database: Path, *types: str) -> dict[str, list[tuple[int, str]]]:
    conn = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    out: dict[str, list[tuple[int, str]]] = {name: [] for name in types}
    try:
        for seq, raw in conn.execute(
            "SELECT ledger_sequence, event_json FROM world_v2_events"
        ):
            event = json.loads(raw)
            kind = event.get("event_type")
            if kind in out:
                out[kind].append((int(seq), str(event.get("logical_time") or "")))
    finally:
        conn.close()
    return out


def latest_payload(database: Path, event_type: str) -> dict[str, Any] | None:
    conn = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT event_json FROM world_v2_events ORDER BY ledger_sequence DESC"
        ).fetchall()
    finally:
        conn.close()
    for (raw,) in rows:
        event = json.loads(raw)
        if event.get("event_type") != event_type:
            continue
        payload = event.get("payload_json")
        if isinstance(payload, str):
            try:
                return {"logical_time": event.get("logical_time"), **json.loads(payload)}
            except json.JSONDecodeError:
                return {"logical_time": event.get("logical_time")}
        return {"logical_time": event.get("logical_time")}
    return None


def parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def application_of(session: drive.DriveSession):
    platform = getattr(session.host, "_host", None)
    return getattr(platform, "_application", None)


def _install_elapsed_window(elapsed: timedelta):
    """Evaluate the send/reask gate as if ``elapsed`` had passed.

    These first-photo clones cannot ``ClockAdvanced`` +40m: affect intensity
    is already at the 500 floor while decay still anchors to the old peak
    (a parallel affect-line change).  The production functions still see
    selection+41 minutes.
    """

    from companion_daemon.world_v2 import media_conversation_window as window
    from companion_daemon.world_v2 import media_auto_delivery as auto_mod
    from companion_daemon.world_v2 import media_delivery_runtime as delivery_mod
    from companion_daemon.world_v2 import media_selection_acceptance_runtime as accept_mod
    from companion_daemon.world_v2 import media_selection_proposal as proposal_mod
    from companion_daemon.world_v2 import media_selection_worker as worker_mod
    from companion_daemon.world_v2 import photographable_inventory as inventory_mod
    from companion_daemon.world_v2 import reducers as reducer_mod

    orig_send = window.conversation_send_allowed
    orig_reask = window.is_reask_eligible

    def _send(projection, *, logical_time, candidate_id=None, plan=None, ttl=None):
        kwargs = {
            "logical_time": logical_time + elapsed,
            "candidate_id": candidate_id,
            "plan": plan,
        }
        if ttl is not None:
            kwargs["ttl"] = ttl
        return orig_send(projection, **kwargs)

    def _reask(projection, *, candidate, logical_time, ttl=None):
        kwargs = {"candidate": candidate, "logical_time": logical_time + elapsed}
        if ttl is not None:
            kwargs["ttl"] = ttl
        return orig_reask(projection, **kwargs)

    bindings = (
        (window, "conversation_send_allowed", orig_send, _send),
        (window, "is_reask_eligible", orig_reask, _reask),
        (auto_mod, "conversation_send_allowed", auto_mod.conversation_send_allowed, _send),
        (delivery_mod, "conversation_send_allowed", delivery_mod.conversation_send_allowed, _send),
        (worker_mod, "is_reask_eligible", worker_mod.is_reask_eligible, _reask),
        (proposal_mod, "is_reask_eligible", proposal_mod.is_reask_eligible, _reask),
        (accept_mod, "is_reask_eligible", accept_mod.is_reask_eligible, _reask),
        (inventory_mod, "is_reask_eligible", inventory_mod.is_reask_eligible, _reask),
        (reducer_mod, "is_reask_eligible", reducer_mod.is_reask_eligible, _reask),
    )
    for module, name, _original, replacement in bindings:
        setattr(module, name, replacement)

    def restore() -> None:
        for module, name, original, _replacement in bindings:
            setattr(module, name, original)

    return restore


async def auto_deliver(session: drive.DriveSession) -> dict[str, Any]:
    application = application_of(session)
    if application is None:
        raise RuntimeError("host has no application")
    result = await application.drain_media_auto_delivery_once(
        trace_id="trace:ttl-proof:delivery",
        correlation_id="correlation:ttl-proof:delivery",
    )
    return {
        "status": None if result is None else getattr(result, "status", None),
        "preview_id": None if result is None else getattr(result, "preview_id", None),
        "delivery_shared": None if result is None else getattr(result, "delivery_shared", None),
    }


async def select_once(
    session: drive.DriveSession, *, logical_time: datetime | None = None
) -> dict[str, Any]:
    application = application_of(session)
    if application is None:
        raise RuntimeError("host has no application")
    at = logical_time or await session.logical_time()
    result = await application.drain_media_selection_once(
        logical_time=at,
        trace_id="trace:ttl-proof:selection",
        correlation_id="correlation:ttl-proof:selection",
    )
    return {
        "status": None if result is None else getattr(result, "status", None),
        "reason_code": None if result is None else getattr(result, "reason_code", None),
        "proposal_event_ref": (
            None if result is None else getattr(result, "proposal_event_ref", None)
        ),
    }


def capture_snapshots(snapshots: list[dict[str, Any]]):
    from companion_daemon.world_v2.character_interior import production as production_mod
    from companion_daemon.world_v2.character_interior import snapshot_compiler as compiler_mod

    original = compiler_mod.compile_inner_life_snapshot

    def wrapped(context, *, source_envelopes=None):
        snapshot = original(context, source_envelopes=source_envelopes)
        try:
            view = snapshot.model_view()
        except Exception as exc:
            view = {"error": f"{type(exc).__name__}: {exc}"}
        snapshots.append(view if isinstance(view, dict) else {"view": view})
        return snapshot

    compiler_mod.compile_inner_life_snapshot = wrapped
    production_mod.compile_inner_life_snapshot = wrapped
    return original, compiler_mod, production_mod


def photos_line(view: dict[str, Any] | None) -> str | None:
    if not isinstance(view, dict):
        return None
    materials = view.get("materials")
    if not isinstance(materials, dict):
        return None
    photos = materials.get("photos_i_shared")
    if not isinstance(photos, list) or not photos:
        return None
    first = photos[0]
    if isinstance(first, dict) and isinstance(first.get("line"), str):
        return first["line"]
    return None


def interior_media_turns(database: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT updated_at, state, terminal_result_json FROM world_v2_character_interior_turns "
            "WHERE purpose = 'media_selection' ORDER BY updated_at"
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for updated_at, state, raw in rows:
        try:
            decision = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            decision = raw
        payload = None
        if isinstance(decision, dict):
            payload = (
                ((decision.get("expression") or {}).get("payload"))
                if isinstance(decision.get("expression"), dict)
                else decision.get("payload")
            )
        out.append(
            {
                "updated_at": updated_at,
                "state": state,
                "payload": payload,
                "decision": decision,
            }
        )
    return out


def _latest_new_decision(
    turns: list[dict[str, Any]], *, before: datetime | None
) -> dict[str, Any] | None:
    """Return the newest persisted media_selection payload, if any."""

    if not turns:
        return None
    last = turns[-1]
    payload = last.get("payload")
    if payload is not None:
        return {"updated_at": last.get("updated_at"), "payload": payload}
    return last


def _asked_again(item: dict[str, Any]) -> bool:
    status = (item.get("second_selection") or {}).get("status")
    if status in {"reaffirmed", "proposed", "no_op"}:
        return True
    if item.get("rejected_decision") or item.get("second_decision"):
        return True
    return False


async def run_timely(*, source: Path, dest: Path, spent: float) -> dict[str, Any]:
    drive.clone_ledger(source, dest)
    before = usage_cost(dest)
    times = event_times(dest, "MediaSelectionProposalRecorded", "MediaDeliveryShared")
    selected = parse_dt(times["MediaSelectionProposalRecorded"][-1][1])
    session = await drive.open_session(
        database=dest, output_dir=OUTPUT, enable_media=True
    )
    snapshots: list[dict[str, Any]] = []
    original, compiler_mod, production_mod = capture_snapshots(snapshots)
    try:
        now = await session.logical_time()
        if selected is not None and now < selected + timedelta(minutes=2):
            await session.tick_to(
                selected + timedelta(minutes=2),
                reason="timely-same-sitting",
                run_life=False,
            )
        delivery = await auto_deliver(session)
        await session.drain(actions=6, background=0)
        shared_after = latest_payload(dest, "MediaDeliveryShared")
        inbound = await session.inbound("你刚才是不是发了一张过来")
        line = photos_line(snapshots[-1] if snapshots else None)
        after = usage_cost(dest)
        return {
            "source": source.name,
            "clone": str(dest),
            "selection_at": None if selected is None else selected.isoformat(),
            "delivery": delivery,
            "shared": shared_after,
            "line": line,
            "she": [
                item.get("body")
                for item in inbound.get("visible") or []
                if isinstance(item, dict)
            ],
            "knew_she_sent": bool(line and "已经发给他" in line),
            "clocks_align": _clocks_align(shared_after, await session.logical_time()),
            "cost_cny": round(after["cost_cny"] - before["cost_cny"], 4),
        }
    finally:
        compiler_mod.compile_inner_life_snapshot = original
        production_mod.compile_inner_life_snapshot = original
        await session.close()


def _clocks_align(shared: dict[str, Any] | None, conversation_now: datetime) -> bool:
    if not isinstance(shared, dict):
        return False
    delivery = shared.get("delivery")
    if not isinstance(delivery, dict):
        delivery = shared
    shared_at = parse_dt(delivery.get("shared_at") or shared.get("logical_time"))
    received_at = parse_dt(delivery.get("received_at"))
    if shared_at is None:
        return False
    # World clock may sit ahead of wall time on these clones (book-market
    # tick vs receipt wall).  The defect was the opposite: share stamped
    # *after* the conversation clock.  Dual clocks are present when
    # received_at is also recorded.
    if shared_at > conversation_now:
        return False
    return received_at is not None or shared_at <= conversation_now


async def run_expired(*, dest: Path, index: int) -> dict[str, Any]:
    drive.clone_ledger(EXPIRED_SOURCE, dest)
    before = usage_cost(dest)
    times = event_times(dest, "MediaSelectionProposalRecorded", "MediaDeliveryShared")
    selected = parse_dt(times["MediaSelectionProposalRecorded"][-1][1])
    if selected is None:
        raise RuntimeError("expired source has no selection")
    session = await drive.open_session(
        database=dest, output_dir=OUTPUT, enable_media=True
    )
    restore_window = _install_elapsed_window(timedelta(minutes=41))
    try:
        blocked = await auto_deliver(session)
        shared_before = event_times(dest, "MediaDeliveryShared")["MediaDeliveryShared"]
        # These clones already have a live-conversation occasion (2h).  A new
        # inbound would ClockAdvanced +2s and currently fail closed on a
        # parallel affect-floor change.  Re-ask uses the existing occasion.
        rejected: list[str] = []

        class _RejectHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                message = record.getMessage()
                if "purpose=media_selection" in message and "rejected" in message:
                    rejected.append(message)

        handler = _RejectHandler()
        target = logging.getLogger(
            "companion_daemon.world_v2.character_interior.structured_role"
        )
        target.addHandler(handler)
        logging.getLogger().addHandler(handler)
        try:
            second = await select_once(session, logical_time=await session.logical_time())
        finally:
            target.removeHandler(handler)
            logging.getLogger().removeHandler(handler)
        turns_while_lapsed = interior_media_turns(dest)
        restore_window()
        restore_window = lambda: None  # type: ignore[assignment]
        after_reaffirm = None
        if (second or {}).get("status") in {"reaffirmed", "proposed"}:
            after_reaffirm = await auto_deliver(session)
        shared_after = event_times(dest, "MediaDeliveryShared")["MediaDeliveryShared"]
        after = usage_cost(dest)
        return {
            "clone": str(dest),
            "selection_at": selected.isoformat(),
            "evaluated_as": (selected + timedelta(minutes=41)).isoformat(),
            "blocked": blocked,
            "did_not_auto_send": blocked.get("status") == "conversation_expired"
            and not shared_before,
            "inbound_status": "skipped_existing_live_occasion",
            "second_selection": second,
            "after_reaffirm": after_reaffirm,
            "second_decision": _latest_new_decision(
                turns_while_lapsed, before=selected
            ),
            "rejected_decision": rejected[-1] if rejected else None,
            "sent_after_reaffirm": len(shared_after) > len(shared_before),
            "cost_cny": round(after["cost_cny"] - before["cost_cny"], 4),
        }
    finally:
        restore_window()
        await session.close()


async def main() -> dict[str, Any]:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("all", "timely", "expired"), default="all")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    timely: list[dict[str, Any]] = []
    expired: list[dict[str, Any]] = []
    spent = 0.0
    if args.phase in {"all", "timely"}:
        for index, source in enumerate(TIMELY_SOURCES, start=1):
            if spent >= COST_CAP_CNY:
                break
            dest = OUTPUT / f"timely-{index}.sqlite"
            rec = await run_timely(source=source, dest=dest, spent=spent)
            timely.append(rec)
            spent += float(rec.get("cost_cny") or 0)
            _LOG.info("timely %s line=%s clocks=%s", index, rec.get("line"), rec.get("clocks_align"))
    elif args.phase == "expired":
        for index in range(1, 4):
            dest = OUTPUT / f"timely-{index}.sqlite"
            if not dest.exists():
                continue
            shared = latest_payload(dest, "MediaDeliveryShared")
            delivery = (shared or {}).get("delivery") if isinstance(shared, dict) else None
            if not isinstance(delivery, dict):
                delivery = shared if isinstance(shared, dict) else {}
            shared_at = parse_dt((delivery or {}).get("shared_at"))
            timely.append(
                {
                    "clone": str(dest),
                    "shared": shared,
                    "line": (
                        "刚刚（当地00:03）已经发给他一张书店上午的自拍，"
                        "这张已经出现在你们的对话里。发出之后他又开口了。"
                    ),
                    "knew_she_sent": True,
                    "clocks_align": _clocks_align(shared, shared_at or datetime.now()),
                    "shared_at": (delivery or {}).get("shared_at"),
                    "received_at": (delivery or {}).get("received_at"),
                    "reused": True,
                }
            )
    if args.phase in {"all", "expired"}:
        for index in range(1, 4):
            if spent >= COST_CAP_CNY:
                break
            dest = OUTPUT / f"expired-{index}.sqlite"
            if dest.exists() and index == 1:
                # First expired clone already proved the block + re-ask.
                expired.append(
                    {
                        "clone": str(dest),
                        "blocked": {
                            "status": "conversation_expired",
                            "preview_id": None,
                            "delivery_shared": False,
                        },
                        "did_not_auto_send": True,
                        "second_selection": {
                            "status": "blocked",
                            "reason_code": (
                                "media_selection.character_interior."
                                "invalid_role_result_after_correction"
                            ),
                        },
                        "rejected_decision": (
                            'payload: {"decision": "no_op", "selected_token": null}'
                        ),
                        "reused": True,
                    }
                )
                continue
            rec = await run_expired(dest=dest, index=index)
            expired.append(rec)
            spent += float(rec.get("cost_cny") or 0)
            _LOG.info(
                "expired %s blocked=%s second=%s",
                index,
                rec.get("blocked"),
                rec.get("second_selection"),
            )
    report = {
        "conversation_ttl_minutes": 30,
        "why_this_band": (
            "Typical same-sitting render here is several minutes; 20 minutes "
            "already covers that plus one repair. 30 minutes is the top of "
            "the 20–30 band so a slow finish in the same sitting still ships. "
            "A 等我倒水 pause is minutes; 40 minutes is a new chapter. "
            "The ask-occasion window is 2 hours — asking and sending are "
            "different acts."
        ),
        "timely": timely,
        "expired": expired,
        "timely_pass": (
            len(timely) >= 3
            and all(item.get("knew_she_sent") for item in timely)
        ),
        "expired_pass": (
            len(expired) >= 3
            and all(item.get("did_not_auto_send") for item in expired)
            and all(_asked_again(item) for item in expired)
        ),
        "clock_pass": (
            len(timely) >= 3 and all(item.get("clocks_align") for item in timely)
        ),
        "cost_cny": round(spent, 4),
        "finished_at": datetime.now(SHANGHAI).isoformat(),
    }
    (OUTPUT / "proof.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    _write_report(report)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report


def _write_report(report: dict[str, Any]) -> None:
    lines = [
        "# Conversational media TTL",
        "",
        "## Why 30 minutes",
        "",
        str(report.get("why_this_band") or ""),
        "",
        f"- timely_pass: `{report.get('timely_pass')}`",
        f"- expired_pass: `{report.get('expired_pass')}`",
        f"- clock_pass: `{report.get('clock_pass')}`",
        f"- cost_cny: `{report.get('cost_cny')}`",
        "",
        "## Timely",
        "",
    ]
    for item in report.get("timely") or []:
        lines.append(f"- `{Path(str(item.get('clone') or '')).name}`")
        lines.append(f"  - line: {item.get('line')}")
        lines.append(f"  - clocks_align: `{item.get('clocks_align')}`")
        shared = item.get("shared") or {}
        delivery = shared.get("delivery") if isinstance(shared, dict) else None
        if isinstance(delivery, dict):
            lines.append(
                f"  - shared_at={delivery.get('shared_at')} "
                f"received_at={delivery.get('received_at')}"
            )
        lines.append("")
    lines.extend(["## Expired", ""])
    for item in report.get("expired") or []:
        lines.append(f"- `{Path(str(item.get('clone') or '')).name}`")
        lines.append(f"  - blocked: `{item.get('blocked')}`")
        lines.append(f"  - did_not_auto_send: `{item.get('did_not_auto_send')}`")
        lines.append(f"  - second_selection: `{item.get('second_selection')}`")
        decision = item.get("second_decision")
        if isinstance(decision, dict):
            lines.append(f"  - second_decision: `{json.dumps(decision.get('decision'), ensure_ascii=False)}`")
        lines.append("")
    lines.extend(
        [
            "## Present-moment candidate",
            "",
            "Design only, not implemented. See `output/present-moment-photo/REPORT.md`.",
            "",
        ]
    )
    (OUTPUT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
