"""The one same-role correction reports the existing appraisal wire bound."""

from __future__ import annotations

import json

import pytest

from test_rejected_life_role_result import _ResponseHTTP, _build, _model, _settled
from test_world_stimulus_life_intent import _http_result
from companion_daemon.world_v2.character_interior.structured_role import _WireRoleResult


class _AttentionResponseHTTP(_ResponseHTTP):
    def __init__(self, *, correction_valid: bool) -> None:
        super().__init__(text=None)
        self.correction_valid = correction_valid

    async def __call__(self, request):
        body = json.loads(request.content)
        material = json.loads(body["messages"][-1]["content"])
        if material.get("inner_turn", {}).get("purpose") != "world_stimulus_appraisal":
            return await super().__call__(request)

        response = await super().__call__(request)
        call_number = len(self.stimulus_requests)
        offered = tuple(dict.fromkeys(material["capability_manifest"]["source_refs"]))
        assert offered, "fixture must offer at least one pinned ref"

        message = response.json()["choices"][0]["message"]
        tool_call = message["tool_calls"][0]["function"]
        authored = json.loads(tool_call["arguments"])["result"]
        if call_number == 1:
            authored["attended_source_refs"] = [f"source:attention:{i}" for i in range(9)]
        elif self.correction_valid:
            # The character chooses an offered source; this is not a host
            # truncation to an arbitrary prefix.
            authored["attended_source_refs"] = [offered[-1]]
        else:
            authored["attended_source_refs"] = [f"source:attention:{i}" for i in range(9)]
        return _http_result(body, authored)


def _model_validation_payload(evidence):
    payloads = []
    for row in evidence.events:
        event = row.event
        if event.event_type != "ModelResultRecorded":
            continue
        payload = event.payload()
        audit = json.loads(payload.get("audit_json", "{}"))
        payloads.append(audit)
    return payloads


@pytest.mark.asyncio
@pytest.mark.parametrize("correction_valid", [True, False])
async def test_world_stimulus_attention_overflow_gets_one_bounded_same_role_correction(
    tmp_path, monkeypatch, correction_valid
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = _AttentionResponseHTTP(correction_valid=correction_valid)
    model = _model(provider)
    app = _build(tmp_path / f"attention-{correction_valid}.sqlite", model)
    try:
        await _settled(app)
        await app.drain_background_once()

        assert len(provider.stimulus_requests) == 2
        first, second = [
            json.loads(request["messages"][-1]["content"])
            for request in provider.stimulus_requests
        ]
        # The active reference_wire=false role carrier keeps its existing
        # eight-ref maximum; this repair does not replace or widen it.
        assert (
            _WireRoleResult.model_json_schema()["properties"]["attended_source_refs"][
                "maxItems"
            ]
            == 8
        )

        correction = second["correction"]
        assert correction["failure_code"] == "role_result_schema_invalid"
        assert "上限是 8 条" in correction["failure_detail"]
        assert "本次确实注意过" in correction["failure_detail"]
        assert "不要让宿主按顺序截断" in correction["failure_detail"]
        assert "source:attention:" not in correction["failure_detail"]
        assert correction["rejected_role_result"]["authority"] == (
            "rejected_candidate_not_world_evidence"
        )

        evidence = app.export_replay_evidence()
        audits = _model_validation_payload(evidence)
        failures = [item for item in audits if item.get("status") == "main_exception"]
        if correction_valid:
            assert not failures
            assert any(item.get("status") == "proposal_validated" for item in audits)
        else:
            assert len(failures) == 1
            assert failures[0]["failure_code"] == "invalid_role_result_after_correction"

        # This appraisal path cannot authorize or deliver a user-facing Action.
        event_types = {row.event.event_type for row in evidence.events}
        assert not any(
            event_type.startswith("Action")
            or "Dispatch" in event_type
            or "Delivery" in event_type
            for event_type in event_types
        )
    finally:
        await app.aclose()
        await model.aclose()
