"""Same-role correction keeps the exact rejected Life candidate and its pin."""
import hashlib
import json

import pytest

from companion_daemon.world_v2.character_interior.rejected_role_result import (
    MAX_REJECTED_RESULT_BYTES,
    RejectedRoleResult,
    role_request_binding,
)
from companion_daemon.world_v2.character_interior.structured_role import StructuredRoleResultError
from companion_daemon.world_v2.character_interior.ports import _InteriorRoleRequest
from test_character_interior_structured_role import (
    StructuredCharacterRoleFaculty,
    _RequiredToolQueueModel,
    _request,
    _world_stimulus_manifest,
    _world_stimulus_no_change_result,
)
from test_world_stimulus_life_response import _ResponseHTTP, _build, _model, _settled


async def _rejected():
    valid = _world_stimulus_no_change_result()
    invalid = json.loads(valid)
    invalid["attended_source_refs"] = ["source:not-offered"]
    invalid["summary"] = "原稿里的全部内心，不是新的世界证据。"
    raw = json.dumps(invalid, ensure_ascii=False, indent=2)
    model = _RequiredToolQueueModel(raw, valid)
    role = StructuredCharacterRoleFaculty(model=model, model_id="deepseek-v4-flash")
    request = await _request(phase="experience", purpose="world_stimulus_appraisal", capability_manifest=_world_stimulus_manifest())
    with pytest.raises(StructuredRoleResultError) as raised:
        await role.experience(request)
    error = raised.value
    assert error.rejected_role_result is not None
    correction = request.model_copy(update={
        "correction_ordinal": 1, "correction_failure_code": error.code,
        "correction_failure_detail": error.detail,
        "correction_rejected_role_result": error.rejected_role_result,
    })
    return role, model, request, correction, error, raw


@pytest.mark.asyncio
async def test_complete_rejected_candidate_only_enters_correction_not_sources():
    role, model, request, correction, error, raw = await _rejected()
    correction = _InteriorRoleRequest.model_validate_json(correction.model_dump_json())
    result = await role.experience(correction)
    initial = json.loads(model.calls[0][0][-1]["content"])
    corrected = json.loads(model.calls[1][0][-1]["content"])
    context = corrected.pop("correction")
    assert initial == corrected
    assert model.tool_calls[0] == model.tool_calls[1]
    assert context["rejected_role_result"]["raw_result"] == raw
    assert context["rejected_role_result"]["response_hash"] == error.response_hash
    assert context["rejected_role_result"]["request_binding_sha256"] == role_request_binding(request)
    assert context["rejected_role_result"]["authority"] == "rejected_candidate_not_world_evidence"
    assert result["author_lineage"]["parent_model_call_id"] == error.model_call_id
    assert result["author_lineage"]["request_hash"] != error.request_hash
    assert len(model.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["model_id", "model_version", "model_call_id", "provider_request_hash", "raw_result"])
async def test_changed_invocation_or_original_bytes_rejected_before_provider(field):
    role, model, _, correction, error, _ = await _rejected()
    value = "sha256:" + "0" * 64 if field == "provider_request_hash" else "tampered"
    forged = error.rejected_role_result.model_copy(update={field: value})
    with pytest.raises(ValueError):
        await role.experience(correction.model_copy(update={"correction_rejected_role_result": forged}))
    assert len(model.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("update", [
    {"subject_ref": "subject:another"}, {"inner_turn_id": "turn:another"},
    {"context_note": "different evidence context"}, {"recall_completed": True},
    {"purpose": "generic"}, {"correction_ordinal": 0},
    {"phase": "consider"},
])
async def test_cross_turn_subject_phase_or_recall_transfer_is_rejected(update):
    role, model, _, correction, _, _ = await _rejected()
    with pytest.raises(ValueError):
        await role.experience(correction.model_copy(update=update))
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_utf8_bound_never_silently_truncates_and_round_trip_is_exact():
    _, _, _, _, error, _ = await _rejected()
    value = error.rejected_role_result.model_dump(mode="json")
    raw = "我" * (MAX_REJECTED_RESULT_BYTES // 3)
    value.update(raw_result=raw, response_hash="sha256:" + hashlib.sha256(raw.encode()).hexdigest())
    packet = RejectedRoleResult.model_validate(value)
    assert RejectedRoleResult.model_validate_json(packet.model_dump_json()) == packet
    assert packet.raw_result == raw
    raw += "我"
    value.update(raw_result=raw, response_hash="sha256:" + hashlib.sha256(raw.encode()).hexdigest())
    with pytest.raises(ValueError, match="byte bound"):
        RejectedRoleResult.model_validate(value)


class _CapturingResponseHTTP(_ResponseHTTP):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.originals = []

    async def __call__(self, request):
        body = json.loads(request.content)
        result = await super().__call__(request)
        if json.loads(body["messages"][-1]["content"]).get("inner_turn", {}).get("purpose") == "world_stimulus_appraisal":
            if body.get("stream"):
                first = json.loads(result.text.splitlines()[0].removeprefix("data: "))
                message = first["choices"][0]["delta"]
            else:
                message = result.json()["choices"][0]["message"]
            self.originals.append(message["tool_calls"][0]["function"]["arguments"])
        return result


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,accepted", [("missing_once", True), ("missing", False)])
async def test_core_carries_original_through_life_correction_without_extra_attempts(
    tmp_path, monkeypatch, fault, accepted
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = _CapturingResponseHTTP(fault=fault, text="我对此有自己的感觉。")
    model = _model(provider)
    app = _build(tmp_path / "life-correction.sqlite", model)
    try:
        await _settled(app)
        await app.drain_background_once()
        assert len(provider.stimulus_requests) == len(provider.originals) == 2
        first, second = [json.loads(r["messages"][-1]["content"]) for r in provider.stimulus_requests]
        correction = second.pop("correction")
        assert second == first
        assert correction["rejected_role_result"]["raw_result"] == provider.originals[0]
        assert "life_responses" in correction["failure_detail"]
        evidence = app.export_replay_evidence()
        responses = [r.event for r in evidence.events if r.event.event_type == "CharacterLifeResponseRecorded"]
        assert len(responses) == len(evidence.projection.experiences) == int(accepted)
    finally:
        await app.aclose()
        await model.aclose()


@pytest.mark.asyncio
async def test_overbound_original_stays_technical_without_truncated_correction(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = _CapturingResponseHTTP(text="我" * (MAX_REJECTED_RESULT_BYTES // 3 + 1))
    model = _model(provider)
    app = _build(tmp_path / "overbound.sqlite", model)
    try:
        await _settled(app)
        await app.drain_background_once()
        assert len(provider.stimulus_requests) == 1
        evidence = app.export_replay_evidence()
        assert not any(r.event.event_type == "CharacterLifeResponseRecorded" for r in evidence.events)
        assert evidence.projection.experiences == ()
        failures = [
            r.event.payload() for r in evidence.events
            if r.event.event_type == "ModelResultRecorded"
        ]
        assert any("role_rejected_candidate_unavailable" in json.dumps(f) for f in failures)
    finally:
        await app.aclose()
        await model.aclose()
