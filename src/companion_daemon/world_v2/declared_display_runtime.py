"""Commit character-authored display intent through the existing declaration seam.

Mirrors ``RecipientScopedImageEvidenceDeclarationRuntime``: the caller names a
committed inbound observation; the runtime re-reads that event's actor as the
recipient so a model-authored ref cannot enter the ledger.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .declared_display_contract import (
    DECLARED_DISPLAY_HITCH_ADVISORY_KIND,
    DECLARED_DISPLAY_HITCH_RETRYABLE_REASONS,
    DECLARED_DISPLAY_HITCH_TERMINAL_REASONS,
    DECLARED_DISPLAY_RECORDED,
    DECLARED_DISPLAY_SOURCE_EVENT_TYPE,
    DECLARED_DISPLAY_WITHDRAWN,
    DeclaredDisplayRecordedPayload,
    DeclaredDisplayWithdrawnPayload,
)
from .errors import ConcurrencyConflict, IdempotencyConflict
from .event_identity import domain_idempotency_key
from .relationship_media_context import DeclaredMediaIntentV1
from .schema_core import FrozenModel
from .schemas import CommitResult, ProjectionCursor, WorldEvent


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


class DeclaredDisplayHitchResult(FrozenModel):
    """Visible outcome of one paid inbound display hitch. Never silent."""

    outcome: Literal[
        "omitted",
        "not_delivered",
        "landed",
        "retryable",
        "rejected",
        "failed",
    ]
    reason_code: str | None = Field(default=None, exclude_if=lambda value: value is None)
    event_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    attempts: int = Field(default=0, ge=0)


def classify_declared_display_hitch_failure(exc: BaseException) -> tuple[str, bool]:
    """Map a hitch exception to a reason code and whether another attempt is legal.

    Independent of the hitch loop: tests and a later reducer re-proof can call
    this without trusting the worker's own label. Exhausted retryable reasons
    stay retryable; they are not rewritten as terminals.
    """

    if isinstance(exc, ConcurrencyConflict):
        return "concurrency_conflict", True
    if isinstance(exc, IdempotencyConflict):
        return "idempotency_conflict", True
    message = str(exc)
    if "logical clock is unavailable" in message:
        return "logical_clock_unavailable", True
    if "must use the current logical clock" in message:
        return "logical_clock_mismatch", True
    if "source is unavailable" in message or "source event is unavailable" in message:
        return "source_unavailable", True
    if "source bytes are unavailable" in message:
        return "source_bytes_unavailable", True
    if "recipient must be bound" in message:
        return "recipient_not_user_bound", False
    return "host_failure", False


def validate_declared_display_hitch_terminal(
    *,
    reason_code: str,
    ledger: object,
    source_event_ref: str,
    observation_actor: str,
) -> None:
    """Re-prove a named hitch terminal from current ledger bytes.

    Mirrors the typed-change terminal: the allowlist is closed, and a retryable
    conflict cannot be recorded as rejected. The reducer is not extended here;
    hitch and tests call this before writing ``AdvisoryAcceptanceRejected``.
    """

    if reason_code in DECLARED_DISPLAY_HITCH_RETRYABLE_REASONS:
        raise ValueError("declared display hitch retryable reason is not a terminal")
    if reason_code not in DECLARED_DISPLAY_HITCH_TERMINAL_REASONS:
        raise ValueError("declared display hitch terminal reason is not installed")
    if reason_code == "recipient_not_user_bound":
        if observation_actor.startswith("user:"):
            raise ValueError("declared display hitch recipient is user-bound")
        return
    projection = ledger.project()  # type: ignore[union-attr]
    source_ref = next(
        (
            item
            for item in getattr(projection, "committed_world_event_refs", ())
            if getattr(item, "event_id", None) == source_event_ref
        ),
        None,
    )
    located = ledger.lookup_event_commit(source_event_ref)  # type: ignore[union-attr]
    if reason_code == "source_unavailable":
        if source_ref is not None and located is not None:
            raise ValueError("declared display hitch source is available")
        return
    if located is None or source_ref is None:
        raise ValueError("declared display hitch source is missing, not mismatched")
    event = located[0]
    if (
        event.event_type == DECLARED_DISPLAY_SOURCE_EVENT_TYPE
        and event.event_type == source_ref.event_type
        and event.payload_hash == source_ref.payload_hash
    ):
        raise ValueError("declared display hitch source bytes are available")


def declared_display_hitch_terminal_payload(
    *,
    proposal_id: str,
    source_event_ref: str,
    reason_code: str,
    failure_fingerprint: str,
) -> dict[str, str]:
    if reason_code not in DECLARED_DISPLAY_HITCH_TERMINAL_REASONS:
        raise ValueError("declared display hitch terminal reason is not installed")
    return {
        "proposal_id": proposal_id,
        "source_event_ref": source_event_ref,
        "advisory_kind": DECLARED_DISPLAY_HITCH_ADVISORY_KIND,
        "stage": "rejected",
        "reason_code": reason_code,
        "failure_fingerprint": failure_fingerprint,
    }


class DeclaredDisplayCommand(FrozenModel):
    """Request one declare or withdraw from an already committed observation."""

    command_id: str = Field(min_length=1, max_length=256)
    source_event_ref: str = Field(min_length=1, max_length=512)
    media_intent: DeclaredMediaIntentV1 | None = None
    withdraw: bool = False
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def intent_and_withdraw_are_exclusive(self) -> "DeclaredDisplayCommand":
        if self.withdraw:
            if self.media_intent is not None or self.expires_at is not None:
                raise ValueError("withdraw cannot carry media_intent or expires_at")
            return self
        if self.media_intent is None:
            raise ValueError("declared display requires media_intent or withdraw")
        return self


class DeclaredDisplayRuntime:
    """Resolve recipient from the observation actor, then commit the declaration."""

    def __init__(self, *, ledger, source: str = "world-v2:declared-display") -> None:  # type: ignore[no-untyped-def]
        self._ledger, self._source = ledger, source

    def declare(
        self,
        command: DeclaredDisplayCommand,
        *,
        logical_time: datetime,
        created_at: datetime,
        actor: str,
        trace_id: str,
        correlation_id: str,
        prior_event_ref: str | None = None,
    ) -> CommitResult:
        projection = self._ledger.project()
        if projection.logical_time != logical_time:
            raise ValueError("declared display must use the current logical clock")
        source_ref = next(
            (
                item
                for item in projection.committed_world_event_refs
                if item.event_id == command.source_event_ref
            ),
            None,
        )
        if source_ref is None:
            raise ValueError("declared display source is unavailable")
        located = self._ledger.lookup_event_commit(command.source_event_ref)
        if located is None:
            raise ValueError("declared display source event is unavailable")
        source_event, _source_commit = located
        if (
            source_event.event_type != DECLARED_DISPLAY_SOURCE_EVENT_TYPE
            or source_event.event_type != source_ref.event_type
            or source_event.payload_hash != source_ref.payload_hash
        ):
            raise ValueError("declared display source bytes are unavailable")
        recipient_ref = _observation_actor(source_event)
        if command.withdraw:
            event_type: Literal["DeclaredDisplayRecorded", "DeclaredDisplayWithdrawn"] = (
                DECLARED_DISPLAY_WITHDRAWN
            )
            payload = DeclaredDisplayWithdrawnPayload(
                source_event_ref=source_event.event_id,
                source_event_payload_hash=source_event.payload_hash,
                source_event_type=DECLARED_DISPLAY_SOURCE_EVENT_TYPE,
                recipient_ref=recipient_ref,
                withdrawn_at=logical_time,
                prior_event_ref=prior_event_ref,
            ).model_dump(mode="json")
        else:
            if command.media_intent is None:
                raise ValueError("declared display requires media_intent or withdraw")
            event_type = DECLARED_DISPLAY_RECORDED
            payload = DeclaredDisplayRecordedPayload(
                source_event_ref=source_event.event_id,
                source_event_payload_hash=source_event.payload_hash,
                source_event_type=DECLARED_DISPLAY_SOURCE_EVENT_TYPE,
                recipient_ref=recipient_ref,
                media_intent=command.media_intent,
                declared_at=logical_time,
                expires_at=command.expires_at,
            ).model_dump(mode="json")
        event = WorldEvent.from_payload(
            schema_version="world-v2.1",
            event_id="event:declared-display:" + _digest(
                {"world_id": self._ledger.world_id, "command_id": command.command_id, "payload": payload}
            ),
            event_type=event_type,
            world_id=self._ledger.world_id,
            logical_time=logical_time,
            created_at=created_at,
            actor=actor,
            source=self._source,
            trace_id=trace_id,
            causation_id=source_event.event_id,
            correlation_id=correlation_id,
            idempotency_key=domain_idempotency_key(
                event_type=event_type, world_id=self._ledger.world_id, payload=payload
            )
            or "declared-display:" + _digest(payload),
            payload=payload,
        )
        cursor = ProjectionCursor(
            world_revision=projection.world_revision,
            deliberation_revision=projection.deliberation_revision,
            ledger_sequence=projection.ledger_sequence,
        )
        return self._ledger.commit_at_cursor(
            (event,),
            expected_cursor=cursor,
            commit_id="commit:declared-display:" + _digest(
                {"cursor": cursor.model_dump(mode="json"), "event_id": event.event_id}
            ),
        )


def _observation_actor(source_event: WorldEvent) -> str:
    payload = source_event.payload()
    actor = payload.get("actor") if isinstance(payload, dict) else None
    if not isinstance(actor, str) or not actor.startswith("user:"):
        raise ValueError("declared display recipient must be bound from the observation actor")
    return actor


__all__ = [
    "DeclaredDisplayCommand",
    "DeclaredDisplayHitchResult",
    "DeclaredDisplayRuntime",
    "classify_declared_display_hitch_failure",
    "declared_display_hitch_terminal_payload",
    "validate_declared_display_hitch_terminal",
]
