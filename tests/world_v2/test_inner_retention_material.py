"""The derived reading of what her own recent inner state left behind.

The material is pure projection arithmetic over the already redacted model
view.  These tests pin what it counts, prove it cannot total a source she may
not see, and prove that an empty result stays visible instead of vanishing.
"""

from __future__ import annotations

import json

from companion_daemon.world_v2.character_interior.contracts import (
    INNER_RETENTION_MATERIAL_KEY,
)
from companion_daemon.world_v2.character_interior.snapshot_compiler import (
    compile_inner_life_snapshot,
)


def _dialogue_item(
    ref: str, *, speaker: str, text: str, occurred_at: str, sequence: int
) -> dict[str, object]:
    return {
        "item_ref": ref,
        "privacy_class": "private",
        "value": {
            "dialogue_id": ref,
            "speaker": speaker,
            "speaker_ref": "agent:companion" if speaker == "companion" else "user:primary",
            "text": text,
            "occurred_at": occurred_at,
            "delivery_state": "delivered" if speaker == "companion" else "observed",
            "sequence": sequence,
        },
    }


_DIALOGUE = [
    _dialogue_item(
        "dialogue:1",
        speaker="counterpart",
        text="在吗",
        occurred_at="2026-08-04T11:00:00+08:00",
        sequence=1,
    ),
    _dialogue_item(
        "dialogue:2",
        speaker="companion",
        text="在",
        occurred_at="2026-08-04T11:01:00+08:00",
        sequence=2,
    ),
    _dialogue_item(
        "dialogue:3",
        speaker="counterpart",
        text="今天怎么样",
        occurred_at="2026-08-04T11:30:00+08:00",
        sequence=3,
    ),
    _dialogue_item(
        "dialogue:4",
        speaker="companion",
        text="还行",
        occurred_at="2026-08-04T11:31:00+08:00",
        sequence=4,
    ),
    _dialogue_item(
        "dialogue:5",
        speaker="counterpart",
        text="那就好",
        occurred_at="2026-08-04T11:50:00+08:00",
        sequence=5,
    ),
    _dialogue_item(
        "dialogue:6",
        speaker="companion",
        text="嗯",
        occurred_at="2026-08-04T11:51:00+08:00",
        sequence=6,
    ),
]


def _context() -> dict[str, object]:
    return {
        "world_id": "world:retention",
        "actor_ref": "agent:companion",
        "world_revision": 12,
        "deliberation_revision": 7,
        "ledger_sequence": 31,
        "logical_time": "2026-08-04T12:00:00+08:00",
        "slices": {
            "recent_dialogue": {
                "availability": "available",
                "items": [json.loads(json.dumps(item)) for item in _DIALOGUE],
            },
            "affect_episodes": {
                "availability": "available",
                "items": [
                    {
                        "item_ref": "affect:warmth",
                        "value": {
                            "episode_id": "affect-episode:warmth",
                            "status": "active",
                            "opened_at": "2026-08-04T09:00:00+08:00",
                            "components": [
                                {
                                    "component_id": "affect-component:warmth",
                                    "dimension": "warmth",
                                    "intensity_bp": 4200,
                                    "opened_at": "2026-08-04T09:00:00+08:00",
                                }
                            ],
                        },
                    },
                    {
                        "item_ref": "affect:sadness",
                        "value": {
                            "episode_id": "affect-episode:sadness",
                            "status": "active",
                            "opened_at": "2026-08-03T11:00:00+08:00",
                            "updated_at": "2026-08-04T11:31:00+08:00",
                            "components": [
                                {
                                    "component_id": "affect-component:sadness",
                                    "dimension": "sadness",
                                    "intensity_bp": 1500,
                                    "opened_at": "2026-08-03T11:00:00+08:00",
                                }
                            ],
                        },
                    },
                ],
            },
            "private_impressions": {
                "availability": "available",
                "items": [
                    {
                        "item_ref": "impression:he-holds-back",
                        "privacy_class": "private",
                        "value": {
                            "subject_ref": "user:primary",
                            "reflection_summary": "他讲难受的事总是先说没事",
                            "confidence_bp": 6000,
                            "first_seen": "2026-08-02T20:00:00+08:00",
                            "last_supported": "2026-08-02T20:00:00+08:00",
                            "expiry_condition": "他主动说了一次难受",
                            "status": "active",
                        },
                    }
                ],
            },
            "appraisals": {
                "availability": "available",
                "items": [
                    {
                        "item_ref": "appraisal:1",
                        "value": {
                            "subject_ref": "user:primary",
                            "confidence_bp": 5000,
                            "accepted_at": "2026-08-04T11:01:00+08:00",
                        },
                    },
                    {
                        "item_ref": "appraisal:2",
                        "value": {
                            "subject_ref": "user:primary",
                            "confidence_bp": 5000,
                            "accepted_at": "2026-08-04T11:31:00+08:00",
                        },
                    },
                ],
            },
        },
    }


def _retention(
    context: dict[str, object], *, hidden: frozenset[str] = frozenset()
) -> dict[str, object]:
    typed = compile_inner_life_snapshot(context)
    view = typed.model_view(
        visible_source_refs=frozenset(typed.source_refs) - hidden
    )
    return view["materials"][INNER_RETENTION_MATERIAL_KEY]


def test_retention_counts_the_living_state_and_the_turns_that_added_none() -> None:
    retention = _retention(_context())

    assert retention == {
        "还活着的持续情绪": [
            "warmth 4200（3 小时前开的）",
            "sadness 1500（1 天前开的）",
        ],
        "还在的私人印象": 1,
        "最近这些读法的分量": [5000, 5000],
        "最近我说话的回合": 3,
        "其中没有新增持续情绪或私人印象的": 2,
    }


def test_a_resolved_episode_is_no_longer_counted_as_living() -> None:
    context = _context()
    episode = context["slices"]["affect_episodes"]["items"][1]["value"]
    episode["status"] = "resolved"
    episode["closed_at"] = "2026-08-04T11:40:00+08:00"

    retention = _retention(context)

    assert retention["还活着的持续情绪"] == ["warmth 4200（3 小时前开的）"]
    # The turn that revised it still counts: it was left at the time.
    assert retention["其中没有新增持续情绪或私人印象的"] == 2


def test_retention_totals_only_what_survived_redaction() -> None:
    context = _context()
    typed = compile_inner_life_snapshot(context)
    hidden = frozenset({"affect:sadness", "impression:he-holds-back"})
    view = typed.model_view(visible_source_refs=frozenset(typed.source_refs) - hidden)
    retention = view["materials"][INNER_RETENTION_MATERIAL_KEY]

    assert retention == {
        "还活着的持续情绪": ["warmth 4200（3 小时前开的）"],
        "还在的私人印象": 0,
        "最近这些读法的分量": [5000, 5000],
        "最近我说话的回合": 3,
        "其中没有新增持续情绪或私人印象的": 3,
    }
    assert "private_impressions" not in view["materials"]
    assert [item["episode_id"] for item in view["materials"]["affect"]] == [
        "affect-episode:warmth"
    ]
    serialized = json.dumps(retention, ensure_ascii=False)
    assert "sadness" not in serialized
    assert "他讲难受的事总是先说没事" not in serialized


def test_a_redacted_turn_leaves_the_window_it_cannot_prove() -> None:
    retention = _retention(_context(), hidden=frozenset({"dialogue:4"}))

    assert retention["最近我说话的回合"] == 2
    # The 11:31 revision belonged to the turn she can no longer see, so it is
    # not credited to either surviving turn.
    assert retention["其中没有新增持续情绪或私人印象的"] == 2


def test_a_multi_beat_expression_is_one_turn_rather_than_several() -> None:
    context = _context()
    context["slices"]["recent_dialogue"]["items"].extend(
        [
            _dialogue_item(
                "dialogue:6b",
                speaker="companion",
                text="就是有点累",
                occurred_at="2026-08-04T11:51:04+08:00",
                sequence=7,
            ),
            _dialogue_item(
                "dialogue:6c",
                speaker="companion",
                text="不说这个了",
                occurred_at="2026-08-04T11:51:09+08:00",
                sequence=8,
            ),
        ]
    )

    retention = _retention(context)

    assert retention["最近我说话的回合"] == 3
    assert retention["其中没有新增持续情绪或私人印象的"] == 2


def test_a_turn_she_has_no_earlier_message_for_is_left_uncounted() -> None:
    context = _context()
    items = context["slices"]["recent_dialogue"]["items"]
    context["slices"]["recent_dialogue"]["items"] = [
        item for item in items if item["item_ref"] != "dialogue:1"
    ]

    retention = _retention(context)

    assert retention["最近我说话的回合"] == 2
    assert retention["其中没有新增持续情绪或私人印象的"] == 1


def test_retention_stays_visible_when_nothing_at_all_was_kept() -> None:
    context = _context()
    del context["slices"]["affect_episodes"]
    del context["slices"]["private_impressions"]
    del context["slices"]["appraisals"]

    retention = _retention(context)

    assert retention == {
        "还活着的持续情绪": [],
        "还在的私人印象": 0,
        "最近这些读法的分量": [],
        "最近我说话的回合": 3,
        "其中没有新增持续情绪或私人印象的": 3,
    }


def test_an_empty_snapshot_states_nothing_rather_than_an_empty_tally() -> None:
    context = _context()
    context["slices"] = {
        "current_situation": {
            "availability": "available",
            "items": [
                {"item_ref": "situation:now", "value": {"time_segment": "noon"}}
            ],
        }
    }

    view = compile_inner_life_snapshot(context).model_view()

    assert INNER_RETENTION_MATERIAL_KEY not in view["materials"]


def test_retention_is_view_only_and_mints_no_source_coordinate() -> None:
    typed = compile_inner_life_snapshot(_context())
    view = typed.model_view()

    assert INNER_RETENTION_MATERIAL_KEY not in typed.materials
    assert INNER_RETENTION_MATERIAL_KEY not in json.loads(typed.materials_json)
    assert all(
        item.scope != INNER_RETENTION_MATERIAL_KEY for item in typed.source_inventory
    )
    assert all(
        INNER_RETENTION_MATERIAL_KEY not in facet["material_keys"]
        for facet in view["faculties"].values()
    )
    assert view["snapshot_hash"] == typed.snapshot_hash


def test_retention_states_facts_without_asking_her_for_anything() -> None:
    serialized = json.dumps(_retention(_context()), ensure_ascii=False)

    for pushed in ("应该", "建议", "记得", "请", "需要", "最好", "不妨", "可以试"):
        assert pushed not in serialized
