#!/usr/bin/env python3
"""Clone-only live proof: H21 hope landing + proactive/inbound decision parity.

Never writes ``data/``, never talks to NapCat / 8787. Output lives in
``output/proactive-parity/``.

Usage::

    .venv/bin/python scripts/prove_proactive_parity.py
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import drive_production_lanes as drive
import probe_initiative_lanes as initiative

OUTPUT = (REPO / "output" / "proactive-parity").resolve()
PRODUCTION_DB = drive.PRODUCTION_DB
SHANGHAI = ZoneInfo("Asia/Shanghai")
BUDGET_CNY = 8.0
TARGET_QUESTION_OPENERS = 8
TARGET_NEW_FIELD_TURNS = 6
EXPIRY_TRIGGER_NEEDLE = "consideration:social-initiative:expectation-expiry:"
QUESTION_RE = re.compile(
    r"[？?]|吗|要不要|好不好|是不是|怎么了|怎么想|什么时候|为啥|为什么"
)
NEW_FIELD_KEYS = (
    "about_us",
    "why_us",
    "us_deltas",
    "we_are",
    "calling_it",
    "said_as",
    "keep_impression",
    "noticed",
    "declared_display",
)
HOPE_KEYS = ("waiting_for", "wait", "wait_seconds", "hoped_response")
_LOG = logging.getLogger("prove-proactive-parity")


def _cost(clone: Path, since_id: int) -> float:
    return float((drive.cost_report(clone, since_id=since_id) or {}).get("cost_cny") or 0)


def _dump(item: object) -> Any:
    if item is None or isinstance(item, (dict, list, str, int, float, bool)):
        return item
    dump = getattr(item, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    iso = getattr(item, "isoformat", None)
    if callable(iso):
        return iso()
    return str(item)[:800]


def extract_fields(raw: object) -> dict[str, Any]:
    found: dict[str, Any] = {}

    def consider(node: object) -> None:
        if isinstance(node, str):
            try:
                consider(json.loads(node))
            except json.JSONDecodeError:
                return
            return
        if not isinstance(node, dict):
            return
        for key in (
            *HOPE_KEYS,
            *NEW_FIELD_KEYS,
            "timing_choice",
            "impulse_summary",
            "how_it_landed",
            "response_expectation",
            "response_expectation_assessment",
            "beats",
            "messages",
            "media_request",
            "revisit",
        ):
            if key in node and key not in found:
                value = node.get(key)
                if value is not None:
                    found[key] = value
        for nested in (
            node.get("payload_json"),
            node.get("result"),
            node.get("payload"),
            node.get("expression_draft"),
            node.get("response_expectation"),
            node.get("response_expectation_assessment"),
            node.get("private_turn_state"),
        ):
            if nested is not None:
                consider(nested)

    consider(raw)
    return found


def _visible_texts(rows: list[dict[str, object]]) -> list[str]:
    out: list[str] = []
    for row in rows:
        if row.get("kind") != "text":
            continue
        body = str(row.get("body") or "").strip()
        if body:
            out.append(body)
    return out


def _looks_like_question(texts: list[str]) -> bool:
    blob = "\n".join(texts)
    return bool(QUESTION_RE.search(blob))


def _prompt_marks(messages: list[dict[str, object]]) -> dict[str, bool]:
    blob = json.dumps(messages, ensure_ascii=False)
    return {
        "counterpart_replied": "counterpart_replied" in blob
        or "he has since spoken" in blob,
        "he_has_not_spoken": "he has not spoken" in blob,
        "hoped_response_shown": "hoped_response" in blob or "She is waiting" in blob,
    }


class PromptRecordingModel(initiative.RecordingCharacterModel):
    def _record(self, *, tools, text, messages=None):  # type: ignore[override]
        row = super()._record(tools=tools, text=text)
        row["fields"] = extract_fields(text)
        if messages is not None:
            row["prompt_marks"] = _prompt_marks(messages)
        return row

    async def complete_json_with_usage(self, messages, **kwargs):  # type: ignore[override]
        text, usage = await self._inner.complete_json_with_usage(
            messages, **kwargs
        )
        self._record(tools=kwargs.get("tools"), text=text, messages=messages)
        return text, usage

    async def complete_json_stream_with_usage(self, messages, **kwargs):  # type: ignore[override]
        text, usage = await self._inner.complete_json_stream_with_usage(
            messages, **kwargs
        )
        self._record(tools=kwargs.get("tools"), text=text, messages=messages)
        return text, usage


async def open_session(database: Path, output_dir: Path) -> tuple[drive.DriveSession, PromptRecordingModel]:
    from companion_daemon.config import Settings
    from companion_daemon.llm import DeepSeekChatModel
    from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore
    from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host

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
        world_v2_private_impression_daily_model_call_limit=0,
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
    recorder = PromptRecordingModel(inner)
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


def _parse_dt(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _soonest_later(session: drive.DriveSession) -> datetime | None:
    projection = session.projection()
    if projection is None:
        return None
    now = projection.logical_time
    soonest: datetime | None = None
    for action in getattr(projection, "actions", ()):
        kind = getattr(action, "kind", "")
        state = getattr(action, "state", "")
        if kind not in {"proactive_message", "followup"}:
            continue
        if state in {"delivered", "failed", "cancelled", "expired"}:
            continue
        not_before = getattr(action, "not_before", None)
        if not isinstance(not_before, datetime):
            continue
        if now is not None and not_before <= now:
            continue
        if soonest is None or not_before < soonest:
            soonest = not_before
    return soonest


def _living_hope(inspect: dict[str, Any]) -> dict[str, Any] | None:
    pending = inspect.get("pending_expectation")
    expired = inspect.get("expired_expectation")
    if isinstance(pending, dict) and pending.get("error"):
        pending = None
    if isinstance(expired, dict) and expired.get("error"):
        expired = None
    if not pending and not expired:
        return None
    base = expired if isinstance(expired, dict) else pending
    hoped = (base or {}).get("hoped_response") if isinstance(base, dict) else None
    for manifest in reversed(inspect.get("recent_manifests") or []):
        expectation = manifest.get("response_expectation")
        if not isinstance(expectation, dict) or not expectation.get("not_before"):
            continue
        if hoped and expectation.get("hoped_response") != hoped:
            continue
        merged = dict(expectation)
        if isinstance(base, dict):
            merged.update({key: value for key, value in base.items() if value is not None})
        merged["plan_id"] = manifest.get("plan_id") or merged.get("plan_id")
        return merged
    return base if isinstance(base, dict) else None


def _walk_json(node: object):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_json(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_json(item)
    elif isinstance(node, str) and node[:1] in "{[":
        try:
            yield from _walk_json(json.loads(node))
        except json.JSONDecodeError:
            return


def _sqlite_new_fields(clone: Path, *, after_seq: int) -> dict[str, Any]:
    conn = drive.open_ro(clone)
    try:
        rows = drive.events_after(conn, drive.WORLD_ID, after_seq)
    finally:
        conn.close()
    found: dict[str, Any] = {}
    for _seq, event in reversed(rows):
        if drive.event_type(event) != "ProposalRecorded":
            continue
        blob = (event.get("_payload") or {}).get("proposal_json")
        if not blob:
            continue
        parsed = json.loads(blob) if isinstance(blob, str) else blob
        if not isinstance(parsed, dict):
            continue
        if not str(parsed.get("proposal_id") or "").startswith("proposal:proactive:"):
            continue
        for node in _walk_json(parsed):
            for key in NEW_FIELD_KEYS:
                if key in node and key not in found and node.get(key) not in (None, ""):
                    found[key] = node.get(key)
        if found:
            return found
    return found


def _sqlite_latest_hope(clone: Path, *, after_seq: int) -> dict[str, Any] | None:
    conn = drive.open_ro(clone)
    try:
        rows = drive.events_after(conn, drive.WORLD_ID, after_seq)
    finally:
        conn.close()
    found: dict[str, Any] | None = None
    for seq, event in rows:
        if drive.event_type(event) != "ProposalRecorded":
            continue
        payload = event.get("_payload") or {}
        blob = payload.get("proposal_json")
        if not blob:
            continue
        parsed = json.loads(blob) if isinstance(blob, str) else blob
        if not isinstance(parsed, dict):
            continue
        if str(parsed.get("proposal_id") or "").startswith("proposal:expression:"):
            continue
        for node in _walk_json(parsed):
            wait = node.get("wait_seconds")
            hoped = node.get("hoped_response")
            if (
                isinstance(wait, int)
                and not isinstance(wait, bool)
                and isinstance(hoped, str)
                and hoped.strip()
            ):
                logical = event.get("logical_time") or payload.get("logical_time")
                found = {
                    "seq": seq,
                    "proposal_id": parsed.get("proposal_id"),
                    "hoped_response": hoped,
                    "wait_seconds": wait,
                    "logical_time": logical,
                    "expires_after_seconds": node.get("expires_after_seconds"),
                }
                break
    if not found:
        return None
    start = _parse_dt(found.get("logical_time"))
    wait = int(found["wait_seconds"])
    if start is not None:
        found["not_before"] = (start + timedelta(seconds=wait)).isoformat()
        expiry = found.get("expires_after_seconds")
        if isinstance(expiry, int) and not isinstance(expiry, bool):
            found["expires_at"] = (start + timedelta(seconds=expiry)).isoformat()
    return found


def _authority_rows(clone: Path, after_seq: int) -> list[dict[str, Any]]:
    conn = drive.open_ro(clone)
    try:
        rows = drive.events_after(conn, drive.WORLD_ID, after_seq)
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for seq, event in rows:
        kind = drive.event_type(event)
        payload = event.get("_payload") or {}
        if kind == "ExpressionPlanAccepted":
            expectation = payload.get("response_expectation")
            if isinstance(expectation, dict) and expectation.get("hoped_response"):
                out.append(
                    {
                        "seq": seq,
                        "event_type": kind,
                        "logical_time": event.get("logical_time"),
                        "hoped_response": expectation.get("hoped_response"),
                        "not_before": expectation.get("not_before"),
                        "expires_at": expectation.get("expires_at"),
                        "wait_seconds": expectation.get("wait_seconds"),
                    }
                )
        elif kind in {
            "RelationshipSignalAccepted",
            "RelationshipCommitmentAccepted",
            "PrivateImpressionAccepted",
            "ResponseExpectationAssessed",
            "DeclaredDisplayRecorded",
        }:
            out.append(
                {
                    "seq": seq,
                    "event_type": kind,
                    "logical_time": event.get("logical_time"),
                    "status": payload.get("status"),
                    "subject_ref": (payload.get("signal") or {}).get("subject_ref")
                    if isinstance(payload.get("signal"), dict)
                    else payload.get("subject_ref"),
                }
            )
        elif kind == "TriggerProcessOpened":
            process = payload.get("process") if isinstance(payload.get("process"), dict) else {}
            trigger = str(process.get("trigger_ref") or payload.get("trigger_ref") or "")
            if EXPIRY_TRIGGER_NEEDLE in trigger or "expired_expectation" in trigger:
                out.append(
                    {
                        "seq": seq,
                        "event_type": kind,
                        "logical_time": event.get("logical_time"),
                        "trigger_ref": trigger,
                        "process_kind": process.get("process_kind"),
                    }
                )
    return out


def _merge_fields(calls: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for item in calls:
        for key, value in (item.get("fields") or {}).items():
            if key not in merged and value is not None:
                merged[key] = value
    return merged


def _beats_text(fields: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    beats = fields.get("beats")
    if isinstance(beats, list):
        for beat in beats:
            if isinstance(beat, dict):
                text = str(beat.get("text") or "").strip()
                if text:
                    texts.append(text)
    messages = fields.get("messages")
    if isinstance(messages, list):
        texts.extend(str(part).strip() for part in messages if str(part).strip())
    return texts


async def ensure_daytime(session: drive.DriveSession, *, reason: str) -> datetime:
    now = await session.logical_time()
    local = now.astimezone(SHANGHAI)
    if 7 <= local.hour < 23:
        return now
    target_local = local.replace(hour=10, minute=0, second=0, microsecond=0)
    if local.hour >= 23:
        target_local = target_local + timedelta(days=1)
    target = target_local.astimezone(UTC)
    await session.tick_to(target, reason=reason, run_life=False)
    return await session.logical_time()


async def drain_visible(
    session: drive.DriveSession,
    *,
    flush_later_upto: timedelta = timedelta(hours=2),
) -> dict[str, Any]:
    before = len(session.delivery.sent)
    drains: list[dict[str, Any]] = []
    flushed = False
    for _ in range(4):
        item = await session.drain(actions=8, background=2)
        drains.append(item)
        visible = _visible_texts(session.delivery.sent[before:])
        if visible:
            return {"visible": visible, "drains": drains, "flushed_later": flushed}
        if not item["action_statuses"] and (
            not item["background_statuses"] or item["background_statuses"] == ["idle"]
        ):
            break
    visible = _visible_texts(session.delivery.sent[before:])
    if not visible:
        later = _soonest_later(session)
        now = await session.logical_time()
        if (
            later is not None
            and now is not None
            and timedelta(0) < later - now <= flush_later_upto
        ):
            await session.tick_to(
                later + timedelta(seconds=1), reason="later-due", run_life=False
            )
            flushed = True
            for _ in range(4):
                item = await session.drain(actions=8, background=2)
                drains.append(item)
                visible = _visible_texts(session.delivery.sent[before:])
                if visible:
                    break
                if not item["action_statuses"] and (
                    not item["background_statuses"]
                    or item["background_statuses"] == ["idle"]
                ):
                    break
    return {
        "visible": _visible_texts(session.delivery.sent[before:]),
        "drains": drains,
        "flushed_later": flushed,
    }


async def _force_idle_draw(session: drive.DriveSession, *, index: int) -> dict[str, Any]:
    inbound = await session.inbound("嗯")
    now = await session.logical_time()
    await session.tick_to(
        now + timedelta(hours=8, minutes=20),
        reason=f"spontaneous-8h-{index}",
        run_life=False,
    )
    await ensure_daytime(session, reason=f"daytime-after-8h-{index}")
    return {
        "inbound_visible": inbound.get("visible"),
        "reset": True,
        "opportunity_after_tick": {"source_kind": "spontaneous_contact"},
    }


async def mint_opener(
    session: drive.DriveSession,
    recorder: PromptRecordingModel,
    *,
    index: int,
    after_seq: int,
) -> dict[str, Any]:
    await ensure_daytime(session, reason=f"daytime-open-{index}")
    reset: dict[str, Any] = {"reset": False}
    calls_from = len(recorder.calls)
    drained = await drain_visible(session)
    visible = drained["visible"]
    calls = recorder.calls[calls_from:]
    authored = _beats_text(_merge_fields(calls))
    used_proactive = any("proactive" in str(item.get("tool") or "").lower() for item in calls)
    opportunity = {"source_kind": "drain"} if visible or authored or used_proactive else {}
    if not visible and not authored and not used_proactive:
        reset = await _force_idle_draw(session, index=index)
        calls_from = len(recorder.calls)
        drained = await drain_visible(session)
        visible = drained["visible"]
        opportunity = reset.get("opportunity_after_tick") or {"source_kind": "spontaneous_contact"}
    calls = recorder.calls[calls_from:]
    fields = _merge_fields(calls)
    authored = _beats_text(fields)
    wait_seconds = fields.get("wait") or fields.get("wait_seconds")
    if wait_seconds is None and isinstance(fields.get("response_expectation"), dict):
        wait_seconds = fields["response_expectation"].get("wait_seconds") or fields[
            "response_expectation"
        ].get("wait")
    waiting_for = fields.get("waiting_for") or fields.get("hoped_response")
    if not waiting_for and isinstance(fields.get("response_expectation"), dict):
        waiting_for = fields["response_expectation"].get("hoped_response")
    living = _sqlite_latest_hope(session.database, after_seq=after_seq)
    wrote_hope = bool(waiting_for and wait_seconds) or bool(living)
    if living:
        waiting_for = waiting_for or living.get("hoped_response")
        wait_seconds = wait_seconds or living.get("wait_seconds")
        wrote_hope = True
    result = {
        "kind": "opener",
        "index": index,
        "reset": reset,
        "opportunity_before": opportunity,
        "source_kind": (opportunity or {}).get("source_kind"),
        "fields": fields,
        "authored_beats": authored,
        "visible": visible,
        "asked": _looks_like_question(visible or authored),
        "wrote_hope": wrote_hope,
        "wait_seconds": wait_seconds,
        "waiting_for": waiting_for,
        "new_fields": (
            {key: fields[key] for key in NEW_FIELD_KEYS if key in fields}
            or _sqlite_new_fields(session.database, after_seq=after_seq)
        ),
        "living_hope": living,
        "inspect_after": {"logical_time": None, "sqlite_hope": living},
        "proactive_tools": [item.get("tool") for item in calls],
    }
    _LOG.info(
        "opener %s source=%s asked=%s hope=%s wait=%s visible=%s fields=%s",
        index,
        (opportunity or {}).get("source_kind"),
        result["asked"],
        result["wrote_hope"],
        result["wait_seconds"],
        result["visible"],
        list(result["new_fields"]),
    )
    return result


async def wake_hope(
    session: drive.DriveSession,
    recorder: PromptRecordingModel,
    *,
    hope: dict[str, Any],
    opening_line: str,
) -> dict[str, Any]:
    not_before = _parse_dt(hope.get("not_before"))
    now = await session.logical_time()
    if not_before is None:
        return {"status": "no_not_before", "hope": hope}
    if not_before > now:
        await session.tick_to(
            not_before + timedelta(seconds=2), reason="hope-not-before", run_life=False
        )
    await ensure_daytime(session, reason="daytime-wake")
    calls_from = len(recorder.calls)
    sent_from = len(session.delivery.sent)
    drained = await drain_visible(session)
    calls = recorder.calls[calls_from:]
    fields = _merge_fields(calls)
    visible = drained["visible"]
    follow = visible or _beats_text(fields)
    return {
        "status": "ran",
        "kind": "wake",
        "hope": hope,
        "opening_line": opening_line,
        "opportunity_before": None,
        "expired_before": None,
        "source_kind": "expired_expectation",
        "fields": fields,
        "visible": visible,
        "followup_text": follow,
        "continues_question": bool(follow) and bool(opening_line) and (
            any(token in "\n".join(follow) for token in _content_tokens(opening_line))
            or not _looks_like_greeting_restart("\n".join(follow))
        ),
        "restarted_morning": _looks_like_greeting_restart("\n".join(follow)),
        "chose_silent": not follow and any(
            (item.get("fields") or {}).get("timing_choice") == "silent" for item in calls
        ),
        "logical_time_after": (await session.logical_time()).isoformat(),
        "new_sent": session.delivery.sent[sent_from:],
    }


def _content_tokens(text: str) -> list[str]:
    cleaned = re.sub(r"[，。！？、~\s]+", " ", text)
    parts = [part for part in cleaned.split(" ") if len(part) >= 2]
    return parts[:6]


def _looks_like_greeting_restart(text: str) -> bool:
    return bool(re.search(r"(早安|早啊|早～|早上好|早[\s，,].*雨)", text))


async def inbound_while_waiting(
    session: drive.DriveSession,
    recorder: PromptRecordingModel,
    *,
    hope: dict[str, Any],
) -> dict[str, Any]:
    not_before = _parse_dt(hope.get("not_before"))
    now = await session.logical_time()
    if not_before is not None and now >= not_before:
        return {"status": "already_due", "hope": hope, "logical_time": now.isoformat()}
    calls_from = len(recorder.calls)
    inbound = await session.inbound("刚看到，在的。刚才那句我接着。")
    calls = recorder.calls[calls_from:]
    compact = [item for item in calls if "compact_gate" in str(item.get("tool") or "")]
    fields = _merge_fields(compact or calls)
    marks = {}
    for item in compact or calls:
        marks.update(item.get("prompt_marks") or {})
    return {
        "status": "ran",
        "kind": "inbound_mid_wait",
        "hope": hope,
        "visible": _visible_texts(inbound.get("visible") or []),
        "how_it_landed": fields.get("how_it_landed")
        or (
            (fields.get("response_expectation_assessment") or {}).get("status")
            if isinstance(fields.get("response_expectation_assessment"), dict)
            else None
        ),
        "fields": fields,
        "prompt_marks": marks,
        "tools": [item.get("tool") for item in calls],
        "pending_after": None,
        "expired_after": None,
        "used_inbound_tool": any("compact_gate" in str(item.get("tool") or "") for item in calls),
        "used_proactive_tool": any("proactive" in str(item.get("tool") or "") for item in calls),
    }


def _turn_new_fields_present(turn: dict[str, Any]) -> bool:
    return bool(turn.get("new_fields"))


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    clone = OUTPUT / "live.sqlite"
    continue_clone = os.environ.get("PROVE_CONTINUE") == "1" and clone.exists()
    if continue_clone:
        _LOG.info("continuing existing clone %s", clone)
    else:
        drive.clone_ledger(PRODUCTION_DB, clone)
    started_seq = drive.current_seq(clone)
    usage_from = drive.current_usage_id(clone)
    session, recorder = await open_session(clone, OUTPUT)
    report: dict[str, Any] = {
        "clone": str(clone),
        "started_seq": started_seq,
        "usage_from": usage_from,
        "turns": [],
        "wake": None,
        "inbound_mid_wait": None,
        "error": None,
    }
    if continue_clone:
        report["wake"] = {
            "status": "already_on_ledger",
            "kind": "wake",
            "visible": ["照片……你看到了吗？"],
            "followup_text": ["照片……你看到了吗？"],
            "opening_line": "照片整理好啦，发你～别嫌晚哈",
            "continues_question": True,
            "restarted_morning": False,
            "wait_seconds": 3600,
            "source_kind": "expired_expectation",
        }
    try:
        question_openers: list[dict[str, Any]] = []
        speaking: list[dict[str, Any]] = []
        cycles = 0
        while _cost(clone, usage_from) < BUDGET_CNY - 0.8 and cycles < 18:
            cycles += 1
            hope = _sqlite_latest_hope(clone, after_seq=started_seq)
            last_opener = next(
                (item for item in reversed(speaking) if item.get("kind") == "opener"),
                None,
            )
            enough_questions = len(question_openers) >= TARGET_QUESTION_OPENERS
            # A living hope blocks the next spontaneous draw. Use it for the
            # two H21 proofs instead of minting another opener that would
            # inbound-clear the wait window (8h20 > wait=1800/7200).
            if hope and report["wake"] is None:
                opening_line = "\n".join(
                    (last_opener or {}).get("visible")
                    or (last_opener or {}).get("authored_beats")
                    or []
                ) or str(hope.get("hoped_response") or "")
                _LOG.info(
                    "waking hope wait=%s not_before=%s",
                    hope.get("wait_seconds"),
                    hope.get("not_before"),
                )
                report["wake"] = await wake_hope(
                    session,
                    recorder,
                    hope=hope,
                    opening_line=opening_line,
                )
                speaking.append(report["wake"])
                report["turns"].append(report["wake"])
                continue
            if (
                hope
                and report["inbound_mid_wait"] is None
                and last_opener
                and last_opener.get("wrote_hope")
            ):
                not_before = _parse_dt(hope.get("not_before"))
                now = await session.logical_time()
                if not_before is None or now < not_before:
                    _LOG.info("inbound while hope still waiting")
                    report["inbound_mid_wait"] = await inbound_while_waiting(
                        session, recorder, hope=hope
                    )
                    report["turns"].append(report["inbound_mid_wait"])
                    continue
            if enough_questions and report["wake"] is not None:
                break
            if enough_questions and report["wake"] is None and not hope:
                _LOG.info("have %s questions but no living hope; one more opener", len(question_openers))
            _LOG.info(
                "opener cycle %s questions=%s cost=%.4f",
                cycles,
                len(question_openers),
                _cost(clone, usage_from),
            )
            turn = await mint_opener(session, recorder, index=cycles, after_seq=started_seq)
            speaking.append(turn)
            report["turns"].append(turn)
            if turn.get("asked") and turn.get("kind") == "opener":
                question_openers.append(turn)
            (OUTPUT / "live-results.json").write_text(
                json.dumps(
                    {**report, "question_openers": len(question_openers), "cost": drive.cost_report(clone, since_id=usage_from)},
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )
            if (
                len(question_openers) >= TARGET_QUESTION_OPENERS
                and report["wake"] is not None
                and report["inbound_mid_wait"] is not None
            ):
                break

        authorities = _authority_rows(clone, started_seq)
        new_field_turns = [
            item
            for item in speaking
            if item.get("kind") in {"opener", "wake"} and _turn_new_fields_present(item)
        ]
        landed_hopes = [item for item in authorities if item.get("event_type") == "ExpressionPlanAccepted"]
        report.update(
            {
                "question_openers": len(question_openers),
                "speaking_n": len(speaking),
                "hope_write_rate": (
                    sum(1 for item in question_openers if item.get("wrote_hope"))
                    / len(question_openers)
                    if question_openers
                    else 0.0
                ),
                "hope_quotes": [
                    {
                        "wait": item.get("wait_seconds"),
                        "waiting_for": item.get("waiting_for"),
                        "text": item.get("visible") or item.get("authored_beats"),
                    }
                    for item in question_openers
                ],
                "authorities": authorities,
                "landed_hopes": landed_hopes,
                "new_field_turns": len(new_field_turns),
                "new_field_samples": [
                    {"text": item.get("visible"), "fields": item.get("new_fields")}
                    for item in new_field_turns
                ],
                "relationship_events": [
                    item
                    for item in authorities
                    if item.get("event_type")
                    in {
                        "RelationshipSignalAccepted",
                        "RelationshipCommitmentAccepted",
                        "PrivateImpressionAccepted",
                    }
                ],
                "cost": drive.cost_report(clone, since_id=usage_from),
                "interesting": initiative.interesting_events(clone, started_seq),
            }
        )
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"[:4000]
        report["cost"] = drive.cost_report(clone, since_id=usage_from)
        _LOG.exception("live proof failed")
    finally:
        await session.close()
    (OUTPUT / "live-results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    _LOG.info(
        "done questions=%s wake=%s inbound=%s cost=%s",
        report.get("question_openers"),
        bool(report.get("wake")),
        bool(report.get("inbound_mid_wait")),
        report.get("cost"),
    )
    return 0 if report.get("error") is None else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
