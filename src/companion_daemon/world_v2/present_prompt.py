"""Stable Present prompt pieces: identity prose and prefix-friendly ordering."""

from __future__ import annotations

from collections.abc import Mapping

from .companion_identity import CompanionIdentityFrame

PRESENT_RECENT_DIALOGUE_ITEM_LIMIT = 80
PRESENT_COMPANION_DIALOGUE_ITEM_LIMIT = 40
PRESENT_MEMORY_ITEM_LIMIT = 8
PRESENT_FACT_ITEM_LIMIT = 8
PRESENT_IMPRESSION_ITEM_LIMIT = 8
PRESENT_EXPERIENCE_ITEM_LIMIT = 8
PRESENT_CAPSULE_HARD_MAX_CHARACTERS = 100_000
PRESENT_DIALOGUE_SLICE_CHARACTERS = 32_000

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
    "recent_dialogue",
    "logical_time",
)


def combined_turn_system_lead(*, private_turn_state_required: bool) -> str:
    if private_turn_state_required:
        return (
            "Return either one JSON object with exactly two keys, appraisal_draft "
            "and expression_draft, or a recall choice with exactly the keys "
            "private_turn_state and recall_request in either serialization order "
            "when the occasion (last user object) says recall is available. "
            "If recall is unavailable, return only the two-draft envelope. "
            "A slim object with messages, felt, stuck_with_me, wants, and photo is also complete. "
        )
    return (
        "Return either one JSON object with exactly two keys, "
        "appraisal_draft and expression_draft, or the single recall_request "
        "object described below when the occasion says recall is available. "
        "If recall is unavailable, return only the two-draft envelope. "
        "A slim object with messages, felt, stuck_with_me, wants, and photo is also complete. "
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
    if frame.appearance:
        parts.append("外貌：" + frame.appearance.strip())
    if frame.background:
        parts.append("成长背景：" + frame.background.strip())
    if frame.daily_life:
        parts.append("日常：" + " ".join(item.strip() for item in frame.daily_life if item.strip()))
    if frame.speech_frame:
        parts.append("说话：" + frame.speech_frame.strip())
    if frame.first_message:
        parts.append("文风样例：" + frame.first_message.strip())
    return "\n".join(parts)


_SLIM_CONSIDER_KEYS = frozenset({"messages", "felt", "stuck_with_me", "wants", "photo"})
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


def compile_slim_consider_payload(
    value: Mapping[str, object],
) -> dict[str, object] | None:
    """Compile the five-field consider shape into dual drafts. Old drafts stay as-is."""

    if not is_slim_consider_payload(value):
        return None
    messages = _slim_messages(value.get("messages"))
    if messages is None:
        return None
    felt = (
        _clip_text(value.get("felt"), 240)
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
    timing = "now" if messages else "silent"
    expression: dict[str, object] = {
        "private_turn_state": {
            "contract": "private-turn-state.1",
            "inner_state_summary": stuck,
            "attended_source_refs": [],
        },
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
    return {
        "appraisal_draft": {
            "appraise": False,
            "affect": "no_change",
            "brief_rationale": felt,
            "behavior_tendency": label,
            "stance": label,
            "display_strategy": label,
            "confidence": 5000,
        },
        "expression_draft": expression,
    }


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
        if (
            expression.get("timing_choice") != "now"
            or len(beats) != 1
            or expression.get("media_request") != "none"
            or expression.get("media_source_refs")
        ):
            raise ValueError("reply_only slim payload exceeds text-only capability")
        beat = beats[0]
        head = {
            "type": "head",
            "private_turn_state": expression["private_turn_state"],
            "timing_choice": "now",
            "turn_posture": None,
            "cadence": expression.get("cadence", "conversational"),
            "beat": beat,
            "stance": expression["stance"],
            "brief_rationale": expression["brief_rationale"],
            "confidence": expression["confidence"],
            "response_expectation": None,
            "response_expectation_assessment": None,
            "world_claims": [],
            "media_request": "none",
            "media_source_refs": [],
        }
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
            "response_expectation": None,
            "response_expectation_assessment": None,
            "world_claims": [],
            "media_request": expression.get("media_request", "none"),
            "media_source_refs": list(expression.get("media_source_refs") or []),
        }
    return {
        "protocol": "character-interior-events.1",
        "appraisal_draft": compiled["appraisal_draft"],
        "events": [head, {"type": "end"}],
    }
