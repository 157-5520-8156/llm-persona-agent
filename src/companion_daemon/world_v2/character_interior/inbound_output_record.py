"""Stable completed-output evidence; no author, retrieval, or acceptance operation."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field

from ..deliberation import (
    MAX_MODEL_OUTPUT_BYTES,
    ModelInput,
    ModelOutput,
    PhysicalProviderInvocationAudit,
    ProviderSubcallAudit,
    AuthoredCandidateInvocationAudit,
    _bounded_raw,
    _checked_output,
)
from ..proposal_envelope import validate_proposal_envelope
from ..recall_audit import PrefetchPresentationAudit, RecallAuditTrace
from ..recall_runtime import (
    PresentedPrefetchTrace,
    TrustedRecallTrace,
    verify_trusted_recall_trace,
)
from ..schema_core import FrozenModel
from ..schemas import ProjectionCursor
from .contracts import InnerDecision, InnerLifeSnapshot, _InteriorAuthorLineage, _PrivateSelfLineage
from .ports import _InteriorRoleRequest, _InteriorRoleResult
from .turn_store import _CharacterInteriorTurnStore, _TurnCoordinationRequest

DECISION_CONTRACT = "character-interior-inbound-turn-decision.2"
LEGACY_DECISION_CONTRACT = "character-interior-inbound-turn-decision.1"
PREPARED_CONTRACT = "character-interior-prepared-turn.2"
LEGACY_PREPARED_CONTRACT = "character-interior-prepared-turn.1"
# Only the new inbound selective-Recall carrier has this whole-record bound.
# Legacy prepared bytes and limits are unchanged. Nothing is truncated.
MAX_INBOUND_PREPARED_BYTES = 3 * MAX_MODEL_OUTPUT_BYTES


def _validated_initial_snapshot(
    prepared: dict[str, object],
    *,
    result: _InteriorRoleResult,
    snapshot: InnerLifeSnapshot,
    private: _PrivateSelfLineage,
) -> InnerLifeSnapshot | None:
    """Validate the original Core checkpoint anchor, never a supplied trace alone."""
    contract = prepared.get("contract")
    if contract == LEGACY_PREPARED_CONTRACT:
        if "recall_initial_snapshot" in prepared:
            raise ValueError("legacy prepared turn cannot acquire an initial snapshot")
        return None
    if contract != PREPARED_CONTRACT:
        raise ValueError("unsupported prepared turn contract")
    if len(_canonical(prepared).encode("utf-8")) > MAX_INBOUND_PREPARED_BYTES:
        raise ValueError("inbound prepared turn exceeds its byte limit")
    if (
        result.decision.get("contract") != DECISION_CONTRACT
        or private.relation != "selective_recall"
        or private.initial_author_lineage is None
        or private.final_author_lineage != result.author_lineage
    ):
        raise ValueError("new prepared turn requires an authored inbound Recall decision")
    initial = InnerLifeSnapshot.model_validate_json(_canonical(prepared["recall_initial_snapshot"]))
    if (
        (initial.snapshot_id, initial.snapshot_hash)
        != (private.initial_snapshot_id, private.initial_snapshot_hash)
        or (snapshot.snapshot_id, snapshot.snapshot_hash)
        != (private.final_snapshot_id, private.final_snapshot_hash)
        or any(
            getattr(initial, name) != getattr(snapshot, name)
            for name in (
                "world_id",
                "actor_ref",
                "cursor",
                "logical_time",
                "capability_scope",
                "viewer_scope",
                "privacy_scope",
                "context_compiler",
            )
        )
    ):
        raise ValueError("inbound Recall initial snapshot escaped its original pin")
    return initial


def _canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


class _RecordedOutput(ModelOutput):
    """Same output grammar, with recorded rather than process-sealed Recall."""

    physical_provider_audits: tuple[PhysicalProviderInvocationAudit, ...] = Field(
        default=(), max_length=1
    )
    provider_subcall_audits: tuple[ProviderSubcallAudit, ...] = Field(default=(), max_length=16)
    authored_candidate_audits: tuple[AuthoredCandidateInvocationAudit, ...] = Field(
        default=(), max_length=8
    )
    recall_trace: RecallAuditTrace | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    prefetch_trace: RecallAuditTrace | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    presented_prefetch_traces: tuple[PrefetchPresentationAudit, ...] = Field(
        default=(), max_length=4, exclude_if=lambda value: not value
    )


class _OutputRecord(FrozenModel):
    contract: Literal["character-interior-inbound-output.1"] = "character-interior-inbound-output.1"
    world_id: str
    actor_ref: str
    cursor: ProjectionCursor
    inner_turn_id: str
    snapshot_id: str
    snapshot_hash: str
    capability_ref: str
    capability_payload_hash: str
    model_input_hash: str
    source_refs: tuple[str, ...]
    proposal_hash: str
    author_lineage: _InteriorAuthorLineage
    output: _RecordedOutput


def _recorded_output(output: ModelOutput) -> _RecordedOutput:
    output = _checked_output(output)
    material = output.model_dump(
        mode="json", exclude={"recall_trace", "prefetch_trace", "presented_prefetch_traces"}
    )
    for name in (
        "physical_provider_audits",
        "provider_subcall_audits",
        "authored_candidate_audits",
    ):
        material[name] = [item.model_dump(mode="json") for item in getattr(output, name)]
    for name in ("recall_trace", "prefetch_trace"):
        trace = getattr(output, name)
        if trace is not None:
            material[name] = verify_trusted_recall_trace(trace).model_dump(mode="json")
    if output.presented_prefetch_traces:
        material["presented_prefetch_traces"] = [
            item.recorded().model_dump(mode="json") for item in output.presented_prefetch_traces
        ]
    return _RecordedOutput.model_validate_json(_canonical(material))


def stable_output_hash(output: ModelOutput) -> str:
    return _hash(_recorded_output(output).model_dump(mode="json"))


def record_inbound_output(
    output: ModelOutput, *, request: _InteriorRoleRequest, lineage: _InteriorAuthorLineage
) -> dict[str, object]:
    manifest = request.capability_manifest
    if manifest is None or request.snapshot.cursor is None:
        raise ValueError("inbound_output_record.source_unavailable")
    body = _recorded_output(output)
    proposal = validate_proposal_envelope(body.raw_proposal)
    record = _OutputRecord(
        world_id=request.snapshot.world_id,
        actor_ref=request.snapshot.actor_ref,
        cursor=request.snapshot.cursor,
        inner_turn_id=request.inner_turn_id,
        snapshot_id=request.snapshot.snapshot_id,
        snapshot_hash=request.snapshot.snapshot_hash,
        capability_ref=manifest.capability_ref,
        capability_payload_hash=manifest.payload_hash,
        model_input_hash=manifest.payload["model_input_hash"],
        source_refs=manifest.source_refs,
        proposal_hash=proposal.proposal_hash,
        author_lineage=lineage,
        output=body,
    )
    value = record.model_dump(mode="json")
    _bounded_raw(value, label="inbound output record")
    if len(_canonical(value).encode()) > MAX_MODEL_OUTPUT_BYTES:
        raise ValueError("inbound_output_record.byte_limit")
    return value


def output_record_identity(record: dict[str, object]) -> tuple[str, str]:
    output_hash = "sha256:" + _hash(record)
    return "inbound-turn-output:sha256:" + _hash(
        {
            "inner_turn_id": record["inner_turn_id"],
            "output_hash": output_hash,
            "proposal_hash": record["proposal_hash"],
        }
    ), output_hash


def _validate_record(*, decision: InnerDecision, model_input: ModelInput) -> _OutputRecord:
    payload = decision.decision
    if not isinstance(payload, dict) or payload.get("contract") != DECISION_CONTRACT:
        raise ValueError("inbound_output_record.contract_unavailable")
    raw = payload.get("output_record")
    _bounded_raw(raw, label="inbound output record")
    if len(_canonical(raw).encode()) > MAX_MODEL_OUTPUT_BYTES:
        raise ValueError("inbound_output_record.byte_limit")
    try:
        record = _OutputRecord.model_validate_json(_canonical(raw))
    except ValueError as exc:
        raise ValueError("inbound_output_record.invalid_body") from exc
    canonical_record = record.model_dump(mode="json")
    if raw != canonical_record or output_record_identity(canonical_record) != (
        payload.get("output_ref"),
        payload.get("output_hash"),
    ):
        raise ValueError("inbound_output_record.identity_mismatch")
    cursor = ProjectionCursor(
        world_revision=model_input.evaluated_world_revision,
        deliberation_revision=model_input.evaluated_deliberation_revision,
        ledger_sequence=model_input.evaluated_ledger_sequence,
    )
    proposal = validate_proposal_envelope(record.output.raw_proposal)
    author = record.author_lineage
    if (
        decision.status != "decided"
        or record.actor_ref != decision.actor_ref
        or record.cursor != cursor
        or record.cursor != decision.cursor
        or record.inner_turn_id != decision.inner_turn_id
        or (record.snapshot_id, record.snapshot_hash)
        != (decision.snapshot_id, decision.snapshot_hash)
        or record.capability_ref != payload.get("capability_ref")
        or record.capability_payload_hash != payload.get("capability_payload_hash")
        or list(record.source_refs) != payload.get("source_refs")
        or model_input.trigger_ref not in record.source_refs
        or record.model_input_hash != "sha256:" + _hash(model_input.model_dump(mode="json"))
        or record.proposal_hash != payload.get("proposal_hash")
        or record.proposal_hash != proposal.proposal_hash
        or proposal.trigger_ref != model_input.trigger_ref
        or proposal.evaluated_world_revision != cursor.world_revision
        or decision.author_lineage != author
        or (author.model_id, author.model_version)
        != (record.output.model_id, record.output.model_version)
        or author.response_hash != "sha256:" + _hash(record.output.raw_proposal)
        or (
            record.output.winning_model_call_id is not None
            and (author.model_call_id, author.request_hash)
            != (record.output.winning_model_call_id, "sha256:" + record.output.winning_request_hash)
        )
    ):
        raise ValueError("inbound_output_record.binding_mismatch")
    return record


def validate_cached_output(
    output: ModelOutput, *, decision: InnerDecision, model_input: ModelInput
) -> ModelOutput:
    record = _validate_record(decision=decision, model_input=model_input)
    if _recorded_output(output) != record.output:
        raise ValueError("inbound_output_record.cached_body_mismatch")
    return output


_VERIFIED_TERMINAL = object()


class _VerifiedTerminalTraces:
    __slots__ = ("__audits",)

    def __init__(self, audits: tuple[RecallAuditTrace, ...], *, authority: object):
        if authority is not _VERIFIED_TERMINAL:
            raise TypeError("completed inbound traces require terminal verification")
        self.__audits = audits

    def __reduce__(self):
        raise TypeError("completed inbound trace authority cannot be serialized")


def _verified_terminal_trace_audits(proof: _VerifiedTerminalTraces) -> tuple[RecallAuditTrace, ...]:
    if type(proof) is not _VerifiedTerminalTraces:
        raise TypeError("completed inbound trace authority is unavailable")
    return proof._VerifiedTerminalTraces__audits


def _restore_completed_inbound_output(
    *,
    store: _CharacterInteriorTurnStore,
    expected_request: _TurnCoordinationRequest,
    decision: InnerDecision,
    model_input: ModelInput,
) -> ModelOutput:
    """Called only after Core has acquired the exact current coordination request.

    Read the installed store ourselves. Caller-supplied output/audit dictionaries
    cannot authorize a process seal; the original terminal and checkpoint must
    agree with the request Core just validated at its pinned Context.
    """
    rows = store.terminal_records_for_source(
        world_id=expected_request.world_id,
        actor_ref=expected_request.actor_ref,
        purpose="inbound_turn",
        source_ref=model_input.trigger_ref,
    )
    rows = tuple(row for row in rows if row.request.inner_turn_id == decision.inner_turn_id)
    if len(rows) != 1 or rows[0].request != expected_request:
        raise ValueError("inbound_output_record.original_terminal_unavailable")
    row = rows[0]
    for raw, digest in (
        (row.terminal_result_json, row.terminal_result_hash),
        (row.authored_state_json, row.authored_state_hash),
    ):
        if not isinstance(raw, str) or _hash(raw) != digest:
            raise ValueError("inbound_output_record.stored_hash_mismatch")
    try:
        terminal = InnerDecision.model_validate_json(row.terminal_result_json)
        prepared = json.loads(row.authored_state_json)
        role = _InteriorRoleResult.model_validate_json(_canonical(prepared["result"]))
        snapshot = InnerLifeSnapshot.model_validate_json(_canonical(prepared["snapshot"]))
        private = _PrivateSelfLineage.model_validate_json(
            _canonical(prepared["private_self_lineage"])
        )
        presentations = tuple(
            PrefetchPresentationAudit.model_validate_json(_canonical(item))
            for item in prepared["presented_prefetch_traces"]
        )
        initial_snapshot = _validated_initial_snapshot(
            prepared, result=role, snapshot=snapshot, private=private
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("inbound_output_record.invalid_original_checkpoint") from exc
    record = _validate_record(decision=decision, model_input=model_input)
    binding = snapshot.capability_scope.value
    if (
        terminal != decision
        or role.status != "decision"
        or role.decision != decision.decision
        or role.author_lineage != decision.author_lineage
        or private != decision.private_self_lineage
        or role.summary != decision.summary
        or role.attended_source_refs != decision.attended_source_refs
        or (private.final_snapshot_id, private.final_snapshot_hash)
        != (snapshot.snapshot_id, snapshot.snapshot_hash)
        or private.final_author_lineage != record.author_lineage
        or snapshot.world_id != expected_request.world_id
        or snapshot.actor_ref != expected_request.actor_ref
        or record.world_id != expected_request.world_id
        or record.actor_ref != expected_request.actor_ref
        or snapshot.cursor != decision.cursor
        or snapshot.snapshot_id != decision.snapshot_id
        or snapshot.snapshot_hash != decision.snapshot_hash
        or expected_request.trigger_ref != model_input.trigger_ref
        or expected_request.subject_ref != decision.opportunity_ref
        or not isinstance(binding, dict)
        or binding.get("capability_ref") != record.capability_ref
        or binding.get("payload_hash") != record.capability_payload_hash
        or binding.get("source_refs") != list(record.source_refs)
        or presentations != decision.presented_prefetch_traces
    ):
        raise ValueError("inbound_output_record.original_authority_mismatch")
    audits: list[RecallAuditTrace] = []
    original_prefetch: RecallAuditTrace | None = None
    for name in ("recall_trace", "prefetch_trace"):
        trace = getattr(record.output, name)
        raw = getattr(snapshot, name + "_json")
        original = TrustedRecallTrace.model_validate_json(raw).audit if raw is not None else None
        if name == "prefetch_trace":
            original_prefetch = original
            # Core owns automatic prefetch and the Faculty records which
            # invocation saw it. The legacy top-level slot stays None in this
            # producer format; it must not be backfilled during restoration.
            if trace is None and original is not None:
                if not any(
                    item.trace == original
                    and item.model_call_id == record.author_lineage.model_call_id
                    for item in presentations
                ):
                    raise ValueError("inbound_output_record.prefetch_source_mismatch")
            elif trace != original:
                raise ValueError("inbound_output_record.prefetch_source_mismatch")
        elif trace != original:
            raise ValueError("inbound_output_record.recall_source_mismatch")
        if trace is not None:
            audits.append(trace)
    if record.output.presented_prefetch_traces != presentations:
        raise ValueError("inbound_output_record.prefetch_presentation_mismatch")
    call_ids = {
        record.author_lineage.model_call_id,
        *(item.model_call_id for item in record.output.authored_candidate_audits),
    }
    initial_prefetch = original_prefetch
    if initial_snapshot is not None:
        raw = initial_snapshot.prefetch_trace_json
        initial_prefetch = TrustedRecallTrace.model_validate_json(raw).audit if raw else None
        parent = private.initial_author_lineage
        if parent is None or not any(
            item.purpose == "recall_control_transfer"
            and item.outcome == "control_transfer"
            and item.model_call_id == parent.model_call_id
            and item.request_hash == parent.request_hash.removeprefix("sha256:")
            and item.response_hash == parent.response_hash.removeprefix("sha256:")
            and item.model_id == parent.model_id
            and item.model_version == parent.model_version
            for item in record.output.authored_candidate_audits
        ):
            raise ValueError("inbound_output_record.recall_parent_mismatch")
    for item in presentations:
        if item.phase in {"initial", "recovery_initial"}:
            expected_prefetch = initial_prefetch
        elif item.phase == "recall_followup" and private.relation == "selective_recall":
            expected_prefetch = original_prefetch
        else:
            raise ValueError("inbound_output_record.prefetch_phase_mismatch")
        if item.trace != expected_prefetch:
            raise ValueError("inbound_output_record.prefetch_source_mismatch")
        if initial_snapshot is not None and (
            (
                item.model_call_id == private.final_parent_model_call_id
                and item.phase not in {"initial", "recovery_initial"}
            )
            or (
                item.model_call_id == record.author_lineage.model_call_id
                and item.phase != "recall_followup"
            )
        ):
            raise ValueError("inbound_output_record.prefetch_call_mismatch")
        if item.model_call_id not in call_ids:
            raise ValueError("inbound_output_record.prefetch_call_mismatch")
        audits.append(item.trace)
    for trace in audits:
        at = trace.evaluated_cursor or trace.index_cursor
        if (
            trace.trigger_ref != model_input.trigger_ref
            or at.model_dump() != decision.cursor.model_dump()
        ):
            raise ValueError("inbound_output_record.recall_pin_mismatch")
    # This narrowly typed proof can only expose the exact verified audit tuple.
    # The Recall module does not accept an arbitrary audit argument to re-seal.
    from ..recall_runtime import _restore_completed_inbound_traces

    restored = iter(
        _restore_completed_inbound_traces(
            _VerifiedTerminalTraces(tuple(audits), authority=_VERIFIED_TERMINAL)
        )
    )
    material = record.output.model_dump(mode="python")
    for name in ("recall_trace", "prefetch_trace"):
        if getattr(record.output, name) is not None:
            material[name] = next(restored)
    if presentations:
        material["presented_prefetch_traces"] = tuple(
            PresentedPrefetchTrace(
                phase=item.phase, model_call_id=item.model_call_id, trace=next(restored)
            )
            for item in presentations
        )
    return _checked_output(ModelOutput.model_validate(material))
