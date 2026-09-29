#!/usr/bin/env python3
"""Read-only auditor: what she saw vs what the ledger actually recorded.

Every finding is a decidable mismatch between Inner Life materials (the
snapshot she was given on that turn) and World Events at that cursor.
The host never writes ``data/``, never talks to NapCat, and never restarts
the production process.

Usage::

    .venv/bin/python scripts/audit_seen_vs_ledger.py
    .venv/bin/python scripts/audit_seen_vs_ledger.py --database path/to/clone.sqlite
    .venv/bin/python scripts/audit_seen_vs_ledger.py --out output/seen-vs-ledger

Artifacts land under ``output/seen-vs-ledger/`` (REPORT.md + findings.json).
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any, Iterable
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
PRODUCTION_DB = (REPO / "data" / "companion.epoch2.sqlite").resolve()
WORLD_ID = "world:companion-v2:qq-c2c:geoff"
DEFAULT_OUTPUT = (REPO / "output" / "seen-vs-ledger").resolve()
SHANGHAI = ZoneInfo("Asia/Shanghai")

SITTING_WINDOW = timedelta(hours=2)
LIVE_HEAD_EACH = 3
SITTING_FULL_LIMIT = 12
STALE_PIN_AGE = timedelta(hours=2)
DUPLICATE_LANE_WINDOW = timedelta(minutes=90)
SINCE_SPOKE_SLACK_SECONDS = 90
LATER_INTERVENING_SLACK = timedelta(seconds=5)

COMPLETED_SEND_FRAGMENTS = (
    "于是把照片发了过去",
    "把照片发了过去",
    "已经发给他了",
    "照片已经挑好发给他了",
    "照片发过去了",
    "照片已经发过去",
    "照片昨晚已经发给他了",
    "已经发出去了",
    "发出去了",
    "照片已经发出去",
    "照片昨晚发出去了",
    "发过去之后",
    "发给他了",
    "已经发给他",
    "已经发过去",
    "也真的发了",
    "也真的做到了",
    "照片已经挑好发给",
)

DENIAL_FRAGMENTS = (
    "还没打开",
    "还没翻",
    "晚点再",
    "明天再给",
    "明天白天我再给",
    "还没发",
    "照片还没整理",
    "等整理好再说",
    "晚上整理好发你",
)

FALSE_RETURN_FRAGMENTS = (
    "他倒完水回来",
    "倒完水回来",
    "水喝上了",
    "他回来了还不",
    "他已经回来",
)

LEAVE_FRAGMENTS = (
    "等我一下",
    "我去倒",
    "倒杯水",
    "接个电话",
    "我去上个",
    "有人敲门",
    "我去拿个",
    "电梯到了",
    "我先睡了",
)

GREETING_MOTIFS = ("下雨", "早～", "早上好", "早啊")

SEVERITY_ORDER = ("critical", "high", "medium", "low", "info")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def as_dt(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def iso(value: object) -> str | None:
    parsed = as_dt(value)
    return parsed.isoformat() if parsed is not None else None


def local_iso(value: object) -> str | None:
    parsed = as_dt(value)
    if parsed is None:
        return None
    return parsed.astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S")


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
        elif isinstance(payload_json, dict):
            event["_payload"] = payload_json
        else:
            event["_payload"] = {}
    return event


def payload_of(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("_payload")
    return payload if isinstance(payload, dict) else {}


def nested(mapping: MappingLike, *keys: str) -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


MappingLike = dict[str, Any]


def looks_like_prose(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 8 or len(stripped) > 4000:
        return False
    if stripped.startswith(("event:", "sha256:", "payload:", "dialogue:", "content:")):
        return False
    if re.fullmatch(r"[0-9a-f]{32,}", stripped):
        return False
    return any("\u4e00" <= char <= "\u9fff" for char in stripped) or " " in stripped


def walk_prose(obj: object, key: str, out: list[tuple[str, str]], *, depth: int = 0) -> None:
    if depth > 8:
        return
    if isinstance(obj, str):
        if looks_like_prose(obj):
            out.append((key, obj.strip()))
        return
    if isinstance(obj, dict):
        for child_key, value in obj.items():
            if not isinstance(child_key, str):
                continue
            if child_key.endswith(("_ref", "_hash", "_id", "_ids", "_refs")):
                continue
            walk_prose(value, f"{key}.{child_key}", out, depth=depth + 1)
        return
    if isinstance(obj, list):
        for item in obj[:24]:
            walk_prose(item, key, out, depth=depth + 1)


def clip(text: str, limit: int = 180) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def norm_text(text: str) -> str:
    return " ".join(text.replace("\n", " ").split())


def text_keys(text: str) -> set[str]:
    keys = {norm_text(text)}
    for line in text.splitlines():
        line_key = norm_text(line)
        if line_key:
            keys.add(line_key)
    keys.discard("")
    return keys


def texts_match(left: str, right: str) -> bool:
    left_keys = text_keys(left)
    right_keys = text_keys(right)
    if left_keys & right_keys:
        return True
    for a in left_keys:
        for b in right_keys:
            if len(a) < 4 or len(b) < 4:
                continue
            if a in b or b in a:
                return True
    return False


def first_fragment(text: str, fragments: Iterable[str]) -> str | None:
    for fragment in fragments:
        if fragment and fragment in text:
            return fragment
    return None


def open_ro(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def assert_output_is_safe(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    data = (REPO / "data").resolve()
    try:
        resolved.relative_to(data)
    except ValueError:
        return resolved
    raise SystemExit(f"refusing to write under data/: {resolved}")


# ---------------------------------------------------------------------------
# Domain records
# ---------------------------------------------------------------------------


@dataclass
class Utterance:
    seq: int
    world_revision: int
    logical_time: datetime
    speaker: str
    text: str
    event_id: str
    observation_id: str | None = None
    payload_ref: str | None = None
    action_id: str | None = None
    action_kind: str | None = None
    not_before: datetime | None = None
    visible_at: datetime | None = None
    delivered: bool = False
    is_reaction: bool = False


@dataclass
class ActionRow:
    seq: int
    action_id: str
    kind: str
    payload_ref: str | None
    not_before: datetime | None
    created_at: datetime | None
    logical_time: datetime | None
    text: str | None = None
    provider_accepted_at: datetime | None = None
    provider_accepted_seq: int | None = None
    delivered_at: datetime | None = None
    delivered_seq: int | None = None
    stored_seq: int | None = None


@dataclass
class Hope:
    seq: int
    world_revision: int
    logical_time: datetime
    hoped_response: str
    wait_seconds: int | None = None
    expires_at: datetime | None = None


@dataclass
class ProcessOpen:
    seq: int
    logical_time: datetime
    process_kind: str
    trigger_id: str
    trigger_ref: str
    source_evidence_ref: str
    lane: str


@dataclass
class SeenTurn:
    inner_turn_id: str
    purpose: str
    phase: str
    trigger_ref: str
    updated_at: datetime | None
    cursor_seq: int
    world_revision: int
    logical_time: datetime | None
    snapshot_id: str | None
    compiler: str | None
    materials: dict[str, Any]
    recent_dialogue: list[dict[str, Any]]
    inner_summary: str
    impulse_summary: str
    brief_rationale: str
    beat_texts: list[str]
    timing_choice: str | None
    lane: str
    process: ProcessOpen | None = None


@dataclass
class Finding:
    severity: str
    invariant: str
    code: str
    title: str
    ledger: str
    seen: str
    detail: str
    turn_id: str | None = None
    purpose: str | None = None
    lane: str | None = None
    cursor_seq: int | None = None
    logical_local: str | None = None
    group: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Ledger load
# ---------------------------------------------------------------------------


class Ledger:
    def __init__(self, conn: sqlite3.Connection, world_id: str) -> None:
        self.world_id = world_id
        self.events: list[dict[str, Any]] = []
        self.by_seq: dict[int, dict[str, Any]] = {}
        self.observations: list[Utterance] = []
        self.her_messages: list[Utterance] = []
        self.actions: dict[str, ActionRow] = {}
        self.actions_by_payload: dict[str, ActionRow] = {}
        self.hopes: list[Hope] = []
        self.processes: list[ProcessOpen] = []
        self.appraisal_expired_ids: dict[str, int] = {}
        self.affect_decayed: dict[str, int] = {}
        self.media_shared_seqs: list[int] = []
        self.photo_candidates: list[int] = []
        self.life_content: dict[str, str] = {}
        self.usage: list[dict[str, Any]] = []
        self.notices: list[dict[str, Any]] = []
        self.model_failures: list[dict[str, Any]] = []
        self.invalid_role: list[dict[str, Any]] = []
        self.technical_failures: list[dict[str, Any]] = []
        self.head_appraisals: list[dict[str, Any]] = []
        self.head_affect: list[dict[str, Any]] = []
        self.head_impressions: list[dict[str, Any]] = []
        self.head_threads: list[dict[str, Any]] = []
        self.head_media_deliveries: int = 0
        self.head_logical_time: datetime | None = None
        self.max_seq = 0
        self._load(conn)

    def _load(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            "SELECT ledger_sequence, world_revision, deliberation_revision, event_json "
            "FROM world_v2_events WHERE world_id = ? ORDER BY ledger_sequence",
            (self.world_id,),
        ).fetchall()
        stored_by_ref: dict[str, tuple[int, str, datetime | None]] = {}
        for row in rows:
            event = parse_event(row["event_json"])
            seq = int(row["ledger_sequence"])
            wr = int(row["world_revision"] or 0)
            event["_seq"] = seq
            event["_wr"] = wr
            event["_type"] = str(event.get("event_type") or "")
            event["_lt"] = as_dt(event.get("logical_time"))
            self.events.append(event)
            self.by_seq[seq] = event
            self.max_seq = seq
            self._ingest_event(event, stored_by_ref)
        self._bind_message_visibility(stored_by_ref)
        self.head_logical_time = self.events[-1]["_lt"] if self.events else None
        self._load_sidecars(conn)

    def _ingest_event(
        self, event: dict[str, Any], stored_by_ref: dict[str, tuple[int, str, datetime | None]]
    ) -> None:
        kind = event["_type"]
        payload = payload_of(event)
        seq = event["_seq"]
        wr = event["_wr"]
        lt = event["_lt"] or datetime.min.replace(tzinfo=UTC)
        if kind == "ObservationRecorded":
            text = payload.get("text") if isinstance(payload.get("text"), str) else ""
            meta = payload.get("coalescing_metadata") if isinstance(payload.get("coalescing_metadata"), dict) else {}
            reactions = meta.get("reaction_refs") if isinstance(meta.get("reaction_refs"), list) else []
            is_reaction = (not text.strip()) and bool(reactions)
            if is_reaction:
                text = "[reaction " + ", ".join(str(item) for item in reactions[:4]) + "]"
            if text.strip() or is_reaction:
                self.observations.append(
                    Utterance(
                        seq=seq,
                        world_revision=wr,
                        logical_time=lt,
                        speaker="counterpart",
                        text=text.strip(),
                        event_id=str(event.get("event_id") or ""),
                        observation_id=str(payload.get("observation_id") or "") or None,
                        is_reaction=is_reaction,
                    )
                )
            return
        if kind == "MessagePayloadStored":
            message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
            text = message.get("text") if isinstance(message.get("text"), str) else ""
            payload_ref = message.get("payload_ref") if isinstance(message.get("payload_ref"), str) else None
            if payload_ref and text.strip():
                stored_by_ref[payload_ref] = (seq, text.strip(), lt)
            return
        if kind == "ActionAuthorized":
            action = payload.get("action") if isinstance(payload.get("action"), dict) else {}
            action_id = str(action.get("action_id") or "")
            if not action_id:
                return
            payload_ref = action.get("payload_ref") if isinstance(action.get("payload_ref"), str) else None
            row = ActionRow(
                seq=seq,
                action_id=action_id,
                kind=str(action.get("kind") or ""),
                payload_ref=payload_ref,
                not_before=as_dt(action.get("not_before")),
                created_at=as_dt(action.get("created_at")),
                logical_time=as_dt(action.get("logical_time")) or lt,
            )
            self.actions[action_id] = row
            if payload_ref:
                self.actions_by_payload[payload_ref] = row
            return
        if kind == "ActionProviderAccepted":
            action_id = str(payload.get("action_id") or "")
            row = self.actions.get(action_id)
            if row is not None:
                row.provider_accepted_at = as_dt(payload.get("logical_time") or payload.get("created_at") or event.get("logical_time"))
                row.provider_accepted_seq = seq
            return
        if kind == "ActionDelivered":
            action_id = str(payload.get("action_id") or "")
            row = self.actions.get(action_id)
            if row is not None:
                row.delivered_at = as_dt(payload.get("logical_time") or payload.get("observed_at") or event.get("logical_time"))
                row.delivered_seq = seq
            return
        if kind == "MediaDeliveryShared":
            self.media_shared_seqs.append(seq)
            return
        if kind == "PhotoCandidateOpened":
            self.photo_candidates.append(seq)
            return
        if kind == "AppraisalExpired":
            appraisal_id = str(payload.get("appraisal_id") or "")
            if appraisal_id:
                self.appraisal_expired_ids[appraisal_id] = seq
            return
        if kind == "AffectEpisodeDecayed":
            episode_id = str(payload.get("episode_id") or nested(payload, "episode", "episode_id") or "")
            if episode_id:
                self.affect_decayed[episode_id] = seq
            return
        if kind == "TriggerProcessOpened":
            process = payload.get("process") if isinstance(payload.get("process"), dict) else payload
            trigger_ref = str(process.get("trigger_ref") or "")
            source = str(process.get("source_evidence_ref") or "")
            self.processes.append(
                ProcessOpen(
                    seq=seq,
                    logical_time=lt,
                    process_kind=str(process.get("process_kind") or ""),
                    trigger_id=str(process.get("trigger_id") or ""),
                    trigger_ref=trigger_ref,
                    source_evidence_ref=source,
                    lane=_lane_from_trigger(trigger_ref, source),
                )
            )
            return
        if kind == "AcceptanceRecorded":
            blob = json.dumps(payload, ensure_ascii=False)
            if "hoped_response" not in blob:
                return
            hope = _extract_hope(payload, seq=seq, world_revision=wr, logical_time=lt)
            if hope is not None:
                self.hopes.append(hope)
            return
        if kind == "ModelResultRecorded":
            failure = _failure_from_model_result(payload)
            if failure is not None:
                failure["seq"] = seq
                failure["logical_time"] = iso(lt)
                self.model_failures.append(failure)
                code = str(failure.get("failure_code") or "")
                if "invalid_role" in code:
                    self.invalid_role.append(failure)
                if "technical_failure" in code or code.endswith("technical_failure"):
                    self.technical_failures.append(failure)
            return
        if kind == "TriggerProcessCompleted":
            blob = json.dumps(payload, ensure_ascii=False)
            if "technical_failure" in blob or "invalid_role_result" in blob:
                status = nested(payload, "character_interior_model_result", "status")
                code = nested(payload, "character_interior_model_result", "failure_code")
                if not isinstance(code, str):
                    audit = nested(payload, "character_interior_model_result", "audit_json")
                    if isinstance(audit, str):
                        try:
                            decoded = json.loads(audit)
                        except json.JSONDecodeError:
                            decoded = {}
                        code = decoded.get("failure_code") if isinstance(decoded, dict) else None
                self.technical_failures.append(
                    {
                        "seq": seq,
                        "status": status,
                        "failure_code": code,
                        "logical_time": iso(lt),
                    }
                )

    def _bind_message_visibility(
        self, stored_by_ref: dict[str, tuple[int, str, datetime | None]]
    ) -> None:
        for payload_ref, (seq, text, lt) in stored_by_ref.items():
            action = self.actions_by_payload.get(payload_ref)
            if action is not None:
                action.text = text
                action.stored_seq = seq
            visible = None
            delivered = False
            if action is not None:
                visible = action.provider_accepted_at or action.delivered_at
                delivered = action.delivered_seq is not None or action.provider_accepted_seq is not None
            self.her_messages.append(
                Utterance(
                    seq=seq,
                    world_revision=action.seq if action is not None else 0,
                    logical_time=lt or datetime.min.replace(tzinfo=UTC),
                    speaker="companion",
                    text=text,
                    event_id=payload_ref,
                    payload_ref=payload_ref,
                    action_id=action.action_id if action is not None else None,
                    action_kind=action.kind if action is not None else None,
                    not_before=action.not_before if action is not None else None,
                    visible_at=visible,
                    delivered=delivered,
                )
            )
        self.her_messages.sort(key=lambda item: (item.seq, item.logical_time))

    def _load_sidecars(self, conn: sqlite3.Connection) -> None:
        try:
            for row in conn.execute(
                "SELECT content_ref, text FROM world_v2_life_content WHERE world_id = ?",
                (self.world_id,),
            ):
                text = row["text"]
                if isinstance(text, str) and text.strip():
                    self.life_content[str(row["content_ref"])] = text
        except sqlite3.OperationalError:
            pass
        try:
            columns = {info[1] for info in conn.execute("PRAGMA table_info(world_v2_model_usage)")}
            if "world_id" in columns:
                usage_rows = conn.execute(
                    "SELECT purpose, status, error, cost_cny, recorded_at FROM world_v2_model_usage "
                    "WHERE world_id = ?",
                    (self.world_id,),
                ).fetchall()
            else:
                usage_rows = conn.execute(
                    "SELECT purpose, status, error, cost_cny, recorded_at FROM world_v2_model_usage"
                ).fetchall()
            self.usage = [dict(row) for row in usage_rows]
        except sqlite3.OperationalError:
            self.usage = []
        try:
            self.notices = [
                dict(row)
                for row in conn.execute(
                    "SELECT notice_key, failure_code, attempted_at, status, error_class "
                    "FROM world_v2_system_notice_dispatch WHERE world_id = ?",
                    (self.world_id,),
                )
            ]
        except sqlite3.OperationalError:
            self.notices = []
        try:
            for field, target in (
                ("appraisals", self.head_appraisals),
                ("affect_episodes", self.head_affect),
                ("private_impressions", self.head_impressions),
                ("threads", self.head_threads),
            ):
                for row in conn.execute(
                    "SELECT item_json FROM world_v2_head_state_items "
                    "WHERE world_id = ? AND field = ?",
                    (self.world_id, field),
                ):
                    try:
                        item = json.loads(row["item_json"])
                    except json.JSONDecodeError:
                        continue
                    if isinstance(item, dict):
                        target.append(item)
            self.head_media_deliveries = int(
                conn.execute(
                    "SELECT COUNT(*) FROM world_v2_head_state_items "
                    "WHERE world_id = ? AND field = 'media_deliveries'",
                    (self.world_id,),
                ).fetchone()[0]
            )
        except sqlite3.OperationalError:
            pass

    def utterances_before(self, seq: int) -> list[Utterance]:
        items = [item for item in self.observations if item.seq <= seq]
        for item in self.her_messages:
            if item.seq <= seq and not _is_still_queued(item, seq, self):
                items.append(item)
        items.sort(key=lambda row: (row.logical_time, row.seq))
        return items

    def queued_later_at(self, seq: int, when: datetime | None) -> list[ActionRow]:
        queued: list[ActionRow] = []
        for action in self.actions.values():
            if action.kind != "followup":
                continue
            if action.seq > seq:
                continue
            if action.delivered_seq is not None and action.delivered_seq <= seq:
                continue
            if action.provider_accepted_seq is not None and action.provider_accepted_seq <= seq:
                continue
            if action.not_before is None:
                continue
            queued.append(action)
        return queued

    def media_delivery_count_before(self, seq: int) -> int:
        shared = sum(1 for item in self.media_shared_seqs if item <= seq)
        if shared:
            return shared
        count = 0
        for action in self.actions.values():
            if action.kind != "media_delivery":
                continue
            visible = action.delivered_seq or action.provider_accepted_seq
            if visible is not None and visible <= seq:
                count += 1
        return count

    def last_counterpart_before(self, seq: int) -> Utterance | None:
        for item in reversed(self.observations):
            if item.seq <= seq and not item.is_reaction:
                return item
        for item in reversed(self.observations):
            if item.seq <= seq:
                return item
        return None

    def process_for_turn(self, turn: SeenTurn) -> ProcessOpen | None:
        trigger = turn.trigger_ref
        matches = [
            item
            for item in self.processes
            if item.source_evidence_ref == trigger or item.trigger_ref == trigger
        ]
        if len(matches) == 1:
            return matches[0]
        if matches and turn.logical_time is not None:
            matches.sort(
                key=lambda item: abs((item.logical_time - turn.logical_time).total_seconds())
            )
            return matches[0]
        if turn.logical_time is None:
            return matches[-1] if matches else None
        nearby = [
            item
            for item in self.processes
            if abs((item.logical_time - turn.logical_time).total_seconds()) <= 3
        ]
        return nearby[-1] if nearby else None


def _is_still_queued(item: Utterance, seq: int, ledger: Ledger) -> bool:
    if item.action_kind != "followup" or item.not_before is None:
        return False
    action = ledger.actions.get(item.action_id or "")
    if action is None:
        return True
    visible = action.provider_accepted_seq or action.delivered_seq
    return visible is None or visible > seq


def _lane_from_trigger(trigger_ref: str, source: str) -> str:
    blob = f"{trigger_ref} {source}"
    if "expectation-expiry" in blob or "expired_expectation" in blob:
        return "expired_expectation"
    if "later-refresh" in blob or "later_expression" in blob:
        return "later_refresh"
    if "private-impression" in blob or "private_impression" in blob:
        return "private_impression"
    if "post-silent" in blob or "post_silent" in blob:
        return "post_silent"
    if "social-initiative" in blob:
        return "spontaneous_contact"
    if "proactive" in blob:
        return "proactive"
    return "other"


def _extract_hope(payload: dict[str, Any], *, seq: int, world_revision: int, logical_time: datetime) -> Hope | None:
    found: list[dict[str, Any]] = []

    def hunt(obj: object) -> None:
        if isinstance(obj, dict):
            hoped = obj.get("hoped_response")
            if isinstance(hoped, str) and hoped.strip():
                found.append(obj)
            for value in obj.values():
                hunt(value)
        elif isinstance(obj, list):
            for item in obj:
                hunt(item)

    hunt(payload)
    if not found:
        return None
    body = found[-1]
    return Hope(
        seq=seq,
        world_revision=world_revision,
        logical_time=logical_time,
        hoped_response=str(body.get("hoped_response") or "").strip(),
        wait_seconds=body.get("wait_seconds") if isinstance(body.get("wait_seconds"), int) else None,
        expires_at=as_dt(body.get("expires_at")),
    )


def _failure_from_model_result(payload: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [payload]
    for key in ("result", "model_result", "audit"):
        value = payload.get(key)
        if isinstance(value, dict):
            candidates.append(value)
        elif isinstance(value, str):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict):
                candidates.append(decoded)
    for item in candidates:
        code = item.get("failure_code")
        status = item.get("status")
        if isinstance(code, str) and code.strip():
            return {"failure_code": code, "status": status, "purpose": item.get("purpose")}
        if isinstance(status, str) and status in {"technical_failure", "invalid_role_result"}:
            return {"failure_code": status, "status": status, "purpose": item.get("purpose")}
    blob = json.dumps(payload, ensure_ascii=False)
    if "invalid_role_result" in blob:
        return {"failure_code": "invalid_role_result", "status": None}
    return None


# ---------------------------------------------------------------------------
# Turns
# ---------------------------------------------------------------------------


def _load_json(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _snapshot_materials(authored: dict[str, Any]) -> dict[str, Any]:
    snapshot = authored.get("snapshot")
    if not isinstance(snapshot, dict):
        return {}
    materials = snapshot.get("materials")
    if isinstance(materials, dict):
        return materials
    blob = snapshot.get("materials_json")
    if isinstance(blob, str) and blob:
        try:
            decoded = json.loads(blob)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _decision_payload(blob: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(blob, dict):
        return {}
    for root in (blob.get("result"), blob.get("decision"), blob):
        if not isinstance(root, dict):
            continue
        decision = root.get("decision") if isinstance(root.get("decision"), dict) else root
        payload = decision.get("payload") if isinstance(decision.get("payload"), dict) else None
        if isinstance(payload, dict) and (
            "beats" in payload or "timing_choice" in payload or "impulse_summary" in payload
        ):
            return payload
        if "impulse_summary" in decision or "timing_choice" in decision:
            return decision
    return {}


def _beat_texts(payload: dict[str, Any]) -> list[str]:
    beats = payload.get("beats")
    if not isinstance(beats, list):
        return []
    texts: list[str] = []
    for beat in beats:
        if not isinstance(beat, dict):
            continue
        text = beat.get("text")
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
    return texts


def _inner_summary(authored: dict[str, Any], terminal: dict[str, Any] | None) -> str:
    result = authored.get("result") if isinstance(authored.get("result"), dict) else {}
    summary = result.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    lineage = authored.get("private_self_lineage") if isinstance(authored.get("private_self_lineage"), dict) else {}
    for key in ("final_private_self", "initial_private_self"):
        private = lineage.get(key)
        if isinstance(private, dict):
            text = private.get("inner_state_summary") or private.get("summary")
            if isinstance(text, str) and text.strip():
                return text.strip()
    if isinstance(terminal, dict):
        text = terminal.get("summary") or terminal.get("inner_state_summary")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


def load_turns(conn: sqlite3.Connection, world_id: str, ledger: Ledger) -> list[SeenTurn]:
    rows = conn.execute(
        """
        SELECT inner_turn_id, purpose, phase, trigger_ref, cursor_json,
               snapshot_id, authored_state_json, terminal_result_json, updated_at
        FROM world_v2_character_interior_turns
        WHERE world_id = ? AND authored_state_json IS NOT NULL
        ORDER BY updated_at
        """,
        (world_id,),
    ).fetchall()
    turns: list[SeenTurn] = []
    for row in rows:
        authored = _load_json(row["authored_state_json"])
        if authored is None:
            continue
        terminal = _load_json(row["terminal_result_json"])
        cursor = _load_json(row["cursor_json"]) or {}
        materials = _snapshot_materials(authored)
        snapshot = authored.get("snapshot") if isinstance(authored.get("snapshot"), dict) else {}
        compiler = None
        binding = snapshot.get("snapshot_compiler")
        if isinstance(binding, dict):
            compiler = str(binding.get("value") or binding.get("version") or "")
        elif isinstance(binding, str):
            compiler = binding
        logical_time = as_dt(materials.get("logical_time") or snapshot.get("logical_time"))
        payload = _decision_payload(authored) or _decision_payload(terminal)
        dialogue = materials.get("recent_dialogue")
        dialogue = dialogue if isinstance(dialogue, list) else []
        turn = SeenTurn(
            inner_turn_id=str(row["inner_turn_id"]),
            purpose=str(row["purpose"]),
            phase=str(row["phase"]),
            trigger_ref=str(row["trigger_ref"] or ""),
            updated_at=as_dt(row["updated_at"]),
            cursor_seq=int(cursor.get("ledger_sequence") or 0),
            world_revision=int(cursor.get("world_revision") or 0),
            logical_time=logical_time,
            snapshot_id=str(row["snapshot_id"] or snapshot.get("snapshot_id") or "") or None,
            compiler=compiler,
            materials=materials,
            recent_dialogue=[item for item in dialogue if isinstance(item, dict)],
            inner_summary=_inner_summary(authored, terminal),
            impulse_summary=str(payload.get("impulse_summary") or "").strip(),
            brief_rationale=str(payload.get("brief_rationale") or "").strip(),
            beat_texts=_beat_texts(payload),
            timing_choice=str(payload.get("timing_choice") or "") or None,
            lane=row["purpose"],
        )
        process = ledger.process_for_turn(turn)
        turn.process = process
        if turn.purpose == "inbound_turn":
            turn.lane = "inbound"
        elif process is not None:
            turn.lane = process.lane
        elif turn.purpose == "proactive_contact":
            turn.lane = _lane_from_trigger(turn.trigger_ref, turn.trigger_ref)
        turns.append(turn)
    return turns


def seen_dialogue_lines(turn: SeenTurn) -> list[dict[str, str]]:
    lines: list[dict[str, str]] = []
    for item in turn.recent_dialogue:
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        speaker = str(item.get("speaker") or "?")
        lines.append(
            {
                "speaker": speaker,
                "text": text.strip(),
                "occurred_at": str(item.get("occurred_at") or ""),
                "reasons": ",".join(str(reason) for reason in (item.get("continuity_reasons") or [])),
            }
        )
    return lines


def render_seen_conversation(turn: SeenTurn) -> str:
    lines = seen_dialogue_lines(turn)
    if not lines:
        return "（对话栏空）"
    return " / ".join(
        f"{'他' if item['speaker']=='counterpart' else '我' if item['speaker']=='companion' else item['speaker']}：{clip(item['text'], 40)}"
        for item in lines
    )


# ---------------------------------------------------------------------------
# Checkers
# ---------------------------------------------------------------------------


def _live_head(utterances: list[Utterance], *, now: datetime | None) -> list[Utterance]:
    counterpart = [
        item for item in utterances if item.speaker == "counterpart" and not item.is_reaction
    ]
    companion = [item for item in utterances if item.speaker == "companion"]
    required = counterpart[-LIVE_HEAD_EACH:] + companion[-LIVE_HEAD_EACH:]
    if now is not None:
        sitting = [
            item
            for item in utterances
            if item.logical_time >= now - SITTING_WINDOW and not item.is_reaction
        ]
        if len(sitting) <= SITTING_FULL_LIMIT:
            required = sitting if sitting else required
    deduped: dict[tuple[str, int], Utterance] = {}
    for item in required:
        deduped[(item.speaker, item.seq)] = item
    return sorted(deduped.values(), key=lambda item: (item.logical_time, item.seq))


def _matched(seen: list[dict[str, str]], utterance: Utterance) -> dict[str, str] | None:
    for item in seen:
        if texts_match(item["text"], utterance.text):
            expected = "counterpart" if utterance.speaker == "counterpart" else "companion"
            if item["speaker"] == expected or item["speaker"] in {"他", "我"}:
                return item
            return item
    return None


def check_conversation(turn: SeenTurn, ledger: Ledger) -> list[Finding]:
    if turn.cursor_seq <= 0:
        return []
    findings: list[Finding] = []
    seen = seen_dialogue_lines(turn)
    ledger_rows = ledger.utterances_before(turn.cursor_seq)
    now = turn.logical_time
    head = _live_head(ledger_rows, now=now)
    missing = [item for item in head if _matched(seen, item) is None]
    her_missing = [item for item in missing if item.speaker == "companion"]
    his_missing = [item for item in missing if item.speaker == "counterpart"]
    if her_missing:
        last = her_missing[-1]
        findings.append(
            Finding(
                severity="high",
                invariant="own_speech",
                code="missing_own_recent_speech",
                title="她刚说过的话不在下一轮视野里",
                ledger=f"cursor≤{turn.cursor_seq} 她最近说过：{clip(last.text)}（seq {last.seq} {local_iso(last.logical_time)}）",
                seen=render_seen_conversation(turn),
                detail=(
                    f"近 {int(SITTING_WINDOW.total_seconds()//3600)} 小时对话头里有 {len(her_missing)} 句她自己的话 "
                    "没进 recent_dialogue。她会以为那几句没发生过。"
                ),
                group="missing_own_recent_speech",
                evidence={"missing": [clip(item.text, 80) for item in her_missing[-4:]]},
            )
        )
    if his_missing:
        last = his_missing[-1]
        findings.append(
            Finding(
                severity="high",
                invariant="conversation",
                code="missing_counterpart_recent_speech",
                title="他刚说过的话不在她的对话栏里",
                ledger=f"他最近说过：{clip(last.text)}（seq {last.seq} {local_iso(last.logical_time)}）",
                seen=render_seen_conversation(turn),
                detail=(
                    f"近窗对话头里有 {len(his_missing)} 句对方的话没进切片。"
                    "她会按更早的一句继续聊。"
                ),
                group="missing_counterpart_recent_speech",
                evidence={"missing": [clip(item.text, 80) for item in his_missing[-4:]]},
            )
        )
    stale_pins: list[dict[str, str]] = []
    if now is not None:
        cutoff = now - STALE_PIN_AGE
        for item in seen:
            reasons = item.get("reasons") or ""
            occurred = as_dt(item.get("occurred_at"))
            pinned = "current_turn" in reasons or "pending_interaction" in reasons
            if not pinned or occurred is None:
                continue
            if occurred <= cutoff and missing:
                stale_pins.append(item)
        latest_his = ledger.last_counterpart_before(turn.cursor_seq)
        for item in seen:
            if "current_turn" not in (item.get("reasons") or ""):
                continue
            if latest_his is None:
                continue
            if not texts_match(item["text"], latest_his.text):
                findings.append(
                    Finding(
                        severity="medium",
                        invariant="conversation",
                        code="stale_current_turn",
                        title="current_turn 钉在过时的对方句上",
                        ledger=f"此刻最新对方句是「{clip(latest_his.text)}」（seq {latest_his.seq}）",
                        seen=f"切片把 current_turn 标在「{clip(item['text'])}」",
                        detail="永久钉住的 current_turn 不是账本上这一刻之前的最后一句。",
                        group="stale_current_turn",
                    )
                )
    if stale_pins:
        findings.append(
            Finding(
                severity="medium",
                invariant="conversation",
                code="old_pin_crowding_live_head",
                title="旧句占着对话栏，把刚发生的话挤掉了",
                ledger=f"近窗缺 {len(missing)} 句；账本近窗头有 {len(head)} 句",
                seen="旧钉：" + " / ".join(clip(item["text"], 36) for item in stale_pins[:3]),
                detail="切片里仍留着超过两小时、且带 current_turn/pending_interaction 的旧句，同时近窗真实对话缺失。",
                group="old_pin_crowding_live_head",
            )
        )
    ledger_texts = [item for item in ledger_rows if not item.is_reaction]
    for item in seen:
        if item["text"].startswith("[") and item["text"].endswith("]"):
            continue
        if any(texts_match(item["text"], row.text) for row in ledger_texts):
            continue
        findings.append(
            Finding(
                severity="high",
                invariant="conversation",
                code="phantom_dialogue_line",
                title="对话栏出现了账本里没有的话",
                ledger=f"cursor≤{turn.cursor_seq} 的 Observation/MessagePayload 对不上这句",
                seen=f"{item['speaker']}：{clip(item['text'])}",
                detail="切片里有一句在该时刻之前的真实对话里找不到。",
                group="phantom_dialogue_line",
            )
        )
    by_text: dict[str, Utterance] = {}
    for row in ledger_texts:
        by_text[norm_text(row.text)] = row
    for item in seen:
        matched = next((row for row in ledger_texts if texts_match(item["text"], row.text)), None)
        if matched is None:
            continue
        expected = "counterpart" if matched.speaker == "counterpart" else "companion"
        if item["speaker"] != expected:
            findings.append(
                Finding(
                    severity="high",
                    invariant="conversation",
                    code="speaker_mismatch",
                    title="对话栏把说话人标错了",
                    ledger=f"账本 speaker={matched.speaker}：{clip(matched.text)}",
                    seen=f"切片 speaker={item['speaker']}：{clip(item['text'])}",
                    detail="同一句话的说话人标签和账本不一致。",
                    group="speaker_mismatch",
                )
            )
    matched_pairs: list[tuple[int, int]] = []
    for index, item in enumerate(seen):
        matched = next((row for row in ledger_texts if texts_match(item["text"], row.text)), None)
        if matched is not None:
            matched_pairs.append((index, matched.seq))
    seen_order = [seq for _, seq in matched_pairs]
    if seen_order != sorted(seen_order):
        findings.append(
            Finding(
                severity="medium",
                invariant="conversation",
                code="dialogue_order_mismatch",
                title="对话栏顺序和账本时间线不一致",
                ledger="账本按 logical_time/seq 排列",
                seen=render_seen_conversation(turn),
                detail="她看见的相对顺序不是真实发生顺序。",
                group="dialogue_order_mismatch",
            )
        )
    for finding in findings:
        _stamp(finding, turn)
    return findings


def check_forged_actions(turn: SeenTurn, ledger: Ledger) -> list[Finding]:
    deliveries = ledger.media_delivery_count_before(turn.cursor_seq)
    texts = _material_prose(turn, ledger)
    findings: list[Finding] = []
    claimed: list[tuple[str, str, str]] = []
    for source, text in texts:
        fragment = first_fragment(text, COMPLETED_SEND_FRAGMENTS)
        if fragment is None:
            continue
        claimed.append((source, fragment, text))
    if claimed and deliveries == 0:
        sources = sorted({item[0] for item in claimed})
        sample = claimed[0]
        severity = "critical" if any(
            item[0].startswith(("week_diary", "recent_self_experiences", "remembered_material", "private_impressions"))
            for item in claimed
        ) else "high"
        findings.append(
            Finding(
                severity=severity,
                invariant="forged_action",
                code="completed_send_without_delivery",
                title="材料把「已经发给他」写成了既成事实，账本上从未投递",
                ledger=(
                    f"cursor≤{turn.cursor_seq}：MediaDeliveryShared=0，"
                    "ActionAuthorized kind=media_delivery=0，"
                    f"PhotoCandidateOpened={sum(1 for seq in ledger.photo_candidates if seq <= turn.cursor_seq)}"
                ),
                seen=f"{sample[0]} 含「{sample[1]}」：{clip(sample[2])}",
                detail=(
                    "生活散文/记忆/印象把对外发送写成已经发生，但没有对应的 "
                    "ActionAuthorized + ActionDelivered/回执。这是伪造世界事实。"
                ),
                group="completed_send_without_delivery",
                evidence={"sources": sources, "fragments": sorted({item[1] for item in claimed})},
            )
        )
    moments = turn.materials.get("moments_i_can_share")
    already = 0
    if isinstance(moments, dict) and isinstance(moments.get("already_sent_count"), int):
        already = moments["already_sent_count"]
    photos = turn.materials.get("photos_i_shared")
    photo_n = len(photos) if isinstance(photos, list) else 0
    if already != deliveries or photo_n != deliveries:
        if deliveries or already or photo_n:
            findings.append(
                Finding(
                    severity="high",
                    invariant="media",
                    code="photos_shared_mismatch",
                    title="photos_i_shared / already_sent_count 对不上真实投递",
                    ledger=f"该时刻已投递 {deliveries} 张（MediaDeliveryShared 或 media_delivery Action）",
                    seen=f"photos_i_shared={photo_n}，already_sent_count={already}",
                    detail="她看见的已分享库存不是账本上的投递记录。",
                    group="photos_shared_mismatch",
                )
            )
    if deliveries > 0:
        conversation_has_photo = any(
            item.get("text", "").startswith("[") and item.get("text", "").endswith("]")
            for item in turn.recent_dialogue
        )
        if photo_n == 0 and not conversation_has_photo:
            findings.append(
                Finding(
                    severity="high",
                    invariant="media",
                    code="delivered_photo_invisible_in_conversation",
                    title="照片已经投递，对话栏里却看不见那张图",
                    ledger=f"该时刻已投递 {deliveries} 张",
                    seen=f"photos_i_shared 缺省；对话栏：{render_seen_conversation(turn)}",
                    detail="她无法从对话记录确认自己刚发出去的图，只能靠一个数字或干脆以为没发。",
                    group="delivered_photo_invisible_in_conversation",
                )
            )
        denial_source = None
        denial_fragment = None
        for source, text in texts:
            fragment = first_fragment(text, DENIAL_FRAGMENTS)
            if fragment:
                denial_source, denial_fragment = source, fragment
                break
        if denial_source and (photo_n > 0 or already > 0):
            findings.append(
                Finding(
                    severity="high",
                    invariant="media",
                    code="denies_visible_delivery",
                    title="材料里已经有投递事实，她仍按「还没发」在说",
                    ledger=f"已投递 {deliveries} 张；already_sent_count={already}",
                    seen=f"{denial_source} 含「{denial_fragment}」",
                    detail="可见的 photos_i_shared/already_sent_count 与口头「还没发」同时存在。",
                    group="denies_visible_delivery",
                )
            )
    for finding in findings:
        _stamp(finding, turn)
    return findings


def _material_prose(turn: SeenTurn, ledger: Ledger) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    materials = turn.materials
    for key in (
        "week_diary",
        "remembered_material",
        "recent_self_experiences",
        "private_impressions",
        "lived_moment",
        "advisories",
        "situation",
    ):
        walk_prose(materials.get(key), key, out)
    experiences = materials.get("recent_self_experiences")
    items = []
    if isinstance(experiences, dict):
        raw_items = experiences.get("items")
        if isinstance(raw_items, list):
            items = raw_items
    elif isinstance(experiences, list):
        items = experiences
    for item in items:
        if not isinstance(item, dict):
            continue
        content = item.get("content") if isinstance(item.get("content"), dict) else {}
        ref = content.get("content_ref") if isinstance(content, dict) else None
        if isinstance(ref, str) and ref in ledger.life_content:
            out.append(("recent_self_experiences.life_content", ledger.life_content[ref]))
    if turn.inner_summary:
        out.append(("inner_state_summary", turn.inner_summary))
    if turn.impulse_summary:
        out.append(("impulse_summary", turn.impulse_summary))
    if turn.brief_rationale:
        out.append(("brief_rationale", turn.brief_rationale))
    for index, text in enumerate(turn.beat_texts):
        out.append((f"beats[{index}]", text))
    return out


def check_lifecycle(turn: SeenTurn, ledger: Ledger) -> list[Finding]:
    findings: list[Finding] = []
    now = turn.logical_time
    if now is None:
        return findings
    overdue_appraisals: list[str] = []
    for item in turn.materials.get("appraisals") or []:
        if not isinstance(item, dict):
            continue
        expires = as_dt(item.get("expires_at"))
        status = item.get("status")
        if expires is None or expires > now:
            continue
        if status not in {None, "active"}:
            continue
        meanings = []
        for hypothesis in item.get("hypotheses") or []:
            if isinstance(hypothesis, dict) and isinstance(hypothesis.get("meaning"), str):
                meanings.append(hypothesis["meaning"])
        overdue_appraisals.append(clip(meanings[0] if meanings else str(item.get("source_ref")), 60))
    if overdue_appraisals:
        findings.append(
            Finding(
                severity="high",
                invariant="lifecycle",
                code="overdue_appraisal_still_in_attention",
                title="已过自己 expires_at 的读法仍出现在注意力里",
                ledger=f"逻辑时钟 {local_iso(now)}；这些读法的 expires_at 已过",
                seen=f"{len(overdue_appraisals)} 条仍在 appraisals 切片，例如：{overdue_appraisals[0]}",
                detail="胶囊只应收 active 且未到期的读法。过期仍排在最前，就是翻旧账。",
                group="overdue_appraisal_still_in_attention",
                evidence={"meanings": overdue_appraisals[:6]},
            )
        )
    overdue_affect: list[str] = []
    for item in turn.materials.get("affect") or []:
        if not isinstance(item, dict):
            continue
        episode_id = str(item.get("episode_id") or "")
        status = item.get("status")
        decayed_seq = ledger.affect_decayed.get(episode_id)
        if decayed_seq is not None and decayed_seq <= turn.cursor_seq and status == "active":
            overdue_affect.append(episode_id[-12:])
        expires = as_dt(item.get("expires_at") or item.get("closed_at"))
        if expires is not None and expires <= now and status == "active":
            overdue_affect.append(episode_id[-12:] or "affect")
    if overdue_affect:
        findings.append(
            Finding(
                severity="medium",
                invariant="lifecycle",
                code="decayed_affect_still_active_in_attention",
                title="已收口的情绪仍以 active 出现在注意力里",
                ledger=f"AffectEpisodeDecayed 已在 cursor≤{turn.cursor_seq} 落账",
                seen=f"affect 切片仍 status=active：{overdue_affect[:4]}",
                detail="槽位离开 active 之后不应再当未了结工作集。",
                group="decayed_affect_still_active_in_attention",
            )
        )
    overdue_impressions: list[str] = []
    for item in turn.materials.get("private_impressions") or []:
        if not isinstance(item, dict):
            continue
        status = item.get("status") or "active"
        if status != "active":
            continue
        expiry = as_dt(item.get("expiry_condition") or item.get("expires_at"))
        if expiry is not None and expiry <= now:
            overdue_impressions.append(clip(str(item.get("reflection_summary") or item.get("source_ref")), 50))
    if overdue_impressions:
        findings.append(
            Finding(
                severity="medium",
                invariant="lifecycle",
                code="overdue_impression_still_active",
                title="已过声明到期的印象仍是 active",
                ledger=f"逻辑时钟 {local_iso(now)}",
                seen="；".join(overdue_impressions[:3]),
                detail="印象自己的到期已经过去，注意力仍把它当活心事。",
                group="overdue_impression_still_active",
            )
        )
    for finding in findings:
        _stamp(finding, turn)
    return findings


def check_advisory(turn: SeenTurn, ledger: Ledger) -> list[Finding]:
    findings: list[Finding] = []
    last_his = ledger.last_counterpart_before(turn.cursor_seq)
    actual_seconds = None
    if last_his is not None and turn.logical_time is not None:
        actual_seconds = int((turn.logical_time - last_his.logical_time).total_seconds())
    seen_last = None
    for item in reversed(turn.recent_dialogue):
        if item.get("speaker") == "counterpart" and isinstance(item.get("text"), str):
            seen_last = item
            break
    since = turn.materials.get("since_he_last_spoke")
    seen_seconds = None
    if isinstance(since, dict) and isinstance(since.get("seconds"), int):
        seen_seconds = since["seconds"]
    elif seen_last is not None and turn.logical_time is not None:
        occurred = as_dt(seen_last.get("occurred_at"))
        if occurred is not None:
            seen_seconds = int((turn.logical_time - occurred).total_seconds())
    if (
        actual_seconds is not None
        and seen_seconds is not None
        and abs(actual_seconds - seen_seconds) > SINCE_SPOKE_SLACK_SECONDS
        and last_his is not None
        and seen_last is not None
        and not texts_match(str(seen_last.get("text")), last_his.text)
    ):
        findings.append(
            Finding(
                severity="high",
                invariant="advisory",
                code="since_he_last_spoke_wrong_anchor",
                title="「他多久没说话」锚在切片残句上，不是账本最后一句",
                ledger=(
                    f"最后一句他是「{clip(last_his.text)}」，距此时 {actual_seconds}s"
                    f"（{local_iso(last_his.logical_time)}）"
                ),
                seen=f"她可见的最后一句他是「{clip(str(seen_last.get('text')))}」，派生间隔 {seen_seconds}s",
                detail="叫醒/等待车道如果用这个间隔，会把已经说过的话当成沉默。",
                group="since_he_last_spoke_wrong_anchor",
            )
        )
    if turn.lane == "expired_expectation":
        hope = _hope_before(ledger, turn.cursor_seq)
        after_hope = []
        if hope is not None:
            after_hope = [
                item
                for item in ledger.observations
                if hope.seq < item.seq <= turn.cursor_seq and not item.is_reaction
            ]
        if after_hope:
            missing = [
                item
                for item in after_hope
                if _matched(seen_dialogue_lines(turn), item) is None
            ]
            findings.append(
                Finding(
                    severity="critical",
                    invariant="advisory",
                    code="expired_hope_after_he_already_answered",
                    title="盼头已被他答过，宿主仍按「没人理」叫醒她，而且没把后文给她",
                    ledger=(
                        f"盼头「{clip(hope.hoped_response if hope else '')}」之后他已经说了 "
                        + " / ".join(clip(item.text, 36) for item in after_hope[-3:])
                    ),
                    seen=(
                        "对话栏：" + render_seen_conversation(turn)
                        + ("；后文缺失：" + " / ".join(clip(item.text, 28) for item in missing[-3:]) if missing else "")
                    ),
                    detail="expired_expectation 在对方已经开口之后仍然 mint，切片还停在更早的一句。",
                    group="expired_hope_after_he_already_answered",
                    evidence={
                        "answered_texts": [item.text for item in after_hope],
                        "missing_from_slice": [item.text for item in missing],
                    },
                )
            )
    if last_his is not None and first_fragment(last_his.text, LEAVE_FRAGMENTS):
        later = [
            item
            for item in ledger.observations
            if item.seq > last_his.seq and item.seq <= turn.cursor_seq and not item.is_reaction
        ]
        if not later:
            belief = turn.inner_summary + " " + turn.impulse_summary + " " + turn.brief_rationale
            fragment = first_fragment(belief, FALSE_RETURN_FRAGMENTS)
            if fragment:
                findings.append(
                    Finding(
                        severity="critical",
                        invariant="advisory",
                        code="false_return_while_he_is_still_away",
                        title="他说去干一件事还没回来，材料/内心却写成他回来了",
                        ledger=f"最后一句他仍是「{clip(last_his.text)}」，此后没有新的 Observation",
                        seen=f"内心含「{fragment}」：{clip(turn.inner_summary)}",
                        detail="叫醒车道把未结束的离开当成「他回来了还不说话」。",
                        group="false_return_while_he_is_still_away",
                    )
                )
    for source, text in _material_prose(turn, ledger):
        if source not in {"advisories", "situation"} and "advisory" not in source:
            continue
        if "he has not spoken" in text.lower() and last_his is not None and turn.logical_time is not None:
            gap = turn.logical_time - last_his.logical_time
            if gap < timedelta(minutes=2) and last_his.seq > turn.cursor_seq - 80:
                # If he spoke very recently, claiming "has not spoken" is false.
                findings.append(
                    Finding(
                        severity="high",
                        invariant="advisory",
                        code="advisory_claims_silence_after_speech",
                        title="advisory 说他没开口，账本上他刚说过话",
                        ledger=f"最后一句他：{clip(last_his.text)} @ {local_iso(last_his.logical_time)}",
                        seen=clip(text),
                        detail="处境描述把刚发生的对方发言写成沉默。",
                        group="advisory_claims_silence_after_speech",
                    )
                )
    for finding in findings:
        _stamp(finding, turn)
    return findings


def _hope_before(ledger: Ledger, seq: int) -> Hope | None:
    prior = [item for item in ledger.hopes if item.seq <= seq]
    return prior[-1] if prior else None


def check_cross_lane(turns: list[SeenTurn], ledger: Ledger) -> list[Finding]:
    findings: list[Finding] = []
    for turn in turns:
        queued = ledger.queued_later_at(turn.cursor_seq, turn.logical_time)
        if not queued:
            continue
        waiting = turn.materials.get("messages_waiting_to_send")
        waiting_n = len(waiting) if isinstance(waiting, list) else 0
        seen = seen_dialogue_lines(turn)
        for action in queued:
            text = action.text or ""
            in_dialogue = bool(text) and any(texts_match(item["text"], text) for item in seen)
            if waiting_n == 0 and not in_dialogue:
                finding = Finding(
                    severity="high",
                    invariant="cross_lane",
                    code="queued_later_invisible",
                    title="已经写好、还在排队的话对其它车道不可见",
                    ledger=(
                        f"followup {action.action_id[-12:]} 已授权 seq {action.seq}，"
                        f"not_before={local_iso(action.not_before)}，正文：{clip(text)}"
                    ),
                    seen="messages_waiting_to_send 缺省，对话栏也没有这句",
                    detail="下一轮（或另一条主动车道）看不见她已经写好待发的气泡，容易再开一次头。",
                    group="queued_later_invisible",
                )
                _stamp(finding, turn)
                findings.append(finding)
    visible_actions = [
        action
        for action in ledger.actions.values()
        if action.kind in {"proactive_message", "followup"}
        and (action.provider_accepted_seq or action.delivered_seq)
        and action.text
    ]
    visible_actions.sort(key=lambda item: item.provider_accepted_seq or item.delivered_seq or item.seq)
    for index, left in enumerate(visible_actions):
        left_at = left.provider_accepted_at or left.delivered_at or left.logical_time
        if left_at is None:
            continue
        for right in visible_actions[index + 1 :]:
            right_at = right.provider_accepted_at or right.delivered_at or right.logical_time
            if right_at is None:
                continue
            if right_at - left_at > DUPLICATE_LANE_WINDOW:
                break
            shared = [
                motif
                for motif in GREETING_MOTIFS
                if motif in (left.text or "") and motif in (right.text or "")
            ]
            if not shared:
                continue
            findings.append(
                Finding(
                    severity="high",
                    invariant="cross_lane",
                    code="duplicate_lane_opening",
                    title="同一时段两条车道各自开了一次头，重复同一件事",
                    ledger=(
                        f"{left.kind} @ {local_iso(left_at)}：「{clip(left.text or '')}」；"
                        f"{right.kind} @ {local_iso(right_at)}：「{clip(right.text or '')}」"
                    ),
                    seen=f"共同母题：{shared}",
                    detail="两条可见主动消息间隔不到 90 分钟，且都提到同一开场母题。后写的那条看不见先发出去的那条。",
                    group="duplicate_lane_opening",
                    evidence={"motifs": shared},
                )
            )
    for action in ledger.actions.values():
        if action.kind != "followup" or not action.text:
            continue
        start = action.stored_seq or action.seq
        end = action.provider_accepted_seq or action.delivered_seq
        if end is None or start is None or end <= start:
            continue
        intervening_obs = [
            item for item in ledger.observations if start < item.seq < end and not item.is_reaction
        ]
        intervening_her = [
            item
            for item in ledger.her_messages
            if start < item.seq < end and item.payload_ref != action.payload_ref and item.delivered
        ]
        if not intervening_obs and not intervening_her:
            continue
        findings.append(
            Finding(
                severity="high",
                invariant="cross_lane",
                code="later_fired_after_world_moved",
                title="延迟表达发出时世界已经变了，宿主没有重新给她看",
                ledger=(
                    f"followup 写于 seq {start}，发出 seq {end}。"
                    + (
                        " 其间他说话：" + " / ".join(clip(item.text, 28) for item in intervening_obs[-3:])
                        if intervening_obs
                        else ""
                    )
                    + (
                        " 其间她另说过：" + " / ".join(clip(item.text, 28) for item in intervening_her[-3:])
                        if intervening_her
                        else ""
                    )
                ),
                seen=f"仍发出写好的原文：{clip(action.text)}",
                detail="later 气泡在 due 之后世界已动（他开口或她另说了），原句仍被投递。",
                group="later_fired_after_world_moved",
            )
        )
    return findings


def check_head_lifecycle(ledger: Ledger) -> list[Finding]:
    findings: list[Finding] = []
    now = ledger.head_logical_time
    if now is None:
        return findings
    overdue = []
    for item in ledger.head_appraisals:
        if item.get("status") != "active":
            continue
        expires = as_dt(item.get("expires_at"))
        if expires is None or expires > now:
            continue
        meanings = [
            hypothesis.get("meaning")
            for hypothesis in item.get("hypotheses") or []
            if isinstance(hypothesis, dict) and isinstance(hypothesis.get("meaning"), str)
        ]
        overdue.append(clip(str(meanings[0] if meanings else item.get("appraisal_id")), 50))
    if overdue:
        findings.append(
            Finding(
                severity="high",
                invariant="lifecycle",
                code="head_overdue_appraisal_active",
                title="账本头状态仍有已过期却 active 的读法",
                ledger=f"逻辑时钟 {local_iso(now)}；head appraisals 中 {len(overdue)} 条 expires_at 已过且 status=active",
                seen="当前注意力库存会继续把它们当未了结，例如：" + overdue[0],
                detail="时钟执刑之后这条不应再出现。出现就是生命周期没兑现。",
                group="head_overdue_appraisal_active",
                evidence={"meanings": overdue[:12]},
            )
        )
    decayed_active = [
        item.get("episode_id")
        for item in ledger.head_affect
        if item.get("status") == "active" and item.get("episode_id") in ledger.affect_decayed
    ]
    if decayed_active:
        findings.append(
            Finding(
                severity="medium",
                invariant="lifecycle",
                code="head_decayed_affect_active",
                title="头状态里已 Decayed 的情绪仍是 active",
                ledger=f"{len(decayed_active)} 条 episode 有 AffectEpisodeDecayed 但仍 status=active",
                seen=str(decayed_active[:6]),
                detail="收口事件没反映到头投影。",
                group="head_decayed_affect_active",
            )
        )
    open_threads = []
    for item in ledger.head_threads:
        values = item.get("values") if isinstance(item.get("values"), dict) else item
        status = values.get("status") if isinstance(values, dict) else item.get("status")
        expires = as_dt(
            (values.get("expires_at") if isinstance(values, dict) else None) or item.get("expires_at")
        )
        if status in {None, "open", "active"} and expires is not None and expires <= now:
            open_threads.append(str(item.get("thread_id") or "")[-16:])
    if open_threads:
        findings.append(
            Finding(
                severity="medium",
                invariant="lifecycle",
                code="head_overdue_thread_open",
                title="已过期仍 open 的对话线程",
                ledger=f"{open_threads}",
                seen="线程切片仍会当活题",
                detail="线程自己的 expires_at 已过，状态未离开 open。",
                group="head_overdue_thread_open",
            )
        )
    return findings


def check_silenced(ledger: Ledger) -> list[Finding]:
    findings: list[Finding] = []
    status_counts = Counter(str(item.get("status") or "") for item in ledger.usage)
    error_counts = Counter(
        clip(str(item.get("error") or "ok"), 80)
        for item in ledger.usage
        if str(item.get("status") or "") != "succeeded"
    )
    purpose_fail = Counter(
        str(item.get("purpose") or "?")
        for item in ledger.usage
        if str(item.get("status") or "") != "succeeded"
    )
    failed = sum(count for status, count in status_counts.items() if status != "succeeded")
    total = sum(status_counts.values())
    findings.append(
        Finding(
            severity="info" if failed == 0 else "low",
            invariant="silenced",
            code="model_usage_failures",
            title="模型调用失败分布",
            ledger=f"world_v2_model_usage {total} 次，失败 {failed} 次",
            seen=f"status={dict(status_counts)}；error={dict(error_counts)}；purpose={dict(purpose_fail)}",
            detail="失败不一定消音可见回复；caller_cancelled 常是重选。此项只报发生率。",
            group="model_usage_failures",
            evidence={
                "status": dict(status_counts),
                "errors": dict(error_counts),
                "purpose_fail": dict(purpose_fail),
            },
        )
    )
    role_codes = Counter(str(item.get("failure_code") or "") for item in ledger.invalid_role)
    if ledger.invalid_role:
        findings.append(
            Finding(
                severity="medium",
                invariant="silenced",
                code="invalid_role_result",
                title="invalid_role_result 曾经把一轮判定作废",
                ledger=f"{len(ledger.invalid_role)} 次，codes={dict(role_codes)}；seq={[item.get('seq') for item in ledger.invalid_role]}",
                seen="该轮没有合法角色结果，可见话被技术失败吃掉",
                detail="这是宿主消音，不是她选择沉默。",
                group="invalid_role_result",
                evidence={"events": ledger.invalid_role[:12]},
            )
        )
    tech_codes = Counter(str(item.get("failure_code") or item.get("status") or "") for item in ledger.technical_failures)
    if ledger.technical_failures:
        findings.append(
            Finding(
                severity="low",
                invariant="silenced",
                code="technical_failure_events",
                title="账本里的 technical_failure 事件",
                ledger=f"{len(ledger.technical_failures)} 条；codes={dict(tech_codes)}",
                seen="TriggerProcessCompleted / ModelResultRecorded 带 technical_failure",
                detail="需要对照是否伴随 System Notice 或可见回复被吞。",
                group="technical_failure_events",
                evidence={"sample": ledger.technical_failures[:8]},
            )
        )
    if ledger.notices:
        findings.append(
            Finding(
                severity="medium",
                invariant="silenced",
                code="system_notice",
                title="System Notice 曾经对外发出过",
                ledger=json.dumps(ledger.notices, ensure_ascii=False)[:500],
                seen="这是系统口吻，不是她说的话",
                detail="Notice 本身证明某一轮表达没走通，才落到技术通知。",
                group="system_notice",
                evidence={"notices": ledger.notices},
            )
        )
    findings.append(
        Finding(
            severity="info",
            invariant="silenced",
            code="deferred_not_in_ledger",
            title="deferred_visible_turn 无法从账本机械判定",
            ledger="Action 生命周期事件只有 scheduled/claimed/dispatch/accepted/delivered，没有 deferred 状态字段",
            seen="action_pump 的 deferred_visible_turn 是运行时状态，不落世界事件",
            detail=(
                "这项不变量现在无法对生产账本做闭环检查：延迟可见拍只存在于进程内存。"
                "能检查的替代项是 later followup 在世界已动之后仍发出（见 cross_lane）。"
            ),
            group="deferred_not_in_ledger",
        )
    )
    return findings


def _stamp(finding: Finding, turn: SeenTurn) -> None:
    finding.turn_id = turn.inner_turn_id
    finding.purpose = turn.purpose
    finding.lane = turn.lane
    finding.cursor_seq = turn.cursor_seq
    finding.logical_local = local_iso(turn.logical_time or turn.updated_at)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def sort_findings(findings: list[Finding]) -> list[Finding]:
    rank = {name: index for index, name in enumerate(SEVERITY_ORDER)}
    return sorted(
        findings,
        key=lambda item: (
            rank.get(item.severity, 9),
            item.invariant,
            item.code,
            item.cursor_seq or 0,
        ),
    )


def _group(findings: list[Finding]) -> list[tuple[str, list[Finding]]]:
    grouped: dict[str, list[Finding]] = defaultdict(list)
    order: list[str] = []
    for item in findings:
        key = item.group or f"{item.code}:{item.turn_id}"
        if key not in grouped:
            order.append(key)
        grouped[key].append(item)
    return [(key, grouped[key]) for key in order]


def write_report(
    *,
    output: Path,
    database: Path,
    ledger: Ledger,
    turns: list[SeenTurn],
    findings: list[Finding],
    skipped: list[dict[str, str]],
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    findings = sort_findings(findings)
    counts = Counter(item.severity for item in findings)
    by_code = Counter(item.code for item in findings)
    payload = {
        "database": str(database),
        "world_id": ledger.world_id,
        "max_seq": ledger.max_seq,
        "event_count": len(ledger.events),
        "turn_count": len(turns),
        "head_logical_time": iso(ledger.head_logical_time),
        "head_logical_local": local_iso(ledger.head_logical_time),
        "finding_count": len(findings),
        "by_severity": dict(counts),
        "by_code": dict(by_code),
        "skipped_invariants": skipped,
        "findings": [asdict(item) for item in findings],
    }
    (output / "findings.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines: list[str] = []
    lines.append("# 她看见的世界 vs 账本")
    lines.append("")
    lines.append("只读审计。未写 `data/`，未碰生产进程。")
    lines.append("")
    lines.append(f"- 账本：`{database}`")
    lines.append(f"- 世界：`{ledger.world_id}`")
    lines.append(
        f"- 事件 {len(ledger.events)} 条，HEAD seq **{ledger.max_seq}**，逻辑时钟 {local_iso(ledger.head_logical_time)}"
    )
    lines.append(f"- 带快照的内心轮 **{len(turns)}**")
    lines.append(
        f"- 可判定分歧 **{len(findings)}** 条（critical {counts.get('critical', 0)} / "
        f"high {counts.get('high', 0)} / medium {counts.get('medium', 0)} / "
        f"low {counts.get('low', 0)} / info {counts.get('info', 0)}）"
    )
    lines.append("")
    lines.append("## 她被骗了什么（按严重度）")
    lines.append("")
    if not findings:
        lines.append("没有可判定分歧。")
    for _group_key, group in _group(findings):
        head = group[0]
        extra = f"（{len(group)} 轮）" if len(group) > 1 else ""
        lines.append(f"### [{head.severity}] {head.title}{extra}")
        lines.append("")
        lines.append(f"- 不变量：`{head.invariant}` / `{head.code}`")
        if len(group) == 1:
            _append_case(lines, head)
        else:
            lines.append(f"- 分歧：{head.detail}")
            lines.append("")
            lines.append("| 时间 | 车道 | cursor | 账本事实 | 她看到的 |")
            lines.append("|---|---|---|---|---|")
            for item in group[:40]:
                lines.append(
                    f"| {item.logical_local or ''} | {item.lane or item.purpose or ''} | "
                    f"{item.cursor_seq or ''} | {clip(item.ledger, 70)} | {clip(item.seen, 70)} |"
                )
            if len(group) > 40:
                lines.append(f"| … | | | 其余 {len(group) - 40} 轮见 findings.json | |")
            lines.append("")
            lines.append(f"- 代表轮：`{head.turn_id}` @ {head.logical_local}")
            lines.append(f"- 账本：{head.ledger}")
            lines.append(f"- 她看到的：{head.seen}")
            lines.append("")
    lines.append("## 按代码计数")
    lines.append("")
    lines.append("| 次数 | 严重度 | 代码 |")
    lines.append("|---|---|---|")
    seen_codes: set[str] = set()
    for item in findings:
        if item.code in seen_codes:
            continue
        seen_codes.add(item.code)
        lines.append(f"| {by_code[item.code]} | {item.severity} | `{item.code}` |")
    lines.append("")
    lines.append("## 没做成机械检查的不变量")
    lines.append("")
    for item in skipped:
        lines.append(f"- **{item['name']}**：{item['why']}")
    lines.append("")
    lines.append("完整机器可读结果：`findings.json`。")
    lines.append("")
    (output / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def _append_case(lines: list[str], item: Finding) -> None:
    if item.turn_id:
        lines.append(
            f"- 轮次：`{item.turn_id}` · {item.purpose} · {item.lane} · cursor {item.cursor_seq} · {item.logical_local}"
        )
    lines.append(f"- 账本：{item.ledger}")
    lines.append(f"- 她看到的：{item.seen}")
    lines.append(f"- 分歧：{item.detail}")
    lines.append("")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


SKIPPED_INVARIANTS = [
    {
        "name": "口头承诺语义（「明天再给」是否算新许诺）",
        "why": "需要理解她这句话是未来意向还是对已投递事实的否认。只有当 photos_i_shared/already_sent_count 已在材料里仍说「还没发」时才能机械判定；没有投递事实时的口头许诺不是分歧。",
    },
    {
        "name": "表情渲染是否等于他发的那个 face",
        "why": "Observation 的 reaction 没有 text，切片可能渲染成「可爱」或「☀️ 太阳」。缺一张官方 face→可见文本对照表，不能判定标错。",
    },
    {
        "name": "day_sheet 模板日课 vs 已结算生活是否「同一种人生」",
        "why": "两边都是真材料（作息表是日程，书店是结算）。并排出现是编排问题，不是账本事实被捏造。要对齐需要语义比较。",
    },
    {
        "name": "deferred_visible_turn 发生率",
        "why": "该状态只在 action_pump 运行时出现，不写入世界事件。账本只能看到 scheduled/claimed/delivered。",
    },
    {
        "name": "她选沉默是否「该回却没回」",
        "why": "timing_choice=silent 是角色决定权。机械检查只能报发生了，不能报成失真。",
    },
]


def run(database: Path, output: Path, *, world_id: str, limit_turns: int | None) -> int:
    output = assert_output_is_safe(output)
    if not database.is_file():
        raise SystemExit(f"ledger not found: {database}")
    conn = open_ro(database)
    try:
        ledger = Ledger(conn, world_id)
        turns = load_turns(conn, world_id, ledger)
    finally:
        conn.close()
    if limit_turns is not None:
        turns = turns[-limit_turns:]
    findings: list[Finding] = []
    for turn in turns:
        findings.extend(check_conversation(turn, ledger))
        findings.extend(check_forged_actions(turn, ledger))
        findings.extend(check_lifecycle(turn, ledger))
        findings.extend(check_advisory(turn, ledger))
    findings.extend(check_cross_lane(turns, ledger))
    findings.extend(check_head_lifecycle(ledger))
    findings.extend(check_silenced(ledger))
    write_report(
        output=output,
        database=database,
        ledger=ledger,
        turns=turns,
        findings=findings,
        skipped=SKIPPED_INVARIANTS,
    )
    counts = Counter(item.severity for item in findings)
    print(
        f"seq={ledger.max_seq} events={len(ledger.events)} turns={len(turns)} "
        f"findings={len(findings)} "
        f"critical={counts.get('critical', 0)} high={counts.get('high', 0)} "
        f"medium={counts.get('medium', 0)} low={counts.get('low', 0)} info={counts.get('info', 0)}"
    )
    print(f"report: {output / 'REPORT.md'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=PRODUCTION_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--world-id", default=WORLD_ID)
    parser.add_argument("--limit-turns", type=int, default=None)
    args = parser.parse_args()
    return run(args.database, args.out, world_id=args.world_id, limit_turns=args.limit_turns)


if __name__ == "__main__":
    raise SystemExit(main())
