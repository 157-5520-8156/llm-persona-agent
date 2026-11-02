"""Deterministic opener for the ``silence_appraisal`` inner-feeling lane.

After the companion's visible reply is delivered and the user stays quiet,
this module leaves at most one recoverable work opportunity anchored to that
expression's first visible execution receipt.  It never interprets the silence itself:
what the quiet means (missing them, unease, indifference, nothing) belongs to
the model behind the trigger consumer, and every authoritative value here is
derived from committed ledger state.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import hashlib
import json

from pydantic import Field

from .batch_invariants import silence_appraisal_trigger_identity
from .event_identity import domain_idempotency_key
from .ledger import LedgerPort
from .schema_core import FrozenModel
from .schemas import TriggerProcess, WorldEvent
from .response_expectation_view import answerable_receipt_history


SILENCE_APPRAISAL_TRIGGER_VERSION = "silence-appraisal-trigger.3"

# The silence anchor must be one of her own visible conversational messages.
# Media, tool, and perception receipts are provider plumbing, not something a
# person would sit with while waiting for an answer.
_VISIBLE_MESSAGE_ACTION_KINDS = frozenset({"reply", "followup", "proactive_message"})
_MAX_SILENCE_APPRAISAL_ORDINALS = 4
# (base_multiplier, jitter_span_multiplier, min_gap_from_prior_multiplier)
_SILENCE_STAGE_WINDOWS: tuple[tuple[int, int, int], ...] = (
    (1, 0, 0),
    (6, 3, 4),
    (18, 8, 10),
    (42, 12, 18),
)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def silence_appraisal_stage_threshold(
    anchor_event_ref: str, *, idle_seconds_threshold: int, ordinal: int = 1
) -> int:
    """Return the deterministic jittered idle threshold for ``ordinal`` (1-indexed).

    Ordinal 1 uses ``idle_seconds_threshold`` directly; later stages widen with
    a source-bound jitter window so half-day and cross-day silence can be
    re-appraised without a fixed mechanical clock.
    """

    if idle_seconds_threshold <= 0 or not 1 <= ordinal <= len(_SILENCE_STAGE_WINDOWS):
        raise ValueError("invalid silence appraisal stage parameters")
    base_mult, jitter_mult, _gap_mult = _SILENCE_STAGE_WINDOWS[ordinal - 1]
    base_seconds = base_mult * idle_seconds_threshold
    if jitter_mult <= 0:
        return base_seconds
    jitter_span = jitter_mult * idle_seconds_threshold
    jitter_draw = int(
        _digest({"anchor": anchor_event_ref, "ordinal": ordinal, "version": SILENCE_APPRAISAL_TRIGGER_VERSION})[:16],
        16,
    ) % (jitter_span + 1)
    return base_seconds + jitter_draw


class SilenceOpportunity(FrozenModel):
    """One deterministic, source-bound chance to appraise an unanswered reply."""

    trigger_id: str = Field(min_length=1)
    source_evidence_ref: str = Field(min_length=1)
    receipt_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    action_kind: str = Field(min_length=1)
    anchored_at: datetime
    idle_seconds: int = Field(ge=0)
    ordinal: int = Field(default=1, ge=1)


def answerable_expression_anchors(projection, *, actor_ref=None, target=None):
    """First visible receipt per expression, with current delivery evidence.

    Later verification and additional Beats do not constitute a new contact.
    Receipt failure removes delivery evidence; it says nothing about the user.
    """
    actions = {item.action_id: item for item in projection.actions}
    scoped = {key: a for key, a in actions.items()
              if a.kind in _VISIBLE_MESSAGE_ACTION_KINDS
              and (actor_ref is None or a.actor == actor_ref)
              and (target is None or a.target == target)}
    currently_answerable = answerable_receipt_history(projection)
    valid_groups = {scoped[key].expression_plan_id or key
                    for key in currently_answerable if key in scoped}
    # Current receipt validity decides whether the expression is answerable;
    # all its historical visible sends decide when that expression began.
    # Losing the first Beat's delivery evidence must not move it past a reply.
    refs = (e for e in projection.committed_world_event_refs
            if e.event_type == "ExecutionReceiptRecorded")
    groups = {}
    for ref, receipt in zip(refs, projection.execution_receipts, strict=True):
        action = scoped.get(receipt.action_id)
        if (action is None or receipt.observed_state not in {"provider_accepted", "delivered"}
            or ref.logical_time > projection.logical_time):
            continue
        key = action.expression_plan_id or action.action_id
        previous = groups.get(key)
        if key in valid_groups and (previous is None or ref.world_revision < previous.world_revision):
            groups[key] = ref
    return groups


def silence_appraisal_opportunity(
    projection, *, idle_seconds_threshold: int, actor_ref=None, target=None
) -> SilenceOpportunity | None:
    """Derive the one open-able silence anchor from committed projection state.

    Returns ``None`` unless all of the following hold: the companion's latest
    visible message receipt is acknowledged or delivered, no newer user message
    observation exists, the durable clock has moved past the idle threshold,
    and no silence trigger was ever opened for this expression.
    """

    if idle_seconds_threshold <= 0:
        return None
    logical_time = projection.logical_time
    if logical_time is None:
        return None
    groups = answerable_expression_anchors(projection, actor_ref=actor_ref, target=target)
    if not groups:
        return None
    group_id, anchor_ref = max(groups.items(), key=lambda item: item[1].world_revision)
    # Compare the user's reply with the expression's first visible send, not
    # its delayed terminal verification or a later Beat of the same plan.
    if any(item.world_revision > anchor_ref.world_revision
           for item in projection.message_observations):
        return None
    idle_seconds = int((logical_time - anchor_ref.logical_time).total_seconds())
    if idle_seconds < idle_seconds_threshold:
        return None
    refs = tuple(e for e in projection.committed_world_event_refs
                 if e.event_type == "ExecutionReceiptRecorded")
    receipts = dict(zip((e.event_id for e in refs), projection.execution_receipts, strict=True))
    actions = {a.action_id: a for a in projection.actions}
    group_receipt_refs = {ref for ref, receipt in receipts.items()
                          if receipt.action_id in actions
                          and (actions[receipt.action_id].expression_plan_id or receipt.action_id) == group_id}
    # Older ledgers keyed this work by any receipt. Preserve their completed
    # or pending work instead of reopening it under the canonical first send.
    non_anchor_old_ids = {
        silence_appraisal_trigger_identity(projection.world_id, ref)
        for ref in group_receipt_refs
        if ref != anchor_ref.event_id
    }
    if any(
        getattr(item, "trigger_id", None) in non_anchor_old_ids
        or (
            getattr(item, "process_kind", None) == "silence_appraisal"
            and getattr(item, "source_evidence_ref", None) in group_receipt_refs
            and getattr(item, "source_evidence_ref", None) != anchor_ref.event_id
        )
        for item in projection.trigger_processes
    ):
        return None
    anchor_base_id = silence_appraisal_trigger_identity(projection.world_id, anchor_ref.event_id)
    existing_anchor_processes = [
        item
        for item in projection.trigger_processes
        if getattr(item, "trigger_id", None) == anchor_base_id
        or str(getattr(item, "trigger_id", "") or "").startswith(f"{anchor_base_id}:")
        or (
            getattr(item, "process_kind", None) == "silence_appraisal"
            and getattr(item, "source_evidence_ref", None) == anchor_ref.event_id
        )
    ]
    if any(getattr(item, "state", None) != "terminal" for item in existing_anchor_processes):
        return None
    ordinal = len(existing_anchor_processes) + 1
    if ordinal > _MAX_SILENCE_APPRAISAL_ORDINALS:
        return None
    latest_inbound_rev = max(
        (item.world_revision for item in projection.message_observations),
        default=-1,
    )
    unanswered_refs = [
        ref for ref in groups.values() if ref.world_revision > latest_inbound_rev
    ]
    first_unanswered_ref = min(
        unanswered_refs, key=lambda item: item.world_revision, default=anchor_ref
    )
    cumulative_idle_seconds = max(
        idle_seconds,
        int((logical_time - first_unanswered_ref.logical_time).total_seconds()),
    )
    stage_threshold = silence_appraisal_stage_threshold(
        anchor_ref.event_id,
        idle_seconds_threshold=idle_seconds_threshold,
        ordinal=ordinal,
    )
    effective_idle_seconds = cumulative_idle_seconds if ordinal >= 2 else idle_seconds
    if effective_idle_seconds < stage_threshold:
        return None
    if ordinal >= 2:
        prior_trigger_id = silence_appraisal_trigger_identity(
            projection.world_id, anchor_ref.event_id, ordinal=ordinal - 1
        )
        prior_opened_event_id = "event:silence-appraisal:opened:" + _digest(
            {"world_id": projection.world_id, "trigger_id": prior_trigger_id}
        )
        prior_opened_ref = next(
            (
                e
                for e in projection.committed_world_event_refs
                if getattr(e, "event_id", None) == prior_opened_event_id
            ),
            None,
        )
        min_gap_seconds = _SILENCE_STAGE_WINDOWS[ordinal - 1][2] * idle_seconds_threshold
        if prior_opened_ref is not None and isinstance(
            getattr(prior_opened_ref, "logical_time", None), datetime
        ):
            if (logical_time - prior_opened_ref.logical_time).total_seconds() < min_gap_seconds:
                return None
    anchor_receipt = receipts[anchor_ref.event_id]
    trigger_id = silence_appraisal_trigger_identity(
        projection.world_id, anchor_ref.event_id, ordinal=ordinal
    )
    return SilenceOpportunity(
        trigger_id=trigger_id,
        source_evidence_ref=anchor_ref.event_id,
        receipt_id=anchor_receipt.receipt_id,
        action_id=anchor_receipt.action_id,
        action_kind=actions[anchor_receipt.action_id].kind,
        anchored_at=anchor_ref.logical_time,
        idle_seconds=idle_seconds,
        ordinal=ordinal,
    )


class SilenceAppraisalTriggerOpener:
    """Commit at most one ``TriggerProcessOpened`` per eligible silence anchor."""

    def __init__(
        self,
        *,
        ledger: LedgerPort,
        owner_id: str,
        idle_seconds_threshold: int,
        actor_ref: str | None = None,
        target: str | None = None,
        source: str = "world-v2:silence-appraisal-trigger-opener",
    ) -> None:
        if not owner_id or idle_seconds_threshold <= 0:
            raise ValueError("silence appraisal opener needs owner and positive idle threshold")
        self._ledger = ledger
        self._owner_id = owner_id
        self._idle_seconds_threshold = idle_seconds_threshold
        self._source = source
        self._actor_ref = actor_ref
        self._target = target

    async def open_once(self) -> str | None:
        """Open the current silence opportunity, returning its trigger id."""

        projection = await self._project()
        opportunity = silence_appraisal_opportunity(
            projection, idle_seconds_threshold=self._idle_seconds_threshold,
            actor_ref=self._actor_ref, target=self._target,
        )
        if opportunity is None:
            return None
        located = await self._lookup(opportunity.source_evidence_ref)
        if located is None or located[0].event_type != "ExecutionReceiptRecorded":
            raise ValueError("silence appraisal anchor authority is unavailable")
        source_event = located[0]
        process = TriggerProcess(
            trigger_id=opportunity.trigger_id,
            trigger_ref=f"silence:{opportunity.source_evidence_ref}",
            process_kind="silence_appraisal",
            source_evidence_ref=opportunity.source_evidence_ref,
            state="open",
        )
        payload = {"process": process.model_dump(mode="json")}
        identity = domain_idempotency_key(
            event_type="TriggerProcessOpened", world_id=self._ledger.world_id, payload=payload
        )
        if identity is None:
            raise ValueError("silence appraisal trigger has no domain identity")
        event = WorldEvent.from_payload(
            schema_version="world-v2.1",
            event_id="event:silence-appraisal:opened:"
            + _digest({"world_id": self._ledger.world_id, "trigger_id": opportunity.trigger_id}),
            world_id=self._ledger.world_id,
            event_type="TriggerProcessOpened",
            # The trigger opens when the durable clock crossed the threshold,
            # not retroactively at the anchor's own delivery instant.
            logical_time=projection.logical_time,
            created_at=source_event.created_at,
            actor=self._owner_id,
            source=self._source,
            trace_id=source_event.trace_id,
            causation_id=source_event.event_id,
            correlation_id=source_event.correlation_id,
            idempotency_key=identity,
            payload=payload,
        )
        await self._commit(
            (event,),
            world_revision=projection.world_revision,
            deliberation_revision=projection.deliberation_revision,
            commit_id="commit:silence-appraisal:opened:" + _digest(opportunity.trigger_id),
        )
        return opportunity.trigger_id

    async def _project(self):
        if self._ledger.blocks_event_loop:
            return await asyncio.to_thread(self._ledger.project)
        return self._ledger.project()

    async def _lookup(self, event_id: str):
        if self._ledger.blocks_event_loop:
            return await asyncio.to_thread(self._ledger.lookup_event_commit, event_id)
        return self._ledger.lookup_event_commit(event_id)

    async def _commit(self, events, *, world_revision, deliberation_revision, commit_id):
        if self._ledger.blocks_event_loop:
            return await asyncio.to_thread(
                self._ledger.commit,
                events,
                expected_world_revision=world_revision,
                expected_deliberation_revision=deliberation_revision,
                commit_id=commit_id,
            )
        return self._ledger.commit(
            events,
            expected_world_revision=world_revision,
            expected_deliberation_revision=deliberation_revision,
            commit_id=commit_id,
        )


__all__ = [
    "SILENCE_APPRAISAL_TRIGGER_VERSION",
    "SilenceAppraisalTriggerOpener",
    "SilenceOpportunity",
    "silence_appraisal_opportunity",
    "silence_appraisal_stage_threshold",
]
