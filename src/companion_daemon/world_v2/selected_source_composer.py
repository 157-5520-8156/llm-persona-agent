"""Shared typed source compilation for character evidence consumers.

No ModelInput, candidate, provider, ledger or acceptance port belongs here.
Consumer-specific input and participant bindings remain with their adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from .biographical_claim_authority import biographical_coordinate_authorities
from .context_capsule import CapsuleItem, ContextCapsule
from .life_content import RecentExperienceContextItem
from .memory_retrieval import MemoryRetrievalItem
from .situation_compiler import SituationProjection
from .selected_source_context import compile_selected_fact_dialogue_context
from .visible_source_closure_protocol import compact_source_reference_table
from .world_life_context import (
    ActiveActivityContextItem,
    ActivityLifecycleStateContextItem,
    BiographicalWorldContextItem,
    CompletedActivityContextItem,
    PlannedActivityContextItem,
    WorldLifeContextItem,
)


VISIBLE_SOURCE_TABLE_CONTRACT = "visible-source-row-table.1"
PLANNED_SOURCE_TABLE_CONTRACT = "visible-source-row-table.2"
SETTLED_LIFE_SOURCE_TABLE_CONTRACT = "visible-source-row-table.3"
PREHISTORY_SOURCE_TABLE_CONTRACT = "visible-source-row-table.4"
SUBJECTIVE_SOURCE_TABLE_CONTRACT = "visible-source-row-table.6"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SelectedSourceTable:
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


def _world_entries(capsule: ContextCapsule, *, include_lifecycle_states: bool = False) -> tuple[list[dict], dict, set[str]]:
    entries, selections, unsupported = [], {}, set()
    biography_items = []
    world_types = {
        "active_activity": ActiveActivityContextItem,
        "completed_activity": CompletedActivityContextItem,
        "planned_activity": PlannedActivityContextItem,
        "biographical_context": BiographicalWorldContextItem,
    }
    if include_lifecycle_states:
        world_types["activity_lifecycle_state"] = ActivityLifecycleStateContextItem
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
                if kind is None and "occurrence_id" in value and "settled_at" in value:
                    model = WorldLifeContextItem
                if model is None:
                    unsupported.add(kind if isinstance(kind, str) else "unrecognized_world_life")
                    continue
                typed = model.model_validate_json(item.payload_json, strict=True)
                if isinstance(typed, WorldLifeContextItem):
                    if capsule.actor_ref not in typed.participant_refs or (
                        capsule.logical_time is None or typed.settled_at > capsule.logical_time
                    ):
                        raise ValueError("selected settlement differs from its original actor or time")
                    entries.append({**_entry(lane, item), "actor_ref": capsule.actor_ref})
                    continue
                if isinstance(typed, BiographicalWorldContextItem):
                    # The broad parent is attention context. Only the existing
                    # coordinate reader may expose its narrow claim materials.
                    biography_items.append(_selected_item(item))
                    continue
                if typed.owner_actor_ref != capsule.actor_ref:
                    raise ValueError("selected activity does not belong to the original actor")
            entries.append(_entry(lane, item))
    selected = capsule.recent_experiences
    if selected.availability == "available":
        for item in selected.items:
            value = json.loads(item.payload_json)
            if value.get("authority_contract_version") != "experience.2":
                continue
            experience = RecentExperienceContextItem.model_validate_json(item.payload_json)
            if (
                experience.status != "committed"
                or capsule.actor_ref not in experience.values.participant_refs
                or item.privacy_class == "withhold"
                or experience.values.privacy_class == "withhold"
            ):
                raise ValueError("selected Experience differs from its original actor or privacy")
            # Its two authors remain readable, but the composite's private
            # interpretation cannot authorize a new external event or action.
            entries.append({
                **_entry("recent_experiences", item),
                "authority": "non_authoritative_advisory_not_external_fact",
                "actor_ref": capsule.actor_ref,
            })
            selections["recent_experiences"] = {
                "availability": selected.availability,
                "unavailable_reason": selected.unavailable_reason,
                "slice_hash": selected.slice_hash,
                "item_refs": [item.item_ref for item in selected.items],
            }
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


def _prehistory_entries(capsule: ContextCapsule) -> list[dict]:
    """Read only retained excerpts already selected in the original Capsule."""
    selected = capsule.active_memory_candidates
    if selected.availability != "available":
        return []
    entries = []
    for item in selected.items:
        raw = json.loads(item.payload_json)
        if "source_excerpts" not in raw:
            continue
        memory = MemoryRetrievalItem.model_validate_json(item.payload_json, strict=True)
        historical = [source for source in memory.source_excerpts if source.prehistory is not None]
        if not historical:
            continue
        if (item.privacy_class == "withhold" or memory.privacy_ceiling == "withhold"
            or any(source.prehistory.actor_ref != capsule.actor_ref for source in historical)):
            raise ValueError("selected prehistory differs from its original actor or privacy")
        # Mixed-source cues have no whole-item historical qualification. They
        # remain readable context; each other source kind needs its own reader.
        entries.append({
            **_entry("active_memory_candidates", item),
            "authority": (
                "retained_character_prehistory_exact_excerpt_only"
                if len(historical) == len(memory.source_excerpts)
                else "non_authoritative_advisory_not_external_fact"
            ),
            "actor_ref": capsule.actor_ref,
            "does_not_authorize": [
                "runtime_occurrence_or_current_activity",
                "unretrieved_or_forgotten_archive_detail",
                "historical_person_as_current_counterpart_or_live_npc",
                "new_shared_history_with_the_current_user",
                "present_emotion_or_behavior_from_retention_rationale",
            ],
        })
    return entries


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


def _compose_source_materials(
    *, capsule: ContextCapsule, proof: dict, include_subjective_history: bool,
    supplemental_entries: tuple[dict, ...] = (),
    include_lifecycle_states: bool = False,
) -> dict:
    """Compose already validated selection; preserve historical row ordering."""
    entries, selections, unsupported = _world_entries(capsule, include_lifecycle_states=include_lifecycle_states)
    entries.extend(proof["entries"])
    history_entries = _prehistory_entries(capsule)
    entries.extend(history_entries)
    if history_entries:
        selected = capsule.active_memory_candidates
        selections["active_memory_candidates"] = {
            "availability": selected.availability,
            "slice_hash": selected.slice_hash,
            "item_refs": [entry["item"]["item_ref"] for entry in history_entries],
        }
    if include_subjective_history:
        from .visible_subjective_source import AUTHORITY, CONTRACT
        for lane in ("appraisals", "affect_episodes"):
            if lane == "appraisals" and capsule.pinned_appraisals is not None:
                from .pinned_appraisal_context import appraisal_context_envelopes
                inventory = capsule.pinned_appraisals
                envelopes = appraisal_context_envelopes(inventory)
                selections[lane] = {
                    "availability": "available", "inventory_contract": inventory.contract,
                    "inventory_hash": _hash(inventory.model_dump(mode="json")),
                    "item_refs": [item["item_ref"] for item in envelopes],
                    "omitted_count": inventory.omitted_count,
                }
                for item in envelopes:
                    entries.append({
                        "kind": "pinned_context_item", "lane": lane,
                        "privacy_class": "private", "availability": "available",
                        "item": item, "source_refs": sorted({item["item_ref"],
                            item["source_hash"], item["value_hash"],
                            *(b["ref"] for b in item["source_bindings"])}),
                        "authority": AUTHORITY, "actor_ref": capsule.actor_ref,
                        "scope": {"contract": CONTRACT, "owner_actor_ref": capsule.actor_ref,
                            "owner_basis": "pinned_companion_private_context",
                            "logical_at": capsule.logical_time.isoformat(),
                            "world_revision": capsule.world_revision},
                        "does_not_authorize": "Accepted subjective history only. The contents of a belief do not establish external facts, actions, causes, or another person's mind.",
                    })
                continue
            selected = getattr(capsule, lane)
            selections[lane] = {
                "availability": selected.availability, "slice_hash": selected.slice_hash,
                "item_refs": [item.item_ref for item in selected.items],
            }
            if selected.availability != "available" or capsule.logical_time is None:
                continue
            for item in selected.items:
                if item.privacy_class != "private":
                    continue
                entries.append({
                    **_entry(lane, item), "authority": AUTHORITY, "actor_ref": capsule.actor_ref,
                    "scope": {"contract": CONTRACT, "owner_actor_ref": capsule.actor_ref,
                              "owner_basis": "pinned_companion_private_context",
                              "logical_at": capsule.logical_time.isoformat(), "world_revision": capsule.world_revision},
                    "does_not_authorize": "Only her recorded subjective history. Beliefs about others, inferred motives, physical events, actions and causes are not established by this record. Do not infer unrecorded earlier intensity from the current affect value.",
                })
    entries.extend(supplemental_entries)
    rows, materials = _indexed_materials(entries, proof["subjects"])
    includes_planned = any(
        entry.get("item", {}).get("value", {}).get("context_kind") == "planned_activity"
        for entry in entries
    )
    includes_settled_life = any(
        "settled_at" in entry.get("item", {}).get("value", {})
        or entry.get("lane") == "recent_experiences"
        for entry in entries
    )
    payload = {
        "contract": (
            SUBJECTIVE_SOURCE_TABLE_CONTRACT if include_subjective_history else
            PREHISTORY_SOURCE_TABLE_CONTRACT if history_entries
            else SETTLED_LIFE_SOURCE_TABLE_CONTRACT if includes_settled_life
            else PLANNED_SOURCE_TABLE_CONTRACT if includes_planned
            else VISIBLE_SOURCE_TABLE_CONTRACT
        ),
        "additional_selection": selections,
        "coverage_scope": (
            "selected_context_with_subjective_history" if include_subjective_history else
            "selected_context_with_retained_prehistory_excerpts"
            if history_entries else
            "selected_situation_activity_settled_life_biography_fact_dialogue_and_private_readings"
            if includes_settled_life else "selected_situation_activity_biography_fact_dialogue_only"
        ),
        "subjects": proof["subjects"],
        "logical_time": proof["logical_time"],
        "unsupported_source_kinds": sorted(unsupported | {"identity_source"}),
        "source_references": rows,
        "source_materials": materials,
        "table_hash": _hash(rows),
        "materials_hash": _hash(materials),
    }
    return payload


def compile_selected_source_table(
    *, capsule: ContextCapsule, include_subjective_history: bool = False,
    include_lifecycle_states: bool = False,
) -> SelectedSourceTable:
    """Prepare candidate-independent evidence from one trusted original Capsule.

    This pin proves only the Capsule selection, not what a Life author saw.
    There is no current counterpart report or inferred counterpart identity.
    Life acceptance must additionally bind its original snapshot, provider view,
    candidate and semantic result; this table grants no write authority.
    """
    if type(include_subjective_history) is not bool:
        raise TypeError("subjective history selection flag must be boolean")
    if type(include_lifecycle_states) is not bool:
        raise TypeError("lifecycle state selection flag must be boolean")
    original, proof = compile_selected_fact_dialogue_context(capsule)
    payload = _compose_source_materials(
        capsule=original, proof=proof, include_subjective_history=include_subjective_history,
        include_lifecycle_states=include_lifecycle_states,
    )
    payload.update({
        "contract": "selected-source-row-table.1",
        "pin": proof["selected_source_projection"],
        "write_authority": False,
        "author_view_binding": "not_assessed",
    })
    return SelectedSourceTable(payload_json=_json(payload))
