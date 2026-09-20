"""Original public Capsule → review table, with no semantic/provider review.

The lifecycle journey uses the real adapter with MockTransport; Fact and
biography use public temporary SQLite producers. No accepted events are forged.
"""

from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path

import pytest

from companion_daemon.world_v2.biographical_claim_authority import (
    biographical_coordinate_authorities,
)
from companion_daemon.world_v2.biographical_lifecycle import BiographicalLifecycleCatalog
from companion_daemon.world_v2.biographical_timeline_authority import (
    BiographicalTimelineConfiguredPayload,
)
from companion_daemon.world_v2.context_resolver import query_from_projection
from companion_daemon.world_v2.context_capsule import ContextCapsuleBudgetPolicy, SliceBudget
from companion_daemon.world_v2.deliberation import ModelInput, ModelRoute
from companion_daemon.world_v2.ledger_context_resolver import context_capsule_compiler_from_ledger
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.visible_source_closure_protocol import (
    VisibleSourceClosureWireFailure,
    parse_visible_source_closure,
    visible_source_closure_messages,
)
from test_biographical_model_context import _event
from test_chat_life_intent_runtime import INTENT, _run_http_journey
from test_completed_activity_context import open_journey_ledger
from test_current_activity_context import current_context
from test_visible_selected_source_context import _sources


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _request(capsule):
    return ModelInput(
        call_id="fixture:source-composer",
        attempt_id="fixture:source-composer",
        route=ModelRoute(tier="flash", reason_code="offline_fixture", router_version="fixture.1"),
        capsule_id=capsule.capsule_id,
        trigger_ref=capsule.trigger_ref,
        evaluated_world_revision=capsule.world_revision,
        evaluated_deliberation_revision=capsule.deliberation_revision,
        evaluated_ledger_sequence=capsule.ledger_sequence,
        model_content_json=capsule.model_content_json,
    )


def _compile(request, capsule):
    # The RED reaches this interface after the public producer, even before
    # the new module exists. This helper never fabricates accepted material.
    from companion_daemon.world_v2.visible_source_composer import compile_visible_source_table

    return compile_visible_source_table(request=request, capsule=capsule)


def _close(rows, index, *, subject="companion", text="这段活动正在进行中。"):
    return parse_visible_source_closure(
        _json(
            {
                "contract": "visible-beat-source-verdict.1",
                "decisions": [
                    {
                        "beat_index": 0,
                        "verdict": "closed",
                        "semantic_role": "external_proposition",
                        "subject_role": subject,
                        "source_ref_indexes": [index],
                    }
                ],
            }
        ),
        visible_beats=(text,),
        source_references=rows,
        source_ref_kinds=tuple(row["kind"] for row in rows),
        source_ref_subject_roles=tuple(row["subject_role"] for row in rows),
    )


def _assert_bound_table(table, capsule):
    payload = table.as_dict()
    assert payload["contract"] == "visible-source-row-table.1"
    assert payload["pin"]["capsule_id"] == capsule.capsule_id
    assert payload["pin"]["compiler_result_hash"] == capsule.compiler_result_hash
    assert payload["table_hash"] == _hash(payload["source_references"])
    assert payload["materials_hash"] == _hash(payload["source_materials"])
    assert payload["unsupported_source_kinds"] == ["identity_source"]
    rows = deepcopy(payload["source_references"])
    materials = payload["source_materials"]
    assert len({(row["source_ref"], row["material_identity"]) for row in rows}) == len(rows)
    assert len({item["material_identity"] for item in materials}) == len(materials)
    for index, row in enumerate(rows):
        assert row["source_ref_index"] == index
        stored = materials[row["material_index"]]
        assert row["material_identity"] == stored["material_identity"] == _hash(stored["material"])
    original = table.payload_json
    payload["source_references"].clear()
    assert table.payload_json == original and table.as_dict()["source_references"] == rows
    with pytest.raises(FrozenInstanceError):
        table.payload_json = "{}"
    return table.as_dict()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["active", "completed"])
async def test_public_chat_activity_remains_readable_beside_same_ref_situation(
    tmp_path, monkeypatch, status
):
    result, events, _, output, chat_calls = await _run_http_journey(
        tmp_path,
        monkeypatch,
        intent={**INTENT, "duration_seconds": 600 if status == "active" else 180},
        duration_minutes=3 if status == "active" else 5,
        prefer_complete=True,
    )
    assert result["completed"] and chat_calls == 1
    event_type = "ActivityStarted" if status == "active" else "ActivityCompleted"
    event = next(event for event in events if event["event_type"] == event_type)
    ledger = open_journey_ledger(output)
    try:
        capsule, _, _ = current_context(ledger, None, actor_ref="agent:companion")
        table = _compile(_request(capsule), capsule)
        payload = _assert_bound_table(table, capsule)
        rows = table.source_references()
        same_ref = [row for row in rows if row["source_ref"] == event["event_id"]]
        typed = next(row for row in same_ref if "activity_support" in row)
        assert typed["support_eligibility"] == "eligible"
        assert typed["support_subject_ref"] == capsule.actor_ref
        assert typed["activity_support"]["status"] == status
        assert typed["review_material"]["item"]["value"] == json.loads(
            next(
                item.payload_json
                for item in capsule.world_life.items
                if item.item_ref == event["event_id"]
            )
        )
        assert _close(rows, typed["source_ref_index"])
        if status == "active":
            assert len(same_ref) == 2
            weak = next(
                row for row in same_ref if row["review_material"]["lane"] == "current_situation"
            )
            assert weak["support_eligibility"] == "baseline_only"
            with pytest.raises(VisibleSourceClosureWireFailure, match="eligible"):
                _close(rows, weak["source_ref_index"])
        swapped = tuple(
            {**row, "source_ref_index": index} for index, row in enumerate(reversed(rows))
        )
        typed_swapped = next(row for row in swapped if row.get("activity_support"))
        assert _close(swapped, typed_swapped["source_ref_index"])
        assert _hash(list(reversed(payload["source_references"]))) != payload["table_hash"]
        # Perturb only source enumeration order after the real typed producer.
        # The public composer must retain both materials; their identity sets
        # stay equal while an ordered receipt hash intentionally changes.
        import companion_daemon.world_v2.selected_source_composer as composer

        original_world_entries = composer._world_entries

        def reverse_entries(original_capsule, *, include_lifecycle_states=False):
            entries, selection, unsupported = original_world_entries(
                original_capsule, include_lifecycle_states=include_lifecycle_states
            )
            return list(reversed(entries)), selection, unsupported

        monkeypatch.setattr(composer, "_world_entries", reverse_entries)
        reversed_table = _compile(_request(capsule), capsule)
        reversed_rows = reversed_table.source_references()
        assert {(r["source_ref"], r["material_identity"]) for r in reversed_rows} == {
            (r["source_ref"], r["material_identity"]) for r in rows
        }
        assert _close(
            reversed_rows,
            next(r["source_ref_index"] for r in reversed_rows if r.get("activity_support")),
        )
        returned = table.source_references()
        returned[typed["source_ref_index"]]["review_material"].clear()
        assert table.source_references()[typed["source_ref_index"]]["review_material"]
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_empty_claims_keep_fact_dialogue_and_exact_current_report(tmp_path):
    async with _sources(tmp_path) as case:
        table = _compile(case.request, case.capsule)
        payload = _assert_bound_table(table, case.capsule)
        rows = table.source_references()
        assert {row["review_material"].get("lane") for row in rows} >= {
            "relevant_facts",
            "recent_dialogue",
        }
        report = next(row for row in rows if row["kind"] == "current_counterpart_report")
        assert report["source_ref"] == case.request.trigger_ref
        assert report["review_material"]["message"]["text"] == case.observation.text
        assert report["support_subject_ref"] == case.observation.actor
        packet = json.loads(
            visible_source_closure_messages(
                visible_beats=("你取消了周五的报告。",),
                world_claims=(),
                source_references=rows,
            )[1]["content"]
        )
        visible_report = next(
            material
            for material in packet["source_materials"]
            if material["kind"] == "current_counterpart_report"
        )
        assert visible_report["authority"] == "report_only_not_external_truth"
        assert visible_report["message"]["text"] == case.observation.text
        assert "reply_target" not in visible_report["message"]
        assert _close(
            rows, report["source_ref_index"], subject="counterpart", text="你取消了周五的报告。"
        )
        assert payload["pin"]["model_input_hash"] == _hash(case.request.model_dump(mode="json"))
        without_trigger = case.request.model_copy(update={"trigger_message": None})
        other = _compile(without_trigger, case.capsule)
        assert not any(
            row["kind"] == "current_counterpart_report" for row in other.source_references()
        )
        assert other.as_dict()["pin"]["model_input_hash"] != payload["pin"]["model_input_hash"]
        # Public provenance validation remains authoritative, including before
        # any table is emitted. Source material is never borrowed from head.
        for field, value in (
            ("capsule_id", "0" * 64),
            ("evaluated_world_revision", case.capsule.world_revision + 1),
            ("model_content_json", "{}"),
        ):
            with pytest.raises(ValueError, match="original Capsule"):
                _compile(case.request.model_copy(update={field: value}), case.capsule)
        with pytest.raises(ValueError):
            _compile(case.request, case.capsule.model_copy(update={"actor_ref": "actor:other"}))


def test_public_biography_coordinates_are_narrow_original_clock_readings(tmp_path):
    world = "world:source-composer-biography"
    ledger = SQLiteWorldLedger(path=tmp_path / "biography.sqlite", world_id=world)
    at = datetime(2026, 7, 28, 4, tzinfo=UTC)
    timeline = BiographicalTimelineConfiguredPayload.from_yaml(
        path=Path("configs/world_seed.yaml"), timezone_name="Asia/Shanghai"
    )
    assert timeline is not None
    try:
        ledger.commit(
            (
                _event(
                    world_id=world,
                    event_id="event:start",
                    event_type="WorldStarted",
                    logical_at=at - timedelta(days=1),
                    payload={},
                ),
                _event(
                    world_id=world,
                    event_id="event:timeline",
                    event_type="BiographicalTimelineConfigured",
                    logical_at=at - timedelta(days=1),
                    payload=timeline.model_dump(mode="json"),
                ),
                _event(
                    world_id=world,
                    event_id="event:clock",
                    event_type="ClockAdvanced",
                    logical_at=at,
                    payload={
                        "logical_time_from": (at - timedelta(days=1)).isoformat(),
                        "logical_time_to": at.isoformat(),
                    },
                ),
            ),
            expected_world_revision=0,
            expected_deliberation_revision=0,
        )
        compiler = context_capsule_compiler_from_ledger(
            ledger=ledger,
            biographical_catalog=BiographicalLifecycleCatalog.from_yaml(
                path=Path("configs/world_seed.yaml"), timezone_name="Asia/Shanghai"
            ),
            biographical_timezone_name="Asia/Shanghai",
            biographical_timeline=timeline,
        )
        capsule = compiler.compile(
            query_from_projection(
                ledger.project(), actor_ref="agent:companion", trigger_ref="event:clock"
            )
        )
        table = _compile(_request(capsule), capsule)
        _assert_bound_table(table, capsule)
        rows = table.source_references()
        coordinates = biographical_coordinate_authorities(json.loads(capsule.model_content_json))
        coordinate_rows = [row for row in rows if row["kind"] == "biographical_coordinate"]
        assert len(coordinate_rows) == len(coordinates) >= 6
        for row in coordinate_rows:
            source = next(
                source for source in coordinates if source.source_ref == row["source_ref"]
            )
            assert row["review_material"]["material"] == source.evidence_material()
            assert row["support_eligibility"] == "eligible"
        assert not any(
            row["review_material"].get("item", {}).get("value", {}).get("context_kind")
            == "biographical_context"
            for row in rows
        )
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_withheld_selected_fact_never_emits_a_table(tmp_path):
    async with _sources(tmp_path, privacy="withhold") as case:
        with pytest.raises(ValueError, match="withheld"):
            _compile(case.request, case.capsule)


@pytest.mark.asyncio
async def test_trigger_without_selected_dialogue_cannot_supply_current_report(tmp_path):
    policy = ContextCapsuleBudgetPolicy(
        recent_dialogue=SliceBudget(max_items=0, max_fields=128, max_characters=8000),
    )
    async with _sources(tmp_path, policy=policy) as case:
        assert case.request.trigger_message is not None
        assert case.capsule.recent_dialogue.items == ()
        table = _compile(case.request, case.capsule)
        assert not any(
            row["kind"] == "current_counterpart_report" for row in table.source_references()
        )
        assert "counterpart_actor_ref" not in table.as_dict()["subjects"]
