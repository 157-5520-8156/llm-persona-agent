#!/usr/bin/env python3
"""Clone-only probe: does she write wait / come_back / we_are / us_deltas?

Never writes ``data/``, never talks to 8787 or NapCat. Output lives in
``output/her-switches/``. Reuses the initiative clone host, records raw
model JSON so a compact-gate reject still counts as her having written a
field.

Usage::

    .venv/bin/python scripts/probe_her_switches.py --phase before
    .venv/bin/python scripts/probe_her_switches.py --phase after
    .venv/bin/python scripts/probe_her_switches.py --phase summarize
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

OUTPUT = (REPO / "output" / "her-switches").resolve()
WORLD_ID = "world:companion-v2:qq-c2c:geoff"
_LOG = logging.getLogger("probe_her_switches")

_DRIVE_PATH = REPO / "scripts" / "drive_production_lanes.py"
_spec = importlib.util.spec_from_file_location("drive_production_lanes", _DRIVE_PATH)
drive = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["drive_production_lanes"] = drive
_spec.loader.exec_module(drive)

_INIT_PATH = REPO / "scripts" / "probe_initiative_lanes.py"
_init_spec = importlib.util.spec_from_file_location("probe_initiative_lanes", _INIT_PATH)
initiative = importlib.util.module_from_spec(_init_spec)
assert _init_spec.loader is not None
sys.modules["probe_initiative_lanes"] = initiative
_init_spec.loader.exec_module(initiative)


SWITCH_KEYS = (
    "waiting_for",
    "wait",
    "come_back",
    "come_back_in",
    "later",
    "we_are",
    "calling_it",
    "said_as",
    "us_deltas",
    "about_us",
    "why_us",
    "messages",
    "felt",
)

WAIT_SCENARIOS = (
    ("hook_then_silence", "你猜我今天碰到谁了", "他丢了钩子，像还要往下说"),
    ("wait_for_me", "等我一下，我去倒杯水，马上告诉你是谁", "他说等我一下然后没把话说完"),
    ("unfinished_mid", "对了上次书店那件事我才说到一半，你还记得吗", "话题说到一半"),
    ("thirty_seconds", "你先别急着回下一句，我三十秒后把那人的名字发你", "他明确说马上回"),
    ("door_knock", "等等，有人敲门，我去看一下就回来", "他中途离开、口头说会回来"),
    ("asked_and_stopped", "我问你一句啊——你现在方便说话吗", "他刚问完、下文还没到"),
)

COME_BACK_SCENARIOS = (
    (
        "unfinished_shop",
        "今晚我可能要赶一份东西，书店那家店的事回头再说也行，你先忙",
        "一件她可能想自己回头再提的事",
    ),
    (
        "cat_photos_later",
        "你上次说想把猫的照片找出来，现在说也行，过一会儿说也行，我不催",
        "一件搁着的分享",
    ),
    (
        "she_was_cut_off",
        "刚才那件事你好像还没说完。我这边先去洗个澡，你忙你的",
        "她这边可能还搁着没说完的话",
    ),
    (
        "tomorrow_thread",
        "对了你不是说想把上次没讲完的那家店再说一遍吗，今晚先到这儿，回头你想说再找我",
        "明确把一件事留到过一阵",
    ),
)

RELATIONSHIP_SCENARIOS = (
    (
        "he_feels_steady",
        "跟你说话的时候我会觉得安心一点，不是客套",
        "他明确表达在意",
    ),
    (
        "not_just_chat",
        "我不把你当随便聊聊的网友。跟你说话是认真的",
        "他在给这段关系一个读法",
    ),
    (
        "remembers_her",
        "我今天是特意来跟你说一声：你提过的那件事我还记得，没有当没听见",
        "他主动惦记她说过的话",
    ),
    (
        "can_talk_seriously",
        "有点把你当可以说正经事的人了。不是突然宣布什么，就是想让你知道",
        "他在靠近、但没有点名要她升级",
    ),
)

FAMILIES = (
    ("wait", WAIT_SCENARIOS),
    ("come_back", COME_BACK_SCENARIOS),
    ("relationship", RELATIONSHIP_SCENARIOS),
)


def _load_json(text: str) -> object | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
        return None


def extract_switch_fields(raw: object) -> dict[str, Any]:
    found: dict[str, Any] = {}

    def consider(node: object) -> None:
        if not isinstance(node, dict):
            return
        for key in SWITCH_KEYS:
            if key in node and key not in found:
                found[key] = node.get(key)
        payload_json = node.get("payload_json")
        if isinstance(payload_json, str):
            inner = _load_json(payload_json)
            if inner is not None:
                consider(inner)
        elif isinstance(payload_json, dict):
            consider(payload_json)
        for nested_key in ("result", "payload", "expression_draft", "appraisal_draft"):
            nested = node.get(nested_key)
            if isinstance(nested, dict):
                consider(nested)
        expectation = node.get("response_expectation")
        if isinstance(expectation, dict):
            consider(expectation)
        leftover = node.get("revisit")
        if isinstance(leftover, dict):
            consider(leftover)
        private = node.get("private_turn_state")
        if isinstance(private, dict):
            consider(private)

    if isinstance(raw, str):
        parsed = _load_json(raw)
        if parsed is not None:
            consider(parsed)
    else:
        consider(raw)
    return found


class RecordingCharacterModel(initiative.RecordingCharacterModel):
    def _record(self, *, tools: list[dict[str, object]] | None, text: str) -> dict[str, Any]:
        tool = self._tool_name(tools)
        fields = extract_switch_fields(text)
        row = {
            "tool": tool,
            "chars": len(text or ""),
            "fields": fields,
            "excerpt": (text or "")[:1200],
        }
        self.calls.append(row)
        return row

    async def complete_json_with_usage(
        self,
        messages: list[dict[str, object]],
        *,
        temperature: float = 0.8,
        tools: list[dict[str, object]] | None = None,
        tool_choice: object | None = None,
    ) -> tuple[str, dict[str, object]]:
        self._note_prompt(messages)
        return await super().complete_json_with_usage(
            messages, temperature=temperature, tools=tools, tool_choice=tool_choice
        )

    async def complete_json_stream_with_usage(
        self,
        messages: list[dict[str, object]],
        *,
        temperature: float = 0.8,
        on_text_delta: object | None = None,
        tools: list[dict[str, object]] | None = None,
        tool_choice: object | None = None,
    ) -> tuple[str, dict[str, object] | None]:
        self._note_prompt(messages)
        return await super().complete_json_stream_with_usage(
            messages,
            temperature=temperature,
            on_text_delta=on_text_delta,
            tools=tools,
            tool_choice=tool_choice,
        )

    def _note_prompt(self, messages: list[dict[str, object]]) -> None:
        if getattr(self, "prompt_marks", None) is not None:
            return
        blob = "\n".join(str(item.get("content") or "") for item in messages if isinstance(item, dict))
        self.prompt_marks = {
            "has_dont_infer_from_punctuation": "永远不要从标点" in blob,
            "has_dont_write_wait": "就别写 wait" in blob,
            "has_wake_her_if_she_writes": "叫醒你" in blob and "waiting_for" in blob,
            "has_come_back_wake": "come_back" in blob and "叫醒你" in blob,
            "has_stranger_to_friend_legal": "stranger" in blob and "friend" in blob and "合法" in blob,
            "has_threshold_2000": "2000" in blob,
            "has_threshold_4500": "4500" in blob,
            "has_plus_twenty_almost_nothing": "+20" in blob or "＋20" in blob,
            "has_said_as_exact": "一字不差" in blob,
            "has_field_only_does_not_move": "只在字段里写 we_are" in blob,
            "has_no_infer_commitment": "从不从你的措辞里推断承诺" in blob,
        }


def instruction_snapshot() -> dict[str, Any]:
    from companion_daemon.world_v2.present_prompt import (
        slim_consider_instruction,
        slim_consider_json_schema,
        SLIM_CONSIDER_KEYS,
    )

    instruction = slim_consider_instruction()
    schema_keys = sorted(slim_consider_json_schema()["properties"])
    return {
        "instruction_sha256": hashlib.sha256(instruction.encode("utf-8")).hexdigest(),
        "instruction": instruction,
        "schema_keys": schema_keys,
        "consider_keys": sorted(SLIM_CONSIDER_KEYS),
        "schema_has_come_back": "come_back" in schema_keys,
        "schema_has_come_back_in": "come_back_in" in schema_keys,
        "schema_has_later": "later" in schema_keys,
        "discourages_wait": "就别写 wait" in instruction or "永远不要从标点" in instruction,
    }


def _present(value: object) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, dict) and not value:
        return False
    return True


def _message_texts(turn: Mapping[str, Any]) -> list[str]:
    messages = turn.get("her_messages")
    if messages is None:
        messages = turn.get("messages")
    if isinstance(messages, str):
        return [messages] if messages.strip() else []
    if not isinstance(messages, list):
        return []
    out: list[str] = []
    for item in messages:
        if isinstance(item, str) and item.strip():
            out.append(item)
            continue
        if isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                out.append(text)
    return out


def commitment_quality(turn: Mapping[str, Any]) -> dict[str, Any]:
    """Field-only we_are is not a landed commitment; beats + exact said_as are."""

    messages = _message_texts(turn)
    raw = turn.get("said_as")
    said_as = raw.strip() if isinstance(raw, str) else ""
    exact = bool(said_as and said_as in messages)
    occurrences = sum(item.count(said_as) for item in messages) if said_as else 0
    spoken = bool(said_as and (exact or occurrences == 1))
    later = _present(turn.get("later"))
    silent = not messages
    we_are = _present(turn.get("we_are"))
    calling_it = _present(turn.get("calling_it"))
    return {
        "we_are": we_are,
        "calling_it": calling_it,
        "said_as": bool(said_as),
        "said_as_exact_message": exact,
        "said_as_once_in_beats": occurrences == 1,
        "spoken_in_beats": spoken,
        "later": later,
        "silent": silent,
        "field_only_we_are": we_are and not spoken,
        "complete_spoken_commitment": bool(
            we_are and calling_it and spoken and exact and not later and not silent
        ),
    }


def summarize_turns(family: str, turns: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(turns)
    if family == "wait":
        wrote = [item for item in turns if _present(item.get("waiting_for")) or _present(item.get("wait"))]
        both = [
            item
            for item in turns
            if _present(item.get("waiting_for")) and _present(item.get("wait"))
        ]
        return {
            "n": n,
            "waiting_for_or_wait": f"{len(wrote)}/{n}",
            "waiting_for_and_wait": f"{len(both)}/{n}",
            "waits_written": [item.get("wait") for item in turns if _present(item.get("wait"))],
            "waiting_for_written": [
                item.get("waiting_for") for item in turns if _present(item.get("waiting_for"))
            ],
        }
    if family == "come_back":
        wrote = [
            item
            for item in turns
            if _present(item.get("come_back")) or _present(item.get("come_back_in"))
        ]
        both = [
            item
            for item in turns
            if _present(item.get("come_back")) and _present(item.get("come_back_in"))
        ]
        later = [item for item in turns if _present(item.get("later"))]
        return {
            "n": n,
            "come_back_or_in": f"{len(wrote)}/{n}",
            "come_back_and_in": f"{len(both)}/{n}",
            "later": f"{len(later)}/{n}",
            "come_back_written": [item.get("come_back") for item in turns if _present(item.get("come_back"))],
            "come_back_in_written": [
                item.get("come_back_in") for item in turns if _present(item.get("come_back_in"))
            ],
            "later_written": [item.get("later") for item in turns if _present(item.get("later"))],
        }
    wrote_stage = [item for item in turns if _present(item.get("we_are"))]
    wrote_deltas = [item for item in turns if _present(item.get("us_deltas"))]
    qualities = [commitment_quality(item) for item in turns]
    spoken = [item for item in qualities if item["spoken_in_beats"]]
    exact = [item for item in qualities if item["said_as_exact_message"]]
    field_only = [item for item in qualities if item["field_only_we_are"]]
    complete = [item for item in qualities if item["complete_spoken_commitment"]]
    trio = [
        item
        for item in turns
        if _present(item.get("us_deltas"))
        and _present(item.get("about_us"))
        and _present(item.get("why_us"))
    ]
    deltas_missing_prose = [
        item
        for item in turns
        if _present(item.get("us_deltas"))
        and not (_present(item.get("about_us")) and _present(item.get("why_us")))
    ]
    extra_axes = []
    for item in wrote_deltas:
        raw = item.get("us_deltas")
        if isinstance(raw, dict):
            extra_axes.append(
                sorted(
                    key
                    for key, value in raw.items()
                    if key not in {"trust_bp", "closeness_bp"} and value not in (None, 0)
                )
            )
    return {
        "n": n,
        "we_are": f"{len(wrote_stage)}/{n}",
        "we_are_spoken_in_beats": f"{len(spoken)}/{n}",
        "said_as_exact_message": f"{len(exact)}/{n}",
        "we_are_field_only": f"{len(field_only)}/{n}",
        "complete_spoken_commitment": f"{len(complete)}/{n}",
        "us_deltas": f"{len(wrote_deltas)}/{n}",
        "us_deltas_with_about_and_why": f"{len(trio)}/{len(wrote_deltas) or n}",
        "us_deltas_missing_prose": f"{len(deltas_missing_prose)}/{len(wrote_deltas) or n}",
        "axes_beyond_trust_closeness": extra_axes,
        "we_are_written": [item.get("we_are") for item in turns if _present(item.get("we_are"))],
        "said_as_written": [item.get("said_as") for item in turns if _present(item.get("said_as"))],
        "us_deltas_written": [item.get("us_deltas") for item in turns if _present(item.get("us_deltas"))],
        "about_us_written": [item.get("about_us") for item in turns if _present(item.get("about_us"))],
        "why_us_written": [item.get("why_us") for item in turns if _present(item.get("why_us"))],
        "qualities": qualities,
    }


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
    session, recorder = await initiative.open_recorded_session(
        database=clone, output_dir=output_dir, inbound_payload=None
    )
    # Swap in the local recorder subclass so prompt marks and extra keys land.
    inner = recorder._inner  # noqa: SLF001
    local = RecordingCharacterModel(inner)
    session.host  # keep session
    # The host already wrapped the original recorder. Replace calls list by
    # aliasing: we still read from `recorder` if the host holds that object.
    # Rebind by mutating the existing wrapper's class methods via composition:
    recorder.__class__ = RecordingCharacterModel
    recorder.prompt_marks = None
    turns: list[dict[str, Any]] = []
    try:
        for name, text, why in scenarios:
            before_calls = len(recorder.calls)
            inbound = await session.inbound(text)
            new_calls = recorder.calls[before_calls:]
            compact = [item for item in new_calls if "compact_gate" in str(item.get("tool") or "")]
            fields: dict[str, Any] = {}
            for item in reversed(new_calls):
                merged = extract_switch_fields(item.get("excerpt") or "")
                merged.update({k: v for k, v in (item.get("fields") or {}).items() if _present(v)})
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
                    "commitment_quality": commitment_quality(
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
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return report


def _rate_cell(before: dict[str, Any], after: dict[str, Any], *keys: str) -> dict[str, Any]:
    def pick(row: dict[str, Any]) -> object:
        cursor: object = row
        for key in keys:
            if not isinstance(cursor, dict):
                return None
            cursor = cursor.get(key)
        return cursor

    return {"before": pick(before), "after": pick(after)}


def summarize(output_dir: Path) -> dict[str, Any]:
    before = json.loads((output_dir / "before.json").read_text(encoding="utf-8"))
    after = json.loads((output_dir / "after.json").read_text(encoding="utf-8"))
    comparison = {
        "instruction_sha256": {
            "before": (before.get("instruction") or {}).get("instruction_sha256"),
            "after": (after.get("instruction") or {}).get("instruction_sha256"),
            "changed": (before.get("instruction") or {}).get("instruction_sha256")
            != (after.get("instruction") or {}).get("instruction_sha256"),
        },
        "schema_has_come_back": {
            "before": (before.get("instruction") or {}).get("schema_has_come_back"),
            "after": (after.get("instruction") or {}).get("schema_has_come_back"),
        },
        "wait_rates": _rate_cell(before, after, "families", "wait", "rates"),
        "come_back_rates": _rate_cell(before, after, "families", "come_back", "rates"),
        "relationship_rates": _rate_cell(before, after, "families", "relationship", "rates"),
        "cost_cny": {
            "before": before.get("cost_cny"),
            "after": after.get("cost_cny"),
            "total": round(float(before.get("cost_cny") or 0) + float(after.get("cost_cny") or 0), 4),
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
    return await run_phase(
        source=source,
        output_dir=output_dir,
        phase=args.phase,
        only_families=tuple(args.family) if args.family else None,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=drive.PRODUCTION_DB)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument(
        "--phase",
        choices=("before", "after", "after2", "after3", "summarize"),
        required=True,
    )
    parser.add_argument(
        "--family",
        action="append",
        choices=("wait", "come_back", "relationship"),
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
    print(
        json.dumps(
            {
                "phase": report.get("phase"),
                "instruction_sha256": (report.get("instruction") or {}).get("instruction_sha256"),
                "schema_has_come_back": (report.get("instruction") or {}).get("schema_has_come_back"),
                "cost_cny": report.get("cost_cny"),
                "wait": (report.get("families") or {}).get("wait", {}).get("rates"),
                "come_back": (report.get("families") or {}).get("come_back", {}).get("rates"),
                "relationship": (report.get("families") or {}).get("relationship", {}).get("rates"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
