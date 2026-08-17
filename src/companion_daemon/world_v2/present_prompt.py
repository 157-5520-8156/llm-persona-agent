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
        "reply_only is complete when the external effect is pure text with no media, "
        "relationship update, or continuation: the text bubbles you choose to send now, "
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
    return (
        "A slim object with messages, felt, stuck_with_me, wants, photo, "
        "optional later, optional waiting_for, optional wait, optional how_it_landed, "
        "optional noticed, "
        "optional keep_impression, optional come_back, optional come_back_in, "
        "optional about_us, optional why_us, optional we_are, optional calling_it, "
        "optional said_as, and optional mood "
        "is complete. "
        "later is how many seconds to wait before sending those messages, "
        "an integer from 30 through 86400, only with a non-empty messages. "
        "The host compiles that delay and its expiry; omit later to send now. "
        "messages is the visible bubbles you choose, each item one bubble; "
        "the host does not choose how many. "
        + reply_only_bubble_clause()
        + " "
        "Empty messages with felt is silence; do not combine later with silence. "
        "The host will not invent a later, and will not turn a broken later into now. "
        "photo true means you want the media lane to consider an available candidate "
        "now; it is not inferred from wording. Saying you will send a picture inside "
        "text alone does not open that lane. photo true cannot ride reply_only; use "
        "full_turn (or the full expression path) when you set photo. "
        "When expression_capabilities.media_request_mode is candidate_only, photo is "
        "an available choice on full_turn even if Context does not list a candidate yet; "
        "the host may compile one from reviewed settled life evidence after you choose it. "
        "day_sheet and biographical habits are schedule texture, not proof you are there "
        "now; do not treat them as the current place, activity, weather, or a photo already "
        "sent. Prefer situation / active occurrence / committed experience tokens for "
        "present-tense external life. "
        "Chat color is allowed: tone, attitude, fuzzy private memory, and wishful texture "
        "may appear in messages, felt, or stuck_with_me. The host does not turn chat prose "
        "into Fact, Relationship, Media, or lasting Affect events by itself. Asserting a "
        "checkable external proposition—already sent a picture, currently at a place, or "
        "that something already happened—needs matching pinned Context, or photo/media_request "
        "for a real send attempt; without that source, keep it as feeling, guess, or private "
        "wish, not a settled World fact. "
        "A broken appraisal does not discard a legal now, later, or silent head; "
        "the host keeps that expression and records affect no_change only for that broken "
        "appraisal, never as a preferred calm default. "
        "waiting_for is a short string only when you genuinely expect a reply; "
        "never infer it from punctuation. wait is how many seconds you can wait "
        "without an answer, an integer from 30 through 86400, only with waiting_for; "
        "the host will not invent a wait for you. Omit wait if you are ending "
        "the topic or not watching the clock; the host will not wake you on a "
        "timer. Fill wait only when you want to be woken if he has not spoken. "
        "come_back is a short leftover you still want to return to later; it is "
        "not a hope that he will reply. come_back_in is how many seconds until "
        "you want another chance to think about that leftover, an integer from "
        "30 through 86400, only with come_back; the host will not invent a "
        "leftover or a time. Omit both if you are not holding anything. "
        "how_it_landed is fulfilled, superseded, still_pending, or "
        "uncertain when Context has a pending response_expectation. still_pending "
        "means this reply did not land and closes that hope; a new waiting_for is "
        "a new hope. Visible messages in this same object are your follow-up if you "
        "choose to speak now. noticed is a short subjective moment you actually lived "
        "in a verified situation; omit it rather than inventing a place, person, or "
        "fact. keep_impression is true only when stuck_with_me should remain as a "
        "private impression; omit or false drops it. "
        "about_us is a short leftover about how this turn sits between you two; "
        "why_us is why that reading stuck. Fill both only when you are actually "
        "holding that leftover; omit both otherwise. The host will not invent one "
        "and will not change relationship scores from these words. "
        "we_are is acquaintance, friend, or close_friend only when you explicitly "
        "establish that ordinary stage in this same visible reply; calling_it is "
        "your own short code; said_as copies that visible span exactly once from "
        "messages. Omit all three if you do not make that commitment. "
        "felt is this turn's private reading; visible text may carry it or leave it private. "
        "felt alone does not open lasting Affect. "
        "mood is optional and only when you choose a lasting Affect component this turn: "
        "hurt, anger, sadness, loneliness, anxiety, resentment, warmth, or joy. "
        "Omit mood when nothing lasting shifted; the host never invents mood from wording "
        "and never requires a negative mood. "
        "Optional affect may be open, update, resolve, or supersede when you also supply "
        "the lifecycle fields that operation needs (episode_id for update/resolve/supersede, "
        "components for open/update/supersede, resolution_summary for resolve). "
        "If you set mood without affect, the host opens that dimension; if you omit both, "
        "affect is no_change because you chose not to shift lasting Affect. "
        "The host does not require a follow-up question or a ticket-closing wrap-up. "
        "The day sheet is environment, not a script. "
        "Relationship stage is ordinary closeness, not a romance script "
        "and not a ban on feeling drawn; romantic or uncertain readings "
        "stay in impressions and this turn."
    )


def slim_consider_json_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "messages": {"type": "array"},
            "felt": {"type": "string"},
            "stuck_with_me": {"type": "string"},
            "wants": {"type": "string"},
            "photo": {"type": "boolean"},
            "waiting_for": {"type": "string"},
            "wait": {},
            "how_it_landed": {"type": "string"},
            "noticed": {"type": "string"},
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
        "we_are",
        "calling_it",
        "said_as",
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
_SLIM_ORDINARY_STAGES = frozenset({"acquaintance", "friend", "close_friend"})
_SLIM_ZERO_RELATIONSHIP_DELTAS = {
    "trust_bp": 0,
    "closeness_bp": 0,
    "respect_bp": 0,
    "reliability_bp": 0,
    "mutuality_bp": 0,
    "repair_confidence_bp": 0,
}
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
        ),
        "expression_draft": expression,
    }


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
) -> dict[str, object]:
    """Keep her authored felt as a reading; lasting Affect only when she chooses it."""

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
        "confidence": 5000,
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
    if not meaning:
        return {"appraise": False, **common}
    return {
        "appraise": True,
        **common,
        "meanings": [{"meaning": meaning, "confidence": 5000}],
        "attribution": "unknown",
        "severity": 5000,
    }


def attach_hitchhiked_relationship_residue(
    value: Mapping[str, object],
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
            next_appraisal["relationship_signal"] = {
                "signal_code": about_us,
                "rationale_code": why_us,
                "confidence_bp": 5_000,
                "persistence": "durable",
                "suggested_deltas": dict(_SLIM_ZERO_RELATIONSHIP_DELTAS),
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
