"""Compile-time sourceless materials must not bypass `_redact_materials`.

`_redact_materials` keeps a list item when it has no string `source_ref`.
A derived string or `{date, lines}` dict compiled before the view is therefore
shown even after its sources are hidden. These tests pin the leak shapes,
keep the visible reading, and make a new sourceless compile-time material fail
snapshot identity instead of waiting for a human to notice.
"""

from __future__ import annotations

import json

import pytest

from companion_daemon.world_v2.character_interior.contracts import (
    INNER_RETENTION_MATERIAL_KEY,
    LIVED_MOMENT_MATERIAL_KEY,
    POST_REDACTION_ONLY_KEYS,
    InnerLifeSnapshot,
    assert_compile_time_materials_are_source_bound,
    compile_time_material_is_source_bound,
)
from companion_daemon.world_v2.character_interior.snapshot_compiler import (
    compile_inner_life_snapshot,
)

_SECRET_DIARY = "SECRET_WEEK_DIARY_私密书店独自待到很晚"
_VISIBLE_DIARY = "VISIBLE_WEEK_DIARY_图书馆靠窗坐了一下午"
_SECRET_SEASON = "SECRET_BIO_SEASON_token"
_SECRET_MESSAGE = "SECRET_APPRAISAL_EXCERPT_今晚可能不去了"
_VISIBLE_MESSAGE = "VISIBLE_DIALOGUE_下午见"


def _context(**slices: object) -> dict[str, object]:
    return {
        "world_id": "world:redaction-audit",
        "actor_ref": "agent:companion",
        "world_revision": 4,
        "deliberation_revision": 2,
        "ledger_sequence": 4,
        "logical_time": "2026-08-16T16:00:00+08:00",
        "slices": slices,
    }


def _experience(ref: str, stamp: str, text: str) -> dict[str, object]:
    return {
        "item_ref": ref,
        "source_ref": ref,
        "privacy_class": "private",
        "value": {
            "experience_id": ref,
            "values": {
                "occurred_from": stamp,
                "occurred_to": stamp,
                "participant_refs": ["agent:companion"],
                "privacy_class": "private",
            },
            "content": {"text": text},
        },
    }


def _dialogue(
    ref: str,
    *,
    speaker: str,
    text: str,
    occurred_at: str,
    sequence: int,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    value = {
        "dialogue_id": ref,
        "speaker": speaker,
        "speaker_ref": "agent:companion" if speaker == "companion" else "user:primary",
        "text": text,
        "occurred_at": occurred_at,
        "delivery_state": "delivered" if speaker == "companion" else "observed",
        "sequence": sequence,
    }
    if extra:
        value.update(extra)
    return {
        "item_ref": ref,
        "source_ref": ref,
        "privacy_class": "private",
        "value": value,
    }


def _recreate(typed: InnerLifeSnapshot, materials: dict[str, object]) -> InnerLifeSnapshot:
    return InnerLifeSnapshot.create(
        availability=typed.availability,
        world_id=typed.world_id,
        actor_ref=typed.actor_ref,
        cursor=typed.cursor,
        logical_time=typed.logical_time,
        situation=typed.situation,
        continuity=typed.continuity,
        facet_views=typed.facet_views,
        materials=materials,
        source_refs=typed.source_refs,
        source_inventory=typed.source_inventory,
        viewer_scope=typed.viewer_scope,
        privacy_scope=typed.privacy_scope,
        capability_scope=typed.capability_scope,
        context_compiler=typed.context_compiler,
        snapshot_compiler=typed.snapshot_compiler,
        truncation=typed.truncation,
        recall_trace_json=typed.recall_trace_json,
        prefetch_trace_json=typed.prefetch_trace_json,
    )


def test_sourceless_compile_shapes_are_not_treated_as_source_bound() -> None:
    assert compile_time_material_is_source_bound(
        "logical_time", "2026-08-16T16:00:00+08:00"
    )
    assert not compile_time_material_is_source_bound(
        "day_sheet", "今天是2026-08-16，21岁。"
    )
    assert not compile_time_material_is_source_bound(
        "week_diary", [{"date": "2026-08-16", "lines": [_SECRET_DIARY]}]
    )
    assert not compile_time_material_is_source_bound(
        "since_he_last_spoke", {"seconds": 3600}
    )
    assert compile_time_material_is_source_bound(
        "week_diary",
        [{"date": "2026-08-16", "line": _VISIBLE_DIARY, "source_ref": "experience:1"}],
    )


def test_a_new_sourceless_derived_material_is_rejected_at_snapshot_identity() -> None:
    typed = compile_inner_life_snapshot(
        _context(
            recent_experiences={
                "availability": "available",
                "items": [
                    _experience(
                        "experience:visible",
                        "2026-08-16T12:00:00+08:00",
                        _VISIBLE_DIARY,
                    )
                ],
            }
        )
    )
    leaky = dict(typed.materials)
    leaky["leaky_week_diary"] = [{"date": "2026-08-16", "lines": [_SECRET_DIARY]}]

    with pytest.raises(ValueError, match="source-bound"):
        assert_compile_time_materials_are_source_bound(leaky)
    with pytest.raises(ValueError, match="source-bound"):
        _recreate(typed, leaky)

    hashed = dict(typed.materials)
    hashed["day_sheet"] = f"今天已经过的：{_SECRET_DIARY}"
    with pytest.raises(ValueError, match="source-bound"):
        assert_compile_time_materials_are_source_bound(hashed)
    with pytest.raises(ValueError, match="source-bound"):
        _recreate(typed, hashed)


def test_post_redaction_keys_must_not_enter_materials_json() -> None:
    typed = compile_inner_life_snapshot(
        _context(
            world_life={
                "availability": "available",
                "items": [
                    {
                        "item_ref": "bio:1",
                        "source_ref": "bio:1",
                        "privacy_class": "shareable",
                        "value": {
                            "context_kind": "biographical_context",
                            "logical_at": "2026-08-16T16:00:00+08:00",
                            "age": 21,
                            "academic_phase": "term",
                            "academic_year": 3,
                            "season": "summer",
                        },
                    }
                ],
            },
            private_impressions={
                "availability": "available",
                "items": [
                    {
                        "item_ref": "impression:1",
                        "source_ref": "impression:1",
                        "privacy_class": "private",
                        "value": {
                            "subject_ref": "user:geoff",
                            "reflection_summary": "他讲难受的事总是先说没事",
                            "confidence_bp": 6_000,
                            "status": "active",
                        },
                    }
                ],
            },
        )
    )
    hashed = json.loads(typed.materials_json)
    assert POST_REDACTION_ONLY_KEYS.isdisjoint(hashed)
    view = typed.model_view()
    assert "day_sheet" in view["materials"]
    assert LIVED_MOMENT_MATERIAL_KEY in view["materials"]
    assert INNER_RETENTION_MATERIAL_KEY in view["materials"]


def test_hidden_experience_does_not_leak_through_week_diary() -> None:
    typed = compile_inner_life_snapshot(
        _context(
            recent_experiences={
                "availability": "available",
                "items": [
                    _experience(
                        "experience:hidden",
                        "2026-08-16T11:00:00+08:00",
                        _SECRET_DIARY,
                    ),
                    _experience(
                        "experience:visible",
                        "2026-08-16T12:00:00+08:00",
                        _VISIBLE_DIARY,
                    ),
                ],
            }
        )
    )
    hashed = json.loads(typed.materials_json)
    assert all(
        isinstance(row.get("source_ref"), str) for row in hashed["week_diary"]
    )
    view = typed.model_view(
        visible_source_refs=frozenset(typed.source_refs) - {"experience:hidden"}
    )
    serialized = json.dumps(view, ensure_ascii=False)

    assert _SECRET_DIARY not in serialized
    assert view["materials"]["week_diary"] == [
        {"date": "2026-08-16", "lines": [_VISIBLE_DIARY]}
    ]
    assert _VISIBLE_DIARY in view["materials"][LIVED_MOMENT_MATERIAL_KEY]


def test_hidden_biography_does_not_leak_through_day_sheet() -> None:
    typed = compile_inner_life_snapshot(
        _context(
            world_life={
                "availability": "available",
                "items": [
                    {
                        "item_ref": "bio:hidden",
                        "source_ref": "bio:hidden",
                        "privacy_class": "personal",
                        "value": {
                            "context_kind": "biographical_context",
                            "logical_at": "2026-08-16T16:00:00+08:00",
                            "age": 21,
                            "academic_phase": "term",
                            "academic_year": 3,
                            "season": _SECRET_SEASON,
                        },
                    }
                ],
            }
        )
    )
    view = typed.model_view(visible_source_refs=frozenset())
    serialized = json.dumps(view, ensure_ascii=False)

    assert _SECRET_SEASON not in serialized
    assert "21岁" not in serialized
    assert "day_sheet" in view["materials"]
    assert str(view["materials"]["day_sheet"]).startswith("今天是2026-08-16")


def test_hidden_dialogue_does_not_leak_through_stimulus_excerpts() -> None:
    typed = compile_inner_life_snapshot(
        _context(
            recent_dialogue={
                "availability": "available",
                "items": [
                    _dialogue(
                        "dialogue:observation:obs-hidden",
                        speaker="counterpart",
                        text=_SECRET_MESSAGE,
                        occurred_at="2026-08-16T12:00:00+08:00",
                        sequence=100,
                        extra={
                            "source_claims": [
                                {"authority_event_ref": "event:obs-hidden"}
                            ]
                        },
                    ),
                    _dialogue(
                        "dialogue:observation:obs-visible",
                        speaker="counterpart",
                        text=_VISIBLE_MESSAGE,
                        occurred_at="2026-08-16T12:05:00+08:00",
                        sequence=200,
                    ),
                ],
            },
            appraisals={
                "availability": "available",
                "items": [
                    {
                        "item_ref": "appraisal:1",
                        "source_ref": "appraisal:1",
                        "privacy_class": "private",
                        "value": {
                            "subject_ref": "user:geoff",
                            "source_cluster_ref": "cluster:1",
                            "hypotheses": [
                                {
                                    "hypothesis_id": "h1",
                                    "meaning": "他在往后推",
                                    "attribution": "user",
                                    "controllability": "uncontrollable",
                                    "severity": "moderate",
                                    "weight_bp": 10_000,
                                }
                            ],
                            "evidence_refs": [
                                {
                                    "ref_id": "event:obs-hidden",
                                    "evidence_type": "observed_message",
                                    "claim_purpose": "private_hypothesis",
                                }
                            ],
                            "confidence_bp": 8_800,
                            "accepted_at": "2026-08-16T12:01:00+08:00",
                            "expires_at": "2026-08-17T12:01:00+08:00",
                        },
                    }
                ],
            },
        )
    )
    view = typed.model_view(
        visible_source_refs=frozenset(typed.source_refs)
        - {"dialogue:observation:obs-hidden"}
    )
    serialized = json.dumps(view, ensure_ascii=False)

    assert _SECRET_MESSAGE not in serialized
    appraisals = view["materials"]["appraisals"]
    row = appraisals["rows"][0]
    assert len(row) == 5
    assert row[4][0][0] == "他在往后推"
    assert _VISIBLE_MESSAGE in serialized


def test_hidden_counterpart_line_does_not_count_in_since_he_last_spoke() -> None:
    typed = compile_inner_life_snapshot(
        _context(
            recent_dialogue={
                "availability": "available",
                "items": [
                    _dialogue(
                        "dialogue:old",
                        speaker="counterpart",
                        text="在吗",
                        occurred_at="2026-08-16T12:00:00+08:00",
                        sequence=100,
                    ),
                    _dialogue(
                        "dialogue:hidden",
                        speaker="counterpart",
                        text=_SECRET_MESSAGE,
                        occurred_at="2026-08-16T15:00:00+08:00",
                        sequence=200,
                    ),
                ],
            }
        )
    )
    full = typed.model_view()
    hidden = typed.model_view(
        visible_source_refs=frozenset(typed.source_refs) - {"dialogue:hidden"}
    )

    assert full["materials"]["since_he_last_spoke"] == {"seconds": 3600}
    assert hidden["materials"]["since_he_last_spoke"] == {"seconds": 14400}
    assert _SECRET_MESSAGE not in json.dumps(hidden, ensure_ascii=False)
    assert "conversation" in hidden["materials"]
    assert hidden["materials"]["conversation"] == ["他（4 小时前）：在吗"]


def test_hidden_folded_line_does_not_leak_through_fold_chunks() -> None:
    items = [
        _dialogue(
            f"dialogue:{index}",
            speaker="counterpart" if index % 2 == 0 else "companion",
            text=(
                _SECRET_MESSAGE + " " + ("雅思报名细节 " * 120)
                if index == 0
                else f"bubble {index} " + ("雅思报名细节 " * 120)
            ),
            occurred_at=f"2026-08-13T12:{index:02d}:00+08:00",
            sequence=(index + 1) * 100,
        )
        for index in range(40)
    ]
    typed = compile_inner_life_snapshot(
        _context(
            recent_dialogue={"availability": "available", "items": items},
        )
    )
    hashed = json.loads(typed.materials_json)
    assert all(
        isinstance(row.get("source_ref"), str) for row in hashed["folded_dialogue"]
    )
    view = typed.model_view(
        visible_source_refs=frozenset(typed.source_refs) - {"dialogue:0"}
    )
    serialized = json.dumps(view, ensure_ascii=False)

    assert _SECRET_MESSAGE not in serialized
    folded = view["materials"]["folded_dialogue"]
    assert folded
    assert "dialogue:0" not in folded[0]["dialogue_ids"]
    assert any("bubble 1" in line for chunk in folded for line in chunk["lines"])


def test_hidden_source_payload_cannot_appear_in_any_derived_material() -> None:
    """A unique token in a hidden source must not survive `model_view()`.

    Top-level sourceless strings are already rejected at identity. This pins
    the remaining copy pattern: nested quotes or regrouped presentation that
    still mention a source the viewer may not see. A new derived material
    that concatenates a hidden payload fails here even if it has a
    `source_ref` belonging to a different, still-visible item.
    """

    tokens = {
        "experience:secret": "TOK_EXP_HIDDEN_zq9m4",
        "bio:secret": "TOK_BIO_SEASON_zq9m4",
        "dialogue:secret": "TOK_DLG_HIDDEN_zq9m4",
        "impression:secret": "TOK_IMP_HIDDEN_zq9m4",
        "appraisal:secret": "TOK_APR_MEANING_zq9m4",
        "delivery:secret": "TOK_PHOTO_ABOUT_zq9m4",
    }
    typed = compile_inner_life_snapshot(
        _context(
            recent_experiences={
                "availability": "available",
                "items": [
                    _experience(
                        "experience:secret",
                        "2026-08-16T11:00:00+08:00",
                        tokens["experience:secret"],
                    ),
                    _experience(
                        "experience:visible",
                        "2026-08-16T12:00:00+08:00",
                        _VISIBLE_DIARY,
                    ),
                ],
            },
            world_life={
                "availability": "available",
                "items": [
                    {
                        "item_ref": "bio:secret",
                        "source_ref": "bio:secret",
                        "privacy_class": "personal",
                        "value": {
                            "context_kind": "biographical_context",
                            "logical_at": "2026-08-16T16:00:00+08:00",
                            "age": 21,
                            "academic_phase": "term",
                            "academic_year": 3,
                            "season": tokens["bio:secret"],
                        },
                    }
                ],
            },
            recent_dialogue={
                "availability": "available",
                "items": [
                    _dialogue(
                        "dialogue:secret",
                        speaker="counterpart",
                        text=tokens["dialogue:secret"],
                        occurred_at="2026-08-16T12:00:00+08:00",
                        sequence=100,
                        extra={
                            "source_claims": [
                                {"authority_event_ref": "event:obs-secret"}
                            ]
                        },
                    ),
                    _dialogue(
                        "dialogue:visible",
                        speaker="counterpart",
                        text=_VISIBLE_MESSAGE,
                        occurred_at="2026-08-16T12:05:00+08:00",
                        sequence=200,
                    ),
                ],
            },
            private_impressions={
                "availability": "available",
                "items": [
                    {
                        "item_ref": "impression:secret",
                        "source_ref": "impression:secret",
                        "privacy_class": "private",
                        "value": {
                            "subject_ref": "user:geoff",
                            "reflection_summary": tokens["impression:secret"],
                            "confidence_bp": 6_000,
                            "status": "active",
                        },
                    }
                ],
            },
            media_deliveries={
                "availability": "available",
                "items": [
                    {
                        "item_ref": "delivery:secret",
                        "source_ref": "delivery:secret",
                        "privacy_class": "personal",
                        "value": {
                            "delivery_id": "delivery:secret",
                            "shared_at": "2026-08-16T12:00:00+08:00",
                            "family": "life_share",
                            "kind": "activity_result",
                            "privacy_layer": "ordinary",
                            "about": tokens["delivery:secret"],
                            "he_spoke_after": False,
                        },
                    },
                    {
                        "item_ref": "delivery:visible",
                        "source_ref": "delivery:visible",
                        "privacy_class": "personal",
                        "value": {
                            "delivery_id": "delivery:visible",
                            "shared_at": "2026-08-16T12:10:00+08:00",
                            "family": "character_media",
                            "kind": "selfie",
                            "privacy_layer": "personal",
                            "about": "一张自拍",
                            "he_spoke_after": True,
                        },
                    },
                ],
            },
            appraisals={
                "availability": "available",
                "items": [
                    {
                        "item_ref": "appraisal:secret",
                        "source_ref": "appraisal:secret",
                        "privacy_class": "private",
                        "value": {
                            "subject_ref": "user:geoff",
                            "source_cluster_ref": "cluster:1",
                            "hypotheses": [
                                {
                                    "hypothesis_id": "h1",
                                    "meaning": tokens["appraisal:secret"],
                                    "attribution": "user",
                                    "controllability": "uncontrollable",
                                    "severity": "moderate",
                                    "weight_bp": 10_000,
                                }
                            ],
                            "evidence_refs": [
                                {
                                    "ref_id": "event:obs-secret",
                                    "evidence_type": "observed_message",
                                    "claim_purpose": "private_hypothesis",
                                }
                            ],
                            "confidence_bp": 8_800,
                            "accepted_at": "2026-08-16T12:01:00+08:00",
                            "expires_at": "2026-08-17T12:01:00+08:00",
                        },
                    }
                ],
            },
        )
    )
    full = json.dumps(typed.model_view(), ensure_ascii=False)
    for token in tokens.values():
        assert token in full

    for hidden_ref, token in tokens.items():
        view = typed.model_view(
            visible_source_refs=frozenset(typed.source_refs) - {hidden_ref}
        )
        serialized = json.dumps(view, ensure_ascii=False)
        assert token not in serialized, hidden_ref
        assert _VISIBLE_DIARY in serialized
        assert _VISIBLE_MESSAGE in serialized
