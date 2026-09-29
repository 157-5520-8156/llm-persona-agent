#!/usr/bin/env python3
"""Bind the separately authored operator consistency report to the exact fixture."""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/private-audits/luna-memory-integration-20260929"
REPORT = ROOT / "docs/audits/luna-long-record-independent-assessment-2026-09-29.md"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_private(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    path.chmod(0o600)


def main() -> None:
    OUT.chmod(0o700)
    brief_path = OUT / "long-record-brief.json"
    draft_path = OUT / "long-record-draft.json"
    response_path = OUT / "long-record-review-response.json"
    report = REPORT.read_text(encoding="utf-8")
    expected = {
        "brief": "7e945df19b8386743f525ba2aef60e2c13fef0710ceaae0f8a5799c9fd43038c",
        "draft": "e8f2b379cfeb31da8bfbb2857c533f94e834efb03744ec0546488be5115e828f",
        "deepseek_review": "4b19913e4b058dedafed0af2ff3b61563a55f77b1a9f3a6eec20e93e68834dbc",
    }
    actual = {
        "brief": sha256(brief_path),
        "draft": sha256(draft_path),
        "deepseek_review": sha256(response_path),
    }
    if actual != expected or "**Verdict: approve。**" not in report:
        raise SystemExit("independent operator report no longer matches the reviewed input bytes")

    sys_path = str(ROOT / "src")
    import sys
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    from companion_daemon.world_v2.character_prehistory import (
        PrehistoryArchiveDocument,
        ReviewedPrehistoryArchive,
        digest,
    )
    from companion_daemon.world_v2.prehistory_authoring import (
        PrehistoryAuthoringBrief,
        PrehistoryCreationReview,
        PrehistoryRecordVerdict,
        package_reviewed,
        validate_draft,
    )

    brief = PrehistoryAuthoringBrief.model_validate_json(brief_path.read_text())
    document = PrehistoryArchiveDocument.model_validate_json(draft_path.read_text())
    validate_draft(brief, document)
    report_sha = sha256(REPORT)
    review = PrehistoryCreationReview(
        brief_hash=digest(brief),
        document_hash=digest(document),
        reviewer_ref="operator:gpt-6-luna-independent-consistency-review",
        reviewed_at=datetime.now(UTC),
        decision="approved",
        records=tuple(
            PrehistoryRecordVerdict(
                record_id=record.record_id,
                record_hash=digest(record),
                verdict="approve",
                rationale=(
                    "Independent operator report approved this exact record for the isolated retrieval-boundary fixture; "
                    "see luna-long-record-independent-assessment-2026-09-29.md."
                ),
            )
            for record in document.records
        ),
        cross_record_findings=(),
    )
    package = package_reviewed(
        brief,
        document,
        review,
        review_artifact_ref=str(REPORT.relative_to(ROOT)) + f"#sha256={report_sha}",
    )
    package_path = OUT / "long-record-operator-reviewed.json"
    write_private(package_path, package.model_dump(mode="json"))
    write_private(OUT / "long-record-operator-review-binding.json", {
        "reviewer_ref": review.reviewer_ref,
        "scope": "isolated synthetic retrieval-boundary fixture only",
        "review_artifact": str(REPORT.relative_to(ROOT)),
        "review_artifact_sha256": report_sha,
        "brief_sha256": sha256(brief_path),
        "document_sha256": sha256(draft_path),
        "deepseek_rejection_sha256": sha256(response_path),
        "review_contract": "operator consistency report; not DeepSeek approval and not production qualification",
        "reviewed_package_sha256": sha256(package_path),
    })
    print(json.dumps({
        "records": len(document.records),
        "reviewer_ref": review.reviewer_ref,
        "operator_report_sha256": report_sha,
        "deepseek_rejection_preserved_sha256": sha256(response_path),
        "reviewed_package_sha256": sha256(package_path),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    os.chdir(ROOT)
    main()
