"""Opt-in wire compression: the complete atomic role choice stays authoritative.

The seam is InboundToolContracts.contract_for / InboundToolContract.unwrap.
Provider grammar is checked independently with JSON Schema; semantic bounds
remain the existing canonical model validators. No model or transport runs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from companion_daemon.world_v2.character_interior.inbound_appraisal_wire import (
    AppraisalDraftWire,
)
from companion_daemon.world_v2.character_interior.inbound_tool_contract import (
    InboundToolContracts,
)
from companion_daemon.world_v2.expression_draft import (
    ExpressionDraft,
    qq_expression_capabilities,
)
from companion_daemon.world_v2.recall_audit import CharacterRecallRequest


def _contract(*, version: str = "2", phase: str = "initial", recall_allowed: bool = True):
    return InboundToolContracts().contract_for(
        phase=phase,
        capabilities=qq_expression_capabilities("napcat"),
        recall_allowed=recall_allowed,
        schema_dialect="deepseek-strict",
        atomic_envelope_version=version,
    )


def _recall() -> dict[str, object]:
    return {
        "result_kind": "recall",
        "appraisal_draft": None,
        "expression_draft": None,
        "private_turn_state": _decision()["expression_draft"]["private_turn_state"],
        "recall_request": {
            "query_text": "A previous conversation.",
            "lexical_text": None,
            "occurred_from": None,
            "occurred_to": None,
            "link_refs": [],
            "memory_kinds": [],
            "include_historical": False,
            "limit": 8,
        },
    }


def _decision() -> dict[str, object]:
    return {
        "result_kind": "decision",
        "appraisal_draft": {
            "appraise": False,
            "affect": "no_change",
            "brief_rationale": "Nothing lasting changed.",
            "behavior_tendency": "Respond freely.",
            "stance": "Present.",
            "display_strategy": "Direct.",
            "confidence": 7000,
            "meanings": None,
            "attribution": None,
            "severity": None,
            "components": None,
            "episode_id": None,
            "resolution_summary": None,
            "relationship_signal": None,
            "relationship_commitment": None,
            "interaction_act": None,
            "life_intent": None,
        },
        "expression_draft": {
            "private_turn_state": {
                "contract": "private-turn-state.1",
                "inner_state_summary": "I am considering this.",
                "attended_source_refs": [],
                "keep_impression": None,
                "stuck_with_me": None,
                "noticed": None,
                "about_us": None,
                "why_us": None,
                "we_are": None,
                "calling_it": None,
                "said_as": None,
                "declared_display": None,
            },
            "timing_choice": "now",
            "turn_posture": "continue",
            "cadence": "conversational",
            "beats": [
                {"modality": "text", "text": "Hello.", "reaction_id": None, "sticker_id": None}
            ],
            "delay_seconds": None,
            "expires_after_seconds": None,
            "stance": "Present.",
            "brief_rationale": "I want to say this.",
            "impulse_summary": None,
            "confidence": 7000,
            "variation_profile": None,
            "response_expectation": None,
            "response_expectation_assessment": None,
            "revisit": None,
            "world_claims": [],
            "media_request": "none",
            "media_source_refs": [],
        },
        "private_turn_state": None,
        "recall_request": None,
    }


def test_opt_in_atomic_wrapper_preserves_the_complete_canonical_decision() -> None:
    options = {
        "phase": "initial",
        "capabilities": qq_expression_capabilities("napcat"),
        "recall_allowed": True,
        "schema_dialect": "deepseek-strict",
    }
    legacy = InboundToolContracts().contract_for(**options)
    compact = InboundToolContracts().contract_for(**options, atomic_envelope_version="2")
    value = _decision()

    schema = compact.provider_tools[0]["function"]["parameters"]
    Draft202012Validator(schema).validate({"result": value})
    unwrapped = compact.unwrap(json.dumps({"result": value}))
    assert unwrapped == legacy.unwrap(json.dumps(value))
    result = json.loads(unwrapped)
    AppraisalDraftWire.model_validate_json(json.dumps(result["appraisal_draft"]), strict=True)
    ExpressionDraft.model_validate_json(json.dumps(result["expression_draft"]), strict=True)
    assert compact.identity.version == "2"
    assert compact.identity.tool_name == "character_inbound_initial_v2"
    assert compact.identity.schema_sha256 != legacy.identity.schema_sha256
    assert compact.identity.contract_sha256 != legacy.identity.contract_sha256
    assert compact.identity.capabilities_sha256 == legacy.identity.capabilities_sha256


def test_v2_rejects_duplicate_result_keys_before_selecting_a_branch() -> None:
    contract = InboundToolContracts().contract_for(
        phase="initial",
        capabilities=qq_expression_capabilities("napcat"),
        recall_allowed=True,
        schema_dialect="deepseek-strict",
        atomic_envelope_version="2",
    )
    raw = '{"result":null,"result":' + json.dumps(_decision()) + "}"
    with pytest.raises(ValueError, match="duplicate"):
        contract.unwrap(raw)


@pytest.mark.parametrize("phase", ["initial", "after_recall", "final"])
@pytest.mark.parametrize("timing", ["now", "later", "silent"])
def test_v2_preserves_atomic_timing_choices_and_exact_unwrapped_wire(phase, timing) -> None:
    legacy, compact = _contract(version="1", phase=phase), _contract(phase=phase)
    value = _decision()
    if phase != "initial":
        del value["private_turn_state"], value["recall_request"]
    expression = value["expression_draft"]
    expression["timing_choice"] = timing
    if timing == "later":
        expression.update(delay_seconds=20, expires_after_seconds=120)
    elif timing == "silent":
        expression["beats"] = []

    schema = compact.provider_tools[0]["function"]["parameters"]
    Draft202012Validator(schema).validate({"result": value})
    unwrapped = compact.unwrap(json.dumps({"result": value}))
    assert unwrapped == legacy.unwrap(json.dumps(value))
    ExpressionDraft.model_validate_json(
        json.dumps(json.loads(unwrapped)["expression_draft"]), strict=True
    )
    assert compact.identity.tool_name.endswith("_v2")
    assert compact.identity.version == "2"


def test_v2_preserves_character_recall_and_the_existing_permission_boundary() -> None:
    value = _recall()
    compact = _contract()
    schema = compact.provider_tools[0]["function"]["parameters"]
    Draft202012Validator(schema).validate({"result": value})
    unwrapped = compact.unwrap(json.dumps({"result": value}))
    assert unwrapped == _contract(version="1").unwrap(json.dumps(value))
    CharacterRecallRequest.model_validate_json(
        json.dumps(json.loads(unwrapped)["recall_request"]), strict=True
    )
    with pytest.raises(ValueError, match="recall transport is unavailable"):
        _contract(recall_allowed=False).unwrap(json.dumps({"result": value}))
    for phase in ("after_recall", "final"):
        with pytest.raises(ValueError):
            _contract(phase=phase).unwrap(json.dumps({"result": value}))


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        "result",
        {},
        {"result": None},
        {"result": []},
        {"result": "{}"},
        {"result": {}, "extra": None},
    ],
)
def test_v2_rejects_nonexact_outer_envelopes(value) -> None:
    with pytest.raises(ValueError):
        _contract().unwrap(json.dumps(value))


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_field",
        "extra_field",
        "mixed_recall",
        "missing_kind",
        "invalid_kind",
        "null_appraisal",
    ],
)
def test_v2_rejects_ambiguous_inner_envelopes(mutation) -> None:
    value = _decision()
    if mutation == "missing_field":
        del value["recall_request"]
    elif mutation == "extra_field":
        value["extra"] = None
    elif mutation == "mixed_recall":
        value["recall_request"] = _recall()["recall_request"]
    elif mutation == "missing_kind":
        del value["result_kind"]
    elif mutation == "invalid_kind":
        value["result_kind"] = "reply_only"
    else:
        value["appraisal_draft"] = None
    compact = _contract()
    schema = compact.provider_tools[0]["function"]["parameters"]
    assert not Draft202012Validator(schema).is_valid({"result": value})
    with pytest.raises(ValueError):
        compact.unwrap(json.dumps({"result": value}))


def test_v2_rejects_nested_duplicate_fields_and_does_not_reinterpret_v1() -> None:
    value = _decision()
    raw = json.dumps({"result": value}).replace(
        '"confidence": 7000', '"confidence": 1, "confidence": 7000', 1
    )
    with pytest.raises(ValueError, match="duplicate"):
        _contract().unwrap(raw)
    with pytest.raises(ValueError):
        _contract().unwrap(json.dumps(value))
    with pytest.raises(ValueError):
        _contract(version="1").unwrap(json.dumps({"result": value}))


@pytest.mark.parametrize("version", [None, 2, "3"])
def test_unknown_atomic_envelope_versions_are_rejected(version) -> None:
    with pytest.raises(ValueError, match="envelope version"):
        _contract(version=version)


@pytest.mark.parametrize(
    "transport,dialect",
    [("stream", "deepseek-strict"), ("atomic", "standard"), ("stream", "standard")],
)
def test_v2_cannot_change_other_transport_dialects(transport, dialect) -> None:
    with pytest.raises(ValueError, match="strict atomic"):
        InboundToolContracts().contract_for(
            phase="initial",
            capabilities=qq_expression_capabilities("napcat"),
            recall_allowed=True,
            transport=transport,
            schema_dialect=dialect,
            atomic_envelope_version="2",
        )


def test_v2_preserves_all_strict_branch_constraints_while_halving_schema_size() -> None:
    legacy = _contract(version="1").provider_tools[0]["function"]["parameters"]
    compact = _contract().provider_tools[0]["function"]["parameters"]
    Draft202012Validator.check_schema(compact)
    assert compact["properties"]["result"]["anyOf"] == legacy["anyOf"]
    decision = compact["properties"]["result"]["anyOf"][0]["properties"]
    assert set(AppraisalDraftWire.model_fields) <= set(decision["appraisal_draft"]["properties"])
    assert set(ExpressionDraft.model_fields) <= set(decision["expression_draft"]["properties"])
    assert len(json.dumps(compact)) < 0.6 * len(json.dumps(legacy))

    # Root-only deletion is not equivalent: both payloads can then coexist.
    weakened = deepcopy(legacy)
    del weakened["anyOf"]
    mixed = _decision() | {"recall_request": _recall()["recall_request"]}
    assert Draft202012Validator(weakened).is_valid(mixed)
    assert not Draft202012Validator(compact).is_valid({"result": mixed})


def test_v2_keeps_canonical_validation_after_provider_subset_validation() -> None:
    invalid = _decision()
    invalid["expression_draft"]["confidence"] = 10001
    for version in ("1", "2"):
        contract = _contract(version=version)
        carrier = invalid if version == "1" else {"result": invalid}
        schema = contract.provider_tools[0]["function"]["parameters"]
        # DeepSeek's existing subset omits numeric maximum; the canonical
        # validator is still required after either lossless decoder.
        Draft202012Validator(schema).validate(carrier)
        decoded = json.loads(contract.unwrap(json.dumps(carrier)))
        with pytest.raises(ValueError, match="10000"):
            ExpressionDraft.model_validate_json(
                json.dumps(decoded["expression_draft"]), strict=True
            )


def test_default_v1_tool_bytes_and_identity_match_the_frozen_pre_v2_contract() -> None:
    # Captured from 33690130 before this change, using PYTHONHASHSEED=0.
    # This covers every phase, transport, dialect, Recall permission and both
    # production QQ capability profiles; no git checkout is needed at test time.
    source = """
import hashlib,itertools,json
from companion_daemon.world_v2.character_interior.inbound_tool_contract import InboundToolContracts
from companion_daemon.world_v2.expression_draft import qq_expression_capabilities
results=[]
for phase,transport,dialect,adapter,recall in itertools.product(
    ('initial','after_recall','final'),('atomic','stream'),
    ('standard','deepseek-strict'),('napcat','official'),(False,True)
):
    c=InboundToolContracts().contract_for(
        phase=phase,transport=transport,schema_dialect=dialect,
        capabilities=qq_expression_capabilities(adapter),recall_allowed=recall
    )
    results.append({
        'coordinates':[phase,transport,dialect,adapter,recall],
        'tools':c.provider_tools,'choice':c.provider_tool_choice,
        'identity':c.identity.request_identity_material()
    })
wire=json.dumps(results,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
print(json.dumps({'contracts':len(results),'bytes':len(wire),'sha256':hashlib.sha256(wire).hexdigest()}))
"""
    root = Path(__file__).resolve().parents[2]
    env = os.environ | {
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(root / "src"),
    }
    result = subprocess.run(
        [sys.executable, "-c", source],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert json.loads(result.stdout) == {
        "contracts": 48,
        "bytes": 3690113,
        "sha256": "0d35bbe31d871a5d33623382bb6961f8106431935d62ba99dca5633c68f662e0",
    }
