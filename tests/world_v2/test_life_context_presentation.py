import json

from companion_daemon.world_v2.character_interior.contracts import _InteriorBinding
from companion_daemon.world_v2.character_interior.life_context_presentation import (
    DIARY_TEXT_CHARACTERS, LIFE_CONTEXT_COMPILER_VERSION, PENDING_WORLD_SCOPE,
    SETTLED_WORLD_SCOPE,
)
from companion_daemon.world_v2.character_interior.snapshot_compiler import compile_inner_life_snapshot
from companion_daemon.world_v2.model_facing_context import _semantic_value
from companion_daemon.world_v2.world_life_context import WorldLifeContextItem
from test_derived_material_redaction import _context, _experience, _recreate


def occurrence(*, settled=False, ref="occurrence:test"):
    value = {
        "context_kind": "settled_world_occurrence" if settled else "active_world_occurrence",
        "occurrence_id": ref, "occurrence_entity_revision": 4 if settled else 2,
        "participant_refs": ["agent:companion"], "location_ref": "location:campus",
        "privacy_class": "private",
    }
    if settled:
        value.pop("context_kind")
        value.update(settled_at="2026-08-16T14:00:00+08:00", content={
            "content_id": "content:" + ref, "content_kind": "outcome_candidate",
            "content_ref": "content:" + ref, "content_payload_hash": "a" * 64,
            "truncated": False, "privacy_class": "private", "source_entity_id": ref,
            "source_entity_revision": 4, "authority_event_ref": "event:settled:" + ref,
            "authority_world_revision": 4, "authority_payload_hash": "b" * 64,
            "descriptor_event_ref": "event:content:" + ref,
            "descriptor_world_revision": 4, "descriptor_payload_hash": "c" * 64,
            "world_consequence": {
                "contract": "world-consequence.2",
                "environment": {"epistemic_scope": "settled_world_environment", "text": "路边开始下雨。"},
                "authorized_attempt_result": {
                    "epistemic_scope": "settled_result_of_bound_attempt",
                    "execution_binding": {
                        "source_kind": "activity_execution", "source_event_type": "ActivityStarted",
                        "source_event_ref": "event:started", "actor_ref": "agent:companion",
                        "source_world_revision": 2, "source_payload_hash": "d" * 64,
                        "privacy_class": "private", "plan_id": "plan:test",
                        "activity_id": "activity:test", "plan_entity_revision": 2,
                    },
                    "text": "这次散步已走到校园门口。",
                },
            },
            "character_response": {"response_text": "我有点意外。", "source_event_ref": "event:response",
                "actor_ref": "agent:companion", "epistemic_scope": "private_interpretation_not_world_fact"},
        })
        value.update(result_id="result:" + ref, result_payload_ref="payload:" + ref,
            result_payload_hash="a" * 64, source={"authority_event_ref": "event:settled:" + ref,
                "authority_world_revision": 4, "authority_payload_hash": "b" * 64})
        value = WorldLifeContextItem.model_validate_json(json.dumps(value)).model_dump(mode="json")
    else:
        value.update(status="active", activated_at="2026-08-16T13:00:00+08:00",
                     premise={"text": "PENDING_PREMISE 校园路径可能出现天气变化。"},
                     time_window={"opens_at": "2026-08-16T13:00:00+08:00", "closes_at": "2026-08-16T14:00:00+08:00"})
    return {"item_ref": ref, "source_ref": ref, "privacy_class": "private", "value": value}


def compile_with(*items, legacy=True):
    slices = {"world_life": {"availability": "available", "items": list(items)}}
    if legacy:
        slices["recent_experiences"] = {"availability": "available", "items": [
            _experience("experience:legacy", "2026-08-16T12:00:00+08:00", "午饭后整理了书架。"),
        ]}
    return compile_inner_life_snapshot(_context(**slices))


def test_pending_occurrence_never_becomes_a_diary_or_lived_moment():
    snapshot = compile_with(occurrence())
    assert snapshot.snapshot_compiler.value == LIFE_CONTEXT_COMPILER_VERSION
    material = snapshot.model_view()["materials"]
    pending = material["pending_world_occurrences"]["items"][0]
    assert pending["epistemic_scope"] == PENDING_WORLD_SCOPE
    assert pending["source_ref"] == "occurrence:test"
    for key in ("week_diary", "lived_moment", "recent_self_experiences"):
        assert "PENDING_PREMISE" not in json.dumps(material[key], ensure_ascii=False)
    hidden = snapshot.model_view(visible_source_refs=frozenset({"experience:legacy"}))
    assert "PENDING_PREMISE" not in json.dumps(hidden, ensure_ascii=False)


def test_settlement_enters_diary_without_merging_world_result_and_feeling():
    snapshot = compile_with(occurrence(settled=True))
    material = snapshot.model_view()["materials"]
    assert "pending_world_occurrences" not in material
    day = material["week_diary"][0]
    reading = day["readings"][0]
    assert reading["source_ref"] == "occurrence:test"
    assert reading["settled_at"] == "2026-08-16T14:00:00+08:00"
    assert reading["world_consequence"] == _semantic_value(occurrence(settled=True)["value"]["content"]["world_consequence"])
    assert reading["character_response"]["response_text"] == "我有点意外。"
    assert day["lines"] == ["午饭后整理了书架。"]
    assert "下雨" not in material["lived_moment"]
    hidden = snapshot.model_view(visible_source_refs=frozenset({"experience:legacy"}))
    assert "下雨" not in json.dumps(hidden, ensure_ascii=False)
    assert "我有点意外" not in json.dumps(hidden, ensure_ascii=False)


def test_structured_only_diary_is_not_silently_discarded_and_text_is_bounded():
    source = occurrence(settled=True)
    source["value"]["content"]["world_consequence"]["environment"]["text"] = "雨" * 1000
    snapshot = compile_with(source, legacy=False)
    original = json.loads(snapshot.materials_json)
    material = snapshot.model_view()["materials"]
    environment = material["week_diary"][0]["readings"][0]["world_consequence"]["environment"]
    assert environment["text"] == "雨" * DIARY_TEXT_CHARACTERS
    assert environment["truncated"] is True
    assert environment["epistemic_scope"] == "settled_world_environment"
    assert json.loads(snapshot.materials_json) == original
    assert "lived_moment" not in material


def test_historical_snapshot_keeps_historical_diary_rendering():
    snapshot = compile_with(occurrence(settled=True))
    # Reconstruct a recorded old snapshot identity, not a newly compiled view.
    old = _recreate(snapshot.model_copy(update={
        "snapshot_compiler": _InteriorBinding.available("inner-life-snapshot-compiler.24"),
    }), dict(snapshot.materials))
    assert "readings" not in old.model_view()["materials"]["week_diary"][0]


def test_same_day_results_keep_their_individual_times_and_source_identities():
    earlier = occurrence(settled=True, ref="occurrence:earlier")
    earlier["value"]["settled_at"] = "2026-08-16T09:00:00+08:00"
    earlier["value"]["content"]["world_consequence"]["environment"]["text"] = "灯还没有修好。"
    later = occurrence(settled=True, ref="occurrence:later")
    later["value"]["content"]["world_consequence"]["environment"]["text"] = "灯修好了。"
    readings = compile_with(later, earlier, legacy=False).model_view()["materials"]["week_diary"][0]["readings"]
    assert [(r["source_ref"], r["settled_at"]) for r in readings] == [
        ("occurrence:later", "2026-08-16T14:00:00+08:00"),
        ("occurrence:earlier", "2026-08-16T09:00:00+08:00"),
    ]


def test_environment_only_settlement_keeps_its_world_type_in_both_presented_lanes():
    # Shape of trial07: a settled .2 environment with the companion among its
    # participants, but no execution-bound action or separate private response.
    source = occurrence(settled=True)
    content = source["value"]["content"]
    content["world_consequence"].pop("authorized_attempt_result")
    content.pop("character_response")
    snapshot = compile_with(source)
    material = snapshot.model_view()["materials"]
    world, experience = material["recent_self_experiences"]["items"]
    assert snapshot.snapshot_compiler.value == LIFE_CONTEXT_COMPILER_VERSION
    assert world["context_kind"] == "settled_world_occurrence"
    assert world["epistemic_scope"] == SETTLED_WORLD_SCOPE
    assert world["content"] == _semantic_value(content)
    assert world["participant_refs"] == ["agent:companion"]
    assert world["location_ref"] == "location:campus"
    assert world["settled_at"] == source["value"]["settled_at"]
    assert experience["context_kind"] == "committed_experience"
    reading = material["week_diary"][0]["readings"][0]
    assert reading["context_kind"] == world["context_kind"]
    assert reading["epistemic_scope"] == world["epistemic_scope"]
    assert reading["source_ref"] == world["source_ref"]
    assert reading["world_consequence"] == _semantic_value(content["world_consequence"])
    assert "下雨" not in material["lived_moment"]
    hidden = snapshot.model_view(visible_source_refs=frozenset({"experience:legacy"}))
    assert "settled_world_occurrence" not in json.dumps(hidden, ensure_ascii=False)
    assert "下雨" not in json.dumps(hidden, ensure_ascii=False)


def test_provenance_preserves_independently_bound_result_and_private_response():
    source = occurrence(settled=True)
    snapshot = compile_with(source, legacy=False)
    material = snapshot.model_view()["materials"]
    entry = material["recent_self_experiences"]["items"][0]
    assert entry["epistemic_scope"] == SETTLED_WORLD_SCOPE
    assert entry["content"] == _semantic_value(source["value"]["content"])
    reading = material["week_diary"][0]["readings"][0]
    assert reading["world_consequence"] == entry["content"]["world_consequence"]
    assert reading["character_response"] == entry["content"]["character_response"]


def test_recorded_25_diary_still_uses_its_scoped_renderer():
    snapshot = compile_with(occurrence(settled=True), legacy=False)
    materials = dict(snapshot.materials)
    # A saved .25 carries no newly compiled provenance. It must retain the
    # structured diary instead of falling back to the pre-.25 line renderer.
    for row in materials["recent_self_experiences"]["items"] + materials["week_diary"]:
        row.pop("context_kind", None)
        row.pop("epistemic_scope", None)
    old = _recreate(snapshot.model_copy(update={
        "snapshot_compiler": _InteriorBinding.available("inner-life-snapshot-compiler.25"),
    }), materials)
    view = old.model_view()["materials"]
    assert view["week_diary"] == [{
        "date": "2026-08-16", "lines": [], "line_sources": [],
        "readings": [{
            **_semantic_value({key: occurrence(settled=True)["value"]["content"][key]
                               for key in ("world_consequence", "character_response")}),
            "source_ref": "occurrence:test", "settled_at": "2026-08-16T14:00:00+08:00",
        }],
    }]
    assert "lived_moment" not in view


def test_recalled_episode_keeps_recall_authority_and_plain_legacy_stays_unchanged():
    recalled = {
        "source_ref": "recalled:episode", "value": {
            "memory_kind": "episodic", "actor_ref": "agent:companion",
            "authority": "retained_experience", "epistemic_scope": "recalled_episode",
            "text": "记得书架旁的小灯。",
        },
    }
    snapshot = compile_inner_life_snapshot(_context(
        world_life={"availability": "available", "items": [occurrence(settled=True)]},
        recent_experiences={"availability": "available", "items": [recalled]},
    ))
    entry = snapshot.model_view()["materials"]["recent_self_experiences"]["items"][1]
    assert entry == {**recalled["value"], "source_ref": "recalled:episode"}
    legacy = compile_with(legacy=True)
    assert legacy.snapshot_compiler.value == "inner-life-snapshot-compiler.23"
    assert "context_kind" not in legacy.materials["recent_self_experiences"]["items"][0]
