"""Offline preservation checks for the reviewer evidence-card transport."""

from copy import deepcopy
import hashlib
import json

import pytest

from companion_daemon.world_v2.recent_dialogue import RecentDialogueItem
from companion_daemon.world_v2.situation_compiler import SituationProjection
from companion_daemon.world_v2.visible_source_closure_protocol import (
    _packet_materials,
    compact_source_reference_table,
)
from companion_daemon.world_v2.visible_source_evidence_cards import compile_visible_evidence_cards
from companion_daemon.world_v2.world_life_context import (
    ActiveActivityContextItem,
    CompletedActivityContextItem,
    PlannedActivityContextItem,
)


HASH = "a" * 64
NOW = "2026-09-09T10:00:00+08:00"


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _rows(packet):
    return sorted(
        (
            dict(zip(table["columns"], row, strict=True))
            for table in packet["source_reference_tables"] for row in table["rows"]
        ),
        key=lambda row: row["source_ref_index"],
    )


def _binding():
    return {
        "source_kind": "committed_event", "authority_type": "ActivityPlanned",
        "ref": "event:planned", "source_world_revision": 2, "immutable_hash": HASH,
    }


def _material(value, *, lane="world_life"):
    return {
        "kind": "pinned_context_item", "lane": lane,
        "availability": "available", "privacy_class": "private",
        "source_refs": ["event:planned", "alias:baseline"],
        "item": {
            "item_ref": "event:planned", "privacy_class": "private",
            "source_hash": HASH, "value_hash": HASH,
            "source_bindings": [_binding()], "value": value,
        },
    }


def _activity(status="planned"):
    value = {
        "context_kind": f"{status}_activity", "status": status,
        "activity_event_ref": f"event:{status}", "plan_id": "plan:1",
        "plan_entity_revision": {"planned": 1, "active": 2, "completed": 3}[status],
        "owner_actor_ref": "agent:companion", "activity_kind": "self_directed:opaque",
        "privacy_class": "private",
        "accepted_intention": {
            "content_ref": "content:intent", "content_payload_hash": HASH,
            "text": "晚些时候整理那些很乱的笔记，不确定能否做完。",
            "truncated": False,
            "epistemic_scope": "accepted_intention_only_not_embedded_history_or_outcome",
        },
        "proposal_source": {
            "authority_event_ref": "event:role-choice", "authority_ledger_sequence": 1,
            "authority_payload_hash": HASH,
        },
        "source_bindings": [{
            "authority_event_ref": "event:planned", "authority_world_revision": 2,
            "authority_payload_hash": HASH,
        }],
    }
    if status == "planned":
        value.update(
            scheduled_window={"opens_at": NOW, "closes_at": "2026-09-09T12:00:00+08:00"},
            planning_scope="accepted_plan_not_started_or_completed",
        )
    else:
        value["source_bindings"].append({
            "authority_event_ref": f"event:{status}", "authority_world_revision": 3,
            "authority_payload_hash": HASH,
        })
        if status == "active":
            value.update(participant_refs=[], location_ref=None, active_since=NOW)
        else:
            value.update(
                ended_at=NOW,
                completion_scope="activity_lifecycle_ended_not_intention_fulfilled",
            )
    return value


def _dialogue():
    return {
        "dialogue_id": "dialogue:1", "speaker": "counterpart", "speaker_ref": "user:1",
        "text": "原样保留这句：source_hash = not a hash。还没完成。",
        "occurred_at": NOW, "delivery_state": "observed", "sequence": 1,
        "privacy_class": "private", "sidecar_ref": "payload:1", "sidecar_hash": "sha256:" + HASH,
        "source_claims": [{
            "authority_event_ref": "event:observed", "authority_world_revision": 1,
            "authority_payload_hash": HASH,
        }],
    }


def test_grouping_preserves_per_reference_authority_missing_fields_null_and_source_mapping():
    support = {
        "contract": "visible-planned-activity-source.1", "status": "planned",
        "source_event_type": "ActivityPlanned", "scope": "accepted_plan_not_started_or_completed",
    }
    references = [
        {
            "source_ref_index": 0, "source_ref": "event:planned", "material_index": 0,
            "actor_ref": None, "subject_role": None, "support_subject_ref": "agent:companion",
            "support_subject_role": "companion", "support_eligibility": "eligible",
            "activity_support": support, "material_identity": HASH,
        },
        {
            "source_ref_index": 1, "source_ref": "alias:baseline", "material_index": 0,
            "actor_ref": None, "support_eligibility": "baseline_only",
        },
        {"source_ref_index": 2, "source_ref": "legacy", "material_index": 0},
        {"source_ref_index": 3, "source_ref": "legacy:null", "material_index": 0, "actor_ref": None},
        {"source_ref_index": 4, "source_ref": "legacy:empty", "material_index": 0, "actor_ref": ""},
    ]
    materials = [_material(_activity())]
    before = _json([references, materials])
    packet = compile_visible_evidence_cards(references=references, materials=materials)
    expected = deepcopy(references)
    del expected[0]["material_identity"]
    assert _rows(packet) == expected
    assert packet["source_material_contract"] == "visible-source-evidence-cards.1"
    assert len(packet["source_reference_tables"]) == 4
    notes = packet["source_materials"][0]["evidence_card_scope_notes"]
    assert notes["reference_scopes"] == [{"source_ref_index": 0, "activity_support": support}]
    assert "preexisting object condition" in notes["boundary"]
    assert _json([references, materials]) == before
    # Returned mutable objects are not aliases back into the pinned audit.
    notes["reference_scopes"][0]["activity_support"]["status"] = "changed"
    assert _json([references, materials]) == before


@pytest.mark.parametrize("status,model", [
    ("planned", PlannedActivityContextItem), ("active", ActiveActivityContextItem),
    ("completed", CompletedActivityContextItem),
])
def test_typed_lifecycle_retains_full_intention_time_status_and_no_success_promotion(status, model):
    value = _activity(status)
    model.model_validate_json(_json(value))
    material = _material(value)
    material["item"]["unverified_value_hash"] = HASH
    compact = compile_visible_evidence_cards(references=[], materials=[material])["source_materials"][0]
    expected = deepcopy(material)
    for field in ("source_hash", "value_hash", "unverified_value_hash"):
        expected["item"].pop(field)
    expected["item"]["source_bindings"][0].pop("immutable_hash")
    expected_value = expected["item"]["value"]
    expected_value["accepted_intention"].pop("content_payload_hash")
    expected_value["proposal_source"].pop("authority_payload_hash")
    for binding in expected_value["source_bindings"]:
        binding.pop("authority_payload_hash")
    notes = compact.pop("evidence_card_scope_notes")
    assert compact == expected
    assert notes["accepted_intention_scopes"] == [{
        "item_ref": "event:planned",
        "epistemic_scope": value["accepted_intention"]["epistemic_scope"],
    }]


@pytest.mark.parametrize("mutation", ["unknown_field", "invalid_status", "bad_context_kind", "truncated"])
def test_unknown_or_invalid_activity_body_is_never_trimmed_by_key_names(mutation):
    value = _activity()
    if mutation == "unknown_field":
        value["unknown"] = {"text": "full body", "content_payload_hash": HASH}
    elif mutation == "invalid_status":
        value["status"] = "happened"
    elif mutation == "bad_context_kind":
        value["context_kind"] = ["planned_activity"]
    else:
        value["accepted_intention"]["truncated"] = True
        value["accepted_intention"]["text"] = "截断后的可见原文"
    material = _material(value)
    compact = compile_visible_evidence_cards(references=[], materials=[material])["source_materials"][0]
    if mutation != "truncated":
        assert compact["item"]["value"] == value
        assert "evidence_card_scope_notes" not in compact
    else:
        assert compact["item"]["value"]["accepted_intention"]["truncated"] is True
        assert compact["item"]["value"]["accepted_intention"]["text"] == "截断后的可见原文"


@pytest.mark.parametrize("legacy_actor", [False, True])
def test_typed_dialogue_retains_full_body_actor_and_prior_utterance_authority(legacy_actor):
    value = _dialogue()
    if legacy_actor:
        value.pop("speaker_ref")
    RecentDialogueItem.model_validate_json(_json(value))
    material = _material(value, lane="recent_dialogue")
    material["authority"] = "prior_utterance_only_not_external_fact"
    compact = compile_visible_evidence_cards(references=[], materials=[material])["source_materials"][0]
    expected = deepcopy(value)
    expected.pop("sidecar_hash")
    expected["source_claims"][0].pop("authority_payload_hash")
    assert compact["item"]["value"] == expected
    assert compact["authority"] == material["authority"]


@pytest.mark.parametrize("mutation", ["extra", "bad_hash", "explicit_null"])
def test_dialogue_unknown_prose_fields_and_explicit_null_survive(mutation):
    value = _dialogue()
    if mutation == "extra":
        value["nested"] = {"sidecar_hash": HASH, "text": "不可删除的正文"}
    elif mutation == "bad_hash":
        value["source_claims"][0]["authority_payload_hash"] = "正文，不能按字段名删除"
    else:
        value["sidecar_ref"] = value["sidecar_hash"] = None
    compact = compile_visible_evidence_cards(
        references=[], materials=[_material(value, lane="recent_dialogue")],
    )["source_materials"][0]["item"]["value"]
    if mutation == "explicit_null":
        assert "sidecar_hash" in compact and compact["sidecar_hash"] is None
    else:
        assert compact == value


def test_situation_only_three_known_hashes_are_omitted_after_schema_validation():
    value = {
        "world_id": "world:1", "authority_snapshot_hash": HASH,
        "situation_policy_input_hash": HASH, "internal_semantic_hash": HASH,
        "compiled_at_world_revision": 2, "actor_ref": "agent:companion", "logical_time": NOW,
        "time_segment": None, "location_slice": {"availability": "unavailable"},
        "activity_slices": [{"status": "paused", "plan_id": "plan:1"}],
        "goal_slices": [], "resource_slices": [],
        "resource_pressure": {"availability": "unavailable"},
        "attention_slice": {"availability": "unavailable"},
        "social_environment": {"availability": "unavailable"},
        "plan_relation": {"availability": "unavailable"}, "commitment_slices": [],
        "scene_visibility": None, "source_revisions": [], "policy_versions": ["policy:1"],
    }
    SituationProjection.model_validate_json(_json(value))
    compact = compile_visible_evidence_cards(
        references=[], materials=[_material(value, lane="current_situation")],
    )["source_materials"][0]["item"]["value"]
    expected = {key: content for key, content in value.items() if key not in {
        "authority_snapshot_hash", "situation_policy_input_hash", "internal_semantic_hash",
    }}
    assert compact == expected
    value["unknown_fact"] = "future source_hash prose"
    compact = compile_visible_evidence_cards(
        references=[], materials=[_material(value, lane="current_situation")],
    )["source_materials"][0]["item"]["value"]
    assert compact == value


@pytest.mark.parametrize("state", ["withhold", "unavailable"])
def test_preprojected_withheld_or_unavailable_body_never_reappears(state):
    raw = _material({"text": "SENSITIVE RAW BODY MUST NOT REAPPEAR"})
    if state == "withhold":
        raw["item"]["privacy_class"] = "withhold"
    else:
        raw["item"]["availability"] = "unavailable"
    rows = compact_source_reference_table({"entries": [raw]})
    before = _json(rows)
    refs, materials = _packet_materials(rows)
    assert "value" not in materials[0]["item"]
    packet = compile_visible_evidence_cards(references=refs, materials=materials)
    assert "SENSITIVE RAW BODY" not in _json(packet)
    assert "value" not in packet["source_materials"][0]["item"]
    assert all(row["support_eligibility"] == "baseline_only" for row in _rows(packet))
    assert hashlib.sha256(_json(rows).encode()).digest() == hashlib.sha256(before.encode()).digest()


def test_unknown_material_and_null_or_prose_hash_fields_are_preserved_in_order():
    materials = [
        {"kind": "future_shape", "item": {"source_hash": HASH, "value": {"text": "unknown"}}},
        {"kind": "pinned_context_slice", "slice": {"items": [_material(_activity())["item"]]}},
        {"kind": "pinned_context_item", "item": {
            "value_hash": None, "source_hash": "source_hash is part of this explanation",
            "unverified_value_hash": "", "value": {"text": "all text", "value_hash": HASH},
            "source_bindings": [{"immutable_hash": HASH, "unknown": "new authority meaning"}],
        }},
    ]
    references = [{"source_ref_index": i, "material_index": i, "material_identity": identity}
                  for i, identity in enumerate([None, "unknown identity prose", ""])]
    packet = compile_visible_evidence_cards(references=references, materials=materials)
    assert packet["source_materials"] == materials
    assert _rows(packet) == references


def test_counterpart_report_retains_actor_full_prose_time_and_report_only_scope():
    material = {
        "kind": "current_counterpart_report", "epistemic_status": "counterpart_report_only",
        "does_not_authorize": ["independent_world_fact", "companion_experience"],
        "message": {"event_payload_hash": HASH, "event_ref": "event:report", "actor": "user:1",
                    "text": "还没做完。我只是计划明天整理。", "observed_at": NOW},
        "messages": [{"text": "full preceding report", "event_payload_hash": HASH}],
        "unknown": {"event_payload_hash": HASH, "text": "future prose"},
    }
    expected = deepcopy(material)
    del expected["message"]["event_payload_hash"]
    assert compile_visible_evidence_cards(references=[], materials=[material])["source_materials"] == [expected]


def test_compiler_never_overwrites_an_unknown_card_field():
    material = _material(_activity())
    material["evidence_card_scope_notes"] = {"unknown": "preexisting producer material"}
    compiled = compile_visible_evidence_cards(references=[], materials=[material])
    assert compiled["source_materials"][0]["evidence_card_scope_notes"] == material["evidence_card_scope_notes"]


def test_complete_maximum_dialogue_text_is_retained_without_excerpts():
    value = _dialogue()
    value["text"] = "前文" + "原文🙂" * 1_364 + "结尾"
    assert len(value["text"]) == 4_096
    RecentDialogueItem.model_validate_json(_json(value))
    packet = compile_visible_evidence_cards(
        references=[], materials=[_material(value, lane="recent_dialogue")],
    )
    assert packet["source_materials"][0]["item"]["value"]["text"] == value["text"]


def test_omitted_legacy_scope_is_not_filled_from_schema_default():
    value = _activity()
    value["accepted_intention"].pop("epistemic_scope")
    PlannedActivityContextItem.model_validate_json(_json(value))
    packet = compile_visible_evidence_cards(references=[], materials=[_material(value)])
    material = packet["source_materials"][0]
    assert "epistemic_scope" not in material["item"]["value"]["accepted_intention"]
    assert material["evidence_card_scope_notes"]["accepted_intention_scopes"] == [{
        "item_ref": "event:planned",
    }]


def test_column_group_uses_field_set_independently_of_dict_insertion_order():
    references = [
        {"source_ref_index": 9, "source_ref": "source:9", "material_index": 1, "actor_ref": None},
        {"actor_ref": None, "material_index": 0, "source_ref": "source:2", "source_ref_index": 2},
    ]
    materials = [{"kind": "unknown", "body": "first"}, {"kind": "unknown", "body": "second"}]
    packet = compile_visible_evidence_cards(references=references, materials=materials)
    assert len(packet["source_reference_tables"]) == 1
    assert _rows(packet) == sorted(references, key=lambda row: row["source_ref_index"])
    assert packet["source_materials"] == materials
