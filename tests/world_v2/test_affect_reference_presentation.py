from __future__ import annotations

from copy import deepcopy
import json

import pytest

from companion_daemon.world_v2.affect_reference_view import (
    APPRAISAL_REFERENCE_TABLE_CONTRACT,
    expand_appraisal_references,
    pack_appraisal_references,
)
from companion_daemon.world_v2.character_interior.affect_model_view import (
    compact_affect_for_model_view,
)
from companion_daemon.world_v2.character_interior.contracts import _material_entries
from companion_daemon.world_v2.present_prompt import (
    affect_material_entries,
    cache_stable_affect,
    expand_present_world_context,
    order_user_present_payload,
)
from companion_daemon.world_v2.schemas import AppraisalMeaningRef


def _wire(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _refs(count: int = 12) -> list[dict]:
    return [
        AppraisalMeaningRef(
            appraisal_id=f"appraisal:{index:064x}",
            hypothesis_id=f"hypothesis:{index:064x}",
            source_cluster_ref="source-cluster:shared-stimulus",
            accepted_change_id=f"change:{index:064x}",
            accepted_transition_id=f"transition:{index:064x}",
        ).model_dump(mode="json")
        for index in range(count)
    ]


def _episode(index: int) -> dict:
    refs = _refs()
    return {
        "episode_id": f"affect:{index}",
        "entity_revision": index + 1,
        "status": "active",
        "opened_at": "2026-09-01T10:00:00Z",
        "expression_history_refs": ["expression:a", "expression:b"],
        "components": [
            {
                "component_id": f"component:{index}",
                "dimension": "warmth",
                "intensity_bp": 3234,
                "residue_bp": 100,
                "decay_profile": {"kind": "exponential", "half_life_seconds": 3600},
                # A duplicate retains its exact position; table encoding does
                # not claim that equal provenance implies one stimulus.
                "appraisal_refs": [refs[1], *refs, refs[0]],
                "arbitrary_new_semantic_field": {"prose": "这份感觉仍然有点复杂。"},
            },
        ],
        "source_ref": f"affect:{index}:revision:{index + 1}",
    }


@pytest.mark.parametrize("projected", [False, True])
def test_typed_reference_tables_round_trip_every_field_and_member_order(projected: bool) -> None:
    episode = _episode(0)
    if projected:
        episode = compact_affect_for_model_view([episode])[0]
    refs = episode["components"][0]["appraisal_refs"]
    original = _wire(refs)
    packed = pack_appraisal_references(refs)
    assert packed["contract"] == APPRAISAL_REFERENCE_TABLE_CONTRACT
    assert len(packed["rows"]) == len(refs)
    assert len(_wire(packed).encode()) < len(original.encode())
    assert _wire(expand_appraisal_references(packed)) == original
    assert _wire(refs) == original
    assert pack_appraisal_references(packed) is packed


@pytest.mark.parametrize("wrapper", ["list", "cache", "legacy"])
def test_author_presentation_and_host_readers_retain_complete_affect(wrapper: str) -> None:
    canonical = [_episode(0), _episode(1), _episode(2)]
    if wrapper == "cache":
        affect = {"stable_entries": canonical[:-1], "volatile_last_entry": canonical[-1]}
    elif wrapper == "legacy":
        affect = {"items": canonical, "availability": "available"}
    else:
        affect = canonical
    source = {
        "inner_life_snapshot": {
            "materials": {
                "affect": affect,
                "appraisals": {"columns": ["ref", "meaning"], "rows": [["a", "没有改变。"]]},
            },
        },
    }
    original = deepcopy(source)
    presented = order_user_present_payload(source)
    view = presented["inner_life_snapshot"]["materials"]["affect"]
    assert "appraisal-refs-table.1" in _wire(view)
    assert _wire(affect_material_entries(view)) == _wire(canonical)
    assert _wire(_material_entries({"affect": view}, "affect")) == _wire(canonical)
    assert source == original
    assert order_user_present_payload(presented) == presented
    expanded = expand_present_world_context(presented)
    restored = expanded["inner_life_snapshot"]["materials"]["affect"]
    assert _wire(affect_material_entries(restored)) == _wire(canonical)
    assert "appraisal-refs-table.1" not in _wire(restored)
    assert expanded["inner_life_snapshot"]["materials"]["appraisals"] == original["inner_life_snapshot"]["materials"]["appraisals"]
    if wrapper == "legacy":
        assert restored["availability"] == "available"
    assert order_user_present_payload(expanded) == presented


def test_new_episode_does_not_reencode_prior_component_tables() -> None:
    old = cache_stable_affect([_episode(0), _episode(1)])
    new = cache_stable_affect([_episode(0), _episode(1), _episode(2)])
    assert _wire(old["stable_entries"][0]) == _wire(new["stable_entries"][0])
    assert _wire(old["volatile_last_entry"]) == _wire(new["stable_entries"][1])


@pytest.mark.parametrize("fault", ["extra", "missing", "mixed_order", "revision", "bool", "empty", "null"])
def test_unrecognized_reference_shapes_are_kept_verbatim(fault: str) -> None:
    refs = _refs()
    if fault == "extra":
        refs[3]["new_field"] = "retain me"
    elif fault == "missing":
        del refs[3]["hypothesis_id"]
    elif fault == "mixed_order":
        refs[3] = dict(reversed(list(refs[3].items())))
    elif fault == "revision":
        refs[3]["accepted_entity_revision"] = 2
    elif fault == "bool":
        refs[3]["accepted_entity_revision"] = True
    elif fault == "empty":
        refs[3]["appraisal_id"] = ""
    else:
        refs[3]["appraisal_id"] = None
    original = _wire(refs)
    assert pack_appraisal_references(refs) is refs
    assert expand_appraisal_references(refs) is refs
    assert _wire(refs) == original


@pytest.mark.parametrize("fault", ["extra", "column", "duplicate", "short", "bool", "missing", "empty"])
def test_corrupt_recognized_tables_never_silently_drop_provenance(fault: str) -> None:
    packet = pack_appraisal_references(_refs())
    if fault == "extra":
        packet["unexpected"] = None
    elif fault == "column":
        packet["columns"][0] = "unknown"
    elif fault == "duplicate":
        packet["columns"][1] = packet["columns"][0]
    elif fault == "short":
        packet["rows"][0].pop()
    elif fault == "bool":
        packet["rows"][0][packet["columns"].index("accepted_entity_revision")] = True
    elif fault == "missing":
        del packet["rows"]
    else:
        packet["rows"] = []
    with pytest.raises(ValueError, match="Affect appraisal reference"):
        expand_appraisal_references(packet)
    with pytest.raises(ValueError, match="Affect appraisal reference"):
        affect_material_entries([{"components": [{"appraisal_refs": packet}]}])


def test_small_and_unknown_values_keep_the_original_shape() -> None:
    for value in (None, [], {}, {"contract": "appraisal-refs-table.future", "rows": []}, _refs(1)):
        assert pack_appraisal_references(value) is value
        assert expand_appraisal_references(value) is value
    malformed_episode = {"components": [None, {"dimension": "joy"}, {"appraisal_refs": None}]}
    assert affect_material_entries([malformed_episode]) == [malformed_episode]
