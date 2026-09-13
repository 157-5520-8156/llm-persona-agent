"""The bounded recall reading presented by CharacterInterior to its author."""
from __future__ import annotations

from .recall_index import RecallDocument


def interior_recall_item(document: RecallDocument) -> dict[str, object]:
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
        **({"prehistory": document.prehistory.model_dump(mode="json")}
           if document.prehistory is not None else {}),
    }
