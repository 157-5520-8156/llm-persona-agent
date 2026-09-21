"""The bounded recall reading presented by CharacterInterior to its author."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .recall_index import RecallDocument


RECALL_INDEX_POLICY_VERSION = "world-v2-recall-index.hybrid.8"
_ATTRIBUTED_READING_POLICIES = frozenset({"world-v2-recall-index.hybrid.8"})


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
    return {
        "source_ref": document.source_item_ref,
        "memory_kind": document.memory_kind,
        "source_slice": document.source_slice,
        "authority": document.authority,
        "epistemic_scope": document.effective_epistemic_scope,
        "text": document.text,
        "occurred_from": document.occurred_from.isoformat(),
        "occurred_to": document.occurred_to.isoformat() if document.occurred_to else None,
        "privacy_class": document.privacy_class,
        **({"subject_refs": list(document.subject_refs)} if attributed else {}),
        **({"speaker_ref": document.speaker_ref}
           if attributed and document.speaker_ref is not None else {}),
        **({"prehistory": document.prehistory.model_dump(mode="json")}
           if document.prehistory is not None else {}),
    }
