"""Deterministic compiler for the one canonical ``InnerLifeSnapshot``.

The input is the verified Context Capsule's model material.  This module owns
the semantic join and snapshot identity; provider-specific views may only
redact its model view and must retain the same snapshot id and hash.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from collections.abc import Iterable, Mapping
from zoneinfo import ZoneInfo

from ..dialogue_fold import fold_dialogue_entries, fold_dialogue_line
from ..pinned_source_ref import PinnedSourceCatalog
from ..photographable_inventory import (
    PhotographableMomentFact,
    compile_photographable_inventory,
    empty_inventory_material,
    inventory_material,
)
from ..present_moment_candidate import (
    activity_kind_is_sleep,
    present_moment_material,
    PresentMomentFact,
)
from ..present_prompt import (
    PRESENT_ACCEPTED_RELATIONSHIP_COMMITMENT_LIMIT,
    PRESENT_AUTHORED_RELATIONSHIP_SIGNAL_LIMIT,
    PRESENT_DIALOGUE_SLICE_CHARACTERS,
    PRESENT_EXPERIENCE_ITEM_LIMIT,
    PRESENT_FACT_ITEM_LIMIT,
    PRESENT_IMPRESSION_ITEM_LIMIT,
    PRESENT_MEMORY_ITEM_LIMIT,
    PRESENT_PENDING_OUTBOUND_ITEM_LIMIT,
    PRESENT_SHARED_MEDIA_ITEM_LIMIT,
    PRESENT_WEEK_DIARY_DAYS,
    PRESENT_WEEK_DIARY_LINES_PER_DAY,
)
from ..schemas import ProjectionCursor
from .contracts import (
    FACET_NAMES,
    InnerLifeSnapshot,
    assert_compile_time_materials_are_source_bound,
    _InteriorBinding,
    _InteriorContextView,
    _InteriorFacet,
    _InteriorSourceAuthorityBinding,
    _InteriorSourceInventoryItem,
    _elapsed_phrase,
    _instant,
)


SNAPSHOT_COMPILER_VERSION = "inner-life-snapshot-compiler.17"

_AUTHORITY_VALUE_KEYS = frozenset(
    {
        "origin",
        "proposal_source",
        "source_bindings",
        "source_evidence_refs",
        "anchor_evidence_refs",
        "source_revisions",
        "policy_versions",
        "policy_refs",
        "resolver_proof",
        "accepted_event_ref",
        "entity_revision",
        "authority_contract_version",
        "semantic_fingerprint",
    }
)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _semantic_value(value: object) -> object:
    if isinstance(value, list):
        return [_semantic_value(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: _semantic_value(item)
        for key, item in value.items()
        if isinstance(key, str)
        and key not in _AUTHORITY_VALUE_KEYS
        and not key.endswith("_hash")
        and not key.endswith("_digest")
        and not key.endswith("_version")
    }


def _dialogue_causal_order(entry: Mapping[str, object]) -> tuple[int, str, str]:
    sequence = entry.get("sequence")
    seq = sequence if isinstance(sequence, int) and not isinstance(sequence, bool) else 0
    occurred = entry.get("occurred_at")
    if isinstance(occurred, datetime):
        occurred_key = occurred.isoformat()
    elif isinstance(occurred, str):
        occurred_key = occurred
    else:
        occurred_key = ""
    dialogue_id = entry.get("dialogue_id")
    did = dialogue_id if isinstance(dialogue_id, str) else ""
    return (seq, occurred_key, did)


def _media_delivery_dialogue_source(entry: dict[str, object]) -> dict[str, object]:
    dialogue_id = entry.get("dialogue_id")
    prefix = "dialogue:media-delivery:"
    if isinstance(dialogue_id, str) and dialogue_id.startswith(prefix):
        delivery_id = dialogue_id.removeprefix(prefix)
        if delivery_id:
            return {**entry, "source_ref": delivery_id}
    return entry


def _living_or_capsule_items(
    slices: Mapping[str, object], *, living_name: str, capsule_name: str
) -> list[dict[str, object]]:
    """Prefer a projection-installed living lane, including a true empty set."""

    living = slices.get(living_name)
    if isinstance(living, dict) and living.get("availability") == "available":
        return _slice_items(slices, living_name)
    return _slice_items(slices, capsule_name)


def _slice_items(slices: Mapping[str, object], name: str) -> list[dict[str, object]]:
    lane = slices.get(name)
    if not isinstance(lane, dict) or lane.get("availability") != "available":
        return []
    items = lane.get("items")
    if not isinstance(items, list):
        return []
    normalized: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        source_ref = item.get("source_ref")
        if not isinstance(source_ref, str):
            source_ref = item.get("item_ref")
        normalized.append(
            {
                **item,
                **({"source_ref": source_ref} if isinstance(source_ref, str) else {}),
            }
        )
    return normalized


def _state_entry(
    item: dict[str, object], *, fields: tuple[str, ...] | None = None
) -> dict[str, object] | None:
    source_ref = item.get("source_ref")
    value = item.get("value")
    if not isinstance(source_ref, str) or not source_ref or not isinstance(value, dict):
        return None
    semantic = (
        {key: value[key] for key in fields if key in value}
        if fields is not None
        else value
    )
    semantic = _semantic_value(semantic)
    if not isinstance(semantic, dict) or not semantic:
        return None
    return {**semantic, "source_ref": source_ref}


_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _shared_photo_when(
    shared_at: object, logical_time: datetime | None
) -> str | None:
    instant = shared_at if isinstance(shared_at, datetime) else _instant(shared_at)
    if instant is None or logical_time is None:
        return None
    if logical_time.tzinfo is None or logical_time.utcoffset() is None:
        return None
    if instant > logical_time:
        # Execution receipts may stamp wall time after the conversation clock.
        # A committed delivery is still "刚刚", not a future event.
        return "刚刚"
    return _elapsed_phrase(instant, logical_time)


def _shared_photo_clock(shared_at: object) -> str | None:
    instant = shared_at if isinstance(shared_at, datetime) else _instant(shared_at)
    if instant is None:
        return None
    return instant.astimezone(_SHANGHAI).strftime("%H:%M")


def _photos_i_shared_entry(
    item: dict[str, object], *, logical_time: datetime | None
) -> dict[str, object] | None:
    """Bind one delivery as a fact she can read, not a UTC sidecar."""

    entry = _state_entry(
        item,
        fields=(
            "delivery_id",
            "shared_at",
            "family",
            "kind",
            "privacy_layer",
            "about",
            "he_spoke_after",
        ),
    )
    if entry is None:
        return None
    about = entry.get("about")
    if not (isinstance(about, str) and about.strip()):
        return entry
    when = _shared_photo_when(entry.get("shared_at"), logical_time)
    clock = _shared_photo_clock(entry.get("shared_at"))
    after = (
        "发出之后他又开口了。"
        if entry.get("he_spoke_after") is True
        else "发出之后他还没回这张。"
    )
    age = when or "已经"
    stamp = f"（当地{clock}）" if clock else ""
    if when:
        entry["when"] = when
    if clock:
        entry["local_clock"] = clock
    entry["already_in_chat"] = True
    entry["line"] = (
        f"{age}{stamp}已经发给他{about.strip()}，这张已经出现在你们的对话里。{after}"
    )
    return entry


def _messages_waiting_to_send_entry(
    item: dict[str, object], *, logical_time: datetime | None
) -> dict[str, object] | None:
    """Bind one unsent later followup as a fact she can read."""

    entry = _state_entry(
        item,
        fields=(
            "action_id",
            "plan_id",
            "beat_id",
            "text",
            "written_at",
            "send_at",
            "he_spoke_after",
            "i_spoke_after",
        ),
    )
    if entry is None:
        return None
    text = entry.get("text")
    if not (isinstance(text, str) and text.strip()):
        return entry
    body = text.strip()
    preview = body if len(body) <= 240 else body[:239] + "…"
    written_clock = _shared_photo_clock(entry.get("written_at"))
    send_clock = _shared_photo_clock(entry.get("send_at"))
    when = _shared_photo_when(entry.get("written_at"), logical_time)
    written_stamp = f"当地{written_clock}写好" if written_clock else "已经写好"
    send_stamp = f"定在当地{send_clock}发出" if send_clock else "还没发出"
    i_spoke = (
        "写好之后你另外发过话。"
        if entry.get("i_spoke_after") is True
        else "写好之后你没另外发过。"
    )
    he_spoke = (
        "写好之后他又开口了。"
        if entry.get("he_spoke_after") is True
        else "写好之后他还没回。"
    )
    if when:
        entry["when"] = when
    if written_clock:
        entry["written_clock"] = written_clock
    if send_clock:
        entry["send_clock"] = send_clock
    entry["already_in_chat"] = False
    entry["line"] = (
        f"{written_stamp}、还没发出，{send_stamp}。正文：「{preview}」{i_spoke}{he_spoke}"
    )
    return entry


_ORDINARY_COMMITTED_STAGES = frozenset({"acquaintance", "friend", "close_friend"})
_RELATIONSHIP_HEAD_FIELDS = (
    "relationship_id",
    "direction",
    "subject_ref",
    "stage",
    "variables",
    "temperature",
    "hysteresis",
    "commitment_refs",
    "last_adjusted_at",
    "recent_authored_signals",
    "accepted_commitments",
)
_USER_RELATIONSHIP_HEAD_FIELDS = (
    "subject_ref",
    "stage",
    "variables",
    "temperature",
    "hysteresis",
    "commitment_refs",
    "last_adjusted_at",
    "recent_authored_signals",
    "accepted_commitments",
)


def _authored_signal_view(item: object) -> dict[str, object] | None:
    if not isinstance(item, dict):
        return None
    signal_code = item.get("signal_code")
    rationale_code = item.get("rationale_code")
    confidence_bp = item.get("confidence_bp")
    if not isinstance(signal_code, str) or not signal_code.strip():
        return None
    if not isinstance(rationale_code, str) or not rationale_code.strip():
        return None
    if not isinstance(confidence_bp, int) or isinstance(confidence_bp, bool):
        return None
    if not 1 <= confidence_bp <= 10_000:
        return None
    return {
        "signal_code": signal_code.strip(),
        "rationale_code": rationale_code.strip(),
        "confidence_bp": confidence_bp,
    }


def _accepted_commitment_view(item: object) -> dict[str, object] | None:
    if not isinstance(item, dict):
        return None
    committed_stage = item.get("committed_stage")
    commitment_code = item.get("commitment_code")
    visible_text_span = item.get("visible_text_span")
    if committed_stage not in _ORDINARY_COMMITTED_STAGES:
        return None
    if not isinstance(commitment_code, str) or not commitment_code.strip():
        return None
    if not isinstance(visible_text_span, str) or not visible_text_span.strip():
        return None
    return {
        "committed_stage": committed_stage,
        "commitment_code": commitment_code.strip(),
        "visible_text_span": visible_text_span.strip(),
    }


def _relationship_entry(
    item: dict[str, object], *, fields: tuple[str, ...]
) -> dict[str, object] | None:
    entry = _state_entry(item, fields=fields)
    if entry is None:
        return None
    raw_signals = entry.get("recent_authored_signals")
    signals: list[dict[str, object]] = []
    if isinstance(raw_signals, (list, tuple)):
        for candidate in raw_signals:
            viewed = _authored_signal_view(candidate)
            if viewed is None:
                continue
            signals.append(viewed)
            if len(signals) >= PRESENT_AUTHORED_RELATIONSHIP_SIGNAL_LIMIT:
                break
    raw_commitments = entry.get("accepted_commitments")
    commitments: list[dict[str, object]] = []
    if isinstance(raw_commitments, (list, tuple)):
        for candidate in raw_commitments:
            viewed = _accepted_commitment_view(candidate)
            if viewed is None:
                continue
            commitments.append(viewed)
            if len(commitments) >= PRESENT_ACCEPTED_RELATIONSHIP_COMMITMENT_LIMIT:
                break
    if signals:
        entry["recent_authored_signals"] = signals
    else:
        entry.pop("recent_authored_signals", None)
    if commitments:
        entry["accepted_commitments"] = commitments
    else:
        entry.pop("accepted_commitments", None)
    return entry


def _core_entry(item: dict[str, object]) -> dict[str, object] | None:
    source_ref = item.get("source_ref")
    value = item.get("value")
    values = value.get("values") if isinstance(value, dict) else None
    slow = values.get("slow_evolving") if isinstance(values, dict) else None
    if not isinstance(source_ref, str) or not isinstance(slow, dict):
        return None
    return {"slow_evolving": _semantic_value(slow), "source_ref": source_ref}


def _affect_entry(item: dict[str, object]) -> dict[str, object] | None:
    source_ref = item.get("source_ref")
    value = item.get("value")
    if not isinstance(source_ref, str) or not isinstance(value, dict):
        return None
    components: list[dict[str, object]] = []
    for component in value.get("components", []):
        if not isinstance(component, dict) or not isinstance(component.get("dimension"), str):
            continue
        continuity = {
            key: _semantic_value(component[key])
            for key in (
                "component_id",
                "dimension",
                "source_cluster_ref",
                "appraisal_refs",
                "intensity_bp",
                "decay_anchor_intensity_bp",
                "residue_bp",
                "opened_at",
                "decay_anchor_at",
                "last_stimulus_at",
                "last_updated_at",
                "decay_not_before",
            )
            if key in component
        }
        decay_profile = component.get("decay_profile")
        if isinstance(decay_profile, dict):
            # These are already accepted deterministic lifecycle parameters,
            # not a second mood verdict.  Keep the complete profile so the
            # character can perceive whether a feeling is rising, lingering,
            # or only resting on residue instead of seeing one flat number.
            continuity["decay_profile"] = {
                key: decay_profile[key]
                for key in (
                    "kind",
                    "half_life_seconds",
                    "floor_bp",
                    "delay_seconds",
                    "config_version",
                    "algorithm_version",
                    "table_digest",
                    "rounding_mode",
                    "config_digest",
                )
                if key in decay_profile
            }
        components.append(continuity)
    if not components:
        return None
    return {
        "components": components,
        **{
            key: _semantic_value(value[key])
            for key in (
                "episode_id",
                "entity_revision",
                "status",
                "opened_at",
                "updated_at",
                "expression_history_refs",
                "closed_at",
                "resolution_refs",
                "supersedes_episode_id",
                "superseded_by_episode_id",
            )
            if value.get(key) is not None
        },
        "source_ref": source_ref,
    }


def _recalled_entry(
    item: dict[str, object], *, kinds: frozenset[str]
) -> dict[str, object] | None:
    source_ref = item.get("source_ref")
    value = item.get("value")
    if (
        not isinstance(source_ref, str)
        or not isinstance(value, dict)
        or value.get("memory_kind") not in kinds
        or not isinstance(value.get("actor_ref"), str)
        or not isinstance(value.get("text"), str)
        or not value["text"].strip()
    ):
        return None
    fields = (
        "memory_kind",
        "authority",
        "epistemic_scope",
        "actor_ref",
        "speaker_ref",
        "subject_refs",
        "text",
        "occurred_from",
        "occurred_to",
        "valid_from",
        "valid_to",
        "status",
    )
    return {
        **{key: value[key] for key in fields if value.get(key) is not None},
        "source_ref": source_ref,
    }


def _experience_entry(
    item: dict[str, object], *, lane: str
) -> dict[str, object] | None:
    recalled = _recalled_entry(item, kinds=frozenset({"episodic"}))
    if recalled is not None:
        return recalled
    source_ref = item.get("source_ref")
    value = item.get("value")
    if not isinstance(source_ref, str) or not isinstance(value, dict):
        return None
    if lane == "world_life":
        if value.get("context_kind") == "biographical_context":
            return None
        fields = (
            (
                "occurrence_id",
                "occurrence_entity_revision",
                "participant_refs",
                "location_ref",
                "time_window",
                "activated_at",
                "status",
                "privacy_class",
                "premise",
            )
            if value.get("context_kind") == "active_world_occurrence"
            else (
                "occurrence_id",
                "occurrence_entity_revision",
                "participant_refs",
                "location_ref",
                "result_id",
                "settled_at",
                "privacy_class",
                "content",
                "photo_in_hand",
            )
        )
        semantic = {key: value[key] for key in fields if key in value}
    else:
        values = value.get("values")
        if not isinstance(values, dict):
            return None
        semantic = {
            **(
                {"experience_id": value["experience_id"]}
                if isinstance(value.get("experience_id"), str)
                else {}
            ),
            **{
                key: values[key]
                for key in (
                    "summary_ref",
                    "occurred_from",
                    "occurred_to",
                    "participant_refs",
                    "privacy_class",
                )
                if key in values
            },
        }
        content = value.get("content")
        if isinstance(content, dict) and isinstance(content.get("text"), str):
            semantic["content"] = {
                key: content[key]
                for key in ("content_ref", "text", "truncated")
                if key in content
            }
    semantic = _semantic_value(semantic)
    return (
        {**semantic, "source_ref": source_ref}
        if isinstance(semantic, dict) and semantic
        else None
    )


def _slice_source_refs(slices: Mapping[str, object], name: str) -> tuple[str, ...]:
    lane = slices.get(name)
    if not isinstance(lane, dict):
        return ()
    refs = lane.get("source_refs")
    if isinstance(refs, (list, tuple)):
        return tuple(item for item in refs if isinstance(item, str) and item)
    return tuple(
        item["source_ref"]
        for item in _slice_items(slices, name)
        if isinstance(item.get("source_ref"), str)
    )


def _moment_what_happened(entry: Mapping[str, object]) -> str | None:
    content = entry.get("content")
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()[:240]
    if isinstance(content, str) and content.strip():
        return content.strip()[:240]
    happened = entry.get("what_happened")
    if isinstance(happened, str) and happened.strip():
        return happened.strip()[:240]
    return None


_SHAREABLE_HOLD = frozenset(
    {
        "expired",
        "already_shared",
        "skipped",
        "unrenderable",
        "failed",
        "already_chosen",
        "not_available",
    }
)


def _parse_optional_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _shareable_photo_entry(item: Mapping[str, object]) -> dict[str, object] | None:
    source_ref = item.get("source_ref")
    value = item.get("value")
    if not isinstance(source_ref, str) or not source_ref:
        return None
    if not isinstance(value, dict):
        value = {}
    privacy = value.get("privacy_class") or item.get("privacy_class")
    return {
        "source_ref": source_ref,
        "privacy_class": privacy,
        "photo_in_hand": value.get("photo_in_hand") is True,
        "hold_reason": value.get("hold_reason"),
        "family": value.get("family"),
        "candidate_id": value.get("candidate_id"),
        "taxonomy": value.get("taxonomy"),
        "location_ref": value.get("location_ref"),
        "expires_at": value.get("expires_at"),
        "settled_at": value.get("settled_at"),
        "content": value.get("content"),
        "what_happened": value.get("what_happened"),
    }


def _moment_fact_from_entry(
    entry: Mapping[str, object],
) -> PhotographableMomentFact | None:
    source_ref = entry.get("source_ref")
    if not isinstance(source_ref, str) or not source_ref:
        return None
    privacy = entry.get("privacy_class")
    if privacy not in {"public", "shareable", "personal", "private"}:
        return None
    hold = entry.get("hold_reason")
    if hold not in _SHAREABLE_HOLD:
        hold = None
    family = entry.get("family")
    candidate_id = entry.get("candidate_id")
    taxonomy = entry.get("taxonomy")
    location_ref = entry.get("location_ref")
    return PhotographableMomentFact(
        source_ref=source_ref,
        photo_in_hand=entry.get("photo_in_hand") is True,
        privacy_class=privacy,
        settled_at=_parse_optional_datetime(entry.get("settled_at")),
        expires_at=_parse_optional_datetime(entry.get("expires_at")),
        location_ref=location_ref if isinstance(location_ref, str) else None,
        what_happened=_moment_what_happened(entry),
        hold_reason=hold,
        family=family if isinstance(family, str) else None,
        candidate_id=candidate_id if isinstance(candidate_id, str) else None,
        taxonomy=taxonomy if isinstance(taxonomy, str) else None,
    )


def _present_moment_from_situation(slices: Mapping[str, object]) -> dict[str, object]:
    """Whether she is in an active, non-sleep activity that could be photographed."""

    items = _slice_items(slices, "current_situation")
    refs = _slice_source_refs(slices, "current_situation")
    active: dict[str, object] | None = None
    for item in items:
        value = item.get("value") if isinstance(item, dict) else None
        slices_of = value.get("activity_slices") if isinstance(value, dict) else None
        if not isinstance(slices_of, list):
            continue
        for row in slices_of:
            if not isinstance(row, dict) or row.get("status") != "active":
                continue
            active = row
            break
        if active is not None:
            break
    if active is None:
        fact = PresentMomentFact(photographable=False, reason="no_active_activity")
    elif activity_kind_is_sleep(active.get("activity_kind")):
        fact = PresentMomentFact(
            photographable=False,
            reason="sleep",
            plan_id=active.get("plan_id") if isinstance(active.get("plan_id"), str) else None,
            activity_kind=active.get("activity_kind")
            if isinstance(active.get("activity_kind"), str)
            else None,
        )
    else:
        fact = PresentMomentFact(
            photographable=True,
            reason="active",
            plan_id=active.get("plan_id") if isinstance(active.get("plan_id"), str) else None,
            activity_kind=active.get("activity_kind")
            if isinstance(active.get("activity_kind"), str)
            else None,
        )
    payload = present_moment_material(fact)
    if refs:
        payload["source_refs"] = list(refs)
    return payload


def _moments_i_can_share(
    slices: Mapping[str, object],
    *,
    recent: list[dict[str, object]],
    already_sent_count: int,
) -> dict[str, object] | None:
    """Always state album emptiness.  Never a suggestion to take or send."""

    shareable_lane = slices.get("shareable_photos")
    using_candidates = (
        isinstance(shareable_lane, dict)
        and shareable_lane.get("availability") == "available"
    )
    if using_candidates:
        source_entries = [
            entry
            for item in _slice_items(slices, "shareable_photos")
            if (entry := _shareable_photo_entry(item))
        ]
    else:
        source_entries = recent
    moments: list[PhotographableMomentFact] = []
    for entry in source_entries:
        fact = _moment_fact_from_entry(entry)
        if fact is not None:
            moments.append(fact)
    extra = (
        _slice_source_refs(slices, "shareable_photos")
        or _slice_source_refs(slices, "world_life")
        or _slice_source_refs(slices, "media_deliveries")
        or _slice_source_refs(slices, "current_situation")
    )
    if not moments:
        if not extra:
            world_life = slices.get("world_life")
            media = slices.get("media_deliveries")
            situation = slices.get("current_situation")
            if not (
                isinstance(world_life, dict) and world_life.get("availability") == "available"
                or isinstance(media, dict) and media.get("availability") == "available"
                or isinstance(situation, dict) and situation.get("availability") == "available"
            ):
                return None
            return _with_present_moment(empty_inventory_material(source_refs=extra), slices)
        return _with_present_moment(empty_inventory_material(source_refs=extra), slices)
    return _with_present_moment(
        inventory_material(
            compile_photographable_inventory(
                moments=tuple(moments),
                already_sent_count=already_sent_count,
                extra_source_refs=extra,
            )
        ),
        slices,
    )


def _with_present_moment(
    material: dict[str, object], slices: Mapping[str, object]
) -> dict[str, object]:
    now = _present_moment_from_situation(slices)
    material = dict(material)
    material["now"] = now
    refs = list(material.get("source_refs") or [])
    for ref in now.get("source_refs") or ():
        if isinstance(ref, str) and ref and ref not in refs:
            refs.append(ref)
    source_ref = now.get("source_ref")
    if isinstance(source_ref, str) and source_ref and source_ref not in refs:
        refs.append(source_ref)
    if refs and not material.get("source_refs"):
        material["source_refs"] = refs
    elif refs:
        material["source_refs"] = refs
    return material


def _material_refs(value: object) -> tuple[str, ...]:
    candidates = value.get("items") if isinstance(value, dict) else value
    if not isinstance(candidates, list):
        return ()
    return tuple(
        item["source_ref"]
        for item in candidates
        if isinstance(item, dict) and isinstance(item.get("source_ref"), str)
    )


def _inventory_text(item: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _unique_refs(value: object, *, keys: frozenset[str]) -> tuple[str, ...]:
    """Extract only explicitly typed ref fields, never arbitrary string values."""

    refs: list[str] = []

    def visit(item: object, field_name: str | None = None) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    continue
                if key in keys:
                    if isinstance(child, str) and child:
                        refs.append(child)
                    elif isinstance(child, list):
                        for candidate in child:
                            if isinstance(candidate, str) and candidate:
                                refs.append(candidate)
                            elif isinstance(candidate, dict):
                                ref = candidate.get("ref_id") or candidate.get("event_ref")
                                if isinstance(ref, str) and ref:
                                    refs.append(ref)
                    elif isinstance(child, dict):
                        ref = child.get("ref_id") or child.get("event_ref")
                        if isinstance(ref, str) and ref:
                            refs.append(ref)
                visit(child, key)
        elif isinstance(item, list):
            for child in item:
                visit(child, field_name)

    visit(value)
    return tuple(dict.fromkeys(refs))


_DIRECT_REF_FIELDS = frozenset(
    {
        "source_refs",
        "source_evidence_refs",
        "anchor_evidence_refs",
        "evidence_refs",
        "accepted_event_ref",
        "source_event_ref",
        "timeline_source_event_ref",
        "authority_event_ref",
        "event_ref",
    }
)
_PREDECESSOR_REF_FIELDS = frozenset(
    {
        "predecessor_refs",
        "predecessor_thread_refs",
        "predecessor_commitment_ref",
        "supersedes_episode_id",
        "supersedes_goal_id",
    }
)
_CONFLICT_REF_FIELDS = frozenset(
    {
        "conflict_refs",
        "contradiction_refs",
        "contradiction_group_ref",
        "conflict_key",
    }
)
_REVISION_REF_FIELDS = frozenset(
    {
        "revision_event_ref",
        "planted_event_ref",
        "superseded_by_episode_id",
        "superseded_by_thread_ref",
    }
)


def _entity_revision(value: object) -> int | None:
    if not isinstance(value, dict):
        return None
    for key in (
        "entity_revision",
        "occurrence_entity_revision",
        "source_entity_revision",
    ):
        candidate = value.get(key)
        if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate >= 1:
            return candidate
    nested = value.get("values")
    return _entity_revision(nested)


def _source_envelope(
    item: Mapping[str, object],
    *,
    trusted: Mapping[str, object] | None,
) -> tuple[
    tuple[_InteriorSourceAuthorityBinding, ...],
    tuple[str, ...],
    int | None,
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    envelope = trusted or item
    raw_bindings = envelope.get("source_bindings")
    bindings: list[_InteriorSourceAuthorityBinding] = []
    if isinstance(raw_bindings, (list, tuple)):
        for binding in raw_bindings:
            if not isinstance(binding, dict):
                continue
            bindings.append(_InteriorSourceAuthorityBinding.model_validate(binding))
    bindings.sort(
        key=lambda value: (
            value.source_kind,
            value.authority_type,
            value.ref,
            value.source_world_revision,
            value.immutable_hash,
        )
    )
    raw_value = envelope.get("value")
    if not isinstance(raw_value, dict):
        raw_value = item.get("value")
    authority_refs = [binding.ref for binding in bindings]
    direct = tuple(
        dict.fromkeys(
            (
                *authority_refs,
                *_unique_refs(raw_value, keys=_DIRECT_REF_FIELDS),
            )
        )
    )
    return (
        tuple(bindings),
        direct,
        _entity_revision(raw_value),
        _unique_refs(raw_value, keys=_PREDECESSOR_REF_FIELDS),
        _unique_refs(raw_value, keys=_CONFLICT_REF_FIELDS),
        _unique_refs(raw_value, keys=_REVISION_REF_FIELDS),
    )


def source_envelopes_from_capsule(capsule: object) -> dict[str, dict[str, object]]:
    """Extract full trusted item proofs without placing them in model material."""

    result: dict[str, dict[str, object]] = {}
    for lane in (
        "character_core",
        "current_situation",
        "recent_dialogue",
        "relationship_slice",
        "appraisals",
        "affect_episodes",
        "open_threads",
        "relevant_facts",
        "recent_experiences",
        "world_life",
        "perception_results",
        "active_memory_candidates",
        "available_capabilities",
        "action_budget",
        "private_impressions",
        "advisories",
    ):
        bound = getattr(capsule, lane, None)
        for item in getattr(bound, "items", ()):
            dumped = item.model_dump(mode="json")
            item_ref = dumped.get("item_ref")
            if not isinstance(item_ref, str):
                raise ValueError("trusted Capsule item has no stable ref")
            value = json.loads(item.payload_json)
            envelope = {**dumped, "value": value}
            existing = result.get(item_ref)
            if existing is not None and existing != envelope:
                raise ValueError("Capsule reused an item ref with conflicting authority")
            result[item_ref] = envelope
    return result


# The provider view only needs enough source tokens to ground its attended
# refs (the role contract caps attended_source_refs at eight); the durable
# snapshot keeps the full ref list for replay/audit authority.  Capping the
# visible slice avoids paying thousands of hash-id tokens on every turn.
_MAX_VISIBLE_SOURCE_REFS_PER_VIEW = 64


def _view(
    materials: Mapping[str, object], keys: tuple[str, ...]
) -> _InteriorContextView:
    selected = {key: materials[key] for key in keys if key in materials}
    refs = tuple(
        dict.fromkeys(ref for value in selected.values() for ref in _material_refs(value))
    )[:_MAX_VISIBLE_SOURCE_REFS_PER_VIEW]
    if not refs:
        return _InteriorContextView.from_material(
            availability="unavailable", content={}, source_refs=()
        )
    return _InteriorContextView.from_material(
        availability="available", content=selected, source_refs=refs
    )


def _datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _dialogue_stimulus_index(items: list[dict[str, object]]) -> dict[str, str]:
    index: dict[str, str] = {}
    for item in items:
        value = item.get("value")
        if not isinstance(value, dict):
            continue
        text = value.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        body = text.strip()
        for key in (item.get("source_ref"), value.get("dialogue_id")):
            if isinstance(key, str) and key:
                index[key] = body
        dialogue_id = value.get("dialogue_id")
        if isinstance(dialogue_id, str) and dialogue_id.startswith("dialogue:observation:"):
            index[dialogue_id.removeprefix("dialogue:observation:")] = body
        claims = value.get("source_claims")
        if isinstance(claims, list):
            for claim in claims:
                if isinstance(claim, dict):
                    ref = claim.get("authority_event_ref")
                    if isinstance(ref, str) and ref:
                        index[ref] = body
    return index


def _stimulus_excerpts(
    evidence_refs: object, dialogue_by_ref: Mapping[str, str]
) -> list[str]:
    if not isinstance(evidence_refs, list):
        return []
    excerpts: list[str] = []
    seen: set[str] = set()
    for ref in evidence_refs:
        if not isinstance(ref, dict):
            continue
        if ref.get("evidence_type") not in {"observed_message", "committed_world_event"}:
            continue
        ref_id = ref.get("ref_id")
        if not isinstance(ref_id, str):
            continue
        text = dialogue_by_ref.get(ref_id)
        if not text or text in seen:
            continue
        seen.add(text)
        excerpts.append(text)
    return excerpts


def _experience_line(entry: dict[str, object]) -> str | None:
    content = entry.get("content")
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()[:160]
    if isinstance(content, str) and content.strip():
        return content.strip()[:160]
    premise = entry.get("premise")
    if isinstance(premise, str) and premise.strip():
        return premise.strip()[:160]
    if isinstance(premise, dict):
        summary = premise.get("summary") or premise.get("text")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()[:160]
    return None


def _experience_day(
    entry: dict[str, object], timezone_name: str = "Asia/Shanghai"
) -> str | None:
    for key in ("occurred_to", "occurred_from", "settled_at", "activated_at"):
        instant = _datetime(entry.get(key))
        if instant is None:
            continue
        return instant.astimezone(ZoneInfo(timezone_name)).date().isoformat()
    window = entry.get("time_window")
    if isinstance(window, dict):
        for key in ("end", "start", "to", "from"):
            instant = _datetime(window.get(key))
            if instant is not None:
                return instant.astimezone(ZoneInfo(timezone_name)).date().isoformat()
    return None


def _week_diary(
    entries: list[dict[str, object]],
    logical_time: datetime | None,
) -> list[dict[str, object]]:
    """One sourced row per diary line so redaction can drop a hidden day."""

    if logical_time is None:
        return []
    local = logical_time.astimezone(ZoneInfo("Asia/Shanghai")).date()
    allowed = {
        (local - timedelta(days=offset)).isoformat()
        for offset in range(PRESENT_WEEK_DIARY_DAYS)
    }
    grouped: dict[str, list[dict[str, object]]] = {}
    for entry in entries:
        source_ref = entry.get("source_ref")
        if not isinstance(source_ref, str) or not source_ref:
            continue
        day = _experience_day(entry)
        if day not in allowed:
            continue
        line = _experience_line(entry)
        if line is None:
            continue
        existing = grouped.setdefault(day, [])
        if any(item.get("line") == line for item in existing):
            continue
        if len(existing) >= PRESENT_WEEK_DIARY_LINES_PER_DAY:
            continue
        existing.append({"date": day, "line": line, "source_ref": source_ref})
    diary: list[dict[str, object]] = []
    for offset in range(PRESENT_WEEK_DIARY_DAYS - 1, -1, -1):
        day = (local - timedelta(days=offset)).isoformat()
        diary.extend(grouped.get(day, ()))
    return diary


def _sourced_folded_dialogue(
    chunks: list[dict[str, object]],
    original: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Flatten fold chunks into sourced lines so a hidden message can drop."""

    by_id = {
        dialogue_id: entry
        for entry in original
        if isinstance(entry, dict)
        and isinstance((dialogue_id := entry.get("dialogue_id")), str)
        and dialogue_id
    }
    sourced: list[dict[str, object]] = []
    for ordinal, chunk in enumerate(chunks):
        ids = chunk.get("dialogue_ids")
        if not isinstance(ids, list):
            continue
        for dialogue_id in ids:
            if not isinstance(dialogue_id, str):
                continue
            entry = by_id.get(dialogue_id)
            if not isinstance(entry, dict):
                continue
            source_ref = entry.get("source_ref")
            if not isinstance(source_ref, str) or not source_ref:
                continue
            row: dict[str, object] = {
                "source_ref": source_ref,
                "dialogue_id": dialogue_id,
                "line": fold_dialogue_line(entry),
                "chunk_ordinal": ordinal,
            }
            occurred = entry.get("occurred_at")
            if isinstance(occurred, str) and occurred:
                row["occurred_at"] = occurred
            sourced.append(row)
    return sourced


def _cursor(context: Mapping[str, object]) -> ProjectionCursor | None:
    values = tuple(context.get(key) for key in (
        "world_revision", "deliberation_revision", "ledger_sequence"
    ))
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values):
        return None
    return ProjectionCursor(
        world_revision=values[0],
        deliberation_revision=values[1],
        ledger_sequence=values[2],
    )


def _binding(context: Mapping[str, object], key: str, reason: str) -> _InteriorBinding:
    value = context.get(key)
    return _InteriorBinding.available(value) if value is not None else _InteriorBinding.unavailable(reason)


def compile_inner_life_snapshot(
    context: Mapping[str, object],
    *,
    source_envelopes: Mapping[str, Mapping[str, object]] | None = None,
) -> InnerLifeSnapshot:
    """Compile the canonical typed snapshot from verified Capsule material."""

    raw_slices = context.get("slices")
    slices: Mapping[str, object] = raw_slices if isinstance(raw_slices, dict) else {}
    materials: dict[str, object] = {}
    logical_time = _datetime(context.get("logical_time"))
    if logical_time is not None:
        materials["logical_time"] = logical_time.isoformat()
    dialogue_items = _slice_items(slices, "recent_dialogue")
    dialogue_by_ref = _dialogue_stimulus_index(dialogue_items)

    stable = [entry for item in _slice_items(slices, "character_core") if (entry := _core_entry(item))]
    if stable:
        materials["stable_self"] = stable
    biography = [
        entry
        for item in _slice_items(slices, "world_life")
        if isinstance(item.get("value"), dict)
        and item["value"].get("context_kind") == "biographical_context"
        and (entry := _state_entry(item, fields=(
            "reviewed_timeline_ref", "timeline_source_event_ref", "logical_at", "age",
            "academic_phase", "academic_year", "season", "calendar_context_tags",
            "current_residence_context_tags", "active_life_arcs",
            "settled_biographical_coordinates",
        )))
    ]
    if biography:
        materials["biographical_context"] = biography

    lanes = (
        ("situation", "current_situation", (
            "logical_time", "time_segment", "activity_slices", "goal_slices",
            "resource_pressure", "attention_slice", "social_environment",
            "plan_relation", "commitment_slices",
        )),
        ("relationship", "relationship_slice", _USER_RELATIONSHIP_HEAD_FIELDS),
        (
            "protagonist_npc_relationships",
            "protagonist_npc_relationships",
            _RELATIONSHIP_HEAD_FIELDS,
        ),
        (
            "npc_observable_attitudes",
            "npc_observable_attitudes",
            (
                "direction",
                "npc_ref",
                "toward_actor_ref",
                "epistemic_scope",
                "observable_act",
            ),
        ),
        (
            "interaction_acts",
            "interaction_acts",
            (
                "frame",
                "participant_statuses",
                "external_outcome",
            ),
        ),
        ("advisories", "advisories", (
            "kind", "candidate_refs", "candidates", "confidence_bp", "expiry",
            "producer_version",
        )),
        ("perception", "perception_results", None),
    )
    for output, lane, fields in lanes:
        compile_entry = (
            _relationship_entry
            if output in {"relationship", "protagonist_npc_relationships"}
            else _state_entry
        )
        entries = [
            entry
            for item in _slice_items(slices, lane)
            if (entry := compile_entry(item, fields=fields))
        ]
        if entries:
            materials[output] = entries
    appraisal_fields = (
        "subject_ref", "source_cluster_ref", "hypotheses", "evidence_refs",
        "confidence_bp", "accepted_at", "expires_at",
    )
    unresolved_fields = (
        "kind", "subject_ref", "importance_bp", "due_window",
        "window_closes_at", "expected_response_ref", "status",
    )
    appraisals = [
        entry
        for item in _living_or_capsule_items(
            slices, living_name="living_appraisals", capsule_name="appraisals"
        )
        if (entry := _state_entry(item, fields=appraisal_fields))
    ]
    if appraisals:
        materials["appraisals"] = appraisals
    unresolved = [
        entry
        for item in _living_or_capsule_items(
            slices, living_name="living_threads", capsule_name="open_threads"
        )
        if (entry := _state_entry(item, fields=unresolved_fields))
    ]
    if unresolved:
        materials["unresolved"] = unresolved
    advisories = materials.get("advisories")
    if isinstance(advisories, list):
        interruption = [item for item in advisories if isinstance(item.get("kind"), str) and item["kind"].startswith("interruption.")]
        change_phase = [
            item for item in advisories if item.get("kind") == "change_phase"
        ]
        if interruption:
            materials["interruption"] = interruption
        if change_phase:
            materials["change_phase"] = change_phase
        extracted = {*map(id, interruption), *map(id, change_phase)}
        remaining = [item for item in advisories if id(item) not in extracted]
        if remaining:
            materials["advisories"] = remaining
        else:
            materials.pop("advisories")

    compiled_appraisals = materials.get("appraisals")
    if isinstance(compiled_appraisals, list):
        raw_appraisals = {
            item.get("source_ref"): item
            for item in _living_or_capsule_items(
                slices, living_name="living_appraisals", capsule_name="appraisals"
            )
            if isinstance(item.get("source_ref"), str)
        }
        for entry in compiled_appraisals:
            raw = raw_appraisals.get(entry.get("source_ref"))
            value = raw.get("value") if isinstance(raw, dict) else None
            excerpts = _stimulus_excerpts(
                value.get("evidence_refs") if isinstance(value, dict) else None,
                dialogue_by_ref,
            )
            if excerpts:
                entry["stimulus_excerpts"] = excerpts

    affect = [entry for item in _slice_items(slices, "affect_episodes") if (entry := _affect_entry(item))]
    if affect:
        materials["affect"] = affect
    remembered = [entry for item in _slice_items(slices, "active_memory_candidates") if (entry := _state_entry(item))][:PRESENT_MEMORY_ITEM_LIMIT]
    if remembered:
        materials["remembered_material"] = remembered
    impressions = [entry for item in _living_or_capsule_items(
        slices, living_name="living_impressions", capsule_name="private_impressions"
    ) if (entry := _state_entry(item, fields=(
        "subject_ref", "reflection_summary", "confidence_bp", "first_seen",
        "last_supported", "expiry_condition", "contradiction_refs", "status",
        "hold_reason",
    )))][:PRESENT_IMPRESSION_ITEM_LIMIT]
    if impressions:
        materials["private_impressions"] = impressions

    compiled_dialogue = [
        _media_delivery_dialogue_source(entry)
        for item in dialogue_items
        if (
            entry := _state_entry(
                item,
                fields=(
                    "dialogue_id",
                    "speaker",
                    "speaker_ref",
                    "text",
                    "occurred_at",
                    "delivery_state",
                    "acknowledges_observation_event_refs",
                    "continuity_reasons",
                    "sequence",
                ),
            )
        )
    ]
    compiled_dialogue.sort(key=_dialogue_causal_order)
    folded_dialogue, recent_dialogue = fold_dialogue_entries(
        compiled_dialogue,
        budget_characters=PRESENT_DIALOGUE_SLICE_CHARACTERS,
    )
    sourced_folds = _sourced_folded_dialogue(folded_dialogue, compiled_dialogue)
    if sourced_folds:
        materials["folded_dialogue"] = sourced_folds
    if recent_dialogue:
        materials["recent_dialogue"] = recent_dialogue

    # Verified facts are memory material, not host-authored conclusions about
    # what the character should do.  Keeping them in the same snapshot lets
    # every purpose reason from one source closure while preserving the role's
    # freedom to ignore or reinterpret their relevance.
    relevant_facts = [
        entry
        for item in _slice_items(slices, "relevant_facts")
        if (entry := _state_entry(item))
    ][:PRESENT_FACT_ITEM_LIMIT]
    if relevant_facts:
        materials["relevant_facts"] = relevant_facts

    experience_lanes = [
        [entry for item in _slice_items(slices, lane) if (entry := _experience_entry(item, lane=lane))]
        for lane in ("world_life", "recent_experiences")
    ]
    recent = [entries[0] for entries in experience_lanes if entries]
    if len(recent) < PRESENT_EXPERIENCE_ITEM_LIMIT:
        recent.extend(entry for entries in experience_lanes for entry in entries[1:])
    materials["recent_self_experiences"] = (
        {"availability": "available", "items": recent[:PRESENT_EXPERIENCE_ITEM_LIMIT]}
        if recent
        else {"availability": "unavailable"}
    )
    photos_i_shared = [
        entry
        for item in _slice_items(slices, "media_deliveries")
        if (entry := _photos_i_shared_entry(item, logical_time=logical_time))
    ][:PRESENT_SHARED_MEDIA_ITEM_LIMIT]
    if photos_i_shared:
        materials["photos_i_shared"] = photos_i_shared
    waiting = [
        entry
        for item in _slice_items(slices, "pending_outbound")
        if (entry := _messages_waiting_to_send_entry(item, logical_time=logical_time))
    ][:PRESENT_PENDING_OUTBOUND_ITEM_LIMIT]
    if waiting:
        materials["messages_waiting_to_send"] = waiting
    shareable = _moments_i_can_share(
        slices,
        recent=[
            entry
            for item in _slice_items(slices, "world_life")
            if (entry := _experience_entry(item, lane="world_life"))
        ],
        already_sent_count=len(photos_i_shared),
    )
    if shareable is not None:
        materials["moments_i_can_share"] = shareable
    diary_source = [entry for entries in experience_lanes for entry in entries]
    week_diary = _week_diary(diary_source, logical_time)
    if week_diary:
        materials["week_diary"] = week_diary

    facet_keys = {
        "private_self": ("stable_self", "biographical_context", "week_diary", "situation", "private_impressions", "recent_self_experiences"),
        "selective_memory": (
            "folded_dialogue",
            "recent_dialogue",
            "relevant_facts",
            "remembered_material",
            "photos_i_shared",
            "messages_waiting_to_send",
            "moments_i_can_share",
        ),
        "appraisal_affect": ("appraisals", "affect"),
        "emotional_continuity": (
            "appraisals",
            "affect",
            "change_phase",
            "interruption",
        ),
        "subjective_relationship": (
            "relationship",
            "protagonist_npc_relationships",
            "npc_observable_attitudes",
            "private_impressions",
            "folded_dialogue",
            "recent_dialogue",
            "interaction_acts",
            "photos_i_shared",
            "messages_waiting_to_send",
            "moments_i_can_share",
        ),
        "aspirations_conflicts": ("situation", "unresolved"),
        "autonomous_impulses": (
            "situation",
            "relationship",
            "protagonist_npc_relationships",
            "npc_observable_attitudes",
            "appraisals",
            "affect",
            "unresolved",
            "perception",
            "recent_self_experiences",
            "folded_dialogue",
            "recent_dialogue",
            "relevant_facts",
            "interaction_acts",
            "photos_i_shared",
            "messages_waiting_to_send",
            "moments_i_can_share",
        ),
        "expression_stance": (
            "stable_self",
            "situation",
            "relationship",
            "protagonist_npc_relationships",
            "npc_observable_attitudes",
            "appraisals",
            "affect",
            "private_impressions",
            "folded_dialogue",
            "recent_dialogue",
            "relevant_facts",
            "interaction_acts",
            "photos_i_shared",
            "messages_waiting_to_send",
            "moments_i_can_share",
        ),
    }
    facets: list[_InteriorFacet] = []
    for name in FACET_NAMES:
        keys = tuple(key for key in facet_keys[name] if key in materials)
        refs = tuple(dict.fromkeys(ref for key in keys for ref in _material_refs(materials[key])))
        view = _InteriorContextView.from_material(
            availability="available" if refs else "unavailable",
            content={"material_keys": list(keys)} if refs else {},
            source_refs=refs,
        )
        facets.append(_InteriorFacet(name=name, **view.model_dump(mode="python")))

    privacy_by_ref = {
        item["source_ref"]: item.get("privacy_class")
        for lane in slices
        for item in _slice_items(slices, lane)
        if isinstance(item.get("source_ref"), str)
    }
    inventory: list[_InteriorSourceInventoryItem] = []
    for scope, value in materials.items():
        candidates = value.get("items") if isinstance(value, dict) else value
        if not isinstance(candidates, list):
            continue
        for item in candidates:
            if not isinstance(item, dict) or not isinstance(item.get("source_ref"), str):
                continue
            raw_source = next(
                (
                    raw
                    for lane in slices
                    for raw in _slice_items(slices, lane)
                    if raw.get("source_ref") == item["source_ref"]
                ),
                item,
            )
            trusted = (
                source_envelopes.get(item["source_ref"])
                if source_envelopes is not None
                else None
            )
            (
                authority_bindings,
                direct_source_refs,
                entity_revision,
                predecessor_refs,
                conflict_refs,
                revision_refs,
            ) = _source_envelope(raw_source, trusted=trusted)
            raw_value = (
                trusted.get("value")
                if isinstance(trusted, Mapping)
                else raw_source.get("value")
            )
            provenance_value = raw_value if isinstance(raw_value, Mapping) else item
            inventory.append(_InteriorSourceInventoryItem(
                source_ref=item["source_ref"], scope=scope,
                content_hash=_digest(item),
                privacy_class=(
                    trusted.get("privacy_class")
                    if isinstance(trusted, Mapping)
                    and isinstance(trusted.get("privacy_class"), str)
                    else privacy_by_ref.get(item["source_ref"])
                    if isinstance(privacy_by_ref.get(item["source_ref"]), str)
                    else None
                ),
                authority_scope=_inventory_text(
                    provenance_value,
                    "authority",
                    "epistemic_scope",
                ),
                authority_bindings=authority_bindings,
                direct_source_refs=direct_source_refs,
                entity_revision=entity_revision,
                valid_from=_inventory_text(provenance_value, "valid_from"),
                valid_to=_inventory_text(provenance_value, "valid_to"),
                expires_at=_inventory_text(
                    provenance_value,
                    "expires_at",
                    "expiry",
                    "window_closes_at",
                ),
                predecessor_refs=predecessor_refs,
                conflict_refs=conflict_refs,
                revision_refs=revision_refs,
            ))
    inventory.sort(key=lambda item: (item.source_ref, item.scope))
    source_refs = tuple(dict.fromkeys(item.source_ref for item in inventory))

    capabilities = _slice_items(slices, "available_capabilities")
    capability_scope = (
        _InteriorBinding.available({
            "source_refs": [item["source_ref"] for item in capabilities if isinstance(item.get("source_ref"), str)],
            "content_hash": _digest([{"source_ref": item.get("source_ref"), "value": _semantic_value(item.get("value"))} for item in capabilities]),
        })
        if capabilities
        else _InteriorBinding.unavailable("capability_scope_unavailable")
    )
    situation = _view(materials, ("logical_time", "biographical_context", "situation"))
    continuity = _view(materials, tuple(key for key in materials if key not in {"logical_time", "biographical_context", "situation"}))
    world_id = context.get("world_id") if isinstance(context.get("world_id"), str) else None
    actor_ref = context.get("actor_ref") if isinstance(context.get("actor_ref"), str) else None
    cursor = _cursor(context)
    available = bool(source_refs)
    assert_compile_time_materials_are_source_bound(materials)
    return InnerLifeSnapshot.create(
        availability="available" if available else "unavailable",
        world_id=world_id, actor_ref=actor_ref, cursor=cursor, logical_time=logical_time,
        situation=situation, continuity=continuity, facet_views=tuple(facets),
        materials=materials, source_refs=source_refs, source_inventory=tuple(inventory),
        viewer_scope=_binding(context, "consumer_scope", "viewer_scope_unavailable"),
        privacy_scope=_binding(context, "viewer_privacy_ceiling", "viewer_privacy_scope_unavailable"),
        capability_scope=capability_scope,
        context_compiler=_binding(context, "context_compiler_version", "context_compiler_unavailable"),
        snapshot_compiler=_InteriorBinding.available(SNAPSHOT_COMPILER_VERSION),
        truncation=_binding(context, "truncation", "truncation_metadata_unavailable"),
    )


def visible_source_refs(context: Mapping[str, object]) -> frozenset[str]:
    """Return the source tokens visible in one redacted provider Context."""

    slices = context.get("slices")
    if not isinstance(slices, dict):
        return frozenset()
    return frozenset(
        source_ref
        for lane in slices
        for item in _slice_items(slices, lane)
        if isinstance((source_ref := item.get("source_ref")), str)
    )


def citeable_source_labels(snapshot: InnerLifeSnapshot) -> dict[str, str]:
    """Short identity labels for the pickable catalog, never a suggested cite.

    Derived after the snapshot exists, so this does not enter snapshot
    identity.  Labels only restate material already in the provider view
    (who said a line, which advisory kind) so she can pick an id instead of
    reconstructing an opaque string.
    """

    labels: dict[str, str] = {}
    materials = snapshot.materials
    dialogue = materials.get("recent_dialogue")
    if isinstance(dialogue, list):
        for entry in dialogue:
            if not isinstance(entry, dict):
                continue
            source_ref = entry.get("source_ref")
            text = entry.get("text")
            if not isinstance(source_ref, str) or not isinstance(text, str) or not text.strip():
                continue
            speaker = entry.get("speaker")
            prefix = (
                "他："
                if speaker == "counterpart"
                else "我："
                if speaker == "companion"
                else ""
            )
            labels[source_ref] = prefix + text.strip()[:32]
    advisories = materials.get("advisories")
    candidates = (
        advisories.get("items")
        if isinstance(advisories, dict)
        else advisories
        if isinstance(advisories, list)
        else ()
    )
    if isinstance(candidates, list):
        for entry in candidates:
            if not isinstance(entry, dict):
                continue
            source_ref = entry.get("source_ref")
            kind = entry.get("kind")
            if isinstance(source_ref, str) and isinstance(kind, str) and kind:
                labels.setdefault(source_ref, kind)
    return labels


def compile_citeable_source_catalog(
    snapshot: InnerLifeSnapshot,
    *,
    extra_refs: Iterable[str] = (),
) -> PinnedSourceCatalog:
    """Presentation catalog: short ids over this snapshot's pinned refs."""

    return PinnedSourceCatalog.from_refs(
        (*snapshot.source_refs, *extra_refs),
        labels=citeable_source_labels(snapshot),
    )


__all__ = [
    "SNAPSHOT_COMPILER_VERSION",
    "citeable_source_labels",
    "compile_citeable_source_catalog",
    "compile_inner_life_snapshot",
    "source_envelopes_from_capsule",
    "visible_source_refs",
]
