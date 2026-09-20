"""Versioned Fact presentation; complete original evidence stays in the pin.

An accepted Fact exposes its exact accepted value and typed qualifications.
Its enclosing Observation is retained for hash verification, not displayed as
additional Fact authority. Independently qualified dialogue is untouched.
"""

from copy import deepcopy
import hashlib
import json


CONTRACT = "visible-accepted-fact-display.1"
MARKER = "accepted_fact_display_contract"
VALUE_POINTER = "/accepted_fact/accepted_value"


def _fact_material(material):
    return (material.get("kind") == "pinned_context_item"
            and material.get("lane") == "relevant_facts"
            and material.get("authority") == "accepted_fact_with_observation_source")


def _hash(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def with_fact_value_display(payload: dict) -> dict:
    """Mark only newly compiled tables; do not rewrite saved preparations."""
    if MARKER in payload or any(MARKER in item["material"] for item in payload["source_materials"]):
        raise ValueError("Fact display requires an unmarked source table")
    result = deepcopy(payload)
    result[MARKER] = CONTRACT
    for item in result["source_materials"]:
        if _fact_material(item["material"]):
            item["material"][MARKER] = CONTRACT
        item["material_identity"] = _hash(item["material"])
    for row in result["source_references"]:
        row["material_identity"] = result["source_materials"][row["material_index"]]["material_identity"]
    result["table_hash"] = _hash(result["source_references"])
    result["materials_hash"] = _hash(result["source_materials"])
    return result


def verify_fact_value_display(payload: dict) -> None:
    version = payload.get(MARKER)
    if MARKER in payload and version != CONTRACT:
        raise ValueError("unknown accepted Fact display contract")
    for item in payload["source_materials"]:
        material = item["material"]
        expected = version is not None and _fact_material(material)
        if (MARKER in material) != expected or (expected and material[MARKER] != CONTRACT):
            raise ValueError("accepted Fact display marker differs from its source table")


def fact_display_pointer(material: dict) -> str | None:
    if MARKER not in material:
        return None
    if material[MARKER] != CONTRACT or not _fact_material(material):
        raise ValueError("invalid accepted Fact display source")
    return VALUE_POINTER


def display_fact_material(*, material: dict, rows: tuple[dict, ...]) -> dict:
    """Derive the display from qualified original rows, never from model text."""
    if fact_display_pointer(material) is None:
        return material
    from .visible_fact_value_readings import compile_fact_value_reading

    accepted = []
    for row in rows:
        try:
            reading = compile_fact_value_reading(row)
        except ValueError:
            continue
        if reading is not None:
            accepted.append({"accepted_value": reading["accepted_value"], **reading["fact_context"]})
    if accepted and any(value != accepted[0] for value in accepted[1:]):
        raise ValueError("accepted Fact aliases disagree on their display")
    return {
        "kind": "accepted_fact_value",
        MARKER: CONTRACT,
        "authority": material["authority"],
        "privacy_class": material.get("privacy_class"),
        "availability": "available" if accepted else "unavailable",
        "selection_field": "fact_value_selections",
        **({"accepted_fact": accepted[0]} if accepted else {}),
    }
