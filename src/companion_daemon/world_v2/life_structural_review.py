"""Structural Life review: the Life review lifecycle without a semantic reviewer.

Life Development used to send every World Author draft to one or two extra
model calls (general source closure, focused novel origin) that judged whether
prose was "entailed" by its cited sources.  Measured on the production ledger
(2026-09-24..10-21) those calls were 29% of all model spend, the focused critic
returned ``supported`` for every committed occurrence, and the general reviewer
mostly rejected ordinary inference ("the hall was rearranged" -> "the queue
moved") or her own account of her day.  That is exactly the texture that makes
her read as a person, so the operator retired the semantic review.

What still guards truth is structural and runs whether or not a reviewer exists:

* the draft compiler accepts only manifest-offered opaque entity / location /
  source refs, and provisional NPCs and places through their own authority;
* ``_source_closure_cited_sources`` still resolves every cited existing-world
  ref to an exact committed event or pinned material before review (missing ->
  technical failure);
* commit batch invariants still bind the review hash and audit identity;
* a message can only count as delivered to the user through an Action receipt.

This model plugs into the unchanged review lifecycle so audit records, review
hashes, recovery and cold replay keep their exact shape.  It never calls a
provider, never spends budget and always answers ``supported``.  Historical
model-authored verdicts keep decoding through the frozen parsers.
"""

from __future__ import annotations

import json

STRUCTURAL_LIFE_REVIEW_MODEL_ID = "structural/life-review.1"
STRUCTURAL_LIFE_REVIEW_REASON = (
    "structural-life-review.1: no semantic review. Cited existing-world refs were "
    "resolved to exact ledger/pinned material and opaque refs were validated by the "
    "draft compiler before this point; invented detail within the draft is the "
    "World Author's authority."
)
_SUPPORTED = json.dumps(
    {"decision": "supported", "reason": STRUCTURAL_LIFE_REVIEW_REASON},
    ensure_ascii=False,
    separators=(",", ":"),
)


class StructuralLifeReviewModel:
    """Provider-free reviewer accepted by ``RoleBoundLifeDevelopmentModelAdapter``."""

    model = STRUCTURAL_LIFE_REVIEW_MODEL_ID
    # Never ask the lifecycle for a provider tool contract.
    supports_strict_tool_choice = False
    # Health and isolation readers treat this as its own semantic authority:
    # it can never be the provider that authored the draft.
    semantic_authority_id = STRUCTURAL_LIFE_REVIEW_MODEL_ID

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.0,
    ) -> str:
        del messages, temperature
        return _SUPPORTED

    async def complete_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.0,
        tools: list[dict[str, object]] | None = None,
        tool_choice: object | None = None,
    ) -> str:
        del tools, tool_choice
        return await self.complete(messages, temperature=temperature)

    def health_snapshot(self) -> dict[str, object]:
        return {"mode": "structural", "model": STRUCTURAL_LIFE_REVIEW_MODEL_ID}


def is_structural_life_review(model: object | None) -> bool:
    seen: set[int] = set()
    current = model
    while current is not None and id(current) not in seen:
        if isinstance(current, StructuralLifeReviewModel):
            return True
        seen.add(id(current))
        current = getattr(current, "authority_origin", None) or getattr(current, "_model", None)
    return False


__all__ = [
    "STRUCTURAL_LIFE_REVIEW_MODEL_ID",
    "STRUCTURAL_LIFE_REVIEW_REASON",
    "StructuralLifeReviewModel",
    "is_structural_life_review",
]
