from copy import deepcopy
import hashlib
import json

import pytest

from companion_daemon.world_v2.life_development_output_schema import life_possibility_output_schema
from companion_daemon.world_v2.world_consequence_contract import (
    WorldConsequenceAuthority, WorldConsequenceAuthorCursor,
)
from companion_daemon.world_v2.world_consequence_prompt import compile_world_consequence_messages


def _inputs():
    authority = WorldConsequenceAuthority(
        world_id="world:prompt", actor_ref="actor:companion",
        evaluated_cursor=WorldConsequenceAuthorCursor(
            world_revision=1, deliberation_revision=0, ledger_sequence=1,
        ),
    )
    context = {
        "capability_manifest": {
            "owner_actor_ref": authority.actor_ref,
            "pinned_cursor": authority.evaluated_cursor.model_dump(mode="json"),
            "outcome_contract": "world-consequence.2",
        },
        "cross_field_authority": {"outcome_text": {"what_she_did_in_her_world": True}},
        "disturbance_consequence_usage_specimen": {"outcome_text": "old action example"},
        "pinned_world_context": {"original_source": "The hail damaged a tree."},
    }
    return authority, context


def test_historical_output_schema_keeps_the_exact_pre_protocol_fingerprint():
    # Independently recovered from 4fed62c8, including original key insertion
    # order and required-text semantics. New replay never upgrades this wire.
    schema = life_possibility_output_schema()
    digest = hashlib.sha256(json.dumps(
        schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    assert digest == "04ed2aa92f4fbd96d66fbcc65b116ad5c427b673abc8945035efc553cace5720"


def test_current_prompt_requires_new_consequences_and_removes_old_action_permissions():
    authority, context = _inputs()
    before = deepcopy(context)
    messages = compile_world_consequence_messages(
        user_context=context, authority=authority, execution_materials=(),
    )
    assert context == before
    user = json.loads(messages[1]["content"])
    assert user["execution_authority"] == authority.model_dump(mode="json")
    assert user["execution_materials"] == []
    assert user["pinned_world_context"] == context["pinned_world_context"]
    assert "disturbance_consequence_usage_specimen" not in user
    assert "outcome_text" not in user["cross_field_authority"]
    assert "what_she_did_in_her_world" not in messages[0]["content"] + messages[1]["content"]
    outcome = user["output_contract"]["propose"]["$defs"]["LifeDevelopmentOutcomeDraft"]
    assert "text" not in outcome["properties"]
    assert "world_consequence" in outcome["required"]
    assert outcome["properties"]["world_consequence"] == {"$ref": "#/$defs/WorldConsequenceV2"}


@pytest.mark.parametrize("wrong", ["owner", "cursor", "legacy"])
def test_current_prompt_cannot_relabel_another_or_historical_manifest(wrong):
    authority, context = _inputs()
    manifest = context["capability_manifest"]
    if wrong == "owner":
        manifest["owner_actor_ref"] = "actor:other"
    elif wrong == "cursor":
        manifest["pinned_cursor"]["ledger_sequence"] += 1
    else:
        manifest.pop("outcome_contract")
    with pytest.raises(ValueError, match="exact execution material"):
        compile_world_consequence_messages(
            user_context=context, authority=authority, execution_materials=(),
        )

def test_current_prompt_example_uses_only_offered_refs_and_teaches_windows():
    authority, context = _inputs()
    context["capability_manifest"].update(
        {
            "anchor_refs": ["event:anchor:offered"],
            "max_window_minutes": 720,
        }
    )
    context["occasion_mode"] = "disturbance"

    messages = compile_world_consequence_messages(
        user_context=context, authority=authority, execution_materials=(),
    )
    system = messages[0]["content"]

    assert 'event:anchor:offered' in system
    assert 'event:anchor:1' not in system
    assert 'location:reviewed-place' not in system
    assert "capability_manifest.max_window_minutes" in system
    assert "location_capability_coordinates" in system
    assert '"dynamic_life_direction"' in system

