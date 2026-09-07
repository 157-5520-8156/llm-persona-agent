"""Compact transport preserves the frozen pre-change provider language.

The baseline fixture was compiled from 29a2cf7c and checked against both
world-stimulus requests captured in real trial-01 (2026-09-07). It contains
only public schema, never character context, requests, or credentials.

Replacing only those captured parameters reduces their compact JSON from
25,103 to 12,817 UTF-8 bytes. The two full captured request bodies shrink from
62,670/72,669 to 50,384/60,383 bytes, with all other request values unchanged.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

from companion_daemon.world_v2.character_interior.inbound_tool_contract import (
    deepseek_strict_tool_schema,
)
from companion_daemon.world_v2.character_interior.structured_role_tool_contract import (
    StructuredRoleToolContracts,
)


def _schema(*, recall_allowed):
    contract = StructuredRoleToolContracts().world_stimulus_appraisal(
        capability_payload={},
        recall_allowed=recall_allowed,
    )
    return contract.provider_tools[0]["function"]["parameters"]


def _baseline(*, recall_allowed):
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/world_stimulus_schema_precompact.json").read_text()
    )
    schema = fixture["parameters"]
    assert hashlib.sha256(_wire(schema)).hexdigest() == fixture["schema_sha256"]
    if not recall_allowed:
        schema["anyOf"] = schema["anyOf"][:2]
    return schema


def _wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _result():
    return {
        "status": "transition",
        "summary": "I noticed this change.",
        "attended_source_refs": ["source:fixture"],
        "decision": None,
        "recall_query": None,
        "proposals": [
            {
                "proposal_type": "world_stimulus_appraisal_result",
                "decision": "activate",
                "brief_rationale": "It matters to me.",
                "behavior_tendency": "Thinking about it.",
                "stance": "Interested.",
                "display_strategy": "Keep it private.",
                "confidence": 7000,
                "meaning_candidates": [{"meaning": "A possibility", "confidence": 7000}],
                "attribution": "situation",
                "severity": 3000,
                "expiry": None,
                "affect_transition": None,
                "relationship_signal": None,
                "aspiration_transition": None,
                "experience_transition": None,
            }
        ],
    }


def _witnesses():
    # Authored examples exercise every optional capability; they are not
    # generated from the implementation's merged branch or its field order.
    additions = [
        {},
        {
            "affect_transition": {
                "operation": "open",
                "component_targets": [{"dimension": "joy", "target_intensity_bp": 6000}],
            }
        },
        {
            "relationship_signal": {
                "subject_ref": "user:fixture",
                "signal_code": "felt_understood",
                "confidence_bp": 6500,
                "persistence": "session",
                "rationale_code": "shared_reading",
                "suggested_deltas": {
                    name: 100
                    for name in (
                        "trust_bp",
                        "closeness_bp",
                        "respect_bp",
                        "reliability_bp",
                        "mutuality_bp",
                        "repair_confidence_bp",
                    )
                },
            }
        },
        {
            "aspiration_transition": {
                "operation": "plant",
                "aspiration_id": None,
                "text": "Learn pottery.",
                "privacy_class": "private",
                "tension_summary": None,
                "tension_source_refs": [],
                "source_refs": ["source:fixture"],
                "reason_summary": "I want to try it.",
            }
        },
        {
            "experience_transition": {
                "domain": "goal",
                "operation": "pause",
                "target_id": "goal:fixture",
                "expected_entity_revision": 1,
                "reason_kind": "priority_shift",
                "source_refs": ["source:fixture"],
                "reason_summary": "Another matter comes first.",
            }
        },
    ]
    for status in ("no_change", "transition"):
        for addition in additions:
            value = _result()
            value["status"] = status
            value["proposals"][0].update(addition)
            yield value, True
        for field, malformed in (
            ("recall_query", "null"),
            ("decision", {}),
            ("proposals", []),
            ("proposals", [_result()["proposals"][0]] * 2),
            ("summary", ""),
            ("attended_source_refs", ["source:fixture"] * 9),
            ("unexpected", "not allowed"),
        ):
            value = _result()
            value.update(status=status)
            value[field] = malformed
            yield value, False
        for field, malformed in (("confidence", 10001), ("attribution", "invented")):
            value = _result()
            value["status"] = status
            value["proposals"][0][field] = malformed
            yield value, False
    value = _result()
    value["status"] = "unsupported_status"
    yield value, False


@pytest.mark.parametrize("recall_allowed", [False, True])
def test_complete_proposal_contract_is_transmitted_once(recall_allowed):
    assert len(_wire(_schema(recall_allowed=recall_allowed))) <= 13_000


@pytest.mark.parametrize("recall_allowed", [False, True])
@pytest.mark.parametrize("dialect", ["standard", "deepseek-strict"])
def test_valid_and_invalid_witnesses_match_the_original_schema(recall_allowed, dialect):
    old = _baseline(recall_allowed=recall_allowed)
    new = _schema(recall_allowed=recall_allowed)
    if dialect == "deepseek-strict":
        old = deepseek_strict_tool_schema(old)
        new = deepseek_strict_tool_schema(new)
    Draft202012Validator.check_schema(old)
    Draft202012Validator.check_schema(new)
    before, after = Draft202012Validator(old), Draft202012Validator(new)
    for witness, expected in _witnesses():
        accepted_before = before.is_valid(witness)
        assert after.is_valid(witness) == accepted_before, witness
        if dialect == "standard":
            assert accepted_before == expected, witness
    recall = {
        "status": "recall_request",
        "summary": "I want to remember.",
        "attended_source_refs": [],
        "decision": None,
        "recall_query": "What happened last time?",
        "proposals": [],
    }
    assert before.is_valid(recall) == after.is_valid(recall) == recall_allowed
    for query in (None, ""):
        recall["recall_query"] = query
        assert before.is_valid(recall) == after.is_valid(recall)
        if dialect == "standard":
            assert not after.is_valid(recall)


def test_every_non_status_constraint_matches_the_frozen_original():
    schema = deepcopy(_schema(recall_allowed=True))
    expanded = []
    for branch in schema["anyOf"]:
        for status in branch["properties"]["status"]["enum"]:
            single = deepcopy(branch)
            single["properties"]["status"]["enum"] = [status]
            expanded.append(single)
    schema["anyOf"] = expanded
    assert schema == _baseline(recall_allowed=True)
