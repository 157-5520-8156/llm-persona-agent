"""Derive model retry due times from the existing immutable failure journal.

No retry state or character outcome is written here. The opportunity at the
failure's committed cursor owns the failure, so a changed source group cannot
inherit an old group's backoff. Delays reuse the Life post-processing policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import logging

from ..contextual_life_retry import CONTEXTUAL_LIFE_RETRY_DELAYS_SECONDS
from ..errors import LedgerIntegrityError


_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class _FailedAttempt:
    opportunity_ref: str
    attempt_id: str
    failed_at: datetime


class WorldStimulusRetrySchedule:
    """Read-only, replayable capped backoff for paid WorldStimulus attempts.

    The cache contains only derivations of immutable events, never retry
    authority. Closing/reopening SQLite reconstructs exactly the same due time.
    """

    def __init__(self, *, ledger, identity_for_audit):
        self._ledger = ledger
        self._identity_for_audit = identity_for_audit
        self._failures: dict[str, _FailedAttempt] = {}
        self._unreadable_event_refs: set[str] = set()
        self.unresolved_attempt_ids: frozenset[str] = frozenset()
        self._reported_unresolved: set[str] = set()

    def deferred_opportunities(self, projection, *, attempt_ids: frozenset[str]) -> frozenset[str]:
        attempts: dict[str, dict[str, datetime]] = {}
        unresolved: set[str] = set()
        for item in projection.model_result_audits:
            if item.attempt_id not in attempt_ids:
                continue
            if item.event_ref in self._unreadable_event_refs:
                unresolved.add(item.attempt_id)
                continue
            audit = json.loads(item.audit_json)
            if (
                audit.get("route", {}).get("reason_code")
                != "world_stimulus_appraisal.technical_failure"
                or audit.get("failure_code") is None
            ):
                continue
            try:
                failed = self._failure(item, projection)
            except LedgerIntegrityError:
                # A corrupt/unreplayable historical prefix is immutable from
                # this runtime's perspective.  Quarantine only this audit for
                # the process lifetime; repeatedly replaying genesis for the
                # same failed identity both blocks unrelated work on the
                # ledger lock and cannot make the old bytes valid.  A restart
                # with a repaired reader naturally retries reconstruction.
                self._unreadable_event_refs.add(item.event_ref)
                unresolved.add(item.attempt_id)
                if item.event_ref not in self._reported_unresolved:
                    _LOG.warning(
                        "world stimulus retry_identity_unavailable event=%s attempt=%s",
                        item.event_ref, item.attempt_id,
                    )
                    self._reported_unresolved.add(item.event_ref)
                continue
            except (ValueError, KeyError):
                # An unreadable historical identity cannot authorize a paid
                # retry. Isolate its exact attempts, keeping other work live;
                # retry reconstruction next pass so repaired readers recover.
                unresolved.add(item.attempt_id)
                if item.event_ref not in self._reported_unresolved:
                    _LOG.warning(
                        "world stimulus retry_identity_unavailable event=%s attempt=%s",
                        item.event_ref, item.attempt_id,
                    )
                    self._reported_unresolved.add(item.event_ref)
                continue
            self._reported_unresolved.discard(item.event_ref)
            group = attempts.setdefault(failed.opportunity_ref, {})
            group[failed.attempt_id] = max(
                group.get(failed.attempt_id, failed.failed_at), failed.failed_at
            )
        self.unresolved_attempt_ids = frozenset(unresolved)
        if not attempts:
            return frozenset()
        now = projection.logical_time
        return frozenset(
            opportunity_ref
            for opportunity_ref, failures in attempts.items()
            if now is None
            or now < max(failures.values()) + timedelta(
                seconds=CONTEXTUAL_LIFE_RETRY_DELAYS_SECONDS[
                    min(len(failures), len(CONTEXTUAL_LIFE_RETRY_DELAYS_SECONDS)) - 1
                ]
            )
        )

    def _failure(self, item, projection) -> _FailedAttempt:
        cached = self._failures.get(item.event_ref)
        if cached is not None:
            return cached
        located = self._ledger.lookup_event_commit(item.event_ref)
        if located is None:
            raise ValueError("world stimulus retry lacks its immutable failure event")
        event, _commit = located
        if event.event_type != "ModelResultRecorded" or event.event_id != item.event_ref:
            raise ValueError("world stimulus retry failure reference is not a model audit")
        # The recorded audit carries the exact causal source set and epoch
        # used for this attempt. Rebuilding a historical projection here is
        # unnecessary and makes a retry depend on every reducer ever used by
        # the ledger, even though the immutable lineage has already been
        # validated in the current projection.
        identity = self._identity_for_audit(item)
        if identity is None or item.trigger_ref not in identity.source_refs:
            raise ValueError("world stimulus retry lacks its audited causal opportunity")
        process = next((
            process for process in projection.trigger_processes
            if process.source_evidence_ref == item.trigger_ref
            and item.attempt_id in process.attempt_ids
        ), None)
        if process is None:
            raise ValueError("world stimulus retry lacks its exact source process")
        failed = _FailedAttempt(identity.opportunity_ref, item.attempt_id, event.logical_time)
        self._failures[item.event_ref] = failed
        return failed
