"""Add verified, actually presented recall to the original visible source table.

Callers must supply independently verified live or persisted Recall audits.
This module has no ledger, archive reader, model, or behavioral policy.
"""
from __future__ import annotations

import hashlib
import json

from .context_capsule import ResolvedSourceBinding, source_bindings_hash
from .recall_audit import RecallAuditTrace
from .recall_model_reading import interior_recall_item, supports_fact_reading, supports_life_reading
from .model_facing_context import compact_model_facing_context
from .schema_core import canonicalize_json_value
from .selected_source_composer import _indexed_materials
from .visible_source_composer import VisibleSourceTable

RECALLED_SOURCE_TABLE_CONTRACT = "visible-source-row-table.5"


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _presented_user(author_request_json, audits):
    author = json.loads(author_request_json)
    user = json.loads(author["messages"][1]["content"])
    # New traces compare the authenticated expanded identifiers, just as the
    # visible-author verifier does. Retain historical receipt compilation.
    if any(supports_life_reading(audit.index_version) for audit in audits):
        from .reference_wire import expand_reference_view
        user = expand_reference_view(
            user, (author.get("identity_extras") or {}).get("reference_bindings"),
        )
        from .present_prompt import expand_present_world_context
        user = expand_present_world_context(user)
    return user


def supplement_recalled_prehistory(
    *, table: VisibleSourceTable, audits: tuple[RecallAuditTrace, ...], author_request_json: str,
) -> tuple[VisibleSourceTable, tuple[RecallAuditTrace, ...]]:
    """Preserve original row indexes; add only the winning author's exact readings."""
    if len(audits) > 2 or len({audit.mode for audit in audits}) != len(audits):
        raise ValueError("visible recall support accepts at most one prefetch and one pull")
    base = table.as_dict()
    pin = base["pin"]
    actor = base["subjects"]["companion_actor_ref"]
    user = _presented_user(author_request_json, audits)
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
            reading = interior_recall_item(document, index_version=audit.index_version)
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


def _presented_recalled_facts(*, table, audits, materials):
    """Select exact, source-bound Fact values; old excerpts remain advisory."""
    if len(audits) > 2 or len({audit.mode for audit in audits}) != len(audits):
        raise ValueError("visible recall support accepts at most one prefetch and one pull")
    base = table.as_dict()
    pin, subjects = base["pin"], base["subjects"]
    selected, used, seen = [], [], set()
    for raw in audits:
        audit = RecallAuditTrace.model_validate_json(raw.model_dump_json())
        if not supports_fact_reading(audit.index_version):
            continue
        cursor = audit.evaluated_cursor or audit.index_cursor
        if (audit.trigger_ref != pin["trigger_ref"]
            or audit.query.actor_ref != subjects["companion_actor_ref"]
            or audit.reuse_contract != "same_context"
            or (cursor.world_revision, cursor.deliberation_revision, cursor.ledger_sequence)
            != (pin["world_revision"], pin["deliberation_revision"], pin["ledger_sequence"])):
            raise ValueError("recalled Fact does not bind the original review cursor and actor")
        material = materials.get(
            "selected_recall" if audit.mode == "character_pull" else "automatic_prefetch", {}
        )
        shown = (material.get("content", {}) if audit.mode == "character_pull" else material).get("items", [])
        included = False
        for hit in audit.hits:
            document, fact = hit.document, hit.document.accepted_fact
            if fact is None:
                continue
            if (document.actor_ref != subjects["companion_actor_ref"]
                or document.privacy_class == "withhold"
                or document.status not in {"active", "superseded"}):
                raise ValueError("recalled Fact owner, privacy or status is invalid")
            # This consumer supplies counterpart-history permissions. Other
            # subjects require their own scope, not a relabelled user Fact.
            if fact.subject_ref != subjects.get("counterpart_actor_ref"):
                continue
            reading = interior_recall_item(document, index_version=audit.index_version)
            view = json.loads(compact_model_facing_context(_json(canonicalize_json_value({
                "slices": {}, "inner_life_snapshot": {"materials": {"reading": reading}},
            }))))["inner_life_snapshot"]["materials"]["reading"]
            if view not in shown:
                continue
            included = True
            identity = _hash(document.model_dump(mode="json"))
            if identity in seen:
                continue
            seen.add(identity)
            bindings = tuple(ResolvedSourceBinding.model_validate(binding.model_dump())
                             for binding in document.source_bindings)
            value = fact.model_dump(mode="json")
            entry = {
                "kind": "pinned_context_item", "lane": "relevant_facts",
                "authority": "accepted_fact_with_observation_source",
                "actor_ref": fact.subject_ref, "privacy_class": fact.privacy_class,
                "availability": "available",
                # Observation is proof of the accepted value, not a second
                # selectable source authorizing all clauses in its text.
                "source_refs": sorted({fact.fact_id, fact.accepted_fact_event_ref}),
                "item": {
                    "item_ref": fact.fact_id, "privacy_class": fact.privacy_class,
                    "source_hash": source_bindings_hash(bindings), "value_hash": _hash(value),
                    "source_bindings": [binding.model_dump(mode="json") for binding in bindings],
                    "value": value,
                },
            }
            from .visible_fact_value_display import CONTRACT, MARKER
            if MARKER in base:
                if base[MARKER] != CONTRACT:
                    raise ValueError("unknown accepted Fact display contract")
                entry[MARKER] = CONTRACT
            selected.append((document, entry))
        if included:
            used.append(audit)
    # Existing and appended views may share an event, never different bytes.
    known = {}
    entries = [wrapped["material"] for wrapped in base["source_materials"]]
    for entry in [*entries, *(entry for _, entry in selected)]:
        for binding in entry.get("item", {}).get("source_bindings", ()):
            key = (binding["source_kind"], binding["ref"])
            identity = (binding["source_world_revision"], binding["immutable_hash"])
            if known.setdefault(key, identity) != identity:
                raise ValueError("recalled Fact conflicts with an original source binding")
    return selected, tuple(used)


def bind_presented_recalled_facts(request):
    """Give the author the same typed sources its eventual reviewer can read."""
    if not request.visible_source_recall_traces or request.visible_source_requirement_json is None:
        return request
    from .recall_runtime import verify_trusted_recall_trace
    from .visible_source_runtime import requirement_table

    audits = tuple(verify_trusted_recall_trace(trace) for trace in request.visible_source_recall_traces)
    if not any(supports_fact_reading(audit.index_version)
               and any(hit.document.accepted_fact is not None or (
                   supports_life_reading(audit.index_version) and hit.document.settled_life_json is not None
               ) for hit in audit.hits)
               for audit in audits):
        return request
    table = requirement_table(request.visible_source_requirement_json)
    base = table.as_dict()
    for field, expected in (
        ("capsule_id", request.capsule_id), ("trigger_ref", request.trigger_ref),
        ("world_revision", request.evaluated_world_revision),
        ("deliberation_revision", request.evaluated_deliberation_revision),
        ("ledger_sequence", request.evaluated_ledger_sequence),
    ):
        if base["pin"][field] != expected:
            raise ValueError("recalled Fact differs from original input pin")
    content = json.loads(request.model_content_json)
    if content.get("actor_ref") != base["subjects"]["companion_actor_ref"]:
        raise ValueError("recalled Fact differs from presented actor")
    view = json.loads(compact_model_facing_context(_json(canonicalize_json_value(content))))
    selected, _ = _presented_recalled_facts(
        table=table,
        audits=audits,
        materials=view.get("inner_life_snapshot", {}).get("materials", {}),
    )
    from .recalled_life_source import presented_recalled_life
    life, _ = presented_recalled_life(
        table=table, audits=audits,
        materials=view.get("inner_life_snapshot", {}).get("materials", {}),
    )
    if not selected and not life:
        return request
    lane = content.setdefault("slices", {}).setdefault("relevant_facts", {})
    items = list(lane.get("items", []))
    for document, entry in selected:
        # A historical image cites its unique accepted event, not the search
        # index's composite id. Both this event and the current Fact id are
        # actual reviewer rows; distinct versions cannot overwrite each other.
        fact = document.accepted_fact
        source_ref = fact.accepted_fact_event_ref if fact.status == "historical" else fact.fact_id
        item = {**entry["item"], "item_ref": source_ref,
                "recall_injected": True}
        existing = [shown for shown in items if shown.get("item_ref") == item["item_ref"]]
        if existing:
            # The original capsule may carry a slim display beside the full
            # proof. Its immutable bindings were checked above; do not replace
            # an already selected source merely to change that representation.
            continue
        items.append(item)
    lane.update(availability="available", items=items)
    if life:
        life_lane = content.setdefault("slices", {}).setdefault("world_life", {})
        life_items = list(life_lane.get("items", []))
        for entry in life:
            if any(item.get("item_ref") == entry["item"]["item_ref"] for item in life_items):
                continue
            life_items.append({**entry["item"], "recall_injected": True})
        life_lane.update(availability="available", items=life_items)
    return request.model_copy(update={"model_content_json": _json(canonicalize_json_value(content))})


def supplement_recalled_sources(
    *, table: VisibleSourceTable, audits: tuple[RecallAuditTrace, ...], author_request_json: str,
) -> tuple[VisibleSourceTable, tuple[RecallAuditTrace, ...]]:
    """Extend new Fact-capable traces; historical receipts keep their old table."""
    original = table
    table, historical = supplement_recalled_prehistory(
        table=table, audits=audits, author_request_json=author_request_json,
    )
    user = _presented_user(author_request_json, audits)
    # Compare the canonical reading through the saved presentation contract.
    # The native prefetch view elides constants such as occurred_to=None;
    # their exact inverse is not a missing/unshown source.
    from .present_prompt import expand_present_world_context
    user = expand_present_world_context(user)
    selected, facts = _presented_recalled_facts(
        table=original, audits=audits,
        materials=user.get("inner_life_snapshot", {}).get("materials", {}),
    )
    from .recalled_life_source import presented_recalled_life
    life, life_audits = presented_recalled_life(
        table=original, audits=audits,
        materials=user.get("inner_life_snapshot", {}).get("materials", {}),
    )
    if not selected and not life:
        return table, historical
    base = table.as_dict()
    rows, materials = _indexed_materials([entry for _, entry in selected] + life, base["subjects"])
    row_offset, material_offset = len(base["source_references"]), len(base["source_materials"])
    for row in rows:
        row["source_ref_index"] += row_offset
        row["material_index"] += material_offset
    base["source_references"].extend(rows)
    base["source_materials"].extend(materials)
    used = tuple(audit for audit in audits if audit in historical or audit in facts or audit in life_audits)
    base["contract"] = RECALLED_SOURCE_TABLE_CONTRACT
    base["coverage_scope"] += "_with_presented_recalled_facts"
    if life:
        base["coverage_scope"] += "_and_settled_life"
    base["recall_trace_hashes"] = [_hash(audit.model_dump(mode="json")) for audit in used]
    base["table_hash"], base["materials_hash"] = _hash(base["source_references"]), _hash(base["source_materials"])
    return VisibleSourceTable(payload_json=_json(base)), used
