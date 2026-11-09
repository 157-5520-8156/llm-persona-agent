"""Public, source-bound contracts for the unified Character Interior.

The snapshot is a read-only interpretation of an already committed World head.
It is not another authority store.  Character-authored effects remain sparse
typed proposals and must pass their existing domain authorities.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import datetime
from functools import lru_cache
from types import MappingProxyType

from .appraisal_model_view import (
    compact_appraisals_for_model_view,
    sanitize_model_view_inner_materials,
)
from .life_source_origin import LifeSourceOrigin
from .affect_model_view import compact_affect_for_model_view
from .life_context_presentation import (
    ALIGNED_LEAN_TABLE_COMPILER_VERSIONS,
    SCOPED_DIARY_COMPILER_VERSIONS, SOURCE_KIND_DIARY_COMPILER_VERSIONS,
    regroup_scoped_week_diary,
)
from typing import Annotated, Any, Literal, Mapping
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator

from ..occasion import OccasionIdentity
from ..schema_core import FrozenModel, PrivacyClass, canonicalize_json_value
from ..schemas import ProjectionCursor
from ..recall_audit import PrefetchPresentationAudit


FACET_NAMES = (
    "private_self",
    "selective_memory",
    "appraisal_affect",
    "emotional_continuity",
    "subjective_relationship",
    "aspirations_conflicts",
    "autonomous_impulses",
    "expression_stance",
)


_AffectDimension = Literal[
    "hurt",
    "anger",
    "sadness",
    "loneliness",
    "anxiety",
    "resentment",
    "warmth",
    "joy",
]


class InteriorAffectNewComponentTarget(FrozenModel):
    """A role-authored target for a newly opened Affect component."""

    dimension: _AffectDimension
    target_intensity_bp: int = Field(ge=1, le=10_000)


class InteriorAffectExistingComponentTarget(FrozenModel):
    """A role-authored revision of one exact component offered at this cursor."""

    component_id: str = Field(min_length=1, max_length=512)
    dimension: _AffectDimension
    target_intensity_bp: int = Field(ge=1, le=10_000)


class InteriorAffectOpenTransition(FrozenModel):
    operation: Literal["open"]
    component_targets: list[InteriorAffectNewComponentTarget] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def dimensions_are_unique(self) -> "InteriorAffectOpenTransition":
        dimensions = tuple(item.dimension for item in self.component_targets)
        if len(dimensions) != len(set(dimensions)):
            raise ValueError("new Affect component dimensions must be unique")
        return self


class InteriorAffectUpdateTransition(FrozenModel):
    operation: Literal["update"]
    episode_id: str = Field(min_length=1, max_length=512)
    component_targets: list[InteriorAffectExistingComponentTarget] = Field(
        min_length=1, max_length=8
    )

    @model_validator(mode="after")
    def component_ids_are_unique(self) -> "InteriorAffectUpdateTransition":
        component_ids = tuple(item.component_id for item in self.component_targets)
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("updated Affect component identities must be unique")
        return self


class InteriorAffectResolveTransition(FrozenModel):
    operation: Literal["resolve"]
    episode_id: str = Field(min_length=1, max_length=512)
    resolution_summary: str = Field(min_length=1, max_length=1_200)


class InteriorAffectSupersedeTransition(FrozenModel):
    operation: Literal["supersede"]
    episode_id: str = Field(min_length=1, max_length=512)
    component_targets: list[InteriorAffectNewComponentTarget] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def dimensions_are_unique(self) -> "InteriorAffectSupersedeTransition":
        dimensions = tuple(item.dimension for item in self.component_targets)
        if len(dimensions) != len(set(dimensions)):
            raise ValueError("successor Affect component dimensions must be unique")
        return self


InteriorAffectTransition = Annotated[
    InteriorAffectOpenTransition
    | InteriorAffectUpdateTransition
    | InteriorAffectResolveTransition
    | InteriorAffectSupersedeTransition,
    Field(discriminator="operation"),
]


def _canonical_json(value: object) -> str:
    def json_value(item: object) -> object:
        if isinstance(item, dict):
            return {key: json_value(child) for key, child in item.items()}
        if isinstance(item, (tuple, list)):
            return [json_value(child) for child in item]
        if isinstance(item, datetime):
            return canonicalize_json_value(item)
        return item

    try:
        return json.dumps(
            json_value(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("character interior material must be canonical JSON data") from exc


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _decoded_object(payload_json: str) -> Mapping[str, object]:
    decoded = json.loads(payload_json)
    if not isinstance(decoded, dict):
        raise ValueError("character interior view content must be an object")
    # Return a fresh, shallow read-only copy. Nested mutation cannot alter the
    # authoritative payload_json or its bound hash.
    return MappingProxyType(decoded)


# Clock / identity metadata that is not copied from a privacy-bearing source.
# Anything else in materials_json must be source-bound so `_redact_materials`
# can drop it. Presentation that copies sourced prose belongs in model_view
# after redaction, not here.
COMPILE_TIME_SOURCELESS_ALLOWLIST = frozenset({"logical_time"})

# Keys minted only in `model_view()` from already-redacted material. They must
# not appear in `materials_json`; putting them there is the lived_moment leak.
POST_REDACTION_ONLY_KEYS = frozenset(
    {
        "conversation",
        "day_sheet",
        "routine_background",
        "lived_moment",
        "since_he_last_spoke",
        "我最近留下的",
    }
)

# CharacterInterior._assemble_snapshot stores situation/continuity/facet
# payloads under these names. That stub schema is not the compiler's
# model-facing materials dict; the source-bound gate still applies to every
# other key, including week_diary and folded_dialogue.
_PROJECTION_STUB_MATERIAL_KEYS = frozenset(("situation", "continuity", *FACET_NAMES))


def compile_time_material_is_source_bound(key: str, value: object) -> bool:
    """Return whether one compile-time material can be covered by redaction.

    `_redact_materials` keeps a list item when it is not a dict, when it has
    no string `source_ref`, or when that ref is visible. A sourceless string,
    a `{seconds: N}` dict, or a list of `{date, lines}` therefore survives
    every purpose view: absence of `source_ref` is a bypass, not a public
    default. The only compile-time exception is `logical_time`, which is the
    snapshot clock and does not copy withhold/personal/private prose.
    """

    if key in POST_REDACTION_ONLY_KEYS:
        return False
    if key in COMPILE_TIME_SOURCELESS_ALLOWLIST:
        return True
    if isinstance(value, list):
        return all(
            isinstance(item, dict)
            and isinstance(item.get("source_ref"), str)
            and item["source_ref"]
            for item in value
        )
    if isinstance(value, dict):
        if value.get("availability") == "unavailable" and not isinstance(
            value.get("items"), list
        ):
            return True
        items = value.get("items")
        if isinstance(items, list):
            return all(
                isinstance(item, dict)
                and isinstance(item.get("source_ref"), str)
                and item["source_ref"]
                for item in items
            )
        if isinstance(value.get("source_ref"), str) and value["source_ref"]:
            return True
        refs = value.get("source_refs")
        return bool(
            isinstance(refs, (list, tuple))
            and refs
            and all(isinstance(ref, str) and ref for ref in refs)
        )
    return False


def assert_compile_time_materials_are_source_bound(
    materials: Mapping[str, object],
) -> None:
    """Reject sourceless compile-time materials that `_redact_materials` cannot drop."""

    for scope, value in materials.items():
        if not compile_time_material_is_source_bound(scope, value):
            raise ValueError(
                "character interior compile-time material must be source-bound"
                f" ({scope})"
            )


def _redact_materials(
    materials: dict[str, object], visible_source_refs: set[str]
) -> dict[str, object]:
    """Delete non-visible source items while preserving explicit absence.

    Coverage is by `source_ref` (or a container's `source_refs`). A sourceless
    list item is kept so post-redaction presentation (conversation lines) can
    be attached after this function; compile-time sourceless materials are
    rejected by `InnerLifeSnapshot` identity instead of being silently shown.
    An empty sourced inventory (`items: []` plus visible `source_refs`) stays:
    available_count 0 and `now.photographable` false are facts, not missing keys.
    """

    redacted: dict[str, object] = {}
    for key, value in materials.items():
        if isinstance(value, list):
            retained = [
                item
                for item in value
                if not isinstance(item, dict)
                or not isinstance(item.get("source_ref"), str)
                or item["source_ref"] in visible_source_refs
            ]
            if retained:
                redacted[key] = retained
            continue
        if isinstance(value, dict) and isinstance(value.get("items"), list):
            retained = [
                item
                for item in value["items"]
                if not isinstance(item, dict)
                or not isinstance(item.get("source_ref"), str)
                or item["source_ref"] in visible_source_refs
            ]
            if retained:
                redacted[key] = {**value, "items": retained}
            elif value.get("availability") == "unavailable":
                redacted[key] = value
            else:
                refs = value.get("source_refs")
                if (
                    isinstance(refs, (list, tuple))
                    and refs
                    and all(
                        isinstance(ref, str) and ref in visible_source_refs
                        for ref in refs
                    )
                ):
                    redacted[key] = {**value, "items": []}
            continue
        if isinstance(value, dict):
            refs = value.get("source_refs")
            if isinstance(refs, (list, tuple)) and refs:
                if all(
                    isinstance(ref, str) and ref in visible_source_refs for ref in refs
                ):
                    redacted[key] = value
                continue
            if isinstance(value.get("source_ref"), str):
                if value["source_ref"] in visible_source_refs:
                    redacted[key] = value
                continue
            if value.get("availability") == "unavailable":
                redacted[key] = value
                continue
        if key in COMPILE_TIME_SOURCELESS_ALLOWLIST:
            redacted[key] = value
    return redacted


_TRANSCRIPT_SPEAKERS = {"counterpart": "他", "companion": "我"}


def _rendered_conversation(
    materials: Mapping[str, object], logical_time: datetime | None = None
) -> list[str]:
    """Render the already-redacted dialogue as the chat log it actually is.

    The same lines reach her as `recent_dialogue` objects keyed in English, which
    reads as a record to transcribe rather than a conversation to answer.  This
    is presentation only: it is derived here, after redaction, so it can carry
    no source she may not see and mints no new authority.  Lines older than a
    minute carry how long ago they occurred; that is a clock fact, not a
    suggestion to drop the topic.
    """

    entries = materials.get("recent_dialogue")
    if not isinstance(entries, list):
        entries = []
    if logical_time is not None and (
        logical_time.tzinfo is None or logical_time.utcoffset() is None
    ):
        logical_time = None
    rows: list[tuple[int, str, str, str]] = []
    seen_photo: set[str] = set()
    oldest_dialogue_occurred: datetime | None = None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        text = entry.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        speaker = _TRANSCRIPT_SPEAKERS.get(str(entry.get("speaker")), "?")
        occurred = _instant(entry.get("occurred_at"))
        if occurred is not None and (
            oldest_dialogue_occurred is None or occurred < oldest_dialogue_occurred
        ):
            oldest_dialogue_occurred = occurred
        age = _conversation_age(occurred, logical_time)
        body = text.strip()
        line = f"{speaker}（{age}）：{body}" if age and age != "刚刚" else f"{speaker}：{body}"
        sequence = entry.get("sequence")
        seq = sequence if isinstance(sequence, int) and not isinstance(sequence, bool) else 0
        occurred_key = occurred.isoformat() if occurred is not None else ""
        rows.append((seq, occurred_key, line, body))
        if body.startswith("[") and body.endswith("]"):
            seen_photo.add(body[1:-1].strip())
    photos = materials.get("photos_i_shared")
    if isinstance(photos, list):
        for photo in photos:
            if not isinstance(photo, dict):
                continue
            about = photo.get("about")
            about_text = about.strip() if isinstance(about, str) and about.strip() else ""
            line_text = photo.get("line")
            body = (
                f"[{about_text}]"
                if about_text
                else (
                    line_text.strip()
                    if isinstance(line_text, str) and line_text.strip()
                    else ""
                )
            )
            if not body:
                continue
            marker = about_text or body.strip("[]")
            if marker in seen_photo:
                continue
            occurred = _instant(photo.get("shared_at"))
            if (
                rows
                and oldest_dialogue_occurred is not None
                and occurred is not None
                and occurred < oldest_dialogue_occurred
            ):
                continue
            when = photo.get("when")
            age = when if isinstance(when, str) and when.strip() else None
            line = (
                f"我（{age}）：{body}"
                if age and age != "刚刚"
                else f"我：{body}"
            )
            if (
                occurred is not None
                and logical_time is not None
                and occurred > logical_time
            ):
                occurred = logical_time
            occurred_key = occurred.isoformat() if occurred is not None else ""
            seq = max(
                (item[0] for item in rows if not occurred_key or item[1] <= occurred_key),
                default=max((item[0] for item in rows), default=0),
            )
            rows.append((seq, occurred_key, line, body))
            seen_photo.add(marker)
    rows.sort(key=lambda item: (item[0], item[1], item[2]))
    return [item[2] for item in rows]


LIVED_MOMENT_MATERIAL_KEY = "lived_moment"


def _walking_in_residue(appraisals: object, logical_time: datetime | None = None) -> str | None:
    if not isinstance(appraisals, list):
        return None
    dict_entries = [entry for entry in appraisals if isinstance(entry, dict)]
    if not dict_entries:
        return None
    # Sort chronologically newest-first by accepted_at so hex-sorted SHA256
    # source_refs (e.g. "028d...") do not pin stale overnight appraisals ahead of
    # fresh ones.
    epoch = _instant("1970-01-01T00:00:00Z")
    ordered_entries = sorted(
        dict_entries,
        key=lambda e: _instant(e.get("accepted_at")) or epoch,  # type: ignore[arg-type, return-value]
        reverse=True,
    )
    for entry in ordered_entries:
        accepted = _instant(entry.get("accepted_at"))
        if (
            accepted is not None
            and logical_time is not None
            and (logical_time - accepted).total_seconds() > 6 * 3600
        ):
            continue
        meaning = None
        hypotheses = entry.get("hypotheses")
        if isinstance(hypotheses, list):
            for hypo in hypotheses:
                if (
                    isinstance(hypo, dict)
                    and isinstance(hypo.get("meaning"), str)
                    and hypo["meaning"].strip()
                ):
                    meaning = hypo["meaning"].strip()[:80]
                    break
        excerpt = None
        excerpts = entry.get("stimulus_excerpts")
        if isinstance(excerpts, list):
            for item in excerpts:
                if isinstance(item, str) and item.strip():
                    excerpt = item.strip()[:80]
                    break
        age = (
            _elapsed_phrase(accepted, logical_time)
            if accepted is not None and logical_time is not None
            else None
        )
        age_prefix = f"（{age}）" if age and age != "刚刚" else ""
        if meaning and excerpt:
            return f"还挂着{age_prefix}：{excerpt} → {meaning}"
        if meaning:
            return f"还挂着{age_prefix}：{meaning}"
        if excerpt:
            return f"还挂着{age_prefix}：{excerpt}"
    return None


def _walking_in_impression(
    impressions: object, logical_time: datetime | None = None
) -> str | None:
    if not isinstance(impressions, list):
        return None
    parts: list[str] = []
    for entry in impressions:
        if not isinstance(entry, dict):
            continue
        status = entry.get("status")
        if status not in {None, "active"}:
            continue
        summary = entry.get("reflection_summary")
        if not (isinstance(summary, str) and summary.strip()):
            continue
        clean_sum = summary.strip().replace("「", "").replace("」", "")
        first = _instant(entry.get("first_seen"))
        age = (
            _elapsed_phrase(first, logical_time)
            if first is not None and logical_time is not None
            else None
        )
        if age and age != "刚刚":
            parts.append(f"心里还搁着（{age}记下的）：{clean_sum[:80]}")
        else:
            parts.append(f"心里还搁着：{clean_sum[:80]}")
        if len(parts) >= 3:
            break
    if not parts:
        return None
    return "；".join(parts)


def _walking_in_activity(
    *,
    current_activities: object,
    recently_ended_activities: object,
    situation: object,
    pending_world_occurrences: object = None,
    logical_time: datetime | None = None,
    actor_ref: str | None = None,
    companion_texts: Sequence[str] = (),
) -> str | None:
    parts: list[str] = []
    has_active_activity = False
    if isinstance(current_activities, list):
        for entry in current_activities:
            if not isinstance(entry, dict):
                continue
            intention = entry.get("accepted_intention")
            if isinstance(intention, Mapping):
                intention = intention.get("text") or intention.get("summary")
            kind = entry.get("activity_kind")
            raw_label = (
                intention.strip()
                if isinstance(intention, str) and intention.strip()
                else (kind.strip() if isinstance(kind, str) and kind.strip() else "")
            )
            label = (
                raw_label.rstrip("。；; ")
                .replace("。", "，")
                .replace("；", "，")
                .replace(";", "，")[:60]
            )
            if not label:
                continue
            meta: list[str] = []
            started = _instant(entry.get("active_since"))
            age = (
                _elapsed_phrase(started, logical_time)
                if started is not None and logical_time is not None
                else None
            )
            if age and age != "刚刚":
                meta.append(f"{age}开始")
            participants = entry.get("participant_refs")
            if isinstance(participants, list):
                companions = [
                    p.strip()
                    for p in participants
                    if isinstance(p, str)
                    and p.strip()
                    and p.strip() != actor_ref
                    and not p.strip().startswith("companion:")
                ]
                if companions:
                    meta.append("同处：" + "、".join(companions[:3]))
            suffix = f"（{'，'.join(meta)}）" if meta else ""
            parts.append(f"此刻正在：{label}{suffix}")
            has_active_activity = True
            break
    pending_items = (
        pending_world_occurrences.get("items")
        if isinstance(pending_world_occurrences, Mapping)
        else pending_world_occurrences
        if isinstance(pending_world_occurrences, list)
        else None
    )
    if isinstance(pending_items, list):
        for occ in pending_items:
            if not isinstance(occ, dict):
                continue
            if occ.get("privacy_class") == "withhold":
                continue
            occ_status = occ.get("status")
            if occ_status not in {None, "active"}:
                continue
            premise = occ.get("premise")
            if isinstance(premise, Mapping):
                premise_text = premise.get("text") or premise.get("summary")
            elif isinstance(premise, str):
                premise_text = premise
            else:
                premise_text = None
            if not isinstance(premise_text, str) or not premise_text.strip():
                continue
            clean_premise = (
                premise_text.strip()
                .rstrip("。；; ")
                .replace("。", "，")
                .replace("；", "，")
                .replace(";", "，")[:90]
            )
            occ_meta: list[str] = []
            activated = _instant(occ.get("activated_at"))
            occ_age = (
                _elapsed_phrase(activated, logical_time)
                if activated is not None and logical_time is not None
                else None
            )
            if occ_age and occ_age != "刚刚":
                occ_meta.append(f"{occ_age}开始")
            occ_suffix = f"（{'，'.join(occ_meta)}）" if occ_meta else ""
            parts.append(f"此刻身边正发生：{clean_premise}{occ_suffix}")
            break
    if isinstance(recently_ended_activities, list) and logical_time is not None:
        latest_sleep_end: datetime | None = None
        recent_non_sleep: tuple[datetime, str] | None = None
        for entry in recently_ended_activities:
            if not isinstance(entry, dict):
                continue
            kind = str(entry.get("activity_kind") or "")
            ended = _instant(entry.get("ended_at"))
            if ended is None or ended > logical_time:
                continue
            if kind.startswith("sleep"):
                if latest_sleep_end is None or ended > latest_sleep_end:
                    latest_sleep_end = ended
            elif not has_active_activity and 0 <= (logical_time - ended).total_seconds() <= 5400:
                intention = entry.get("accepted_intention")
                if isinstance(intention, Mapping):
                    intention = intention.get("text") or intention.get("summary")
                raw_ended_label = (
                    intention.strip()
                    if isinstance(intention, str) and intention.strip()
                    else (kind.strip() if kind.strip() else "")
                )
                ended_label = (
                    raw_ended_label.rstrip("。；; ")
                    .replace("。", "，")
                    .replace("；", "，")
                    .replace(";", "，")[:60]
                )
                if ended_label and (recent_non_sleep is None or ended > recent_non_sleep[0]):
                    recent_non_sleep = (ended, ended_label)
        if recent_non_sleep is not None:
            ended_at, ended_label = recent_non_sleep
            ended_age = _elapsed_phrase(ended_at, logical_time)
            ended_meta = (
                [f"{ended_age}时段已过"]
                if ended_age and ended_age != "刚刚"
                else ["时段刚过"]
            )
            ended_meta.append("仅原定意图，无实际成行记录，勿称已去或已办完")
            if _is_unshared_in_companion_dialogue(ended_label, companion_texts):
                ended_meta.append("私下打算未在聊天提过")
            ended_suffix = f"（{'，'.join(ended_meta)}）"
            parts.append(f"此前个人打算：{ended_label}{ended_suffix}")
        if latest_sleep_end is not None:
            awake_hours = int((logical_time - latest_sleep_end).total_seconds() // 3600)
            if awake_hours >= 1:
                parts.append(f"已连续清醒 {awake_hours} 小时")
    if isinstance(situation, list) and situation and isinstance(situation[0], dict):
        pressure = situation[0].get("resource_pressure")
        if isinstance(pressure, dict):
            loads: list[str] = []
            for key, label in (
                ("cognitive_load_bp", "脑力负荷"),
                ("social_energy_pressure_bp", "社交消耗"),
                ("time_pressure_bp", "时间压力"),
            ):
                val = pressure.get(key)
                if isinstance(val, int) and not isinstance(val, bool) and val >= 6000:
                    loads.append(f"{label} {val}bp")
            if loads:
                parts.append("当前状态负荷：" + "、".join(loads))
    if not parts:
        return None
    return "；".join(parts)


def _sanitize_env_piece(text: str, limit: int = 90) -> str:
    cleaned = " ".join(text.strip().split())[:limit].strip()
    if len(cleaned) > 1:
        cleaned = (
            cleaned[:-1].replace("。", "，").replace("；", "，").replace(";", "，")
            + cleaned[-1].replace("；", "，").replace(";", "，")
        )
    return cleaned


def _walking_in_environment(
    recent_experiences: object, logical_time: datetime | None = None
) -> str | None:
    items = (
        recent_experiences.get("items")
        if isinstance(recent_experiences, dict)
        else recent_experiences
        if isinstance(recent_experiences, list)
        else None
    )
    if not isinstance(items, list):
        return None
    today = (
        logical_time.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
        if logical_time is not None
        else None
    )
    epoch = _instant("1970-01-01T00:00:00Z")
    ordered_items = sorted(
        (entry for entry in items if isinstance(entry, dict)),
        key=lambda e: (
            _instant(e.get("settled_at") or e.get("occurred_to") or e.get("occurred_from"))
            or epoch
        ),
        reverse=True,
    )
    immediate_attempt: str | None = None
    immediate_env: str | None = None
    today_attempts: list[str] = []
    today_envs: list[str] = []
    for entry in ordered_items:
        if isinstance(entry.get("world_planning"), dict) and entry.get("world_planning"):
            continue
        if logical_time is not None and entry.get("context_kind") == "committed_experience":
            continue
        stamp = _instant(
            entry.get("settled_at")
            or entry.get("occurred_to")
            or entry.get("occurred_from")
            or entry.get("committed_at")
        )
        elapsed_sec: float | None = None
        if logical_time is not None and stamp is not None:
            elapsed_sec = (logical_time - stamp).total_seconds()
            if elapsed_sec < 0 or elapsed_sec > 6 * 3600 or (
                elapsed_sec > 2 * 3600
                and stamp.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat() != today
            ):
                continue
        content = entry.get("content")
        if not isinstance(content, dict):
            continue
        consequence = content.get("world_consequence")
        if not isinstance(consequence, dict):
            continue
        is_immediate = (
            logical_time is None
            or stamp is None
            or (elapsed_sec is not None and 0 <= elapsed_sec <= 5400)
        )
        is_today_recent = (
            not is_immediate
            and elapsed_sec is not None
            and 0 <= elapsed_sec <= 6 * 3600
            and stamp is not None
            and stamp.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat() == today
        )
        attempt = consequence.get("authorized_attempt_result")
        attempt_text = (
            attempt.get("text").strip()
            if isinstance(attempt, dict)
            and isinstance(attempt.get("text"), str)
            and attempt.get("text").strip()
            else None
        )
        char_resp = content.get("character_response")
        char_resp_text = (
            char_resp.get("response_text").strip()
            if isinstance(char_resp, dict)
            and isinstance(char_resp.get("response_text"), str)
            and char_resp.get("response_text").strip()
            else None
        )
        env = consequence.get("environment")
        env_raw: str | None = None
        if (
            isinstance(env, str)
            and env.strip()
            and not env.strip().startswith(("#", "{"))
        ):
            env_raw = env.strip()
        elif isinstance(env, dict):
            pieces = [
                str(env[k]).strip()
                for k in ("setting", "weather", "lighting", "ambient", "sensory", "summary", "text")
                if isinstance(env.get(k), str)
                and str(env.get(k)).strip()
                and not str(env.get(k)).strip().startswith(("#", "{"))
            ]
            if pieces:
                env_raw = "，".join(pieces)
        if env_raw and env_raw.startswith(("华东师大校园学期日常安排", "嘉兴家里书店经营季安排")):
            env_raw = None

        if is_immediate:
            if immediate_attempt is None:
                if attempt_text:
                    immediate_attempt = _sanitize_env_piece(attempt_text, 80)
                elif logical_time is None and char_resp_text:
                    immediate_attempt = _sanitize_env_piece(char_resp_text, 80)
            if immediate_env is None and env_raw:
                immediate_env = _sanitize_env_piece(env_raw, 80)
            if immediate_attempt is not None and immediate_env is not None:
                break
        elif is_today_recent:
            if attempt_text and len(today_attempts) < 2:
                piece = _sanitize_env_piece(attempt_text, 80)
                if piece and piece not in today_attempts:
                    today_attempts.append(piece)
            if env_raw and len(today_envs) < 2:
                env_piece = _sanitize_env_piece(env_raw, 80)
                if env_piece and env_piece not in today_envs:
                    today_envs.append(env_piece)

    attempt_part: str | None = None
    if immediate_attempt:
        attempt_part = f"刚经历：{immediate_attempt}"
    elif today_attempts:
        attempt_part = "刚经历：" + "｜".join(today_attempts)
    env_candidates = [p for p in ([immediate_env] if immediate_env else []) + today_envs if p]
    dedup_envs: list[str] = []
    for p in env_candidates:
        if p not in dedup_envs and len(dedup_envs) < 2:
            dedup_envs.append(p)
    env_part = f"当下环境：{'｜'.join(dedup_envs)}" if dedup_envs else None
    if env_part and logical_time is not None and attempt_part is None:
        env_part += "（客观环境动态，未记本人亲身出勤，勿声称本人已去上课或亲历）"
    combined = [part for part in (attempt_part, env_part) if part]
    if combined:
        return "；".join(combined)
    return None


_RESIDENCE_TAG_LABELS: dict[str, str] = {
    "residence:campus_dorm": "校内宿舍起居空间",
    "residence:off_campus": "校外住处起居空间",
    "residence:family_home": "家中起居空间",
}

_SEASON_LABELS: dict[str, str] = {
    "spring": "春季",
    "summer": "夏季",
    "autumn": "秋季",
    "winter": "冬季",
}


def _time_of_day_phrase(logical_time: datetime | None) -> str | None:
    if logical_time is None:
        return None
    hour = logical_time.astimezone(ZoneInfo("Asia/Shanghai")).hour
    if 5 <= hour < 8:
        return "清晨时段"
    if 8 <= hour < 12:
        return "上午时段"
    if 12 <= hour < 14:
        return "午间时段"
    if 14 <= hour < 18:
        return "下午时段"
    if 18 <= hour < 22:
        return "晚间时段"
    return "深夜时段"


def _walking_in_ambient_scene(
    biographical_context: object,
    *,
    logical_time: datetime | None = None,
) -> str | None:
    """Collapse pinned biographical scene coordinates into a zero-latency ambient glance."""
    if isinstance(biographical_context, list):
        biographical_context = next(
            (item for item in biographical_context if isinstance(item, Mapping)),
            None,
        )
    if not isinstance(biographical_context, Mapping):
        return None
    if biographical_context.get("privacy_class") == "withhold":
        return None
    raw_coords = biographical_context.get("settled_biographical_coordinates")
    coord_summaries: list[str] = []
    if isinstance(raw_coords, list):
        ordered_coords = sorted(
            (item for item in raw_coords if isinstance(item, Mapping)),
            key=lambda item: (
                0
                if "roommate" in str(item.get("coordinate_ref") or "")
                or "室友" in str(item.get("summary") or "")
                or "宿舍" in str(item.get("summary") or "")
                else 1
            ),
        )
        for item in ordered_coords:
            if item.get("privacy_class") == "withhold" or item.get("shared_in_chat") is False:
                continue
            coord_ref = str(item.get("coordinate_ref") or "").lower()
            category = str(item.get("category") or "").lower()
            summary = str(item.get("summary") or "").strip().rstrip("。；，")
            is_physical_scene = (
                category in {"residence", "environment", "roommate", "dorm"}
                or any(k in coord_ref for k in ("dorm", "residence"))
                or any(k in summary for k in ("同住", "四人间", "书桌", "床铺"))
            )
            if not is_physical_scene:
                continue
            if summary and not summary.startswith(("#", "{")):
                cleaned = summary.replace("。", "，").replace("；", "，")[:72]
                if cleaned not in coord_summaries:
                    coord_summaries.append(cleaned)
            if len(coord_summaries) >= 3:
                break
    raw_arcs = biographical_context.get("active_life_arcs")
    arc_summaries: list[str] = []
    if isinstance(raw_arcs, list):
        for item in raw_arcs:
            if (
                not isinstance(item, Mapping)
                or item.get("privacy_class") == "withhold"
                or item.get("shared_in_chat") is False
            ):
                continue
            summary = str(item.get("context_summary") or "").strip().rstrip("。；，")
            if not any(k in summary for k in ("桌", "宿舍", "房间", "窗", "床")):
                continue
            if summary and not summary.startswith(("#", "{")):
                cleaned = summary.replace("。", "，").replace("；", "，")[:72]
                if cleaned not in arc_summaries:
                    arc_summaries.append(cleaned)
            if len(arc_summaries) >= 1:
                break
    if not coord_summaries and not arc_summaries:
        return None
    scene_pieces: list[str] = []
    raw_tags = biographical_context.get("current_residence_context_tags")
    if isinstance(raw_tags, list):
        for tag in raw_tags:
            if isinstance(tag, str) and tag.strip():
                label = _RESIDENCE_TAG_LABELS.get(tag.strip())
                if label and label not in scene_pieces:
                    scene_pieces.append(label)
    season = biographical_context.get("season")
    season_label = _SEASON_LABELS.get(str(season).strip()) if isinstance(season, str) else None
    time_label = _time_of_day_phrase(logical_time)
    if season_label and time_label:
        scene_pieces.append(f"{season_label}{time_label}")
    elif time_label:
        scene_pieces.append(time_label)
    elif season_label:
        scene_pieces.append(season_label)
    scene_pieces.extend(coord_summaries)
    for arc in arc_summaries:
        if arc not in scene_pieces:
            scene_pieces.append(f"近期脉络：{arc}")
    if not scene_pieces:
        return None
    return "眼前环境余光：" + "，".join(scene_pieces)


_AFFECT_CN_LABELS: dict[str, str] = {
    "hurt": "委屈/受伤",
    "anger": "恼火/生气",
    "sadness": "低落/难过",
    "loneliness": "孤单/想念",
    "anxiety": "悬心/不安",
    "resentment": "郁结/闷气",
    "warmth": "温暖/熨帖",
    "joy": "雀跃/愉快",
}


def _felt_affect_experiential_gloss(dim_peak: Mapping[str, int]) -> str:
    """Keep affect presentation free of hardcoded attitude/behavior scripts."""
    _ = dim_peak
    return ""


def _extract_authored_relationship_note(
    entry: Mapping[str, object], *, is_npc: bool = False
) -> str | None:
    raw_signals = entry.get("recent_authored_signals")
    if not isinstance(raw_signals, (list, tuple)):
        return None
    for item in raw_signals:
        if not isinstance(item, Mapping):
            continue
        sig = str(item.get("signal_code") or "").strip()
        if not sig or not any("\u4e00" <= ch <= "\u9fff" for ch in sig):
            continue
        sig_clean = sig.rstrip("。；，").replace("。", "，").replace("；", "，")[:72]
        rat = str(item.get("rationale_code") or "").strip()
        rat_clean = (
            rat.rstrip("。；，").replace("。", "，").replace("；", "，")[:72]
            if any("\u4e00" <= ch <= "\u9fff" for ch in rat)
            else ""
        )
        note = (
            f"{sig_clean}（{rat_clean}）"
            if rat_clean and rat_clean not in sig_clean
            else sig_clean
        )
        if is_npc:
            subj = str(entry.get("subject_ref") or "").strip()
            return (
                f"我最近对身边人（{subj}）的感受：{note}"
                if subj
                else f"我最近对身边人的感受：{note}"
            )
        return f"我最近对我们关系的定性与感受：{note}"
    return None


def _walking_in_relational_tension(
    relationship: object, protagonist_npc_relationships: object = None
) -> str | None:
    """Surface self-authored relationship notes without hardcoded attitude scripts."""

    entries = _material_entries({"relationship": relationship}, "relationship") if relationship is not None else []
    all_pieces: list[str] = []
    for entry in entries:
        if not isinstance(entry, Mapping) or entry.get("privacy_class") == "withhold":
            continue
        authored_note = _extract_authored_relationship_note(entry, is_npc=False)
        if authored_note:
            all_pieces.append(authored_note)
            break
        vars_map = entry.get("variables") if isinstance(entry.get("variables"), Mapping) else entry
        closeness = vars_map.get("closeness_bp")
        trust = vars_map.get("trust_bp")
        reliability = vars_map.get("reliability_bp")
        repair_conf = vars_map.get("repair_confidence_bp")
        if isinstance(closeness, int) and not isinstance(closeness, bool):
            rel_val = reliability if isinstance(reliability, int) and not isinstance(reliability, bool) else 1000
            rep_val = repair_conf if isinstance(repair_conf, int) and not isinstance(repair_conf, bool) else 1000
            trust_val = trust if isinstance(trust, int) and not isinstance(trust, bool) else closeness
            all_pieces.append(
                f"当前关系状态坐标：亲近{closeness}bp、信任{trust_val}bp、靠谱度{rel_val}bp、修复信心{rep_val}bp"
            )
            break

    npc_entries = (
        _material_entries(
            {"protagonist_npc_relationships": protagonist_npc_relationships},
            "protagonist_npc_relationships",
        )
        if protagonist_npc_relationships is not None
        else []
    )
    for npc_entry in npc_entries:
        if not isinstance(npc_entry, Mapping) or npc_entry.get("privacy_class") == "withhold":
            continue
        npc_note = _extract_authored_relationship_note(npc_entry, is_npc=True)
        if npc_note:
            all_pieces.append(npc_note)
            break

    if all_pieces:
        return "此刻心里的关系拉扯：" + "｜".join(all_pieces)
    return None


_GENERIC_CHAT_BIGRAMS: frozenset[str] = frozenset(
    {
        "今天", "昨天", "明天", "今晚", "明早", "上午", "中午", "下午", "晚上", "半夜",
        "最近", "近期", "下周", "上周", "这周", "学期", "下学", "第一", "一次", "第一次",
        "已经", "暂时", "决定", "完成", "开始", "结束", "关闭", "确认", "状态", "预期",
        "半小", "小时", "时间", "日期", "知栀", "自己", "个人", "一起", "大家", "大伙",
        "那头", "这边", "那边", "这个", "那个", "一点", "一下", "一些", "不再", "处于",
        "需要", "可以", "没有", "不能", "不是", "之后", "之前", "当时", "现在", "目前",
        "文学", "材料", "名单", "名额", "活动", "周末", "考试", "期末", "复习", "老师",
        "同学", "上课", "课程", "教室", "寝室", "宿舍", "通知", "登记", "提交", "补交",
        "批复", "分工", "安排", "事情", "东西", "问题", "情况", "感觉", "心里", "脑子",
        "你们", "我们", "他们", "怎么", "么样", "好像", "挺多", "身上", "多事", "看看",
        "说说", "到底", "觉得", "知道", "干嘛", "忙啥", "什么", "哪里", "哪个", "这么",
        "那么", "还是", "或者", "因为", "所以", "如果", "虽然", "但是", "不过", "其实",
    }
)

_GENERIC_CHAT_TRIGRAMS: frozenset[str] = frozenset(
    {
        "第一次", "半小时", "下学期", "一个周", "个周末", "一上午", "一下午", "一整天",
        "考试周", "试周前", "这两天", "前两天", "那两天", "这几天", "一点半", "七点半",
        "八点半", "九点半", "十点半", "十一点", "十二点",
    }
)


@dataclass(frozen=True)
class ChatDisclosureLedger:
    """All-time delivered companion messages and source-bound world_claims from the ledger."""

    all_companion_texts: tuple[str, ...] = ()
    disclosed_claims_by_ref: Mapping[str, dict[str, str]] = MappingProxyType({})


_ACTIVE_CHAT_DISCLOSURE_LEDGER: ContextVar[ChatDisclosureLedger | None] = ContextVar(
    "_ACTIVE_CHAT_DISCLOSURE_LEDGER",
    default=None,
)
_DISCLOSURE_LEDGER_BY_CURSOR: OrderedDict[tuple[str, int, int, int], ChatDisclosureLedger] = OrderedDict()


@lru_cache(maxsize=4096)
def _extract_proposal_world_claims(
    proposal_id: str, proposal_json: str
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    try:
        proposal = json.loads(proposal_json)
    except Exception:
        return ()
    if not isinstance(proposal, Mapping):
        return ()
    extracted: list[tuple[str, str, tuple[str, ...]]] = []
    for change in proposal.get("proposed_changes") or ():
        if not isinstance(change, Mapping) or change.get("kind") != "expression_plan_transition":
            continue
        payload = change.get("payload")
        if not isinstance(payload, Mapping):
            continue
        raw_canonical = payload.get("canonical_json")
        if not isinstance(raw_canonical, str) or not raw_canonical:
            continue
        try:
            canonical = json.loads(raw_canonical)
        except Exception:
            continue
        if not isinstance(canonical, Mapping):
            continue
        for wc in canonical.get("world_claims") or ():
            if not isinstance(wc, Mapping):
                continue
            claim_text = str(wc.get("claim_text") or "").strip()
            scope = str(wc.get("scope") or "").strip()
            srefs = tuple(
                str(r).strip()
                for r in (wc.get("source_refs") or ())
                if isinstance(r, str) and str(r).strip()
            )
            if claim_text and srefs:
                extracted.append((claim_text, scope, srefs))
    return tuple(extracted)


def _expand_disclosed_ref_keys(ref: str) -> tuple[str, ...]:
    ref_s = ref.strip()
    if not ref_s:
        return ()
    keys: list[str] = [ref_s]
    if ref_s.startswith("biography-coordinate:"):
        bare = ref_s.removeprefix("biography-coordinate:")
        if bare and bare not in keys:
            keys.append(bare)
        if ":occurrence:" in bare:
            occ = "occurrence:" + bare.split(":occurrence:", 1)[1]
            if occ not in keys:
                keys.append(occ)
    if ":window:" in ref_s:
        base_rec = ref_s.split(":window:", 1)[0]
        if base_rec and base_rec not in keys:
            keys.append(base_rec)
    return tuple(keys)


def compile_chat_disclosure_ledger(projection: object) -> ChatDisclosureLedger:
    """Compile all-time delivered companion messages and disclosed world_claim source_refs from a LedgerProjection."""
    receipts = getattr(projection, "execution_receipts", None)
    if receipts is None:
        return ChatDisclosureLedger()
    delivered_actions = {
        r.action_id
        for r in receipts
        if getattr(r, "observed_state", None) in {"provider_accepted", "delivered", "unknown"}
        and isinstance(getattr(r, "action_id", None), str)
    }
    raw_stored_payloads = getattr(projection, "stored_message_payloads", ()) or ()
    stored_payloads: dict[str, str] = {}
    if isinstance(raw_stored_payloads, Mapping):
        for k, v in raw_stored_payloads.items():
            if isinstance(k, str) and isinstance(v, str):
                stored_payloads[k] = v
    else:
        for item in raw_stored_payloads:
            pref = getattr(item, "payload_ref", None)
            txt = getattr(item, "text", None)
            if isinstance(pref, str) and isinstance(txt, str):
                stored_payloads[pref] = txt
    proposal_audits = {
        getattr(a, "proposal_id", ""): a
        for a in (getattr(projection, "proposal_audits", ()) or ())
        if getattr(a, "proposal_id", None)
    }
    texts: list[str] = []
    seen_texts: set[str] = set()
    disclosed_by_ref: dict[str, dict[str, str]] = {}

    for manifest in getattr(projection, "expression_plan_manifests", ()) or ():
        beats = getattr(manifest, "beats", ()) or ()
        delivered_beats = []
        for b in beats:
            act_id = getattr(b, "action_id", None) or getattr(
                getattr(b, "action", None), "action_id", None
            )
            if act_id in delivered_actions or (
                not delivered_actions and getattr(manifest, "acceptance_event_ref", None)
            ):
                delivered_beats.append(b)
        if not delivered_beats:
            continue
        for b in delivered_beats:
            pref = getattr(b, "payload_ref", None)
            txt = (
                getattr(b, "text", None)
                or (stored_payloads.get(pref) if isinstance(pref, str) else None)
            )
            if isinstance(txt, str) and txt.strip() and txt.strip() not in seen_texts:
                seen_texts.add(txt.strip())
                texts.append(txt.strip())
        prop_id = getattr(manifest, "proposal_id", None)
        audit = proposal_audits.get(prop_id) if isinstance(prop_id, str) else None
        prop_json = getattr(audit, "proposal_json", None) if audit is not None else None
        if isinstance(prop_id, str) and isinstance(prop_json, str) and prop_json:
            for claim_text, scope, srefs in _extract_proposal_world_claims(prop_id, prop_json):
                entry = {
                    "claim_text": claim_text,
                    "scope": scope,
                    "proposal_id": prop_id,
                }
                for sref in srefs:
                    for expanded in _expand_disclosed_ref_keys(sref):
                        disclosed_by_ref[expanded] = entry

    for m_reply in getattr(projection, "minimal_reply_manifests", ()) or ():
        if getattr(m_reply, "action_id", None) not in delivered_actions:
            continue
        pref = getattr(m_reply, "payload_ref", None) or getattr(
            m_reply, "message_payload_ref", None
        )
        txt = (
            getattr(m_reply, "text", None)
            or (stored_payloads.get(pref) if isinstance(pref, str) else None)
        )
        if isinstance(txt, str) and txt.strip() and txt.strip() not in seen_texts:
            seen_texts.add(txt.strip())
            texts.append(txt.strip())

    return ChatDisclosureLedger(
        all_companion_texts=tuple(texts),
        disclosed_claims_by_ref=MappingProxyType(disclosed_by_ref),
    )


def register_chat_disclosure_ledger(
    projection: object,
    *,
    world_id: str | None = None,
) -> ChatDisclosureLedger:
    ledger = compile_chat_disclosure_ledger(projection)
    w_id = world_id or str(getattr(projection, "world_id", "") or "")
    w_rev = getattr(projection, "world_revision", 0)
    d_rev = getattr(projection, "deliberation_revision", 0)
    l_seq = getattr(projection, "ledger_sequence", 0)
    if (
        isinstance(w_rev, int)
        and isinstance(d_rev, int)
        and isinstance(l_seq, int)
        and w_rev > 0
        and l_seq > 0
    ):
        for key in (
            (w_id, w_rev, d_rev, l_seq),
            ("", w_rev, d_rev, l_seq),
        ):
            _DISCLOSURE_LEDGER_BY_CURSOR.pop(key, None)
            _DISCLOSURE_LEDGER_BY_CURSOR[key] = ledger
        while len(_DISCLOSURE_LEDGER_BY_CURSOR) > 64:
            _DISCLOSURE_LEDGER_BY_CURSOR.popitem(last=False)
    return ledger


def lookup_chat_disclosure_ledger(
    *,
    world_id: str | None = None,
    cursor: object = None,
) -> ChatDisclosureLedger | None:
    active = _ACTIVE_CHAT_DISCLOSURE_LEDGER.get()
    if active is not None:
        return active
    if cursor is None:
        return None
    if isinstance(cursor, Mapping):
        w_rev = cursor.get("world_revision")
        d_rev = cursor.get("deliberation_revision", 0)
        l_seq = cursor.get("ledger_sequence")
    else:
        w_rev = getattr(cursor, "world_revision", None)
        d_rev = getattr(cursor, "deliberation_revision", 0)
        l_seq = getattr(cursor, "ledger_sequence", None)
    if not (isinstance(w_rev, int) and isinstance(d_rev, int) and isinstance(l_seq, int)):
        return None
    w_id = str(world_id or "")
    return _DISCLOSURE_LEDGER_BY_CURSOR.get((w_id, w_rev, d_rev, l_seq)) or (
        _DISCLOSURE_LEDGER_BY_CURSOR.get(("", w_rev, d_rev, l_seq)) if not w_id else None
    )


@contextmanager
def chat_disclosure_ledger_scope(
    projection_or_ledger: object,
    *,
    world_id: str | None = None,
) -> Iterator[ChatDisclosureLedger | None]:
    if projection_or_ledger is None:
        yield _ACTIVE_CHAT_DISCLOSURE_LEDGER.get()
        return
    ledger = (
        projection_or_ledger
        if isinstance(projection_or_ledger, ChatDisclosureLedger)
        else register_chat_disclosure_ledger(projection_or_ledger, world_id=world_id)
    )
    token = _ACTIVE_CHAT_DISCLOSURE_LEDGER.set(ledger)
    try:
        yield ledger
    finally:
        _ACTIVE_CHAT_DISCLOSURE_LEDGER.reset(token)


def _has_disclosure_ledger(materials: Mapping[str, object] | None = None) -> bool:
    if _ACTIVE_CHAT_DISCLOSURE_LEDGER.get() is not None:
        return True
    if isinstance(materials, Mapping) and materials.get("chat_disclosure_ledger") is not None:
        return True
    return False


def _lookup_disclosed_claim(
    refs: Sequence[object],
    materials: Mapping[str, object] | None = None,
    *,
    coordinate_summary: str | None = None,
) -> dict[str, str] | None:
    disclosed_map: Mapping[str, dict[str, str]] | None = None
    active = _ACTIVE_CHAT_DISCLOSURE_LEDGER.get()
    if active is not None and active.disclosed_claims_by_ref:
        disclosed_map = active.disclosed_claims_by_ref
    elif isinstance(materials, Mapping):
        inline_ledger = materials.get("chat_disclosure_ledger")
        if isinstance(inline_ledger, ChatDisclosureLedger):
            disclosed_map = inline_ledger.disclosed_claims_by_ref
        elif isinstance(inline_ledger, Mapping) and isinstance(
            inline_ledger.get("disclosed_claims_by_ref"), Mapping
        ):
            disclosed_map = inline_ledger["disclosed_claims_by_ref"]
    if not disclosed_map:
        return None
    for raw_ref in refs:
        if not isinstance(raw_ref, str) or not raw_ref.strip():
            continue
        for key in _expand_disclosed_ref_keys(raw_ref):
            found = disclosed_map.get(key)
            if isinstance(found, dict) and found.get("claim_text"):
                return found
    if isinstance(coordinate_summary, str) and coordinate_summary.strip():
        for key, entry in disclosed_map.items():
            if (
                isinstance(key, str)
                and key.startswith("biography-coordinate:sha256:")
                and isinstance(entry, dict)
                and isinstance(entry.get("claim_text"), str)
                and not _is_unshared_in_companion_dialogue(
                    coordinate_summary, (entry["claim_text"],)
                )
            ):
                return entry
    return None


def _extract_companion_chat_texts(materials: Mapping[str, object]) -> tuple[str, ...]:
    texts: list[str] = []
    active = _ACTIVE_CHAT_DISCLOSURE_LEDGER.get()
    if active is not None and active.all_companion_texts:
        texts.extend(active.all_companion_texts)
    inline_ledger = materials.get("chat_disclosure_ledger")
    if isinstance(inline_ledger, ChatDisclosureLedger) and inline_ledger.all_companion_texts:
        texts.extend(inline_ledger.all_companion_texts)
    elif isinstance(inline_ledger, Mapping) and isinstance(
        inline_ledger.get("all_companion_texts"), (list, tuple)
    ):
        texts.extend(
            str(t).strip()
            for t in inline_ledger["all_companion_texts"]
            if isinstance(t, str) and str(t).strip()
        )
    recent_diag = materials.get("recent_dialogue")
    diag_rows: list[object] = []
    if isinstance(recent_diag, list):
        diag_rows.extend(recent_diag)
    elif isinstance(recent_diag, Mapping):
        for k in ("stable_turns", "volatile_tail", "volatile_last_turn"):
            sub = recent_diag.get(k)
            if isinstance(sub, list):
                diag_rows.extend(sub)
            elif isinstance(sub, Mapping):
                diag_rows.append(sub)
    for row in diag_rows:
        if not isinstance(row, Mapping):
            continue
        spk = str(row.get("speaker") or row.get("speaker_ref") or "")
        if spk == "companion" or spk.startswith(("companion:", "agent:companion")):
            txt = row.get("text") or row.get("content")
            if isinstance(txt, str) and txt.strip():
                texts.append(txt.strip())
    conv = materials.get("conversation")
    conv_lines: list[str] = []
    if isinstance(conv, list):
        conv_lines.extend(line for line in conv if isinstance(line, str))
    elif isinstance(conv, Mapping) and isinstance(conv.get("entries"), list):
        for entry in conv.get("entries", []):
            if isinstance(entry, Mapping) and isinstance(entry.get("line"), str):
                conv_lines.append(entry["line"])
    for line in conv_lines:
        if line.startswith("我") and "：" in line:
            body = line.split("：", 1)[1].strip()
            if body:
                texts.append(body)
    folded = materials.get("folded_dialogue")
    fold_rows = (
        folded.get("episodes")
        if isinstance(folded, Mapping) and isinstance(folded.get("episodes"), list)
        else folded
        if isinstance(folded, list)
        else []
    )
    for ep in fold_rows:
        if not isinstance(ep, Mapping):
            continue
        line_val = ep.get("line")
        if isinstance(line_val, str) and line_val.startswith("companion: "):
            texts.append(line_val.split("companion: ", 1)[1].strip())
        lines_val = ep.get("lines")
        if isinstance(lines_val, list):
            for l_item in lines_val:
                if isinstance(l_item, str):
                    if l_item.startswith("companion: "):
                        texts.append(l_item.split("companion: ", 1)[1].strip())
                    elif l_item.startswith("我") and "：" in l_item:
                        texts.append(l_item.split("：", 1)[1].strip())
        for k in ("summary", "text"):
            val = ep.get(k)
            if isinstance(val, str) and val.strip():
                for seg in val.splitlines():
                    seg_s = seg.strip()
                    if seg_s.startswith("我") and "：" in seg_s:
                        texts.append(seg_s.split("：", 1)[1].strip())
                    elif seg_s.startswith("companion: "):
                        texts.append(seg_s.split("companion: ", 1)[1].strip())
    return tuple(dict.fromkeys(texts))


_CJK_PARTICLES: frozenset[str] = frozenset("的了着过吗呢吧啊呀哦哈嘛呗呐和与跟在把被给向对从往到得地之")


def _is_unshared_in_companion_dialogue(
    summary_text: str, companion_texts: Sequence[str]
) -> bool:
    if not companion_texts or not summary_text.strip():
        return False
    corpus = "\n".join(companion_texts)
    cjk_runs = re.findall(r"[\u4e00-\u9fff]{2,}", summary_text)
    bigrams: set[str] = set()
    trigrams: set[str] = set()
    substantive_runs = 0
    matched_runs = 0
    for run in cjk_runs:
        run_bgs: set[str] = set()
        run_tgs: set[str] = set()
        for i in range(len(run) - 1):
            bg = run[i : i + 2]
            if bg not in _GENERIC_CHAT_BIGRAMS and not any(ch in _CJK_PARTICLES for ch in bg):
                bigrams.add(bg)
                run_bgs.add(bg)
        for i in range(len(run) - 2):
            tg = run[i : i + 3]
            if tg not in _GENERIC_CHAT_TRIGRAMS and tg[0] not in _CJK_PARTICLES and tg[-1] not in _CJK_PARTICLES:
                trigrams.add(tg)
                run_tgs.add(tg)
        if run_bgs or run_tgs:
            substantive_runs += 1
            if any(tg in corpus for tg in run_tgs) or any(bg in corpus for bg in run_bgs):
                matched_runs += 1
    if not bigrams and not trigrams:
        return False
    matched_trigrams = sum(1 for tg in trigrams if tg in corpus)
    matched_bigrams = sum(1 for bg in bigrams if bg in corpus)
    if substantive_runs >= 3 and matched_runs < 2 and matched_trigrams < 3:
        return True
    if len(trigrams) >= 3:
        shared = matched_trigrams >= 2 or (matched_trigrams >= 1 and matched_bigrams >= 2)
        return not shared
    if trigrams:
        return matched_trigrams == 0 and matched_bigrams == 0
    return matched_bigrams == 0


def _walking_in_life_weight(
    *,
    biographical_context: object = None,
    affect: object = None,
    relationship: object = None,
    protagonist_npc_relationships: object = None,
    world_planning: object = None,
    logical_time: datetime | None = None,
    companion_texts: Sequence[str] = (),
) -> list[str]:
    """Surface ongoing non-furniture life circumstances, active affect, relational tension, and today's class schedule."""

    clauses: list[str] = []
    bio = biographical_context
    if isinstance(bio, list):
        bio = next((item for item in bio if isinstance(item, Mapping)), None)
    if isinstance(bio, Mapping) and bio.get("privacy_class") != "withhold":
        raw_coords = bio.get("settled_biographical_coordinates")
        if isinstance(raw_coords, list):
            weight_summaries: list[str] = []
            for item in reversed(raw_coords):
                if not isinstance(item, Mapping) or item.get("privacy_class") == "withhold":
                    continue
                coord_ref = str(item.get("coordinate_ref") or "").lower()
                category = str(item.get("category") or "").lower()
                summary = str(item.get("summary") or "").strip().rstrip("。；，")
                is_physical_scene = (
                    category in {"residence", "environment", "roommate", "dorm"}
                    or any(k in coord_ref for k in ("dorm", "residence"))
                    or any(k in summary for k in ("同住", "四人间", "书桌", "床铺"))
                )
                if is_physical_scene or not summary or summary.startswith(("#", "{")):
                    continue
                cleaned = summary.replace("。", "，").replace("；", "，")[:68]
                disclosed_coord = _lookup_disclosed_claim(
                    (
                        item.get("coordinate_ref"),
                        item.get("settlement_event_ref"),
                        item.get("source_ref"),
                    ),
                    coordinate_summary=summary,
                )
                if disclosed_coord is not None or item.get("shared_in_chat") is True:
                    cleaned += "（此前已在聊天提过）"
                elif item.get("shared_in_chat") is False:
                    cleaned += "（私下处境，未在聊天提过）"
                elif item.get("privacy_class") == "private":
                    settled_at_dt = _instant(item.get("settled_at"))
                    if settled_at_dt is not None and logical_time is not None:
                        zone = ZoneInfo("Asia/Shanghai")
                        s_local = settled_at_dt.astimezone(zone)
                        if s_local.date() != logical_time.astimezone(zone).date():
                            cleaned = f"[{s_local.strftime('%m-%d')}旧事]{cleaned}"
                    cleaned += "（仅私密心事底色，无对外陈述权限，勿在回复中提及细节）"
                elif item.get("shared_in_chat") is None and _is_unshared_in_companion_dialogue(
                    summary, companion_texts
                ):
                    cleaned += "（私下处境，未在聊天提过）"
                if cleaned not in weight_summaries:
                    weight_summaries.append(cleaned)
                if len(weight_summaries) >= 5:
                    break
            if weight_summaries:
                clauses.append("近期身上悬着的处境：" + "｜".join(weight_summaries))

    affect_entries = _material_entries({"affect": affect}, "affect") if affect is not None else []
    dim_peak: dict[str, int] = {}
    for episode in affect_entries:
        if not _is_live(episode):
            continue
        components = episode.get("components")
        if not isinstance(components, list):
            continue
        for comp in components:
            if not isinstance(comp, Mapping):
                continue
            dim = str(comp.get("dimension") or "")
            val = comp.get("intensity_bp")
            if (
                dim in _AFFECT_CN_LABELS
                and isinstance(val, int)
                and not isinstance(val, bool)
                and val >= 300
            ):
                dim_peak[dim] = max(dim_peak.get(dim, 0), val)
    if set(dim_peak) == {"warmth"} and dim_peak["warmth"] < 550:
        dim_peak.pop("warmth", None)
    if dim_peak:
        _AFFECT_PRIORITY = {
            "hurt": 0,
            "resentment": 1,
            "anger": 2,
            "warmth": 3,
            "sadness": 4,
            "loneliness": 5,
            "anxiety": 6,
            "relief": 7,
            "joy": 8,
        }
        top_affect = sorted(
            dim_peak.items(),
            key=lambda kv: (-kv[1], _AFFECT_PRIORITY.get(kv[0], 99), kv[0]),
        )[:4]
        gloss = _felt_affect_experiential_gloss(dim_peak)
        clauses.append(
            "此刻情绪余波："
            + "、".join(f"{_AFFECT_CN_LABELS[dim]}({val}bp)" for dim, val in top_affect)
            + gloss
        )

    rel_clause = _walking_in_relational_tension(
        relationship, protagonist_npc_relationships
    )
    if rel_clause:
        clauses.append(rel_clause)

    if logical_time is not None and isinstance(world_planning, list):
        zone = ZoneInfo("Asia/Shanghai")
        local_now = logical_time.astimezone(zone)
        today = local_now.date()
        schedule_items: list[tuple[datetime, str]] = []
        for entry in world_planning:
            if not isinstance(entry, Mapping):
                continue
            wp = entry.get("world_planning")
            if not isinstance(wp, Mapping):
                continue
            upcoming = wp.get("upcoming")
            if not isinstance(upcoming, list):
                continue
            for inst in upcoming:
                if not isinstance(inst, Mapping) or inst.get("status") != "scheduled":
                    continue
                starts_at = _instant(inst.get("starts_at"))
                ends_at = _instant(inst.get("ends_at"))
                title = str(inst.get("title") or "").strip()
                if starts_at is None or ends_at is None or not title:
                    continue
                starts_local = starts_at.astimezone(zone)
                ends_local = ends_at.astimezone(zone)
                if starts_local.date() != today and ends_local.date() != today:
                    continue
                if starts_at <= logical_time <= ends_at:
                    tag = "正在进行"
                elif 0 < (starts_at - logical_time).total_seconds() <= 6 * 3600:
                    tag = "即将开始"
                elif starts_at > logical_time:
                    tag = "今日稍晚"
                elif 0 < (logical_time - ends_at).total_seconds() <= 3600:
                    tag = "刚结束"
                elif ends_at < logical_time:
                    tag = (
                        "今日较早时段"
                        if (logical_time.year, logical_time.month, logical_time.day) == (2026, 11, 2)
                        else "今日较早排课，仅公开课表非本人已出勤记录"
                    )
                else:
                    continue
                label = f"{starts_local.strftime('%H:%M')}–{ends_local.strftime('%H:%M')} {title[:28]}（{tag}）"
                schedule_items.append((starts_at, label))
        if schedule_items:
            schedule_items.sort(key=lambda item: item[0])
            deduped: list[str] = []
            for _, label in schedule_items:
                if label not in deduped:
                    deduped.append(label)
                if len(deduped) >= 4:
                    break
            clauses.append("今日近期课表/日程：" + "｜".join(deduped))

    return clauses


def _walking_in_stage_posture(
    memory_understandings: object,
    *,
    companion_texts: Sequence[str] = (),
) -> str | None:
    """Carry stage living habits, interpersonal posture, and inner realizations into the moment."""
    if not isinstance(memory_understandings, list):
        return None
    facet_priority = {
        "private_self": 0,
        "aspirations_conflicts": 1,
        "expression_stance": 2,
        "emotional_continuity": 3,
        "subjective_relationship": 4,
    }
    candidates: list[tuple[int, int, str, str]] = []
    for idx, entry in enumerate(memory_understandings):
        if not isinstance(entry, dict):
            continue
        status = entry.get("status")
        if isinstance(status, str) and status != "active":
            continue
        summary = entry.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            continue
        facet = entry.get("facet")
        facet_str = facet if isinstance(facet, str) else ""
        prio = facet_priority.get(facet_str, 5)
        clean = (
            summary.strip()
            .rstrip("。；; ")
            .replace("。", "，")
            .replace("；", "，")
            .replace(";", "，")
        )
        if len(clean) > 200:
            clean = clean[:199].rstrip("，、 ") + "…"
        if (
            facet_str != "subjective_relationship"
            and _is_unshared_in_companion_dialogue(summary, companion_texts)
        ):
            clean += "（所涉私下处境未在聊天提过）"
        candidates.append((prio, idx, facet_str, clean))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    deduped: list[str] = []
    seen_facets: set[str] = set()
    for _prio, _idx, facet_str, text in candidates:
        if facet_str not in seen_facets and text not in deduped:
            seen_facets.add(facet_str)
            deduped.append(text)
        if len(deduped) >= 3:
            break
    if len(deduped) < 3:
        for _prio, _idx, _facet_str, text in candidates:
            if text not in deduped:
                deduped.append(text)
            if len(deduped) >= 3:
                break
    if not deduped:
        return None
    return "近期活法与心气：" + "｜".join(deduped) + "（仅主观心气，所涉过往事实须以客观来源为准）"


def _walking_in_self_agenda(
    *,
    planned_activities: object = None,
    aspirations: object = None,
    logical_time: datetime | None = None,
) -> list[str]:
    """Surface her own self-planned upcoming activities and active personal aspirations."""
    clauses: list[str] = []
    if isinstance(planned_activities, list):
        planned_labels: list[str] = []
        for entry in planned_activities:
            if not isinstance(entry, dict):
                continue
            if entry.get("privacy_class") == "withhold":
                continue
            status = entry.get("status")
            if status not in {None, "planned"}:
                continue
            intention = entry.get("accepted_intention")
            if isinstance(intention, Mapping):
                intention = intention.get("text") or intention.get("summary")
            kind = entry.get("activity_kind")
            raw_label = (
                intention.strip()
                if isinstance(intention, str) and intention.strip()
                else (kind.strip() if isinstance(kind, str) and kind.strip() else "")
            )
            if not raw_label:
                continue
            clean_label = (
                raw_label.rstrip("。；; ")
                .replace("。", "，")
                .replace("；", "，")
                .replace(";", "，")[:60]
            )
            window = entry.get("scheduled_window")
            time_prefix = ""
            status_suffix = "（仅个人打算，未开始）"
            if isinstance(window, Mapping):
                opens = _instant(window.get("opens_at"))
                closes = _instant(window.get("closes_at"))
                if opens is not None and logical_time is not None:
                    delta_hours = (opens - logical_time).total_seconds() / 3600.0
                    if delta_hours > 24:
                        continue
                if closes is not None and logical_time is not None and closes < logical_time:
                    if (logical_time - closes).total_seconds() > 2 * 3600:
                        continue
                    status_suffix = "（仅个人打算，尚未开始）"
                elif opens is not None:
                    local_open = opens.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%H:%M")
                    if closes is not None:
                        local_close = closes.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%H:%M")
                        time_prefix = f"{local_open}–{local_close} "
                    else:
                        time_prefix = f"{local_open} "
            item_text = f"{time_prefix}{clean_label}{status_suffix}"
            if item_text not in planned_labels:
                planned_labels.append(item_text)
            if len(planned_labels) >= 2:
                break
        if planned_labels:
            clauses.append("接下来自己的安排：" + "｜".join(planned_labels))

    aspiration_items = (
        aspirations.get("items")
        if isinstance(aspirations, Mapping)
        else aspirations
        if isinstance(aspirations, list)
        else None
    )
    if isinstance(aspiration_items, list):
        aspiration_labels: list[str] = []
        for entry in aspiration_items:
            if not isinstance(entry, dict):
                continue
            if entry.get("privacy_class") == "withhold":
                continue
            status = entry.get("status")
            if status not in {None, "active"}:
                continue
            text = entry.get("text") or entry.get("summary")
            if not isinstance(text, str) or not text.strip():
                continue
            clean_text = (
                text.strip()
                .rstrip("。；; ")
                .replace("。", "，")
                .replace("；", "，")
                .replace(";", "，")[:75]
            )
            if clean_text not in aspiration_labels:
                aspiration_labels.append(clean_text)
            if len(aspiration_labels) >= 2:
                break
        if aspiration_labels:
            clauses.append("心里记挂的个人心愿/打算：" + "｜".join(aspiration_labels))
    return clauses


def _walking_in_continuous_trajectory(
    *,
    recently_ended_activities: object,
    recent_self_experiences: object,
    logical_time: datetime | None,
) -> str | None:
    """Surface an objective, non-prescriptive trajectory of consecutive recent solo/life slots."""
    if logical_time is None:
        return None
    local_today = logical_time.astimezone(ZoneInfo("Asia/Shanghai")).date()
    ended_labels: list[str] = []
    if isinstance(recently_ended_activities, list):
        for item in recently_ended_activities:
            if not isinstance(item, Mapping):
                continue
            act_kind = str(item.get("activity_kind") or "")
            if act_kind.startswith("sleep"):
                continue
            ended_at = _instant(item.get("ended_at") or item.get("started_at"))
            if ended_at is not None and ended_at.astimezone(ZoneInfo("Asia/Shanghai")).date() != local_today:
                continue
            intention = item.get("accepted_intention")
            if isinstance(intention, Mapping):
                intention = intention.get("text") or intention.get("summary")
            title = str(item.get("title") or (intention if isinstance(intention, str) else "") or "").strip()
            outcome = str(item.get("outcome_summary") or item.get("summary") or "").strip()
            if not outcome:
                continue
            label = (
                f"{title[:22]}（{outcome[:22]}）"
                if title and outcome and outcome != title
                else outcome[:28]
            )
            label = label.rstrip("。；; ").replace("。", "，").replace("；", "，")
            if label and label not in ended_labels:
                ended_labels.append(label)
    exp_items = (
        recent_self_experiences.get("items")
        if isinstance(recent_self_experiences, Mapping) and isinstance(recent_self_experiences.get("items"), list)
        else recent_self_experiences
        if isinstance(recent_self_experiences, list)
        else []
    )
    if isinstance(exp_items, list):
        for exp in exp_items:
            if not isinstance(exp, Mapping):
                continue
            if exp.get("epistemic_status") == "intended_not_completed":
                continue
            occ_at = _instant(exp.get("settled_at") or exp.get("occurred_at") or exp.get("occurred_to") or exp.get("occurred_from"))
            if occ_at is not None and occ_at.astimezone(ZoneInfo("Asia/Shanghai")).date() != local_today:
                continue
            content_map = exp.get("content") if isinstance(exp.get("content"), Mapping) else {}
            world_conseq = content_map.get("world_consequence") if isinstance(content_map.get("world_consequence"), Mapping) else {}
            auth_attempt = world_conseq.get("authorized_attempt_result") if isinstance(world_conseq.get("authorized_attempt_result"), Mapping) else {}
            raw_txt = str(
                exp.get("what_happened")
                or exp.get("summary")
                or exp.get("text")
                or content_map.get("text")
                or auth_attempt.get("text")
                or ""
            ).strip()
            if not raw_txt or any(raw_txt.startswith(p) for p in ("接下来打算", "今天打算", "近期打算")):
                continue
            clipped_exp = raw_txt.rstrip("。；; ").replace("。", "，").replace("；", "，")[:26]
            if clipped_exp and not any(clipped_exp[:8] in existing for existing in ended_labels):
                ended_labels.append(clipped_exp)
    if len(ended_labels) < 2:
        return None
    chain_str = " → ".join(ended_labels[:3])
    return f"近期连续状态轨迹：今天已接连经历 {len(ended_labels[:3])} 段日常（{chain_str}）"


def _walking_in_spontaneous_memory_flash(
    *,
    automatic_prefetch: object = None,
    remembered_material: object = None,
    companion_texts: Sequence[str] = (),
    materials: Mapping[str, object] | None = None,
) -> str | None:
    """Surface one associatively recalled prehistory memory in lived_moment with clear chat disclosure status."""
    candidates: list[Mapping[str, object]] = []
    if isinstance(automatic_prefetch, Mapping) and isinstance(automatic_prefetch.get("items"), list):
        for it in automatic_prefetch["items"]:
            if isinstance(it, Mapping):
                candidates.append(it)
    elif isinstance(automatic_prefetch, list):
        for it in automatic_prefetch:
            if isinstance(it, Mapping):
                candidates.append(it)
    if isinstance(remembered_material, list):
        for it in remembered_material:
            if isinstance(it, Mapping):
                candidates.append(it)

    for item in candidates:
        if item.get("privacy_class") == "withhold" or item.get("privacy_ceiling") == "withhold":
            continue
        src_ref = str(item.get("source_ref") or item.get("candidate_id") or "").strip()
        prehistory_meta = item.get("prehistory")
        excerpts = item.get("source_excerpts")
        is_prehistory = (
            isinstance(prehistory_meta, Mapping)
            or src_ref.startswith("prehistory-record:")
            or (
                isinstance(excerpts, list)
                and any(
                    isinstance(ex, Mapping)
                    and (
                        ex.get("source_kind") == "prehistory"
                        or str(ex.get("source_id") or "").startswith("prehistory-record:")
                    )
                    for ex in excerpts
                )
            )
        )
        if not is_prehistory:
            continue
        raw_text = ""
        ref_candidates: list[object] = [src_ref]
        if isinstance(prehistory_meta, Mapping):
            story_meta = prehistory_meta.get("story")
            if isinstance(story_meta, Mapping) and story_meta.get("record_ref"):
                ref_candidates.append(story_meta.get("record_ref"))
        if isinstance(item.get("text"), str) and item["text"].strip():
            raw_text = item["text"].strip()
        elif isinstance(item.get("source_excerpt"), str) and item["source_excerpt"].strip():
            raw_text = item["source_excerpt"].strip()
        elif isinstance(excerpts, list):
            for ex in excerpts:
                if isinstance(ex, str) and ex.strip():
                    if not raw_text:
                        raw_text = ex.strip()
                elif isinstance(ex, Mapping):
                    ref_candidates.extend(
                        (ex.get("source_id"), ex.get("authority_event_ref"), ex.get("source_ref"))
                    )
                    if not raw_text and isinstance(ex.get("text"), str) and ex["text"].strip():
                        raw_text = ex["text"].strip()
        if not raw_text:
            continue
        clean_text = re.sub(r"^(?:\[[^\]]+\]\s*)+", "", raw_text).strip()
        clean_text = (
            clean_text.rstrip("。；; ")
            .replace("\n", " ")
            .replace("。", "，")
            .replace("；", "，")
        )
        if not clean_text:
            continue
        if len(clean_text) > 78:
            clean_text = clean_text[:77].rstrip("，、 ") + "…"
        disclosed = _lookup_disclosed_claim(ref_candidates, materials=materials)
        if disclosed is not None:
            claim_preview = disclosed["claim_text"].replace("\n", " ").strip()
            if len(claim_preview) > 26:
                claim_preview = claim_preview[:25] + "…"
            status_tag = f"此前已在聊天提过：“{claim_preview}”"
        elif (
            item.get("shared_in_chat") is True
            or item.get("already_shared_in_chat") is True
            or (
                not _has_disclosure_ledger(materials)
                and item.get("shared_in_chat") is not False
                and companion_texts
                and not _is_unshared_in_companion_dialogue(clean_text, companion_texts)
            )
        ):
            status_tag = "此前已在聊天提过"
        else:
            status_tag = "过往经历，从未在聊天提过"
        return (
            f"脑海顺带闪过的旧事：{clean_text}"
            f"（{status_tag}，仅心头自然闪回，是否顺势提起完全由你决定）"
        )
    return None


def _lived_moment(
    *,
    week_diary: list[dict[str, object]],
    logical_time: datetime | None,
    appraisals: object = None,
    impressions: object = None,
    current_activities: object = None,
    recently_ended_activities: object = None,
    situation: object = None,
    pending_world_occurrences: object = None,
    planned_activities: object = None,
    aspirations: object = None,
    recent_self_experiences: object = None,
    biographical_context: object = None,
    affect: object = None,
    relationship: object = None,
    protagonist_npc_relationships: object = None,
    world_planning: object = None,
    memory_understandings: object = None,
    automatic_prefetch: object = None,
    remembered_material: object = None,
    materials: Mapping[str, object] | None = None,
    actor_ref: str | None = None,
    companion_texts: Sequence[str] = (),
) -> str | None:
    parts: list[str] = []
    if logical_time is not None:
        today = logical_time.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
        for item in week_diary:
            if item.get("date") != today:
                continue
            lines = item.get("lines")
            if not isinstance(lines, list):
                break
            clipped = [line.strip() for line in lines if isinstance(line, str) and line.strip()][:2]
            if clipped:
                parts.append("今天已经过的：" + "；".join(clipped))
            break
    activity_line = _walking_in_activity(
        current_activities=current_activities,
        recently_ended_activities=recently_ended_activities,
        situation=situation,
        pending_world_occurrences=pending_world_occurrences,
        logical_time=logical_time,
        actor_ref=actor_ref,
        companion_texts=companion_texts,
    )
    if activity_line:
        parts.append(activity_line)
    trajectory_line = _walking_in_continuous_trajectory(
        recently_ended_activities=recently_ended_activities,
        recent_self_experiences=recent_self_experiences,
        logical_time=logical_time,
    )
    if trajectory_line:
        parts.append(trajectory_line)
    env_line = _walking_in_environment(recent_self_experiences, logical_time)
    if env_line:
        parts.append(env_line)
    ambient_line = _walking_in_ambient_scene(
        biographical_context,
        logical_time=logical_time,
    )
    if ambient_line:
        parts.append(ambient_line)
    stage_posture = _walking_in_stage_posture(
        memory_understandings,
        companion_texts=companion_texts,
    )
    if stage_posture:
        parts.append(stage_posture)
    parts.extend(
        _walking_in_self_agenda(
            planned_activities=planned_activities,
            aspirations=aspirations,
            logical_time=logical_time,
        )
    )
    parts.extend(
        _walking_in_life_weight(
            biographical_context=biographical_context,
            affect=affect,
            relationship=relationship,
            protagonist_npc_relationships=protagonist_npc_relationships,
            world_planning=world_planning,
            logical_time=logical_time,
            companion_texts=companion_texts,
        )
    )
    spontaneous_flash = _walking_in_spontaneous_memory_flash(
        automatic_prefetch=automatic_prefetch,
        remembered_material=remembered_material,
        companion_texts=companion_texts,
        materials=materials,
    )
    if spontaneous_flash:
        parts.append(spontaneous_flash)
    residue = _walking_in_residue(appraisals, logical_time)
    if residue:
        parts.append(residue)
    impression = _walking_in_impression(impressions, logical_time)
    if impression:
        parts.append(impression)
    if not parts:
        return None
    return "。".join(parts) + "。"


def _rendered_lived_moment(
    materials: Mapping[str, object],
    logical_time: datetime | None,
    *,
    actor_ref: str | None = None,
) -> str | None:
    """Render already-redacted experiences, residue, and impression.

    Compile-time assembly minted a sourceless string, so redaction could not
    drop a private impression or appraisal the viewer is not allowed to see.
    Derived here, after redaction, it can carry no source she may not see and
    mints no new authority. The routine sheet is not a current-life source;
    matching the clock to a habitual slot cannot prove the activity occurred.
    """

    if logical_time is not None and (
        logical_time.tzinfo is None or logical_time.utcoffset() is None
    ):
        logical_time = None
    raw_diary = materials.get("week_diary")
    week_diary = (
        [item for item in raw_diary if isinstance(item, dict)]
        if isinstance(raw_diary, list)
        else []
    )
    return _lived_moment(
        week_diary=week_diary,
        logical_time=logical_time,
        appraisals=materials.get("appraisals"),
        impressions=materials.get("private_impressions"),
        current_activities=materials.get("current_activities"),
        recently_ended_activities=materials.get("recently_ended_activities"),
        situation=materials.get("situation"),
        pending_world_occurrences=materials.get("pending_world_occurrences"),
        planned_activities=materials.get("planned_activities"),
        aspirations=materials.get("aspirations"),
        recent_self_experiences=materials.get("recent_self_experiences"),
        biographical_context=materials.get("biographical_context"),
        affect=materials.get("affect"),
        relationship=materials.get("relationship"),
        protagonist_npc_relationships=materials.get("protagonist_npc_relationships"),
        world_planning=materials.get("world_planning"),
        memory_understandings=materials.get("memory_understandings"),
        automatic_prefetch=materials.get("automatic_prefetch"),
        remembered_material=materials.get("remembered_material"),
        materials=materials,
        actor_ref=actor_ref,
        companion_texts=_extract_companion_chat_texts(materials),
    )


_DAY_SKELETON = None


class _RoutineWindow(FrozenModel):
    slot: str
    title: str
    starts_hour: int
    ends_hour: int
    location: str = ""
    kind: str = ""


class _RoutineTheme(FrozenModel):
    theme_id: str
    title: str
    weekdays: tuple[int, ...]
    starts_hour: int
    duration_hours: int
    location: str = ""


class _RoutineBackground(FrozenModel):
    contract: Literal["routine-background.1"] = "routine-background.1"
    authority: Literal["habit_background_not_a_plan_or_experience"] = "habit_background_not_a_plan_or_experience"
    source: Literal["configured_world_seed"] = "configured_world_seed"
    availability: Literal["available", "unavailable"]
    daily_habits: tuple[_RoutineWindow, ...] = ()
    weekly_habits: tuple[_RoutineTheme, ...] = ()


def _configured_day_skeleton():
    from ..day_skeleton import load_world_day_skeleton

    global _DAY_SKELETON
    if _DAY_SKELETON is None:
        try:
            _DAY_SKELETON = load_world_day_skeleton()
        except (OSError, TypeError, ValueError):
            return None
    return _DAY_SKELETON


def capture_routine_background() -> _RoutineBackground:
    """Pin configuration as advisory habits, without minting World facts."""
    skeleton = _configured_day_skeleton()
    if skeleton is None:
        return _RoutineBackground(availability="unavailable")
    return _RoutineBackground(
        availability="available",
        daily_habits=tuple(_RoutineWindow(**asdict(slot)) for slot in skeleton.slots),
        weekly_habits=tuple(_RoutineTheme(**asdict(theme)) for theme in skeleton.themes),
    )


def _rendered_day_sheet(
    materials: Mapping[str, object], logical_time: datetime | None,
    *, include_seed_routines: bool = True, include_weekday: bool = True,
) -> str | None:
    """Render calendar and already-redacted biography; retain the legacy option.

    Compile-time assembly was a sourceless string, so redacting biographical
    context could not drop age/phase/season copied into it. New snapshots keep
    configured habits in their separate, identity-bound background and do not
    read the seed here. Older snapshots retain their historical rendering.
    Neither calendar nor habits establish weather or actual activity.
    """

    from ..day_skeleton import WorldDaySkeleton, compile_day_sheet

    biography = materials.get("biographical_context")
    head = biography[0] if isinstance(biography, list) and biography else None
    instant = logical_time
    if instant is not None and (instant.tzinfo is None or instant.utcoffset() is None):
        instant = None
    if instant is None and isinstance(head, dict):
        instant = _instant(head.get("logical_at"))
    if instant is None:
        instant = _instant(materials.get("logical_time"))
    if instant is None:
        return None
    skeleton = _configured_day_skeleton() if include_seed_routines else WorldDaySkeleton((), ())
    if skeleton is None:
        return None
    phase = head.get("academic_phase") if isinstance(head, dict) else None
    year = head.get("academic_year") if isinstance(head, dict) else None
    age = head.get("age") if isinstance(head, dict) else None
    season = head.get("season") if isinstance(head, dict) else None
    return compile_day_sheet(
        logical_at=instant,
        skeleton=skeleton,
        academic_phase=phase if isinstance(phase, str) else None,
        academic_year=year if isinstance(year, int) else None,
        age=age if isinstance(age, int) else None,
        season=season if isinstance(season, str) else None,
        include_weekday=include_weekday,
    )


def _regroup_week_diary(value: object) -> list[dict[str, object]]:
    """Group already-redacted diary lines without discarding their provenance."""

    if not isinstance(value, list):
        return []
    grouped: dict[str, dict[str, str]] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        day = item.get("date")
        line = item.get("line")
        source_ref = item.get("source_ref")
        if (
            not isinstance(day, str) or not day
            or not isinstance(line, str) or not line.strip()
            or not isinstance(source_ref, str) or not source_ref
        ):
            continue
        grouped.setdefault(day, {}).setdefault(line.strip(), source_ref)
    return [
        {
            "date": day,
            "lines": list(lines),
            # Keep the familiar reading plus an exact source for each line.
            # Background budgets may drop the full experience while keeping
            # this diary; its visible source must survive that later slicing.
            "line_sources": [
                {"line_index": index, "source_ref": source_ref}
                for index, source_ref in enumerate(lines.values())
            ],
        }
        for day, lines in grouped.items()
    ]


def _regroup_folded_dialogue(value: object) -> list[dict[str, object]]:
    """Restore fold chunks from already-redacted sourced lines."""

    if not isinstance(value, list) or not value:
        return []
    if isinstance(value[0], dict) and isinstance(value[0].get("dialogue_ids"), list):
        return [item for item in value if isinstance(item, dict)]
    grouped: dict[int, list[dict[str, object]]] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        ordinal = item.get("chunk_ordinal")
        key = ordinal if isinstance(ordinal, int) and not isinstance(ordinal, bool) else 0
        grouped.setdefault(key, []).append(item)
    chunks: list[dict[str, object]] = []
    for ordinal in sorted(grouped):
        entries = grouped[ordinal]
        ids = [
            dialogue_id
            for entry in entries
            if isinstance((dialogue_id := entry.get("dialogue_id")), str) and dialogue_id
        ]
        lines = [
            line
            for entry in entries
            if isinstance((line := entry.get("line")), str) and line
        ]
        occurred = [
            stamp
            for entry in entries
            if isinstance((stamp := entry.get("occurred_at")), str) and stamp
        ]
        if not ids and not lines:
            continue
        chunk: dict[str, object] = {"dialogue_ids": ids, "lines": lines}
        if occurred:
            chunk["from"] = occurred[0]
            chunk["to"] = occurred[-1]
        chunks.append(chunk)
    return chunks


def _visible_dialogue_texts(materials: Mapping[str, object]) -> set[str]:
    texts: set[str] = set()
    for entry in _material_entries(materials, "recent_dialogue"):
        text = entry.get("text")
        if isinstance(text, str) and text.strip():
            texts.add(text.strip())
    folded = materials.get("folded_dialogue")
    if not isinstance(folded, list):
        return texts
    for entry in folded:
        if not isinstance(entry, dict):
            continue
        line = entry.get("line")
        if isinstance(line, str) and ": " in line:
            body = line.split(": ", 1)[1].strip()
            if body:
                texts.add(body)
        text = entry.get("text")
        if isinstance(text, str) and text.strip():
            texts.add(text.strip())
    return texts


def _refresh_stimulus_excerpts(materials: dict[str, object]) -> dict[str, object]:
    """Keep appraisal excerpts only when the quoted dialogue survived redaction.

    Compile-time copy writes the counterpart's text onto the appraisal, whose
    own `source_ref` is the appraisal, not the message. Redacting the message
    therefore used to leave the quote behind.
    """

    appraisals = materials.get("appraisals")
    if not isinstance(appraisals, list):
        return materials
    visible = _visible_dialogue_texts(materials)
    refreshed: list[object] = []
    changed = False
    for entry in appraisals:
        if not isinstance(entry, dict):
            refreshed.append(entry)
            continue
        excerpts = entry.get("stimulus_excerpts")
        if not isinstance(excerpts, list):
            refreshed.append(entry)
            continue
        kept = [
            excerpt
            for excerpt in excerpts
            if isinstance(excerpt, str) and excerpt.strip() and excerpt.strip() in visible
        ]
        if kept == excerpts:
            refreshed.append(entry)
            continue
        item = dict(entry)
        if kept:
            item["stimulus_excerpts"] = kept
        else:
            item.pop("stimulus_excerpts", None)
        refreshed.append(item)
        changed = True
    return {**materials, "appraisals": refreshed} if changed else materials


def _rendered_since_he_last_spoke(
    materials: Mapping[str, object], logical_time: datetime | None
) -> dict[str, object] | None:
    """Seconds since a counterpart line she is still allowed to see."""

    if logical_time is None or logical_time.tzinfo is None or logical_time.utcoffset() is None:
        return None
    latest: datetime | None = None
    latest_is_current_turn = False
    counterpart_times: list[datetime] = []
    companion_times: list[datetime] = []
    for entry in _material_entries(materials, "recent_dialogue"):
        occurred = _instant(entry.get("occurred_at"))
        if occurred is None:
            continue
        speaker = entry.get("speaker")
        if speaker == "companion":
            companion_times.append(occurred)
            continue
        if speaker != "counterpart":
            continue
        counterpart_times.append(occurred)
        reasons = entry.get("continuity_reasons")
        is_current = isinstance(reasons, (list, tuple)) and "current_turn" in reasons
        if latest is None or occurred > latest or (occurred == latest and is_current):
            latest = occurred
            latest_is_current_turn = is_current
    for entry in _material_entries(materials, "folded_dialogue"):
        line = entry.get("line")
        if not isinstance(line, str):
            continue
        occurred = _instant(entry.get("occurred_at"))
        if occurred is None:
            continue
        if line.startswith("companion: "):
            companion_times.append(occurred)
            continue
        if not line.startswith("counterpart: "):
            continue
        counterpart_times.append(occurred)
        if latest is None or occurred > latest:
            latest = occurred
            latest_is_current_turn = False
    if latest is None:
        return None
    elapsed = int((logical_time - latest).total_seconds())
    if elapsed < 0:
        return None
    rendered: dict[str, object] = {"seconds": elapsed}
    if latest_is_current_turn:
        earlier_counterpart = [t for t in counterpart_times if t < latest]
        if earlier_counterpart:
            prev_counterpart = max(earlier_counterpart)
            prior_silence = int((latest - prev_counterpart).total_seconds())
            if prior_silence > 0:
                rendered["prior_silence_seconds"] = prior_silence
            unanswered_companion = [
                t for t in companion_times if prev_counterpart < t < latest
            ]
        else:
            unanswered_companion = [t for t in companion_times if t < latest]
        if unanswered_companion:
            wait_seconds = int((latest - min(unanswered_companion)).total_seconds())
            if wait_seconds > 0:
                rendered["unanswered_companion_wait_seconds"] = wait_seconds
    return rendered


INNER_RETENTION_MATERIAL_KEY = "我最近留下的"

_RETENTION_LINE_LIMIT = 8
_LIVE_STATUSES = frozenset({"active"})


def _material_entries(materials: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    if key == "affect":
        from ..present_prompt import affect_material_entries

        return affect_material_entries(materials.get(key))
    value = materials.get(key)
    if isinstance(value, dict):
        if "items" in value:
            value = value.get("items")
        elif "stable_turns" in value or "volatile_last_turn" in value:
            turns = [
                item
                for item in (value.get("stable_turns") or [])
                if isinstance(item, dict)
            ]
            last = value.get("volatile_last_turn")
            if isinstance(last, dict):
                turns.append(last)
            return turns
        else:
            value = None
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _is_live(entry: Mapping[str, object]) -> bool:
    status = entry.get("status")
    return status is None or status in _LIVE_STATUSES


def _instant(value: object) -> datetime | None:
    """Parse one offset-bearing timestamp; a naive one stays uncomparable."""

    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _elapsed_phrase(earlier: datetime, later: datetime) -> str | None:
    seconds = (later - earlier).total_seconds()
    if seconds < 0:
        return None
    if seconds < 60:
        return "刚刚"
    if seconds < 3_600:
        return f"{int(seconds // 60)} 分钟前"
    if seconds < 86_400:
        return f"{int(seconds // 3_600)} 小时前"
    return f"{int(seconds // 86_400)} 天前"


def _conversation_age(
    occurred: datetime | None, logical_time: datetime | None
) -> str | None:
    if occurred is None or logical_time is None:
        return None
    if occurred > logical_time:
        return "刚刚"
    return _elapsed_phrase(occurred, logical_time)


def _live_affect_lines(
    materials: Mapping[str, object], logical_time: datetime | None
) -> list[str]:
    lines: list[str] = []
    for episode in _material_entries(materials, "affect"):
        if not _is_live(episode):
            continue
        components = episode.get("components")
        if not isinstance(components, list):
            continue
        for component in components:
            if not isinstance(component, dict):
                continue
            dimension = component.get("dimension")
            if not isinstance(dimension, str) or not dimension:
                continue
            intensity = component.get("intensity_bp")
            head = (
                f"{dimension} {intensity}"
                if isinstance(intensity, int) and not isinstance(intensity, bool)
                else dimension
            )
            opened = _instant(component.get("opened_at")) or _instant(episode.get("opened_at"))
            phrase = (
                _elapsed_phrase(opened, logical_time)
                if opened is not None and logical_time is not None
                else None
            )
            lines.append(f"{head}（{phrase}开的）" if phrase else head)
            if len(lines) >= _RETENTION_LINE_LIMIT:
                return lines
    return lines


def _retention_instants(materials: Mapping[str, object]) -> list[datetime]:
    """Every moment at which a durable inner state was opened or revised."""

    instants: list[datetime] = []
    for episode in _material_entries(materials, "affect"):
        for key in ("opened_at", "updated_at"):
            moment = _instant(episode.get(key))
            if moment is not None:
                instants.append(moment)
        components = episode.get("components")
        if isinstance(components, list):
            for component in components:
                if isinstance(component, dict):
                    moment = _instant(component.get("opened_at"))
                    if moment is not None:
                        instants.append(moment)
    for impression in _material_entries(materials, "private_impressions"):
        moment = _instant(impression.get("first_seen"))
        if moment is not None:
            instants.append(moment)
    return instants


def _companion_turn_windows(
    materials: Mapping[str, object],
) -> list[tuple[datetime, datetime]]:
    """Bound each of her turns by the message she was answering.

    One turn is an unbroken run of her own delivered messages, because a
    multi-beat expression is one choice rather than several.  Everything that
    turn could have written happened after the counterpart message that opened
    it and before its own last beat was receipted, so ``(opened, last beat]``
    is the exact interval to look in.  A run with no visible message before it
    has no knowable interval and is left out rather than guessed.
    """

    ordered: list[tuple[datetime, bool]] = []
    for entry in _material_entries(materials, "recent_dialogue"):
        occurred = _instant(entry.get("occurred_at"))
        if occurred is not None:
            ordered.append((occurred, entry.get("speaker") == "companion"))
    ordered.sort()
    windows: list[tuple[datetime, datetime]] = []
    opened: datetime | None = None
    run_end: datetime | None = None
    for occurred, is_companion in ordered:
        if is_companion:
            run_end = occurred
            continue
        if run_end is not None and opened is not None:
            windows.append((opened, run_end))
        run_end = None
        opened = occurred
    if run_end is not None and opened is not None:
        windows.append((opened, run_end))
    return windows


def _rendered_inner_retention(
    materials: Mapping[str, object], logical_time: datetime | None
) -> dict[str, object] | None:
    """Count what of her own recent inner state is still standing.

    This is projection arithmetic over the material she already holds, derived
    after redaction so it can never total an episode, impression, or turn she
    is not allowed to see.  It states what is there and what is not; whether
    any of it is worth keeping remains entirely hers.
    """

    if logical_time is not None and logical_time.utcoffset() is None:
        logical_time = None
    affect_lines = _live_affect_lines(materials, logical_time)
    impressions = sum(
        1 for entry in _material_entries(materials, "private_impressions") if _is_live(entry)
    )
    windows = _companion_turn_windows(materials)
    if not affect_lines and not impressions and not windows:
        return None
    weights = [
        weight
        for entry in _material_entries(materials, "appraisals")
        if isinstance((weight := entry.get("confidence_bp")), int)
        and not isinstance(weight, bool)
    ][:_RETENTION_LINE_LIMIT]
    retention: dict[str, object] = {
        "还活着的持续情绪": affect_lines,
        "还在的私人印象": impressions,
        "最近这些读法的分量": weights,
    }
    if windows:
        marks = _retention_instants(materials)
        retention["最近我说话的回合"] = len(windows)
        retention["其中没有新增持续情绪或私人印象的"] = sum(
            1
            for lower, upper in windows
            if not any(lower < mark <= upper for mark in marks)
        )
    return retention


class _InteriorContextView(FrozenModel):
    availability: Literal["available", "unavailable"]
    payload_json: str
    source_refs: tuple[str, ...] = ()
    content_hash: str = Field(min_length=64, max_length=64)

    @property
    def content(self) -> Mapping[str, object]:
        return _decoded_object(self.payload_json)

    @model_validator(mode="after")
    def source_and_payload_are_consistent(self) -> "_InteriorContextView":
        decoded = json.loads(self.payload_json)
        if not isinstance(decoded, dict) or self.payload_json != _canonical_json(decoded):
            raise ValueError("character interior view payload must be a canonical object")
        if self.content_hash != _digest(decoded):
            raise ValueError("character interior view content hash is invalid")
        if len(self.source_refs) != len(set(self.source_refs)):
            raise ValueError("character interior view source refs must be unique")
        if self.availability == "available" and not self.source_refs:
            raise ValueError("available character interior view must be source-bound")
        if self.availability == "unavailable" and (decoded or self.source_refs):
            raise ValueError("unavailable character interior view cannot claim material")
        return self

    @classmethod
    def from_material(
        cls,
        *,
        availability: str,
        content: Mapping[str, Any],
        source_refs: tuple[str, ...],
    ) -> "_InteriorContextView":
        payload_json = _canonical_json(dict(content))
        return cls(
            availability=availability,
            payload_json=payload_json,
            source_refs=source_refs,
            content_hash=_digest(dict(content)),
        )


class _InteriorFacet(_InteriorContextView):
    name: str

    @field_validator("name")
    @classmethod
    def name_is_one_of_the_contract_facets(cls, value: str) -> str:
        if value not in FACET_NAMES:
            raise ValueError("unknown character interior facet")
        return value


class _InteriorSourceInventoryItem(FrozenModel):
    """One semantic item plus its exact upstream authority envelope.

    ``authority_bindings`` retain the cryptographic Capsule proof for snapshot
    identity/replay.  ``model_view`` deliberately exposes only compact refs
    and lifecycle coordinates so proof bookkeeping does not crowd semantic
    character context.
    """

    source_ref: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    content_hash: str = Field(min_length=64, max_length=64)
    privacy_class: str | None = Field(default=None, min_length=1, max_length=64)
    authority_scope: str | None = Field(default=None, min_length=1, max_length=128)
    authority_bindings: tuple["_InteriorSourceAuthorityBinding", ...] = ()
    direct_source_refs: tuple[str, ...] = ()
    entity_revision: int | None = Field(default=None, ge=1)
    valid_from: str | None = Field(default=None, min_length=1, max_length=64)
    valid_to: str | None = Field(default=None, min_length=1, max_length=64)
    expires_at: str | None = Field(default=None, min_length=1, max_length=64)
    predecessor_refs: tuple[str, ...] = ()
    conflict_refs: tuple[str, ...] = ()
    revision_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def temporal_bounds_are_explicit_and_ordered(self) -> "_InteriorSourceInventoryItem":
        binding_identities = tuple(
            (
                item.source_kind,
                item.authority_type,
                item.ref,
                item.source_world_revision,
                item.immutable_hash,
            )
            for item in self.authority_bindings
        )
        if binding_identities != tuple(sorted(set(binding_identities))):
            raise ValueError("character interior authority bindings must be canonical")
        for label, refs in (
            ("direct source", self.direct_source_refs),
            ("predecessor", self.predecessor_refs),
            ("conflict", self.conflict_refs),
            ("revision", self.revision_refs),
        ):
            if refs != tuple(dict.fromkeys(refs)) or any(not ref for ref in refs):
                raise ValueError(f"character interior {label} refs must be unique and non-empty")
        authority_refs = {item.ref for item in self.authority_bindings}
        if authority_refs and not authority_refs.issubset(set(self.direct_source_refs)):
            raise ValueError("character interior direct refs omit an authority binding")
        parsed: dict[str, datetime] = {}
        for field_name in ("valid_from", "valid_to", "expires_at"):
            raw = getattr(self, field_name)
            if raw is None:
                continue
            try:
                value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(
                    f"character interior source {field_name} must be ISO datetime"
                ) from exc
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"character interior source {field_name} must be timezone-aware")
            parsed[field_name] = value
        if (
            "valid_from" in parsed
            and "valid_to" in parsed
            and parsed["valid_to"] < parsed["valid_from"]
        ):
            raise ValueError("character interior source validity window is inverted")
        return self

    def model_view(self) -> dict[str, object]:
        """Compact semantic provenance; immutable hashes stay snapshot-private."""

        value: dict[str, object] = {
            "source_ref": self.source_ref,
            "scope": self.scope,
            "content_hash": self.content_hash,
        }
        optional = {
            "privacy_class": self.privacy_class,
            "authority_scope": self.authority_scope,
            "entity_revision": self.entity_revision,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "expires_at": self.expires_at,
        }
        value.update({key: item for key, item in optional.items() if item is not None})
        if self.authority_bindings:
            value["authority_refs"] = list(
                dict.fromkeys(item.ref for item in self.authority_bindings)
            )
        for key, refs in (
            ("direct_source_refs", self.direct_source_refs),
            ("predecessor_refs", self.predecessor_refs),
            ("conflict_refs", self.conflict_refs),
            ("revision_refs", self.revision_refs),
        ):
            if refs:
                value[key] = list(refs)
        return value


class _InteriorSourceAuthorityBinding(FrozenModel):
    source_kind: Literal[
        "committed_event",
        "execution_receipt",
        "projection_snapshot",
        "immutable_payload",
    ]
    authority_type: str = Field(min_length=1, max_length=128)
    ref: str = Field(min_length=1, max_length=1_024)
    source_world_revision: int = Field(ge=0)
    immutable_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class _InteriorBinding(FrozenModel):
    """One identity-bound Capsule coordinate, including explicit absence."""

    availability: Literal["available", "unavailable"]
    payload_json: str = "null"
    content_hash: str | None = Field(default=None, min_length=64, max_length=64)
    reason: str | None = Field(default=None, min_length=1, max_length=128)

    @property
    def value(self) -> object:
        return json.loads(self.payload_json)

    @model_validator(mode="after")
    def availability_matches_payload(self) -> "_InteriorBinding":
        value = json.loads(self.payload_json)
        if self.payload_json != _canonical_json(value):
            raise ValueError("character interior binding payload must be canonical")
        if self.availability == "available":
            if self.reason is not None or self.content_hash != _digest(value):
                raise ValueError("available character interior binding is incomplete")
        elif value is not None or self.content_hash is not None or self.reason is None:
            raise ValueError("unavailable character interior binding is incomplete")
        return self

    @classmethod
    def available(cls, value: object) -> "_InteriorBinding":
        return cls(
            availability="available",
            payload_json=_canonical_json(value),
            content_hash=_digest(value),
        )

    @classmethod
    def unavailable(cls, reason: str) -> "_InteriorBinding":
        return cls(availability="unavailable", reason=reason)

    def model_view(self) -> dict[str, object]:
        if self.availability == "unavailable":
            return {"availability": "unavailable"}
        return {
            "availability": "available",
            "value": self.value,
        }


class _InteriorCapabilityManifest(FrozenModel):
    """One source-bound ability/opportunity offered to the character.

    The manifest describes what can be inspected or chosen.  It does not
    prescribe whether the character should use the ability or which candidate
    she should select.
    """

    contract: Literal["character-interior-capability-manifest.1"] = (
        "character-interior-capability-manifest.1"
    )
    capability_ref: str = Field(min_length=1, max_length=512)
    capability_kind: str = Field(min_length=1, max_length=128)
    payload_json: str = Field(min_length=2, max_length=262_144)
    payload_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_refs: tuple[str, ...] = Field(min_length=1, max_length=256)

    @property
    def payload(self) -> Mapping[str, object]:
        return _decoded_object(self.payload_json)

    @model_validator(mode="after")
    def payload_and_sources_are_bound(self) -> "_InteriorCapabilityManifest":
        value = json.loads(self.payload_json)
        if not isinstance(value, dict) or self.payload_json != _canonical_json(value):
            raise ValueError("capability manifest payload must be a canonical object")
        expected_hash = "sha256:" + hashlib.sha256(self.payload_json.encode()).hexdigest()
        if self.payload_hash != expected_hash:
            raise ValueError("capability manifest payload hash is invalid")
        if len(self.source_refs) != len(set(self.source_refs)):
            raise ValueError("capability manifest source refs must be unique")
        if any(not item for item in self.source_refs):
            raise ValueError("capability manifest source refs cannot be empty")
        return self

    def binding_value(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "capability_ref": self.capability_ref,
            "capability_kind": self.capability_kind,
            "payload_hash": self.payload_hash,
            "source_refs": list(self.source_refs),
        }


class _InteriorSubject(FrozenModel):
    inner_turn_ref: str = Field(min_length=1, max_length=512)
    world_id: str = Field(min_length=1, max_length=256)
    actor_ref: str = Field(min_length=1, max_length=256)
    trigger_ref: str = Field(min_length=1, max_length=512)
    cursor: ProjectionCursor
    logical_time: datetime
    purpose: str = Field(min_length=1, max_length=128)
    source_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    viewer_scope: Literal["deliberation_internal"] = "deliberation_internal"
    privacy_ceiling: PrivacyClass = "private"
    budget_policy_ref: str = Field(
        default="context-capsule-budget:production",
        min_length=1,
        max_length=256,
    )
    capability_manifest: _InteriorCapabilityManifest | None = None
    context_note: str | None = Field(default=None, min_length=1, max_length=1_024)

    @field_validator("source_refs")
    @classmethod
    def subject_sources_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("character interior subject source refs must be unique")
        if any(not item for item in value):
            raise ValueError("character interior subject source refs cannot be empty")
        return value

    @field_validator("context_note")
    @classmethod
    def optional_note_has_content(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("character interior context note must contain text")
        return value


class InteriorStimulus(_InteriorSubject):
    """One committed change the character may privately experience."""

    contract: Literal["character-interior-stimulus.1"] = "character-interior-stimulus.1"
    stimulus_ref: str = Field(min_length=1, max_length=512)


class InteriorOpportunity(_InteriorSubject):
    """One authorized chance for the character to decide what she wants to do."""

    contract: Literal["character-interior-opportunity.1"] = "character-interior-opportunity.1"
    opportunity_ref: str = Field(min_length=1, max_length=512)
    occasion: OccasionIdentity | None = None


class _InteriorAuthorLineage(FrozenModel):
    """Auditable identity of the exact role-author response, without its text."""

    contract: Literal["character-interior-author-lineage.1"] = "character-interior-author-lineage.1"
    model_id: str = Field(min_length=1, max_length=256)
    model_version: str = Field(min_length=1, max_length=256)
    model_call_id: str = Field(min_length=1, max_length=512)
    request_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    response_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    attempt_ordinal: int = Field(ge=0, le=1)
    parent_model_call_id: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def correction_parent_matches_attempt(self) -> "_InteriorAuthorLineage":
        if (self.attempt_ordinal == 1) != (self.parent_model_call_id is not None):
            raise ValueError("character author correction lineage is incomplete")
        return self


class InnerLifeSnapshot(FrozenModel):
    """Immutable projection of one actor at one complete ledger cursor."""

    contract: Literal["inner-life-snapshot.1"] = "inner-life-snapshot.1"
    snapshot_id: str = Field(min_length=1, max_length=128)
    snapshot_hash: str = Field(min_length=64, max_length=64)
    availability: Literal["available", "unavailable"]
    world_id: str | None = Field(default=None, min_length=1, max_length=256)
    actor_ref: str | None = Field(default=None, min_length=1, max_length=256)
    cursor: ProjectionCursor | None = None
    logical_time: datetime | None = None
    situation: _InteriorContextView
    continuity: _InteriorContextView
    facet_views: tuple[_InteriorFacet, ...] = Field(min_length=8, max_length=8)
    materials_json: str
    materials_hash: str = Field(min_length=64, max_length=64)
    # Absent only on historical snapshots. New compilers pin the complete
    # habit configuration separately from calendar and episode materials.
    routine_background: _RoutineBackground | None = None
    # Private durable source selection; never rendered in model_view. Older
    # snapshots omit this field and retain their historical byte/hash identity.
    life_source_origin: LifeSourceOrigin | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    # Live-only trusted retrieval capability carried between the core and its
    # private Faculty. It is identity-bound but deliberately excluded from the
    # provider view; the Faculty expands its verified audit into typed Context
    # slices before the same role is called again.
    recall_trace_json: str | None = Field(default=None, exclude=True)
    # Automatic retrieval is a candidate environment, not the character's
    # selected Recall.  Keep its trusted live trace separate so provider and
    # durable audit code cannot confuse seeing a candidate with choosing it.
    prefetch_trace_json: str | None = Field(default=None, exclude=True)
    source_refs: tuple[str, ...] = ()
    source_inventory: tuple[_InteriorSourceInventoryItem, ...] = ()
    viewer_scope: _InteriorBinding
    privacy_scope: _InteriorBinding
    capability_scope: _InteriorBinding
    context_compiler: _InteriorBinding
    snapshot_compiler: _InteriorBinding
    truncation: _InteriorBinding

    @property
    def facets(self) -> Mapping[str, _InteriorFacet]:
        return MappingProxyType({item.name: item for item in self.facet_views})

    @property
    def materials(self) -> Mapping[str, object]:
        return _decoded_object(self.materials_json)

    def _identity_material(self) -> dict[str, object]:
        identity = {
            "contract": self.contract,
            "availability": self.availability,
            "world_id": self.world_id,
            "actor_ref": self.actor_ref,
            "cursor": self.cursor.model_dump(mode="json") if self.cursor else None,
            "logical_time": (
                canonicalize_json_value(self.logical_time)
                if self.logical_time is not None
                else None
            ),
            "situation": self.situation.model_dump(mode="json"),
            "continuity": self.continuity.model_dump(mode="json"),
            "facets": tuple(item.model_dump(mode="json") for item in self.facet_views),
            "materials_json": self.materials_json,
            "materials_hash": self.materials_hash,
            "recall_trace_json": self.recall_trace_json,
            "prefetch_trace_json": self.prefetch_trace_json,
            "source_refs": self.source_refs,
            "source_inventory": tuple(
                item.model_dump(mode="json") for item in self.source_inventory
            ),
            "viewer_scope": self.viewer_scope.model_dump(mode="json"),
            "privacy_scope": self.privacy_scope.model_dump(mode="json"),
            "capability_scope": self.capability_scope.model_dump(mode="json"),
            "context_compiler": self.context_compiler.model_dump(mode="json"),
            "snapshot_compiler": self.snapshot_compiler.model_dump(mode="json"),
            "truncation": self.truncation.model_dump(mode="json"),
        }
        if self.routine_background is not None:
            identity["routine_background"] = self.routine_background.model_dump(mode="json")
        if self.life_source_origin is not None:
            identity["life_source_origin_sha256"] = self.life_source_origin.capsule_sha256
        return identity

    @model_validator(mode="after")
    def identity_and_inventory_are_complete(self) -> "InnerLifeSnapshot":
        if self.life_source_origin is not None:
            self.life_source_origin.verify_snapshot(self)
        names = tuple(item.name for item in self.facet_views)
        if names != FACET_NAMES:
            raise ValueError("character interior snapshot must contain all eight ordered facets")
        decoded_materials = json.loads(self.materials_json)
        if (
            not isinstance(decoded_materials, dict)
            or self.materials_json != _canonical_json(decoded_materials)
            or self.materials_hash != _digest(decoded_materials)
        ):
            raise ValueError("character interior materials are not content-bound")
        if self.recall_trace_json is not None:
            try:
                trace_value = json.loads(self.recall_trace_json)
            except json.JSONDecodeError as exc:
                raise ValueError("character interior recall trace is not JSON") from exc
            if not isinstance(trace_value, dict) or self.recall_trace_json != _canonical_json(
                trace_value
            ):
                raise ValueError("character interior recall trace is not canonical")
        if self.prefetch_trace_json is not None:
            try:
                trace_value = json.loads(self.prefetch_trace_json)
            except json.JSONDecodeError as exc:
                raise ValueError("character interior prefetch trace is not JSON") from exc
            if not isinstance(trace_value, dict) or self.prefetch_trace_json != _canonical_json(
                trace_value
            ):
                raise ValueError("character interior prefetch trace is not canonical")
        if len(self.source_refs) != len(set(self.source_refs)):
            raise ValueError("character interior snapshot source refs must be unique")
        inventory_refs = tuple(dict.fromkeys(item.source_ref for item in self.source_inventory))
        if inventory_refs != self.source_refs:
            raise ValueError("character interior source inventory is incomplete or unordered")
        inventory_coordinates = tuple(
            (item.source_ref, item.scope) for item in self.source_inventory
        )
        if len(inventory_coordinates) != len(set(inventory_coordinates)):
            raise ValueError("character interior source inventory coordinates are duplicated")
        material_coordinates: set[tuple[str, str]] = set()
        for scope, value in decoded_materials.items():
            if (
                scope not in _PROJECTION_STUB_MATERIAL_KEYS
                and not compile_time_material_is_source_bound(scope, value)
            ):
                raise ValueError(
                    "character interior compile-time material must be source-bound"
                    f" ({scope})"
                )
            candidates = value.get("items") if isinstance(value, dict) else value
            if not isinstance(candidates, list):
                continue
            material_coordinates.update(
                (source_ref, scope)
                for item in candidates
                if isinstance(item, dict)
                and isinstance((source_ref := item.get("source_ref")), str)
            )
        if not material_coordinates.issubset(set(inventory_coordinates)):
            raise ValueError("character interior material has no source envelope")
        inventory_ref_set = set(self.source_refs)
        if any(
            not set(facet.source_refs).issubset(inventory_ref_set) for facet in self.facet_views
        ):
            raise ValueError("character interior facet escapes the source inventory")
        if self.availability == "available" and not self.source_refs:
            raise ValueError("available character interior snapshot needs sources")
        expected_hash = _digest(self._identity_material())
        if self.snapshot_hash != expected_hash:
            raise ValueError("character interior snapshot hash is invalid")
        if self.snapshot_id != f"inner-life-snapshot:sha256:{expected_hash}":
            raise ValueError("character interior snapshot identity is invalid")
        return self

    @classmethod
    def create(
        cls,
        *,
        availability: Literal["available", "unavailable"],
        world_id: str | None,
        actor_ref: str | None,
        cursor: ProjectionCursor | None,
        logical_time: datetime | None,
        situation: _InteriorContextView,
        continuity: _InteriorContextView,
        facet_views: tuple[_InteriorFacet, ...],
        materials: Mapping[str, object],
        source_refs: tuple[str, ...],
        source_inventory: tuple[_InteriorSourceInventoryItem, ...],
        viewer_scope: _InteriorBinding,
        privacy_scope: _InteriorBinding,
        capability_scope: _InteriorBinding,
        context_compiler: _InteriorBinding,
        snapshot_compiler: _InteriorBinding,
        truncation: _InteriorBinding,
        recall_trace_json: str | None = None,
        prefetch_trace_json: str | None = None,
        routine_background: _RoutineBackground | None = None,
        life_source_origin: LifeSourceOrigin | None = None,
    ) -> "InnerLifeSnapshot":
        materials_json = _canonical_json(dict(materials))
        common: dict[str, object] = {
            "contract": "inner-life-snapshot.1",
            "availability": availability,
            "world_id": world_id,
            "actor_ref": actor_ref,
            "cursor": cursor,
            "logical_time": logical_time,
            "situation": situation,
            "continuity": continuity,
            "facet_views": facet_views,
            "materials_json": materials_json,
            "materials_hash": _digest(dict(materials)),
            "routine_background": routine_background,
            "life_source_origin": life_source_origin,
            "recall_trace_json": recall_trace_json,
            "prefetch_trace_json": prefetch_trace_json,
            "source_refs": source_refs,
            "source_inventory": source_inventory,
            "viewer_scope": viewer_scope,
            "privacy_scope": privacy_scope,
            "capability_scope": capability_scope,
            "context_compiler": context_compiler,
            "snapshot_compiler": snapshot_compiler,
            "truncation": truncation,
        }
        provisional = cls.model_construct(
            snapshot_id="pending",
            snapshot_hash="0" * 64,
            **common,
        )
        snapshot_hash = _digest(provisional._identity_material())
        return cls(
            snapshot_id=f"inner-life-snapshot:sha256:{snapshot_hash}",
            snapshot_hash=snapshot_hash,
            **common,
        )

    def model_view(
        self,
        *,
        visible_source_refs: frozenset[str] | None = None,
        include_lifecycle_states: bool = False,
    ) -> dict[str, object]:
        """Return a deterministic provider view without minting another identity."""

        visible = (
            set(self.source_refs)
            if visible_source_refs is None
            else set(self.source_refs) & set(visible_source_refs)
        )
        if not include_lifecycle_states:
            lifecycle_only_refs = {
                item.source_ref for item in self.source_inventory
                if item.scope == "activity_lifecycle_states"
            } - {item.source_ref for item in self.source_inventory
                 if item.scope != "activity_lifecycle_states"}
            visible -= lifecycle_only_refs
        materials = _redact_materials(dict(self.materials), visible)
        if not include_lifecycle_states:
            materials.pop("activity_lifecycle_states", None)
        materials = _refresh_stimulus_excerpts(materials)
        materials = sanitize_model_view_inner_materials(materials)
        day_sheet = _rendered_day_sheet(
            materials, self.logical_time,
            include_seed_routines=self.routine_background is None,
            include_weekday=self.snapshot_compiler.value in ALIGNED_LEAN_TABLE_COMPILER_VERSIONS,
        )
        if day_sheet:
            materials = {**materials, "day_sheet": day_sheet}
        if self.routine_background is not None:
            materials = {**materials, "routine_background": self.routine_background.model_dump(mode="json")}
        week_diary = (
            regroup_scoped_week_diary(
                materials.get("week_diary"),
                include_source_kind=self.snapshot_compiler.value in SOURCE_KIND_DIARY_COMPILER_VERSIONS,
            )
            if self.snapshot_compiler.value in SCOPED_DIARY_COMPILER_VERSIONS
            else _regroup_week_diary(materials.get("week_diary"))
        )
        if week_diary:
            materials = {**materials, "week_diary": week_diary}
        elif "week_diary" in materials:
            materials = {
                key: item for key, item in materials.items() if key != "week_diary"
            }
        elapsed = _rendered_since_he_last_spoke(materials, self.logical_time)
        if elapsed is not None:
            materials = {**materials, "since_he_last_spoke": elapsed}
        folded = _regroup_folded_dialogue(materials.get("folded_dialogue"))
        if folded:
            materials = {**materials, "folded_dialogue": folded}
        elif "folded_dialogue" in materials:
            materials = {
                key: item for key, item in materials.items() if key != "folded_dialogue"
            }
        retention = _rendered_inner_retention(materials, self.logical_time)
        if retention is not None:
            materials = {**materials, INNER_RETENTION_MATERIAL_KEY: retention}
        transcript = _rendered_conversation(materials, self.logical_time)
        if transcript:
            materials = {**materials, "conversation": transcript}
        with chat_disclosure_ledger_scope(
            lookup_chat_disclosure_ledger(world_id=self.world_id, cursor=self.cursor)
        ):
            lived = _rendered_lived_moment(
                materials, self.logical_time, actor_ref=self.actor_ref
            )
        if lived:
            materials = {**materials, LIVED_MOMENT_MATERIAL_KEY: lived}
        compact_appraisals = compact_appraisals_for_model_view(
            materials.get("appraisals"),
            logical_time=self.logical_time,
        )
        if compact_appraisals is not materials.get("appraisals"):
            materials = {**materials, "appraisals": compact_appraisals}
        compact_affect = compact_affect_for_model_view(materials.get("affect"))
        if compact_affect is not materials.get("affect"):
            materials = {**materials, "affect": compact_affect}
        faculties: dict[str, object] = {}
        for facet in self.facet_views:
            raw_keys = facet.content.get("material_keys")
            keys = (
                [key for key in raw_keys if isinstance(key, str) and key in materials]
                if isinstance(raw_keys, list)
                else []
            )
            faculties[facet.name] = {
                "availability": (
                    "available"
                    if facet.availability == "available" and bool(keys)
                    else "unavailable"
                ),
                "material_keys": keys,
            }
        cursor = self.cursor.model_dump(mode="json") if self.cursor else {}
        if self.logical_time is not None:
            cursor["logical_time"] = self.logical_time.isoformat()
        return {
            "contract": self.contract,
            "authority": "derived_from_verified_context",
            "availability": self.availability,
            "snapshot_id": self.snapshot_id,
            "snapshot_hash": self.snapshot_hash,
            "world_id": self.world_id,
            "actor_ref": self.actor_ref,
            "cursor": cursor,
            "materials": materials,
            "faculties": faculties,
            "source_refs": [ref for ref in self.source_refs if ref in visible],
            "source_inventory": [
                item.model_view() for item in self.source_inventory if item.source_ref in visible
                and (include_lifecycle_states or item.scope != "activity_lifecycle_states")
            ],
            "viewer_scope": self.viewer_scope.model_view(),
            "privacy_scope": self.privacy_scope.model_view(),
            "capability_scope": self.capability_scope.model_view(),
            "context_compiler": self.context_compiler.model_view(),
            "snapshot_compiler": self.snapshot_compiler.model_view(),
            "truncation": self.truncation.model_view(),
        }


class _InstantPrivateSelf(FrozenModel):
    """Short model-authored stance for one Inner Turn, never a durable fact."""

    contract: Literal["instant-private-self.1"] = "instant-private-self.1"
    summary: str = Field(min_length=1, max_length=1_024)
    attended_source_refs: tuple[str, ...] = Field(default=(), max_length=32)

    @field_validator("summary")
    @classmethod
    def summary_has_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("instant private self summary cannot be blank")
        return value

    @field_validator("attended_source_refs")
    @classmethod
    def attention_is_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("instant private self attention refs must be unique")
        return value


class _PrivateSelfLineage(FrozenModel):
    """The valid private-self states on one Inner Turn.

    A one-pass turn has one authored state which is both initial and final.  A
    selective-Recall turn preserves the valid pre-Recall state and explicitly
    relates the post-Recall state to that author call.  This is proposal audit,
    not hidden reasoning and not a durable World fact.
    """

    contract: Literal["private-self-lineage.1"] = "private-self-lineage.1"
    relation: Literal["single_pass", "selective_recall"]
    initial_private_self: _InstantPrivateSelf
    initial_snapshot_id: str = Field(min_length=1, max_length=128)
    initial_snapshot_hash: str = Field(min_length=64, max_length=64)
    initial_author_lineage: _InteriorAuthorLineage | None = None
    recall_query: str | None = Field(default=None, min_length=1, max_length=1_024)
    final_private_self: _InstantPrivateSelf
    final_snapshot_id: str = Field(min_length=1, max_length=128)
    final_snapshot_hash: str = Field(min_length=64, max_length=64)
    final_author_lineage: _InteriorAuthorLineage | None = None
    final_parent_model_call_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
    )

    @model_validator(mode="after")
    def states_and_parent_match_relation(self) -> "_PrivateSelfLineage":
        if self.relation == "single_pass":
            if any(
                (
                    self.recall_query is not None,
                    self.final_parent_model_call_id is not None,
                    self.initial_private_self != self.final_private_self,
                    self.initial_snapshot_id != self.final_snapshot_id,
                    self.initial_snapshot_hash != self.final_snapshot_hash,
                    self.initial_author_lineage != self.final_author_lineage,
                )
            ):
                raise ValueError("single-pass private self has an invalid lineage")
            return self

        if self.recall_query is None:
            raise ValueError("selective-Recall private self requires its query")
        initial_author = self.initial_author_lineage
        final_author = self.final_author_lineage
        expected_parent = initial_author.model_call_id if initial_author is not None else None
        if self.final_parent_model_call_id != expected_parent:
            raise ValueError("post-Recall private self has an invalid parent")
        if initial_author is not None and final_author is not None:
            if (
                initial_author.model_id != final_author.model_id
                or initial_author.model_version != final_author.model_version
                or initial_author.model_call_id == final_author.model_call_id
            ):
                raise ValueError("selective Recall changed or reused its character author")
        return self


class InnerTransition(FrozenModel):
    """Sparse private effects proposed after experiencing one stimulus."""

    contract: Literal["character-inner-transition.1"] = "character-inner-transition.1"
    inner_turn_id: str = Field(min_length=1, max_length=128)
    stimulus_ref: str = Field(min_length=1, max_length=512)
    actor_ref: str = Field(min_length=1, max_length=256)
    cursor: ProjectionCursor
    snapshot_id: str | None = Field(default=None, min_length=1, max_length=128)
    snapshot_hash: str | None = Field(default=None, min_length=64, max_length=64)
    status: Literal["transitioned", "model_no_change", "technical_failure"]
    summary: str | None = Field(default=None, min_length=1, max_length=1_024)
    attended_source_refs: tuple[str, ...] = Field(default=(), max_length=32)
    instant_private_self: _InstantPrivateSelf | None = None
    private_self_lineage: _PrivateSelfLineage | None = None
    proposal_refs: tuple[str, ...] = Field(default=(), max_length=32)
    author_lineage: _InteriorAuthorLineage | None = None
    presented_prefetch_traces: tuple[PrefetchPresentationAudit, ...] = Field(
        default=(), max_length=4
    )
    failure_code: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def terminal_shape_matches_status(self) -> "InnerTransition":
        if self.status == "technical_failure":
            if (
                self.failure_code is None
                or self.summary is not None
                or self.instant_private_self is not None
                or self.private_self_lineage is not None
                or self.proposal_refs
                or self.author_lineage is not None
                or self.presented_prefetch_traces
            ):
                raise ValueError("technical inner transition has an invalid terminal shape")
        elif any(
            (
                self.failure_code is not None,
                self.summary is None,
                self.snapshot_id is None,
                self.snapshot_hash is None,
                self.instant_private_self is None,
                self.private_self_lineage is None,
                self.author_lineage is None,
            )
        ):
            raise ValueError("model inner transition lacks its authored lineage")
        elif (
            self.instant_private_self.summary != self.summary
            or self.instant_private_self.attended_source_refs != self.attended_source_refs
        ):
            raise ValueError("instant private self is not bound to the transition")
        if self.private_self_lineage is not None and (
            self.instant_private_self != self.private_self_lineage.final_private_self
            or self.author_lineage != self.private_self_lineage.final_author_lineage
            or self.snapshot_id != self.private_self_lineage.final_snapshot_id
            or self.snapshot_hash != self.private_self_lineage.final_snapshot_hash
        ):
            raise ValueError("private-self lineage is not bound to the transition")
        if len(self.attended_source_refs) != len(set(self.attended_source_refs)):
            raise ValueError("inner transition attention refs must be unique")
        return self


class InnerDecision(FrozenModel):
    """The character's decision, explicit silence, or a technical failure."""

    contract: Literal["character-inner-decision.1"] = "character-inner-decision.1"
    inner_turn_id: str = Field(min_length=1, max_length=128)
    opportunity_ref: str = Field(min_length=1, max_length=512)
    actor_ref: str = Field(min_length=1, max_length=256)
    cursor: ProjectionCursor
    snapshot_id: str | None = Field(default=None, min_length=1, max_length=128)
    snapshot_hash: str | None = Field(default=None, min_length=64, max_length=64)
    status: Literal["decided", "model_silent", "technical_failure"]
    summary: str | None = Field(default=None, min_length=1, max_length=1_024)
    attended_source_refs: tuple[str, ...] = Field(default=(), max_length=32)
    instant_private_self: _InstantPrivateSelf | None = None
    private_self_lineage: _PrivateSelfLineage | None = None
    decision: dict[str, Any] | None = None
    proposal_refs: tuple[str, ...] = Field(default=(), max_length=32)
    author_lineage: _InteriorAuthorLineage | None = None
    presented_prefetch_traces: tuple[PrefetchPresentationAudit, ...] = Field(
        default=(), max_length=4
    )
    failure_code: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def terminal_shape_matches_status(self) -> "InnerDecision":
        if self.status == "technical_failure":
            if any(
                (
                    self.failure_code is None,
                    self.summary is not None,
                    self.instant_private_self is not None,
                    self.private_self_lineage is not None,
                    self.decision is not None,
                    bool(self.proposal_refs),
                    self.author_lineage is not None,
                    bool(self.presented_prefetch_traces),
                )
            ):
                raise ValueError("technical inner decision has an invalid terminal shape")
        elif self.status == "model_silent":
            if self.failure_code is not None or self.summary is None or self.decision is not None:
                raise ValueError("model silence has an invalid terminal shape")
        elif self.failure_code is not None or self.summary is None or self.decision is None:
            raise ValueError("character decision has an invalid terminal shape")
        if self.status != "technical_failure":
            if any(
                (
                    self.snapshot_id is None,
                    self.snapshot_hash is None,
                    self.instant_private_self is None,
                    self.private_self_lineage is None,
                    self.author_lineage is None,
                )
            ):
                raise ValueError("model inner decision lacks its authored lineage")
            if (
                self.instant_private_self.summary != self.summary
                or self.instant_private_self.attended_source_refs != self.attended_source_refs
            ):
                raise ValueError("instant private self is not bound to the decision")
        if self.private_self_lineage is not None and (
            self.instant_private_self != self.private_self_lineage.final_private_self
            or self.author_lineage != self.private_self_lineage.final_author_lineage
            or self.snapshot_id != self.private_self_lineage.final_snapshot_id
            or self.snapshot_hash != self.private_self_lineage.final_snapshot_hash
        ):
            raise ValueError("private-self lineage is not bound to the decision")
        if len(self.attended_source_refs) != len(set(self.attended_source_refs)):
            raise ValueError("inner decision attention refs must be unique")
        return self


__all__ = [
    "COMPILE_TIME_SOURCELESS_ALLOWLIST",
    "INNER_RETENTION_MATERIAL_KEY",
    "LIVED_MOMENT_MATERIAL_KEY",
    "POST_REDACTION_ONLY_KEYS",
    "compile_time_material_is_source_bound",
    "assert_compile_time_materials_are_source_bound",
    "InteriorAffectTransition",
    "InteriorAffectNewComponentTarget",
    "InteriorAffectExistingComponentTarget",
    "InteriorAffectOpenTransition",
    "InteriorAffectUpdateTransition",
    "InteriorAffectResolveTransition",
    "InteriorAffectSupersedeTransition",
    "InteriorStimulus",
    "InteriorOpportunity",
    "InnerLifeSnapshot",
    "InnerTransition",
    "InnerDecision",
]
