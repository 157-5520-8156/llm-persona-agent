"""Living appraisals, open threads, and private impressions from the ledger.

The capsule may keep one fat item or drop a whole lane.  This installer pins
the actual living set into Context so the snapshot states what is true, not
what survived a character budget.  It does not decide what she should do
with any of those facts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Mapping

LivingHoldReason = Literal["user_channel_limited"]
_PRIVACY = frozenset({"public", "shareable", "personal", "private", "withhold"})


def _iso(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and value:
        return value
    return None


def _privacy(value: object) -> str:
    return value if isinstance(value, str) and value in _PRIVACY else "private"


def _evidence_refs(value: object) -> list[dict[str, object]]:
    dumped: list[dict[str, object]] = []
    for item in value or ():
        raw = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
        if not isinstance(raw, dict):
            continue
        evidence_type = raw.get("evidence_type")
        ref_id = raw.get("ref_id")
        if isinstance(evidence_type, str) and isinstance(ref_id, str) and ref_id:
            dumped.append({"evidence_type": evidence_type, "ref_id": ref_id})
    return dumped


def _hypotheses(value: object) -> list[dict[str, object]]:
    dumped: list[dict[str, object]] = []
    for item in value or ():
        raw = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
        if not isinstance(raw, dict):
            continue
        hypothesis_id = raw.get("hypothesis_id")
        meaning = raw.get("meaning")
        if not isinstance(hypothesis_id, str) or not isinstance(meaning, str):
            continue
        row: dict[str, object] = {
            "hypothesis_id": hypothesis_id,
            "meaning": meaning,
        }
        for key in ("attribution", "controllability", "severity", "weight_bp"):
            if key in raw:
                row[key] = raw[key]
        dumped.append(row)
    return dumped


def living_appraisal_slice_items(
    projection: object, *, logical_time: datetime | None
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for appraisal in getattr(projection, "appraisals", ()) or ():
        if getattr(appraisal, "status", None) != "active":
            continue
        expires_at = getattr(appraisal, "expires_at", None)
        if (
            logical_time is not None
            and isinstance(expires_at, datetime)
            and expires_at <= logical_time
        ):
            continue
        appraisal_id = getattr(appraisal, "appraisal_id", None)
        if not isinstance(appraisal_id, str) or not appraisal_id:
            continue
        origin = getattr(appraisal, "origin", None)
        origin_ref = getattr(origin, "accepted_event_ref", None) if origin is not None else None
        value: dict[str, object] = {
            "appraisal_id": appraisal_id,
            "status": "active",
            "subject_ref": getattr(appraisal, "subject_ref", None),
            "source_cluster_ref": getattr(appraisal, "source_cluster_ref", None),
            "hypotheses": _hypotheses(getattr(appraisal, "hypotheses", ())),
            "evidence_refs": _evidence_refs(getattr(appraisal, "evidence_refs", ())),
            "confidence_bp": getattr(appraisal, "confidence_bp", None),
        }
        accepted_at = _iso(getattr(appraisal, "accepted_at", None))
        if accepted_at is not None:
            value["accepted_at"] = accepted_at
        expires = _iso(expires_at)
        if expires is not None:
            value["expires_at"] = expires
        attention = [origin_ref] if isinstance(origin_ref, str) and origin_ref else []
        items.append(
            {
                "item_ref": appraisal_id,
                "source_ref": appraisal_id,
                "privacy_class": "private",
                "attention_source_refs": attention,
                "value": {key: item for key, item in value.items() if item is not None},
            }
        )
    items.sort(key=lambda item: str(item["source_ref"]))
    return items


def living_thread_slice_items(projection: object) -> list[dict[str, object]]:
    """Every still-open thread.  Subject may be a person or an event."""

    items: list[dict[str, object]] = []
    for thread in getattr(projection, "threads", ()) or ():
        values = getattr(thread, "values", None)
        if values is None or getattr(values, "status", None) != "open":
            continue
        thread_id = getattr(thread, "thread_id", None)
        if not isinstance(thread_id, str) or not thread_id:
            continue
        origin = getattr(thread, "origin", None)
        origin_ref = getattr(origin, "accepted_event_ref", None) if origin is not None else None
        due_window = getattr(values, "due_window", None)
        value: dict[str, object] = {
            "thread_id": thread_id,
            "kind": getattr(values, "kind", None),
            "subject_ref": getattr(values, "subject_ref", None),
            "importance_bp": getattr(values, "importance_bp", None),
            "status": "open",
        }
        if due_window is not None:
            dumped = (
                due_window.model_dump(mode="json")
                if hasattr(due_window, "model_dump")
                else due_window
            )
            if isinstance(dumped, dict):
                value["due_window"] = dumped
                closes = dumped.get("closes_at")
                if isinstance(closes, str) and closes:
                    value["window_closes_at"] = closes
        expected = getattr(values, "expected_response_ref", None)
        if isinstance(expected, str) and expected:
            value["expected_response_ref"] = expected
        attention = [origin_ref] if isinstance(origin_ref, str) and origin_ref else []
        items.append(
            {
                "item_ref": thread_id,
                "source_ref": thread_id,
                "privacy_class": _privacy(getattr(values, "privacy_class", None)),
                "attention_source_refs": attention,
                "value": {key: item for key, item in value.items() if item is not None},
            }
        )
    items.sort(key=lambda item: str(item["source_ref"]))
    return items


def living_affect_slice_items(projection: object) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for episode in getattr(projection, "affect_episodes", ()) or ():
        if getattr(episode, "status", None) != "active":
            continue
        episode_id = getattr(episode, "episode_id", None)
        if not isinstance(episode_id, str) or not episode_id:
            continue
        origin = getattr(episode, "origin", None)
        origin_ref = getattr(origin, "accepted_event_ref", None) if origin is not None else None
        dumped = episode.model_dump(mode="json") if hasattr(episode, "model_dump") else {}
        if not isinstance(dumped, dict):
            continue
        attention = [origin_ref] if isinstance(origin_ref, str) and origin_ref else []
        items.append(
            {
                "item_ref": episode_id,
                "source_ref": episode_id,
                "privacy_class": "private",
                "attention_source_refs": attention,
                "value": dumped,
            }
        )
    items.sort(key=lambda item: str(item["source_ref"]))
    return items


def living_impression_slice_items(
    projection: object,
    *,
    user_channel_limited_ids: frozenset[str] = frozenset(),
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for impression in getattr(projection, "private_impressions", ()) or ():
        if getattr(impression, "status", None) != "active":
            continue
        impression_id = getattr(impression, "impression_id", None)
        if not isinstance(impression_id, str) or not impression_id:
            continue
        origin = getattr(impression, "origin", None)
        origin_ref = getattr(origin, "accepted_event_ref", None) if origin is not None else None
        limited = impression_id in user_channel_limited_ids
        value: dict[str, object] = {
            "impression_id": impression_id,
            "subject_ref": getattr(impression, "subject_ref", None),
            "confidence_bp": getattr(impression, "confidence_bp", None),
            "status": "active",
            "expiry_condition": getattr(impression, "expiry_condition", None),
        }
        first_seen = _iso(getattr(impression, "first_seen", None))
        if first_seen is not None:
            value["first_seen"] = first_seen
        last_supported = _iso(getattr(impression, "last_supported", None))
        if last_supported is not None:
            value["last_supported"] = last_supported
        contradiction_refs = getattr(impression, "contradiction_refs", ()) or ()
        if contradiction_refs:
            value["contradiction_refs"] = list(contradiction_refs)
        if limited:
            value["hold_reason"] = "user_channel_limited"
        else:
            summary = getattr(impression, "reflection_summary", None)
            if isinstance(summary, str) and summary.strip():
                value["reflection_summary"] = summary
        attention = [origin_ref] if isinstance(origin_ref, str) and origin_ref else []
        items.append(
            {
                "item_ref": impression_id,
                "source_ref": impression_id,
                "privacy_class": "private",
                "attention_source_refs": attention,
                "value": {key: item for key, item in value.items() if item is not None},
            }
        )
    items.sort(key=lambda item: str(item["source_ref"]))
    return items


def _available_slice(items: list[dict[str, object]]) -> dict[str, object]:
    return {
        "availability": "available",
        "source_refs": [item["source_ref"] for item in items],
        "items": items,
    }


def install_living_state_context(
    context: Mapping[str, object],
    projection: object,
    *,
    user_channel_limited_impression_ids: frozenset[str] = frozenset(),
) -> dict[str, object]:
    """Pin living interior state the capsule may have omitted or truncated."""

    result = dict(context)
    slices = dict(context.get("slices") or {})
    logical_time = getattr(projection, "logical_time", None)
    slices["living_appraisals"] = _available_slice(
        living_appraisal_slice_items(projection, logical_time=logical_time)
    )
    slices["living_threads"] = _available_slice(living_thread_slice_items(projection))
    slices["living_impressions"] = _available_slice(
        living_impression_slice_items(
            projection,
            user_channel_limited_ids=user_channel_limited_impression_ids,
        )
    )
    slices["living_affect"] = _available_slice(living_affect_slice_items(projection))
    result["slices"] = slices
    return result


__all__ = [
    "LivingHoldReason",
    "install_living_state_context",
    "living_affect_slice_items",
    "living_appraisal_slice_items",
    "living_impression_slice_items",
    "living_thread_slice_items",
]
