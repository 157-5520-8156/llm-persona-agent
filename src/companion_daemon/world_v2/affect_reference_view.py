"""Reversible row presentation for typed Affect appraisal provenance.

This encoder changes neither the canonical snapshot nor the reference inventory.
It recognizes only complete AppraisalMeaningRef records and the existing model
view's four-field projection; unknown shapes stay verbatim. Each component owns
its table, so appending another episode never renumbers earlier references.
"""

from __future__ import annotations

import json

APPRAISAL_REFERENCE_TABLE_CONTRACT = "appraisal-refs-table.1"
_CORE_FIELDS = frozenset({
    "appraisal_id", "accepted_entity_revision", "hypothesis_id", "source_cluster_ref",
})
_FULL_FIELDS = _CORE_FIELDS | {"accepted_change_id", "accepted_transition_id"}


def _columns_valid(columns: object) -> bool:
    return (
        isinstance(columns, list)
        and all(isinstance(column, str) for column in columns)
        and len(columns) == len(set(columns))
        and set(columns) in (_CORE_FIELDS, _FULL_FIELDS)
    )


def _row_valid(columns: list[str], row: object) -> bool:
    if not isinstance(row, list) or len(row) != len(columns):
        return False
    return all(
        type(value) is int and value == 1
        if column == "accepted_entity_revision"
        else isinstance(value, str) and bool(value)
        for column, value in zip(columns, row, strict=True)
    )


def _wire_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def pack_appraisal_references(value: object) -> object:
    """Encode matching rows only when smaller; retain field and reference order."""

    if not isinstance(value, list) or not value or not isinstance(value[0], dict):
        return value
    columns = list(value[0])
    if not _columns_valid(columns):
        return value
    rows = []
    for reference in value:
        # Different member order is deliberately left alone as well: expansion
        # preserves the original JSON member order, not only dict equality.
        if not isinstance(reference, dict) or list(reference) != columns:
            return value
        row = list(reference.values())
        if not _row_valid(columns, row):
            return value
        rows.append(row)
    packet = {
        "contract": APPRAISAL_REFERENCE_TABLE_CONTRACT,
        "columns": columns,
        "rows": rows,
    }
    return packet if _wire_size(packet) < _wire_size(value) else value


def expand_appraisal_references(value: object) -> object:
    """Read old values unchanged and reject malformed recognized table packets."""

    if not isinstance(value, dict) or value.get("contract") != APPRAISAL_REFERENCE_TABLE_CONTRACT:
        return value
    if set(value) != {"contract", "columns", "rows"} or not _columns_valid(value["columns"]):
        raise ValueError("invalid Affect appraisal reference table")
    columns, rows = value["columns"], value["rows"]
    if not isinstance(rows, list) or not rows or any(not _row_valid(columns, row) for row in rows):
        raise ValueError("invalid Affect appraisal reference rows")
    return [dict(zip(columns, row, strict=True)) for row in rows]


def present_affect_entry(value: object, *, expand: bool = False) -> object:
    """Change only direct typed component references, never arbitrary nested data."""

    if not isinstance(value, dict) or not isinstance(value.get("components"), list):
        return value
    transform = expand_appraisal_references if expand else pack_appraisal_references
    components: list[object] = []
    changed = False
    for component in value["components"]:
        if not isinstance(component, dict) or "appraisal_refs" not in component:
            components.append(component)
            continue
        refs = transform(component["appraisal_refs"])
        if refs is component["appraisal_refs"]:
            components.append(component)
        else:
            components.append({**component, "appraisal_refs": refs})
            changed = True
    return {**value, "components": components} if changed else value
