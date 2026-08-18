"""lived_moment is a post-redaction view, not a compile-time sourceless string.

Compile-time assembly copied private-impression and appraisal prose into a
string with no source_ref, so InnerLifeSnapshot.model_view could not redact it.
These tests pin the leak, keep the visible reading, and prove the derived
string is not part of snapshot identity.
"""

from __future__ import annotations

import json

from companion_daemon.world_v2.character_interior.contracts import (
    LIVED_MOMENT_MATERIAL_KEY,
)
from companion_daemon.world_v2.character_interior.snapshot_compiler import (
    compile_inner_life_snapshot,
)

_HIDDEN_SUMMARY = "他说完我就一直在想他到底怎么看我"
_VISIBLE_SUMMARY = "他讲难受的事总是先说没事"


def _impression(ref: str, summary: str) -> dict[str, object]:
    return {
        "item_ref": ref,
        "privacy_class": "private",
        "value": {
            "subject_ref": "user:geoff",
            "reflection_summary": summary,
            "confidence_bp": 6_000,
            "status": "active",
        },
    }


def _context(*, impressions: list[dict[str, object]]) -> dict[str, object]:
    return {
        "world_id": "world:lived-moment",
        "actor_ref": "agent:companion",
        "world_revision": 2,
        "deliberation_revision": 1,
        "ledger_sequence": 2,
        "logical_time": "2026-08-16T16:00:00+08:00",
        "slices": {
            "private_impressions": {
                "availability": "available",
                "items": impressions,
            }
        },
    }


def test_a_hidden_private_impression_does_not_leak_through_lived_moment() -> None:
    typed = compile_inner_life_snapshot(
        _context(
            impressions=[
                _impression("impression:hidden", _HIDDEN_SUMMARY),
                _impression("impression:visible", _VISIBLE_SUMMARY),
            ]
        )
    )
    view = typed.model_view(
        visible_source_refs=frozenset(typed.source_refs) - {"impression:hidden"}
    )
    serialized = json.dumps(view, ensure_ascii=False)

    assert _HIDDEN_SUMMARY not in serialized
    assert LIVED_MOMENT_MATERIAL_KEY in view["materials"]
    assert _VISIBLE_SUMMARY in view["materials"][LIVED_MOMENT_MATERIAL_KEY]


def test_visible_impression_still_appears_in_lived_moment() -> None:
    view = compile_inner_life_snapshot(
        _context(impressions=[_impression("impression:1", _VISIBLE_SUMMARY)])
    ).model_view()

    assert f"心里还搁着：{_VISIBLE_SUMMARY}" in view["materials"][LIVED_MOMENT_MATERIAL_KEY]


def test_lived_moment_names_how_old_an_impression_is() -> None:
    impression = _impression("impression:1", _VISIBLE_SUMMARY)
    impression["value"]["first_seen"] = "2026-08-14T16:00:00+08:00"
    view = compile_inner_life_snapshot(_context(impressions=[impression])).model_view()

    assert (
        f"心里还搁着（2 天前记下的）：{_VISIBLE_SUMMARY}"
        in view["materials"][LIVED_MOMENT_MATERIAL_KEY]
    )
    hashed = json.loads(
        compile_inner_life_snapshot(_context(impressions=[impression])).materials_json
    )
    assert LIVED_MOMENT_MATERIAL_KEY not in hashed


def test_lived_moment_is_view_only_and_does_not_enter_materials_hash() -> None:
    typed = compile_inner_life_snapshot(
        _context(impressions=[_impression("impression:1", _VISIBLE_SUMMARY)])
    )
    view = typed.model_view()
    hashed = json.loads(typed.materials_json)

    assert LIVED_MOMENT_MATERIAL_KEY not in typed.materials
    assert LIVED_MOMENT_MATERIAL_KEY not in hashed
    assert LIVED_MOMENT_MATERIAL_KEY in view["materials"]
    assert all(item.scope != LIVED_MOMENT_MATERIAL_KEY for item in typed.source_inventory)
    assert all(
        LIVED_MOMENT_MATERIAL_KEY not in facet["material_keys"]
        for facet in view["faculties"].values()
    )
    assert view["snapshot_hash"] == typed.snapshot_hash
    assert view["faculties"]["private_self"]["availability"] == "available"
    assert view["faculties"]["expression_stance"]["availability"] == "available"
