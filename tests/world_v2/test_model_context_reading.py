"""First-call evidence transport, without a semantic reviewer or cloud calls."""
from copy import deepcopy
import json

import pytest

from companion_daemon.world_v2.background_context_profile import (
    background_context_profile_for_purpose, slice_background_capsule_context,
)
from companion_daemon.world_v2.character_interior.core import CharacterInterior
from companion_daemon.world_v2.character_interior.ports import _RecallResult
from test_character_interior_structured_role import (
    StructuredCharacterRoleFaculty, _RequiredToolQueueModel, _request,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose", [
    "proactive_contact", "private_impression_reflection", "memory_consolidation",
    "experience_memory_retention", "life_development_choice", "world_stimulus_appraisal",
    "media_selection", "expression_reconsideration", "external_perception_attention",
    "qq_attachment_perception", "activity_lifecycle_choice", "outcome_selection",
    "prehistory_memory_batch", "fact_memory_retention", "memory_withdrawal_review",
])
@pytest.mark.parametrize("reference_wire", [False, True])
async def test_actual_recall_reaches_next_role_request(purpose, reference_wire):
    from companion_daemon.world_v2.reference_wire import expand_reference_view, prepare_reference_view

    request = await _request(purpose=purpose)
    recalled = _RecallResult(world_id=request.snapshot.world_id, actor_ref=request.snapshot.actor_ref,
        cursor=request.snapshot.cursor, content={"text": "共同核对稿件，后来补上署名。"},
        source_refs=("memory:explicit-pull",))
    merged = CharacterInterior._merge_recall(request.snapshot, recalled)
    frozen = merged.model_dump_json()
    followup = request.model_copy(update={"snapshot": merged, "recall_completed": True})
    role = StructuredCharacterRoleFaculty(model=_RequiredToolQueueModel(), model_id="fixture",
        reference_wire=reference_wire)
    # Test the provider presentation seam, not action/manifest acceptance.
    messages = role._base_messages(followup, contract=role._contracts[purpose])
    packet = json.loads(messages[1]["content"])
    if reference_wire:
        view, bindings = prepare_reference_view(packet)
        packet = expand_reference_view(view, bindings)
    selected = packet["inner_life_snapshot"]["materials"]["selected_recall"]
    assert selected["content"] == recalled.content
    assert selected["source_refs"] == list(recalled.source_refs)
    assert "memory:explicit-pull" in packet["inner_life_snapshot"]["source_refs"]
    assert any(row["source_ref"] == "memory:explicit-pull"
               for row in packet["inner_life_snapshot"]["source_inventory"])
    assert packet["selective_recall"]["available"] is False
    assert packet["context_reading"]["selected_recall"] == "presented"
    assert merged.model_dump_json() == frozen


def test_world_author_does_not_inherit_private_recall():
    context = {"slices": {"private_impressions": {"items": ["PRIVATE"]},
                          "character_core": {"items": ["identity"]}}}
    original = deepcopy(context)
    for purpose in ("world_consequence_draft", "life_development_draft"):
        view = slice_background_capsule_context(context, background_context_profile_for_purpose(purpose))
        assert "PRIVATE" not in json.dumps(view)
        assert view["context_reading"]["history_coverage"] == "partial"
    assert context == original


def test_encoded_materials_are_not_reported_absent():
    from companion_daemon.world_v2.model_context_reading import context_reading_scope

    reading = context_reading_scope({"materials": {"contract": "shared-string-view.1"}})
    assert reading["selected_recall"] == "encoded_view"


@pytest.mark.asyncio
async def test_pinned_life_reviewer_view_keeps_its_registered_shape():
    from companion_daemon.world_v2.character_interior.life_source_view import _expected_view
    from companion_daemon.world_v2.character_interior.life_source_review import MINTED_CONTRACT

    request = await _request(purpose="world_stimulus_appraisal")
    recalled = _RecallResult(world_id=request.snapshot.world_id, actor_ref=request.snapshot.actor_ref,
        cursor=request.snapshot.cursor, content={"text": "PRIVATE RECALL"},
        source_refs=("memory:explicit-pull",))
    merged = CharacterInterior._merge_recall(request.snapshot, recalled)
    followup = request.model_copy(update={"snapshot": merged, "recall_completed": True})
    role = StructuredCharacterRoleFaculty(model=_RequiredToolQueueModel(), model_id="fixture",
        life_source_reviewer=object())
    packet = json.loads(role._base_messages(followup, contract=role._contracts[request.purpose])[1]["content"])
    assert packet["inner_life_snapshot"] == _expected_view(merged, MINTED_CONTRACT)
    assert packet["context_reading"]["selected_recall"] == "not_presented"


@pytest.mark.asyncio
async def test_one_background_provider_call_contains_the_completed_pull():
    from test_character_interior_structured_role import _proactive_manifest, _result

    request = await _request(purpose="proactive_contact", capability_manifest=_proactive_manifest())
    recalled = _RecallResult(world_id=request.snapshot.world_id, actor_ref=request.snapshot.actor_ref,
        cursor=request.snapshot.cursor, content={"text": "共同核对稿件，后来补上署名。"},
        source_refs=("memory:explicit-pull",))
    merged = CharacterInterior._merge_recall(request.snapshot, recalled)
    model = _RequiredToolQueueModel(_result(status="decision", decision={
        "source_refs": ["source:private_self"], "payload": {
            "timing_choice": "silent", "cadence": "conversational", "beats": [],
            "stance": "keeping the thought private", "brief_rationale": "not sending now",
            "impulse_summary": "remembering a shared experience", "confidence": 6400,
            "world_claims": [],
        },
    }))
    role = StructuredCharacterRoleFaculty(model=model, model_id="fixture")
    await role.consider(request.model_copy(update={"snapshot": merged, "recall_completed": True}))
    assert len(model.calls) == 1
    packet = json.loads(model.calls[0][0][1]["content"])
    assert packet["inner_life_snapshot"]["materials"]["selected_recall"]["content"] == recalled.content
