"""Each offered memory receives exactly one explicit role-authored choice."""

from copy import deepcopy
import hashlib
import json

from jsonschema import Draft202012Validator
import pytest

from companion_daemon.world_v2.memory_consolidation_contract import (
    HOST_SOURCE_BINDING, UNDERSTANDING_CONTRACT, tool_contract, validate_payload,
)


def test_named_memory_choices_require_every_offered_item_and_no_unoffered_item():
    capability = {
        "source_binding": HOST_SOURCE_BINDING, "understanding_contract": UNDERSTANDING_CONTRACT,
        "offered_tokens": ["m0", "m1"], "understanding_tokens": [],
        "understanding_source_tokens": ["m0", "m1"],
    }
    contract = tool_contract(capability_payload=capability, source_refs=("source:memory",),
                             recall_allowed=False, compact_synthesis_transport=True, named_choices=True)
    validator = Draft202012Validator(contract.provider_tools[0]["function"]["parameters"])
    value = {
        "status": "decision", "summary": "我想记下其中一件，另一件让它淡去。",
        "attended_source_refs": [], "recall_query": None, "proposals": [],
        "decision": {"payload": {"choices": {
            "m0": {"disposition": "retain", "next_review_hours": 72},
            "m1": {"disposition": "forget", "next_review_hours": 240},
        }, "understandings": []}},
    }
    validator.validate(value)
    for token, action in [("m1", "remove"), ("m8", "add")]:
        bad = deepcopy(value)
        choices = bad["decision"]["payload"]["choices"]
        if action == "remove":
            del choices[token]
        else:
            choices[token] = choices["m0"]
        assert list(validator.iter_errors(bad))
    assert contract.identity.tool_name == "character_memory_consolidation_v5"


def test_missing_memory_correction_reports_actual_tokens():
    with pytest.raises(ValueError, match="missing=.*m1"):
        validate_payload({"choices": [{"candidate_token": "m0", "disposition": "retain", "next_review_hours": 72}]}, {"m0", "m1"})


def test_false_quote_correction_identifies_the_exact_source_and_field():
    from test_stable_memory_transport import payload
    value = payload()
    value["understandings"][0]["evidence_quotes"][0]["quote"] = "拼接出来的新句子"
    with pytest.raises(ValueError, match=r"understandings\[0\].evidence_quotes\[0\].*m0"):
        validate_payload(value, {"m0"}, readings={"m0": {"source_excerpts": [{"text": "她曾经犹豫。"}]}})


def test_quote_handles_restore_exact_selected_prose_without_rewriting_names():
    from companion_daemon.world_v2.memory_consolidation_contract import expand_quote_handles, quote_handle_view
    from test_stable_memory_transport import payload
    original = "知栀把转盘来回拧了两次，没听见熟悉的咔咔声。" * 15
    cap = {"memories": [{"token": "m0", "reading": {"source_excerpts": [{"text": original}]}}],
           "related_memories": []}
    view = quote_handle_view(cap)
    refs = view["memories"][0]["reading"]["source_excerpts"][0]["text"]["quote_parts"]
    assert "".join(view["quote_options"][ref]["text"] for ref in refs) == original
    assert cap["memories"][0]["reading"]["source_excerpts"][0]["text"] == original
    value = payload()
    value["understandings"][0]["evidence_quotes"] = [{"quote_ref": refs[-1]}]
    restored = expand_quote_handles(value, capability_payload=cap)
    expected = original[240:]
    assert restored["understandings"][0]["evidence_quotes"] == [{"source_token": "m0", "quote": expected}]
    validate_payload(restored, {"m0"}, readings={"m0": cap["memories"][0]["reading"]})
    value["understandings"][0]["evidence_quotes"][0]["quote_ref"] = "m8.q0"
    with pytest.raises(ValueError, match="unoffered quote"):
        expand_quote_handles(value, capability_payload=cap)


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed_updates", [None, 1, True])
async def test_public_role_decodes_only_the_authored_named_choices(malformed_updates):
    from companion_daemon.world_v2.character_interior.structured_role import StructuredCharacterRoleFaculty, StructuredRoleResultError
    from test_character_interior_structured_role import _StrictToolQueueModel, _request, _manifest

    cap = {
        "source_binding": HOST_SOURCE_BINDING, "understanding_contract": UNDERSTANDING_CONTRACT,
        "offered_tokens": ["m0", "m1"], "understanding_tokens": [],
        "understanding_source_tokens": ["m0", "m1"],
        "memories": [], "related_memories": [],
    }
    encoded = json.dumps(cap, sort_keys=True, separators=(",", ":"))
    manifest = _manifest("m0", "m1", kind="memory_consolidation").model_copy(update={
        "payload_json": encoded, "payload_hash": "sha256:" + hashlib.sha256(encoded.encode()).hexdigest(),
    })
    choices = {"m1": {"disposition": "forget", "next_review_hours": 240},
               "m0": {"disposition": "retain", "next_review_hours": 72}}
    value = {"status": "decision", "summary": "我留下第一件，另一件不再保留。",
             "attended_source_refs": [], "recall_query": None, "proposals": [],
             "decision": {"payload": {"choices": choices, "understandings": []}}}
    if malformed_updates is not None:
        value["decision"]["payload"]["understandings"] = malformed_updates
    model = _StrictToolQueueModel(json.dumps(value))
    role = StructuredCharacterRoleFaculty(model=model, model_id="deepseek-flash", reference_wire=True)
    request = await _request(purpose="memory_consolidation", capability_manifest=manifest)
    if malformed_updates is not None:
        with pytest.raises(StructuredRoleResultError) as raised:
            await role.consider(request)
        assert raised.value.code == "role_result_schema_invalid"
        assert "understandings" in raised.value.detail
        return
    result = await role.consider(request)
    assert result["decision"]["payload"]["choices"] == [
        {"candidate_token": token, **choices[token]} for token in ("m0", "m1")
    ]
    assert result["decision"]["source_refs"] == list(manifest.source_refs)
    assert model.tool_calls[0][0][0]["function"]["name"] == "character_memory_consolidation_v6"
