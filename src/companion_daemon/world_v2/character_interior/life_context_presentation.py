"""Keep unresolved world circumstances distinct from sourced lived material.

Presentation never selects an outcome or turns an environmental result into
the character's action or feeling. Call diary rendering only after redaction.
"""
from __future__ import annotations

LIFE_CONTEXT_COMPILER_VERSION = "inner-life-snapshot-compiler.27"
SCOPED_DIARY_COMPILER_VERSIONS = frozenset({
    "inner-life-snapshot-compiler.25", "inner-life-snapshot-compiler.26", "inner-life-snapshot-compiler.27",
})
SOURCE_KIND_DIARY_COMPILER_VERSIONS = frozenset({
    "inner-life-snapshot-compiler.26", "inner-life-snapshot-compiler.27",
})
EXPLICIT_PREFETCH_AUTHORITY_COMPILER_VERSIONS = frozenset({"inner-life-snapshot-compiler.27"})
PENDING_WORLD_SCOPE = "active_world_occurrence_outcome_unsettled_not_personal_experience"
SETTLED_WORLD_SCOPE = "settled_world_occurrence_with_field_scoped_authority"
DIARY_TEXT_CHARACTERS = 160


def is_pending_world_material(entry: dict[str, object]) -> bool:
    return entry.get("epistemic_scope") == PENDING_WORLD_SCOPE


def _bounded_reading(value: object) -> object:
    if isinstance(value, list):
        return [_bounded_reading(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _bounded_reading(item) for key, item in value.items()}
    for key in ("text", "response_text"):
        text = result.get(key)
        if isinstance(text, str) and len(text) > DIARY_TEXT_CHARACTERS:
            result[key] = text[:DIARY_TEXT_CHARACTERS]
            result["truncated"] = True
    return result


def regroup_scoped_week_diary(
    value: object, *, include_source_kind: bool = False,
) -> list[dict[str, object]]:
    """Preserve structured world/subjective readings and their exact sources.

    Legacy lines retain their familiar presentation. Structured readings are
    not flattened into those lines, which feed the lived-moment summary.
    """
    if not isinstance(value, list):
        return []
    days: dict[str, dict[str, object]] = {}
    for row in value:
        if not isinstance(row, dict):
            continue
        day, ref = row.get("date"), row.get("source_ref")
        if not isinstance(day, str) or not day or not isinstance(ref, str) or not ref:
            continue
        readings = {
            key: row[key] for key in ("world_consequence", "character_response")
            if isinstance(row.get(key), dict)
        }
        line = row.get("line")
        if not readings and not (isinstance(line, str) and line.strip()):
            continue
        group = days.setdefault(day, {"date": day, "lines": [], "line_sources": []})
        if readings:
            item = {
                **_bounded_reading(readings), "source_ref": ref,
                **{key: row[key] for key in ("settled_at", "occurred_from", "occurred_to")
                   if isinstance(row.get(key), str)},
            }
            if include_source_kind:
                item.update({key: row[key] for key in ("context_kind", "epistemic_scope")
                             if isinstance(row.get(key), str)})
            entries = group.setdefault("readings", [])
            if item not in entries:
                entries.append(item)
        elif line.strip() not in group["lines"]:
            group["line_sources"].append({"line_index": len(group["lines"]), "source_ref": ref})
            group["lines"].append(line.strip())
    return list(days.values())
