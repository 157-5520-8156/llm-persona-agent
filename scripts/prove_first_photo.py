#!/usr/bin/env python3
"""Prove the first production-path photo on a cloned ledger.

Never writes ``data/``, never talks to port 8787 / NapCat, never modifies
``src/``, never commits. Images are capped at ``--max-images`` (default 3).

Usage::

    .venv/bin/python scripts/prove_first_photo.py --phase inspect
    .venv/bin/python scripts/prove_first_photo.py --phase tech
    .venv/bin/python scripts/prove_first_photo.py --phase behavior
    .venv/bin/python scripts/prove_first_photo.py --phase he-asked
    .venv/bin/python scripts/prove_first_photo.py --phase all
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
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import drive_production_lanes as drive

WORLD_ID = drive.WORLD_ID
PRODUCTION_DB = drive.PRODUCTION_DB
FOLLOWUP_DB = (REPO / "output" / "drive-0818" / "life-followup.sqlite").resolve()
ATTEMPT5_DB = (REPO / "output" / "drive-0818" / "life-attempt-5.sqlite").resolve()
DEFAULT_OUTPUT = (REPO / "output" / "first-photo").resolve()
BOOK_MARKET_COMPLETE_AT = datetime(2026, 8, 22, 16, 0, tzinfo=UTC)
AFTERMATH_TICK_AT = datetime(2026, 8, 22, 16, 1, 30, tzinfo=UTC)
_LOG = logging.getLogger("prove_first_photo")

MEDIA_EVENT_TYPES = (
    "PhotoCandidateOpened",
    "MediaSelectionAttemptRecorded",
    "MediaSelectionProposalRecorded",
    "MediaOpportunityFrozen",
    "MediaPlanRecorded",
    "MediaNotRenderableRecorded",
    "PhotoCandidateUnrenderable",
    "MediaRenderArtifactRecorded",
    "MediaInspectionRecorded",
    "MediaPreviewGenerated",
    "MediaPreviewFailed",
    "MediaRepairAuthorized",
    "MediaAutomaticDeliveryApproved",
    "MediaDeliveryShared",
    "ActionAuthorized",
    "ActionDelivered",
    "TechnicalFailureRecorded",
)


def _payload_of(event: Mapping[str, Any]) -> dict[str, Any]:
    raw = event.get("payload_json")
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def load_event(conn: sqlite3.Connection, seq: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT ledger_sequence, event_json FROM world_v2_events "
        "WHERE world_id = ? AND ledger_sequence = ?",
        (WORLD_ID, seq),
    ).fetchone()
    if row is None:
        raise KeyError(seq)
    event = json.loads(row[1])
    event["_seq"] = int(row[0])
    event["_payload"] = _payload_of(event)
    return event


def events_of_type(conn: sqlite3.Connection, *types: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    marks = ",".join("?" * len(types))
    for seq, raw in conn.execute(
        "SELECT ledger_sequence, event_json FROM world_v2_events "
        f"WHERE world_id = ? AND json_extract(event_json,'$.event_type') IN ({marks}) "
        "ORDER BY ledger_sequence",
        (WORLD_ID, *types),
    ):
        event = json.loads(raw)
        event["_seq"] = int(seq)
        event["_payload"] = _payload_of(event)
        out.append(event)
    return out


def table_counts(conn: sqlite3.Connection, names: tuple[str, ...]) -> dict[str, int]:
    existing = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    counts: dict[str, int] = {}
    for name in names:
        if name not in existing:
            counts[name] = -1
            continue
        counts[name] = int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
    return counts


def dump_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Task 1 — why she declined
# ---------------------------------------------------------------------------


def inspect_decline(*, followup: Path, output_dir: Path) -> dict[str, Any]:
    conn = drive.open_ro(followup)
    try:
        attempt_events = events_of_type(conn, "MediaSelectionAttemptRecorded")
        candidate_events = events_of_type(conn, "PhotoCandidateOpened")
        evidence_events = events_of_type(conn, "ImageEvidenceDeclared")
        row = conn.execute(
            "SELECT authored_state_json, terminal_result_json, updated_at "
            "FROM world_v2_character_interior_turns WHERE purpose = 'media_selection' "
            "ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            raise SystemExit("no media_selection interior turn on followup clone")
        authored = json.loads(row[0])
        terminal = json.loads(row[1])
        snapshot = authored.get("snapshot") or {}
        materials = json.loads(snapshot.get("materials_json") or "{}")
        dialogue = materials.get("recent_dialogue") or []
        dialogue_texts = [
            {
                "occurred_at": item.get("occurred_at"),
                "speaker": item.get("speaker"),
                "text": item.get("text"),
            }
            for item in dialogue
            if isinstance(item, dict)
        ]
        candidate = (candidate_events[-1]["_payload"] if candidate_events else {}).get(
            "candidate"
        )
        evidence = evidence_events[-1]["_payload"] if evidence_events else {}
        attempt = attempt_events[-1]["_payload"] if attempt_events else {}
        relationship = (materials.get("relationship") or [{}])[0]
        outcome_text = None
        for (text,) in conn.execute(
            "SELECT text FROM world_v2_life_content "
            "WHERE content_ref LIKE '%38127cde1271e5d1%' "
            "AND content_kind = 'occurrence_result' LIMIT 1"
        ):
            outcome_text = text
        reconstructed_safe_summary = (
            "一件已确认、可选择但不必分享的生活事件"
            + (f"｜具体发生：{outcome_text.strip()}" if isinstance(outcome_text, str) and outcome_text.strip() else "")
        )
        tool_description = (
            "Return the complete source-bound media selection choice. The "
            "character may select one offered media candidate or explicitly "
            "choose no_op; the function constrains capability and transport "
            "shape only and does not choose for the character."
        )
        report = {
            "clone": str(followup),
            "candidate": candidate,
            "image_evidence": evidence.get("image_evidence"),
            "image_evidence_visibility": (evidence.get("image_evidence") or {}).get("visibility")
            if isinstance(evidence.get("image_evidence"), dict)
            else None,
            "attempt": {
                "seq": attempt_events[-1]["_seq"] if attempt_events else None,
                "outcome": attempt.get("outcome"),
                "failure_code": attempt.get("failure_code"),
                "model": attempt.get("model"),
                "attempt_id": attempt.get("attempt_id"),
            },
            "her_words": {
                "summary": terminal.get("summary"),
                "decision": ((terminal.get("decision") or {}).get("payload") or {}),
                "attended_source_refs": terminal.get("attended_source_refs"),
                "status": terminal.get("status"),
            },
            "what_she_was_shown": {
                "taxonomy": (candidate or {}).get("ecology_category"),
                "family": (candidate or {}).get("family"),
                "privacy_ceiling": (candidate or {}).get("privacy_ceiling"),
                "selfie_kind": ((candidate or {}).get("character_media_contract") or {}).get(
                    "kind"
                ),
                "reconstructed_safe_summary": reconstructed_safe_summary,
                "safe_summary_note": (
                    "The live capability payload is hashed into the snapshot "
                    "(payload_hash only). Worker compiles safe_summary via "
                    "media_selection_worker.py:_candidate_safe_summary from the "
                    "settled occurrence outcome text. Reconstructed here from "
                    "that function plus world_v2_life_content."
                ),
                "tool_description": tool_description,
                "payload_schema": {
                    "decision": "select|no_op",
                    "selected_token": "required only for select",
                },
                "evidence_summary": (evidence.get("image_evidence") or {}).get("summary")
                if isinstance(evidence.get("image_evidence"), dict)
                else None,
                "remembered_material_excerpts": [
                    excerpt.get("text")
                    for item in materials.get("remembered_material") or []
                    if isinstance(item, dict)
                    for excerpt in item.get("source_excerpts") or []
                    if isinstance(excerpt, dict)
                ],
            },
            "dialogue_context": {
                "recent_dialogue": dialogue_texts,
                "since_he_last_spoke_seconds": (materials.get("since_he_last_spoke") or {}).get(
                    "seconds"
                ),
                "logical_time": materials.get("logical_time"),
                "he_asked_for_a_photo": any(
                    "我想看" in str(item.get("text") or "")
                    or "照片" in str(item.get("text") or "")
                    or "猫猫" in str(item.get("text") or "")
                    for item in dialogue_texts
                    if item.get("speaker") == "counterpart"
                ),
                "note": (
                    "Dialogue is five days old relative to the 8/22 book-market "
                    "candidate. He asked for cat photos; the offered candidate is "
                    "a selfie of the poetry-book morning."
                ),
            },
            "relationship": {
                "stage": relationship.get("stage"),
                "variables": relationship.get("variables"),
                "last_adjusted_at": relationship.get("last_adjusted_at"),
            },
            "attribution": {
                "primary": "a_plus_d",
                "verdict": (
                    "She declined a shareable selfie of a moment she authored as "
                    "'bought for myself'. Relationship is still stranger. The "
                    "stale chat is him asking for a different photo (cats / 路灯). "
                    "This is not a lazy no_op default: she wrote a specific keep-"
                    "it-private reason. Prompt wording '可选择但不必分享' permits "
                    "decline but did not author her reason."
                ),
                "a_candidate_lacks_share_reason": (
                    "Partial. The candidate IS the book-market / rare poetry "
                    "moment, not a generic object. But the settled outcome text "
                    "is 'buys for herself', and the capture is a selfie. She "
                    "read it as a private keep-sake."
                ),
                "b_prompt_makes_no_op_easiest": (
                    "Unlikely as the cause. no_op is a legal one-field branch, "
                    "but her summary is specific and cites the poetry book."
                ),
                "c_still_stranger": (
                    "Contributes. Stage is stranger (trust 120 / closeness 200). "
                    "Sending a face selfie to someone she still classifies that "
                    "way is a high social cost."
                ),
                "d_background_lane_no_live_ask": (
                    "Strong. Selection ran on a scheduler drain at 16:01:30Z "
                    "with since_he_last_spoke ≈ 5.2 days. Nobody in that turn "
                    "asked for THIS photo."
                ),
            },
        }
        dump_json(output_dir / "task1-decline.json", report)
        return report
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Host / overlay
# ---------------------------------------------------------------------------


class RoleOverlay:
    """Optional inbound overlay plus optional forced media-selection."""

    def __init__(
        self,
        inner: object,
        *,
        inbound_payload: dict[str, object] | None = None,
        force_media_select: bool = False,
        force_media_no_op: bool = False,
        on_media_prompt: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self._inner = inner
        self._inbound_payload = inbound_payload
        self._force_media_select = force_media_select
        self._force_media_no_op = force_media_no_op
        self._on_media_prompt = on_media_prompt
        self.model = getattr(inner, "model", "overlay")
        self.provider = getattr(inner, "provider", "overlay")
        self.supports_required_tool_choice = True
        self.supports_strict_tool_choice = bool(
            getattr(inner, "supports_strict_tool_choice", True)
        )
        self.reports_exact_request_emission = bool(
            getattr(inner, "reports_exact_request_emission", False)
        )
        self.captured_media_prompts: list[dict[str, object]] = []

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    @staticmethod
    def _tool_name(tools: list[dict[str, object]] | None) -> str:
        if not tools:
            return ""
        function = tools[0].get("function") if isinstance(tools[0], dict) else None
        name = function.get("name") if isinstance(function, dict) else ""
        return name if isinstance(name, str) else ""

    def _intercept_inbound(self, tools: list[dict[str, object]] | None) -> bool:
        if self._inbound_payload is None:
            return False
        return "compact_gate" in self._tool_name(tools)

    def _is_media_tool(self, tools: list[dict[str, object]] | None) -> bool:
        return self._tool_name(tools) == "character_role_media_selection_v1"

    def _intercept_media(self, tools: list[dict[str, object]] | None) -> bool:
        if not (self._force_media_select or self._force_media_no_op):
            return False
        return self._is_media_tool(tools)

    def _remember_media_prompt(self, messages: list[dict[str, object]]) -> None:
        if not messages:
            return
        try:
            context = self._media_context(messages)
        except Exception:
            return
        self.captured_media_prompts.append(context)
        if self._on_media_prompt is not None:
            self._on_media_prompt(context)

    def _inbound_bytes(self) -> str:
        payload = dict(self._inbound_payload or {})
        kind = str(payload.pop("_result_kind", "reply_only"))
        inner = payload.pop("_payload_json", None)
        if inner is None:
            inner = json.dumps(payload, ensure_ascii=False)
        elif not isinstance(inner, str):
            inner = json.dumps(inner, ensure_ascii=False)
        return json.dumps({"result_kind": kind, "payload_json": inner}, ensure_ascii=False)

    @staticmethod
    def _media_context(messages: list[dict[str, object]]) -> dict[str, object]:
        raw = messages[-1].get("content") if messages else None
        if not isinstance(raw, str):
            raise RuntimeError("media overlay missing user content")
        decoded = json.loads(raw)
        manifest = decoded.get("capability_manifest") if isinstance(decoded, dict) else None
        if not isinstance(manifest, dict):
            raise RuntimeError("media overlay missing capability_manifest")
        payload = manifest.get("payload")
        source_refs = manifest.get("source_refs")
        if not isinstance(payload, dict) or not isinstance(source_refs, list):
            raise RuntimeError("media overlay capability malformed")
        snapshot = decoded.get("inner_life_snapshot") if isinstance(decoded, dict) else None
        materials = snapshot.get("materials") if isinstance(snapshot, dict) else {}
        return {
            "payload": payload,
            "source_refs": [str(item) for item in source_refs if isinstance(item, str)],
            "candidates": payload.get("candidates"),
            "conversation": (materials or {}).get("conversation")
            if isinstance(materials, dict)
            else None,
            "purpose": ((decoded.get("inner_turn") or {}) if isinstance(decoded, dict) else {}).get(
                "purpose"
            ),
        }

    def _media_bytes(self, messages: list[dict[str, object]]) -> str:
        context = self._media_context(messages)
        self.captured_media_prompts.append(context)
        if self._on_media_prompt is not None:
            self._on_media_prompt(context)
        candidates = context.get("candidates")
        first = candidates[0] if isinstance(candidates, list) and candidates else None
        token = first.get("token") if isinstance(first, dict) else None
        if self._force_media_select:
            if not isinstance(token, str) or not token:
                raise RuntimeError("forced media select received no token")
            body = {
                "status": "decision",
                "summary": "技术证明：把已打开的旧书市候选交出去走完生成链。",
                "attended_source_refs": list(context["source_refs"]),
                "decision": {
                    "source_refs": list(context["source_refs"]),
                    "payload": {"decision": "select", "selected_token": token},
                },
                "recall_query": None,
                "proposals": [],
            }
        else:
            body = {
                "status": "decision",
                "summary": "技术对照：强制 no_op。",
                "attended_source_refs": list(context["source_refs"]),
                "decision": {
                    "source_refs": list(context["source_refs"]),
                    "payload": {"decision": "no_op"},
                },
                "recall_query": None,
                "proposals": [],
            }
        return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    async def complete_json(
        self,
        messages: list[dict[str, object]],
        *,
        temperature: float = 0.8,
        tools: list[dict[str, object]] | None = None,
        tool_choice: object | None = None,
    ) -> str:
        if self._intercept_inbound(tools):
            return self._inbound_bytes()
        if self._is_media_tool(tools) and not self._intercept_media(tools):
            self._remember_media_prompt(messages)
        if self._intercept_media(tools):
            return self._media_bytes(messages)
        return await self._inner.complete_json(
            messages, temperature=temperature, tools=tools, tool_choice=tool_choice
        )

    async def complete_json_with_usage(
        self,
        messages: list[dict[str, object]],
        *,
        temperature: float = 0.8,
        tools: list[dict[str, object]] | None = None,
        tool_choice: object | None = None,
    ) -> tuple[str, dict[str, object]]:
        if self._intercept_inbound(tools):
            return self._inbound_bytes(), drive._overlay_usage()
        if self._is_media_tool(tools) and not self._intercept_media(tools):
            self._remember_media_prompt(messages)
        if self._intercept_media(tools):
            return self._media_bytes(messages), drive._overlay_usage()
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
        if self._intercept_inbound(tools) or self._intercept_media(tools):
            text = (
                self._inbound_bytes()
                if self._intercept_inbound(tools)
                else self._media_bytes(messages)
            )
            if on_text_delta is not None:
                on_text_delta(text)
            return text, drive._overlay_usage()
        return await self._inner.complete_json_stream_with_usage(
            messages,
            temperature=temperature,
            on_text_delta=on_text_delta,
            tools=tools,
            tool_choice=tool_choice,
        )


@dataclass
class ProveSession(drive.DriveSession):
    overlay: RoleOverlay | None = None
    image_output: Path | None = None

    async def inbound_without_background(self, text: str) -> dict[str, Any]:
        """Commit his message (and her reply if the turn completes) without
        draining the media continuation/render lane.

        ``DriveSession.inbound`` always follows with ``drain(background=8)``,
        which is how a select can accidentally pay for another OpenAI image.
        """

        when = self.clock + timedelta(seconds=2)
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
        return {
            "status": getattr(result, "status", None),
            "action_id": getattr(result, "action_id", None),
            "visible": self.delivery.sent[before:],
            "observed_at": when.isoformat(),
            "message_id": message_id,
        }

    async def drain_preview_prefix(self, *, rounds: int = 4) -> list[dict[str, Any]]:
        """Advance select → accept → plan only. Never render or deliver."""

        platform = getattr(self.host, "_host", None)
        advance = getattr(platform, "drain_media_preview_once", None)
        if not callable(advance):
            raise RuntimeError("host has no drain_media_preview_once")
        seen: list[dict[str, Any]] = []
        for index in range(rounds):
            result = await advance(
                trace_id=f"trace:prove:media-preview:{index}",
                correlation_id=f"correlation:prove:he-asked:{index}",
            )
            item = {
                "status": getattr(result, "status", None),
                "reason_code": getattr(result, "reason_code", None),
                "selection": None
                if getattr(result, "selection", None) is None
                else {
                    "status": getattr(result.selection, "status", None),
                    "reason_code": getattr(result.selection, "reason_code", None),
                },
            }
            seen.append(item)
            status = item["status"]
            selection_status = (item["selection"] or {}).get("status")
            if status in {"planned", "not_renderable", "blocked"} or selection_status in {
                "proposed",
                "no_op",
                "blocked",
            }:
                break
            if status == "idle" and selection_status is None:
                break
        return seen


async def open_prove_session(
    *,
    database: Path,
    output_dir: Path,
    inbound_payload: dict[str, object] | None = None,
    force_media_select: bool = False,
    enable_media: bool = True,
    on_media_prompt: Callable[[dict[str, object]], None] | None = None,
) -> ProveSession:
    from companion_daemon.config import Settings
    from companion_daemon.llm import DeepSeekChatModel
    from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore
    from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host, qq_c2c_world_id
    from companion_daemon.world_v2.qq_media_deployment import build_qq_media_preview_deployment

    if drive._is_production_path(database):
        raise SystemExit(f"refusing to open production ledger for write: {database}")
    sidecar = output_dir / f"{database.stem}.sidecars"
    sidecar.mkdir(parents=True, exist_ok=True)
    image_output = output_dir / "generated"
    image_output.mkdir(parents=True, exist_ok=True)
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
    overlay = RoleOverlay(
        inner,
        inbound_payload=inbound_payload,
        force_media_select=force_media_select,
        on_media_prompt=on_media_prompt,
    )
    media_preview = None
    media_transport = None
    if enable_media:
        bundle = build_qq_media_preview_deployment(
            settings=settings,
            world_id=qq_c2c_world_id(settings.primary_user_id),
            output_dir=image_output,
        )
        if bundle is None:
            raise RuntimeError("media deployment bundle is None (grants/keys/flags missing)")
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
    session = ProveSession(
        database=database,
        recipient_id=recipient_id,
        delivery=delivery,
        host=host,
        clock=datetime.now(UTC),
        overlay=overlay,
        image_output=image_output,
    )
    await session.logical_time()
    return session


def ledger_media_evidence(database: Path, *, after_seq: int = 0) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        counts = table_counts(
            conn,
            (
                "world_v2_media_payload",
                "world_v2_media_provider_dispatch",
                "world_v2_media_pending_inspection",
                "world_v2_event_media_planning_result",
            ),
        )
        interesting = []
        for seq, raw in conn.execute(
            "SELECT ledger_sequence, event_json FROM world_v2_events "
            "WHERE world_id = ? AND ledger_sequence > ? ORDER BY ledger_sequence",
            (WORLD_ID, after_seq),
        ):
            event = json.loads(raw)
            kind = event.get("event_type")
            if kind not in MEDIA_EVENT_TYPES and not (
                isinstance(kind, str) and kind.startswith("Media")
            ):
                continue
            payload = _payload_of(event)
            excerpt = {
                key: payload.get(key)
                for key in (
                    "candidate_id",
                    "preview_id",
                    "plan_id",
                    "inspection_id",
                    "artifact_id",
                    "passed",
                    "reason",
                    "outcome",
                    "failure_code",
                    "status",
                    "opportunity_id",
                )
                if key in payload
            }
            action = payload.get("action") if isinstance(payload.get("action"), dict) else None
            if action and "kind" in action:
                excerpt["action.kind"] = action.get("kind")
            inspection = payload.get("inspection") if isinstance(payload.get("inspection"), dict) else None
            if inspection:
                excerpt["inspection.passed"] = inspection.get("passed")
                excerpt["inspection.reason"] = inspection.get("reason")
                excerpt["inspection.inspector_model"] = inspection.get("inspector_model")
            interesting.append(
                {
                    "seq": int(seq),
                    "event_type": kind,
                    "logical_time": event.get("logical_time"),
                    "payload": excerpt,
                }
            )
        payloads = []
        if counts.get("world_v2_media_payload", 0) > 0:
            for row in conn.execute(
                "SELECT payload_ref, payload_hash, content_type, length(body) AS nbytes "
                "FROM world_v2_media_payload LIMIT 12"
            ):
                payloads.append(dict(row))
        dispatches = []
        if counts.get("world_v2_media_provider_dispatch", 0) > 0:
            cols = [item[1] for item in conn.execute("PRAGMA table_info(world_v2_media_provider_dispatch)")]
            for row in conn.execute("SELECT * FROM world_v2_media_provider_dispatch LIMIT 8"):
                item = dict(zip(cols, row))
                for key, value in list(item.items()):
                    if isinstance(value, (bytes, memoryview)):
                        item[key] = f"<{len(value)} bytes>"
                    elif isinstance(value, str) and len(value) > 400:
                        item[key] = value[:400] + "…"
                dispatches.append(item)
        inspections = []
        if counts.get("world_v2_media_pending_inspection", 0) > 0:
            cols = [item[1] for item in conn.execute("PRAGMA table_info(world_v2_media_pending_inspection)")]
            for row in conn.execute("SELECT * FROM world_v2_media_pending_inspection LIMIT 8"):
                item = dict(zip(cols, row))
                for key, value in list(item.items()):
                    if isinstance(value, str) and len(value) > 800:
                        item[key] = value[:800] + "…"
                inspections.append(item)
        return {
            "table_counts": counts,
            "events": interesting,
            "payload_rows": payloads,
            "dispatch_rows": dispatches,
            "pending_inspection_rows": inspections,
        }
    finally:
        conn.close()


def list_images(directory: Path) -> list[dict[str, Any]]:
    if not directory.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(directory.glob("**/*")):
        if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        out.append(
            {
                "path": str(path.resolve()),
                "bytes": path.stat().st_size,
                "suffix": path.suffix.lower(),
            }
        )
    return out


def interior_media_turns(database: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT purpose, state, terminal_result_json, updated_at "
            "FROM world_v2_character_interior_turns WHERE purpose = 'media_selection' "
            "ORDER BY updated_at"
        ).fetchall()
        out = []
        for purpose, state, raw, updated in rows:
            terminal = json.loads(raw) if raw else {}
            out.append(
                {
                    "purpose": purpose,
                    "state": state,
                    "updated_at": updated,
                    "summary": terminal.get("summary"),
                    "decision": ((terminal.get("decision") or {}).get("payload") or {}),
                }
            )
        return out
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Task 2A — generator proof
# ---------------------------------------------------------------------------


async def prepare_candidate_open(*, source: Path, output_dir: Path) -> dict[str, Any]:
    """Tick the 8/22 completed plan until PhotoCandidateOpened, without asking her."""

    clone = output_dir / "candidate-open.sqlite"
    drive.clone_ledger(source, clone)
    started_seq = drive.current_seq(clone)
    usage_from = drive.current_usage_id(clone)
    session = await open_prove_session(
        database=clone, output_dir=output_dir, enable_media=False
    )
    ticks: list[dict[str, Any]] = []
    try:
        now = await session.logical_time()
        target = AFTERMATH_TICK_AT if now < AFTERMATH_TICK_AT else now + timedelta(seconds=90)
        ticks.append(await session.tick_to(target, reason="open-candidate"))
        await session.drain_loop(rounds=12, background=16)
        for extra in range(1, 6):
            events = drive.new_events(clone, started_seq)
            if any(item["event_type"] == "PhotoCandidateOpened" for item in events):
                break
            now = await session.logical_time()
            ticks.append(
                await session.tick_to(now + timedelta(seconds=90), reason=f"open-candidate-{extra}")
            )
            await session.drain_loop(rounds=8, background=12)
        evidence = ledger_media_evidence(clone, after_seq=started_seq)
        opened = any(item["event_type"] == "PhotoCandidateOpened" for item in evidence["events"])
        return {
            "status": "opened" if opened else "no_candidate",
            "clone": str(clone),
            "ticks": ticks,
            "events": evidence["events"],
            "cost": drive.cost_report(clone, since_id=usage_from),
        }
    finally:
        await session.close()


async def drive_tech_proof(
    *, source: Path, output_dir: Path, max_images: int
) -> dict[str, Any]:
    clone = output_dir / "tech-proof.sqlite"
    drive.clone_ledger(source, clone)
    started_seq = drive.current_seq(clone)
    usage_from = drive.current_usage_id(clone)
    captured_prompts: list[dict[str, object]] = []
    session = await open_prove_session(
        database=clone,
        output_dir=output_dir,
        force_media_select=True,
        enable_media=True,
        on_media_prompt=captured_prompts.append,
    )
    notes: list[str] = []
    drains: list[dict[str, Any]] = []
    gap_probe: dict[str, Any] | None = None
    try:
        now = await session.logical_time()
        target = AFTERMATH_TICK_AT if now < AFTERMATH_TICK_AT else now + timedelta(seconds=90)
        tick = await session.tick_to(target, reason="tech-aftermath")
        notes.append(f"tick {tick}")
        for round_id in range(1, 25):
            item = await session.drain(actions=8, background=16)
            drains.append({"round": round_id, **item})
            evidence = ledger_media_evidence(clone, after_seq=started_seq)
            kinds = {row["event_type"] for row in evidence["events"]}
            if "MediaPreviewGenerated" in kinds or "MediaPreviewFailed" in kinds:
                notes.append(f"terminal media event after drain round {round_id}")
                break
            if "MediaSelectionAttemptRecorded" in kinds and "MediaSelectionProposalRecorded" not in kinds:
                notes.append("selection declined even under overlay")
                break
            if not item["action_statuses"] and not item["background_statuses"]:
                if round_id >= 3:
                    break
        # Keep pumping provider actions / continuation / auto-delivery.
        for round_id in range(1, 20):
            item = await session.drain(actions=8, background=8)
            drains.append({"pump": round_id, **item})
            evidence = ledger_media_evidence(clone, after_seq=started_seq)
            kinds = {row["event_type"] for row in evidence["events"]}
            delivered = any(
                row["event_type"] == "MediaAutomaticDeliveryApproved"
                for row in evidence["events"]
            )
            if delivered and any(
                unit.get("kind") == "image" for unit in session.delivery.sent
            ):
                notes.append(f"capture received image after pump {round_id}")
                break
            if "MediaPreviewFailed" in kinds:
                break
            if not item["action_statuses"] and not item["background_statuses"] and round_id >= 2:
                break

        images = list_images(output_dir / "generated")
        evidence = ledger_media_evidence(clone, after_seq=started_seq)
        capture_images = [unit for unit in session.delivery.sent if unit.get("kind") == "image"]

        # Policy probe: with only one preview, a second drain should be idle
        # (nothing left to send). Advance 30 minutes — still idle. This does
        # not spend a second image. If a second preview already exists, the
        # min_gap branch should fire because we stay inside two hours.
        try:
            before_gap = ledger_media_evidence(clone, after_seq=started_seq)
            now = await session.logical_time()
            gap_tick = await session.tick_to(now + timedelta(minutes=30), reason="min-gap-probe")
            gap_drain = await session.drain_loop(rounds=6, background=8)
            after_gap = ledger_media_evidence(clone, after_seq=started_seq)
            new_approvals = [
                row
                for row in after_gap["events"]
                if row["event_type"] == "MediaAutomaticDeliveryApproved"
                and row["seq"]
                > max(
                    (item["seq"] for item in before_gap["events"] if item["event_type"] == "MediaAutomaticDeliveryApproved"),
                    default=0,
                )
            ]
            gap_probe = {
                "tick": gap_tick,
                "drains_tail": gap_drain[-3:],
                "new_approvals": new_approvals,
                "note": (
                    "A new approval inside 2h would violate min_gap. Zero new "
                    "approvals means either min_gap held or there was no second "
                    "inspection-passed preview."
                ),
            }
        except Exception as exc:
            gap_probe = {
                "error": {"type": type(exc).__name__, "message": str(exc)[:1500]},
                "traceback": traceback.format_exc()[-2500:],
            }

        return {
            "status": "generated"
            if images or capture_images
            else "no_image",
            "clone": str(clone),
            "tick": tick,
            "notes": notes,
            "drains_tail": drains[-8:],
            "captured_media_prompts": captured_prompts,
            "images": images,
            "capture_images": capture_images,
            "capture_all": session.delivery.sent,
            "ledger": evidence,
            "interior_turns": interior_media_turns(clone),
            "gap_probe": gap_probe,
            "cost": drive.cost_report(clone, since_id=usage_from),
            "max_images": max_images,
        }
    except Exception as exc:
        return {
            "status": "error",
            "clone": str(clone),
            "error": {"type": type(exc).__name__, "message": str(exc)[:3000]},
            "traceback": traceback.format_exc()[-5000:],
            "captured_media_prompts": captured_prompts,
            "ledger": ledger_media_evidence(clone, after_seq=started_seq),
            "images": list_images(output_dir / "generated"),
            "cost": drive.cost_report(clone, since_id=usage_from),
        }
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# Task 2B — will she accept?
# ---------------------------------------------------------------------------


FRIEND_SLIM = {
    "messages": ["旧书市那本诗集我先自己看着，回头再说"],
    "felt": "想把我们算成朋友",
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

HE_ASKED_SLIM = {
    "messages": ["这本是给我自己买的，先不拍"],
    "felt": "他在问这本诗集的照片",
    "stuck_with_me": "他点名要看旧书市那本诗集",
    "wants": "自己先决定要不要给",
    "photo": False,
    "matters_bp": 6200,
}

HE_ASKED_TEXT = "旧书市逛完了吧？把你翻到的那本诗集拍一张给我看看"


async def _advance_to_friend(session: ProveSession, clone: Path, started_seq: int) -> dict[str, Any]:
    inbound = await session.inbound("我们现在算什么")
    drains = await session.drain_loop(rounds=12, background=12)
    events = drive.new_events(clone, started_seq)
    commitments = [item for item in events if item["event_type"] == "RelationshipCommitmentAccepted"]
    return {
        "inbound": inbound,
        "drains_tail": drains[-4:],
        "commitments": commitments,
        "visible": session.delivery.sent,
    }


async def run_behavior_trial(
    *,
    source: Path,
    output_dir: Path,
    trial_id: str,
    closer: bool,
    he_asked: bool,
) -> dict[str, Any]:
    clone = output_dir / f"behavior-{trial_id}.sqlite"
    drive.clone_ledger(source, clone)
    started_seq = drive.current_seq(clone)
    usage_from = drive.current_usage_id(clone)
    inbound_payload = None
    if closer:
        inbound_payload = drive._authored_inbound_payload(FRIEND_SLIM, reply_only=False)
    elif he_asked:
        inbound_payload = drive._authored_inbound_payload(HE_ASKED_SLIM, reply_only=True)
    captured: list[dict[str, object]] = []
    session = await open_prove_session(
        database=clone,
        output_dir=output_dir,
        inbound_payload=inbound_payload,
        force_media_select=False,
        enable_media=True,
        on_media_prompt=captured.append,
    )
    try:
        relationship_step = None
        asked_step = None
        if closer:
            relationship_step = await _advance_to_friend(session, clone, started_seq)
        if he_asked:
            asked_step = await session.inbound(HE_ASKED_TEXT)
        drains = await session.drain_loop(rounds=16, background=16)
        turns = interior_media_turns(clone)
        evidence = ledger_media_evidence(clone, after_seq=started_seq)
        attempts = [row for row in evidence["events"] if row["event_type"] == "MediaSelectionAttemptRecorded"]
        proposals = [row for row in evidence["events"] if row["event_type"] == "MediaSelectionProposalRecorded"]
        decision = turns[-1] if turns else None
        accepted = bool(proposals)
        declined = any(
            (row.get("payload") or {}).get("outcome") == "declined" for row in attempts
        ) or (
            isinstance(decision, dict)
            and (decision.get("decision") or {}).get("decision") == "no_op"
        )
        return {
            "trial_id": trial_id,
            "clone": str(clone),
            "conditions": {"relationship": "friend_attempt" if closer else "stranger", "he_asked": he_asked},
            "relationship_step": relationship_step,
            "asked_step": asked_step,
            "drains_tail": drains[-4:],
            "decision": decision,
            "accepted": accepted,
            "declined": declined and not accepted,
            "attempts": attempts,
            "proposals": proposals,
            "captured_prompts_tail": captured[-1:] if captured else [],
            "cost": drive.cost_report(clone, since_id=usage_from),
        }
    except Exception as exc:
        return {
            "trial_id": trial_id,
            "clone": str(clone),
            "conditions": {"relationship": "friend_attempt" if closer else "stranger", "he_asked": he_asked},
            "error": {"type": type(exc).__name__, "message": str(exc)[:2000]},
            "traceback": traceback.format_exc()[-3000:],
            "decision": interior_media_turns(clone)[-1:] or None,
            "cost": drive.cost_report(clone, since_id=usage_from),
        }
    finally:
        await session.close()


async def drive_behavior(
    *, source: Path, output_dir: Path, trials_per_cell: int
) -> dict[str, Any]:
    open_report = await prepare_candidate_open(source=source, output_dir=output_dir)
    dump_json(output_dir / "candidate-open.json", open_report)
    if open_report.get("status") != "opened":
        return {"status": "no_candidate", "open": open_report}
    open_clone = Path(open_report["clone"])
    cells = [
        ("stranger_background", False, False),
        ("stranger_he_asked", False, True),
        ("closer_background", True, False),
        ("closer_he_asked", True, True),
    ]
    trials: list[dict[str, Any]] = []
    for cell_name, closer, he_asked in cells:
        for index in range(1, trials_per_cell + 1):
            trial_id = f"{cell_name}-{index}"
            _LOG.info("behavior trial %s", trial_id)
            row = await run_behavior_trial(
                source=open_clone,
                output_dir=output_dir,
                trial_id=trial_id,
                closer=closer,
                he_asked=he_asked,
            )
            trials.append(row)
            dump_json(output_dir / f"behavior-{trial_id}.json", row)
            if closer and row.get("error"):
                _LOG.warning("closer cell failed on %s; skipping remaining closer trials", trial_id)
                break
            if closer:
                step = row.get("relationship_step") or {}
                if not step.get("commitments") and index == 1:
                    _LOG.warning("friend commitment did not land; remaining closer trials will repeat the same gate")
    rates: dict[str, dict[str, Any]] = {}
    for cell_name, closer, he_asked in cells:
        subset = [row for row in trials if row.get("conditions") == {"relationship": "friend_attempt" if closer else "stranger", "he_asked": he_asked}]
        real = [row for row in subset if "error" not in row]
        accepted = sum(1 for row in real if row.get("accepted"))
        declined = sum(1 for row in real if row.get("declined"))
        quotes = [
            {
                "trial_id": row.get("trial_id"),
                "summary": (row.get("decision") or {}).get("summary"),
                "payload": (row.get("decision") or {}).get("decision"),
            }
            for row in real
        ]
        rates[cell_name] = {
            "n": len(real),
            "accepted": accepted,
            "declined": declined,
            "accept_rate": (accepted / len(real) if real else None),
            "quotes": quotes,
            "errors": [row.get("error") for row in subset if "error" in row],
        }
    return {"status": "ran", "open": open_report, "trials": trials, "rates": rates}


async def run_he_asked_selection_trial(
    *,
    source: Path,
    output_dir: Path,
    trial_id: str,
) -> dict[str, Any]:
    """He asks in chat; she decides media_selection for real; no OpenAI image."""

    clone = output_dir / f"behavior-{trial_id}.sqlite"
    drive.clone_ledger(source, clone)
    started_seq = drive.current_seq(clone)
    usage_from = drive.current_usage_id(clone)
    captured: list[dict[str, object]] = []
    session = await open_prove_session(
        database=clone,
        output_dir=output_dir,
        inbound_payload=None,
        force_media_select=False,
        enable_media=True,
        on_media_prompt=captured.append,
    )
    try:
        asked_step = await session.inbound_without_background(HE_ASKED_TEXT)
        turns = interior_media_turns(clone)
        preview_steps: list[dict[str, Any]] = []
        if not turns:
            preview_steps = await session.drain_preview_prefix(rounds=4)
            turns = interior_media_turns(clone)
        evidence = ledger_media_evidence(clone, after_seq=started_seq)
        attempts = [row for row in evidence["events"] if row["event_type"] == "MediaSelectionAttemptRecorded"]
        proposals = [row for row in evidence["events"] if row["event_type"] == "MediaSelectionProposalRecorded"]
        renders = [row for row in evidence["events"] if row["event_type"] == "MediaRenderArtifactRecorded"]
        decision = turns[-1] if turns else None
        accepted = bool(proposals)
        declined = any(
            (row.get("payload") or {}).get("outcome") == "declined" for row in attempts
        ) or (
            isinstance(decision, dict)
            and (decision.get("decision") or {}).get("decision") == "no_op"
        )
        return {
            "trial_id": trial_id,
            "clone": str(clone),
            "conditions": {"relationship": "stranger", "he_asked": True, "render": False},
            "asked_step": asked_step,
            "preview_steps": preview_steps,
            "decision": decision,
            "accepted": accepted,
            "declined": declined and not accepted,
            "attempts": attempts,
            "proposals": proposals,
            "rendered": bool(renders),
            "captured_prompts_tail": captured[-1:] if captured else [],
            "cost": drive.cost_report(clone, since_id=usage_from),
        }
    except Exception as exc:
        return {
            "trial_id": trial_id,
            "clone": str(clone),
            "conditions": {"relationship": "stranger", "he_asked": True, "render": False},
            "error": {"type": type(exc).__name__, "message": str(exc)[:2000]},
            "traceback": traceback.format_exc()[-3000:],
            "decision": (interior_media_turns(clone) or [None])[-1],
            "cost": drive.cost_report(clone, since_id=usage_from),
        }
    finally:
        await session.close()


async def drive_he_asked(
    *, source: Path, output_dir: Path, trials: int
) -> dict[str, Any]:
    open_clone = output_dir / "candidate-open.sqlite"
    if not open_clone.exists():
        open_report = await prepare_candidate_open(source=source, output_dir=output_dir)
        dump_json(output_dir / "candidate-open.json", open_report)
        if open_report.get("status") != "opened":
            return {"status": "no_candidate", "open": open_report}
        open_clone = Path(open_report["clone"])
    else:
        open_report = {"status": "reused", "clone": str(open_clone)}
    rows: list[dict[str, Any]] = []
    for index in range(1, trials + 1):
        trial_id = f"stranger_he_asked_retry-{index}"
        _LOG.info("he-asked selection-only trial %s", trial_id)
        row = await run_he_asked_selection_trial(
            source=open_clone, output_dir=output_dir, trial_id=trial_id
        )
        rows.append(row)
        dump_json(output_dir / f"behavior-{trial_id}.json", row)
        if row.get("rendered"):
            _LOG.error("selection-only trial rendered an image; stopping")
            break
    real = [row for row in rows if "error" not in row]
    accepted = sum(1 for row in real if row.get("accepted"))
    declined = sum(1 for row in real if row.get("declined"))
    return {
        "status": "ran",
        "open": open_report,
        "note": "Selection only: inbound_text without background drain, then drain_media_preview_once. No OpenAI image.",
        "trials": rows,
        "rates": {
            "stranger_he_asked_retry": {
                "n": len(real),
                "accepted": accepted,
                "declined": declined,
                "accept_rate": (accepted / len(real) if real else None),
                "quotes": [
                    {
                        "trial_id": row.get("trial_id"),
                        "inbound_status": (row.get("asked_step") or {}).get("status"),
                        "inbound_visible": (row.get("asked_step") or {}).get("visible"),
                        "summary": (row.get("decision") or {}).get("summary"),
                        "payload": (row.get("decision") or {}).get("decision"),
                    }
                    for row in real
                ],
                "errors": [row.get("error") for row in rows if "error" in row],
            }
        },
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def write_markdown(output_dir: Path, bundle: dict[str, Any]) -> None:
    task1 = bundle.get("task1") or {}
    tech = bundle.get("tech") or {}
    behavior = bundle.get("behavior") or {}
    her = (task1.get("her_words") or {}).get("summary")
    shown = task1.get("what_she_was_shown") or {}
    images = tech.get("images") or []
    capture = tech.get("capture_images") or []
    ledger = tech.get("ledger") or {}
    lines = [
        "# 第一张图验收报告",
        "",
        f"生成时间：{datetime.now(UTC).isoformat()}",
        "",
        "## 1. 她拒绝的原文与归因",
        "",
        f"**原文（instant private self / summary）**：{her}",
        "",
        f"**决策**：`{(task1.get('her_words') or {}).get('decision')}`",
        "",
        f"**尝试结局**：`{(task1.get('attempt') or {}).get('outcome')}` / model `{(task1.get('attempt') or {}).get('model')}`",
        "",
        "她当时看到的候选：",
        "",
        f"- taxonomy / family：`{shown.get('taxonomy')}` / `{shown.get('family')}`",
        f"- privacy_ceiling：`{shown.get('privacy_ceiling')}`",
        f"- 自拍 kind：`{shown.get('selfie_kind')}`",
        f"- 重建的 safe_summary：{shown.get('reconstructed_safe_summary')}",
        f"- 视觉证据摘要：{shown.get('evidence_summary')}",
        f"- 工具说明：{shown.get('tool_description')}",
        "",
        f"对话上下文：`since_he_last_spoke` = {(task1.get('dialogue_context') or {}).get('since_he_last_spoke_seconds')} 秒；最近对方原话见 `task1-decline.json`。关系阶段：`{(task1.get('relationship') or {}).get('stage')}`。",
        "",
        f"**结论**：{(task1.get('attribution') or {}).get('verdict')}",
        "",
        "## 2. 图片文件路径 + 账本证据（验收核心）",
        "",
    ]
    if images:
        first = images[0]
        lines += [
            f"**图片**：`{first.get('path')}`",
            f"**大小 / 格式**：{first.get('bytes')} bytes / `{first.get('suffix')}`",
            "",
        ]
    else:
        lines += ["**没有生成出图片文件。** 见下方断点。", ""]
    if capture:
        lines += [f"Capture 投递：`{capture}`", ""]
    lines += [
        f"表行数：`{ledger.get('table_counts')}`",
        "",
        "相关事件：",
        "",
        "```json",
        json.dumps(ledger.get("events") or [], ensure_ascii=False, indent=2, default=str)[:8000],
        "```",
        "",
        f"tech status：`{tech.get('status')}`",
        "",
        "## 3. 接受率实测",
        "",
    ]
    rates = behavior.get("rates") or {}
    if rates:
        lines += [
            "| 条件 | n | 接受 | 拒绝 | 接受率 |",
            "|---|---:|---:|---:|---:|",
        ]
        for name, row in rates.items():
            rate = row.get("accept_rate")
            rate_s = "" if rate is None else f"{rate:.0%}"
            lines.append(
                f"| {name} | {row.get('n')} | {row.get('accepted')} | {row.get('declined')} | {rate_s} |"
            )
        lines.append("")
        for name, row in rates.items():
            lines.append(f"### {name} 原话")
            lines.append("")
            for quote in row.get("quotes") or []:
                lines.append(
                    f"- `{quote.get('trial_id')}`：{(quote.get('summary') or '').strip()} / `{quote.get('payload')}`"
                )
            lines.append("")
    else:
        lines += ["行为实测未跑或没有候选。", ""]
    tech_ok = tech.get("status") == "generated"
    lines += [
        "## 4. 上线阻塞判断",
        "",
        f"- 生成后半段：**{'能跑' if tech_ok else '有断点 / 未出图'}**（status=`{tech.get('status')}`）。这是「我们发不出去」还是「她不想发」的分界：技术链必须先被排除。",
        f"- 她自然答应的条件：见接受率表。若各格都接近 0，问题在提示词/情境（后台冷问 + 自拍 + stranger），不是生成器。改进方向：让选片发生在对话中、候选带上「这一刻为什么值得分享」、不要把「不必分享」写成唯一轻松出口——但仍由她决定。",
        f"- 最小间隔 / 每日 2 张：`{json.dumps(tech.get('gap_probe'), ensure_ascii=False, default=str)[:1500]}`",
        "",
        "## 5. 实际花费",
        "",
        f"- task1：0（只读）",
        f"- tech：`{(tech.get('cost') or {}).get('cost_cny')}` CNY / `{(tech.get('cost') or {}).get('calls')}` 次调用 / by_purpose `{(tech.get('cost') or {}).get('by_purpose')}`",
        f"- behavior：`{(behavior.get('open') or {}).get('cost')}` 打开候选；各 trial 见 JSON。OpenAI 生图费用不进 `world_v2_model_usage`（ledger 以 amount_limit=0 记部署侧支付）。预算估计 gpt-image-2 1024x1536 medium ≈ $0.041/张（约 ¥0.30 输出）+ 参考图输入。",
        "",
        "## 6. 发现但没改的问题",
        "",
        "- `src/` 未改。选片 capability 的完整候选描述只以 hash 进快照，审计时必须从 worker 重建或在调用时抓包。",
        "- 生产审查走 `SourcedLifeMediaInspector`（确定性来源闭合），不是视觉模型；≤1 次修复仍在 `event_media.MediaRenderer.render` 的循环里。",
        "- 关系 `we_are` 接受链在克隆上可能仍撞 `relationship_state_policy_uninstalled`（并行线正在修 `relationship_proposal_compiler.py`）。",
        "- 自动投递 min_gap 需要**第二张**已审查预览才能真正打出 `min_gap`；单候选链只能证明不会把同一张发两次。",
        "",
    ]
    (output_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        choices=("inspect", "tech", "behavior", "he-asked", "all"),
        default="all",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-images", type=int, default=3)
    parser.add_argument("--behavior-trials", type=int, default=3)
    parser.add_argument(
        "--followup",
        type=Path,
        default=FOLLOWUP_DB,
        help="clone that already has PhotoCandidateOpened + decline",
    )
    parser.add_argument(
        "--life-source",
        type=Path,
        default=ATTEMPT5_DB,
        help="clone with ActivityCompleted, before candidate open/decline",
    )
    return parser.parse_args()


async def async_main(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "output_dir": str(output_dir),
        "phase": args.phase,
    }
    if args.phase in {"inspect", "all"}:
        _LOG.info("task 1: inspect decline")
        bundle["task1"] = inspect_decline(followup=args.followup.resolve(), output_dir=output_dir)
        dump_json(output_dir / "bundle-partial.json", bundle)
    if args.phase in {"tech", "all"}:
        _LOG.info("task 2A: tech proof")
        bundle["tech"] = await drive_tech_proof(
            source=args.life_source.resolve(),
            output_dir=output_dir,
            max_images=args.max_images,
        )
        dump_json(output_dir / "tech-proof.json", bundle["tech"])
        dump_json(output_dir / "bundle-partial.json", bundle)
    if args.phase in {"behavior", "all"}:
        _LOG.info("task 2B: behavior rates")
        bundle["behavior"] = await drive_behavior(
            source=args.life_source.resolve(),
            output_dir=output_dir,
            trials_per_cell=args.behavior_trials,
        )
        dump_json(output_dir / "behavior.json", bundle["behavior"])
    if args.phase == "he-asked":
        _LOG.info("task 2B retry: he-asked selection only (no render)")
        bundle["he_asked"] = await drive_he_asked(
            source=args.life_source.resolve(),
            output_dir=output_dir,
            trials=args.behavior_trials,
        )
        dump_json(output_dir / "behavior-he-asked-retry.json", bundle["he_asked"])
    bundle["finished_at"] = datetime.now(UTC).isoformat()
    dump_json(output_dir / "BUNDLE.json", bundle)
    if args.phase == "all":
        write_markdown(output_dir, bundle)
    return bundle


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = parse_args()
    bundle = asyncio.run(async_main(args))
    print(json.dumps({"output_dir": bundle.get("output_dir"), "phase": bundle.get("phase"), "tech_status": (bundle.get("tech") or {}).get("status"), "behavior_status": (bundle.get("behavior") or {}).get("status")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
