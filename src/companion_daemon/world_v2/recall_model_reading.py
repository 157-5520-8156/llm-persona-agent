"""The bounded recall reading presented by CharacterInterior to its author."""
from __future__ import annotations

from typing import TYPE_CHECKING

from .fact_observation_value_lookup import resolve_observation_fact_value

if TYPE_CHECKING:
    from .recall_index import RecallDocument


RECALL_INDEX_POLICY_VERSION = "world-v2-recall-index.hybrid.13"
_ATTRIBUTED_READING_POLICIES = frozenset({
    "world-v2-recall-index.hybrid.8", "world-v2-recall-index.hybrid.9",
    "world-v2-recall-index.hybrid.10", "world-v2-recall-index.hybrid.11",
    "world-v2-recall-index.hybrid.12", "world-v2-recall-index.hybrid.13",
    "world-v2-recall-index.hybrid.14", "world-v2-recall-index.hybrid.15",
})
_FACT_READING_POLICIES = frozenset({
    "world-v2-recall-index.hybrid.9", "world-v2-recall-index.hybrid.10",
    "world-v2-recall-index.hybrid.11", "world-v2-recall-index.hybrid.12",
    "world-v2-recall-index.hybrid.13",
    "world-v2-recall-index.hybrid.14", "world-v2-recall-index.hybrid.15",
})


def supports_life_reading(index_version: str) -> bool:
    return index_version.partition("+embedding:")[0] in {
        "world-v2-recall-index.hybrid.10", "world-v2-recall-index.hybrid.11",
        "world-v2-recall-index.hybrid.12", "world-v2-recall-index.hybrid.13",
        "world-v2-recall-index.hybrid.14", "world-v2-recall-index.hybrid.15",
    }


def supports_fact_reading(index_version: str) -> bool:
    """Only newly pinned readings expose typed accepted values as Fact evidence."""
    return index_version.partition("+embedding:")[0] in _FACT_READING_POLICIES


def _includes_attribution(index_version: str) -> bool:
    """Select the exact reading recorded by the index, including old audits."""
    policy = index_version.partition("+embedding:")[0]
    # Earlier and custom recorded index versions predate this reading. Do not
    # rewrite them during replay, or retry a new reading as the legacy shape.
    return policy in _ATTRIBUTED_READING_POLICIES


def interior_recall_item(
    document: RecallDocument, *, index_version: str = RECALL_INDEX_POLICY_VERSION,
) -> dict[str, object]:
    attributed = _includes_attribution(index_version)
    fact = document.accepted_fact if supports_fact_reading(index_version) else None
    life = document.settled_life if supports_life_reading(index_version) else None
    accepted_value = None
    fact_metadata: dict[str, object] = {}
    if fact is not None:
        if fact.accepted_value_binding is None:
            raise ValueError("accepted Fact recall lacks its exact value binding")
        accepted_value = resolve_observation_fact_value(
            binding=fact.accepted_value_binding, source_excerpt=fact.source_excerpt,
        )
        fact_metadata = {
            "fact_ref": fact.fact_id,
            "accepted_event_ref": fact.accepted_fact_event_ref,
            "subject_ref": fact.subject_ref,
            "predicate_code": fact.predicate_code,
            "status": fact.status,
            "confidence_bp": fact.confidence_bp,
            "occurred_at": fact.occurred_at.isoformat(),
            "committed_at": fact.committed_at.isoformat(),
            "updated_at": fact.updated_at.isoformat(),
            "valid_from": document.valid_from.isoformat() if document.valid_from else None,
            "valid_to": document.valid_to.isoformat() if document.valid_to else None,
        }
    story_window = {}
    if document.prehistory is not None and document.prehistory.story is not None:
        start = document.source_window_start or 0
        total = document.prehistory.story.record_characters
        story_window = {"story_window": {
            "record_ref": document.source_item_ref.split(":window:")[0],
            "start": start, "end": start + len(document.text),
            "complete_record": start == 0 and len(document.text) == total,
            "next": {"record_ref": document.source_item_ref.split(":window:")[0], "start": start + 416}
                    if start + len(document.text) < total else None,
        }}
    result = {
        **story_window,
        "source_ref": (
            fact.accepted_fact_event_ref
            if fact is not None and fact.status == "historical"
            else life.occurrence_id if life is not None
            else document.source_item_ref
        ),
        "memory_kind": document.memory_kind,
        "source_slice": document.source_slice,
        "authority": document.authority,
        "epistemic_scope": document.effective_epistemic_scope,
        "text": accepted_value if accepted_value is not None else document.text,
        "occurred_from": document.occurred_from.isoformat(),
        "occurred_to": document.occurred_to.isoformat() if document.occurred_to else None,
        "privacy_class": document.privacy_class,
        **({"status": document.status,
            **({"interpretation_scope": document.interpretation_scope}
               if document.interpretation_scope is not None else {})}
           if document.authority == "defeasible_interpretation"
           and index_version.partition("+embedding:")[0] in {
               "world-v2-recall-index.hybrid.13", "world-v2-recall-index.hybrid.14", "world-v2-recall-index.hybrid.15"}
           else {}),
        **({"subject_refs": list(document.subject_refs)} if attributed else {}),
        **({"speaker_ref": document.speaker_ref}
           if attributed and document.speaker_ref is not None else {}),
        **({"prehistory": document.prehistory.model_dump(mode="json")}
           if document.prehistory is not None else {}),
        **({"accepted_fact": fact_metadata} if fact is not None else {}),
        **({"source_window_start": document.source_window_start}
           if document.source_window_start is not None and index_version.partition("+embedding:")[0] in {
               "world-v2-recall-index.hybrid.11", "world-v2-recall-index.hybrid.12",
               "world-v2-recall-index.hybrid.13",
               "world-v2-recall-index.hybrid.14", "world-v2-recall-index.hybrid.15",
           } else {}),
        **({"settled_life": {
            "settled_at": life.settled_at.isoformat(),
            "world_consequence": life.content.world_consequence.model_dump(mode="json"),
        }}
           if life is not None else {}),
    }

    if (index_version.partition("+embedding:")[0] == "world-v2-recall-index.hybrid.15"
            and document.prehistory is not None and document.prehistory.story is not None):
        # Only transport proof and duplicate coordinates leave the model view.
        # Full typed source proof stays in the immutable RecallAuditTrace.
        history = result["prehistory"]
        for key in ("archive_event_ref", "archive_world_revision", "archive_payload_hash",
                    "world_started_at", "occurred_from", "occurred_until", "time_precision",
                    "participant_refs"):
            history.pop(key, None)
        if history.get("location_ref") is None:
            history.pop("location_ref", None)
        for key in ("memory_kind", "source_slice", "authority", "subject_refs",
                    "occurred_from", "occurred_to"):
            result.pop(key, None)
        story = history["story"]
        for key in ("source_artifact_ref", "contract", "outer_interval_semantics", "record_characters"):
            story.pop(key, None)
        record_ref = document.source_item_ref.split(":window:")[0]
        edges = story.pop("relations")
        if any(edge["left_record_id"] != record_ref for edge in edges):
            raise ValueError("story reading relation does not start at its source record")
        story["outgoing_relations"] = [
            {"to_record_ref": edge["right_record_id"], "relation_kind": edge["relation_kind"]}
            for edge in edges
        ]
        if result["story_window"]["complete_record"]:
            result["story_window"] = {"complete_record": True}
    return result
