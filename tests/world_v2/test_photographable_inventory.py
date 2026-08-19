from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from companion_daemon.world_v2.character_interior.contracts import (
    assert_compile_time_materials_are_source_bound,
)
from companion_daemon.world_v2.character_interior.snapshot_compiler import (
    compile_inner_life_snapshot,
)
from companion_daemon.world_v2.photographable_inventory import (
    available_photo_source_refs,
    compile_photographable_inventory,
    empty_inventory_material,
    inventory_material,
    settlement_has_available_photo,
)
from companion_daemon.world_v2.world_life_context import (
    WorldLifeContextItem,
    WorldLifeSourceBinding,
)

NOW = datetime(2026, 8, 18, 15, 15, 33, tzinfo=UTC)
SETTLEMENT = "event:life-aftermath:settlement:bookstore"


def _candidate(*, refs: tuple[str, ...], status: str = "available"):
    return SimpleNamespace(
        status=status,
        expires_at=NOW + timedelta(days=2),
        source_event_refs=refs,
        source_events=(SimpleNamespace(event_ref=refs[0]),),
    )


def test_empty_projection_has_no_photo_in_hand() -> None:
    projection = SimpleNamespace(photo_candidates=())
    assert available_photo_source_refs(projection, logical_time=NOW) == frozenset()
    assert settlement_has_available_photo(
        projection, settlement_ref=SETTLEMENT, logical_time=NOW
    ) is False


def test_available_candidate_marks_the_bound_settlement() -> None:
    projection = SimpleNamespace(
        photo_candidates=(_candidate(refs=(SETTLEMENT,)),)
    )
    assert settlement_has_available_photo(
        projection, settlement_ref=SETTLEMENT, logical_time=NOW
    ) is True
    assert settlement_has_available_photo(
        projection, settlement_ref="event:other", logical_time=NOW
    ) is False


def test_expired_or_closed_candidates_do_not_count() -> None:
    expired = SimpleNamespace(
        status="available",
        expires_at=NOW - timedelta(seconds=1),
        source_event_refs=(SETTLEMENT,),
        source_events=(),
    )
    declined = _candidate(refs=(SETTLEMENT,), status="declined")
    projection = SimpleNamespace(photo_candidates=(expired, declined))
    assert settlement_has_available_photo(
        projection, settlement_ref=SETTLEMENT, logical_time=NOW
    ) is False


def test_inventory_material_states_zero_when_empty() -> None:
    material = empty_inventory_material(source_refs=(SETTLEMENT,))
    assert material["availability"] == "available"
    assert material["available_count"] == 0
    assert material["already_sent_count"] == 0
    assert material["items"] == []
    assert material["source_refs"] == [SETTLEMENT]
    assert_compile_time_materials_are_source_bound({"moments_i_can_share": material})


def test_inventory_does_not_nudge_a_share() -> None:
    inventory = compile_photographable_inventory(
        moments=(),
        already_sent_count=0,
        extra_source_refs=(SETTLEMENT,),
    )
    blob = str(inventory_material(inventory))
    assert "值得分享" not in blob
    assert "建议" not in blob
    assert "应该" not in blob


def test_world_life_item_defaults_to_no_photo_in_hand() -> None:
    item = WorldLifeContextItem(
        occurrence_id="occurrence:walk",
        occurrence_entity_revision=1,
        participant_refs=("agent:companion",),
        location_ref="location:jiaxing-family-bookstore",
        result_id="result:1",
        result_payload_ref="content:1",
        result_payload_hash="a" * 64,
        settled_at=NOW,
        privacy_class="shareable",
        source=WorldLifeSourceBinding(
            authority_event_ref=SETTLEMENT,
            authority_world_revision=4,
            authority_payload_hash="b" * 64,
        ),
    )
    assert item.photo_in_hand is False


def _world_life_slice(*, photo_in_hand: bool) -> dict[str, object]:
    return {
        "availability": "available",
        "source_refs": [SETTLEMENT],
        "items": [
            {
                "source_ref": SETTLEMENT,
                "privacy_class": "shareable",
                "value": {
                    "occurrence_id": "occurrence:bookstore",
                    "occurrence_entity_revision": 4,
                    "participant_refs": ["agent:companion"],
                    "location_ref": "location:jiaxing-family-bookstore",
                    "result_id": "result:bookstore",
                    "settled_at": NOW.isoformat(),
                    "privacy_class": "shareable",
                    "photo_in_hand": photo_in_hand,
                    "content": {
                        "text": "她决定去书店看看，坐在角落听大家聊书。",
                    },
                },
            }
        ],
    }


def test_snapshot_empty_album_is_an_explicit_fact() -> None:
    typed = compile_inner_life_snapshot(
        {
            "world_id": "world:album",
            "actor_ref": "agent:companion",
            "world_revision": 12,
            "deliberation_revision": 2,
            "ledger_sequence": 12,
            "logical_time": NOW.isoformat(),
            "slices": {"world_life": _world_life_slice(photo_in_hand=False)},
        }
    )
    materials = __import__("json").loads(typed.materials_json)
    inventory = materials["moments_i_can_share"]
    assert inventory["available_count"] == 0
    assert inventory["already_sent_count"] == 0
    assert inventory["items"][0]["photo_in_hand"] is False
    assert inventory["items"][0]["source_ref"] == SETTLEMENT
    assert "书店" in inventory["items"][0]["what_happened"]
    assert "photos_i_shared" not in materials
    view = typed.model_view()
    serialized = __import__("json").dumps(view, ensure_ascii=False)
    assert "available_count" in serialized
    assert "值得分享" not in serialized


def test_snapshot_empty_now_album_survives_redaction() -> None:
    typed = compile_inner_life_snapshot(
        {
            "world_id": "world:album",
            "actor_ref": "agent:companion",
            "world_revision": 12,
            "deliberation_revision": 2,
            "ledger_sequence": 12,
            "logical_time": NOW.isoformat(),
            "slices": {
                "current_situation": {
                    "availability": "available",
                    "source_refs": ["event:situation:empty"],
                    "items": [
                        {
                            "source_ref": "event:situation:empty",
                            "value": {"activity_slices": []},
                        }
                    ],
                },
            },
        }
    )
    materials = __import__("json").loads(typed.materials_json)
    inventory = materials["moments_i_can_share"]
    assert inventory["available_count"] == 0
    assert inventory["items"] == []
    assert inventory["now"]["photographable"] is False
    assert inventory["now"]["reason"] == "no_active_activity"
    view = typed.model_view()
    seen = view["materials"]["moments_i_can_share"]
    assert seen["available_count"] == 0
    assert seen["items"] == []
    assert seen["now"]["photographable"] is False
    hidden = typed.model_view(visible_source_refs=frozenset())
    assert "moments_i_can_share" not in hidden["materials"]


def test_snapshot_now_is_not_photographable_without_active_activity() -> None:
    typed = compile_inner_life_snapshot(
        {
            "world_id": "world:album",
            "actor_ref": "agent:companion",
            "world_revision": 12,
            "deliberation_revision": 2,
            "ledger_sequence": 12,
            "logical_time": NOW.isoformat(),
            "slices": {
                "world_life": _world_life_slice(photo_in_hand=False),
                "current_situation": {
                    "availability": "available",
                    "source_refs": ["event:situation:1"],
                    "items": [
                        {
                            "source_ref": "event:situation:1",
                            "value": {"activity_slices": []},
                        }
                    ],
                },
            },
        }
    )
    materials = __import__("json").loads(typed.materials_json)
    now = materials["moments_i_can_share"]["now"]
    assert now["photographable"] is False
    assert now["reason"] == "no_active_activity"


def test_snapshot_now_is_photographable_for_an_active_activity() -> None:
    typed = compile_inner_life_snapshot(
        {
            "world_id": "world:album",
            "actor_ref": "agent:companion",
            "world_revision": 12,
            "deliberation_revision": 2,
            "ledger_sequence": 12,
            "logical_time": NOW.isoformat(),
            "slices": {
                "world_life": _world_life_slice(photo_in_hand=False),
                "current_situation": {
                    "availability": "available",
                    "source_refs": ["event:started:walk"],
                    "items": [
                        {
                            "source_ref": "event:started:walk",
                            "value": {
                                "activity_slices": [
                                    {
                                        "plan_id": "plan:walk",
                                        "activity_kind": "commute.short_walk",
                                        "status": "active",
                                    }
                                ]
                            },
                        }
                    ],
                },
            },
        }
    )
    materials = __import__("json").loads(typed.materials_json)
    now = materials["moments_i_can_share"]["now"]
    assert now["photographable"] is True
    assert now["reason"] == "active"
    assert now["activity_kind"] == "commute.short_walk"


def test_snapshot_photo_in_hand_is_counted() -> None:
    typed = compile_inner_life_snapshot(
        {
            "world_id": "world:album",
            "actor_ref": "agent:companion",
            "world_revision": 12,
            "deliberation_revision": 2,
            "ledger_sequence": 12,
            "logical_time": NOW.isoformat(),
            "slices": {"world_life": _world_life_slice(photo_in_hand=True)},
        }
    )
    materials = __import__("json").loads(typed.materials_json)
    assert materials["moments_i_can_share"]["available_count"] == 1
    assert materials["moments_i_can_share"]["items"][0]["photo_in_hand"] is True


def _opened_candidate(
    *,
    candidate_id: str,
    refs: tuple[str, ...],
    status: str = "available",
    privacy: str = "personal",
    family: str = "character_media",
    taxonomy: str = "character_media:selfie",
    expires_at: datetime | None = None,
):
    return SimpleNamespace(
        candidate_id=candidate_id,
        status=status,
        family=family,
        privacy_ceiling=privacy,
        ecology_category=taxonomy,
        opened_at=NOW - timedelta(hours=3),
        expires_at=NOW + timedelta(days=2) if expires_at is None else expires_at,
        source_event_refs=refs,
        source_events=tuple(SimpleNamespace(event_ref=ref) for ref in refs),
        opened_event_ref=refs[0],
        opened_event_payload_hash="a" * 64,
    )


def _empty_projection(*candidates: object) -> SimpleNamespace:
    return SimpleNamespace(
        logical_time=NOW,
        photo_candidates=candidates,
        media_deliveries=(),
        media_opportunities=(),
        media_plans=(),
        world_occurrences=(),
        proposal_revisions=(),
        committed_world_event_refs=(),
    )


def _snapshot_context(slices: dict[str, object]) -> dict[str, object]:
    return {
        "world_id": "world:album",
        "actor_ref": "agent:companion",
        "world_revision": 12,
        "deliberation_revision": 2,
        "ledger_sequence": 12,
        "logical_time": NOW.isoformat(),
        "slices": slices,
    }


def test_three_opened_candidates_survive_unavailable_world_life() -> None:
    from companion_daemon.world_v2.photographable_inventory import (
        install_shareable_photos_context,
    )

    projection = _empty_projection(
        _opened_candidate(
            candidate_id="photo-candidate:a",
            refs=("event:image:a", "event:life:bookstore"),
            privacy="personal",
        ),
        _opened_candidate(
            candidate_id="photo-candidate:b",
            refs=("event:activity:b", "event:image:b"),
            privacy="private",
        ),
        _opened_candidate(
            candidate_id="photo-candidate:c",
            refs=("event:activity:c", "event:image:c"),
            privacy="private",
        ),
    )
    context = install_shareable_photos_context(
        _snapshot_context(
            {
                "world_life": {"availability": "unavailable"},
                "current_situation": {
                    "availability": "available",
                    "source_refs": ["event:situation:1"],
                    "items": [
                        {
                            "source_ref": "event:situation:1",
                            "value": {"activity_slices": []},
                        }
                    ],
                },
            }
        ),
        projection,
    )
    typed = compile_inner_life_snapshot(context)
    inventory = __import__("json").loads(typed.materials_json)["moments_i_can_share"]
    assert inventory["available_count"] == 3
    assert [item["photo_in_hand"] for item in inventory["items"]] == [True, True, True]
    assert {item["candidate_id"] for item in inventory["items"]} == {
        "photo-candidate:a",
        "photo-candidate:b",
        "photo-candidate:c",
    }
    assert "hold_reason" not in inventory["items"][0]


def test_candidate_without_settlement_still_counts() -> None:
    from companion_daemon.world_v2.photographable_inventory import (
        install_shareable_photos_context,
    )

    projection = _empty_projection(
        _opened_candidate(
            candidate_id="photo-candidate:activity-only",
            refs=("event:activity:lifecycle", "event:image:declared"),
            privacy="private",
        )
    )
    context = install_shareable_photos_context(
        _snapshot_context({"world_life": {"availability": "unavailable"}}),
        projection,
    )
    typed = compile_inner_life_snapshot(context)
    inventory = __import__("json").loads(typed.materials_json)["moments_i_can_share"]
    assert inventory["available_count"] == 1
    assert inventory["items"][0]["candidate_id"] == "photo-candidate:activity-only"
    assert inventory["items"][0]["photo_in_hand"] is True


def test_held_candidates_are_visible_with_reason() -> None:
    from companion_daemon.world_v2.photographable_inventory import (
        install_shareable_photos_context,
    )

    projection = _empty_projection(
        _opened_candidate(
            candidate_id="photo-candidate:live",
            refs=("event:image:live",),
        ),
        _opened_candidate(
            candidate_id="photo-candidate:old",
            refs=("event:image:old",),
            expires_at=NOW - timedelta(seconds=1),
        ),
        _opened_candidate(
            candidate_id="photo-candidate:skip",
            refs=("event:image:skip",),
            status="skipped",
        ),
    )
    context = install_shareable_photos_context(
        _snapshot_context({"world_life": {"availability": "unavailable"}}),
        projection,
    )
    typed = compile_inner_life_snapshot(context)
    inventory = __import__("json").loads(typed.materials_json)["moments_i_can_share"]
    by_id = {item["candidate_id"]: item for item in inventory["items"]}
    assert inventory["available_count"] == 1
    assert by_id["photo-candidate:live"]["photo_in_hand"] is True
    assert "hold_reason" not in by_id["photo-candidate:live"]
    assert by_id["photo-candidate:old"]["photo_in_hand"] is False
    assert by_id["photo-candidate:old"]["hold_reason"] == "expired"
    assert by_id["photo-candidate:skip"]["hold_reason"] == "skipped"


def test_installed_empty_album_does_not_inherit_world_life_join() -> None:
    from companion_daemon.world_v2.photographable_inventory import (
        install_shareable_photos_context,
    )

    context = install_shareable_photos_context(
        _snapshot_context({"world_life": _world_life_slice(photo_in_hand=True)}),
        _empty_projection(),
    )
    typed = compile_inner_life_snapshot(context)
    inventory = __import__("json").loads(typed.materials_json)["moments_i_can_share"]
    assert inventory["available_count"] == 0
    assert inventory["items"] == []
    assert inventory["availability"] == "available"
