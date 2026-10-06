"""Provider shape and same-role correction agree on explicit response receipts."""

import hashlib
import json

from jsonschema import Draft202012Validator
import pytest

from companion_daemon.world_v2.character_interior.structured_role import (
    StructuredCharacterRoleFaculty, StructuredRoleResultError,
)
from companion_daemon.world_v2.character_interior.structured_role_tool_contract import StructuredRoleToolContracts
from test_character_interior_structured_role import (
    _StrictToolQueueModel, _request, _world_stimulus_manifest, _world_stimulus_no_change_result,
)


def response_manifest():
    original = _world_stimulus_manifest()
    payload = dict(original.payload)
    payload["world_life_response"] = {
        "contract": "world-life-response-capability.1",
        "source_event_refs": ["source:world-occurrence"],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return original.model_copy(update={
        "payload_json": encoded,
        "payload_hash": "sha256:" + hashlib.sha256(encoded.encode()).hexdigest(),
    })


def response_result(status="transition", text=None):
    value = json.loads(_world_stimulus_no_change_result())
    value["status"] = status
    value["proposals"][0].update(
        reflection_depth="brief",
        life_responses=[{"source_event_ref": "source:world-occurrence", "response_text": text}],
    )
    return {"result": value}


@pytest.mark.parametrize("text", [None, "我看过了，暂时没有更多感受。"])
def test_current_tool_excludes_status_that_host_rejects_without_choosing_a_feeling(text):
    compiler = StructuredRoleToolContracts()
    kwargs = dict(capability_payload=response_manifest().payload, recall_allowed=True)
    old = compiler.world_stimulus_appraisal(**kwargs, schema_dialect="deepseek-strict-v6")
    current = compiler.world_stimulus_appraisal(**kwargs, schema_dialect="deepseek-strict-v7")
    old_validator = Draft202012Validator(old.provider_tools[0]["function"]["parameters"])
    validator = Draft202012Validator(current.provider_tools[0]["function"]["parameters"])
    # The old transport remains readable byte-for-byte, including its gap.
    old_validator.validate(response_result("no_change", text))
    assert list(validator.iter_errors(response_result("no_change", text)))
    value = response_result(text=text)
    validator.validate(value)
    assert json.loads(current.unwrap(json.dumps(value))) == value["result"]
    assert value["result"]["proposals"][0]["decision"] == "no_change"
    assert current.identity.version == "7"


@pytest.mark.asyncio
async def test_status_mismatch_correction_names_status_not_unrelated_attention():
    model = _StrictToolQueueModel(json.dumps(response_result("no_change")), json.dumps(response_result()))
    role = StructuredCharacterRoleFaculty(model=model, model_id="deepseek-flash", reference_wire=True)
    initial = await _request(phase="experience", purpose="world_stimulus_appraisal", capability_manifest=response_manifest())
    with pytest.raises(StructuredRoleResultError) as raised:
        await role.experience(initial)
    assert raised.value.code == "world_stimulus_status_mismatch"
    assert "transition" in raised.value.detail and "life_responses" in raised.value.detail
    repaired = await role.experience(await _request(
        phase="experience", purpose="world_stimulus_appraisal", capability_manifest=response_manifest(),
        correction_ordinal=1, correction_failure_code=raised.value.code,
        correction_failure_detail=raised.value.detail,
    ))
    correction = json.loads(model.calls[-1][0][-1]["content"])["correction"]
    assert correction["failure_detail"] == raised.value.detail
    assert "attended_source_refs" not in correction["failure_detail"]
    assert repaired["proposals"][0]["payload"]["decision"] == "no_change"
    assert len(model.calls) == 2
