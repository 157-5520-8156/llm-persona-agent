"""Selected producer material reaches the dormant public review protocol.

These are offline protocol fixtures, not semantic-model qualification.
"""

from copy import deepcopy
import hashlib
import json

import pytest

from companion_daemon.world_v2.biographical_claim_authority import biographical_coordinate_authorities
from companion_daemon.world_v2.character_interior.inbound_wire import (
    _known_capsule_source_refs,
    _source_closure_evidence,
)
from companion_daemon.world_v2.expression_draft import ExpressionDraft
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


def _evidence(context, refs):
    draft = ExpressionDraft(
        timing_choice="now",
        beats=({"modality": "text", "text": "这是离线待核对的句子。"},),
        stance="answer_from_world",
        brief_rationale="Offline protocol fixture.",
        world_claims=({
            "claim_text": "这是离线待核对的句子",
            "scope": "current_world",
            "source_refs": tuple(refs),
        },),
    )
    return _source_closure_evidence(
        request=_request().model_copy(update={"model_content_json": _json(context)}),
        draft=draft,
        visible_context_json=_json(context),
        identity_frame=None,
    )


def _packet(rows):
    return json.loads(visible_source_closure_messages(
        visible_beats=("这是离线待核对的句子。",), world_claims=(), source_references=rows,
    )[1]["content"])


def _closed(rows, *, index=0, with_table=True):
    return parse_visible_source_closure(
        _json({"contract": "visible-beat-source-verdict.1", "decisions": [{
            "beat_index": 0, "verdict": "closed", "semantic_role": "external_proposition",
            "subject_role": "companion", "source_ref_indexes": [index],
        }]}),
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
    tmp_path, monkeypatch, full_source,
):
    async with _source_case(tmp_path, monkeypatch) as case:
        # The ordinary chat projection and opt-in source-bound view are kept
        # separate. This test does not add the latter to any chat producer.
        context = (compile_life_review_context(case.capsule) if full_source
                   else json.loads(case.capsule.model_content_json))
        item = context["slices"]["relevant_facts"]["items"][0]
        original = deepcopy(item)
        evidence = _evidence(context, [item["item_ref"]])
        rows = compact_source_reference_table(evidence)
        packet = _packet(rows)
        assert len(rows) >= 3
        assert len(packet["source_materials"]) == 1
        assert {row["material_index"] for row in packet["source_references"]} == {0}
        material = packet["source_materials"][0]["item"]
        assert material["value"] == original["value"]
        assert material["privacy_class"] == "personal"
        assert item == original
        assert _known_capsule_source_refs(evidence) == frozenset(row["source_ref"] for row in rows)
        if full_source:
            assert material["source_bindings"] == original["source_bindings"]
            assert material["value_hash"] == hashlib.sha256(_json(original["value"]).encode()).hexdigest()
            assert {row["support_eligibility"] for row in rows} == {"eligible"}
            assert _closed(rows)
        else:
            assert {row["support_eligibility"] for row in rows} == {"baseline_only"}
            # The slim value must not be presented as matching the full hash.
            assert "value_hash" not in material
            assert material["unverified_value_hash"] == original["value_hash"]
            with pytest.raises(VisibleSourceClosureWireFailure, match="eligible"):
                _closed(rows)
        assert not case.provider.requests
        old_rows = [{key: row[key] for key in (
            "source_ref_index", "source_ref", "kind", "epistemic_status", "actor_ref",
            "subject_role", "evidence_text",
        )} for row in rows]
        old_wire = visible_source_closure_messages(
            visible_beats=("这是离线待核对的句子。",), world_claims=(), source_references=tuple(old_rows),
        )
        new_wire = visible_source_closure_messages(
            visible_beats=("这是离线待核对的句子。",), world_claims=(), source_references=rows,
        )
        print("fixture_wire", {"full_source": full_source, "refs": len(rows), "entries": 1,
              "old_bytes": len(_json(old_wire).encode()), "new_bytes": len(_json(new_wire).encode())})


@pytest.mark.parametrize("authority", ["reference_metadata_only", "non_authoritative_advisory_not_external_fact"])
def test_metadata_and_attention_rows_keep_refs_but_cannot_close_new_review(authority):
    evidence = {"entries": [{"kind": "trigger_evidence", "authority": authority,
                             "source_refs": ["event:metadata"],
                             "material": {"ref_id": "event:metadata", "payload_hash": "a" * 64}}]}
    rows = compact_source_reference_table(evidence)
    assert rows[0]["source_ref"] == "event:metadata"
    assert rows[0]["support_eligibility"] == "baseline_only"
    with pytest.raises(VisibleSourceClosureWireFailure, match="eligible"):
        _closed(rows)
    assert _closed(rows, with_table=False)  # Historical parser is unchanged.


def test_empty_sources_allow_negative_review_and_new_parser_rejects_missing_material():
    assert _packet(())["source_references"] == []
    rows = compact_source_reference_table({"entries": [{"kind": "biographical_coordinate",
        "source_refs": ["biography-coordinate:missing"], "material": {"field_path": "/age"}}]})
    with pytest.raises(VisibleSourceClosureWireFailure, match="eligible"):
        _closed(rows)

