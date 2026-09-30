"""Lossless request ordering for provider prefix caching.

This is a presentation operation before the actual request is hashed, not a
Context compiler or an authority cache. Every value and array order is retained.
Old request/receipt bytes are never reconstructed through this module.
"""

from collections.abc import Mapping
from copy import deepcopy
import json


def _wire(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def pack_literal_rows(items: list[dict[str, object]]) -> dict[str, object] | list[dict[str, object]]:
    """Share field names, keeping every cell (especially selectable refs) literal."""
    if len(items) < 2 or any(not isinstance(item, dict) for item in items):
        return items
    columns = sorted({key for item in items for key in item})
    if not columns:
        return items
    absent = "__absent_cell__"
    raw = _wire(items)
    while absent in raw:
        absent = "_" + absent
    packet = {
        "contract": "literal-row-table.1",
        "columns": columns,
        "stable_rows": [[item.get(key, absent) for key in columns] for item in items],
        "absent_cell": absent,
    }
    if len(_wire(packet)) >= len(raw):
        return items
    if unpack_literal_rows(packet) != items:
        raise ValueError("literal row presentation changed its original value")
    return packet


def unpack_literal_rows(packet: dict[str, object]) -> list[dict[str, object]]:
    if set(packet) != {"contract", "columns", "stable_rows", "absent_cell"} or packet["contract"] != "literal-row-table.1":
        raise ValueError("invalid literal row table")
    columns, rows, absent = packet["columns"], packet["stable_rows"], packet["absent_cell"]
    if (not isinstance(columns, list) or not columns
        or any(not isinstance(key, str) for key in columns) or len(set(columns)) != len(columns)
        or not isinstance(rows, list) or not isinstance(absent, str) or not absent
        or any(not isinstance(row, list) or len(row) != len(columns) for row in rows)):
        raise ValueError("invalid literal row columns or cells")
    return [{key: deepcopy(cell) for key, cell in zip(columns, row, strict=True) if cell != absent}
            for row in rows]


def _ordered(value: Mapping[str, object], first: tuple[str, ...]) -> dict[str, object]:
    return {
        **{key: value[key] for key in first if key in value},
        **{key: child for key, child in value.items() if key not in first},
    }


def cache_ordered_inbound_material(material: dict[str, object]) -> dict[str, object]:
    """Put reusable evidence before cursor, inbound text and changing dialogue.

    These priorities describe expected byte stability, never relevance or what
    the character should attend to. A life/state change still changes its value
    and naturally invalidates the corresponding provider prefix.
    """
    result = dict(material)
    snapshot = material.get("inner_life_snapshot")
    if isinstance(snapshot, dict):
        snapshot = dict(snapshot)
        materials = snapshot.get("materials")
        if isinstance(materials, dict):
            # An existing shared-string packet is opaque here: reordering its
            # dictionary/value independently could destabilize reference names.
            if materials.get("contract") != "shared-string-view.1":
                snapshot["materials"] = _ordered(materials, (
                    "stable_self", "routine_background", "moments_i_can_share", "photos_i_shared",
                    "recent_self_experiences", "recently_ended_activities", "week_diary", "day_sheet",
                    "relationship", "appraisals", "private_impressions", "unresolved",
                ))
        result["inner_life_snapshot"] = _ordered(snapshot, (
            "contract", "authority", "actor_ref", "world_id", "privacy_scope", "viewer_scope",
            "availability", "context_compiler", "snapshot_compiler", "faculties", "materials",
        ))
    return _ordered(result, (
        "activity_source_rule", "lived_evidence_scope", "expression_capabilities",
        "appraisal_affect_hard_boundaries", "personal_action_evidence", "inner_life_snapshot",
        "expression_hard_boundaries", "reference_dictionary", "request", "current_trigger_message",
    ))


def compact_inbound_media_rows(material: dict[str, object]) -> dict[str, object]:
    """Lossless transport of the album, including all unavailable/expired entries."""
    snapshot = material.get("inner_life_snapshot")
    if not isinstance(snapshot, dict):
        return material
    materials = snapshot.get("materials")
    album = materials.get("moments_i_can_share") if isinstance(materials, dict) else None
    items = album.get("items") if isinstance(album, dict) else None
    if not isinstance(items, list):
        return material
    packed = pack_literal_rows(items)
    if isinstance(packed, list):
        return material
    return {**material, "inner_life_snapshot": {**snapshot, "materials": {
        **materials, "moments_i_can_share": {**album, "items": packed},
    }}}
