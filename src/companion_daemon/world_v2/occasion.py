"""Occasion identity: when the character may consider, and only once.

This is a system timing seam, not a behavior script.  It does not choose what
she says, feels, or does.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from .schema_core import FrozenModel


OCCASION_KINDS = (
    "user_message",
    "quiet_gap",
    "unsettled_feeling",
    "life_beat",
    "day_open",
)
OccasionKind = Literal[
    "user_message",
    "quiet_gap",
    "unsettled_feeling",
    "life_beat",
    "day_open",
]


class OccasionAlreadyConsidered(ValueError):
    """G2: the same Occasion identity already spent its one consider()."""


class OccasionExpired(ValueError):
    """G7: a stale Occasion must be dropped, not backfilled after downtime."""


class OccasionIdentity(FrozenModel):
    kind: OccasionKind
    source_event_ref: str
    merge_key: str
    expires_at: datetime | None = None

    @property
    def occasion_id(self) -> str:
        return f"occasion:{self.kind}:{self.merge_key}"


class OccasionConsiderGate:
    """Process-local one-consider ledger. Durable effect-once stays on the turn."""

    def __init__(self) -> None:
        self._spent: set[str] = set()

    def admit(self, occasion_id: str) -> None:
        if not occasion_id:
            raise ValueError("occasion identity is required")
        if occasion_id in self._spent:
            raise OccasionAlreadyConsidered(occasion_id)

    def mark_spent(self, occasion_id: str) -> None:
        if not occasion_id:
            raise ValueError("occasion identity is required")
        self._spent.add(occasion_id)

    def already_spent(self, occasion_id: str) -> bool:
        return occasion_id in self._spent


def occasion_is_expired(
    *,
    now: datetime,
    expires_at: datetime | None,
) -> bool:
    return expires_at is not None and now >= expires_at


def quiet_gap_expires_at(*, created_at: datetime, expiry_seconds: int) -> datetime:
    return created_at + timedelta(seconds=expiry_seconds)


def newly_accepted_head_refs(
    committed_world_event_refs: tuple[object, ...] | list[object],
    *,
    event_type: str,
) -> frozenset[str]:
    """G7: derive candidates from the head event only, never a historical table."""

    if not committed_world_event_refs:
        return frozenset()
    head = committed_world_event_refs[-1]
    if getattr(head, "event_type", None) != event_type:
        return frozenset()
    event_id = getattr(head, "event_id", None)
    if not isinstance(event_id, str) or not event_id:
        return frozenset()
    return frozenset({event_id})
