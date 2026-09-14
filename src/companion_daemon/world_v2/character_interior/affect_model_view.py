"""Omit transaction coordinates only from the provider's Affect reading.

Canonical snapshots, evidence and authority checks retain the full references.
Appraisal/hypothesis identities, revisions and source clusters remain readable;
this does not choose a feeling, change a target, prune history or drop a source.
"""
from __future__ import annotations


def compact_affect_for_model_view(value: object) -> object:
    """Apply after privacy redaction; unknown or partial shapes stay intact."""
    if not isinstance(value, list):
        return value
    result = []
    for episode in value:
        if not isinstance(episode, dict) or not isinstance(episode.get("components"), list):
            result.append(episode)
            continue
        components = []
        for component in episode["components"]:
            if not isinstance(component, dict) or not isinstance(component.get("appraisal_refs"), list):
                components.append(component)
                continue
            refs = []
            for ref in component["appraisal_refs"]:
                if isinstance(ref, dict) and all(
                    isinstance(ref.get(key), str) and ref[key]
                    for key in ("appraisal_id", "hypothesis_id", "source_cluster_ref")
                ) and type(ref.get("accepted_entity_revision")) is int:
                    refs.append({key: item for key, item in ref.items()
                                 if key not in {"accepted_change_id", "accepted_transition_id"}})
                else:
                    refs.append(ref)
            components.append({**component, "appraisal_refs": refs})
        result.append({**episode, "components": components})
    return result
