"""Deterministic conversational attention over source-bound dialogue.

The ledger remains the history authority.  This module only decides which
verified dialogue items deserve scarce working-context space for one turn.
It models three distinct signals instead of treating recency as continuity:
the current turn, unacknowledged counterpart messages, and older dialogue
whose language is reactivated by the current message.
"""

from __future__ import annotations

from dataclasses import dataclass

from .associative_recall import AssociativeRecallCandidate, AssociativeRecallCompiler
from .present_prompt import (
    PRESENT_COMPANION_DIALOGUE_ITEM_LIMIT,
    PRESENT_RECENT_DIALOGUE_ITEM_LIMIT,
)
from .recent_dialogue import RecentDialogueItem


_ACKNOWLEDGED_COMPANION_TAIL = 8
# ResolverProof proves at most 32 authority refs per slice. Companion beats
# used to carry acceptance+payload+delivery(+ack) and starved the live head.
# Compact claims keep ~2 refs each, so the pack can reserve the same 8-line
# companion live window the continuity tags and context-truth audit expect.
WORKING_DIALOGUE_SOURCE_REF_BUDGET = 32
WORKING_DIALOGUE_ITEM_BUDGET = 16
WORKING_DIALOGUE_COMPANION_ITEMS = 8
WORKING_DIALOGUE_COUNTERPART_ITEMS = 8


@dataclass(frozen=True, slots=True)
class ConversationContinuitySelection:
    dialogue: tuple[RecentDialogueItem, ...]
    rank_overrides: frozenset[tuple[str, str]] = frozenset()


ContinuityRetrievalCandidate = AssociativeRecallCandidate


class ConversationContinuityCompiler:
    """Select bounded working dialogue behind one small deterministic interface."""

    def __init__(
        self,
        *,
        max_items: int = PRESENT_RECENT_DIALOGUE_ITEM_LIMIT,
        max_pending_items: int = 2,
        max_reactivated_items: int = 4,
        max_companion_items: int = PRESENT_COMPANION_DIALOGUE_ITEM_LIMIT,
    ) -> None:
        if not 4 <= max_items <= PRESENT_RECENT_DIALOGUE_ITEM_LIMIT:
            raise ValueError("conversation continuity item budget is invalid")
        if not 1 <= max_pending_items <= 4:
            raise ValueError("conversation continuity pending budget is invalid")
        if not 1 <= max_reactivated_items <= 6:
            raise ValueError("conversation continuity reactivation budget is invalid")
        if not 0 <= max_companion_items <= PRESENT_COMPANION_DIALOGUE_ITEM_LIMIT:
            raise ValueError("conversation continuity companion budget is invalid")
        self._max_items = max_items
        self._max_pending = max_pending_items
        self._max_companion = max_companion_items
        self._associative_recall = AssociativeRecallCompiler(
            max_items=max_reactivated_items
        )

    def compile(
        self,
        *,
        dialogue: tuple[RecentDialogueItem, ...],
        trigger_ref: str,
        acknowledged_observation_event_refs: frozenset[str] = frozenset(),
        retrieval_candidates: tuple[ContinuityRetrievalCandidate, ...] = (),
    ) -> ConversationContinuitySelection:
        if not dialogue:
            return ConversationContinuitySelection(dialogue=())

        ordered = tuple(
            sorted(
                dialogue,
                key=lambda item: (item.sequence, item.occurred_at, item.dialogue_id),
            )
        )
        current = next(
            (
                item
                for item in reversed(ordered)
                if item.speaker == "counterpart"
                and any(
                    claim.authority_event_ref == trigger_ref for claim in item.source_claims
                )
            ),
            None,
        )
        if current is None:
            return ConversationContinuitySelection(
                dialogue=ordered[-self._max_items :]
            )

        selected: dict[str, tuple[RecentDialogueItem, set[str], int]] = {}

        def retain(item: RecentDialogueItem, reason: str, score: int) -> None:
            existing = selected.get(item.dialogue_id)
            if existing is None:
                selected[item.dialogue_id] = (item, {reason}, score)
                return
            existing[1].add(reason)
            selected[item.dialogue_id] = (existing[0], existing[1], max(existing[2], score))

        retain(current, "current_turn", 10_000)
        companions_before = tuple(
            item
            for item in ordered
            if item.speaker == "companion"
            and (item.sequence, item.occurred_at) < (current.sequence, current.occurred_at)
        )
        acknowledged_event_refs = set(acknowledged_observation_event_refs)
        acknowledged_event_refs.update(
            ref for item in companions_before for ref in item.acknowledges_observation_event_refs
        )
        recent_counterpart = tuple(
            item
            for item in ordered
            if item.speaker == "counterpart"
            and item.dialogue_id != current.dialogue_id
            and (item.sequence, item.occurred_at) < (current.sequence, current.occurred_at)
        )[-WORKING_DIALOGUE_COUNTERPART_ITEMS:]
        pending = tuple(
            item
            for item in recent_counterpart
            if not any(
                claim.authority_event_ref in acknowledged_event_refs
                for claim in item.source_claims
            )
        )[-self._max_pending :]
        for offset, item in enumerate(reversed(pending)):
            retain(item, "pending_interaction", 9_850 - offset * 50)

        for offset, item in enumerate(reversed(companions_before[-self._max_companion :])):
            retain(item, "recent_companion", 9_400 - offset * 50)

        counterpart_by_source_ref = {
            claim.authority_event_ref: item
            for item in ordered
            if item.speaker == "counterpart"
            for claim in item.source_claims
        }
        recent_companions = companions_before[-self._max_companion :]
        # Same tail as the live-head companion window.  Only the last two
        # beats were consulted before, so an inbound reaction could keep the
        # photo-thread acknowledgements and drop the line she actually
        # answered minutes earlier.
        for offset, companion in enumerate(
            reversed(recent_companions[-_ACKNOWLEDGED_COMPANION_TAIL:])
        ):
            for acknowledged_ref in companion.acknowledges_observation_event_refs:
                acknowledged = counterpart_by_source_ref.get(acknowledged_ref)
                if acknowledged is not None:
                    retain(
                        acknowledged,
                        "acknowledged_context",
                        9_750 - offset * 50,
                    )

        for offset, item in enumerate(reversed(ordered)):
            if len(selected) >= self._max_items:
                break
            retain(item, "recent", max(1, 8_500 - offset * 25))

        ranked = sorted(
            selected.values(),
            key=lambda value: (
                -value[2],
                -value[0].occurred_at.timestamp(),
                -value[0].sequence,
                value[0].dialogue_id,
            ),
        )[: self._max_items]
        selected_dialogue = tuple(
            sorted(
                (
                    item.model_copy(
                        update={"continuity_reasons": tuple(sorted(reasons))}
                    )
                    for item, reasons, _ in ranked
                ),
                key=lambda item: (item.sequence, item.occurred_at, item.dialogue_id),
            )
        )
        rank_overrides = self._associative_recall.compile(
            cue_text=current.text,
            candidates=retrieval_candidates,
        ).item_refs
        return ConversationContinuitySelection(
            dialogue=selected_dialogue,
            rank_overrides=rank_overrides,
        )


def _dialogue_source_refs(item: RecentDialogueItem) -> set[str]:
    refs = {claim.authority_event_ref for claim in item.source_claims}
    if item.sidecar_ref:
        refs.add(item.sidecar_ref)
    return refs


def pack_recent_dialogue_under_source_budget(
    items: tuple[RecentDialogueItem, ...] | list[RecentDialogueItem],
    *,
    max_refs: int = WORKING_DIALOGUE_SOURCE_REF_BUDGET,
    max_items: int = WORKING_DIALOGUE_ITEM_BUDGET,
    companion_items: int = WORKING_DIALOGUE_COMPANION_ITEMS,
    counterpart_items: int = WORKING_DIALOGUE_COUNTERPART_ITEMS,
) -> tuple[RecentDialogueItem, ...]:
    """Keep a real back-and-forth inside the ResolverProof source-ref bound.

    Rank-order filling spends the 32-ref budget on whichever speaker the
    continuity tags currently favour. The live window is both speakers,
    newest first, with a reserved seat for her last beats so the next turn
    can see what she just said.
    """

    if not items:
        return ()

    selected: list[RecentDialogueItem] = []
    selected_ids: set[str] = set()
    used_refs: set[str] = set()

    def try_add(item: RecentDialogueItem) -> bool:
        if item.dialogue_id in selected_ids:
            return True
        if len(selected) >= max_items:
            return False
        candidate = _dialogue_source_refs(item)
        if len(used_refs | candidate) > max_refs:
            return False
        selected.append(item)
        selected_ids.add(item.dialogue_id)
        used_refs.update(candidate)
        return True

    def has_reason(item: RecentDialogueItem, reason: str) -> bool:
        return reason in item.continuity_reasons

    for item in items:
        if has_reason(item, "current_turn"):
            try_add(item)
    for item in items:
        if has_reason(item, "pending_interaction"):
            try_add(item)
    counterpart_live = sorted(
        (item for item in items if item.speaker == "counterpart"),
        key=lambda item: (item.sequence, item.occurred_at, item.dialogue_id),
        reverse=True,
    )[:WORKING_DIALOGUE_COUNTERPART_ITEMS]
    for item in counterpart_live:
        try_add(item)

    newest_first = sorted(
        items,
        key=lambda item: (item.sequence, item.occurred_at, item.dialogue_id),
        reverse=True,
    )

    companion_added = 0
    for item in newest_first:
        if companion_added >= companion_items:
            break
        if item.speaker != "companion":
            continue
        if item.dialogue_id in selected_ids:
            companion_added += 1
            continue
        if try_add(item):
            companion_added += 1

    for item in newest_first:
        if item.dialogue_id.startswith("dialogue:media-delivery:"):
            try_add(item)

    counterpart_added = 0
    for item in newest_first:
        if counterpart_added >= counterpart_items:
            break
        if item.speaker != "counterpart":
            continue
        if item.dialogue_id in selected_ids:
            counterpart_added += 1
            continue
        if try_add(item):
            counterpart_added += 1

    for item in newest_first:
        if has_reason(item, "acknowledged_context"):
            try_add(item)

    for item in newest_first:
        if item.speaker == "companion":
            try_add(item)

    return tuple(selected)


__all__ = [
    "ContinuityRetrievalCandidate",
    "ConversationContinuityCompiler",
    "ConversationContinuitySelection",
    "WORKING_DIALOGUE_COMPANION_ITEMS",
    "WORKING_DIALOGUE_COUNTERPART_ITEMS",
    "WORKING_DIALOGUE_ITEM_BUDGET",
    "WORKING_DIALOGUE_SOURCE_REF_BUDGET",
    "pack_recent_dialogue_under_source_budget",
]
