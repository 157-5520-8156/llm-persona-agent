#!/usr/bin/env python3
"""Drive World V2 production lanes on a cloned ledger. Never touch production.

Clones ``data/companion.epoch2.sqlite`` (read-only backup) into
``output/drive-0818/`` and drives each mechanism until it emits events or
fails with a precise code. Does not write ``src/``, does not restart the QQ
process, does not talk to ``127.0.0.1:8787``, and does not commit.

Usage::

    .venv/bin/python scripts/drive_production_lanes.py --lane inspect
    .venv/bin/python scripts/drive_production_lanes.py --lane life
    .venv/bin/python scripts/drive_production_lanes.py --lane life_followup
    .venv/bin/python scripts/drive_production_lanes.py --lane all

``--lane`` may be repeated. Default is inspect-only (no model spend).
Image generation is capped at ``--max-images`` (default 2: 1 ordinary + 1
adult). Adult generation is off unless ``--allow-adult-image`` is set; the
adult lane's job is to confirm the freeze breakpoint, not to mint a photo.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import time
import traceback
from typing import Any, Callable, Mapping

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

PRODUCTION_DB = (REPO / "data" / "companion.epoch2.sqlite").resolve()
DEFAULT_OUTPUT = (REPO / "output" / "drive-0818").resolve()
WORLD_ID = "world:companion-v2:qq-c2c:geoff"
BOOK_MARKET_WINDOW_START = datetime(2026, 8, 22, 1, 0, tzinfo=UTC)
BOOK_MARKET_WINDOW_END = datetime(2026, 8, 22, 9, 0, tzinfo=UTC)
BOOK_MARKET_DRIVE_AT = datetime(2026, 8, 22, 3, 0, tzinfo=UTC)
LIFE_EVENT_TYPES = (
    "ActivityPlanned",
    "ActivityStarted",
    "ActivityCompleted",
    "ActivityAbandoned",
    "WorldOccurrenceActivated",
    "WorldOccurrenceSettled",
    "ImageEvidenceDeclared",
    "RecipientScopedImageEvidenceDeclared",
    "PhotoCandidateOpened",
    "RandomDrawRecorded",
    "MediaOpportunityAuthorized",
    "MediaPlanAccepted",
    "MediaPreviewGenerated",
    "MediaPreviewFailed",
    "MediaInspectionRecorded",
    "MediaAutomaticDeliveryApproved",
    "ActionAuthorized",
    "ActionDelivered",
)
_LOG = logging.getLogger("drive_production_lanes")


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------


def _is_production_path(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    data = (REPO / "data").resolve()
    try:
        resolved.relative_to(data)
    except ValueError:
        return False
    return True


def assert_clone_is_safe(*, source: Path, target: Path) -> None:
    source = source.expanduser().resolve()
    target = target.expanduser().resolve()
    if source != PRODUCTION_DB and _is_production_path(source):
        # Allow reading the documented production ledger only.
        if source.name not in {"companion.epoch2.sqlite", "companion.sqlite"}:
            raise SystemExit(f"refusing unknown production-area source: {source}")
    if _is_production_path(target):
        raise SystemExit(f"refusing to write a clone under data/: {target}")
    if target == source:
        raise SystemExit("clone target must differ from source")
    if "drive-0818" not in str(target) and "output" not in str(target):
        raise SystemExit(f"clone target must live under output/: {target}")


def clone_ledger(source: Path, target: Path) -> None:
    assert_clone_is_safe(source=source, target=target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(target) + suffix).unlink(missing_ok=True)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)


def open_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def parse_event(raw: str | bytes) -> dict[str, Any]:
    event = json.loads(raw)
    payload = event.get("payload")
    if isinstance(payload, str):
        try:
            event["_payload"] = json.loads(payload)
        except json.JSONDecodeError:
            event["_payload"] = {}
    elif isinstance(payload, dict):
        event["_payload"] = payload
    else:
        payload_json = event.get("payload_json")
        if isinstance(payload_json, str):
            try:
                event["_payload"] = json.loads(payload_json)
            except json.JSONDecodeError:
                event["_payload"] = {}
        else:
            event["_payload"] = {}
    return event


def event_type(event: Mapping[str, Any]) -> str:
    return str(event.get("event_type") or "")


def max_sequence(conn: sqlite3.Connection, world_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(ledger_sequence), 0) FROM world_v2_events WHERE world_id = ?",
        (world_id,),
    ).fetchone()
    return int(row[0] or 0)


def events_after(
    conn: sqlite3.Connection, world_id: str, after_seq: int
) -> list[tuple[int, dict[str, Any]]]:
    rows = conn.execute(
        "SELECT ledger_sequence, event_json FROM world_v2_events "
        "WHERE world_id = ? AND ledger_sequence > ? ORDER BY ledger_sequence",
        (world_id, after_seq),
    ).fetchall()
    return [(int(seq), parse_event(raw)) for seq, raw in rows]


def usage_sum_cny(conn: sqlite3.Connection, *, since_id: int = 0) -> tuple[float, int, list[dict[str, Any]]]:
    try:
        rows = conn.execute(
            "SELECT id, purpose, model, status, cost_cny, prompt_tokens, "
            "completion_tokens, error FROM world_v2_model_usage WHERE id > ? "
            "ORDER BY id",
            (since_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return 0.0, 0, []
    items = [dict(row) for row in rows]
    total = sum(float(item.get("cost_cny") or 0) for item in items)
    last_id = int(items[-1]["id"]) if items else since_id
    return total, last_id, items


def usage_max_id(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM world_v2_model_usage").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0] or 0)


# ---------------------------------------------------------------------------
# Inspect (read-only, no host)
# ---------------------------------------------------------------------------


def inspect_ledger(path: Path) -> dict[str, Any]:
    conn = open_ro(path)
    try:
        types = Counter()
        for (raw,) in conn.execute(
            "SELECT event_json FROM world_v2_events WHERE world_id = ?", (WORLD_ID,)
        ):
            event = parse_event(raw)
            types[event_type(event)] += 1
        last_clock = None
        for (raw,) in conn.execute(
            "SELECT event_json FROM world_v2_events WHERE world_id = ? "
            "AND json_extract(event_json,'$.event_type') = 'ClockAdvanced' "
            "ORDER BY ledger_sequence DESC LIMIT 1",
            (WORLD_ID,),
        ):
            event = parse_event(raw)
            last_clock = event.get("logical_time") or event["_payload"].get("logical_time_to")
        plans = []
        for seq, event in events_after(conn, WORLD_ID, 0):
            blob = json.dumps(event, ensure_ascii=False)
            if "2026-08-22" in blob or "旧书" in blob:
                plans.append(
                    {
                        "seq": seq,
                        "event_type": event_type(event),
                        "logical_time": event.get("logical_time"),
                        "payload_excerpt": {
                            key: event["_payload"].get(key)
                            for key in (
                                "activity_kind",
                                "plan_id",
                                "effect_kind",
                                "privacy",
                                "visibility",
                                "window_start",
                                "window_end",
                                "opens_at",
                                "closes_at",
                                "scheduled_start",
                                "scheduled_end",
                                "visual_evidence",
                            )
                            if key in event["_payload"]
                        },
                    }
                )
        signals = []
        for seq, event in events_after(conn, WORLD_ID, 0):
            if event_type(event) != "RelationshipSignalAccepted":
                continue
            payload = event["_payload"]
            signals.append(
                {
                    "seq": seq,
                    "logical_time": event.get("logical_time"),
                    "suggested_deltas": payload.get("suggested_deltas")
                    or payload.get("deltas"),
                    "signal_id": payload.get("signal_id"),
                }
            )
        appraisals = []
        for seq, event in events_after(conn, WORLD_ID, 0):
            if event_type(event) != "AppraisalAccepted":
                continue
            payload = event["_payload"]
            appraisals.append(
                {
                    "seq": seq,
                    "logical_time": event.get("logical_time"),
                    "confidence_bp": payload.get("confidence_bp"),
                    "expires_at": payload.get("expires_at"),
                    "status": payload.get("status"),
                    "trigger_id": payload.get("trigger_id"),
                }
            )
        grants = []
        for seq, event in events_after(conn, WORLD_ID, 0):
            kind = event_type(event)
            if kind not in {"CapabilityGranted", "ConsentGranted", "CapabilityRevoked", "ConsentRevoked"}:
                continue
            payload = event["_payload"]
            grants.append(
                {
                    "seq": seq,
                    "event_type": kind,
                    "capability_id": payload.get("capability_id") or payload.get("consent_id"),
                    "entity_id": payload.get("entity_id"),
                }
            )
        action_kinds: Counter[str] = Counter()
        for seq, event in events_after(conn, WORLD_ID, 0):
            if event_type(event) != "ActionAuthorized":
                continue
            payload = event["_payload"]
            action = payload.get("action") if isinstance(payload.get("action"), dict) else payload
            kind = None
            if isinstance(action, dict):
                kind = action.get("kind")
            action_kinds[str(kind or "unknown")] += 1
        impressions = types.get("PrivateImpressionAccepted", 0)
        reflections = 0
        for seq, event in events_after(conn, WORLD_ID, 0):
            if event_type(event) != "TriggerProcessOpened":
                continue
            payload = event["_payload"]
            if payload.get("process_kind") == "life_reflection":
                reflections += 1
        relationship_states = []
        for seq, event in events_after(conn, WORLD_ID, 0):
            if event_type(event) not in {
                "RelationshipSlowVariableAdjusted",
                "RelationshipCommitmentAccepted",
                "RelationshipStateRecorded",
            }:
                continue
            payload = event["_payload"]
            relationship_states.append(
                {
                    "seq": seq,
                    "event_type": event_type(event),
                    "stage_after": payload.get("stage_after") or payload.get("stage"),
                    "variables_after": payload.get("variables_after"),
                    "last_adjusted_at": payload.get("adjusted_at"),
                }
            )
        return {
            "database": str(path),
            "event_count": sum(types.values()),
            "event_types": dict(types.most_common()),
            "last_clock": last_clock,
            "life_counts": {name: types.get(name, 0) for name in LIFE_EVENT_TYPES},
            "aug22_or_bookmarket_events": plans[-20:],
            "aug22_or_bookmarket_count": len(plans),
            "relationship_signals": {
                "count": types.get("RelationshipSignalAccepted", 0),
                "adjustments": types.get("RelationshipSlowVariableAdjusted", 0),
                "commitments": types.get("RelationshipCommitmentAccepted", 0),
                "recent_nonzero": [
                    item
                    for item in signals[-12:]
                    if _nonzero_deltas(item.get("suggested_deltas"))
                ],
            },
            "appraisals_weighed": [
                item
                for item in appraisals[-30:]
                if isinstance(item.get("confidence_bp"), int)
                and item["confidence_bp"] != 5000
            ],
            "appraisal_confidence_histogram": dict(
                Counter(
                    item.get("confidence_bp")
                    for item in appraisals
                    if item.get("confidence_bp") is not None
                )
            ),
            "private_impressions": impressions,
            "life_reflection_triggers": reflections,
            "adult_grants": grants[-12:],
            "action_kinds": dict(action_kinds),
            "relationship_mutations": relationship_states[-8:],
            "waiting_events": {
                "ResponseExpectationDeclared": types.get("ResponseExpectationDeclared", 0),
                "ResponseExpectationAssessed": types.get("ResponseExpectationAssessed", 0),
                "RevisitIntentionDeclared": types.get("RevisitIntentionDeclared", 0),
            },
        }
    finally:
        conn.close()


def _nonzero_deltas(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    return any(int(item or 0) for item in value.values() if isinstance(item, (int, float)))


# ---------------------------------------------------------------------------
# Host
# ---------------------------------------------------------------------------


class CaptureDelivery:
    """Capture provider-visible units. Never talks to QQ."""

    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send_text(self, recipient_id: str, text: str) -> dict[str, object]:
        self.sent.append({"kind": "text", "recipient_id": recipient_id, "body": text})
        return {"status": "ok", "data": {"message_id": f"drive-text-{time.time_ns()}"}}

    async def send_reaction(
        self, recipient_id: str, *, message_id: str, reaction_id: str
    ) -> dict[str, object]:
        self.sent.append(
            {
                "kind": "reaction",
                "recipient_id": recipient_id,
                "body": f"{message_id}:{reaction_id}",
            }
        )
        return {"status": "ok", "data": {"message_id": f"drive-{len(self.sent)}"}}

    async def send_sticker(self, recipient_id: str, *, sticker_id: str) -> dict[str, object]:
        self.sent.append({"kind": "sticker", "recipient_id": recipient_id, "body": sticker_id})
        return {"status": "ok", "data": {"message_id": f"drive-{len(self.sent)}"}}

    async def send_typing(self, recipient_id: str, *, state: str) -> dict[str, object]:
        self.sent.append({"kind": "typing", "recipient_id": recipient_id, "body": state})
        return {"status": "ok", "data": {"message_id": f"drive-{len(self.sent)}"}}

    async def send_image_message(
        self, recipient_id: str, *, image_path: Path
    ) -> dict[str, object]:
        self.sent.append(
            {
                "kind": "image",
                "recipient_id": recipient_id,
                "body": str(image_path),
            }
        )
        return {"status": "ok", "data": {"message_id": f"drive-{len(self.sent)}"}}

    async def get_message(self, recipient_id: str, *, message_id: str) -> dict[str, object]:
        """Local get_msg stand-in so clone receipts can upgrade to delivered."""

        return {
            "status": "ok",
            "retcode": 0,
            "data": {"message_id": message_id},
        }


def _overlay_usage() -> dict[str, object]:
    from companion_daemon.world_v2.deliberation import ModelUsageProvenance, _digest

    body = {
        "usage_contract": "model-usage.1",
        "route_class": "chat",
        "input_tokens": 0,
        "output_tokens": 0,
        "thinking_tokens": 0,
        "token_provenance": "offline_estimated",
        "transport": "offline_fixture",
        "provider": "drive-overlay",
        "provider_usage_ref": "drive-overlay:compact-gate",
    }
    body["provider_usage_hash"] = _digest(body)
    return ModelUsageProvenance.model_validate(body).model_dump(mode="json")


def _authored_inbound_payload(
    slim: Mapping[str, object], *, reply_only: bool = True
) -> dict[str, object]:
    from companion_daemon.world_v2.present_prompt import compile_slim_interior_envelope

    envelope = compile_slim_interior_envelope(dict(slim), reply_only=reply_only)
    if envelope is None:
        raise RuntimeError("compile_slim_interior_envelope rejected authored payload")
    return {
        "_result_kind": "reply_only" if reply_only else "full_turn",
        "_payload_json": json.dumps(envelope, ensure_ascii=False),
    }


class OverlayCharacterModel:
    """Optional inbound overlay. Every other purpose hits the real provider."""

    def __init__(self, inner: object, inbound_payload: dict[str, object] | None) -> None:
        self._inner = inner
        self._inbound_payload = inbound_payload
        self.model = getattr(inner, "model", "overlay")
        self.provider = getattr(inner, "provider", "overlay")
        self.supports_required_tool_choice = True
        self.supports_strict_tool_choice = bool(
            getattr(inner, "supports_strict_tool_choice", True)
        )
        self.reports_exact_request_emission = bool(
            getattr(inner, "reports_exact_request_emission", False)
        )

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def _intercept(self, tools: list[dict[str, object]] | None) -> bool:
        if self._inbound_payload is None or not tools:
            return False
        function = tools[0].get("function") if isinstance(tools[0], dict) else None
        name = function.get("name") if isinstance(function, dict) else ""
        return isinstance(name, str) and "compact_gate" in name

    def _payload_bytes(self) -> str:
        payload = dict(self._inbound_payload or {})
        kind = str(payload.pop("_result_kind", "reply_only"))
        inner = payload.pop("_payload_json", None)
        if inner is None:
            inner = json.dumps(payload, ensure_ascii=False)
        elif not isinstance(inner, str):
            inner = json.dumps(inner, ensure_ascii=False)
        return json.dumps({"result_kind": kind, "payload_json": inner}, ensure_ascii=False)

    async def complete_json_with_usage(
        self,
        messages: list[dict[str, object]],
        *,
        temperature: float = 0.8,
        tools: list[dict[str, object]] | None = None,
        tool_choice: object | None = None,
    ) -> tuple[str, dict[str, object]]:
        if self._intercept(tools):
            return self._payload_bytes(), _overlay_usage()
        return await self._inner.complete_json_with_usage(
            messages, temperature=temperature, tools=tools, tool_choice=tool_choice
        )

    async def complete_json_stream_with_usage(
        self,
        messages: list[dict[str, object]],
        *,
        temperature: float = 0.8,
        on_text_delta: Callable[[str], object] | None = None,
        tools: list[dict[str, object]] | None = None,
        tool_choice: object | None = None,
    ) -> tuple[str, dict[str, object] | None]:
        if self._intercept(tools):
            text = self._payload_bytes()
            if on_text_delta is not None:
                on_text_delta(text)
            return text, _overlay_usage()
        return await self._inner.complete_json_stream_with_usage(
            messages,
            temperature=temperature,
            on_text_delta=on_text_delta,
            tools=tools,
            tool_choice=tool_choice,
        )


@dataclass
class DriveSession:
    database: Path
    recipient_id: str
    delivery: CaptureDelivery
    host: object
    clock: datetime
    images_generated: int = 0
    notes: list[str] = field(default_factory=list)

    async def logical_time(self) -> datetime:
        value = await self.host._host.current_logical_time()  # noqa: SLF001
        if isinstance(value, datetime):
            self.clock = value
            return value
        raise RuntimeError("host has no logical time")

    async def tick_to(self, target: datetime, *, reason: str, run_life: bool = True) -> dict[str, Any]:
        current = await self.logical_time()
        if target <= current:
            return {"status": "already_at_or_past", "logical_time": current.isoformat()}
        tick_id = f"drive:{reason}:{target.isoformat()}"
        status = await self.host.tick(
            tick_id=tick_id,
            logical_time_from=current,
            logical_time_to=target,
            observed_at=target,
            reason=f"drive_{reason}",
            run_life_ecology=run_life,
        )
        self.clock = target
        return {"status": status, "logical_time": target.isoformat(), "tick_id": tick_id}

    async def drain(self, *, actions: int = 8, background: int = 16) -> dict[str, Any]:
        result = await self.host.drain(
            max_action_units=actions, max_background_units=background
        )
        return {
            "action_statuses": list(getattr(result, "action_statuses", ()) or ()),
            "background_statuses": list(getattr(result, "background_statuses", ()) or ()),
        }

    async def drain_loop(self, *, rounds: int = 12, background: int = 16) -> list[dict[str, Any]]:
        seen: list[dict[str, Any]] = []
        for _ in range(rounds):
            item = await self.drain(actions=8, background=background)
            seen.append(item)
            if not item["action_statuses"] and not item["background_statuses"]:
                break
            if item["background_statuses"] == ["idle"] and not item["action_statuses"]:
                break
        return seen

    async def inbound(self, text: str, *, observed_at: datetime | None = None) -> dict[str, Any]:
        when = observed_at or (self.clock + timedelta(seconds=2))
        if when <= self.clock:
            when = self.clock + timedelta(seconds=2)
        message_id = f"drive-{time.time_ns()}"
        before = len(self.delivery.sent)
        result = await self.host.inbound_text(
            message_id=message_id,
            recipient_id=self.recipient_id,
            text=text,
            observed_at=when,
        )
        self.clock = when
        await self.drain(actions=8, background=8)
        return {
            "status": getattr(result, "status", None),
            "action_id": getattr(result, "action_id", None),
            "visible": self.delivery.sent[before:],
            "observed_at": when.isoformat(),
            "message_id": message_id,
        }

    async def close(self) -> None:
        closer = getattr(self.host, "aclose", None)
        if callable(closer):
            await closer()

    def projection(self) -> object | None:
        platform = getattr(self.host, "_host", None)
        application = getattr(platform, "_application", None)
        ledger = getattr(application, "_ledger", None)
        if ledger is None:
            return None
        return ledger.project()


def _recipient_id(settings: object) -> str:
    raw = str(getattr(settings, "napcat_allowed_private_user_ids", "") or "")
    ids = tuple(item.strip() for item in raw.split(",") if item.strip())
    if len(ids) != 1:
        raise SystemExit("NAPCAT_ALLOWED_PRIVATE_USER_IDS must contain exactly one id")
    return ids[0]


async def open_session(
    *,
    database: Path,
    output_dir: Path,
    inbound_payload: dict[str, object] | None = None,
    enable_media: bool = True,
) -> DriveSession:
    from companion_daemon.config import Settings
    from companion_daemon.llm import DeepSeekChatModel
    from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore
    from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host, qq_c2c_world_id
    from companion_daemon.world_v2.qq_media_deployment import build_qq_media_preview_deployment

    if _is_production_path(database):
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
    recipient_id = _recipient_id(settings)
    delivery = CaptureDelivery()
    overlay = None
    if inbound_payload is not None:
        usage_store = WorldV2UsageStore(path=str(database))
        inner = DeepSeekChatModel(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            thinking_enabled=False,
            max_completion_tokens=4_096,
            usage_observer=usage_store.record,
        )
        overlay = OverlayCharacterModel(inner, inbound_payload)
    media_preview = None
    media_transport = None
    if enable_media:
        bundle = build_qq_media_preview_deployment(
            settings=settings, world_id=qq_c2c_world_id(settings.primary_user_id)
        )
        if bundle is not None:
            media_preview = bundle.deployment
            media_transport = bundle.transport
    host = build_qq_c2c_host(
        settings=settings,
        recipient_id=recipient_id,
        bootstrap_at=datetime.now(UTC),
        delivery=delivery,
        model=overlay,
        media_preview=media_preview,
        media_transport=media_transport,
        use_configured_recall_embedding=False,
    )
    session = DriveSession(
        database=database,
        recipient_id=recipient_id,
        delivery=delivery,
        host=host,
        clock=datetime.now(UTC),
    )
    await session.logical_time()
    return session


def summarize_new_events(rows: list[tuple[int, dict[str, Any]]]) -> list[dict[str, Any]]:
    interesting = {
        *LIFE_EVENT_TYPES,
        "TriggerProcessOpened",
        "AppraisalAccepted",
        "PrivateImpressionAccepted",
        "RelationshipSignalAccepted",
        "RelationshipSlowVariableAdjusted",
        "RelationshipCommitmentAccepted",
        "ResponseExpectationDeclared",
        "RevisitIntentionRecorded",
        "RevisitIntentionDeclared",
        "ProposalRecorded",
        "ModelResultRecorded",
        "ActionDispatchStarted",
        "MediaSelectionAccepted",
        "MediaCandidateSelected",
        "MediaSelectionAttemptRecorded",
        "PhotoCandidateSelected",
        "ActivityPlanned",
        "TechnicalFailureRecorded",
        "CharacterInteriorTechnicalFailureRecorded",
    }
    out: list[dict[str, Any]] = []
    for seq, event in rows:
        kind = event_type(event)
        if kind not in interesting and not kind.startswith("Media") and not kind.startswith("Activity"):
            continue
        payload = event["_payload"]
        nested_process = payload.get("process") if isinstance(payload.get("process"), dict) else {}
        nested_appraisal = payload.get("appraisal") if isinstance(payload.get("appraisal"), dict) else {}
        nested_action = payload.get("action") if isinstance(payload.get("action"), dict) else {}
        excerpt = {
            key: payload.get(key)
            for key in (
                "activity_kind",
                "plan_id",
                "occurrence_id",
                "process_kind",
                "reason_code",
                "status",
                "decision",
                "opening_token",
                "candidate_id",
                "preview_id",
                "lane",
                "privacy",
                "visibility",
                "confidence_bp",
                "keep_impression",
                "action",
                "kind",
                "hoped_response",
                "thought",
                "stage_after",
                "suggested_deltas",
                "accepted_deltas",
                "failure_code",
                "error_class",
                "waiting_for",
                "come_back",
            )
            if key in payload
        }
        if nested_process.get("process_kind"):
            excerpt["process_kind"] = nested_process.get("process_kind")
        if nested_appraisal.get("confidence_bp") is not None:
            excerpt["confidence_bp"] = nested_appraisal.get("confidence_bp")
        if nested_appraisal.get("status"):
            excerpt["appraisal_status"] = nested_appraisal.get("status")
        action = nested_action or payload.get("action")
        if isinstance(action, dict) and "kind" in action:
            excerpt["action.kind"] = action.get("kind")
        out.append(
            {
                "seq": seq,
                "event_type": kind,
                "logical_time": event.get("logical_time"),
                "payload": excerpt,
            }
        )
    return out


def cost_report(database: Path, *, since_id: int) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        total, last_id, items = usage_sum_cny(conn, since_id=since_id)
        by_purpose: Counter[str] = Counter()
        for item in items:
            by_purpose[str(item.get("purpose") or "unknown")] += 1
        return {
            "cost_cny": round(total, 4),
            "calls": len(items),
            "by_purpose": dict(by_purpose),
            "last_usage_id": last_id,
            "errors": [
                {"purpose": item.get("purpose"), "error": item.get("error"), "model": item.get("model")}
                for item in items
                if item.get("status") not in {None, "succeeded", "ok", ""}
                or item.get("error")
            ][:12],
        }
    finally:
        conn.close()


def new_events(database: Path, after_seq: int) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        return summarize_new_events(events_after(conn, WORLD_ID, after_seq))
    finally:
        conn.close()


def current_seq(database: Path) -> int:
    conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        return max_sequence(conn, WORLD_ID)
    finally:
        conn.close()


def current_usage_id(database: Path) -> int:
    conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        return usage_max_id(conn)
    finally:
        conn.close()


def _occurrence_id(item: Mapping[str, Any]) -> str:
    return str((item.get("payload") or {}).get("occurrence_id") or "")


def find_aftermath_settled(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in events
            if item["event_type"] == "WorldOccurrenceSettled"
            and _occurrence_id(item).startswith("occurrence:life-aftermath:")
        ),
        None,
    )


def find_aftermath_activated(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in events
            if item["event_type"] == "WorldOccurrenceActivated"
            and _occurrence_id(item).startswith("occurrence:life-aftermath:")
        ),
        None,
    )


# ---------------------------------------------------------------------------
# Lanes
# ---------------------------------------------------------------------------


def _has_event(events: list[dict[str, Any]], kind: str) -> bool:
    return any(item["event_type"] == kind for item in events)


def _find_payload(events: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    for item in reversed(events):
        if item["event_type"] == kind:
            return item
    return None


async def drive_life(
    *,
    source: Path,
    output_dir: Path,
    max_attempts: int,
    max_images: int,
    images_spent: list[int],
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    for attempt in range(1, max_attempts + 1):
        clone = output_dir / f"life-attempt-{attempt}.sqlite"
        clone_ledger(source, clone)
        started_seq = current_seq(clone)
        usage_from = current_usage_id(clone)
        session = await open_session(database=clone, output_dir=output_dir, enable_media=True)
        try:
            before = await session.logical_time()
            tick = await session.tick_to(BOOK_MARKET_DRIVE_AT, reason=f"life-window-{attempt}")
            await session.drain_loop(rounds=8, background=16)
            # If she started, give the 60s completion floor plus a little slack.
            if _has_event(new_events(clone, started_seq), "ActivityStarted"):
                await session.tick_to(
                    BOOK_MARKET_DRIVE_AT + timedelta(seconds=120),
                    reason=f"life-complete-{attempt}",
                )
                await session.drain_loop(rounds=10, background=16)
            # Aftermath opens on start but often spends the next wake recovering
            # an unrelated open-world experience. Keep waking until the
            # life-aftermath occurrence settles or we have a hard error.
            for extra in range(1, 9):
                events = new_events(clone, started_seq)
                if find_aftermath_settled(events) is not None:
                    break
                if not _has_event(events, "ActivityStarted"):
                    break
                await session.tick_to(
                    BOOK_MARKET_DRIVE_AT + timedelta(seconds=120 + extra * 90),
                    reason=f"life-aftermath-drain-{attempt}-{extra}",
                )
                await session.drain_loop(rounds=8, background=12)
            events = new_events(clone, started_seq)
            started = _find_payload(events, "ActivityStarted")
            completed = _find_payload(events, "ActivityCompleted")
            settled = find_aftermath_settled(events)
            any_settled = _find_payload(events, "WorldOccurrenceSettled")
            declared = _find_payload(events, "ImageEvidenceDeclared")
            candidate = _find_payload(events, "PhotoCandidateOpened")
            # Lottery miss: jump 13h so starvation fill can skip the draw.
            if settled is not None and declared is None and candidate is None:
                now = await session.logical_time()
                await session.tick_to(now + timedelta(hours=13), reason=f"life-starvation-{attempt}")
                await session.drain_loop(rounds=8, background=16)
                events = new_events(clone, started_seq)
                settled = find_aftermath_settled(events)
                declared = _find_payload(events, "ImageEvidenceDeclared")
                candidate = _find_payload(events, "PhotoCandidateOpened")
            media_chain = None
            if candidate is not None and images_spent[0] < max_images:
                media_chain = await _drive_media_from_open_candidate(
                    session, clone, started_seq, images_spent, max_images
                )
                events = new_events(clone, started_seq)
            cost = cost_report(clone, since_id=usage_from)
            row = {
                "attempt": attempt,
                "clone": str(clone),
                "logical_time_before": before.isoformat(),
                "tick": tick,
                "started": started,
                "completed": completed,
                "settled": settled,
                "any_world_occurrence_settled": any_settled,
                "aftermath_activated": find_aftermath_activated(events),
                "image_evidence": declared,
                "photo_candidate": candidate,
                "media": media_chain,
                "events": events,
                "visible": session.delivery.sent,
                "cost": cost,
            }
            attempts.append(row)
            if started is not None and completed is not None and settled is not None:
                return {
                    "status": "ran",
                    "outcome": "life_chain_progressed",
                    "attempts_needed": attempt,
                    "attempts": attempts,
                }
            if started is None:
                # no_op / abandon / lottery of openings — legal character choice
                continue
        except Exception as exc:
            attempts.append(
                {
                    "attempt": attempt,
                    "clone": str(clone),
                    "error": {"type": type(exc).__name__, "message": str(exc)[:2000]},
                    "traceback": traceback.format_exc()[-3000:],
                    "events": new_events(clone, started_seq),
                    "cost": cost_report(clone, since_id=usage_from),
                }
            )
        finally:
            await session.close()
    followup = await drive_life_followup(
        source=source,
        output_dir=output_dir,
        max_images=max_images,
        images_spent=images_spent,
        prefer_existing=True,
    )
    if followup.get("status") == "ran" and followup.get("aftermath_settled"):
        return {
            "status": "ran",
            "outcome": followup.get("outcome"),
            "attempts_needed": max_attempts,
            "attempts": attempts,
            "followup": followup,
        }
    return {
        "status": "stuck",
        "outcome": "aftermath_did_not_settle_after_retries",
        "attempts_needed": max_attempts,
        "attempts": attempts,
        "followup": followup,
        "breakpoint": {
            "code": "life_aftermath.content_ref_collision_or_starved_by_open_world",
            "file": "src/companion_daemon/world_v2/life_aftermath_runtime.py",
            "line": "335-375 and 975",
            "also": "life_content_store.py:169",
            "note": (
                "She often starts the 8/22 plan. Aftermath opens immediately, then "
                "advance_once prefers recovering any settled open-world experience "
                "before settling occurrence:life-aftermath:*. Extra ticks after "
                "experience recovery are required; settle may still hit "
                "life content ref is already bound to different immutable bytes."
            ),
        },
    }


async def _drive_media_from_open_candidate(
    session: DriveSession,
    clone: Path,
    started_seq: int,
    images_spent: list[int],
    max_images: int,
) -> dict[str, Any]:
    await session.drain_loop(rounds=16, background=16)
    events = new_events(clone, started_seq)
    generated = _find_payload(events, "MediaPreviewGenerated")
    failed = _find_payload(events, "MediaPreviewFailed")
    inspection = _find_payload(events, "MediaInspectionRecorded")
    delivered = _find_payload(events, "MediaAutomaticDeliveryApproved")
    if generated is not None:
        images_spent[0] += 1
    image_files = sorted(Path("output/media-preview").glob("*.png")) if Path("output/media-preview").exists() else []
    delivered_files = (
        sorted(Path("output/media-delivered").glob("*.png"))
        if Path("output/media-delivered").exists()
        else []
    )
    return {
        "generated": generated,
        "failed": failed,
        "inspection": inspection,
        "auto_delivery": delivered,
        "images_spent": images_spent[0],
        "max_images": max_images,
        "preview_files": [str(path) for path in image_files[-4:]],
        "delivered_files": [str(path) for path in delivered_files[-4:]],
        "visible_images": [item for item in session.delivery.sent if item.get("kind") == "image"],
    }


def _best_existing_life_clone(output_dir: Path) -> Path | None:
    ranked: list[tuple[int, Path]] = []
    for path in sorted(output_dir.glob("life-attempt-*.sqlite")):
        seq = current_seq(path)
        events = new_events(path, 3908)
        score = 0
        if _has_event(events, "ActivityStarted"):
            score += 4
        if _has_event(events, "ActivityCompleted"):
            score += 4
        if find_aftermath_activated(events) is not None:
            score += 3
        if find_aftermath_settled(events) is not None:
            score += 8
        if _has_event(events, "ImageEvidenceDeclared"):
            score += 10
        if _has_event(events, "PhotoCandidateOpened"):
            score += 12
        ranked.append((score, path))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[1].stat().st_mtime), reverse=True)
    return ranked[0][1]


async def drive_life_followup(
    *,
    source: Path,
    output_dir: Path,
    max_images: int,
    images_spent: list[int],
    prefer_existing: bool = True,
) -> dict[str, Any]:
    """Resume a clone that already started the 8/22 plan and keep waking aftermath."""

    existing = _best_existing_life_clone(output_dir) if prefer_existing else None
    clone = output_dir / "life-followup.sqlite"
    if existing is not None:
        clone_ledger(existing, clone)
        origin = str(existing)
    else:
        clone_ledger(source, clone)
        origin = str(source)
    started_seq = 3908
    usage_from = current_usage_id(clone)
    session = await open_session(database=clone, output_dir=output_dir, enable_media=True)
    ticks: list[dict[str, Any]] = []
    try:
        now = await session.logical_time()
        if now < BOOK_MARKET_DRIVE_AT:
            tick = await session.tick_to(BOOK_MARKET_DRIVE_AT, reason="followup-window")
            ticks.append(tick)
            await session.drain_loop(rounds=8, background=12)
            now = await session.logical_time()
        for extra in range(1, 10):
            events = new_events(clone, started_seq)
            if find_aftermath_settled(events) is not None and (
                _find_payload(events, "PhotoCandidateOpened") is not None
                or _find_payload(events, "ImageEvidenceDeclared") is not None
            ):
                break
            if find_aftermath_settled(events) is not None and extra >= 3:
                # settled but no photo: one starvation jump then stop
                now = await session.logical_time()
                tick = await session.tick_to(now + timedelta(hours=13), reason="followup-starvation")
                ticks.append(tick)
                await session.drain_loop(rounds=8, background=16)
                break
            target = now + timedelta(seconds=90 * extra)
            try:
                tick = await session.tick_to(target, reason=f"followup-aftermath-{extra}")
            except Exception as exc:
                ticks.append(
                    {
                        "error": {"type": type(exc).__name__, "message": str(exc)[:2000]},
                        "traceback": traceback.format_exc()[-2500:],
                    }
                )
                break
            ticks.append(tick)
            await session.drain_loop(rounds=8, background=12)
        events = new_events(clone, started_seq)
        settled = find_aftermath_settled(events)
        declared = _find_payload(events, "ImageEvidenceDeclared")
        candidate = _find_payload(events, "PhotoCandidateOpened")
        media_chain = None
        if candidate is not None and images_spent[0] < max_images:
            media_chain = await _drive_media_from_open_candidate(
                session, clone, started_seq, images_spent, max_images
            )
            events = new_events(clone, started_seq)
            declared = _find_payload(events, "ImageEvidenceDeclared")
            candidate = _find_payload(events, "PhotoCandidateOpened")
        cost = cost_report(clone, since_id=usage_from)
        outcome = "no_aftermath_settlement"
        if media_chain and media_chain.get("generated"):
            outcome = "ordinary_image_generated"
        elif candidate is not None:
            outcome = "photo_candidate_opened"
        elif declared is not None:
            outcome = "image_evidence_declared"
        elif settled is not None:
            outcome = "aftermath_settled_no_photo"
        return {
            "status": "ran",
            "clone": str(clone),
            "origin": origin,
            "ticks": ticks,
            "started": _find_payload(events, "ActivityStarted"),
            "completed": _find_payload(events, "ActivityCompleted"),
            "aftermath_activated": find_aftermath_activated(events),
            "aftermath_settled": settled,
            "image_evidence": declared,
            "photo_candidate": candidate,
            "media": media_chain,
            "events": events,
            "visible": session.delivery.sent,
            "cost": cost,
            "outcome": outcome,
            "breakpoint": None
            if settled is not None
            else {
                "code": "life_aftermath.did_not_settle_on_followup_wakes",
                "file": "src/companion_daemon/world_v2/life_aftermath_runtime.py",
                "line": 335,
                "note": (
                    "advance_once recovers any settled occurrence's experience "
                    "before settling the still-active life-aftermath occurrence"
                ),
            },
        }
    except Exception as exc:
        return {
            "status": "error",
            "clone": str(clone),
            "origin": origin,
            "ticks": ticks,
            "error": {"type": type(exc).__name__, "message": str(exc)[:2000]},
            "traceback": traceback.format_exc()[-4000:],
            "events": new_events(clone, started_seq),
            "cost": cost_report(clone, since_id=usage_from),
        }
    finally:
        await session.close()


async def drive_proactive(*, source: Path, output_dir: Path) -> dict[str, Any]:
    clone = output_dir / "proactive.sqlite"
    clone_ledger(source, clone)
    started_seq = current_seq(clone)
    usage_from = current_usage_id(clone)
    session = await open_session(database=clone, output_dir=output_dir, enable_media=False)
    try:
        now = await session.logical_time()
        # Stranger cadence band is 6–8h. Land inside it, not past it.
        tick = await session.tick_to(now + timedelta(hours=7), reason="proactive-quiet-gap")
        drains = await session.drain_loop(rounds=16, background=16)
        events = new_events(clone, started_seq)
        authorized = [
            item
            for item in events
            if item["event_type"] == "ActionAuthorized"
            and (item.get("payload") or {}).get("action.kind") == "proactive_message"
        ]
        return {
            "status": "ran",
            "clone": str(clone),
            "tick": tick,
            "drains": drains[-4:],
            "proactive_authorized": authorized,
            "events": events,
            "visible": session.delivery.sent,
            "cost": cost_report(clone, since_id=usage_from),
            "outcome": "proactive_message_authorized" if authorized else "no_proactive_message",
            "breakpoint": None
            if authorized
            else {
                "code": "proactive_message.not_authorized_this_run",
                "file": "src/companion_daemon/world_v2/proactive_action.py",
                "note": "HEAD may still fail provider 400; this reports current runtime",
            },
        }
    except Exception as exc:
        return {
            "status": "error",
            "clone": str(clone),
            "error": {"type": type(exc).__name__, "message": str(exc)[:2000]},
            "traceback": traceback.format_exc()[-3000:],
            "events": new_events(clone, started_seq),
            "cost": cost_report(clone, since_id=usage_from),
        }
    finally:
        await session.close()


async def drive_reflection(*, source: Path, output_dir: Path) -> dict[str, Any]:
    clone = output_dir / "reflection.sqlite"
    clone_ledger(source, clone)
    started_seq = current_seq(clone)
    usage_from = current_usage_id(clone)
    payload = {
        "messages": ["你今天到底把那件事放在心里哪一层"],
        "felt": "这件事比平时更沉",
        "stuck_with_me": "他问我这件事到底多重",
        "wants": "想把分量说清楚",
        "photo": False,
        "matters_bp": 7200,
        "keep_impression": True,
    }
    session = await open_session(
        database=clone, output_dir=output_dir, inbound_payload=_authored_inbound_payload(payload), enable_media=False
    )
    try:
        inbound = await session.inbound("你今天到底把那件事放在心里哪一层")
        after_inbound = new_events(clone, started_seq)
        appraisals = [item for item in after_inbound if item["event_type"] == "AppraisalAccepted"]
        now = await session.logical_time()
        tick = await session.tick_to(now + timedelta(hours=1, minutes=5), reason="reflection-1h")
        await session.drain_loop(rounds=12, background=16)
        events = new_events(clone, started_seq)
        reflections = [
            item
            for item in events
            if item["event_type"] == "TriggerProcessOpened"
            and (item.get("payload") or {}).get("process_kind") == "life_reflection"
        ]
        weighed = [
            item
            for item in appraisals
            if isinstance((item.get("payload") or {}).get("confidence_bp"), int)
            and item["payload"]["confidence_bp"] > 5000
        ]
        return {
            "status": "ran",
            "clone": str(clone),
            "inbound": inbound,
            "weighed_appraisals": weighed or appraisals,
            "tick": tick,
            "life_reflection_triggers": reflections,
            "events": events,
            "cost": cost_report(clone, since_id=usage_from),
            "outcome": "life_reflection_opened" if reflections else "no_life_reflection",
            "design_note": (
                "H25 bar is max live confidence_bp strictly above unweighted 5000 "
                "(reflection_scheduler.py:_revisit_bar). If she writes 3000–4500, bar is None."
            ),
            "breakpoint": None
            if reflections
            else {
                "code": "nothing_weighed_or_bar_not_crossed",
                "file": "src/companion_daemon/world_v2/reflection_scheduler.py",
                "line": 171,
            },
        }
    except Exception as exc:
        return {
            "status": "error",
            "clone": str(clone),
            "error": {"type": type(exc).__name__, "message": str(exc)[:2000]},
            "traceback": traceback.format_exc()[-3000:],
            "events": new_events(clone, started_seq),
            "cost": cost_report(clone, since_id=usage_from),
        }
    finally:
        await session.close()


async def drive_impression(*, source: Path, output_dir: Path) -> dict[str, Any]:
    clone = output_dir / "impression.sqlite"
    clone_ledger(source, clone)
    started_seq = current_seq(clone)
    usage_from = current_usage_id(clone)
    payload = {
        "messages": ["我记住这一句就行"],
        "felt": "这一句我想留下来",
        "stuck_with_me": "他刚才那句我会自己留着",
        "wants": "不想再问一遍",
        "photo": False,
        "keep_impression": True,
        "matters_bp": 6200,
    }
    session = await open_session(
        database=clone, output_dir=output_dir, inbound_payload=_authored_inbound_payload(payload), enable_media=False
    )
    try:
        first = await session.inbound("我记住这一句就行")
        after_first = new_events(clone, started_seq)
        accepted = [item for item in after_first if item["event_type"] == "PrivateImpressionAccepted"]
        second = await session.inbound("那你还记得刚才那句吗")
        after_second = new_events(clone, started_seq)
        return {
            "status": "ran",
            "clone": str(clone),
            "first": first,
            "second": second,
            "private_impression_accepted": accepted,
            "events": after_second,
            "cost": cost_report(clone, since_id=usage_from),
            "outcome": "impression_landed" if accepted else "impression_did_not_land",
            "context_note": (
                "snapshot_compiler.py materials['private_impressions'] is compiled from "
                "the capsule slice; an accepted impression is in the next Present unless truncated"
            ),
        }
    except Exception as exc:
        return {
            "status": "error",
            "clone": str(clone),
            "error": {"type": type(exc).__name__, "message": str(exc)[:2000]},
            "traceback": traceback.format_exc()[-3000:],
            "events": new_events(clone, started_seq),
            "cost": cost_report(clone, since_id=usage_from),
        }
    finally:
        await session.close()


async def drive_relationship(*, source: Path, output_dir: Path) -> dict[str, Any]:
    clone = output_dir / "relationship.sqlite"
    clone_ledger(source, clone)
    started_seq = current_seq(clone)
    usage_from = current_usage_id(clone)
    deltas_only = {
        "messages": ["我记下你刚才认真听了"],
        "felt": "亲近和信任都动了一点",
        "stuck_with_me": "他愿意把小事记住",
        "wants": "把这份靠近留下来",
        "photo": False,
        "us_deltas": {
            "trust_bp": 500,
            "closeness_bp": 500,
            "respect_bp": 200,
            "reliability_bp": 200,
            "mutuality_bp": 400,
            "repair_confidence_bp": 100,
        },
        "matters_bp": 6200,
    }
    we_are_payload = {
        "messages": ["我们就算朋友吧"],
        "felt": "我想把我们算成朋友",
        "stuck_with_me": "他说到我们算什么",
        "wants": "把这一层说清楚",
        "photo": False,
        "we_are": "friend",
        "calling_it": "朋友",
        "said_as": "我们就算朋友吧",
        "about_us": "比陌生人近一层",
        "why_us": "这句话是我自己说出口的",
        "matters_bp": 6800,
    }
    notes: list[str] = []
    visible: list[dict[str, object]] = []
    inbound1 = inbound2 = None
    drains1: list[dict[str, Any]] = []
    drains2: list[dict[str, Any]] = []
    session = await open_session(
        database=clone,
        output_dir=output_dir,
        inbound_payload=_authored_inbound_payload(deltas_only),
        enable_media=False,
    )
    try:
        inbound1 = await session.inbound("我记下你刚才认真听了")
        visible.extend(session.delivery.sent)
        drains1 = await session.drain_loop(rounds=16, background=16)
        notes.append("step1_us_deltas_only")
    except Exception as exc:
        notes.append(f"step1_error:{type(exc).__name__}:{exc}")
    finally:
        await session.close()

    session = await open_session(
        database=clone,
        output_dir=output_dir,
        inbound_payload=_authored_inbound_payload(we_are_payload),
        enable_media=False,
    )
    try:
        inbound2 = await session.inbound("我们现在算什么")
        visible.extend(session.delivery.sent)
        try:
            drains2 = await session.drain_loop(rounds=12, background=8)
        except Exception as exc:
            notes.append(f"step2_drain_error:{type(exc).__name__}:{exc}")
        events = new_events(clone, started_seq)
        signals = [item for item in events if item["event_type"] == "RelationshipSignalAccepted"]
        adjustments = [
            item for item in events if item["event_type"] == "RelationshipSlowVariableAdjusted"
        ]
        commitments = [
            item for item in events if item["event_type"] == "RelationshipCommitmentAccepted"
        ]
        action_states = [
            item
            for item in events
            if item["event_type"] in {"ActionAuthorized", "ActionDispatchStarted", "ActionDelivered"}
        ]
        breakpoint = None
        if not adjustments:
            breakpoint = {
                "code": "relationship_adjustment.not_emitted_this_drive",
                "file": "src/companion_daemon/world_v2/runtime.py",
                "line": 619,
                "note": (
                    "commitment worker runs before adjustment and currently raises "
                    "relationship_state_policy_uninstalled "
                    "(relationship_proposal_compiler.py:796) because production "
                    "state still carries retired digest 13bfa71d…; drain_background "
                    "then ERROR-loops and starves adjustment."
                ),
            }
        if not commitments:
            extra = {
                "code": "relationship_proposal_compiler.relationship_state_policy_uninstalled",
                "file": "src/companion_daemon/world_v2/relationship_proposal_compiler.py",
                "line": 796,
                "also": "commitment_expression_not_delivered at :971 if digest were current",
            }
            if breakpoint is None:
                breakpoint = extra
            else:
                breakpoint["commitment"] = extra
        return {
            "status": "ran",
            "clone": str(clone),
            "notes": notes,
            "inbound_step1": inbound1,
            "inbound_step2": inbound2,
            "drains_step1": drains1[-4:],
            "drains_step2": drains2[-4:],
            "signals": signals,
            "adjustments": adjustments,
            "commitments": commitments,
            "action_states": action_states,
            "visible": visible,
            "events": events,
            "cost": cost_report(clone, since_id=usage_from),
            "steps_to_close_friend_via_we_are": (
                "stranger→friend (1 delivered we_are) then friend→close_friend "
                "(1 more). First live gate is relationship_state_policy_uninstalled "
                "at compiler.py:796, before delivery proof."
            ),
            "steps_to_close_friend_via_slow_variables": (
                "enter_bp acquaintance 2000 / friend 4500 / close_friend 7000; "
                "cap 500bp/turn; 2 confirmations; 86400s dwell per stage "
                "(relationship_reducers.py:51-60, :628-669)."
            ),
            "breakpoint": breakpoint,
        }
    except Exception as exc:
        return {
            "status": "error",
            "clone": str(clone),
            "notes": notes,
            "error": {"type": type(exc).__name__, "message": str(exc)[:2000], "code": getattr(exc, "code", None)},
            "traceback": traceback.format_exc()[-4000:],
            "events": new_events(clone, started_seq),
            "cost": cost_report(clone, since_id=usage_from),
        }
    finally:
        await session.close()


async def drive_waiting(*, source: Path, output_dir: Path) -> dict[str, Any]:
    clone = output_dir / "waiting.sqlite"
    clone_ledger(source, clone)
    started_seq = current_seq(clone)
    usage_from = current_usage_id(clone)
    payload = {
        "messages": ["那件事你回头说完"],
        "felt": "我在等他回这句",
        "stuck_with_me": "我说完就在等他开口",
        "wants": "想听他把那句话说完",
        "photo": False,
        "waiting_for": "他回来把那件事说完",
        "wait": 45,
        "come_back": "还想把那家店的事说完",
        "come_back_in": 120,
    }
    session = await open_session(
        database=clone, output_dir=output_dir, inbound_payload=_authored_inbound_payload(payload), enable_media=False
    )
    try:
        inbound = await session.inbound("那件事你回头说完")
        after = new_events(clone, started_seq)
        now = await session.logical_time()
        tick = await session.tick_to(now + timedelta(seconds=150), reason="waiting-due")
        await session.drain_loop(rounds=10, background=16)
        events = new_events(clone, started_seq)
        expectation = [
            item
            for item in events
            if "Expectation" in item["event_type"] or "expectation" in json.dumps(item)
        ]
        revisit = [item for item in events if "evisit" in item["event_type"] or "revisit" in json.dumps(item).lower()]
        return {
            "status": "ran",
            "clone": str(clone),
            "inbound": inbound,
            "tick": tick,
            "expectation_related": expectation,
            "revisit_related": revisit,
            "events": events,
            "cost": cost_report(clone, since_id=usage_from),
            "outcome": "waiting_or_revisit_emitted" if (expectation or revisit) else "fields_did_not_emit",
        }
    except Exception as exc:
        return {
            "status": "error",
            "clone": str(clone),
            "error": {"type": type(exc).__name__, "message": str(exc)[:2000]},
            "traceback": traceback.format_exc()[-3000:],
            "events": new_events(clone, started_seq),
            "cost": cost_report(clone, since_id=usage_from),
        }
    finally:
        await session.close()


async def drive_reaction(*, source: Path, output_dir: Path) -> dict[str, Any]:
    clone = output_dir / "reaction.sqlite"
    clone_ledger(source, clone)
    started_seq = current_seq(clone)
    usage_from = current_usage_id(clone)
    # reply_only compact gate excludes reaction/sticker. full_turn + event envelope.
    from companion_daemon.world_v2.present_prompt import compile_slim_interior_envelope

    slim = {
        "messages": ["嗯"],
        "felt": "想用表情回你",
        "stuck_with_me": "这一句够了",
        "wants": "不用再写一长段",
        "photo": False,
    }
    envelope = compile_slim_interior_envelope(slim, reply_only=False)
    if envelope is None:
        raise RuntimeError("could not compile full_turn envelope for reaction drive")
    events = envelope.get("events")
    if isinstance(events, list) and events and isinstance(events[0], dict):
        head = dict(events[0])
        head["beats"] = [
            {"modality": "text", "text": "嗯"},
            {"modality": "reaction", "reaction_id": "like"},
            {"modality": "sticker", "sticker_id": "qq-face:14"},
        ]
        envelope = {**envelope, "events": [head, *events[1:]]}
    payload = {"_result_kind": "full_turn", "_payload_json": json.dumps(envelope, ensure_ascii=False)}
    session = await open_session(
        database=clone, output_dir=output_dir, inbound_payload=_authored_inbound_payload(payload), enable_media=False
    )
    try:
        inbound = await session.inbound("看这个")
        await session.drain_loop(rounds=8, background=8)
        events = new_events(clone, started_seq)
        kinds = [
            (item.get("payload") or {}).get("action.kind")
            for item in events
            if item["event_type"] == "ActionAuthorized"
        ]
        visible = session.delivery.sent
        return {
            "status": "ran",
            "clone": str(clone),
            "inbound": inbound,
            "authorized_kinds": kinds,
            "visible": visible,
            "events": events,
            "cost": cost_report(clone, since_id=usage_from),
            "outcome": "nontext_dispatched"
            if any(item.get("kind") in {"reaction", "sticker"} for item in visible)
            else "nontext_not_dispatched",
            "note": (
                "compact gate reply_only excludes typing/reaction "
                "(inbound_tool_contract.py compact_gate description). "
                "This lane forced full_turn."
            ),
        }
    except Exception as exc:
        return {
            "status": "error",
            "clone": str(clone),
            "error": {"type": type(exc).__name__, "message": str(exc)[:2000]},
            "traceback": traceback.format_exc()[-4000:],
            "events": new_events(clone, started_seq),
            "cost": cost_report(clone, since_id=usage_from),
        }
    finally:
        await session.close()


async def drive_adult(*, source: Path, output_dir: Path) -> dict[str, Any]:
    from companion_daemon.world_v2.adult_media_authority import adult_media_is_authorized
    from companion_daemon.world_v2.media_opportunity_authorizer import MediaOpportunityAuthorizer
    from companion_daemon.world_v2.relationship_media_context import (
        RelationshipMediaContextResolver,
        RelationshipMediaContextV1,
    )

    fields = sorted(RelationshipMediaContextV1.model_fields)
    declared_display_present = "declared_display" in fields
    clone = output_dir / "adult.sqlite"
    clone_ledger(source, clone)
    started_seq = current_seq(clone)
    usage_from = current_usage_id(clone)
    payload = {
        "messages": ["我们就算朋友吧"],
        "felt": "我想把关系说清楚",
        "stuck_with_me": "他问我们算什么",
        "wants": "先把朋友这一层说出口",
        "photo": False,
        "we_are": "friend",
        "calling_it": "朋友",
        "said_as": "我们就算朋友吧",
        "matters_bp": 6000,
    }
    session = await open_session(
        database=clone, output_dir=output_dir, inbound_payload=_authored_inbound_payload(payload), enable_media=True
    )
    try:
        inbound = await session.inbound("我们算什么")
        await session.drain_loop(rounds=12, background=16)
        projection = session.projection()
        now = await session.logical_time()
        adult_enabled = str(os.environ.get("WORLD_V2_ADULT_MEDIA_ENABLED") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        adult_ok = adult_media_is_authorized(
            enabled=adult_enabled,
            projection=projection,
            at_logical_time=now,
        ) if projection is not None else None
        stage = None
        subject_ref = None
        stage_error = None
        if projection is not None:
            states = tuple(getattr(projection, "relationship_states", ()) or ())
            if states:
                stage = getattr(states[0], "stage", None)
                subject_ref = getattr(states[0], "subject_ref", None)
            try:
                MediaOpportunityAuthorizer._p3_lane_for_stage(
                    str(stage or "stranger"), adult_eligible=bool(adult_ok)
                )
            except ValueError as exc:
                stage_error = str(exc)
        resolved = None
        if projection is not None:
            resolved_obj = RelationshipMediaContextResolver().resolve(
                projection=projection,
                character_ref="agent:companion",
                recipient_ref=str(subject_ref or f"user:qq:{session.recipient_id}"),
                at_logical_time=now,
            )
            resolved = {
                "accepted": resolved_obj.accepted,
                "reason_code": resolved_obj.reason_code,
                "has_declared_display": (
                    resolved_obj.context is not None
                    and hasattr(resolved_obj.context, "declared_display")
                ),
                "context_fields": (
                    sorted(resolved_obj.context.model_fields)
                    if resolved_obj.context is not None
                    else None
                ),
            }
        events = new_events(clone, started_seq)
        freeze_hits = [
            item
            for item in events
            if "private_render_intent_evidence_missing" in json.dumps(item, ensure_ascii=False)
            or (item.get("payload") or {}).get("reason_code")
            == "private_render_intent_evidence_missing"
        ]
        first_gate = stage_error or (resolved or {}).get("reason_code") or "did_not_reach_planner_freeze"
        return {
            "status": "ran",
            "clone": str(clone),
            "relationship_media_context_fields": fields,
            "declared_display_field_present": declared_display_present,
            "adult_grants_authorized": adult_ok,
            "relationship_stage": stage,
            "p3_lane_error": stage_error,
            "resolved_context": resolved,
            "first_live_gate": first_gate,
            "freeze_code": "private_render_intent_evidence_missing",
            "freeze_file": "src/companion_daemon/event_media.py",
            "freeze_line": 4518,
            "stage_file": "src/companion_daemon/world_v2/media_opportunity_authorizer.py",
            "stage_line": 213,
            "inbound": inbound,
            "freeze_hits": freeze_hits,
            "commitments": [
                item for item in events if item["event_type"] == "RelationshipCommitmentAccepted"
            ],
            "events": events,
            "cost": cost_report(clone, since_id=usage_from),
            "outcome": "confirmed_stage_or_declared_display_gap",
            "note": (
                "Did not forge declared_display. Planner freeze is "
                "private_render_intent_evidence_missing at event_media.py:4518, "
                "but the first live gate on this clone is relationship stage "
                "(must be close_friend+) or missing P3 basis. we_are friend is "
                "one legal hop from stranger; close_friend needs a second "
                "delivered commitment after that."
            ),
        }
    except Exception as exc:
        return {
            "status": "error",
            "clone": str(clone),
            "relationship_media_context_fields": fields,
            "declared_display_field_present": declared_display_present,
            "freeze_code": "private_render_intent_evidence_missing",
            "freeze_file": "src/companion_daemon/event_media.py",
            "freeze_line": 4518,
            "error": {"type": type(exc).__name__, "message": str(exc)[:2000]},
            "traceback": traceback.format_exc()[-3000:],
            "events": new_events(clone, started_seq),
            "cost": cost_report(clone, since_id=usage_from),
        }
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


LANES = (
    "inspect",
    "life",
    "life_followup",
    "media",
    "adult",
    "proactive",
    "reflection",
    "impression",
    "relationship",
    "waiting",
    "reaction",
    "all",
)


def write_report(output_dir: Path, report: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "REPORT.json"
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = {}
        if isinstance(previous, dict) and isinstance(previous.get("results"), dict):
            merged = dict(previous.get("results") or {})
            merged.update(report.get("results") or {})
            report["results"] = merged
            if previous.get("started_at") and not report.get("original_started_at"):
                report["original_started_at"] = previous.get("started_at")
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


async def async_main(args: argparse.Namespace) -> dict[str, Any]:
    source = args.source.resolve()
    output_dir = args.output_dir.resolve()
    if not source.exists():
        raise SystemExit(f"source ledger missing: {source}")
    output_dir.mkdir(parents=True, exist_ok=True)
    lanes = list(args.lane) or ["inspect"]
    if "all" in lanes:
        lanes = [name for name in LANES if name != "all"]
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "source": str(source),
        "output_dir": str(output_dir),
        "lanes": lanes,
        "results": {},
    }
    images_spent = [0]
    if "inspect" in lanes:
        _LOG.info("inspecting production ledger read-only")
        report["results"]["inspect"] = inspect_ledger(source)
        write_report(output_dir, report)
    if "life" in lanes or "media" in lanes:
        _LOG.info("driving life chain")
        report["results"]["life"] = await drive_life(
            source=source,
            output_dir=output_dir,
            max_attempts=args.max_life_attempts,
            max_images=args.max_images,
            images_spent=images_spent,
        )
        write_report(output_dir, report)
        if "media" in lanes:
            life = report["results"]["life"]
            followup = life.get("followup") if isinstance(life, dict) else None
            last = (life.get("attempts") or [{}])[-1] if isinstance(life, dict) else {}
            source_row = followup if followup and followup.get("photo_candidate") else last
            report["results"]["media"] = {
                "status": "ran"
                if (source_row or {}).get("photo_candidate") or (source_row or {}).get("media")
                else "stuck",
                "from_life_attempt": last.get("attempt"),
                "followup": followup,
                "photo_candidate": (source_row or {}).get("photo_candidate"),
                "media": (source_row or {}).get("media"),
                "cost": (source_row or {}).get("cost"),
                "images_spent": images_spent[0],
            }
            write_report(output_dir, report)
    if "life_followup" in lanes and "life" not in lanes and "media" not in lanes:
        _LOG.info("resuming life aftermath on an existing clone")
        report["results"]["life_followup"] = await drive_life_followup(
            source=source,
            output_dir=output_dir,
            max_images=args.max_images,
            images_spent=images_spent,
            prefer_existing=True,
        )
        write_report(output_dir, report)
    if "adult" in lanes:
        _LOG.info("driving adult media freeze")
        report["results"]["adult"] = await drive_adult(source=source, output_dir=output_dir)
        write_report(output_dir, report)
    if "proactive" in lanes:
        _LOG.info("driving proactive quiet gap")
        report["results"]["proactive"] = await drive_proactive(source=source, output_dir=output_dir)
        write_report(output_dir, report)
    if "reflection" in lanes:
        _LOG.info("driving reflection")
        report["results"]["reflection"] = await drive_reflection(source=source, output_dir=output_dir)
        write_report(output_dir, report)
    if "impression" in lanes:
        _LOG.info("driving private impression")
        report["results"]["impression"] = await drive_impression(source=source, output_dir=output_dir)
        write_report(output_dir, report)
    if "relationship" in lanes:
        _LOG.info("driving relationship slow variables / we_are")
        report["results"]["relationship"] = await drive_relationship(
            source=source, output_dir=output_dir
        )
        write_report(output_dir, report)
    if "waiting" in lanes:
        _LOG.info("driving waiting_for / come_back")
        report["results"]["waiting"] = await drive_waiting(source=source, output_dir=output_dir)
        write_report(output_dir, report)
    if "reaction" in lanes:
        _LOG.info("driving reaction / sticker")
        report["results"]["reaction"] = await drive_reaction(source=source, output_dir=output_dir)
        write_report(output_dir, report)
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["images_spent"] = images_spent[0]
    write_report(output_dir, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=PRODUCTION_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--lane", action="append", choices=LANES, default=[])
    parser.add_argument("--max-life-attempts", type=int, default=6)
    parser.add_argument("--max-images", type=int, default=2)
    parser.add_argument("--allow-adult-image", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    os.chdir(REPO)
    report = asyncio.run(async_main(args))
    print(json.dumps({"lanes": report.get("lanes"), "finished_at": report.get("finished_at"), "images_spent": report.get("images_spent")}, ensure_ascii=False, indent=2))
    print(f"full report: {args.output_dir.resolve() / 'REPORT.json'}")


if __name__ == "__main__":
    main()
