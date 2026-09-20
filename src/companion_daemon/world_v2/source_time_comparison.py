"""Source-bound display coordinates, with no new factual reading authority.

Only named fields from already typed source families are interpreted. Original
materials stay intact; UTC is a derived view, never a replacement timestamp.
World logical time and communication receipt time remain different clocks.
"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json


CONTRACT = "source-time-comparison.1"
_MARKER = "time_comparison_contract"
_VIEW = "time_comparison"


def _hash(value: object) -> str:
    wire = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(wire.encode("utf-8")).hexdigest()


def _coordinate(material: dict) -> dict | None:
    if material.get("kind") != "pinned_context_item":
        return None
    value = material.get("item", {}).get("value", {})
    if material.get("lane") == "current_situation":
        field, clock = "logical_time", "world_logical"
    elif material.get("lane") == "recent_dialogue":
        field = "occurred_at"
        clock = {
            "counterpart": "communication_received",
            "companion": "communication_receipt_recorded",
        }.get(value.get("speaker"))
        if clock is None:
            raise ValueError("time comparison requires a typed dialogue speaker")
    else:
        return None
    original = value.get(field)
    # SituationProjection explicitly permits an as-yet unset logical clock.
    # Communication occurred_at is required, so it does not share this case.
    if field == "logical_time" and original is None:
        return None
    if not isinstance(original, str):
        raise ValueError("time comparison requires an original timestamp string")
    try:
        parsed = datetime.fromisoformat(original)
    except ValueError as exc:
        raise ValueError("time comparison requires an ISO timestamp") from exc
    if parsed.utcoffset() is None:
        raise ValueError("time comparison cannot infer a missing UTC offset")
    return {
        "field": f"/item/value/{field}",
        "clock": clock,
        "utc": parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _view(material: dict) -> dict | None:
    coordinate = _coordinate(material)
    if coordinate is None:
        return None
    return {
        "contract": CONTRACT,
        "original_material_identity": _hash(material),
        "coordinates": [coordinate],
        # One shared scalar is interned by the existing lossless request view.
        "scope": (
            "Display coordinates derived only from original timestamps. Different clocks do not "
            "establish elapsed time; coordinates add no occurrence, delivery or ongoing-conversation evidence."
        ),
    }


def _rehash(payload: dict) -> None:
    materials = payload["source_materials"]
    for item in materials:
        item["material_identity"] = _hash(item["material"])
    for row in payload["source_references"]:
        row["material_identity"] = materials[row["material_index"]]["material_identity"]
    payload["table_hash"] = _hash(payload["source_references"])
    payload["materials_hash"] = _hash(materials)


def with_time_comparison(payload: dict) -> dict:
    """Annotate a newly compiled table; never migrate stored historical tables."""
    if _MARKER in payload or any(_VIEW in row["material"] for row in payload["source_materials"]):
        raise ValueError("time comparison requires an unannotated source table")
    result = deepcopy(payload)
    for row in result["source_materials"]:
        material = row["material"]
        view = _view(material)
        if view is not None:
            material[_VIEW] = view
    result[_MARKER] = CONTRACT
    _rehash(result)
    return result


def verify_time_comparison(payload: dict) -> None:
    """Validate display provenance before a source table reaches its consumer.

    Historical tables without the marker and view are left byte-for-byte alone.
    The complete table pin and its material/row hashes bind the new annotation;
    recomputation additionally prevents a display coordinate from contradicting
    its original evidence even when a caller recalculates the table hashes.
    """
    if _MARKER not in payload:
        if any(_VIEW in row["material"] for row in payload["source_materials"]):
            raise ValueError("unmarked source time comparison")
        return
    if payload[_MARKER] != CONTRACT:
        raise ValueError("unknown source time comparison contract")
    original = deepcopy(payload)
    del original[_MARKER]
    for row in original["source_materials"]:
        row["material"].pop(_VIEW, None)
    _rehash(original)
    if with_time_comparison(original) != payload:
        raise ValueError("source time comparison differs from its original material")
