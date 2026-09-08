"""Selected producer material reaches the dormant public review protocol.

These are offline protocol fixtures, not semantic-model qualification.
"""

from copy import deepcopy
import hashlib
import json

import pytest

from companion_daemon.world_v2.biographical_claim_authority import (
    biographical_coordinate_authorities,
)
from companion_daemon.world_v2.character_interior.inbound_wire import (
    _known_capsule_source_refs,
    _source_closure_evidence,
)
from companion_daemon.world_v2.expression_draft import ExpressionDraft
from companion_daemon.world_v2.deliberation import TriggerMessage
from companion_daemon.world_v2.life_context import compile_life_review_context
from companion_daemon.world_v2.visible_source_closure_protocol import (
    VisibleSourceClosureWireFailure,
    compact_source_reference_table,
    parse_visible_source_closure,
    visible_source_closure_messages,
)
from test_biographical_authority_presentation import _context
from test_character_interior_inbound_wire import _request
from test_life_review_selected_source_proof import _source_case


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _evidence(context, refs, *, trigger=None):
    draft = ExpressionDraft(
        timing_choice="now",
        beats=({"modality": "text", "text": "这是离线待核对的句子。"},),
        stance="answer_from_world",
        brief_rationale="Offline protocol fixture.",
        world_claims=(
            {
                "claim_text": "这是离线待核对的句子",
                "scope": "current_world",
                "source_refs": tuple(refs),
            },
        ),
    )
    return _source_closure_evidence(
        request=_request().model_copy(
            update={"model_content_json": _json(context), "trigger_message": trigger}
        ),
        draft=draft,
        visible_context_json=_json(context),
        identity_frame=None,
    )


def _packet(rows):
    return json.loads(
        visible_source_closure_messages(
            visible_beats=("这是离线待核对的句子。",),
            world_claims=(),
            source_references=rows,
        )[1]["content"]
    )


def _closed(rows, *, index=0, with_table=True, subject="companion"):
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
        visible_beats=("这是离线待核对的句子。",),
        source_ref_kinds=tuple(row["kind"] for row in rows),
        source_ref_subject_roles=tuple(row["subject_role"] for row in rows),
        **({"source_references": rows} if with_table else {}),
    )


def test_actual_biography_producer_preserves_exact_coordinate_and_excludes_private():
    context = _context(snapshot=True)
    coordinates = biographical_coordinate_authorities(context)
    evidence = _evidence(context, [coordinate.source_ref for coordinate in coordinates])
    rows = compact_source_reference_table(evidence)
    packet = _packet(rows)

    assert packet["source_material_contract"] == "visible-source-materials.1"
    assert len(packet["source_materials"]) == len(coordinates)
    for row in packet["source_references"]:
        coordinate = next(c for c in coordinates if c.source_ref == row["source_ref"])
        material = packet["source_materials"][row["material_index"]]
        assert material["material"] == coordinate.evidence_material()
        assert row["support_eligibility"] == "eligible"
    assert "biography:private-direction" not in _json(packet)
    assert _known_capsule_source_refs(evidence) == frozenset(c.source_ref for c in coordinates)
    assert _closed(rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("full_source", [False, True])
async def test_public_sqlite_fact_multiref_keeps_one_exact_selected_payload(
    tmp_path,
    monkeypatch,
    full_source,
):
    async with _source_case(tmp_path, monkeypatch) as case:
        # The ordinary chat projection and opt-in source-bound view are kept
        # separate. This test does not add the latter to any chat producer.
        context = (
            compile_life_review_context(case.capsule)
            if full_source
            else json.loads(case.capsule.model_content_json)
        )
        item = context["slices"]["relevant_facts"]["items"][0]
        original = deepcopy(item)
        event, commit = case.ledger.lookup_event_commit("event:observation:member:1")
        trigger = TriggerMessage(
            event_ref=event.event_id,
            event_payload_hash="sha256:" + event.payload_hash,
            source_world_revision=commit.world_revision,
            observation_ref=case.observation.observation_id,
            actor=case.observation.actor,
            channel=case.observation.channel,
            reply_target="fixture:local",
            text=case.observation.text,
        )
        evidence = _evidence(context, [item["item_ref"]], trigger=trigger)
        rows = compact_source_reference_table(evidence)
        original_refs = tuple(
            dict.fromkeys(ref for entry in evidence["entries"] for ref in entry["source_refs"])
        )
        assert tuple(row["source_ref"] for row in rows) == original_refs
        packet = _packet(rows)
        assert len(rows) >= 3
        assert len(packet["source_materials"]) == 2
        fact_rows = [
            row for row in packet["source_references"] if row["kind"] == "pinned_context_item"
        ]
        assert len(fact_rows) >= 3
        assert {row["material_index"] for row in fact_rows} == {1}
        material = packet["source_materials"][1]["item"]
        assert {row["support_subject_role"] for row in fact_rows} == {"counterpart"}
        assert {row["subject_role"] for row in fact_rows} == {None}  # Frozen legacy projection.
        fact_index = fact_rows[0]["source_ref_index"]
        assert material["value"] == original["value"]
        assert material["privacy_class"] == "personal"
        assert item == original
        assert _known_capsule_source_refs(evidence) == frozenset(original_refs)
        if full_source:
            correction = visible_source_closure_messages(
                visible_beats=("这是离线待核对的句子。",),
                world_claims=(),
                source_references=rows,
                invalid_reason=VisibleSourceClosureWireFailure(
                    "subject_binding_invalid",
                    "fixture actor mismatch",
                    beat_index=0,
                    field="decisions.0.subject_role",
                ),
            )
            correction_rows = json.loads(correction[-1]["content"])["structural_constraints"][
                "source_subject_roles"
            ]
            assert correction_rows[fact_index] == {
                "source_ref_index": fact_index,
                "subject_role": "counterpart",
                "support_subject_ref": case.observation.actor,
                "support_eligibility": "eligible",
            }
        if full_source:
            assert material["source_bindings"] == original["source_bindings"]
            assert (
                material["value_hash"]
                == hashlib.sha256(_json(original["value"]).encode()).hexdigest()
            )
            assert {row["support_eligibility"] for row in fact_rows} == {"eligible"}
            assert _closed(rows, index=fact_index, subject="counterpart")
            with pytest.raises(VisibleSourceClosureWireFailure, match="actor"):
                _closed(rows, index=fact_index, subject="companion")
        else:
            assert {row["support_eligibility"] for row in fact_rows} == {"baseline_only"}
            # The slim value must not be presented as matching the full hash.
            assert "value_hash" not in material
            assert material["unverified_value_hash"] == original["value_hash"]
            with pytest.raises(VisibleSourceClosureWireFailure, match="eligible"):
                _closed(rows, index=fact_index, subject="counterpart")
        assert not case.provider.requests
        old_rows = [
            {
                key: row[key]
                for key in (
                    "source_ref_index",
                    "source_ref",
                    "kind",
                    "epistemic_status",
                    "actor_ref",
                    "subject_role",
                    "evidence_text",
                )
            }
            for row in rows
        ]
        old_wire = visible_source_closure_messages(
            visible_beats=("这是离线待核对的句子。",),
            world_claims=(),
            source_references=tuple(old_rows),
        )
        new_wire = visible_source_closure_messages(
            visible_beats=("这是离线待核对的句子。",),
            world_claims=(),
            source_references=rows,
        )
        duplicated = deepcopy(old_wire)
        duplicated_packet = json.loads(duplicated[1]["content"])
        for row in duplicated_packet["source_references"]:
            row["entry"] = next(
                entry for entry in evidence["entries"] if row["source_ref"] in entry["source_refs"]
            )
        duplicated[1]["content"] = json.dumps(
            duplicated_packet, ensure_ascii=False, separators=(",", ":")
        )
        print(
            "fixture_wire",
            {
                "full_source": full_source,
                "refs": len(rows),
                "entries": 2,
                "old_bytes": len(_json(old_wire).encode()),
                "new_bytes": len(_json(new_wire).encode()),
                "metadata_rows_bytes": len(_json(old_rows).encode()),
                "readable_material_bytes": len(_json(packet["source_materials"]).encode()),
                "full_entry_per_ref_bytes": len(_json(duplicated).encode()),
            },
        )


@pytest.mark.parametrize(
    "authority", ["reference_metadata_only", "non_authoritative_advisory_not_external_fact"]
)
def test_metadata_and_attention_rows_keep_refs_but_cannot_close_new_review(authority):
    evidence = {
        "entries": [
            {
                "kind": "trigger_evidence",
                "authority": authority,
                "source_refs": ["event:metadata"],
                "material": {"ref_id": "event:metadata", "payload_hash": "a" * 64},
            }
        ]
    }
    rows = compact_source_reference_table(evidence)
    assert rows[0]["source_ref"] == "event:metadata"
    assert rows[0]["support_eligibility"] == "baseline_only"
    with pytest.raises(VisibleSourceClosureWireFailure, match="eligible"):
        _closed(rows)
    assert _closed(rows, with_table=False)  # Historical parser is unchanged.


def test_empty_sources_allow_negative_review_and_new_parser_rejects_missing_material():
    assert _packet(())["source_references"] == []
    assert parse_visible_source_closure(
        _json(
            {
                "contract": "visible-beat-source-verdict.1",
                "decisions": [
                    {
                        "beat_index": 0,
                        "verdict": "unclosed",
                        "semantic_role": "external_proposition",
                        "subject_role": "companion",
                        "source_ref_indexes": [],
                    }
                ],
            }
        ),
        visible_beats=("我在图书馆。",),
        source_ref_kinds=(),
        source_references=(),
    )
    rows = compact_source_reference_table(
        {
            "entries": [
                {
                    "kind": "biographical_coordinate",
                    "source_refs": ["biography-coordinate:missing"],
                    "material": {"field_path": "/age"},
                }
            ]
        }
    )
    with pytest.raises(VisibleSourceClosureWireFailure, match="eligible"):
        _closed(rows)


@pytest.mark.parametrize("level", ["entry", "slice", "item", "value"])
def test_withheld_or_unavailable_selected_body_is_not_restored(level):
    item = {
        "item_ref": "fact:hidden",
        "privacy_class": "personal",
        "value": {
            "source_excerpt": "不应在新材料中出现的正文",
            "subject_ref": "user:test",
        },
    }
    entry = {
        "kind": "pinned_context_slice",
        "source_refs": ["fact:hidden"],
        "slice": {"availability": "available", "items": [item]},
    }
    if level == "entry":
        entry["privacy_class"] = "withhold"
    elif level == "slice":
        entry["slice"]["availability"] = "unavailable"
    elif level == "item":
        item["privacy_class"] = "withhold"
    else:
        item["value"]["privacy_class"] = "withhold"
    rows = compact_source_reference_table({"entries": [entry]})
    assert rows[0]["source_ref"] == "fact:hidden"
    assert "不应在新材料中出现的正文" not in _json(_packet(rows))
    with pytest.raises(VisibleSourceClosureWireFailure, match="eligible"):
        _closed(rows)


def test_new_parser_rechecks_body_and_exact_table_alignment():
    context = _context(snapshot=False)
    coordinate = biographical_coordinate_authorities(context)[0]
    rows = compact_source_reference_table(_evidence(context, [coordinate.source_ref]))
    missing_body = deepcopy(rows)
    missing_body[0]["review_material"]["material"].pop("value")
    with pytest.raises(VisibleSourceClosureWireFailure, match="eligible"):
        _closed(missing_body)
    changed_value = deepcopy(rows)
    changed_value[0]["review_material"]["material"]["value"] = "changed value, unchanged ref"
    with pytest.raises(VisibleSourceClosureWireFailure, match="eligible"):
        _closed(changed_value)
    wrong_index = deepcopy(rows)
    wrong_index[0]["source_ref_index"] = 4
    with pytest.raises(ValueError, match="align"):
        _closed(wrong_index)


def test_attention_refs_and_unavailable_biography_keep_the_existing_guard_boundary():
    evidence = {
        "required_source_refs": ["event:attention"],
        "entries": [
            {
                "kind": "pinned_context_item",
                "source_refs": ["event:attention"],
                "authority": "private_attention_exact_time_only_not_world_claim",
                "item": {"value": {"text": "可读提醒"}},
            }
        ],
    }
    assert _known_capsule_source_refs(evidence) == frozenset()
    rows = compact_source_reference_table(evidence)
    assert rows[0]["source_ref"] == "event:attention"
    assert rows[0]["support_eligibility"] == "baseline_only"
    context = _context(snapshot=True)
    coordinate = biographical_coordinate_authorities(context)[0]
    context["slices"]["world_life"]["availability"] = "unavailable"
    with pytest.raises(ValueError, match="could not resolve"):
        _evidence(context, [coordinate.source_ref])


def test_historical_packet_without_material_keeps_2351c4b6_bytes():
    rows = (
        {
            "source_ref_index": 0,
            "source_ref": "event:old",
            "kind": "settled_world_event",
            "epistemic_status": None,
            "actor_ref": "agent:companion",
            "subject_role": "companion",
            "evidence_text": "旧正文。",
        },
    )
    wire = visible_source_closure_messages(
        visible_beats=("旧正文。",), world_claims=(), source_references=rows
    )
    assert (
        hashlib.sha256(_json(wire).encode()).hexdigest()
        == "a528e4b38ccea33a59b03fbbcd05b4dd856db9ed1a2223b4f37f0b16a327f4a6"
    )
    correction = visible_source_closure_messages(
        visible_beats=("旧正文。",),
        world_claims=(),
        source_references=rows,
        invalid_reason=VisibleSourceClosureWireFailure(
            "subject_binding_invalid",
            "fixture actor mismatch",
            beat_index=0,
            field="decisions.0.subject_role",
        ),
    )
    assert (
        hashlib.sha256(_json(correction).encode()).hexdigest()
        == "8a37e7a259117328b2dc3cadec931620776ae80aa36d1ee4f729f2a8d29abbb3"
    )


@pytest.mark.parametrize(
    "boundary", [{"privacy_class": "withhold"}, {"availability": "unavailable"}]
)
def test_new_packet_cannot_leak_blocked_report_through_legacy_evidence_text(boundary):
    evidence = {
        "entries": [
            {
                "kind": "current_counterpart_report",
                **boundary,
                "source_refs": ["event:test"],
                "message": {
                    "text": "WITHHELD_SENTINEL",
                    "actor": "user:test",
                    "event_ref": "event:test",
                    "event_payload_hash": "sha256:test",
                },
            }
        ]
    }
    rows = compact_source_reference_table(evidence)
    assert rows[0]["evidence_text"] == "WITHHELD_SENTINEL"  # Historical table is unchanged.
    assert _known_capsule_source_refs(evidence) == frozenset({"event:test"})
    packet = _packet(rows)
    assert packet["source_references"][0]["source_ref_index"] == 0
    assert packet["source_references"][0]["source_ref"] == "event:test"
    assert packet["source_references"][0]["support_eligibility"] == "baseline_only"
    assert "WITHHELD_SENTINEL" not in _json(packet)
