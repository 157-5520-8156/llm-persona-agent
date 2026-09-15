import json

import pytest

from companion_daemon.world_v2.visible_rejection_context import RejectedVisibleBeat, RejectedVisibleExpression
from companion_daemon.world_v2.character_interior.ports import _InteriorRoleRequest
from companion_daemon.world_v2.character_interior.structured_role import StructuredCharacterRoleFaculty
from test_character_interior_structured_role import _QueueModel, _request, _result, _TEST_GENERIC_CONTRACT


def expression():
    return RejectedVisibleExpression(
        candidate_sha256="a" * 64, source_table_sha256="b" * 64,
        beats=(RejectedVisibleBeat(beat_index=0, text="原文" * 2048),
               RejectedVisibleBeat(beat_index=1, text='含引号 " 和换行\n的被拒候选。')),
    )


def test_full_original_text_survives_round_trip_without_becoming_evidence():
    original = expression()
    assert RejectedVisibleExpression.model_validate_json(original.model_dump_json()) == original
    assert len(original.beats[0].text) == 4096
    assert original.authority == "rejected_candidate_not_world_evidence"
    with pytest.raises(ValueError, match="original order"):
        RejectedVisibleExpression.model_validate({**original.model_dump(), "beats": tuple(reversed(original.beats))})


@pytest.mark.asyncio
async def test_source_correction_retains_original_prose_outside_world_material():
    request = await _request(correction_ordinal=1, correction_failure_code="role_result_source_invalid",
                             correction_failure_detail="source reader rejected the claim")
    request = _InteriorRoleRequest.model_validate({**request.model_dump(), "correction_rejected_expression": expression()})
    model = _QueueModel(_result(status="silent"))
    await StructuredCharacterRoleFaculty(
        model=model, model_id="fixture", purpose_contracts=(_TEST_GENERIC_CONTRACT,),
    ).consider(request)
    user = json.loads(model.calls[0][0][1]["content"])
    assert user["correction"]["failure_code"] == "role_result_source_invalid"
    assert user["correction"]["rejected_expression"] == expression().model_dump(mode="json")
    assert "rejected_expression" not in user["inner_life_snapshot"]["materials"]


@pytest.mark.asyncio
@pytest.mark.parametrize("ordinal,code", [(0, None), (1, "role_result_schema_invalid")])
async def test_rejected_text_cannot_enter_an_ordinary_or_unrelated_correction(ordinal, code):
    request = await _request(correction_ordinal=ordinal, correction_failure_code=code)
    assert "correction_rejected_expression" not in request.model_dump()
    with pytest.raises(ValueError, match="same-author source correction"):
        _InteriorRoleRequest.model_validate({**request.model_dump(), "correction_rejected_expression": expression()})
