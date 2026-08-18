"""Inbound QQ face catalog is a reviewed render table, not a mood translator."""

from __future__ import annotations

import pytest

from companion_daemon.world_v2.deliberation import TriggerMessage
from companion_daemon.world_v2.qq_face_render_catalog import (
    CATALOG_ID,
    compile_inbound_surfaces,
    load_face_render_catalog,
    lookup_face_render,
)

FACE_HASH = "sha256:" + "b" * 64


def test_catalog_covers_the_production_sun_face() -> None:
    catalog_id, entries = load_face_render_catalog()
    assert catalog_id == CATALOG_ID
    assert len(entries) == 382
    assert sum(1 for item in entries.values() if item.family == "system_face") == 331
    assert sum(1 for item in entries.values() if item.family == "unicode_emoji") == 51
    sun = lookup_face_render("qq-face:74")
    assert sun is not None
    assert sun.name == "太阳"
    assert sun.glyph == "☀️"
    assert "qq-bot-openapi" in sun.sources
    assert "napcat-face-config" in sun.sources


def test_catalog_names_are_labels_not_mood_annotations() -> None:
    _catalog_id, entries = load_face_render_catalog()
    forbidden = ("通常表示", "他很开心", "他在示好", "他表示赞同")
    for entry in entries.values():
        blob = f"{entry.name}{entry.glyph or ''}"
        assert all(marker not in blob for marker in forbidden)


def test_unknown_id_does_not_guess_a_nearby_name() -> None:
    assert lookup_face_render("qq-face:99999") is None
    assert lookup_face_render("qq-face:") is None
    assert lookup_face_render("74") is None
    surfaces = compile_inbound_surfaces(reaction_refs=("qq-face:99999",))
    assert surfaces[0].platform_render_name is None
    assert surfaces[0].epistemic_status == "unmatched_provider_ref_no_guessed_name"


def test_client_render_name_wins_the_documented_openapi_conflict() -> None:
    entry = lookup_face_render("qq-face:181")
    assert entry is not None
    assert entry.name == "戳一戳"
    assert "骚扰" not in entry.name


def test_outbound_allowlist_is_not_the_inbound_catalog() -> None:
    from companion_daemon.world_v2.expression_payload_contract import QQ_STICKER_OPTIONS

    outbound_ids = {item[0] for item in QQ_STICKER_OPTIONS}
    assert "qq-face:74" not in outbound_ids
    assert lookup_face_render("qq-face:74") is not None
    assert lookup_face_render("qq-face:1") is not None


def test_trigger_rejects_a_surface_that_is_not_bound_to_a_ref() -> None:
    surfaces = compile_inbound_surfaces(reaction_refs=("qq-face:74",))
    with pytest.raises(ValueError, match="inbound surface is not bound"):
        TriggerMessage(
            event_ref="event:observation:reaction:1",
            event_payload_hash=FACE_HASH,
            observation_ref="observation:reaction:1",
            source_world_revision=4,
            actor="user:primary",
            channel="qq",
            reply_target="conversation:qq:c2c:owner",
            reaction_refs=("qq-face:5",),
            inbound_surfaces=surfaces,
        )
