from copy import deepcopy

from companion_daemon.world_v2.background_context_profile import (
    background_context_profile_for_purpose, slice_background_inner_life_snapshot,
)
from companion_daemon.world_v2.character_interior.continuity_view import continuity_profile


def test_explicit_recall_prose_and_its_authority_survive_profile_filtering():
    # Recall can return a prose payload plus wrapper refs, without a nested
    # source_ref field. Previously both its material and inventory disappeared.
    source = {"materials": {
        "selected_recall": {"content": {"text": "她上次说这件事先不管。"}, "source_refs": ["source:recalled"]},
        "activity_lifecycle_states": [{"source_ref": "event:abandoned", "status": "abandoned", "plan_id": "plan:one"}],
        "planned_activities": [{"source_ref": "event:future", "status": "planned"}],
        "remembered_material": [{"source_ref": "memory:old", "retention_rationales": ["unfinished_business"]}],
    }, "source_refs": ["source:recalled", "event:abandoned", "event:future", "memory:old"],
        "source_inventory": [{"source_ref": ref, "scope": scope, "privacy_class": "private"}
            for ref, scope in (("source:recalled", "selected_recall"), ("event:abandoned", "activity_lifecycle_states"),
                               ("event:future", "planned_activities"), ("memory:old", "remembered_material"))]}
    before = deepcopy(source)
    profile = background_context_profile_for_purpose("activity_lifecycle_choice")
    old = slice_background_inner_life_snapshot(source, profile)
    assert "selected_recall" not in old["materials"]
    current = slice_background_inner_life_snapshot(source, continuity_profile(profile, lifecycle_states=True))
    assert current["materials"]["selected_recall"] == source["materials"]["selected_recall"]
    assert "source:recalled" in current["source_refs"]
    assert any(row["source_ref"] == "source:recalled" for row in current["source_inventory"])
    assert current["materials"]["activity_lifecycle_states"][0]["status"] == "abandoned"
    # Showing a later decision must neither erase the memory nor clear a live plan.
    assert current["materials"]["remembered_material"] == old["materials"]["remembered_material"]
    assert current["materials"]["planned_activities"] == old["materials"]["planned_activities"]
    assert source == before


import json  # noqa: E402
import pytest  # noqa: E402


@pytest.mark.asyncio
async def test_role_request_after_actual_recall_merge_contains_the_returned_memory():
    from companion_daemon.world_v2.character_interior.core import CharacterInterior
    from companion_daemon.world_v2.character_interior.ports import _RecallResult
    from companion_daemon.world_v2.character_interior.living_frame import configured_living_frame
    from companion_daemon.world_v2.companion_identity import CompanionIdentityFrame
    from test_character_interior_structured_role import (
        StructuredCharacterRoleFaculty, _RequiredToolQueueModel, _request, _life_development_manifest,
    )
    request = await _request(purpose="life_development_choice", capability_manifest=_life_development_manifest())
    recalled = _RecallResult(world_id=request.snapshot.world_id, actor_ref=request.snapshot.actor_ref,
        cursor=request.snapshot.cursor, content={"text": "这件事我上次已经决定先放下。"}, source_refs=("memory:explicit-pull",))
    merged = CharacterInterior._merge_recall(request.snapshot, recalled)
    followup = request.model_copy(update={"snapshot": merged, "recall_completed": True})
    role = StructuredCharacterRoleFaculty(model=_RequiredToolQueueModel(), model_id="fixture",
        character_disposition=configured_living_frame(CompanionIdentityFrame(companion_name="测试", counterpart_name="用户")))
    packet = json.loads(role._messages(followup, contract=role._resolve_contract(followup))[1]["content"])
    selected = packet["inner_life_snapshot"]["materials"]["selected_recall"]
    assert selected["content"]["text"] == recalled.content["text"]
    assert selected["source_refs"] == list(recalled.source_refs)
    assert packet["selective_recall"]["available"] is False
    assert any(row["source_ref"] == "memory:explicit-pull" for row in packet["inner_life_snapshot"]["source_inventory"])
