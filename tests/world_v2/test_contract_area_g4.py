from __future__ import annotations

import json

import pytest

from companion_daemon.world_v2.character_interior.inbound_tool_contract import (
    InboundToolContracts,
)
from companion_daemon.world_v2.character_interior.inbound_author import (
    _compact_full_turn_transport_grammar,
    _compact_gate_system_content,
    _compact_reply_only_transport_grammar,
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


def test_compact_gate_semantic_prompt_stays_bounded_and_preserves_truth_boundary() -> None:
    reply_only = _compact_reply_only_transport_grammar(
        response_expectation_assessment_required=False
    )
    full_turn = _compact_full_turn_transport_grammar(
        capabilities=QQ_NAPCAT_EXPRESSION_CAPABILITIES,
        response_expectation_assessment_required=False,
    )
    reply_decoded = reply_only["decoded_payload_json"]
    full_decoded = full_turn["decoded_payload_json"]
    system = _compact_gate_system_content(
        identity_instruction="她是身份框架里的人。",
        reply_only_specimen=reply_decoded["shape_only_nonsemantic_specimen"],
        reply_only_rules={key: value for key, value in reply_only.items() if key != "decoded_payload_json"},
        full_turn_specimen=full_decoded["shape_only_nonsemantic_specimen"],
        full_turn_rules={key: value for key, value in full_turn.items() if key != "decoded_payload_json"},
    )

    assert len(system) <= 15_000
    assert "空 world_claims 不是无事实证明" in system
    assert "没有来源就不要说成已发生的事实" in system
    assert "没有维持对话或提供帮助的任务" in system
    assert "依照身份自然表达" in system


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
    assert stub["contract"] == "expression-hard-boundaries.present.2"
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
                    "meaning_of_this": "他还没有把刚才那件事说完",
                    "my_state": "我心里还挂着刚才那句话",
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
            "meaning_of_this": "他还没有把刚才那件事说完",
            "my_state": "我心里还挂着刚才那句话",
            "stuck_with_me": "他好像没把这件事说完",
            "wants": "想听他继续说",
            "photo": False,
        }
    )
    assert compiled is not None
    assert compiled["appraisal_draft"]["appraise"] is True
    assert compiled["appraisal_draft"]["affect"] == "no_change"
    assert compiled["appraisal_draft"]["meanings"] == [
        {"meaning": "他还没有把刚才那件事说完", "confidence": 5000}
    ]
    assert compiled["appraisal_draft"]["attribution"] == "unknown"
    assert "relationship_signal" not in compiled["appraisal_draft"]
    assert compiled["expression_draft"]["beats"] == [{"modality": "text", "text": "嗯，我在听。"}]
    assert compiled["expression_draft"]["world_claims"] == []
    assert compiled["expression_draft"]["impulse_summary"] == "想听他继续说"
    assert compiled["expression_draft"]["private_turn_state"]["inner_state_summary"] == (
        "我心里还挂着刚才那句话"
    )


def test_slim_affect_requires_role_authored_component_intensity() -> None:
    from companion_daemon.world_v2.present_prompt import compile_slim_consider_payload

    opened = compile_slim_consider_payload(
        {
            "messages": ["嗯。"],
            "meaning_of_this": "这句话让我觉得他没有认真听",
            "my_state": "我心里有点闷",
            "affect": "open",
            "components": [{"dimension": "sadness", "target_intensity_bp": 3600}],
        }
    )
    assert opened is not None
    assert opened["appraisal_draft"]["appraise"] is True
    assert opened["appraisal_draft"]["affect"] == "open"
    assert opened["appraisal_draft"]["components"] == [
        {"dimension": "sadness", "target_intensity_bp": 3600}
    ]

    with pytest.raises(ValueError, match="target_intensity_bp"):
        compile_slim_consider_payload(
            {
                "messages": ["嗯。"],
                "meaning_of_this": "这句话让我觉得他没有认真听",
                "my_state": "我心里有点闷",
                "mood": "sadness",
            }
        )

    stable = compile_slim_consider_payload(
        {
            "messages": ["嗯。"],
            "meaning_of_this": "这句话只是普通说明",
            "my_state": "我现在很平静",
        }
    )
    assert stable is not None
    assert stable["appraisal_draft"]["affect"] == "no_change"
    assert "components" not in stable["appraisal_draft"]


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
