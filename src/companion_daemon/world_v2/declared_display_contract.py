"""Character-authored, recipient-scoped display intent for P3 adult lanes.

This follows ``RecipientScopedImageEvidenceDeclared``: a typed declaration
event, catalogued and reduced, whose bytes stay on the ledger.  It is not
visual evidence and not adult authorization.  His capability/consent pair
still opens the possibility; this event is only *her* intensity, bound to
the inbound observation's actor so she never writes ``recipient_ref``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .relationship_media_context import DeclaredDisplayV1, DeclaredMediaIntentV1
from .schema_core import FrozenModel


DECLARED_DISPLAY_SOURCE_EVENT_TYPE = "ObservationRecorded"
DECLARED_DISPLAY_RECORDED = "DeclaredDisplayRecorded"
DECLARED_DISPLAY_WITHDRAWN = "DeclaredDisplayWithdrawn"
DECLARED_DISPLAY_EVENT_TYPES = frozenset(
    {DECLARED_DISPLAY_RECORDED, DECLARED_DISPLAY_WITHDRAWN}
)
DECLARED_DISPLAY_HITCH_ADVISORY_KIND = "declared_display"
DECLARED_DISPLAY_HITCH_CAS_ATTEMPTS = 8
# Retryable reasons stay retryable after exhaustion.  They must never be
# recorded as a typed terminal just to keep a log line quiet.
DECLARED_DISPLAY_HITCH_RETRYABLE_REASONS = frozenset(
    {
        "concurrency_conflict",
        "idempotency_conflict",
        "logical_clock_unavailable",
        "logical_clock_mismatch",
    }
)
# Durable host refusals that a reducer or hitch can re-prove from current
# bytes.  Exhausted CAS is not in this set.
DECLARED_DISPLAY_HITCH_TERMINAL_REASONS = frozenset(
    {
        "source_unavailable",
        "source_bytes_unavailable",
        "recipient_not_user_bound",
    }
)


class DeclaredDisplayRecordedPayload(FrozenModel):
    """One standing display intent, sourced from a committed inbound observation."""

    source_event_ref: str = Field(min_length=1, max_length=512)
    source_event_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_event_type: Literal["ObservationRecorded"] = DECLARED_DISPLAY_SOURCE_EVENT_TYPE
    recipient_ref: str = Field(min_length=1, max_length=256)
    media_intent: DeclaredMediaIntentV1
    kind: Literal["recipient_directed"] = "recipient_directed"
    declared_at: datetime
    expires_at: datetime | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def recipient_is_host_bound_user(self) -> "DeclaredDisplayRecordedPayload":
        if not self.recipient_ref.startswith("user:"):
            raise ValueError("declared display recipient must be a host-bound user ref")
        if self.expires_at is not None and self.expires_at <= self.declared_at:
            raise ValueError("declared display expiry must be after declaration")
        return self

    def as_declared_display(self, *, event_id: str) -> DeclaredDisplayV1:
        return DeclaredDisplayV1(
            event_id=event_id,
            recipient_ref=self.recipient_ref,
            media_intent=self.media_intent,
        )


class DeclaredDisplayWithdrawnPayload(FrozenModel):
    """Retract the live display intent for one recipient."""

    source_event_ref: str = Field(min_length=1, max_length=512)
    source_event_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_event_type: Literal["ObservationRecorded"] = DECLARED_DISPLAY_SOURCE_EVENT_TYPE
    recipient_ref: str = Field(min_length=1, max_length=256)
    withdrawn_at: datetime
    prior_event_ref: str | None = Field(
        default=None, min_length=1, max_length=512, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def recipient_is_host_bound_user(self) -> "DeclaredDisplayWithdrawnPayload":
        if not self.recipient_ref.startswith("user:"):
            raise ValueError("declared display recipient must be a host-bound user ref")
        return self


DECLARED_DISPLAY_PAYLOAD_MODELS = {
    DECLARED_DISPLAY_RECORDED: DeclaredDisplayRecordedPayload,
    DECLARED_DISPLAY_WITHDRAWN: DeclaredDisplayWithdrawnPayload,
}


def live_declared_display(
    *,
    ledger,
    projection: object,
    recipient_ref: str,
    at_logical_time: datetime,
) -> DeclaredDisplayV1 | None:
    """Replay standing declare/withdraw events; latest live intent for this recipient wins.

    An unreadable display event fail-closes that recipient's standing grant.
    Omission (no live event) is the common legal case and returns None.
    """

    live: DeclaredDisplayV1 | None = None
    for ref in getattr(projection, "committed_world_event_refs", ()):
        event_type = getattr(ref, "event_type", None)
        if event_type not in DECLARED_DISPLAY_EVENT_TYPES:
            continue
        event_id = getattr(ref, "event_id", None)
        payload_hash = getattr(ref, "payload_hash", None)
        if not isinstance(event_id, str) or not isinstance(payload_hash, str):
            live = None
            continue
        located = ledger.lookup_event_commit(event_id)
        if located is None or located[0].payload_hash != payload_hash:
            live = None
            continue
        event = located[0]
        if event.event_type == DECLARED_DISPLAY_WITHDRAWN:
            try:
                withdrawn = DeclaredDisplayWithdrawnPayload.model_validate_json(
                    event.payload_json
                )
            except ValueError:
                live = None
                continue
            if withdrawn.recipient_ref == recipient_ref:
                live = None
            continue
        try:
            recorded = DeclaredDisplayRecordedPayload.model_validate_json(event.payload_json)
        except ValueError:
            live = None
            continue
        if recorded.recipient_ref != recipient_ref:
            continue
        if recorded.expires_at is not None and at_logical_time >= recorded.expires_at:
            continue
        live = recorded.as_declared_display(event_id=event.event_id)
    return live


__all__ = [
    "DECLARED_DISPLAY_EVENT_TYPES",
    "DECLARED_DISPLAY_HITCH_ADVISORY_KIND",
    "DECLARED_DISPLAY_HITCH_CAS_ATTEMPTS",
    "DECLARED_DISPLAY_HITCH_RETRYABLE_REASONS",
    "DECLARED_DISPLAY_HITCH_TERMINAL_REASONS",
    "DECLARED_DISPLAY_PAYLOAD_MODELS",
    "DECLARED_DISPLAY_RECORDED",
    "DECLARED_DISPLAY_SOURCE_EVENT_TYPE",
    "DECLARED_DISPLAY_WITHDRAWN",
    "DeclaredDisplayRecordedPayload",
    "DeclaredDisplayWithdrawnPayload",
    "live_declared_display",
]
