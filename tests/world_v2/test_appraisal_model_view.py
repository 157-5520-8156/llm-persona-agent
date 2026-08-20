from __future__ import annotations

from companion_daemon.world_v2.character_interior.appraisal_model_view import (
    appraisal_meanings,
    compact_appraisals_for_model_view,
)


def _sample_appraisal(*, with_excerpt: bool = False) -> dict[str, object]:
    entry: dict[str, object] = {
        "source_ref": "appraisal:sample",
        "subject_ref": "user:geoff",
        "source_cluster_ref": "cluster:ignored",
        "confidence_bp": 8_800,
        "accepted_at": "2026-08-16T12:01:00+08:00",
        "expires_at": "2026-08-17T12:01:00+08:00",
        "hypotheses": [
            {
                "hypothesis_id": "meaning:long-hash:0",
                "meaning": "他在往后推",
                "attribution": "user",
                "controllability": "uncontrollable",
                "severity": "moderate",
                "weight_bp": 10_000,
            }
        ],
        "evidence_refs": [
            {
                "ref_id": "event:obs-1",
                "evidence_type": "observed_message",
                "claim_purpose": "private_hypothesis",
            }
        ],
    }
    if with_excerpt:
        entry["stimulus_excerpts"] = ["今晚可能不去了"]
    return entry


def test_compact_appraisal_table_drops_audit_fields_and_preserves_meaning() -> None:
    compact = compact_appraisals_for_model_view(
        [_sample_appraisal(with_excerpt=True)],
        logical_time=None,
    )
    assert isinstance(compact, dict)
    assert compact["subject"] == "user:geoff"
    row = compact["rows"][0]
    assert row[0] == "appraisal:sample"
    assert row[4][0][0] == "他在往后推"
    assert row[5] == ["今晚可能不去了"]
    serialized = str(compact)
    assert "evidence_refs" not in serialized
    assert "source_cluster_ref" not in serialized
    assert "hypothesis_id" not in serialized


def test_appraisal_meanings_survive_compaction() -> None:
    raw = [_sample_appraisal(), _sample_appraisal()]
    raw[1] = {**raw[1], "source_ref": "appraisal:sample-2"}
    compact = compact_appraisals_for_model_view(raw, logical_time=None)
    assert appraisal_meanings(raw) == appraisal_meanings(compact)
