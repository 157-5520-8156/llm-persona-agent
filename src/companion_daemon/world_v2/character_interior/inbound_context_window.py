"""Bound redundant narrative history in a model view, never in World storage.

Current state, counterpart reports, facts, capabilities, recalled material and
all action/effect coordinates remain intact. This is an explicit experimental
presentation policy, not a source of facts or a character behavior policy.
"""
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timedelta
import re
from zoneinfo import ZoneInfo

from ..shared_string_view import unpack_shared_strings

CONTRACT = "inbound-narrative-window.1"
MAX_COMPANION_UTTERANCES = 2
MAX_OVERLAP_COMPANION_UTTERANCES = 8
_DEFAULT_LOCAL_TZ = ZoneInfo("Asia/Shanghai")
_TRANSIENT_FACT_PREDICATES = frozenset(
    {
        "situation.recent",
        "activity.current",
        "location.current",
        "schedule.commitment",
        "health.condition",
    }
)
_APPRAISAL_ENUM_SUFFIX_RE = re.compile(
    r"\s*/\s*(?:user|companion|npc|situation|third_party|unknown|world|self)"
    r"\s*/\s*(?:controllable|partly_controllable|uncontrollable|unknown)"
    r"\s*/\s*(?:low|moderate|high|severe|unknown)"
)


def _has_substantive_word_chars(text: str) -> bool:
    return any(ch.isalnum() or "\u4e00" <= ch <= "\u9fff" for ch in text)


def _clean_prefetch_appraisal_text(text: str) -> str:
    cleaned = text.removeprefix("Remembered private interpretation — ").strip()
    cleaned = _APPRAISAL_ENUM_SUFFIX_RE.sub("", cleaned).strip()
    return cleaned or text


def _episode_line_bodies(text: str) -> list[str]:
    if not text.startswith("一段聊天："):
        return []
    bodies: list[str] = []
    for raw_line in text.splitlines()[1:]:
        line = raw_line.strip()
        if not line:
            continue
        if "]: " in line:
            bodies.append(line.split("]: ", 1)[1].strip())
        else:
            bodies.append(line)
    return bodies


def _enrich_relevant_facts_temporal_scope(facts: object, logical_time: object) -> None:
    if not isinstance(facts, list):
        return
    now_dt = _recorded_second(logical_time)
    if now_dt is None:
        return
    now_local_date = now_dt.astimezone(_DEFAULT_LOCAL_TZ).date()
    for f_item in facts:
        if not isinstance(f_item, dict):
            continue
        pred = f_item.get("predicate_code")
        occ_raw = f_item.get("occurred_at")
        if not isinstance(pred, str) or not pred or not isinstance(occ_raw, str):
            continue
        occ_dt = _recorded_second(occ_raw)
        if occ_dt is None:
            continue
        day_diff = (now_local_date - occ_dt.astimezone(_DEFAULT_LOCAL_TZ).date()).days
        if day_diff <= 0:
            occurred_age = "今天"
        elif day_diff == 1:
            occurred_age = "昨天"
        elif day_diff < 30:
            occurred_age = f"{day_diff}天前"
        else:
            occurred_age = f"{day_diff // 30}个月前（{day_diff}天前）"
        f_item["occurred_age"] = occurred_age
        if pred in _TRANSIENT_FACT_PREDICATES:
            f_item["temporal_scope"] = "point_in_time_situation"
            if day_diff >= 1:
                f_item["temporal_note"] = f"{occurred_age}当时的动态/处境，非今日现状"
        else:
            f_item["temporal_scope"] = "durable_trait_or_identity"


def _recorded_second(value):
    """Compare recorded timestamps conservatively at whole-second precision."""
    if not isinstance(value, str):
        return None
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if instant.utcoffset() is None:
        return None
    return instant.replace(microsecond=0)


def _local_hour(instant: datetime) -> int:
    if instant.utcoffset() == timedelta(0):
        return instant.astimezone(_DEFAULT_LOCAL_TZ).hour
    return instant.hour


def _format_duration_human(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}秒"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}分{sec}秒" if sec else f"{minutes}分钟"
    hours, mins = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}小时{mins}分" if mins else f"{hours}小时"
    days, rem_hours = divmod(hours, 24)
    return f"{days}天{rem_hours}小时" if rem_hours else f"{days}天"


def _dialogue_observation_timing(dialogue):
    """Describe receipt/observation overlap, never recipient read state.

    The turn event is often only second-precision while role delivery times
    come from receipts. Ledger sequence and presentation order are not proof
    of what the counterpart saw before composing their current message.
    """
    if isinstance(dialogue, list):
        turns = [item for item in dialogue if isinstance(item, dict)]
        attention = {}
    elif isinstance(dialogue, dict):
        stable = dialogue.get("stable_turns")
        turns = [item for item in stable if isinstance(item, dict)] if isinstance(stable, list) else []
        volatile = dialogue.get("volatile_last_turn")
        if isinstance(volatile, dict):
            turns.append(volatile)
        attention = dialogue.get("per_turn_attention")
    else:
        turns, attention = [], {}
    attention = attention if isinstance(attention, dict) else {}

    def current_marker(item):
        ref = item.get("dialogue_id")
        reasons = item.get("continuity_reasons", ())
        mapped = attention.get(ref, ()) if isinstance(ref, str) else ()
        return ((isinstance(reasons, (list, tuple)) and "current_turn" in reasons)
                or (isinstance(mapped, (list, tuple)) and "current_turn" in mapped))

    candidates = [item for item in turns if item.get("speaker") == "counterpart" and current_marker(item)]
    current = candidates[0] if len(candidates) == 1 else None
    current_ref = current.get("dialogue_id") if current else None
    if not isinstance(current_ref, str) or not current_ref:
        current_ref = None
        current = None
    observed_at = current.get("occurred_at") if current else None
    current_second = _recorded_second(observed_at)
    comparisons = []
    unidentified_count = 0
    previous_counterpart_seconds: list[datetime] = []
    earlier_companion_seconds: list[datetime] = []
    counterpart_local_hours: set[int] = set()
    for item in turns:
        if item.get("speaker") == "counterpart":
            peer_second = _recorded_second(item.get("occurred_at"))
            if peer_second is not None:
                counterpart_local_hours.add(_local_hour(peer_second))
                if item is not current and current_second is not None and peer_second <= current_second:
                    previous_counterpart_seconds.append(peer_second)
            continue
        if item.get("speaker") != "companion":
            continue
        ref = item.get("dialogue_id")
        if not isinstance(ref, str) or not ref:
            unidentified_count += 1
            continue
        state = item.get("delivery_state")
        receipt_second = (_recorded_second(item.get("occurred_at"))
                          if isinstance(state, str) and state in {"provider_accepted", "delivered"}
                          else None)
        if receipt_second is not None and current_second is not None and receipt_second < current_second:
            earlier_companion_seconds.append(receipt_second)
        if current_second is None or receipt_second is None:
            relation = "timestamp_unknown"
        elif receipt_second == current_second:
            relation = "same_second_uncertain"
        elif receipt_second >= current_second + timedelta(seconds=1):
            relation = "role_receipt_recorded_after_observation_second"
        else:
            continue
        comparisons.append({"dialogue_ref": ref, "relation": relation})
    seconds_since_previous_counterpart_turn = (
        int((current_second - max(previous_counterpart_seconds)).total_seconds())
        if current_second is not None and previous_counterpart_seconds
        else None
    )
    seconds_since_latest_companion_turn = (
        int((current_second - max(earlier_companion_seconds)).total_seconds())
        if current_second is not None and earlier_companion_seconds
        else None
    )
    timing_view = {
        "contract": "dialogue-observation-timing.1",
        "current_ref": current_ref,
        "observation_recorded_at": observed_at if current_second is not None else None,
        "comparison_precision": "one_second_conservative" if current_second is not None else "unknown",
        "relation_scope": "observation_and_provider_receipt_only",
        "comparisons": comparisons,
        "unidentified_companion_records": unidentified_count,
        "instruction": (
            "这里比较的是消息观察时间与角色消息的平台回执时间，统一按秒保守比较；"
            "展示顺序或回执不能证明对方在写当前消息时已经看到、读到或回应过这些话。"
            "同秒、时钟差异和缺失时间均可能使先后不确定；角色话语及其记录的投递状态均保留。"
        ),
    }
    if current_second is not None:
        timing_view["counterpart_turn_local_hour"] = _local_hour(current_second)
        if seconds_since_previous_counterpart_turn is not None:
            timing_view["seconds_since_previous_counterpart_turn"] = (
                seconds_since_previous_counterpart_turn
            )
            timing_view["since_his_previous_message_human"] = _format_duration_human(
                seconds_since_previous_counterpart_turn
            )
        if seconds_since_latest_companion_turn is not None:
            timing_view["seconds_since_latest_companion_turn"] = (
                seconds_since_latest_companion_turn
            )
            timing_view["since_my_last_message_human"] = _format_duration_human(
                seconds_since_latest_companion_turn
            )
        prev_cp_sec = max(previous_counterpart_seconds) if previous_counterpart_seconds else None
        unanswered_companion_turns = [
            ts for ts in earlier_companion_seconds if prev_cp_sec is None or ts > prev_cp_sec
        ]
        if unanswered_companion_turns:
            timing_view["unanswered_companion_turn_count"] = len(unanswered_companion_turns)
            unans_wait = int((current_second - min(unanswered_companion_turns)).total_seconds())
            if unans_wait > 0:
                timing_view["unanswered_wait_human"] = _format_duration_human(unans_wait)
        if previous_counterpart_seconds:
            cp_sorted = sorted(set(previous_counterpart_seconds))
            if (current_second - cp_sorted[-1]).total_seconds() <= 300:
                burst_idx = len(cp_sorted) - 1
                while (
                    burst_idx > 0
                    and (cp_sorted[burst_idx] - cp_sorted[burst_idx - 1]).total_seconds() <= 300
                ):
                    burst_idx -= 1
                burst_start = cp_sorted[burst_idx]
                if (
                    burst_idx > 0
                    and (burst_start - cp_sorted[burst_idx - 1]).total_seconds() >= 1800
                ):
                    pre_burst_cp = cp_sorted[burst_idx - 1]
                    pre_silence_sec = int((burst_start - pre_burst_cp).total_seconds())
                    timing_view["silence_before_return_human"] = _format_duration_human(
                        pre_silence_sec
                    )
                    pre_unans = [
                        ts for ts in earlier_companion_seconds if pre_burst_cp < ts < burst_start
                    ]
                    if pre_unans:
                        timing_view["unanswered_companion_turns_before_return"] = len(pre_unans)
                        timing_view["unanswered_wait_before_return_human"] = (
                            _format_duration_human(
                                int((burst_start - min(pre_unans)).total_seconds())
                            )
                        )
        if len(counterpart_local_hours) >= 2:
            timing_view["observed_counterpart_local_hours"] = sorted(
                counterpart_local_hours
            )
        all_turn_seconds = [*previous_counterpart_seconds, *earlier_companion_seconds, current_second]
        recent_30m = sum(
            1 for ts in all_turn_seconds if 0 <= int((current_second - ts).total_seconds()) <= 1800
        )
        if recent_30m >= 4:
            timing_view["recent_30m_turn_count"] = recent_30m
    return timing_view


def present_dialogue_observation_timing(packet):
    """Attach optional timing context without changing or trimming the snapshot."""
    result = dict(packet)
    snapshot = packet.get("inner_life_snapshot")
    materials = snapshot.get("materials") if isinstance(snapshot, dict) else None
    if isinstance(materials, dict) and materials.get("contract") == "shared-string-view.1":
        materials = unpack_shared_strings(materials)
    dialogue = materials.get("recent_dialogue") if isinstance(materials, dict) else None
    correction = result.pop("role_result_correction", None)
    had_correction = "role_result_correction" in packet
    result["dialogue_observation_timing"] = _dialogue_observation_timing(dialogue)
    if had_correction:
        result["role_result_correction"] = correction
    return result


def bounded_inbound_context(packet, *, include_delivery_overlap=False):
    result = deepcopy(packet)
    snapshot = result.get("inner_life_snapshot")
    if not isinstance(snapshot, dict):
        return result
    materials = snapshot.get("materials")
    if isinstance(materials, dict) and materials.get("contract") == "shared-string-view.1":
        materials = unpack_shared_strings(materials)
        snapshot["materials"] = materials
    if not isinstance(materials, dict):
        return result
    omitted = {}
    dialogue = materials.get("recent_dialogue")
    dialogue_seconds: list[datetime] = []
    settled_counterpart_seconds: list[datetime] = []
    companion_seconds: list[datetime] = []
    seq_companion_after_cp: dict[datetime, int] = {}
    all_texts_by_id: dict[str, str] = {}
    if isinstance(dialogue, list):
        all_texts_by_id = {
            ref_id: row["text"].strip()
            for row in dialogue
            if isinstance(row, dict)
            for ref_id in (row.get("dialogue_id") or row.get("source_ref"),)
            if isinstance(ref_id, str)
            and isinstance(row.get("text"), str)
            and row["text"].strip()
        }
        dialogue_seconds = [
            ts
            for r in dialogue
            if isinstance(r, dict)
            for ts in (_recorded_second(r.get("occurred_at")),)
            if ts is not None
        ]
        settled_counterpart_seconds = [
            ts
            for r in dialogue
            if isinstance(r, dict)
            and r.get("speaker") == "counterpart"
            and "current_turn" not in (r.get("continuity_reasons") or ())
            for ts in (_recorded_second(r.get("occurred_at")),)
            if ts is not None
        ]
        companion_seconds = [
            ts
            for r in dialogue
            if isinstance(r, dict) and r.get("speaker") == "companion"
            for ts in (_recorded_second(r.get("occurred_at")),)
            if ts is not None
        ]
        for idx_r, r in enumerate(dialogue):
            if (
                isinstance(r, dict)
                and r.get("speaker") == "counterpart"
                and "current_turn" not in (r.get("continuity_reasons") or ())
            ):
                ts = _recorded_second(r.get("occurred_at"))
                if ts is not None:
                    seq_companion_after_cp[ts] = sum(
                        1
                        for later_r in dialogue[idx_r + 1 :]
                        if isinstance(later_r, dict) and later_r.get("speaker") == "companion"
                    )
    if isinstance(dialogue, dict) and isinstance(dialogue.get("stable_turns"), list):
        rows = dialogue["stable_turns"]
        own = [i for i, row in enumerate(rows) if isinstance(row, dict) and row.get("speaker") == "companion"]
        max_own = (
            MAX_OVERLAP_COMPANION_UTTERANCES
            if include_delivery_overlap
            else MAX_COMPANION_UTTERANCES
        )
        keep = set(own[-max_own:])
        own_indices = set(own)
        volatile_turn = dialogue.get("volatile_last_turn")
        all_dialogue_rows = [*rows, volatile_turn] if isinstance(volatile_turn, dict) else list(rows)
        all_texts_by_id = {
            ref_id: row["text"].strip()
            for row in all_dialogue_rows
            if isinstance(row, dict)
            for ref_id in (row.get("dialogue_id") or row.get("source_ref"),)
            if isinstance(ref_id, str)
            and isinstance(row.get("text"), str)
            and row["text"].strip()
        }
        dropped_texts_by_id = {
            row["dialogue_id"]: row["text"].strip()
            for i, row in enumerate(rows)
            if i in own_indices
            and i not in keep
            and isinstance(row.get("dialogue_id"), str)
            and isinstance(row.get("text"), str)
            and row["text"].strip()
        }
        dialogue["stable_turns"] = [row for i, row in enumerate(rows) if i not in own_indices or i in keep]
        omitted["older_companion_utterances"] = len(own) - len(keep)
        if all_texts_by_id:
            conversation = materials.get("conversation")
            if isinstance(conversation, dict) and isinstance(conversation.get("entries"), list):
                restored_entries = []
                for entry in conversation["entries"]:
                    if (
                        isinstance(entry, dict)
                        and entry.get("source_ref") in all_texts_by_id
                        and isinstance(entry.get("display_prefix"), str)
                    ):
                        ref_id = entry["source_ref"]
                        prefix = entry["display_prefix"]
                        text_val = all_texts_by_id[ref_id]
                        line_text = prefix + text_val
                        if ref_id in dropped_texts_by_id:
                            if "天前" in prefix and not (
                                text_val.startswith("[一张") or text_val.startswith("[发了")
                            ):
                                continue
                            restored_entries.append({"line": line_text})
                        else:
                            restored_entries.append({"source_ref": ref_id, "line": line_text})
                    elif (
                        isinstance(entry, dict)
                        and "source_ref" not in entry
                        and isinstance(entry.get("line"), str)
                        and entry["line"].startswith("我（")
                        and "天前）：" in entry["line"]
                        and not ("）：[一张" in entry["line"] or "）：[发了" in entry["line"])
                    ):
                        continue
                    else:
                        restored_entries.append(entry)
                conversation["entries"] = restored_entries
                live_lines = [
                    e["line"]
                    for e in restored_entries
                    if isinstance(e, dict) and isinstance(e.get("line"), str)
                ][-12:]
                cur_msg = result.get("current_trigger_message")
                if isinstance(cur_msg, dict) and live_lines:
                    result["current_trigger_message"] = {
                        "live_chat_window": live_lines,
                        **cur_msg,
                    }
        volatile_turn = dialogue.get("volatile_last_turn")
        all_dialogue_rows = [*rows, volatile_turn] if isinstance(volatile_turn, dict) else list(rows)
        dialogue_seconds = [
            ts
            for r in all_dialogue_rows
            if isinstance(r, dict)
            for ts in (_recorded_second(r.get("occurred_at")),)
            if ts is not None
        ]
        settled_counterpart_seconds = [
            ts
            for r in rows
            if isinstance(r, dict)
            and r.get("speaker") == "counterpart"
            and "current_turn" not in (r.get("continuity_reasons") or ())
            for ts in (_recorded_second(r.get("occurred_at")),)
            if ts is not None
        ]
        companion_seconds = [
            ts
            for r in all_dialogue_rows
            if isinstance(r, dict) and r.get("speaker") == "companion"
            for ts in (_recorded_second(r.get("occurred_at")),)
            if ts is not None
        ]
        for idx_r, r in enumerate(all_dialogue_rows):
            if (
                isinstance(r, dict)
                and r.get("speaker") == "counterpart"
                and "current_turn" not in (r.get("continuity_reasons") or ())
            ):
                ts = _recorded_second(r.get("occurred_at"))
                if ts is not None:
                    seq_companion_after_cp[ts] = sum(
                        1
                        for later_r in all_dialogue_rows[idx_r + 1 :]
                        if isinstance(later_r, dict) and later_r.get("speaker") == "companion"
                    )

    def _companion_turns_after(cp_ts: datetime) -> int:
        ts_count = sum(1 for c_ts in companion_seconds if c_ts > cp_ts)
        seq_count = seq_companion_after_cp.get(cp_ts, 0)
        return max(ts_count, seq_count)
    impressions = materials.get("private_impressions")
    if settled_counterpart_seconds and isinstance(impressions, list):
        latest_settled_counterpart_at = max(settled_counterpart_seconds)
        latest_dialogue_at = (
            max(dialogue_seconds) if dialogue_seconds else latest_settled_counterpart_at
        )
        earliest_dialogue_at = min(dialogue_seconds) if dialogue_seconds else None
        kept_impressions = []
        for imp in impressions:
            if not isinstance(imp, dict):
                kept_impressions.append(imp)
                continue
            expiry = imp.get("expiry_condition")
            imp_time = _recorded_second(imp.get("last_supported") or imp.get("first_seen"))
            subject_ref = imp.get("subject_ref")
            conf_bp = imp.get("confidence_bp") if isinstance(imp.get("confidence_bp"), int) else 0
            matters_bp = (
                imp.get("matters_bp")
                if isinstance(imp.get("matters_bp"), int)
                else (imp.get("salience_bp") if isinstance(imp.get("salience_bp"), int) else 0)
            )
            is_high_salience = conf_bp >= 7500 or matters_bp >= 6500
            refl_text = str(imp.get("reflection_summary") or "")
            is_resolved_silence_complaint = (
                imp_time is not None
                and imp_time < latest_settled_counterpart_at
                and (
                    any(c_ts > imp_time for c_ts in companion_seconds)
                    or _companion_turns_after(imp_time) > 0
                )
                and any(
                    kw in refl_text
                    for kw in ("没下文", "没人接", "还没回", "晾了一整天", "回了个眼睛表情")
                )
            )
            if is_resolved_silence_complaint:
                continue
            if (
                expiry == "until_appraisal_contradicted"
                and not is_high_salience
                and imp_time is not None
                and imp_time < latest_settled_counterpart_at
                and (latest_settled_counterpart_at - imp_time).total_seconds() >= 180
                and (
                    (isinstance(subject_ref, str) and subject_ref != "agent:companion")
                    or (earliest_dialogue_at is not None and imp_time >= earliest_dialogue_at)
                )
            ):
                continue
            if (
                len(impressions) > 1
                and expiry in {"until_appraisal_contradicted", "until_counter_evidence"}
                and not is_high_salience
                and imp_time is not None
                and (latest_dialogue_at - imp_time).total_seconds() >= 12 * 3600
            ):
                continue
            kept_impressions.append(imp)
        if len(kept_impressions) > 2:
            by_subject_counter_ev: dict[str, list[tuple[datetime, int]]] = {}
            for idx_imp, imp_entry in enumerate(kept_impressions):
                if (
                    isinstance(imp_entry, dict)
                    and imp_entry.get("expiry_condition") in {
                        "until_counter_evidence",
                        "until_relationship_stage_changes",
                        "until_appraisal_contradicted",
                    }
                    and isinstance(imp_entry.get("subject_ref"), str)
                ):
                    t_imp = _recorded_second(
                        imp_entry.get("last_supported") or imp_entry.get("first_seen")
                    ) or datetime.min.replace(tzinfo=_DEFAULT_LOCAL_TZ)
                    by_subject_counter_ev.setdefault(imp_entry["subject_ref"], []).append(
                        (t_imp, idx_imp)
                    )
            drop_counter_ev_indices: set[int] = set()
            for subj_items in by_subject_counter_ev.values():
                if len(subj_items) > 1:
                    ordered_subj = sorted(subj_items, key=lambda pair: (pair[0], pair[1]))
                    for _, drop_idx in ordered_subj[:-1]:
                        drop_counter_ev_indices.add(drop_idx)
            if drop_counter_ev_indices:
                kept_impressions = [
                    imp_entry
                    for idx_imp, imp_entry in enumerate(kept_impressions)
                    if idx_imp not in drop_counter_ev_indices
                ]
        if len(kept_impressions) < len(impressions):
            materials["private_impressions"] = kept_impressions
            omitted["superseded_private_impressions"] = len(impressions) - len(kept_impressions)
    appraisals = materials.get("appraisals")
    if isinstance(appraisals, dict) and isinstance(appraisals.get("stable_rows"), list):
        columns = appraisals.get("columns", [])
        rows = list(appraisals["stable_rows"])
        volatile_last = appraisals.get("volatile_last_row")
        has_volatile = isinstance(volatile_last, list)
        if "since" in columns:
            index = columns.index("since")
            readings_idx = columns.index("readings") if "readings" in columns else None
            excerpts_idx = columns.index("excerpts") if "excerpts" in columns else None
            chat_cutoff = (
                max(dialogue_seconds)
                if dialogue_seconds
                else (max(settled_counterpart_seconds) if settled_counterpart_seconds else None)
            )
            settled_counterpart_cutoff = (
                max(settled_counterpart_seconds) if settled_counterpart_seconds else chat_cutoff
            )
            silence_appraisal_cutoff = settled_counterpart_cutoff
            if chat_cutoff is not None and settled_counterpart_seconds:
                cp_sorted = sorted(set(settled_counterpart_seconds))
                if (chat_cutoff - cp_sorted[-1]).total_seconds() <= 300:
                    burst_idx = len(cp_sorted) - 1
                    while (
                        burst_idx > 0
                        and (cp_sorted[burst_idx] - cp_sorted[burst_idx - 1]).total_seconds()
                        <= 300
                        and _companion_turns_after(cp_sorted[burst_idx - 1]) <= 1
                    ):
                        burst_idx -= 1
                    if (
                        burst_idx > 0
                        and (cp_sorted[burst_idx] - cp_sorted[burst_idx - 1]).total_seconds()
                        >= 1800
                        and _companion_turns_after(cp_sorted[burst_idx]) <= 1
                    ):
                        silence_appraisal_cutoff = cp_sorted[burst_idx - 1]

            def _is_superseded_chat_appraisal(row_item: object) -> bool:
                if chat_cutoff is None or not isinstance(row_item, list) or len(row_item) <= index:
                    return False
                since_sec = _recorded_second(row_item[index])
                if since_sec is None or since_sec >= chat_cutoff:
                    return False
                if (
                    excerpts_idx is not None
                    and len(row_item) > excerpts_idx
                    and isinstance(row_item[excerpts_idx], list)
                    and len(row_item[excerpts_idx]) > 0
                ):
                    return True
                if (
                    readings_idx is not None
                    and len(row_item) > readings_idx
                    and isinstance(row_item[readings_idx], list)
                    and row_item[readings_idx]
                ):
                    attributions = [
                        r[1]
                        for r in row_item[readings_idx]
                        if isinstance(r, list) and len(r) > 1 and isinstance(r[1], str)
                    ]
                    if (
                        attributions
                        and all(a in {"user", "unknown"} for a in attributions)
                        and silence_appraisal_cutoff is not None
                        and since_sec <= silence_appraisal_cutoff
                    ):
                        return True
                return False

            def _is_offline_only_appraisal(row_item: object) -> bool:
                if (
                    not isinstance(row_item, list)
                    or readings_idx is None
                    or len(row_item) <= readings_idx
                    or not isinstance(row_item[readings_idx], list)
                    or not row_item[readings_idx]
                ):
                    return False
                attributions = [
                    r[1]
                    for r in row_item[readings_idx]
                    if isinstance(r, list) and len(r) > 1 and isinstance(r[1], str)
                ]
                return bool(attributions) and all(a in {"npc", "world", "self"} for a in attributions)

            combined = rows + ([volatile_last] if has_volatile else [])
            try:
                ordered = sorted(
                    range(len(combined)),
                    key=lambda i: datetime.fromisoformat(str(combined[i][index])),
                )
            except (TypeError, ValueError, IndexError):
                ordered = list(range(len(combined)))
            kept_ordered = [
                i for i in ordered if not _is_superseded_chat_appraisal(combined[i])
            ]
            offline_indices = [i for i in kept_ordered if _is_offline_only_appraisal(combined[i])]
            user_indices = [i for i in kept_ordered if not _is_offline_only_appraisal(combined[i])]
            if user_indices and len(offline_indices) > 2:
                drop_offline = set(offline_indices[:-2])
                kept_ordered = [i for i in kept_ordered if i not in drop_offline]
            if has_volatile:
                if kept_ordered:
                    newest_idx = kept_ordered[-1]
                    appraisals["volatile_last_row"] = combined[newest_idx]
                    keep = set(kept_ordered[:-1][-4:])
                    appraisals["stable_rows"] = [
                        combined[i] for i in range(len(combined)) if i != newest_idx and i in keep
                    ]
                else:
                    appraisals.pop("volatile_last_row", None)
                    appraisals["stable_rows"] = []
                omitted["older_appraisal_narratives"] = max(
                    0, len(combined) - (1 if "volatile_last_row" in appraisals else 0) - len(appraisals["stable_rows"])
                )
            else:
                keep = set(kept_ordered[-4:])
                appraisals["stable_rows"] = [row for i, row in enumerate(rows) if i in keep]
                omitted["older_appraisal_narratives"] = len(rows) - len(keep)
    # Retain every current affect component and its semantic state, while the
    # original snapshot/receipt keeps the verbose proof reference tables.
    stable_self = materials.get("stable_self")
    if isinstance(stable_self, list):
        for item in stable_self:
            if isinstance(item, dict):
                item.pop("slow_evolving", None)
    affect = materials.get("affect")
    if isinstance(affect, dict):
        entries = list(affect.get("stable_entries", []))
        if isinstance(affect.get("volatile_last_entry"), dict):
            entries.append(affect["volatile_last_entry"])
        for entry in entries:
            if isinstance(entry, dict):
                for component in entry.get("components", []):
                    if isinstance(component, dict):
                        component.pop("appraisal_refs", None)
    relationships = materials.get("relationship")
    if isinstance(relationships, list):
        for rel in relationships:
            if isinstance(rel, dict):
                rel.pop("recent_authored_signals", None)
                rel.pop("hysteresis", None)
    npc_relationships = materials.get("protagonist_npc_relationships")
    if isinstance(npc_relationships, list):
        for npc_rel in npc_relationships:
            if isinstance(npc_rel, dict):
                npc_rel.pop("recent_authored_signals", None)
                npc_rel.pop("variables", None)
                npc_rel.pop("temperature", None)
                npc_rel.pop("hysteresis", None)
    since_spoke = materials.get("since_he_last_spoke")
    if isinstance(since_spoke, dict):
        prior_sec = since_spoke.get("prior_silence_seconds")
        if isinstance(prior_sec, int) and not isinstance(prior_sec, bool) and prior_sec >= 60:
            since_spoke["prior_silence_human"] = _format_duration_human(prior_sec)
        wait_sec = since_spoke.get("unanswered_companion_wait_seconds")
        if isinstance(wait_sec, int) and not isinstance(wait_sec, bool) and wait_sec >= 60:
            since_spoke["unanswered_wait_human"] = _format_duration_human(wait_sec)
        if (
            isinstance(prior_sec, int)
            and not isinstance(prior_sec, bool)
            and prior_sec <= 300
            and len(settled_counterpart_seconds) >= 2
        ):
            cp_sorted = sorted(set(settled_counterpart_seconds))
            burst_idx = len(cp_sorted) - 1
            while (
                burst_idx > 0
                and (cp_sorted[burst_idx] - cp_sorted[burst_idx - 1]).total_seconds() <= 300
                and _companion_turns_after(cp_sorted[burst_idx - 1]) <= 1
            ):
                burst_idx -= 1
            if (
                burst_idx > 0
                and (cp_sorted[burst_idx] - cp_sorted[burst_idx - 1]).total_seconds() >= 1800
                and _companion_turns_after(cp_sorted[burst_idx]) <= 1
            ):
                pre_silence_sec = int(
                    (cp_sorted[burst_idx] - cp_sorted[burst_idx - 1]).total_seconds()
                )
                since_spoke["silence_before_return_human"] = _format_duration_human(
                    pre_silence_sec
                )
    lived_moment = materials.get("lived_moment")
    _INNER_MOMENT_PREFIXES = (
        "此刻身边正发生：",
        "此前个人打算：",
        "刚经历：",
        "近期连续状态轨迹：",
        "当下环境：",
        "眼前环境余光：",
        "近期活法与心气：",
        "接下来自己的安排：",
        "心里记挂的个人心愿/打算：",
        "近期身上悬着的处境：",
        "脑海顺带闪过的旧事：",
        "心里记挂他的近况：",
        "手边刚刷到的闲事：",
        "被勾起的你们旧话头/小梗：",
        "今日近期课表/日程：",
        "此刻情绪余波：",
        "此刻心里的关系拉扯：",
        "当前状态负荷：",
        "已连续清醒",
    )
    has_backed_current_activity = bool(materials.get("current_activities"))
    if isinstance(lived_moment, str):
        raw_clauses = [
            clause.strip()
            for part in lived_moment.split("。")
            for clause in part.split("；")
            if clause.strip()
        ]
        all_clauses = [
            f"接下来自己的安排：{clause.removeprefix('此刻正在：').strip()}（当前时段个人打算，仅意图未作既成事实）"
            if has_backed_current_activity
            and clause.startswith("此刻正在：")
            and clause.removeprefix("此刻正在：").strip()
            else clause
            for clause in raw_clauses
        ]
        moment_parts = [
            clause
            for clause in all_clauses
            if clause.startswith(
                (
                    "此刻身边正发生：",
                    "此前个人打算：",
                    "刚经历：",
                    "近期连续状态轨迹：",
                    "当下环境：",
                    "眼前环境余光：",
                    "近期活法与心气：",
                    "接下来自己的安排：",
                    "心里记挂的个人心愿/打算：",
                    "近期身上悬着的处境：",
                    "脑海顺带闪过的旧事：",
                    "心里记挂他的近况：",
                    "手边刚刷到的闲事：",
                    "被勾起的你们旧话头/小梗：",
                    "此刻情绪余波：",
                    "此刻心里的关系拉扯：",
                    "今日近期课表/日程：",
                    "当前状态负荷：",
                    "已连续清醒",
                )
            )
        ]
        if moment_parts:
            materials["lived_moment"] = "。".join(moment_parts) + "。"
            inner_parts = [
                p for p in all_clauses if p.startswith(_INNER_MOMENT_PREFIXES)
            ]
            cur_msg = result.get("current_trigger_message")
            if isinstance(cur_msg, dict) and inner_parts:
                result["current_trigger_message"] = {
                    **cur_msg,
                    "current_inner_moment": "；".join(inner_parts),
                }
        else:
            materials.pop("lived_moment", None)
    elif isinstance(lived_moment, dict):
        cur_mom = lived_moment.get("current_moment")
        if isinstance(cur_mom, str):
            raw_clauses = [
                clause.strip()
                for part in cur_mom.split("。")
                for clause in part.split("；")
                if clause.strip()
            ]
            all_clauses = [
                f"接下来自己的安排：{clause.removeprefix('此刻正在：').strip()}（当前时段个人打算，仅意图未作既成事实）"
                if has_backed_current_activity
                and clause.startswith("此刻正在：")
                and clause.removeprefix("此刻正在：").strip()
                else clause
                for clause in raw_clauses
            ]
            embodied_clauses = [
                clause
                for clause in all_clauses
                if not clause.startswith(("此刻正在：", "手头正忙：", "心里还搁着"))
            ]
            if embodied_clauses:
                joined_moment = "；".join(embodied_clauses)
                lived_moment["current_moment"] = joined_moment
                inner_parts = [
                    p for p in all_clauses if p.startswith(_INNER_MOMENT_PREFIXES)
                ]
                cur_msg = result.get("current_trigger_message")
                if isinstance(cur_msg, dict) and inner_parts:
                    result["current_trigger_message"] = {
                        **cur_msg,
                        "current_inner_moment": "；".join(inner_parts),
                    }
            else:
                lived_moment.pop("current_moment", None)
        else:
            lived_moment.pop("current_moment", None)
    photos_shared = materials.get("photos_i_shared")
    if isinstance(photos_shared, list):
        deictic_fixed_photos = []
        for p_item in photos_shared:
            if (
                isinstance(p_item, dict)
                and isinstance(p_item.get("when"), str)
                and p_item["when"].endswith("天前")
            ):
                p_copy = dict(p_item)
                for p_field in ("about", "line"):
                    p_val = p_copy.get(p_field)
                    if isinstance(p_val, str) and "昨天" in p_val:
                        p_copy[p_field] = p_val.replace("昨天", f"{p_item['when']}之前的")
                deictic_fixed_photos.append(p_copy)
            else:
                deictic_fixed_photos.append(p_item)
        materials["photos_i_shared"] = deictic_fixed_photos
    perception_items = materials.get("perception")
    if isinstance(perception_items, list):
        latest_chat_sec = max(dialogue_seconds) if dialogue_seconds else None
        kept_perception = []
        fallback_external_perception = []
        for p_item in perception_items:
            if not isinstance(p_item, dict):
                kept_perception.append(p_item)
                continue
            acc_sec = _recorded_second(p_item.get("accepted_at"))
            if (
                latest_chat_sec is not None
                and acc_sec is not None
                and (latest_chat_sec - acc_sec).total_seconds() > 6 * 3600
            ):
                if (
                    len(fallback_external_perception) < 2
                    and (latest_chat_sec - acc_sec).total_seconds() <= 48 * 3600
                    and (
                        p_item.get("headline")
                        or p_item.get("subjective_summary")
                        or p_item.get("licensed_summary")
                    )
                ):
                    fallback_external_perception.append(p_item)
                continue
            kept_perception.append(p_item)
        if not kept_perception and fallback_external_perception:
            kept_perception = fallback_external_perception
        if kept_perception:
            materials["perception"] = kept_perception
        else:
            materials.pop("perception", None)
    raw_logical_time = (
        (snapshot.get("cursor") or {}).get("logical_time")
        if isinstance(snapshot.get("cursor"), dict)
        else snapshot.get("logical_time")
    )
    _enrich_relevant_facts_temporal_scope(materials.get("relevant_facts"), raw_logical_time)
    auto_pre = materials.get("automatic_prefetch")
    if isinstance(auto_pre, dict) and isinstance(auto_pre.get("items"), list):
        latest_settled_cp = max(settled_counterpart_seconds) if settled_counterpart_seconds else None
        visible_dialogue_refs = set(all_texts_by_id.keys())
        visible_dialogue_texts = set(all_texts_by_id.values())
        visible_fact_refs = {
            ref
            for f in (materials.get("relevant_facts") or ())
            if isinstance(f, dict)
            for ref in (f.get("source_ref") or f.get("fact_id"),)
            if isinstance(ref, str) and ref
        }
        visible_appraisal_refs: set[str] = set()
        cur_appraisals = materials.get("appraisals")
        if isinstance(cur_appraisals, dict) and isinstance(cur_appraisals.get("columns"), list):
            app_cols = cur_appraisals["columns"]
            if "ref" in app_cols:
                r_idx = app_cols.index("ref")
                app_rows = list(cur_appraisals.get("stable_rows") or [])
                if isinstance(cur_appraisals.get("volatile_last_row"), list):
                    app_rows.append(cur_appraisals["volatile_last_row"])
                visible_appraisal_refs = {
                    row[r_idx]
                    for row in app_rows
                    if isinstance(row, list) and len(row) > r_idx and isinstance(row[r_idx], str)
                }
        elif isinstance(cur_appraisals, list):
            visible_appraisal_refs = {
                a["source_ref"]
                for a in cur_appraisals
                if isinstance(a, dict) and isinstance(a.get("source_ref"), str)
            }
        visible_impression_refs = {
            imp["source_ref"]
            for imp in (materials.get("private_impressions") or ())
            if isinstance(imp, dict) and isinstance(imp.get("source_ref"), str)
        }
        visible_understanding_refs = {
            u["source_ref"]
            for u in (materials.get("memory_understandings") or ())
            if isinstance(u, dict) and isinstance(u.get("source_ref"), str)
        }
        recalled_boost_refs = _collect_recalled_boost_refs(materials)
        kept_auto_items = []
        non_dup_auto_items = []
        for item in auto_pre["items"]:
            if not isinstance(item, dict):
                kept_auto_items.append(item)
                non_dup_auto_items.append(item)
                continue
            src_ref = item.get("source_ref")
            is_read_boosted = (
                (isinstance(src_ref, str) and src_ref in recalled_boost_refs)
                or (isinstance(item.get("recall_count"), int) and item["recall_count"] > 0)
            )
            occ_to = _recorded_second(item.get("occurred_to") or item.get("occurred_from"))
            if item.get("source_slice") == "private_impressions" and latest_settled_cp is not None:
                imp_bp = item.get("importance_bp") if isinstance(item.get("importance_bp"), int) else 0
                raw_txt = str(item.get("text") or "")
                if (
                    occ_to is not None
                    and occ_to < latest_settled_cp
                    and any(c_ts > occ_to for c_ts in companion_seconds)
                    and any(
                        kw in raw_txt
                        for kw in ("没下文", "没人接", "还没回", "晾了一整天", "回了个眼睛表情")
                    )
                ):
                    continue
                if (
                    not is_read_boosted
                    and occ_to is not None
                    and occ_to < latest_settled_cp
                    and (latest_settled_cp - occ_to).total_seconds() >= 180
                    and imp_bp < 6500
                ):
                    continue
            text_val = item.get("text")
            src_slice = item.get("source_slice")
            if (
                not is_read_boosted
                and src_slice == "recalled_emotional_associations"
                and latest_settled_cp is not None
                and occ_to is not None
                and occ_to <= latest_settled_cp
                and (latest_settled_cp - occ_to).total_seconds() <= 6 * 3600
                and isinstance(src_ref, str)
                and src_ref not in visible_appraisal_refs
            ):
                continue
            if isinstance(text_val, str) and (
                src_slice == "recalled_emotional_associations"
                or text_val.startswith("Remembered private interpretation — ")
            ):
                text_val = _clean_prefetch_appraisal_text(text_val)
                item["text"] = text_val
            if isinstance(text_val, str) and (
                (isinstance(src_ref, str) and src_ref.startswith("dialogue-episode:"))
                or text_val.startswith("一段聊天：")
            ):
                ep_bodies = _episode_line_bodies(text_val)
                if ep_bodies and not any(_has_substantive_word_chars(b) for b in ep_bodies):
                    continue
            else:
                ep_bodies = []
            is_working_mem_dup = False
            if isinstance(src_ref, str) and (
                src_ref in visible_fact_refs
                or src_ref in visible_appraisal_refs
                or src_ref in visible_dialogue_refs
                or src_ref in visible_impression_refs
                or src_ref in visible_understanding_refs
                or (
                    src_ref.startswith("dialogue-episode:")
                    and src_ref.removeprefix("dialogue-episode:") in visible_dialogue_refs
                )
            ):
                is_working_mem_dup = True
            elif (
                src_slice == "recent_dialogue"
                and isinstance(text_val, str)
                and (
                    text_val.strip() in visible_dialogue_texts
                    or (ep_bodies and all(b in visible_dialogue_texts for b in ep_bodies))
                )
            ):
                is_working_mem_dup = True
            kept_auto_items.append(item)
            if not is_working_mem_dup:
                non_dup_auto_items.append(item)
        final_auto_items = non_dup_auto_items if non_dup_auto_items else kept_auto_items[:1]
        if recalled_boost_refs:
            boosted_front = [
                it
                for it in final_auto_items
                if isinstance(it, dict)
                and (
                    (isinstance(it.get("source_ref"), str) and it["source_ref"] in recalled_boost_refs)
                    or (isinstance(it.get("recall_count"), int) and it["recall_count"] > 0)
                )
            ]
            rest_auto = [it for it in final_auto_items if it not in boosted_front]
            final_auto_items = boosted_front + rest_auto
        auto_pre["items"] = final_auto_items
    from .contracts import chat_disclosure_ledger_scope, lookup_chat_disclosure_ledger

    with chat_disclosure_ledger_scope(
        lookup_chat_disclosure_ledger(
            world_id=snapshot.get("world_id") if isinstance(snapshot, dict) else None,
            cursor=snapshot.get("cursor") if isinstance(snapshot, dict) else None,
        )
    ):
        shortcuts = _citable_memory_shortcuts(
            materials,
            result.get("expression_hard_boundaries"),
            logical_time=(snapshot.get("cursor") or {}).get("logical_time") if isinstance(snapshot.get("cursor"), dict) else None,
        )
        memory_tree = _build_memory_tree_index(
            materials,
            shortcuts,
            boundaries=result.get("expression_hard_boundaries"),
            logical_time=raw_logical_time,
        )
        _annotate_unshared_materials_and_trigger(result, materials)
    has_correction = "role_result_correction" in result
    correction = result.pop("role_result_correction", None)
    if include_delivery_overlap:
        timing = _dialogue_observation_timing(materials.get("recent_dialogue"))
        result["dialogue_observation_timing"] = timing
    result["context_window"] = {
        "contract": "inbound-narrative-window.2" if include_delivery_overlap else CONTRACT,
        "scope": "model_presentation_only_not_deletion_or_complete_history",
        "omitted": omitted,
        **({"citable_memory_shortcuts": shortcuts} if shortcuts else {}),
        **({"memory_tree": memory_tree} if memory_tree else {}),
        "instruction": (
            "这是本轮展示窗口，不是全部历史。旧发言和旧解释仍可检索；没展示不证明没发生。"
            "当前情绪、关系、事实和未完成事项仍保留，如何回应由你决定。"
        ),
    }
    if has_correction:
        result["role_result_correction"] = correction
    return result


def _annotate_unshared_materials_and_trigger(result: dict, materials: dict) -> None:
    """Stamp shared_in_chat=True/False on coordinates/experiences/recall items and surface epistemic mismatch if trigger names unshared ones."""
    from .contracts import (
        _CJK_PARTICLES,
        _GENERIC_CHAT_BIGRAMS,
        _extract_companion_chat_texts,
        _has_disclosure_ledger,
        _is_unshared_in_companion_dialogue,
        _lookup_disclosed_claim,
    )

    companion_texts = _extract_companion_chat_texts(materials)
    has_inline_ledger = bool(materials.get("chat_disclosure_ledger"))
    if not companion_texts and not has_inline_ledger:
        return
    unshared_texts: list[str] = []
    bio = materials.get("biographical_context")
    bio_dict = (
        next((item for item in bio if isinstance(item, dict)), None)
        if isinstance(bio, list)
        else bio
        if isinstance(bio, dict)
        else None
    )
    if isinstance(bio_dict, dict):
        coords = bio_dict.get("settled_biographical_coordinates")
        if isinstance(coords, list):
            for coord in coords:
                if not isinstance(coord, dict):
                    continue
                summary = str(coord.get("summary") or "").strip()
                if not summary:
                    continue
                disclosed = _lookup_disclosed_claim(
                    (
                        coord.get("coordinate_ref"),
                        coord.get("settlement_event_ref"),
                        coord.get("source_ref"),
                    ),
                    materials=materials,
                    coordinate_summary=summary,
                )
                if disclosed is not None:
                    coord["shared_in_chat"] = True
                    coord["previously_shared_claim"] = disclosed["claim_text"][:72]
                    coord.pop("counterpart_prior_knowledge", None)
                    disclosed_corpus = [*companion_texts, disclosed["claim_text"]]
                    for sub_clause in re.split(r"[，。；！？—]+", summary):
                        sub_c = sub_clause.strip()
                        if len(sub_c) >= 4 and _is_unshared_in_companion_dialogue(sub_c, disclosed_corpus):
                            unshared_texts.append(sub_c)
                elif coord.get("shared_in_chat") is False or (
                    coord.get("shared_in_chat") is None
                    and _is_unshared_in_companion_dialogue(summary, companion_texts)
                ):
                    coord["shared_in_chat"] = False
                    coord["counterpart_prior_knowledge"] = "never_mentioned_in_any_prior_chat"
                    unshared_texts.append(summary)
                elif coord.get("shared_in_chat") is None and companion_texts:
                    for sub_clause in re.split(r"[，。；！？—]+", summary):
                        sub_c = sub_clause.strip()
                        if len(sub_c) >= 4 and _is_unshared_in_companion_dialogue(sub_c, companion_texts):
                            unshared_texts.append(sub_c)
    rse = materials.get("recent_self_experiences")
    rse_items = (
        rse.get("items")
        if isinstance(rse, dict) and isinstance(rse.get("items"), list)
        else rse
        if isinstance(rse, list)
        else []
    )
    for exp in rse_items:
        if not isinstance(exp, dict):
            continue
        exp_text = str(
            exp.get("text")
            or exp.get("what_happened")
            or exp.get("summary")
            or ""
        ).strip()
        if not exp_text:
            continue
        disclosed = _lookup_disclosed_claim(
            (exp.get("source_ref"), exp.get("experience_id"), exp.get("occurrence_id")),
            materials=materials,
        )
        if disclosed is not None:
            exp["shared_in_chat"] = True
            exp["previously_shared_claim"] = disclosed["claim_text"][:72]
            exp.pop("counterpart_prior_knowledge", None)
            disclosed_corpus = [*companion_texts, disclosed["claim_text"]]
            for sub_clause in re.split(r"[，。；！？—]+", exp_text):
                sub_c = sub_clause.strip()
                if len(sub_c) >= 4 and _is_unshared_in_companion_dialogue(sub_c, disclosed_corpus):
                    unshared_texts.append(sub_c)
        elif exp.get("shared_in_chat") is False or (
            exp.get("shared_in_chat") is None
            and _is_unshared_in_companion_dialogue(exp_text, companion_texts)
        ):
            exp["shared_in_chat"] = False
            exp["counterpart_prior_knowledge"] = "never_mentioned_in_any_prior_chat"
            unshared_texts.append(exp_text)
        elif exp.get("shared_in_chat") is None and companion_texts:
            unshared_sub_clauses = [
                sub_c
                for sub_clause in re.split(r"[，。；！？—]+", exp_text)
                if len(sub_c := sub_clause.strip()) >= 4
                and _is_unshared_in_companion_dialogue(sub_c, companion_texts)
            ]
            if unshared_sub_clauses:
                unshared_texts.extend(unshared_sub_clauses)

    for recall_slice in (
        (materials.get("automatic_prefetch") or {}).get("items")
        if isinstance(materials.get("automatic_prefetch"), dict)
        else None,
        materials.get("remembered_material")
        if isinstance(materials.get("remembered_material"), list)
        else None,
    ):
        if not isinstance(recall_slice, list):
            continue
        for rec_item in recall_slice:
            if not isinstance(rec_item, dict):
                continue
            ref_candidates: list[object] = [
                rec_item.get("source_ref"),
                rec_item.get("candidate_id"),
            ]
            pre_meta = rec_item.get("prehistory")
            if isinstance(pre_meta, dict) and isinstance(pre_meta.get("story"), dict):
                ref_candidates.append(pre_meta["story"].get("record_ref"))
            excerpts = rec_item.get("source_excerpts")
            if isinstance(excerpts, list):
                for ex in excerpts:
                    if isinstance(ex, dict):
                        ref_candidates.extend(
                            (ex.get("source_id"), ex.get("authority_event_ref"), ex.get("source_ref"))
                        )
            rec_text = str(
                rec_item.get("text")
                or rec_item.get("source_excerpt")
                or (
                    excerpts[0].get("text")
                    if isinstance(excerpts, list) and excerpts and isinstance(excerpts[0], dict)
                    else excerpts[0]
                    if isinstance(excerpts, list) and excerpts and isinstance(excerpts[0], str)
                    else ""
                )
                or ""
            ).strip()
            is_prehistory_rec = (
                "prehistory-record:" in str(rec_item.get("source_ref") or "")
                or isinstance(pre_meta, dict)
            )
            disclosed = _lookup_disclosed_claim(ref_candidates, materials=materials)
            if disclosed is not None:
                rec_item["shared_in_chat"] = True
                rec_item["previously_shared_claim"] = disclosed["claim_text"][:72]
                rec_item.pop("counterpart_prior_knowledge", None)
            elif rec_text and (
                rec_item.get("shared_in_chat") is False
                or (
                    rec_item.get("shared_in_chat") is None
                    and (
                        (is_prehistory_rec and _has_disclosure_ledger(materials))
                        or _is_unshared_in_companion_dialogue(rec_text, companion_texts)
                    )
                )
            ):
                rec_item["shared_in_chat"] = False
                rec_item["counterpart_prior_knowledge"] = "never_mentioned_in_any_prior_chat"

    understandings = materials.get("memory_understandings")
    if isinstance(understandings, list):
        for mu in understandings:
            if not isinstance(mu, dict):
                continue
            mu_sum = str(mu.get("summary") or "").strip()
            if not mu_sum:
                continue
            if mu.get("facet") == "subjective_relationship" and any(
                k in mu_sum for k in ("Geoff", "他回我", "回我消息", "守着别人消息")
            ):
                continue
            if _is_unshared_in_companion_dialogue(mu_sum, companion_texts):
                unshared_texts.append(mu_sum)
            else:
                for sub_s in re.split(r"[。；;！？\n]+", mu_sum):
                    if len(sub_s.strip()) >= 8 and _is_unshared_in_companion_dialogue(
                        sub_s.strip(), companion_texts
                    ):
                        unshared_texts.append(sub_s.strip())
    cur_msg = result.get("current_trigger_message")
    if not isinstance(cur_msg, dict) or not unshared_texts:
        return
    trigger_text = str(cur_msg.get("text") or "").strip()
    if not trigger_text:
        return
    companion_corpus = "\n".join(companion_texts)
    unshared_corpus = "\n".join(unshared_texts)
    matched_terms: list[str] = []
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", trigger_text):
        i = 0
        while i < len(run) - 1:
            if i + 4 <= len(run):
                fg = run[i : i + 4]
                if (
                    fg in unshared_corpus
                    and fg not in companion_corpus
                    and fg[:2] not in _GENERIC_CHAT_BIGRAMS
                ):
                    if fg not in matched_terms:
                        matched_terms.append(fg)
                    i += 4
                    continue
            if i + 3 <= len(run):
                tg = run[i : i + 3]
                if (
                    tg in unshared_corpus
                    and tg not in companion_corpus
                    and tg[:2] not in _GENERIC_CHAT_BIGRAMS
                    and tg[1:] not in _GENERIC_CHAT_BIGRAMS
                ):
                    if tg not in matched_terms:
                        matched_terms.append(tg)
                    i += 3
                    continue
            bg = run[i : i + 2]
            if (
                bg not in _GENERIC_CHAT_BIGRAMS
                and not any(ch in _CJK_PARTICLES for ch in bg)
                and bg in unshared_corpus
                and bg not in companion_corpus
            ):
                if bg not in matched_terms:
                    matched_terms.append(bg)
                i += 2
                continue
            i += 1
    if not matched_terms:
        return
    terms_str = "、".join(matched_terms[:4])
    notice = (
        f"信息差提醒：对方这句直接问起的「{terms_str}」属于你私下经历、"
        "从未在过往聊天里跟他说过的事（shared_in_chat=false），他按理原本无从知晓"
    )
    cur_msg["epistemic_prior_knowledge_mismatch"] = notice
    cur_inner = cur_msg.get("current_inner_moment")
    if isinstance(cur_inner, str) and cur_inner:
        cur_msg["current_inner_moment"] = f"{notice}；{cur_inner}"
    else:
        cur_msg["current_inner_moment"] = notice


def _citable_memory_shortcuts(
    materials: dict,
    boundaries: object,
    *,
    logical_time: object = None,
) -> list[dict[str, object]]:
    """Index already-surfaced memory/fact items to their exact world_claim alias and scopes."""
    if not isinstance(boundaries, dict):
        return []
    aliases = boundaries.get("source_ref_aliases")
    claim_refs = boundaries.get("world_claim_source_refs")
    if not isinstance(aliases, dict) or not isinstance(claim_refs, dict) or not aliases or not claim_refs:
        return []
    now_dt = _recorded_second(logical_time)
    now_local_date = now_dt.astimezone(_DEFAULT_LOCAL_TZ).date() if now_dt is not None else None
    ref_to_alias = {
        canonical: alias
        for alias, canonical in aliases.items()
        if isinstance(alias, str) and isinstance(canonical, str)
    }
    alias_to_scopes: dict[str, list[str]] = {}
    for scope, refs in claim_refs.items():
        if isinstance(scope, str) and isinstance(refs, list):
            for ref in refs:
                if isinstance(ref, str):
                    alias_to_scopes.setdefault(ref, []).append(scope)
    shortcuts: list[dict[str, object]] = []
    seen_aliases: set[str] = set()
    from .contracts import (
        _extract_companion_chat_texts,
        _has_disclosure_ledger,
        _is_unshared_in_companion_dialogue,
        _lookup_disclosed_claim,
    )

    companion_texts = _extract_companion_chat_texts(materials)
    alias_to_canonical = {
        alias: canonical
        for alias, canonical in aliases.items()
        if isinstance(alias, str) and isinstance(canonical, str)
    }
    slices = (
        ("automatic_prefetch", (materials.get("automatic_prefetch") or {}).get("items") if isinstance(materials.get("automatic_prefetch"), dict) else None),
        ("selected_recall", (materials.get("selected_recall") or {}).get("items") if isinstance(materials.get("selected_recall"), dict) else None),
        ("remembered_material", materials.get("remembered_material") if isinstance(materials.get("remembered_material"), list) else None),
        ("recent_self_experiences", (materials.get("recent_self_experiences") or {}).get("items") if isinstance(materials.get("recent_self_experiences"), dict) else None),
        ("perception", materials.get("perception") if isinstance(materials.get("perception"), list) else None),
        ("relevant_facts", materials.get("relevant_facts") if isinstance(materials.get("relevant_facts"), list) else None),
    )
    for slice_name, items in slices:
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            src_ref = item.get("source_ref") or item.get("influence_id")
            alias = (
                src_ref
                if isinstance(src_ref, str) and src_ref in alias_to_scopes
                else (ref_to_alias.get(src_ref) if isinstance(src_ref, str) else None)
            )
            scopes = alias_to_scopes.get(alias or "", []) if alias else []
            if not alias or not scopes or alias in seen_aliases:
                continue
            p_title = item.get("title") or item.get("headline")
            p_sum = (
                item.get("subjective_summary")
                or item.get("licensed_summary")
                or item.get("summary")
                or item.get("content_summary")
            )
            preview = (
                item.get("text")
                or item.get("source_excerpt")
                or (
                    item.get("value", {}).get("text")
                    if isinstance(item.get("value"), dict)
                    else None
                )
                or (
                    item["source_excerpts"][0]
                    if isinstance(item.get("source_excerpts"), list)
                    and item["source_excerpts"]
                    and isinstance(item["source_excerpts"][0], str)
                    else None
                )
                or (
                    f"{p_title} — {p_sum}"
                    if isinstance(p_title, str) and p_title and isinstance(p_sum, str) and p_sum
                    else None
                )
                or p_sum
                or p_title
                or (
                    ((item.get("content") or {}).get("world_consequence") or {})
                    .get("authorized_attempt_result", {})
                    .get("text")
                    if isinstance(item.get("content"), dict)
                    else None
                )
                or (
                    ((item.get("content") or {}).get("world_consequence") or {})
                    .get("environment", {})
                    .get("text")
                    if isinstance(item.get("content"), dict)
                    else None
                )
            )
            if not isinstance(preview, str) or not preview.strip():
                continue
            when_local = None
            raw_ts = item.get("occurred_to") or item.get("occurred_from") or item.get("occurred_at")
            item_dt = _recorded_second(raw_ts)
            if item_dt is not None and now_local_date is not None:
                local_dt = item_dt.astimezone(_DEFAULT_LOCAL_TZ)
                day_diff = (now_local_date - local_dt.date()).days
                if day_diff == 1:
                    when_local = f"昨天({local_dt.strftime('%m-%d %H:%M')})记录，内含『今晚/明早』指当时而非此刻"
                elif day_diff > 1:
                    when_local = f"{day_diff}天前({local_dt.strftime('%Y-%m-%d')})记录，内含『今晚/明早』指当时"
                elif day_diff == 0:
                    when_local = f"今天({local_dt.strftime('%H:%M')})记录"
            canonical_ref = alias_to_canonical.get(alias, src_ref)
            item_ref_candidates: list[object] = [src_ref, canonical_ref]
            if isinstance(item.get("prehistory"), dict) and isinstance(item["prehistory"].get("story"), dict):
                item_ref_candidates.append(item["prehistory"]["story"].get("record_ref"))
            if isinstance(item.get("source_excerpts"), list):
                for ex in item["source_excerpts"]:
                    if isinstance(ex, dict):
                        item_ref_candidates.extend(
                            (ex.get("source_id"), ex.get("authority_event_ref"), ex.get("source_ref"))
                        )
            disclosed_claim = _lookup_disclosed_claim(item_ref_candidates, materials=materials)
            explicit_unshared = (
                disclosed_claim is None
                and (
                    item.get("already_shared_in_chat") is False
                    or item.get("shared_in_chat") is False
                )
            )
            explicit_shared = (
                disclosed_claim is not None
                or item.get("already_shared_in_chat") is True
                or item.get("shared_in_chat") is True
            )
            is_prehistory_item = (
                "prehistory-record:" in str(canonical_ref or src_ref or "")
                or isinstance(item.get("prehistory"), dict)
            )
            inferred_unshared = (
                not explicit_shared
                and slice_name in {"recent_self_experiences", "remembered_material", "automatic_prefetch"}
                and (
                    (is_prehistory_item and _has_disclosure_ledger(materials))
                    or _is_unshared_in_companion_dialogue(preview, companion_texts)
                )
            )
            unshared_flag = bool(
                "shared_history" not in scopes
                and (explicit_unshared or inferred_unshared)
            )
            is_landmark = (
                (isinstance(item.get("importance_bp"), int) and not isinstance(item.get("importance_bp"), bool) and item["importance_bp"] >= 7000)
                or (isinstance(item.get("salience_bp"), int) and not isinstance(item.get("salience_bp"), bool) and item["salience_bp"] >= 7000)
            )
            seen_aliases.add(alias)
            shortcuts.append({
                "alias": alias,
                "scopes": scopes,
                "slice": slice_name,
                **({"when_local": when_local} if when_local else {}),
                **({"landmark": True} if is_landmark else {}),
                **(
                    {
                        "shared_in_chat": True,
                        "previously_shared_claim": disclosed_claim["claim_text"][:60],
                    }
                    if disclosed_claim is not None
                    else {
                        "shared_in_chat": False,
                        "counterpart_prior_knowledge": "never_mentioned_in_any_prior_chat",
                    }
                    if unshared_flag
                    else {}
                ),
                "preview": preview.replace("\n", " ").strip()[:38],
            })
    bio_coords = boundaries.get("biographical_coordinate_authority")
    if isinstance(bio_coords, list):
        for coord in bio_coords:
            if not isinstance(coord, dict):
                continue
            raw_ref = coord.get("source_ref")
            alias = (
                raw_ref
                if isinstance(raw_ref, str) and raw_ref in alias_to_scopes
                else (ref_to_alias.get(raw_ref) if isinstance(raw_ref, str) else None)
            )
            scopes = alias_to_scopes.get(alias or "", []) if alias else []
            val = coord.get("value")
            if not alias or not scopes or alias in seen_aliases or not isinstance(val, dict):
                continue
            bio_preview = val.get("context_summary") or val.get("summary")
            if not isinstance(bio_preview, str) or not bio_preview.strip():
                continue
            canonical_bio_ref = alias_to_canonical.get(alias, raw_ref)
            disclosed_bio = _lookup_disclosed_claim(
                (
                    raw_ref,
                    canonical_bio_ref,
                    val.get("coordinate_ref"),
                    val.get("settlement_event_ref"),
                ),
                materials=materials,
                coordinate_summary=bio_preview,
            )
            bio_unshared = disclosed_bio is None and (
                val.get("shared_in_chat") is False
                or (
                    val.get("shared_in_chat") is None
                    and _is_unshared_in_companion_dialogue(bio_preview, companion_texts)
                )
            )
            seen_aliases.add(alias)
            shortcuts.append({
                "alias": alias,
                "scopes": scopes,
                "slice": "biographical_context",
                **(
                    {
                        "shared_in_chat": True,
                        "previously_shared_claim": disclosed_bio["claim_text"][:60],
                    }
                    if disclosed_bio is not None
                    else {
                        "shared_in_chat": False,
                        "counterpart_prior_knowledge": "never_mentioned_in_any_prior_chat",
                    }
                    if bio_unshared
                    else {}
                ),
                "preview": bio_preview.replace("\n", " ").strip()[:72],
            })
    recalled_boost_refs = _collect_recalled_boost_refs(materials)
    if recalled_boost_refs:
        boosted_aliases = {
            ref_to_alias.get(r, r)
            for r in recalled_boost_refs
            if isinstance(r, str)
        }
        front_shortcuts = [s for s in shortcuts if s.get("alias") in boosted_aliases or s.get("slice") == "selected_recall"]
        other_shortcuts = [s for s in shortcuts if s not in front_shortcuts]
        shortcuts = front_shortcuts + other_shortcuts
    return shortcuts


def _collect_recalled_boost_refs(materials: Mapping[str, object]) -> set[str]:
    """Collect memory source_refs that were explicitly recalled or reinforced in recent turns."""
    boost_refs: set[str] = set()
    sel = materials.get("selected_recall")
    sel_items = sel.get("items") if isinstance(sel, Mapping) and isinstance(sel.get("items"), list) else ()
    for item in sel_items:
        if isinstance(item, Mapping):
            ref = item.get("source_ref") or item.get("document_id")
            if isinstance(ref, str) and ref:
                boost_refs.add(ref)
    for slice_key in ("automatic_prefetch", "remembered_material", "recent_self_experiences"):
        raw_slice = materials.get(slice_key)
        items = (
            raw_slice.get("items")
            if isinstance(raw_slice, Mapping) and isinstance(raw_slice.get("items"), list)
            else raw_slice
            if isinstance(raw_slice, list)
            else ()
        )
        for item in items:
            if isinstance(item, Mapping):
                rc = item.get("recall_count")
                ref = item.get("source_ref") or item.get("influence_id")
                if isinstance(rc, int) and rc > 0 and isinstance(ref, str) and ref:
                    boost_refs.add(ref)
    cur_app = materials.get("appraisals")
    if isinstance(cur_app, list):
        for app in cur_app[-3:]:
            if isinstance(app, Mapping) and isinstance(app.get("evidence_refs"), list):
                for ev in app["evidence_refs"]:
                    if isinstance(ev, str) and ev:
                        boost_refs.add(ev)
    return boost_refs


def _build_memory_tree_index(
    materials: Mapping[str, object],
    shortcuts: Sequence[Mapping[str, object]],
    *,
    boundaries: object = None,
    logical_time: object = None,
) -> dict[str, list[str]]:
    """Build a compact 3-layer nested memory title index (Layer 1 front / Layer 2 recent / Layer 3 deep)."""
    entries: list[Mapping[str, object]] = list(shortcuts)
    if not entries:
        for slice_name, raw_slice in (
            ("selected_recall", materials.get("selected_recall")),
            ("recent_self_experiences", materials.get("recent_self_experiences")),
            ("automatic_prefetch", materials.get("automatic_prefetch")),
        ):
            items = (
                raw_slice.get("items")
                if isinstance(raw_slice, Mapping) and isinstance(raw_slice.get("items"), list)
                else raw_slice
                if isinstance(raw_slice, list)
                else ()
            )
            for idx, item in enumerate(items):
                if not isinstance(item, Mapping):
                    continue
                alias = str(item.get("source_ref") or item.get("influence_id") or f"{slice_name}:{idx}").strip()
                preview = str(item.get("text") or item.get("what_happened") or item.get("summary") or "").replace("\n", " ").strip()
                if alias and preview:
                    entries.append({"alias": alias, "slice": slice_name, "preview": preview})
    if not entries:
        return {}
    recalled_boost_refs = _collect_recalled_boost_refs(materials)
    aliases_map = boundaries.get("source_ref_aliases") if isinstance(boundaries, Mapping) else None
    ref_to_alias = (
        {canonical: alias for alias, canonical in aliases_map.items() if isinstance(alias, str) and isinstance(canonical, str)}
        if isinstance(aliases_map, Mapping)
        else {}
    )
    boosted_aliases = {ref_to_alias.get(r, r) for r in recalled_boost_refs} | recalled_boost_refs
    layer_1_front: list[str] = []
    layer_2_recent: list[str] = []
    layer_3_deep: list[str] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        alias = str(entry.get("alias") or "").strip()
        preview = str(entry.get("preview") or "").strip()
        if not alias or not preview:
            continue
        title = preview[:28] + ("…" if len(preview) > 28 else "")
        when_local = str(entry.get("when_local") or "")
        time_tag = ""
        if when_local.startswith("今天"):
            time_tag = "今天"
        elif when_local.startswith("昨天"):
            time_tag = "昨天"
        elif "天前" in when_local:
            time_tag = when_local.split("(", 1)[0]
        node_label = f"[{alias}] {f'({time_tag}) ' if time_tag else ''}{title}"
        slice_name = str(entry.get("slice") or "")
        is_landmark = bool(entry.get("landmark"))
        if (
            slice_name == "selected_recall"
            or is_landmark
            or alias in boosted_aliases
            or time_tag == "今天"
            or slice_name in {"perception", "recent_self_experiences"}
        ):
            if len(layer_1_front) < 6:
                layer_1_front.append(node_label)
        elif time_tag == "昨天" or (
            time_tag.endswith("天前")
            and time_tag.removesuffix("天前").isdigit()
            and int(time_tag.removesuffix("天前")) <= 7
        ):
            if len(layer_2_recent) < 6:
                layer_2_recent.append(node_label)
        else:
            if len(layer_3_deep) < 6:
                layer_3_deep.append(node_label)
    tree: dict[str, list[str]] = {}
    if layer_1_front:
        tree["layer_1_front"] = layer_1_front
    if layer_2_recent:
        tree["layer_2_recent"] = layer_2_recent
    if layer_3_deep:
        tree["layer_3_deep"] = layer_3_deep
    return tree
