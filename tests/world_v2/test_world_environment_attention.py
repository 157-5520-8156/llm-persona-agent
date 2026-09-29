"""Environmental sampling never creates presence, permission or an act."""

from copy import deepcopy
import json

import pytest

from companion_daemon.world_v2.world_environment_attention import environment_attention
from companion_daemon.world_v2.world_consequence_prompt import compile_world_consequence_messages
from test_world_consequence_prompt import _inputs


def context():
    authority, value = _inputs()
    value["capability_manifest"]["location_capabilities"] = [
        {"location_ref": name, "capability_ref": "cap:" + name}
        for name in ("park", "canteen", "closed")
    ]
    value["timing_coordinates"] = {
        "pinned_logical_time": {"utc": "2026-09-27T01:00:00Z"},
        "location_capability_coordinates": [
            {"location_ref": name, "capability_ref": "cap:" + name,
             "maximum_now_duration_minutes": minutes}
            for name, minutes in (("park", 30), ("canteen", 90), ("closed", None), ("unoffered", 100))
        ],
    }
    return authority, value


def test_attention_is_stable_with_permuted_inputs_and_preserves_full_authority():
    authority, value = context()
    before = deepcopy(value)
    messages = compile_world_consequence_messages(user_context=value, authority=authority, execution_materials=())
    packet = json.loads(messages[1]["content"])
    focus = packet["environment_attention"]
    assert value == before
    assert packet["pinned_world_context"] == value["pinned_world_context"]
    assert packet["capability_manifest"] == value["capability_manifest"]
    assert packet["execution_authority"]["execution_bindings"] == []
    assert focus["coordinate"]["location_ref"] in {"park", "canteen"}
    assert focus["scope"] == "candidate_environment_only_not_presence_or_execution"
    value["timing_coordinates"]["location_capability_coordinates"].reverse()
    value["capability_manifest"]["location_capabilities"].reverse()
    assert environment_attention(value) == focus


@pytest.mark.parametrize("key", ["completed_activity_consequence", "active_attempt_consequence"])
def test_attention_cannot_redirect_an_exact_attempt_result(key):
    _, value = context()
    value["capability_manifest"][key] = {"exact_source": "attempt"}
    assert environment_attention(value) is None


def test_no_attention_with_only_closed_or_unoffered_places_or_disturbance():
    _, value = context()
    value["occasion_mode"] = "disturbance"
    assert environment_attention(value) is None
    value["occasion_mode"] = "ordinary"
    value["timing_coordinates"]["location_capability_coordinates"] = value["timing_coordinates"]["location_capability_coordinates"][2:]
    assert environment_attention(value) is None
