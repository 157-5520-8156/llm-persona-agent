"""Production failures: fixed source transport and expired emotional authority."""
import hashlib
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from companion_daemon.world_v2.character_interior.contracts import _InteriorCapabilityManifest
from companion_daemon.world_v2.character_interior.structured_role import StructuredCharacterRoleFaculty, StructuredRoleResultError
from companion_daemon.world_v2.memory_consolidation_contract import HOST_SOURCE_BINDING, tool_contract
from test_character_interior_structured_role import _RequiredToolQueueModel, _request, _result


def manifest(host=True):
    payload = {"offered_tokens": ["m0", "m1"]}
    if host:
        payload["source_binding"] = HOST_SOURCE_BINDING
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return _InteriorCapabilityManifest(capability_ref="capability:memory-review:test",
        capability_kind="memory_consolidation", payload_json=raw,
        payload_hash="sha256:" + hashlib.sha256(raw.encode()).hexdigest(),
        source_refs=tuple(f"source:memory:{i}" for i in range(9)))


def choices():
    return {"choices": [{"candidate_token": "m0", "disposition": "retain", "next_review_hours": 48},
                        {"candidate_token": "m1", "disposition": "forget", "next_review_hours": 24}]}


@pytest.mark.asyncio
async def test_memory_v2_binds_full_sources_without_model_echo_or_choice_changes():
    offered = manifest()
    raw = _result(status="decision", decision={"payload": choices()})
    model = _RequiredToolQueueModel(raw)
    role = StructuredCharacterRoleFaculty(model=model, model_id="deepseek-flash")
    result = await role.consider(await _request(purpose="memory_consolidation", capability_manifest=offered))
    assert tuple(result["decision"]["source_refs"]) == offered.source_refs
    assert result["decision"]["payload"]["choices"] == choices()["choices"]
    tool = model.tool_calls[0][0][0]["function"]
    assert tool["name"] == "character_memory_consolidation_v2"
    Draft202012Validator(tool["parameters"]).validate(json.loads(raw))
    assert result["attended_source_refs"] == ("source:private_self",)


@pytest.mark.asyncio
async def test_memory_v2_attention_can_cover_nine_valid_sources_without_truncation():
    request = await _request(purpose="memory_consolidation", capability_manifest=manifest())
    refs = list(request.snapshot.source_refs[:9])
    assert len(refs) == 9
    raw = json.loads(_result(status="decision", decision={"payload": choices()}))
    raw["attended_source_refs"] = refs
    model = _RequiredToolQueueModel(json.dumps(raw))
    role = StructuredCharacterRoleFaculty(model=model, model_id="deepseek-flash")
    result = await role.consider(request)
    assert result["attended_source_refs"] == tuple(refs)
    schema = model.tool_calls[0][0][0]["function"]["parameters"]
    Draft202012Validator(schema).validate(raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["sources", "token", "missing_choice", "legacy"])
async def test_memory_host_binding_never_repairs_semantic_choices_or_explicit_bad_sources(invalid):
    offered = manifest(host=invalid != "legacy")
    payload = choices()
    decision = {"payload": payload}
    if invalid == "sources":
        decision["source_refs"] = ["source:unknown"]
    elif invalid == "token":
        payload["choices"][0]["candidate_token"] = "m99"
    elif invalid == "missing_choice":
        payload["choices"].pop()
    role = StructuredCharacterRoleFaculty(model=_RequiredToolQueueModel(_result(status="decision", decision=decision)), model_id="deepseek-flash")
    with pytest.raises(StructuredRoleResultError):
        await role.consider(await _request(purpose="memory_consolidation", capability_manifest=offered))


def test_memory_v1_retains_its_explicit_reference_schema_and_v2_pins_sources_in_identity():
    old = manifest(host=False)
    legacy = tool_contract(capability_payload=old.payload, source_refs=old.source_refs, recall_allowed=False)
    assert legacy.provider_tools[0]["function"]["name"] == "character_memory_consolidation_v1"
    schema = legacy.provider_tools[0]["function"]["parameters"]
    assert not Draft202012Validator(schema).is_valid(json.loads(_result(status="decision", decision={"payload": choices()})))
    new = manifest()
    a = tool_contract(capability_payload=new.payload, source_refs=new.source_refs, recall_allowed=False)
    b = tool_contract(capability_payload=new.payload, source_refs=tuple(reversed(new.source_refs)), recall_allowed=False)
    assert a.identity != b.identity


@pytest.mark.asyncio
async def test_expired_appraisal_does_not_loop_or_create_late_affect():
    from companion_daemon.world_v2.accepted_ledger_batch import AcceptedLedgerBatchIssuer
    from companion_daemon.world_v2.ledger import WorldLedger
    from companion_daemon.world_v2.runtime import WorldRuntime
    from companion_daemon.world_v2.schemas import ClockObservation
    from companion_daemon.world_v2.character_interior.world_stimulus import CharacterInteriorWorldStimulusRuntime
    from companion_daemon.world_v2.affect_source_lifecycle import source_appraisal_closed
    from test_immediate_emotion_proposal_worker import _record_combined_emotion_proposal, _worker, WORLD_ID
    issuer = AcceptedLedgerBatchIssuer()
    ledger = WorldLedger.in_memory(world_id=WORLD_ID, accepted_batch_issuer=issuer)
    proposal, cursor = _record_combined_emotion_proposal(ledger)
    worker = _worker(ledger=ledger, issuer=issuer)
    worker._appraisal.process(world_id=WORLD_ID, cursor=cursor, proposal_id=proposal.proposal_id)
    before = ledger.project()
    assert not source_appraisal_closed(proposal=proposal, projection=before)
    at = max(a.expires_at for a in before.appraisals) + timedelta(seconds=1)
    await WorldRuntime(world_id=WORLD_ID, ledger=ledger).advance(ClockObservation(
        schema_version="world-v2.1", tick_id="tick:expired-affect", world_id=WORLD_ID,
        logical_time_from=before.logical_time, logical_time_to=at, created_at=at,
        trace_id="trace:expired-affect", causation_id="cause:expired-affect",
        correlation_id="correlation:expired-affect", logical_time=before.logical_time, reason="scheduled_tick",
    ))
    expired = ledger.project()
    assert source_appraisal_closed(proposal=proposal, projection=expired)
    # An absent appraisal remains an authority failure, not a permission to skip.
    assert not source_appraisal_closed(proposal=proposal, projection=expired.model_copy(update={"appraisals": ()}))
    for _ in range(2):
        result = worker.process(world_id=WORLD_ID, audit_cursor=cursor, proposal_id=proposal.proposal_id)
        assert result.status == "appraisal_only"
        assert result.affect_skip_reason == "affect_proposal_compiler.source_appraisal_closed"
        assert ledger.project() == expired
    runtime = object.__new__(CharacterInteriorWorldStimulusRuntime)
    runtime._ledger = ledger
    audit = next(a for a in expired.proposal_audits if a.proposal_id == proposal.proposal_id)
    assert not runtime._affect_is_pending(expired, audit=audit)


@pytest.mark.asyncio
async def test_terminal_recovery_scan_never_replays_old_prefixes(monkeypatch):
    from test_character_interior_world_stimulus import (
        _RoleModel, _runtime_for_ledger, _seed_relationship_state,
        seed_through_proposal, commit, settlement_batch, SOURCE_REF, WORLD_ID,
    )
    from companion_daemon.world_v2.accepted_ledger_batch import AcceptedLedgerBatchIssuer
    from companion_daemon.world_v2.ledger import WorldLedger
    issuer = AcceptedLedgerBatchIssuer()
    ledger = WorldLedger.in_memory(world_id=WORLD_ID, accepted_batch_issuer=issuer)
    seed_through_proposal(ledger)
    commit(ledger, settlement_batch())
    await _seed_relationship_state(ledger=ledger, issuer=issuer, source_ref=SOURCE_REF,
                                   subject_ref="user:geoff")
    runtime, _, _ = _runtime_for_ledger(ledger=ledger, issuer=issuer,
        model=_RoleModel(decision="activate", relationship_subject_ref="user:geoff"),
        source_ref=SOURCE_REF, companion_actor_ref="actor:companion", settle_relationship=True)
    assert (await runtime.drain_one()).work_status == "accepted"
    before = ledger.project()

    def forbidden(*args, **kwargs):
        raise AssertionError("recovery selection replayed a historical prefix")

    monkeypatch.setattr(ledger, "project_at", forbidden)
    runtime._settlement_pending_memo.clear()
    assert runtime._next_process(before) is None
    assert ledger.project() == before
