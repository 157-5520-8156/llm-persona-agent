"""Mind renderer: background cognition reads content and citable ids, not proofs."""

from __future__ import annotations

import json
import re

import pytest

from companion_daemon.world_v2.present_prompt import (
    LEAN_PRESENTATION_MARKER,
    lean_aliases_from_citeable,
    lean_present_snapshot,
    lean_presentation_enabled,
    present_inner_life,
)

FACT_REF = "fact:3bbdb1b683b94e442b404d8ae9deede3242602c8e16c3ca4625bc34a766d236e"
UNCITED = "event:world-consequence-content:5605a554cc8024ec3cc40efc8853f732d2639535774411d67048708a2417e1c0"
HEX = re.compile(r"[0-9a-f]{16,}")


def _snapshot(version: str) -> dict[str, object]:
    return {
        "snapshot_compiler": {"availability": "available", "value": version},
        "snapshot_hash": "a" * 64,
        "snapshot_id": "projection:" + "b" * 64,
        "source_refs": [FACT_REF, "actor:companion"],
        "source_inventory": [
            {"source_ref": FACT_REF, "scope": "relevant_facts", "privacy_class": "private",
             "content_hash": "c" * 64},
            {"source_ref": UNCITED, "scope": "week_diary", "privacy_class": "private"},
        ],
        "materials": {
            "relevant_facts": [{
                "accepted_value": "成都理工读软件工程，开学大四",
                "fact_id": FACT_REF,
                "source_ref": FACT_REF,
                "fact_context": {"fact_id": FACT_REF, "confidence_bp": 9500},
                "display_contract": "life-fact-value-display.1",
                "occurred_at": "2026-08-16T17:14:01Z",
                "updated_at": "2026-08-16T17:14:13Z",
                "authority_payload_hash": "d" * 64,
                "authority_world_revision": 16976,
                "descriptor_event_ref": UNCITED,
                "subject_ref": "user:geoff",
                "privacy_class": "private",
                "truncated": False,
                "note": None,
                "acknowledges": [],
            }],
        },
    }


ALIASES = {FACT_REF: "s3", "actor:companion": "s1"}


def test_lean_view_keeps_content_time_and_a_citable_id_only() -> None:
    lean = lean_present_snapshot(_snapshot("inner-life-snapshot-compiler.30"), ref_aliases=ALIASES)
    fact = lean["materials"]["relevant_facts"][0]
    assert fact == {
        "accepted_value": "成都理工读软件工程，开学大四",
        "source_ref": "s3",
        "occurred_at": "2026-08-16T17:14:01Z",
        "subject_ref": "user:geoff",
        "privacy_class": "private",
    }
    assert lean["source_refs"] == ["s3", "s1"]
    assert lean["source_inventory"] == [
        {"source_ref": "s3", "scope": "relevant_facts", "privacy_class": "private"}
    ]
    assert "snapshot_hash" not in lean and "snapshot_id" not in lean
    assert not HEX.search(json.dumps(lean, ensure_ascii=False))


def test_only_new_snapshots_with_a_catalog_are_rendered_lean() -> None:
    old = _snapshot("inner-life-snapshot-compiler.29")
    new = _snapshot("inner-life-snapshot-compiler.30")
    assert not lean_presentation_enabled(old)
    assert lean_presentation_enabled(new)
    # A persisted older snapshot keeps its exact presentation.
    assert present_inner_life(old, lean_ref_aliases=ALIASES) == present_inner_life(old)
    # No catalog, no lean view (e.g. private impression, inbound chat).
    assert present_inner_life(new) == present_inner_life(new, lean_ref_aliases=None)
    assert present_inner_life(new, lean_ref_aliases=ALIASES) != present_inner_life(new)


def test_aliases_come_from_the_requests_own_citation_catalog() -> None:
    view = {"items": [{"id": "s0", "ref": "event:a"}, {"id": "s1", "ref": "event:b"}], "instruction": "x"}
    assert lean_aliases_from_citeable(view) == {"event:a": "s0", "event:b": "s1"}
    assert lean_aliases_from_citeable(None) is None
    assert lean_aliases_from_citeable({"items": []}) is None


def test_old_prefetch_presentation_does_not_gain_new_framing() -> None:
    snapshot = _snapshot("inner-life-snapshot-compiler.29")
    snapshot["materials"]["automatic_prefetch"] = {"items": [{"text": "过去的记忆"}]}
    assert present_inner_life(snapshot)["materials"]["automatic_prefetch"] == {
        "items": [{"text": "过去的记忆"}],
    }


@pytest.mark.parametrize("split_cache", [False, True])
def test_lean_tables_keep_column_alignment_and_field_removal(split_cache):
    from test_affect_reference_presentation import _refs
    from companion_daemon.world_v2.affect_reference_view import pack_appraisal_references

    table = pack_appraisal_references(_refs())
    columns = table["columns"][:]
    rows = table["rows"][:]
    if split_cache:
        table = {"columns": columns, "stable_rows": rows[:-1], "volatile_last_row": rows[-1]}
    snapshot = _snapshot("inner-life-snapshot-compiler.31")
    snapshot["materials"]["table"] = table
    lean = lean_present_snapshot(snapshot, ref_aliases={_refs()[0]["appraisal_id"]: "s9"})["materials"]["table"]
    actual_rows = (lean["stable_rows"] + [lean["volatile_last_row"]]) if split_cache else lean["rows"]
    assert len(actual_rows) == len(rows)
    for original, row in zip(rows, actual_rows, strict=True):
        assert len(row) == len(lean["columns"])
        record = dict(zip(lean["columns"], row, strict=True))
        assert record["source_cluster_ref"] == "source-cluster:shared-stimulus"
        assert "accepted_entity_revision" not in record
        assert record["appraisal_id"] == ("s9" if original[0] == _refs()[0]["appraisal_id"] else None)
    # Kept .30 pins retain the old bytes, even though their table is flawed.
    snapshot["snapshot_compiler"]["value"] = "inner-life-snapshot-compiler.30"
    legacy = lean_present_snapshot(snapshot, ref_aliases={})["materials"]["table"]
    assert legacy["columns"] == columns


@pytest.mark.asyncio
async def test_a_real_appraisal_reads_the_lean_view_and_is_still_accepted(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    import test_world_stimulus_life_intent as shared
    from test_world_stimulus_life_response import _ResponseHTTP, _model, _settled

    provider = _ResponseHTTP(text="雨停了，有点想去窗边听听声音。")
    model = _model(provider)
    app = shared._build(tmp_path / "lean.sqlite", model)
    try:
        await _settled(app)
        await app.drain_background_once()
        assert provider.stimulus_requests
        material = json.loads(provider.stimulus_requests[0]["messages"][-1]["content"])
        assert material["presentation"] == LEAN_PRESENTATION_MARKER
        snapshot = material["inner_life_snapshot"]
        ids = {item["id"] for item in material["citeable_sources"]["items"]}
        assert set(snapshot["source_refs"]) <= ids
        rendered = json.dumps(snapshot["materials"], ensure_ascii=False)
        assert not HEX.search(rendered), rendered[:400]
        assert "一阵短雨已经停了" in rendered
        evidence = app.export_replay_evidence()
        responses = [
            row.event for row in evidence.events
            if row.event.event_type == "CharacterLifeResponseRecorded"
        ]
        assert responses, "the lean view must still yield an accepted life response"
    finally:
        await app.aclose()
        await model.aclose()
