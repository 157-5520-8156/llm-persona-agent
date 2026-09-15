"""Compile selected Fact/Dialogue authority without a chat or candidate input.

Only the trusted original Capsule supplies evidence. This is source preparation,
not a semantic verdict, disclosure grant or proof of any author's actual view.
"""
from __future__ import annotations

import hashlib
import json

from .context_capsule import ContextCapsule, FactRecallItem, HistoricalFactRecallItem
from .life_context import _compile_selected_source_context
from .recent_dialogue import RecentDialogueItem
from .schemas import FactProjection

SELECTED_SOURCE_PROOF_CONTRACT = "selected-source-proof.1"
_SELECTED_LANES = ("relevant_facts", "recent_dialogue")


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


def compile_selected_fact_dialogue_context(
    capsule: ContextCapsule,
) -> tuple[ContextCapsule, dict[str, object]]:
    """Revalidate full compiler output and expose only its selected members.

    No counterpart is inferred from old dialogue. A consumer must independently
    bind its actual request, participant mapping and purpose-specific selection.
    """
    original, context = _compile_selected_source_context(capsule)
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
                **({
                    "scope": "recorded_companion_utterance_only",
                    "does_not_authorize": (
                        "This record proves only the companion expression recorded here, "
                        "with its original actor, time and delivery state. Its quoted content "
                        "does not independently prove an external action, location, event or "
                        "past experience. Prior acceptance or repeated telling is not new "
                        "evidence of that content. A claim about what she previously said may "
                        "use this record; a claim that the described event actually happened "
                        "needs the original event evidence. Do not infer that an unsupported "
                        "event never happened, or that an undelivered expression was heard."
                    ),
                } if authority == "companion_expression_record" else {}),
                "actor_ref": actor,
                "privacy_class": item["privacy_class"],
                "availability": selected.availability,
                "source_refs": sorted({
                    item["item_ref"], item["source_hash"], item["value_hash"],
                    *(binding["ref"] for binding in item["source_bindings"]),
                }),
                "item": item,
            })
    return original, {
        "subjects": {"companion_actor_ref": original.actor_ref},
        "entries": entries,
        "logical_time": context["logical_time"],
        "selected_source_projection": {
            "contract": SELECTED_SOURCE_PROOF_CONTRACT,
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
        },
    }
