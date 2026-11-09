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


def _flat_record(record: dict[str, object]) -> dict[str, object]:
    flat: dict[str, object] = {}

    def collect(node, path):
        if isinstance(node, dict) and node:
            for key, value in node.items():
                if not isinstance(key, str):
                    raise ValueError("flat record keys must be strings")
                collect(value, (*path, key))
        else:
            pointer = "/" + "/".join(key.replace("~", "~0").replace("/", "~1") for key in path)
            flat[pointer] = deepcopy(node)

    for key, value in record.items():
        collect(value, (key,))
    return flat


def pack_flat_literal_rows(items: list[dict[str, object]]) -> dict[str, object] | list[dict[str, object]]:
    """Share nested field names; arrays, text and selectable refs stay literal."""
    if len(items) < 2 or any(not isinstance(item, dict) for item in items):
        return items
    packet = pack_literal_rows([_flat_record(item) for item in items])
    if isinstance(packet, list):
        return items
    packet["contract"] = "literal-flat-row-table.1"
    if len(_wire(packet)) >= len(_wire(items)):
        return items
    if unpack_flat_literal_rows(packet) != items:
        raise ValueError("flat row presentation changed its original value")
    return packet


def unpack_flat_literal_rows(packet: dict[str, object]) -> list[dict[str, object]]:
    if packet.get("contract") != "literal-flat-row-table.1":
        raise ValueError("invalid flat row table")
    rows = unpack_literal_rows({**packet, "contract": "literal-row-table.1"})
    result = []
    for row in rows:
        record: dict[str, object] = {}
        for pointer, value in row.items():
            if not pointer.startswith("/"):
                raise ValueError("flat row path must be a JSON pointer")
            path = [key.replace("~1", "/").replace("~0", "~") for key in pointer[1:].split("/")]
            node = record
            for key in path[:-1]:
                node = node.setdefault(key, {})
                if not isinstance(node, dict):
                    raise ValueError("flat row paths collide")
            node[path[-1]] = value
        if _flat_record(record) != row:
            raise ValueError("flat row paths are not canonical")
        result.append(record)
    return result


def _ordered(value: Mapping[str, object], first: tuple[str, ...]) -> dict[str, object]:
    return {
        **{key: value[key] for key in first if key in value},
        **{key: child for key, child in value.items() if key not in first},
    }


def cache_ordered_background_material(material: dict[str, object]) -> dict[str, object]:
    """Keep ticking situation/time after stable background evidence, losslessly.

    The full situation remains visible. This is byte ordering, not a relevance
    ranking, a remembered response, or permission to reuse an old World view.
    """
    def tail(value: dict[str, object], keys: tuple[str, ...]) -> dict[str, object]:
        return {
            **{key: item for key, item in value.items() if key not in keys},
            **{key: value[key] for key in keys if key in value},
        }

    snapshot = material.get("inner_life_snapshot")
    if not isinstance(snapshot, dict):
        return material
    snapshot = dict(snapshot)
    materials = snapshot.get("materials")
    if isinstance(materials, dict) and materials.get("contract") != "shared-string-view.1":
        materials = dict(materials)
        album = materials.get("moments_i_can_share")
        if isinstance(album, dict):
            materials["moments_i_can_share"] = _ordered(
                album, ("items", "source_refs")
            )
        bio = materials.get("biographical_context")
        if isinstance(bio, list):
            materials["biographical_context"] = [
                tail(dict(item), ("logical_at",)) if isinstance(item, dict) else item
                for item in bio
            ]
        sit = materials.get("situation")
        if isinstance(sit, dict):
            materials["situation"] = tail(dict(sit), ("logical_time",))
        rel = materials.get("relationship")
        if isinstance(rel, dict):
            materials["relationship"] = tail(dict(rel), ("recent_authored_signals",))
        ordered = _ordered(materials, (
            "world_planning", "stable_self", "perception", "routine_background",
            "protagonist_npc_relationships", "relevant_facts", "unresolved",
            "private_impressions", "remembered_material", "memory_understandings",
            "relationship", "recent_self_experiences",
        ))
        snapshot["materials"] = tail(ordered, (
            "week_diary", "moments_i_can_share", "photos_i_shared",
            "historical_activity_intentions", "recently_ended_activities",
            "current_activities", "planned_activities", "pending_world_occurrences",
            "appraisals", "affect", "biographical_context", "automatic_prefetch",
            "selected_recall", "我最近留下的", "lived_moment", "day_sheet",
            "situation", "advisories", "since_he_last_spoke", "logical_time",
            "conversation", "recent_dialogue",
        ))
    ordered_snap = _ordered(snapshot, (
        "contract", "authority", "actor_ref", "world_id", "privacy_scope", "viewer_scope",
        "availability", "context_compiler", "snapshot_compiler", "materials",
    ))
    ordered_snap = tail(ordered_snap, (
        "faculties", "background_context_profile", "cursor", "logical_time",
        "snapshot_id", "snapshot_hash", "source_inventory", "capability_scope", "source_refs",
    ))
    return {**material, "inner_life_snapshot": ordered_snap}


def cache_ordered_inbound_material(material: dict[str, object]) -> dict[str, object]:
    """Put reusable evidence before cursor, inbound text and changing dialogue.

    These priorities describe expected byte stability, never relevance or what
    the character should attend to. A life/state change still changes its value
    and naturally invalidates the corresponding provider prefix.
    """
    def tail(value: dict[str, object], keys: tuple[str, ...]) -> dict[str, object]:
        return {
            **{key: item for key, item in value.items() if key not in keys},
            **{key: value[key] for key in keys if key in value},
        }

    result = dict(material)
    snapshot = material.get("inner_life_snapshot")
    if isinstance(snapshot, dict):
        snapshot = dict(snapshot)
        materials = snapshot.get("materials")
        if isinstance(materials, dict):
            # An existing shared-string packet is opaque here: reordering its
            # dictionary/value independently could destabilize reference names.
            if materials.get("contract") != "shared-string-view.1":
                materials = dict(materials)
                album = materials.get("moments_i_can_share")
                if isinstance(album, dict):
                    materials["moments_i_can_share"] = _ordered(
                        album, ("items", "source_refs")
                    )
                ordered_mats = _ordered(materials, (
                    "stable_self", "routine_background", "protagonist_npc_relationships",
                    "week_diary", "relationship", "world_planning", "unresolved",
                    "moments_i_can_share", "recent_self_experiences",
                    "recently_ended_activities", "current_activities",
                    "private_impressions", "perception",
                    "memory_understandings", "relevant_facts",
                ))
                bio = ordered_mats.get("biographical_context")
                if isinstance(bio, list):
                    ordered_mats["biographical_context"] = [
                        tail(dict(item), ("logical_at",)) if isinstance(item, dict) else item
                        for item in bio
                    ]
                sit = ordered_mats.get("situation")
                if isinstance(sit, dict):
                    ordered_mats["situation"] = tail(dict(sit), ("logical_time",))
                snapshot["materials"] = tail(ordered_mats, (
                    "recent_self_experiences", "recently_ended_activities",
                    "current_activities", "private_impressions", "perception",
                    "memory_understandings", "relevant_facts",
                    "appraisals", "affect", "photos_i_shared", "day_sheet",
                    "since_he_last_spoke", "我最近留下的", "biographical_context",
                    "situation", "logical_time", "capability_evidence",
                    "automatic_prefetch", "remembered_material", "advisories",
                    "lived_moment", "conversation", "recent_dialogue",
                ))
        ordered_snap = _ordered(snapshot, (
            "contract", "authority", "actor_ref", "world_id", "privacy_scope", "viewer_scope",
            "availability", "context_compiler", "snapshot_compiler", "materials",
        ))
        result["inner_life_snapshot"] = tail(
            ordered_snap,
            ("faculties", "capability_scope", "cursor", "logical_time", "snapshot_id", "source_refs"),
        )
    ordered_top = _ordered(result, (
        "lived_evidence_scope", "expression_capabilities",
        "inner_life_snapshot",
    ))
    return tail(ordered_top, (
        "activity_source_rule", "context_reading",
        "plan_management", "personal_action_evidence", "context_window",
        "appraisal_affect_hard_boundaries", "expression_hard_boundaries",
        "dialogue_observation_timing", "reference_dictionary", "request",
        "current_trigger_message", "role_result_correction",
    ))


def dialogue_tail_inbound_material(material: dict[str, object]) -> dict[str, object]:
    """Keep the live conversation and volatile turn envelope at the end of an author request.

    Apply after reference encoding: aliases, source permissions, text and array
    order are unchanged. This only orders object members before request hashing;
    persisted requests and receipts never pass through this presentation step.
    """
    def tail(value: dict[str, object], keys: tuple[str, ...]) -> dict[str, object]:
        return {
            **{key: item for key, item in value.items() if key not in keys},
            **{key: value[key] for key in keys if key in value},
        }

    result = dict(material)
    snapshot = result.get("inner_life_snapshot")
    if isinstance(snapshot, dict):
        snapshot = dict(snapshot)
        materials = snapshot.get("materials")
        if isinstance(materials, dict) and materials.get("contract") != "shared-string-view.1":
            materials = dict(materials)
            dialogue = materials.get("recent_dialogue")
            if isinstance(dialogue, dict) and isinstance(dialogue.get("stable_turns"), list):
                materials["recent_dialogue"] = tail(dialogue, ("stable_turns", "volatile_last_turn"))
            snapshot["materials"] = tail(
                materials, ("advisories", "lived_moment", "conversation", "recent_dialogue")
            )
        result["inner_life_snapshot"] = tail(
            snapshot,
            ("materials", "faculties", "capability_scope", "cursor", "logical_time", "snapshot_id", "source_refs"),
        )
    return tail(result, (
        "inner_life_snapshot", "activity_source_rule", "context_reading",
        "plan_management", "personal_action_evidence", "context_window",
        "appraisal_affect_hard_boundaries", "expression_hard_boundaries",
        "dialogue_observation_timing", "reference_dictionary",
        "request", "current_trigger_message", "role_result_correction",
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
