"""Add verified, actually presented recall to the original visible source table.

Callers must supply independently verified live or persisted Recall audits.
This module has no ledger, archive reader, model, or behavioral policy.
"""
from __future__ import annotations

import hashlib
import json

from .context_capsule import ResolvedSourceBinding, source_bindings_hash
from .recall_audit import RecallAuditTrace
from .recall_model_reading import interior_recall_item
from .model_facing_context import compact_model_facing_context
from .schema_core import canonicalize_json_value
from .selected_source_composer import _indexed_materials
from .visible_source_composer import VisibleSourceTable

RECALLED_SOURCE_TABLE_CONTRACT = "visible-source-row-table.5"


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def supplement_recalled_prehistory(
    *, table: VisibleSourceTable, audits: tuple[RecallAuditTrace, ...], author_request_json: str,
) -> tuple[VisibleSourceTable, tuple[RecallAuditTrace, ...]]:
    """Preserve original row indexes; add only the winning author's exact readings."""
    if len(audits) > 2 or len({audit.mode for audit in audits}) != len(audits):
        raise ValueError("visible recall support accepts at most one prefetch and one pull")
    base = table.as_dict()
    pin = base["pin"]
    actor = base["subjects"]["companion_actor_ref"]
    author = json.loads(author_request_json)
    user = json.loads(author["messages"][1]["content"])
    materials = user.get("inner_life_snapshot", {}).get("materials", {})
    entries, used = [], []
    seen = set()
    for raw in audits:
        audit = RecallAuditTrace.model_validate_json(raw.model_dump_json())
        documents = [hit.document for hit in audit.hits if hit.document.prehistory is not None]
        if not documents:
            continue
        cursor = audit.evaluated_cursor or audit.index_cursor
        if (audit.trigger_ref != pin["trigger_ref"]
            or audit.query.actor_ref != actor
            or audit.reuse_contract != "same_context"
            or (cursor.world_revision, cursor.deliberation_revision, cursor.ledger_sequence)
            != (pin["world_revision"], pin["deliberation_revision"], pin["ledger_sequence"])):
            raise ValueError("recalled history does not bind the original review cursor and actor")
        key = "selected_recall" if audit.mode == "character_pull" else "automatic_prefetch"
        material = materials.get(key, {})
        # Core installs automatic prefetch content directly; selected recall
        # retains its result wrapper. Neither path may infer unshown sources.
        shown = (material.get("content", {}) if audit.mode == "character_pull" else material).get("items", [])
        included = False
        for document in documents:
            if (document.actor_ref != actor or document.privacy_class == "withhold"
                or document.status != "active"):
                raise ValueError("recalled history actor, accessibility or privacy is invalid")
            reading = interior_recall_item(document)
            # Apply the same UTC canonicalization and proof-only compaction
            # used between Core materials and the actual provider message.
            view = json.loads(compact_model_facing_context(_json(canonicalize_json_value({
                "slices": {}, "inner_life_snapshot": {"materials": {"reading": reading}},
            }))))["inner_life_snapshot"]["materials"]["reading"]
            if view not in shown:
                # A late/unpresented result must not expand author authority.
                continue
            included = True
            identity = _hash(document.model_dump(mode="json"))
            if identity in seen:
                continue
            seen.add(identity)
            bindings = tuple(ResolvedSourceBinding.model_validate(binding.model_dump())
                             for binding in document.source_bindings)
            entries.append({
                "kind": "pinned_context_item", "lane": "recalled_prehistory",
                "authority": "retained_character_prehistory_exact_excerpt_only",
                "actor_ref": actor, "privacy_class": document.privacy_class,
                "availability": "available",
                "source_refs": sorted({document.source_item_ref, *document.source_refs}),
                "does_not_authorize": [
                    "runtime_occurrence_or_current_activity", "unretrieved_or_forgotten_archive_detail",
                    "historical_person_as_current_counterpart_or_live_npc",
                    "new_shared_history_with_the_current_user",
                ],
                "item": {
                    "item_ref": document.source_item_ref, "privacy_class": document.privacy_class,
                    "source_hash": source_bindings_hash(bindings), "value_hash": identity,
                    "source_bindings": [binding.model_dump(mode="json") for binding in bindings],
                    "value": document.model_dump(mode="json", exclude={"retrieval_text"}),
                },
            })
            # Search-only semantic expansion is not review evidence. Its
            # omission must have its own exact value hash.
            entries[-1]["item"]["value_hash"] = _hash(entries[-1]["item"]["value"])
        if included and audit not in used:
            used.append(audit)
    if not entries:
        return table, ()
    rows, added = _indexed_materials(entries, base["subjects"])
    offset, material_offset = len(base["source_references"]), len(base["source_materials"])
    for row in rows:
        row["source_ref_index"] += offset
        row["material_index"] += material_offset
    base["source_references"].extend(rows)
    base["source_materials"].extend(added)
    base["contract"] = RECALLED_SOURCE_TABLE_CONTRACT
    base["coverage_scope"] += "_with_presented_recalled_prehistory"
    base["recall_trace_hashes"] = [_hash(audit.model_dump(mode="json")) for audit in used]
    base["table_hash"] = _hash(base["source_references"])
    base["materials_hash"] = _hash(base["source_materials"])
    return VisibleSourceTable(payload_json=_json(base)), tuple(used)
