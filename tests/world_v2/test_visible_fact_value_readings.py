"""Visible Fact reads select accepted bytes, not enclosing report or metadata."""
from copy import deepcopy
import hashlib
import json

import pytest

from companion_daemon.world_v2.context_capsule import HistoricalFactRecallItem
from companion_daemon.world_v2.visible_source_composer import compile_visible_source_table
from companion_daemon.world_v2.visible_fact_value_readings import (
    compile_fact_value_reading, require_fact_value_selection,
)
from test_visible_selected_source_context import _sources

VALUE = "保留周四的约定"


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fact_row(case):
    rows = compile_visible_source_table(request=case.request, capsule=case.capsule).source_references()
    return next(row for row in rows if row["review_material"].get("lane") == "relevant_facts"
                and row["source_ref"] == row["review_material"]["item"]["item_ref"])


def _select(reading, **overrides):
    options = {"reading": reading, "quoted_value": VALUE, "claim_scope": "accepted_fact",
               "subject_ref": reading["source_owner_ref"], "subject_role": reading["source_owner_role"]}
    return require_fact_value_selection(**{**options, **overrides})


def _rehash_value(row):
    item = row["review_material"]["item"]
    item["value_hash"] = hashlib.sha256(_canonical(item["value"]).encode()).hexdigest()


@pytest.mark.asyncio
async def test_real_accepted_fact_keeps_full_context_but_requires_exact_selected_value(tmp_path):
    async with _sources(tmp_path, retained_value=VALUE) as case:
        row = _fact_row(case)
        original = deepcopy(row)
        reading = compile_fact_value_reading(row)
        assert row == original
        assert reading["source_row"] == row
        assert reading["value"] == case.observation.text != VALUE
        assert reading["source_ref_index"] == row["source_ref_index"]
        assert reading["permissions"] == []
        assert reading["value_selection_permissions"] == [["accepted_fact", "counterpart"]]
        assert reading["fact_context"]["predicate_code"] == "schedule.commitment"
        assert reading["fact_context"]["status"] == "active"
        assert _select({**reading, "reading_id": "fact-reading:1"}) == _select(reading)
        selected = _select(reading)
        assert selected["quoted_value"] == VALUE
        assert selected["observation_context"] == case.observation.text
        assert selected["fact_context"] == reading["fact_context"]
        assert selected["write_authority"] is False
        assert selected["semantic_coverage"] == "not_assessed"
        for quote in (case.observation.text, "用户决定取消周五的报告", VALUE + "。", "保留周五的约定", "",
                      reading["value_binding"]["value_hash"], reading["fact_context"]["fact_id"]):
            with pytest.raises(ValueError, match="exact accepted Fact value"):
                _select(reading, quoted_value=quote)
        # Reading data is detached from the caller's original source row.
        reading["source_row"]["review_material"]["item"]["value"]["source_excerpt"] = "changed"
        assert row == original


@pytest.mark.asyncio
async def test_fact_event_and_fact_id_aliases_work_but_observation_alias_does_not(tmp_path):
    async with _sources(tmp_path, retained_value=VALUE) as case:
        rows = compile_visible_source_table(request=case.request, capsule=case.capsule).source_references()
        fact_rows = [row for row in rows if row["review_material"].get("lane") == "relevant_facts"]
        accepted = []
        for row in fact_rows:
            value = row["review_material"]["item"]["value"]
            if row["source_ref"] in {value["fact_id"], value["accepted_fact_event_ref"]}:
                reading = compile_fact_value_reading(row)
                accepted.append(_select(reading)["source_ref"])
            else:
                with pytest.raises(ValueError, match="source/subject binding"):
                    compile_fact_value_reading(row)
        assert len(accepted) == 2
        assert all(compile_fact_value_reading(row) is None for row in rows
                   if row["review_material"].get("lane") != "relevant_facts")


@pytest.mark.asyncio
async def test_selection_refuses_changed_descriptor_value_and_wrong_subject_or_scope(tmp_path):
    async with _sources(tmp_path, retained_value=VALUE) as case:
        reading = compile_fact_value_reading(_fact_row(case))
        for change in (
            {"value": VALUE}, {"value_binding": {**reading["value_binding"], "value_hash": "0" * 64}},
            {"fact_context": {**reading["fact_context"], "predicate_code": "changed"}},
            {"permissions": [["external_fact", "counterpart"]]},
            {"source_row_sha256": "0" * 64}, {"source_ref_index": reading["source_ref_index"] + 1},
        ):
            with pytest.raises(ValueError, match="original source compilation"):
                _select({**reading, **change})
        for change in (
            {"subject_ref": case.capsule.actor_ref}, {"subject_role": "companion"},
            {"claim_scope": "external_fact"}, {"claim_scope": "historical_accepted_fact"},
        ):
            with pytest.raises(ValueError, match="subject/status permission"):
                _select(reading, **change)


@pytest.mark.asyncio
async def test_known_invalid_fact_rows_fail_closed_without_scalar_fallback(tmp_path):
    async with _sources(tmp_path, retained_value=VALUE) as case:
        original = _fact_row(case)
        mutations = [
            lambda row: row.update(support_eligibility="baseline_only"),
            lambda row: row.update(support_subject_ref="another:subject"),
            lambda row: row.update(support_subject_role="other"),
            lambda row: row.update(source_ref_index=True),
            lambda row: row["review_material"].update(privacy_class="withhold"),
            lambda row: row["review_material"].update(availability="unavailable"),
            lambda row: row["review_material"].update(lane="recent_dialogue"),
            lambda row: row["review_material"]["item"].update(item_ref="another:fact"),
            lambda row: row["review_material"]["item"]["value"].pop("accepted_value_binding"),
            lambda row: row["review_material"]["item"]["value"].update(privacy_class="withhold"),
            lambda row: row["review_material"]["item"]["value"].update(status="unknown"),
        ]
        for mutate in mutations:
            row = deepcopy(original)
            mutate(row)
            _rehash_value(row)
            with pytest.raises(ValueError):
                compile_fact_value_reading(row)
        # Even a newly self-consistent item hash cannot detach typed coordinates
        # from the exact Fact and Observation bindings in the pinned source card.
        for field in ("accepted_fact_payload_hash", "observation_event_payload_hash"):
            row = deepcopy(original)
            row["review_material"]["item"]["value"][field] = "0" * 64
            _rehash_value(row)
            with pytest.raises(ValueError, match="bound source events"):
                compile_fact_value_reading(row)


@pytest.mark.asyncio
async def test_historical_fact_scope_preserves_validity_and_denies_current_use(tmp_path):
    async with _sources(tmp_path, retained_value=VALUE) as case:
        # Typed adapter fixture only; this does not withdraw the current World
        # Fact or claim that its historical RecallDocument producer is installed.
        row = deepcopy(_fact_row(case))
        value = row["review_material"]["item"]["value"]
        historical = HistoricalFactRecallItem.model_validate_json(_canonical({**value,
            "status": "historical", "valid_from": value["updated_at"],
            "valid_to": case.capsule.logical_time.isoformat()}), strict=True)
        row["review_material"]["item"]["value"] = historical.model_dump(mode="json")
        _rehash_value(row)
        reading = compile_fact_value_reading(row)
        assert reading["permissions"] == []
        assert reading["value_selection_permissions"] == [["historical_accepted_fact", "counterpart"]]
        selected = _select(reading, claim_scope="historical_accepted_fact")
        assert selected["fact_context"]["valid_from"] == historical.model_dump(mode="json")["valid_from"]
        assert selected["fact_context"]["valid_to"] == historical.model_dump(mode="json")["valid_to"]
        with pytest.raises(ValueError, match="subject/status permission"):
            _select(reading)
        del row["review_material"]["item"]["value"]["valid_to"]
        _rehash_value(row)
        with pytest.raises(ValueError):
            compile_fact_value_reading(row)
