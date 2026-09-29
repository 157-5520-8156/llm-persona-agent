#!/usr/bin/env python3
"""Clone-only proof that a living private impression can open a social occasion.

Never writes data/, never talks to 8787 or NapCat, never restarts launchd.
Does not edit proactive_action.py; applies a runtime monkeypatch documented
in output/impression-initiative/REPORT.md.

Usage::

    .venv/bin/python scripts/probe_impression_initiative.py
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import importlib.util
import inspect
import json
import logging
import os
from pathlib import Path
import re
import sys
import textwrap
from typing import Any, Callable, Literal

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

OUTPUT = (REPO / "output" / "impression-initiative").resolve()
WORLD_ID = "world:companion-v2:qq-c2c:geoff"
ACTOR = "agent:companion"
_LOG = logging.getLogger("probe_impression_initiative")
TRIAL_COUNT = 4
TICK_AFTER = timedelta(hours=8, minutes=10)

_DRIVE_PATH = REPO / "scripts" / "drive_production_lanes.py"
_spec = importlib.util.spec_from_file_location("drive_production_lanes", _DRIVE_PATH)
drive = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["drive_production_lanes"] = drive
_spec.loader.exec_module(drive)


def apply_private_impression_proactive_runtime_patch() -> dict[str, str]:
    """Monkeypatch proactive_action.py + envelope Literals. Does not write those files.

    Copy-paste target for the other lane: see REPORT.md section B.
    """

    from companion_daemon.world_v2 import proactive_action as pa
    from companion_daemon.world_v2 import proposal_envelope as env
    from companion_daemon.world_v2.social_initiative import (
        private_impression_opportunity_context,
        private_impression_source_binds_head,
    )

    notes: dict[str, str] = {}

    class PatchedProactiveOpportunity(pa.ProactiveOpportunity):
        source_kind: Literal[
            "settled_world_event",
            "thread",
            "commitment",
            "spontaneous_contact",
            "ambient_presence",
            "post_silent",
            "situation_change",
            "expired_expectation",
            "revisit_intention",
            "private_impression",
        ]

    pa.ProactiveOpportunity = PatchedProactiveOpportunity
    notes["ProactiveOpportunity.source_kind"] = "added private_impression via subclass"

    original_context = pa._proactive_opportunity_context

    def patched_context(*, opportunity, event, head, projection):
        if opportunity.source_kind == "private_impression":
            return private_impression_opportunity_context()
        return original_context(
            opportunity=opportunity, event=event, head=head, projection=projection
        )

    pa._proactive_opportunity_context = patched_context
    notes["_proactive_opportunity_context"] = "eligibility text only; no reflection_summary"

    frame_src = inspect.getsource(pa._proactive_source_frame)
    frame_old = '''            "expired_expectation",
            "revisit_intention",
        }:'''
    frame_new = '''            "expired_expectation",
            "revisit_intention",
            "private_impression",
        }:'''
    if frame_old not in frame_src:
        raise RuntimeError("proactive source-frame kind set drifted; cannot patch")
    frame_globals = dict(pa._proactive_source_frame.__globals__)
    exec(compile(frame_src.replace(frame_old, frame_new, 1), "<frame-patch>", "exec"), frame_globals)
    pa._proactive_source_frame = frame_globals["_proactive_source_frame"]
    notes["_proactive_source_frame"] = "kind set includes private_impression"

    audit_src = textwrap.dedent(inspect.getsource(pa.ProactiveDeliberationTurn.audit))
    audit_old = "    else:\n        valid_source = False\n"
    audit_new = (
        "    elif opportunity.source_kind == \"private_impression\":\n"
        "        valid_source = private_impression_source_binds_head(\n"
        "            projection=projection,\n"
        "            event=event,\n"
        "            opportunity=opportunity,\n"
        "        )\n"
        "    else:\n"
        "        valid_source = False\n"
    )
    if audit_old not in audit_src:
        raise RuntimeError("proactive audit valid_source else-branch drifted; cannot patch")
    audit_globals = dict(pa.ProactiveDeliberationTurn.audit.__globals__)
    audit_globals["private_impression_source_binds_head"] = (
        private_impression_source_binds_head
    )
    exec(compile(audit_src.replace(audit_old, audit_new, 1), "<audit-patch>", "exec"), audit_globals)
    pa.ProactiveDeliberationTurn.audit = audit_globals["audit"]
    notes["ProactiveDeliberationTurn.audit"] = "binds living PrivateImpressionAccepted head"

    def _widen_envelope(model):
        class Wide(model):
            source_kind: Literal[
                "settled_world_event",
                "thread",
                "commitment",
                "spontaneous_contact",
                "response_gap",
                "ambient_presence",
                "post_silent",
                "situation_change",
                "expired_expectation",
                "revisit_intention",
                "private_impression",
            ]

        Wide.__name__ = model.__name__
        Wide.__qualname__ = model.__qualname__
        return Wide

    env.ProactiveOpportunityDecision = _widen_envelope(env.ProactiveOpportunityDecision)
    env.ProactiveExpressionSourceBinding = _widen_envelope(
        env.ProactiveExpressionSourceBinding
    )
    env.ProactiveExpressionPlanSourceBindingV2 = _widen_envelope(
        env.ProactiveExpressionPlanSourceBindingV2
    )
    pa.ProactiveOpportunityDecision = env.ProactiveOpportunityDecision
    pa.ProactiveExpressionSourceBinding = env.ProactiveExpressionSourceBinding
    pa.ProactiveExpressionPlanSourceBindingV2 = env.ProactiveExpressionPlanSourceBindingV2
    notes["proposal_envelope Literals"] = (
        "Decision/Binding source_kind include private_impression (runtime only)"
    )
    return notes


class RecordingCharacterModel:
    """Keep provider prompts and completions so we can audit redaction."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.calls: list[dict[str, Any]] = []
        self.model = getattr(inner, "model", "recording")
        self.provider = getattr(inner, "provider", "recording")
        self.supports_required_tool_choice = True
        self.supports_strict_tool_choice = bool(
            getattr(inner, "supports_strict_tool_choice", True)
        )
        self.reports_exact_request_emission = bool(
            getattr(inner, "reports_exact_request_emission", False)
        )

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def _tool_name(self, tools: list[dict[str, object]] | None) -> str:
        if not tools:
            return ""
        function = tools[0].get("function") if isinstance(tools[0], dict) else None
        name = function.get("name") if isinstance(function, dict) else ""
        return str(name or "")

    def _record(
        self,
        *,
        tools: list[dict[str, object]] | None,
        messages: list[dict[str, object]],
        text: str,
    ) -> dict[str, Any]:
        tool = self._tool_name(tools)
        row = {
            "tool": tool,
            "chars": len(text or ""),
            "excerpt": (text or "")[:1_200],
            "prompt_view": extract_prompt_view(messages),
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
        text, usage = await self._inner.complete_json_with_usage(
            messages, temperature=temperature, tools=tools, tool_choice=tool_choice
        )
        self._record(tools=tools, messages=messages, text=text)
        return text, usage

    async def complete_json_stream_with_usage(
        self,
        messages: list[dict[str, object]],
        *,
        temperature: float = 0.8,
        on_text_delta: Callable[[str], object] | None = None,
        tools: list[dict[str, object]] | None = None,
        tool_choice: object | None = None,
    ) -> tuple[str, dict[str, object] | None]:
        text, usage = await self._inner.complete_json_stream_with_usage(
            messages,
            temperature=temperature,
            on_text_delta=on_text_delta,
            tools=tools,
            tool_choice=tool_choice,
        )
        self._record(tools=tools, messages=messages, text=text)
        return text, usage


def _parse_jsonish(text: object) -> object | None:
    if isinstance(text, dict):
        return text
    if not isinstance(text, str):
        return None
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


def extract_prompt_view(messages: list[dict[str, object]]) -> dict[str, Any]:
    """Pull the inner-life / advisory packet she actually saw."""

    blob: dict[str, Any] = {
        "message_count": len(messages),
        "advisories": [],
        "private_impressions": [],
        "lived_moment": None,
        "source_refs": [],
        "advisory_values": [],
        "raw_contains_inner_life": False,
    }
    for message in messages:
        content = message.get("content")
        parsed = _parse_jsonish(content)
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        if isinstance(text, str) and "inner_life_snapshot" in text:
            blob["raw_contains_inner_life"] = True
        nodes: list[object] = [parsed] if parsed is not None else []
        if isinstance(parsed, dict) and "inner_life_snapshot" not in parsed:
            nested = _parse_jsonish(parsed.get("content")) if isinstance(parsed, dict) else None
            if nested is not None:
                nodes.append(nested)
        for node in nodes:
            _collect_view(node, blob)
        if isinstance(text, str) and not blob["advisory_values"]:
            for match in re.finditer(r"Choose freely: now, later, or silent; kind=([a-z_]+)", text):
                blob.setdefault("kind_markers", []).append(match.group(1))
    return blob


def _collect_view(node: object, blob: dict[str, Any]) -> None:
    if not isinstance(node, dict):
        return
    snapshot = node.get("inner_life_snapshot")
    if isinstance(snapshot, dict):
        _collect_view(snapshot, blob)
    materials = node.get("materials")
    if isinstance(materials, dict):
        lived = materials.get("lived_moment")
        if isinstance(lived, str):
            blob["lived_moment"] = lived
        impressions = materials.get("private_impressions")
        if isinstance(impressions, list):
            for item in impressions:
                if not isinstance(item, dict):
                    continue
                blob["private_impressions"].append(
                    {
                        "source_ref": item.get("source_ref"),
                        "status": item.get("status"),
                        "has_reflection_summary": isinstance(
                            item.get("reflection_summary"), str
                        ),
                        "reflection_summary_chars": (
                            len(item["reflection_summary"])
                            if isinstance(item.get("reflection_summary"), str)
                            else 0
                        ),
                    }
                )
        advisories = materials.get("advisories")
        items = (
            advisories.get("items")
            if isinstance(advisories, dict)
            else advisories
            if isinstance(advisories, list)
            else ()
        )
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                value = item.get("value") if isinstance(item.get("value"), dict) else item
                kind = value.get("kind") if isinstance(value, dict) else item.get("kind")
                candidates = (
                    value.get("candidates")
                    if isinstance(value, dict)
                    else item.get("candidates")
                )
                candidate_refs = (
                    value.get("candidate_refs")
                    if isinstance(value, dict)
                    else item.get("candidate_refs")
                )
                source_refs = (
                    value.get("source_refs")
                    if isinstance(value, dict)
                    else item.get("source_refs")
                )
                guidance = None
                if isinstance(candidates, list) and candidates:
                    first = candidates[0]
                    if isinstance(first, dict):
                        guidance = first.get("value")
                    elif isinstance(first, str):
                        guidance = first
                if isinstance(item.get("value"), str):
                    guidance = item.get("value")
                blob["advisories"].append(
                    {
                        "kind": kind,
                        "source_ref": item.get("source_ref"),
                        "candidate_refs": candidate_refs,
                        "source_refs": source_refs,
                        "guidance": guidance,
                    }
                )
                if isinstance(guidance, str):
                    blob["advisory_values"].append(guidance)
        refs = node.get("source_refs")
        if isinstance(refs, list):
            blob["source_refs"] = [ref for ref in refs if isinstance(ref, str)]
    slices = node.get("slices")
    if isinstance(slices, dict):
        lane = slices.get("advisories")
        items = lane.get("items") if isinstance(lane, dict) else None
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                value = item.get("value")
                if isinstance(value, dict) and value.get("kind") == "proactive_opportunity":
                    candidates = value.get("candidates")
                    guidance = None
                    if isinstance(candidates, list) and candidates:
                        first = candidates[0]
                        if isinstance(first, dict):
                            guidance = first.get("value")
                    blob["advisories"].append(
                        {
                            "kind": "proactive_opportunity",
                            "source_ref": item.get("source_ref") or item.get("item_ref"),
                            "candidate_refs": value.get("candidate_refs"),
                            "source_refs": value.get("source_refs"),
                            "guidance": guidance,
                        }
                    )
                    if isinstance(guidance, str):
                        blob["advisory_values"].append(guidance)
        impressions = slices.get("private_impressions")
        items = impressions.get("items") if isinstance(impressions, dict) else None
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                value = item.get("value") if isinstance(item.get("value"), dict) else {}
                blob["private_impressions"].append(
                    {
                        "source_ref": item.get("source_ref") or item.get("item_ref"),
                        "status": value.get("status") if isinstance(value, dict) else None,
                        "has_reflection_summary": isinstance(
                            value.get("reflection_summary"), str
                        )
                        if isinstance(value, dict)
                        else False,
                        "reflection_summary_chars": (
                            len(value["reflection_summary"])
                            if isinstance(value, dict)
                            and isinstance(value.get("reflection_summary"), str)
                            else 0
                        ),
                    }
                )
    for child in node.values():
        if isinstance(child, dict) and (
            "inner_life_snapshot" in child or "materials" in child or "slices" in child
        ):
            _collect_view(child, blob)


def _open_session(database: Path, output_dir: Path) -> tuple[Any, RecordingCharacterModel]:
    from companion_daemon.config import Settings
    from companion_daemon.llm import DeepSeekChatModel
    from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore
    from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host

    if drive._is_production_path(database):
        raise SystemExit(f"refusing to open production ledger for write: {database}")
    sidecar = output_dir / f"{database.stem}.sidecars"
    sidecar.mkdir(parents=True, exist_ok=True)
    os.environ["WORLD_V2_PRIVATE_IMPRESSION_DAILY_MODEL_CALL_LIMIT"] = "3"
    os.environ["WORLD_V2_PRIVATE_IMPRESSION_MIN_INTERVAL_SECONDS"] = "90"
    os.environ["WORLD_V2_PRIVATE_IMPRESSION_IDLE_AFTER_USER_SECONDS"] = "30"
    settings = Settings(
        database_path=database,
        world_v2_external_perception_mode="off",
        world_v2_external_perception_sidecar_path=sidecar / "perception.sqlite",
        attachment_cache_path=sidecar / "attachments",
        world_v2_text_endpoint_enabled=False,
    )
    recipient_id = drive._recipient_id(settings)
    delivery = drive.CaptureDelivery()
    usage_store = WorldV2UsageStore(path=str(database))
    inner = DeepSeekChatModel(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        thinking_enabled=False,
        max_completion_tokens=4_096,
        usage_observer=usage_store.record,
    )
    recorder = RecordingCharacterModel(inner)
    host = build_qq_c2c_host(
        settings=settings,
        recipient_id=recipient_id,
        bootstrap_at=datetime.now(UTC),
        delivery=delivery,
        model=recorder,
        media_preview=None,
        media_transport=None,
        use_configured_recall_embedding=False,
    )
    session = drive.DriveSession(
        database=database,
        recipient_id=recipient_id,
        delivery=delivery,
        host=host,
        clock=datetime.now(UTC),
    )
    return session, recorder


def _ledger(session: drive.DriveSession):
    platform = getattr(session.host, "_host", None)
    application = getattr(platform, "_application", None)
    return getattr(application, "_ledger", None)


async def inspect_opportunity(session: drive.DriveSession) -> dict[str, Any] | None:
    from companion_daemon.world_v2.social_initiative import (
        SocialInitiativeCompiler,
        SocialInitiativePolicy,
    )

    ledger = _ledger(session)
    if ledger is None:
        return None
    projection = ledger.project()
    compiler = SocialInitiativeCompiler(
        ledger=ledger, actor_ref=ACTOR, policy=SocialInitiativePolicy()
    )
    opportunity = await compiler.next_opportunity(projection)
    impressions = []
    for item in getattr(projection, "private_impressions", ()):
        origin = getattr(item, "origin", None)
        impressions.append(
            {
                "impression_id": item.impression_id,
                "status": item.status,
                "accepted_event_ref": getattr(origin, "accepted_event_ref", None),
                "summary_chars": len(item.reflection_summary or "")
                if isinstance(getattr(item, "reflection_summary", None), str)
                else 0,
            }
        )
    open_processes = []
    for item in getattr(projection, "trigger_processes", ()):
        if (
            item.process_kind == "proactive_action_deliberation"
            and item.state in {"open", "claimed"}
        ):
            open_processes.append(
                {
                    "trigger_ref": item.trigger_ref,
                    "state": item.state,
                    "source_evidence_ref": item.source_evidence_ref,
                }
            )
    if opportunity is None:
        return {
            "opportunity": None,
            "logical_time": projection.logical_time.isoformat()
            if projection.logical_time
            else None,
            "active_impressions": impressions,
            "open_proactive_processes": open_processes,
        }
    return {
        "opportunity": {
            "source_kind": opportunity.source_kind,
            "source_id": opportunity.source_id,
            "source_event_ref": opportunity.source_event_ref,
            "consideration_id": opportunity.consideration_id,
            "scheduled_for": opportunity.scheduled_for.isoformat(),
            "cadence_reason_codes": list(opportunity.cadence_reason_codes),
        },
        "logical_time": projection.logical_time.isoformat()
        if projection.logical_time
        else None,
        "active_impressions": impressions,
        "open_proactive_processes": open_processes,
    }


def impression_events(clone: Path, after_seq: int) -> list[dict[str, Any]]:
    conn = drive.open_ro(clone)
    try:
        rows = drive.events_after(conn, WORLD_ID, after_seq)
    finally:
        conn.close()
    keep = {
        "PrivateImpressionAccepted",
        "TriggerProcessOpened",
        "TriggerProcessCompleted",
        "ActionAuthorized",
        "ModelResultRecorded",
        "ExpressionPlanAccepted",
        "RandomDrawRecorded",
        "CharacterInteriorTechnicalFailureRecorded",
        "TechnicalFailureRecorded",
    }
    out: list[dict[str, Any]] = []
    for seq, event in rows:
        kind = drive.event_type(event)
        if kind not in keep:
            continue
        payload = event.get("_payload") or {}
        process = payload.get("process") if isinstance(payload.get("process"), dict) else {}
        action = payload.get("action") if isinstance(payload.get("action"), dict) else {}
        impression = (
            payload.get("impression") if isinstance(payload.get("impression"), dict) else {}
        )
        row = {
            "seq": seq,
            "event_type": kind,
            "logical_time": event.get("logical_time"),
            "process_kind": process.get("process_kind") or payload.get("process_kind"),
            "trigger_id": process.get("trigger_id") or payload.get("trigger_id"),
            "trigger_ref": process.get("trigger_ref") or payload.get("trigger_ref"),
            "runtime_outcome_ref": process.get("runtime_outcome_ref")
            or payload.get("runtime_outcome_ref"),
            "source_evidence_ref": process.get("source_evidence_ref")
            or payload.get("source_evidence_ref"),
            "action_kind": action.get("kind"),
            "reflection_summary": impression.get("reflection_summary")
            or payload.get("reflection_summary"),
            "failure_code": payload.get("failure_code") or payload.get("reason_code"),
        }
        if kind == "ActionAuthorized" and isinstance(action, dict):
            payload_text = (
                action.get("payload").get("text")
                if isinstance(action.get("payload"), dict)
                else None
            )
            row["action_text"] = action.get("text") or action.get("body") or payload_text
        out.append(row)
    return out


def _usage_rows(clone: Path, since_id: int) -> list[dict[str, Any]]:
    conn = drive.open_ro(clone)
    try:
        _, _, items = drive.usage_sum_cny(conn, since_id=since_id)
    finally:
        conn.close()
    return items


def _redaction_proof(
    *,
    summaries: list[str],
    prompt_view: dict[str, Any],
    advisory_values: list[str],
) -> dict[str, Any]:
    joined_advisories = "\n".join(advisory_values)
    leaked = [
        summary
        for summary in summaries
        if summary and summary in joined_advisories
    ]
    impression_refs = [
        item.get("source_ref")
        for item in prompt_view.get("private_impressions") or []
        if isinstance(item.get("source_ref"), str)
    ]
    return {
        "advisory_contains_raw_summary": bool(leaked),
        "leaked_summaries": leaked[:2],
        "advisory_values": advisory_values[:4],
        "impression_source_refs": impression_refs,
        "lived_moment": prompt_view.get("lived_moment"),
        "kind_markers": prompt_view.get("kind_markers") or [],
        "proactive_kinds": [
            (item.get("candidate_refs") or [None])[0]
            for item in prompt_view.get("advisories") or []
            if item.get("kind") == "proactive_opportunity"
            or (
                isinstance(item.get("candidate_refs"), list)
                and item["candidate_refs"]
                and str(item["candidate_refs"][0]).startswith("private_impression:")
            )
        ],
    }


async def farm_impression(clone: Path) -> dict[str, Any]:
    started_seq = drive.current_seq(clone)
    usage_from = drive.current_usage_id(clone)
    session, recorder = _open_session(clone, OUTPUT)
    try:
        clock = await session.logical_time()
        before = await inspect_opportunity(session)
        drains = await session.drain_loop(rounds=16, background=16)
        after = await inspect_opportunity(session)
        extra = 0
        while after and after.get("open_proactive_processes") and extra < 8:
            extra += 1
            drains.extend(await session.drain_loop(rounds=4, background=8))
            after = await inspect_opportunity(session)
        if extra:
            farm_extra = extra
        else:
            farm_extra = 0
        events = impression_events(clone, started_seq)
        accepted = [item for item in events if item["event_type"] == "PrivateImpressionAccepted"]
        usage = _usage_rows(clone, usage_from)
        return {
            "logical_time_at_open": clock.isoformat(),
            "before": before,
            "after": after,
            "drains": drains,
            "accepted": accepted,
            "events": events,
            "usage": usage,
            "inbound_messages": 0,
            "open_process_extra_drains": farm_extra,
            "farm_calls": [
                {
                    "tool": item.get("tool"),
                    "excerpt": item.get("excerpt"),
                }
                for item in recorder.calls
                if "impression" in str(item.get("tool") or "").lower()
                or "private" in str(item.get("tool") or "").lower()
            ],
        }
    finally:
        await session.close()


async def run_trial(source: Path, trial_dir: Path, index: int) -> dict[str, Any]:
    clone = trial_dir / f"trial-{index}.sqlite"
    drive.clone_ledger(source, clone)
    started_seq = drive.current_seq(clone)
    usage_from = drive.current_usage_id(clone)
    session, recorder = _open_session(clone, trial_dir)
    try:
        before_tick = await inspect_opportunity(session)
        now = await session.logical_time()
        tick = await session.tick_to(now + TICK_AFTER, reason=f"impression-init-{index}", run_life=False)
        due = await inspect_opportunity(session)
        drains = await session.drain_loop(rounds=12, background=16)
        after = await inspect_opportunity(session)
        events = impression_events(clone, started_seq)
        proactive_calls = [
            item
            for item in recorder.calls
            if "proactive" in str(item.get("tool") or "").lower()
        ]
        authorized = [
            item
            for item in events
            if item["event_type"] == "ActionAuthorized" and item.get("action_kind") == "proactive_message"
        ]
        opened = [
            item
            for item in events
            if item["event_type"] == "TriggerProcessOpened"
            and item.get("process_kind") == "proactive_action_deliberation"
        ]
        completed = [
            item
            for item in events
            if item["event_type"] == "TriggerProcessCompleted"
            and "proactive" in str(item.get("runtime_outcome_ref") or item.get("trigger_id") or "")
        ]
        summaries = [
            str(item["reflection_summary"])
            for item in events
            if item.get("reflection_summary")
        ]
        prompt_view = (proactive_calls[0].get("prompt_view") or {}) if proactive_calls else {}
        advisory_values = list(prompt_view.get("advisory_values") or [])
        proof = _redaction_proof(
            summaries=summaries
            or [
                str(item.get("reflection_summary") or "")
                for item in impression_events(source, 0)
                if item.get("event_type") == "PrivateImpressionAccepted"
            ],
            prompt_view=prompt_view,
            advisory_values=advisory_values,
        )
        extra_ticks = 0
        while not authorized and extra_ticks < 2:
            completed_now = [
                item
                for item in completed
                if str(item.get("runtime_outcome_ref") or "").startswith("proactive:")
            ]
            if not opened and not proactive_calls:
                break
            if completed_now and any(
                "authorized" in str(item.get("runtime_outcome_ref") or "")
                for item in completed_now
            ):
                break
            extra_ticks += 1
            now = await session.logical_time()
            await session.tick_to(
                now + TICK_AFTER,
                reason=f"impression-init-{index}-extra-{extra_ticks}",
                run_life=False,
            )
            drains.extend(await session.drain_loop(rounds=8, background=12))
            events = impression_events(clone, started_seq)
            authorized = [
                item
                for item in events
                if item["event_type"] == "ActionAuthorized"
                and item.get("action_kind") == "proactive_message"
            ]
            completed = [
                item
                for item in events
                if item["event_type"] == "TriggerProcessCompleted"
            ]
            proactive_calls = [
                item
                for item in recorder.calls
                if "proactive" in str(item.get("tool") or "").lower()
            ]
        her_words = None
        timing = None
        if proactive_calls:
            excerpt = proactive_calls[0].get("excerpt") or ""
            parsed = _parse_jsonish(excerpt)
            if isinstance(parsed, dict):
                timing = parsed.get("timing_choice")
                messages = parsed.get("messages")
                her_words = messages if messages else parsed.get("impulse_summary")
        usage = _usage_rows(clone, usage_from)
        asked = bool(proactive_calls) or any(
            item.get("process_kind") == "proactive_action_deliberation" for item in opened
        )
        minted = (due.get("opportunity") or {}).get("source_kind") == "private_impression"
        return {
            "index": index,
            "clone": str(clone),
            "before_tick": before_tick,
            "tick": tick,
            "due": due,
            "after": after,
            "drains": drains,
            "opened": opened,
            "completed": completed,
            "authorized": authorized,
            "proactive_calls": [
                {
                    "tool": item.get("tool"),
                    "excerpt": item.get("excerpt"),
                    "prompt_view": item.get("prompt_view"),
                }
                for item in proactive_calls
            ],
            "her_words": her_words,
            "timing_choice": timing,
            "asked": asked,
            "minted_private_impression": minted,
            "extra_ticks": extra_ticks,
            "redaction": proof,
            "usage": usage,
            "visible": list(session.delivery.sent),
        }
    finally:
        await session.close()


async def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reuse-farm",
        action="store_true",
        help="Reuse output/impression-initiative/farm.sqlite instead of farming again",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    patch_notes = apply_private_impression_proactive_runtime_patch()
    farm_clone = OUTPUT / "farm.sqlite"
    previous = None
    if args.reuse_farm and farm_clone.exists():
        previous = json.loads((OUTPUT / "probe.json").read_text(encoding="utf-8"))
        farm = previous.get("farm") or {}
        farm_started_seq = previous.get("farm_started_seq") or drive.current_seq(farm_clone)
        _LOG.info("reusing farm clone %s", farm_clone)
    else:
        source = drive.PRODUCTION_DB
        drive.clone_ledger(source, farm_clone)
        farm_started_seq = drive.current_seq(farm_clone)
        farm = await farm_impression(farm_clone)
        if not farm.get("accepted"):
            report = {
                "status": "farm_did_not_accept",
                "patch_notes": patch_notes,
                "farm": farm,
            }
            (OUTPUT / "probe.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            _LOG.error("farm did not accept a private impression")
            return 1
    trials: list[dict[str, Any]] = []
    for index in range(1, TRIAL_COUNT + 1):
        _LOG.info("trial %s starting", index)
        trials.append(await run_trial(farm_clone, OUTPUT, index))
    usage_all = farm.get("usage") or []
    for trial in trials:
        usage_all.extend(trial.get("usage") or [])
    cost = sum(float(item.get("cost_cny") or 0) for item in usage_all)
    minted_n = sum(1 for item in trials if item.get("minted_private_impression"))
    asked_n = sum(1 for item in trials if item.get("asked"))
    authorized_n = sum(1 for item in trials if item.get("authorized"))
    report = {
        "status": "ok",
        "patch_notes": patch_notes,
        "farm_started_seq": farm_started_seq,
        "farm": {
            "accepted": farm.get("accepted"),
            "inbound_messages": 0,
            "before": farm.get("before"),
            "after": farm.get("after"),
            "drains": farm.get("drains"),
            "usage": farm.get("usage"),
        },
        "trials": trials,
        "success": {
            "n": TRIAL_COUNT,
            "minted": minted_n,
            "asked": asked_n,
            "authorized_proactive_message": authorized_n,
            "mint_rate": minted_n / TRIAL_COUNT,
            "ask_rate": asked_n / TRIAL_COUNT,
        },
        "cost_cny": round(cost, 6),
        "purpose_counts": {},
    }
    purposes: dict[str, int] = {}
    for item in usage_all:
        purpose = str(item.get("purpose") or "")
        purposes[purpose] = purposes.get(purpose, 0) + 1
    report["purpose_counts"] = purposes
    (OUTPUT / "probe.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    _LOG.info(
        "done minted=%s/%s asked=%s/%s authorized=%s cost=¥%s",
        minted_n,
        TRIAL_COUNT,
        asked_n,
        TRIAL_COUNT,
        authorized_n,
        report["cost_cny"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
