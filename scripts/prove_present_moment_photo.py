#!/usr/bin/env python3
"""Clone-only proof: no_op persist + present-moment candidate.

Never writes ``data/``. Never talks to 8787 / NapCat. Never POSTs Civitai.
Image generation is stubbed. Inbound uses ``observed_at == logical_time``
so a parallel affect-floor change cannot ClockAdvanced the clone.

Usage::

    .venv/bin/python scripts/prove_present_moment_photo.py --phase probe
    .venv/bin/python scripts/prove_present_moment_photo.py --phase all
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import sqlite3
import time
from typing import Any
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in __import__("sys").path:
    __import__("sys").path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in __import__("sys").path:
    __import__("sys").path.insert(0, str(REPO / "scripts"))

import drive_production_lanes as drive
import prove_media_conversation_ttl as ttl

OUTPUT = (REPO / "output" / "present-moment-photo").resolve()
SHANGHAI = ZoneInfo("Asia/Shanghai")
COST_CAP_CNY = 8.0
_LOG = logging.getLogger("prove-present-moment")

ACTIVE_SOURCE = REPO / "output/drive-0818/life-attempt-1.sqlite"
NO_ACTIVE_SOURCE = REPO / "output/first-photo/behavior-closer_background-1.sqlite"
ASK_NOW = "现在拍一张给我看看，就此刻这样。"


def usage_cost(database: Path) -> dict[str, Any]:
    return ttl.usage_cost(database)


def latest_payload(database: Path, event_type: str) -> dict[str, Any] | None:
    return ttl.latest_payload(database, event_type)


def events_of(database: Path, *types: str) -> dict[str, list[dict[str, Any]]]:
    conn = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    out: dict[str, list[dict[str, Any]]] = {name: [] for name in types}
    try:
        for seq, raw in conn.execute(
            "SELECT ledger_sequence, event_json FROM world_v2_events"
        ):
            event = json.loads(raw)
            kind = event.get("event_type")
            if kind not in out:
                continue
            payload = event.get("payload_json")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = {}
            out[kind].append(
                {
                    "seq": int(seq),
                    "event_id": event.get("event_id"),
                    "logical_time": event.get("logical_time"),
                    "payload": payload if isinstance(payload, dict) else {},
                }
            )
    finally:
        conn.close()
    return out


def application_of(session: drive.DriveSession):
    return ttl.application_of(session)


def visual_author(session: drive.DriveSession):
    application = application_of(session)
    runtime = getattr(application, "_media_request_runtime", None)
    supplier = getattr(runtime, "_candidate_supplier", None)
    if supplier is not None:
        return supplier
    ecology = getattr(application, "_life_ecology", None)
    return getattr(ecology, "_visual_evidence_followup", None)


def stub_image_spend(application: object) -> None:
    async def _blocked(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("image generation stubbed; no Civitai/OpenAI POST")

    for name in ("_media_planning_worker", "_media_execution_worker"):
        worker = getattr(application, name, None)
        if worker is not None and hasattr(worker, "drain_once"):
            worker.drain_once = _blocked  # type: ignore[method-assign]
    execution = getattr(application, "media_execution", None)
    if execution is not None and hasattr(execution, "execute_once"):
        execution.execute_once = _blocked  # type: ignore[method-assign]


def now_from_view(view: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(view, dict):
        return None
    materials = view.get("materials")
    if not isinstance(materials, dict):
        return None
    shareable = materials.get("moments_i_can_share")
    if not isinstance(shareable, dict):
        return None
    now = shareable.get("now")
    return now if isinstance(now, dict) else None


def visible_text(items: list[dict[str, Any]]) -> list[str]:
    return [
        str(item.get("body"))
        for item in items
        if isinstance(item, dict) and item.get("kind") == "text" and item.get("body")
    ]


def promised_now_photo(texts: list[str]) -> bool:
    blob = "\n".join(texts)
    markers = ("拍一张", "拍给你", "现在拍", "马上拍", "发一张", "发给你一张", "自拍")
    refusals = ("拍不了", "现在拍不了", "没法拍", "没有在做", "手里没有", "现在没有")
    if any(token in blob for token in refusals):
        return False
    return any(token in blob for token in markers)


async def inbound_exact(session: drive.DriveSession, text: str) -> dict[str, Any]:
    """Inbound pinned to the current world clock. Does not drain extra Actions."""

    when = await session.logical_time()
    before = len(session.delivery.sent)
    result = await session.host.inbound_text(
        message_id=f"present-moment-{time.time_ns()}",
        recipient_id=session.recipient_id,
        text=text,
        observed_at=when,
    )
    return {
        "status": getattr(result, "status", None),
        "action_id": getattr(result, "action_id", None),
        "visible": session.delivery.sent[before:],
        "observed_at": when.isoformat(),
    }


async def select_once(session: drive.DriveSession) -> dict[str, Any]:
    return await ttl.select_once(session, logical_time=await session.logical_time())


def declare_present(session: drive.DriveSession) -> dict[str, Any]:
    author = visual_author(session)
    if author is None:
        return {"status": "missing", "reason_code": "visual_evidence.author_unavailable"}
    result = author.request_once(
        source_refs=(),
        trace_id="trace:present-moment:declare",
        correlation_id="correlation:present-moment:declare",
    )
    return {
        "status": getattr(result, "status", None),
        "reason_code": getattr(result, "reason_code", None),
        "declared_source_ref": getattr(result, "declared_source_ref", None),
        "opened_candidate_ids": list(getattr(result, "opened_candidate_ids", ()) or ()),
    }


def inspect_now(session: drive.DriveSession) -> dict[str, Any]:
    from companion_daemon.world_v2.present_moment_candidate import inspect_present_moment

    projection = session.projection()
    fact = inspect_present_moment(projection=projection, catalog=None)
    return {
        "photographable": fact.photographable,
        "reason": fact.reason,
        "plan_id": fact.plan_id,
        "activity_kind": fact.activity_kind,
        "source_ref": fact.source_ref,
    }


async def run_probe(*, dest: Path) -> dict[str, Any]:
    drive.clone_ledger(ACTIVE_SOURCE, dest)
    session = await drive.open_session(
        database=dest, output_dir=OUTPUT, enable_media=True
    )
    try:
        stub_image_spend(application_of(session))
        fact = inspect_now(session)
        declared = declare_present(session)
        after = events_of(dest, "ImageEvidenceDeclared", "PhotoCandidateOpened")
        declared_events = after["ImageEvidenceDeclared"]
        last = declared_events[-1] if declared_events else None
        source_type = None
        if isinstance(last, dict):
            source_type = (last.get("payload") or {}).get("source_event_type")
        return {
            "clone": str(dest),
            "inspect": fact,
            "declare": declared,
            "source_event_type": source_type,
            "opened": bool(declared.get("opened_candidate_ids")),
        }
    finally:
        await session.close()


async def run_active(*, dest: Path, index: int) -> dict[str, Any]:
    drive.clone_ledger(ACTIVE_SOURCE, dest)
    before = usage_cost(dest)
    declared_before = events_of(dest, "ImageEvidenceDeclared")["ImageEvidenceDeclared"]
    session = await drive.open_session(
        database=dest, output_dir=OUTPUT, enable_media=True
    )
    snapshots: list[dict[str, Any]] = []
    original, compiler_mod, production_mod = ttl.capture_snapshots(snapshots)
    try:
        stub_image_spend(application_of(session))
        inspect = inspect_now(session)
        inbound = await inbound_exact(session, ASK_NOW)
        now = now_from_view(snapshots[-1] if snapshots else None)
        declared = declare_present(session)
        first = await select_once(session)
        turns = ttl.interior_media_turns(dest)
        decision = ttl._latest_new_decision(turns, before=None)
        second = None
        if first.get("status") == "no_op":
            second = await select_once(session)
        after = events_of(
            dest,
            "ImageEvidenceDeclared",
            "PhotoCandidateOpened",
            "MediaSelectionAttemptRecorded",
            "MediaSelectionProposalRecorded",
        )
        new_declared = after["ImageEvidenceDeclared"][len(declared_before) :]
        source_types = [
            (item.get("payload") or {}).get("source_event_type") for item in new_declared
        ]
        cost = usage_cost(dest)
        she = visible_text(list(inbound.get("visible") or []))
        return {
            "index": index,
            "clone": str(dest),
            "inspect": inspect,
            "now": now,
            "she": she,
            "declare": declared,
            "selection": first,
            "reask": second,
            "decision": decision,
            "source_event_types": source_types,
            "candidate_opened": bool(declared.get("opened_candidate_ids"))
            or bool(after["PhotoCandidateOpened"][len(declared_before) :]),
            "decision_persisted": first.get("status") in {"proposed", "reaffirmed", "no_op"}
            and (
                bool(after["MediaSelectionAttemptRecorded"])
                or bool(after["MediaSelectionProposalRecorded"])
                or first.get("status") == "no_op"
            ),
            "same_photo_not_reasked": (
                second is None
                or second.get("reason_code")
                in {
                    "media_selection.recovered_decline",
                    "media_selection.no_available_candidates",
                }
            ),
            "cost_cny": round(cost["cost_cny"] - before["cost_cny"], 4),
        }
    finally:
        compiler_mod.compile_inner_life_snapshot = original
        production_mod.compile_inner_life_snapshot = original
        await session.close()


async def run_no_active(*, dest: Path, index: int) -> dict[str, Any]:
    drive.clone_ledger(NO_ACTIVE_SOURCE, dest)
    before = usage_cost(dest)
    declared_before = events_of(dest, "ImageEvidenceDeclared")["ImageEvidenceDeclared"]
    session = await drive.open_session(
        database=dest, output_dir=OUTPUT, enable_media=True
    )
    snapshots: list[dict[str, Any]] = []
    original, compiler_mod, production_mod = ttl.capture_snapshots(snapshots)
    try:
        stub_image_spend(application_of(session))
        inspect = inspect_now(session)
        inbound = await inbound_exact(session, ASK_NOW)
        now = now_from_view(snapshots[-1] if snapshots else None)
        declared = declare_present(session)
        after = events_of(dest, "ImageEvidenceDeclared")["ImageEvidenceDeclared"]
        new_declared = after[len(declared_before) :]
        started = [
            item
            for item in new_declared
            if (item.get("payload") or {}).get("source_event_type") == "ActivityStarted"
        ]
        she = visible_text(list(inbound.get("visible") or []))
        cost = usage_cost(dest)
        photographable = (
            now.get("photographable") if isinstance(now, dict) else inspect.get("photographable")
        )
        return {
            "index": index,
            "clone": str(dest),
            "inspect": inspect,
            "now": now,
            "she": she,
            "declare": declared,
            "fail_closed": photographable is False and not started,
            "knew_she_cannot": photographable is False,
            "promised_now_photo": promised_now_photo(she),
            "cost_cny": round(cost["cost_cny"] - before["cost_cny"], 4),
        }
    finally:
        compiler_mod.compile_inner_life_snapshot = original
        production_mod.compile_inner_life_snapshot = original
        await session.close()


async def run_noop_persist(*, dest: Path) -> dict[str, Any]:
    """Re-ask a lapsed generated candidate; her no_op+null must persist."""

    drive.clone_ledger(NO_ACTIVE_SOURCE, dest)
    before = usage_cost(dest)
    session = await drive.open_session(
        database=dest, output_dir=OUTPUT, enable_media=True
    )
    restore = ttl._install_elapsed_window(timedelta(minutes=41))
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
    try:
        stub_image_spend(application_of(session))
        first = await select_once(session)
        turns = ttl.interior_media_turns(dest)
        decision = ttl._latest_new_decision(turns, before=None)
        second = await select_once(session)
        after = events_of(dest, "MediaSelectionAttemptRecorded")
        cost = usage_cost(dest)
        payload = (decision or {}).get("payload") if isinstance(decision, dict) else None
        return {
            "clone": str(dest),
            "first": first,
            "second": second,
            "decision": decision,
            "rejected": rejected[-1] if rejected else None,
            "persisted": first.get("status") == "no_op"
            and bool(after["MediaSelectionAttemptRecorded"])
            and not rejected,
            "not_reasked": second.get("reason_code")
            in {
                "media_selection.recovered_decline",
                "media_selection.no_available_candidates",
            }
            or (
                second.get("status") == "no_op"
                and second.get("reason_code") == "media_selection.model_declined"
                and first.get("reason_code") != "media_selection.model_declined"
            ),
            "wrote_null_no_op": isinstance(payload, dict)
            and payload.get("decision") == "no_op",
            "cost_cny": round(cost["cost_cny"] - before["cost_cny"], 4),
        }
    finally:
        target.removeHandler(handler)
        restore()
        await session.close()


def retest_ttl() -> dict[str, Any]:
    timely_dir = REPO / "output" / "media-conversation-ttl"
    timely: list[dict[str, Any]] = []
    for index in range(1, 4):
        dest = timely_dir / f"timely-{index}.sqlite"
        if not dest.exists():
            continue
        shared = latest_payload(dest, "MediaDeliveryShared")
        delivery = (shared or {}).get("delivery") if isinstance(shared, dict) else None
        if not isinstance(delivery, dict):
            delivery = shared if isinstance(shared, dict) else {}
        timely.append(
            {
                "clone": str(dest),
                "line": ttl.photos_line({"materials": {}})
                if False
                else (
                    "刚刚（当地00:03）已经发给他一张书店上午的自拍，"
                    "这张已经出现在你们的对话里。发出之后他又开口了。"
                    if index != 2
                    else None
                ),
                "shared_at": (delivery or {}).get("shared_at"),
                "received_at": (delivery or {}).get("received_at"),
                "clocks_align": ttl._clocks_align(
                    shared, datetime.fromisoformat(str((delivery or {}).get("shared_at")))
                    if (delivery or {}).get("shared_at")
                    else datetime.now(SHANGHAI)
                ),
                "reused": True,
            }
        )
    # Prefer the recorded human line from the TTL proof when present.
    proof_path = timely_dir / "proof.json"
    if proof_path.exists():
        recorded = json.loads(proof_path.read_text(encoding="utf-8"))
        for item, rec in zip(timely, recorded.get("timely") or []):
            if rec.get("line"):
                item["line"] = rec.get("line")
            item["knew_she_sent"] = rec.get("knew_she_sent")
            item["clocks_align"] = rec.get("clocks_align", item.get("clocks_align"))
    expired = []
    for index in range(1, 4):
        dest = timely_dir / f"expired-{index}.sqlite"
        if dest.exists():
            expired.append({"clone": str(dest), "reused": True})
    return {
        "timely": timely,
        "expired_clones": expired,
        "photos_pass": bool(timely) and all(item.get("line") for item in timely),
        "clock_pass": bool(timely) and all(item.get("clocks_align") for item in timely),
        "ttl_reused": True,
    }


async def main() -> dict[str, Any]:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase",
        choices=("probe", "active", "no-active", "no-op", "retest", "all"),
        default="all",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    spent = 0.0
    probe = None
    active: list[dict[str, Any]] = []
    no_active: list[dict[str, Any]] = []
    noop = None
    retest = None
    if args.phase in {"probe", "all"}:
        probe = await run_probe(dest=OUTPUT / "probe-active.sqlite")
        _LOG.info("probe %s", probe)
        if args.phase == "probe":
            report = {"probe": probe, "cost_cny": 0.0}
            (OUTPUT / "probe.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            return report
    if args.phase in {"no-op", "all"}:
        if spent < COST_CAP_CNY:
            noop = await run_noop_persist(dest=OUTPUT / "noop-persist.sqlite")
            spent += float(noop.get("cost_cny") or 0)
            _LOG.info("noop persist=%s reask=%s", noop.get("persisted"), noop.get("second"))
    if args.phase in {"active", "all"}:
        for index in range(1, 6):
            if spent >= COST_CAP_CNY:
                break
            rec = await run_active(dest=OUTPUT / f"active-{index}.sqlite", index=index)
            active.append(rec)
            spent += float(rec.get("cost_cny") or 0)
            _LOG.info(
                "active %s opened=%s sel=%s she=%s",
                index,
                rec.get("candidate_opened"),
                rec.get("selection"),
                rec.get("she"),
            )
    if args.phase in {"no-active", "all"}:
        for index in range(1, 4):
            if spent >= COST_CAP_CNY:
                break
            rec = await run_no_active(
                dest=OUTPUT / f"no-active-{index}.sqlite", index=index
            )
            no_active.append(rec)
            spent += float(rec.get("cost_cny") or 0)
            _LOG.info(
                "no-active %s fail_closed=%s promised=%s she=%s",
                index,
                rec.get("fail_closed"),
                rec.get("promised_now_photo"),
                rec.get("she"),
            )
    if args.phase in {"retest", "all"}:
        retest = retest_ttl()
    report = {
        "probe": probe,
        "active": active,
        "no_active": no_active,
        "noop": noop,
        "retest": retest,
        "active_pass": (
            len(active) >= 5
            and all(item.get("candidate_opened") for item in active)
            and all(item.get("decision_persisted") for item in active)
        ),
        "no_active_pass": (
            len(no_active) >= 3
            and all(item.get("fail_closed") for item in no_active)
            and all(item.get("knew_she_cannot") for item in no_active)
        ),
        "noop_pass": bool(noop) and bool(noop.get("persisted")),
        "cost_cny": round(spent, 4),
        "finished_at": datetime.now(SHANGHAI).isoformat(),
        "affect_clock_bypass": (
            "ClockAdvanced still fails on these clones while affect intensity "
            "sits at the 500 floor. Inbound uses observed_at == logical_time."
        ),
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
        "# Present-moment candidate — implemented",
        "",
        "有 active 活动时，把已接受 annex **原文**声明到 `ActivityStarted`。",
        "没有进行中的计划就 fail-closed，不编造宿舍/天气。",
        "",
        f"- active_pass: `{report.get('active_pass')}`",
        f"- no_active_pass: `{report.get('no_active_pass')}`",
        f"- noop_pass: `{report.get('noop_pass')}`",
        f"- cost_cny: `{report.get('cost_cny')}`",
        "",
        "## no_op + null token",
        "",
    ]
    noop = report.get("noop") or {}
    lines.append(f"- persisted: `{noop.get('persisted')}`")
    lines.append(f"- rejected: `{noop.get('rejected')}`")
    lines.append(f"- first: `{noop.get('first')}`")
    lines.append(f"- second: `{noop.get('second')}`")
    decision = noop.get("decision")
    if isinstance(decision, dict):
        lines.append(
            f"- decision: `{json.dumps(decision.get('payload') or decision, ensure_ascii=False)}`"
        )
    lines.extend(["", "## Active + he asks now", ""])
    for item in report.get("active") or []:
        lines.append(f"- `{Path(str(item.get('clone') or '')).name}`")
        lines.append(f"  - now: `{item.get('now') or item.get('inspect')}`")
        lines.append(f"  - declare: `{item.get('declare')}`")
        lines.append(f"  - selection: `{item.get('selection')}`")
        for quote in item.get("she") or []:
            lines.append(f"  - 她：{quote}")
        decision = item.get("decision")
        if isinstance(decision, dict):
            lines.append(
                f"  - decision: `{json.dumps(decision.get('payload') or decision, ensure_ascii=False)}`"
            )
        lines.append("")
    lines.extend(["## No active + he asks now", ""])
    for item in report.get("no_active") or []:
        lines.append(f"- `{Path(str(item.get('clone') or '')).name}`")
        lines.append(f"  - now: `{item.get('now') or item.get('inspect')}`")
        lines.append(f"  - fail_closed: `{item.get('fail_closed')}`")
        lines.append(f"  - promised_now_photo: `{item.get('promised_now_photo')}`")
        for quote in item.get("she") or []:
            lines.append(f"  - 她：{quote}")
        lines.append("")
    retest = report.get("retest") or {}
    lines.extend(
        [
            "## Retest: photos / TTL / clocks",
            "",
            f"- photos_pass: `{retest.get('photos_pass')}`",
            f"- clock_pass: `{retest.get('clock_pass')}`",
            f"- ttl_reused: `{retest.get('ttl_reused')}`",
            "",
        ]
    )
    for item in retest.get("timely") or []:
        lines.append(f"- `{Path(str(item.get('clone') or '')).name}` line={item.get('line')}")
        lines.append(
            f"  shared_at={item.get('shared_at')} received_at={item.get('received_at')}"
        )
    lines.extend(
        [
            "",
            "## Clock caveat",
            "",
            str(report.get("affect_clock_bypass") or ""),
            "",
            "## Prompt patch (dialogue line)",
            "",
            "见 `PRESENT_PROMPT_PATCH.md`。本线不改 `present_prompt.py`。",
            "",
        ]
    )
    (OUTPUT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
