"""Source-bound context contracts shared by current life-world boundaries.

This module carries no character decision loop.  It only exposes the exact
compiler-issued capsule view that a current authority may consume.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Protocol


LIFE_REVIEW_PROJECTION_CONTRACT = "life-review-selected-source-proof.1"


class LifeContextCapsule(Protocol):
    capsule_id: str
    snapshot_hash: str
    world_revision: int
    deliberation_revision: int
    ledger_sequence: int
    logical_time: datetime | None
    model_content_json: str


class LifeContextCapsuleHandle(Protocol):
    @property
    def capsule(self) -> LifeContextCapsule: ...


class LifeContextCapsuleCompiler(Protocol):
    def compile_for_deliberation(self, query) -> LifeContextCapsuleHandle: ...  # type: ignore[no-untyped-def]


def compile_life_decision_context(capsule: LifeContextCapsule) -> dict[str, object]:
    """Return the compiler-issued bounded view without a second truth path."""

    decoded = json.loads(capsule.model_content_json)
    if not isinstance(decoded, dict):
        raise ValueError("life Context Capsule must decode to an object")
    return decoded


def compile_life_review_context(capsule: LifeContextCapsule) -> dict[str, object]:
    """Present exact proof for already selected Fact/Dialogue items only.

    The normal model view is intentionally lossy. Its value hashes cannot be
    attached to shortened values as if those were complete source payloads.
    Revalidate the compiler-issued Capsule, including its tag and full output,
    before deriving this independent presentation. This reads no new state.
    """
    from .context_capsule import ContextCapsule

    if not isinstance(capsule, ContextCapsule):
        raise ValueError("Life review requires a trusted typed Context Capsule")
    validated = ContextCapsule.model_validate_json(capsule.model_dump_json())
    if validated.provenance_kind != "trusted_resolver_compiled":
        raise ValueError("Life review requires a trusted compiler result")
    context = compile_life_decision_context(validated)
    slices = context.get("slices")
    if not isinstance(slices, dict):
        raise ValueError("Life review Capsule lacks selected slices")
    for name in ("relevant_facts", "recent_dialogue"):
        selected = getattr(validated, name)
        if selected.availability != "available":
            continue
        lane = slices.get(name)
        if not isinstance(lane, dict):
            raise ValueError("Life review Capsule lacks its selected slice presentation")
        slices[name] = {
            **lane,
            "items": [
                {
                    "item_ref": item.item_ref,
                    "rank_score_bp": item.rank_score_bp,
                    "privacy_class": item.privacy_class,
                    "source_bindings": [
                        binding.model_dump(mode="json") for binding in item.source_bindings
                    ],
                    "source_hash": item.source_hash,
                    "value_hash": item.value_hash,
                    "value": json.loads(item.payload_json),
                }
                for item in selected.items
            ],
        }
    context["life_review_projection"] = {
        "contract": LIFE_REVIEW_PROJECTION_CONTRACT,
        "capsule_id": validated.capsule_id,
        "model_content_hash": hashlib.sha256(validated.model_content_json.encode()).hexdigest(),
    }
    return context


__all__ = [
    "LifeContextCapsule",
    "LifeContextCapsuleCompiler",
    "LifeContextCapsuleHandle",
    "compile_life_decision_context",
    "compile_life_review_context",
    "LIFE_REVIEW_PROJECTION_CONTRACT",
]
