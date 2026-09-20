"""Fact-only display, exact host proof and unchanged independent speech rights."""

from copy import deepcopy
import json

import pytest

from companion_daemon.world_v2.shared_string_view import unpack_shared_strings
from companion_daemon.world_v2.source_time_comparison import _rehash
from companion_daemon.world_v2.visible_fact_value_display import CONTRACT, MARKER, VALUE_POINTER, with_fact_value_display
from companion_daemon.world_v2.visible_fact_value_readings import compile_fact_value_reading, require_fact_value_selection
from companion_daemon.world_v2.visible_independent_review_receipt import prepare_independent_visible_review, prepare_source_call
from companion_daemon.world_v2.visible_review_protocols import LIFECYCLE_FIELD_PROTOCOL, RECORD_DEPENDENCY_PROTOCOL
from companion_daemon.world_v2.visible_source_closure_protocol import _packet_materials
from companion_daemon.world_v2.visible_source_composer import VisibleSourceTable, compile_visible_source_table
from companion_daemon.world_v2.visible_source_witness_experiment import _json
from test_visible_contextual_fact_values import VALUE
from test_visible_selected_source_context import _sources
from test_visible_source_review_receipt import _candidate


def _call(case, table, protocol):
    prepared = prepare_independent_visible_review(candidate=_candidate(case, texts=("你保留了周四的约定。",)),
        source_table=table, source_ref_aliases={}, review_protocol=protocol,
        source_response_mode="json_object", scope_permission_context=True)
    raw = _json({"contract": "visible-candidate-meaning.16", "decisions": [{
        "beat_index": 0, "reading_complete": True, "unresolved_details": [],
        "meanings": [{"proposition": "用户保留了周四的约定", "subject_role": "counterpart", "mode": "actual_event_or_state"}],
        "presuppositions": [], "questions": [], "hypothetical_conditions": [],
    }]})
    return prepare_source_call(prepared=prepared, meaning_raw_responses=(raw, raw))


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", [RECORD_DEPENDENCY_PROTOCOL, LIFECYCLE_FIELD_PROTOCOL])
async def test_final_source_request_displays_exact_fact_but_preserves_separate_utterance(tmp_path, protocol):
    async with _sources(tmp_path, retained_value=VALUE) as case:
        capsule_before = case.capsule.model_dump_json()
        old = compile_visible_source_table(request=case.request, capsule=case.capsule, include_fact_value_display=False)
        new = compile_visible_source_table(request=case.request, capsule=case.capsule)
        assert new.as_dict() == with_fact_value_display(old.as_dict())
        assert new.as_dict()["pin"] == old.as_dict()["pin"]
        assert case.capsule.model_dump_json() == capsule_before
        old_call, new_call = (_call(case, table, protocol) for table in (old, new))
        old_body, new_body = (json.loads(call.request["messages"][1]["content"]) for call in (old_call, new_call))
        assert old_body["output_schema"] == new_body["output_schema"]
        assert old_body["fixed_facts"] == new_body["fixed_facts"]
        old_cards, new_cards = (unpack_shared_strings(body["source_materials"]) for body in (old_body, new_body))
        fact_ids = []
        for old_card, new_card in zip(old_cards, new_cards, strict=True):
            if old_card["material"].get("authority") != "accepted_fact_with_observation_source":
                assert old_card == new_card
                continue
            assert case.observation.text in _json(old_card)
            assert case.observation.text not in _json(new_card)
            assert "source_excerpt" not in _json(new_card)
            display = new_card["material"]
            assert display[MARKER] == CONTRACT and display["selection_field"] == "fact_value_selections"
            assert display["accepted_fact"]["accepted_value"] == VALUE
            for reading in new_card["readings"]:
                assert reading["field"] == VALUE_POINTER and reading["allowed_claims"] == []
                assert reading["accepted_value"] == VALUE
                assert {k: v for k, v in display["accepted_fact"].items() if k != "accepted_value"} == reading["fact_context"]
                fact_ids.append(reading["reading_id"])
        assert fact_ids
        # The original message remains visible only in separately qualified
        # dialogue/report cards, with their original readings and permissions.
        utterances = [card for card in new_cards if card["material"].get("lane") == "recent_dialogue"
                      or card["material"].get("kind") == "current_counterpart_report"]
        assert utterances and any(case.observation.text in _json(card) for card in utterances)
        for fact in new_body["fixed_facts"]:
            assert set(fact_ids) <= set(fact["eligible_fact_value_ids"])
            assert not set(fact_ids) & set(fact["eligible_reading_ids"])
        schema = new_body["output_schema"]["properties"]["fact_decisions"]["items"]["properties"]
        assert not set(fact_ids) & set(schema["reading_ids"]["items"].get("enum", []))
        assert set(fact_ids) <= set(schema["fact_value_selections"]["items"]["properties"]["reading_id"]["enum"])
        # Historical display recompiles from the same unmarked table bytes.
        assert _call(case, VisibleSourceTable(old.payload_json), protocol) == old_call


@pytest.mark.asyncio
async def test_full_observation_is_audit_only_and_still_required_for_value_verification(tmp_path):
    async with _sources(tmp_path, retained_value=VALUE) as case:
        table = compile_visible_source_table(request=case.request, capsule=case.capsule)
        fact_rows = tuple(row for row in table.source_references() if row["review_material"].get("lane") == "relevant_facts")
        _, cards = _packet_materials(fact_rows)
        assert case.observation.text not in _json(cards) and VALUE in _json(cards)
        row = next(row for row in fact_rows if row["source_ref"] == row["review_material"]["item"]["item_ref"])
        reading = compile_fact_value_reading(row)
        assert reading["value"] == case.observation.text
        assert reading["source_row"]["review_material"]["item"] == row["review_material"]["item"]
        options = dict(reading=reading, quoted_value=VALUE, claim_scope="accepted_fact", subject_ref=case.observation.actor)
        assert require_fact_value_selection(**options)["observation_context"] == case.observation.text
        with pytest.raises(ValueError, match="exact accepted Fact value"):
            require_fact_value_selection(**{**options, "quoted_value": case.observation.text})
        altered = deepcopy(reading)
        altered["pointer"] = "/item/value/source_excerpt"
        with pytest.raises(ValueError, match="original source compilation"):
            require_fact_value_selection(**{**options, "reading": altered})


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing_table", "unknown_table", "missing_material", "unknown_material", "foreign_material"])
async def test_marker_tampering_fails_at_source_consumer_even_if_hashes_are_recomputed(tmp_path, fault):
    async with _sources(tmp_path, retained_value=VALUE) as case:
        value = compile_visible_source_table(request=case.request, capsule=case.capsule).as_dict()
        material = next(item["material"] for item in value["source_materials"] if MARKER in item["material"])
        if fault == "missing_table":
            del value[MARKER]
        elif fault == "unknown_table":
            value[MARKER] = "unknown.2"
        elif fault == "missing_material":
            del material[MARKER]
        elif fault == "unknown_material":
            material[MARKER] = "unknown.2"
        else:
            other = next(item["material"] for item in value["source_materials"] if item["material"].get("kind") == "current_counterpart_report")
            other[MARKER] = CONTRACT
        _rehash(value)
        with pytest.raises(ValueError, match="Fact display"):
            _call(case, VisibleSourceTable(_json(value)), RECORD_DEPENDENCY_PROTOCOL)


@pytest.mark.asyncio
async def test_unqualified_fact_never_falls_back_to_showing_its_observation(tmp_path):
    async with _sources(tmp_path, retained_value=VALUE) as case:
        rows = tuple(row for row in compile_visible_source_table(request=case.request, capsule=case.capsule).source_references()
                     if row["review_material"].get("lane") == "relevant_facts")
        damaged = deepcopy(rows)
        for row in damaged:
            row["support_eligibility"] = "baseline_only"
        _, cards = _packet_materials(damaged)
        assert len(cards) == 1 and cards[0]["availability"] == "unavailable"
        assert "accepted_fact" not in cards[0] and case.observation.text not in _json(cards)
