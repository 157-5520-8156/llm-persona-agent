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
from ..schemas import ProjectionCursor


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

    def __init__(self, *, ledger, identities_for_projection):
        self._ledger = ledger
        self._identities_for_projection = identities_for_projection
        self._failures: dict[str, _FailedAttempt] = {}
        self.unresolved_attempt_ids: frozenset[str] = frozenset()
        self._reported_unresolved: set[str] = set()

    def deferred_opportunities(self, projection, *, attempt_ids: frozenset[str]) -> frozenset[str]:
        attempts: dict[str, dict[str, datetime]] = {}
        unresolved: set[str] = set()
        for item in projection.model_result_audits:
            if item.attempt_id not in attempt_ids:
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
        event, commit = located
        historical = self._ledger.project_at(ProjectionCursor(
            world_revision=commit.world_revision,
            deliberation_revision=commit.deliberation_revision,
            ledger_sequence=commit.ledger_sequence,
        ))
        process = next((
            process for process in historical.trigger_processes
            if process.source_evidence_ref == item.trigger_ref
            and item.attempt_id in process.attempt_ids
        ), None)
        if process is None:
            raise ValueError("world stimulus retry lacks its exact claimed source")
        identities = self._identities_for_projection(historical)
        identity = identities.get(process.trigger_id)
        if identity is None:
            raise ValueError("world stimulus retry lacks its causal opportunity")
        failed = _FailedAttempt(identity.opportunity_ref, item.attempt_id, event.logical_time)
        self._failures[item.event_ref] = failed
        return failed

