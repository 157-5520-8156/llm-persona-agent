"""UTC presentation and real request/replay seams; no model quality claims."""

from copy import deepcopy
import json

import pytest

from companion_daemon.world_v2.shared_string_view import unpack_shared_strings
from companion_daemon.world_v2.source_time_comparison import (
    CONTRACT, _coordinate, _hash, _rehash, verify_time_comparison, with_time_comparison,
)
from companion_daemon.world_v2.visible_independent_review_receipt import (
    prepare_independent_visible_review, prepare_source_call,
)
from companion_daemon.world_v2.visible_review_protocols import (
    LIFECYCLE_FIELD_PROTOCOL, RECORD_DEPENDENCY_PROTOCOL,
)
from companion_daemon.world_v2.visible_source_composer import (
    VisibleSourceTable, compile_visible_source_table,
)
from companion_daemon.world_v2.visible_source_reading_experiment import _catalog, _direct_paths
from companion_daemon.world_v2.visible_source_witness_experiment import _json, _reading, prepare_witness_experiment
from test_visible_selected_source_context import _sources
from test_visible_source_review_receipt import _candidate


def _material(lane, timestamp, speaker="counterpart"):
    field = "logical_time" if lane == "current_situation" else "occurred_at"
    return {"kind": "pinned_context_item", "lane": lane,
            "item": {"value": {field: timestamp, "speaker": speaker,
                               "text": "The prose mentions 1999-01-01T00:00:00Z."}}}


@pytest.mark.parametrize("local,utc", [
    ("2026-09-14T22:58:00+08:00", "2026-09-14T14:58:00Z"),
    ("2026-09-15T00:10:00+08:00", "2026-09-14T16:10:00Z"),
    ("2026-11-01T01:30:00-07:00", "2026-11-01T08:30:00Z"),
    ("2026-11-01T01:30:00-08:00", "2026-11-01T09:30:00Z"),
])
def test_equal_instants_keep_original_offsets_and_distinct_clocks(local, utc):
    world = _material("current_situation", local)
    dialogue = _material("recent_dialogue", utc)
    before = deepcopy((world, dialogue))
    assert _coordinate(world) == {"field": "/item/value/logical_time", "clock": "world_logical", "utc": utc}
    assert _coordinate(dialogue) == {"field": "/item/value/occurred_at", "clock": "communication_received", "utc": utc}
    assert (world, dialogue) == before
    assert _coordinate(_material("recent_dialogue", utc, "companion"))["clock"] == "communication_receipt_recorded"


def test_actual_eight_hour_difference_is_not_erased():
    a = _coordinate(_material("current_situation", "2026-09-14T22:58:00+08:00"))
    b = _coordinate(_material("recent_dialogue", "2026-09-14T22:58:00Z"))
    assert a["utc"] == "2026-09-14T14:58:00Z"
    assert b["utc"] == "2026-09-14T22:58:00Z"


@pytest.mark.parametrize("value", ["2026-09-14T22:58:00", "yesterday", 123])
def test_missing_offset_or_invalid_field_is_not_guessed(value):
    with pytest.raises(ValueError):
        _coordinate(_material("current_situation", value))


def test_unset_logical_clock_is_preserved_without_inventing_a_coordinate():
    material = _material("current_situation", None)
    payload = {"source_materials": [{"material": material}],
               "source_references": [{"material_index": 0}]}
    _rehash(payload)
    before = deepcopy(payload)
    annotated = with_time_comparison(payload)
    assert annotated["source_materials"] == payload["source_materials"]
    assert payload == before and _coordinate(material) is None
    verify_time_comparison(annotated)
    with pytest.raises(ValueError, match="timestamp string"):
        _coordinate(_material("recent_dialogue", None))


def _prepare(case, table, protocol):
    prepared = prepare_independent_visible_review(
        candidate=_candidate(case, texts=("我看到了你这条消息。",)), source_table=table,
        source_ref_aliases={}, review_protocol=protocol,
        source_response_mode="json_object", scope_permission_context=True,
    )
    # Only a transport fixture; the test never supplies a source verdict.
    raw = _json({"contract": "visible-candidate-meaning.16", "decisions": [{
        "beat_index": 0, "reading_complete": True, "unresolved_details": [],
        "meanings": [{"proposition": "对方发送了当前消息", "mode": "actual_event_or_state",
                      "subject_role": "counterpart"}],
        "presuppositions": [], "questions": [], "hypothetical_conditions": [],
    }]})
    return prepared, (raw, raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", [RECORD_DEPENDENCY_PROTOCOL, LIFECYCLE_FIELD_PROTOCOL])
async def test_annotation_reaches_final_model_request_without_new_readings(tmp_path, protocol):
    async with _sources(tmp_path) as case:
        capsule_before = case.capsule.model_dump_json()
        old = compile_visible_source_table(request=case.request, capsule=case.capsule, include_time_comparison=False)
        table = compile_visible_source_table(request=case.request, capsule=case.capsule)
        assert table.as_dict() == with_time_comparison(old.as_dict())
        assert case.capsule.model_dump_json() == capsule_before
        assert table.as_dict()["pin"] == old.as_dict()["pin"]
        for original, shown in zip(old.as_dict()["source_materials"], table.as_dict()["source_materials"], strict=True):
            material = deepcopy(shown["material"])
            annotation = material.pop("time_comparison", None)
            assert material == original["material"]
            if annotation:
                assert annotation["original_material_identity"] == original["material_identity"]
        catalogs = []
        calls = []
        for source_table in (old, table):
            witness = prepare_witness_experiment(beats=("transport",), sources=source_table.source_references(),
                                                 source_owner_semantics=True, prehistory_authority=True)
            catalogs.append(_catalog(json.loads(witness.payload_json), report_uptake=True,
                                     content_fields_only=True, prehistory_authority=True, fact_value_authority=True))
            prepared, raws = _prepare(case, source_table, protocol)
            call = prepare_source_call(prepared=prepared, meaning_raw_responses=raws)
            assert prepare_source_call(prepared=prepared, meaning_raw_responses=raws) == call
            calls.append(call)
        assert catalogs[0] == catalogs[1]
        assert old.payload_json != table.payload_json
        bodies = [json.loads(call.request["messages"][1]["content"]) for call in calls]
        assert bodies[0]["fixed_facts"] == bodies[1]["fixed_facts"]
        assert bodies[0]["output_schema"] == bodies[1]["output_schema"]
        cards = unpack_shared_strings(bodies[1]["source_materials"])
        annotated = [card for card in cards if "time_comparison" in card["material"]]
        assert {card["material"]["lane"] for card in annotated} == {"current_situation", "recent_dialogue"}
        for card in annotated:
            assert card["material"]["time_comparison"]["contract"] == CONTRACT
            assert all("time_comparison" not in reading["field"] for reading in card["readings"])
            with pytest.raises(ValueError, match="original evidence body"):
                _reading(card["material"], "/time_comparison/coordinates/0/utc")
            unknown = {"review_material": {**card["material"], "lane": "unknown"}}
            assert all("time_comparison" not in path for path in _direct_paths(unknown, unknown["review_material"]))


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["utc", "clock", "field", "original_hash", "missing_view", "marker", "missing_marker", "raw_time"])
async def test_actual_source_consumer_rejects_forged_annotation_even_with_new_hashes(tmp_path, fault):
    async with _sources(tmp_path) as case:
        table = compile_visible_source_table(request=case.request, capsule=case.capsule)
        value = table.as_dict()
        material = next(row["material"] for row in value["source_materials"] if "time_comparison" in row["material"])
        view = material["time_comparison"]
        if fault in {"utc", "clock", "field"}:
            view["coordinates"][0][fault] = "forged"
        elif fault == "original_hash":
            view["original_material_identity"] = "a" * 64
        elif fault == "missing_view":
            del material["time_comparison"]
        elif fault == "marker":
            value["time_comparison_contract"] = "unknown.1"
        elif fault == "missing_marker":
            del value["time_comparison_contract"]
        else:
            material["item"]["value"]["logical_time"] = "2026-09-14T00:00:00Z"
        _rehash(value)
        # Attempt the production preparation/source consumer, not only a helper.
        with pytest.raises(ValueError):
            prepared, raws = _prepare(case, VisibleSourceTable(_json(value)), RECORD_DEPENDENCY_PROTOCOL)
            prepare_source_call(prepared=prepared, meaning_raw_responses=raws)


def test_legacy_unmarked_bytes_unchanged_and_unknown_material_prose_not_parsed():
    material = _material("unknown", "not-a-time")
    payload = {"source_materials": [{"material": material, "material_identity": _hash(material)}],
               "source_references": [{"material_index": 0, "material_identity": _hash(material)}]}
    _rehash(payload)
    original = _json(payload)
    verify_time_comparison(payload)
    assert _json(payload) == original
    shown = with_time_comparison(payload)
    assert shown["source_materials"] == payload["source_materials"]
    verify_time_comparison(shown)
