"""Background producer for Private Impressions (CONTEXT.md).

A Private Impression is "the companion's fallible, source-bound
interpretation of a user, relationship, or event".  The typed authority
(``private_impression_transition`` proposal family, ``PrivateImpressionAccepted``
event, pure reducer, capsule ``private_impressions`` slice) has existed since
the impression reducers landed; this module adds the missing *producer*
vertical, following the interaction-fact worker's discipline:

* a deterministic opener leaves at most one recoverable trigger per accepted
  appraisal (the anchor is the committed ``AppraisalAccepted`` event);
* the unified Character Interior may *reflect on* already-accepted appraisal
  hypotheses and write its own tentative private understanding.  It selects
  the exact source hypotheses, confidence and lifecycle, while immutable
  source identities and evidence remain outside model authority;
* the runtime offers one source-bound capability and drives the existing typed
  acceptance authority.  The accepted
  impression then reaches later turns only through the capsule's private
  slice (privacy class ``withhold``); it is never shown to the user.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
from threading import Lock
from typing import Any, Callable, Literal
from weakref import WeakKeyDictionary
from zoneinfo import ZoneInfo

from pydantic import Field
from pydantic_core import to_jsonable_python

from .batch_invariants import private_impression_trigger_identity
from .occasion import newly_accepted_head_refs
from .companion_identity import CompanionIdentityFrame
from .character_interior import CharacterInterior, InteriorStimulus
from .character_interior.audit import (
    causal_opportunity_lineage_fields,
    recorded_character_interior_model_result,
    technical_character_interior_model_result,
)
from .character_interior.contracts import (
    _InteriorAuthorLineage,
    _InteriorCapabilityManifest,
)
from .character_interior.ports import _AuthorityRequest
from .character_interior.run_result import (
    CAUSAL_OPPORTUNITY_CONTRACT_VERSION,
    CausalOpportunityHealth,
    CausalOpportunityIdentity,
    CausalOpportunityPolicy,
    CausalOpportunityRuntime,
    CausalOpportunitySource,
    DEFAULT_CAUSAL_OPPORTUNITY_POLICY,
    causal_opportunity_policy_from_attempt_id,
)
from .event_identity import domain_idempotency_key
from .ledger import LedgerPort
from .life_content_store import ImmutableLifeContentStore
from .model_json import extract_json_object_text
from .private_impression_events import (
    PRIVATE_IMPRESSION_POLICY_REFS,
    PrivateImpressionPredecessorRef,
    offered_private_impression_reflection_bindings,
    private_impression_mutation_hash,
    private_impression_reflection_value_digest,
)
from .proposal_audit_schemas import (
    ModelResultRecordedPayload,
    ProposalRecordedV2Payload,
    RecordedModelResultAudit,
    RecordedModelRoute,
    RecordedCharacterInteriorTurnLineage,
    canonical_json,
    model_audit_json,
    sha256,
)
from .proposal_envelope import DecisionProposal
from .schema_core import FrozenModel
from .schemas import (
    AppraisalMeaningRef,
    AppraisalProjection,
    ClaimLease,
    EvidenceRef,
    PrivateImpressionOrigin,
    PrivateImpressionProjection,
    ProjectionCursor,
    TriggerProcess,
    WorldEvent,
)


EXPIRY_CONDITIONS = (
    "until_appraisal_contradicted",
    "until_counter_evidence",
    "until_relationship_stage_changes",
    "one_month_without_support",
)
PRIVATE_IMPRESSION_PURPOSE = "private_impression_reflection"
_NON_ATTEMPT_TECHNICAL_FAILURES = frozenset({"required_tool_choice_unsupported"})

# Bound the number of times one private-reflection trigger may be reclaimed
# after its model output failed authority validation.  Lease expiry alone
# would otherwise retry the same failing provider call forever, burning
# unbounded tokens on a reflection that never succeeds; after this many
# attempts the process is terminal. A fresh epoch requires new accepted
# appraisal evidence; the opener does not re-derive the same source trigger.
_PRIVATE_IMPRESSION_MAX_ATTEMPTS = 1

# Conservative production defaults for the independent reflection farm.
# The daily cap counts ledger farm *asks* (TriggerProcessCompleted with a
# model outcome), not accepted impressions and not HTTP reselections of the
# same ask.  Three asks a day at ~¥0.03 each stay inside a single-digit
# weekly increment; four hours between asks and 30 minutes after his last
# message keep the farm off the inbound hitch path.
DEFAULT_PRIVATE_IMPRESSION_DAILY_MODEL_CALL_LIMIT = 3
DEFAULT_PRIVATE_IMPRESSION_MIN_INTERVAL_SECONDS = 14_400
DEFAULT_PRIVATE_IMPRESSION_IDLE_AFTER_USER_SECONDS = 1_800
_PAID_INBOUND_MODEL_CALL_PREFIX = "paid-inbound-impression:"
_PAID_TURN_MODEL_ID_PREFIX = "paid-turn:"
_PAID_RECOVERY_REJECTED = "private_impression_paid_retention_recovery_rejected"
_PAID_RECOVERY_PREFIX_CHANGED = "private_impression_paid_retention_recovery_prefix_changed"


class _PaidRetentionPrefixChanged(ValueError):
    """A historical partial write cannot reuse its old evaluation authority."""

_PAID_RECOVERY_STORAGE_FAILED = "private_impression_paid_retention_recovery_storage_failed"
_PAID_RECOVERY_SCAN_LIMIT = 64


def _paid_recovery_now() -> datetime:
    return datetime.now(UTC)


def _paid_retention_attempt_id(source_ref: str, model_result_ref: str) -> str:
    return "attempt:paid-inbound-impression:" + _digest(
        {"source_event": source_ref, "model_result_ref": model_result_ref}
    )
_GATE_REASONS = (
    "daily_cap",
    "min_interval",
    "recent_user_observation",
    "disabled",
)
_GATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS world_v2_private_impression_gates (
    world_id TEXT NOT NULL,
    local_day TEXT NOT NULL,
    trigger_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    daily_calls INTEGER NOT NULL DEFAULT 0,
    daily_limit INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (world_id, local_day, trigger_id, reason)
)
"""
_LOG = logging.getLogger(__name__)
_GATE_LOCK = Lock()
_MEMORY_GATES: WeakKeyDictionary[object, list[dict[str, object]]] = WeakKeyDictionary()


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _paid_inbound_impression_lineage(
    *,
    inbound_model_result_ref: str,
    appraisal_id: str,
    inbound_audit,
    draft: PrivateImpressionDraft,
    attempt_id: str,
) -> tuple[_InteriorAuthorLineage, RecordedCharacterInteriorTurnLineage, str]:
    """Derive a 0-call reflection lineage parented to the paid inbound turn."""

    digest = _reflection_draft_digest(draft)
    parent = getattr(inbound_audit, "model_call_id", None) if inbound_audit is not None else None
    model_id = "paid-turn:inbound"
    model_version = "paid-turn:inbound"
    if inbound_audit is not None:
        try:
            nested = json.loads(inbound_audit.audit_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            nested = {}
        if isinstance(nested, dict):
            model_id = str(
                nested.get("model_id")
                or nested.get("attempted_model_id")
                or model_id
            ).strip() or model_id
            model_version = str(
                nested.get("model_version") or model_id
            ).strip() or model_id
    lineage = _InteriorAuthorLineage(
        model_id=model_id,
        model_version=model_version,
        model_call_id="paid-inbound-impression:"
        + _digest(
            {
                "inbound_model_result": inbound_model_result_ref,
                "appraisal_id": appraisal_id,
            }
        ),
        request_hash="sha256:" + digest,
        response_hash="sha256:" + digest,
        attempt_ordinal=1 if parent else 0,
        parent_model_call_id=parent,
    )
    interior = RecordedCharacterInteriorTurnLineage(
        inner_turn_id=attempt_id,
        purpose=PRIVATE_IMPRESSION_PURPOSE,
        opportunity_ref="paid-inbound-impression:" + appraisal_id,
        snapshot_id="inner-life-snapshot:sha256:" + digest,
        snapshot_hash=digest,
        capability_ref="capability:paid-inbound-impression",
        author_model_id=lineage.model_id,
        author_model_version=lineage.model_version,
        author_model_call_id=lineage.model_call_id,
        author_request_hash=lineage.request_hash,
        author_response_hash=lineage.response_hash,
        author_attempt_ordinal=lineage.attempt_ordinal,
        author_parent_model_call_id=lineage.parent_model_call_id,
        private_self_lineage_hash="sha256:" + digest,
        decision_hash="sha256:" + digest,
    )
    derived_ref = "model-result:" + _digest(
        {
            "model_call_id": lineage.model_call_id,
            "response_hash": lineage.response_hash.removeprefix("sha256:"),
        }
    )
    return lineage, interior, derived_ref




class PrivateImpressionDraft(FrozenModel):
    """One role-authored, non-factual reflection over accepted hypotheses."""

    decision: Literal["retain", "consolidate", "supersede", "release"] = "retain"
    predecessor_refs: tuple[str, ...] = ()
    source_refs: tuple[str, ...]
    reflection_summary: str = Field(min_length=1, max_length=1_200)
    confidence_bp: int
    expiry_condition: Literal[
        "until_appraisal_contradicted",
        "until_counter_evidence",
        "until_relationship_stage_changes",
        "one_month_without_support",
    ]


def _reflection_draft_digest(draft: PrivateImpressionDraft) -> str:
    return private_impression_reflection_value_digest(
        decision=draft.decision,
        predecessor_refs=draft.predecessor_refs,
        source_refs=draft.source_refs,
        reflection_summary=draft.reflection_summary,
        confidence_bp=draft.confidence_bp,
        expiry_condition=draft.expiry_condition,
    )


def compile_paid_private_impression_draft(
    *,
    reflection_summary: str,
    offered_source_refs: tuple[str, ...],
    keep_impression: bool | None = None,
) -> PrivateImpressionDraft | None:
    if keep_impression is not True:
        return None
    summary = reflection_summary.strip()[:1_200]
    sources = tuple(dict.fromkeys(item for item in offered_source_refs if item))
    if not summary or not sources:
        return None
    return PrivateImpressionDraft(
        decision="retain",
        predecessor_refs=(),
        source_refs=sources[:8],
        reflection_summary=summary,
        confidence_bp=5_000,
        expiry_condition="until_counter_evidence",
    )


class PrivateImpressionDrainPolicy(FrozenModel):
    """When the host may spend a provider call asking her to reflect.

    The character still owns retain / no_change.  These numbers only bound
    *whether the host pays to ask*, never what she answers.
    """

    daily_model_call_limit: int = Field(
        default=DEFAULT_PRIVATE_IMPRESSION_DAILY_MODEL_CALL_LIMIT, ge=0, le=24
    )
    min_interval_seconds: int = Field(
        default=DEFAULT_PRIVATE_IMPRESSION_MIN_INTERVAL_SECONDS, ge=0, le=86_400
    )
    idle_after_user_seconds: int = Field(
        default=DEFAULT_PRIVATE_IMPRESSION_IDLE_AFTER_USER_SECONDS, ge=0, le=86_400
    )
    local_timezone: str = "Asia/Shanghai"

    @property
    def allows_model_calls(self) -> bool:
        return self.daily_model_call_limit > 0


class PrivateImpressionGateDecision(FrozenModel):
    """One drain-tick decision: ask her, stay idle, or skip without a call."""

    action: Literal["ask", "idle", "skip"]
    reason: str | None = None
    daily_calls: int = 0
    daily_limit: int = 0
    trigger_id: str = ""
    last_user_observation_at: datetime | None = None
    last_background_call_at: datetime | None = None
    local_timezone: str = "Asia/Shanghai"


def private_impression_drain_policy_from_settings(
    settings: object | None = None,
) -> PrivateImpressionDrainPolicy:
    """Build the drain policy from Settings / explicit host overrides."""

    if settings is None:
        from companion_daemon.config import Settings

        settings = Settings()
    timezone_name = str(getattr(settings, "local_timezone", None) or "Asia/Shanghai")
    return PrivateImpressionDrainPolicy(
        daily_model_call_limit=int(
            getattr(
                settings,
                "world_v2_private_impression_daily_model_call_limit",
                DEFAULT_PRIVATE_IMPRESSION_DAILY_MODEL_CALL_LIMIT,
            )
        ),
        min_interval_seconds=int(
            getattr(
                settings,
                "world_v2_private_impression_min_interval_seconds",
                DEFAULT_PRIVATE_IMPRESSION_MIN_INTERVAL_SECONDS,
            )
        ),
        idle_after_user_seconds=int(
            getattr(
                settings,
                "world_v2_private_impression_idle_after_user_seconds",
                DEFAULT_PRIVATE_IMPRESSION_IDLE_AFTER_USER_SECONDS,
            )
        ),
        local_timezone=timezone_name,
    )


def _local_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Asia/Shanghai")


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _local_day(moment: datetime, timezone_name: str) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(_local_zone(timezone_name)).date().isoformat()


def _ledger_sqlite_path(ledger: object) -> Path | None:
    path = getattr(ledger, "_database_path", None)
    if isinstance(path, Path):
        return path
    if isinstance(path, str) and path:
        return Path(path)
    return None


def _memory_gate_rows(ledger: object) -> list[dict[str, object]]:
    """Per-ledger skip rows for in-memory ledgers.

    Object identity is the key.  A process-global ``id(ledger)`` map used to
    leak another test's daily_cap / min_interval rows after GC reused the
    integer.  WeakKeyDictionary holds live ledgers; objects that cannot be
    weakly referenced carry their own list.
    """

    try:
        return _MEMORY_GATES.setdefault(ledger, [])
    except TypeError:
        rows = getattr(ledger, "_world_v2_private_impression_gate_rows", None)
        if not isinstance(rows, list):
            rows = []
            object.__setattr__(ledger, "_world_v2_private_impression_gate_rows", rows)
        return rows


def _farm_process_asked_the_model(process) -> bool:
    """True when this farm trigger spent a provider call and then completed.

    Replay / ignored / expired complete without calling her.  Technical
    failures that never complete are not counted until a later drain
    terminals them.  The paid inbound hitch does not open a farm trigger.
    """

    if getattr(process, "process_kind", None) != "private_impression_deliberation":
        return False
    if getattr(process, "state", None) != "terminal":
        return False
    outcome = str(getattr(process, "runtime_outcome_ref", None) or "")
    if ":replay:" in outcome or ":ignored" in outcome or ":expired:" in outcome:
        return False
    attempt_id = _farm_completion_attempt_id(process)
    if attempt_id.startswith("attempt:" + _PAID_INBOUND_MODEL_CALL_PREFIX):
        return False
    if ":accepted:" in outcome:
        return True
    return outcome.endswith(":no-change")


def _farm_completion_attempt_id(process) -> str:
    lease = getattr(process, "claim_lease", None)
    if lease is not None and getattr(lease, "attempt_id", None):
        return str(lease.attempt_id)
    attempt_ids = getattr(process, "attempt_ids", ()) or ()
    if attempt_ids:
        return str(attempt_ids[-1])
    return ""


def _completion_moment(event) -> datetime | None:
    payload = event.payload() if hasattr(event, "payload") else {}
    if not isinstance(payload, dict):
        payload = {}
    raw = payload.get("completed_at")
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str) and raw:
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    moment = getattr(event, "logical_time", None)
    return moment if isinstance(moment, datetime) else None


def _background_private_impression_calls(
    projection,
    ledger: object | None = None,
) -> tuple[tuple[datetime, str], ...]:
    """Ledger-auditable background asks, timed at drain completion.

    ``TriggerProcessCompleted`` is a deliberation event, so it never appears
    in ``committed_world_event_refs``.  ``ModelResultRecorded`` copies the
    source ``AppraisalAccepted`` logical time, which can be hours old on a
    quiet farm tick.  ``completed_at`` on the completion event is the drain
    clock, which is what interval and the local-day cap must use.
    """

    lookup = getattr(ledger, "find_trigger_completion", None) if ledger is not None else None
    found: list[tuple[datetime, str]] = []
    for process in getattr(projection, "trigger_processes", ()):
        if not _farm_process_asked_the_model(process):
            continue
        trigger_id = str(process.trigger_id)
        event = lookup(trigger_id) if callable(lookup) else None
        if event is None:
            continue
        moment = _completion_moment(event)
        if moment is None:
            continue
        found.append((moment, trigger_id))
    found.sort(key=lambda item: (item[0], item[1]))
    return tuple(found)


def last_user_observation_at(projection) -> datetime | None:
    """Authoritative time of his last inbound ObservationRecorded."""

    latest: datetime | None = None
    for ref in getattr(projection, "committed_world_event_refs", ()):
        if getattr(ref, "event_type", None) != "ObservationRecorded":
            continue
        moment = getattr(ref, "logical_time", None)
        if not isinstance(moment, datetime):
            continue
        if latest is None or moment > latest:
            latest = moment
    return latest


def pending_private_impression_trigger_id(projection) -> str:
    pending = [
        item
        for item in getattr(projection, "trigger_processes", ())
        if item.process_kind == "private_impression_deliberation" and item.state != "terminal"
    ]
    if not pending:
        return ""
    pending.sort(key=lambda item: item.trigger_id)
    return pending[0].trigger_id


def evaluate_private_impression_drain_gate(
    projection,
    *,
    policy: PrivateImpressionDrainPolicy,
    ledger: object | None = None,
) -> PrivateImpressionGateDecision:
    """Decide whether this drain tick may spend a provider call.

    Skip reasons are host gates, not character decisions.  ``no_change`` after
    a call is recorded on the ledger by the existing completion path.
    Interval and daily cap count farm ``TriggerProcessCompleted`` events whose
    outcome is ``:accepted:`` or ``:no-change``, using ``completed_at`` (the
    drain clock) rather than the source appraisal's logical time.  One farm
    ask is one slot, not one HTTP reselection.
    """

    if not policy.allows_model_calls:
        return PrivateImpressionGateDecision(
            action="skip",
            reason="disabled",
            daily_limit=policy.daily_model_call_limit,
            local_timezone=policy.local_timezone,
        )
    now = getattr(projection, "logical_time", None)
    if now is None:
        return PrivateImpressionGateDecision(
            action="idle", local_timezone=policy.local_timezone
        )
    trigger_id = pending_private_impression_trigger_id(projection)
    if not trigger_id:
        return PrivateImpressionGateDecision(
            action="idle", local_timezone=policy.local_timezone
        )
    user_at = last_user_observation_at(projection)
    calls = _background_private_impression_calls(projection, ledger)
    day = _local_day(now, policy.local_timezone)
    ledger_today = [item for item in calls if _local_day(item[0], policy.local_timezone) == day]
    daily_calls = len(ledger_today)
    last_call_at = max((item[0] for item in calls), default=None)
    if user_at is not None:
        idle = (_as_utc(now) - _as_utc(user_at)).total_seconds()
        if idle < policy.idle_after_user_seconds:
            return PrivateImpressionGateDecision(
                action="skip",
                reason="recent_user_observation",
                daily_calls=daily_calls,
                daily_limit=policy.daily_model_call_limit,
                trigger_id=trigger_id,
                last_user_observation_at=user_at,
                last_background_call_at=last_call_at,
                local_timezone=policy.local_timezone,
            )
    if last_call_at is not None and policy.min_interval_seconds > 0:
        gap = (_as_utc(now) - _as_utc(last_call_at)).total_seconds()
        if gap < policy.min_interval_seconds:
            return PrivateImpressionGateDecision(
                action="skip",
                reason="min_interval",
                daily_calls=daily_calls,
                daily_limit=policy.daily_model_call_limit,
                trigger_id=trigger_id,
                last_user_observation_at=user_at,
                last_background_call_at=last_call_at,
                local_timezone=policy.local_timezone,
            )
    if daily_calls >= policy.daily_model_call_limit:
        return PrivateImpressionGateDecision(
            action="skip",
            reason="daily_cap",
            daily_calls=daily_calls,
            daily_limit=policy.daily_model_call_limit,
            trigger_id=trigger_id,
            last_user_observation_at=user_at,
            last_background_call_at=last_call_at,
            local_timezone=policy.local_timezone,
        )
    return PrivateImpressionGateDecision(
        action="ask",
        daily_calls=daily_calls,
        daily_limit=policy.daily_model_call_limit,
        trigger_id=trigger_id,
        last_user_observation_at=user_at,
        last_background_call_at=last_call_at,
        local_timezone=policy.local_timezone,
    )


def recorded_private_impression_gates(ledger: object) -> tuple[dict[str, object], ...]:
    """Return skip rows from the sqlite side table or the in-memory fallback."""

    path = _ledger_sqlite_path(ledger)
    if path is not None:
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    "SELECT world_id, local_day, trigger_id, reason, recorded_at, "
                    "daily_calls, daily_limit FROM world_v2_private_impression_gates "
                    "ORDER BY recorded_at, reason"
                ).fetchall()
            return tuple(dict(row) for row in rows)
        except sqlite3.Error:
            pass
    return tuple(_memory_gate_rows(ledger))


def record_private_impression_gate(
    ledger: object,
    decision: PrivateImpressionGateDecision,
    *,
    now: datetime | None = None,
) -> bool:
    """Persist one skip.  Returns True when this exact skip is new today.

    Repeated drain ticks with the same (day, trigger, reason) stay silent so
    the scheduler cannot flood logs.  Never records ``ask`` or ``idle``.
    """

    if decision.action != "skip" or decision.reason not in _GATE_REASONS:
        return False
    world_id = str(getattr(ledger, "world_id", "") or "")
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    timezone_name = decision.local_timezone or "Asia/Shanghai"
    local_day = _local_day(moment, timezone_name)
    row = {
        "world_id": world_id,
        "local_day": local_day,
        "trigger_id": decision.trigger_id,
        "reason": decision.reason,
        "recorded_at": moment.isoformat(),
        "daily_calls": decision.daily_calls,
        "daily_limit": decision.daily_limit,
    }
    path = _ledger_sqlite_path(ledger)
    inserted = False
    if path is not None:
        with _GATE_LOCK:
            try:
                with sqlite3.connect(path) as connection:
                    connection.execute(_GATE_TABLE_SQL)
                    cursor = connection.execute(
                        "INSERT OR IGNORE INTO world_v2_private_impression_gates ("
                        "world_id, local_day, trigger_id, reason, recorded_at, "
                        "daily_calls, daily_limit) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            world_id,
                            local_day,
                            decision.trigger_id,
                            decision.reason,
                            row["recorded_at"],
                            decision.daily_calls,
                            decision.daily_limit,
                        ),
                    )
                    connection.commit()
                    inserted = cursor.rowcount == 1
            except sqlite3.Error:
                inserted = False
    if path is None or not inserted:
        bucket = _memory_gate_rows(ledger)
        key = (world_id, local_day, decision.trigger_id, decision.reason)
        if any(
            (
                item.get("world_id"),
                item.get("local_day"),
                item.get("trigger_id"),
                item.get("reason"),
            )
            == key
            for item in bucket
        ):
            return False
        bucket.append(row)
        inserted = True
    if inserted:
        _LOG.info(
            "private impression gated reason=%s trigger_id=%s daily_calls=%s daily_limit=%s",
            decision.reason,
            decision.trigger_id or "-",
            decision.daily_calls,
            decision.daily_limit,
        )
    return inserted


class PrivateImpressionReflectionSource(FrozenModel):
    """One source-bound item in the pinned private reflection capsule."""

    source_ref: str = Field(min_length=1, max_length=512)
    source_kind: Literal[
        "appraisal",
        "character_core",
        "relationship",
        "affect",
        "experience",
        "existing_impression",
    ]
    authority_event_ref: str = Field(min_length=1, max_length=512)
    value_json: str = Field(min_length=2, max_length=8_192)


class PrivateImpressionReflectionCapsule(FrozenModel):
    """Cursor-pinned, multi-layer context offered to the role model."""

    capsule_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    world_id: str = Field(min_length=1)
    world_revision: int = Field(ge=0)
    deliberation_revision: int = Field(ge=0)
    ledger_sequence: int = Field(ge=0)
    logical_time: str = Field(min_length=1)
    subject_ref: str = Field(min_length=1)
    anchor_appraisal_id: str = Field(min_length=1)
    identity_frame: CompanionIdentityFrame
    sources: tuple[PrivateImpressionReflectionSource, ...] = Field(
        min_length=1,
        max_length=48,
    )








def _materialize_draft(
    raw: str,
    *,
    capsule: PrivateImpressionReflectionCapsule,
) -> PrivateImpressionDraft | None:
    try:
        value = json.loads(extract_json_object_text(raw))
    except json.JSONDecodeError as exc:
        raise ValueError("private impression model did not return one JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError("private impression model did not return one JSON object")
    decision = value.get("decision")
    legacy_retain = value.get("retain")
    if decision is None and isinstance(legacy_retain, bool):
        decision = "retain" if legacy_retain else "no_change"
    if decision not in {"no_change", "retain", "consolidate", "supersede", "release"}:
        raise ValueError("private impression decision is invalid")
    if decision == "no_change":
        return None
    predecessor_refs = value.get("predecessor_refs", [])
    source_refs = value.get("source_refs")
    reflection_summary = value.get("reflection_summary")
    confidence = value.get("confidence")
    expiry = value.get("expiry_condition")
    if (
        isinstance(confidence, float)
        and not isinstance(confidence, bool)
        and 0.0 <= confidence <= 1.0
    ):
        confidence = round(confidence * 10_000)
    # Map any short capability tokens ("s0", "s1", ...) back to the real
    # source refs.  The capability offers short tokens so any provider can
    # select a source without echoing a very long hash; the mapping here is
    # deterministic from capsule source order and idempotent (real refs are
    # not keys of the map).
    short_token_map = {
        f"s{index}": item.source_ref
        for index, item in enumerate(capsule.sources)
    }
    if isinstance(source_refs, list):
        source_refs = [short_token_map.get(item, item) for item in source_refs]
    if isinstance(predecessor_refs, list):
        predecessor_refs = [
            short_token_map.get(item, item) for item in predecessor_refs
        ]
    offered = {item.source_ref for item in capsule.sources}
    anchor_refs = {
        item.source_ref
        for item in capsule.sources
        if item.source_kind == "appraisal"
        and json.loads(item.value_json).get("appraisal_id") == capsule.anchor_appraisal_id
    }
    existing_refs = {
        item.source_ref for item in capsule.sources if item.source_kind == "existing_impression"
    }
    if (
        not isinstance(source_refs, list)
        or not source_refs
        or any(not isinstance(item, str) or item not in offered for item in source_refs)
        or len(source_refs) != len(set(source_refs))
        or not set(source_refs) & anchor_refs
        or not isinstance(predecessor_refs, list)
        or any(not isinstance(item, str) for item in predecessor_refs)
        or len(predecessor_refs) != len(set(predecessor_refs))
        or (
            decision in {"consolidate", "supersede", "release"}
            and (
                not predecessor_refs
                or (decision == "release" and len(predecessor_refs) != 1)
                or any(
                    not isinstance(item, str)
                    or item not in existing_refs
                    or item not in source_refs
                    for item in predecessor_refs
                )
            )
        )
        or (decision == "retain" and predecessor_refs)
        or not isinstance(reflection_summary, str)
        or not reflection_summary.strip()
        or len(reflection_summary) > 1_200
        or isinstance(confidence, bool)
        or not isinstance(confidence, int)
        or not 0 <= confidence <= 10_000
        or expiry not in EXPIRY_CONDITIONS
    ):
        raise ValueError("private impression fields are invalid or not appraisal-grounded")
    # Preserve capsule source order so proposal identity is deterministic
    # across retries and provider output ordering cannot alter replay.
    ordered = tuple(
        item.source_ref for item in capsule.sources if item.source_ref in set(source_refs)
    )
    ordered_predecessors = tuple(
        item.source_ref for item in capsule.sources if item.source_ref in set(predecessor_refs)
    )
    return PrivateImpressionDraft(
        decision=decision,  # type: ignore[arg-type]
        predecessor_refs=ordered_predecessors,
        source_refs=ordered,
        reflection_summary=reflection_summary.strip(),
        confidence_bp=confidence,
        expiry_condition=expiry,  # type: ignore[arg-type]
    )


def compile_private_impression_reflection_capsule(
    *,
    projection: Any,
    appraisal: AppraisalProjection,
    identity_frame: CompanionIdentityFrame,
    world_id: str,
    content_reader: Callable[[str], str | None] | None = None,
    life_content_store: ImmutableLifeContentStore | None = None,
    companion_actor_ref: str | None = None,
) -> PrivateImpressionReflectionCapsule:
    """Compile bounded layered context without granting it mutation authority."""

    cursor = _cursor(projection)
    logical_time = projection.logical_time
    if logical_time is None:
        raise ValueError("private impression reflection requires authoritative time")
    sources: list[PrivateImpressionReflectionSource] = []

    def add(
        *,
        source_ref: str,
        source_kind: str,
        authority_event_ref: str,
        value: object,
    ) -> None:
        if not authority_event_ref or any(item.source_ref == source_ref for item in sources):
            return
        sources.append(
            PrivateImpressionReflectionSource(
                source_ref=source_ref,
                source_kind=source_kind,  # type: ignore[arg-type]
                authority_event_ref=authority_event_ref,
                value_json=canonical_json(to_jsonable_python(value)),
            )
        )

    related_appraisals = [
        item
        for item in projection.appraisals
        if item.status == "active" and item.subject_ref == appraisal.subject_ref
    ]
    ordered_appraisals = [
        appraisal,
        *(
            item
            for item in reversed(related_appraisals)
            if item.appraisal_id != appraisal.appraisal_id
        ),
    ][:8]
    for item in ordered_appraisals:
        for hypothesis in item.hypotheses:
            add(
                source_ref=f"appraisal:{item.appraisal_id}:{hypothesis.hypothesis_id}",
                source_kind="appraisal",
                authority_event_ref=item.origin.accepted_event_ref,
                value={
                    "appraisal_id": item.appraisal_id,
                    "subject_ref": item.subject_ref,
                    "source_cluster_ref": item.source_cluster_ref,
                    "confidence_bp": item.confidence_bp,
                    "hypothesis_id": hypothesis.hypothesis_id,
                    "meaning": hypothesis.meaning,
                    "attribution": hypothesis.attribution,
                    "severity": hypothesis.severity,
                    "weight_bp": hypothesis.weight_bp,
                    "accepted_change_id": item.origin.change_id,
                    "accepted_transition_id": item.origin.transition_id,
                },
            )

    core = projection.character_core
    if core is not None and core.origin is not None:
        add(
            source_ref=f"character-core:{core.core_id}:{core.entity_revision}",
            source_kind="character_core",
            authority_event_ref=core.origin.accepted_event_ref,
            value={
                "actor_ref": core.actor_ref,
                "values": core.values.model_dump(mode="json"),
            },
        )

    for relationship in reversed(projection.relationship_states):
        if relationship.subject_ref != appraisal.subject_ref or relationship.origin is None:
            continue
        add(
            source_ref=(
                f"relationship:{relationship.relationship_id}:{relationship.entity_revision}"
            ),
            source_kind="relationship",
            authority_event_ref=relationship.origin.accepted_event_ref,
            value={
                "stage": relationship.stage,
                "variables": relationship.variables.model_dump(mode="json"),
                "temperature": relationship.temperature,
                "commitment_refs": relationship.commitment_refs,
                "last_adjusted_at": relationship.last_adjusted_at,
            },
        )
        break

    appraisal_ids = {item.appraisal_id for item in related_appraisals}
    affect = [
        item
        for item in projection.affect_episodes
        if item.status == "active"
        and any(
            ref.appraisal_id in appraisal_ids
            for component in item.components
            for ref in component.appraisal_refs
        )
    ][-4:]
    for episode in reversed(affect):
        add(
            source_ref=f"affect:{episode.episode_id}:{episode.entity_revision}",
            source_kind="affect",
            authority_event_ref=episode.origin.accepted_event_ref,
            value={
                "components": [
                    {
                        "dimension": component.dimension,
                        "intensity_bp": component.intensity_bp,
                        "residue_bp": component.residue_bp,
                        "last_updated_at": component.last_updated_at,
                    }
                    for component in episode.components
                ],
                "updated_at": episode.updated_at,
            },
        )

    experiences = [
        item
        for item in projection.experiences
        if getattr(item, "status", None) == "committed"
        and appraisal.subject_ref in item.values.participant_refs
    ][-4:]
    for experience in reversed(experiences):
        if getattr(experience, "authority_contract_version", None) == "experience.2":
            from .life_content_reading import read_character_life_experience_content

            # The legacy raw-text callback cannot prove a composite's World
            # descriptor/body. New material needs the installed exact reader;
            # missing ports or sources never fall back to flattened prose.
            if life_content_store is None or companion_actor_ref is None:
                raise ValueError("private reflection requires the exact composite source reader")
            reading = read_character_life_experience_content(
                store=life_content_store, projection=projection, experience=experience,
                actor_ref=companion_actor_ref, viewer_privacy_ceiling="private",
                max_characters=1_200,
            )
            add(
                source_ref=f"experience:{experience.experience_id}",
                source_kind="experience", authority_event_ref=experience.origin.accepted_event_ref,
                value={
                    **reading.model_dump(mode="json"),
                    "occurred_from": experience.values.occurred_from,
                    "occurred_to": experience.values.occurred_to,
                },
            )
            continue
        add(
            source_ref=f"experience:{experience.experience_id}",
            source_kind="experience",
            authority_event_ref=experience.origin.accepted_event_ref,
            value={
                "summary_ref": experience.values.summary_ref,
                "summary_text": (
                    content_reader(experience.values.summary_ref)
                    if content_reader is not None
                    else None
                ),
                "occurred_from": experience.values.occurred_from,
                "occurred_to": experience.values.occurred_to,
                "participant_refs": experience.values.participant_refs,
            },
        )

    impressions = [
        item
        for item in projection.private_impressions
        if item.status == "active"
        and item.subject_ref == appraisal.subject_ref
        and item.origin is not None
    ][-6:]
    for impression in reversed(impressions):
        add(
            source_ref=f"private-impression:{impression.impression_id}",
            source_kind="existing_impression",
            authority_event_ref=impression.origin.accepted_event_ref,
            value={
                "reflection_summary": impression.reflection_summary,
                "confidence_bp": impression.confidence_bp,
                "first_seen": getattr(impression, "first_seen", None),
                "last_supported": impression.last_supported,
                "expiry_condition": impression.expiry_condition,
                "interpretation_refs": impression.interpretation_refs,
            },
        )

    offered = offered_private_impression_reflection_bindings(
        projection,
        appraisal=appraisal,
    )
    by_ref = {item.source_ref: item for item in sources}
    if any(item.source_ref not in by_ref for item in offered):
        raise ValueError("private impression source manifest is incomplete")
    sources = [by_ref[item.source_ref] for item in offered]

    material = {
        "world_id": world_id,
        "world_revision": cursor.world_revision,
        "deliberation_revision": cursor.deliberation_revision,
        "ledger_sequence": cursor.ledger_sequence,
        "logical_time": logical_time.isoformat(),
        "subject_ref": appraisal.subject_ref,
        "anchor_appraisal_id": appraisal.appraisal_id,
        "identity_frame": identity_frame.model_dump(mode="json"),
        "sources": [item.model_dump(mode="json") for item in sources],
    }
    return PrivateImpressionReflectionCapsule(
        capsule_id=sha256(canonical_json(material)),
        world_id=world_id,
        world_revision=cursor.world_revision,
        deliberation_revision=cursor.deliberation_revision,
        ledger_sequence=cursor.ledger_sequence,
        logical_time=logical_time.isoformat(),
        subject_ref=appraisal.subject_ref,
        anchor_appraisal_id=appraisal.appraisal_id,
        identity_frame=identity_frame,
        sources=tuple(sources),
    )


def private_impression_opportunity(projection) -> tuple[str, str] | None:
    """Open at most one impression trigger for one uninterpreted appraisal.

    G7 still wins when the head event is an eligible ``AppraisalAccepted``.
    Production inbound batches bury that event behind later receipts, so the
    farm also accepts the newest active uninterpreted appraisal that does not
    already have a trigger.  That is still one identity per appraisal and one
    open per ``open_once`` — not a historical sweep.
    """

    if projection.logical_time is None:
        return None
    if pending_private_impression_trigger_id(projection):
        return None
    interpretation_refs = {
        ref
        for impression in projection.private_impressions
        for ref in impression.interpretation_refs
    }
    existing_triggers = {item.trigger_id for item in projection.trigger_processes}
    paid_choices: dict[str, bool] = {}

    def already_authored_paid_retention(change_id: str | None) -> bool:
        if change_id is None:
            return False
        if change_id in paid_choices:
            return paid_choices[change_id]
        # Resolve only the otherwise eligible candidate's accepted change.
        # Quiet ticks and already interpreted sources never parse old prose.
        for audit in reversed(getattr(projection, "proposal_audits", ())):
            proposal = json.loads(audit.proposal_json)
            if any(change.get("kind") == "appraisal_transition"
                   and change.get("change_id") == change_id
                   for change in proposal.get("proposed_changes", ())):
                state = proposal.get("private_turn_state") or {}
                paid_choices[change_id] = state.get("keep_impression") is True and bool(state.get("stuck_with_me"))
                return paid_choices[change_id]
        paid_choices[change_id] = False
        return False

    def eligible(appraisal: object) -> str | None:
        origin = getattr(appraisal, "origin", None)
        source_ref = getattr(origin, "accepted_event_ref", None)
        if (
            getattr(appraisal, "status", None) != "active"
            or not isinstance(source_ref, str)
            or not source_ref
        ):
            return None
        trigger_id = private_impression_trigger_identity(projection.world_id, source_ref)
        if trigger_id in existing_triggers:
            return None
        # Match exact emitted identities only for the candidate under review;
        # finding the newest opportunity does not require every old hypothesis.
        if interpretation_refs and any(
            f"appraisal:{appraisal.appraisal_id}:{hypothesis.hypothesis_id}"
            in interpretation_refs
            for hypothesis in appraisal.hypotheses
        ):
            return None
        # Its paid choice may still be pending or technically rejected. That
        # does not authorize a new semantic ask on this same accepted reading.
        if already_authored_paid_retention(getattr(origin, "change_id", None)):
            return None
        return source_ref

    head_refs = newly_accepted_head_refs(
        projection.committed_world_event_refs,
        event_type="AppraisalAccepted",
    )
    if head_refs:
        source_ref = next(iter(head_refs))
        appraisal = next(
            (
                item
                for item in projection.appraisals
                if item.origin is not None
                and item.origin.accepted_event_ref == source_ref
            ),
            None,
        )
        if appraisal is not None and eligible(appraisal) == source_ref:
            return (
                private_impression_trigger_identity(projection.world_id, source_ref),
                source_ref,
            )
    for appraisal in reversed(projection.appraisals):
        source_ref = eligible(appraisal)
        if source_ref is None:
            continue
        return (
            private_impression_trigger_identity(projection.world_id, source_ref),
            source_ref,
        )
    return None


class PrivateImpressionTriggerOpener:
    """Commit at most one ``TriggerProcessOpened`` per accepted appraisal."""

    def __init__(
        self,
        *,
        ledger: LedgerPort,
        owner_id: str,
        source: str = "world-v2:private-impression-trigger-opener",
    ) -> None:
        if not owner_id:
            raise ValueError("private impression opener needs an owner")
        self._ledger = ledger
        self._owner_id = owner_id
        self._source = source

    async def open_once(self) -> str | None:
        projection = await _project(self._ledger)
        opportunity = private_impression_opportunity(projection)
        if opportunity is None:
            return None
        trigger_id, source_ref = opportunity
        located = await _lookup(self._ledger, source_ref)
        if located is None or located[0].event_type != "AppraisalAccepted":
            raise ValueError("private impression anchor authority is unavailable")
        source_event = located[0]
        process = TriggerProcess(
            trigger_id=trigger_id,
            trigger_ref=f"impression:{source_ref}",
            process_kind="private_impression_deliberation",
            source_evidence_ref=source_ref,
            state="open",
        )
        payload = {"process": process.model_dump(mode="json")}
        identity = domain_idempotency_key(
            event_type="TriggerProcessOpened", world_id=self._ledger.world_id, payload=payload
        )
        if identity is None:
            raise ValueError("private impression trigger has no domain identity")
        event = WorldEvent.from_payload(
            schema_version="world-v2.1",
            event_id="event:private-impression:opened:"
            + _digest({"world_id": self._ledger.world_id, "trigger_id": trigger_id}),
            world_id=self._ledger.world_id,
            event_type="TriggerProcessOpened",
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
        await _commit(
            self._ledger,
            (event,),
            world_revision=projection.world_revision,
            deliberation_revision=projection.deliberation_revision,
            commit_id="commit:private-impression:opened:" + _digest(trigger_id),
        )
        return trigger_id


class PrivateImpressionRunResult(FrozenModel):
    trigger_id: str
    status: Literal["idle", "owned_elsewhere", "processed"]
    work_status: Literal[
        "no_change", "ignored", "expired", "accepted", "technical_failure"
    ] | None = None
    opportunity_ref: str | None = None
    source_refs: tuple[str, ...] = ()
    epoch: str | None = None
    contract_version: str | None = None


@dataclass(frozen=True, slots=True)
class _PrivateImpressionLocatedSource:
    process: TriggerProcess
    source_event: WorldEvent
    policy: CausalOpportunityPolicy

    @property
    def route_source(self) -> CausalOpportunitySource:
        return CausalOpportunitySource(
            source_ref=self.source_event.event_id,
            process_ref=self.process.trigger_id,
            process_kind=self.process.process_kind,
            causal_key=self.source_event.correlation_id,
            logical_time=self.source_event.logical_time,
            policy=self.policy,
        )


@dataclass(frozen=True, slots=True)
class _PrivateImpressionOpportunityBatch:
    processes: tuple[TriggerProcess, ...]
    source_events: tuple[WorldEvent, ...]
    identity: CausalOpportunityIdentity


def _private_impression_opportunity_identity(
    *,
    world_id: str,
    actor_ref: str,
    source_ref: str | None = None,
    source_refs: tuple[str, ...] = (),
    epoch: str | None = None,
    policy: CausalOpportunityPolicy | None = None,
) -> CausalOpportunityIdentity:
    refs = tuple(source_refs)
    if source_ref:
        refs += (source_ref,)
    refs = tuple(dict.fromkeys(refs))
    canonical_refs = tuple(sorted(refs))
    if not canonical_refs:
        raise ValueError("private impression opportunity requires source refs")
    selected_policy = policy or DEFAULT_CAUSAL_OPPORTUNITY_POLICY
    runtime = CausalOpportunityRuntime(
        world_id=world_id,
        actor_ref=actor_ref,
        purpose=PRIVATE_IMPRESSION_PURPOSE,
    )
    return runtime.identity_for_refs(
        canonical_refs,
        epoch=epoch or canonical_refs[0],
        policy=selected_policy,
    )


def _private_impression_capability(
    capsule: PrivateImpressionReflectionCapsule,
    *,
    opportunity_identity: CausalOpportunityIdentity | None = None,
) -> _InteriorCapabilityManifest:
    if opportunity_identity is None:
        anchor_source_ref = next(
            (
                item.source_ref
                for item in capsule.sources
                if item.source_kind == "appraisal"
                and json.loads(item.value_json).get("appraisal_id")
                == capsule.anchor_appraisal_id
            ),
            None,
        )
        if anchor_source_ref is None:
            raise ValueError("private impression capability lacks an anchor source")
        opportunity_identity = _private_impression_opportunity_identity(
            world_id=capsule.world_id,
            actor_ref="actor:companion",
            source_ref=anchor_source_ref,
        )
    anchor_source_refs = [
        item.source_ref
        for item in capsule.sources
        if item.source_kind == "appraisal"
        and json.loads(item.value_json).get("appraisal_id")
        == capsule.anchor_appraisal_id
    ]
    # The model must select exact source hypotheses.  The real source refs are
    # very long (compiled appraisal hashes), and asking a provider to echo one
    # verbatim is a reliable way to fail validation on any model.  Instead we
    # hand the model short, position-stable tokens ("s0", "s1", ...) and map
    # them back to the real refs at the validation boundary.  The map is
    # derived deterministically from capsule source order, so proposal
    # identity is stable across retries and provider output ordering.
    short_tokens = [f"s{index}" for index in range(len(capsule.sources))]
    existing_impression_short_tokens = [
        token
        for token, item in zip(short_tokens, capsule.sources, strict=True)
        if item.source_kind == "existing_impression"
    ]
    token_map = {
        token: item.source_ref
        for token, item in zip(short_tokens, capsule.sources, strict=True)
    }
    payload = {
        "contract": "character-interior-private-impression-capability.1",
        "causal_opportunity": opportunity_identity.model_dump(mode="json"),
        "reflection_capsule": capsule.model_dump(mode="json"),
        "reflection_sources": [
            {
                **item.model_dump(mode="json"),
                "short_token": short_tokens[index],
            }
            for index, item in enumerate(capsule.sources)
        ],
        "short_tokens": short_tokens,
        # Predecessors are a narrower role-owned choice than the offered
        # evidence set: only existing private impressions may be retired or
        # consolidated.  Keep that distinction in the provider contract so a
        # structurally valid tool call cannot select an appraisal/affect token
        # and wait for the host to reject it later.
        "existing_impression_short_tokens": existing_impression_short_tokens,
        "token_map": token_map,
        "anchor_source_refs": anchor_source_refs,
        "anchor_short_tokens": [
            token for token, ref in token_map.items() if ref in anchor_source_refs
        ],
        "expiry_conditions": list(EXPIRY_CONDITIONS),
        "decision_meanings": {
            "no_change": "这次先不动印象：不开新的，也不搁下已有的。省略也完全正常。",
            "retain": "记下一条新的、仍可改的私人印象。",
            **(
                {
                    "consolidate": "把已有印象收成一条，仍然搁在心里。",
                    "supersede": "用一条新的印象替代旧的；旧的不再作为未了结的心事。",
                    "release": (
                        "这件事我已经说过或做过，可以搁下了。"
                        "仍记得，只是不再占着未了结的位置。选或不选都正常。"
                    ),
                }
                if existing_impression_short_tokens
                else {}
            ),
        },
    }
    payload_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _InteriorCapabilityManifest(
        capability_ref=f"capability:private-impression:{capsule.capsule_id}",
        capability_kind="private_impression_reflection",
        payload_json=payload_json,
        payload_hash="sha256:" + hashlib.sha256(payload_json.encode()).hexdigest(),
        source_refs=tuple(
            dict.fromkeys(item.authority_event_ref for item in capsule.sources)
        ),
    )


class PrivateImpressionTriggerRuntime:
    """Drain one claimed-or-open ``private_impression_deliberation`` trigger."""

    def __init__(
        self,
        *,
        ledger,
        character_interior: CharacterInterior,
        companion_actor_ref: str,
        identity_frame: CompanionIdentityFrame | None = None,
        content_reader: Callable[[str], str | None] | None = None,
        life_content_store: ImmutableLifeContentStore | None = None,
        owner_id: str,
        lease_seconds: int = 120,
        merge_window_seconds: int = DEFAULT_CAUSAL_OPPORTUNITY_POLICY.merge_window_seconds,
        expiry_seconds: int = DEFAULT_CAUSAL_OPPORTUNITY_POLICY.expiry_seconds,
        source: str = "world-v2:private-impression-trigger-runtime",
    ) -> None:
        if not owner_id or lease_seconds <= 0 or merge_window_seconds < 0 or expiry_seconds <= 0:
            raise ValueError("private impression runtime needs an owner and positive lease")
        if not isinstance(character_interior, CharacterInterior):
            raise TypeError("private impression runtime requires CharacterInterior")
        if not companion_actor_ref:
            raise ValueError("private impression Interior path needs the companion actor ref")
        self._ledger = ledger
        self._character_interior = character_interior
        self._companion_actor_ref = companion_actor_ref
        self._identity_frame = (
            identity_frame
            or CompanionIdentityFrame(
                companion_name="沈知栀",
                counterpart_name="对方",
            )
        )
        self._content_reader = content_reader
        self._life_content_store = life_content_store
        self._owner_id = owner_id
        self._lease_seconds = lease_seconds
        self._policy = CausalOpportunityPolicy(
            merge_window_seconds=merge_window_seconds,
            expiry_seconds=expiry_seconds,
        )
        self._opportunity_runtime = CausalOpportunityRuntime(
            world_id=ledger.world_id,
            actor_ref=companion_actor_ref,
            purpose=PRIVATE_IMPRESSION_PURPOSE,
            contract_version=CAUSAL_OPPORTUNITY_CONTRACT_VERSION,
        )
        self._source = source
        # These are accelerators over immutable ledger records, never a second
        # source of work. A fresh runtime rebuilds them without a provider call.
        self._paid_proposal_count = 0
        self._paid_unscanned_ranges: list[tuple[int, int]] = []
        self._paid_finished: set[str] = set()
        self._paid_pending: dict[str, tuple[str, str]] = {}
        self._paid_retry_after: dict[str, datetime] = {}
        self._paid_model_audit_count = 0
        self._paid_failure_index: dict[str, list[tuple[str, str]]] = {}

    async def recover_paid_once(self) -> PrivateImpressionRunResult:
        """Finish one persisted paid choice before any independent model gate.

        The paid DecisionProposal itself is the outbox. No separate enqueue
        write can be lost after the visible turn commits or during shutdown.
        Old and newly appended audits are scanned incrementally, newest first.
        """
        projection = await _project(self._ledger)
        count = len(projection.proposal_audits)
        if count > self._paid_proposal_count:
            self._paid_unscanned_ranges.append((self._paid_proposal_count, count))
            self._paid_proposal_count = count
        remaining = _PAID_RECOVERY_SCAN_LIMIT
        while self._paid_unscanned_ranges and remaining:
            start, stop = self._paid_unscanned_ranges.pop()
            batch_start = max(start, stop - remaining)
            if batch_start > start:
                self._paid_unscanned_ranges.append((start, batch_start))
            for audit in reversed(projection.proposal_audits[batch_start:stop]):
                value = json.loads(audit.proposal_json)
                state = value.get("private_turn_state") or {}
                summary = state.get("stuck_with_me")
                if state.get("keep_impression") is True and isinstance(summary, str) and summary.strip():
                    self._paid_pending[audit.model_result_ref] = (audit.trigger_ref, summary)
            remaining -= stop - batch_start
        # Model results are append-only. Index each new paid technical result
        # once instead of reparsing the entire history for every pending item.
        for audit in projection.model_result_audits[self._paid_model_audit_count:]:
            if not audit.attempt_id.startswith("attempt:paid-inbound-impression:"):
                continue
            failure = json.loads(audit.audit_json).get("failure_code")
            if failure in {
                _PAID_RECOVERY_REJECTED, _PAID_RECOVERY_PREFIX_CHANGED,
                _PAID_RECOVERY_STORAGE_FAILED,
            }:
                base_attempt = audit.attempt_id.partition(":recovery:")[0]
                self._paid_failure_index.setdefault(base_attempt, []).append((failure, audit.event_ref))
        self._paid_model_audit_count = len(projection.model_result_audits)
        now = _paid_recovery_now()
        for model_ref, (source_ref, summary) in tuple(self._paid_pending.items())[:_PAID_RECOVERY_SCAN_LIMIT]:
            if model_ref in self._paid_finished:
                self._paid_pending.pop(model_ref)
                continue
            attempt_id = _paid_retention_attempt_id(source_ref, model_ref)
            indexed = self._paid_failure_index.get(attempt_id, ())
            terminal = any(failure in {_PAID_RECOVERY_REJECTED, _PAID_RECOVERY_PREFIX_CHANGED}
                           for failure, _ in indexed)
            failures = [event_ref for failure, event_ref in indexed
                        if failure == _PAID_RECOVERY_STORAGE_FAILED]
            if terminal:
                self._paid_finished.add(model_ref)
                self._paid_pending.pop(model_ref)
                continue
            retry_after = self._paid_retry_after.get(model_ref)
            if failures:
                located = await _lookup(self._ledger, failures[-1])
                if located is not None:
                    durable_due = located[0].created_at + timedelta(
                        seconds=min(3600, 60 * 2 ** min(len(failures) - 1, 6))
                    )
                    retry_after = max(retry_after or durable_due, durable_due)
            if retry_after is not None and now < retry_after:
                # Round-robin infrastructure work; one delayed write must not
                # starve another paid result behind it.
                self._paid_pending[model_ref] = self._paid_pending.pop(model_ref)
                continue
            source = await _lookup(self._ledger, source_ref)
            if source is None:
                # Proposal audit authority already guarantees this immutable
                # event. Missing physical storage must not become role silence.
                raise ValueError("paid retention recovery source is unavailable")
            # A paid audit can become visible before the same turn's typed
            # appraisal transaction lands. It remains pending across that cut;
            # absence at this instant is not a final authority rejection.
            if await self._paid_appraisal_authority(
                projection=projection, model_result_ref=model_ref,
                source_event=source[0], reflection_summary=summary,
            ) is None:
                self._paid_pending[model_ref] = self._paid_pending.pop(model_ref)
                continue
            try:
                accepted = await self.record_paid_inbound(
                    keep_impression=True, reflection_summary=summary,
                    model_result_ref=model_ref, source_event=source[0],
                )
            except ValueError as exc:
                await self._record_failure_audit(
                    source_event=source[0], attempt_id=attempt_id,
                    failure_code=(
                        _PAID_RECOVERY_PREFIX_CHANGED
                        if isinstance(exc, _PaidRetentionPrefixChanged)
                        else _PAID_RECOVERY_REJECTED
                    ),
                )
                self._paid_finished.add(model_ref)
                self._paid_pending.pop(model_ref)
                return PrivateImpressionRunResult(
                    trigger_id=source_ref, status="processed", work_status="technical_failure",
                )
            except Exception:
                # Backoff uses host time because paid storage recovery must not
                # require advancing the pinned World's clock to become due.
                self._paid_retry_after[model_ref] = now + timedelta(
                    seconds=min(3600, 60 * 2 ** min(len(failures), 6))
                )
                try:
                    await self._record_failure_audit(
                        source_event=source[0],
                        attempt_id=f"{attempt_id}:recovery:{len(failures) + 1}",
                        failure_code=_PAID_RECOVERY_STORAGE_FAILED, created_at=now,
                    )
                except Exception:
                    _LOG.warning("paid retention recovery audit storage failed", exc_info=True)
                return PrivateImpressionRunResult(
                    trigger_id=source_ref, status="processed", work_status="technical_failure",
                )
            self._paid_finished.add(model_ref)
            self._paid_pending.pop(model_ref)
            if accepted is not None:
                return PrivateImpressionRunResult(
                    trigger_id=source_ref, status="processed", work_status="accepted",
                )
        return PrivateImpressionRunResult(trigger_id="", status="idle")

    async def record_paid_inbound(
        self,
        *,
        keep_impression: bool | None,
        reflection_summary: str,
        model_result_ref: str,
        source_event: WorldEvent,
    ) -> str | None:
        """Keep a hitchhiked impression only when she explicitly asked to retain it.

        This writes the existing reflection audit and typed acceptance. It does
        not call a model: the paid inbound turn already produced the keep
        decision, and the audit is a deterministic ledger attestation.
        """

        if keep_impression is not True:
            return None
        projection = await _project(self._ledger)
        attempt_id = _paid_retention_attempt_id(source_event.event_id, model_result_ref)
        bound = await self._paid_appraisal_authority(
            projection=projection, model_result_ref=model_result_ref,
            source_event=source_event, reflection_summary=reflection_summary,
        )
        if bound is None:
            failure_code = "private_impression_paid_retention_appraisal_authority_missing"
            await self._record_failure_audit(
                source_event=source_event, attempt_id=attempt_id, failure_code=failure_code,
            )
            raise ValueError(failure_code)
        appraisal, accepted, inbound = bound
        capsule = compile_private_impression_reflection_capsule(
            projection=projection,
            appraisal=appraisal,
            identity_frame=self._identity_frame,
            world_id=self._ledger.world_id,
            content_reader=self._content_reader,
            life_content_store=self._life_content_store,
            companion_actor_ref=self._companion_actor_ref,
        )
        # A paid turn authored this appraisal. Other old readings in the
        # reflection capsule are context, not sources selected by this turn.
        offered = tuple(
            f"appraisal:{appraisal.appraisal_id}:{item.hypothesis_id}"
            for item in appraisal.hypotheses
        )
        draft = compile_paid_private_impression_draft(
            reflection_summary=reflection_summary,
            offered_source_refs=offered,
            keep_impression=True,
        )
        if draft is None:
            failure_code = "private_impression_paid_retention_authored_residue_missing"
            await self._record_failure_audit(
                source_event=source_event, attempt_id=attempt_id, failure_code=failure_code,
            )
            raise ValueError(failure_code)
        lineage, interior_lineage, derived_ref = _paid_inbound_impression_lineage(
            inbound_model_result_ref=model_result_ref,
            appraisal_id=appraisal.appraisal_id,
            inbound_audit=inbound,
            draft=draft,
            attempt_id=attempt_id,
        )
        if any(item.model_result_ref == derived_ref for item in projection.model_result_audits):
            # Accepted proposals leave the pending projection. Read immutable
            # acceptance history only on recovery, including later-released
            # impressions, rather than authoring the same decision again.
            accepted_ref = await self._accepted_paid_impression(projection, derived_ref)
            if accepted_ref is not None:
                self._paid_finished.add(model_result_ref)
                return accepted_ref
        if appraisal.status != "active":
            failure_code = "private_impression_paid_retention_appraisal_inactive"
            await self._record_failure_audit(
                source_event=source_event, attempt_id=attempt_id, failure_code=failure_code,
            )
            raise ValueError(failure_code)
        try:
            pending = tuple(
                item for item in projection.private_impression_proposals
                if item.source_model_result == derived_ref
            )
            if len(pending) > 1:
                raise ValueError("paid retention has ambiguous pending proposal authority")
            if pending:
                accepted_ref = await self._accept_recorded_impression(
                    payload=json.loads(pending[0].proposed_mutation.payload_json),
                    source_event=accepted[0],
                )
                self._paid_finished.add(model_result_ref)
                return accepted_ref
            existing_audit = next((item for item in projection.model_result_audits
                                   if item.model_result_ref == derived_ref), None)
            if existing_audit is not None:
                # The reflection audit and semantic proposal were one atomic
                # deliberation-only commit. Rebuild its *original* capsule;
                # extra technical audits must not silently replace that pin.
                located = await _lookup(self._ledger, existing_audit.event_ref)
                if located is None or len(located[1].event_ids) != 2:
                    raise ValueError("paid retention reflection audit commit is unavailable")
                commit = located[1]
                pinned = await _project_at(self._ledger, ProjectionCursor(
                    world_revision=commit.world_revision,
                    deliberation_revision=commit.deliberation_revision - 2,
                    ledger_sequence=commit.ledger_sequence - 2,
                ))
                capsule = compile_private_impression_reflection_capsule(
                    projection=pinned, appraisal=appraisal,
                    identity_frame=self._identity_frame, world_id=self._ledger.world_id,
                    content_reader=self._content_reader,
                    life_content_store=self._life_content_store,
                    companion_actor_ref=self._companion_actor_ref,
                )
                if (capsule.capsule_id != existing_audit.capsule_id
                        or pinned.world_revision != projection.world_revision
                        or pinned.logical_time != projection.logical_time):
                    raise _PaidRetentionPrefixChanged("paid retention recorded reflection prefix changed")
            accepted_ref = await self._accept(
                appraisal=appraisal,
                draft=draft,
                capsule=capsule,
                model_result_ref=derived_ref,
                source_event=accepted[0],
                before=projection,
                attempt_id=attempt_id,
                author_lineage=lineage,
                character_interior_lineage=interior_lineage,
                preserve_reflection_audit_cursor=True,
            )
            self._paid_finished.add(model_result_ref)
            return accepted_ref
        except Exception:
            # The commit may have succeeded before its acknowledgement was
            # lost, or a concurrent recovery may already be the CAS winner.
            # Resolve that uncertainty from immutable effect authority first.
            current = await _project(self._ledger)
            accepted_ref = await self._accepted_paid_impression(current, derived_ref)
            if accepted_ref is not None:
                self._paid_finished.add(model_result_ref)
                return accepted_ref
            await self._record_failure_audit(
                source_event=source_event, attempt_id=attempt_id,
                failure_code="private_impression_paid_retention_acceptance_failed",
            )
            raise

    async def _accepted_paid_impression(self, projection, derived_ref: str) -> str | None:
        for ref in reversed(projection.committed_world_event_refs):
            if ref.event_type != "PrivateImpressionAccepted":
                continue
            prior = await _lookup(self._ledger, ref.event_id)
            if prior is not None and prior[0].payload().get("source_model_result") == derived_ref:
                return ref.event_id
        return None

    async def _paid_appraisal_authority(
        self, *, projection, model_result_ref, source_event, reflection_summary,
    ):
        """Prove the accepted reading came from this exact paid decision.

        Paid retention has no capability for selecting older appraisals. That
        remains the explicit, source-selecting private reflection contract.
        """

        inbound = next((item for item in projection.model_result_audits
                        if item.model_result_ref == model_result_ref), None)
        audits = tuple(item for item in projection.proposal_audits
                       if item.model_result_ref == model_result_ref
                       and item.trigger_ref == source_event.event_id)
        if inbound is None or len(audits) != 1:
            return None
        audit = audits[0]
        proposal = DecisionProposal.model_validate_json(audit.proposal_json)
        state = proposal.private_turn_state
        if (state is None or state.keep_impression is not True
                or state.stuck_with_me != reflection_summary
                or proposal.proposal_hash != inbound.proposal_hash):
            return None
        change_ids = {item.change_id for item in proposal.proposed_changes
                      if item.kind == "appraisal_transition"}
        source_evidence_ref = (
            source_event.payload().get("observation_id")
            if source_event.event_type == "ObservationRecorded" else source_event.event_id
        )
        candidates = []
        for appraisal in projection.appraisals:
            origin = appraisal.origin
            if (origin is None
                    or origin.change_id not in change_ids
                    or source_evidence_ref not in {ref.ref_id for ref in appraisal.evidence_refs}):
                continue
            accepted = await _lookup(self._ledger, origin.accepted_event_ref)
            if accepted is None or accepted[0].event_type != "AppraisalAccepted":
                continue
            acceptance = await _lookup(self._ledger, accepted[0].causation_id)
            if acceptance is None or acceptance[0].event_type != "AcceptanceRecorded":
                continue
            typed = await _lookup(self._ledger, acceptance[0].causation_id)
            if (typed is None or typed[0].event_type != "ProposalRecorded"
                    or typed[0].causation_id != audit.event_ref
                    or typed[0].payload().get("proposal_id") != accepted[0].payload().get("proposal_id")):
                continue
            candidates.append((appraisal, accepted, inbound))
        return candidates[0] if len(candidates) == 1 else None

    def _route_groups(
        self,
        located_sources: tuple[_PrivateImpressionLocatedSource, ...],
    ) -> tuple[tuple[_PrivateImpressionLocatedSource, ...], ...]:
        by_process_ref = {
            item.process.trigger_id: item
            for item in located_sources
        }
        return tuple(
            tuple(by_process_ref[item.process_ref] for item in group)
            for group in self._opportunity_runtime.group_sources(
                tuple(item.route_source for item in located_sources)
            )
        )

    async def advance_due_once(self) -> PrivateImpressionRunResult:
        """Route one due private-impression opportunity through this seam."""

        result = await self._drain_one_impl()
        return await self._attach_opportunity_lineage(result)

    async def drain_one(self) -> PrivateImpressionRunResult:
        """Compatibility entry point for replay/tests; production uses ``advance_due_once``."""

        return await self.advance_due_once()

    async def _drain_one_impl(self) -> PrivateImpressionRunResult:
        projection = await _project(self._ledger)
        pending = tuple(
            sorted(
                (
                    item
                    for item in projection.trigger_processes
                    if item.process_kind == "private_impression_deliberation"
                    and item.state != "terminal"
                ),
                key=self._health_process_sort_key,
            )
        )
        if not pending:
            return PrivateImpressionRunResult(trigger_id="", status="idle")
        process = pending[0]
        batch = await self._opportunity_batch(process=process, projection=projection)
        opportunity_identity = batch.identity
        source_events_by_trigger = {
            candidate.trigger_id: source_event
            for candidate, source_event in zip(
                batch.processes,
                batch.source_events,
                strict=True,
            )
        }
        source_event = source_events_by_trigger[process.trigger_id]

        def result(
            *,
            work_status: str | None = None,
            status: str = "processed",
        ) -> PrivateImpressionRunResult:
            return PrivateImpressionRunResult(
                trigger_id=process.trigger_id,
                status=status,
                work_status=work_status,  # type: ignore[arg-type]
                opportunity_ref=opportunity_identity.opportunity_ref,
                source_refs=opportunity_identity.source_refs,
                epoch=opportunity_identity.epoch,
                contract_version=opportunity_identity.contract_version,
            )

        active_processes: list[TriggerProcess] = []
        for candidate in batch.processes:
            current_projection = await _project(self._ledger)
            current_process = next(
                item
                for item in current_projection.trigger_processes
                if item.trigger_id == candidate.trigger_id
            )
            active_candidate = await self._claim_or_reclaim(
                process=current_process,
                source_event=source_events_by_trigger[candidate.trigger_id],
                projection=current_projection,
            )
            if active_candidate is None:
                return result(status="owned_elsewhere")
            active_processes.append(active_candidate)
        active = next(
            item for item in active_processes if item.trigger_id == process.trigger_id
        )
        before = await _project(self._ledger)
        cursor = _cursor(before)
        policy = opportunity_identity.opportunity_policy
        durable_identity = self._durable_opportunity_identity(
            before,
            source_ref=source_event.event_id,
        )
        if durable_identity is not None:
            accepted = any(
                impression.status == "active"
                and set(impression.source_refs).intersection(durable_identity.source_refs)
                for impression in before.private_impressions
            )
            await self._complete_opportunity_processes(
                processes=active_processes,
                source_events=source_events_by_trigger,
                outcome_ref=(
                    f"outcome:{process.trigger_id}:replay:"
                    f"{('accepted' if accepted else 'no-change')}"
                ),
            )
            return result(work_status="accepted" if accepted else "no_change")
        if self._opportunity_runtime.is_expired(
            tuple(
                CausalOpportunitySource(
                    source_ref=source_event_item.event_id,
                    process_ref=process_item.trigger_id,
                    process_kind=process_item.process_kind,
                    causal_key=source_event_item.correlation_id,
                    logical_time=source_event_item.logical_time,
                    policy=policy,
                )
                for process_item, source_event_item in zip(
                    batch.processes,
                    batch.source_events,
                    strict=True,
                )
            ),
            at=before.logical_time or source_event.logical_time,
        ):
            await self._complete_opportunity_processes(
                processes=active_processes,
                source_events=source_events_by_trigger,
                outcome_ref=(
                    f"outcome:{process.trigger_id}:expired:{opportunity_identity.opportunity_ref}"
                ),
            )
            return result(work_status="expired")
        appraisals = tuple(
            next(
                (
                    item
                    for item in before.appraisals
                    if item.origin.accepted_event_ref == source_event_item.event_id
                ),
                None,
            )
            for source_event_item in batch.source_events
        )
        already_interpreted = any(
            appraisal is not None
            and any(
                impression.status == "active"
                and any(
                    ref.startswith(f"appraisal:{appraisal.appraisal_id}:")
                    for ref in impression.interpretation_refs
                )
                for impression in before.private_impressions
            )
            for appraisal in appraisals
        )
        if (
            any(appraisal is None or appraisal.status != "active" for appraisal in appraisals)
            or already_interpreted
        ):
            await self._complete_opportunity_processes(
                processes=active_processes,
                source_events=source_events_by_trigger,
                outcome_ref=f"outcome:{process.trigger_id}:ignored",
            )
            return result(work_status="ignored")
        appraisal = appraisals[0]
        assert appraisal is not None
        capsule = compile_private_impression_reflection_capsule(
            projection=before,
            appraisal=appraisal,
            identity_frame=self._identity_frame,
            world_id=self._ledger.world_id,
            content_reader=self._content_reader,
            life_content_store=self._life_content_store,
            companion_actor_ref=self._companion_actor_ref,
        )
        attempt_id = active.claim_lease.attempt_id
        capability_manifest = _private_impression_capability(
            capsule,
            opportunity_identity=opportunity_identity,
        )
        transition = await self._character_interior.experience(
            InteriorStimulus(
                stimulus_ref=opportunity_identity.opportunity_ref,
                inner_turn_ref=attempt_id,
                world_id=self._ledger.world_id,
                actor_ref=self._companion_actor_ref,
                trigger_ref=source_event.event_id,
                cursor=cursor,
                logical_time=before.logical_time or source_event.logical_time,
                purpose=PRIVATE_IMPRESSION_PURPOSE,
                source_refs=opportunity_identity.source_refs,
                capability_manifest=capability_manifest,
                context_note=(
                    "One or more accepted appraisals are available for private, defeasible "
                    "reflection; durable interpretation remains optional."
                ),
            )
        )
        if transition.status == "technical_failure":
            failure_code = transition.failure_code or "interior_technical_failure"
            # No provider/model result exists when the required tool is not
            # supported. Keep the typed technical outcome and retry lease, but
            # do not manufacture a ModelResultRecorded audit that pollutes an
            # unrelated replay sequence. CharacterInterior topology health is
            # the authority for this preflight qualification failure.
            if failure_code not in _NON_ATTEMPT_TECHNICAL_FAILURES:
                await self._record_technical_failure(
                    process=active,
                    source_event=source_event,
                    failure_code=failure_code,
                )
            return result(work_status="technical_failure")
        if transition.status == "model_no_change":
            model_result_audit = recorded_character_interior_model_result(
                transition,
                purpose=PRIVATE_IMPRESSION_PURPOSE,
                subject_ref=transition.stimulus_ref,
                trigger_ref=source_event.event_id,
                capability_ref=capability_manifest.capability_ref,
                route_tier="thinking",
                route_reason_code="character_interior_private_impression",
                router_version="character-interior-private-impression-transition.1",
                causal_opportunity=opportunity_identity,
            )
            await self._complete_opportunity_processes(
                processes=active_processes,
                source_events=source_events_by_trigger,
                outcome_ref=f"outcome:{process.trigger_id}:no-change",
                model_result_audit=model_result_audit,
            )
            return result(work_status="no_change")
        if len(transition.proposal_refs) != 1:
            # A transitioned result without exactly one accepted typed
            # proposal is an authority failure, never model no-change.
            await self._record_technical_failure(
                process=active,
                source_event=source_event,
                failure_code="invalid_proposal_count",
            )
            return result(work_status="technical_failure")
        accepted_ref = transition.proposal_refs[0]
        await self._complete_opportunity_processes(
            processes=active_processes,
            source_events=source_events_by_trigger,
            outcome_ref=f"outcome:{process.trigger_id}:accepted:{accepted_ref}",
        )
        return result(work_status="accepted")

    async def _opportunity_batch(
        self,
        *,
        process: TriggerProcess,
        projection,
    ) -> _PrivateImpressionOpportunityBatch:
        policy = self._policy_for_process(process)
        candidates = tuple(
            item
            for item in projection.trigger_processes
            if item.process_kind == process.process_kind
            and item.state != "terminal"
            and item.source_evidence_ref is not None
            and self._policy_for_process(item).policy_ref == policy.policy_ref
        )
        located_items: list[_PrivateImpressionLocatedSource] = []
        for candidate in candidates:
            located_items.append(
                _PrivateImpressionLocatedSource(
                    process=candidate,
                    source_event=await self._source_event(candidate),
                    policy=policy,
                )
            )
        located = tuple(located_items)
        groups = self._route_groups(located)
        selected = next(
            (
                group
                for group in groups
                if any(item.process.trigger_id == process.trigger_id for item in group)
            ),
            None,
        )
        if selected is None:
            raise ValueError("private impression process is absent from its source groups")
        source_refs = tuple(item.source_event.event_id for item in selected)
        identity = _private_impression_opportunity_identity(
            world_id=self._ledger.world_id,
            actor_ref=self._companion_actor_ref,
            source_refs=source_refs,
            epoch=source_refs[0],
            policy=policy,
        )
        return _PrivateImpressionOpportunityBatch(
            processes=tuple(item.process for item in selected),
            source_events=tuple(item.source_event for item in selected),
            identity=identity,
        )

    async def _complete_opportunity_processes(
        self,
        *,
        processes: list[TriggerProcess],
        source_events: dict[str, WorldEvent],
        outcome_ref: str,
        model_result_audit: ModelResultRecordedPayload | None = None,
    ) -> None:
        """Terminalize every claimed appraisal source exactly once."""

        for process in processes:
            current = await _project(self._ledger)
            current_process = next(
                item for item in current.trigger_processes if item.trigger_id == process.trigger_id
            )
            if current_process.state == "terminal":
                continue
            await self._complete(
                process=current_process,
                source_event=source_events[process.trigger_id],
                cursor=_cursor(current),
                outcome_ref=outcome_ref,
                model_result_audit=model_result_audit if process is processes[0] else None,
            )

    def _durable_opportunity_identity(
        self,
        projection,
        *,
        source_ref: str | None,
    ) -> CausalOpportunityIdentity | None:
        if source_ref is None:
            return None
        identities: dict[str, CausalOpportunityIdentity] = {}
        for model_audit in reversed(projection.model_result_audits):
            try:
                recorded = RecordedModelResultAudit.model_validate_json(model_audit.audit_json)
            except ValueError:
                continue
            lineage = recorded.character_interior_lineage
            if (
                lineage is None
                or source_ref not in lineage.causal_source_refs
                or lineage.causal_actor_ref != self._companion_actor_ref
                or lineage.purpose != PRIVATE_IMPRESSION_PURPOSE
                or lineage.causal_policy_ref is None
            ):
                continue
            try:
                policy = (
                    CausalOpportunityPolicy.from_ref(lineage.causal_policy_ref)
                    if lineage.causal_policy_ref is not None
                    else DEFAULT_CAUSAL_OPPORTUNITY_POLICY
                )
                identity = CausalOpportunityRuntime(
                    world_id=lineage.causal_world_id,
                    actor_ref=lineage.causal_actor_ref,
                    purpose=lineage.purpose,
                    contract_version=lineage.causal_contract_version,
                ).identity_for_refs(
                    lineage.causal_source_refs,
                    epoch=lineage.causal_epoch,
                    policy=policy,
                )
            except (TypeError, ValueError):
                continue
            if (
                (
                    lineage.causal_policy_version is not None
                    and identity.policy_version != lineage.causal_policy_version
                )
                or identity.opportunity_ref != lineage.opportunity_ref
            ):
                continue
            identities[identity.opportunity_ref] = identity
        if len(identities) != 1:
            return None
        return next(iter(identities.values()))

    def _health_opportunity_identities(
        self,
        projection,
        processes: tuple[TriggerProcess, ...],
    ) -> dict[str, CausalOpportunityIdentity]:
        identities: dict[str, CausalOpportunityIdentity] = {}
        unresolved: list[_PrivateImpressionLocatedSource] = []
        for process in processes:
            source_ref = process.source_evidence_ref
            durable = self._durable_opportunity_identity(projection, source_ref=source_ref)
            if durable is not None:
                identities[process.trigger_id] = durable
                continue
            if source_ref is None:
                continue
            located = self._ledger.lookup_event_commit(source_ref)
            if located is None:
                identities[process.trigger_id] = _private_impression_opportunity_identity(
                    world_id=self._ledger.world_id,
                    actor_ref=self._companion_actor_ref,
                    source_ref=source_ref,
                    policy=self._policy_for_process(process),
                )
                continue
            unresolved.append(
                _PrivateImpressionLocatedSource(
                    process=process,
                    source_event=located[0],
                    policy=self._policy_for_process(process),
                )
            )
        by_policy: dict[str, list[_PrivateImpressionLocatedSource]] = {}
        for item in unresolved:
            by_policy.setdefault(item.policy.policy_ref, []).append(item)
        for policy_ref in sorted(by_policy):
            policy_sources = tuple(by_policy[policy_ref])
            for group in self._route_groups(tuple(policy_sources)):
                source_refs = tuple(item.source_event.event_id for item in group)
                identity = _private_impression_opportunity_identity(
                    world_id=self._ledger.world_id,
                    actor_ref=self._companion_actor_ref,
                    source_refs=source_refs,
                    epoch=source_refs[0],
                    policy=group[0].policy,
                )
                for item in group:
                    identities[item.process.trigger_id] = identity
        return identities

    async def _attach_opportunity_lineage(
        self,
        result: PrivateImpressionRunResult,
    ) -> PrivateImpressionRunResult:
        if not result.trigger_id or result.opportunity_ref is not None:
            return result
        projection = await _project(self._ledger)
        process = next(
            (
                item
                for item in projection.trigger_processes
                if item.trigger_id == result.trigger_id
            ),
            None,
        )
        if process is None or process.source_evidence_ref is None:
            raise RuntimeError("private impression result has no source-bound opportunity")
        identity = self._health_opportunity_identities(projection, (process,)).get(
            process.trigger_id
        )
        if identity is None:
            raise RuntimeError("private impression result has no recoverable opportunity")
        return result.model_copy(
            update={
                "opportunity_ref": identity.opportunity_ref,
                "source_refs": identity.source_refs,
                "epoch": identity.epoch,
                "contract_version": identity.contract_version,
            }
        )

    def health_snapshot(self, world_id: str) -> CausalOpportunityHealth:
        if world_id != self._ledger.world_id:
            raise ValueError("private impression health world does not match the ledger")
        projection = self._ledger.project()
        processes = tuple(
            sorted(
                (
                    item
                    for item in projection.trigger_processes
                    if item.process_kind == "private_impression_deliberation"
                    and item.source_evidence_ref is not None
                ),
                key=self._health_process_sort_key,
            )
        )
        identity_by_trigger = self._health_opportunity_identities(projection, processes)
        identities = tuple(identity_by_trigger[item.trigger_id] for item in processes)
        outcomes = tuple(item.runtime_outcome_ref or "" for item in processes)
        last = processes[-1] if processes else None
        last_identity = identities[-1] if identities else None
        return CausalOpportunityHealth(
            world_id=world_id,
            actor_ref=self._companion_actor_ref,
            purpose=PRIVATE_IMPRESSION_PURPOSE,
            open_count=sum(item.state == "open" for item in processes),
            claimed_count=sum(item.state == "claimed" for item in processes),
            terminal_count=sum(item.state == "terminal" for item in processes),
            deferred_count=0,
            opportunity_count=len({item.opportunity_ref for item in identities}),
            last_source_ref=last.source_evidence_ref if last is not None else None,
            last_opportunity_ref=last_identity.opportunity_ref if last_identity else None,
            no_change_count=sum(item.endswith(":no-change") for item in outcomes),
            ignored_count=sum(":ignored" in item or ":no-source" in item for item in outcomes),
            expired_count=sum(":expired:" in item for item in outcomes),
            accepted_count=sum(
                process.state == "terminal"
                and (outcome.endswith(":accepted") or ":accepted:" in outcome)
                for process, outcome in zip(processes, outcomes, strict=True)
            ),
            technical_failure_count=sum(
                self._process_has_technical_failure(projection, item)
                for item in processes
            ),
        )

    def _health_process_sort_key(
        self,
        process,
    ) -> tuple[datetime, str, str]:
        source_event = self._ledger.lookup_event_commit(process.source_evidence_ref)
        event = source_event[0] if source_event is not None else None
        return (
            event.logical_time if event is not None else datetime.min.replace(tzinfo=UTC),
            event.event_id if event is not None else "",
            process.trigger_id,
        )

    def _policy_for_process(self, process) -> CausalOpportunityPolicy:
        lease = process.claim_lease
        if lease is None:
            return self._policy
        try:
            persisted = causal_opportunity_policy_from_attempt_id(lease.attempt_id)
        except ValueError as exc:
            raise ValueError("private impression claim has an invalid durable policy") from exc
        return persisted or self._policy

    @staticmethod
    def _process_has_technical_failure(projection, process) -> bool:  # type: ignore[no-untyped-def]
        attempt_ids = set(process.attempt_ids)
        for item in projection.model_result_audits:
            if item.trigger_ref != process.source_evidence_ref or item.attempt_id not in attempt_ids:
                continue
            try:
                audit = RecordedModelResultAudit.model_validate_json(item.audit_json)
            except ValueError:
                continue
            if audit.failure_code is not None:
                return True
        return False

    async def _accept(
        self,
        *,
        appraisal: AppraisalProjection,
        draft: PrivateImpressionDraft,
        capsule: PrivateImpressionReflectionCapsule,
        model_result_ref: str,
        source_event: WorldEvent,
        before,
        attempt_id: str,
        author_lineage: _InteriorAuthorLineage | None = None,
        character_interior_lineage: RecordedCharacterInteriorTurnLineage | None = None,
        reflection_contract: str = "character-interior-private-impression-transition.1",
        preserve_reflection_audit_cursor: bool = False,
    ) -> str:
        """Record the typed proposal, then drive the existing acceptance seam."""

        if author_lineage is None or character_interior_lineage is None:
            raise ValueError("private impression acceptance requires author lineage")
        audited = await self._record_character_interior_reflection_audit(
            draft=draft,
            capsule=capsule,
            source_event=source_event,
            before=before,
            attempt_id=attempt_id,
            author_lineage=author_lineage,
            character_interior_lineage=character_interior_lineage,
            defer_commit=preserve_reflection_audit_cursor,
        )
        paid_audit_events: tuple[WorldEvent, ...] = ()
        if preserve_reflection_audit_cursor:
            before, paid_audit_events = audited
        else:
            before = audited
        cursor = _cursor(before)
        identity_cursor = cursor
        if paid_audit_events:
            # Preserve the historical identity formula while committing the
            # complete new materialization atomically. Both audit events are
            # deliberation-only and precede the typed mutation in the batch.
            identity_cursor = ProjectionCursor(
                world_revision=cursor.world_revision,
                deliberation_revision=cursor.deliberation_revision + len(paid_audit_events),
                ledger_sequence=cursor.ledger_sequence + len(paid_audit_events),
            )
        elif preserve_reflection_audit_cursor:
            recorded = await _lookup(
                self._ledger, f"event:private-impression:model-result:{model_result_ref}"
            )
            if recorded is None:
                raise ValueError("paid retention requires its persisted reflection audit")
            identity_cursor = ProjectionCursor(
                world_revision=recorded[1].world_revision,
                deliberation_revision=recorded[1].deliberation_revision,
                ledger_sequence=recorded[1].ledger_sequence,
            )
        logical_time = before.logical_time
        if logical_time is None:
            raise ValueError("private impression acceptance requires authoritative time")
        source_by_ref = {item.source_ref: item for item in capsule.sources}
        selected = tuple(source_by_ref[item] for item in draft.source_refs)
        predecessor_by_ref = {
            f"private-impression:{item.impression_id}": item
            for item in before.private_impressions
            if item.status == "active" and item.subject_ref == appraisal.subject_ref
        }
        predecessors = tuple(predecessor_by_ref[item] for item in draft.predecessor_refs)
        transition_kind = "open" if draft.decision == "retain" else draft.decision
        # This is the identity of one exact role-model decision, not merely of
        # its World facts.  A deliberation-only commit can strand the typed
        # proposal before Acceptance.  A later model pass at the same World
        # revision must therefore derive a fresh proposal instead of silently
        # reusing the old decision authority.
        identity = _digest(
            {
                "contract": reflection_contract,
                "world_id": self._ledger.world_id,
                "source_event_ref": source_event.event_id,
                "transition_kind": transition_kind,
                "predecessor_refs": list(draft.predecessor_refs),
                "reflection_source_refs": list(draft.source_refs),
                "evaluated_cursor": identity_cursor.model_dump(mode="json"),
                "source_capsule_id": capsule.capsule_id,
                "source_model_result": model_result_ref,
                "reflection_digest": _reflection_draft_digest(draft),
            }
        )
        proposal_id = f"proposal:private-impression:{identity}"
        change_id = f"change:private-impression:{identity}"
        transition_id = f"transition:private-impression:{identity}"
        acceptance_id = f"acceptance:private-impression:{identity}"
        accepted_event_id = f"event:private-impression:accepted:{identity}"
        direct_appraisal_refs = tuple(
            AppraisalMeaningRef(
                appraisal_id=value["appraisal_id"],
                hypothesis_id=value["hypothesis_id"],
                source_cluster_ref=value["source_cluster_ref"],
                accepted_change_id=value["accepted_change_id"],
                accepted_transition_id=value["accepted_transition_id"],
            )
            for item in selected
            if item.source_kind == "appraisal"
            for value in (json.loads(item.value_json),)
        )
        appraisal_refs = direct_appraisal_refs
        selected_event_refs = tuple(dict.fromkeys(item.authority_event_ref for item in selected))
        committed_by_ref = {item.event_id: item for item in before.committed_world_event_refs}
        evidence_refs = tuple(
            EvidenceRef(
                ref_id=event_ref,
                evidence_type="committed_world_event",
                claim_purpose="private_hypothesis",
                source_world_revision=committed_by_ref[event_ref].world_revision,
                immutable_hash=committed_by_ref[event_ref].payload_hash,
            )
            for event_ref in selected_event_refs
        )
        origin = PrivateImpressionOrigin(
            change_id=change_id,
            transition_id=transition_id,
            policy_refs=PRIVATE_IMPRESSION_POLICY_REFS,
            accepted_event_ref=accepted_event_id,
        )
        expected_entity_revision = 0
        if draft.decision == "release":
            if len(predecessors) != 1:
                raise ValueError("private impression release requires exactly one predecessor")
            predecessor = predecessors[0]
            expected_entity_revision = predecessor.entity_revision
            impression = predecessor.model_copy(
                update={
                    "entity_revision": predecessor.entity_revision + 1,
                    "status": "released",
                    "reflection_summary": draft.reflection_summary,
                    "confidence_bp": draft.confidence_bp,
                    "last_supported": logical_time,
                    "expiry_condition": draft.expiry_condition,
                    "source_refs": selected_event_refs,
                    "origin": origin,
                }
            )
        else:
            impression = PrivateImpressionProjection(
                impression_id="impression:"
                + _digest(
                    {
                        "world_id": self._ledger.world_id,
                        "appraisal_id": appraisal.appraisal_id,
                        "transition_kind": transition_kind,
                        "predecessor_refs": list(draft.predecessor_refs),
                        "reflection_source_refs": list(draft.source_refs),
                    }
                ),
                entity_revision=1,
                subject_ref=appraisal.subject_ref,
                interpretation_refs=tuple(
                    f"appraisal:{item.appraisal_id}:{item.hypothesis_id}"
                    for item in appraisal_refs
                ),
                source_refs=selected_event_refs,
                reflection_summary=draft.reflection_summary,
                confidence_bp=draft.confidence_bp,
                first_seen=(
                    min(item.first_seen for item in predecessors)
                    if draft.decision == "consolidate"
                    else logical_time
                ),
                last_supported=logical_time,
                expiry_condition=draft.expiry_condition,
                status="active",
                origin=origin,
            )
        payload: dict[str, object] = {
            "change_id": change_id,
            "transition_id": transition_id,
            "transition_kind": transition_kind,
            "expected_entity_revision": expected_entity_revision,
            "predecessor_refs": [
                PrivateImpressionPredecessorRef(
                    impression_id=item.impression_id,
                    expected_entity_revision=item.entity_revision,
                ).model_dump(mode="json")
                for item in predecessors
            ],
            "evidence_refs": [item.model_dump(mode="json") for item in evidence_refs],
            "appraisal_refs": [item.model_dump(mode="json") for item in appraisal_refs],
            "policy_refs": list(PRIVATE_IMPRESSION_POLICY_REFS),
            "reflection_contract": reflection_contract,
            "reflection_decision": draft.decision,
            "reflection_source_refs": list(draft.source_refs),
            "source_model_result": model_result_ref,
            "source_capsule_id": capsule.capsule_id,
            "acceptance_id": acceptance_id,
            "proposal_id": proposal_id,
            "evaluated_world_revision": cursor.world_revision,
            "accepted_change_hash": "0" * 64,
            "impression": impression.model_dump(mode="json"),
        }
        payload["accepted_change_hash"] = private_impression_mutation_hash(payload)
        proposal_event_id = "event:private-impression:proposed:" + identity
        if await _lookup(self._ledger, proposal_event_id) is None:
            proposal_payload = {
                "proposal_id": proposal_id,
                "proposal_kind": "private_impression_transition",
                "proposal_encoding": "typed-authority-v1",
                "authority_contract_ref": "proposal-contract:private-impression.1",
                "transition_kind": transition_kind,
                "change_id": change_id,
                "transition_id": transition_id,
                "evaluated_world_revision": payload["evaluated_world_revision"],
                "expected_entity_revision": expected_entity_revision,
                "proposed_change_hash": payload["accepted_change_hash"],
                "evidence_refs": payload["evidence_refs"],
                "appraisal_refs": payload["appraisal_refs"],
                "policy_refs": payload["policy_refs"],
                "reflection_contract": payload["reflection_contract"],
                "reflection_source_refs": payload["reflection_source_refs"],
                "source_model_result": payload["source_model_result"],
                "source_capsule_id": payload["source_capsule_id"],
                "proposed_mutation": {
                    "event_type": "PrivateImpressionAccepted",
                    "payload_json": json.dumps(
                        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ),
                },
            }
            proposal_event = self._event(
                event_id=proposal_event_id,
                event_type="ProposalRecorded",
                logical_time=logical_time,
                source_event=source_event,
                payload=proposal_payload,
                fallback_identity="private-impression-proposal:" + identity,
            )
            if paid_audit_events:
                # This already-paid choice has no reason to expose a partial
                # audit/proposal between writes. Keep all derived authority in
                # one effect-once CAS transaction; the paid source audit remains
                # the recoverable outbox if this transaction never commits.
                await _commit_at_cursor(
                    self._ledger,
                    (*paid_audit_events, proposal_event,
                     *self._impression_acceptance_events(payload=payload, source_event=source_event)),
                    cursor=cursor,
                    commit_id="commit:private-impression:paid-accepted:" + identity,
                )
                return accepted_event_id
            await _commit_at_cursor(
                self._ledger,
                (proposal_event,),
                cursor=cursor,
                commit_id="commit:private-impression:proposed:" + identity,
            )
        return await self._accept_recorded_impression(payload=payload, source_event=source_event)

    async def _accept_recorded_impression(self, *, payload, source_event) -> str:
        """Finish the exact persisted proposal, preserving its audit capsule."""

        accepted_event_id = payload["impression"]["origin"]["accepted_event_ref"]
        if await _lookup(self._ledger, accepted_event_id) is None:
            after_proposal = await _project(self._ledger)
            logical_time = datetime.fromisoformat(payload["impression"]["last_supported"])
            if (after_proposal.world_revision != payload["evaluated_world_revision"]
                    or after_proposal.logical_time != logical_time):
                raise _PaidRetentionPrefixChanged("paid retention pending proposal prefix changed")
            identity = payload["proposal_id"].removeprefix("proposal:private-impression:")
            await _commit_at_cursor(
                self._ledger,
                self._impression_acceptance_events(payload=payload, source_event=source_event),
                cursor=_cursor(after_proposal),
                commit_id="commit:private-impression:accepted:" + identity,
            )
        return accepted_event_id

    def _impression_acceptance_events(self, *, payload, source_event) -> tuple[WorldEvent, ...]:
        logical_time = datetime.fromisoformat(payload["impression"]["last_supported"])
        identity = payload["proposal_id"].removeprefix("proposal:private-impression:")
        acceptance_event = self._event(
            event_id="event:private-impression:acceptance:" + identity,
            event_type="AcceptanceRecorded",
            logical_time=logical_time,
            source_event=source_event,
            payload={
                "status": "accepted",
                "acceptance_id": payload["acceptance_id"],
                "proposal_id": payload["proposal_id"],
                "evaluated_world_revision": payload["evaluated_world_revision"],
                "accepted_change_id": payload["change_id"],
                "accepted_change_hash": payload["accepted_change_hash"],
            },
            fallback_identity="private-impression-acceptance:" + identity,
        )
        accepted_event = self._event(
            event_id=payload["impression"]["origin"]["accepted_event_ref"],
            event_type="PrivateImpressionAccepted",
            logical_time=logical_time,
            source_event=source_event,
            payload=payload,
            fallback_identity="private-impression-accepted:" + identity,
        )
        return acceptance_event, accepted_event

    async def _record_character_interior_reflection_audit(
        self,
        *,
        draft: PrivateImpressionDraft,
        capsule: PrivateImpressionReflectionCapsule,
        source_event: WorldEvent,
        before,
        attempt_id: str,
        author_lineage: _InteriorAuthorLineage,
        character_interior_lineage: RecordedCharacterInteriorTurnLineage,
        defer_commit: bool = False,
    ):
        """Prepare or persist the role lineage required by immutable V2 authority."""

        reflection_digest = _reflection_draft_digest(draft)
        decision = DecisionProposal(
            proposal_id=(
                "proposal:private-reflection-decision:"
                + _digest(
                    {
                        "capsule_id": capsule.capsule_id,
                        "reflection_digest": reflection_digest,
                    }
                )
            ),
            trigger_ref=source_event.event_id,
            evaluated_world_revision=capsule.world_revision,
            confidence=draft.confidence_bp,
            brief_rationale="private-reflection-draft:" + reflection_digest,
            behavior_tendency="reflect_privately",
            stance="tentative_internal_reading",
            display_strategy="withhold",
            timing_choice="silent",
        )
        model_result_ref = "model-result:" + _digest(
            {
                "model_call_id": author_lineage.model_call_id,
                "response_hash": author_lineage.response_hash.removeprefix("sha256:"),
            }
        )
        if await _lookup(self._ledger, f"event:private-impression:model-result:{model_result_ref}") is not None:
            current = await _project(self._ledger)
            return (current, ()) if defer_commit else current
        audit = RecordedModelResultAudit(
            model_call_id=author_lineage.model_call_id,
            parent_model_call_id=author_lineage.parent_model_call_id,
            model_result_ref=model_result_ref,
            attempt_id=attempt_id,
            route=RecordedModelRoute(
                tier="thinking",
                reason_code="character_interior_private_impression",
                router_version="character-interior-private-impression-transition.1",
            ),
            model_id=author_lineage.model_id,
            model_version=author_lineage.model_version,
            attempted_model_id=author_lineage.model_id,
            attempted_model_version=author_lineage.model_version,
            request_hash=author_lineage.request_hash.removeprefix("sha256:"),
            response_hash=author_lineage.response_hash.removeprefix("sha256:"),
            character_interior_lineage=character_interior_lineage,
            status="proposal_validated",
        )
        audit_json = model_audit_json(audit)
        deliberation_result_id = "deliberation:" + sha256(
            canonical_json(
                {
                    "capsule_id": capsule.capsule_id,
                    "proposal_hash": decision.proposal_hash,
                    "attempt_audits": [json.loads(audit_json)],
                }
            )
        )
        model_payload = ModelResultRecordedPayload(
            audit_contract="model-result-audit.7",
            model_result_ref=model_result_ref,
            deliberation_result_id=deliberation_result_id,
            proposal_hash=decision.proposal_hash,
            model_call_id=author_lineage.model_call_id,
            parent_model_call_id=author_lineage.parent_model_call_id,
            attempt_id=attempt_id,
            capsule_id=capsule.capsule_id,
            trigger_ref=source_event.event_id,
            evaluated_world_revision=capsule.world_revision,
            # CharacterInterior exposes one final semantic result.  Its
            # same-author correction ordinal and parent are closed inside the
            # nested lineage; this outer transaction therefore contains one
            # persisted result, not an invented missing first attempt.
            attempt_index=0,
            attempt_count=1,
            audit_json=audit_json,
            audit_hash=sha256(audit_json),
        )
        proposal_json = canonical_json(decision.model_dump(mode="json"))
        proposal_payload = ProposalRecordedV2Payload(
            proposal_id=decision.proposal_id,
            proposal_kind="decision",
            model_result_ref=model_result_ref,
            deliberation_result_id=deliberation_result_id,
            model_call_id=author_lineage.model_call_id,
            attempt_id=attempt_id,
            capsule_id=capsule.capsule_id,
            trigger_ref=source_event.event_id,
            evaluated_world_revision=capsule.world_revision,
            proposal_json=proposal_json,
            proposal_hash=decision.proposal_hash,
        )
        events = (
            self._event(
                event_id=f"event:private-impression:model-result:{model_result_ref}",
                event_type="ModelResultRecorded",
                logical_time=source_event.logical_time,
                source_event=source_event,
                payload=model_payload.model_dump(mode="json"),
                fallback_identity=f"private-impression-model-result:{model_result_ref}",
            ),
            self._event(
                event_id=f"event:private-impression:model-proposal:{decision.proposal_id}",
                event_type="ProposalRecorded",
                logical_time=source_event.logical_time,
                source_event=source_event,
                payload=proposal_payload.model_dump(mode="json"),
                fallback_identity=f"private-impression-model-proposal:{decision.proposal_id}",
            ),
        )
        if defer_commit:
            return before, events
        await _commit_at_cursor(
            self._ledger,
            events,
            cursor=_cursor(before),
            commit_id="commit:private-impression:character-interior-audit:"
            + _digest([model_result_ref, decision.proposal_id]),
        )
        return await _project(self._ledger)


    def _event(
        self,
        *,
        event_id: str,
        event_type: str,
        logical_time,
        source_event: WorldEvent,
        payload: dict,
        fallback_identity: str,
    ) -> WorldEvent:
        return WorldEvent.from_payload(
            schema_version="world-v2.1",
            event_id=event_id,
            event_type=event_type,
            world_id=self._ledger.world_id,
            logical_time=logical_time,
            created_at=max(source_event.created_at, logical_time),
            actor=self._owner_id,
            source=self._source,
            trace_id=source_event.trace_id,
            causation_id=source_event.event_id,
            correlation_id=source_event.correlation_id,
            idempotency_key=domain_idempotency_key(
                event_type=event_type, world_id=self._ledger.world_id, payload=payload
            )
            or fallback_identity,
            payload=payload,
        )

    async def _source_event(self, process: TriggerProcess) -> WorldEvent:
        if process.source_evidence_ref is None:
            raise ValueError("private impression trigger has no appraisal source")
        stored = await _lookup(self._ledger, process.source_evidence_ref)
        if stored is None or stored[0].event_type != "AppraisalAccepted":
            raise ValueError("private impression appraisal authority is unavailable")
        if process.trigger_ref != f"impression:{process.source_evidence_ref}":
            raise ValueError("private impression trigger does not bind its appraisal")
        return stored[0]

    async def _claim_or_reclaim(
        self, *, process: TriggerProcess, source_event: WorldEvent, projection
    ) -> TriggerProcess | None:
        at = projection.logical_time or source_event.logical_time
        if process.state == "claimed" and process.claim_lease is not None:
            # A provider crossing happens only in the same drain that first
            # commits this claim.  Any later drain observes an already-claimed
            # process and must not resume that external call identity, even
            # when the owner string matches after a daemon restart.  Lease
            # expiry opens a new reclaim attempt instead.
            if at < process.claim_lease.expires_at:
                return None
        if len(process.attempt_ids) >= _PRIVATE_IMPRESSION_MAX_ATTEMPTS:
            # A reflection whose model output repeatedly fails authority
            # validation must not burn unbounded provider calls.  Terminal the
            # process after a bounded number of attempts. A new epoch can only
            # come from a later accepted appraisal source, not from retrying
            # this same source or advancing the clock.
            # Completion requires a live claim lease, so first reclaim with a
            # fresh lease at the current logical time, then complete it.
            exhausted_attempt_id = (
                "attempt:private-impression:exhausted:"
                + _digest(process.trigger_id)
                + ":policy="
                + self._policy_for_process(process).policy_ref
            )
            exhausted = process.model_copy(
                update={
                    "state": "claimed",
                    "claim_lease": ClaimLease(
                        owner_id=self._owner_id,
                        attempt_id=exhausted_attempt_id,
                        acquired_at=at,
                        expires_at=at + timedelta(seconds=self._lease_seconds),
                    ),
                    "attempt_ids": (*process.attempt_ids, exhausted_attempt_id),
                }
            )
            exhausted_event_type = (
                "TriggerProcessClaimed"
                if process.state == "open"
                else "TriggerProcessReclaimed"
            )
            exhausted_payload = {"process": exhausted.model_dump(mode="json")}
            exhausted_identity = domain_idempotency_key(
                event_type=exhausted_event_type,
                world_id=self._ledger.world_id,
                payload=exhausted_payload,
            )
            if exhausted_identity is None:
                raise ValueError("private impression exhausted claim has no domain identity")
            exhausted_event = WorldEvent.from_payload(
                schema_version="world-v2.1",
                event_id=(
                    "event:private-impression:"
                    + exhausted_event_type.lower()
                    + ":"
                    + _digest([process.trigger_id, exhausted_attempt_id])
                ),
                world_id=self._ledger.world_id,
                event_type=exhausted_event_type,
                logical_time=at,
                created_at=source_event.created_at,
                actor=self._owner_id,
                source=self._source,
                trace_id=source_event.trace_id,
                causation_id=source_event.event_id,
                correlation_id=source_event.correlation_id,
                idempotency_key=exhausted_identity,
                payload=exhausted_payload,
            )
            await _commit(
                self._ledger,
                (exhausted_event,),
                world_revision=projection.world_revision,
                deliberation_revision=projection.deliberation_revision,
                commit_id="commit:private-impression:exhausted-claim:"
                + _digest([process.trigger_id, exhausted_attempt_id]),
            )
            # The reclaim advanced the ledger; complete against the cursor
            # that includes it so the completion CAS is not stale.
            after_exhausted = await _project(self._ledger)
            await self._complete(
                process=exhausted,
                source_event=source_event,
                cursor=_cursor(after_exhausted),
                outcome_ref=f"outcome:{process.trigger_id}:attempts-exhausted",
            )
            return None
        attempt_id = (
            "attempt:private-impression:"
            + _digest({"trigger_id": process.trigger_id, "attempt": len(process.attempt_ids) + 1})
            + ":policy="
            + self._policy_for_process(process).policy_ref
        )
        claimed = process.model_copy(
            update={
                "state": "claimed",
                "claim_lease": ClaimLease(
                    owner_id=self._owner_id,
                    attempt_id=attempt_id,
                    acquired_at=at,
                    expires_at=at + timedelta(seconds=self._lease_seconds),
                ),
                "attempt_ids": (*process.attempt_ids, attempt_id),
            }
        )
        event_type = (
            "TriggerProcessClaimed" if process.state == "open" else "TriggerProcessReclaimed"
        )
        payload = {"process": claimed.model_dump(mode="json")}
        identity = domain_idempotency_key(
            event_type=event_type, world_id=self._ledger.world_id, payload=payload
        )
        if identity is None:
            raise ValueError("private impression claim has no domain identity")
        event = WorldEvent.from_payload(
            schema_version="world-v2.1",
            event_id=(
                "event:private-impression:"
                + event_type.lower()
                + ":"
                + _digest([process.trigger_id, attempt_id])
            ),
            world_id=self._ledger.world_id,
            event_type=event_type,
            logical_time=at,
            created_at=source_event.created_at,
            actor=self._owner_id,
            source=self._source,
            trace_id=source_event.trace_id,
            causation_id=source_event.event_id,
            correlation_id=source_event.correlation_id,
            idempotency_key=identity,
            payload=payload,
        )
        await _commit(
            self._ledger,
            (event,),
            world_revision=projection.world_revision,
            deliberation_revision=projection.deliberation_revision,
            commit_id="commit:private-impression:claim:"
            + _digest([process.trigger_id, attempt_id]),
        )
        return claimed

    async def _record_technical_failure(
        self,
        *,
        process,
        source_event: WorldEvent,
        failure_code: str,
    ) -> None:
        if process.claim_lease is None:
            raise ValueError("private impression technical audit requires a claim")
        await self._record_failure_audit(
            source_event=source_event,
            attempt_id=process.claim_lease.attempt_id,
            failure_code=failure_code,
        )

    async def _record_failure_audit(
        self, *, source_event: WorldEvent, attempt_id: str, failure_code: str,
        created_at: datetime | None = None,
    ) -> None:
        current = await _project(self._ledger)
        model_payload = technical_character_interior_model_result(
            purpose=PRIVATE_IMPRESSION_PURPOSE,
            trigger_ref=source_event.event_id,
            attempt_id=attempt_id,
            evaluated_world_revision=current.world_revision,
            failure_code=failure_code,
        )
        if any(
            item.model_result_ref == model_payload.model_result_ref
            for item in current.model_result_audits
        ):
            return
        at = current.logical_time or source_event.logical_time
        payload = model_payload.model_dump(mode="json")
        identity = domain_idempotency_key(
            event_type="ModelResultRecorded",
            world_id=self._ledger.world_id,
            payload=payload,
        )
        event = WorldEvent.from_payload(
            schema_version="world-v2.1",
            event_id="event:private-impression:technical:" + model_payload.model_result_ref,
            world_id=self._ledger.world_id,
            event_type="ModelResultRecorded",
            logical_time=at,
            created_at=max(source_event.created_at, at, created_at or at),
            actor=self._owner_id,
            source=self._source,
            trace_id=source_event.trace_id,
            causation_id=source_event.event_id,
            correlation_id=source_event.correlation_id,
            idempotency_key=identity or "world-v2:private-impression:technical:" + model_payload.model_result_ref,
            payload=payload,
        )
        await _commit_at_cursor(
            self._ledger,
            (event,),
            cursor=_cursor(current),
            commit_id="commit:private-impression:technical:" + model_payload.model_result_ref,
        )

    async def _complete(
        self,
        *,
        process: TriggerProcess,
        source_event: WorldEvent,
        cursor: ProjectionCursor,
        outcome_ref: str,
        model_result_audit: ModelResultRecordedPayload | None = None,
    ) -> None:
        if process.claim_lease is None:
            raise ValueError("private impression completion requires a claimed process")
        projection = await _project_at(self._ledger, cursor)
        at = max(
            projection.logical_time or source_event.logical_time,
            process.claim_lease.acquired_at,
        )
        if at > process.claim_lease.expires_at:
            raise ValueError("private impression lease expired before completion")
        payload = {
            "trigger_id": process.trigger_id,
            "owner_id": process.claim_lease.owner_id,
            "attempt_id": process.claim_lease.attempt_id,
            "completed_at": at.isoformat(),
            "runtime_outcome_ref": outcome_ref,
            **(
                {
                    "character_interior_model_result": (
                        model_result_audit.model_dump(mode="json")
                    )
                }
                if model_result_audit is not None
                else {}
            ),
        }
        event = WorldEvent.from_payload(
            schema_version="world-v2.1",
            event_id="event:private-impression:completed:"
            + _digest([process.trigger_id, process.claim_lease.attempt_id]),
            world_id=self._ledger.world_id,
            event_type="TriggerProcessCompleted",
            logical_time=at,
            created_at=source_event.created_at,
            actor=self._owner_id,
            source=self._source,
            trace_id=source_event.trace_id,
            causation_id=source_event.event_id,
            correlation_id=source_event.correlation_id,
            idempotency_key="world-v2:private-impression:completion:"
            + _digest([self._ledger.world_id, process.trigger_id, process.claim_lease.attempt_id]),
            payload=payload,
        )
        await _commit_at_cursor(
            self._ledger,
            (event,),
            cursor=cursor,
            commit_id="commit:private-impression:completed:"
            + _digest([process.trigger_id, process.claim_lease.attempt_id, outcome_ref]),
        )


class _PrivateImpressionInteriorAuthorityHandler:
    """Validate and accept one Interior-authored private reflection proposal."""

    proposal_type = "private_impression_transition"

    def __init__(self, runtime: PrivateImpressionTriggerRuntime) -> None:
        self._runtime = runtime

    async def prepare(
        self,
        request: _AuthorityRequest,
        proposal: dict[str, object],
    ) -> object:
        manifest = request.capability_manifest
        if (
            request.purpose != "private_impression_reflection"
            or manifest is None
            or manifest.capability_kind != "private_impression_reflection"
            or proposal.get("contract") != "character-interior-typed-proposal.1"
            or proposal.get("proposal_type") != self.proposal_type
            or proposal.get("purpose") != request.purpose
            or proposal.get("capability_ref") != manifest.capability_ref
            or proposal.get("capability_payload_hash") != manifest.payload_hash
            or proposal.get("source_refs") != list(manifest.source_refs)
        ):
            raise ValueError("private impression proposal authority binding is invalid")
        raw_identity = manifest.payload.get("causal_opportunity")
        try:
            if isinstance(raw_identity, dict) and isinstance(raw_identity.get("source_refs"), list):
                raw_identity = {
                    **raw_identity,
                    "source_refs": tuple(raw_identity["source_refs"]),
                }
            opportunity_identity = CausalOpportunityIdentity.model_validate(raw_identity)
        except ValueError as exc:
            raise ValueError("private impression capability lacks a valid causal opportunity") from exc
        if (
            opportunity_identity.world_id != request.world_id
            or opportunity_identity.actor_ref != request.actor_ref
            or opportunity_identity.purpose != request.purpose
            or opportunity_identity.opportunity_ref != request.subject_ref
            or opportunity_identity.source_refs != request.subject_source_refs
            or request.trigger_ref not in opportunity_identity.source_refs
            or any(source_ref not in manifest.source_refs for source_ref in opportunity_identity.source_refs)
        ):
            raise ValueError("private impression InnerTurn is not actor-bound to its opportunity")
        raw_capsule = manifest.payload.get("reflection_capsule")
        if not isinstance(raw_capsule, dict):
            raise ValueError("private impression capability lacks its reflection capsule")
        capsule = PrivateImpressionReflectionCapsule.model_validate_json(
            json.dumps(
                raw_capsule,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        if (
            capsule.world_id != request.world_id
            or capsule.world_revision != request.cursor.world_revision
            or capsule.deliberation_revision != request.cursor.deliberation_revision
            or capsule.ledger_sequence != request.cursor.ledger_sequence
        ):
            raise ValueError("private impression capsule is not pinned to the InnerTurn")
        payload = proposal.get("payload")
        if not isinstance(payload, dict) or payload.get("contract") != (
            "character-interior-private-impression-transition.1"
        ):
            raise ValueError("private impression transition payload is invalid")
        expected = {
            "contract",
            "decision",
            "predecessor_refs",
            "source_refs",
            "reflection_summary",
            "confidence_bp",
            "expiry_condition",
        }
        if set(payload) != expected:
            raise ValueError("private impression transition has unsupported fields")
        raw_draft = {
            "decision": payload["decision"],
            "predecessor_refs": payload["predecessor_refs"],
            "source_refs": payload["source_refs"],
            "reflection_summary": payload["reflection_summary"],
            "confidence": payload["confidence_bp"],
            "expiry_condition": payload["expiry_condition"],
        }
        if raw_draft["decision"] == "retain":
            raw_draft.pop("predecessor_refs")
        draft = _materialize_draft(
            json.dumps(raw_draft, ensure_ascii=False, separators=(",", ":")),
            capsule=capsule,
        )
        if draft is None:
            raise ValueError("private impression transition cannot be no-change")
        source = await _lookup(self._runtime._ledger, request.trigger_ref)  # noqa: SLF001
        if source is None or source[0].event_type != "AppraisalAccepted":
            raise ValueError("private impression trigger authority is unavailable")
        before = await _project_at(self._runtime._ledger, request.cursor)  # noqa: SLF001
        for source_ref in opportunity_identity.source_refs:
            located = await _lookup(self._runtime._ledger, source_ref)  # noqa: SLF001
            if located is None or located[0].event_type != "AppraisalAccepted":
                raise ValueError("private impression merged appraisal authority is unavailable")
        appraisal = next(
            (
                item
                for item in before.appraisals
                if item.appraisal_id == capsule.anchor_appraisal_id
                and item.origin.accepted_event_ref == source[0].event_id
            ),
            None,
        )
        if appraisal is None or appraisal.status not in {"active", "expired"}:
            raise ValueError("private impression anchor appraisal is no longer readable")
        lineage = request.author_lineage
        if lineage is None:
            raise ValueError("private impression transition lacks character author lineage")
        model_result_ref = "model-result:" + _digest(
            {
                "model_call_id": lineage.model_call_id,
                "response_hash": lineage.response_hash.removeprefix("sha256:"),
            }
        )
        return _PreparedPrivateImpressionAuthority(
            appraisal=appraisal,
            draft=draft,
            capsule=capsule,
            model_result_ref=model_result_ref,
            source_event=source[0],
            before=before,
            attempt_id=request.inner_turn_id,
            author_lineage=lineage,
            character_interior_lineage=RecordedCharacterInteriorTurnLineage(
                inner_turn_id=request.inner_turn_id,
                purpose=request.purpose,
                opportunity_ref=request.subject_ref,
                **causal_opportunity_lineage_fields(
                    opportunity_identity,
                    subject_ref=request.subject_ref,
                ),
                snapshot_id=request.snapshot_id,
                snapshot_hash=request.snapshot_hash,
                capability_ref=manifest.capability_ref,
                author_model_id=lineage.model_id,
                author_model_version=lineage.model_version,
                author_model_call_id=lineage.model_call_id,
                author_request_hash=lineage.request_hash,
                author_response_hash=lineage.response_hash,
                author_attempt_ordinal=lineage.attempt_ordinal,
                author_parent_model_call_id=lineage.parent_model_call_id,
                private_self_lineage_hash=request.private_self_lineage_hash,
                decision_hash=request.decision_hash,
            ),
            reflection_contract="character-interior-private-impression-transition.1",
        )

    async def submit(
        self,
        request: _AuthorityRequest,
        prepared: tuple[object, ...],
    ) -> tuple[str, ...]:
        del request
        if len(prepared) != 1 or not isinstance(
            prepared[0], _PreparedPrivateImpressionAuthority
        ):
            raise ValueError("private impression authority needs one prepared transition")
        item = prepared[0]
        accepted = await self._runtime._accept(  # noqa: SLF001 - exact typed authority
            appraisal=item.appraisal,
            draft=item.draft,
            capsule=item.capsule,
            model_result_ref=item.model_result_ref,
            source_event=item.source_event,
            before=item.before,
            attempt_id=item.attempt_id,
            author_lineage=item.author_lineage,
            character_interior_lineage=item.character_interior_lineage,
            reflection_contract=item.reflection_contract,
        )
        return (accepted,)


@dataclass(frozen=True, slots=True)
class _PreparedPrivateImpressionAuthority:
    appraisal: AppraisalProjection
    draft: PrivateImpressionDraft
    capsule: PrivateImpressionReflectionCapsule
    model_result_ref: str
    source_event: WorldEvent
    before: object
    attempt_id: str
    author_lineage: _InteriorAuthorLineage
    character_interior_lineage: RecordedCharacterInteriorTurnLineage
    reflection_contract: str


async def _project(ledger):
    if getattr(ledger, "blocks_event_loop", False):
        return await asyncio.to_thread(ledger.project)
    return ledger.project()


async def _project_at(ledger, cursor: ProjectionCursor):
    if getattr(ledger, "blocks_event_loop", False):
        return await asyncio.to_thread(ledger.project_at, cursor)
    return ledger.project_at(cursor)


async def _lookup(ledger, event_id: str):
    if getattr(ledger, "blocks_event_loop", False):
        return await asyncio.to_thread(ledger.lookup_event_commit, event_id)
    return ledger.lookup_event_commit(event_id)


async def _commit(ledger, events, *, world_revision, deliberation_revision, commit_id):
    if getattr(ledger, "blocks_event_loop", False):
        return await asyncio.to_thread(
            ledger.commit,
            events,
            expected_world_revision=world_revision,
            expected_deliberation_revision=deliberation_revision,
            commit_id=commit_id,
        )
    return ledger.commit(
        events,
        expected_world_revision=world_revision,
        expected_deliberation_revision=deliberation_revision,
        commit_id=commit_id,
    )


async def _commit_at_cursor(ledger, events, *, cursor, commit_id):
    if getattr(ledger, "blocks_event_loop", False):
        return await asyncio.to_thread(
            ledger.commit_at_cursor, events, expected_cursor=cursor, commit_id=commit_id
        )
    return ledger.commit_at_cursor(events, expected_cursor=cursor, commit_id=commit_id)


def _cursor(projection) -> ProjectionCursor:
    return ProjectionCursor(
        world_revision=projection.world_revision,
        deliberation_revision=projection.deliberation_revision,
        ledger_sequence=projection.ledger_sequence,
    )


__all__ = [
    "DEFAULT_PRIVATE_IMPRESSION_DAILY_MODEL_CALL_LIMIT",
    "DEFAULT_PRIVATE_IMPRESSION_IDLE_AFTER_USER_SECONDS",
    "DEFAULT_PRIVATE_IMPRESSION_MIN_INTERVAL_SECONDS",
    "EXPIRY_CONDITIONS",
    "PrivateImpressionDraft",
    "PrivateImpressionDrainPolicy",
    "PrivateImpressionGateDecision",
    "PrivateImpressionReflectionCapsule",
    "PrivateImpressionReflectionSource",
    "PrivateImpressionRunResult",
    "PrivateImpressionTriggerOpener",
    "PrivateImpressionTriggerRuntime",
    "compile_paid_private_impression_draft",
    "compile_private_impression_reflection_capsule",
    "evaluate_private_impression_drain_gate",
    "private_impression_drain_policy_from_settings",
    "private_impression_opportunity",
    "record_private_impression_gate",
    "recorded_private_impression_gates",
]
