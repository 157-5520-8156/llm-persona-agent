from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import re
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")

QUESTION_END_RE = re.compile(r"[?？]\s*$")
SHORT_TEXT_MAX = 6
MEAL_WINDOWS = ((11.5, 13.5), (17.5, 19.5))
BEDTIME_HOURS = {22, 23, 0, 1}
CLOSING_HINTS = ("早点休息", "晚安", "好梦", "去睡", "睡了", "休息吧", "早点睡")
FOOD_HINTS = ("吃了", "吃饭", "午饭", "晚饭", "早餐", "饿了", "吃没", "吃过")
CLARIFY_HINTS = (
    "什么意思",
    "哪种",
    "是指",
    "你是说",
    "到底",
    "怎么讲",
    "看不懂",
    "啥表情",
    "什么奇怪",
    "好奇",
)
CAPABILITY_ASK_HINTS = (
    "帮我",
    "替我",
    "操作",
    "转账",
    "打电话",
    "联系",
    "下载",
    "安装",
    "修电脑",
    "代买",
    "远程",
)
PHOTO_ASK_HINTS = ("照片", "自拍", "发张", "看看你", "拍照", "发图", "看看你")
REFUSAL_HINTS = ("不拍", "不发", "不给看", "不想拍", "不方便发")


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


def local_hour(value: object) -> float | None:
    parsed = as_dt(value)
    if parsed is None:
        return None
    local = parsed.astimezone(SHANGHAI)
    return local.hour + local.minute / 60.0


def in_meal_window(value: object) -> bool:
    hour = local_hour(value)
    if hour is None:
        return False
    return any(start <= hour <= end for start, end in MEAL_WINDOWS)


def in_bedtime_window(value: object) -> bool:
    parsed = as_dt(value)
    if parsed is None:
        return False
    return parsed.astimezone(SHANGHAI).hour in BEDTIME_HOURS


def parse_event(raw: str | bytes) -> dict[str, Any]:
    event = json.loads(raw)
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


@dataclass
class MessageRow:
    seq: int
    text: str
    logical_time: datetime | None
    correlation_id: str | None = None
    is_reaction: bool = False
    action_id: str | None = None


@dataclass
class ObservationRow:
    seq: int
    text: str
    logical_time: datetime | None
    correlation_id: str
    source_count: int
    is_reaction: bool = False


@dataclass
class ProposalRow:
    seq: int
    correlation_id: str
    timing_choice: str | None
    impulse_summary: str
    later: str | None
    delay_seconds: int | None
    media_request: str | None
    beat_texts: list[str]
    has_delivered_expression: bool


@dataclass
class TurnBundle:
    correlation_id: str
    observation: ObservationRow | None = None
    proposals: list[ProposalRow] = field(default_factory=list)
    her_messages: list[MessageRow] = field(default_factory=list)
    reaction_deliveries: list[MessageRow] = field(default_factory=list)


@dataclass
class LedgerIndex:
    world_id: str
    max_seq: int
    observations: list[ObservationRow]
    her_messages: list[MessageRow]
    reaction_deliveries: list[MessageRow]
    proposals: list[ProposalRow]
    turns: dict[str, TurnBundle]
    multi_beat_correlations: list[str]
    interjection_opportunities: list[dict[str, Any]]
    reconsideration_opens: int
    photo_user_asks: list[ObservationRow]
    capability_user_asks: list[ObservationRow]
    meal_window_inbounds: list[ObservationRow]
    bedtime_active_windows: list[dict[str, Any]]

    @classmethod
    def load(
        cls,
        conn: sqlite3.Connection,
        *,
        world_id: str,
        seq_from: int = 0,
        seq_to: int | None = None,
    ) -> LedgerIndex:
        rows = conn.execute(
            """
            SELECT ledger_sequence, event_json
            FROM world_v2_events
            WHERE world_id = ? AND ledger_sequence >= ?
            ORDER BY ledger_sequence
            """,
            (world_id, seq_from),
        ).fetchall()
        if seq_to is not None:
            rows = [row for row in rows if int(row[0]) <= seq_to]

        observations: list[ObservationRow] = []
        her_messages: list[MessageRow] = []
        reaction_deliveries: list[MessageRow] = []
        proposals: list[ProposalRow] = []
        beat_auth_by_corr: dict[str, list[int]] = defaultdict(list)
        delivered_actions: set[str] = set()
        reaction_action_ids: set[str] = set()
        reconsideration_opens = 0
        max_seq = seq_from

        corr_delivered: dict[str, bool] = defaultdict(bool)
        for seq_raw, raw in rows:
            seq = int(seq_raw)
            max_seq = max(max_seq, seq)
            event = parse_event(raw)
            kind = str(event.get("event_type") or "")
            payload = payload_of(event)
            corr = str(event.get("correlation_id") or "") or None

            if kind == "ObservationRecorded":
                meta = payload.get("coalescing_metadata")
                meta = meta if isinstance(meta, dict) else {}
                sources = meta.get("source_event_ids")
                source_count = len(sources) if isinstance(sources, list) else 1
                text = payload.get("text") if isinstance(payload.get("text"), str) else ""
                reactions = meta.get("reaction_refs")
                is_reaction = (not text.strip()) and isinstance(reactions, list) and bool(reactions)
                if is_reaction and isinstance(reactions, list):
                    text = "[reaction " + ", ".join(str(item) for item in reactions[:4]) + "]"
                logical_time = as_dt(event.get("logical_time") or payload.get("received_at"))
                if text.strip() or is_reaction:
                    observations.append(
                        ObservationRow(
                            seq=seq,
                            text=text.strip(),
                            logical_time=logical_time,
                            correlation_id=corr or f"missing-corr:{seq}",
                            source_count=source_count,
                            is_reaction=is_reaction,
                        )
                    )
                continue

            if kind == "MessagePayloadStored":
                message = payload.get("message")
                message = message if isinstance(message, dict) else {}
                text = message.get("text") if isinstance(message.get("text"), str) else ""
                if text.strip():
                    her_messages.append(
                        MessageRow(
                            seq=seq,
                            text=text.strip(),
                            logical_time=as_dt(event.get("logical_time")),
                            correlation_id=corr,
                        )
                    )
                continue

            if kind == "ActionAuthorized":
                action = payload.get("action")
                action = action if isinstance(action, dict) else {}
                action_id = str(action.get("action_id") or "")
                action_payload = action.get("payload")
                blob = json.dumps(action_payload, ensure_ascii=False).lower()
                if "face" in blob or "reaction" in blob or "qq-face" in blob:
                    reaction_action_ids.add(action_id)
                continue

            if kind == "ActionDelivered":
                action_id = str(payload.get("action_id") or "")
                delivered_actions.add(action_id)
                if action_id in reaction_action_ids:
                    reaction_deliveries.append(
                        MessageRow(
                            seq=seq,
                            text="[qq-reaction]",
                            logical_time=as_dt(payload.get("observed_at") or event.get("logical_time")),
                            correlation_id=corr,
                            is_reaction=True,
                            action_id=action_id,
                        )
                    )
                if corr:
                    corr_delivered[corr] = True
                continue

            if kind == "ExpressionBeatAuthorized":
                if corr:
                    beat_auth_by_corr[corr].append(seq)
                continue

            if kind == "TriggerProcessOpened":
                process = payload.get("process")
                process = process if isinstance(process, dict) else {}
                if process.get("process_kind") == "expression_reconsideration":
                    reconsideration_opens += 1
                continue

            if kind != "ProposalRecorded":
                continue

            proposal_json = payload.get("proposal_json")
            proposal: dict[str, Any] = {}
            if isinstance(proposal_json, str):
                try:
                    proposal = json.loads(proposal_json)
                except json.JSONDecodeError:
                    proposal = {}
            timing = proposal.get("timing_choice")
            timing_s = str(timing) if timing else None
            beat_texts: list[str] = []
            media_request = proposal.get("media_request")
            if isinstance(media_request, str):
                media_request = media_request if media_request not in ("none", "") else None
            for change in proposal.get("proposed_changes") or []:
                if not isinstance(change, dict):
                    continue
                change_payload = change.get("payload")
                change_payload = change_payload if isinstance(change_payload, dict) else {}
                canonical = change_payload.get("canonical_json")
                if isinstance(canonical, str):
                    try:
                        canonical = json.loads(canonical)
                    except json.JSONDecodeError:
                        canonical = {}
                if not isinstance(canonical, dict):
                    continue
                media_request = media_request or canonical.get("media_request")
                if media_request in ("none", ""):
                    media_request = None
                for beat in canonical.get("beat_drafts") or []:
                    if isinstance(beat, dict):
                        text = beat.get("text")
                        if isinstance(text, str) and text.strip():
                            beat_texts.append(text.strip())
            proposals.append(
                ProposalRow(
                    seq=seq,
                    correlation_id=corr or f"missing-corr:{seq}",
                    timing_choice=timing_s,
                    impulse_summary=str(proposal.get("impulse_summary") or "").strip(),
                    later=str(proposal.get("later") or "").strip() or None,
                    delay_seconds=proposal.get("delay_seconds")
                    if isinstance(proposal.get("delay_seconds"), int)
                    else None,
                    media_request=str(media_request) if media_request else None,
                    beat_texts=beat_texts,
                    has_delivered_expression=bool(corr and corr_delivered.get(corr)),
                )
            )

        for proposal in proposals:
            proposal.has_delivered_expression = bool(
                corr_delivered.get(proposal.correlation_id)
            )

        turns: dict[str, TurnBundle] = {}
        for obs in observations:
            bundle = turns.setdefault(obs.correlation_id, TurnBundle(correlation_id=obs.correlation_id))
            bundle.observation = obs
        for proposal in proposals:
            bundle = turns.setdefault(
                proposal.correlation_id, TurnBundle(correlation_id=proposal.correlation_id)
            )
            bundle.proposals.append(proposal)
        for msg in her_messages:
            if not msg.correlation_id:
                continue
            bundle = turns.setdefault(msg.correlation_id, TurnBundle(correlation_id=msg.correlation_id))
            bundle.her_messages.append(msg)
        for msg in reaction_deliveries:
            if not msg.correlation_id:
                continue
            bundle = turns.setdefault(msg.correlation_id, TurnBundle(correlation_id=msg.correlation_id))
            bundle.reaction_deliveries.append(msg)

        multi_beat_correlations = [
            corr for corr, seqs in beat_auth_by_corr.items() if len(seqs) >= 2
        ]
        interjection_opportunities: list[dict[str, Any]] = []
        obs_seqs = [obs.seq for obs in observations]
        for corr, beat_seqs in beat_auth_by_corr.items():
            if len(beat_seqs) < 2:
                continue
            first, last = min(beat_seqs), max(beat_seqs)
            between = [seq for seq in obs_seqs if first < seq < last]
            if between:
                interjection_opportunities.append(
                    {
                        "correlation_id": corr,
                        "first_beat_seq": first,
                        "last_beat_seq": last,
                        "interleaved_observation_seqs": between,
                    }
                )

        photo_user_asks = [
            obs
            for obs in observations
            if any(hint in obs.text for hint in PHOTO_ASK_HINTS)
        ]
        capability_user_asks = [
            obs
            for obs in observations
            if any(hint in obs.text for hint in CAPABILITY_ASK_HINTS)
        ]
        meal_window_inbounds = [obs for obs in observations if in_meal_window(obs.logical_time)]

        bedtime_active_windows: list[dict[str, Any]] = []
        for obs in observations:
            if not in_bedtime_window(obs.logical_time):
                continue
            bedtime_active_windows.append(
                {"seq": obs.seq, "text_preview": obs.text[:80], "correlation_id": obs.correlation_id}
            )

        return cls(
            world_id=world_id,
            max_seq=max_seq,
            observations=observations,
            her_messages=her_messages,
            reaction_deliveries=reaction_deliveries,
            proposals=proposals,
            turns=turns,
            multi_beat_correlations=multi_beat_correlations,
            interjection_opportunities=interjection_opportunities,
            reconsideration_opens=reconsideration_opens,
            photo_user_asks=photo_user_asks,
            capability_user_asks=capability_user_asks,
            meal_window_inbounds=meal_window_inbounds,
            bedtime_active_windows=bedtime_active_windows,
        )

    def inbound_turns(self) -> list[TurnBundle]:
        return [
            turn
            for turn in self.turns.values()
            if turn.correlation_id.startswith("qq:") and turn.observation is not None
        ]

    def final_proposals(self) -> list[ProposalRow]:
        latest: dict[str, ProposalRow] = {}
        for proposal in sorted(self.proposals, key=lambda item: item.seq):
            latest[proposal.correlation_id] = proposal
        return list(latest.values())
