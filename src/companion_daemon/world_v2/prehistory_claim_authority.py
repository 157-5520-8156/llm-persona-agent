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


def _visible_prehistory_claim_bindings(
    request: ModelInput,
    *,
    context: dict | None,
    source_authority_context_json: str | None,
) -> dict[str, tuple[ResolvedSourceBinding, ...]]:
    """Bind only exact prehistory excerpts already present in ordinary Context."""

    from .recall_runtime import verify_trusted_recall_trace
    from .visible_source_closure_protocol import _historical_memory, _review_item

    try:
        authority_context = json.loads(source_authority_context_json or request.model_content_json)
        visible_context = _compact(
            context if context is not None else json.loads(request.model_content_json)
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    actor_ref = authority_context.get("actor_ref")
    if not isinstance(actor_ref, str) or visible_context.get("actor_ref") != actor_ref:
        return {}
    for field, expected in (
        ("trigger_ref", request.trigger_ref),
        ("world_revision", request.evaluated_world_revision),
        ("deliberation_revision", request.evaluated_deliberation_revision),
        ("ledger_sequence", request.evaluated_ledger_sequence),
    ):
        if authority_context.get(field) != expected or visible_context.get(field) != expected:
            return {}
    participant = request.visible_review_participants
    if participant is not None and participant.actor_ref != actor_ref:
        return {}

    def lane_items(value: dict) -> tuple[dict, ...]:
        slices = value.get("slices")
        lane = slices.get("active_memory_candidates") if isinstance(slices, dict) else None
        items = (
            [item for item in lane.get("items", ()) if isinstance(item, dict)]
            if isinstance(lane, dict) and lane.get("availability") == "available"
            else []
        )
        snapshot = value.get("inner_life_snapshot")
        materials = snapshot.get("materials") if isinstance(snapshot, dict) else None
        if isinstance(materials, dict) and materials.get("contract") == "shared-string-view.1":
            try:
                from .shared_string_view import unpack_shared_strings

                materials = unpack_shared_strings(materials)
            except (TypeError, ValueError):
                materials = None
        if isinstance(materials, dict):
            remembered = materials.get("remembered_material")
            if isinstance(remembered, list):
                items.extend(item for item in remembered if isinstance(item, dict))
            prefetch = materials.get("automatic_prefetch")
            prefetch_items = prefetch.get("items") if isinstance(prefetch, dict) else None
            if isinstance(prefetch_items, list):
                items.extend(item for item in prefetch_items if isinstance(item, dict))
        # The same item can appear in both the legacy slice and the canonical
        # InnerLifeSnapshot materials. Keep its first exact provider view.
        unique: dict[str, dict] = {}
        for item in items:
            ref = item.get("source_ref") or item.get("item_ref")
            if isinstance(ref, str):
                unique.setdefault(ref, item)
        return tuple(unique.values())

    visible_items = lane_items(visible_context)
    result: dict[str, tuple[ResolvedSourceBinding, ...]] = {}

    def add(ref: str, bindings: tuple[ResolvedSourceBinding, ...]) -> None:
        previous = result.get(ref)
        if previous is not None and previous != bindings:
            raise ValueError("historical claim has ambiguous proof")
        result[ref] = bindings

    def visible_for(ref: str, *, text: str, actor: str) -> dict | None:
        for item in visible_items:
            if ref not in {item.get("source_ref"), item.get("item_ref")}:
                continue
            value = item.get("value") if isinstance(item.get("value"), dict) else item
            if not isinstance(value, dict) or item.get("privacy_class") == "withhold":
                continue
            if value.get("text") == text:
                prehistory = value.get("prehistory")
                if isinstance(prehistory, dict) and prehistory.get("actor_ref") == actor:
                    return item
            excerpts = value.get("source_excerpts")
            if isinstance(excerpts, list) and any(
                isinstance(excerpt, dict)
                and excerpt.get("text") == text
                and isinstance(excerpt.get("prehistory"), dict)
                and excerpt["prehistory"].get("actor_ref") == actor
                for excerpt in excerpts
            ):
                return item
        return None

    # Active MemoryCandidates retain both immutable event proofs in the local
    # pinned Context even when compact provider material omits proof-only fields.
    full_lane = authority_context.get("slices", {}).get("active_memory_candidates", {})
    full_items = full_lane.get("items", ()) if isinstance(full_lane, dict) else ()
    for item in full_items if isinstance(full_items, (list, tuple)) else ():
        if not isinstance(item, dict) or item.get("privacy_class") == "withhold":
            continue
        value = item.get("value")
        memory = _historical_memory(value)
        if memory is None or memory.privacy_ceiling == "withhold":
            continue
        _, qualified = _review_item(item)
        if not qualified:
            continue
        try:
            bindings = tuple(
                ResolvedSourceBinding.model_validate(binding)
                for binding in item.get("source_bindings", ())
            )
        except (TypeError, ValueError):
            continue
        item_ref = item.get("item_ref")
        if not isinstance(item_ref, str):
            continue
        shown = visible_for(item_ref, text=memory.source_excerpts[0].text or "", actor=actor_ref)
        if shown is None:
            continue
        for source in memory.source_excerpts:
            historical = source.prehistory
            if historical is None or historical.actor_ref != actor_ref or source.text is None:
                continue
            expected = {
                (source.authority_event_ref, source.authority_world_revision,
                 source.authority_payload_hash),
                (historical.archive_event_ref, historical.archive_world_revision,
                 historical.archive_payload_hash),
            }
            source_bindings = tuple(
                binding for binding in bindings
                if (binding.ref, binding.source_world_revision, binding.immutable_hash) in expected
            )
            actual = {
                (binding.ref, binding.source_world_revision, binding.immutable_hash)
                for binding in source_bindings
            }
            if actual != expected:
                continue
            add(source.source_id, source_bindings)

    # A recalled scene arrives as a source-closed RecallDocument. Require that
    # exact trace and excerpt to have been presented to this same pinned turn.
    expected_cursor = (
        request.evaluated_world_revision,
        request.evaluated_deliberation_revision,
        request.evaluated_ledger_sequence,
    )
    for trusted in request.visible_source_recall_traces:
        try:
            trace = verify_trusted_recall_trace(trusted)
        except (TypeError, ValueError):
            continue
        cursor = trace.evaluated_cursor or trace.index_cursor
        if (
            trace.trigger_ref != request.trigger_ref
            or (cursor.world_revision, cursor.deliberation_revision, cursor.ledger_sequence)
            != expected_cursor
            or trace.query.actor_ref != actor_ref
        ):
            continue
        for hit in trace.hits:
            document = hit.document
            historical = document.prehistory
            if (
                historical is None
                or document.actor_ref != actor_ref
                or document.status != "active"
                or document.privacy_class == "withhold"
                or visible_for(
                    document.source_item_ref, text=document.text, actor=actor_ref,
                ) is None
            ):
                continue
            try:
                bindings = tuple(
                    ResolvedSourceBinding.model_validate(binding.model_dump(mode="json"))
                    for binding in document.source_bindings
                )
            except (TypeError, ValueError):
                continue
            record_bindings = tuple(
                binding for binding in bindings
                if binding.authority_type == "CharacterPrehistoryRecordImported"
            )
            archive_bindings = tuple(
                binding for binding in bindings
                if binding.authority_type == "CharacterPrehistoryArchiveAccepted"
            )
            if (
                len(record_bindings) != 1
                or len(archive_bindings) != 1
                or archive_bindings[0].ref != historical.archive_event_ref
                or archive_bindings[0].source_world_revision != historical.archive_world_revision
                or archive_bindings[0].immutable_hash != historical.archive_payload_hash
                or {binding.ref for binding in bindings} != set(document.source_refs)
            ):
                continue
            add(document.source_item_ref, bindings)
    return result


def prehistory_claim_bindings(
    request: ModelInput,
    *,
    context: dict | None = None,
    source_authority_context_json: str | None = None,
) -> dict[str, tuple[ResolvedSourceBinding, ...]]:
    """Require original selected proof and actual presentation; no archive reads."""
    if request.visible_source_requirement_json is None:
        return _visible_prehistory_claim_bindings(
            request,
            context=context,
            source_authority_context_json=source_authority_context_json,
        )
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
