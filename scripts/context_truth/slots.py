"""Slot inventory: every world-fact the snapshot states, with a ledger twin.

The six known production mismatches are coverage self-checks (K1–K6), not
the complete set. A slot that is only "her inner mood" is omitted; a slot
that tells her a countable / enumerable world fact is included.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from companion_daemon.world_v2.qq_face_render_catalog import lookup_face_render

from .types import CatalogFace, CompanionLine, CounterpartLine, LedgerTruth, SeenView, SlotSpec


# Facet names are the production InnerLifeSnapshot contract order.
FACETS = (
    "private_self",
    "selective_memory",
    "appraisal_affect",
    "emotional_continuity",
    "subjective_relationship",
    "aspirations_conflicts",
    "autonomous_impulses",
    "expression_stance",
)

PHOTO_SENT_FRAGMENTS = (
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
    "发给他了",
    "已经发给他",
    "已经发过去",
    "也真的发了",
)

FALSE_RETURN_FRAGMENTS = (
    "他倒完水回来",
    "倒完水回来",
    "水喝上了",
    "他回来了还不",
    "他已经回来",
    "他回来了",
)

SLEEP_MARKERS = (".sleep", "sleep.")


def _entries(materials: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    value = materials.get(key)
    if isinstance(value, dict):
        value = value.get("items")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _count(materials: Mapping[str, Any], key: str) -> int:
    return len(_entries(materials, key))


def _walk_strings(value: object, *, depth: int = 0) -> Iterable[str]:
    if depth > 8:
        return
    if isinstance(value, str):
        text = value.strip()
        if text:
            yield text
        return
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_strings(child, depth=depth + 1)
        return
    if isinstance(value, list):
        for child in value[:64]:
            yield from _walk_strings(child, depth=depth + 1)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _contains(haystack: str, needle: str) -> bool:
    if not needle or not haystack:
        return False
    return _norm(needle) in _norm(haystack)


def _line_matches(seen_lines: Iterable[str], ledger_text: str) -> bool:
    target = _norm(ledger_text)
    if not target:
        return False
    for line in seen_lines:
        body = _norm(line)
        if not body:
            continue
        if target in body or body in target:
            return True
        # Transcript prefix: "我：…" / "他：…"
        for prefix in ("我：", "他：", "我（", "他（"):
            if body.startswith(prefix) and target in body:
                return True
    return False


def _dialogue_texts(seen: SeenView) -> list[str]:
    texts: list[str] = []
    for entry in _entries(seen.materials, "recent_dialogue"):
        text = entry.get("text")
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
    for entry in _entries(seen.materials, "folded_dialogue"):
        line = entry.get("line") or entry.get("text")
        if isinstance(line, str) and line.strip():
            texts.append(line.strip())
        lines = entry.get("lines")
        if isinstance(lines, list):
            texts.extend(item.strip() for item in lines if isinstance(item, str) and item.strip())
    texts.extend(seen.conversation)
    return texts


def _week_diary_claims_photo_sent(seen: SeenView) -> bool:
    for entry in _entries(seen.materials, "week_diary"):
        blob = " ".join(_walk_strings(entry))
        if any(fragment in blob for fragment in PHOTO_SENT_FRAGMENTS):
            return True
    grouped = seen.materials.get("week_diary")
    if isinstance(grouped, list):
        blob = " ".join(_walk_strings(grouped))
        if any(fragment in blob for fragment in PHOTO_SENT_FRAGMENTS):
            return True
    experiences = seen.materials.get("recent_self_experiences")
    blob = " ".join(_walk_strings(experiences))
    return any(fragment in blob for fragment in PHOTO_SENT_FRAGMENTS)


def _false_return_claim(seen: SeenView) -> bool:
    keys = (
        "appraisals",
        "interruption",
        "advisories",
        "lived_moment",
        "private_impressions",
        "situation",
        "week_diary",
    )
    blob = " ".join(
        text for key in keys for text in _walk_strings(seen.materials.get(key))
    )
    return any(fragment in blob for fragment in FALSE_RETURN_FRAGMENTS)


def _available_count(seen: SeenView) -> int | None:
    inventory = seen.materials.get("moments_i_can_share")
    if not isinstance(inventory, dict):
        return 0
    count = inventory.get("available_count")
    return int(count) if isinstance(count, int) and not isinstance(count, bool) else 0


def _already_sent_count(seen: SeenView) -> int | None:
    inventory = seen.materials.get("moments_i_can_share")
    if not isinstance(inventory, dict):
        return None
    count = inventory.get("already_sent_count")
    return int(count) if isinstance(count, int) and not isinstance(count, bool) else None


def _since_seconds(seen: SeenView) -> int | None:
    elapsed = seen.materials.get("since_he_last_spoke")
    if isinstance(elapsed, dict) and isinstance(elapsed.get("seconds"), int):
        return int(elapsed["seconds"])
    return None


def _ledger_since_seconds(truth: LedgerTruth) -> int | None:
    if truth.logical_time is None or truth.last_counterpart is None:
        return None
    occurred = truth.last_counterpart.occurred_at
    if occurred is None:
        return None
    delta = int((truth.logical_time - occurred).total_seconds())
    return delta if delta >= 0 else None


def _active_plan_ids_seen(seen: SeenView) -> tuple[str, ...]:
    ids: list[str] = []
    for entry in _entries(seen.materials, "situation"):
        slices = entry.get("activity_slices")
        if not isinstance(slices, list):
            continue
        for row in slices:
            if not isinstance(row, dict) or row.get("status") != "active":
                continue
            plan_id = row.get("plan_id")
            if isinstance(plan_id, str) and plan_id:
                ids.append(plan_id)
    return tuple(dict.fromkeys(ids))


def _photographable(seen: SeenView) -> bool | None:
    inventory = seen.materials.get("moments_i_can_share")
    if not isinstance(inventory, dict):
        return None
    now = inventory.get("now")
    if not isinstance(now, dict):
        return None
    flag = now.get("photographable")
    return flag if isinstance(flag, bool) else None


def _perception_count(seen: SeenView) -> int:
    return _count(seen.materials, "perception")


def _relationship_stage_seen(seen: SeenView) -> str | None:
    for entry in _entries(seen.materials, "relationship"):
        stage = entry.get("stage")
        if isinstance(stage, str) and stage:
            return stage
    return None


def _life_arc_ids_seen(seen: SeenView) -> tuple[str, ...]:
    ids: list[str] = []
    for entry in _entries(seen.materials, "biographical_context"):
        arcs = entry.get("active_life_arcs")
        if isinstance(arcs, list):
            for item in arcs:
                if isinstance(item, str) and item:
                    ids.append(item)
                elif isinstance(item, dict):
                    arc_id = item.get("arc_id") or item.get("id")
                    if isinstance(arc_id, str) and arc_id:
                        ids.append(arc_id)
    return tuple(dict.fromkeys(ids))


def _stable_self_present(seen: SeenView) -> bool:
    return bool(_entries(seen.materials, "stable_self"))


def _face_aliases(ref: str, text: str | None = None) -> list[str]:
    aliases = [item for item in (ref, text) if isinstance(item, str) and item.strip()]
    entry = lookup_face_render(ref)
    if entry is None:
        return aliases
    if entry.name:
        aliases.append(entry.name)
    if entry.glyph:
        aliases.append(entry.glyph)
        if entry.name:
            aliases.append(f"{entry.glyph} {entry.name}".strip())
    return aliases


def _needles_for_ledger_line(item: Any) -> list[str]:
    if isinstance(item, CounterpartLine):
        needles = [item.text] if item.text.strip() else []
        for ref in (*item.reaction_refs, *item.sticker_refs):
            needles.extend(_face_aliases(ref, item.text))
        return [text for text in dict.fromkeys(needles) if text.strip()]
    if isinstance(item, CompanionLine):
        return [item.text] if item.text.strip() else []
    if isinstance(item, str) and item.strip():
        return [item]
    return []


def _compare_head_included(seen_lines: Any, ledger_texts: Any) -> str | None:
    if not isinstance(ledger_texts, (list, tuple)):
        return None
    visible = [str(item) for item in seen_lines] if isinstance(seen_lines, (list, tuple)) else []
    missing: list[str] = []
    for item in ledger_texts:
        needles = _needles_for_ledger_line(item)
        if not needles:
            continue
        if any(_line_matches(visible, needle) for needle in needles):
            continue
        missing.append(needles[0][:80])
    if not missing:
        return None
    return f"账本近窗有 {len(missing)} 句她看不见，最近一句：{missing[-1]}"


def _compare_subset(seen_lines: Any, ledger_texts: Any) -> str | None:
    if not isinstance(seen_lines, (list, tuple)) or not isinstance(ledger_texts, (list, tuple)):
        return None
    needles: list[str] = []
    for item in ledger_texts:
        needles.extend(_needles_for_ledger_line(item))
    ledger_blob = "\n".join(_norm(text) for text in needles)
    extras = []
    for line in seen_lines:
        if not isinstance(line, str) or not line.strip():
            continue
        body = _norm(line)
        if body.startswith("[") and body.endswith("]"):
            continue
        if any(body in _norm(item) or _norm(item) in body for item in needles):
            continue
        if body in ledger_blob:
            continue
        extras.append(line[:80])
    if not extras:
        return None
    return f"她看见 {len(extras)} 句账本对不上的话，例如：{extras[0]}"


def _compare_faces(seen_faces: Any, ledger_faces: Any) -> str | None:
    rows = ledger_faces if isinstance(ledger_faces, (list, tuple)) else ()
    seen_texts = [str(item) for item in seen_faces] if isinstance(seen_faces, (list, tuple)) else []
    failures: list[str] = []
    for item in rows:
        if not isinstance(item, CatalogFace):
            continue
        if not item.catalog_name:
            continue
        raw = item.provider_ref
        number = raw.split(":")[-1] if raw else ""
        name = item.catalog_name
        glyph = item.catalog_glyph or ""
        visible_hits = [
            text
            for text in seen_texts
            if text
            and (
                raw in text
                or (number and number in text)
                or name in text
                or (glyph and glyph in text)
            )
        ]
        if not visible_hits:
            # Face is not in the current snapshot window.
            continue
        blob = " ".join(visible_hits)
        if name in blob or (glyph and glyph in blob):
            continue
        failures.append(
            f"{raw} 目录名「{name}」{(' / ' + glyph) if glyph else ''}，"
            f"她看见的是 {visible_hits[0][:80]}"
        )
    if not failures:
        return None
    return "；".join(failures[:4])


def _compare_sidecar(seen_count: Any, ledger_pair: Any) -> str | None:
    if not isinstance(ledger_pair, (list, tuple)) or len(ledger_pair) != 2:
        return None
    admitted, processed = ledger_pair
    if not isinstance(processed, int) or processed <= 0:
        return None
    visible = int(seen_count or 0)
    admitted_n = int(admitted or 0)
    if visible == 0 and admitted_n == 0 and processed > 0:
        return (
            f"sidecar 已处理 {processed} 条，账本 admitted={admitted_n}，"
            f"她看见 {visible} 条"
        )
    return None


SLOTS: tuple[SlotSpec, ...] = (
    SlotSpec(
        slot_id="selective_memory.moments_i_can_share.available_count",
        facets=("selective_memory", "subjective_relationship", "autonomous_impulses", "expression_stance"),
        material_key="moments_i_can_share",
        title="可分享照片张数",
        relation="eq",
        severity="critical",
        known_issue="K1",
        why_it_matters="她会按「相册是空的」拒绝发送、改口或编一个没有候选的理由。",
        extract_seen=_available_count,
        extract_ledger=lambda truth: len(truth.available_photo_candidate_ids),
        notes="账本侧计 shareable_photo_facts.photo_in_hand，不经过 InnerLifeSnapshot。",
    ),
    SlotSpec(
        slot_id="selective_memory.moments_i_can_share.stated",
        facets=("selective_memory", "subjective_relationship", "expression_stance"),
        material_key="moments_i_can_share",
        title="相册事实是否被陈述（空也是事实）",
        relation="eq",
        severity="critical",
        known_issue="K1",
        why_it_matters="材料整段缺席时她会自己发明相册状态，而不只是把张数看成 0。",
        extract_seen=lambda seen: isinstance(seen.materials.get("moments_i_can_share"), dict),
        extract_ledger=lambda truth: True,
        notes="生产编译器约定 empty inventory 仍要出现。缺席本身就是和账本对不上。",
    ),
    SlotSpec(
        slot_id="selective_memory.moments_i_can_share.already_sent_count",
        facets=("selective_memory", "subjective_relationship", "expression_stance"),
        material_key="moments_i_can_share",
        title="已分享照片张数（inventory）",
        relation="eq",
        severity="high",
        why_it_matters="already_sent_count 会让她以为照片已经在对话里。",
        extract_seen=_already_sent_count,
        extract_ledger=lambda truth: len(truth.media_delivery_ids),
    ),
    SlotSpec(
        slot_id="selective_memory.photos_i_shared.count",
        facets=("selective_memory", "subjective_relationship", "expression_stance"),
        material_key="photos_i_shared",
        title="photos_i_shared 条数",
        relation="eq",
        severity="high",
        why_it_matters="她用这条材料确认自己有没有把图发出去。",
        extract_seen=lambda seen: _count(seen.materials, "photos_i_shared"),
        extract_ledger=lambda truth: len(truth.media_delivery_ids),
    ),
    SlotSpec(
        slot_id="private_self.week_diary.photo_sent_claim",
        facets=("private_self",),
        material_key="week_diary",
        title="周记/近况把「已经发出照片」写成既成事实",
        relation="implies_positive",
        severity="critical",
        known_issue="K2",
        why_it_matters="她会按已经完成的承诺继续聊，而你这边从未收到图。",
        extract_seen=_week_diary_claims_photo_sent,
        extract_ledger=lambda truth: len(truth.media_delivery_ids),
        notes="机械判定用固定完成态短语，不是自由文本相似度。",
    ),
    SlotSpec(
        slot_id="emotional_continuity.since_he_last_spoke.seconds",
        facets=("emotional_continuity", "selective_memory", "expression_stance"),
        material_key="since_he_last_spoke",
        title="距他上次开口的秒数",
        relation="approx_seconds",
        severity="high",
        known_issue="K3",
        tolerance_seconds=90,
        why_it_matters="她会按「他刚说话 / 他消失很久」选节奏，包括要不要追问。",
        extract_seen=_since_seconds,
        extract_ledger=_ledger_since_seconds,
        notes="Path A 只看她仍可见的 counterpart 行；Path B 看账本最后一条 Observation。",
    ),
    SlotSpec(
        slot_id="emotional_continuity.false_return_claim",
        facets=("emotional_continuity", "appraisal_affect"),
        material_key="appraisals",
        title="材料声称他回来了",
        relation="implies_positive",
        severity="high",
        known_issue="K3",
        why_it_matters="她会按「人已经回来了」继续刚才的线程，而账本上他从未开口。",
        extract_seen=_false_return_claim,
        extract_ledger=lambda truth: (
            1
            if truth.last_counterpart is not None
            and truth.logical_time is not None
            and truth.last_counterpart.occurred_at is not None
            and (truth.logical_time - truth.last_counterpart.occurred_at).total_seconds() <= 15 * 60
            else 0
        ),
    ),
    SlotSpec(
        slot_id="selective_memory.recent_dialogue.companion_head",
        facets=("selective_memory", "subjective_relationship", "expression_stance"),
        material_key="recent_dialogue",
        title="她自己刚说的话是否在对话栏",
        relation="custom",
        severity="high",
        known_issue="K4",
        why_it_matters="看不见自己的上一句，她会重复、改口或以为没发出去。",
        extract_seen=_dialogue_texts,
        extract_ledger=lambda truth: tuple(
            item.text for item in truth.companion_recent_settled[-8:]
        ),
        compare=_compare_head_included,
    ),
    SlotSpec(
        slot_id="selective_memory.recent_dialogue.counterpart_head",
        facets=("selective_memory", "subjective_relationship", "expression_stance"),
        material_key="recent_dialogue",
        title="他刚说的话是否在对话栏",
        relation="custom",
        severity="high",
        why_it_matters="看不见他的最新一句，她会回答更早的话题。",
        extract_seen=_dialogue_texts,
        extract_ledger=lambda truth: truth.counterpart_recent,
        compare=_compare_head_included,
    ),
    SlotSpec(
        slot_id="selective_memory.recent_dialogue.no_phantom",
        facets=("selective_memory", "expression_stance"),
        material_key="recent_dialogue",
        title="对话栏没有账本里不存在的句子",
        relation="custom",
        severity="high",
        why_it_matters="幻影对话会让她回应从未发生过的话。",
        extract_seen=_dialogue_texts,
        extract_ledger=lambda truth: tuple(
            [
                *(truth.counterpart_all or truth.counterpart_recent),
                *(truth.companion_all_settled or truth.companion_recent_settled),
                *truth.companion_waiting,
            ]
        ),
        compare=_compare_subset,
    ),
    SlotSpec(
        slot_id="selective_memory.inbound_surfaces.catalog_name",
        facets=("selective_memory", "expression_stance"),
        material_key="recent_dialogue",
        title="QQ 系统表情是否带目录名",
        relation="custom",
        severity="medium",
        known_issue="K5",
        why_it_matters="只看见编号时她会把表情当成无意义数字，无法按平台渲染名回应。",
        extract_seen=_dialogue_texts,
        extract_ledger=lambda truth: truth.catalog_faces,
        compare=_compare_faces,
        notes="Path B 用 configs/qq_face_render_catalog.yaml，不猜情绪。",
    ),
    SlotSpec(
        slot_id="autonomous_impulses.perception.admitted_count",
        facets=("autonomous_impulses",),
        material_key="perception",
        title="已进入世界的感知条数",
        relation="eq",
        severity="high",
        known_issue="K6",
        why_it_matters="她用 perception 材料判断外面有没有值得提起的事。",
        extract_seen=_perception_count,
        extract_ledger=lambda truth: truth.external_perception_count + truth.perception_result_count,
        notes="账本侧是 admitted 的 ExternalPerception + QQ perception_results，不是 sidecar 原始抓取。",
    ),
    SlotSpec(
        slot_id="autonomous_impulses.perception.sidecar_unadmitted",
        facets=("autonomous_impulses",),
        material_key="perception",
        title="sidecar 已处理但从未进入世界",
        relation="custom",
        severity="high",
        known_issue="K6",
        why_it_matters="处理了却不告诉她，等于外面发生的事对她不存在。",
        extract_seen=_perception_count,
        extract_ledger=lambda truth: (
            truth.external_perception_count + truth.perception_result_count,
            truth.sidecar_processed_count,
        ),
        compare=_compare_sidecar,
        notes="处理量只计 live_outbox（真正排队入世界的）。storage_samples 是小时遥测，不算。",
    ),
    SlotSpec(
        slot_id="private_self.stable_self.present",
        facets=("private_self", "expression_stance"),
        material_key="stable_self",
        title="稳定自我材料是否在",
        relation="eq",
        severity="medium",
        why_it_matters="缺 identity 材料时她会用更浅的自我描述。",
        extract_seen=_stable_self_present,
        extract_ledger=lambda truth: truth.has_character_core,
    ),
    SlotSpec(
        slot_id="private_self.situation.active_plan_ids",
        facets=("private_self", "aspirations_conflicts", "autonomous_impulses"),
        material_key="situation",
        title="当前进行中的活动",
        relation="eq",
        severity="high",
        why_it_matters="她会按正在做的事选择现在能不能拍照、回不回消息。",
        extract_seen=lambda seen: tuple(sorted(_active_plan_ids_seen(seen))),
        extract_ledger=lambda truth: tuple(sorted(truth.active_plan_ids)),
    ),
    SlotSpec(
        slot_id="private_self.biographical.active_life_arcs",
        facets=("private_self",),
        material_key="biographical_context",
        title="进行中的 Life Arc",
        relation="eq",
        severity="medium",
        why_it_matters="错的章节会让她用错学校/住处/工作坐标。",
        extract_seen=lambda seen: tuple(sorted(_life_arc_ids_seen(seen))),
        extract_ledger=lambda truth: tuple(sorted(truth.active_life_arc_ids)),
        notes="若编译器只给标签不给 id，seen 可能是空元组；那种情况记 info 级缺口。",
    ),
    SlotSpec(
        slot_id="private_self.private_impressions.active_count",
        facets=("private_self", "subjective_relationship"),
        material_key="private_impressions",
        title="仍活着的私人印象条数",
        relation="eq",
        severity="medium",
        why_it_matters="少了她会漏掉仍活着的私下理解；多了她会按已经过期的印象待人。",
        extract_seen=lambda seen: _count(seen.materials, "private_impressions"),
        extract_ledger=lambda truth: len(truth.active_impression_ids),
    ),
    SlotSpec(
        slot_id="private_self.recent_self_experiences.count",
        facets=("private_self", "autonomous_impulses"),
        material_key="recent_self_experiences",
        title="近况条目数不超过账本经历",
        relation="monotonic_le",
        severity="medium",
        why_it_matters="条数多于账本等于捏造经历。",
        extract_seen=lambda seen: _count(seen.materials, "recent_self_experiences")
        if isinstance(seen.materials.get("recent_self_experiences"), dict)
        or isinstance(seen.materials.get("recent_self_experiences"), list)
        else 0,
        extract_ledger=lambda truth: truth.settled_occurrence_count + truth.experience_count,
    ),
    SlotSpec(
        slot_id="appraisal_affect.appraisals.active_count",
        facets=("appraisal_affect", "emotional_continuity", "autonomous_impulses"),
        material_key="appraisals",
        title="仍有效的读法条数",
        relation="eq",
        severity="high",
        why_it_matters="少了她会忘掉仍有效的判断；多了她会按已经作废的判断说话。",
        extract_seen=lambda seen: _count(seen.materials, "appraisals"),
        extract_ledger=lambda truth: len(truth.active_appraisal_ids),
    ),
    SlotSpec(
        slot_id="appraisal_affect.affect.active_count",
        facets=("appraisal_affect", "emotional_continuity"),
        material_key="affect",
        title="仍活着的持续情绪条数",
        relation="eq",
        severity="medium",
        why_it_matters="衰减/已结束的情绪如果还在，她会表演一段已经过去的感觉。",
        extract_seen=lambda seen: _count(seen.materials, "affect"),
        extract_ledger=lambda truth: len(truth.active_affect_ids),
    ),
    SlotSpec(
        slot_id="subjective_relationship.stage",
        facets=("subjective_relationship", "autonomous_impulses", "expression_stance"),
        material_key="relationship",
        title="和他的关系阶段",
        relation="eq",
        severity="high",
        why_it_matters="阶段错了会动隐私地板和她愿不愿靠近。",
        extract_seen=_relationship_stage_seen,
        extract_ledger=lambda truth: truth.relationship_stage,
    ),
    SlotSpec(
        slot_id="subjective_relationship.npc_relationship_count",
        facets=("subjective_relationship", "autonomous_impulses"),
        material_key="protagonist_npc_relationships",
        title="NPC 关系条数不超过账本",
        relation="monotonic_le",
        severity="low",
        why_it_matters="多出来的 NPC 关系是无源社交事实。",
        extract_seen=lambda seen: _count(seen.materials, "protagonist_npc_relationships"),
        extract_ledger=lambda truth: truth.npc_relationship_count,
    ),
    SlotSpec(
        slot_id="aspirations_conflicts.unresolved.open_count",
        facets=("aspirations_conflicts", "autonomous_impulses"),
        material_key="unresolved",
        title="未完成线程条数",
        relation="eq",
        severity="high",
        why_it_matters="少了她会忘掉仍开着的问题；多了她会追问已经关闭的线程。",
        extract_seen=lambda seen: _count(seen.materials, "unresolved"),
        extract_ledger=lambda truth: len(truth.open_thread_ids),
    ),
    SlotSpec(
        slot_id="aspirations_conflicts.aspirations.active_count",
        facets=("aspirations_conflicts", "autonomous_impulses"),
        material_key="aspirations",
        title="仍开着的愿望条数",
        relation="eq",
        severity="medium",
        why_it_matters="愿望清单是她自己选方向的输入，多/少都会偏。",
        extract_seen=lambda seen: _count(seen.materials, "aspirations"),
        extract_ledger=lambda truth: len(truth.active_aspiration_ids),
    ),
    SlotSpec(
        slot_id="selective_memory.messages_waiting_to_send.count",
        facets=("selective_memory", "expression_stance"),
        material_key="messages_waiting_to_send",
        title="尚未发出的 later 句条数",
        relation="eq",
        severity="high",
        why_it_matters="她会以为某句还在路上，或重复发送已经排队的话。",
        extract_seen=lambda seen: _count(seen.materials, "messages_waiting_to_send"),
        extract_ledger=lambda truth: len(truth.companion_waiting),
    ),
    SlotSpec(
        slot_id="selective_memory.relevant_facts.count",
        facets=("selective_memory", "autonomous_impulses", "expression_stance"),
        material_key="relevant_facts",
        title="可见事实条数不超过账本",
        relation="monotonic_le",
        severity="medium",
        why_it_matters="多出来的事实是无源断言。",
        extract_seen=lambda seen: _count(seen.materials, "relevant_facts"),
        extract_ledger=lambda truth: truth.fact_count,
    ),
    SlotSpec(
        slot_id="selective_memory.remembered_material.count",
        facets=("selective_memory",),
        material_key="remembered_material",
        title="可见记忆候选不超过仍 active 的账本记忆",
        relation="monotonic_le",
        severity="medium",
        why_it_matters="忘掉的记忆如果还在，她会当近事重提。",
        extract_seen=lambda seen: _count(seen.materials, "remembered_material"),
        extract_ledger=lambda truth: truth.active_memory_count,
    ),
    SlotSpec(
        slot_id="selective_memory.moments_i_can_share.now.photographable",
        facets=("selective_memory", "expression_stance"),
        material_key="moments_i_can_share",
        title="此刻能不能拍（进行中且非睡眠）",
        relation="eq",
        severity="medium",
        why_it_matters="photographable 会改变她是否认为「现在」能出一张图。",
        extract_seen=_photographable,
        extract_ledger=lambda truth: bool(truth.active_non_sleep_plan_ids),
    ),
    SlotSpec(
        slot_id="autonomous_impulses.interaction_acts.count",
        facets=("autonomous_impulses", "subjective_relationship", "expression_stance"),
        material_key="interaction_acts",
        title="可见互动行为不超过账本",
        relation="monotonic_le",
        severity="medium",
        why_it_matters="多出来的互动行为是无源社交事实。",
        extract_seen=lambda seen: _count(seen.materials, "interaction_acts"),
        extract_ledger=lambda truth: truth.interaction_act_count,
    ),
    SlotSpec(
        slot_id="subjective_relationship.npc_observable_attitudes.count",
        facets=("subjective_relationship", "autonomous_impulses"),
        material_key="npc_observable_attitudes",
        title="可见 NPC 态度不超过账本 NPC 关系",
        relation="monotonic_le",
        severity="low",
        why_it_matters="多出来的 NPC 态度是无源社交事实。",
        extract_seen=lambda seen: _count(seen.materials, "npc_observable_attitudes"),
        extract_ledger=lambda truth: truth.npc_relationship_count,
    ),
    SlotSpec(
        slot_id="clock.logical_time",
        facets=("private_self", "emotional_continuity"),
        material_key="logical_time",
        title="快照逻辑时钟",
        relation="eq",
        severity="critical",
        why_it_matters="时钟不对，所有「刚刚/几小时前」都会一起错。",
        extract_seen=lambda seen: seen.logical_time,
        extract_ledger=lambda truth: truth.logical_time,
    ),
)


def slots_by_id() -> dict[str, SlotSpec]:
    return {item.slot_id: item for item in SLOTS}


def known_issue_coverage() -> dict[str, tuple[str, ...]]:
    covered: dict[str, list[str]] = {key: [] for key in ("K1", "K2", "K3", "K4", "K5", "K6")}
    for slot in SLOTS:
        if slot.known_issue in covered:
            covered[slot.known_issue].append(slot.slot_id)
    return {key: tuple(value) for key, value in covered.items()}


def facet_coverage() -> dict[str, tuple[str, ...]]:
    covered = {name: [] for name in FACETS}
    for slot in SLOTS:
        for facet in slot.facets:
            if facet in covered:
                covered[facet].append(slot.slot_id)
    return {key: tuple(value) for key, value in covered.items()}
