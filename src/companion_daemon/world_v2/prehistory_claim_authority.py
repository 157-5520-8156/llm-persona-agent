"""Claim permissions for historical excerpts already in the pinned role input.

This grants past-autobiography source capability, never semantic entailment.
Whole-visible review must still check every assertion against the exact text.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .context_capsule import ResolvedSourceBinding
from .model_facing_context import compact_chat_model_facing_context
from .schema_core import canonicalize_json_value

if TYPE_CHECKING:
    from .deliberation import ModelInput


def _compact(value):
    # Initial and paired materialization use full and chat-compacted inputs.
    # Compare their same bounded semantic view; immutable proof stays in the
    # original requirement table and is validated independently below.
    return json.loads(compact_chat_model_facing_context(json.dumps(
        canonicalize_json_value(value), ensure_ascii=False,
    )))


def prehistory_claim_bindings(
    request: ModelInput, *, context: dict | None = None,
) -> dict[str, tuple[ResolvedSourceBinding, ...]]:
    """Require original selected proof and actual presentation; no archive reads."""
    if request.visible_source_requirement_json is None:
        return {}
    from .recall_runtime import verify_trusted_recall_trace
    from .visible_recall_sources import supplement_recalled_prehistory
    from .visible_source_runtime import requirement_table
    from .visible_source_closure_protocol import _review_item, _historical_memory, _historical_recall

    table = requirement_table(request.visible_source_requirement_json)
    base = table.as_dict()
    pin = base["pin"]
    for field, expected in (
        ("capsule_id", request.capsule_id), ("trigger_ref", request.trigger_ref),
        ("world_revision", request.evaluated_world_revision),
        ("deliberation_revision", request.evaluated_deliberation_revision),
        ("ledger_sequence", request.evaluated_ledger_sequence),
    ):
        if pin[field] != expected:
            raise ValueError("historical claim differs from original input pin")
    if (request.visible_review_participants is not None
        and request.visible_review_participants.actor_ref != base["subjects"]["companion_actor_ref"]):
        raise ValueError("historical claim differs from original actor")
    view = _compact(context if context is not None else json.loads(request.model_content_json))
    if view.get("actor_ref") != base["subjects"]["companion_actor_ref"]:
        raise ValueError("historical claim differs from presented actor")
    # Reuse the review's exact Core-reading comparison. This local envelope is
    # only a presentation adapter, not a provider request or review receipt.
    if request.visible_source_recall_traces:
        table, _ = supplement_recalled_prehistory(
            table=table,
            audits=tuple(verify_trusted_recall_trace(trace)
                         for trace in request.visible_source_recall_traces),
            author_request_json=json.dumps({"messages": [
                {"content": ""}, {"content": json.dumps(view)},
            ]}),
        )
    result = {}
    for wrapped in table.as_dict()["source_materials"]:
        material = wrapped["material"]
        if material.get("authority") != "retained_character_prehistory_exact_excerpt_only":
            continue
        lane = material.get("lane")
        if lane not in {"active_memory_candidates", "recalled_prehistory"}:
            continue
        item = material["item"]
        actor = base["subjects"]["companion_actor_ref"]
        historical = (_historical_memory(item.get("value")) if lane == "active_memory_candidates"
                      else _historical_recall(item.get("value")))
        if (historical is None or material.get("privacy_class") == "withhold"
            or material.get("availability") != "available"
            or (lane == "active_memory_candidates" and any(
                source.prehistory.actor_ref != actor for source in historical.source_excerpts))
            or (lane == "recalled_prehistory" and historical.actor_ref != actor)):
            raise ValueError("historical claim has invalid owner or reading")
        _, qualified = _review_item(item)
        if not qualified:
            raise ValueError("historical claim lost exact record and archive proof")
        if lane == "active_memory_candidates":
            selected = view.get("slices", {}).get(lane, {})
            expected = _compact({"slices": {lane: {
                "availability": "available", "items": [item],
            }}})["slices"][lane]["items"][0]
            if selected.get("availability") != "available" or not any(
                shown.get("source_ref") == expected["source_ref"]
                and shown.get("value") == expected.get("value")
                for shown in selected.get("items", [])
            ):
                continue
        bindings = tuple(ResolvedSourceBinding.model_validate(binding)
                         for binding in item["source_bindings"])
        # A cited item/event brings both proofs into the proposal. Hashes are
        # attention metadata, not independent autobiographical capabilities.
        refs = {item["item_ref"], *(binding.ref for binding in bindings
                if binding.authority_type == "CharacterPrehistoryRecordImported")}
        if lane == "recalled_prehistory":
            refs.add(item["value"]["source_item_ref"])
        for ref in refs:
            if ref in result and result[ref] != bindings:
                raise ValueError("historical claim has ambiguous proof")
            result[ref] = bindings
    return result
