"""The generated schema must describe existing cross-choice consistency rules."""

from copy import deepcopy
import json

from jsonschema import Draft202012Validator
import pytest

from companion_daemon.world_v2.memory_consolidation_contract import (
    HOST_SOURCE_BINDING, UNDERSTANDING_CONTRACT, tool_contract, validate_payload,
)
from test_character_interior_structured_role import _result


@pytest.mark.parametrize("disposition,has_understanding,valid", [
    ("retain", True, True), ("forget", False, True), ("forget", True, False),
])
def test_advertised_memory_schema_preserves_both_legal_choices_and_rejects_contradiction(disposition, has_understanding, valid):
    cap = {"source_binding": HOST_SOURCE_BINDING, "understanding_contract": UNDERSTANDING_CONTRACT,
           "offered_tokens": ["m0", "m1"], "understanding_source_tokens": ["m0", "m1"], "understanding_tokens": []}
    payload = {"choices": [{"candidate_token": "m0", "disposition": disposition, "next_review_hours": 24},
                           {"candidate_token": "m1", "disposition": "retain", "next_review_hours": 24}],
               "understandings": [{"operation": "retain", "predecessor_tokens": [], "facet": "偏好",
                                   "summary": "她觉得这个时刻值得留住。", "confidence_bp": 6000,
                                   "evidence_quotes": [{"source_token": "m0", "quote": "旧书店"}]}] if has_understanding else []}
    contract = tool_contract(capability_payload=cap, source_refs=("event:source",), recall_allowed=False)
    raw = json.loads(_result(status="decision", decision={"payload": deepcopy(payload)}))
    validator = Draft202012Validator(contract.provider_tools[0]["function"]["parameters"])
    assert validator.is_valid(raw) is valid
    if valid:
        validate_payload(payload, {"m0", "m1"}, source_tokens={"m0", "m1"}, predecessor_tokens=set())
    else:
        with pytest.raises(ValueError, match="forgotten in this batch"):
            validate_payload(payload, {"m0", "m1"}, source_tokens={"m0", "m1"}, predecessor_tokens=set())
