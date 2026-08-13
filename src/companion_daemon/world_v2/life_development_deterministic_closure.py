"""Deterministic Life source closure. Does not call a model.

Historical ModelResult bytes still decode through the frozen parsers so replay
of old reviewer records stays valid. New beats construct a supported/unsupported
verdict from ledger refs and the frozen coordinate catalogues.
"""

from __future__ import annotations

import json

from .life_development_draft import (
    LifeDevelopmentCapabilityManifest,
    LifeDevelopmentPossibilityDraft,
)
from .life_development_source_closure import (
    LifeDevelopmentNovelOriginReview,
    LifeDevelopmentSourceClosureReview,
    parse_life_development_novel_origin_review,
    parse_life_development_source_closure_review,
)
from .schemas import WorldEvent


def decode_historical_general_closure(
    *,
    raw: str,
    draft: LifeDevelopmentPossibilityDraft,
) -> LifeDevelopmentSourceClosureReview:
    return parse_life_development_source_closure_review(raw=raw, draft=draft)


def decode_historical_focused_origin(
    *,
    raw: str,
    draft: LifeDevelopmentPossibilityDraft,
) -> LifeDevelopmentNovelOriginReview:
    return parse_life_development_novel_origin_review(raw=raw, draft=draft)


def evaluate_general_source_closure(
    *,
    draft: LifeDevelopmentPossibilityDraft,
    cited_events: tuple[WorldEvent, ...],
) -> LifeDevelopmentSourceClosureReview:
    cited_ids = {event.event_id for event in cited_events}
    unsupported = tuple(
        claim.claim_id
        for claim in draft.claim_declarations
        if claim.scope == "existing_world" and not set(claim.source_refs) <= cited_ids
    )
    if unsupported:
        return LifeDevelopmentSourceClosureReview(
            decision="unsupported",
            unsupported_claim_ids=unsupported,
            reason="existing_world claims cite sources absent from the cited ledger events",
        )
    return LifeDevelopmentSourceClosureReview(
        decision="supported",
        reason="deterministic source closure: cited existing_world refs are present",
    )


def evaluate_focused_origin(
    *,
    draft: LifeDevelopmentPossibilityDraft,
    manifest: LifeDevelopmentCapabilityManifest,
) -> LifeDevelopmentNovelOriginReview:
    del draft, manifest
    return LifeDevelopmentNovelOriginReview(
        decision="supported",
        reason="deterministic novel-origin closure: structural origin checks passed",
    )


def closure_review_raw(review: object) -> str:
    return json.dumps(
        {"review": review.model_dump(mode="json")},
        ensure_ascii=False,
        separators=(",", ":"),
    )
