"""Compose review material from the original selected Capsule, never claims.

This preparation module installs no reviewer, receipt or release guard. It
qualifies selected activity/biography and the existing Fact/Dialogue proof view;
it is not all-source or semantic qualification. Identity requires a separate
original identity pin and is explicitly unsupported here. Private material keeps
its scope; readable evidence is not permission to disclose it.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from .biographical_claim_authority import biographical_coordinate_authorities
from .context_capsule import CapsuleItem, ContextCapsule
from .deliberation import ModelInput
from .situation_compiler import SituationProjection
from .visible_review_context import compile_visible_selected_source_context
from .visible_source_closure_protocol import compact_source_reference_table
from .world_life_context import (
    ActiveActivityContextItem,
    BiographicalWorldContextItem,
    CompletedActivityContextItem,
)


VISIBLE_SOURCE_TABLE_CONTRACT = "visible-source-row-table.1"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class VisibleSourceTable:
    """Immutable serialized result; mutable views are independent copies.

    The future receipt must bind the complete payload, including original pin,
    ordered row table, material identities and qualifications, not a ref set.
    """

    payload_json: str

    @property
    def payload_hash(self) -> str:
        return hashlib.sha256(self.payload_json.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, object]:
        return json.loads(self.payload_json)

    def source_references(self) -> tuple[dict[str, object], ...]:
        """Return isolated host rows for the existing indexed review parser."""
        payload = self.as_dict()
        materials = payload["source_materials"]
        return tuple(
            {**row, "review_material": materials[row["material_index"]]["material"]}
            for row in payload["source_references"]
        )


def _selected_item(item: CapsuleItem) -> dict[str, object]:
    return {
        "item_ref": item.item_ref,
        "privacy_class": item.privacy_class,
        "source_hash": item.source_hash,
        "value_hash": item.value_hash,
        "source_bindings": [binding.model_dump(mode="json") for binding in item.source_bindings],
        "value": json.loads(item.payload_json),
    }


def _entry(lane: str, item: CapsuleItem) -> dict[str, object]:
    return {
        "kind": "pinned_context_item",
        "lane": lane,
        "privacy_class": item.privacy_class,
        "availability": "available",
        "source_refs": sorted(
            {
                item.item_ref,
                item.source_hash,
                item.value_hash,
                *(binding.ref for binding in item.source_bindings),
            }
        ),
        "item": _selected_item(item),
    }


def _world_entries(capsule: ContextCapsule) -> tuple[list[dict], dict, set[str]]:
    entries, selections, unsupported = [], {}, set()
    biography_items = []
    world_types = {
        "active_activity": ActiveActivityContextItem,
        "completed_activity": CompletedActivityContextItem,
        "biographical_context": BiographicalWorldContextItem,
    }
    for lane in ("current_situation", "world_life"):
        selected = getattr(capsule, lane)
        selections[lane] = {
            "availability": selected.availability,
            "unavailable_reason": selected.unavailable_reason,
            "slice_hash": selected.slice_hash,
            "item_refs": [item.item_ref for item in selected.items],
        }
        if selected.availability != "available":
            continue
        for item in selected.items:
            value = json.loads(item.payload_json)
            if item.privacy_class == "withhold" or value.get("privacy_class") == "withhold":
                raise ValueError("visible source table cannot expose a withheld selected member")
            if lane == "current_situation":
                SituationProjection.model_validate_json(item.payload_json, strict=True)
            else:
                kind = value.get("context_kind")
                model = world_types.get(kind) if isinstance(kind, str) else None
                if model is None:
                    unsupported.add(kind if isinstance(kind, str) else "unrecognized_world_life")
                    continue
                typed = model.model_validate_json(item.payload_json, strict=True)
                if isinstance(typed, BiographicalWorldContextItem):
                    # The broad parent is attention context. Only the existing
                    # coordinate reader may expose its narrow claim materials.
                    biography_items.append(_selected_item(item))
                    continue
                if typed.owner_actor_ref != capsule.actor_ref:
                    raise ValueError("selected activity does not belong to the original actor")
            entries.append(_entry(lane, item))
    coordinates = biographical_coordinate_authorities(
        {
            "logical_time": capsule.logical_time.isoformat() if capsule.logical_time else None,
            "slices": {"world_life": {"availability": "available", "items": biography_items}},
        }
    )
    entries.extend(
        {
            "kind": "biographical_coordinate",
            "authority": "exact_coordinate_only_not_unlisted_activity_or_occurrence_history",
            "source_refs": [coordinate.source_ref],
            "material": coordinate.evidence_material(),
        }
        for coordinate in coordinates
    )
    return entries, selections, unsupported


def _current_report(request: ModelInput, proof: dict) -> dict | None:
    trigger = request.trigger_message
    if trigger is None or proof["subjects"].get("counterpart_actor_ref") != trigger.actor:
        return None
    # The public proof producer verifies the exact original Observation,
    # text, actor, revision and hash before granting this participant binding.
    dialogue = next(
        entry
        for entry in proof["entries"]
        if (
            entry["lane"] == "recent_dialogue"
            and entry["item"]["value"]["dialogue_id"]
            == f"dialogue:observation:{trigger.observation_ref}"
        )
    )
    return {
        "kind": "current_counterpart_report",
        "packet_contract": "current-counterpart-report-packet.1",
        "authority": "report_only_not_external_truth",
        "privacy_class": dialogue["privacy_class"],
        "availability": "available",
        "epistemic_status": "counterpart_report_only_not_objective_truth_or_companion_experience",
        "permits_natural_visible_uptake_without_world_claim": True,
        "natural_uptake_does_not_need_attribution_phrase": True,
        "source_refs": [trigger.event_ref],
        "message": trigger.model_dump(mode="json"),
        "does_not_authorize": [
            "added_or_changed_subject_time_occurrence_or_status",
            "added_detail_or_motive",
            "objective_world_fact",
            "companion_experience",
            "durable_world_mutation",
        ],
    }


def _indexed_materials(entries: list[dict], subjects: dict) -> tuple[list[dict], list[dict]]:
    rows, materials = [], []
    material_indexes = {}
    represented = set()
    committed = {}
    for entry in entries:
        # Two projections of one committed event may have different authority
        # labels, but cannot disagree about that event's immutable bytes/pin.
        for binding in entry.get("item", {}).get("source_bindings", ()):
            if binding["source_kind"] != "committed_event":
                continue
            identity = (binding["source_world_revision"], binding["immutable_hash"])
            previous = committed.setdefault(binding["ref"], identity)
            if previous != identity:
                raise ValueError("selected sources disagree about an immutable event binding")
        # Keep the old public projection unchanged. Calling it for each exact
        # entry prevents its legacy global-ref dedup from hiding another view.
        projected = compact_source_reference_table({"subjects": subjects, "entries": [entry]})
        for row in projected:
            material = row["review_material"]
            identity = _hash(material)
            pair = (row["source_ref"], identity)
            if pair in represented:
                continue
            represented.add(pair)
            if identity not in material_indexes:
                material_indexes[identity] = len(materials)
                materials.append({"material_identity": identity, "material": material})
            rows.append(
                {
                    **{
                        key: value
                        for key, value in row.items()
                        if key not in {"review_material", "evidence_text"}
                    },
                    "source_ref_index": len(rows),
                    "material_index": material_indexes[identity],
                    "material_identity": identity,
                }
            )
    return rows, materials


def compile_visible_source_table(
    *, request: ModelInput, capsule: ContextCapsule
) -> VisibleSourceTable:
    """Compose original selected sources without a draft, claim list or lookup.

    The Fact/Dialogue producer validates the public compiler tag, complete
    Capsule and exact ModelInput first. Unknown source kinds do not silently
    acquire qualification; unavailable slices remain explicit in selection
    metadata. This returns preparation evidence, never an accepted receipt.
    """
    proof = compile_visible_selected_source_context(request=request, capsule=capsule)
    entries, selections, unsupported = _world_entries(capsule)
    entries.extend(proof["entries"])
    report = _current_report(request, proof)
    if report is not None:
        entries.append(report)
    rows, materials = _indexed_materials(entries, proof["subjects"])
    payload = {
        "contract": VISIBLE_SOURCE_TABLE_CONTRACT,
        "pin": proof["visible_review_projection"],
        "additional_selection": selections,
        "coverage_scope": "selected_situation_activity_biography_fact_dialogue_only",
        "subjects": proof["subjects"],
        "logical_time": proof["logical_time"],
        "unsupported_source_kinds": sorted(unsupported | {"identity_source"}),
        "source_references": rows,
        "source_materials": materials,
        "table_hash": _hash(rows),
        "materials_hash": _hash(materials),
    }
    return VisibleSourceTable(payload_json=_json(payload))


__all__ = ["VISIBLE_SOURCE_TABLE_CONTRACT", "VisibleSourceTable", "compile_visible_source_table"]
