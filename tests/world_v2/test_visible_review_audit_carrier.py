"""Audit carrier tests only; the fixture JSON does not qualify a source review."""

from datetime import UTC, datetime
import json

import pytest

from companion_daemon.world_v2.deliberation import DeliberationResult, ModelResultAudit
from companion_daemon.world_v2.proposal_audit import ProposalAuditContext, ProposalAuditRecorder
from companion_daemon.world_v2.proposal_audit_schemas import (
    ModelResultRecordedPayload,
    RecordedModelResultAudit,
    canonical_json,
    model_audit_json,
    sha256,
)
from companion_daemon.world_v2.proposal_envelope import DecisionProposal
from companion_daemon.world_v2.recall_audit import CharacterRecallRequest, RecallAuditTrace
from companion_daemon.world_v2.recall_index import (
    RecallCursor,
    RecallQuery,
    recall_query_hash,
    recall_result_hash,
)
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger


NOW = datetime(2026, 9, 8, tzinfo=UTC)
CARRIER = '{ "fixture_only": "原 review 材料", "required": true }'
# Captured from the unchanged audit.1-.8 implementation at 2a1a6f05.
LEGACY_HASHES = (
    "9426ce74934f0daf08b26a4b0412fb8ae0da52602d9ebc936cf462c38de65c5b",
    "c34f0410a5127aada4351723dd7415f54851f2966c03bc9c43ebcb96b219a914",
    "78f574d19237f099a7bd1472ed45f3864de9616a46c6b5e8a2dde4fd363d3aed",
    "624c4130bba17885e14ccc9ca2cb3ae3b4985c9cf4e4bc9c491ce3793fbdf769",
    "2ec6379ff6bdff4ee2eccc14e5c542234adff222c5bd84fbe1d72fbadf8e9fc8",
    "7a9b71d21aad888ac4ca8f4b528b3c42c04c5c3ad89331b1b906474eac31884b",
    "b7fad7d11c380e0822efb29dd4b559ec2254ddba569a62c2b3f917252bc02796",
    "ac552cf90b47212e5a3f1394c7268ed5b714030208345602e5bea2bab6b59e16",
)


def _trace(*, prefetch: bool = False) -> RecallAuditTrace:
    cursor = RecallCursor(world_revision=0, deliberation_revision=0, ledger_sequence=0)
    request = CharacterRecallRequest(query_text="a remembered exchange")
    query = RecallQuery(
        query_text=request.query_text,
        cursor=cursor,
        actor_ref="actor:companion",
        subject_refs=("actor:companion",),
        viewer_privacy_ceiling="withhold",
        at=NOW,
        limit=request.limit,
        accessibility_seed="draw:carrier",
    )
    query_hash = recall_query_hash(index_version="recall-index:test", query=query)
    return RecallAuditTrace(
        mode="prefetch" if prefetch else "character_pull",
        trigger_ref="trigger:carrier",
        request=request,
        query=query,
        query_hash=query_hash,
        result_hash=recall_result_hash(query_hash=query_hash, cursor=cursor, hit_values=[]),
        index_version="recall-index:test",
        embedding_version="embedding:test",
        index_cursor=cursor,
        hits=(),
    )


def _audit(version: int = 1) -> RecordedModelResultAudit:
    call_id = "model-call:carrier"
    response_hash = "b" * 64 if version != 8 else None
    material = {
        "model_call_id": call_id,
        "model_result_ref": "model-result:"
        + sha256(canonical_json({"model_call_id": call_id, "response_hash": response_hash})),
        "attempt_id": "attempt:carrier",
        "route": {"tier": "flash", "reason_code": "ordinary", "router_version": "router.1"},
        "model_id": "model:test" if version != 8 else None,
        "model_version": "1" if version != 8 else None,
        "request_hash": "a" * 64,
        "response_hash": response_hash,
        "status": "proposal_validated",
    }
    if version == 2:
        usage = {
            "usage_contract": "model-usage.1",
            "route_class": "chat",
            "input_tokens": 11,
            "output_tokens": 17,
            "thinking_tokens": 0,
            "token_provenance": "offline_estimated",
            "transport": "offline_fixture",
            "provider": "fixture",
            "provider_usage_ref": "usage:carrier",
        }
        material.update(
            input_tokens=11,
            output_tokens=17,
            usage={**usage, "provider_usage_hash": sha256(canonical_json(usage))},
        )
    if version in {3, 6, 8}:
        material.update(slot="primary", outcome="winner")
    if version == 4:
        material["recall_trace"] = _trace()
    if version == 5:
        material["presented_prefetch_traces"] = (
            {"phase": "initial", "model_call_id": call_id, "trace": _trace(prefetch=True)},
        )
    if version == 6:
        material.update(parent_model_call_id="model-call:physical", semantic_stream_part="head")
    if version == 7:
        material["character_interior_lineage"] = {
            "inner_turn_id": "character-inner-turn:sha256:" + "c" * 64,
            "purpose": "inbound_turn",
            "opportunity_ref": "inbound-opportunity:sha256:" + "d" * 64,
            "snapshot_id": "inner-life-snapshot:sha256:" + "e" * 64,
            "snapshot_hash": "e" * 64,
            "capability_ref": "inbound-turn-capability:sha256:" + "f" * 64,
            "author_model_id": "model:test",
            "author_model_version": "1",
            "author_model_call_id": call_id,
            "author_request_hash": "sha256:" + "a" * 64,
            "author_response_hash": "sha256:" + "b" * 64,
            "author_attempt_ordinal": 0,
            "private_self_lineage_hash": "sha256:" + "1" * 64,
            "decision_hash": "sha256:" + "2" * 64,
        }
    if version == 8:
        material.update(
            status="main_exception",
            failure_code="primary_exception",
            outcome="exception",
            attempted_model_id="model:test",
            attempted_model_version="1",
            physical_provider_audits=(
                {
                    "model_call_id": "model-call:physical",
                    "request_hash": "c" * 64,
                    "model_id": "model:test",
                    "model_version": "1",
                    "outcome": "unresolved",
                    "failure_code": "stream_provider_unresolved",
                    "usage_status": "unresolved",
                    "semantic_model_call_ids": ("model-call:head",),
                },
            ),
        )
    return RecordedModelResultAudit.model_validate(material)


def _payload(audit_json: str, version: int) -> ModelResultRecordedPayload:
    material = json.loads(audit_json)
    return ModelResultRecordedPayload(
        audit_contract=f"model-result-audit.{version}",
        model_result_ref=material["model_result_ref"],
        deliberation_result_id="deliberation:carrier",
        model_call_id=material["model_call_id"],
        parent_model_call_id=material.get("parent_model_call_id"),
        attempt_id=material["attempt_id"],
        capsule_id="d" * 64,
        trigger_ref="trigger:carrier",
        evaluated_world_revision=0,
        attempt_index=0,
        attempt_count=1,
        audit_json=audit_json,
        audit_hash=sha256(audit_json),
    )


def _with_carrier(audit: RecordedModelResultAudit, carrier: str = CARRIER) -> str:
    return canonical_json({**audit.model_dump(mode="json"), "visible_source_review_json": carrier})


def test_recorder_retains_exact_carrier_through_sqlite_cold_replay(tmp_path) -> None:
    path = tmp_path / "review-audit.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id="world:carrier")
    audit = ModelResultAudit.model_validate(
        {**_audit().model_dump(mode="python"), "visible_source_review_json": CARRIER}
    )
    proposal = DecisionProposal(
        proposal_id="proposal:carrier",
        trigger_ref="trigger:carrier",
        evaluated_world_revision=0,
        evidence_refs=(),
        proposed_changes=(),
        action_intents=(),
        confidence=8000,
        brief_rationale="Offline audit carrier fixture.",
        behavior_tendency="observe",
        stance="quiet",
        display_strategy="none",
    )
    result = DeliberationResult(
        result_id="deliberation:"
        + sha256(
            canonical_json(
                {
                    "capsule_id": "d" * 64,
                    "proposal_hash": proposal.proposal_hash,
                    "attempt_audits": [audit.model_dump(mode="json")],
                }
            )
        ),
        capsule_id="d" * 64,
        proposal=proposal,
        audit=audit,
        attempt_audits=(audit,),
    )
    context = ProposalAuditContext(
        world_id="world:carrier",
        trigger_ref="trigger:carrier",
        logical_time=NOW,
        created_at=NOW,
        actor="actor:companion",
        source="test",
        trace_id="trace:carrier",
        causation_id="cause:carrier",
        correlation_id="corr:carrier",
        evaluated_world_revision=0,
        expected_commit_world_revision=0,
        expected_deliberation_revision=0,
        expected_ledger_sequence=0,
    )
    ProposalAuditRecorder(ledger=ledger).record(result, context)
    ledger.close()
    reopened = SQLiteWorldLedger(path=path, world_id="world:carrier")
    try:
        projected = reopened.rebuild().model_result_audits[0]
        recorded = RecordedModelResultAudit.model_validate_json(projected.audit_json)
        assert projected.audit_contract == "model-result-audit.9"
        assert projected.deliberation_result_id == result.result_id
        assert projected.audit_hash == sha256(projected.audit_json)
        assert recorded.visible_source_review_json == CARRIER
        assert model_audit_json(recorded) == projected.audit_json
    finally:
        reopened.close()


@pytest.mark.parametrize("version", range(1, 9))
def test_legacy_audit_bytes_and_contracts_remain_unchanged(version: int) -> None:
    original = model_audit_json(_audit(version))
    assert sha256(original) == LEGACY_HASHES[version - 1]
    assert "visible_source_review_json" not in json.loads(original)
    assert _payload(original, version).audit_json == original
    with_none = _audit(version).model_copy(update={"visible_source_review_json": None})
    assert model_audit_json(with_none) == original


@pytest.mark.parametrize("version", range(1, 9))
def test_legacy_contract_cannot_carry_visible_review(version: int) -> None:
    with pytest.raises(ValueError, match="visible source review.*model-result-audit.9"):
        _payload(_with_carrier(_audit(version)), version)


def test_v9_requires_carrier_and_rejects_dropping_it() -> None:
    with pytest.raises(ValueError, match="visible source review.*model-result-audit.9"):
        _payload(model_audit_json(_audit()), 9)


@pytest.mark.parametrize("version", [1, 2, 3, 4, 5, 7])
def test_v9_keeps_existing_atomic_usage_recall_slot_and_character_lineage(version: int) -> None:
    original = _audit(version)
    payload = _payload(_with_carrier(original), 9)
    recorded = RecordedModelResultAudit.model_validate_json(payload.audit_json)
    material = recorded.model_dump(mode="json")
    assert material.pop("visible_source_review_json") == CARRIER
    assert material == original.model_dump(mode="json")


@pytest.mark.parametrize("version", [6, 8])
def test_v9_does_not_expand_stream_or_physical_authority(version: int) -> None:
    with pytest.raises(ValueError, match="stream lineage|independent physical terminal"):
        _payload(_with_carrier(_audit(version)), 9)


def test_v9_checks_entire_utf8_audit_limit_without_truncation() -> None:
    audit = _audit()
    # Multibyte carrier stays below its 1,048,576-character field limit too.
    empty = _with_carrier(audit, canonical_json({"proof": ""}))
    available = 2_000_000 - len(empty.encode("utf-8"))
    text = "证" * (available // 3) + "x" * (available % 3)
    at_limit = _with_carrier(audit, canonical_json({"proof": text}))
    assert len(at_limit.encode("utf-8")) == 2_000_000
    assert _payload(at_limit, 9).audit_json == at_limit
    over_limit = _with_carrier(audit, canonical_json({"proof": text + "x"}))
    with pytest.raises(ValueError, match="byte limit"):
        _payload(over_limit, 9)


@pytest.mark.parametrize("version", range(1, 9))
def test_legacy_audit_byte_limit_is_not_increased(version: int) -> None:
    # Invalid extra content must still be bounded before recursive schema parsing.
    material = _audit(version).model_dump(mode="json")
    material["unexpected_large_value"] = "证" * 90_000
    encoded = canonical_json(material)
    assert len(encoded) < 262_144 < len(encoded.encode("utf-8"))
    with pytest.raises(ValueError, match="byte limit"):
        _payload(encoded, version)


def test_v9_rejects_carrier_character_overflow_and_audit_hash_tampering() -> None:
    with pytest.raises(ValueError, match="1048576"):
        _payload(_with_carrier(_audit(), "x" * 1_048_577), 9)
    payload = _payload(_with_carrier(_audit()), 9)
    with pytest.raises(ValueError, match="bytes/hash"):
        ModelResultRecordedPayload.model_validate(
            {
                **payload.model_dump(mode="python"),
                "audit_hash": "f" * 64,
            }
        )
