"""Compact provider-facing appraisal material without dropping readings.

The canonical ``InnerLifeSnapshot`` materials keep the full resolver envelope
for replay and acceptance.  ``model_view`` alone replaces the verbose list with
a row-oriented table that preserves every ``source_ref``, meaning, confidence,
temporal bound, optional stimulus excerpt, and non-default hypothesis facets.
"""

from __future__ import annotations

from datetime import datetime
from typing import Mapping

_APPRAISAL_COLUMNS = ("ref", "conf", "since", "until", "readings", "excerpts")
_READING_ATTR_KEYS = ("attribution", "controllability", "severity")


def _instant(value: object) -> datetime | None:
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
        return f"{int(seconds // 60)}分钟前"
    if seconds < 86_400:
        return f"{int(seconds // 3_600)}小时前"
    return f"{int(seconds // 86_400)}天前"


def _remaining_phrase(expires_at: datetime, logical_time: datetime) -> str | None:
    seconds = (expires_at - logical_time).total_seconds()
    if seconds <= 0:
        return "已到期"
    if seconds < 3_600:
        return f"还剩{max(1, int(seconds // 60))}分钟"
    if seconds < 86_400:
        return f"还剩{max(1, int(seconds // 3_600))}小时"
    return f"还剩{max(1, int(seconds // 86_400))}天"


def _reading_entry(hypothesis: Mapping[str, object]) -> list[object]:
    meaning = hypothesis.get("meaning")
    if not isinstance(meaning, str) or not meaning:
        return []
    weight = hypothesis.get("weight_bp")
    attrs = {
        key: hypothesis[key]
        for key in _READING_ATTR_KEYS
        if isinstance(hypothesis.get(key), str)
    }
    if len(attrs) == len(_READING_ATTR_KEYS):
        shared = [meaning, attrs["attribution"], attrs["controllability"], attrs["severity"]]
        if isinstance(weight, int) and weight != 10_000:
            shared.append(weight)
        return shared
    item: list[object] = [meaning]
    if isinstance(weight, int) and weight != 10_000:
        item.append(weight)
    if attrs:
        item.append(attrs)
    return item


def _compact_row(
    entry: Mapping[str, object],
    *,
    logical_time: datetime | None,
) -> list[object] | None:
    source_ref = entry.get("source_ref")
    if not isinstance(source_ref, str) or not source_ref:
        return None
    hypotheses = entry.get("hypotheses")
    if not isinstance(hypotheses, list) or not hypotheses:
        return None
    readings: list[list[object]] = []
    for raw in hypotheses:
        if not isinstance(raw, dict):
            continue
        reading = _reading_entry(raw)
        if reading:
            readings.append(reading)
    if not readings:
        return None
    confidence = entry.get("confidence_bp")
    accepted = _instant(entry.get("accepted_at"))
    expires = _instant(entry.get("expires_at"))
    since = (
        _elapsed_phrase(accepted, logical_time)
        if accepted is not None and logical_time is not None
        else entry.get("accepted_at")
    )
    until = (
        _remaining_phrase(expires, logical_time)
        if expires is not None and logical_time is not None
        else entry.get("expires_at")
    )
    row: list[object] = [
        source_ref,
        confidence if isinstance(confidence, int) else None,
        since,
        until,
        readings,
    ]
    excerpts = entry.get("stimulus_excerpts")
    if isinstance(excerpts, list):
        kept = [item for item in excerpts if isinstance(item, str) and item.strip()]
        if kept:
            row.append(kept)
    return row


def compact_appraisals_for_model_view(
    appraisals: object,
    *,
    logical_time: datetime | None,
) -> object:
    """Return a compact appraisal table or the original value when not applicable."""

    if not isinstance(appraisals, list) or not appraisals:
        return appraisals
    subjects: set[str] = set()
    rows: list[list[object]] = []
    has_excerpts = False
    for entry in appraisals:
        if not isinstance(entry, dict):
            continue
        subject = entry.get("subject_ref")
        if isinstance(subject, str) and subject:
            subjects.add(subject)
        row = _compact_row(entry, logical_time=logical_time)
        if row is None:
            continue
        if len(row) > 5:
            has_excerpts = True
        rows.append(row)
    if not rows:
        return appraisals
    payload: dict[str, object] = {
        "columns": list(_APPRAISAL_COLUMNS[:5] + (("excerpts",) if has_excerpts else ())),
        "rows": rows,
    }
    if len(subjects) == 1:
        payload["subject"] = next(iter(subjects))
    return payload


def appraisal_meanings(material: object) -> list[str]:
    """Extract every meaning string from either the canonical or compact view."""

    meanings: list[str] = []
    if isinstance(material, dict) and isinstance(material.get("rows"), list):
        for row in material["rows"]:
            if not isinstance(row, list) or len(row) < 5:
                continue
            readings = row[4]
            if not isinstance(readings, list):
                continue
            for reading in readings:
                if isinstance(reading, list) and reading and isinstance(reading[0], str):
                    meanings.append(reading[0])
        return meanings
    if not isinstance(material, list):
        return meanings
    for entry in material:
        if not isinstance(entry, dict):
            continue
        hypotheses = entry.get("hypotheses")
        if not isinstance(hypotheses, list):
            continue
        for hypothesis in hypotheses:
            if isinstance(hypothesis, dict):
                meaning = hypothesis.get("meaning")
                if isinstance(meaning, str) and meaning:
                    meanings.append(meaning)
    return meanings


__all__ = ["appraisal_meanings", "compact_appraisals_for_model_view"]
