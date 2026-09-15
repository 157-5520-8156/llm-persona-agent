import json

from companion_daemon.world_v2.character_interior.contracts import _InteriorBinding
from companion_daemon.world_v2.character_interior.life_context_presentation import (
    DIARY_TEXT_CHARACTERS, LIFE_CONTEXT_COMPILER_VERSION, PENDING_WORLD_SCOPE,
)
from companion_daemon.world_v2.character_interior.snapshot_compiler import compile_inner_life_snapshot
from test_derived_material_redaction import _context, _experience, _recreate


def occurrence(*, settled=False, ref="occurrence:test"):
    value = {
        "context_kind": "settled_world_occurrence" if settled else "active_world_occurrence",
        "occurrence_id": ref, "occurrence_entity_revision": 4 if settled else 2,
        "participant_refs": ["agent:companion"], "location_ref": "location:campus",
        "privacy_class": "private",
    }
    if settled:
        value.update(settled_at="2026-08-16T14:00:00+08:00", content={
            "world_consequence": {
                "contract": "world-consequence.2",
                "environment": {"epistemic_scope": "settled_world_environment", "text": "路边开始下雨。"},
                "authorized_attempt_result": {
                    "epistemic_scope": "settled_authorized_attempt_result",
                    "execution_binding": {"source_event_ref": "event:started", "actor_ref": "agent:companion"},
                    "text": "这次散步已走到校园门口。",
                },
            },
            "character_response": {"response_text": "我有点意外。", "epistemic_scope": "accepted_character_response"},
        })
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
    assert reading["world_consequence"] == occurrence(settled=True)["value"]["content"]["world_consequence"]
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
