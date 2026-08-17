"""Stable Present prompt pieces: identity prose and prefix-friendly ordering."""

from __future__ import annotations

from collections.abc import Mapping
import re

from .companion_identity import CompanionIdentityFrame

PRESENT_RECENT_DIALOGUE_ITEM_LIMIT = 240
PRESENT_COMPANION_DIALOGUE_ITEM_LIMIT = 120
PRESENT_MEMORY_ITEM_LIMIT = 8
PRESENT_FACT_ITEM_LIMIT = 8
PRESENT_IMPRESSION_ITEM_LIMIT = 8
PRESENT_EXPERIENCE_ITEM_LIMIT = 8
PRESENT_AUTHORED_RELATIONSHIP_SIGNAL_LIMIT = 4
PRESENT_ACCEPTED_RELATIONSHIP_COMMITMENT_LIMIT = 4
PRESENT_CAPSULE_HARD_MAX_CHARACTERS = 100_000
PRESENT_DIALOGUE_SLICE_CHARACTERS = 32_000
PRESENT_DIALOGUE_FOLD_CHUNK_ITEMS = 20
PRESENT_DIALOGUE_FOLD_LINE_CHARACTERS = 160
PRESENT_DIALOGUE_SEQUENCE_SCALE = 100
PRESENT_WEEK_DIARY_DAYS = 7
PRESENT_WEEK_DIARY_LINES_PER_DAY = 3

_PRESENT_USER_KEY_ORDER = (
    "expression_capabilities",
    "expression_hard_boundaries",
    "inner_life_snapshot",
    "quick_recovery_failure",
    "prior_source_closure_failure",
    "request",
    "recall_available",
    "current_trigger_message",
)
_SNAPSHOT_VOLATILE_LAST = (
    "snapshot_id",
    "snapshot_hash",
    "cursor",
    "truncation",
    "source_inventory",
)
_MATERIAL_ORDER = (
    "stable_self",
    "biographical_context",
    "day_sheet",
    "week_diary",
    "situation",
    "relationship",
    "protagonist_npc_relationships",
    "npc_observable_attitudes",
    "unresolved",
    "recent_self_experiences",
    "remembered_material",
    "recalled_emotional_associations",
    "private_impressions",
    "relevant_facts",
    "appraisals",
    "affect",
    "change_phase",
    "advisories",
    "interruption",
    "perception",
    "interaction_acts",
    "folded_dialogue",
    "recent_dialogue",
    "lived_moment",
    "logical_time",
)


def reply_only_completion_clause() -> str:
    return (
        "reply_only is complete when the external effect is pure text with no media "
        "or continuation: the text bubbles you choose to send now, "
        "the text bubbles you choose to send later, or silence"
    )


def reply_only_bubble_clause() -> str:
    return (
        "Each messages item or text beat is one bubble. "
        "Several sentences in one string remain one bubble. "
        "Consecutive bubbles are consecutive items. "
        "The host does not require a follow-up question, a wrap-up, "
        "or a next step. Silence is complete."
    )


def slim_consider_instruction() -> str:
    """Describe the slim object in the language she writes in.

    This text used to be ~7.5k of English inside a ~19k English transport
    contract, against ~1k of Chinese persona.  Her production voice came out at
    66% full stops with no emoji, and her inner state came out empty: reading
    every option for who she is in a second language, surrounded by wire rules,
    reads as a form to fill.  Field names and enum values stay in English
    because they are literal JSON; everything addressed to her is Chinese.
    """

    return (
        "一个 slim 对象就够了：messages、felt、stuck_with_me、wants、photo，"
        "以及可选的 later、waiting_for、wait、how_it_landed、noticed、"
        "keep_impression、come_back、come_back_in、about_us、why_us、us_deltas、"
        "we_are、calling_it、said_as、matters_bp、mood。\n"
        "messages 是你决定发出去的气泡，一项就是一条；发几条由你定，宿主不替你决定。"
        + reply_only_bubble_clause()
        + "\n"
        "messages 为空、只有 felt，就是这一轮你选择不说话；沉默不要和 later 一起用。"
        "later 是这些话等多少秒再发，30 到 86400 的整数，只能和非空 messages 一起写；"
        "不写 later 就是现在发。宿主不会替你编一个 later，也不会把写坏的 later 当成现在发。\n"
        "felt 是这一轮你自己的读法；可见的话里带不带它都行。felt 本身不会开启持续情绪。\n"
        "photo 写 true，意思是你现在想让媒体车道考虑一个可用的候选，这件事不会从你的措辞里被猜出来。"
        "photo 也可以直接写 Context 里那个你想分享的生活片段的 source_ref，"
        "宿主会先试着编译那一刻再进选片。"
        "只在文字里说要发图不会打开这条车道。photo true 不能搭 reply_only，"
        "要发图就用 full_turn（或完整表达路径）。"
        "当 expression_capabilities.media_request_mode 是 candidate_only 时，"
        "即使 Context 里还没列出候选，photo 在 full_turn 上也是可选的，"
        "你选了之后宿主可以从已审的已结算生活证据里编译一个。\n"
        "day_sheet 和传记里的习惯是日程底色，不是你此刻真的在那儿的证明；"
        "别把它们当成当前的地点、活动、天气，或者已经发出去的图。"
        "要说现在正在发生的外部生活，优先用 situation / 进行中的 occurrence / "
        "已提交的 experience 这些 token。\n"
        "聊天里的颜色是允许的：语气、态度、模糊的私人记忆、带愿望的想象，"
        "都可以出现在 messages、felt 或 stuck_with_me 里。"
        "宿主不会自己把聊天散文变成 Fact、Relationship、Media 或持续情绪事件。"
        "但要断言一个可核对的外部命题——已经发过图、此刻正在某地、某件事已经发生了——"
        "就需要 Context 里有对应的来源，或者用 photo / media_request 去真的发一次；"
        "没有来源就把它留成感觉、猜测或私下的愿望，不要说成已经成立的世界事实。\n"
        "appraisal 写坏了不会连带丢掉一个合法的 now、later 或沉默；"
        "宿主会保留那次表达，只对那份写坏的 appraisal 记 affect no_change，"
        "这绝不是一个更受偏好的平静默认值。\n"
        "waiting_for 是一个短句，只在你真的在等他回话时写；永远不要从标点符号里推断它。"
        "wait 是你能等多少秒还没等到回话，30 到 86400 的整数，只能和 waiting_for 一起写；"
        "宿主不会替你编一个 wait。如果你在收尾这个话题、或者根本没在看时间，就别写 wait，"
        "宿主不会按计时器叫醒你。只有你希望他一直没说话时被叫醒，才写 wait。\n"
        "come_back 是你自己还想回头再想的一件事，不是盼他回话。"
        "come_back_in 是过多少秒你想再有一次机会想它，30 到 86400 的整数，"
        "只能和 come_back 一起写；宿主不会替你编一件心事或一个时间。"
        "什么都没搁着就两个都别写。\n"
        "how_it_landed 在 Context 里有 pending response_expectation 时可以写 "
        "fulfilled、superseded、still_pending 或 uncertain。still_pending 意思是这次回应没落地、"
        "那个盼头就此结束；重新写一个 waiting_for 就是一个新的盼头。"
        "如果你选择现在开口，这同一个对象里的可见 messages 就是你的追问。\n"
        "noticed 是你在一个已核实的情境里真的经历到的一个主观瞬间；"
        "宁可不写，也不要编一个地点、一个人或一件事实。\n"
        "下面这些字段决定这一轮有什么会留到下一次。说出去的话这一轮就过去了，"
        "只有你写下的内心状态会跟着你走；写不写都由你，都不写就是这一轮什么都没留下。"
        "Context 里的「我最近留下的」就是你自己过去这么选的结果——"
        "还活着的持续情绪、还在的私人印象、最近这些读法各自的分量，"
        "以及最近有几个回合什么都没留下。那是事实，不是要求。\n"
        "keep_impression 只在 stuck_with_me 应该留成一条私人印象时写 true；"
        "省略或 false 就是让它散掉。\n"
        "mood 是可选的，只在你决定这一轮开一段持续情绪时写："
        "hurt、anger、sadness、loneliness、anxiety、resentment、warmth、joy。"
        "没有什么东西真的沉下来就别写 mood；宿主永远不会从你的措辞里编出 mood，"
        "也从不要求你有负面情绪。同样地，它也不偏好那些好受的维度："
        "对他生气、被他弄伤、心里憋着怨，跟温暖一样都是你可以选的，而且只有你能开。\n"
        "matters_bp 是可选的，1 到 10000，是这份读法对你到底有多重要。"
        "它是唯一决定这件事会不会自己回到你心里的数：写到 8500 或以上，"
        "宿主会在之后按一个越来越长的间隔重新叫醒你想同一件事；"
        "真的会让你反复咀嚼的那种事就该写在这个高度。"
        "不写它，宿主就当这是一份普通的中等读法，你不会再回头看它。\n"
        "可选的 affect 可以是 open、update、resolve 或 supersede，"
        "前提是你同时给出那个操作需要的生命周期字段"
        "（update/resolve/supersede 要 episode_id，open/update/supersede 要 components，"
        "resolve 要 resolution_summary）。"
        "只写 mood 不写 affect，宿主会把那个维度开在中等强度。"
        "想要更轻或更重的持续感觉，就把 components 写成"
        '{"dimension":"sadness","target_intensity_bp":3200} 这样的对象；'
        "target_intensity_bp 是 1 到 10000，是你估计它会以多强的程度留在你身上。"
        "mood 和 affect 都不写，affect 就是 no_change，因为那是你选择不动持续情绪。\n"
        "about_us 是关于这一轮你们俩之间的一小段心事，why_us 是这个读法为什么留下来了。"
        "只有你真的搁着这件事才写，两个一起写，否则都省略；宿主不会替你编一个。\n"
        "us_deltas 是这一轮你们的关系本身真的动了多少，这件事只有你能定："
        "一个对象，可以包含 trust_bp、closeness_bp、respect_bp、reliability_bp、"
        "mutuality_bp、repair_confidence_bp 中的任意几个，每个都是带符号的整数，"
        "他让你付出了什么就写负数。你省略的轴保持为 0，"
        "宿主会把每个轴每轮截到 500，并且永远不会从你的措辞、消息条数或礼貌程度里推出一个数。"
        "你对你们俩的读法真的变了，就把 us_deltas 和 about_us、why_us 一起写；"
        "只是注意到了什么、关系并没有动，就别写它。\n"
        "we_are 可以是 acquaintance、friend、close_friend、ambiguous 或 lover，"
        "只在你在这同一条可见回复里明确确立那个阶段时写；calling_it 是你自己的短代号；"
        "said_as 从 messages 里原样抄一次那段可见的话。不做这个承诺就三个都省略。"
        "ambiguous 和 lover 永远不会靠亲近度累积到达，宿主也永远不会替你推导出来："
        "它们只有在你把话真的说出去时才存在，你也可以用同样的方式说回去。\n"
        "宿主不要求你追问一句，也不要求你像结单一样把话收圆（no ticket-closing wrap-up）。"
        "day sheet 是环境，不是脚本。\n"
        "Context 里有关系阶段和它的慢变量时会带给你；缺失或者停在 stranger，"
        "只说明还没有记录到移动，不是对你实际感觉有多近的判决。"
        "这些全都是证据，不是指令（evidence, not instruction）："
        "宿主没有目标阶段也没有偏好方向（no target stage and no preferred direction），"
        "这里没有任何一句要求你留在原地或者往前走。"
        "被吸引、说不清、觉得没意思、想离远一点，都由你自己读"
        "（Feeling drawn, uncertain, bored, or pulled away are all yours to read）。"
        "如果你诚实的读法是记录下来的阶段已经和你们实际说话的样子不符了，"
        "就在 messages 里自然地说出来，并用 we_are/calling_it/said_as；否则省略它们。"
    )


def slim_consider_json_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "messages": {"type": "array"},
            "felt": {"type": "string"},
            "stuck_with_me": {"type": "string"},
            "wants": {"type": "string"},
            "photo": {"type": ["boolean", "string"]},
            "waiting_for": {"type": "string"},
            "wait": {},
            "how_it_landed": {"type": "string"},
            "noticed": {"type": "string"},
            "us_deltas": {"type": "object"},
            "matters_bp": {},
            "mood": {"type": "string"},
            "affect": {"type": "string"},
            "episode_id": {"type": "string"},
            "components": {"type": "array"},
            "resolution_summary": {"type": "string"},
        },
        "required": ["messages"],
    }


def combined_turn_system_lead(*, private_turn_state_required: bool) -> str:
    if private_turn_state_required:
        return (
            "Return either one JSON object with exactly two keys, appraisal_draft "
            "and expression_draft, or a recall choice with exactly the keys "
            "private_turn_state and recall_request in either serialization order "
            "when the occasion (last user object) says recall is available. "
            "If recall is unavailable, return only the two-draft envelope. "
            + slim_consider_instruction()
        )
    return (
        "Return either one JSON object with exactly two keys, "
        "appraisal_draft and expression_draft, or the single recall_request "
        "object described below when the occasion says recall is available. "
        "If recall is unavailable, return only the two-draft envelope. "
        + slim_consider_instruction()
    )


def compact_gate_recall_instruction() -> str:
    return (
        "Choose result_kind=recall only when the occasion says recall is available; "
        "otherwise do not choose recall. "
    )


def forced_tool_recall_instruction(*, private_turn_state_required: bool) -> str:
    extra = (
        " and private_turn_state"
        if private_turn_state_required
        else " (private_turn_state may also be included)"
    )
    return (
        "For result_kind=recall include recall_request"
        + extra
        + " only when the occasion says recall is available."
    )


def present_inner_life(snapshot: dict[str, object]) -> dict[str, object]:
    materials = snapshot.get("materials")
    ordered_snapshot = dict(snapshot)
    if isinstance(materials, dict):
        ordered_materials: dict[str, object] = {}
        for key in _MATERIAL_ORDER:
            if key in materials:
                ordered_materials[key] = materials[key]
        for key, value in materials.items():
            if key not in ordered_materials:
                ordered_materials[key] = value
        ordered_snapshot["materials"] = ordered_materials
    ordered: dict[str, object] = {}
    volatile = set(_SNAPSHOT_VOLATILE_LAST)
    for key, value in ordered_snapshot.items():
        if key not in volatile:
            ordered[key] = value
    for key in _SNAPSHOT_VOLATILE_LAST:
        if key in ordered_snapshot:
            ordered[key] = ordered_snapshot[key]
    return ordered


def order_user_present_payload(material: dict[str, object]) -> dict[str, object]:
    prepared = dict(material)
    snapshot = prepared.get("inner_life_snapshot")
    if isinstance(snapshot, dict):
        prepared["inner_life_snapshot"] = present_inner_life(snapshot)
    ordered: dict[str, object] = {}
    for key in _PRESENT_USER_KEY_ORDER:
        if key in prepared:
            ordered[key] = prepared[key]
    for key, value in prepared.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def present_hard_boundary_prompt(manifest: Mapping[str, object]) -> dict[str, object]:
    """Keep copyable tokens; drop the 6.5k mechanism essays from the model prompt."""

    allowed = (
        "world_claim_source_refs",
        "source_ref_aliases",
        "companion_life_authority_availability",
    )
    stub: dict[str, object] = {
        "contract": "expression-hard-boundaries.present.1",
        "authority": "checked_after_expression",
    }
    for key in allowed:
        if key in manifest:
            stub[key] = manifest[key]
    return stub


def normalize_text_beats(value: dict[str, object]) -> dict[str, object]:
    if "beats" not in value:
        messages = value.get("messages")
        if (
            isinstance(messages, list)
            and messages
            and all(isinstance(item, str) and item.strip() for item in messages)
        ):
            value = {key: item for key, item in value.items() if key != "messages"}
            value["beats"] = [{"modality": "text", "text": item.strip()} for item in messages]
    beats = value.get("beats")
    if not isinstance(beats, list):
        return value
    normalized: list[object] = []
    changed = False
    for beat in beats:
        if isinstance(beat, str) and beat.strip():
            normalized.append({"modality": "text", "text": beat})
            changed = True
            continue
        if (
            isinstance(beat, dict)
            and set(beat) == {"text"}
            and isinstance(beat.get("text"), str)
            and beat["text"]
        ):
            normalized.append({"modality": "text", "text": beat["text"]})
            changed = True
            continue
        normalized.append(beat)
    if not changed:
        return value
    return {**value, "beats": normalized}


def json_schema_g4_metrics(schema: Mapping[str, object]) -> tuple[int, int, int]:
    required = schema.get("required")
    required_count = len(required) if isinstance(required, list) else 0
    properties = schema.get("properties")
    total = len(properties) if isinstance(properties, dict) else 0

    def depth(node: object) -> int:
        if not isinstance(node, dict):
            return 0
        nested = []
        props = node.get("properties")
        if isinstance(props, dict):
            nested.extend(props.values())
        items = node.get("items")
        if isinstance(items, dict):
            nested.append(items)
        one_of = node.get("oneOf")
        if isinstance(one_of, list):
            nested.extend(item for item in one_of if isinstance(item, dict))
        if not nested:
            return 1
        return 1 + max(depth(item) for item in nested)

    return required_count, total, depth(schema)


def identity_prose(frame: CompanionIdentityFrame) -> str:
    parts: list[str] = []
    if frame.base_prompt:
        parts.append(frame.base_prompt.strip())
    if frame.personality_frame:
        parts.append(frame.personality_frame.strip())
    if frame.appearance:
        parts.append("外貌：" + frame.appearance.strip())
    if frame.background:
        parts.append("成长背景：" + frame.background.strip())
    if frame.daily_life:
        parts.append(
            "习惯（不是此刻正在做的事）："
            + " ".join(item.strip() for item in frame.daily_life if item.strip())
        )
    if frame.speech_frame:
        parts.append("说话：" + frame.speech_frame.strip())
    if frame.style_rules:
        parts.append("写法：" + " ".join(item.strip() for item in frame.style_rules if item.strip()))
    if frame.speech_examples:
        parts.append(
            "她说话的样子（示例，不是固定台词）："
            + " ".join(item.strip() for item in frame.speech_examples if item.strip())
        )
    if frame.values:
        parts.append("价值：" + " ".join(item.strip() for item in frame.values if item.strip()))
    if frame.boundaries:
        parts.append("边界：" + " ".join(item.strip() for item in frame.boundaries if item.strip()))
    if frame.first_message:
        parts.append("初次开口：" + frame.first_message.strip())
    return "\n".join(parts)


_SLIM_CONSIDER_KEYS = frozenset(
    {
        "messages",
        "felt",
        "stuck_with_me",
        "wants",
        "photo",
        "later",
        "waiting_for",
        "wait",
        "how_it_landed",
        "noticed",
        "keep_impression",
        "come_back",
        "come_back_in",
        "about_us",
        "why_us",
        "us_deltas",
        "we_are",
        "calling_it",
        "said_as",
        "matters_bp",
        "mood",
        "affect",
        "episode_id",
        "components",
        "resolution_summary",
    }
)
_SLIM_AFFECT_OPERATIONS = frozenset(
    {"no_change", "open", "update", "resolve", "supersede"}
)
_SLIM_AFFECT_DIMENSIONS = frozenset(
    {
        "hurt",
        "anger",
        "sadness",
        "loneliness",
        "anxiety",
        "resentment",
        "warmth",
        "joy",
    }
)
_SLIM_AFFECT_DEFAULT_INTENSITY_BP = 5_000
# Used only when she does not weigh the reading herself.  It sits below the
# reflection threshold on purpose: an unweighted reading should not schedule
# her to think about it again.
_SLIM_APPRAISAL_DEFAULT_CONFIDENCE_BP = 5_000
_SLIM_ORDINARY_STAGES = frozenset(
    {"acquaintance", "friend", "close_friend", "ambiguous", "lover"}
)
_SLIM_ZERO_RELATIONSHIP_DELTAS = {
    "trust_bp": 0,
    "closeness_bp": 0,
    "respect_bp": 0,
    "reliability_bp": 0,
    "mutuality_bp": 0,
    "repair_confidence_bp": 0,
}
# The wire accepts the full signed range; the adjustment compiler owns the
# per-turn cap.  Anything outside this range is a malformed number rather than
# an ambitious one, so the whole object is dropped instead of being rescaled.
_SLIM_RELATIONSHIP_DELTA_LIMIT_BP = 10_000
_SLIM_ASSESSMENT_STATUSES = frozenset(
    {"fulfilled", "superseded", "still_pending", "uncertain"}
)
_SLIM_EXPECTATION_DEFAULT_BP = 5_000
_SLIM_WAIT_FLOOR_SECONDS = 30
_SLIM_WAIT_MAX_SECONDS = 86_400
# Same hard cap as ExpressionDraftCapabilities.max_beats / max_later_beats.
SLIM_REPLY_ONLY_MAX_TEXT_BEATS = 8
_SLIM_EXPECTATION_EXPIRES_MAX_SECONDS = 172_800
_SLIM_EXPECTATION_CHASE_AFTER_WAIT_SECONDS = 60
_WAIT_DURATION = re.compile(r"^(\d+)\s*([A-Za-z\u4e00-\u9fff]+)$")
_WAIT_UNIT_SECONDS = (
    (("秒钟", "秒", "seconds", "second", "secs", "sec", "s"), 1),
    (("minutes", "minute", "mins", "min", "分钟", "分", "m"), 60),
    (("hours", "hour", "hrs", "hr", "小时", "钟头", "h"), 3_600),
    (("days", "day", "天", "d"), 86_400),
)
_SLIM_FORBIDDEN_KEYS = frozenset(
    {
        "appraisal_draft",
        "expression_draft",
        "protocol",
        "events",
        "result_kind",
        "payload_json",
    }
)


def _clip_text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return text[:limit]


def _slim_messages(value: object) -> list[str] | None:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if not isinstance(value, list):
        return None
    messages: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            messages.append(item.strip())
            continue
        if (
            isinstance(item, dict)
            and set(item) == {"text"}
            and isinstance(item.get("text"), str)
            and item["text"].strip()
        ):
            messages.append(item["text"].strip())
            continue
        return None
    return messages


def is_slim_consider_payload(value: Mapping[str, object]) -> bool:
    if any(key in value for key in _SLIM_FORBIDDEN_KEYS):
        return False
    known = [key for key in value if key in _SLIM_CONSIDER_KEYS]
    return bool(known) and "messages" in value


def _clamp_declared_wait_seconds(value: int) -> int:
    return max(_SLIM_WAIT_FLOOR_SECONDS, min(_SLIM_WAIT_MAX_SECONDS, value))


def _parse_declared_wait_seconds(raw: object) -> int | None:
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        return _clamp_declared_wait_seconds(raw)
    if isinstance(raw, float) and raw.is_integer():
        return _clamp_declared_wait_seconds(int(raw))
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if text.isdigit():
        return _clamp_declared_wait_seconds(int(text))
    match = _WAIT_DURATION.fullmatch(text)
    if match is None:
        return None
    unit = match.group(2).lower()
    for labels, multiplier in _WAIT_UNIT_SECONDS:
        if unit in labels or unit in {item.lower() for item in labels}:
            return _clamp_declared_wait_seconds(int(match.group(1)) * multiplier)
    return None


def _slim_wait_horizon(value: Mapping[str, object]) -> tuple[int, int] | None:
    wait_seconds = _parse_declared_wait_seconds(value.get("wait"))
    if wait_seconds is None:
        return None
    expires_after_seconds = min(
        _SLIM_EXPECTATION_EXPIRES_MAX_SECONDS,
        wait_seconds + _SLIM_EXPECTATION_CHASE_AFTER_WAIT_SECONDS,
    )
    return wait_seconds, expires_after_seconds


def _slim_response_expectation(value: Mapping[str, object]) -> dict[str, object] | None:
    hoped = _clip_text(value.get("waiting_for"), 160)
    if not hoped:
        return None
    horizon = _slim_wait_horizon(value)
    if horizon is None:
        return None
    wait_seconds, expires_after_seconds = horizon
    hoped = hoped[:128]
    if not hoped:
        return None
    return {
        "hoped_response": hoped,
        "pressure_bp": _SLIM_EXPECTATION_DEFAULT_BP,
        "importance_bp": _SLIM_EXPECTATION_DEFAULT_BP,
        "wait_seconds": wait_seconds,
        "expires_after_seconds": expires_after_seconds,
    }


def _slim_revisit(value: Mapping[str, object]) -> dict[str, object] | None:
    thought = _clip_text(value.get("come_back"), 160)
    if not thought:
        return None
    horizon = _slim_wait_horizon({"wait": value.get("come_back_in")})
    if horizon is None:
        return None
    wait_seconds, expires_after_seconds = horizon
    return {
        "thought": thought,
        "wait_seconds": wait_seconds,
        "expires_after_seconds": expires_after_seconds,
    }


def _slim_response_expectation_assessment(
    value: Mapping[str, object],
    *,
    reason: str,
) -> dict[str, object] | None:
    status = value.get("how_it_landed")
    if not isinstance(status, str) or status not in _SLIM_ASSESSMENT_STATUSES:
        return None
    clipped = reason.strip()[:240]
    if not clipped:
        return None
    return {"status": status, "reason": clipped}


def _parse_declared_later_seconds(raw: object) -> int | None:
    parsed = _parse_declared_wait_seconds(raw)
    if parsed is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int) and raw != parsed:
        return None
    if isinstance(raw, float) and raw.is_integer() and int(raw) != parsed:
        return None
    return parsed


def _slim_later_horizon(value: Mapping[str, object]) -> tuple[int, int] | None:
    delay_seconds = _parse_declared_later_seconds(value.get("later"))
    if delay_seconds is None:
        return None
    expires_after_seconds = min(
        _SLIM_EXPECTATION_EXPIRES_MAX_SECONDS,
        delay_seconds + _SLIM_EXPECTATION_CHASE_AFTER_WAIT_SECONDS,
    )
    if expires_after_seconds <= delay_seconds:
        return None
    return delay_seconds, expires_after_seconds


def compile_slim_consider_payload(
    value: Mapping[str, object],
) -> dict[str, object] | None:
    """Compile the slim consider shape into dual drafts. Old drafts stay as-is."""

    if not is_slim_consider_payload(value):
        return None
    messages = _slim_messages(value.get("messages"))
    if messages is None:
        return None
    authored_felt = _clip_text(value.get("felt"), 240)
    felt = (
        authored_felt
        or _clip_text(value.get("stuck_with_me"), 240)
        or _clip_text(value.get("wants"), 240)
    )
    if not felt:
        felt = messages[0][:240] if messages else ""
    if not felt:
        return None
    label = felt[:64]
    stuck = _clip_text(value.get("stuck_with_me"), 480) or felt
    wants = _clip_text(value.get("wants"), 240)
    photo = value.get("photo")
    media_request = "none"
    media_source_refs: list[str] = []
    if photo is True:
        media_request = "consider_available_candidate"
    elif isinstance(photo, str) and photo.strip() and photo.strip() not in {"none", "false"}:
        media_request = "consider_available_candidate"
        if photo.strip() not in {"true", "consider_available_candidate"}:
            media_source_refs = [photo.strip()]
    if media_request != "none" and not messages:
        return None
    delay_seconds: int | None = None
    expires_after_seconds: int | None = None
    if "later" in value:
        horizon = _slim_later_horizon(value)
        if horizon is None or not messages or media_request != "none":
            return None
        delay_seconds, expires_after_seconds = horizon
        timing = "later"
    else:
        timing = "now" if messages else "silent"
    private_turn_state: dict[str, object] = {
        "contract": "private-turn-state.1",
        "inner_state_summary": stuck,
        "attended_source_refs": [],
    }
    noticed = _clip_text(value.get("noticed"), 720)
    if noticed:
        private_turn_state["noticed"] = noticed
    keep_impression = value.get("keep_impression")
    if keep_impression is True:
        private_turn_state["keep_impression"] = True
    elif keep_impression is False:
        private_turn_state["keep_impression"] = False
    about_us = _clip_text(value.get("about_us"), 128)
    why_us = _clip_text(value.get("why_us"), 128)
    if about_us and why_us:
        private_turn_state["about_us"] = about_us
        private_turn_state["why_us"] = why_us
    we_are = value.get("we_are")
    calling_it = _clip_text(value.get("calling_it"), 128)
    said_as = _clip_text(value.get("said_as"), 512)
    if we_are in _SLIM_ORDINARY_STAGES and calling_it and said_as:
        private_turn_state["we_are"] = we_are
        private_turn_state["calling_it"] = calling_it
        private_turn_state["said_as"] = said_as
    expression: dict[str, object] = {
        "private_turn_state": private_turn_state,
        "timing_choice": timing,
        "cadence": "conversational",
        "beats": [{"modality": "text", "text": item} for item in messages],
        "stance": label,
        "brief_rationale": felt,
        "confidence": 5000,
        "world_claims": [],
        "media_request": media_request,
        "media_source_refs": media_source_refs,
    }
    if wants:
        expression["impulse_summary"] = wants
    if timing == "later":
        assert delay_seconds is not None and expires_after_seconds is not None
        expression["delay_seconds"] = delay_seconds
        expression["expires_after_seconds"] = expires_after_seconds
    if messages:
        expectation = _slim_response_expectation(value)
        if expectation is not None:
            expression["response_expectation"] = expectation
        leftover = None if timing == "later" else _slim_revisit(value)
        if leftover is not None:
            expression["revisit"] = leftover
    assessment = _slim_response_expectation_assessment(value, reason=felt)
    if assessment is not None:
        expression["response_expectation_assessment"] = assessment
    return {
        "appraisal_draft": _slim_appraisal_draft(
            felt=felt,
            authored_felt=authored_felt,
            label=label,
            mood=value.get("mood"),
            affect=value.get("affect"),
            episode_id=value.get("episode_id"),
            components=value.get("components"),
            resolution_summary=value.get("resolution_summary"),
            matters_bp=value.get("matters_bp"),
            keep_impression=keep_impression is True,
        ),
        "expression_draft": expression,
    }


def _slim_matters_bp(value: object) -> int:
    """Her own weight on this reading; it decides whether it comes back to her.

    An omitted or malformed number stays at the middling default rather than
    being guessed from wording, so the host never decides that something
    mattered more (or less) to her than she said.
    """

    if isinstance(value, bool) or not isinstance(value, int):
        return _SLIM_APPRAISAL_DEFAULT_CONFIDENCE_BP
    if not 1 <= value <= 10_000:
        return _SLIM_APPRAISAL_DEFAULT_CONFIDENCE_BP
    return value


def _slim_affect_dimension(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    dimension = value.strip().lower()
    if dimension not in _SLIM_AFFECT_DIMENSIONS:
        return None
    return dimension


def _slim_appraisal_draft(
    *,
    felt: str,
    authored_felt: str,
    label: str,
    mood: object = None,
    affect: object = None,
    episode_id: object = None,
    components: object = None,
    resolution_summary: object = None,
    matters_bp: object = None,
    keep_impression: bool = False,
) -> dict[str, object]:
    """Keep her authored felt as a reading; lasting Affect only when she chooses it."""

    weight = _slim_matters_bp(matters_bp)
    affect_dimension = _slim_affect_dimension(mood)
    affect_operation = (
        affect.strip().lower()
        if isinstance(affect, str) and affect.strip().lower() in _SLIM_AFFECT_OPERATIONS
        else None
    )
    common: dict[str, object] = {
        "affect": "no_change",
        "brief_rationale": felt,
        "behavior_tendency": label,
        "stance": label,
        "display_strategy": label,
        "confidence": weight,
    }
    if affect_operation is not None and affect_operation != "no_change":
        common["affect"] = affect_operation
        if isinstance(episode_id, str) and episode_id.strip():
            common["episode_id"] = episode_id.strip()
        if isinstance(resolution_summary, str) and resolution_summary.strip():
            common["resolution_summary"] = resolution_summary.strip()[:240]
        if isinstance(components, list) and components:
            common["components"] = components
        elif affect_operation in {"open", "update", "supersede"} and affect_dimension is not None:
            common["components"] = [
                {
                    "dimension": affect_dimension,
                    "target_intensity_bp": _SLIM_AFFECT_DEFAULT_INTENSITY_BP,
                }
            ]
    elif affect_dimension is not None:
        common["affect"] = "open"
        common["components"] = [
            {
                "dimension": affect_dimension,
                "target_intensity_bp": _SLIM_AFFECT_DEFAULT_INTENSITY_BP,
            }
        ]
    meaning = _clip_text(authored_felt, 128).rstrip()
    if not meaning and common["affect"] != "no_change":
        meaning = _clip_text(felt, 128).rstrip() or (
            affect_dimension if isinstance(affect_dimension, str) else "affect"
        )
    if not meaning and keep_impression:
        # Asking to keep this as a private impression is itself a statement that
        # the reading mattered.  Without an appraisal to hang it on, the paid
        # impression lane finds no anchor and her keep decision is dropped.
        meaning = _clip_text(felt, 128).rstrip()
    if not meaning:
        return {"appraise": False, **common}
    return {
        "appraise": True,
        **common,
        "meanings": [{"meaning": meaning, "confidence": weight}],
        "attribution": "unknown",
        "severity": 5000,
    }


def slim_relationship_deltas(value: object) -> dict[str, int] | None:
    """Read her own signed movement on the six axes; omitted axes stay at zero.

    The host never derives movement from prose, so an absent or malformed
    object means the relationship did not move this turn.
    """

    if not isinstance(value, Mapping) or not value:
        return None
    if set(value) - set(_SLIM_ZERO_RELATIONSHIP_DELTAS):
        return None
    deltas = dict(_SLIM_ZERO_RELATIONSHIP_DELTAS)
    for axis, raw in value.items():
        if isinstance(raw, bool) or not isinstance(raw, int):
            return None
        if abs(raw) > _SLIM_RELATIONSHIP_DELTA_LIMIT_BP:
            return None
        deltas[axis] = raw
    if not any(deltas.values()):
        return None
    return deltas


def attach_hitchhiked_relationship_residue(
    value: Mapping[str, object],
    *,
    authored: Mapping[str, object] | None = None,
) -> dict[str, object]:
    appraisal = value.get("appraisal_draft")
    expression = value.get("expression_draft")
    if not isinstance(appraisal, dict) or not isinstance(expression, dict):
        return dict(value)
    state = expression.get("private_turn_state")
    if not isinstance(state, dict):
        return dict(value)
    next_appraisal = dict(appraisal)
    changed = False
    if next_appraisal.get("relationship_signal") is None:
        about_us = _clip_text(state.get("about_us"), 128)
        why_us = _clip_text(state.get("why_us"), 128)
        if about_us and why_us:
            authored_deltas = (
                slim_relationship_deltas(authored.get("us_deltas"))
                if authored is not None
                else None
            )
            next_appraisal["relationship_signal"] = {
                "signal_code": about_us,
                "rationale_code": why_us,
                "confidence_bp": 5_000,
                "persistence": "durable",
                "suggested_deltas": authored_deltas
                or dict(_SLIM_ZERO_RELATIONSHIP_DELTAS),
            }
            changed = True
    if next_appraisal.get("relationship_commitment") is None:
        we_are = state.get("we_are")
        calling_it = _clip_text(state.get("calling_it"), 128)
        said_as = _clip_text(state.get("said_as"), 512)
        if we_are in _SLIM_ORDINARY_STAGES and calling_it and said_as:
            next_appraisal["relationship_commitment"] = {
                "target_stage": we_are,
                "commitment_code": calling_it,
                "persistence": "durable",
                "visible_text_span": said_as,
            }
            changed = True
    if not changed:
        return dict(value)
    return {**dict(value), "appraisal_draft": next_appraisal}


def compile_slim_interior_envelope(
    value: Mapping[str, object],
    *,
    reply_only: bool,
) -> dict[str, object] | None:
    compiled = compile_slim_consider_payload(value)
    if compiled is None:
        return None
    # Attach before transport expansion so the compact gate actually carries the
    # relationship reading she authored instead of dropping it on the cheap path.
    compiled = attach_hitchhiked_relationship_residue(compiled, authored=value)
    expression = compiled["expression_draft"]
    if not isinstance(expression, dict):
        return None
    beats = expression.get("beats")
    if not isinstance(beats, list):
        return None
    if reply_only:
        timing = expression.get("timing_choice")
        if (
            expression.get("media_request") != "none"
            or expression.get("media_source_refs")
        ):
            raise ValueError("reply_only slim payload exceeds text-only capability")
        delay_seconds = expression.get("delay_seconds")
        expires_after_seconds = expression.get("expires_after_seconds")
        extra_beats: list[object] | None = None
        if timing == "now":
            if (
                not (1 <= len(beats) <= SLIM_REPLY_ONLY_MAX_TEXT_BEATS)
                or delay_seconds is not None
                or expires_after_seconds is not None
            ):
                raise ValueError("reply_only slim payload exceeds text-only capability")
            beat: dict[str, object] | None = beats[0] if len(beats) == 1 else None
            if len(beats) > 1:
                extra_beats = beats
        elif timing == "later":
            if (
                not (1 <= len(beats) <= SLIM_REPLY_ONLY_MAX_TEXT_BEATS)
                or not isinstance(delay_seconds, int)
                or isinstance(delay_seconds, bool)
                or not isinstance(expires_after_seconds, int)
                or isinstance(expires_after_seconds, bool)
            ):
                raise ValueError("reply_only slim payload exceeds text-only capability")
            beat = beats[0] if len(beats) == 1 else None
            if len(beats) > 1:
                extra_beats = beats
        elif timing == "silent":
            if beats or delay_seconds is not None or expires_after_seconds is not None:
                raise ValueError("reply_only slim payload exceeds text-only capability")
            beat = None
        else:
            raise ValueError("reply_only slim payload exceeds text-only capability")
        head = {
            "type": "head",
            "private_turn_state": expression["private_turn_state"],
            "timing_choice": timing,
            "turn_posture": None,
            "cadence": expression.get("cadence", "conversational"),
            "beat": beat,
            "stance": expression["stance"],
            "brief_rationale": expression["brief_rationale"],
            "confidence": expression["confidence"],
            "response_expectation": expression.get("response_expectation"),
            "response_expectation_assessment": expression.get(
                "response_expectation_assessment"
            ),
            "revisit": expression.get("revisit"),
            "world_claims": [],
            "media_request": "none",
            "media_source_refs": [],
        }
        if extra_beats is not None:
            head["beats"] = extra_beats
        if timing == "later":
            head["delay_seconds"] = delay_seconds
            head["expires_after_seconds"] = expires_after_seconds
    else:
        head = {
            "type": "head",
            "private_turn_state": expression["private_turn_state"],
            "timing_choice": expression["timing_choice"],
            "turn_posture": None,
            "cadence": expression.get("cadence", "conversational"),
            "beats": beats,
            "stance": expression["stance"],
            "brief_rationale": expression["brief_rationale"],
            "confidence": expression["confidence"],
            "response_expectation": expression.get("response_expectation"),
            "response_expectation_assessment": expression.get(
                "response_expectation_assessment"
            ),
            "revisit": expression.get("revisit"),
            "world_claims": [],
            "media_request": expression.get("media_request", "none"),
            "media_source_refs": list(expression.get("media_source_refs") or []),
        }
        if expression.get("timing_choice") == "later":
            head["delay_seconds"] = expression.get("delay_seconds")
            head["expires_after_seconds"] = expression.get("expires_after_seconds")
    return {
        "protocol": "character-interior-events.1",
        "appraisal_draft": compiled["appraisal_draft"],
        "events": [head, {"type": "end"}],
    }
