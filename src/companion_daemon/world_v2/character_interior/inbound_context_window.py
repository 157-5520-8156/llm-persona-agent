"""Bound redundant narrative history in a model view, never in World storage.

Current state, counterpart reports, facts, capabilities, recalled material and
all action/effect coordinates remain intact. This is an explicit experimental
presentation policy, not a source of facts or a character behavior policy.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..shared_string_view import unpack_shared_strings

CONTRACT = "inbound-narrative-window.1"
MAX_COMPANION_UTTERANCES = 2
MAX_OVERLAP_COMPANION_UTTERANCES = 8
_DEFAULT_LOCAL_TZ = ZoneInfo("Asia/Shanghai")


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
    dialogue_seconds: list[int] = []
    settled_counterpart_seconds: list[int] = []
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
            row["dialogue_id"]: row["text"].strip()
            for row in all_dialogue_rows
            if isinstance(row, dict)
            and isinstance(row.get("dialogue_id"), str)
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
                            if ("小时前" in prefix or "天前" in prefix) and not (
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
                        and ("小时前）：" in entry["line"] or "天前）：" in entry["line"])
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
                ][-6:]
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
        impressions = materials.get("private_impressions")
        if settled_counterpart_seconds and isinstance(impressions, list):
            latest_settled_counterpart_at = max(settled_counterpart_seconds)
            earliest_dialogue_at = min(dialogue_seconds) if dialogue_seconds else None
            kept_impressions = []
            for imp in impressions:
                if not isinstance(imp, dict):
                    kept_impressions.append(imp)
                    continue
                expiry = imp.get("expiry_condition")
                imp_time = _recorded_second(imp.get("last_supported") or imp.get("first_seen"))
                subject_ref = imp.get("subject_ref")
                if (
                    expiry == "until_appraisal_contradicted"
                    and imp_time is not None
                    and imp_time < latest_settled_counterpart_at
                    and (
                        (isinstance(subject_ref, str) and subject_ref != "agent:companion")
                        or (earliest_dialogue_at is not None and imp_time >= earliest_dialogue_at)
                    )
                ):
                    continue
                kept_impressions.append(imp)
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
            from datetime import datetime
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
                        and settled_counterpart_cutoff is not None
                        and since_sec <= settled_counterpart_cutoff
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
    lived_moment = materials.get("lived_moment")
    if isinstance(lived_moment, dict):
        cur_mom = lived_moment.get("current_moment")
        if isinstance(cur_mom, str):
            embodied_clauses = [
                c.strip()
                for c in cur_mom.split("；")
                if c.strip() and not c.strip().startswith(("此刻正在：", "手头正忙："))
            ]
            if embodied_clauses:
                lived_moment["current_moment"] = "；".join(embodied_clauses)
            else:
                lived_moment.pop("current_moment", None)
        else:
            lived_moment.pop("current_moment", None)
    perception_items = materials.get("perception")
    if isinstance(perception_items, list):
        latest_chat_sec = max(dialogue_seconds) if dialogue_seconds else None
        kept_perception = []
        for p_item in perception_items:
            if not isinstance(p_item, dict):
                kept_perception.append(p_item)
                continue
            acc_sec = _recorded_second(p_item.get("accepted_at"))
            if (
                latest_chat_sec is not None
                and acc_sec is not None
                and (latest_chat_sec - acc_sec).total_seconds() > 600
            ):
                continue
            kept_perception.append(p_item)
        if kept_perception:
            materials["perception"] = kept_perception
        else:
            materials.pop("perception", None)
    auto_pre = materials.get("automatic_prefetch")
    if isinstance(auto_pre, dict) and isinstance(auto_pre.get("items"), list):
        latest_settled_cp = max(settled_counterpart_seconds) if settled_counterpart_seconds else None
        kept_auto_items = []
        for item in auto_pre["items"]:
            if (
                isinstance(item, dict)
                and item.get("source_slice") == "private_impressions"
                and latest_settled_cp is not None
            ):
                occ_to = _recorded_second(item.get("occurred_to") or item.get("occurred_from"))
                if occ_to is not None and occ_to < latest_settled_cp:
                    continue
            kept_auto_items.append(item)
        auto_pre["items"] = kept_auto_items
    shortcuts = _citable_memory_shortcuts(
        materials,
        result.get("expression_hard_boundaries"),
        logical_time=(snapshot.get("cursor") or {}).get("logical_time") if isinstance(snapshot.get("cursor"), dict) else None,
    )
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
        "instruction": (
            "这是本轮展示窗口，不是全部历史。旧发言和旧解释仍可检索；没展示不证明没发生。"
            "当前情绪、关系、事实和未完成事项仍保留，如何回应由你决定。"
        ),
    }
    if has_correction:
        result["role_result_correction"] = correction
    return result


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
                    f"{item['title']} — {item['summary']}"
                    if isinstance(item.get("title"), str) and isinstance(item.get("summary"), str)
                    else None
                )
                or item.get("summary")
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
            unshared_flag = bool(
                "shared_history" not in scopes
                and (item.get("already_shared_in_chat") is False or item.get("shared_in_chat") is False)
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
            bio_unshared = val.get("shared_in_chat") is False
            seen_aliases.add(alias)
            shortcuts.append({
                "alias": alias,
                "scopes": scopes,
                "slice": "biographical_context",
                **(
                    {
                        "shared_in_chat": False,
                        "counterpart_prior_knowledge": "never_mentioned_in_any_prior_chat",
                    }
                    if bio_unshared
                    else {}
                ),
                "preview": bio_preview.replace("\n", " ").strip()[:72],
            })
    return shortcuts
