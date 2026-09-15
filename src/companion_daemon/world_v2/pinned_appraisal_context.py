"""One accepted private-history inventory for author and source consumers.

This is a context read, never an appraisal producer or semantic reviewer. The
ledger adapter joins exact native acceptances; consumers receive immutable
values at the same cursor, independently of the short working Capsule budget.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from .appraisal_events import AppraisalAcceptedPayload, AppraisalSupersededPayload
from .schemas import AppraisalProjection, CommittedWorldEventRef, FrozenModel, ProjectionCursor

MAX_PRESENT_APPRAISALS = 128


def appraisal_context_envelopes(context: PinnedAppraisalContext) -> tuple[dict, ...]:
    """Present the same complete native values to either context consumer."""
    from .context_capsule import ResolvedSourceBinding, source_bindings_hash

    result = []
    for record in context.records:
        value = record.value.model_dump(mode="json")
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        binding = ResolvedSourceBinding(
            ref=record.source.event_id, source_kind="committed_event",
            source_world_revision=record.source.world_revision,
            authority_type=record.source.event_type,
            immutable_hash=record.source.payload_hash,
        )
        result.append({
            "item_ref": record.value.appraisal_id, "privacy_class": "private",
            "source_hash": source_bindings_hash((binding,)),
            "value_hash": hashlib.sha256(payload.encode()).hexdigest(),
            "source_bindings": [binding.model_dump(mode="json")], "value": value,
        })
    return tuple(result)


class PinnedAppraisalRecord(FrozenModel):
    value: AppraisalProjection
    source: CommittedWorldEventRef

    @model_validator(mode="after")
    def acceptance_matches(self):
        if (self.value.origin.accepted_event_ref != self.source.event_id
                or self.source.event_type not in {"AppraisalAccepted", "AppraisalSuperseded"}
                or self.value.status != "active"):
            raise ValueError("appraisal inventory requires its active native acceptance")
        return self


class PinnedAppraisalContext(FrozenModel):
    contract: Literal["pinned-appraisal-context.1"] = "pinned-appraisal-context.1"
    world_id: str
    owner_actor_ref: str
    snapshot_hash: str = Field(min_length=64, max_length=64)
    cursor: ProjectionCursor
    logical_at: datetime
    records: tuple[PinnedAppraisalRecord, ...] = Field(max_length=MAX_PRESENT_APPRAISALS)
    omitted_count: int = Field(ge=0)

    @model_validator(mode="after")
    def records_match_pin(self):
        if self.logical_at.tzinfo is None:
            raise ValueError("appraisal inventory time must be aware")
        refs = [r.value.appraisal_id for r in self.records]
        if len(set(refs)) != len(refs):
            raise ValueError("appraisal inventory duplicates a record")
        for record in self.records:
            if (record.source.world_revision > self.cursor.world_revision
                    or record.source.logical_time > self.logical_at
                    or not record.value.accepted_at <= self.logical_at < record.value.expires_at):
                raise ValueError("appraisal inventory record lies outside its pinned time")
        return self


def compile_pinned_appraisals(*, projection, query, ledger) -> PinnedAppraisalContext:
    """Read native values at the caller's already validated World/actor cursor."""
    if query.logical_time is None:
        raise ValueError("appraisal inventory needs logical time")
    candidates = sorted(
        (a for a in projection.appraisals
         if a.status == "active" and a.accepted_at <= query.logical_time < a.expires_at),
        key=lambda a: (a.accepted_at, a.appraisal_id), reverse=True,
    )
    committed = {e.event_id: e for e in projection.committed_world_event_refs}
    records = []
    for appraisal in candidates[:MAX_PRESENT_APPRAISALS]:
        ref = appraisal.origin.accepted_event_ref
        source = committed.get(ref)
        located = ledger.lookup_event_commit(ref)
        if source is None or located is None:
            raise ValueError("appraisal inventory acceptance is unavailable")
        event, commit = located
        if (event.event_id != ref or event.event_type != source.event_type
                or event.payload_hash != source.payload_hash
                or commit.world_revision != source.world_revision):
            raise ValueError("appraisal inventory native event differs from its pin")
        if event.event_type == "AppraisalAccepted":
            native = AppraisalAcceptedPayload.model_validate_json(event.payload_json).appraisal
        elif event.event_type == "AppraisalSuperseded":
            native = AppraisalSupersededPayload.model_validate_json(event.payload_json).successor
        else:
            raise ValueError("appraisal inventory has another event authority")
        if native != appraisal:
            raise ValueError("appraisal inventory differs from its accepted native value")
        records.append(PinnedAppraisalRecord(value=native, source=source))
    return PinnedAppraisalContext(
        world_id=query.world_id, owner_actor_ref=query.actor_ref,
        snapshot_hash=query.snapshot_hash, cursor=query.cursor, logical_at=query.logical_time,
        records=tuple(records), omitted_count=max(0, len(candidates) - MAX_PRESENT_APPRAISALS),
    )
