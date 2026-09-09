"""A lossless semantic view of already privacy-projected source material.

This compiler only removes repeated reference keys and explicitly identified
opaque hashes. It never sees candidate prose, selects a source, or changes
support eligibility. The original source table remains the audit authority.
"""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
import json

from pydantic import BaseModel

from .context_capsule import ResolvedSourceBinding
from .recent_dialogue import RecentDialogueItem
from .situation_compiler import SituationProjection
from .world_life_context import (
    ActiveActivityContextItem,
    CompletedActivityContextItem,
    PlannedActivityContextItem,
)


_ACTIVITY_TYPES = {
    "active_activity": ActiveActivityContextItem,
    "completed_activity": CompletedActivityContextItem,
    "planned_activity": PlannedActivityContextItem,
}
_ACTIVITY_SCOPE_BOUNDARY = (
    "An accepted plan proves only its accepted intention and scheduled window. "
    "It does not prove embedded history, preexisting object condition, arrival, "
    "execution, or successful fulfillment. Active/completed lifecycle evidence "
    "proves only its recorded status and time, not intention fulfillment; outcomes "
    "need separate evidence. Each reference retains its own eligibility and actor "
    "binding in source_reference_tables; sharing this card does not share authority."
)


def _opaque_hash(value: object) -> bool:
    if not isinstance(value, str):
        return False
    digest = value.removeprefix("sha256:")
    return len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)


def _drop_hash(value: dict[str, object], field: str) -> None:
    # A prose value or explicit null in an unfamiliar shape is not an opaque
    # digest. In particular, never delete arbitrary nested keys named "hash".
    if _opaque_hash(value.get(field)):
        del value[field]


def _valid_value(value: object, model: type[BaseModel]) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        model.model_validate_json(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return False
    return True


def _compact_item(material: dict[str, object]) -> dict[str, object] | None:
    if material.get("kind") != "pinned_context_item":
        return None
    item = material.get("item")
    if not isinstance(item, dict):
        return None
    for field in ("source_hash", "value_hash", "unverified_value_hash"):
        _drop_hash(item, field)
    bindings = item.get("source_bindings")
    if isinstance(bindings, (list, tuple)):
        for binding in bindings:
            if _valid_value(binding, ResolvedSourceBinding):
                _drop_hash(binding, "immutable_hash")

    value = item.get("value")
    lane = material.get("lane")
    if lane == "recent_dialogue" and _valid_value(value, RecentDialogueItem):
        _drop_hash(value, "sidecar_hash")
        for claim in value["source_claims"]:
            _drop_hash(claim, "authority_payload_hash")
    elif lane == "current_situation" and _valid_value(value, SituationProjection):
        for field in (
            "authority_snapshot_hash", "situation_policy_input_hash", "internal_semantic_hash",
        ):
            _drop_hash(value, field)
    elif lane == "world_life" and isinstance(value, dict):
        context_kind = value.get("context_kind")
        activity_type = _ACTIVITY_TYPES.get(context_kind) if isinstance(context_kind, str) else None
        if activity_type is not None and _valid_value(value, activity_type):
            intention = value["accepted_intention"]
            # Preserve omission instead of filling the schema's default.
            scope = {
                field: deepcopy(intention[field])
                for field in ("epistemic_scope",) if field in intention
            }
            if "item_ref" in item:
                scope["item_ref"] = deepcopy(item["item_ref"])
            _drop_hash(intention, "content_payload_hash")
            _drop_hash(value["proposal_source"], "authority_payload_hash")
            for binding in value["source_bindings"]:
                _drop_hash(binding, "authority_payload_hash")
            return scope
    return None


def compile_visible_evidence_cards(
    *,
    references: Sequence[dict[str, object]],
    materials: Sequence[dict[str, object]],
) -> dict[str, object]:
    """Render preprojected refs/materials without altering their source indexes.

    Each table groups references with exactly the same field set, preserving
    absence versus explicit null. Every cell is the original value. Material
    order and full prose remain unchanged; unknown typed bodies stay verbatim.
    This is not a privacy projector: callers must first apply _packet_materials
    to the canonical, privacy-projected source table.
    """

    tables: list[dict[str, object]] = []
    table_by_columns: dict[tuple[str, ...], dict[str, object]] = {}
    for original in references:
        reference = deepcopy(original)
        _drop_hash(reference, "material_identity")
        columns = tuple(sorted(reference))
        table = table_by_columns.get(columns)
        if table is None:
            table = {"columns": list(columns), "rows": []}
            table_by_columns[columns] = table
            tables.append(table)
        table["rows"].append([reference[column] for column in columns])

    cards = deepcopy(list(materials))
    for material_index, card in enumerate(cards):
        intention_scope = _compact_item(card)
        if card.get("kind") == "current_counterpart_report":
            message = card.get("message")
            if isinstance(message, dict):
                _drop_hash(message, "event_payload_hash")
        reference_scopes = [
            {
                "source_ref_index": deepcopy(reference["source_ref_index"]),
                "activity_support": deepcopy(reference["activity_support"]),
            }
            for reference in references
            if type(reference.get("material_index")) is int
            and reference["material_index"] == material_index
            and "source_ref_index" in reference
            and "activity_support" in reference
        ]
        if (
            (reference_scopes or intention_scope is not None)
            and "evidence_card_scope_notes" not in card
        ):
            card["evidence_card_scope_notes"] = {
                "reference_scopes": reference_scopes,
                "accepted_intention_scopes": (
                    [intention_scope] if intention_scope is not None else []
                ),
                "boundary": _ACTIVITY_SCOPE_BOUNDARY,
            }

    return {
        "source_material_contract": "visible-source-evidence-cards.1",
        "source_reference_tables": tables,
        "source_materials": cards,
    }
