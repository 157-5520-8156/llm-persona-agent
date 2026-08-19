"""Source-bound facts about photos she actually has in hand.

This is not a share recommendation and not a media-selection occasion.  It
answers one world question the inbound snapshot otherwise cannot: whether any
already-opened ``PhotoCandidate`` is currently choosable, and which ledger
events those candidates bind.  An empty inventory is itself a fact.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Mapping

from .media_conversation_window import candidate_has_delivery, is_reask_eligible
from .schema_core import FrozenModel, PrivacyClass

ShareableHoldReason = Literal[
    "expired",
    "already_shared",
    "skipped",
    "unrenderable",
    "failed",
    "already_chosen",
    "not_available",
]
_PRIVACY = frozenset({"public", "shareable", "personal", "private"})
_STATUS_HOLD: dict[str, ShareableHoldReason] = {
    "skipped": "skipped",
    "unrenderable": "unrenderable",
    "failed": "failed",
    "expired": "expired",
    "shared": "already_shared",
}


class PhotographableMomentFact(FrozenModel):
    """One ledger-closed photo fact.  ``photo_in_hand`` is choosability now."""

    source_ref: str
    photo_in_hand: bool
    privacy_class: PrivacyClass
    settled_at: datetime | None = None
    expires_at: datetime | None = None
    location_ref: str | None = None
    what_happened: str | None = None
    hold_reason: ShareableHoldReason | None = None
    family: str | None = None
    candidate_id: str | None = None
    taxonomy: str | None = None


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
        if _candidate_hold_reason(projection, item, logical_time) is not None:
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


def _candidate_source_ref(candidate: object) -> str | None:
    opened = getattr(candidate, "opened_event_ref", None)
    if isinstance(opened, str) and opened:
        return opened
    for ref in getattr(candidate, "source_event_refs", ()) or ():
        if isinstance(ref, str) and ref:
            return ref
    return None


def _bound_location_ref(projection: object, candidate: object) -> str | None:
    bound = {
        ref
        for ref in getattr(candidate, "source_event_refs", ()) or ()
        if isinstance(ref, str) and ref
    }
    bound.update(
        getattr(source, "event_ref", None)
        for source in getattr(candidate, "source_events", ()) or ()
        if isinstance(getattr(source, "event_ref", None), str)
    )
    for occurrence in getattr(projection, "world_occurrences", ()) or ():
        settlement = getattr(occurrence, "settlement_event_ref", None)
        if not isinstance(settlement, str) or settlement not in bound:
            continue
        location = getattr(occurrence, "location_ref", None)
        if isinstance(location, str) and location:
            return location
    return None


def _candidate_hold_reason(
    projection: object, candidate: object, logical_time: datetime | None
) -> ShareableHoldReason | None:
    """Why this candidate is not a fresh choosable photo.  None means it is."""

    candidate_id = getattr(candidate, "candidate_id", None)
    if isinstance(candidate_id, str) and candidate_id and candidate_has_delivery(
        projection, candidate_id=candidate_id
    ):
        return "already_shared"
    expires_at = getattr(candidate, "expires_at", None)
    if (
        isinstance(logical_time, datetime)
        and isinstance(expires_at, datetime)
        and expires_at <= logical_time
    ):
        return "expired"
    status = getattr(candidate, "status", None)
    if status == "available":
        return None
    if isinstance(logical_time, datetime) and is_reask_eligible(
        projection, candidate=candidate, logical_time=logical_time
    ):
        return None
    mapped = _STATUS_HOLD.get(status) if isinstance(status, str) else None
    if mapped is not None:
        return mapped
    if status in {"selected", "planned", "generated"}:
        return "already_chosen"
    return "not_available"


def shareable_photo_facts(
    projection: object, *, logical_time: datetime | None
) -> tuple[PhotographableMomentFact, ...]:
    """One fact per opened candidate, including those that cannot be sent yet."""

    facts: list[PhotographableMomentFact] = []
    for item in getattr(projection, "photo_candidates", ()) or ():
        source_ref = _candidate_source_ref(item)
        privacy = getattr(item, "privacy_ceiling", None)
        if not isinstance(source_ref, str) or privacy not in _PRIVACY:
            continue
        hold = _candidate_hold_reason(projection, item, logical_time)
        taxonomy = getattr(item, "ecology_category", None)
        family = getattr(item, "family", None)
        candidate_id = getattr(item, "candidate_id", None)
        expires_at = getattr(item, "expires_at", None)
        facts.append(
            PhotographableMomentFact(
                source_ref=source_ref,
                photo_in_hand=hold is None,
                privacy_class=privacy,
                expires_at=expires_at if isinstance(expires_at, datetime) else None,
                location_ref=_bound_location_ref(projection, item),
                hold_reason=hold,
                family=family if isinstance(family, str) else None,
                candidate_id=candidate_id if isinstance(candidate_id, str) else None,
                taxonomy=taxonomy if isinstance(taxonomy, str) else None,
            )
        )
    return tuple(facts)


def _event_source_binding(projection: object, event_id: str) -> dict[str, object] | None:
    for item in getattr(projection, "committed_world_event_refs", ()) or ():
        if getattr(item, "event_id", None) != event_id:
            continue
        payload_hash = getattr(item, "payload_hash", None)
        revision = getattr(item, "world_revision", None)
        event_type = getattr(item, "event_type", None)
        if not isinstance(payload_hash, str) or not isinstance(revision, int):
            return None
        digest = payload_hash.removeprefix("sha256:")
        if len(digest) != 64:
            return None
        return {
            "ref": event_id,
            "source_kind": "committed_event",
            "authority_type": event_type if isinstance(event_type, str) else "PhotoCandidateOpened",
            "source_world_revision": revision,
            "immutable_hash": digest,
        }
    return None


def shareable_photo_slice_items(
    projection: object, *, logical_time: datetime | None
) -> list[dict[str, object]]:
    """Capsule-shaped items for ``shareable_photos``.  Empty list is a fact."""

    items: list[dict[str, object]] = []
    for fact in shareable_photo_facts(projection, logical_time=logical_time):
        value: dict[str, object] = {
            "photo_in_hand": fact.photo_in_hand,
            "privacy_class": fact.privacy_class,
        }
        if fact.candidate_id:
            value["candidate_id"] = fact.candidate_id
        if fact.family:
            value["family"] = fact.family
        if fact.taxonomy:
            value["taxonomy"] = fact.taxonomy
        if fact.hold_reason:
            value["hold_reason"] = fact.hold_reason
        if fact.location_ref:
            value["location_ref"] = fact.location_ref
        if fact.expires_at is not None:
            value["expires_at"] = fact.expires_at.isoformat()
        row: dict[str, object] = {
            "item_ref": fact.candidate_id or fact.source_ref,
            "source_ref": fact.source_ref,
            "privacy_class": fact.privacy_class,
            "value": value,
        }
        binding = _event_source_binding(projection, fact.source_ref)
        if binding is not None:
            row["source_bindings"] = [binding]
            row["attention_source_refs"] = [binding["ref"]]
        items.append(row)
    return items


def install_shareable_photos_context(
    context: Mapping[str, object], projection: object
) -> dict[str, object]:
    """Pin opened photo candidates into Context.  The capsule may omit world_life."""

    result = dict(context)
    slices = dict(context.get("slices") or {})
    logical_time = getattr(projection, "logical_time", None)
    items = shareable_photo_slice_items(projection, logical_time=logical_time)
    slices["shareable_photos"] = {
        "availability": "available",
        "source_refs": [item["source_ref"] for item in items],
        "items": items,
    }
    result["slices"] = slices
    return result


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
            **(
                {"expires_at": item.expires_at.isoformat()}
                if item.expires_at is not None
                else {}
            ),
            **({"location_ref": item.location_ref} if item.location_ref else {}),
            **({"what_happened": item.what_happened} if item.what_happened else {}),
            **({"hold_reason": item.hold_reason} if item.hold_reason else {}),
            **({"family": item.family} if item.family else {}),
            **({"candidate_id": item.candidate_id} if item.candidate_id else {}),
            **({"taxonomy": item.taxonomy} if item.taxonomy else {}),
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
    "install_shareable_photos_context",
    "inventory_material",
    "settlement_has_available_photo",
    "shareable_photo_facts",
    "shareable_photo_slice_items",
]
