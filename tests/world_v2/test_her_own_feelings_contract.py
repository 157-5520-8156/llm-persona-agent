from __future__ import annotations

import pytest

from companion_daemon.world_v2.present_prompt import (
    compile_slim_consider_payload,
    reply_only_slim_shape_specimen,
    slim_consider_instruction,
    slim_consider_json_schema,
)


def _slim(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "messages": ["我先缓一下"],
        "meaning_of_this": "他这句把我的认真当成了玩笑",
        "my_state": "我有点被刺到，也不想立刻装作没事",
    }
    payload.update(updates)
    return payload


def test_slim_contract_separates_counterpart_reading_from_her_own_state() -> None:
    specimen = reply_only_slim_shape_specimen()
    schema = slim_consider_json_schema()

    assert specimen["meaning_of_this"] == "<role:reading_text>"
    assert specimen["my_state"] == "<role:self_state_text>"
    assert "felt" not in specimen
    assert schema["required"] == ["messages", "meaning_of_this", "my_state"]
    assert "felt" not in schema["properties"]

    compiled = compile_slim_consider_payload(_slim())
    assert compiled is not None
    assert compiled["appraisal_draft"]["meanings"] == [
        {"meaning": "他这句把我的认真当成了玩笑", "confidence": 5000}
    ]
    assert (
        compiled["expression_draft"]["private_turn_state"]["inner_state_summary"]
        == "我有点被刺到，也不想立刻装作没事"
    )


def test_slim_allows_calm_self_state_without_opening_affect() -> None:
    compiled = compile_slim_consider_payload(
        _slim(
            messages=["嗯 知道啦"],
            meaning_of_this="就是一句普通的行程说明",
            my_state="我现在很平静，没有特别的情绪要留下",
        )
    )

    assert compiled is not None
    assert compiled["appraisal_draft"]["affect"] == "no_change"
    assert "components" not in compiled["appraisal_draft"]


def test_lasting_affect_requires_her_authored_target_intensity() -> None:
    compiled = compile_slim_consider_payload(
        _slim(
            affect="open",
            components=[{"dimension": "hurt", "target_intensity_bp": 7300}],
        )
    )

    assert compiled is not None
    assert compiled["appraisal_draft"]["components"] == [
        {"dimension": "hurt", "target_intensity_bp": 7300}
    ]

    with pytest.raises(ValueError, match="target_intensity_bp"):
        compile_slim_consider_payload(_slim(mood="hurt"))


def test_declared_expectation_preserves_her_pressure_and_importance() -> None:
    compiled = compile_slim_consider_payload(
        _slim(
            waiting_for="想听他认真回应这件事",
            wait=90,
            pressure_bp=8200,
            importance_bp=6700,
        )
    )

    assert compiled is not None
    expectation = compiled["expression_draft"]["response_expectation"]
    assert expectation["pressure_bp"] == 8200
    assert expectation["importance_bp"] == 6700

    with pytest.raises(ValueError, match="pressure_bp"):
        compile_slim_consider_payload(
            _slim(
                waiting_for="想听他认真回应这件事",
                wait=90,
                importance_bp=6700,
            )
        )


def test_contract_exposes_strength_without_mapping_it_to_message_count() -> None:
    instruction = slim_consider_instruction()

    assert "target_intensity_bp" in instruction
    assert "pressure_bp" in instruction
    assert "情绪强度不会命令你发几条" in instruction
    assert "真的有很多话时可以连续写多条 messages" in instruction

    intense = compile_slim_consider_payload(
        _slim(
            messages=["你等一下", "这句话我真的不舒服", "别拿这个开玩笑"],
            affect="open",
            components=[{"dimension": "anger", "target_intensity_bp": 8800}],
        )
    )
    restrained = compile_slim_consider_payload(
        _slim(
            messages=["我先不说了"],
            affect="open",
            components=[{"dimension": "anger", "target_intensity_bp": 8800}],
        )
    )
    assert intense is not None and restrained is not None
    assert len(intense["expression_draft"]["beats"]) == 3
    assert len(restrained["expression_draft"]["beats"]) == 1
