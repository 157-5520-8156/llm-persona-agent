#!/usr/bin/env python3
"""Clone-only probe: does making slim choices visible change what she writes?

Never writes ``data/``, never talks to 8787 or NapCat. Output lives in
``output/visible-choices/``. Reuses the initiative clone host.

Usage::

    .venv/bin/python scripts/probe_visible_choices.py --phase after
    .venv/bin/python scripts/probe_visible_choices.py --phase idle
    .venv/bin/python scripts/probe_visible_choices.py --phase summarize
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import hashlib
import importlib.util
import json
import logging
from pathlib import Path
import sys
from typing import Any, Mapping

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

OUTPUT = (REPO / "output" / "visible-choices").resolve()
_LOG = logging.getLogger("probe_visible_choices")

_DRIVE_PATH = REPO / "scripts" / "drive_production_lanes.py"
_spec = importlib.util.spec_from_file_location("drive_production_lanes", _DRIVE_PATH)
drive = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["drive_production_lanes"] = drive
_spec.loader.exec_module(drive)

_SWITCH_PATH = REPO / "scripts" / "probe_her_switches.py"
_switch_spec = importlib.util.spec_from_file_location("probe_her_switches", _SWITCH_PATH)
switches = importlib.util.module_from_spec(_switch_spec)
assert _switch_spec.loader is not None
sys.modules["probe_her_switches"] = switches
_switch_spec.loader.exec_module(switches)

COME_BACK_SCENARIOS = switches.COME_BACK_SCENARIOS + (
    (
        "meeting_gap",
        "我先去开个会，大概半小时。你刚才好像还有一句没说完？现在说也行，回头说也行",
        "间隙里她可以给自己设一个回来开口的闹钟",
    ),
    (
        "early_sleep_thread",
        "那个路灯的事今晚我可能早睡。你要是还想说，可以自己回头找我，我不催",
        "他把回头开口的决定交给她",
    ),
)

RELATIONSHIP_SCENARIOS = switches.RELATIONSHIP_SCENARIOS + (
    (
        "listens_for_real",
        "跟你聊天我会认真听，不是敷衍。想让你知道",
        "他在给这段靠近一个读法",
    ),
    (
        "can_say_the_real_thing",
        "有点把你当可以说心里话的人了。不是突然宣布什么",
        "他在靠近、但没有点名要她升级",
    ),
)

LATER_SCENARIOS = (
    (
        "no_need_to_instant",
        "这句你不用秒回，我看着就行",
        "允许她把已经想好的话延后发",
    ),
    (
        "subway_signal",
        "我进地铁了信号不好，你打字慢点也没关系",
        "延迟发送在情境上说得通",
    ),
    (
        "busy_ok_delay",
        "你先忙，我这边也还在做事，不急着回也行，现在回也行",
        "延后和现在都合法",
    ),
    (
        "pour_water_take_time",
        "我去倒杯水，你慢慢说，隔一会儿发我也看得到",
        "她可以现在写完、稍后再发",
    ),
    (
        "seen_no_rush",
        "先看到了。你忙完再说也行，或者现在说也行",
        "不催她立刻把气泡送出",
    ),
    (
        "two_beats_ok",
        "你要是想隔一会儿再补一句也行，我不盯着秒回",
        "later 可以是她自己的节奏",
    ),
)

IDLE_SCENARIOS = (
    ("going_to_sleep", "我先睡了，明天聊", "收尾，不需要等他下一句"),
    ("weather", "今天天气真好", "无关闲聊"),
    ("noodles", "刚吃了碗面，还行", "日常一句"),
    ("laugh", "哈哈行吧", "轻收"),
    ("thats_it", "先这样，没事了", "明确收束"),
    ("cat_video", "那猫视频我笑死", "闲聊"),
)

FAMILIES = (
    ("wait", switches.WAIT_SCENARIOS),
    ("come_back", COME_BACK_SCENARIOS),
    ("later", LATER_SCENARIOS),
    ("relationship", RELATIONSHIP_SCENARIOS),
    ("idle", IDLE_SCENARIOS),
)


def instruction_snapshot() -> dict[str, Any]:
    from companion_daemon.world_v2.character_interior.inbound_author import (
        _compact_gate_voice_close,
    )
    from companion_daemon.world_v2.present_prompt import (
        SLIM_OPTIONAL_SPECIMEN_KEYS,
        reply_only_slim_shape_specimen,
        slim_consider_instruction,
        slim_consider_json_schema,
        SLIM_CONSIDER_KEYS,
    )

    specimen = reply_only_slim_shape_specimen()
    specimen_json = json.dumps(specimen, ensure_ascii=False, separators=(",", ":"))
    voice_close = _compact_gate_voice_close()
    instruction = slim_consider_instruction()
    return {
        "instruction_sha256": hashlib.sha256(instruction.encode("utf-8")).hexdigest(),
        "voice_close": voice_close,
        "specimen_json": specimen_json,
        "specimen_optional_keys": list(SLIM_OPTIONAL_SPECIMEN_KEYS),
        "schema_keys": sorted(slim_consider_json_schema()["properties"]),
        "consider_keys": sorted(SLIM_CONSIDER_KEYS),
        "omit_is_normal": "省略是常态" in voice_close,
        "not_a_recommendation": "看见键名不是建议你填" in voice_close,
        "specimen_has_waiting_for_null": '"waiting_for":null' in specimen_json,
        "specimen_has_come_back_null": '"come_back":null' in specimen_json,
        "specimen_has_we_are_null": '"we_are":null' in specimen_json,
        "specimen_has_later_null": '"later":null' in specimen_json,
        "specimen_has_declared_display_null": '"declared_display":null' in specimen_json,
        "specimen_has_photo_null": '"photo":null' in specimen_json,
        "specimen_has_example_wait_30": '"wait":30' in specimen_json,
    }


class RecordingCharacterModel(switches.RecordingCharacterModel):
    def _note_prompt(self, messages: list[dict[str, object]]) -> None:
        if getattr(self, "prompt_marks", None) is not None:
            return
        blob = "\n".join(
            str(item.get("content") or "") for item in messages if isinstance(item, dict)
        )
        self.prompt_marks = {
            "has_omit_is_normal": "省略是常态" in blob,
            "has_not_a_recommendation": "看见键名不是建议你填" in blob,
            "has_waiting_for_null_specimen": '"waiting_for":null' in blob,
            "has_come_back_null_specimen": '"come_back":null' in blob,
            "has_we_are_null_specimen": '"we_are":null' in blob,
            "has_later_null_specimen": '"later":null' in blob,
            "has_declared_display_null_specimen": '"declared_display":null' in blob,
            "has_photo_null_specimen": '"photo":null' in blob,
            "has_wait_30_in_specimen": '"wait":30' in blob,
            "has_wake_her_if_she_writes": "叫醒你" in blob and "waiting_for" in blob,
            "has_said_as_exact": "一字不差" in blob,
            "discourages_wait": "就别写 wait" in blob or "请写 wait" in blob,
        }


def _present(value: object) -> bool:
    return switches._present(value)


def summarize_turns(family: str, turns: list[dict[str, Any]]) -> dict[str, Any]:
    if family in {"wait", "come_back", "relationship"}:
        return switches.summarize_turns(family, turns)
    n = len(turns)
    later = [item for item in turns if _present(item.get("later"))]
    wait_pair = [
        item
        for item in turns
        if _present(item.get("waiting_for")) and _present(item.get("wait"))
    ]
    wait_any = [
        item
        for item in turns
        if _present(item.get("waiting_for")) or _present(item.get("wait"))
    ]
    come_pair = [
        item
        for item in turns
        if _present(item.get("come_back")) and _present(item.get("come_back_in"))
    ]
    come_any = [
        item
        for item in turns
        if _present(item.get("come_back")) or _present(item.get("come_back_in"))
    ]
    felt_waitish = [
        item
        for item in turns
        if isinstance(item.get("felt"), str)
        and any(token in item["felt"] for token in ("等", "回头", "再说"))
    ]
    base = {
        "n": n,
        "later": f"{len(later)}/{n}",
        "later_written": [item.get("later") for item in later],
        "waiting_for_or_wait": f"{len(wait_any)}/{n}",
        "waiting_for_and_wait": f"{len(wait_pair)}/{n}",
        "come_back_or_in": f"{len(come_any)}/{n}",
        "come_back_and_in": f"{len(come_pair)}/{n}",
        "felt_mentions_wait_or_later": f"{len(felt_waitish)}/{n}",
        "waits_written": [item.get("wait") for item in wait_any],
        "waiting_for_written": [item.get("waiting_for") for item in wait_any],
        "come_back_written": [item.get("come_back") for item in come_any],
        "come_back_in_written": [item.get("come_back_in") for item in come_any],
    }
    if family == "idle":
        abuse = [
            item
            for item in turns
            if _present(item.get("wait"))
            or _present(item.get("waiting_for"))
            or _present(item.get("come_back"))
            or _present(item.get("come_back_in"))
            or _present(item.get("later"))
        ]
        base["wrote_wait_come_back_or_later"] = f"{len(abuse)}/{n}"
        base["abuse_scenarios"] = [item.get("scenario") for item in abuse]
    return base


async def run_family(
    *,
    source: Path,
    output_dir: Path,
    phase: str,
    family: str,
    scenarios: tuple[tuple[str, str, str], ...],
) -> dict[str, Any]:
    clone = output_dir / f"{phase}-{family}.sqlite"
    drive.clone_ledger(source, clone)
    usage_from = drive.current_usage_id(clone)
    session, recorder = await switches.initiative.open_recorded_session(
        database=clone, output_dir=output_dir, inbound_payload=None
    )
    inner = recorder._inner  # noqa: SLF001
    local = RecordingCharacterModel(inner)
    recorder.__class__ = RecordingCharacterModel
    recorder.prompt_marks = None
    turns: list[dict[str, Any]] = []
    try:
        for name, text, why in scenarios:
            before_calls = len(recorder.calls)
            inbound = await session.inbound(text)
            new_calls = recorder.calls[before_calls:]
            compact = [
                item
                for item in new_calls
                if "compact_gate" in str(item.get("tool") or "")
            ]
            fields: dict[str, Any] = {}
            for item in reversed(new_calls):
                merged = switches.extract_switch_fields(item.get("excerpt") or "")
                merged.update(
                    {
                        k: v
                        for k, v in (item.get("fields") or {}).items()
                        if _present(v)
                    }
                )
                for key, value in merged.items():
                    if key not in fields and _present(value):
                        fields[key] = value
            if compact:
                compact_fields = compact[-1].get("fields") or {}
                for key, value in compact_fields.items():
                    if _present(value):
                        fields[key] = value
            visible = inbound.get("visible") or []
            visible_texts = []
            for item in visible:
                if isinstance(item, dict):
                    visible_texts.append(item.get("text") or item.get("content") or item)
                else:
                    visible_texts.append(item)
            turns.append(
                {
                    "scenario": name,
                    "why": why,
                    "user": text,
                    "inbound_status": inbound.get("status"),
                    "visible": visible_texts,
                    "her_messages": fields.get("messages"),
                    "felt": fields.get("felt"),
                    "waiting_for": fields.get("waiting_for"),
                    "wait": fields.get("wait"),
                    "come_back": fields.get("come_back"),
                    "come_back_in": fields.get("come_back_in"),
                    "later": fields.get("later"),
                    "we_are": fields.get("we_are"),
                    "calling_it": fields.get("calling_it"),
                    "said_as": fields.get("said_as"),
                    "us_deltas": fields.get("us_deltas"),
                    "about_us": fields.get("about_us"),
                    "why_us": fields.get("why_us"),
                    "mood": fields.get("mood"),
                    "declared_display": fields.get("declared_display"),
                    "photo": fields.get("photo"),
                    "commitment_quality": switches.commitment_quality(
                        {
                            "her_messages": fields.get("messages"),
                            "we_are": fields.get("we_are"),
                            "calling_it": fields.get("calling_it"),
                            "said_as": fields.get("said_as"),
                            "later": fields.get("later"),
                        }
                    ),
                    "tools": [item.get("tool") for item in new_calls],
                    "compact_excerpt": (compact[-1].get("excerpt") if compact else None),
                }
            )
        return {
            "status": "ran",
            "clone": str(clone),
            "family": family,
            "prompt_marks": getattr(recorder, "prompt_marks", None),
            "turns": turns,
            "rates": summarize_turns(family, turns),
            "cost": drive.cost_report(clone, since_id=usage_from),
            "model_calls_recorded": len(recorder.calls),
        }
    except Exception as exc:
        return {
            "status": "error",
            "clone": str(clone),
            "family": family,
            "error": f"{type(exc).__name__}: {exc}"[:2000],
            "turns": turns,
            "rates": summarize_turns(family, turns),
            "cost": drive.cost_report(clone, since_id=usage_from),
        }
    finally:
        await session.close()
        del local


async def run_phase(
    *,
    source: Path,
    output_dir: Path,
    phase: str,
    only_families: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    snapshot = instruction_snapshot()
    (output_dir / "specimen.json").write_text(
        json.dumps(
            json.loads(snapshot["specimen_json"]),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (output_dir / "voice_close.txt").write_text(snapshot["voice_close"], encoding="utf-8")
    families: dict[str, Any] = {}
    selected = tuple(
        (family, scenarios)
        for family, scenarios in FAMILIES
        if not only_families or family in set(only_families)
    )
    for family, scenarios in selected:
        _LOG.info("phase=%s family=%s n=%s", phase, family, len(scenarios))
        families[family] = await run_family(
            source=source,
            output_dir=output_dir,
            phase=phase,
            family=family,
            scenarios=scenarios,
        )
    cost = 0.0
    calls = 0
    for item in families.values():
        row = item.get("cost") or {}
        cost += float(row.get("cost_cny") or 0)
        calls += int(row.get("calls") or 0)
    report = {
        "phase": phase,
        "started_at": datetime.now(UTC).isoformat(),
        "model": "deepseek-v4-flash",
        "instruction": snapshot,
        "families": families,
        "cost_cny": round(cost, 4),
        "usage_calls": calls,
    }
    path = output_dir / f"{phase}.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return report


def summarize(output_dir: Path) -> dict[str, Any]:
    after = json.loads((output_dir / "after.json").read_text(encoding="utf-8"))
    idle_path = output_dir / "idle.json"
    idle = json.loads(idle_path.read_text(encoding="utf-8")) if idle_path.exists() else {}
    families = after.get("families") or {}
    idle_families = idle.get("families") or {}
    comparison = {
        "prompt_marks": {
            family: (row.get("prompt_marks") if isinstance(row, dict) else None)
            for family, row in families.items()
        },
        "wait_rates": (families.get("wait") or {}).get("rates"),
        "come_back_rates": (families.get("come_back") or {}).get("rates"),
        "later_rates": (families.get("later") or {}).get("rates"),
        "relationship_rates": (families.get("relationship") or {}).get("rates"),
        "idle_rates": (idle_families.get("idle") or families.get("idle") or {}).get(
            "rates"
        ),
        "cost_cny": {
            "after": after.get("cost_cny"),
            "idle": idle.get("cost_cny"),
            "total": round(
                float(after.get("cost_cny") or 0) + float(idle.get("cost_cny") or 0),
                4,
            ),
        },
    }
    path = output_dir / "comparison.json"
    path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    return comparison


async def async_main(args: argparse.Namespace) -> dict[str, Any]:
    source = args.source.resolve()
    output_dir = args.output_dir.resolve()
    if drive._is_production_path(output_dir):  # noqa: SLF001
        raise SystemExit(f"refusing to write under data/: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.phase == "summarize":
        return summarize(output_dir)
    if not source.exists():
        raise SystemExit(f"source ledger missing: {source}")
    only = tuple(args.family) if args.family else None
    if args.phase == "idle" and not only:
        only = ("idle",)
    if args.phase == "after" and not only:
        only = ("wait", "come_back", "later", "relationship")
    return await run_phase(
        source=source,
        output_dir=output_dir,
        phase=args.phase,
        only_families=only,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=drive.PRODUCTION_DB)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument(
        "--phase",
        choices=("after", "idle", "summarize"),
        required=True,
    )
    parser.add_argument(
        "--family",
        action="append",
        choices=("wait", "come_back", "later", "relationship", "idle"),
        default=[],
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    report = asyncio.run(async_main(args))
    if args.phase == "summarize":
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    rates = {
        family: (row or {}).get("rates")
        for family, row in (report.get("families") or {}).items()
    }
    print(
        json.dumps(
            {
                "phase": report.get("phase"),
                "cost_cny": report.get("cost_cny"),
                "omit_is_normal": (report.get("instruction") or {}).get("omit_is_normal"),
                "rates": rates,
                "errors": {
                    family: (row or {}).get("error")
                    for family, row in (report.get("families") or {}).items()
                    if isinstance(row, dict) and row.get("status") == "error"
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
