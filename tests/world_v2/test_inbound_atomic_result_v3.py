"""Branch-local transport preserves full decisions without unused null siblings."""

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

from jsonschema import Draft202012Validator
import pytest

from companion_daemon.world_v2.character_interior.inbound_appraisal_wire import AppraisalDraftWire
from companion_daemon.world_v2.expression_draft import ExpressionDraft
from test_inbound_atomic_result_v2 import _contract, _decision, _recall


def _branch_value(kind):
    value = _decision() if kind == "decision" else _recall()
    inactive = (
        ("private_turn_state", "recall_request")
        if kind == "decision" else ("appraisal_draft", "expression_draft")
    )
    for key in inactive:
        del value[key]
    return value


@pytest.mark.parametrize("phase", ["initial", "after_recall", "final"])
@pytest.mark.parametrize("timing", ["now", "later", "silent"])
def test_v3_whole_decision_preserves_canonical_choice(phase, timing):
    contract = _contract(version="3", phase=phase)
    value = _branch_value("decision")
    expression = value["expression_draft"]
    expression["timing_choice"] = timing
    if timing == "later":
        expression.update(delay_seconds=20, expires_after_seconds=120)
    elif timing == "silent":
        expression["beats"] = []
    schema = contract.provider_tools[0]["function"]["parameters"]
    Draft202012Validator(schema).validate({"result": value})
    result = json.loads(contract.unwrap(json.dumps({"result": value})))
    assert result == {key: item for key, item in value.items() if key != "result_kind"}
    AppraisalDraftWire.model_validate_json(json.dumps(result["appraisal_draft"]), strict=True)
    ExpressionDraft.model_validate_json(json.dumps(result["expression_draft"]), strict=True)
    assert contract.identity.version == "3"
    assert contract.identity.tool_name.endswith("_v3")


def test_v3_changes_only_branch_envelope_not_nested_capability_constraints():
    old, new = _contract(), _contract(version="3")
    old_branches = old.provider_tools[0]["function"]["parameters"]["properties"]["result"]["anyOf"]
    new_branches = new.provider_tools[0]["function"]["parameters"]["properties"]["result"]["anyOf"]
    for before, after in zip(old_branches, new_branches, strict=True):
        stripped = deepcopy(before)
        unused = {key for key, field in stripped["properties"].items() if field == {"type": "null"}}
        stripped["properties"] = {key: value for key, value in stripped["properties"].items() if key not in unused}
        stripped["required"] = [key for key in stripped["required"] if key not in unused]
        # Required list order carries no authority; every nested schema is exact.
        assert set(stripped.pop("required")) == set(after["required"])
        assert stripped == {key: value for key, value in after.items() if key != "required"}
    assert new.identity.capabilities_sha256 == old.identity.capabilities_sha256
    assert new.identity.schema_sha256 != old.identity.schema_sha256
    assert new.identity.contract_sha256 != old.identity.contract_sha256
    assert new.required_null_padding_paths() == {}


@pytest.mark.parametrize("phase,allowed", [("initial", True), ("initial", False), ("after_recall", False), ("final", False)])
def test_v3_recall_available_in_both_schema_and_decoder_only_when_permitted(phase, allowed):
    contract = _contract(version="3", phase=phase, recall_allowed=allowed)
    value = {"result": _branch_value("recall")}
    validator = Draft202012Validator(contract.provider_tools[0]["function"]["parameters"])
    assert validator.is_valid(value) is allowed
    assert ("recall" in contract.result_branch_fields()) is allowed
    if allowed:
        assert set(json.loads(contract.unwrap(json.dumps(value)))) == {"private_turn_state", "recall_request"}
    else:
        with pytest.raises(ValueError, match="unavailable"):
            contract.unwrap(json.dumps(value))


@pytest.mark.parametrize("kind,foreign", [("decision", "recall_request"), ("decision", "private_turn_state"), ("recall", "appraisal_draft"), ("recall", "expression_draft")])
@pytest.mark.parametrize("padding", [None, {}, {"query_text": "unavailable foreign branch"}])
def test_v3_rejects_cross_branch_fields_even_when_empty(kind, foreign, padding):
    contract = _contract(version="3")
    value = _branch_value(kind)
    value[foreign] = padding
    wrapped = {"result": value}
    assert not Draft202012Validator(contract.provider_tools[0]["function"]["parameters"]).is_valid(wrapped)
    with pytest.raises(ValueError, match="shape invalid"):
        contract.unwrap(json.dumps(wrapped))


def test_v3_rejects_missing_fields_duplicates_and_nonobjects():
    contract = _contract(version="3")
    value = _branch_value("decision")
    raw = json.dumps({"result": value})
    duplicate = raw.replace('"confidence": 7000', '"confidence": 1, "confidence": 7000', 1)
    with pytest.raises(ValueError, match="duplicate"):
        contract.unwrap(duplicate)
    for bad in ({"result": None}, {"result": {"result_kind": "decision"}}, value, {"result": {"result_kind": []}}):
        with pytest.raises(ValueError):
            contract.unwrap(json.dumps(bad))


# Pre-change a4ecc9df tools/choice/identity, compiled with PYTHONHASHSEED=0.
# The legacy initial schema has set-derived required-list ordering; this test
# preserves it without claiming legacy bytes are stable across hash seeds.
_LEGACY_SHA = {
    "1:initial": "ffea67f68451b470a6b53a27908fc4140fbc26c001ca195d02373003677ab36b",
    "1:after_recall": "89357ce821e142ab7f0c2063958bae240fc31b1f62bd641b919d9b55c371b863",
    "1:final": "1b196926097abfa4d88a468c6593dc7a5412578af72da0a15352f7fc69d01941",
    "2:initial": "4750746adfb392ae755118cd73185c0739e09f814031d8f74bd6ed86230859cd",
    "2:after_recall": "0b9ebf7d7c5c1982c670555bd0a59d5e8228dfd0eaf59574a02ca0bea32e1ff3",
    "2:final": "09b2a9230709b12cc8b4a0f8b5d9d637ab0fd4464a86ff0b68ad01494d727912",
}


def test_legacy_tools_and_identities_retain_original_bytes():
    root = Path(__file__).resolve().parents[2]
    script = """
import hashlib, json
from test_inbound_atomic_result_v2 import _contract
rows = {}
for version in ('1', '2'):
    for phase in ('initial', 'after_recall', 'final'):
        c = _contract(version=version, phase=phase)
        raw = json.dumps([c.provider_tools, c.provider_tool_choice, c.identity.request_identity_material()],
                         ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        rows[version + ':' + phase] = hashlib.sha256(raw.encode()).hexdigest()
print(json.dumps(rows))
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=root, check=True, capture_output=True, text=True,
        env={**os.environ, "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1",
             "PYTHONPATH": os.pathsep.join((str(root / "src"), str(root / "tests/world_v2")))},
    )
    assert json.loads(result.stdout) == _LEGACY_SHA
