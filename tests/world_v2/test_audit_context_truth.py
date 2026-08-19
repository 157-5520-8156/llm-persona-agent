from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

SCRIPT = REPO / "scripts" / "audit_context_truth.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "context_truth"


def _load_cli():
    spec = importlib.util.spec_from_file_location("audit_context_truth", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_known_six_issues_are_mapped() -> None:
    from context_truth.slots import known_issue_coverage

    coverage = known_issue_coverage()
    assert set(coverage) == {"K1", "K2", "K3", "K4", "K5", "K6"}
    missing = [key for key, slots in coverage.items() if not slots]
    assert missing == [], f"slot inventory dropped known issues: {missing}"


def test_every_facet_has_at_least_one_slot() -> None:
    from context_truth.slots import FACETS, facet_coverage

    coverage = facet_coverage()
    empty = [facet for facet in FACETS if not coverage.get(facet)]
    assert empty == [], f"facets with no world-fact slots: {empty}"


def test_ci_mismatch_fixture_exits_nonzero(tmp_path: Path) -> None:
    cli = _load_cli()
    out = tmp_path / "mismatch"
    code = cli.main(
        [
            "--ci",
            "--fixture",
            str(FIXTURES / "ci_mismatch.json"),
            "--out",
            str(out),
            "--skip-behavior",
        ]
    )
    assert code == 1
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    fired = set(summary["known_issues_fired"])
    assert fired == {"K1", "K2", "K3", "K4", "K5", "K6"}
    assert summary["novel_finding_count"] >= 1
    assert (out / "REPORT.md").is_file()
    assert (out / "SLOTS.md").is_file()


def test_ci_clean_fixture_exits_zero(tmp_path: Path) -> None:
    cli = _load_cli()
    out = tmp_path / "clean"
    code = cli.main(
        [
            "--ci",
            "--fixture",
            str(FIXTURES / "ci_clean.json"),
            "--out",
            str(out),
            "--skip-behavior",
        ]
    )
    assert code == 0
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["finding_count"] == 0


def test_k5_passes_when_snapshot_shows_catalog_name() -> None:
    from context_truth.compare import compare_slot
    from context_truth.slots import slots_by_id
    from context_truth.types import CatalogFace, LedgerTruth, SeenView

    slot = slots_by_id()["selective_memory.inbound_surfaces.catalog_name"]
    seen = SeenView(
        materials={
            "recent_dialogue": [
                {"speaker": "counterpart", "text": "☀️ 太阳"},
            ]
        }
    )
    truth = LedgerTruth(
        catalog_faces=(
            CatalogFace(
                provider_ref="qq-face:74",
                catalog_name="太阳",
                catalog_glyph="☀️",
                seen_text="qq-face:74",
            ),
        )
    )
    assert compare_slot(slot, seen, truth) is None


def test_k5_fires_when_snapshot_shows_only_the_number() -> None:
    from context_truth.compare import compare_slot
    from context_truth.slots import slots_by_id
    from context_truth.types import CatalogFace, LedgerTruth, SeenView

    slot = slots_by_id()["selective_memory.inbound_surfaces.catalog_name"]
    seen = SeenView(
        materials={
            "recent_dialogue": [
                {"speaker": "counterpart", "text": "qq-face:74"},
            ]
        }
    )
    truth = LedgerTruth(
        catalog_faces=(
            CatalogFace(
                provider_ref="qq-face:74",
                catalog_name="太阳",
                catalog_glyph="☀️",
                seen_text="qq-face:74",
            ),
        )
    )
    finding = compare_slot(slot, seen, truth)
    assert finding is not None
    assert finding.known_issue == "K5"


def test_list_slots_covers_how_to_extend() -> None:
    from context_truth.report import slot_inventory

    inventory = slot_inventory()
    assert inventory
    assert all("how_to_extend" in item for item in inventory)
    assert any(item["known_issue"] == "K1" for item in inventory)
