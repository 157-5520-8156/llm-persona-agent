"""Evidence-first transport changes neither source authority nor old receipts."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from companion_daemon.world_v2.proposal_envelope import DecisionProposal
from companion_daemon.world_v2.visible_source_composer import VisibleSourceTable
from companion_daemon.world_v2.visible_source_review_receipt import prepare_visible_source_review
from companion_daemon.world_v2.visible_source_closure_protocol import (
    VisibleSourceClosureWireFailure,
    visible_source_closure_messages,
    visible_source_closure_schema,
)


LEGACY_HASHES = (
    "b421d3c32bd46e89b52fcf465a781002943930f1be54e9c0b6c618b4a5c57132",
    "03cc4f6973f8cea977a7d4ba114d3bdca1669c0e34225932392890223c3b1e2e",
    "a384f6cb3c0ef939284e2078e70c3b454567c767535ebabba3736e5416709888",
    "b67427d064283ac3b8883b248c04718053351b9143dd308179fb84b8224f32e5",
    "83372b6c5e8066060b6dbb05d7bc6a98812f10b2d1c06c8fbaf6af38e1b9ff86",
    "2ecae5110afa69e00cc3ab2a1859414173cb6ed846a4e194e7424540052841b2",
)


@pytest.mark.parametrize("version,digest", tuple(enumerate(LEGACY_HASHES, 1)))
def test_v1_to_v6_preparations_keep_exact_bytes(version, digest):
    fixture = Path(__file__).parent / "fixtures/visible_source_review_receipt_v2.json"
    value = json.loads(json.loads(fixture.read_text())["prepared_json"])
    prepared = prepare_visible_source_review(
        candidate=DecisionProposal.model_validate_json(value["candidate_json"]),
        source_table=VisibleSourceTable(payload_json=value["source_table_json"]),
        source_ref_aliases=value["source_ref_aliases"], review_version=str(version),
    )
    assert hashlib.sha256(prepared.payload_json.encode()).hexdigest() == digest


@pytest.mark.parametrize("repair", [False, True])
def test_v7_corrections_share_all_evidence_before_different_candidate(repair):
    fixture = Path(__file__).parent / "fixtures/visible_source_review_receipt_v2.json"
    value = json.loads(json.loads(fixture.read_text())["prepared_json"])
    sources = VisibleSourceTable(payload_json=value["source_table_json"]).source_references()
    kwargs = dict(source_references=sources, invalid_reason=(
        VisibleSourceClosureWireFailure("schema_invalid", "fixture") if repair else None
    ))
    original = dict(visible_beats=("最初的表达。",), world_claims=())
    changed = dict(visible_beats=("重选后的表达。",), world_claims=({"changed": "claim"},))
    old = visible_source_closure_messages(**kwargs, **original, version="6")
    first = visible_source_closure_messages(**kwargs, **original, version="7")
    corrected = visible_source_closure_messages(**kwargs, **changed, version="7")
    old_packet = json.loads(old[1]["content"])
    packet = json.loads(first[1]["content"])
    old_packet["output_contract"]["contract"] = "visible-beat-source-verdict.7"
    assert packet == old_packet  # Every source, index, permission and clause survives.
    assert list(packet)[-2:] == ["visible_beats", "world_claims"]
    assert first[0] == corrected[0]
    prefix = first[1]["content"].split(',"visible_beats":', 1)[0]
    assert corrected[1]["content"].startswith(prefix + ',"visible_beats":')
    assert len(os.path.commonprefix([first[1]["content"], corrected[1]["content"]])) >= len(prefix)
    assert json.loads(corrected[1]["content"])["world_claims"] == [{"changed": "claim"}]
    old_schema = visible_source_closure_schema(version="6")
    old_schema["properties"]["contract"]["enum"] = ["visible-beat-source-verdict.7"]
    assert visible_source_closure_schema(version="7") == old_schema
