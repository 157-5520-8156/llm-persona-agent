"""Exact settled-life material binding, with no semantic verdict or retrieval.

The public Capsule reader owns ledger/content verification.  A review row may
use only that selected World's settlement and descriptor; a character's private
response, an opaque payload address, or an unselected outcome is not that proof.
"""

from __future__ import annotations

import hashlib
import json

from .context_capsule import ResolvedSourceBinding, source_bindings_hash
from .world_life_context import WorldLifeContextItem

_PRIVACY_RANK = {"public": 0, "shareable": 1, "personal": 2, "private": 3, "withhold": 4}


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def settled_life_source_support(material: dict, source_ref: str) -> tuple[str, dict] | None:
    """Revalidate the exact typed material and one ref, never the prose meaning."""
    if (
        material.get("kind") != "pinned_context_item"
        or material.get("lane") != "world_life"
        or material.get("availability") != "available"
        or material.get("privacy_class") == "withhold"
        or source_ref not in material.get("source_refs", ())
    ):
        return None
    item = material.get("item")
    if not isinstance(item, dict):
        return None
    value = item.get("value")
    if not isinstance(value, dict) or item.get("value_hash") != _hash(value):
        return None
    try:
        life = WorldLifeContextItem.model_validate_json(json.dumps(value))
        bindings = tuple(
            ResolvedSourceBinding.model_validate(binding)
            for binding in item.get("source_bindings", ())
        )
    except (TypeError, ValueError):
        return None
    content = life.content
    actor = material.get("actor_ref")
    if (
        not isinstance(actor, str) or actor not in life.participant_refs
        or item.get("item_ref") != life.occurrence_id
        or material.get("privacy_class") != item.get("privacy_class")
        or _PRIVACY_RANK.get(item.get("privacy_class"), -1) < _PRIVACY_RANK[life.privacy_class]
        or life.privacy_class == "withhold"
        or content is None or content.world_consequence is None
        or content.character_response is not None
        or content.content_kind != "occurrence_result"
        or content.source_entity_id != life.occurrence_id
        or content.source_entity_revision != life.occurrence_entity_revision
        or content.content_ref != life.result_payload_ref
        or content.content_payload_hash != life.result_payload_hash.removeprefix("sha256:")
        or content.authority_event_ref != life.source.authority_event_ref
        or content.authority_world_revision != life.source.authority_world_revision
        or content.authority_payload_hash != life.source.authority_payload_hash
        or _PRIVACY_RANK[content.privacy_class] > _PRIVACY_RANK[life.privacy_class]
        or content.world_consequence.environment.epistemic_scope != "settled_world_environment"
        or source_bindings_hash(bindings) != item.get("source_hash")
    ):
        return None
    attempt = content.world_consequence.authorized_attempt_result
    if attempt is not None and (
        attempt.epistemic_scope != "settled_result_of_bound_attempt"
        or attempt.execution_binding.actor_ref != actor
    ):
        return None
    expected = {
        life.source.authority_event_ref: (
            "WorldOccurrenceSettled", life.source.authority_world_revision,
            life.source.authority_payload_hash,
        ),
        content.descriptor_event_ref: (
            "LifeContentRecorded", content.descriptor_world_revision,
            content.descriptor_payload_hash,
        ),
    }
    if len(expected) != 2 or len(bindings) != 2:
        return None
    for binding in bindings:
        if binding.source_kind != "committed_event" or expected.get(binding.ref) != (
            binding.authority_type, binding.source_world_revision, binding.immutable_hash,
        ):
            return None
    if len({binding.ref for binding in bindings}) != 2 or source_ref not in {
        life.occurrence_id, *expected,
    }:
        return None
    return actor, {
        "contract": "visible-settled-life-source.1",
        "status": "settled",
        "source_event_type": "WorldOccurrenceSettled",
        "scope": "settled_environment_and_bound_attempt_not_current_activity_or_character_response",
        "settled_at": life.settled_at.isoformat(),
    }
