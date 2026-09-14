from copy import deepcopy
import json

from companion_daemon.world_v2.character_interior.affect_model_view import compact_affect_for_model_view
from companion_daemon.world_v2.character_interior.snapshot_compiler import compile_inner_life_snapshot
from test_inner_life_model_context import _context


def test_provider_view_keeps_all_continuity_and_canonical_proof_unchanged():
    raw = json.loads(_context())
    item = raw["slices"]["affect_episodes"]["items"][0]
    item["value"] = {"episode_id": "affect:warmth", "status": "active", "components": [{
        "component_id": "component:warmth", "dimension": "warmth", "intensity_bp": 1700,
        "source_cluster_ref": "cluster:context", "residue_bp": 500,
        "decay_profile": {"kind": "exponential_half_life", "half_life_seconds": 7200},
        "appraisal_refs": [{"appraisal_id": "appraisal:a", "hypothesis_id": "meaning:a:0",
            "source_cluster_ref": "cluster:context", "accepted_entity_revision": 2,
            "accepted_change_id": "change:a", "accepted_transition_id": "transition:a"}],
    }]}
    typed = compile_inner_life_snapshot(raw)
    before = typed.model_dump_json()
    view = typed.model_view()
    original_component = typed.materials["affect"][0]["components"][0]
    viewed = view["materials"]["affect"][0]["components"][0]
    assert {k: v for k, v in viewed.items() if k != "appraisal_refs"} == {
        k: v for k, v in original_component.items() if k != "appraisal_refs"}
    assert viewed["appraisal_refs"] == [{"appraisal_id": "appraisal:a", "hypothesis_id": "meaning:a:0",
        "source_cluster_ref": "cluster:context", "accepted_entity_revision": 2}]
    assert "accepted_change_id" in original_component["appraisal_refs"][0]
    assert typed.model_dump_json() == before
    assert (view["snapshot_id"], view["snapshot_hash"]) == (typed.snapshot_id, typed.snapshot_hash)
    hidden = typed.model_view(visible_source_refs=frozenset())
    assert "affect" not in hidden["materials"]
    assert "appraisal:a" not in json.dumps(hidden)


def test_unknown_and_partially_redacted_refs_are_not_reinterpreted_or_mutated():
    value = [{"components": [{"appraisal_refs": [
        "older-reference", {"appraisal_id": "only-partial", "accepted_change_id": "keep-this"},
        {"appraisal_id": "a", "hypothesis_id": "h", "source_cluster_ref": "s",
         "accepted_entity_revision": 1, "accepted_change_id": "proof", "extra_semantic_field": "keep"},
    ]}]}]
    original = deepcopy(value)
    result = compact_affect_for_model_view(value)
    assert result[0]["components"][0]["appraisal_refs"][:2] == original[0]["components"][0]["appraisal_refs"][:2]
    assert result[0]["components"][0]["appraisal_refs"][2]["extra_semantic_field"] == "keep"
    assert value == original
    assert compact_affect_for_model_view({"unknown": "shape"}) == {"unknown": "shape"}
