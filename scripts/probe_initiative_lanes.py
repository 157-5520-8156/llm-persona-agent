#!/usr/bin/env python3
"""Clone-only probe of initiative lanes. Never writes data/, never talks to 8787.

Reuses helpers from scripts/drive_production_lanes.py. Records raw model JSON
so we can see waiting_for / come_back even when the host parse wall drops the
proactive draft.

Usage::

    .venv/bin/python scripts/probe_initiative_lanes.py --phase write
    .venv/bin/python scripts/probe_initiative_lanes.py --phase wake
    .venv/bin/python scripts/probe_initiative_lanes.py --phase rest
    .venv/bin/python scripts/probe_initiative_lanes.py --phase life
    .venv/bin/python scripts/probe_initiative_lanes.py --phase all
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
import importlib.util
import json
import logging
from pathlib import Path
import sqlite3
import sys
from typing import Any, Callable, Mapping

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

OUTPUT = (REPO / "output" / "initiative").resolve()
WORLD_ID = "world:companion-v2:qq-c2c:geoff"
ACTOR = "agent:companion"
_LOG = logging.getLogger("probe_initiative")

_DRIVE_PATH = REPO / "scripts" / "drive_production_lanes.py"
_spec = importlib.util.spec_from_file_location("drive_production_lanes", _DRIVE_PATH)
drive = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["drive_production_lanes"] = drive
_spec.loader.exec_module(drive)


WRITE_SCENARIOS = (
    (
        "question_bait",
        "你猜我今天碰到谁了",
        "她刚被丢了一个明显要她追问的钩子",
    ),
    (
        "wait_for_me",
        "等我一下，我去倒杯水，马上告诉你是谁",
        "他说等我一下然后没把话说完",
    ),
    (
        "unfinished_topic",
        "对了上次书店那件事我才说到一半，你还记得吗",
        "话题说到一半，她可能想听下文",
    ),
    (
        "asks_her_to_hold",
        "你先别急着回下一句，我三十秒后把那人的名字发你",
        "他明确说马上回，制造短等待",
    ),
    (
        "she_should_ask",
        "我这边先问你一句：你现在方便说话吗，回我一下",
        "他点名要她回话，她回完可能还想等下文",
    ),
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


def extract_slim_fields(raw: object) -> dict[str, Any]:
    """Pull waiting_for / come_back / later / messages out of a model blob."""

    found: dict[str, Any] = {}

    def consider(node: object) -> None:
        if not isinstance(node, dict):
            return
        for key in (
            "waiting_for",
            "wait",
            "come_back",
            "come_back_in",
            "later",
            "photo",
            "messages",
            "hoped_response",
            "timing_choice",
            "impulse_summary",
            "media_request",
        ):
            if key in node and key not in found:
                found[key] = node.get(key)
        payload_json = node.get("payload_json")
        if isinstance(payload_json, str):
            inner = _load_json(payload_json)
            if inner is not None:
                consider(inner)
        result = node.get("result")
        if isinstance(result, dict):
            consider(result)
        payload = node.get("payload")
        if isinstance(payload, dict):
            consider(payload)
        expression = node.get("expression_draft")
        if isinstance(expression, dict):
            consider(expression)
            expectation = expression.get("response_expectation")
            if isinstance(expectation, dict):
                consider(expectation)
            leftover = expression.get("revisit")
            if isinstance(leftover, dict):
                consider(leftover)

    if isinstance(raw, str):
        parsed = _load_json(raw)
        if parsed is not None:
            consider(parsed)
    else:
        consider(raw)
    return found


class RecordingCharacterModel:
    """Wrap a chat model and keep every JSON completion for later inspection."""

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

    def _record(self, *, tools: list[dict[str, object]] | None, text: str) -> dict[str, Any]:
        tool = self._tool_name(tools)
        fields = extract_slim_fields(text)
        row = {
            "tool": tool,
            "chars": len(text or ""),
            "fields": fields,
            "excerpt": (text or "")[:800],
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
        self._record(tools=tools, text=text)
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
        self._record(tools=tools, text=text)
        return text, usage


def _ledger(session: drive.DriveSession):
    platform = getattr(session.host, "_host", None)
    application = getattr(platform, "_application", None)
    return getattr(application, "_ledger", None)


def dump_opportunity(opportunity) -> dict[str, Any] | None:
    if opportunity is None:
        return None
    return {
        "source_kind": opportunity.source_kind,
        "source_id": opportunity.source_id,
        "source_event_ref": opportunity.source_event_ref,
        "consideration_id": opportunity.consideration_id,
        "scheduled_for": opportunity.scheduled_for.isoformat()
        if opportunity.scheduled_for
        else None,
        "cadence_reason_codes": list(opportunity.cadence_reason_codes),
        "stimulus_event_refs": list(opportunity.stimulus_event_refs),
    }


async def inspect_lanes(session: drive.DriveSession) -> dict[str, Any]:
    from companion_daemon.world_v2.response_expectation_view import (
        expired_unanswered_expectation,
        pending_response_expectation,
    )
    from companion_daemon.world_v2.revisit_intention_view import due_unfinished_revisit
    from companion_daemon.world_v2.silence_appraisal_trigger import silence_appraisal_opportunity
    from companion_daemon.world_v2.social_initiative import (
        SocialInitiativeCompiler,
        SocialInitiativePolicy,
    )

    ledger = _ledger(session)
    projection = ledger.project()
    compiler = SocialInitiativeCompiler(
        ledger=ledger, actor_ref=ACTOR, policy=SocialInitiativePolicy()
    )
    opportunity = await compiler.next_opportunity(projection)
    pending = None
    expired = None
    leftover = None
    silence = None
    try:
        pending = pending_response_expectation(projection)
    except Exception as exc:
        pending = {"error": str(exc)[:200]}
    try:
        expired = expired_unanswered_expectation(projection)
    except Exception as exc:
        expired = {"error": str(exc)[:200]}
    try:
        leftover = due_unfinished_revisit(projection)
    except Exception as exc:
        leftover = {"error": str(exc)[:200]}
    try:
        silence = silence_appraisal_opportunity(projection, idle_seconds_threshold=3_600)
    except Exception as exc:
        silence = {"error": str(exc)[:200]}

    manifests = []
    for item in getattr(projection, "expression_plan_manifests", ())[-6:]:
        expectation = getattr(item, "response_expectation", None)
        revisit = getattr(item, "revisit", None)
        manifests.append(
            {
                "plan_id": getattr(item, "plan_id", None),
                "media_request": getattr(item, "media_request", None),
                "response_expectation": None
                if expectation is None
                else {
                    "hoped_response": getattr(expectation, "hoped_response", None),
                    "not_before": getattr(expectation, "not_before", None),
                    "expires_at": getattr(expectation, "expires_at", None),
                    "wait_seconds": getattr(expectation, "wait_seconds", None),
                },
                "revisit": None
                if revisit is None
                else {
                    "thought": getattr(revisit, "thought", None),
                    "not_before": getattr(revisit, "not_before", None),
                    "expires_at": getattr(revisit, "expires_at", None),
                },
            }
        )
    processes = []
    for item in getattr(projection, "trigger_processes", ())[-12:]:
        processes.append(
            {
                "process_kind": item.process_kind,
                "state": item.state,
                "trigger_ref": item.trigger_ref,
                "source_evidence_ref": item.source_evidence_ref,
                "runtime_outcome_ref": item.runtime_outcome_ref,
            }
        )
    return {
        "logical_time": projection.logical_time.isoformat()
        if projection.logical_time
        else None,
        "world_revision": projection.world_revision,
        "last_message_revision": (
            projection.message_observations[-1].world_revision
            if projection.message_observations
            else None
        ),
        "opportunity": dump_opportunity(opportunity),
        "pending_expectation": None
        if pending in (None,) or isinstance(pending, dict)
        else {
            "hoped_response": pending.hoped_response,
            "declared_seconds_ago": pending.declared_seconds_ago,
        }
        if pending is not None
        else None,
        "pending_expectation_error": pending if isinstance(pending, dict) else None,
        "expired_expectation": None
        if expired in (None,) or isinstance(expired, dict)
        else {
            "plan_id": expired.plan_id,
            "hoped_response": expired.hoped_response,
            "not_before": expired.not_before.isoformat(),
            "expires_at": expired.expires_at.isoformat(),
        }
        if expired is not None
        else None,
        "expired_expectation_error": expired if isinstance(expired, dict) else None,
        "due_revisit": None
        if leftover in (None,) or isinstance(leftover, dict)
        else {
            "plan_id": leftover.plan_id,
            "thought": leftover.thought,
            "not_before": leftover.not_before.isoformat(),
            "expires_at": leftover.expires_at.isoformat(),
        }
        if leftover is not None
        else None,
        "due_revisit_error": leftover if isinstance(leftover, dict) else None,
        "silence_opportunity": None
        if silence in (None,) or isinstance(silence, dict)
        else {
            "trigger_id": silence.trigger_id,
            "idle_seconds": silence.idle_seconds,
            "source_evidence_ref": silence.source_evidence_ref,
        }
        if silence is not None
        else None,
        "silence_error": silence if isinstance(silence, dict) else None,
        "recent_manifests": manifests,
        "recent_processes": processes,
    }


def interesting_events(database: Path, after_seq: int) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    try:
        rows = drive.events_after(conn, WORLD_ID, after_seq)
    finally:
        conn.close()
    keep = {
        "TriggerProcessOpened",
        "TriggerProcessCompleted",
        "ModelResultRecorded",
        "ProposalRecorded",
        "ExpressionPlanAccepted",
        "ActionAuthorized",
        "ActionDelivered",
        "AppraisalAccepted",
        "ResponseExpectationAssessed",
        "ActivityStarted",
        "ActivityCompleted",
        "WorldOccurrenceSettled",
        "WorldOccurrenceActivated",
        "AffectEpisodeOpened",
        "RandomDrawRecorded",
        "TechnicalFailureRecorded",
        "CharacterInteriorTechnicalFailureRecorded",
        "PhotoCandidateOpened",
        "MediaOpportunityAuthorized",
    }
    out: list[dict[str, Any]] = []
    for seq, event in rows:
        kind = drive.event_type(event)
        if kind not in keep:
            continue
        payload = event.get("_payload") or {}
        process = payload.get("process") if isinstance(payload.get("process"), dict) else {}
        action = payload.get("action") if isinstance(payload.get("action"), dict) else {}
        expectation = payload.get("response_expectation")
        leftover = payload.get("revisit")
        out.append(
            {
                "seq": seq,
                "event_type": kind,
                "logical_time": event.get("logical_time"),
                "process_kind": process.get("process_kind") or payload.get("process_kind"),
                "trigger_ref": process.get("trigger_ref") or payload.get("trigger_ref"),
                "runtime_outcome_ref": process.get("runtime_outcome_ref")
                or payload.get("runtime_outcome_ref"),
                "action_kind": action.get("kind"),
                "catalog_version": payload.get("catalog_version"),
                "failure_code": payload.get("failure_code") or payload.get("reason_code"),
                "has_response_expectation": bool(expectation),
                "has_revisit": bool(leftover),
                "hoped_response": (
                    expectation.get("hoped_response")
                    if isinstance(expectation, dict)
                    else None
                ),
                "revisit_thought": leftover.get("thought")
                if isinstance(leftover, dict)
                else None,
            }
        )
    return out


def layer_report(
    *,
    minted: bool,
    consideration_opened: bool,
    model_called: bool,
    drafted: bool,
    delivered: bool,
    note: str,
) -> dict[str, Any]:
    if delivered:
        layer = "delivered"
    elif drafted:
        layer = "drafted"
    elif model_called:
        layer = "model_called"
    elif consideration_opened:
        layer = "consideration_opened"
    elif minted:
        layer = "occasion_minted"
    else:
        layer = "not_triggered"
    return {
        "layer": layer,
        "occasion_minted": minted,
        "consideration_opened": consideration_opened,
        "model_called": model_called,
        "drafted": drafted,
        "delivered": delivered,
        "note": note,
    }


async def open_recorded_session(
    *,
    database: Path,
    output_dir: Path,
    inbound_payload: dict[str, object] | None = None,
    enable_media: bool = False,
) -> tuple[drive.DriveSession, RecordingCharacterModel]:
    from companion_daemon.config import Settings
    from companion_daemon.llm import DeepSeekChatModel
    from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore
    from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host, qq_c2c_world_id

    if drive._is_production_path(database):
        raise SystemExit(f"refusing to open production ledger for write: {database}")
    sidecar = output_dir / f"{database.stem}.sidecars"
    sidecar.mkdir(parents=True, exist_ok=True)
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
    wrapped: object = inner
    if inbound_payload is not None:
        wrapped = drive.OverlayCharacterModel(inner, inbound_payload)
    recorder = RecordingCharacterModel(wrapped)
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
    await session.logical_time()
    return session, recorder


def _calls_for(recorder: RecordingCharacterModel, fragment: str) -> list[dict[str, Any]]:
    return [item for item in recorder.calls if fragment in item.get("tool", "")]


async def phase_write(*, source: Path, output_dir: Path) -> dict[str, Any]:
    clone = output_dir / "write.sqlite"
    drive.clone_ledger(source, clone)
    started_seq = drive.current_seq(clone)
    usage_from = drive.current_usage_id(clone)
    session, recorder = await open_recorded_session(
        database=clone, output_dir=output_dir, inbound_payload=None
    )
    turns: list[dict[str, Any]] = []
    try:
        for name, text, why in WRITE_SCENARIOS:
            before_calls = len(recorder.calls)
            inbound = await session.inbound(text)
            new_calls = recorder.calls[before_calls:]
            compact = [item for item in new_calls if "compact_gate" in item.get("tool", "")]
            fields = compact[-1]["fields"] if compact else extract_slim_fields(
                new_calls[-1]["excerpt"] if new_calls else ""
            )
            inspect = await inspect_lanes(session)
            turns.append(
                {
                    "scenario": name,
                    "why": why,
                    "user": text,
                    "inbound_status": inbound.get("status"),
                    "visible": inbound.get("visible"),
                    "her_messages": fields.get("messages"),
                    "waiting_for": fields.get("waiting_for"),
                    "wait": fields.get("wait"),
                    "come_back": fields.get("come_back"),
                    "come_back_in": fields.get("come_back_in"),
                    "later": fields.get("later"),
                    "photo": fields.get("photo"),
                    "compact_calls": compact,
                    "all_new_tools": [item.get("tool") for item in new_calls],
                    "manifests": inspect.get("recent_manifests"),
                    "pending_expectation": inspect.get("pending_expectation"),
                    "due_revisit": inspect.get("due_revisit"),
                }
            )
        wrote_wait = sum(1 for item in turns if item.get("waiting_for"))
        wrote_revisit = sum(1 for item in turns if item.get("come_back"))
        return {
            "status": "ran",
            "clone": str(clone),
            "turns": turns,
            "waiting_for_rate": f"{wrote_wait}/{len(turns)}",
            "come_back_rate": f"{wrote_revisit}/{len(turns)}",
            "events": interesting_events(clone, started_seq),
            "cost": drive.cost_report(clone, since_id=usage_from),
            "model_calls_recorded": len(recorder.calls),
        }
    except Exception as exc:
        return {
            "status": "error",
            "clone": str(clone),
            "error": f"{type(exc).__name__}: {exc}"[:2000],
            "turns": turns,
            "cost": drive.cost_report(clone, since_id=usage_from),
        }
    finally:
        await session.close()


async def phase_wake(*, source: Path, output_dir: Path, kind: str) -> dict[str, Any]:
    """Overlay-declare waiting_for or come_back, then tick past the wait."""

    if kind == "wait":
        clone = output_dir / "wake-wait.sqlite"
        slim = {
            "messages": ["那件事你回头说完，我在听"],
            "meaning_of_this": "他答应回头把那句话说完",
            "my_state": "我说完就在等他开口",
            "wants": "想听他把那件事说完",
            "photo": False,
            "waiting_for": "他把那件事说完",
            "wait": 45,
        }
        tick_after = timedelta(seconds=50)
        reason = "wake-wait-50s"
    else:
        clone = output_dir / "wake-revisit.sqlite"
        slim = {
            "messages": ["先这样，我等会儿还想把书店的事说完"],
            "meaning_of_this": "书店那件事没说完",
            "my_state": "这件事我自己还搁着",
            "wants": "过一会儿自己再想起来找他",
            "photo": False,
            "come_back": "还想把那家书店的事说完",
            "come_back_in": 90,
        }
        tick_after = timedelta(seconds=100)
        reason = "wake-revisit-100s"
    drive.clone_ledger(source, clone)
    started_seq = drive.current_seq(clone)
    usage_from = drive.current_usage_id(clone)
    payload = drive._authored_inbound_payload(slim)
    session, recorder = await open_recorded_session(
        database=clone, output_dir=output_dir, inbound_payload=payload
    )
    try:
        inbound = await session.inbound(str(slim["messages"][0]))
        after_inbound = await inspect_lanes(session)
        now = await session.logical_time()
        tick = await session.tick_to(now + tick_after, reason=reason, run_life=False)
        before_due = await inspect_lanes(session)
        drains = await session.drain_loop(rounds=10, background=16)
        after_drain = await inspect_lanes(session)
        events = interesting_events(clone, started_seq)
        opened = [
            item
            for item in events
            if item["event_type"] == "TriggerProcessOpened"
            and item.get("process_kind") == "proactive_action_deliberation"
        ]
        proactive_calls = _calls_for(recorder, "proactive")
        authorized = [
            item
            for item in events
            if item["event_type"] == "ActionAuthorized" and item.get("action_kind") == "proactive_message"
        ]
        drafted = bool(proactive_calls) and any(
            (item.get("fields") or {}).get("timing_choice") in {"now", "later", "silent"}
            or (item.get("fields") or {}).get("impulse_summary")
            or (item.get("fields") or {}).get("messages")
            for item in proactive_calls
        )
        minted = False
        if kind == "wait":
            minted = before_due.get("expired_expectation") is not None or (
                (before_due.get("opportunity") or {}).get("source_kind") == "expired_expectation"
            )
        else:
            minted = before_due.get("due_revisit") is not None or (
                (before_due.get("opportunity") or {}).get("source_kind") == "revisit_intention"
            )
        note = "overlay declared the field; host should wake after wait"
        if not minted:
            note = "declared field did not compile onto a due opportunity"
        elif opened and not drafted:
            note = "consideration opened and model likely hit the proactive parse wall"
        return {
            "status": "ran",
            "clone": str(clone),
            "kind": kind,
            "inbound": inbound,
            "after_inbound": after_inbound,
            "tick": tick,
            "before_drain": before_due,
            "after_drain": after_drain,
            "drains_tail": drains[-4:],
            "opened_proactive": opened,
            "proactive_calls": proactive_calls,
            "authorized_proactive": authorized,
            "visible": session.delivery.sent,
            "events": events,
            "cost": drive.cost_report(clone, since_id=usage_from),
            "layers": layer_report(
                minted=minted,
                consideration_opened=bool(opened),
                model_called=bool(proactive_calls) or any(
                    item.get("event_type") == "ModelResultRecorded" for item in events
                ),
                drafted=drafted,
                delivered=bool(authorized) or any(
                    item.get("kind") == "text" for item in session.delivery.sent
                ),
                note=note,
            ),
        }
    except Exception as exc:
        return {
            "status": "error",
            "clone": str(clone),
            "kind": kind,
            "error": f"{type(exc).__name__}: {exc}"[:2000],
            "cost": drive.cost_report(clone, since_id=usage_from),
        }
    finally:
        await session.close()


async def phase_silence(*, source: Path, output_dir: Path) -> dict[str, Any]:
    clone = output_dir / "silence.sqlite"
    drive.clone_ledger(source, clone)
    started_seq = drive.current_seq(clone)
    usage_from = drive.current_usage_id(clone)
    slim = {
        "messages": ["那我先忙去了"],
        "meaning_of_this": "他如果还不回",
        "my_state": "说完这一句",
        "wants": "先把这句说完",
        "photo": False,
    }
    session, recorder = await open_recorded_session(
        database=clone,
        output_dir=output_dir,
        inbound_payload=drive._authored_inbound_payload(slim),
    )
    try:
        await session.drain_loop(rounds=8, background=12)
        inbound = await session.inbound("那我先忙去了")
        now = await session.logical_time()
        tick = await session.tick_to(now + timedelta(seconds=3_610), reason="silence-1h", run_life=False)
        before = await inspect_lanes(session)
        drains = await session.drain_loop(rounds=12, background=16)
        after = await inspect_lanes(session)
        events = interesting_events(clone, started_seq)
        opened = [
            item
            for item in events
            if item["event_type"] == "TriggerProcessOpened"
            and item.get("process_kind") == "silence_appraisal"
        ]
        stimulus_calls = [
            item
            for item in recorder.calls
            if "world_stimulus" in item.get("tool", "") or "stimulus" in item.get("tool", "")
        ]
        minted = before.get("silence_opportunity") is not None
        return {
            "status": "ran",
            "clone": str(clone),
            "inbound": inbound,
            "tick": tick,
            "before_drain": before,
            "after_drain": after,
            "drains_tail": drains[-4:],
            "opened_silence": opened,
            "stimulus_calls": stimulus_calls,
            "events": events,
            "cost": drive.cost_report(clone, since_id=usage_from),
            "layers": layer_report(
                minted=minted,
                consideration_opened=bool(opened),
                model_called=bool(stimulus_calls),
                drafted=any((item.get("fields") or {}) for item in stimulus_calls),
                delivered=False,
                note=(
                    "silence_appraisal is an inner-feeling lane (world_stimulus), "
                    "not a contact send. Contact would only hitch later."
                ),
            ),
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


async def phase_spontaneous(*, source: Path, output_dir: Path) -> dict[str, Any]:
    clone = output_dir / "spontaneous.sqlite"
    drive.clone_ledger(source, clone)
    started_seq = drive.current_seq(clone)
    usage_from = drive.current_usage_id(clone)
    slim = {
        "messages": ["嗯"],
        "meaning_of_this": "他刚回了一下",
        "my_state": "就这一句",
        "wants": "先应一声",
        "photo": False,
    }
    session, recorder = await open_recorded_session(
        database=clone,
        output_dir=output_dir,
        inbound_payload=drive._authored_inbound_payload(slim),
    )
    try:
        await session.drain_loop(rounds=8, background=12)
        inbound = await session.inbound("嗯")
        now = await session.logical_time()
        # Stranger band 6–8h; land at 7h. Idle floor is 30min, expiry 12h.
        tick = await session.tick_to(now + timedelta(hours=7), reason="spontaneous-7h", run_life=False)
        before = await inspect_lanes(session)
        drains = await session.drain_loop(rounds=12, background=16)
        after = await inspect_lanes(session)
        events = interesting_events(clone, started_seq)
        opened = [
            item
            for item in events
            if item["event_type"] == "TriggerProcessOpened"
            and item.get("process_kind") == "proactive_action_deliberation"
        ]
        draws = [
            item
            for item in events
            if item["event_type"] == "RandomDrawRecorded"
            and str(item.get("catalog_version") or "").startswith("social-initiative")
        ]
        proactive_calls = _calls_for(recorder, "proactive")
        opp = before.get("opportunity") or {}
        minted = opp.get("source_kind") in {"spontaneous_contact", "ambient_presence"}
        authorized = [
            item
            for item in events
            if item["event_type"] == "ActionAuthorized" and item.get("action_kind") == "proactive_message"
        ]
        drafted = bool(proactive_calls) and any(
            (item.get("fields") or {}).get("timing_choice")
            or (item.get("fields") or {}).get("impulse_summary")
            for item in proactive_calls
        )
        silent = any(
            item.get("runtime_outcome_ref") == "proactive:silent"
            for item in events
            if item["event_type"] == "TriggerProcessCompleted"
        )
        return {
            "status": "ran",
            "clone": str(clone),
            "inbound": inbound,
            "tick": tick,
            "before_drain": before,
            "after_drain": after,
            "opened_proactive": opened,
            "draws": draws,
            "proactive_calls": proactive_calls,
            "authorized_proactive": authorized,
            "silent_completed": silent,
            "visible": session.delivery.sent,
            "events": events,
            "cost": drive.cost_report(clone, since_id=usage_from),
            "layers": layer_report(
                minted=minted,
                consideration_opened=bool(opened),
                model_called=bool(proactive_calls),
                drafted=drafted,
                delivered=bool(authorized),
                note="quiet_gap / spontaneous_contact after 7h stranger band",
            ),
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


async def phase_life(*, source: Path, output_dir: Path) -> dict[str, Any]:
    """Inbound, jump into the book-market window so a life event can land, then 7h idle."""

    clone = output_dir / "life-hitch.sqlite"
    drive.clone_ledger(source, clone)
    started_seq = drive.current_seq(clone)
    usage_from = drive.current_usage_id(clone)
    slim = {
        "messages": ["我先去忙一会儿"],
        "meaning_of_this": "他去忙了",
        "my_state": "先应一声",
        "wants": "先这样",
        "photo": False,
    }
    session, recorder = await open_recorded_session(
        database=clone,
        output_dir=output_dir,
        inbound_payload=drive._authored_inbound_payload(slim),
        enable_media=False,
    )
    try:
        await session.drain_loop(rounds=8, background=12)
        inbound_at = drive.BOOK_MARKET_DRIVE_AT - timedelta(minutes=10)
        inbound = await session.inbound("我先去忙一会儿", observed_at=inbound_at)
        life_tick = await session.tick_to(
            drive.BOOK_MARKET_DRIVE_AT, reason="life-hitch-window", run_life=True
        )
        await session.drain_loop(rounds=10, background=16)
        if any(
            item["event_type"] == "ActivityStarted"
            for item in interesting_events(clone, started_seq)
        ):
            await session.tick_to(
                drive.BOOK_MARKET_DRIVE_AT + timedelta(seconds=120),
                reason="life-hitch-complete",
                run_life=True,
            )
            await session.drain_loop(rounds=8, background=12)
        after_life = await inspect_lanes(session)
        consider_at = inbound_at + timedelta(hours=7)
        now = await session.logical_time()
        if consider_at > now:
            await session.tick_to(consider_at, reason="life-hitch-7h", run_life=False)
        before = await inspect_lanes(session)
        drains = await session.drain_loop(rounds=12, background=16)
        after = await inspect_lanes(session)
        events = interesting_events(clone, started_seq)
        life_events = [
            item
            for item in events
            if item["event_type"]
            in {
                "ActivityStarted",
                "ActivityCompleted",
                "WorldOccurrenceSettled",
                "WorldOccurrenceActivated",
            }
        ]
        opened = [
            item
            for item in events
            if item["event_type"] == "TriggerProcessOpened"
            and item.get("process_kind") == "proactive_action_deliberation"
        ]
        opp = before.get("opportunity") or after.get("opportunity") or {}
        hitch = "stimulus:situation_change" in (opp.get("cadence_reason_codes") or [])
        minted = bool(opp.get("source_kind")) or hitch
        proactive_calls = _calls_for(recorder, "proactive")
        return {
            "status": "ran",
            "clone": str(clone),
            "inbound": inbound,
            "life_tick": life_tick,
            "after_life": after_life,
            "before_consider": before,
            "after_consider": after,
            "life_events": life_events,
            "opened_proactive": opened,
            "proactive_calls": proactive_calls,
            "hitch_reason_present": hitch,
            "stimulus_event_refs": opp.get("stimulus_event_refs"),
            "drains_tail": drains[-4:],
            "events": events,
            "cost": drive.cost_report(clone, since_id=usage_from),
            "layers": layer_report(
                minted=minted,
                consideration_opened=bool(opened),
                model_called=bool(proactive_calls),
                drafted=bool(proactive_calls),
                delivered=False,
                note=(
                    "situation_change is hitch-only; a life event after the last "
                    "user message should appear as stimulus_event_refs on the 7h consider"
                    if life_events
                    else "no ActivityStarted/WorldOccurrenceSettled on this clone"
                ),
            ),
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


def summarize(results: Mapping[str, Any]) -> dict[str, Any]:
    costs = []
    for value in results.values():
        if isinstance(value, dict) and isinstance(value.get("cost"), dict):
            costs.append(float(value["cost"].get("cost_cny") or 0))
    write = results.get("write") if isinstance(results.get("write"), dict) else {}
    return {
        "waiting_for_rate": write.get("waiting_for_rate"),
        "come_back_rate": write.get("come_back_rate"),
        "layers": {
            key: (value.get("layers") if isinstance(value, dict) else None)
            for key, value in results.items()
        },
        "total_cost_cny": round(sum(costs), 4),
    }


async def async_main(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    source = args.source.resolve()
    drive.assert_clone_is_safe(source=source, target=output_dir / "safety-check.sqlite")
    phases = args.phase
    if "all" in phases:
        phases = ["write", "wake", "silence", "spontaneous", "life"]
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "source": str(source),
        "output_dir": str(output_dir),
        "phases": phases,
        "results": {},
    }
    if "write" in phases:
        _LOG.info("phase write: will she declare waiting_for / come_back")
        report["results"]["write"] = await phase_write(source=source, output_dir=output_dir)
        (output_dir / "REPORT.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
    if "wake" in phases:
        _LOG.info("phase wake: overlay wait then tick 50s")
        report["results"]["wake_wait"] = await phase_wake(
            source=source, output_dir=output_dir, kind="wait"
        )
        _LOG.info("phase wake: overlay come_back then tick 100s")
        report["results"]["wake_revisit"] = await phase_wake(
            source=source, output_dir=output_dir, kind="revisit"
        )
        (output_dir / "REPORT.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
    if "silence" in phases:
        _LOG.info("phase silence")
        report["results"]["silence"] = await phase_silence(source=source, output_dir=output_dir)
        (output_dir / "REPORT.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
    if "spontaneous" in phases:
        _LOG.info("phase spontaneous 7h")
        report["results"]["spontaneous"] = await phase_spontaneous(
            source=source, output_dir=output_dir
        )
        (output_dir / "REPORT.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
    if "life" in phases:
        _LOG.info("phase life hitch")
        report["results"]["life"] = await phase_life(source=source, output_dir=output_dir)
        (output_dir / "REPORT.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["summary"] = summarize(report["results"])
    (output_dir / "REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=drive.PRODUCTION_DB)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument(
        "--phase",
        action="append",
        choices=["write", "wake", "silence", "spontaneous", "life", "all"],
        default=[],
    )
    args = parser.parse_args()
    if not args.phase:
        args.phase = ["all"]
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    report = asyncio.run(async_main(args))
    print(
        json.dumps(
            {
                "phases": report.get("phases"),
                "summary": report.get("summary"),
                "finished_at": report.get("finished_at"),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    print(f"full report: {args.output_dir.resolve() / 'REPORT.json'}")


if __name__ == "__main__":
    main()
