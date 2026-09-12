"""Prepare original selected Fact/Dialogue proof, independently of claims.

This pure producer is not an exhaustive visible-source composer, a semantic
review, or a release guard. It has no ledger, retrieval, provider or Action port.
"""

from __future__ import annotations

import hashlib
import json

from .context_capsule import ContextCapsule, FactRecallItem, HistoricalFactRecallItem
from .deliberation import ModelInput
from .life_context import _compile_selected_source_context
from .recent_dialogue import RecentDialogueItem
from .schemas import FactProjection


VISIBLE_SELECTED_SOURCE_PROOF_CONTRACT = "visible-review-selected-source-proof.1"
_SELECTED_LANES = ("relevant_facts", "recent_dialogue")


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def _source_actor_and_authority(lane: str, item: dict) -> tuple[str | None, str]:
    raw = json.dumps(item["value"], ensure_ascii=False)
    if lane == "recent_dialogue":
        dialogue = RecentDialogueItem.model_validate_json(raw, strict=True)
        return dialogue.speaker_ref, (
            "counterpart_report_only"
            if dialogue.speaker == "counterpart"
            else "companion_expression_record"
        )
    if "source_excerpt" in item["value"]:
        model = (
            HistoricalFactRecallItem
            if item["value"].get("status") == "historical"
            else FactRecallItem
        )
        fact = model.model_validate_json(raw, strict=True)
        return fact.subject_ref, "accepted_fact_with_observation_source"
    projection = FactProjection.model_validate_json(raw, strict=True)
    return projection.values.subject_ref, "reference_metadata_only"


def _counterpart_actor(request: ModelInput, entries: list[dict]) -> str | None:
    """Use only a selected Observation dialogue to bind the current actor."""
    trigger = request.trigger_message
    if trigger is None:
        return None
    if trigger.event_ref != request.trigger_ref:
        raise ValueError("visible selected proof trigger does not bind the original request")
    for entry in entries:
        if entry["lane"] != "recent_dialogue":
            continue
        value = entry["item"]["value"]
        matching = [
            claim for claim in value["source_claims"]
            if claim["authority_event_ref"] == trigger.event_ref
        ]
        if not matching:
            continue
        claim = matching[0]
        if (
            value["dialogue_id"] != f"dialogue:observation:{trigger.observation_ref}"
            or value.get("speaker_ref") != trigger.actor
            or value["speaker"] != "counterpart"
            or value["delivery_state"] != "observed"
            or value["text"] != trigger.text
            or claim["authority_world_revision"] != trigger.source_world_revision
            or "sha256:" + claim["authority_payload_hash"] != trigger.event_payload_hash
        ):
            raise ValueError("visible selected proof trigger differs from its selected source")
        return trigger.actor
    return None


def compile_visible_selected_source_context(
    *, request: ModelInput, capsule: ContextCapsule,
) -> dict[str, object]:
    """Return selected Fact/Dialogue entries bound to the exact original input.

    No claim list is accepted: empty declarations cannot suppress selected
    proof and invented declarations cannot add members. Re-presented/aliased
    ModelInput bytes are not an original pin. Other source lanes and alias
    composition remain the responsibility of a future explicit composer.
    """
    original, context = _compile_selected_source_context(capsule)
    if not isinstance(request, ModelInput):
        raise ValueError("visible selected proof requires a typed original ModelInput")
    request = ModelInput.model_validate_json(request.model_dump_json())
    if (
        request.capsule_id != original.capsule_id
        or request.trigger_ref != original.trigger_ref
        or request.evaluated_world_revision != original.world_revision
        or request.evaluated_deliberation_revision != original.deliberation_revision
        or request.evaluated_ledger_sequence != original.ledger_sequence
        or request.model_content_json != original.model_content_json
    ):
        raise ValueError("visible selected proof ModelInput does not bind the original Capsule")

    entries = []
    selections = {}
    for lane in _SELECTED_LANES:
        selected = getattr(original, lane)
        selections[lane] = {
            "availability": selected.availability,
            "unavailable_reason": selected.unavailable_reason,
            "slice_hash": selected.slice_hash,
            "item_refs": [item.item_ref for item in selected.items],
        }
        if selected.availability != "available":
            continue
        for item in context["slices"][lane]["items"]:
            value = item["value"]
            nested = value.get("values", {})
            if (
                item["privacy_class"] == "withhold"
                or value.get("privacy_class") == "withhold"
                or isinstance(nested, dict) and nested.get("privacy_class") == "withhold"
            ):
                raise ValueError("visible selected proof cannot expose a withheld member")
            actor, authority = _source_actor_and_authority(lane, item)
            entries.append({
                "kind": "pinned_context_item",
                "lane": lane,
                "authority": authority,
                "actor_ref": actor,
                "privacy_class": item["privacy_class"],
                "availability": selected.availability,
                "source_refs": sorted({
                    item["item_ref"], item["source_hash"], item["value_hash"],
                    *(binding["ref"] for binding in item["source_bindings"]),
                }),
                "item": item,
            })
    subjects = {"companion_actor_ref": original.actor_ref}
    counterpart = _counterpart_actor(request, entries)
    binding = request.visible_review_participants
    if binding is not None:
        if (binding.world_id, binding.actor_ref) != (original.world_id, original.actor_ref):
            raise ValueError("visible review participants belong to another pinned world or actor")
        if counterpart is not None and counterpart != binding.counterpart_actor_ref:
            raise ValueError("visible review counterpart differs from the exact trigger actor")
        counterpart = binding.counterpart_actor_ref
    if counterpart is not None:
        subjects["counterpart_actor_ref"] = counterpart
    return {
        "contract": "source-closure-evidence.3",
        "subjects": subjects,
        "required_source_refs": [],
        "entries": entries,
        "logical_time": context["logical_time"],
        "visible_review_projection": {
            "contract": VISIBLE_SELECTED_SOURCE_PROOF_CONTRACT,
            "selected_lanes": list(_SELECTED_LANES),
            "source_selection": selections,
            "capsule_id": original.capsule_id,
            "compiler_result_hash": original.compiler_result_hash,
            "world_id": original.world_id,
            "snapshot_id": original.snapshot_id,
            "snapshot_hash": original.snapshot_hash,
            "actor_ref": original.actor_ref,
            "trigger_ref": original.trigger_ref,
            "world_revision": original.world_revision,
            "deliberation_revision": original.deliberation_revision,
            "ledger_sequence": original.ledger_sequence,
            "model_content_hash": hashlib.sha256(original.model_content_json.encode()).hexdigest(),
            "model_input_hash": _hash(request.model_dump(mode="json")),
        },
    }
