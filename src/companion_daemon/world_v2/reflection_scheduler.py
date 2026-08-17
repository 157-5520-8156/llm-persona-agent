"""Deterministic reflection scheduling for significant life appraisals.

The role model already owns appraisal, affect, and aspiration formation in
``world_stimulus``.  This scheduler is the algorithm side of the reflection
root cause: it decides *when* a committed original wound may be thought about
again.  It never chooses the reflection's content — that stays with the
character.  Revisits bind to the original ``AppraisalAccepted``, not to a
later residue she authored while thinking about it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import json

from .errors import ConcurrencyConflict, IdempotencyConflict
from .event_identity import domain_idempotency_key
from .schema_core import FrozenModel
from .schemas import ProjectionCursor, TriggerProcess, WorldEvent


# What comes back to her is the heaviest thing she is currently carrying, on her
# own scale.  A fixed 8_500 bar sat above the whole range she actually uses: four
# recorded conversations produced weights of 4_500 through 7_000 and never once
# crossed it, so nothing was ever revisited and "it keeps coming back to me" was
# unreachable no matter what she chose.  The bar that replaced it is her own live
# maximum, which invents no significance she did not assign and stays naturally
# bounded to one wound.
#
# The absolute floor is the weight the host assumes when she does not weigh a
# reading at all: a turn she never marked must never schedule her to think about
# it again.  The self-perpetuating loop that once justified a high constant is
# closed structurally instead, by `_is_reflection_residue` (a reflection's own
# appraisal cannot open a new wound), one opening per pass, and widening gaps.
REFLECTION_UNWEIGHTED_BP = 5_000
# Growing gaps between thoughts about the same original wound.  Timing is a
# system Occasion rhythm, not a cap on how she may feel or act.
_REVISIT_GAPS = (
    timedelta(hours=1),
    timedelta(hours=3),
    timedelta(hours=12),
    timedelta(hours=24),
)

_PROCESS_KIND = "life_reflection"
_SOURCE_EVENT_TYPE = "AppraisalAccepted"


class ReflectionSchedulerResult(FrozenModel):
    opened: int = 0
    skipped: int = 0
    reason: str | None = None


def reflection_revisit_gap(visit_count: int) -> timedelta:
    if visit_count < 1:
        raise ValueError("visit_count must be at least 1")
    index = min(visit_count - 1, len(_REVISIT_GAPS) - 1)
    return _REVISIT_GAPS[index]


def reflection_trigger_ref(source_ref: str) -> str:
    return f"reflection:{source_ref}"


def reflection_trigger_id(source_ref: str, ordinal: int) -> str:
    if ordinal < 1:
        raise ValueError("reflection ordinal must be at least 1")
    if ordinal == 1:
        return reflection_trigger_ref(source_ref)
    return f"reflection:{source_ref}:{ordinal}"


def reflection_opened_event_id(source_ref: str, ordinal: int) -> str:
    if ordinal < 1:
        raise ValueError("reflection ordinal must be at least 1")
    if ordinal == 1:
        return f"event:life-reflection:opened:{source_ref}"
    return f"event:life-reflection:opened:{source_ref}:{ordinal}"


class ReflectionScheduler:
    """Open bounded reflection triggers for significant accepted appraisals."""

    def __init__(
        self,
        *,
        ledger,
        actor: str,
        unweighted_bp: int = REFLECTION_UNWEIGHTED_BP,
        source: str = "world-v2:reflection-scheduler",
    ) -> None:
        if not actor:
            raise ValueError("reflection scheduler requires an actor")
        if not 0 <= unweighted_bp <= 10_000:
            raise ValueError("reflection scheduler bounds are invalid")
        self._ledger = ledger
        self._actor = actor
        self._unweighted = unweighted_bp
        self._source = source

    def open_once(
        self,
        *,
        trace_id: str,
        correlation_id: str,
    ) -> ReflectionSchedulerResult:
        """Open at most one reflection trigger for the strongest eligible wound.

        Algorithmic gating only: intensity threshold, growing revisit gap, and
        original-wound identity.  The character owns the reflection.
        """

        projection = self._ledger.project()
        logical_time = projection.logical_time
        if logical_time is None:
            return ReflectionSchedulerResult(skipped=0, reason="no_logical_time")
        processes_by_source: dict[str, list] = {}
        for process in projection.trigger_processes:
            if process.process_kind != _PROCESS_KIND:
                continue
            source_ref = process.source_evidence_ref
            if not source_ref:
                continue
            processes_by_source.setdefault(source_ref, []).append(process)

        bar = self._revisit_bar(projection, logical_time)
        if bar is None:
            return ReflectionSchedulerResult(skipped=0, reason="nothing_weighed")
        candidates: list[tuple[int, str, int]] = []
        for appraisal in projection.appraisals:
            if not self._is_live_wound(appraisal, logical_time, bar):
                continue
            source_ref = appraisal.origin.accepted_event_ref
            if source_ref in processes_by_source:
                continue
            if self._is_reflection_residue(source_ref):
                continue
            candidates.append((appraisal.confidence_bp, source_ref, 1))

        for source_ref, processes in processes_by_source.items():
            if any(getattr(process, "state", None) != "terminal" for process in processes):
                continue
            visit_count = len(processes)
            if not self._revisit_interval_elapsed(
                source_ref=source_ref,
                visit_count=visit_count,
                now=logical_time,
            ):
                continue
            appraisal = self._appraisal_for_source(projection, source_ref)
            if appraisal is None or not self._is_live_wound(
                appraisal, logical_time, bar
            ):
                continue
            candidates.append((appraisal.confidence_bp, source_ref, visit_count + 1))

        candidates.sort(key=lambda item: item[0], reverse=True)
        for _confidence, source_ref, ordinal in candidates:
            if self._open_trigger(
                source_ref=source_ref,
                ordinal=ordinal,
                logical_time=logical_time,
                projection=projection,
                trace_id=trace_id,
                correlation_id=correlation_id,
            ):
                return ReflectionSchedulerResult(opened=1)
        return ReflectionSchedulerResult(skipped=len(candidates))

    def _revisit_bar(self, projection, logical_time: datetime) -> int | None:
        """The heaviest weight she is currently carrying, or None if she carried none.

        Reflection residue is excluded so a reflection's own appraisal cannot
        raise the bar it would then be measured against.
        """

        weights = [
            appraisal.confidence_bp
            for appraisal in projection.appraisals
            if getattr(appraisal, "status", None) == "active"
            and appraisal.expires_at > logical_time
            and appraisal.confidence_bp > self._unweighted
            and not self._is_reflection_residue(appraisal.origin.accepted_event_ref)
        ]
        return max(weights) if weights else None

    def _is_live_wound(self, appraisal, logical_time: datetime, bar: int) -> bool:
        return (
            getattr(appraisal, "status", None) == "active"
            and appraisal.expires_at > logical_time
            and appraisal.confidence_bp >= bar
        )

    def _appraisal_for_source(self, projection, source_ref: str):
        for appraisal in projection.appraisals:
            if appraisal.origin.accepted_event_ref == source_ref:
                return appraisal
        return None

    def _is_reflection_residue(self, source_ref: str) -> bool:
        located = self._ledger.lookup_event_commit(source_ref)
        if located is None:
            return False
        event = located[0]
        try:
            payload = json.loads(event.payload_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        trigger_id = payload.get("trigger_id")
        return isinstance(trigger_id, str) and trigger_id.startswith("reflection:")

    def _revisit_interval_elapsed(
        self,
        *,
        source_ref: str,
        visit_count: int,
        now: datetime,
    ) -> bool:
        located = self._ledger.lookup_event_commit(
            reflection_opened_event_id(source_ref, visit_count)
        )
        if located is None:
            return False
        last_at = located[0].logical_time
        if last_at is None:
            return False
        return now >= last_at + reflection_revisit_gap(visit_count)

    def _open_trigger(
        self,
        *,
        source_ref: str,
        ordinal: int,
        logical_time: datetime,
        projection,
        trace_id: str,
        correlation_id: str,
    ) -> bool:
        process = TriggerProcess(
            trigger_id=reflection_trigger_id(source_ref, ordinal),
            trigger_ref=reflection_trigger_ref(source_ref),
            process_kind=_PROCESS_KIND,
            source_evidence_ref=source_ref,
            state="open",
        )
        event = WorldEvent.from_payload(
            schema_version="world-v2.1",
            event_id=reflection_opened_event_id(source_ref, ordinal),
            world_id=self._ledger.world_id,
            event_type="TriggerProcessOpened",
            logical_time=logical_time,
            created_at=logical_time,
            actor=self._actor,
            source=self._source,
            trace_id=trace_id,
            causation_id=source_ref,
            correlation_id=correlation_id,
            idempotency_key=(
                domain_idempotency_key(
                    event_type="TriggerProcessOpened",
                    world_id=self._ledger.world_id,
                    payload={"process": process.model_dump(mode="json")},
                )
                or f"life-reflection-opened:{source_ref}:{ordinal}"
            ),
            payload={"process": process.model_dump(mode="json")},
        )
        try:
            self._ledger.commit_at_cursor(
                (event,),
                expected_cursor=ProjectionCursor(
                    world_revision=projection.world_revision,
                    deliberation_revision=projection.deliberation_revision,
                    ledger_sequence=projection.ledger_sequence,
                ),
                commit_id="commit:" + event.event_id,
            )
        except (ConcurrencyConflict, IdempotencyConflict):
            return False
        return True


__all__ = [
    "REFLECTION_UNWEIGHTED_BP",
    "ReflectionScheduler",
    "ReflectionSchedulerResult",
    "reflection_opened_event_id",
    "reflection_revisit_gap",
    "reflection_trigger_id",
    "reflection_trigger_ref",
]
