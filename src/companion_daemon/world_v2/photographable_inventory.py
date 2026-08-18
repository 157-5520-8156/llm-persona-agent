"""Source-bound facts about photos she actually has in hand.

This is not a share recommendation and not a media-selection occasion.  It
answers one world question the inbound snapshot otherwise cannot: whether any
already-opened ``PhotoCandidate`` is currently available, and which settled
life moments those candidates bind.  An empty inventory is itself a fact.
"""

from __future__ import annotations

from datetime import datetime
from .media_conversation_window import is_reask_eligible
from .schema_core import FrozenModel, PrivacyClass


class PhotographableMomentFact(FrozenModel):
    """One lived moment plus whether a sendable photo currently exists for it."""

    source_ref: str
    photo_in_hand: bool
    privacy_class: PrivacyClass
    settled_at: datetime | None = None
    location_ref: str | None = None
    what_happened: str | None = None


class PhotographableInventory(FrozenModel):
    """Album state as a world fact.  ``available_count`` may be zero."""

    available_count: int
    already_sent_count: int
    moments: tuple[PhotographableMomentFact, ...]
    source_refs: tuple[str, ...]


def available_photo_source_refs(
    projection: object, *, logical_time: datetime | None
) -> frozenset[str]:
    """Settlement (or other) refs bound to a still-available photo candidate."""

    refs: set[str] = set()
    for item in getattr(projection, "photo_candidates", ()) or ():
        available = getattr(item, "status", None) == "available"
        reask = isinstance(logical_time, datetime) and is_reask_eligible(
            projection, candidate=item, logical_time=logical_time
        )
        if not available and not reask:
            continue
        expires_at = getattr(item, "expires_at", None)
        if (
            isinstance(logical_time, datetime)
            and isinstance(expires_at, datetime)
            and expires_at <= logical_time
        ):
            continue
        for ref in getattr(item, "source_event_refs", ()) or ():
            if isinstance(ref, str) and ref:
                refs.add(ref)
        for source in getattr(item, "source_events", ()) or ():
            event_ref = getattr(source, "event_ref", None)
            if isinstance(event_ref, str) and event_ref:
                refs.add(event_ref)
    return frozenset(refs)


def settlement_has_available_photo(
    projection: object, *, settlement_ref: str, logical_time: datetime | None
) -> bool:
    if not isinstance(settlement_ref, str) or not settlement_ref:
        return False
    return settlement_ref in available_photo_source_refs(
        projection, logical_time=logical_time
    )


def compile_photographable_inventory(
    *,
    moments: tuple[PhotographableMomentFact, ...],
    already_sent_count: int,
    extra_source_refs: tuple[str, ...] = (),
) -> PhotographableInventory:
    """Fold already-sourced moments into one inventory fact.

    ``available_count`` is the number of moments with a photo in hand, not a
    host suggestion to create more.
    """

    if already_sent_count < 0:
        raise ValueError("already_sent_count cannot be negative")
    available = sum(1 for item in moments if item.photo_in_hand)
    source_refs = tuple(
        dict.fromkeys(
            (
                *(item.source_ref for item in moments),
                *(ref for ref in extra_source_refs if isinstance(ref, str) and ref),
            )
        )
    )
    return PhotographableInventory(
        available_count=available,
        already_sent_count=already_sent_count,
        moments=moments,
        source_refs=source_refs,
    )


def inventory_material(inventory: PhotographableInventory) -> dict[str, object]:
    """Model-facing packet.  Empty album still has ``available_count: 0``."""

    items = [
        {
            "source_ref": item.source_ref,
            "photo_in_hand": item.photo_in_hand,
            "privacy_class": item.privacy_class,
            **(
                {"settled_at": item.settled_at.isoformat()}
                if item.settled_at is not None
                else {}
            ),
            **({"location_ref": item.location_ref} if item.location_ref else {}),
            **({"what_happened": item.what_happened} if item.what_happened else {}),
        }
        for item in inventory.moments
    ]
    payload: dict[str, object] = {
        "availability": "available",
        "available_count": inventory.available_count,
        "already_sent_count": inventory.already_sent_count,
        "items": items,
    }
    if inventory.source_refs:
        payload["source_refs"] = list(inventory.source_refs)
    return payload


def empty_inventory_material(*, source_refs: tuple[str, ...]) -> dict[str, object]:
    """Explicit empty album.  ``source_refs`` must be ledger-closed."""

    refs = tuple(dict.fromkeys(ref for ref in source_refs if isinstance(ref, str) and ref))
    if not refs:
        return {"availability": "unavailable"}
    return inventory_material(
        compile_photographable_inventory(
            moments=(),
            already_sent_count=0,
            extra_source_refs=refs,
        )
    )


__all__ = [
    "PhotographableInventory",
    "PhotographableMomentFact",
    "available_photo_source_refs",
    "compile_photographable_inventory",
    "empty_inventory_material",
    "inventory_material",
    "settlement_has_available_photo",
]
