from __future__ import annotations

import json

from companion_daemon.world_v2.character_interior.inbound_tool_contract import (
    InboundToolContracts,
)
from companion_daemon.world_v2.expression_draft import (
    QQ_NAPCAT_EXPRESSION_CAPABILITIES,
    normalize_expression_draft_wire,
)
from companion_daemon.world_v2.present_prompt import (
    json_schema_g4_metrics,
    present_hard_boundary_prompt,
)


def test_compact_gate_schema_fits_g4_area_cap() -> None:
    contract = InboundToolContracts().compact_gate_for(
        capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
        recall_allowed=True,
    )
    parameters = contract.provider_tools[0]["function"]["parameters"]
    required, total, depth = json_schema_g4_metrics(parameters)
    assert required <= 3
    assert total <= 8
    assert depth <= 2


def test_present_hard_boundary_prompt_drops_mechanism_essays() -> None:
    stub = present_hard_boundary_prompt(
        {
            "contract": "expression-hard-boundaries.8",
            "private_turn_state": {"epistemic_authority": {"covers": "essay"}},
            "world_claim_source_refs": {"stable_identity": ["identity-frame:sha256:abc"]},
            "source_ref_aliases": {"S1": "identity-frame:sha256:abc"},
            "companion_life_authority_availability": {"authority": "pinned_claim_capability_only"},
            "single_report_epistemic_scope": {"cannot_authorize": ["class_wide_assertion"]},
        }
    )
    assert stub["contract"] == "expression-hard-boundaries.present.1"
    assert stub["authority"] == "checked_after_expression"
    assert "private_turn_state" not in stub
    assert "single_report_epistemic_scope" not in stub
    assert stub["world_claim_source_refs"]["stable_identity"] == ["identity-frame:sha256:abc"]


def test_compact_gate_accepts_slim_payload_json() -> None:
    from companion_daemon.world_v2.character_interior.inbound_tool_contract import (
        _expand_compact_gate_payload,
    )

    expanded = _expand_compact_gate_payload(
        {
            "result_kind": "reply_only",
            "payload_json": json.dumps(
                {
                    "messages": ["嗯，我在听。"],
                    "felt": "心里还挂着刚才那句话",
                    "stuck_with_me": "他还没说完",
                    "wants": "想听他继续",
                    "photo": False,
                },
                ensure_ascii=False,
            ),
        }
    )
    assert expanded["result_kind"] == "reply_only"
    assert expanded["protocol"] == "character-interior-events.1"
    assert expanded["events"][0]["beat"]["text"] == "嗯，我在听。"
    assert expanded["events"][0]["world_claims"] == []
    from companion_daemon.world_v2.present_prompt import compile_slim_consider_payload

    compiled = compile_slim_consider_payload(
        {
            "messages": ["嗯，我在听。"],
            "felt": "心里还挂着刚才那句话",
            "stuck_with_me": "他好像没把这件事说完",
            "wants": "想听他继续说",
            "photo": False,
        }
    )
    assert compiled is not None
    assert compiled["appraisal_draft"]["appraise"] is False
    assert compiled["expression_draft"]["beats"] == [{"modality": "text", "text": "嗯，我在听。"}]
    assert compiled["expression_draft"]["world_claims"] == []
    assert compiled["expression_draft"]["impulse_summary"] == "想听他继续说"
    assert compiled["expression_draft"]["private_turn_state"]["inner_state_summary"] == (
        "他好像没把这件事说完"
    )


def test_string_beats_normalize_to_text_objects() -> None:
    normalized = normalize_expression_draft_wire(
        {
            "timing_choice": "now",
            "beats": ["嗯，我在听。", "你继续说。"],
            "stance": "present",
            "brief_rationale": "small reply",
            "confidence": 5000,
        }
    )
    assert normalized["beats"] == [
        {"modality": "text", "text": "嗯，我在听。"},
        {"modality": "text", "text": "你继续说。"},
    ]
