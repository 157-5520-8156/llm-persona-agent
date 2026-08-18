from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from companion_daemon.world_v2.character_interior.contracts import (
    assert_compile_time_materials_are_source_bound,
)
from companion_daemon.world_v2.character_interior.snapshot_compiler import (
    compile_inner_life_snapshot,
)
from companion_daemon.world_v2.context_capsule import (
    compile_shared_media_delivery_item,
)
from companion_daemon.world_v2.ledger_context_resolver import _shared_media_kind

_HASH = "a" * 64
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_LEAK_TOKENS = (
    "suggestive_private",
    "explicit_private",
    "civitai",
    "lora",
    "urn:air",
    "media_lane",
    "payload_hash",
    "gpt-image",
    "charge",
)


def _shared_at() -> datetime:
    return datetime(2026, 8, 18, 16, 0, tzinfo=_SHANGHAI)


def _item(
    *,
    delivery_id: str = "delivery:book-market",
    family: str = "life_share",
    kind: str = "activity_result",
    privacy_layer: str = "ordinary",
    he_spoke_after: bool = False,
    event_ref: str = "event:media-delivery:1",
):
    return compile_shared_media_delivery_item(
        delivery_id=delivery_id,
        shared_at=_shared_at(),
        family=family,
        kind=kind,
        privacy_layer=privacy_layer,
        he_spoke_after=he_spoke_after,
        authority_event_ref=event_ref,
        authority_world_revision=12,
        authority_payload_hash=_HASH,
    )


def _media_slice_item(item, *, source_ref: str | None = None) -> dict[str, object]:
    return {
        "item_ref": item.delivery_id,
        "source_ref": item.delivery_id if source_ref is None else source_ref,
        "privacy_class": item.privacy_class,
        "value": item.model_dump(mode="json"),
    }


def test_shared_media_kind_strips_character_media_prefix() -> None:
    assert (
        _shared_media_kind(
            family="character_media",
            contract_kind="character_media:selfie",
            ecology_category=None,
        )
        == "selfie"
    )
    assert (
        _shared_media_kind(
            family="life_share",
            contract_kind=None,
            ecology_category="activity_result",
        )
        == "activity_result"
    )
    assert (
        _shared_media_kind(family="life_share", contract_kind=None, ecology_category=None)
        == "life_share"
    )


def test_three_privacy_lanes_compile_citeable_labels_without_render_leaks() -> None:
    ordinary = _item(kind="activity_result", privacy_layer="ordinary")
    personal = _item(
        delivery_id="delivery:selfie",
        family="character_media",
        kind="selfie",
        privacy_layer="personal",
        event_ref="event:media-delivery:2",
    )
    intimate = _item(
        delivery_id="delivery:body",
        family="character_media",
        kind="body_detail",
        privacy_layer="intimate",
        event_ref="event:media-delivery:3",
    )

    assert ordinary.about == "刚做完的一件事的照片"
    assert ordinary.privacy_class == "personal"
    assert personal.about == "一张自拍"
    assert personal.privacy_class == "personal"
    assert intimate.about == "一张更近的身体细节照"
    assert intimate.privacy_class == "private"
    for item in (ordinary, personal, intimate):
        assert item.about not in _LEAK_TOKENS
        for token in _LEAK_TOKENS:
            assert token not in item.about
            assert token not in item.kind
            assert token not in item.family


def test_unknown_kind_falls_back_without_inventing_a_scene() -> None:
    life = _item(family="life_share", kind="life_share")
    face = _item(
        delivery_id="delivery:face",
        family="character_media",
        kind="character_media",
        privacy_layer="personal",
        event_ref="event:media-delivery:face",
    )
    assert life.about == "一张生活照"
    assert face.about == "一张她自己的照片"


def test_snapshot_photos_i_shared_are_source_bound() -> None:
    item = _item(he_spoke_after=True)
    typed = compile_inner_life_snapshot(
        {
            "world_id": "world:shared-media",
            "actor_ref": "agent:companion",
            "world_revision": 12,
            "deliberation_revision": 2,
            "ledger_sequence": 12,
            "logical_time": "2026-08-18T17:00:00+08:00",
            "slices": {
                "media_deliveries": {
                    "availability": "available",
                    "items": [_media_slice_item(item)],
                }
            },
        }
    )
    hashed = typed.model_dump(mode="python")
    materials = __import__("json").loads(typed.materials_json)
    photos = materials["photos_i_shared"]
    assert photos == [
        {
            "delivery_id": "delivery:book-market",
            "shared_at": "2026-08-18T16:00:00+08:00",
            "family": "life_share",
            "kind": "activity_result",
            "privacy_layer": "ordinary",
            "about": "刚做完的一件事的照片",
            "he_spoke_after": True,
            "source_ref": "delivery:book-market",
        }
    ]
    assert item.delivery_id in typed.source_refs
    view = typed.model_view()
    serialized = __import__("json").dumps(view, ensure_ascii=False)
    for token in _LEAK_TOKENS:
        assert token not in serialized
    assert "刚做完的一件事的照片" in serialized
    del hashed


def test_sourceless_photos_i_shared_fail_identity() -> None:
    with pytest.raises(ValueError, match="source-bound"):
        assert_compile_time_materials_are_source_bound(
            {
                "photos_i_shared": [
                    {
                        "about": "一张自拍",
                        "kind": "selfie",
                    }
                ]
            }
        )


def test_hiding_delivery_ref_drops_the_about_token() -> None:
    hidden = _item()
    visible = _item(
        delivery_id="delivery:selfie",
        family="character_media",
        kind="selfie",
        privacy_layer="personal",
        event_ref="event:media-delivery:2",
    )
    typed = compile_inner_life_snapshot(
        {
            "world_id": "world:shared-media",
            "actor_ref": "agent:companion",
            "world_revision": 12,
            "deliberation_revision": 2,
            "ledger_sequence": 12,
            "logical_time": "2026-08-18T17:00:00+08:00",
            "slices": {
                "media_deliveries": {
                    "availability": "available",
                    "items": [
                        _media_slice_item(hidden),
                        _media_slice_item(visible),
                    ],
                }
            },
        }
    )
    hidden_view = typed.model_view(
        visible_source_refs=frozenset(typed.source_refs) - {hidden.delivery_id}
    )
    serialized = __import__("json").dumps(hidden_view, ensure_ascii=False)
    assert "刚做完的一件事的照片" not in serialized
    assert "一张自拍" in serialized
    other_hidden = typed.model_view(
        visible_source_refs=frozenset(typed.source_refs) - {"event:media-delivery:1"}
    )
    other = __import__("json").dumps(other_hidden, ensure_ascii=False)
    assert "刚做完的一件事的照片" in other
