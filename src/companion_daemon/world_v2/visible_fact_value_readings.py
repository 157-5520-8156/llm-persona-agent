"""Exact accepted-Fact value selection for a pinned visible source row.

The enclosing Observation remains context. A model must select the original
accepted value bytes, and the host verifies their binding and typed scope. This
does not decide whether a visible claim follows from the Fact's predicate, or
replace the caller's verification of the original source table and full prose.
"""
from copy import deepcopy
import hashlib
import json

from .context_capsule import FactRecallItem, HistoricalFactRecallItem
from .fact_observation_value import FactObservationValueBinding
from .fact_observation_value_lookup import resolve_observation_fact_value
from .visible_source_closure_protocol import _eligible_reference

CONTRACT = "visible-fact-value-reading.1"
AUTHORITY = "accepted_fact_with_observation_source"


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def compile_fact_value_reading(row: dict) -> dict | None:
    """Compile one qualified Fact alias; reject invalid known-family rows.

    None denotes a different source family. A known Fact without a complete
    value binding is not eligible for the ordinary scalar fallback. Original
    source identity and material are retained for exact re-verification.
    """
    material = row.get("review_material") if isinstance(row, dict) else None
    if not isinstance(material, dict) or material.get("authority") != AUTHORITY:
        return None
    if (material.get("kind") != "pinned_context_item"
        or material.get("lane") != "relevant_facts"
        or material.get("privacy_class") == "withhold"
        or material.get("availability") != "available"
        or not _eligible_reference(row)):
        raise ValueError("visible Fact source is not qualified")
    item = material.get("item")
    value = item.get("value") if isinstance(item, dict) else None
    if not isinstance(value, dict):
        raise ValueError("visible Fact source lacks its typed value")
    model = HistoricalFactRecallItem if value.get("status") == "historical" else FactRecallItem
    fact = model.model_validate_json(_canonical(value), strict=True)
    index, owner_role = row.get("source_ref_index"), row.get("support_subject_role")
    if (type(index) is not int or index < 0
        or owner_role not in {"companion", "counterpart"}
        or row.get("support_subject_ref") != fact.subject_ref
        or material.get("actor_ref") != fact.subject_ref
        or item.get("item_ref") != fact.fact_id
        or row.get("source_ref") not in {fact.fact_id, fact.accepted_fact_event_ref}
        or row.get("source_ref") not in material.get("source_refs", ())
        or fact.privacy_class == "withhold"
        or fact.accepted_value_binding is None):
        raise ValueError("visible Fact value lacks its exact source/subject binding")
    # _eligible_reference verifies the complete value/source hashes. Also tie
    # the typed Fact and Observation coordinates to those exact bound events.
    expected = {
        (fact.accepted_fact_event_ref, fact.accepted_fact_world_revision, fact.accepted_fact_payload_hash),
        (fact.observation_event_ref, fact.observation_world_revision, fact.observation_event_payload_hash),
    }
    actual = {(binding["ref"], binding["source_world_revision"], binding["immutable_hash"])
              for binding in item.get("source_bindings", ()) if binding.get("source_kind") == "committed_event"}
    if not expected <= actual:
        raise ValueError("visible Fact events differ from the bound source events")
    fields = ("fact_id", "subject_ref", "predicate_code", "status", "privacy_class", "confidence_bp",
              "occurred_at", "committed_at", "updated_at")
    if fact.status == "historical":
        fields += ("valid_from", "valid_to")
    source_row = deepcopy(row)
    accepted_value = resolve_observation_fact_value(binding=fact.accepted_value_binding,
                                                   source_excerpt=fact.source_excerpt)
    return {
        "contract": CONTRACT, "source_family": "accepted_fact_value",
        "source_ref_index": index, "source_ref": row["source_ref"],
        "item_ref": fact.fact_id, "pointer": "/item/value/source_excerpt",
        "value": fact.source_excerpt, "fact_context": {key: value[key] for key in fields},
        "accepted_value": accepted_value,
        "value_binding": fact.accepted_value_binding.model_dump(mode="json"),
        "source_owner_ref": fact.subject_ref, "source_owner_role": owner_role,
        "permissions": [], "value_selection_permissions": [[
            "historical_accepted_fact" if fact.status == "historical" else "accepted_fact", owner_role,
        ]],
        "source_row": source_row,
        "source_row_sha256": hashlib.sha256(_canonical(source_row).encode()).hexdigest(),
    }


def require_fact_value_selection(*, reading: dict, quoted_value: str, claim_scope: str,
                                 subject_ref: str, subject_role: str | None = None) -> dict:
    """Verify a model-selected exact value; never infer one from the excerpt.

    The caller may add a catalog reading ID, but cannot alter helper-owned
    fields. The original source table must independently remain pinned and
    recompiled by that caller; a self-contained descriptor is not provenance.
    """
    if not isinstance(reading, dict) or reading.get("contract") != CONTRACT:
        raise ValueError("visible Fact reading contract is invalid")
    expected = compile_fact_value_reading(reading.get("source_row"))
    if expected is None or any(key not in reading or _canonical(reading[key]) != _canonical(value)
                               for key, value in expected.items()):
        raise ValueError("visible Fact reading differs from its original source compilation")
    role = reading["source_owner_role"] if subject_role is None else subject_role
    if (subject_ref != reading["source_owner_ref"]
        or role != reading["source_owner_role"]
        or [claim_scope, role] not in reading["value_selection_permissions"]):
        raise ValueError("visible Fact selection exceeds its subject/status permission")
    binding = FactObservationValueBinding.model_validate_json(_canonical(reading["value_binding"]), strict=True)
    value = binding.select(source_excerpt=reading["value"], quoted_value=quoted_value)
    return {
        "contract": CONTRACT, "source_ref_index": reading["source_ref_index"],
        "source_ref": reading["source_ref"], "source_row_sha256": reading["source_row_sha256"],
        "quoted_value": value, "claim_scope": claim_scope, "subject_ref": subject_ref,
        "subject_role": role, "fact_context": deepcopy(reading["fact_context"]),
        "observation_context": reading["value"], "write_authority": False,
        "semantic_coverage": "not_assessed",
    }
