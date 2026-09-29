#!/usr/bin/env python3
"""Clone-only acceptance for waiting_for cadence wakeup.

Never writes ``data/``, never talks to 8787 or NapCat. Output lives in
``output/wake-truth/``. Real inbound turns, not synthetic probes.

Usage::

    .venv/bin/python scripts/prove_wake_truth.py
    .venv/bin/python scripts/prove_wake_truth.py --only cadence
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
import json
import logging
from pathlib import Path
import sys
from typing import Any, Mapping
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

OUTPUT = (REPO / "output" / "wake-truth").resolve()
_LOG = logging.getLogger("prove_wake_truth")

import importlib.util

_SWITCH_PATH = REPO / "scripts" / "probe_her_switches.py"
_switch_spec = importlib.util.spec_from_file_location("probe_her_switches", _SWITCH_PATH)
switches = importlib.util.module_from_spec(_switch_spec)
assert _switch_spec.loader is not None
sys.modules["probe_her_switches"] = switches
_switch_spec.loader.exec_module(switches)

drive = switches.drive
initiative = switches.initiative
RecordingCharacterModel = switches.RecordingCharacterModel
extract_switch_fields = switches.extract_switch_fields
_present = switches._present
_message_texts = switches._message_texts


WAIT_SCENARIOS = (
    ("pour_water", "等我一下哈，我去倒杯水", "他说去倒水、口头等一下"),
    ("wait_for_me", "等我一下，我去倒杯水，马上告诉你是谁", "他说等我一下然后没把话说完"),
    ("phone", "稍等，我接个电话，回来继续说", "他中途离开接电话"),
    ("toilet", "我去上个厕所马上回来", "他短离开"),
    ("door", "等等，有人敲门，我去看一下就回来", "他中途离开、口头说会回来"),
    ("takeout", "我去拿个外卖，回来接着聊", "他说去拿东西"),
    ("elevator", "等等我，电梯到了先出去", "他在移动中口头停一下"),
    ("eat_first", "你等我几分钟，我把这口饭吃完", "他明确说几分钟后回来"),
)

SLEEP_SCENARIOS = (
    ("goodnight", "今天就到这儿吧，你也早点休息，我先睡了", "他收束今晚、点名去睡"),
    ("you_sleep", "你要是困了就先去睡，不用现在回我", "他明确说她可以不回"),
    ("late", "好晚了，明天再说，你先睡", "很晚了、允许她不回"),
    ("hang_up", "先挂了，我要睡了，你忙你的", "他结束这一轮"),
    ("tired", "困死了明天再说哈，你也别撑着", "他困了、允许沉默"),
)

FALSE_FACT_MARKERS = (
    "Hope expired:",
    "他倒完水回来，",
    "水喝上了",
    "倒完水回来了",
)

HITCH_MARKERS = (
    "her words, not a world event",
    "She is waiting for",
    "he has not spoken since she declared",
    "She chose not to reply",
)

BUDGET_CNY = 8.0
HOT_WAKE_TICK = timedelta(seconds=90)
COLD_GAP = timedelta(hours=3)
SHANGHAI = ZoneInfo("Asia/Shanghai")
EXPIRY_TRIGGER_NEEDLE = "consideration:social-initiative:expectation-expiry:"


def _cost(clone: Path, since_id: int) -> float:
    return float((drive.cost_report(clone, since_id=since_id) or {}).get("cost_cny") or 0)


def _dump_model(item: object) -> Any:
    if item is None or isinstance(item, dict):
        return item
    dump = getattr(item, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    return str(item)[:500]


def _expiry_opens(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in events
        if item.get("event_type") == "TriggerProcessOpened"
        and EXPIRY_TRIGGER_NEEDLE in str(item.get("trigger_ref") or "")
    ]


async def _ensure_local_hour(
    session, *, hour: int, reason: str, run_life: bool = False
) -> datetime:
    now = await session.logical_time()
    local = now.astimezone(SHANGHAI)
    if hour >= 12 and local.hour >= 12:
        return now
    if hour < 7 and local.hour < 7:
        return now
    target_local = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target_local <= local:
        target_local = target_local + timedelta(days=1)
    target = target_local.astimezone(UTC)
    await session.tick_to(target, reason=reason, run_life=run_life)
    return await session.logical_time()


def _scan_prompt(messages: list[dict[str, object]]) -> dict[str, Any]:
    blob = "\n".join(
        str(item.get("content") or "") for item in messages if isinstance(item, dict)
    )
    return {
        "has_hitch": any(marker in blob for marker in HITCH_MARKERS),
        "false_fact_hits": [marker for marker in FALSE_FACT_MARKERS if marker in blob],
        "has_waiting_for_short": "一句短话就够" in blob or "waiting_for 是一个短句" in blob,
        "has_empty_messages_silent": "空数组就是这一轮不回" in blob or "空 messages" in blob,
        "excerpt": blob[:1500],
    }


class PromptRecordingModel(RecordingCharacterModel):
    def __init__(self, inner: object) -> None:
        super().__init__(inner)
        self.prompts: list[dict[str, Any]] = []

    def _note_prompt(self, messages: list[dict[str, object]]) -> None:
        scan = _scan_prompt(messages)
        self.prompts.append(
            {
                "tool_hint": None,
                **scan,
            }
        )
        super()._note_prompt(messages)


def _collect_fields(calls: list[dict[str, Any]]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    compact = [item for item in calls if "compact_gate" in str(item.get("tool") or "")]
    for item in reversed(calls):
        merged = extract_switch_fields(item.get("excerpt") or "")
        merged.update({k: v for k, v in (item.get("fields") or {}).items() if _present(v)})
        for key, value in merged.items():
            if key not in fields and _present(value):
                fields[key] = value
    if compact:
        for key, value in (compact[-1].get("fields") or {}).items():
            if _present(value):
                fields[key] = value
    return fields


def _turn_row(
    *,
    name: str,
    why: str,
    user: str,
    inbound: Mapping[str, Any],
    fields: Mapping[str, Any],
    calls: list[dict[str, Any]],
) -> dict[str, Any]:
    messages = fields.get("messages")
    visible = inbound.get("visible") or []
    visible_texts = []
    for item in visible:
        if isinstance(item, dict):
            visible_texts.append(item.get("text") or item.get("content") or item)
        else:
            visible_texts.append(item)
    empty = not _message_texts({"her_messages": messages})
    timing = fields.get("timing_choice")
    return {
        "scenario": name,
        "why": why,
        "user": user,
        "inbound_status": inbound.get("status"),
        "visible": visible_texts,
        "her_messages": messages,
        "felt": fields.get("felt"),
        "waiting_for": fields.get("waiting_for"),
        "wait": fields.get("wait"),
        "later": fields.get("later"),
        "timing_choice": timing,
        "silent": empty or timing == "silent",
        "tools": [item.get("tool") for item in calls],
        "compact_excerpt": next(
            (
                item.get("excerpt")
                for item in reversed(calls)
                if "compact_gate" in str(item.get("tool") or "")
            ),
            None,
        ),
    }


async def run_family(
    *,
    source: Path,
    output_dir: Path,
    family: str,
    scenarios: tuple[tuple[str, str, str], ...],
    budget_left: float,
) -> dict[str, Any]:
    clone = output_dir / f"{family}.sqlite"
    drive.clone_ledger(source, clone)
    usage_from = drive.current_usage_id(clone)
    session, recorder = await initiative.open_recorded_session(
        database=clone, output_dir=output_dir, inbound_payload=None
    )
    recorder.__class__ = PromptRecordingModel
    recorder.prompt_marks = None
    recorder.prompts = []
    turns: list[dict[str, Any]] = []
    stopped = False
    try:
        for name, text, why in scenarios:
            if _cost(clone, usage_from) >= budget_left:
                stopped = True
                break
            before = len(recorder.calls)
            inbound = await session.inbound(text)
            new_calls = recorder.calls[before:]
            turns.append(
                _turn_row(
                    name=name,
                    why=why,
                    user=text,
                    inbound=inbound,
                    fields=_collect_fields(new_calls),
                    calls=new_calls,
                )
            )
        false_hits = [
            item
            for item in getattr(recorder, "prompts", [])
            if item.get("false_fact_hits")
        ]
        return {
            "status": "stopped_for_budget" if stopped else "ran",
            "clone": str(clone),
            "family": family,
            "turns": turns,
            "prompt_marks": getattr(recorder, "prompt_marks", None),
            "false_fact_prompts": false_hits[:8],
            "prompt_scans": getattr(recorder, "prompts", [])[:12],
            "cost": drive.cost_report(clone, since_id=usage_from),
        }
    except Exception as exc:
        return {
            "status": "error",
            "clone": str(clone),
            "family": family,
            "error": f"{type(exc).__name__}: {exc}"[:2000],
            "turns": turns,
            "cost": drive.cost_report(clone, since_id=usage_from),
        }
    finally:
        await session.close()


async def _hope_snapshot(session) -> dict[str, Any]:
    from companion_daemon.world_v2.response_expectation_view import (
        expired_unanswered_expectation,
        living_unanswered_hope,
    )

    projection = initiative._ledger(session).project()
    living = living_unanswered_hope(projection)
    expired = expired_unanswered_expectation(projection)
    return {
        "logical_time": (await session.logical_time()).isoformat(),
        "living": _dump_model(living),
        "expired": _dump_model(expired),
        "opportunity_kind": "expired_expectation" if expired is not None else None,
        "opportunity_id": None if expired is None else expired.plan_id,
        "scheduled_for": None if expired is None else expired.not_before.isoformat(),
    }


def _parse_dt(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _wake_delay_seconds(*, declared: object, opened: object) -> float | None:
    start = _parse_dt(declared)
    end = _parse_dt(opened)
    if start is None or end is None:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return (end - start).total_seconds()


async def run_cadence_hot(
    *,
    source: Path,
    output_dir: Path,
    budget_left: float,
) -> dict[str, Any]:
    trials: list[dict[str, Any]] = []
    antispam: dict[str, Any] | None = None
    spent = 0.0
    clones: list[str] = []
    all_false: list[dict[str, Any]] = []
    hitch_n = 0
    for name, text, why in WAIT_SCENARIOS[:6]:
        if spent >= budget_left:
            break
        clone = output_dir / f"cadence-hot-{name}.sqlite"
        drive.clone_ledger(source, clone)
        clones.append(str(clone))
        usage_from = drive.current_usage_id(clone)
        session, recorder = await initiative.open_recorded_session(
            database=clone, output_dir=output_dir, inbound_payload=None
        )
        recorder.__class__ = PromptRecordingModel
        recorder.prompt_marks = None
        recorder.prompts = []
        try:
            await _ensure_local_hour(session, hour=20, reason=f"cadence-evening-{name}")
            await session.inbound("刚忙完，我们继续刚才的")
            await session.inbound("对，你刚才说的那个，我听着呢")
            seq_before = drive.current_seq(clone)
            before_calls = len(recorder.calls)
            inbound = await session.inbound(text)
            inbound_row = _turn_row(
                name=name,
                why=why,
                user=text,
                inbound=inbound,
                fields=_collect_fields(recorder.calls[before_calls:]),
                calls=recorder.calls[before_calls:],
            )
            hope_at = await session.logical_time()
            after_inbound = await _hope_snapshot(session)
            living = after_inbound.get("living") or {}
            wrote_hope = bool(inbound_row.get("waiting_for") or living)
            tick = await session.tick_to(
                hope_at + HOT_WAKE_TICK, reason=f"cadence-hot-{name}", run_life=False
            )
            before_wake = await _hope_snapshot(session)
            wake_calls_from = len(recorder.calls)
            drains = await session.drain_loop(rounds=12, background=16)
            after_wake = await _hope_snapshot(session)
            wake_fields = _collect_fields(recorder.calls[wake_calls_from:])
            events = initiative.interesting_events(clone, seq_before)
            opens = _expiry_opens(events)
            opened_at = opens[0].get("logical_time") if opens else before_wake.get("scheduled_for")
            delay = _wake_delay_seconds(declared=hope_at, opened=opened_at)
            scheduled = None
            if isinstance(before_wake.get("expired"), dict):
                scheduled = _wake_delay_seconds(
                    declared=before_wake["expired"].get("declared_logical_time"),
                    opened=before_wake["expired"].get("not_before"),
                )
            trial = {
                "scenario": name,
                "clone": str(clone),
                "why": why,
                "user": text,
                "inbound": inbound_row,
                "wrote_waiting_for": inbound_row.get("waiting_for"),
                "wait_seconds": inbound_row.get("wait"),
                "her_inbound_lines": inbound_row.get("her_messages"),
                "hope_at": hope_at.isoformat(),
                "after_inbound": after_inbound,
                "tick": tick,
                "before_drain": before_wake,
                "after_drain": after_wake,
                "wake_offered": bool(
                    opens
                    or before_wake.get("opportunity_kind") == "expired_expectation"
                    or before_wake.get("expired")
                ),
                "expiry_opens": opens,
                "wake_delay_seconds": delay,
                "scheduled_delay_seconds": scheduled,
                "wake_choice": {
                    "messages": wake_fields.get("messages"),
                    "timing_choice": wake_fields.get("timing_choice"),
                    "silent": (
                        not _message_texts({"her_messages": wake_fields.get("messages")})
                        or wake_fields.get("timing_choice") == "silent"
                    ),
                    "waiting_for": wake_fields.get("waiting_for"),
                    "later": wake_fields.get("later"),
                },
                "wake_prompts": [
                    {
                        "has_hitch": item.get("has_hitch"),
                        "false_fact_hits": item.get("false_fact_hits"),
                        "excerpt": item.get("excerpt"),
                    }
                    for item in getattr(recorder, "prompts", [])[wake_calls_from:]
                ],
                "drains_tail": drains[-3:] if drains else [],
            }
            trials.append(trial)
            all_false.extend(
                item for item in getattr(recorder, "prompts", []) if item.get("false_fact_hits")
            )
            hitch_n += sum(1 for item in getattr(recorder, "prompts", []) if item.get("has_hitch"))
            if antispam is None and wrote_hope and trial["wake_offered"]:
                first_ref = (opens[0].get("trigger_ref") if opens else None)
                now = await session.logical_time()
                seq_spam = drive.current_seq(clone)
                await session.tick_to(
                    now + HOT_WAKE_TICK, reason="cadence-antispam", run_life=False
                )
                spam_before = await _hope_snapshot(session)
                spam_from = len(recorder.calls)
                spam_drains = await session.drain_loop(rounds=8, background=16)
                spam_after = await _hope_snapshot(session)
                spam_opens = _expiry_opens(initiative.interesting_events(clone, seq_spam))
                antispam = {
                    "after_scenario": name,
                    "first_trigger_ref": first_ref,
                    "she_chose_silent": trial["wake_choice"].get("silent"),
                    "second_tick_opens": spam_opens,
                    "same_hope_reminted": bool(
                        first_ref
                        and any(first_ref in str(item.get("trigger_ref") or "") for item in spam_opens)
                    ),
                    "second_opportunity_kind": spam_before.get("opportunity_kind"),
                    "second_expired": spam_before.get("expired"),
                    "after_drain_kind": spam_after.get("opportunity_kind"),
                    "extra_wake_calls": len(recorder.calls) - spam_from,
                    "drains_tail": spam_drains[-2:] if spam_drains else [],
                }
        except Exception as exc:
            trials.append(
                {
                    "scenario": name,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}"[:2000],
                }
            )
        finally:
            await session.close()
            spent += _cost(clone, usage_from)
    offered = [
        item for item in trials if item.get("wake_offered") and item.get("wrote_waiting_for")
    ]
    delays = [
        item["wake_delay_seconds"]
        for item in offered
        if isinstance(item.get("wake_delay_seconds"), (int, float))
    ]
    scheduled = [
        item["scheduled_delay_seconds"]
        for item in offered
        if isinstance(item.get("scheduled_delay_seconds"), (int, float))
    ]
    return {
        "status": "ran",
        "clones": clones,
        "trials": trials,
        "offered_n": len(offered),
        "delay_seconds": delays,
        "scheduled_delay_seconds": scheduled,
        "delay_min": min(delays) if delays else None,
        "delay_max": max(delays) if delays else None,
        "antispam": antispam,
        "false_fact_prompts": all_false[:8],
        "hitch_prompts_n": hitch_n,
        "cost": {"cost_cny": round(spent, 4)},
    }

async def run_cadence_gap(
    *,
    source: Path,
    output_dir: Path,
    name: str,
    budget_left: float,
    local_hour: int,
    gap: timedelta,
    tick_after: timedelta,
    leave_text: str,
    warmup: bool,
) -> dict[str, Any]:
    clone = output_dir / f"cadence-{name}.sqlite"
    drive.clone_ledger(source, clone)
    usage_from = drive.current_usage_id(clone)
    session, recorder = await initiative.open_recorded_session(
        database=clone, output_dir=output_dir, inbound_payload=None
    )
    recorder.__class__ = PromptRecordingModel
    recorder.prompt_marks = None
    recorder.prompts = []
    try:
        await _ensure_local_hour(session, hour=local_hour, reason=f"cadence-{name}-clock")
        if warmup and _cost(clone, usage_from) < budget_left:
            await session.inbound("刚忙完，我们继续刚才的")
        now = await session.logical_time()
        if gap.total_seconds() > 0:
            await session.tick_to(now + gap, reason=f"cadence-{name}-gap", run_life=False)
        seq_before = drive.current_seq(clone)
        before_calls = len(recorder.calls)
        inbound = None
        inbound_row = None
        if _cost(clone, usage_from) < budget_left:
            inbound = await session.inbound(leave_text)
            inbound_row = _turn_row(
                name=name,
                why=name,
                user=leave_text,
                inbound=inbound,
                fields=_collect_fields(recorder.calls[before_calls:]),
                calls=recorder.calls[before_calls:],
            )
        hope_at = await session.logical_time()
        after_inbound = await _hope_snapshot(session)
        tick = await session.tick_to(
            hope_at + tick_after, reason=f"cadence-{name}-wait", run_life=False
        )
        before_drain = await _hope_snapshot(session)
        wake_from = len(recorder.calls)
        drains = await session.drain_loop(rounds=8, background=16)
        after_drain = await _hope_snapshot(session)
        events = initiative.interesting_events(clone, seq_before)
        opens = _expiry_opens(events)
        false_hits = [
            item for item in getattr(recorder, "prompts", []) if item.get("false_fact_hits")
        ]
        return {
            "status": "ran",
            "clone": str(clone),
            "local_hour": local_hour,
            "gap_seconds": gap.total_seconds(),
            "tick_after_seconds": tick_after.total_seconds(),
            "inbound": inbound_row,
            "after_inbound": after_inbound,
            "tick": tick,
            "before_drain": before_drain,
            "after_drain": after_drain,
            "wake_offered": bool(opens or before_drain.get("expired")),
            "expiry_opens": opens,
            "wake_calls": len(recorder.calls) - wake_from,
            "false_fact_prompts": false_hits[:4],
            "drains_tail": drains[-2:] if drains else [],
            "cost": drive.cost_report(clone, since_id=usage_from),
        }
    except Exception as exc:
        return {
            "status": "error",
            "clone": str(clone),
            "error": f"{type(exc).__name__}: {exc}"[:2000],
            "cost": drive.cost_report(clone, since_id=usage_from),
        }
    finally:
        await session.close()


def _rates_wait(turns: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(turns)
    wrote = [item for item in turns if _present(item.get("waiting_for"))]
    timed = [item for item in wrote if _present(item.get("wait"))]
    return {
        "n": n,
        "waiting_for": f"{len(wrote)}/{n}",
        "waiting_for_with_wait": f"{len(timed)}/{n}",
        "waiting_for_written": [item.get("waiting_for") for item in wrote],
        "wait_written": [item.get("wait") for item in timed],
        "her_lines": [
            {"scenario": item["scenario"], "messages": item.get("her_messages")}
            for item in turns
        ],
    }


def _rates_sleep(turns: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(turns)
    silent = [item for item in turns if item.get("silent")]
    later = [item for item in turns if _present(item.get("later"))]
    return {
        "n": n,
        "silent": f"{len(silent)}/{n}",
        "later": f"{len(later)}/{n}",
        "silent_or_later": f"{len(silent) + len(later)}/{n}",
        "her_lines": [
            {
                "scenario": item["scenario"],
                "silent": item.get("silent"),
                "later": item.get("later"),
                "messages": item.get("her_messages"),
            }
            for item in turns
        ],
    }


def _false_anywhere(*sections: Mapping[str, Any]) -> bool:
    return any(bool(item.get("false_fact_prompts")) for item in sections)


async def async_main(args: argparse.Namespace) -> dict[str, Any]:
    source = args.source.resolve()
    output_dir = args.output_dir.resolve()
    if drive._is_production_path(output_dir):  # noqa: SLF001
        raise SystemExit(f"refusing to write under data/: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        raise SystemExit(f"source ledger missing: {source}")
    spent = 0.0
    wait: dict[str, Any] = {}
    report_path = output_dir / "report.json"
    existing = {}
    if report_path.exists():
        existing = json.loads(report_path.read_text(encoding="utf-8"))
        wait = existing.get("wait") or {}
    if args.only in {"all", "wait"}:
        wait = await run_family(
            source=source,
            output_dir=output_dir,
            family="wait",
            scenarios=WAIT_SCENARIOS,
            budget_left=BUDGET_CNY - spent,
        )
        spent += float((wait.get("cost") or {}).get("cost_cny") or 0)
        wait = {**wait, "rates": _rates_wait(wait.get("turns") or [])}
    hot = await run_cadence_hot(
        source=source,
        output_dir=output_dir,
        budget_left=max(0.0, BUDGET_CNY - spent),
    )
    spent += float((hot.get("cost") or {}).get("cost_cny") or 0)
    cold = await run_cadence_gap(
        source=source,
        output_dir=output_dir,
        name="cold",
        budget_left=max(0.0, BUDGET_CNY - spent),
        local_hour=20,
        gap=COLD_GAP,
        tick_after=HOT_WAKE_TICK,
        leave_text="等我一下哈，我去倒杯水",
        warmup=False,
    )
    spent += float((cold.get("cost") or {}).get("cost_cny") or 0)
    overnight = await run_cadence_gap(
        source=source,
        output_dir=output_dir,
        name="overnight",
        budget_left=max(0.0, BUDGET_CNY - spent),
        local_hour=2,
        gap=timedelta(0),
        tick_after=HOT_WAKE_TICK,
        leave_text="等我一下哈，我去倒杯水",
        warmup=True,
    )
    spent += float((overnight.get("cost") or {}).get("cost_cny") or 0)
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "budget_cny": BUDGET_CNY,
        "cost_cny": round(spent, 4),
        "wait": wait,
        "hot": hot,
        "cold": cold,
        "overnight": overnight,
        "false_facts_anywhere": _false_anywhere(wait, hot, cold, overnight),
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=drive.PRODUCTION_DB)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--only", choices=("all", "cadence", "wait"), default="all")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    report = asyncio.run(async_main(args))
    hot = report.get("hot") or {}
    print(
        json.dumps(
            {
                "cost_cny": report.get("cost_cny"),
                "wait": (report.get("wait") or {}).get("rates"),
                "hot_offered_n": hot.get("offered_n"),
                "hot_delays": hot.get("delay_seconds"),
                "antispam": hot.get("antispam"),
                "cold_woke": (report.get("cold") or {}).get("wake_offered"),
                "overnight_woke": (report.get("overnight") or {}).get("wake_offered"),
                "false_facts_anywhere": report.get("false_facts_anywhere"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
